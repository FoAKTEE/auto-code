"""Drive the broker addon's hooks through mitmproxy's own exception wrapper, with faults injected
into the broker log (Codex M1 milestone review B1; contracts §J1, §L).

Run inside ``.venv-broker`` (mitmproxy 11.0.2): ``python -I addon_harness.py <spec.json>``. The spec
names the addon, the campaign policy written by ``zkqec.broker.launcher.Broker``, one proxy token per
job and a list of scenarios. Every hook is invoked with ``AddonManager.trigger``, i.e. inside
mitmproxy's ``safecall``, which logs and swallows an addon exception and lets the flow continue: that
is exactly how a failing hook would leave a request forwardable in production. Prints one JSON object
with the observed outcome of every step.

Faults (armed for exactly one step, then disarmed, so later steps run on a healthy disk):
``enospc`` (log write fails), ``eio_fsync`` (log fsync fails), ``short_enospc`` (a short log write,
then ENOSPC), ``short_ok`` (every log write is short but the loop can complete), ``marker_eio``
(the job's blocked-marker cannot be published).
"""
from __future__ import annotations

import base64
import errno
import importlib.util
import json
import os
import sys

from mitmproxy import http, websocket
from mitmproxy.proxy.layers.http import HttpConnectHook, HttpRequestHook
from mitmproxy.proxy.layers.websocket import WebsocketMessageHook
from mitmproxy.test import taddons, tflow, tutils
from wsproto.frame_protocol import Opcode

MODEL = {"model": "m1", "max_tokens": 50, "messages": [{"role": "user", "content": "x"}]}
TOOL = dict(MODEL, tools=[{"name": "Bash", "input_schema": {}}])
CREATE = {"type": "response.create", "model": "g1", "input": [
    {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}]}
WS_TOOL = {"type": "response.create", "model": "g1", "input": [
    {"type": "additional_tools", "role": "developer",
     "tools": [{"type": "function", "name": "exec"}]}]}
BODIES = {"model": MODEL, "tool": TOOL, "create": CREATE, "ws_tool": WS_TOOL}


class FaultOS:
    """The addon module's ``os``, with faults on the broker log descriptor and the blocked markers."""

    def __init__(self, real) -> None:
        self._real, self.fd, self.mode, self.partial = real, None, None, False

    def __getattr__(self, name):
        return getattr(self._real, name)

    def write(self, fd, data):
        if fd == self.fd and self.mode == "enospc":
            raise OSError(errno.ENOSPC, "No space left on device (injected)")
        if fd == self.fd and self.mode == "short_ok":
            return self._real.write(fd, bytes(data)[:7])
        if fd == self.fd and self.mode == "short_enospc":
            if self.partial:
                raise OSError(errno.ENOSPC, "No space left on device (injected)")
            self.partial = True
            return self._real.write(fd, bytes(data)[:7])
        return self._real.write(fd, data)

    def fsync(self, fd):
        if fd == self.fd and self.mode == "eio_fsync":
            raise OSError(errno.EIO, "Input/output error (injected)")
        return self._real.fsync(fd)

    def replace(self, src, dst, *a, **k):
        if self.mode == "marker_eio" and f"{os.sep}blocked{os.sep}" in str(dst):
            raise OSError(errno.EIO, "Input/output error (injected)")
        return self._real.replace(src, dst, *a, **k)


def load_addon(path: str):
    spec = importlib.util.spec_from_file_location("zkqec_broker_addon_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.os = FaultOS(os)
    return mod


def _auth(token: str) -> str:
    return "Basic " + base64.b64encode(f"zkqec:{token}".encode()).decode()


class Run:
    def __init__(self, mod, spec: dict) -> None:
        self.mod, self.spec = mod, spec
        self.host, self.port = spec["host"], spec["port"]
        self.conns: dict[str, object] = {}
        self.ws: dict[str, object] = {}
        self.start()

    def start(self) -> None:
        self.broker = self.mod.ZkqecBroker()
        self.tctx = taddons.context(self.broker, loadcore=False)
        self.tctx.configure(self.broker, zkqec_policy=self.spec["policy"])
        self.mod.os.fd = self.broker.log._fd

    def trigger(self, hook) -> None:
        self.tctx.master.addons.trigger(hook)          # mitmproxy's safecall wrapper

    def step(self, st: dict) -> dict:
        fault = self.mod.os
        fault.mode, fault.partial = st.get("fault"), False
        try:
            return getattr(self, "op_" + st["op"])(st)
        finally:
            fault.mode = None

    def op_connect(self, st: dict) -> dict:
        cc = tflow.tclient_conn()
        req = tutils.treq(method=b"CONNECT", host=self.host, port=self.port, scheme=b"",
                          authority=f"{self.host}:{self.port}".encode(), path=b"",
                          headers=http.Headers(proxy_authorization=_auth(self.spec["tokens"][st["job"]])),
                          content=b"")
        f = tflow.tflow(client_conn=cc, req=req)
        self.trigger(HttpConnectHook(f))
        self.conns[st["conn"]] = cc
        return {"op": "connect", "forwarded": f.response is None and f.error is None}

    def _req(self, st: dict, method: bytes, path: bytes, body: bytes, extra: dict) -> object:
        hdr = http.Headers(host=f"{self.host}:{self.port}", content_type="application/json", **extra)
        req = tutils.treq(method=method, host=self.host, port=self.port, scheme=b"https",
                          authority=b"", path=path, headers=hdr, content=body)
        f = tflow.tflow(client_conn=self.conns[st["conn"]], req=req)
        self.trigger(HttpRequestHook(f))
        return f

    def op_post(self, st: dict) -> dict:
        f = self._req(st, b"POST", b"/v1/messages", json.dumps(BODIES[st["body"]]).encode(), {})
        return {"op": "post", "forwarded": f.response is None and f.error is None,
                "status": f.response.status_code if f.response is not None else None}

    def op_ws_upgrade(self, st: dict) -> dict:
        f = self._req(st, b"GET", b"/backend-api/codex/responses", b"",
                      {"upgrade": "websocket", "connection": "Upgrade"})
        f.websocket = websocket.WebSocketData()
        self.ws[st["conn"]] = f
        return {"op": "ws_upgrade", "forwarded": f.response is None and f.error is None}

    def op_ws(self, st: dict) -> dict:
        f = self.ws[st["conn"]]
        msg = websocket.WebSocketMessage(Opcode.TEXT, True, json.dumps(BODIES[st["frame"]]).encode())
        f.websocket.messages.append(msg)
        self.trigger(WebsocketMessageHook(f))
        return {"op": "ws", "forwarded": not msg.dropped}

    def op_restart(self, st: dict) -> dict:
        """A new broker process: fresh addon state, same policy, log and state directory."""
        self.conns, self.ws = {}, {}
        self.start()
        return {"op": "restart"}

    def op_enable(self, st: dict) -> dict:
        """The operator removes the persistent dispatch switch after the incident."""
        try:
            os.unlink(self.spec["switch"])
        except FileNotFoundError:
            pass
        return {"op": "enable"}

    def summary(self) -> dict:
        lines = open(self.spec["log"], "rb").read().split(b"\n")
        parsed, bad = [], 0
        for ln in lines[:-1]:
            try:
                parsed.append(json.loads(ln))
            except ValueError:
                bad += 1
        return {"failed": {j: c.failed for j, c in self.broker.counters.items()},
                "disabled": getattr(self.broker, "disabled", None),
                "markers": sorted(os.listdir(os.path.join(self.spec["state"], "blocked"))),
                "switch": os.path.lexists(self.spec["switch"]),
                "log_tail_newline": lines[-1] == b"", "log_bad_lines": bad,
                "log": [{k: r.get(k) for k in ("event", "job_id", "decision", "reason")}
                        for r in parsed]}


def main() -> None:
    spec = json.load(open(sys.argv[1]))
    mod = load_addon(spec["addon"])
    out = {}
    for sc in spec["scenarios"]:
        run = Run(mod, {**spec, **sc["env"]})
        steps = [run.step(st) for st in sc["steps"]]
        out[sc["name"]] = {"steps": steps, **run.summary()}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
