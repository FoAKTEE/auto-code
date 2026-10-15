# prompt/ — plans for owner review before the build starts

- `00-master-prompt.md` — the self-prompt Claude will follow for the full build (draft; see §14 for the
  decisions that need your answer, §0 for a one-page summary).
- `reviews/codex-independent-analysis.md` — Codex (gpt-6-astra, max) blind analysis of the mission and
  reference workflows, written without seeing the master prompt.
- `reviews/codex-prompt-review.md` — Codex review of the master prompt.
- `reviews/TRIAGE.md` — how each Codex finding was handled (accepted / changed / rejected with reason).

Supporting analyses of the reference workflows: `notes/ref-analysis/{qa,chandra,question-gen}.md`.
