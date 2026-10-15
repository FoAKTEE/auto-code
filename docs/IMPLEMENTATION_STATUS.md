# Implementation status

Normative claim boundary for zkqec. "Implemented" = code exists; "tested" = automated tests pass; "validated" =
scientific acceptance criterion of the prompt met; "planned" = not built. Update with every milestone.

Status at the end of M1 (Phase 1a), after the second hardening round, pending the Codex re-review
of the third hardening round; the milestone review and the first re-review (`M1-codex-milestone.md`,
`M1-codex-rereview.md`) blocked M1.
Suite: `.venv/bin/python -m pytest -q tests` (slow tests included; the one skip is the live-model test).

| Capability | State | Evidence |
|---|---|---|
| Vendored QECForge kernel (`zkqec.kernel`) | tested | 64/64 upstream tests (`tests/kernel_upstream`); KD-1 fix test-first; KD-2..5 superseded (guard test) |
| Contracts, predicate registry p1a-1, designer contracts + prompt firewall | tested | `tests/contracts`, `tests/firewall`; `roles/FINGERPRINTS.json` |
| Store (sanitize-before-hash, durable publication) | tested | `tests/store`; sanitizer v2 |
| Admission ledger, resolver, revocation, quarantine | tested | `tests/admission` (forgery, conflicts, descendants); semantic-conflict control 0/2, 0/2 |
| Grammars `explicit_sparse`, `two_block_group_algebra`; L0 intake | tested | `tests/grammar`, `tests/orchestrator/test_intake.py` |
| L1 CSS algebra, L2 distance (C2 rule, preflight), L3 signed tableau | validated (M1) | qecforge reference table, Steane t=2 → d=3, positive S-A/S-F fixtures + paired negatives (`tests/verify`, `tests/pipeline`) |
| L2 certification feasibility (this host) | measured | `results/feasibility/M1-certification.md`; D10 ranges `notes/DECISIONS.md` G8 |
| Job state machine, fencing, settlement, §K reservations (invocation totals) | tested | `tests/orchestrator/test_jobs.py`, `tests/pipeline/test_recovery.py`, `tests/agents/test_reservation_bound.py` |
| Bounded execution of verifier, identity, control and confirmation work (child + RLIMIT_CPU/AS + wall kill) | tested | `tests/exec/test_bounded.py`, `tests/exec/test_deadline.py`, `tests/pipeline/test_ceilings.py` |
| Read-only evaluator snapshot (isolated re-exec, runtime facts in the manifest) | tested + live | `tests/exec/test_snapshot.py`; m1-live-5..9 ran from it |
| Live-call barrier for test runs | tested | `tests/sandbox/test_live_guard.py`, `tests/conftest.py` |
| Sandbox (bwrap), campaign slice, TLS-inspecting broker (streaming, fail-closed on log failure, mandatory usage counters) | qualified (M0) | `runs/qualify/20261015T213850Z`: 34 qualified, 3 unqualified (Codex not tool-free ×2; slice CPU quota, G7) |
| ClaudeCall provider (K0) | qualified + live | m1-live-9; Codex: dev-only, `K0-unenforced` (G6) |
| P1a control suite (§I) | tested + live | 20 controls, 0/37 false authorizations, 0/24 false rejections (`notes/build/controls.md`); pass required by the audit; recorded by m1-live-9 |
| Report (ledger-rendered, linted), bundles v2 with broker evidence, confirmation supplement, offline audit | tested + live | `tests/report`; `results/bundles/index.md` (every live bundle re-audited; m1-live-4 fails as designed) |
| M1 live slice (one S-A, one S-F) | done (non-evaluation), acceptance pending re-review | m1-live-9: both targets met, $0.18; cumulative M1 live $3.12; `notes/postmortems/M1-slice.md` |
| ISD, identity relations, theorem registry, twins | planned (M2) | — |
| Multi-agent runtime (Designer-B, skeptic, route agent, commentator) | planned (M3) | — |
| Evaluation campaigns, known-code database, novelty labels | planned (M4) | — |
