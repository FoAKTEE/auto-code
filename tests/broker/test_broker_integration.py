"""The real mitmdump broker + addon against a local TLS fake upstream (no network, no credentials).

Covers: TLS interception with the per-campaign CA, HTTP body policy, WebSocket frame policy (including
Codex's `additional_tools` input item), ceilings, the dispatch switch, fail-closed, log-before-forward
ordering, log sanitization, and streamed (unbuffered) model responses with usage from the stream.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from zkqec.broker.bridge import Bridge
from zkqec.broker.launcher import MITMDUMP, Broker, Ceilings, summarize_job
from zkqec.broker.testing import FakeUpstream, ProxyClient, local_policy_overrides, make_test_pki

pytestmark = pytest.mark.skipif(not MITMDUMP.exists() or shutil.which("openssl") is None,
                                reason="needs .venv-broker (mitmproxy) and openssl")

MSG = {"model": "m1", "max_tokens": 50, "messages": [{"role": "user", "content": "x"}]}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("broker")
    pki = make_test_pki(d / "pki")
    up = FakeUpstream(pki["cert"], pki["key"]).start()
    b = Broker("itest", d, overrides=local_policy_overrides(up.port))
    b.start(extra_args=["--set", f"ssl_verify_upstream_trusted_ca={pki['ca']}"])
    tokens: list[str] = []
    yield d, b, up, tokens
    b.stop()
    up.stop()


def _client(env, jid, provider, model, ceil):
    d, b, up, tokens = env
    tok = b.grant(jid, provider, [model], ceil)
    tokens.append(tok)
    br = Bridge(str(d / f"{jid}.sock"), b.port, tok).start()
    return br, ProxyClient(str(d / f"{jid}.sock"), str(b.ca_cert), "localhost", up.port)


def test_http_allowed_then_ceiling(env):
    _, b, up, _ = env
    br, c = _client(env, "h_ok", "claude", "m1", Ceilings(1, 3, 4096, 100, 60))
    n0 = len(up.seen)
    assert c.request("POST", "/v1/messages", json.dumps(MSG).encode())[0] == 200
    assert c.request("POST", "/v1/messages", json.dumps(MSG).encode())[0] == 403
    c.close(); br.stop()
    assert len(up.seen) == n0 + 1
    recs = b.records("h_ok")
    usage = [r for r in recs if r.get("event") == "response"]
    assert usage and usage[0]["usage"] == {"input_tokens": 11, "output_tokens": 7, "cache_creation_input_tokens": 0}
    assert b.blocked_reason("h_ok") == "model_request_ceiling"


def test_http_tool_and_unknown_endpoint_fail_closed(env):
    _, b, up, _ = env
    br, c = _client(env, "h_tool", "claude", "m1", Ceilings(3, 6, 4096, 100, 60))
    n0 = len(up.seen)
    body = dict(MSG, tools=[{"name": "Bash", "input_schema": {}}])
    assert c.request("POST", "/v1/messages", json.dumps(body).encode())[0] == 403
    assert c.request("POST", "/v1/messages", json.dumps(MSG).encode())[0] == 403   # failed closed
    c.close(); br.stop()
    assert len(up.seen) == n0
    assert b.blocked_reason("h_tool") == "tool_not_allowed:Bash"


def test_quiet_block_does_not_fail_job(env):
    _, b, _, _ = env
    br, c = _client(env, "h_quiet", "claude", "m1", Ceilings(3, 6, 4096, 100, 60))
    assert c.request("GET", "/v1/mcp_servers")[0] == 403
    assert c.request("POST", "/v1/messages", json.dumps(MSG).encode())[0] == 200
    c.close(); br.stop()
    assert b.blocked_reason("h_quiet") is None


@pytest.mark.parametrize("gz", [False, True])
def test_model_response_is_streamed_not_buffered(env, gz):
    # m1-live-2: a buffered SSE body gave the CLI no first byte for ~180 s; it aborted and re-sent a
    # (possibly billed) request. The head and first event must reach the client before the stream ends.
    _, b, up, _ = env
    up.sse_delay, up.sse_gzip = 3.0, gz
    try:
        br, c = _client(env, f"h_stream_{int(gz)}", "claude", "m1", Ceilings(1, 3, 4096, 100, 60))
        status, data, t_head, t_done = c.request_timed("POST", "/v1/messages", json.dumps(MSG).encode())
        c.close(); br.stop()
    finally:
        up.sse_delay, up.sse_gzip = 0.0, False
    assert status == 200 and t_done >= 3.0
    assert t_head < 2.0
    if not gz:
        assert data.startswith(b"event: message_start") and b"message_delta" in data
    usage = [r for r in b.records(f"h_stream_{int(gz)}") if r.get("event") == "response"]
    assert len(usage) == 1 and usage[0]["usage_status"] == "reported"
    assert usage[0]["usage"] == {"input_tokens": 11, "output_tokens": 7, "cache_creation_input_tokens": 0}
    assert usage[0]["body_bytes"] == len(data) and usage[0]["stop_reason"] == "end_turn"


def test_client_abort_mid_stream_leaves_usage_unreported(env):
    # An aborted stream is a model request without a usage record: the charge stays uncertain (§K),
    # and the partial copy is logged for diagnosis only.
    _, b, up, _ = env
    up.sse_delay = 2.0
    try:
        br, c = _client(env, "h_abort", "claude", "m1", Ceilings(1, 3, 4096, 100, 60))
        status, _, t_head, _ = c.request_timed("POST", "/v1/messages", json.dumps(MSG).encode(),
                                               abort_after_head=True)
        time.sleep(3.0)                         # the upstream finishes into a closed client
        br.stop()
    finally:
        up.sse_delay = 0.0
    assert status == 200 and t_head < 1.5
    recs = b.records("h_abort")
    assert not [r for r in recs if r.get("event") == "response"]
    partial = [r for r in recs if r.get("event") == "response_incomplete"]
    assert len(partial) == 1 and partial[0]["usage_status"] == "incomplete"
    assert partial[0]["usage_partial"] == {"input_tokens": 11, "output_tokens": 1,
                                           "cache_creation_input_tokens": 0}
    s = summarize_job(recs)
    assert s["model_requests"] == 1 and s["usage_records"] == 0 and s["usage_missing"] == 1


def test_websocket_frames(env):
    _, b, up, _ = env
    br, c = _client(env, "w1", "codex", "g1", Ceilings(3, 6, 100000, 100, 60))
    assert c.websocket("/backend-api/codex/responses") == 101
    c.ws_send(json.dumps({"type": "response.create", "model": "g1", "input": [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}]}))
    op, data = c.ws_recv()
    assert json.loads(data)["type"] == "response.completed"
    n0 = len(up.seen)
    c.ws_send(json.dumps({"type": "response.create", "model": "g1", "input": [
        {"type": "additional_tools", "role": "developer",
         "tools": [{"type": "namespace", "name": "functions", "tools": [{"type": "function", "name": "exec"}]}]}]}))
    op, data = c.ws_recv()
    assert json.loads(data)["type"] == "error"
    c.close(); br.stop()
    assert len(up.seen) == n0
    assert b.blocked_reason("w1") == "tool_not_allowed:functions.exec"
    frames = [r for r in b.records("w1") if r.get("event") == "ws_frame"]
    assert [f["decision"] for f in frames] == ["allow", "block"]
    assert frames[1]["tools"] == ["functions.exec"]


def test_connect_rules(env):
    d, b, up, tokens = env
    tok = b.grant("c1", "claude", ["m1"], Ceilings(1, 2, 100, 10, 60))
    tokens.append(tok)
    with Bridge(str(d / "c1.sock"), b.port, tok):
        c = ProxyClient(str(d / "c1.sock"), str(b.ca_cert), "127.0.0.1", up.port)   # not the allowlisted name
        assert c.connect_status == 403
    assert b.blocked_reason("c1") == "host_not_allowlisted"
    with Bridge(str(d / "nobody.sock"), b.port, "not-a-granted-token"):
        c = ProxyClient(str(d / "nobody.sock"), str(b.ca_cert), "localhost", up.port)
        assert c.connect_status == 403


def test_dispatch_switch_and_log_hygiene(env):
    d, b, up, tokens = env
    b.disable_dispatch("test")
    tok = b.grant("s1", "claude", ["m1"], Ceilings(1, 2, 4096, 100, 60))
    tokens.append(tok)
    with Bridge(str(d / "s1.sock"), b.port, tok):
        c = ProxyClient(str(d / "s1.sock"), str(b.ca_cert), "localhost", up.port)
        assert c.connect_status == 403
    assert any(r.get("reason") == "dispatch_disabled" for r in b.records("s1"))
    b.switch_path.unlink()
    raw = b.log_path.read_bytes() + b.stdout_path.read_bytes()
    for t in tokens:
        assert t.encode() not in raw
    assert b"authorization" not in raw.lower()
    # log-before-forward: every response is preceded by an allow of the same job/path
    recs = b.records()
    for r in (x for x in recs if x.get("event") == "response"):
        assert any(a["seq"] < r["seq"] and a.get("decision") == "allow" and a.get("job_id") == r.get("job_id")
                   and a.get("path") == r.get("path") for a in recs)
