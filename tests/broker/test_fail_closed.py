"""The broker fails closed when it cannot inspect or durably log (Codex M1 milestone review B1;
contracts §J1, §L): the block (HTTP 403 / dropped frame) is in place before any logging; a request
is forwarded only after its decision record is completely written and fsync'ed; any exception in a
hook blocks the flow, fails the job and disables dispatch in memory, so later requests stay blocked
even when no marker or log record can be written.

The addon runs under mitmproxy itself: ``addon_harness.py`` is executed by ``.venv-broker``'s python
and invokes every hook through mitmproxy's exception wrapper (``safecall``) with ENOSPC, EIO, short
writes or a failing marker injected; one test drives the real ``mitmdump`` with an RLIMIT_FSIZE that
cuts the log short mid-record.
"""
from __future__ import annotations

import json
import resource
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from zkqec.broker.bridge import Bridge
from zkqec.broker.launcher import ADDON, MITMDUMP, Broker, Ceilings
from zkqec.broker.testing import FakeUpstream, ProxyClient, local_policy_overrides, make_test_pki

PY = MITMDUMP.with_name("python")
HARNESS = Path(__file__).with_name("addon_harness.py")
PORT = 8443
FAULTS = ("enospc", "eio_fsync", "short_enospc")
MSG = {"model": "m1", "max_tokens": 50, "messages": [{"role": "user", "content": "x"}]}

pytestmark = pytest.mark.skipif(not PY.exists(), reason="needs .venv-broker (mitmproxy)")

CLAUDE = ("claude", "m1", Ceilings(3, 6, 4096, 100, 60))
CODEX = ("codex", "g1", Ceilings(3, 6, 100000, 100, 60))
ONE = ("claude", "m1", Ceilings(1, 3, 4096, 100, 60))


def _scenarios() -> dict[str, tuple[dict, list[dict]]]:
    """name -> (job grants, steps). ``fault`` is armed for that step only."""
    sc: dict[str, tuple[dict, list[dict]]] = {
        "baseline": ({"ja": CLAUDE, "jw": CODEX},
                     [{"op": "connect", "conn": "a", "job": "ja"},
                      {"op": "post", "conn": "a", "body": "model"},
                      {"op": "connect", "conn": "w", "job": "jw"},
                      {"op": "ws_upgrade", "conn": "w"},
                      {"op": "ws", "conn": "w", "frame": "create"}]),
        "short_ok": ({"ja": CLAUDE},
                     [{"op": "connect", "conn": "a", "job": "ja"},
                      {"op": "post", "conn": "a", "body": "model", "fault": "short_ok"},
                      {"op": "post", "conn": "a", "body": "model"}]),
        "restart": ({"ja": CLAUDE, "jc": CLAUDE, "jd": CLAUDE},
                    [{"op": "connect", "conn": "a", "job": "ja"},
                     {"op": "post", "conn": "a", "body": "tool", "fault": "short_enospc"},
                     {"op": "restart"},
                     {"op": "connect", "conn": "c", "job": "jc"},          # still disabled
                     {"op": "enable"},
                     {"op": "connect", "conn": "a", "job": "ja"},          # still failed
                     {"op": "connect", "conn": "d", "job": "jd"},
                     {"op": "post", "conn": "d", "body": "model"}]),
    }
    for fault in FAULTS + ("marker_eio",):
        sc[f"tool_{fault}"] = ({"ja": CLAUDE, "jb": CLAUDE},
                               [{"op": "connect", "conn": "a", "job": "ja"},
                                {"op": "connect", "conn": "b", "job": "jb"},
                                {"op": "post", "conn": "a", "body": "tool", "fault": fault},
                                {"op": "post", "conn": "a", "body": "model"},
                                {"op": "post", "conn": "b", "body": "model"}])
        sc[f"ws_tool_{fault}"] = ({"jw": CODEX, "jb": CLAUDE},
                                  [{"op": "connect", "conn": "w", "job": "jw"},
                                   {"op": "connect", "conn": "b", "job": "jb"},
                                   {"op": "ws_upgrade", "conn": "w"},
                                   {"op": "ws", "conn": "w", "frame": "ws_tool", "fault": fault},
                                   {"op": "ws", "conn": "w", "frame": "create"},
                                   {"op": "post", "conn": "b", "body": "model"}])
    for fault in FAULTS:
        sc[f"model_{fault}"] = ({"ja": CLAUDE, "jb": CLAUDE},
                                [{"op": "connect", "conn": "a", "job": "ja"},
                                 {"op": "connect", "conn": "b", "job": "jb"},
                                 {"op": "post", "conn": "a", "body": "model", "fault": fault},
                                 {"op": "post", "conn": "a", "body": "model"},
                                 {"op": "post", "conn": "b", "body": "model"}])
        sc[f"ceiling_{fault}"] = ({"ja": ONE, "jb": CLAUDE},
                                  [{"op": "connect", "conn": "a", "job": "ja"},
                                   {"op": "connect", "conn": "b", "job": "jb"},
                                   {"op": "post", "conn": "a", "body": "model"},
                                   {"op": "post", "conn": "a", "body": "model", "fault": fault},
                                   {"op": "post", "conn": "b", "body": "model"}])
        sc[f"ws_{fault}"] = ({"jw": CODEX, "jb": CLAUDE},
                             [{"op": "connect", "conn": "w", "job": "jw"},
                              {"op": "connect", "conn": "b", "job": "jb"},
                              {"op": "ws_upgrade", "conn": "w"},
                              {"op": "ws", "conn": "w", "frame": "create", "fault": fault},
                              {"op": "ws", "conn": "w", "frame": "create"},
                              {"op": "post", "conn": "b", "body": "model"}])
    return sc


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    root = tmp_path_factory.mktemp("harness")
    scenarios = []
    for name, (grants, steps) in _scenarios().items():
        b = Broker(f"h-{name}".replace("_", "-"), root / name, overrides=local_policy_overrides(PORT))
        tokens = {job: b.grant(job, prov, [model], ceil) for job, (prov, model, ceil) in grants.items()}
        scenarios.append({"name": name, "steps": steps, "env": {
            "policy": str(b.policy_path), "state": str(b.state), "log": str(b.log_path),
            "switch": str(b.switch_path), "tokens": tokens}})
    spec = root / "spec.json"
    spec.write_text(json.dumps({"addon": str(ADDON), "host": "localhost", "port": PORT,
                                "scenarios": scenarios}))
    proc = subprocess.run([str(PY), "-I", str(HARNESS), str(spec)], capture_output=True, text=True,
                          timeout=300, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _forwarded(result: dict) -> list[bool]:
    return [s["forwarded"] for s in result["steps"] if s["op"] in ("post", "ws")]


def test_healthy_requests_are_forwarded_and_logged(harness):
    r = harness["baseline"]
    assert _forwarded(r) == [True, True]
    assert not r["disabled"] and not any(r["failed"].values()) and not r["switch"]
    assert r["log_bad_lines"] == 0 and r["log_tail_newline"]


def test_short_writes_are_completed_before_forwarding(harness):
    r = harness["short_ok"]
    assert _forwarded(r) == [True, True]
    assert r["log_bad_lines"] == 0 and r["log_tail_newline"]
    allowed = [x for x in r["log"] if x["event"] == "request" and x["decision"] == "allow"]
    assert [x["job_id"] for x in allowed] == ["ja", "ja"]


@pytest.mark.parametrize("fault", FAULTS + ("marker_eio",))
@pytest.mark.parametrize("kind", ["tool", "ws_tool"])
def test_forbidden_request_stays_blocked_when_logging_or_marking_fails(harness, kind, fault):
    r = harness[f"{kind}_{fault}"]
    # the forbidden request, a later request of the job and another job's request: all blocked
    assert _forwarded(r) == [False, False, False]
    assert r["failed"].get("jw" if kind == "ws_tool" else "ja") and r["disabled"]


@pytest.mark.parametrize("fault", FAULTS)
@pytest.mark.parametrize("kind", ["model", "ws"])
def test_allowed_request_is_not_forwarded_without_a_durable_record(harness, kind, fault):
    r = harness[f"{kind}_{fault}"]
    assert _forwarded(r) == [False, False, False]
    job = "jw" if kind == "ws" else "ja"
    assert r["failed"].get(job) and r["disabled"]
    # no request or frame of the faulted job is on record as allowed (the upgrade is not a frame);
    # after a failed fsync the written record may survive: it over-counts, never under-counts
    allowed = [x for x in r["log"] if x["job_id"] == job and x["decision"] == "allow"
               and x["event"] == ("ws_frame" if kind == "ws" else "request")]
    assert len(allowed) == (1 if fault == "eio_fsync" else 0)


@pytest.mark.parametrize("fault", FAULTS)
def test_ceiling_breach_is_blocked_when_logging_fails(harness, fault):
    r = harness[f"ceiling_{fault}"]
    assert _forwarded(r) == [True, False, False]
    assert r["failed"].get("ja") and r["disabled"]


def test_restart_after_a_torn_log_keeps_the_job_failed_and_the_log_parseable(harness):
    r = harness["restart"]
    steps = r["steps"]
    assert steps[1]["forwarded"] is False                     # the forbidden request
    assert steps[3]["forwarded"] is False                     # dispatch stays disabled on disk
    assert steps[5]["forwarded"] is False                     # the failed job stays failed
    assert steps[6]["forwarded"] and steps[7]["forwarded"]    # a healthy job proceeds
    assert r["failed"].get("ja") and "ja.json" in r["markers"]
    assert r["log_bad_lines"] == 1 and r["log_tail_newline"]  # only the torn record is unreadable
    assert [x["event"] for x in r["log"]].count("broker_start") == 2


# --- the real mitmdump ------------------------------------------------------------------------------
@pytest.mark.skipif(not MITMDUMP.exists() or shutil.which("openssl") is None,
                    reason="needs .venv-broker (mitmproxy) and openssl")
def test_real_broker_blocks_when_its_log_is_cut_short(tmp_path):
    """RLIMIT_FSIZE on the running mitmdump: the next log record is written short, then EFBIG."""
    pki = make_test_pki(tmp_path / "pki")
    up = FakeUpstream(pki["cert"], pki["key"]).start()
    b = Broker("fsize", tmp_path, overrides=local_policy_overrides(up.port))
    b.start(extra_args=["--set", f"ssl_verify_upstream_trusted_ca={pki['ca']}"])
    body = json.dumps(MSG).encode()
    try:
        ta = b.grant("fa", "claude", ["m1"], Ceilings(3, 6, 4096, 100, 60))
        tb = b.grant("fb", "claude", ["m1"], Ceilings(3, 6, 4096, 100, 60))
        with Bridge(str(tmp_path / "a.sock"), b.port, ta), Bridge(str(tmp_path / "b.sock"), b.port, tb):
            ca = ProxyClient(str(tmp_path / "a.sock"), str(b.ca_cert), "localhost", up.port)
            cb = ProxyClient(str(tmp_path / "b.sock"), str(b.ca_cert), "localhost", up.port)
            assert ca.request("POST", "/v1/messages", body)[0] == 200
            deadline = time.time() + 10
            while not [r for r in b.records("fa") if r.get("event") == "response"]:
                assert time.time() < deadline
                time.sleep(0.05)
            n0 = len(up.seen)
            soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
            resource.prlimit(b.proc.pid, resource.RLIMIT_FSIZE,
                             (b.log_path.stat().st_size + 16, hard))
            try:
                status = ca.request("POST", "/v1/messages", body)[0]
            finally:
                resource.prlimit(b.proc.pid, resource.RLIMIT_FSIZE, (soft, hard))
            assert status == 403
            assert ca.request("POST", "/v1/messages", body)[0] == 403
            assert cb.request("POST", "/v1/messages", body)[0] == 403
            ca.close(); cb.close()
        assert len(up.seen) == n0
        assert b.blocked_reason("fa") is not None and b.dispatch_disabled()
        assert not b.log_path.read_bytes().endswith(b"\n")          # the torn record
    finally:
        b.stop()
        up.stop()
