# P1a control suite (contracts §I; M1 acceptance #17)

`zkqec.controls` runs each case in its own campaign-built scratch evaluator. Campaigns with
`"controls": "p1a"` run it before sealing as a reserved `verify` job and record it
(`controls_recorded`); `audit_inner` re-runs it and requires equal sha256 and rows.

**Observed at 5bc3182: k = 0/37, j = 0/24, no outcome mismatches.**

| control (§I) | gate | injections → expected | k/N | j/M |
|---|---|---|---|---|
| task_mutated (#1) | frozen-task hash; schema | edited task file → aborted; `targets` field → rejected; weaker `requested` → refuted | 0/3 | 0/1 |
| invalid_css (#3) | L1 | non-commuting → refuted | 0/1 | 0/1 |
| incomplete_basis (#3) | L3 basis | truncated, unpaired → rejected | 0/2 | 0/1 |
| timeout_as_bound (#4) | L2 completion | preflight refusal, killed run (twice) → unknown | 0/3 | 0/1 |
| s_vs_sdg (#5) | L3 signs | global S claimed as S → refuted | 0/1 | 0/1 |
| spoofed_issuer (#14) | writer, registry, replay | no capability, issuer `designer`, forged chain → rejected | 0/3 | 0/1 |
| proposer_owned_fields (#14) | intake | owned fields, certificate → rejected | 0/3 | 0/1 |
| evaluator_tamper (#14) | fingerprint | edited source, new module → aborted | 0/2 | 0/1 |
| duplicate_completion (#15) | job fencing | duplicate, stale → rejected; retry reusing key → charged | 0/3 | 0/2 |
| semantic_conflict | quarantine | lower 5, upper 3 → quarantined | 0/2 | 0/2 |
| steane_t2_d5_misreading | L2 w* | Steane, d 5 → refuted | 0/1 | 0/1 |
| chain_l2_declared_d6 | L2 | chain(2,7), d 6 → refuted | 0/1 | 0/2 |
| witness_as_lower_bound | resolver | weight-6 witness, d 6 → unknown | 0/1 | 0/1 |
| wrong_automorphism | L3 | transposition, inverse shift → refuted | 0/2 | 0/1 |
| tampered_or_stale_artifact | hash reads; domain | edited bytes → rejected; superseded domain → unknown | 0/2 | 0/2 |
| revoked_premise | resolver | revoked bound or identity → unknown | 0/2 | 0/1 |
| real_field_rank | GF(2) rank | real-field k = 0 → refuted | 0/1 | 0/1 |
| solver_claim_as_proof | §A2 authority | verified → rejected; unknown → unknown | 0/2 | 0/1 |
| source_requests_evaluator_change | frozen evaluator | edit request in text → refuted | 0/1 | 0/1 |
| presentation_weight_transport | presentation lookup | heavy generators, same code → refuted | 0/1 | 0/1 |

**Deferred (in the evidence, never counted):** torus_collision and weight transport across
qubit-permuted presentations (need §D equivalence relations, M2); 15-mode #1-transform (P3), #2,
#13 (P1b), #6, #9, #10 (P3), #7 (P2/P3), #8, #11 (P2), #12 (P2/P1b).

**Gate flips (tests):** disabling `check_task`, revocation or Pauli signs flips its control.

**Residual risks.** A rebuilt self-consistent forged chain passes the resolver and `verify`; only
the audit replay rejects it (§O). Conflict and solver_claim evidence comes from a scratch-only
fault issuer.
