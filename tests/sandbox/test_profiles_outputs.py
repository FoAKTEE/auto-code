"""Launch profiles, bwrap argv, output caps and the nsexec collection logic -- pure, offline."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from zkqec.sandbox import nsexec
from zkqec.sandbox.outputs import CappedStream, OutputRejected, load_json_file_capped, parse_json_capped, read_regular_capped
from zkqec.sandbox.profiles import (SB_CA, SB_HOME, JobLayout, agent_call_profile, build_bwrap_argv,
                                    program_profile)

REPO = Path(__file__).resolve().parents[2]
HOME = os.path.expanduser("~")


def _argv(tmp_path, profile, env=None, ca=None):
    lay = JobLayout(tmp_path / "job")
    return build_bwrap_argv(profile, lay, env or {"HOME": SB_HOME, "PATH": "/usr/bin"}, ["/bin/true"], ca_cert=ca)


@pytest.mark.parametrize("provider", ["claude", "codex", "none"])
def test_agent_call_argv_has_isolation_flags(tmp_path, provider):
    a = _argv(tmp_path, agent_call_profile(provider), ca="/x/ca.pem")
    for flag in ("--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-net",
                 "--new-session", "--die-with-parent", "--clearenv"):
        assert flag in a
    assert a[a.index("--proc") + 1] == "/proc" and a[a.index("--dev") + 1] == "/dev"
    assert ["--remount-ro", "/"] == a[a.index("--remount-ro"):a.index("--remount-ro") + 2]
    assert ["--ro-bind", "/x/ca.pem", SB_CA] == a[a.index("/x/ca.pem") - 1:a.index("/x/ca.pem") + 2]
    sources = [a[i + 1] for i, x in enumerate(a) if x in ("--bind", "--ro-bind")]
    for s in sources:  # nothing from the repo (except the static entry script) or from $HOME config
        assert not s.startswith(str(REPO)) or s.endswith(("entry.sh",)) or "/job/" in s or s.startswith(str(tmp_path))
        assert not s.startswith(os.path.join(HOME, ".claude")) and not s.startswith(os.path.join(HOME, ".codex"))
        assert s not in (HOME, "/", "/run", "/tmp", "/etc", "/home", "/data")
    assert "/run/user" not in " ".join(a) and "/var/run" not in a


def test_codex_profile_binds_real_binary_not_wrapper(tmp_path):
    a = " ".join(_argv(tmp_path, agent_call_profile("codex")))
    assert "@openai/codex" in a and "/.local/bin/codex" not in a
    assert "/opt/zkqec/node/bin/node" in a


def test_env_allowlist_enforced(tmp_path):
    with pytest.raises(ValueError, match="not allowlisted"):
        _argv(tmp_path, agent_call_profile("claude"), env={"HOME": SB_HOME, "AWS_SECRET_ACCESS_KEY": "x"})
    with pytest.raises(ValueError, match="not allowlisted|no network"):
        _argv(tmp_path, program_profile(), env={"HOME": SB_HOME, "HTTPS_PROXY": "http://x"})


def test_program_profile_has_no_network_bridge(tmp_path):
    a = _argv(tmp_path, program_profile())
    assert "/run/zkqec" not in a and SB_CA not in a and "--unshare-net" in a
    assert dict(program_profile().rlimits)["CPU"] > 0


def test_profile_hash_is_stable_and_sensitive():
    p = agent_call_profile("claude")
    assert p.sha256() == agent_call_profile("claude").sha256()
    assert p.sha256() != agent_call_profile("claude", tmpfs_bytes=1).sha256()
    assert json.dumps(p.to_json())  # serializable for the manifest


def test_job_layout_permissions(tmp_path):
    lay = JobLayout(tmp_path / "j")
    lay.create()
    for d in (lay.job_dir, lay.in_dir, lay.priv_dir, lay.bridge_dir, lay.out_dir):
        assert d.stat().st_mode & 0o777 == 0o700
    assert "agent" in (lay.etc_dir / "passwd").read_text()


# -- trusted-side output caps ------------------------------------------------------------------------------
def test_read_regular_capped(tmp_path):
    f = tmp_path / "a.json"
    f.write_text('{"x": 1}')
    assert load_json_file_capped(f, max_bytes=100) == {"x": 1}
    with pytest.raises(OutputRejected, match="too_large"):
        read_regular_capped(f, 3)
    (tmp_path / "l").symlink_to(f)
    with pytest.raises(OutputRejected, match="not_regular"):
        read_regular_capped(tmp_path / "l", 100)
    os.mkfifo(tmp_path / "p")
    with pytest.raises(OutputRejected, match="not_regular"):
        read_regular_capped(tmp_path / "p", 100)
    with pytest.raises(OutputRejected, match="not_regular"):
        read_regular_capped(tmp_path, 100)
    with pytest.raises(OutputRejected, match="missing"):
        read_regular_capped(tmp_path / "nope", 100)


@pytest.mark.parametrize("data", [b"[" * 40 + b"]" * 40, b'{"a": "' + b"x" * 200 + b'"}', b"\xff", b"NaN", b"{"])
def test_parse_json_capped_rejects(data):
    with pytest.raises(OutputRejected):
        parse_json_capped(data, max_bytes=100, max_depth=32)


def test_capped_stream_overflow(tmp_path):
    r, w = os.pipe()
    hits = []
    cs = CappedStream(os.fdopen(r, "rb"), 10, lambda: hits.append(1))
    os.write(w, b"x" * 25)
    os.close(w)
    assert cs.join(5) == b"x" * 10 and cs.total == 25 and cs.overflowed and hits == [1]


# -- nsexec helpers (run in-process; no namespaces needed) -------------------------------------------------------
def test_nsexec_collect(tmp_path):
    src, dst = tmp_path / "out", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.json").write_text("{}")
    (src / "big.bin").write_bytes(b"x" * 2000)
    (src / "link").symlink_to("/etc/passwd")
    os.mkfifo(src / "fifo")
    (src / "sub").mkdir()
    (src / "bad name").write_text("x")
    for i in range(5):
        (src / f"f{i}").write_text(str(i))
    rep = nsexec.collect(str(src), {"to": str(dst), "max_files": 3, "max_file_bytes": 1000, "max_total_bytes": 10_000})
    acc = [a["name"] for a in rep["accepted"]]
    rej = {r["name"]: r["reason"] for r in rep["rejected"]}
    assert acc == ["a.json", "f0", "f1"]
    assert rej["big.bin"] == "file_too_large" and rej["link"] == "not_regular_file"
    assert rej["fifo"] == "not_regular_file" and rej["sub"] == "not_regular_file" and rej[None] == "bad_name"
    assert rej["f4"] == "too_many_files"
    assert sorted(p.name for p in dst.iterdir()) == ["a.json", "f0", "f1"]
    assert all((dst / n).stat().st_mode & 0o777 == 0o600 for n in acc)


def test_nsexec_copy_credential(tmp_path):
    src = tmp_path / "cred.json"
    src.write_text('{"k": "v"}')
    dest = tmp_path / "home" / ".claude" / ".credentials.json"
    nsexec.copy_credential(str(src), str(dest), 0o600, 1000)
    assert dest.read_text() == '{"k": "v"}' and dest.stat().st_mode & 0o777 == 0o600
    assert dest.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(OSError):
        nsexec.copy_credential(str(src), str(tmp_path / "x"), 0o600, 3)       # over the cap
    (tmp_path / "ln").symlink_to(src)
    with pytest.raises(OSError):
        nsexec.copy_credential(str(tmp_path / "ln"), str(tmp_path / "y"), 0o600, 1000)   # no symlinks
