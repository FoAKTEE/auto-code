"""Request-inspection policy of the zkqec egress broker (contracts §J1, §K, §L).

This module is **stdlib only**. It is loaded by the mitmproxy addon inside the broker venv
(``.venv-broker``) and imported as ``zkqec.broker.policy`` by the orchestrator and the tests, so the
decision logic is unit-testable without mitmproxy.

Vocabulary:

* *campaign policy* -- the frozen JSON file the broker is started with: provider allowlists (exact
  ``(host, method, path-pattern)`` triples), the allowed tool names, the log/state paths, the dispatch
  switch and the campaign deadline.
* *grant* -- one job's authorization: provider, model manifest and per-invocation ceilings. A grant is
  looked up by the SHA-256 of the job's proxy token; the token itself is never stored or logged.
* *counters* -- what the job has consumed so far at the broker (requests, input bytes). A violation
  fails the job closed: every later request of that job is blocked.

Nothing here ever looks at header values other than the proxy token, and nothing returned for logging
contains a header or a body: only host, method, path (query stripped), model, tool names, sizes,
decision and reason.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

SCHEMA = "zkqec.broker.policy/1"
GRANT_SCHEMA = "zkqec.broker.grant/1"
MAX_JSON_DEPTH = 64
MAX_LABEL = 96
DEFAULT_MAX_BODY_BYTES = 8 * 1024 * 1024

# Endpoint kinds.
MODEL = "model"          # an HTTP request that is a model invocation (body inspected)
WEBSOCKET = "websocket"  # a WebSocket upgrade whose client frames are model invocations
AUX = "aux"              # an allowlisted non-model request (no body expected)

BODY_INSPECTORS = ("anthropic_messages", "openai_responses")
FRAME_INSPECTORS = ("openai_responses_ws",)

# Decisions written to the log.
ALLOW = "allow"
BLOCK = "block"                    # policy violation: blocked AND the job fails closed
BLOCK_EXPECTED = "block_expected"  # known non-essential endpoint (telemetry): blocked, job continues


class PolicyError(ValueError):
    """Malformed policy, grant or inspected payload."""


# --------------------------------------------------------------------------------------------------
# Bounded JSON
# --------------------------------------------------------------------------------------------------
_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[\[\]{}]', re.S)


def json_depth(text: str) -> int:
    """Maximum container nesting depth of a JSON text (strings skipped)."""
    depth = best = 0
    for m in _TOKEN.finditer(text):
        t = m.group()
        if t in "[{":
            depth += 1
            best = max(best, depth)
        elif t in "]}":
            depth -= 1
    return best


def bounded_json_loads(data: bytes | str, *, max_bytes: int, max_depth: int = MAX_JSON_DEPTH) -> Any:
    """Parse JSON only after byte and nesting caps pass (raises PolicyError)."""
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    if len(raw) > max_bytes:
        raise PolicyError(f"json_too_large:{len(raw)}>{max_bytes}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyError("json_not_utf8") from exc
    if json_depth(text) > max_depth:
        raise PolicyError("json_too_deep")
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise PolicyError("json_invalid") from exc


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite constant {name}")


def sanitize_label(value: Any, maxlen: int = MAX_LABEL) -> str | None:
    """Printable, length-capped rendering of a short identifier (model or tool name)."""
    if value is None:
        return None
    s = value if isinstance(value, str) else repr(value)
    s = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in s)
    return s if len(s) <= maxlen else s[: maxlen - 3] + "..."


def strip_query(path: str) -> str:
    return path.split("?", 1)[0].split("#", 1)[0]


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------------------
# Policy objects
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Endpoint:
    host: str
    method: str
    path: str                 # regular expression, full match against the query-stripped path
    kind: str                 # MODEL | WEBSOCKET | AUX
    body: str | None = None   # inspector for MODEL
    frames: str | None = None  # inspector for WEBSOCKET client frames

    def matches(self, host: str, method: str, path: str) -> bool:
        return (host == self.host and method == self.method
                and re.fullmatch(self.path, strip_query(path)) is not None)


@dataclass(frozen=True)
class ProviderPolicy:
    name: str
    hosts: frozenset[str]
    endpoints: tuple[Endpoint, ...]
    quiet_block: tuple[Endpoint, ...]
    quiet_block_hosts: frozenset[str]
    allowed_tools: frozenset[str]
    ws_client_frame_types: frozenset[str]
    connect_ports: frozenset[int] = frozenset({443})
    allowed_body_keys: frozenset[str] | None = None   # None = not enforced

    def endpoint(self, host: str, method: str, path: str) -> Endpoint | None:
        for ep in self.endpoints:
            if ep.matches(host, method, path):
                return ep
        return None

    def is_quiet_block(self, host: str, method: str | None = None, path: str | None = None) -> bool:
        if host in self.quiet_block_hosts:
            return True
        if method is None or path is None:
            return False
        return any(ep.matches(host, method, path) for ep in self.quiet_block)


@dataclass(frozen=True)
class CampaignPolicy:
    campaign: str
    providers: dict[str, ProviderPolicy]
    log_path: str
    state_dir: str
    dispatch_disabled_path: str
    deadline_unix: float | None
    max_body_bytes: int
    sha256: str

    @property
    def grants_dir(self) -> str:
        return os.path.join(self.state_dir, "grants")

    @property
    def blocked_dir(self) -> str:
        return os.path.join(self.state_dir, "blocked")

    def dispatch_disabled(self) -> bool:
        return os.path.lexists(self.dispatch_disabled_path)

    def past_deadline(self, now: float) -> bool:
        return self.deadline_unix is not None and now >= self.deadline_unix


@dataclass(frozen=True)
class Grant:
    job_id: str
    provider: str
    models: frozenset[str]
    max_model_requests: int
    max_total_requests: int
    max_input_bytes: int
    max_output_tokens: int
    expires_unix: float

    def to_json(self) -> dict[str, Any]:
        return {"schema": GRANT_SCHEMA, "job_id": self.job_id, "provider": self.provider,
                "models": sorted(self.models), "max_model_requests": self.max_model_requests,
                "max_total_requests": self.max_total_requests, "max_input_bytes": self.max_input_bytes,
                "max_output_tokens": self.max_output_tokens, "expires_unix": self.expires_unix}


@dataclass
class Counters:
    model_requests: int = 0
    total_requests: int = 0
    input_bytes: int = 0
    failed: str | None = None


def _endpoint(d: dict[str, Any], host_default: str | None = None) -> Endpoint:
    kind = d.get("kind", AUX)
    if kind not in (MODEL, WEBSOCKET, AUX):
        raise PolicyError(f"bad endpoint kind {kind!r}")
    body, frames = d.get("body"), d.get("frames")
    if kind == MODEL and body not in BODY_INSPECTORS:
        raise PolicyError("model endpoint needs a known body inspector")
    if kind == WEBSOCKET and frames not in FRAME_INSPECTORS:
        raise PolicyError("websocket endpoint needs a known frame inspector")
    method = str(d["method"]).upper()
    path = str(d["path"])
    if not (path.startswith("^") and path.endswith("$")):
        raise PolicyError(f"endpoint path pattern must be anchored: {path!r}")
    re.compile(path)
    return Endpoint(host=str(d.get("host", host_default)), method=method, path=path, kind=kind,
                    body=body, frames=frames)


def parse_policy(doc: dict[str, Any], raw: bytes = b"") -> CampaignPolicy:
    if doc.get("schema") != SCHEMA:
        raise PolicyError("unknown policy schema")
    providers: dict[str, ProviderPolicy] = {}
    for name, p in doc["providers"].items():
        endpoints = tuple(_endpoint(e) for e in p["endpoints"])
        hosts = frozenset(p["hosts"])
        for ep in endpoints:
            if ep.host not in hosts:
                raise PolicyError(f"endpoint host {ep.host} not in provider hosts")
        quiet = p.get("quiet_block", [])
        providers[name] = ProviderPolicy(
            name=name, hosts=hosts, endpoints=endpoints,
            quiet_block=tuple(_endpoint(dict(e, kind=AUX)) for e in quiet if "path" in e),
            quiet_block_hosts=frozenset(e["host"] for e in quiet if "path" not in e),
            allowed_tools=frozenset(p.get("allowed_tools", [])),
            ws_client_frame_types=frozenset(p.get("ws_client_frame_types", [])),
            allowed_body_keys=frozenset(p["allowed_body_keys"]) if "allowed_body_keys" in p else None,
            connect_ports=frozenset(int(x) for x in p.get("connect_ports", [443])),
        )
    for key in ("campaign", "log_path", "state_dir", "dispatch_disabled_path"):
        if not isinstance(doc.get(key), str) or not doc[key]:
            raise PolicyError(f"campaign policy needs {key}")
    deadline = doc.get("deadline_unix")
    return CampaignPolicy(campaign=doc["campaign"], providers=providers, log_path=doc["log_path"],
                          state_dir=doc["state_dir"], dispatch_disabled_path=doc["dispatch_disabled_path"],
                          deadline_unix=float(deadline) if deadline is not None else None,
                          max_body_bytes=int(doc.get("max_body_bytes", DEFAULT_MAX_BODY_BYTES)),
                          sha256=hashlib.sha256(raw).hexdigest() if raw else "")


def load_policy(path: str) -> CampaignPolicy:
    with open(path, "rb") as fh:
        raw = fh.read(1 << 20)
    return parse_policy(bounded_json_loads(raw, max_bytes=1 << 20), raw)


def parse_grant(doc: dict[str, Any]) -> Grant:
    if doc.get("schema") != GRANT_SCHEMA:
        raise PolicyError("unknown grant schema")
    g = Grant(job_id=str(doc["job_id"]), provider=str(doc["provider"]), models=frozenset(doc["models"]),
              max_model_requests=int(doc["max_model_requests"]),
              max_total_requests=int(doc["max_total_requests"]),
              max_input_bytes=int(doc["max_input_bytes"]), max_output_tokens=int(doc["max_output_tokens"]),
              expires_unix=float(doc["expires_unix"]))
    if min(g.max_model_requests, g.max_total_requests, g.max_input_bytes, g.max_output_tokens) < 0:
        raise PolicyError("negative ceiling")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", g.job_id):
        raise PolicyError("bad job id")
    return g


def load_grant(policy: CampaignPolicy, digest: str) -> Grant | None:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    path = os.path.join(policy.grants_dir, digest + ".json")
    try:
        with open(path, "rb") as fh:
            raw = fh.read(65536)
    except FileNotFoundError:
        return None
    return parse_grant(bounded_json_loads(raw, max_bytes=65536))


# --------------------------------------------------------------------------------------------------
# Inspection
# --------------------------------------------------------------------------------------------------
@dataclass
class Inspection:
    """What the broker learned from one request body or client frame (no content retained)."""
    model: str | None = None
    tools: tuple[str, ...] = ()
    tools_key: bool = False
    max_output_tokens: int | None = None
    output_field: str = "absent"     # "present" | "absent": whether an output ceiling field exists
    input_bytes: int = 0
    frame_type: str | None = None
    is_model_request: bool = False
    keys: tuple[str, ...] = ()        # top-level field names of the body (names only, never values)
    include: tuple[str, ...] = ()     # OpenAI ``include`` selectors (short identifiers)
    shape: tuple[str, ...] = ()       # structural sketch of input items: "type/role:bytes" (no content)
    tool_choice: str | None = None
    violations: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {"model": sanitize_label(self.model), "tools": [sanitize_label(t) for t in self.tools],
                "tools_key": self.tools_key, "max_output_tokens": self.max_output_tokens,
                "output_field": self.output_field, "input_bytes": self.input_bytes,
                "frame_type": sanitize_label(self.frame_type), "model_request": self.is_model_request,
                "body_keys": [sanitize_label(k, 48) for k in self.keys[:48]],
                "include": [sanitize_label(k, 64) for k in self.include[:16]],
                "input_shape": [sanitize_label(k, 64) for k in self.shape[:40]],
                "tool_choice": sanitize_label(self.tool_choice, 48)}


def _shape(items: Any) -> tuple[str, ...]:
    out = []
    if isinstance(items, list):
        for it in items[:40]:
            if isinstance(it, dict):
                parts = it.get("content")
                ptypes = ""
                if isinstance(parts, list):
                    ptypes = "+".join(sorted({str(p.get("type")) for p in parts if isinstance(p, dict)}))
                size = len(json.dumps(it, separators=(",", ":")))
                keys = ",".join(sorted(k for k in it if k not in ("type", "role", "content")))
                out.append(f"{it.get('type')}/{it.get('role')}/{ptypes}/{keys}:{size}")
            else:
                out.append(f"{type(it).__name__}")
    return tuple(out)


def _note_keys(ins: Inspection, doc: dict[str, Any], provider: ProviderPolicy) -> None:
    ins.keys = tuple(sorted(str(k) for k in doc))
    inc = doc.get("include")
    if isinstance(inc, list):
        ins.include = tuple(str(x) for x in inc if isinstance(x, str))
    ins.shape = _shape(doc.get("input") if "input" in doc else doc.get("messages"))
    tc = doc.get("tool_choice")
    ins.tool_choice = tc if isinstance(tc, str) else (json.dumps(tc, sort_keys=True)[:48] if tc is not None else None)
    if provider.allowed_body_keys is not None:
        for k in ins.keys:
            if k not in provider.allowed_body_keys:
                ins.violations.append(f"body_key_not_allowed:{sanitize_label(k, 48)}")


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or tool.get("type") or "?")
    return "?"


def _check_model(ins: Inspection, grant: Grant) -> None:
    if not isinstance(ins.model, str) or ins.model not in grant.models:
        ins.violations.append("model_not_in_manifest")


def _check_output(ins: Inspection, value: Any, grant: Grant) -> None:
    if value is None:
        ins.output_field = "absent"
        return
    ins.output_field = "present"
    if not isinstance(value, int) or isinstance(value, bool):
        ins.violations.append("output_field_malformed")
        return
    ins.max_output_tokens = value
    if value > grant.max_output_tokens:
        ins.violations.append("output_ceiling")


def inspect_anthropic_messages(body: bytes, grant: Grant, provider: ProviderPolicy,
                               max_body_bytes: int) -> Inspection:
    """POST /v1/messages: model in manifest, tools ⊆ allowlist (custom tools only), max_tokens ≤ ceiling."""
    ins = Inspection(input_bytes=len(body), is_model_request=True)
    try:
        doc = bounded_json_loads(body, max_bytes=max_body_bytes)
    except PolicyError as exc:
        ins.violations.append(f"body_unparseable:{exc}")
        return ins
    if not isinstance(doc, dict):
        ins.violations.append("body_not_object")
        return ins
    ins.model = doc.get("model") if isinstance(doc.get("model"), str) else None
    _note_keys(ins, doc, provider)
    _check_model(ins, grant)
    tools = doc.get("tools")
    if tools is not None:
        ins.tools_key = True
        if not isinstance(tools, list):
            ins.violations.append("tools_malformed")
        else:
            ins.tools = tuple(_tool_name(t) for t in tools)
            for t in tools:
                name = _tool_name(t)
                if not isinstance(t, dict) or t.get("type") not in (None, "custom"):
                    ins.violations.append(f"server_tool:{sanitize_label(name)}")
                elif name not in provider.allowed_tools:
                    ins.violations.append(f"tool_not_allowed:{sanitize_label(name)}")
    for key in ("mcp_servers", "container"):
        if doc.get(key):
            ins.violations.append(f"server_feature:{key}")
    _inspect_anthropic_content(doc, ins, provider)
    _check_output(ins, doc.get("max_tokens"), grant)
    return ins


ANTHROPIC_BLOCK_TYPES = frozenset({"text", "tool_use", "tool_result", "thinking", "redacted_thinking"})
ANTHROPIC_ROLES = frozenset({"user", "assistant", "system"})


def _inspect_anthropic_content(doc: dict[str, Any], ins: Inspection, provider: ProviderPolicy) -> None:
    """Messages may carry only text (plus tool_use/tool_result of allowlisted tools and thinking);
    server-tool, MCP, document and other block types fail closed. ``system`` is text only."""
    msgs = doc.get("messages")
    if not isinstance(msgs, list):
        ins.violations.append("messages_malformed")
        return
    for m in msgs:
        if not isinstance(m, dict) or m.get("role") not in ANTHROPIC_ROLES:
            ins.violations.append("message_role_not_allowed")
            continue
        content = m.get("content")
        if isinstance(content, str):
            continue
        if not isinstance(content, list):
            ins.violations.append("message_content_malformed")
            continue
        for block in content:
            bt = block.get("type") if isinstance(block, dict) else None
            if bt not in ANTHROPIC_BLOCK_TYPES:
                ins.violations.append(f"content_block_not_allowed:{sanitize_label(bt)}")
            elif bt == "tool_use" and block.get("name") not in provider.allowed_tools:
                ins.violations.append(f"tool_use_not_allowed:{sanitize_label(block.get('name'))}")
    system = doc.get("system")
    if isinstance(system, list):
        for block in system:
            if not isinstance(block, dict) or block.get("type") != "text":
                ins.violations.append("system_block_not_text")
    elif system is not None and not isinstance(system, str):
        ins.violations.append("system_malformed")


def _inspect_openai_doc(doc: Any, ins: Inspection, grant: Grant, provider: ProviderPolicy) -> None:
    if not isinstance(doc, dict):
        ins.violations.append("body_not_object")
        return
    ins.model = doc.get("model") if isinstance(doc.get("model"), str) else None
    _note_keys(ins, doc, provider)
    _check_model(ins, grant)
    if "tools" in doc:
        ins.tools_key = True
        tools = doc["tools"]
        if not isinstance(tools, list):
            ins.violations.append("tools_malformed")
        else:
            ins.tools = tuple(_tool_name(t) for t in tools)
            for t in tools:
                name = _tool_name(t)
                if name not in provider.allowed_tools:
                    ins.violations.append(f"tool_not_allowed:{sanitize_label(name)}")
    if doc.get("mcp_servers"):
        ins.violations.append("server_feature:mcp_servers")
    _inspect_openai_input(doc.get("input"), ins, provider)
    _check_output(ins, doc.get("max_output_tokens"), grant)


OPENAI_MESSAGE_ROLES = frozenset({"developer", "user", "system", "assistant"})
OPENAI_PART_TYPES = frozenset({"input_text", "output_text"})
OPENAI_TOOL_ITEMS = frozenset({"additional_tools", "tools", "tool_definitions"})


def _nested_tool_names(decl: list[Any], prefix: str = "", depth: int = 0) -> list[str]:
    """Tool names in a declaration list; namespaces (entries with their own ``tools`` list) are
    expanded to ``namespace.tool``."""
    names: list[str] = []
    for x in decl[:256]:
        name = _tool_name(x)
        inner = x.get("tools") if isinstance(x, dict) else None
        if isinstance(inner, list) and depth < 3:
            sub = _nested_tool_names(inner, f"{prefix}{name}.", depth + 1)
            names.extend(sub if sub else [f"{prefix}{name}.<empty>"])
        else:
            names.append(prefix + name)
    return names


def _inspect_openai_input(items: Any, ins: Inspection, provider: ProviderPolicy) -> None:
    """Tool declarations can travel inside ``input`` (Codex 0.160.1 sends an ``additional_tools``
    developer item instead of a top-level ``tools`` key). Only plain text messages are allowed; tool
    items contribute their tool names to the declared set; any other item type fails closed."""
    if items is None or isinstance(items, str):
        return
    if not isinstance(items, list):
        ins.violations.append("input_malformed")
        return
    for it in items:
        if not isinstance(it, dict):
            ins.violations.append("input_item_malformed")
            continue
        t = it.get("type", "message")
        if t in OPENAI_TOOL_ITEMS:
            ins.tools_key = True
            decl = it.get("tools") if isinstance(it.get("tools"), list) else it.get("content")
            names = _nested_tool_names(decl) if isinstance(decl, list) else ["?"]
            ins.tools = ins.tools + tuple(names)
            for name in names:
                if name not in provider.allowed_tools:
                    ins.violations.append(f"tool_not_allowed:{sanitize_label(name)}")
        elif t == "message":
            if it.get("role") not in OPENAI_MESSAGE_ROLES:
                ins.violations.append(f"input_role_not_allowed:{sanitize_label(it.get('role'))}")
            content = it.get("content")
            if isinstance(content, list):
                for part in content:
                    pt = part.get("type") if isinstance(part, dict) else None
                    if pt not in OPENAI_PART_TYPES:
                        ins.violations.append(f"input_part_not_allowed:{sanitize_label(pt)}")
            elif not isinstance(content, str):
                ins.violations.append("input_content_malformed")
        else:
            ins.violations.append(f"input_item_not_allowed:{sanitize_label(t)}")


def inspect_openai_responses(body: bytes, grant: Grant, provider: ProviderPolicy,
                             max_body_bytes: int) -> Inspection:
    """POST .../responses (HTTP fallback): model in manifest, no tools, output ceiling if present."""
    ins = Inspection(input_bytes=len(body), is_model_request=True)
    try:
        doc = bounded_json_loads(body, max_bytes=max_body_bytes)
    except PolicyError as exc:
        ins.violations.append(f"body_unparseable:{exc}")
        return ins
    _inspect_openai_doc(doc, ins, grant, provider)
    return ins


def inspect_ws_client_frame(content: bytes, is_text: bool, grant: Grant, provider: ProviderPolicy,
                            max_body_bytes: int) -> Inspection:
    """A WebSocket client frame to .../codex/responses: only allowlisted frame types; ``response.create``
    is a model request and gets the Responses checks."""
    ins = Inspection(input_bytes=len(content))
    if not is_text:
        ins.violations.append("ws_binary_frame")
        return ins
    try:
        doc = bounded_json_loads(content, max_bytes=max_body_bytes)
    except PolicyError as exc:
        ins.violations.append(f"frame_unparseable:{exc}")
        return ins
    if not isinstance(doc, dict):
        ins.violations.append("frame_not_object")
        return ins
    ftype = doc.get("type")
    ins.frame_type = ftype if isinstance(ftype, str) else None
    if ins.frame_type not in provider.ws_client_frame_types:
        ins.violations.append(f"ws_frame_type_not_allowed:{sanitize_label(ftype)}")
        return ins
    if ins.frame_type == "response.create":
        ins.is_model_request = True
        _inspect_openai_doc(doc, ins, grant, provider)
    return ins


# --------------------------------------------------------------------------------------------------
# Usage extraction (responses)
# --------------------------------------------------------------------------------------------------
_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
               "cached_tokens", "cache_write_tokens", "reasoning_tokens", "total_tokens")


def _flat_usage(u: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    if not isinstance(u, dict):
        return out
    for k, v in u.items():
        if k in _USAGE_KEYS and isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
        elif k in ("input_tokens_details", "output_tokens_details") and isinstance(v, dict):
            for kk, vv in v.items():
                if kk in _USAGE_KEYS and isinstance(vv, int) and not isinstance(vv, bool):
                    out[kk] = vv
        elif k == "server_tool_use" and isinstance(v, dict):
            for kk, vv in v.items():
                if isinstance(vv, int) and not isinstance(vv, bool):
                    out["server_tool_use." + sanitize_label(kk, 48)] = vv
    return out


MAX_USAGE_BODY_BYTES = 64 << 20               # larger model responses are not parsed: usage unknown


def anthropic_usage(body: bytes, content_type: str | None, max_bytes: int = MAX_USAGE_BODY_BYTES, *,
                    complete: bool = True) -> dict[str, Any] | None:
    """Usage of one **complete** Anthropic Messages response, else None (unknown, §J5, §K).

    SSE: exactly one ``message_start`` (initial usage), a final ``message_delta`` carrying usage with
    an output count, then ``message_stop``, and no ``error`` event. JSON: a message with ``usage`` and
    a non-null ``stop_reason``. A cleanly ended HTTP body without these (a stream cut short upstream)
    is not complete accounting, whatever partial counters it held (Codex M1 milestone review B2).
    ``complete=False`` returns whatever partial usage was seen (diagnostics only, never a usage
    record)."""
    if len(body) > max_bytes:
        return None
    text = body.decode("utf-8", "replace")
    if content_type and "event-stream" in content_type:
        usage: dict[str, int] = {}
        stop = None
        starts = 0
        final_usage = stopped = failed = after_stop = False
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload.startswith("{"):
                continue
            try:
                ev = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            t = ev.get("type")
            if stopped and t != "ping":
                after_stop = True
            if t == "message_start":
                starts += 1
                msg = ev.get("message")
                usage.update(_flat_usage(msg.get("usage") if isinstance(msg, dict) else None))
            elif t == "message_delta":
                u = ev.get("usage")
                final_usage = isinstance(u, dict) and type(u.get("output_tokens")) is int
                usage.update(_flat_usage(u))
                delta = ev.get("delta")
                stop = (delta.get("stop_reason") if isinstance(delta, dict) else None) or stop
            elif t == "message_stop":
                stopped = True
            elif t == "error":
                failed = True
        ok = starts == 1 and final_usage and stopped and not failed and not after_stop
        if ok or (not complete and (starts or usage)):
            return {"usage": usage, "stop_reason": sanitize_label(stop)}
        return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if isinstance(doc, dict) and isinstance(doc.get("usage"), dict) \
            and (doc.get("stop_reason") is not None or not complete):
        return {"usage": _flat_usage(doc["usage"]), "stop_reason": sanitize_label(doc.get("stop_reason"))}
    return None


def ws_server_usage(content: bytes) -> dict[str, Any] | None:
    """Usage from a server frame of type ``response.completed`` (also notes failures)."""
    head = content[:256]
    if b"response.completed" not in head and b"response.failed" not in head and b'"error"' not in head:
        return None
    try:
        doc = json.loads(content.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    t = doc.get("type")
    if t == "response.completed":
        return {"frame_type": t, "usage": _flat_usage((doc.get("response") or {}).get("usage"))}
    if t in ("response.failed", "error"):
        return {"frame_type": t, "usage": None}
    return None


# --------------------------------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Decision:
    decision: str           # ALLOW | BLOCK | BLOCK_EXPECTED
    reason: str
    fails_job: bool

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW


def _gate(policy: CampaignPolicy, grant: Grant | None, counters: Counters | None, now: float) -> Decision | None:
    if policy.dispatch_disabled():
        return Decision(BLOCK, "dispatch_disabled", True)
    if policy.past_deadline(now):
        return Decision(BLOCK, "campaign_deadline", True)
    if grant is None:
        return Decision(BLOCK, "unknown_job", False)
    if counters is not None and counters.failed:
        return Decision(BLOCK, "job_failed_closed", False)
    if now >= grant.expires_unix:
        return Decision(BLOCK, "grant_expired", True)
    if grant.provider not in policy.providers:
        return Decision(BLOCK, "provider_not_in_policy", True)
    return None


def decide_connect(policy: CampaignPolicy, grant: Grant | None, counters: Counters | None,
                   host: str, port: int, now: float | None = None) -> Decision:
    now = time.time() if now is None else now
    gate = _gate(policy, grant, counters, now)
    if gate is not None:
        return gate
    assert grant is not None
    prov = policy.providers[grant.provider]
    if host in prov.hosts and port in prov.connect_ports:
        return Decision(ALLOW, "host_allowlisted", False)
    if prov.is_quiet_block(host) and port in prov.connect_ports:
        return Decision(BLOCK_EXPECTED, "host_quiet_blocked", False)
    return Decision(BLOCK, "host_not_allowlisted", True)


def decide_request(policy: CampaignPolicy, grant: Grant | None, counters: Counters, host: str,
                   method: str, path: str, inspection: Inspection | None,
                   now: float | None = None, endpoint: Endpoint | None = None) -> Decision:
    """Decide one upstream HTTP request or WebSocket client frame; on ALLOW, charge the counters."""
    now = time.time() if now is None else now
    gate = _gate(policy, grant, counters, now)
    if gate is not None:
        return gate
    assert grant is not None
    prov = policy.providers[grant.provider]
    if endpoint is None:
        endpoint = prov.endpoint(host, method, path)
    if endpoint is None:
        if prov.is_quiet_block(host, method, path):
            return Decision(BLOCK_EXPECTED, "endpoint_quiet_blocked", False)
        return Decision(BLOCK, "endpoint_not_allowlisted", True)
    if inspection is not None and inspection.violations:
        return Decision(BLOCK, inspection.violations[0], True)
    if counters.total_requests + 1 > grant.max_total_requests:
        return Decision(BLOCK, "total_request_ceiling", True)
    is_model = inspection is not None and inspection.is_model_request
    if is_model:
        if counters.model_requests + 1 > grant.max_model_requests:
            return Decision(BLOCK, "model_request_ceiling", True)
        if inspection.input_bytes > grant.max_input_bytes:
            return Decision(BLOCK, "input_bound", True)
    counters.total_requests += 1
    if is_model:
        counters.model_requests += 1
        counters.input_bytes += inspection.input_bytes
    return Decision(ALLOW, "ok", False)


def reservation_bound(grant: Grant, price_in_per_mtok: float, price_out_per_mtok: float,
                      price_per_request: float = 0.0) -> float:
    """Maximum total charge under the grant's ceilings (§K): input bound × highest input price (incl.
    cache-write premium) per model request + output ceiling × output price per model request
    + request ceiling × per-request price. Bytes bound tokens from above (UTF-8 bytes ≥ tokens)."""
    per_call_in = grant.max_input_bytes * price_in_per_mtok / 1e6
    per_call_out = grant.max_output_tokens * price_out_per_mtok / 1e6
    return grant.max_model_requests * (per_call_in + per_call_out) + grant.max_total_requests * price_per_request
