"""Sanitize before addressing (contracts §M).

Secrets never enter content-addressed storage. Every payload is scanned and redacted *before* it is
hashed, and the redaction is described by counts per rule — never by the secret, nor by any hash of
it. Sanitizing is idempotent: a second pass over sanitized output makes no change, which the store
checks as a residual scan (fail closed).

Rules
-----
* ``auth_header`` / ``cookie_header``: ``Authorization:``, ``Proxy-Authorization:``, ``X-Api-Key:``,
  ``Cookie:`` and ``Set-Cookie:`` header lines (value replaced).
* ``secret_field``: in text, every ``"key": "value"`` string pair whose key — after JSON-unescaping
  (``"\u0061pi_key"`` is ``api_key``) and normalising case and ``-``/``_`` — is in
  :data:`SENSITIVE_KEYS`; in structured objects any dict key in :data:`SENSITIVE_KEYS`, and
  ``[header_name, value]`` pairs naming a sensitive header. JSON payloads (kinds ``json``/``jsonl``)
  are additionally parsed and sanitized structurally, so JSON nested inside JSON strings is covered.
* ``secret_param``: ``access_token=…`` style query/form parameters.
* ``bearer``: ``Bearer <token>``.
* ``anthropic_key`` (``sk-ant-…``) and ``sk_key`` (``sk-…``).

Only an *exact* placeholder (``[REDACTED:<rule>]`` and nothing else in the value) is exempt from
re-redaction; a value that merely starts with ``[REDACTED:`` is redacted again (idempotence without a
bypass). Detection is the same code path as sanitizing, so it shares no weaker rule.

Binary payloads cannot be redacted without corrupting them, so a binary payload that matches any rule
is refused (:class:`SecretDetected`) rather than stored.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable

SANITIZER_VERSION = "zkqec-sanitizer-2"
TEXT_KINDS = frozenset({"text", "json", "jsonl", "http_request", "http_response", "prompt",
                        "response", "log", "csv", "markdown"})
SENSITIVE_KEYS = frozenset({"access_token", "refresh_token", "id_token", "api_key", "apikey",
                            "x_api_key", "authorization", "proxy_authorization", "cookie",
                            "set_cookie", "client_secret"})
RULE_NAMES = ("auth_header", "cookie_header", "secret_field", "secret_param", "bearer",
              "anthropic_key", "sk_key")
_PH = r"\[REDACTED:(?:" + "|".join(RULE_NAMES) + r")\]"
# A header value starts at its first non-blank (no backtracking) and is exempt only when it is
# exactly one placeholder up to the end of the line.
_VALUE = r"(?=[^ \t\r\n])(?!" + _PH + r"[ \t]*\r?$)"


def placeholder(rule: str) -> str:
    return f"[REDACTED:{rule}]"


PLACEHOLDERS = frozenset(placeholder(r) for r in RULE_NAMES)


class SecretDetected(ValueError):
    """A payload carries a secret that cannot be redacted safely (binary) or survived redaction."""


@dataclass(frozen=True)
class TextRule:
    name: str
    pattern: re.Pattern[str]
    repl: str | Callable[[re.Match[str]], str]


def _json_key(raw: str) -> str:
    """The key a JSON parser would see for the raw (still escaped) string body ``raw``."""
    try:
        key = json.loads(f'"{raw}"')
    except ValueError:
        key = raw
    return key if isinstance(key, str) else raw


def _norm_key(key: str) -> str:
    return key.strip().lower().replace("-", "_")


_PAIR = re.compile(r'"((?:[^"\\]|\\.)*)"(\s*:\s*)"((?:[^"\\]|\\.)*)"')


def _redact_pair(m: re.Match[str]) -> str:
    key, value = m.group(1), m.group(3)
    if _norm_key(_json_key(key)) not in SENSITIVE_KEYS or value == "" or value in PLACEHOLDERS:
        return m.group(0)
    return f'"{key}"{m.group(2)}"{placeholder("secret_field")}"'


TEXT_RULES: tuple[TextRule, ...] = (
    TextRule("auth_header",
             re.compile(r"(?im)^([ \t]*(?:proxy-authorization|authorization|x-api-key)[ \t]*:[ \t]*)"
                        + _VALUE + r"[^\r\n]+"),
             r"\1" + placeholder("auth_header")),
    TextRule("cookie_header",
             re.compile(r"(?im)^([ \t]*(?:set-cookie|cookie)[ \t]*:[ \t]*)" + _VALUE + r"[^\r\n]+"),
             r"\1" + placeholder("cookie_header")),
    TextRule("secret_field", _PAIR, _redact_pair),
    TextRule("secret_param",
             re.compile(r"(?i)\b(access_token|refresh_token|id_token|api_key|client_secret)="
                        r"(?!" + _PH + r"""(?:[&\s"']|$))[^&\s"']+"""),
             r"\1=" + placeholder("secret_param")),
    TextRule("bearer",
             re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9\-._~+/]{8,}=*"),
             r"\1 " + placeholder("bearer")),
    TextRule("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), placeholder("anthropic_key")),
    TextRule("sk_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), placeholder("sk_key")),
)


class Sanitizer:
    """Applies the redaction rules. Returns redacted values plus ``{rule: count}``."""

    version = SANITIZER_VERSION

    def __init__(self, rules: tuple[TextRule, ...] = TEXT_RULES):
        self.rules = rules

    # -- text ------------------------------------------------------------------------------------
    def sanitize_text(self, text: str, counts: Counter | None = None) -> tuple[str, Counter]:
        counts = Counter() if counts is None else counts
        for rule in self.rules:
            if callable(rule.repl):
                hits = 0

                def repl(m, _f=rule.repl):
                    nonlocal hits
                    out = _f(m)
                    hits += out != m.group(0)
                    return out
                text = rule.pattern.sub(repl, text)
                n = hits
            else:
                text, n = rule.pattern.subn(rule.repl, text)
            if n:
                counts[rule.name] += n
        return text, counts

    # -- structured ------------------------------------------------------------------------------
    def sanitize_obj(self, obj: Any) -> tuple[Any, Counter]:
        counts: Counter = Counter()
        if hasattr(obj, "model_dump"):
            obj = obj.model_dump(mode="json")
        return self._walk(obj, counts), counts

    def _walk(self, obj: Any, counts: Counter) -> Any:
        if isinstance(obj, dict):
            out = {}
            for key, value in obj.items():
                if not isinstance(key, str):
                    raise TypeError("JSON object keys must be strings")
                new_key, _ = self.sanitize_text(key, counts)
                if _norm_key(key) in SENSITIVE_KEYS and self._redactable(value):
                    counts["secret_field"] += 1
                    out[new_key] = placeholder("secret_field")
                else:
                    out[new_key] = self._walk(value, counts)
            return out
        if isinstance(obj, (list, tuple)):
            items = [self._walk(v, counts) for v in obj]
            if (len(items) == 2 and all(isinstance(v, str) for v in obj)
                    and _norm_key(obj[0]) in SENSITIVE_KEYS and self._redactable(items[1])):
                counts["secret_field"] += 1
                items[1] = placeholder("secret_field")
            return items
        if isinstance(obj, str):
            return self.sanitize_text(obj, counts)[0]
        if obj is None or isinstance(obj, (bool, int, float)):
            return obj
        raise TypeError(f"unsupported JSON value of type {type(obj).__name__}")

    @staticmethod
    def _redactable(value: Any) -> bool:
        if value is None or value == "":
            return False
        return not (isinstance(value, str) and value in PLACEHOLDERS)     # exact placeholder only

    # -- bytes -----------------------------------------------------------------------------------
    def sanitize_bytes(self, data: bytes, kind: str) -> tuple[bytes, Counter]:
        """Text kinds are redacted in place; binary payloads that match any rule are refused."""
        if kind in TEXT_KINDS:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            if text is not None:
                clean, counts = self.sanitize_text(text)
                if kind == "json":
                    clean = self._structural_json(clean, counts)
                elif kind == "jsonl":
                    clean = "\n".join(self._structural_json(line, counts) if line.strip() else line
                                      for line in clean.split("\n"))
                return clean.encode("utf-8"), counts
        _, counts = self.sanitize_text(data.decode("latin-1"))
        if counts:
            raise SecretDetected(f"binary payload of kind {kind!r} matches secret rules "
                                 f"{sorted(counts)}; refusing to store it")
        return data, counts

    def _structural_json(self, text: str, counts: Counter) -> str:
        """Parse JSON text and sanitize the decoded object; re-encode only if that finds more."""
        try:
            obj = json.loads(text)
        except (ValueError, RecursionError):
            return text
        clean, more = self.sanitize_obj(obj)
        if not more:
            return text
        counts.update(more)
        return json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    # -- detection -------------------------------------------------------------------------------
    def detect_obj(self, obj: Any) -> Counter:
        """Rule counts that :meth:`sanitize_obj` would apply (empty means clean)."""
        return self.sanitize_obj(obj)[1]


DEFAULT_SANITIZER = Sanitizer()
