# Decisions log

| Date | ID | Decision | Status | Rationale |
|---|---|---|---|---|
| 2026-10-15 | G1 | Git commits carry no Claude co-author/attribution trailers | owner instruction | "use git to track without claude coauthorship"; also Chandra's rule "no tool or model attribution of any kind" |
| 2026-10-15 | G4 | Git commit principles follow Chandra exactly: gate files copied byte-identical (`_common/PROVENANCE.md`), hook installed, one commit per DAG node (or finer), strict-mode landing | owner instruction | "git commit principle should follow chandra exactly" |
| 2026-10-15 | G2 | `ref-code/` is untracked (gitignored) | provisional (D6) | contains nested git repos; read-only inputs |
| 2026-10-15 | G3 | Work on branch `zkqec-dev` | provisional (D7) | keep `main` clean until owner merges |
