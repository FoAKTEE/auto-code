# Decisions log

| Date | ID | Decision | Status | Rationale |
|---|---|---|---|---|
| 2026-10-15 | G1 | Git commits carry no Claude co-author/attribution trailers | owner instruction | "use git to track without claude coauthorship"; also Chandra's rule "no tool or model attribution of any kind" |
| 2026-10-15 | G4 | Git commit principles follow Chandra exactly: gate files copied byte-identical (`_common/PROVENANCE.md`), hook installed, one commit per DAG node (or finer), strict-mode landing | owner instruction | "git commit principle should follow chandra exactly" |
| 2026-10-15 | G2 | `ref-code/` is untracked (gitignored) | provisional (D6) | contains nested git repos; read-only inputs |
| 2026-10-15 | G3 | Work on branch `zkqec-dev` | provisional (D7) | keep `main` clean until owner merges |
| 2026-10-15 | G5 | Owner: "upon finish prompt editing, proceed to begin the project" — build starts after the v0.4 review is triaged, with the prompt's default decisions D1–D17 adopted (incl. D4 Phase 1a now, Phase 1b conditional; D15 local TLS-inspecting broker; D16 unenforced runs dev-only) | owner instruction | defaults stand unless the owner changes them |
| 2026-10-15 | G6 | Codex disqualified for K0 (declares tools via `additional_tools`); live K0 campaigns use Claude only; Codex runs dev-only `K0-unenforced` (D16) until an alternative is qualified in P1b | qualification evidence | runs/qualify/20261015T105932Z |
| 2026-10-15 | G7 | Aggregate CPU quota unenforceable without root (`Delegate=cpu`); accept concurrency caps + per-process RLIMIT_CPU + wall time; ask owner if a hard aggregate CPU bound is needed | host limitation | J4.slice_cpu unqualified |
