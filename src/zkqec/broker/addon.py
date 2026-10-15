"""mitmproxy addon: the zkqec request-inspecting egress broker (contracts §J1, §K, §L, §M).

Loaded by ``mitmdump -s addon.py --set zkqec_policy=<campaign policy JSON>`` inside ``.venv-broker``.
Imports only the stdlib, mitmproxy and the sibling ``policy.py`` (loaded by file path, so the addon
needs no zkqec installation).

Per connection: the CONNECT request must carry ``Proxy-Authorization: Basic base64("zkqec:<token>")``,
which the host-side bridge injects (the sandbox never sees the token). The token's SHA-256 selects the
job's grant. The header is removed and never logged.

Per upstream request / WebSocket client frame: the decision is appended to the JSONL broker log and
fsync'ed **before** the request is forwarded (§L). Logged fields are sanitized metadata only -- no
header, no body, no query string. A violation answers 403 (HTTP) or drops the frame and injects an
error frame (WebSocket), and fails the job closed: a marker is written to ``<state>/blocked/<job>.json``
and every later request of that job is blocked.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import threading
import time
from typing import Any

from mitmproxy import ctx, http

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("_zkqec_broker_policy", os.path.join(_here, "policy.py"))
assert _spec is not None and _spec.loader is not None
P = importlib.util.module_from_spec(_spec)
sys.modules["_zkqec_broker_policy"] = P
_spec.loader.exec_module(P)

BLOCK_BODY = b"zkqec broker: request blocked by policy\n"


class BrokerLog:
    """Append-only JSONL log; every record is flushed and fsync'ed before the caller proceeds."""

    def __init__(self, path: str, campaign: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o600)
        self._campaign = campaign
        self._lock = threading.Lock()
        self._seq = 0
        try:
            with open(path, "rb") as fh:
                self._seq = sum(1 for _ in fh)
        except OSError:
            pass

    def write(self, rec: dict[str, Any], durable: bool = True) -> None:
        with self._lock:
            self._seq += 1
            full = {"seq": self._seq, "ts": round(time.time(), 6), "campaign": self._campaign}
            full.update(rec)
            line = (json.dumps(full, sort_keys=True, separators=(",", ":")) + "\n").encode()
            os.write(self._fd, line)
            if durable:
                os.fsync(self._fd)


def _proxy_token(headers: http.Headers) -> str | None:
    value = headers.get("proxy-authorization")
    if not value:
        return None
    parts = value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return None
    try:
        user, _, token = base64.b64decode(parts[1], validate=True).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return None
    return token if user == "zkqec" and token else None


class ZkqecBroker:
    def __init__(self) -> None:
        self.policy: Any = None
        self.log: BrokerLog | None = None
        self.counters: dict[str, Any] = {}
        self.conn_job: dict[str, str] = {}      # client connection id -> job id
        self.conn_host: dict[str, str] = {}     # client connection id -> CONNECT target host
        self.grants: dict[str, Any] = {}        # job id -> grant (as loaded at CONNECT)

    # -- lifecycle -------------------------------------------------------------------------------
    def load(self, loader: Any) -> None:
        loader.add_option("zkqec_policy", str, "", "Path to the zkqec campaign broker policy JSON.")

    def configure(self, updated: set[str]) -> None:
        if "zkqec_policy" not in updated:
            return
        path = ctx.options.zkqec_policy
        if not path:
            raise ValueError("zkqec_policy is required")
        self.policy = P.load_policy(path)
        os.makedirs(self.policy.grants_dir, exist_ok=True)
        os.makedirs(self.policy.blocked_dir, exist_ok=True)
        self.log = BrokerLog(self.policy.log_path, self.policy.campaign)
        self._replay_counters()
        self.log.write({"event": "broker_start", "policy_sha256": self.policy.sha256,
                        "pid": os.getpid()})

    def _replay_counters(self) -> None:
        """Rebuild per-job counters from the authoritative log (survives a broker restart)."""
        try:
            fh = open(self.policy.log_path, "rb")
        except OSError:
            return
        with fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                job = rec.get("job_id")
                if not job:
                    continue
                c = self.counters.setdefault(job, P.Counters())
                if rec.get("event") in ("request", "ws_frame") and rec.get("decision") == P.ALLOW:
                    c.total_requests += 1
                    if rec.get("model_request"):
                        c.model_requests += 1
                        c.input_bytes += int(rec.get("input_bytes") or 0)
                if rec.get("event") == "job_failed":
                    c.failed = rec.get("reason") or "replayed_failure"

    # -- helpers ---------------------------------------------------------------------------------
    def _counters(self, job_id: str | None) -> Any:
        if job_id is None:
            return None
        return self.counters.setdefault(job_id, P.Counters())

    def _fail_job(self, job_id: str | None, reason: str) -> None:
        if not job_id:
            return
        c = self._counters(job_id)
        if c.failed:
            return
        c.failed = reason
        assert self.log is not None
        self.log.write({"event": "job_failed", "job_id": job_id, "reason": reason})
        path = os.path.join(self.policy.blocked_dir, job_id + ".json")
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"job_id": job_id, "reason": reason, "ts": time.time()}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def _job_of(self, flow: Any) -> tuple[str | None, Any]:
        job_id = self.conn_job.get(flow.client_conn.id)
        return job_id, (self.grants.get(job_id) if job_id else None)

    def _apply(self, job_id: str | None, decision: Any) -> None:
        if decision.decision == P.BLOCK and decision.fails_job:
            self._fail_job(job_id, decision.reason)

    # -- CONNECT ---------------------------------------------------------------------------------
    def http_connect(self, flow: http.HTTPFlow) -> None:
        assert self.log is not None
        token = _proxy_token(flow.request.headers)
        flow.request.headers.pop("proxy-authorization", None)
        grant = None
        if token:
            try:
                grant = P.load_grant(self.policy, P.token_digest(token))
            except (P.PolicyError, ValueError, KeyError):
                grant = None
        job_id = grant.job_id if grant else None
        if grant is not None:
            self.grants[job_id] = grant
        host, port = flow.request.host, flow.request.port
        d = P.decide_connect(self.policy, grant, self._counters(job_id), host, port)
        self.log.write({"event": "connect", "job_id": job_id, "host": P.sanitize_label(host), "port": port,
                        "token_present": token is not None, "decision": d.decision, "reason": d.reason})
        self._apply(job_id, d)
        if d.allowed:
            self.conn_job[flow.client_conn.id] = job_id
            self.conn_host[flow.client_conn.id] = host
        else:
            flow.response = http.Response.make(403, BLOCK_BODY, {"content-type": "text/plain"})

    def client_disconnected(self, client: Any) -> None:
        self.conn_job.pop(client.id, None)
        self.conn_host.pop(client.id, None)

    # -- HTTP ------------------------------------------------------------------------------------
    def request(self, flow: http.HTTPFlow) -> None:
        assert self.log is not None
        if flow.request.method == "CONNECT":
            return
        job_id, grant = self._job_of(flow)
        req = flow.request
        host, method, path = req.pretty_host, req.method, P.strip_query(req.path)
        counters = self._counters(job_id)
        ins = None
        endpoint = None
        if grant is not None and host != self.conn_host.get(flow.client_conn.id):
            ins = P.Inspection(violations=["host_header_differs_from_connect_target"])
        elif grant is not None and grant.provider in self.policy.providers:
            prov = self.policy.providers[grant.provider]
            endpoint = prov.endpoint(host, method, path)
            if endpoint is not None and endpoint.kind == P.MODEL:
                body = req.get_content(strict=False) or b""
                if endpoint.body == "anthropic_messages":
                    ins = P.inspect_anthropic_messages(body, grant, prov, self.policy.max_body_bytes)
                else:
                    ins = P.inspect_openai_responses(body, grant, prov, self.policy.max_body_bytes)
            elif endpoint is not None and endpoint.kind == P.WEBSOCKET:
                if req.headers.get("upgrade", "").lower() != "websocket":
                    ins = P.Inspection(violations=["websocket_endpoint_without_upgrade"])
            elif endpoint is not None and endpoint.kind == P.AUX and req.raw_content:
                ins = P.Inspection(input_bytes=len(req.raw_content), violations=["aux_endpoint_with_body"])
        if counters is None:
            d = P.Decision(P.BLOCK, "unknown_job", False)
        else:
            d = P.decide_request(self.policy, grant, counters, host, method, path, ins, endpoint=endpoint)
        rec = {"event": "request", "job_id": job_id, "host": P.sanitize_label(host), "method": method,
               "path": P.sanitize_label(path, 160), "http_version": req.http_version,
               "kind": endpoint.kind if endpoint else None, "decision": d.decision, "reason": d.reason,
               "body_bytes": len(req.raw_content or b"")}
        if ins is not None:
            rec.update(ins.summary())
        if counters is not None:
            rec.update({"model_requests_after": counters.model_requests,
                        "total_requests_after": counters.total_requests})
        self.log.write(rec)  # durable BEFORE forwarding
        self._apply(job_id, d)
        if not d.allowed:
            flow.response = http.Response.make(403, BLOCK_BODY, {"content-type": "text/plain"})
            return
        flow.metadata["zkqec_job"] = job_id
        flow.metadata["zkqec_kind"] = endpoint.kind if endpoint else None
        flow.metadata["zkqec_body"] = endpoint.body if endpoint else None

    def response(self, flow: http.HTTPFlow) -> None:
        assert self.log is not None
        job_id = flow.metadata.get("zkqec_job")
        if job_id is None or flow.response is None:
            return
        rec: dict[str, Any] = {"event": "response", "job_id": job_id, "host": P.sanitize_label(flow.request.pretty_host),
                               "path": P.sanitize_label(P.strip_query(flow.request.path), 160),
                               "status": flow.response.status_code,
                               "body_bytes": len(flow.response.raw_content or b"")}
        if flow.metadata.get("zkqec_kind") == P.MODEL:
            content = flow.response.get_content(strict=False) or b""
            ctype = flow.response.headers.get("content-type")
            if flow.metadata.get("zkqec_body") == "anthropic_messages":
                u = P.anthropic_usage(content, ctype)
            else:
                u = None
                for line in content.decode("utf-8", "replace").splitlines():
                    if line.startswith("data:") and "response.completed" in line:
                        u = P.ws_server_usage(line[5:].strip().encode())
            rec["usage"] = u["usage"] if u else None
            rec["stop_reason"] = u.get("stop_reason") if u else None
            rec["usage_status"] = "reported" if u else "unknown"
        self.log.write(rec)

    def error(self, flow: http.HTTPFlow) -> None:
        if self.log is None:
            return
        job_id = self.conn_job.get(flow.client_conn.id) if flow.client_conn else None
        msg = flow.error.msg if flow.error else ""
        self.log.write({"event": "flow_error", "job_id": job_id,
                        "host": P.sanitize_label(flow.request.pretty_host if flow.request else None),
                        "error": P.sanitize_label(msg, 160)})

    def tls_failed_client(self, data: Any) -> None:
        if self.log is None:
            return
        job_id = self.conn_job.get(data.context.client.id)
        self.log.write({"event": "tls_failed_client", "job_id": job_id,
                        "sni": P.sanitize_label(data.context.client.sni),
                        "error": P.sanitize_label(str(data.conn.error), 160)})

    # -- WebSocket -------------------------------------------------------------------------------
    def websocket_start(self, flow: http.HTTPFlow) -> None:
        assert self.log is not None
        self.log.write({"event": "ws_start", "job_id": flow.metadata.get("zkqec_job"),
                        "host": P.sanitize_label(flow.request.pretty_host),
                        "path": P.sanitize_label(P.strip_query(flow.request.path), 160)}, durable=False)

    def websocket_message(self, flow: http.HTTPFlow) -> None:
        assert self.log is not None and flow.websocket is not None
        msg = flow.websocket.messages[-1]
        job_id = flow.metadata.get("zkqec_job")
        grant = self.grants.get(job_id) if job_id else None
        host, path = flow.request.pretty_host, P.strip_query(flow.request.path)
        if not msg.from_client:
            u = P.ws_server_usage(msg.content)
            if u is not None:
                self.log.write({"event": "ws_server", "job_id": job_id, "host": P.sanitize_label(host),
                                "path": P.sanitize_label(path, 160), "frame_type": u["frame_type"],
                                "usage": u["usage"], "frame_bytes": len(msg.content),
                                "usage_status": "reported" if u["usage"] is not None else "unknown"})
            return
        counters = self._counters(job_id)
        ins = None
        endpoint = None
        if grant is not None and grant.provider in self.policy.providers:
            prov = self.policy.providers[grant.provider]
            endpoint = prov.endpoint(host, "GET", path)
            if endpoint is not None and endpoint.kind == P.WEBSOCKET:
                ins = P.inspect_ws_client_frame(msg.content, msg.is_text, grant, prov, self.policy.max_body_bytes)
            else:
                ins = P.Inspection(violations=["ws_frame_on_non_websocket_endpoint"])
        if counters is None:
            d = P.Decision(P.BLOCK, "unknown_job", False)
        else:
            d = P.decide_request(self.policy, grant, counters, host, "GET", path, ins, endpoint=endpoint)
        rec = {"event": "ws_frame", "job_id": job_id, "host": P.sanitize_label(host),
               "path": P.sanitize_label(path, 160), "decision": d.decision, "reason": d.reason,
               "frame_bytes": len(msg.content)}
        if ins is not None:
            rec.update(ins.summary())
        if counters is not None:
            rec.update({"model_requests_after": counters.model_requests,
                        "total_requests_after": counters.total_requests})
        self.log.write(rec)  # durable BEFORE forwarding
        self._apply(job_id, d)
        if not d.allowed:
            msg.drop()
            err = json.dumps({"type": "error", "status": 403,
                              "error": {"type": "zkqec_policy", "code": "policy_blocked",
                                        "message": "zkqec broker: frame blocked by policy"}})
            try:
                ctx.master.commands.call("inject.websocket", flow, True, err.encode(), True)
            except Exception:  # noqa: BLE001 - best effort; the frame is already dropped
                pass

    def websocket_end(self, flow: http.HTTPFlow) -> None:
        if self.log is None:
            return
        ws = flow.websocket
        self.log.write({"event": "ws_end", "job_id": flow.metadata.get("zkqec_job"),
                        "close_code": ws.close_code if ws else None,
                        "closed_by_client": ws.closed_by_client if ws else None}, durable=False)


addons = [ZkqecBroker()]
