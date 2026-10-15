"""Call-mode CLI builders (contracts §J2, §J3): exact flags, env allowlist, home layout -- offline."""
from __future__ import annotations

import json

import pytest

from zkqec.agents import launch as L
from zkqec.sandbox.profiles import AGENT_ENV_ALLOWLIST, SB_CA, SB_PROXY_URL

SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}


def spec(provider, **kw):
    model = "claude-haiku-5-5" if provider == "claude" else "gpt-6-astra"
    return L.CallSpec(provider, model, "ROLE", "PROMPT", SCHEMA, **kw)


def test_claude_call_flags():
    c = L.claude_call(spec("claude", max_budget_usd=0.05, max_output_tokens=1024, effort="low"))
    a = list(c.argv)
    assert a[0] == "/opt/zkqec/bin/claude" and "-p" in a
    assert a[a.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in a and a[a.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert a[a.index("--system-prompt") + 1] == "ROLE"
    assert json.loads(a[a.index("--json-schema") + 1]) == SCHEMA
    assert a[a.index("--output-format") + 1] == "json" and "--no-session-persistence" in a
    assert a[a.index("--max-budget-usd") + 1] == "0.0500" and a[a.index("--model") + 1] == "claude-haiku-5-5"
    assert "WebSearch" in a and "WebFetch" in a
    assert "PROMPT" not in a and c.inputs["prompt.txt"] == b"PROMPT" and c.stdin_input == "prompt.txt"
    e = c.env
    for k in ("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "CLAUDE_CODE_DISABLE_CLAUDE_MDS", "DISABLE_AUTOUPDATER",
              "DISABLE_TELEMETRY", "DISABLE_ERROR_REPORTING", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
        assert e[k] == "1"
    assert e["HTTPS_PROXY"] == SB_PROXY_URL and e["NODE_EXTRA_CA_CERTS"] == SB_CA
    assert e["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "1024" and e["HOME"] == "/home/agent"
    assert set(e) <= AGENT_ENV_ALLOWLIST
    assert [h.dest for h in c.home_files] == [".claude/.credentials.json"] and c.home_files[0].mode == 0o600


def test_claude_negative_variant_drops_tools_flag():
    a = L.claude_call(spec("claude"), include_tools_flag=False).argv
    assert "--tools" not in a


def test_codex_call_flags():
    c = L.codex_call(spec("codex", effort="low"))
    a = list(c.argv)
    assert a[:2] == ["/opt/zkqec/node/bin/codex", "exec"]
    for f in ("--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--json"):
        assert f in a
    disabled = {a[i + 1] for i, x in enumerate(a) if x == "--disable"}
    assert set(L.CODEX_PROBED_FEATURES) <= disabled and {"unified_exec", "image_generation"} <= disabled
    assert 'web_search="disabled"' in a and 'model_reasoning_effort="low"' in a
    assert any(x.startswith("model_instructions_file=") for x in a)
    assert a[a.index("-s") + 1] == "read-only" and a[a.index("-m") + 1] == "gpt-6-astra"
    assert a[a.index("--output-schema") + 1] == "/job/in/schema.json" and a[a.index("-o") + 1] == "/job/out/last_message.json"
    assert a[-2:] == ["--", "PROMPT"] and c.stdin_input is None
    assert json.loads(c.inputs["schema.json"]) == SCHEMA and c.inputs["role_contract.md"] == b"ROLE"
    assert c.env["CODEX_HOME"] == "/home/agent/.codex" and c.env["SSL_CERT_FILE"] == SB_CA
    assert c.env["CODEX_CA_CERTIFICATE"] == SB_CA and set(c.env) <= AGENT_ENV_ALLOWLIST
    assert [h.dest for h in c.home_files] == [".codex/auth.json"]
    assert "/.local/bin/codex" not in " ".join(a)
    assert c.describe()["argv"][-1] == "<prompt>"


def test_build_call_dispatch_and_errors():
    assert L.build_call(spec("claude")).provider == "claude"
    assert L.build_call(spec("codex")).provider == "codex"
    with pytest.raises(ValueError):
        L.claude_call(spec("codex"))
    with pytest.raises(ValueError):
        L.build_call(L.CallSpec("gemini", "m", "r", "p", SCHEMA))


def test_real_codex_is_node_launcher():
    info = L.assert_real_codex()
    assert info["real"].endswith("@openai/codex/bin/codex.js")
