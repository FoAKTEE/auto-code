"""Host bridge (token injection) and broker launcher bookkeeping -- offline."""
from __future__ import annotations

import base64
import json
import socket
import threading
from pathlib import Path

import pytest

from zkqec.broker import policy as P
from zkqec.broker.bridge import Bridge, rewrite_connect_head
from zkqec.broker.launcher import Broker, Ceilings, summarize_job


def test_rewrite_connect_head_injects_token_and_strips_client_auth():
    head = (b"CONNECT api.anthropic.com:443 HTTP/1.1\r\nHost: api.anthropic.com:443\r\n"
            b"Proxy-Authorization: Basic Zm9vOmJhcg==\r\nUser-Agent: x\r\n\r\n")
    out = rewrite_connect_head(head, "tok123")
    assert out.startswith(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\n")
    assert b"Zm9vOmJhcg==" not in out
    cred = base64.b64encode(b"zkqec:tok123")
    assert out.count(b"Proxy-Authorization") == 1 and cred in out and out.endswith(b"\r\n\r\n")


@pytest.mark.parametrize("head", [
    b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n",
    b"CONNECT example.com HTTP/1.1\r\n\r\n",
    b"CONNECT a b:443 HTTP/1.1\r\n\r\n",
    b"CONNECT example.com:443 HTTP/1.1\r\nbroken header\r\n\r\n",
    b"CONNECT example.com:443 HTTP/1.1\r\n",
])
def test_rewrite_connect_head_rejects(head):
    with pytest.raises(ValueError):
        rewrite_connect_head(head, "t")


def _upstream_server():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    got: dict[str, bytes] = {}

    def serve():
        conn, _ = srv.accept()
        data = b""
        while b"\r\n\r\n" not in data:
            data += conn.recv(1024)
        got["head"] = data
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        got["tail"] = conn.recv(1024)
        conn.sendall(b"pong")
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return srv, got, t


def test_bridge_end_to_end(tmp_path):
    srv, got, t = _upstream_server()
    sock = str(tmp_path / "b.sock")
    with Bridge(sock, srv.getsockname()[1], "secret-token") as br:
        c = socket.socket(socket.AF_UNIX)
        c.connect(sock)
        c.sendall(b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n\r\n")
        assert c.recv(1024).startswith(b"HTTP/1.1 200")
        c.sendall(b"ping")
        assert c.recv(16) == b"pong"
        c.close()
        t.join(5)
        assert base64.b64encode(b"zkqec:secret-token") in got["head"] and got["tail"] == b"ping"
        # a non-CONNECT request is refused by the bridge itself
        c2 = socket.socket(socket.AF_UNIX)
        c2.connect(sock)
        c2.sendall(b"GET http://x/ HTTP/1.1\r\nHost: x\r\n\r\n")
        assert c2.recv(1024).startswith(b"HTTP/1.1 403")
        c2.close()
        assert br.stats.connects_forwarded == 1 and br.stats.rejected == 1
    assert not Path(sock).exists()
    srv.close()


def test_broker_bookkeeping(tmp_path):
    b = Broker("unit", tmp_path)
    pol = P.load_policy(str(b.policy_path))
    assert pol.campaign == "unit" and pol.log_path.endswith("broker-log.jsonl")
    assert (b.root / "conf").stat().st_mode & 0o777 == 0o700
    tok = b.grant("job_a", "claude", ["claude-haiku-5-5"], Ceilings(1, 2, 100, 10, 60))
    files = list((b.state / "grants").iterdir())
    assert [f.name for f in files] == [P.token_digest(tok) + ".json"]
    assert tok.encode() not in files[0].read_bytes()          # only the digest names the grant
    assert P.load_grant(pol, P.token_digest(tok)).job_id == "job_a"
    b.revoke(tok)
    assert P.load_grant(pol, P.token_digest(tok)) is None
    assert b.blocked_reason("job_a") is None
    (b.state / "blocked" / "job_a.json").write_text(json.dumps({"reason": "tool_not_allowed:Bash"}))
    assert b.blocked_reason("job_a") == "tool_not_allowed:Bash"
    assert not b.dispatch_disabled()
    b.disable_dispatch("test")
    assert b.dispatch_disabled() and pol.dispatch_disabled()
    argv = b.argv(12345)
    for opt in ("flow_detail=0", "http3=false", "ssl_insecure=false", f"zkqec_policy={b.policy_path}"):
        assert opt in argv
    with pytest.raises(ValueError):
        Broker("bad id!", tmp_path)


def test_summarize_job_unknown_usage_is_not_zero():
    recs = [{"event": "request", "decision": "allow", "model_request": True, "tools": ["StructuredOutput"],
             "model": "m", "input_bytes": 10, "output_field": "present"},
            {"event": "response", "usage": None, "usage_status": "unknown"}]
    s = summarize_job(recs)
    assert s["usage"] == "unknown" and s["usage_missing"] == 1 and s["declared_tools"] == ["StructuredOutput"]
    recs[1]["usage"] = {"input_tokens": 3, "output_tokens": 4}
    assert summarize_job(recs)["usage"] == {"input_tokens": 3, "output_tokens": 4}
