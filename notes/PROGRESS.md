# Progress log

## 2026-10-15 — M0: analysis and plan (in progress)
- Read `ref-code/qecforge` in full (paper, appendices, docs, all source, tests, configs, schemas, artifacts).
  Verified here: 60 core tests pass, 4 optional skipped (stim/ldpc/pymatching absent; pip can install them).
- Reference-workflow analyses written by three parallel read-only agents:
  `notes/ref-analysis/{qa,chandra,question-gen}.md`.
- Environment probes: headless `claude -p --output-format json` works (reports tokens/cost);
  `codex exec` (gpt-6-astra, max) works when the prompt is passed as an argument with `< /dev/null`;
  `bwrap` isolation works (masking, no network, read-only FS).
- Draft master prompt: `prompt/00-master-prompt.md` (v0.2). Codex blind analysis and prompt review running.
- Build has NOT started; waiting for owner approval of the prompt (decisions D1–D9).
- Commit discipline switched to Chandra's, byte-identical: `.gitmessage`, `_common/hooks/{commit-msg,install.sh,README.md}`,
  `_common/contracts/commit_template.md`, `tests/test_commit_msg_gate.py`, `.claude/skills/commit-gated/SKILL.md`;
  rule restated in `AGENTS.md`; hook installed (`core.hooksPath=_common/hooks`); gate self-test 23 passed.
  The first (non-compliant) M0 commit was unpushed and was re-landed as node-scoped gated commits.
- Codex blind analysis (`prompt/reviews/codex-independent-analysis.md`) and v0.2 review
  (`prompt/reviews/codex-prompt-review.md`, 35 findings, 6 blockers) received. Reproduced here: qecforge
  `analyze_postselected_map` accepts a non-unitary target at tol 1e-10 and passes a 1-rad relative phase on a tiny
  Kraus operator; demo operator bound 0.07 -> observable bias 0.14 > 0.1 target.
- Host facts added: socat present; user cgroup scopes (`systemd-run --user --scope -p MemoryMax -p TasksMax`) work;
  codex web search only with `--search`.
- Prompt v0.3: phased plan (Phase 1 = M0 host qualification, M1 live slice, M2 verifier hardening, M3 agent runtime,
  M4 evaluation; algebra-level claims only), binding appendix `prompt/01-technical-contracts.md`, triage
  `prompt/reviews/TRIAGE.md`. Codex review of v0.3 launched. Build still waits for owner approval (D1–D16).
- Codex review of v0.3 (`prompt/reviews/codex-prompt-review-v0.3.md`): "with changes"; 17 triage gaps + 12 new
  findings (2 blockers: CONNECT cannot enforce API policy; no process isolation). Host check: codex enables
  shell/multi-agent/apps/browser/computer-use/plugins by default.
- Prompt v0.4: Phase 1a (M0 host qualification incl. TLS-inspecting broker, M1 narrowed live slice) approved first;
  Phase 1b (M2–M4) continues only under D4 conditions; appendix v0.4 fixes distance completion rule (Steane t=2),
  Singleton "if", sector-correct disjoint bound, predicate-keyed transport, revocation/quarantine, dispatch state
  machine, fsync-dir publication, sanitize-before-hash, offline executable bundles, 15-mode control mapping.
- Codex v0.4 verification review: "yes-with-listed-edits — Phase 1a"; five majors fixed in v0.5 (input-charge
  reservations, dispatch recovery moved into M1, semantic bound conflicts, slice deadline via supervisor on
  systemd 249, archived runnable runtime).
- M0 probe (mitmproxy 11.0.2 in `.venv-broker`): Claude under normal HOME leaks ~20 claude.ai connector tools even
  with `--tools ""`; fresh HOME + strict empty MCP config gives tools {StructuredOutput, DeferredToolPlaceholder}
  only. Codex speaks WebSocket to chatgpt.com; with features disabled its frames have no tools key. TLS interception
  works for both. `~/.local/bin/codex` silently falls back to Claude on usage limits; use `~/.local/node/bin/codex`.
- Prompt v0.5 is final for Phase 1a; owner instructed to proceed with defaults (G5). Build started: `.venv` (uv,
  pinned + hash lock), kernel vendored byte-identically, 64/64 upstream tests pass.
