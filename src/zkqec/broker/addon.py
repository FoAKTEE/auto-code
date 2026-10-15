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

Fail closed (Codex M1 milestone review B1). mitmproxy logs and swallows an exception raised by a hook
and lets the flow continue, so every hook guards itself: the 403 response (HTTP, CONNECT) or the drop
(client frame) is in place before anything is inspected or logged, and it is lifted only after the
allowing record is completely written (short writes are continued) and fsync'ed. Any exception fails
the job and disables all dispatch **in memory** first, then tries to make that durable (log record,
job marker, ``DISPATCH_DISABLED`` switch); a log that failed once refuses every later record until
the broker restarts. A restarted broker closes a torn last record with a newline and reloads the job
markers, so a job failed while the log was unwritable stays failed.

Model responses are streamed to the client unchanged as they arrive, never buffered: a client waiting
for its first byte must not time out and re-send a billed request (m1-live-2). A bounded copy of the
stream is kept and parsed for usage at end of stream; a stream that ends early is logged as
``response_incomplete`` (no usage record, so the charge stays uncertain, §K).
"""
from __future__ import annotations

import base64
import errno
import importlib.util
import json
import os
import sys
import threading
import time
from typing import Any

from mitmproxy import ctx, http
from mitmproxy.net import encoding

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("_zkqec_broker_policy", os.path.join(_here, "policy.py"))
assert _spec is not None and _spec.loader is not None
P = importlib.util.module_from_spec(_spec)
sys.modules["_zkqec_broker_policy"] = P
_spec.loader.exec_module(P)

BLOCK_BODY = b"zkqec broker: request blocked by policy\n"


class _Tee:
    """Stream transform: passes every chunk through unchanged and keeps a bounded copy."""

    def __init__(self, cap: int) -> None:
        self.buf = bytearray()
        self.cap = cap
        self.overflow = False

    def __call__(self, chunk: bytes) -> bytes:
        if not self.overflow:
            if len(self.buf) + len(chunk) > self.cap:
                self.overflow = True
                self.buf = bytearray()
            else:
                self.buf += chunk
        return chunk

    def content(self, content_encoding: str | None) -> bytes | None:
        if self.overflow:
            return None
        try:
            return encoding.decode(bytes(self.buf), content_encoding or "identity")
        except ValueError:
            return None


def _write_all(fd: int, data: bytes) -> None:
    """``os.write`` until every byte is written (a short write is continued, never ignored)."""
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if not n or n < 0:
            raise OSError(errno.EIO, "write made no progress")
        view = view[n:]


def _fsync_dir(path: str) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(path: str, data: bytes) -> None:
    """Durable small-file publication: temp → fsync → rename → directory fsync (§L)."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o600)
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path))


class BrokerLog:
    """Append-only JSONL log. A record counts only once it is completely written and fsync'ed; after
    any write or fsync failure the log refuses every later record (``broken``), so nothing can be
    forwarded on the strength of a record that may be torn or lost (§L)."""

    def __init__(self, path: str, campaign: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o600)
        self._campaign = campaign
        self._lock = threading.Lock()
        self._seq = 0
        self.broken: str | None = None
        last = b"\n"
        try:
            with open(path, "rb") as fh:
                for line in fh:
                    self._seq += 1
                    last = line[-1:]
        except OSError:
            pass
        if last != b"\n":                  # a record torn by an earlier failure: close its line
            _write_all(self._fd, b"\n")
            os.fsync(self._fd)

    def write(self, rec: dict[str, Any], durable: bool = True) -> None:
        with self._lock:
            if self.broken:
                raise OSError(errno.EIO, f"broker log unusable since {self.broken}")
            seq = self._seq + 1
            full = {"seq": seq, "ts": round(time.time(), 6), "campaign": self._campaign}
            full.update(rec)
            line = (json.dumps(full, sort_keys=True, separators=(",", ":")) + "\n").encode()
            try:
                _write_all(self._fd, line)
                if durable:
                    os.fsync(self._fd)
            except BaseException as exc:
                self.broken = f"{type(exc).__name__} errno={getattr(exc, 'errno', None)}"
                raise
            self._seq = seq


def _block(flow: Any) -> None:
    flow.response = http.Response.make(403, BLOCK_BODY, {"content-type": "text/plain"})


def _kill(flow: Any) -> None:
    try:
        if flow.killable:
            flow.kill()
    except Exception:  # noqa: BLE001 - best effort; the job is already failed and dispatch disabled
        pass


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
        self.disabled: str | None = None        # in-memory dispatch switch (set by any hook failure)

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
        """Rebuild per-job counters from the authoritative log and the job markers (survives a
        broker restart, including one after a log failure that lost the ``job_failed`` record)."""
        try:
            fh = open(self.policy.log_path, "rb")
        except OSError:
            fh = None
        if fh is not None:
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
        for name in sorted(os.listdir(self.policy.blocked_dir)):
            if name.endswith(".json"):
                c = self.counters.setdefault(name[:-5], P.Counters())
                c.failed = c.failed or "blocked_marker"

    # -- helpers ---------------------------------------------------------------------------------
    def _counters(self, job_id: str | None) -> Any:
        if job_id is None:
            return None
        return self.counters.setdefault(job_id, P.Counters())

    def _fail_job(self, job_id: str | None, reason: str) -> None:
        """In memory first (every later request of the job is blocked from here on), then the
        durable record and marker; a failure of either is re-raised for the hook's guard."""
        if not job_id:
            return
        c = self._counters(job_id)
        if c.failed:
            return
        c.failed = reason
        errors: list[BaseException] = []
        try:
            assert self.log is not None
            self.log.write({"event": "job_failed", "job_id": job_id, "reason": reason})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        try:
            _publish(os.path.join(self.policy.blocked_dir, job_id + ".json"),
                     json.dumps({"job_id": job_id, "reason": reason, "ts": time.time()}).encode())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        if errors:
            raise errors[0]

    def _hook_failed(self, where: str, job_id: str | None, exc: BaseException) -> None:
        """A hook raised: the flow is already blocked by the caller. Fail the job and disable all
        dispatch in memory, then try to record it durably (each step best effort)."""
        reason = f"broker_{where}_failed"
        self.disabled = self.disabled or reason
        try:
            self._fail_job(job_id, reason)
        except Exception:  # noqa: BLE001
            pass
        try:
            assert self.log is not None
            self.log.write({"event": "hook_failed", "where": where, "job_id": job_id,
                            "error": type(exc).__name__, "errno": getattr(exc, "errno", None),
                            "dispatch": "disabled"})
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.policy is not None and not os.path.lexists(self.policy.dispatch_disabled_path):
                _publish(self.policy.dispatch_disabled_path,
                         json.dumps({"reason": reason, "by": "broker", "ts": time.time()}).encode())
        except Exception:  # noqa: BLE001
            pass

    def _job_of(self, flow: Any) -> tuple[str | None, Any]:
        job_id = self.conn_job.get(flow.client_conn.id)
        return job_id, (self.grants.get(job_id) if job_id else None)

    def _apply(self, job_id: str | None, decision: Any) -> None:
        if decision.decision == P.BLOCK and decision.fails_job:
            self._fail_job(job_id, decision.reason)

    def _decide(self, counters: Any, decide: Any) -> Any:
        if counters is None:
            return P.Decision(P.BLOCK, "unknown_job", False)
        if self.disabled:
            return P.Decision(P.BLOCK, "dispatch_disabled", True)
        return decide()

    # -- CONNECT ---------------------------------------------------------------------------------
    def http_connect(self, flow: http.HTTPFlow) -> None:
        _block(flow)                    # fail closed: lifted only after the allowing record (B1)
        job_id = None
        try:
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
            counters = self._counters(job_id)
            d = P.Decision(P.BLOCK, "dispatch_disabled", True) if self.disabled else \
                P.decide_connect(self.policy, grant, counters, host, port)
            assert self.log is not None
            self.log.write({"event": "connect", "job_id": job_id, "host": P.sanitize_label(host),
                            "port": port, "token_present": token is not None,
                            "decision": d.decision, "reason": d.reason})
            self._apply(job_id, d)
            if d.allowed:
                self.conn_job[flow.client_conn.id] = job_id
                self.conn_host[flow.client_conn.id] = host
                flow.response = None
        except Exception as exc:  # noqa: BLE001
            self.conn_job.pop(flow.client_conn.id, None)
            _block(flow)
            self._hook_failed("connect", job_id, exc)

    def client_disconnected(self, client: Any) -> None:
        self.conn_job.pop(client.id, None)
        self.conn_host.pop(client.id, None)

    # -- HTTP ------------------------------------------------------------------------------------
    def request(self, flow: http.HTTPFlow) -> None:
        if flow.request.method == "CONNECT":
            return
        _block(flow)                    # fail closed: lifted only after the allowing record (B1)
        job_id = None
        try:
            job_id = self.conn_job.get(flow.client_conn.id)
            self._request(flow)
        except Exception as exc:  # noqa: BLE001
            flow.metadata.pop("zkqec_job", None)
            _block(flow)
            self._hook_failed("request", job_id, exc)

    def _request(self, flow: http.HTTPFlow) -> None:
        assert self.log is not None
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
        d = self._decide(counters, lambda: P.decide_request(self.policy, grant, counters, host, method,
                                                            path, ins, endpoint=endpoint))
        rec = {"event": "request", "job_id": job_id, "host": P.sanitize_label(host), "method": method,
               "path": P.sanitize_label(path, 160), "http_version": req.http_version,
               "kind": endpoint.kind if endpoint else None, "decision": d.decision, "reason": d.reason,
               "body_bytes": len(req.raw_content or b"")}
        if ins is not None:
            rec.update(ins.summary())
        if counters is not None:
            rec.update({"model_requests_after": counters.model_requests,
                        "total_requests_after": counters.total_requests})
        self.log.write(rec)  # complete and durable BEFORE forwarding
        self._apply(job_id, d)
        if not d.allowed:
            return
        flow.metadata["zkqec_job"] = job_id
        flow.metadata["zkqec_kind"] = endpoint.kind if endpoint else None
        flow.metadata["zkqec_body"] = endpoint.body if endpoint else None
        flow.response = None            # forward

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get("zkqec_job") is None or flow.response is None \
                or flow.metadata.get("zkqec_kind") != P.MODEL:
            return
        try:
            tee = _Tee(P.MAX_USAGE_BODY_BYTES)
            flow.metadata["zkqec_tee"] = tee
            flow.response.stream = tee
        except Exception as exc:  # noqa: BLE001
            _kill(flow)
            self._hook_failed("responseheaders", flow.metadata.get("zkqec_job"), exc)

    def _model_usage(self, flow: http.HTTPFlow, complete: bool = True) -> tuple[int, dict[str, Any] | None]:
        """(body bytes seen, parsed usage) of a model response, from the streamed copy; usage only for
        a complete message unless ``complete`` is False (diagnostics)."""
        assert flow.response is not None
        tee = flow.metadata.get("zkqec_tee")
        if tee is None:                         # not streamed (no body): parse what was buffered
            raw, content = flow.response.raw_content or b"", flow.response.get_content(strict=False) or b""
        else:
            raw, content = bytes(tee.buf), tee.content(flow.response.headers.get("content-encoding"))
        if content is None:
            return len(raw), None
        ctype = flow.response.headers.get("content-type")
        if flow.metadata.get("zkqec_body") == "anthropic_messages":
            return len(raw), P.anthropic_usage(content, ctype, complete=complete)
        u = None
        for line in content.decode("utf-8", "replace").splitlines():
            if line.startswith("data:") and "response.completed" in line:
                u = P.ws_server_usage(line[5:].strip().encode())
        return len(raw), u

    def response(self, flow: http.HTTPFlow) -> None:
        job_id = flow.metadata.get("zkqec_job")
        if job_id is None or flow.response is None:
            return
        try:
            assert self.log is not None
            rec: dict[str, Any] = {"event": "response", "job_id": job_id,
                                   "host": P.sanitize_label(flow.request.pretty_host),
                                   "path": P.sanitize_label(P.strip_query(flow.request.path), 160),
                                   "status": flow.response.status_code,
                                   "body_bytes": len(flow.response.raw_content or b"")}
            if flow.metadata.get("zkqec_kind") == P.MODEL:
                rec["body_bytes"], u = self._model_usage(flow)
                rec["usage"] = u["usage"] if u else None
                rec["stop_reason"] = u.get("stop_reason") if u else None
                rec["usage_status"] = "reported" if u else "unknown"
            self.log.write(rec)
        except Exception as exc:  # noqa: BLE001 - usage stays unrecorded: the charge stays uncertain
            _kill(flow)
            self._hook_failed("response", job_id, exc)

    def error(self, flow: http.HTTPFlow) -> None:
        if self.log is None:
            return
        job_id = self.conn_job.get(flow.client_conn.id) if flow.client_conn else None
        try:
            msg = flow.error.msg if flow.error else ""
            self.log.write({"event": "flow_error", "job_id": job_id,
                            "host": P.sanitize_label(flow.request.pretty_host if flow.request else None),
                            "error": P.sanitize_label(msg, 160)})
            if flow.metadata.get("zkqec_tee") is not None and flow.response is not None:
                # Diagnostic only: an interrupted stream never yields a usage record (§K).
                n, u = self._model_usage(flow, complete=False)
                self.log.write({"event": "response_incomplete", "job_id": flow.metadata.get("zkqec_job"),
                                "path": P.sanitize_label(P.strip_query(flow.request.path), 160),
                                "status": flow.response.status_code, "body_bytes": n,
                                "usage_partial": u["usage"] if u else None, "usage_status": "incomplete"})
        except Exception as exc:  # noqa: BLE001
            self._hook_failed("error", job_id, exc)

    def tls_failed_client(self, data: Any) -> None:
        if self.log is None:
            return
        job_id = None
        try:
            job_id = self.conn_job.get(data.context.client.id)
            self.log.write({"event": "tls_failed_client", "job_id": job_id,
                            "sni": P.sanitize_label(data.context.client.sni),
                            "error": P.sanitize_label(str(data.conn.error), 160)})
        except Exception as exc:  # noqa: BLE001
            self._hook_failed("tls_failed_client", job_id, exc)

    # -- WebSocket -------------------------------------------------------------------------------
    def websocket_start(self, flow: http.HTTPFlow) -> None:
        job_id = flow.metadata.get("zkqec_job")
        try:
            assert self.log is not None
            self.log.write({"event": "ws_start", "job_id": job_id,
                            "host": P.sanitize_label(flow.request.pretty_host),
                            "path": P.sanitize_label(P.strip_query(flow.request.path), 160)},
                           durable=False)
        except Exception as exc:  # noqa: BLE001
            _kill(flow)
            self._hook_failed("websocket_start", job_id, exc)

    def websocket_message(self, flow: http.HTTPFlow) -> None:
        msg = None
        job_id = flow.metadata.get("zkqec_job")
        try:
            assert flow.websocket is not None
            msg = flow.websocket.messages[-1]
            if msg.from_client:
                msg.drop()              # fail closed: lifted only after the allowing record (B1)
            self._websocket_message(flow, msg, job_id)
        except Exception as exc:  # noqa: BLE001
            if msg is not None:
                msg.drop()
            self._hook_failed("websocket_message", job_id, exc)

    def _websocket_message(self, flow: http.HTTPFlow, msg: Any, job_id: str | None) -> None:
        assert self.log is not None
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
        d = self._decide(counters, lambda: P.decide_request(self.policy, grant, counters, host, "GET",
                                                            path, ins, endpoint=endpoint))
        rec = {"event": "ws_frame", "job_id": job_id, "host": P.sanitize_label(host),
               "path": P.sanitize_label(path, 160), "decision": d.decision, "reason": d.reason,
               "frame_bytes": len(msg.content)}
        if ins is not None:
            rec.update(ins.summary())
        if counters is not None:
            rec.update({"model_requests_after": counters.model_requests,
                        "total_requests_after": counters.total_requests})
        self.log.write(rec)  # complete and durable BEFORE forwarding
        self._apply(job_id, d)
        if d.allowed:
            msg.dropped = False         # forward
            return
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
        job_id = flow.metadata.get("zkqec_job")
        try:
            ws = flow.websocket
            self.log.write({"event": "ws_end", "job_id": job_id,
                            "close_code": ws.close_code if ws else None,
                            "closed_by_client": ws.closed_by_client if ws else None}, durable=False)
        except Exception as exc:  # noqa: BLE001
            self._hook_failed("websocket_end", job_id, exc)


addons = [ZkqecBroker()]
