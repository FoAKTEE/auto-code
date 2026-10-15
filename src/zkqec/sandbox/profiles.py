"""Frozen bwrap launch profiles (contracts §J0, §J2, §J4).

Two profiles:

* ``agent-call`` -- one headless CLI invocation in call mode. Empty root; read-only ``/usr`` and the
  provider's CLI runtime; a fresh per-job home holding only the credential; the job's private tmpfs
  for ``/home/agent``, ``/tmp``, ``/job/work``, ``/job/out``; read-only ``/job/in``; the egress bridge
  socket at ``/run/zkqec`` and the per-campaign broker CA. No route out except the bridge.
* ``program`` -- a generated program (P1b): same isolation, no credentials, no network at all, a
  size-limited tmpfs scratch, optional read-only Python runtime.

Both: ``--unshare-user --unshare-pid --unshare-ipc --unshare-uts --unshare-net --unshare-cgroup-try
--new-session --die-with-parent --clearenv`` with an explicit environment allowlist, private ``/proc``
and ``/dev``, read-only root, and nothing from ``$HOME``, the repo, ``/run/user``, D-Bus, SSH agent or
X11. The profile (and hence its hash, which goes into the campaign manifest, §O) contains no per-job
values; per-job paths are supplied by :class:`JobLayout` at build time.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

ENTRY_SH = Path(__file__).resolve().with_name("entry.sh")
NSEXEC = Path(__file__).resolve().with_name("nsexec.py")
BWRAP = "/usr/bin/bwrap"
SOCAT = "/usr/bin/socat"

# In-sandbox paths.
SB_HOME = "/home/agent"
SB_WORK = "/job/work"
SB_OUT = "/job/out"
SB_IN = "/job/in"
SB_TMP = "/tmp"
SB_RUN = "/run/zkqec"
SB_CA = "/opt/zkqec/ca/broker-ca.pem"
SB_PROXY_PORT = 3128
SB_PROXY_URL = f"http://127.0.0.1:{SB_PROXY_PORT}"

UNSHARE = ("--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-net",
           "--unshare-cgroup-try")
SESSION = ("--new-session", "--die-with-parent", "--clearenv")

# Names that may appear in a sandbox environment. Values are set by zkqec.agents.launch.
AGENT_ENV_ALLOWLIST = frozenset({
    "HOME", "PATH", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "LOGNAME", "SHELL",
    "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY",
    "no_proxy", "ZKQEC_PROXY_PORT", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "CODEX_CA_CERTIFICATE",
    "CODEX_HOME", "CLAUDE_CODE_DISABLE_AUTO_MEMORY", "CLAUDE_CODE_DISABLE_CLAUDE_MDS",
    "DISABLE_AUTOUPDATER", "DISABLE_TELEMETRY", "DISABLE_ERROR_REPORTING",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "CLAUDE_CODE_MAX_RETRIES",
    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "CLAUDE_CODE_DISABLE_ADVISOR_TOOL",
    "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS", "CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING", "DISABLE_BUG_COMMAND",
    "DISABLE_COST_WARNINGS", "NO_COLOR", "RUST_LOG",
})
PROGRAM_ENV_ALLOWLIST = frozenset({"HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "PYTHONHASHSEED",
                                   "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "OMP_NUM_THREADS"})


@dataclass(frozen=True)
class RuntimeBind:
    host: str
    sandbox: str


@dataclass(frozen=True)
class LaunchProfile:
    name: str
    network: str                                   # "bridge" | "none"
    credentials: bool
    runtime: tuple[RuntimeBind, ...]               # read-only runtime binds (besides /usr)
    symlinks: tuple[tuple[str, str], ...] = ()     # (target, link) inside the sandbox
    env_allowlist: frozenset[str] = frozenset()
    tmpfs_bytes: int = 512 * 1024 * 1024           # private writable area (home+tmp+work+out)
    rlimits: tuple[tuple[str, int], ...] = (("CORE", 0), ("NOFILE", 4096), ("FSIZE", 256 * 1024 * 1024))
    unshare: tuple[str, ...] = UNSHARE
    session: tuple[str, ...] = SESSION
    extra: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict:
        d = asdict(self)
        d["env_allowlist"] = sorted(self.env_allowlist)
        d["entry_sh_sha256"] = _sha256(ENTRY_SH)
        d["nsexec_sha256"] = _sha256(NSEXEC)
        return d

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.to_json(), sort_keys=True).encode()).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------------------------------
# Host runtime locations
# --------------------------------------------------------------------------------------------------
HOME = Path(os.path.expanduser("~"))
CLAUDE_LINK = HOME / ".local" / "bin" / "claude"
NODE_PREFIX = HOME / ".local" / "node"
CODEX_PACKAGE = NODE_PREFIX / "lib" / "node_modules" / "@openai" / "codex"
PYTHON_RUNTIME = Path("/data/haiyangw/claude/zkqec-runtime/python/cpython-3.10.22-linux-x86_64-gnu")


def claude_runtime() -> tuple[RuntimeBind, ...]:
    real = os.path.realpath(CLAUDE_LINK)
    return (RuntimeBind(real, "/opt/zkqec/bin/claude"),)


def codex_runtime() -> tuple[tuple[RuntimeBind, ...], tuple[tuple[str, str], ...]]:
    binds = (RuntimeBind(str(NODE_PREFIX / "bin" / "node"), "/opt/zkqec/node/bin/node"),
             RuntimeBind(os.path.realpath(CODEX_PACKAGE), "/opt/zkqec/node/lib/node_modules/@openai/codex"))
    links = (("../lib/node_modules/@openai/codex/bin/codex.js", "/opt/zkqec/node/bin/codex"),)
    return binds, links


def agent_call_profile(provider: str, *, tmpfs_bytes: int = 512 * 1024 * 1024) -> LaunchProfile:
    if provider == "claude":
        runtime, links = claude_runtime(), ()
    elif provider == "codex":
        runtime, links = codex_runtime()
    elif provider == "none":   # bare profile used by qualification probes
        runtime, links = (), ()
    else:
        raise ValueError(f"unknown provider {provider}")
    return LaunchProfile(name=f"agent-call/{provider}", network="bridge", credentials=provider != "none",
                         runtime=runtime, symlinks=links, env_allowlist=AGENT_ENV_ALLOWLIST,
                         tmpfs_bytes=tmpfs_bytes)


def program_profile(*, tmpfs_bytes: int = 256 * 1024 * 1024, with_python: bool = True,
                    cpu_seconds: int = 3600) -> LaunchProfile:
    runtime = (RuntimeBind(str(PYTHON_RUNTIME), "/opt/zkqec/python"),) if with_python else ()
    return LaunchProfile(name="program", network="none", credentials=False, runtime=runtime,
                         env_allowlist=PROGRAM_ENV_ALLOWLIST, tmpfs_bytes=tmpfs_bytes,
                         rlimits=(("CORE", 0), ("NOFILE", 1024), ("FSIZE", 64 * 1024 * 1024),
                                  ("CPU", cpu_seconds)))


# --------------------------------------------------------------------------------------------------
# Per-job layout and argv
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class JobLayout:
    """Host-side paths of one job (all under the job directory, mode 0700)."""
    job_dir: Path

    @property
    def in_dir(self) -> Path:
        return self.job_dir / "in"

    @property
    def etc_dir(self) -> Path:
        return self.job_dir / "kit" / "etc"

    @property
    def priv_dir(self) -> Path:        # mount point of the private tmpfs (empty on the host)
        return self.job_dir / "priv"

    @property
    def bridge_dir(self) -> Path:
        return self.job_dir / "bridge"

    @property
    def socket_path(self) -> Path:
        return self.bridge_dir / "broker.sock"

    @property
    def out_dir(self) -> Path:         # collected, capped outputs (trusted side)
        return self.job_dir / "out"

    @property
    def report_path(self) -> Path:
        return self.job_dir / "nsexec-report.json"

    @property
    def spec_path(self) -> Path:
        return self.job_dir / "nsexec-spec.json"

    def create(self) -> None:
        for d in (self.job_dir, self.in_dir, self.etc_dir.parent, self.etc_dir, self.priv_dir,
                  self.bridge_dir, self.out_dir):
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o700)
        uid, gid = os.getuid(), os.getgid()
        files = {
            "passwd": f"agent:x:{uid}:{gid}:zkqec agent:{SB_HOME}:/bin/sh\n",
            "group": f"agent:x:{gid}:\n",
            "hosts": "127.0.0.1 localhost\n::1 localhost\n",
            "nsswitch.conf": "passwd: files\ngroup: files\nhosts: files\n",
            "resolv.conf": "",
        }
        for name, text in files.items():
            (self.etc_dir / name).write_text(text)
        os.chmod(self.etc_dir, 0o755)
        for name in files:
            os.chmod(self.etc_dir / name, 0o644)


def build_bwrap_argv(profile: LaunchProfile, layout: JobLayout, env: dict[str, str],
                     command: list[str] | tuple[str, ...], *, ca_cert: str | None = None) -> list[str]:
    """The exact bwrap argv for one job. Raises ValueError on a non-allowlisted environment name."""
    bad = sorted(set(env) - profile.env_allowlist)
    if bad:
        raise ValueError(f"environment names not allowlisted by profile {profile.name}: {bad}")
    if profile.network == "none" and any(k.lower().endswith("_proxy") or k == "ZKQEC_PROXY_PORT" for k in env):
        raise ValueError("program profile has no network; proxy variables are not allowed")
    priv = layout.priv_dir
    a: list[str] = [BWRAP, *profile.unshare, *profile.session, "--hostname", "zkqec"]
    a += ["--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
          "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64"]
    a += ["--dir", "/etc", "--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache",
          "--ro-bind", "/etc/alternatives", "/etc/alternatives"]   # /usr/bin/awk etc. are alternatives links
    for name in ("passwd", "group", "hosts", "nsswitch.conf", "resolv.conf"):
        a += ["--ro-bind", str(layout.etc_dir / name), f"/etc/{name}"]
    a += ["--proc", "/proc", "--dev", "/dev"]
    a += ["--dir", "/opt/zkqec"]
    for rb in profile.runtime:
        a += ["--ro-bind", rb.host, rb.sandbox]
    for target, link in profile.symlinks:
        a += ["--symlink", target, link]
    a += ["--ro-bind", str(ENTRY_SH), "/opt/zkqec/entry.sh"]
    a += ["--ro-bind", str(layout.in_dir), SB_IN]
    a += ["--bind", str(priv / "work"), SB_WORK, "--bind", str(priv / "out"), SB_OUT,
          "--bind", str(priv / "home"), SB_HOME, "--bind", str(priv / "tmp"), SB_TMP]
    if profile.network == "bridge":
        a += ["--ro-bind", str(layout.bridge_dir), SB_RUN]
        if ca_cert:
            a += ["--ro-bind", ca_cert, SB_CA]
    a += ["--remount-ro", "/", "--remount-ro", "/dev", "--chdir", SB_WORK]
    for k in sorted(env):
        a += ["--setenv", k, env[k]]
    a += ["--", "/bin/sh", "/opt/zkqec/entry.sh", *command]
    return a
