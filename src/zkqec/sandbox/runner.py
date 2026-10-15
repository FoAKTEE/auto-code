"""Run one job under a launch profile, inside its cgroup scope (contracts §J0–§J4, §L).

Chain: ``systemd-run --user --scope`` (job scope in the campaign slice, RuntimeMaxSec, MemoryMax,
TasksMax) → ``nsexec.py`` (private size-limited tmpfs, credential copy, rlimits, output collection)
→ ``bwrap`` (profile) → ``entry.sh`` (in-sandbox egress bridge) → the job command.

The launcher keeps only stdio to the job (stdin from a regular input file or /dev/null; stdout/stderr
drained under byte caps, overflow kills the scope). While the job runs it watches the broker's
fail-closed marker: a policy violation kills the scope at once.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .cgroup import CampaignSlice, host_env
from .outputs import CappedStream
from .profiles import NSEXEC, JobLayout, LaunchProfile, build_bwrap_argv

NSEXEC_PYTHON = sys.executable


@dataclass(frozen=True)
class HomeFile:
    """A file placed into the fresh per-job home (credentials only)."""
    src: str          # host path (read by nsexec; content never passes through this process)
    dest: str         # path relative to the sandbox home, e.g. ".claude/.credentials.json"
    mode: int = 0o600
    max_bytes: int = 65536


@dataclass(frozen=True)
class Limits:
    wall_seconds: int = 600
    memory_max: int = 4 * 1024**3
    tasks_max: int = 512
    stdout_cap: int = 4 * 1024**2
    stderr_cap: int = 1024**2
    collect_max_files: int = 16
    collect_max_file_bytes: int = 4 * 1024**2
    collect_max_total_bytes: int = 16 * 1024**2


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    profile: LaunchProfile
    command: tuple[str, ...]
    env: dict[str, str]
    inputs: dict[str, bytes] = field(default_factory=dict)
    home_files: tuple[HomeFile, ...] = ()
    stdin_input: str | None = None          # name in ``inputs`` fed as stdin (else /dev/null)
    limits: Limits = Limits()
    ca_cert: str | None = None
    broker_port: int | None = None
    token: str | None = None


@dataclass
class JobResult:
    job_id: str
    outcome: str            # completed | nonzero_exit | timeout | output_cap | policy_blocked | launch_failure
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    stdout_total: int
    stderr_total: int
    wall_seconds: float
    blocked_reason: str | None
    collected: dict[str, Any]
    argv: list[str]
    bwrap_argv: list[str]
    profile_sha256: str
    job_dir: str


class LiveCallForbidden(RuntimeError):
    """A job would receive a real credential file while live calls are forbidden (test runs)."""


_LIVE_CREDENTIALS = ("~/.claude/.credentials.json", "~/.codex/auth.json")


def _forbid_live(spec: JobSpec) -> None:
    """While ``ZKQEC_FORBID_LIVE=1`` (set for every test run), no real credential file enters a job
    home, so no code path can make a billed model call (an unintended test-run campaign spent $0.2047)."""
    if os.environ.get("ZKQEC_FORBID_LIVE") != "1":
        return
    real = {os.path.realpath(os.path.expanduser(p)) for p in _LIVE_CREDENTIALS}
    for h in spec.home_files:
        if os.path.realpath(h.src) in real:
            raise LiveCallForbidden(f"job {spec.job_id}: live credentials are forbidden here "
                                    "(ZKQEC_FORBID_LIVE=1)")


def run_job(spec: JobSpec, jobs_root: str | os.PathLike[str], slice_: CampaignSlice, *,
            blocked_probe: Callable[[str], str | None] | None = None, keep_job_dir: bool = True,
            poll: float = 0.2, host_env_extra: dict[str, str] | None = None) -> JobResult:
    """Run one job to completion. ``host_env_extra`` adds variables to the *launcher-side* environment
    (qualification uses it to prove that nothing inherited crosses into the sandbox)."""
    _forbid_live(spec)
    if spec.profile.credentials is False and spec.home_files:
        raise ValueError(f"profile {spec.profile.name} carries no credentials")
    if spec.profile.network == "bridge" and (spec.broker_port is None or spec.token is None):
        raise ValueError("bridge network profile needs a broker port and a job token")
    layout = JobLayout(Path(jobs_root).resolve() / spec.job_id)
    if layout.job_dir.exists():
        raise FileExistsError(f"job directory exists: {layout.job_dir}")
    layout.create()
    for name, data in spec.inputs.items():
        if "/" in name or name.startswith("."):
            raise ValueError(f"bad input name {name!r}")
        p = layout.in_dir / name
        p.write_bytes(data)
        os.chmod(p, 0o444)

    bwrap_argv = build_bwrap_argv(spec.profile, layout, spec.env, spec.command, ca_cert=spec.ca_cert)
    lim = spec.limits
    nsspec = {
        "priv_dir": str(layout.priv_dir), "tmpfs_size": spec.profile.tmpfs_bytes,
        "subdirs": ["home", "tmp", "work", "out"],
        "home_files": [{"src": h.src, "dest": os.path.join("home", h.dest), "mode": h.mode,
                        "max_bytes": h.max_bytes} for h in spec.home_files],
        "rlimits": dict(spec.profile.rlimits), "argv": bwrap_argv, "report": str(layout.report_path),
        "collect": {"to": str(layout.out_dir), "max_files": lim.collect_max_files,
                    "max_file_bytes": lim.collect_max_file_bytes, "max_total_bytes": lim.collect_max_total_bytes},
    }
    fd = os.open(layout.spec_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(nsspec, fh)

    bridge = None
    if spec.profile.network == "bridge":
        from ..broker.bridge import Bridge
        bridge = Bridge(str(layout.socket_path), spec.broker_port, spec.token).start()  # type: ignore[arg-type]

    argv = slice_.scope_argv(spec.job_id, runtime_max_sec=lim.wall_seconds, memory_max=lim.memory_max,
                             tasks_max=lim.tasks_max) + [NSEXEC_PYTHON, "-I", "-S", str(NSEXEC), str(layout.spec_path)]
    stdin = open(layout.in_dir / spec.stdin_input, "rb") if spec.stdin_input else subprocess.DEVNULL
    killed: list[str] = []

    def _kill(reason: str) -> None:
        if not killed:
            killed.append(reason)
            slice_.kill_scope(spec.job_id)

    t0 = time.time()
    try:
        proc = subprocess.Popen(argv, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                close_fds=True, env={**host_env(), **(host_env_extra or {})})
    except OSError as exc:
        if bridge:
            bridge.stop()
        return JobResult(spec.job_id, "launch_failure", None, b"", str(exc).encode(), 0, 0, 0.0, None, {},
                         argv, bwrap_argv, spec.profile.sha256(), str(layout.job_dir))
    finally:
        if stdin is not subprocess.DEVNULL:
            stdin.close()  # type: ignore[union-attr]
    out = CappedStream(proc.stdout, lim.stdout_cap, lambda: _kill("output_cap"))  # type: ignore[arg-type]
    err = CappedStream(proc.stderr, lim.stderr_cap, lambda: _kill("output_cap"))  # type: ignore[arg-type]
    blocked = None
    while proc.poll() is None:
        if blocked_probe is not None and blocked is None:
            blocked = blocked_probe(spec.job_id)
            if blocked:
                _kill("policy_blocked")
        if time.time() - t0 > lim.wall_seconds + 10:   # backstop to RuntimeMaxSec
            _kill("timeout")
        time.sleep(poll)
    rc = proc.returncode
    wall = time.time() - t0
    stdout, stderr = out.join(5), err.join(5)
    if bridge:
        bridge.stop()
    if blocked_probe is not None and blocked is None:
        blocked = blocked_probe(spec.job_id)
    report: dict[str, Any] = {}
    try:
        report = json.loads(layout.report_path.read_text())
    except (OSError, ValueError):
        pass
    if blocked:
        outcome = "policy_blocked"
    elif killed and killed[0] == "output_cap":
        outcome = "output_cap"
    elif killed and killed[0] == "timeout" or (rc != 0 and wall >= lim.wall_seconds - 0.5):
        outcome = "timeout"
    elif rc == 0:
        outcome = "completed"
    else:
        outcome = "nonzero_exit"
    try:
        os.rmdir(layout.priv_dir)   # the tmpfs lived only in the job's mount namespace
    except OSError:
        pass
    result = JobResult(spec.job_id, outcome, rc, stdout, stderr, out.total, err.total, wall, blocked,
                       report.get("collect", {}), argv, bwrap_argv, spec.profile.sha256(), str(layout.job_dir))
    if not keep_job_dir:
        shutil.rmtree(layout.job_dir, ignore_errors=True)
    return result
