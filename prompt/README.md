# prompt/ — plans for owner review before the build starts

Read in this order:
1. `00-master-prompt.md` — the self-prompt Claude will follow (v0.5, final for Phase 1a). §0 is a one-page summary; §14 lists the
   decisions that need your answer (D1–D17, defaults in bold).
2. `01-technical-contracts.md` — binding semantics (§A–§O): typed propositions and entailment, design-candidate
   contract, distance certification, code identity/novelty, theorem registry, logical-action verification,
   circuit level (Phase 2), statistics, controls, sandbox/egress launch contract, budgets, transactions,
   artifact retention, namespacing, environment freeze.
3. `reviews/TRIAGE.md` — disposition of every review finding (rounds 1–3).

Reviews (Codex gpt-6-astra, reasoning max, read-only):
- `reviews/codex-independent-analysis.md` — blind analysis of the mission and reference workflows (did not see
  the prompt).
- `reviews/codex-prompt-review.md` — review of v0.2 (35 findings).
- `reviews/codex-prompt-review-v0.3.md` — review of v0.3.
- `reviews/codex-prompt-review-v0.4.md` — verification review of v0.4 ("yes-with-listed-edits — Phase 1a"); edits applied in v0.5.

Supporting analyses of the reference workflows: `notes/ref-analysis/{qa,chandra,question-gen}.md`.
