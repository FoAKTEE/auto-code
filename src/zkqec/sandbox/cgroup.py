"""Campaign cgroup slice, per-job scopes and the campaign-deadline supervisor (contracts §J4, D14).

* The campaign parent slice ``zkqec-<campaign>.slice`` (a runtime drop-in in the user manager) carries
  the aggregate limits: ``MemoryMax`` (which also bounds tmpfs pages, charged to the writer), ``TasksMax``
  and ``CPUQuota``. On this host the user manager is delegated only the ``memory`` and ``pids``
  controllers, so ``CPUQuota`` is accepted by systemd but **not enforced** -- :func:`controllers`
  reports this and qualification records it.
* Every job runs in a transient scope inside that slice with ``RuntimeMaxSec`` (per-job wall time),
  its own ``MemoryMax``/``TasksMax`` and no swap.
* systemd 249 has no ``RuntimeMaxSec`` for slices, so the campaign deadline is a transient systemd
  timer (independent of the orchestrator process) whose service first writes the broker's
  dispatch-disabled switch and then stops the whole slice, i.e. every child scope, including jobs
  abandoned by a dead orchestrator.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

SYSTEMD_RUN = "/usr/bin/systemd-run"
SYSTEMCTL = "/usr/bin/systemctl"
CGROUP_ROOT = Path("/sys/fs/cgroup")
_ID = re.compile(r"^[A-Za-z0-9_]{1,48}$")


def host_env() -> dict[str, str]:
    """Minimal environment for talking to the user's systemd manager."""
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    for k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
        if k in os.environ:
            env[k] = os.environ[k]
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _systemctl(*args: str, check: bool = False, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run([SYSTEMCTL, "--user", *args], capture_output=True, text=True, env=host_env(),
                          check=check, timeout=timeout)


def check_id(value: str) -> str:
    if not _ID.match(value):
        raise ValueError(f"identifier must match {_ID.pattern}: {value!r}")
    return value


def unit_cgroup(unit: str) -> Path | None:
    out = _systemctl("show", "-p", "ControlGroup", "--value", unit).stdout.strip()
    return CGROUP_ROOT / out.lstrip("/") if out else None


def controllers() -> dict[str, list[str]]:
    """Controllers available to the user manager's subtree (what user slices can enforce)."""
    uid = os.getuid()
    base = CGROUP_ROOT / "user.slice" / f"user-{uid}.slice" / f"user@{uid}.service"
    res: dict[str, list[str]] = {}
    for name in ("cgroup.controllers", "cgroup.subtree_control"):
        try:
            res[name] = (base / name).read_text().split()
        except OSError:
            res[name] = []
    return res


@dataclass
class CampaignSlice:
    campaign: str
    memory_max: int
    tasks_max: int
    cpu_quota_percent: int | None = None

    def __post_init__(self) -> None:
        check_id(self.campaign)

    @property
    def name(self) -> str:
        return f"zkqec-{self.campaign}.slice"

    def create(self) -> None:
        props = [f"MemoryMax={self.memory_max}", f"TasksMax={self.tasks_max}", "MemorySwapMax=0"]
        if self.cpu_quota_percent is not None:
            props.append(f"CPUQuota={self.cpu_quota_percent}%")
        _systemctl("set-property", "--runtime", self.name, *props, check=True)

    def cgroup(self) -> Path:
        """``a-b.slice`` is a child of ``a.slice`` (systemd slice naming), so the path is nested."""
        uid = os.getuid()
        path = CGROUP_ROOT / "user.slice" / f"user-{uid}.slice" / f"user@{uid}.service"
        parts = self.name[: -len(".slice")].split("-")
        for i in range(1, len(parts) + 1):
            path = path / ("-".join(parts[:i]) + ".slice")
        return path

    def limits(self) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for f in ("memory.max", "pids.max", "cpu.max", "memory.swap.max"):
            try:
                out[f] = (self.cgroup() / f).read_text().strip()
            except OSError:
                out[f] = None
        return out

    def scope_cgroups(self) -> list[Path]:
        try:
            return sorted(p for p in self.cgroup().iterdir() if p.is_dir() and p.name.endswith(".scope"))
        except OSError:
            return []

    def pids(self) -> list[int]:
        pids: list[int] = []
        for cg in [self.cgroup(), *self.scope_cgroups()]:
            try:
                pids += [int(x) for x in (cg / "cgroup.procs").read_text().split()]
            except OSError:
                pass
        return pids

    def stop(self) -> None:
        _systemctl("stop", self.name, timeout=120)

    def kill_all(self) -> None:
        """Immediate SIGKILL of every process in the slice (cgroup.kill), then stop."""
        try:
            (self.cgroup() / "cgroup.kill").write_text("1")
        except OSError:
            pass
        self.stop()

    def cleanup(self) -> None:
        self.stop()
        _systemctl("reset-failed", f"zkqec-{self.campaign}-*")
        _systemctl("revert", self.name)

    def scope_argv(self, job_id: str, *, runtime_max_sec: int, memory_max: int, tasks_max: int) -> list[str]:
        check_id(job_id)
        return [SYSTEMD_RUN, "--user", "--scope", "--quiet", "--collect", f"--slice={self.name}",
                f"--unit={self.scope_unit(job_id)}", "-p", f"RuntimeMaxSec={int(runtime_max_sec)}",
                "-p", f"MemoryMax={int(memory_max)}", "-p", "MemorySwapMax=0", "-p", f"TasksMax={int(tasks_max)}",
                "-p", "TimeoutStopSec=5", "--"]

    def service_argv(self, job_id: str, *, runtime_max_sec: int) -> list[str]:
        """A detached job (a transient service in the slice): survives its launcher -- used to model
        abandoned jobs in qualification."""
        check_id(job_id)
        return [SYSTEMD_RUN, "--user", "--quiet", "--collect", f"--slice={self.name}",
                f"--unit=zkqec-{self.campaign}-{job_id}", "-p", f"RuntimeMaxSec={int(runtime_max_sec)}", "--"]

    def scope_unit(self, job_id: str) -> str:
        return f"zkqec-{self.campaign}-{job_id}.scope"

    def kill_scope(self, job_id: str) -> None:
        unit = self.scope_unit(job_id)
        cg = unit_cgroup(unit)
        if cg is not None:
            try:
                (cg / "cgroup.kill").write_text("1")
            except OSError:
                pass
        _systemctl("kill", "--signal=SIGKILL", unit)

    def units(self) -> list[str]:
        out = _systemctl("list-units", "--all", "--plain", "--no-legend", f"zkqec-{self.campaign}-*").stdout
        return [line.split()[0] for line in out.splitlines() if line.strip()]


class DeadlineSupervisor:
    """Campaign wall-clock deadline as a transient systemd timer (outlives the orchestrator)."""

    SCRIPT = ('umask 077; printf \'{"reason":"campaign_deadline","ts":%s}\\n\' "$(date +%s)" > "$1.tmp" '
              '&& mv -f "$1.tmp" "$1"; exec /usr/bin/systemctl --user stop "$2"')

    def __init__(self, slice_: CampaignSlice, switch_path: str | os.PathLike[str], deadline_unix: float) -> None:
        self.slice = slice_
        self.switch_path = str(switch_path)
        self.deadline_unix = float(deadline_unix)

    @property
    def unit(self) -> str:
        return f"zkqec-{self.slice.campaign}-deadline"

    def argv(self) -> list[str]:
        secs = max(1, int(round(self.deadline_unix - time.time())))
        return [SYSTEMD_RUN, "--user", "--quiet", "--collect", f"--unit={self.unit}", f"--on-active={secs}s",
                "--timer-property=AccuracySec=1s", "/bin/sh", "-c", self.SCRIPT, "zkqec-deadline",
                self.switch_path, self.slice.name]

    def arm(self) -> None:
        subprocess.run(self.argv(), check=True, capture_output=True, text=True, env=host_env(), timeout=60)

    def armed(self) -> bool:
        out = _systemctl("show", "-p", "ActiveState", "--value", f"{self.unit}.timer").stdout.strip()
        return out == "active"

    def disarm(self) -> None:
        _systemctl("stop", f"{self.unit}.timer")
        _systemctl("reset-failed", f"{self.unit}.timer", f"{self.unit}.service")
