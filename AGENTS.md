# AGENTS.md — binding rules for every agent (Claude, Codex, subagents) in this repo

- **Commits.** One commit per DAG node (or finer); tests before/with the code
  they verify; the title grammar of `_common/contracts/commit_template.md`,
  enforced by the gate installed with `bash _common/hooks/install.sh`; no tool
  or model attribution of any kind; never commit large datasets or mirrors.
  *(Verbatim from Chandra `AGENTS.md`; see `_common/PROVENANCE.md`.)*
  - Procedure: `.claude/skills/commit-gated/SKILL.md` — write the message to a file, check it with
    `COMMIT_GATE_STRICT=1 _common/hooks/commit-msg <file>`, stage only the node's files, inspect
    `git diff --cached`, land with `COMMIT_GATE_STRICT=1 git commit --file <file>`. Never `--no-verify`.
  - A DAG node here is one milestone sub-task of `prompt/00-master-prompt.md` §11, tracked in
    `notes/PROGRESS.md`.
- **Never modify `ref-code/`.** It holds read-only reference repositories (gitignored).
