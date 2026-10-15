"""Host-local isolation checks (bwrap + user systemd). No network, no credentials, a few seconds.

Skipped automatically where bwrap or a user systemd manager is unavailable.
"""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest

from zkqec.sandbox import cgroup as CG
from zkqec.sandbox.profiles import program_profile
from zkqec.sandbox.runner import JobSpec, Limits, run_job

REPO = Path(__file__).resolve().parents[2]


def _host_ok() -> bool:
    if not (shutil.which("bwrap") and shutil.which("systemd-run")):
        return False
    r = subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True, text=True, env=CG.host_env())
    return r.stdout.strip() in ("running", "degraded")


pytestmark = pytest.mark.skipif(not _host_ok(), reason="needs bwrap and a user systemd manager")
ENV = {"PATH": "/usr/bin:/bin", "HOME": "/home/agent", "LANG": "C.UTF-8"}


@pytest.fixture(scope="module")
def slice_():
    s = CG.CampaignSlice("pytest" + secrets.token_hex(3), memory_max=2 * 1024**3, tasks_max=256)
    s.create()
    yield s
    s.cleanup()


def _run(slice_, tmp_path, script, *, tmpfs=32 << 20, limits=Limits(wall_seconds=30), jid=None, **kw):
    jid = jid or "t" + secrets.token_hex(4)
    spec = JobSpec(jid, program_profile(tmpfs_bytes=tmpfs, with_python=False), ("/bin/sh", "-c", script), dict(ENV),
                   limits=limits)
    return run_job(spec, tmp_path / "jobs", slice_, **kw)


def test_sandbox_basics(slice_, tmp_path):
    canary = tmp_path / "canary.txt"
    canary.write_text("x")
    script = (f"id -u; test -e {canary} && echo CANARY_VISIBLE; test -e {REPO} && echo REPO_VISIBLE; "
              f"test -e {os.path.expanduser('~')}/.claude && echo HOME_VISIBLE; touch /x 2>/dev/null && echo ROOT_RW; "
              "echo procs=$(ls -d /proc/[0-9]* | wc -l); env | sort | tr '\\n' ' '; echo; "
              "cat /proc/net/route | wc -l; echo ok > /job/out/r.json")
    r = _run(slice_, tmp_path, script, host_env_extra={"ZKQEC_LEAK": "should-not-pass"})
    out = r.stdout.decode()
    assert r.outcome == "completed", r.stderr
    for bad in ("CANARY_VISIBLE", "REPO_VISIBLE", "HOME_VISIBLE", "ROOT_RW", "ZKQEC_LEAK", "DBUS", "XDG_RUNTIME"):
        assert bad not in out
    assert int(out.split("procs=")[1].split()[0]) < 10
    assert [a["name"] for a in r.collected["accepted"]] == ["r.json"]
    assert (Path(r.job_dir) / "out" / "r.json").read_text() == "ok\n"
    assert not list((Path(r.job_dir) / "priv").glob("*")) if (Path(r.job_dir) / "priv").exists() else True


def test_tmpfs_size_and_wall_time(slice_, tmp_path):
    r = _run(slice_, tmp_path, "dd if=/dev/zero of=/job/work/f bs=1M count=64 2>&1 | grep -c 'No space'",
             tmpfs=16 << 20)
    assert r.stdout.strip() == b"1"
    r2 = _run(slice_, tmp_path, "sleep 60", limits=Limits(wall_seconds=2))
    assert r2.outcome == "timeout" and r2.wall_seconds < 15


def test_stdout_cap_kills(slice_, tmp_path):
    r = _run(slice_, tmp_path, "yes | head -c 5000000", limits=Limits(wall_seconds=30, stdout_cap=4096))
    assert r.outcome == "output_cap" and len(r.stdout) == 4096


def test_slice_limits_applied(slice_):
    lim = slice_.limits()
    assert lim["memory.max"] == str(2 * 1024**3) and lim["pids.max"] == "256"
    assert slice_.cgroup().name == slice_.name and slice_.cgroup().parent.name == "zkqec.slice"


def test_scope_argv_shape(slice_):
    a = slice_.scope_argv("j1", runtime_max_sec=5, memory_max=10, tasks_max=3)
    assert a[:4] == [CG.SYSTEMD_RUN, "--user", "--scope", "--quiet"] and "RuntimeMaxSec=5" in a and a[-1] == "--"
    with pytest.raises(ValueError):
        slice_.scope_argv("bad-id", runtime_max_sec=5, memory_max=10, tasks_max=3)
