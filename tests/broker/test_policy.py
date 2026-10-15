"""Broker decision logic (contracts §J1, §K): pure unit tests, no mitmproxy, no network."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from zkqec.broker import policy as P

REPO = Path(__file__).resolve().parents[2]
BASE = REPO / "configs" / "broker" / "policy.json"


def campaign_policy(tmp_path: Path, **over) -> P.CampaignPolicy:
    doc = json.loads(BASE.read_text())
    doc.pop("_comment", None)
    doc.update(campaign="t", log_path=str(tmp_path / "log.jsonl"), state_dir=str(tmp_path / "state"),
               dispatch_disabled_path=str(tmp_path / "OFF"), deadline_unix=None)
    doc.update(over)
    return P.parse_policy(doc)


def grant(provider="claude", models=("claude-haiku-5-5",), mreq=2, treq=5, inb=100_000, out=1024, ttl=60) -> P.Grant:
    return P.Grant("job1", provider, frozenset(models), mreq, treq, inb, out, time.time() + ttl)


def anthropic_body(**over) -> bytes:
    d = {"model": "claude-haiku-5-5", "max_tokens": 1024, "stream": True,
         "messages": [{"role": "user", "content": "hi"},
                      {"role": "system", "content": [{"type": "text", "text": "x"}]}],
         "system": [{"type": "text", "text": "role"}],
         "tools": [{"name": "StructuredOutput", "input_schema": {}}, {"name": "DeferredToolPlaceholder"}]}
    d.update(over)
    return json.dumps(d).encode()


def codex_frame(**over) -> bytes:
    d = {"type": "response.create", "model": "gpt-6-astra", "stream": True, "store": False,
         "tool_choice": "auto", "parallel_tool_calls": False, "include": ["reasoning.encrypted_content"],
         "input": [{"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "x"}]},
                   {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "q"}]}]}
    d.update(over)
    return json.dumps(d).encode()


# -- bounded JSON ------------------------------------------------------------------------------------
def test_bounded_json_caps():
    assert P.bounded_json_loads(b'{"a": [1, 2]}', max_bytes=100) == {"a": [1, 2]}
    with pytest.raises(P.PolicyError, match="too_large"):
        P.bounded_json_loads(b"[" + b"1," * 100 + b"1]", max_bytes=50)
    with pytest.raises(P.PolicyError, match="too_deep"):
        P.bounded_json_loads(b"[" * 70 + b"]" * 70, max_bytes=1000)
    with pytest.raises(P.PolicyError, match="invalid"):
        P.bounded_json_loads(b"NaN", max_bytes=10)
    with pytest.raises(P.PolicyError, match="utf8"):
        P.bounded_json_loads(b"\xff", max_bytes=10)
    assert P.json_depth('{"s": "[[[[[["}') == 1   # brackets inside strings do not count


def test_labels_and_paths():
    assert P.strip_query("/v1/messages?beta=true") == "/v1/messages"
    assert P.sanitize_label("a\nb\x00c") == "a?b?c"
    assert len(P.sanitize_label("x" * 500)) == P.MAX_LABEL
    assert len(P.token_digest("t")) == 64


# -- policy parsing ----------------------------------------------------------------------------------
def test_base_policy_parses_and_is_strict(tmp_path):
    pol = campaign_policy(tmp_path)
    claude = pol.providers["claude"]
    assert claude.endpoint("api.anthropic.com", "POST", "/v1/messages?beta=true").kind == P.MODEL
    assert claude.endpoint("api.anthropic.com", "GET", "/v1/mcp_servers") is None
    assert claude.is_quiet_block("api.anthropic.com", "GET", "/v1/mcp_servers")
    assert claude.is_quiet_block("api.anthropic.com", "GET", "/api/oauth/profile")
    codex = pol.providers["codex"]
    assert codex.endpoint("chatgpt.com", "GET", "/backend-api/codex/responses").kind == P.WEBSOCKET
    assert codex.is_quiet_block("ab.chatgpt.com")
    assert codex.allowed_tools == frozenset()


@pytest.mark.parametrize("bad", [
    {"path": "/v1/messages"},                      # unanchored
    {"kind": "teleport"},                          # unknown kind
    {"kind": "model", "body": None},               # model without inspector
    {"host": "evil.example"},                      # host outside provider hosts
])
def test_policy_rejects_malformed_endpoints(tmp_path, bad):
    doc = json.loads(BASE.read_text())
    ep = dict(doc["providers"]["claude"]["endpoints"][0])
    ep.update(bad)
    doc["providers"]["claude"]["endpoints"] = [ep]
    doc.update(campaign="t", log_path="/x", state_dir="/y", dispatch_disabled_path="/z")
    with pytest.raises((P.PolicyError, KeyError)):
        P.parse_policy(doc)


def test_grant_validation():
    g = grant()
    assert P.parse_grant(g.to_json()) == g
    bad = g.to_json() | {"job_id": "../etc"}
    with pytest.raises(P.PolicyError):
        P.parse_grant(bad)


# -- Anthropic bodies ----------------------------------------------------------------------------------
def test_anthropic_allowed(tmp_path):
    prov = campaign_policy(tmp_path).providers["claude"]
    ins = P.inspect_anthropic_messages(anthropic_body(), grant(), prov, 1 << 20)
    assert ins.violations == []
    assert ins.tools == ("StructuredOutput", "DeferredToolPlaceholder")
    assert ins.max_output_tokens == 1024 and ins.output_field == "present" and ins.is_model_request


@pytest.mark.parametrize("over,reason", [
    ({"tools": [{"name": "Bash", "input_schema": {}}]}, "tool_not_allowed:Bash"),
    ({"tools": [{"type": "web_search_20250305", "name": "web_search"}]}, "server_tool:web_search"),
    ({"mcp_servers": [{"url": "https://x"}]}, "server_feature:mcp_servers"),
    ({"model": "claude-opus-5-5"}, "model_not_in_manifest"),
    ({"max_tokens": 128000}, "output_ceiling"),
    ({"messages": [{"role": "user", "content": [{"type": "document", "source": {}}]}]}, "content_block_not_allowed"),
    ({"messages": [{"role": "user", "content": [{"type": "tool_use", "name": "Bash", "input": {}}]}]},
     "tool_use_not_allowed:Bash"),
    ({"container": "c1"}, "server_feature:container"),
    ({"betas_extra": 1}, "body_key_not_allowed:betas_extra"),
])
def test_anthropic_violations(tmp_path, over, reason):
    prov = campaign_policy(tmp_path).providers["claude"]
    ins = P.inspect_anthropic_messages(anthropic_body(**over), grant(), prov, 1 << 20)
    assert any(v.startswith(reason) for v in ins.violations), ins.violations


def test_anthropic_unparseable_and_absent_output(tmp_path):
    prov = campaign_policy(tmp_path).providers["claude"]
    assert P.inspect_anthropic_messages(b"{not json", grant(), prov, 1 << 20).violations[0].startswith("body_unparseable")
    body = json.loads(anthropic_body())
    del body["max_tokens"]
    ins = P.inspect_anthropic_messages(json.dumps(body).encode(), grant(), prov, 1 << 20)
    assert ins.output_field == "absent" and ins.violations == []


# -- Codex frames ------------------------------------------------------------------------------------------
def test_codex_frame_allowed(tmp_path):
    prov = campaign_policy(tmp_path).providers["codex"]
    ins = P.inspect_ws_client_frame(codex_frame(), True, grant("codex", ("gpt-6-astra",)), prov, 1 << 20)
    assert ins.violations == [] and ins.is_model_request and ins.output_field == "absent"
    assert ins.include == ("reasoning.encrypted_content",)


def test_codex_additional_tools_item_blocked(tmp_path):
    """Codex 0.160.1 declares tools in an `additional_tools` input item, not a `tools` key."""
    prov = campaign_policy(tmp_path).providers["codex"]
    item = {"type": "additional_tools", "role": "developer", "id": "x", "tools": [
        {"type": "namespace", "name": "functions", "tools": [{"type": "function", "name": "exec"},
                                                             {"type": "function", "name": "wait"}]},
        {"type": "namespace", "name": "collaboration", "tools": [{"type": "function", "name": "spawn_agent"}]}]}
    frame = json.loads(codex_frame())
    frame["input"].insert(0, item)
    ins = P.inspect_ws_client_frame(json.dumps(frame).encode(), True, grant("codex", ("gpt-6-astra",)), prov, 1 << 20)
    assert ins.tools == ("functions.exec", "functions.wait", "collaboration.spawn_agent")
    assert ins.tools_key and "tool_not_allowed:functions.exec" in ins.violations


@pytest.mark.parametrize("frame,text,reason", [
    (codex_frame(tools=[{"type": "web_search"}]), True, "tool_not_allowed:web_search"),
    (codex_frame(tools=[]), True, None),
    (codex_frame(type="session.update"), True, "ws_frame_type_not_allowed"),
    (b"\x00\x01", False, "ws_binary_frame"),
    (b"{oops", True, "frame_unparseable"),
    (codex_frame(model="gpt-4"), True, "model_not_in_manifest"),
    (codex_frame(input=[{"type": "function_call_output", "output": "x"}]), True, "input_item_not_allowed"),
    (codex_frame(input=[{"type": "message", "role": "user", "content": [{"type": "input_image"}]}]), True,
     "input_part_not_allowed"),
    (codex_frame(max_output_tokens=10 ** 6), True, "output_ceiling"),
    (codex_frame(web_search_options={}), True, "body_key_not_allowed"),
])
def test_codex_frame_violations(tmp_path, frame, text, reason):
    prov = campaign_policy(tmp_path).providers["codex"]
    ins = P.inspect_ws_client_frame(frame, text, grant("codex", ("gpt-6-astra",)), prov, 1 << 20)
    if reason is None:
        assert ins.violations == []
    else:
        assert any(v.startswith(reason) for v in ins.violations), ins.violations


# -- decisions -----------------------------------------------------------------------------------------------
def test_decide_request_ceilings_and_counters(tmp_path):
    pol = campaign_policy(tmp_path)
    g, c = grant(mreq=1, treq=2, inb=10_000), P.Counters()
    ins = P.inspect_anthropic_messages(anthropic_body(), g, pol.providers["claude"], 1 << 20)
    d = P.decide_request(pol, g, c, "api.anthropic.com", "POST", "/v1/messages", ins)
    assert d.allowed and c.model_requests == 1 and c.total_requests == 1 and c.input_bytes == ins.input_bytes
    d2 = P.decide_request(pol, g, c, "api.anthropic.com", "POST", "/v1/messages", ins)
    assert d2.reason == "model_request_ceiling" and d2.fails_job and c.model_requests == 1
    big = P.Inspection(input_bytes=20_000, is_model_request=True)
    d3 = P.decide_request(pol, g, P.Counters(), "api.anthropic.com", "POST", "/v1/messages", big)
    assert d3.reason == "input_bound"


def test_decide_request_gates(tmp_path):
    pol = campaign_policy(tmp_path)
    g = grant()
    assert P.decide_request(pol, None, P.Counters(), "api.anthropic.com", "POST", "/v1/messages", None).reason == "unknown_job"
    failed = P.Counters(failed="x")
    assert P.decide_request(pol, g, failed, "api.anthropic.com", "POST", "/v1/messages", None).reason == "job_failed_closed"
    expired = grant(ttl=-1)
    assert P.decide_request(pol, expired, P.Counters(), "api.anthropic.com", "POST", "/v1/messages", None).reason == "grant_expired"
    d = P.decide_request(pol, g, P.Counters(), "api.anthropic.com", "GET", "/v1/models", None)
    assert d.reason == "endpoint_not_allowlisted" and d.fails_job
    q = P.decide_request(pol, g, P.Counters(), "api.anthropic.com", "GET", "/v1/mcp_servers", None)
    assert q.decision == P.BLOCK_EXPECTED and not q.fails_job
    (tmp_path / "OFF").write_text("{}")
    assert P.decide_request(pol, g, P.Counters(), "api.anthropic.com", "POST", "/v1/messages", None).reason == "dispatch_disabled"
    late = campaign_policy(tmp_path / "b", deadline_unix=time.time() - 1)
    assert P.decide_request(late, g, P.Counters(), "api.anthropic.com", "POST", "/v1/messages", None).reason == "campaign_deadline"


def test_decide_connect(tmp_path):
    pol = campaign_policy(tmp_path)
    g = grant("codex", ("gpt-6-astra",))
    assert P.decide_connect(pol, g, P.Counters(), "chatgpt.com", 443).allowed
    assert P.decide_connect(pol, g, P.Counters(), "chatgpt.com", 8443).reason == "host_not_allowlisted"
    assert P.decide_connect(pol, g, P.Counters(), "ab.chatgpt.com", 443).decision == P.BLOCK_EXPECTED
    other = P.decide_connect(pol, g, P.Counters(), "example.com", 443)
    assert other.reason == "host_not_allowlisted" and other.fails_job
    assert P.decide_connect(pol, grant(), P.Counters(), "chatgpt.com", 443).reason == "host_not_allowlisted"


# -- usage -------------------------------------------------------------------------------------------------------
def test_usage_parsing():
    sse = (b'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":2,'
           b'"cache_creation_input_tokens":899,"output_tokens":1}}}\n\n'
           b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"tool_use"},'
           b'"usage":{"output_tokens":52}}\n\n')
    u = P.anthropic_usage(sse, "text/event-stream")
    assert u == {"usage": {"input_tokens": 2, "cache_creation_input_tokens": 899, "output_tokens": 52},
                 "stop_reason": "tool_use"}
    assert P.anthropic_usage(b'{"type":"error"}', "application/json") is None
    frame = json.dumps({"type": "response.completed", "response": {"usage": {
        "input_tokens": 10, "input_tokens_details": {"cached_tokens": 3}, "output_tokens": 4}}}).encode()
    assert P.ws_server_usage(frame) == {"frame_type": "response.completed",
                                        "usage": {"input_tokens": 10, "cached_tokens": 3, "output_tokens": 4}}
    assert P.ws_server_usage(b'{"type":"response.output_text.delta"}') is None


def test_reservation_bound():
    g = P.Grant("j", "claude", frozenset(), 3, 8, 400_000, 1024, 0)
    r = P.reservation_bound(g, price_in_per_mtok=0.2, price_out_per_mtok=0.5, price_per_request=0.0)
    assert r == pytest.approx(3 * (400_000 * 0.2 + 1024 * 0.5) / 1e6)
