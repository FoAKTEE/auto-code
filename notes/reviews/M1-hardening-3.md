# M1 hardening round 3, engineer E3: N1–N5

Response to `M1-codex-rereview.md` under the binding triage `M1-triage-2.md`. Every regression
failed before its fix; each commit's `verify:` says how.

| # | Commit | Regression test(s) | Change | Residual risk |
|---|---|---|---|---|
| N1 | 92dcf66 | `tests/exec/test_bounded.py` (Codex repro, re-executed evaluator, nested deadline, straggler, escape); `test_an_expired_control_job_leaves_no_inner_worker_and_retains_its_charge` (`/proc` empty, `wall_exceeded`, CPU charged at its reservation); escaped controls, confirmation, verifier | Bounded children mark the process (flag + `ZKQEC_BOUNDED_PGID`, inherited by the confirmation's re-executed evaluator). Nested runs stay in the enclosing group and are killed by pid. The top-level parent kills the group's remainder and waits for `ESRCH`. Survivors after 5 s mean `escaped`, which callers settle `uncertain`. CPU the child never reaped is omitted from `usage()`, so it is charged at the reservation. | A nested deadline leaves its child's descendants alive until the enclosing job ends. CPU of a descendant exiting after its parent died goes unmeasured. `escaped` is tested with a stubbed probe. |
| N2 | 89d2459 | `test_usage_requires_its_mandatory_counters`; real broker, SSE/JSON × input/output × alone/retry; `tests/agents/test_usage_counters.py`; `test_a_missing_input_counter_is_never_audited_as_zero` | `charged_tokens` gives tokens only when `input_tokens` (`message_start`/JSON) and `output_tokens` (final delta/JSON) are non-negative ints. Broker, `summarize_job`, provider and `audit_broker` never default a missing counter. Cache counters that are absent (or null) count as 0; malformed ones mean unknown usage. | `policy.py` is pinned: re-qualify the broker before m1-live-6. |
| N3 | b1dc9b1 | both Codex mutations on a sealed-ledger copy (triggers dropped/recreated); clean control | The `ops_log` replay tracks completion outcome and `actual`. Each settlement record must match its replayed from-state and charge `charge_for(state, reservation, actual)`. `reconcile` checks `actual`, `from_state` and `charged` against the hash-chained `job_settled` event. | Ops tables stay mutable: tampering is detected, not prevented. |
| N4 | 38b9945 | declaration, discovery (CLI included), legacy inference, index failure, archived classification | `seal` declares `confirmation: required` (broker evidence) or `not_required`. The format stays v2: the field is additive and the auditing code decides. An undeclared bundle with broker evidence is required (inferred); one with neither is pre-B6. `audit_bundle` is final by default (it discovers `<sha>.confirmation.json`); `_confirm` passes `final=False`. The index exits 2 when a required supplement is missing. | Final-acceptance tests stub the reconstruction (slow round trips cover it). `results/bundles/index.*` has not been regenerated. |
| N5 | 46676b1 | 10 totals cases (NaN, Infinity, 1e999, negative, string, bool, missing/extra field, missing unit, non-object); `-Infinity` anywhere | `load_supplement` refuses NaN/Infinity (`parse_constant`). `final_totals` must be exactly `{unit: {spent, open}}` of finite, non-negative, non-bool numbers. Summed charges must pass `check_usage(complete=True)`. | — |

**Archived bundles under N4.** m1-live-1..3 hold no broker evidence and are labelled `none (pre-B6)`.
m1-live-4 and m1-live-5 sealed broker evidence before the declaration existed, so their supplement
is required by inference; both are `linked`. m1-live-5 checks clean. m1-live-4 reports only its
failed confirmation audit and stays failed evidence. A scratch re-index matched `results/bundles/index.json`
(problems, totals).

**Suite** at 46676b1, slow tests included: `795 passed, 1 skipped in 333.20s` (skip: opt-in live
qualification).
