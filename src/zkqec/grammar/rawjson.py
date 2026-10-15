"""L0 raw-input caps for untrusted agent output (prompt §8 L0, appendix §B3).

:func:`check_raw_json` scans the raw text *before* any JSON parser sees it: byte length, container
nesting depth, the number of elements of every array/object, and the length of every bare token
(numbers, literals) are bounded by a single linear pass that allocates nothing proportional to the
input. Only text that passes is handed to :func:`json.loads` (:func:`parse_untrusted_json`), which
additionally rejects NaN/Infinity and duplicate object keys.
"""
from __future__ import annotations

import json
from typing import Any

from .errors import MalformedProposal

_WS = " \t\r\n"


def _as_text(raw: str | bytes, max_bytes: int) -> str:
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > max_bytes:
            raise MalformedProposal(f"{len(raw)} bytes exceed cap {max_bytes}", field="L0.bytes")
        try:
            return bytes(raw).decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise MalformedProposal(f"not valid UTF-8: {exc.reason}", field="L0.encoding") from None
    if not isinstance(raw, str):
        raise MalformedProposal("raw input must be str or bytes", field="L0.type")
    if len(raw) > max_bytes:   # every code point is >= 1 UTF-8 byte: reject before encoding
        raise MalformedProposal(f"more than {max_bytes} bytes", field="L0.bytes")
    size = len(raw.encode("utf-8", errors="surrogatepass"))
    if size > max_bytes:
        raise MalformedProposal(f"{size} bytes exceed cap {max_bytes}", field="L0.bytes")
    return raw


def check_raw_json(text: str | bytes, max_bytes: int, max_depth: int, max_list_len: int,
                   max_token_len: int = 64) -> None:
    """Raise :class:`MalformedProposal` unless the raw text is within every L0 cap.

    ``max_list_len`` bounds the element count of every array *and* the member count of every
    object. ``max_token_len`` bounds bare tokens (numbers, ``true``/``false``/``null``), so a
    megabyte-long integer can never reach the parser. Syntax errors are left to the parser.
    """
    for name, cap in (("max_bytes", max_bytes), ("max_depth", max_depth),
                      ("max_list_len", max_list_len), ("max_token_len", max_token_len)):
        if type(cap) is not int or cap < 1:
            raise ValueError(f"{name} must be a positive int")
    s = _as_text(text, max_bytes)
    counts: list[int] = []        # separators seen per open container
    in_string = escaped = False
    token = 0
    for ch in s:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch in "[{":
            token = 0
            counts.append(0)
            if len(counts) > max_depth:
                raise MalformedProposal(f"nesting depth exceeds cap {max_depth}", field="L0.depth")
        elif ch in "]}":
            token = 0
            if counts:
                counts.pop()
        elif ch == ",":
            token = 0
            if counts:
                counts[-1] += 1
                if counts[-1] + 1 > max_list_len:
                    raise MalformedProposal(f"a container has more than {max_list_len} elements",
                                            field="L0.list_len")
        elif ch == '"':
            token = 0
            in_string = True
        elif ch in _WS or ch == ":":
            token = 0
        else:
            token += 1
            if token > max_token_len:
                raise MalformedProposal(f"a bare token exceeds {max_token_len} characters",
                                        field="L0.token_len")


def _reject_constant(name: str) -> Any:
    raise MalformedProposal(f"non-finite number {name} is not JSON", field="L0.number")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise MalformedProposal(f"duplicate object key {key[:40]!r}", field="L0.duplicate_key")
        out[key] = value
    return out


def parse_untrusted_json(text: str | bytes, *, max_bytes: int, max_depth: int, max_list_len: int,
                         max_token_len: int = 64) -> Any:
    """L0 caps first, then a strict parse (no NaN/Infinity, no duplicate keys)."""
    check_raw_json(text, max_bytes, max_depth, max_list_len, max_token_len)
    s = _as_text(text, max_bytes)
    try:
        return json.loads(s, parse_constant=_reject_constant, object_pairs_hook=_no_duplicate_keys)
    except MalformedProposal:
        raise
    except (ValueError, RecursionError) as exc:
        raise MalformedProposal(f"invalid JSON: {str(exc)[:200]}", field="L0.syntax") from None
