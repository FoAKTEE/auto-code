"""zkqec sandbox pre-exec helper (contracts §J0, §J2, §J4). Standalone: stdlib only.

Run as ``python -I -S nsexec.py <spec.json>`` inside the job's systemd scope. It

1. sets ``PR_SET_PDEATHSIG`` so it dies with the launcher;
2. enters a fresh user + mount namespace (uid/gid mapped to themselves) and makes ``/`` private;
3. mounts a **size-limited tmpfs** (bwrap 0.6.1 has no ``--size``) on the job's private mount point,
   invisible to the host, and creates ``home``, ``tmp``, ``work``, ``out`` in it;
4. copies the job's credential file(s) into the fresh home (mode 0600, dirs 0700; regular files only,
   size-capped) -- credentials never touch disk outside this tmpfs, and vanish with the namespace;
5. runs bwrap (given argv) with an empty environment, only stdio inherited, and rlimits applied;
6. after bwrap exits, copies **only regular files** from ``out`` to the host collection directory,
   under per-file, file-count and total byte caps, and writes a manifest of what was accepted/rejected.

The spec file contains paths and limits only, never secrets.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import resource
import signal
import stat
import subprocess
import sys

CLONE_NEWNS = 0x00020000
CLONE_NEWUSER = 0x10000000
MS_NOSUID, MS_NODEV, MS_REC, MS_PRIVATE = 0x2, 0x4, 0x4000, 1 << 18
PR_SET_PDEATHSIG = 1
NAME_OK = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
RLIMITS = {"FSIZE": resource.RLIMIT_FSIZE, "CORE": resource.RLIMIT_CORE, "NOFILE": resource.RLIMIT_NOFILE,
           "CPU": resource.RLIMIT_CPU, "AS": resource.RLIMIT_AS, "MEMLOCK": resource.RLIMIT_MEMLOCK}

_libc = ctypes.CDLL(None, use_errno=True)


def _check(rc: int, what: str) -> None:
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"{what}: {os.strerror(err)}")


def _write(path: str, data: str) -> None:
    with open(path, "w") as fh:
        fh.write(data)


def enter_namespaces() -> None:
    uid, gid = os.getuid(), os.getgid()
    _check(_libc.unshare(CLONE_NEWUSER | CLONE_NEWNS), "unshare")
    _write("/proc/self/setgroups", "deny")
    _write("/proc/self/uid_map", f"{uid} {uid} 1\n")
    _write("/proc/self/gid_map", f"{gid} {gid} 1\n")
    _check(_libc.mount(b"none", b"/", None, MS_REC | MS_PRIVATE, None), "make-rprivate")


def mount_tmpfs(target: str, size: int) -> None:
    opts = f"size={int(size)},mode=0700,nr_inodes=65536".encode()
    _check(_libc.mount(b"zkqec-job", target.encode(), b"tmpfs", MS_NOSUID | MS_NODEV, opts), "mount tmpfs")


def copy_credential(src: str, dest: str, mode: int, max_bytes: int) -> None:
    fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
            raise OSError("credential source is not a small regular file")
        data = b""
        while len(data) <= max_bytes:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
    finally:
        os.close(fd)
    if len(data) > max_bytes:
        raise OSError("credential source exceeds cap")
    parent = os.path.dirname(dest)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    out = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        os.write(out, data)
    finally:
        os.close(out)
        data = b""
    os.chmod(dest, mode)


def collect(src_dir: str, cfg: dict) -> dict:
    """Copy regular files from src_dir to cfg['to'] under caps; never follow links."""
    dest = cfg["to"]
    max_files, max_file, max_total = int(cfg["max_files"]), int(cfg["max_file_bytes"]), int(cfg["max_total_bytes"])
    accepted, rejected, total = [], [], 0
    try:
        entries = sorted(os.scandir(src_dir), key=lambda e: e.name)
    except OSError as exc:
        return {"accepted": [], "rejected": [{"name": None, "reason": f"scan_failed:{exc.errno}"}]}
    for entry in entries:
        name = entry.name
        if not NAME_OK.match(name):
            rejected.append({"name": None, "reason": "bad_name"})
            continue
        st = os.lstat(entry.path)
        if not stat.S_ISREG(st.st_mode):
            rejected.append({"name": name, "reason": "not_regular_file"})
            continue
        if len(accepted) >= max_files:
            rejected.append({"name": name, "reason": "too_many_files"})
            continue
        if st.st_size > max_file:
            rejected.append({"name": name, "reason": "file_too_large", "size": st.st_size})
            continue
        if total + st.st_size > max_total:
            rejected.append({"name": name, "reason": "total_too_large", "size": st.st_size})
            continue
        try:
            fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError:
            rejected.append({"name": name, "reason": "open_failed"})
            continue
        try:
            fst = os.fstat(fd)
            if not stat.S_ISREG(fst.st_mode):
                rejected.append({"name": name, "reason": "not_regular_file"})
                continue
            data = os.read(fd, max_file + 1)
        finally:
            os.close(fd)
        if len(data) > max_file:
            rejected.append({"name": name, "reason": "file_too_large"})
            continue
        out = os.open(os.path.join(dest, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(out, data)
            os.fsync(out)
        finally:
            os.close(out)
        total += len(data)
        accepted.append({"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return {"accepted": accepted, "rejected": rejected, "total_bytes": total}


def main(argv: list[str]) -> int:
    with open(argv[1]) as fh:
        spec = json.load(fh)
    _check(_libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0), "prctl")
    if os.getppid() == 1:
        return 125
    priv = spec["priv_dir"]
    enter_namespaces()
    mount_tmpfs(priv, spec["tmpfs_size"])
    for sub in spec["subdirs"]:
        os.mkdir(os.path.join(priv, sub), 0o700)
    for hf in spec.get("home_files", []):
        dest = os.path.normpath(os.path.join(priv, hf["dest"]))
        if not dest.startswith(os.path.join(priv, "home") + os.sep):
            raise OSError("credential destination escapes the home")
        copy_credential(hf["src"], dest, int(hf["mode"]), int(hf.get("max_bytes", 65536)))
    limits = {RLIMITS[k]: int(v) for k, v in spec.get("rlimits", {}).items()}

    def _child() -> None:
        for res, val in limits.items():
            resource.setrlimit(res, (val, val))
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    proc = subprocess.Popen(spec["argv"], env={}, close_fds=True, preexec_fn=_child)
    while True:
        try:
            rc = proc.wait()
            break
        except KeyboardInterrupt:
            continue
    report = {"exit_code": rc}
    if "collect" in spec:
        report["collect"] = collect(os.path.join(priv, "out"), spec["collect"])
    tmp = spec["report"] + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(report, fh)
    os.replace(tmp, spec["report"])
    return rc if rc >= 0 else 128 - rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
