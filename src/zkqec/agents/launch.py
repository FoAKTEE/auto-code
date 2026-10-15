"""Exact argv / environment / home layout for headless CLI calls in call mode (contracts §J2, §J3, §K).

Builders only: they return a :class:`CliLaunch` (in-sandbox argv, allowlisted environment, input files,
and the credential file to place in the fresh per-job home). Running it is
:func:`zkqec.sandbox.run_job`'s job; the per-job home is a private tmpfs created by ``nsexec`` that
holds only the credential (mode 0600) and disappears with the job. No function here reads credential
contents.

Qualified flag sets (M0):

* **Claude** (``claude`` 2.1.300): ``-p --tools "" --strict-mcp-config --mcp-config '{"mcpServers":{}}'``
  with a fresh HOME, ``--system-prompt`` (replaces the default prompt that carries environment details),
  ``--json-schema``, ``--output-format json``, ``--no-session-persistence``, ``--max-budget-usd``,
  ``--disallowedTools WebSearch WebFetch``; ``CLAUDE_CODE_MAX_OUTPUT_TOKENS`` sets ``max_tokens``.
  The prompt is fed on stdin.
* **Codex** (``codex`` 0.160.1, the real binary via node, never the ``~/.local/bin`` wrapper):
  ``exec --skip-git-repo-check --ephemeral --ignore-user-config --ignore-rules``, every tool-bearing
  feature disabled, ``web_search="disabled"``, ``-s read-only``, ``--output-schema``, ``--json``, ``-o``;
  the role contract replaces the base instructions via ``model_instructions_file``; the prompt is the
  final argument and stdin is /dev/null.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..sandbox.profiles import (CLAUDE_LINK, SB_CA, SB_HOME, SB_IN, SB_OUT, SB_PROXY_PORT, SB_PROXY_URL,
                                SB_TMP, SB_WORK)
from ..sandbox.runner import HomeFile

CLAUDE_CREDENTIALS = Path(os.path.expanduser("~/.claude/.credentials.json"))
CODEX_AUTH = Path(os.path.expanduser("~/.codex/auth.json"))
REAL_CODEX = Path(os.path.expanduser("~/.local/node/bin/codex"))   # never ~/.local/bin/codex (wrapper)
CODEX_WRAPPER = Path(os.path.expanduser("~/.local/bin/codex"))

# Tool-bearing (or tool-adjacent) Codex features disabled for call mode. ``codex features list`` on
# 0.160.1 shows these enabled by default; the broker verifies the effect on every response.create frame.
CODEX_DISABLED_FEATURES: tuple[str, ...] = (
    "shell_tool", "unified_exec", "multi_agent", "apps", "browser_use", "browser_use_external",
    "computer_use", "in_app_browser", "plugins", "remote_plugin", "code_mode_host", "shell_snapshot",
    "image_generation", "view_image", "sleep_tool", "tool_suggest", "skill_search",
    "skill_mcp_dependency_install", "tool_call_mcp_elicitation", "hooks", "goals", "worktrees",
    "workspace_dependencies", "realtime_conversation", "unbounded_connection_retries", "daemon_auto_start",
    "in_app_updates",
)
# The minimal set probed on 2026-10-15 (kept for the negative test: removing it must surface tools).
CODEX_PROBED_FEATURES: tuple[str, ...] = (
    "shell_tool", "multi_agent", "apps", "browser_use", "browser_use_external", "computer_use",
    "in_app_browser", "plugins", "remote_plugin", "code_mode_host", "shell_snapshot",
)

COMMON_ENV = {
    "HOME": SB_HOME, "LANG": "C.UTF-8", "TERM": "dumb", "TMPDIR": SB_TMP, "USER": "agent", "LOGNAME": "agent",
    "HTTPS_PROXY": SB_PROXY_URL, "https_proxy": SB_PROXY_URL, "HTTP_PROXY": SB_PROXY_URL,
    "http_proxy": SB_PROXY_URL, "ALL_PROXY": SB_PROXY_URL, "NO_PROXY": "", "no_proxy": "",
    "ZKQEC_PROXY_PORT": str(SB_PROXY_PORT), "NO_COLOR": "1",
}
CLAUDE_ENV = {
    "PATH": "/opt/zkqec/bin:/usr/bin:/bin", "NODE_EXTRA_CA_CERTS": SB_CA,
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "DISABLE_AUTOUPDATER": "1",
    "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_BUG_COMMAND": "1", "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1",
}
CODEX_ENV = {
    "PATH": "/opt/zkqec/node/bin:/usr/bin:/bin", "CODEX_HOME": f"{SB_HOME}/.codex", "SSL_CERT_FILE": SB_CA,
    "CODEX_CA_CERTIFICATE": SB_CA,
}


@dataclass(frozen=True)
class CallSpec:
    """One call-mode invocation: role contract + request → schema-valid JSON."""
    provider: str                      # "claude" | "codex"
    model: str
    role_contract: str                 # system prompt (≤ 5 KB, task-independent; §6)
    prompt: str
    output_schema: dict[str, Any]
    effort: str | None = None          # claude --effort / codex model_reasoning_effort
    max_budget_usd: float | None = None
    max_output_tokens: int | None = None
    max_retries: int | None = None


@dataclass(frozen=True)
class CliLaunch:
    provider: str
    argv: tuple[str, ...]
    env: dict[str, str]
    inputs: dict[str, bytes]
    home_files: tuple[HomeFile, ...]
    stdin_input: str | None
    outputs: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> dict[str, Any]:
        """Effective settings for the campaign manifest (§O): argv with the prompt elided, env names."""
        argv = list(self.argv)
        if self.provider == "codex" and argv:
            argv[-1] = "<prompt>"
        return {"provider": self.provider, "argv": argv, "env": dict(sorted(self.env.items())),
                "home_files": [h.dest for h in self.home_files], "stdin": self.stdin_input}


def _schema_bytes(schema: dict[str, Any]) -> bytes:
    return json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()


def claude_call(spec: CallSpec, *, include_tools_flag: bool = True, strict_mcp: bool = True,
                credentials: Path = CLAUDE_CREDENTIALS) -> CliLaunch:
    """Claude Code headless call. ``include_tools_flag``/``strict_mcp`` exist only so qualification can
    build the negative cases; production always uses the defaults."""
    if spec.provider != "claude":
        raise ValueError("not a claude call")
    argv: list[str] = ["/opt/zkqec/bin/claude", "-p", "--model", spec.model, "--output-format", "json",
                       "--no-session-persistence"]
    if include_tools_flag:
        argv += ["--tools", ""]
    argv += ["--disallowedTools", "WebSearch", "WebFetch"]
    if strict_mcp:
        argv += ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
    argv += ["--system-prompt", spec.role_contract, "--json-schema", _schema_bytes(spec.output_schema).decode()]
    if spec.max_budget_usd is not None:
        argv += ["--max-budget-usd", f"{spec.max_budget_usd:.4f}"]
    if spec.effort:
        argv += ["--effort", spec.effort]
    env = {**COMMON_ENV, **CLAUDE_ENV}
    if spec.max_output_tokens is not None:
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(spec.max_output_tokens)
    if spec.max_retries is not None:
        env["CLAUDE_CODE_MAX_RETRIES"] = str(spec.max_retries)
    return CliLaunch(provider="claude", argv=tuple(argv), env=env,
                     inputs={"prompt.txt": spec.prompt.encode()},
                     home_files=(HomeFile(str(credentials), ".claude/.credentials.json"),),
                     stdin_input="prompt.txt")


def codex_call(spec: CallSpec, *, disabled_features: tuple[str, ...] = CODEX_DISABLED_FEATURES,
               web_search_disabled: bool = True, auth: Path = CODEX_AUTH) -> CliLaunch:
    """Codex headless call through the real binary (node launcher → native binary)."""
    if spec.provider != "codex":
        raise ValueError("not a codex call")
    argv: list[str] = ["/opt/zkqec/node/bin/codex", "exec", "--skip-git-repo-check", "--ephemeral",
                       "--ignore-user-config", "--ignore-rules"]
    for feat in disabled_features:
        argv += ["--disable", feat]
    if web_search_disabled:
        argv += ["-c", 'web_search="disabled"']
    argv += ["-c", f'model_instructions_file="{SB_IN}/role_contract.md"']
    if spec.effort:
        argv += ["-c", f'model_reasoning_effort="{spec.effort}"']
    # Codex 0.160.1 exposes no output-token ceiling for the ChatGPT backend (its response.create frames
    # carry no max_output_tokens field); spec.max_output_tokens is enforced only by wall time and is
    # reported as an unenforced ceiling (see notes/build/m0.md). Retries are bounded at the broker.
    argv += ["-m", spec.model, "-s", "read-only", "-C", SB_WORK, "--output-schema", f"{SB_IN}/schema.json",
             "--json", "-o", f"{SB_OUT}/last_message.json", "--", spec.prompt]
    return CliLaunch(provider="codex", argv=tuple(argv), env={**COMMON_ENV, **CODEX_ENV},
                     inputs={"schema.json": _schema_bytes(spec.output_schema),
                             "role_contract.md": spec.role_contract.encode()},
                     home_files=(HomeFile(str(auth), ".codex/auth.json"),), stdin_input=None,
                     outputs=("last_message.json",))


def build_call(spec: CallSpec) -> CliLaunch:
    if spec.provider == "claude":
        return claude_call(spec)
    if spec.provider == "codex":
        return codex_call(spec)
    raise ValueError(f"unknown provider {spec.provider}")


def assert_real_codex() -> dict[str, str]:
    """The real Codex binary is a node launcher, not the shell wrapper that falls back to Claude."""
    real = os.path.realpath(REAL_CODEX)
    if not real.endswith("/@openai/codex/bin/codex.js"):
        raise RuntimeError(f"unexpected codex target {real}")
    return {"real": real, "wrapper": str(CODEX_WRAPPER), "claude": os.path.realpath(CLAUDE_LINK)}
