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
