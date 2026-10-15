# Provenance of the commit-gate files

Copied byte-identical from the owner's Chandra repository (`ref-code/Chandra`, commit `89a453b7667104bd89cc1941e7d6c6e521ae1d9f`) on 2026-10-15,
per the owner's instruction that git commit principles follow Chandra exactly. Do not edit in place;
re-copy from Chandra and update this table if the upstream gate changes.

| file here | source path in Chandra | sha256 |
|---|---|---|
| `.gitmessage` | `.gitmessage` | `4b3aaf8ce6c465a9…` |
| `_common/hooks/commit-msg` | `_common/hooks/commit-msg` | `b2a7caa83fb96598…` |
| `_common/hooks/install.sh` | `_common/hooks/install.sh` | `3bc47082ae7d6e07…` |
| `_common/hooks/README.md` | `_common/hooks/README.md` | `97f9fb52d585f16e…` |
| `_common/contracts/commit_template.md` | `_common/contracts/commit_template.md` | `96277da8b092c274…` |
| `tests/test_commit_msg_gate.py` | `tests/test_commit_msg_gate.py` | `2d834f720b38c589…` |
| `.claude/skills/commit-gated/SKILL.md` | `.claude/skills/commit-gated/SKILL.md` | `95dea3714cd7e8ea…` |

Chandra-specific references inside these files that have no counterpart here: `_common/contracts/progress_principles.md`
(wave/packet commit cadence of Chandra's TypeScript orchestrator) and `orchestrator/src/gitops.ts`. The binding
commit rule they support is restated verbatim in `AGENTS.md` ("Commits.").
