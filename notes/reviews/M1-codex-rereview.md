BLOCK M1

## A. Disposition of prior findings

The m1-live-5 scientific results survive review. M1 remains blocked because nested bounded workers can survive their enclosing job’s timeout and continue consuming unaccounted resources. Several accounting and audit gaps also remain.

Reviewed `5b7cf89..af00955`. Independent validation produced **133 passing tests**, with **34 fixture setup errors caused by the read-only filesystem**, plus 19 passing test functions using adapted in-memory fixtures. No repository files were changed. Even `/tmp` was read-only, preventing fresh filesystem-based reconstruction and crash testing.

“Would fail before the fix” below refers to behavioral assertions, not merely imports of newly introduced APIs. Historical comparisons were actually executed for B1, B2 and B7; the other assessments combine current execution and test inspection.

| Finding | Disposition | Code, regression discrimination and Phase 1a residual |
|---|---|---|
| **B1 — blocker: broker logging failure** | **FIXED** | [addon.py:137](/data/haiyangw/claude/auto-code/src/zkqec/broker/addon.py:137), [addon.py:345](/data/haiyangw/claude/auto-code/src/zkqec/broker/addon.py:345). Injected ENOSPC, fsync EIO and short writes through the **actual mitmproxy exception wrapper**, for HTTP and WebSocket hooks, including failed marker publication. HEAD blocked forwarding and disabled dispatch; the predecessor forwarded requests. Successful short writes were completed. The recorded real-mitmdump qualification also passes. **Acceptable; no further fix for this defect.** |
| **B2 — major: incomplete usage** | **PARTIAL** | [policy.py:596](/data/haiyangw/claude/auto-code/src/zkqec/broker/policy.py:596). Truncated SSE now returns unknown; the predecessor returned partial counters. Plain/gzip truncation-and-retry regressions discriminate the original defect. However, complete framing with missing counters still becomes complete telemetry with zero input usage: **N2**. **Not closed for Phase 1a.** |
| **B3 — major: failed/missing controls** | **FIXED** | [campaign.py:398](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:398), [record.py:45](/data/haiyangw/claude/auto-code/src/zkqec/controls/record.py:45). Failure evidence is retained; completeness and pass/fail are separate. Regression assertions would reject the predecessor’s clean audit after failed/missing controls. Independently removing the required control record was detected. m1-live-4’s failed audit corroborates this behavior. **Acceptable.** |
| **B4 — major: cross-profile reuse** | **FIXED** | [service.py:404](/data/haiyangw/claude/auto-code/src/zkqec/admission/service.py:404), [render.py:46](/data/haiyangw/claude/auto-code/src/zkqec/report/render.py:46). Execution identity and evidence resolution are namespaced. Five namespace regressions passed with in-memory fixtures; their reuse/isolation assertions would fail before the fix. Schema-1 ledgers fail closed under the new registry/schema check. **Acceptable.** |
| **B5 — major: compute ceilings** | **PARTIAL** | [service.py:176](/data/haiyangw/claude/auto-code/src/zkqec/admission/service.py:176), [bounded.py:159](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:159). CPU, allocation, wall-time and composed-timer regressions distinguish the predecessor. An outer 0.1-second timer now survives an inner 0.5-second deadline. But nested process groups defeat enclosing termination: **N1, blocker**. **Not acceptable.** |
| **B6 — major: broker evidence/confirmation** | **PARTIAL** | [launcher.py:222](/data/haiyangw/claude/auto-code/src/zkqec/broker/launcher.py:222), [bundle.py:701](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:701). Broker evidence is sealed and reconciled; the actual confirmation supplement validates. Original missing/tampered-evidence regressions meaningfully reject predecessor behavior. Final supplement enforcement and numeric validation remain incomplete: **N4–N5**. **Not closed.** |
| **B7 — major: ancestor durability** | **FIXED** | [durable.py:55](/data/haiyangw/claude/auto-code/src/zkqec/store/durable.py:55). Publication synchronizes existing ancestors through the durable root. An already-existing-directory retry model synchronized the full chain at HEAD; the predecessor synchronized only the immediate parent. Crash-step regressions discriminate this behavior. Actual filesystem crash testing was unavailable here. **Acceptable on the implementation and recorded tests.** |
| **B8 — major: invocation-total reservations** | **FIXED** | [providers.py:38](/data/haiyangw/claude/auto-code/src/zkqec/agents/providers.py:38). Request ceilings use `total // requests`; the USD floor covers invocation totals, and zero-request live reservations are refused. Retry-cap assertions passed and would fail with the predecessor’s multiplied allowance. **Acceptable.** |
| **#7 — major: full operational replay** | **PARTIAL** | [jobs.py:422](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/jobs.py:422). Edited `input_sha` and deleted confirmation-job reproductions are now rejected, unlike the previously reviewed code. But replay ignores completion usage and portions of settlement history: **N3**. **Not closed.** |
| **#13 — major: ceilings/confirmation** | **PARTIAL** | [campaign.py:385](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:385), [campaign.py:444](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:444). Controls and confirmation now enter bounded workers; the original timeout/charging assertions discriminate predecessor behavior. **N1 and N4–N5 remain; not acceptable as complete enforcement.** |
| **#14 — major: evaluator snapshot** | **FIXED** | [snapshot.py:45](/data/haiyangw/claude/auto-code/src/zkqec/exec/snapshot.py:45), [snapshot.py:132](/data/haiyangw/claude/auto-code/src/zkqec/exec/snapshot.py:132). Independently re-executed the existing snapshot with hostile `PYTHONPATH`: loaded evaluator modules came from the snapshot, isolation flags were set, archive override worked, and files had nonzero mtimes. Snapshot-origin/read-only regressions would fail before this implementation. **Acceptable under §O’s trusted-runtime boundary; dependencies are discussed below.** |
| **#15 — major: bundle/call completeness** | **FIXED for the original retention defect** | [campaign.py:242](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:242), [bundle.py:214](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:214). Input artifacts and references precede dispatch; broker evidence and executable closures are retained. Crash-during-call retention assertions would fail before the fix. **Acceptable for retention; final validation remains B6.** |
| **#16 — major: publication durability** | **FIXED** | [durable.py:74](/data/haiyangw/claude/auto-code/src/zkqec/store/durable.py:74). Same repair and discriminating retry reproduction as B7. **Acceptable with the filesystem-test limitation stated above.** |

## B. New defects and remaining gaps

### N1 — Blocker: nested workers escape the enclosing wall kill

**Location:** [bounded.py:112](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:112), [bounded.py:151](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:151), [bounded.py:196](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:196).

Every `run_bounded` child creates a new process group. When a bounded control suite starts a bounded pipeline verifier, that verifier leaves the outer worker’s group. Killing the outer group therefore leaves the inner verifier running and removes its watchdog.

**Reproduction:** an outer 0.2-second limit returned `wall_exceeded` after approximately 0.201 seconds, charging approximately 0.007 CPU-seconds. Its inner busy worker remained in runnable state and accumulated **19 additional CPU ticks during the next 0.2 seconds**. I explicitly killed the surviving group afterward.

This composition occurs through [campaign.py:385](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:385) → control [Env.pipeline](/data/haiyangw/claude/auto-code/src/zkqec/controls/env.py:104) → bounded admission execution. It is a resource-containment failure, independent of the documented absence of aggregate CPU quota.

**Fix:** supervise the entire nested process tree, preferably with a per-job cgroup or an external worker supervisor. Confirm all descendants have terminated before settlement; retain unresolved charges otherwise. Add a regression that expires an outer control/confirmation job while an inner worker is busy and checks both liveness and accounting afterward. The existing same-process-group descendant test does not cover this.

### N2 — Major: complete framing still turns missing usage into zero

**Location:** [policy.py:629](/data/haiyangw/claude/auto-code/src/zkqec/broker/policy.py:629), [providers.py:140](/data/haiyangw/claude/auto-code/src/zkqec/agents/providers.py:140), [bundle.py:775](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:775).

A stream containing `message_start` with no initial usage, a final delta with `output_tokens: 20`, and `message_stop` is accepted as complete. The provider records zero input tokens through `.get(..., 0)`. The auditor uses the same assumption. JSON responses have the analogous missing-counter problem.

**Reproduction:** this complete-but-incomplete-accounting stream returned reported usage containing only `output_tokens`; its missing input counter became zero. The original truncated-stream reproduction correctly returned unknown.

**Fix:** validate mandatory counters before marking telemetry complete, or track completeness separately for each accounting unit. Missing mandatory counters must retain their reservation. Add complete SSE/JSON cases with missing counters, including retry sequences.

### N3 — Major: operational replay does not replay the full accounting history

**Location:** [jobs.py:460](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/jobs.py:460), [jobs.py:354](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/jobs.py:354).

Replay retains completion `result_sha` and settlement `charged`, but does not reconcile completion `actual` or settlement `from` against the authoritative settlement and transition history.

**Reproduction:** in separate in-memory copies of the sealed ledger, changing completion `actual.usd` to `999999`, or changing settlement `from` to `uncertain`, still produced `verify=True` and `audit_campaign=[]`. I temporarily removed and restored the copied database’s update triggers to model corrupted evidence; ordinary production updates remain protected.

**Fix:** replay and validate the complete operational record, including actual usage, outcome, settlement source and charging rule, against the hash-chained settlement. Add these mutations to the audit regression suite. The repaired input-hash/deleted-confirmation tests do not cover them.

### N4 — Major: final audit can omit the confirmation supplement

**Location:** [cli.py:66](/data/haiyangw/claude/auto-code/src/zkqec/cli.py:66), [bundle.py:291](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:291), [bundle_index.py:50](/data/haiyangw/claude/auto-code/scripts/bundle_index.py:50).

The public audit command does not supply a supplement. The index checks one only if it exists, and labels any absence `none (pre-B6)` without establishing that the bundle actually predates B6. Consequently, a post-B6 bundle can pass final audit/indexing with only its reserved confirmation checkpoint and no final accounting supplement.

This is established by the control flow; I did not delete archive files.

**Fix:** distinguish internal pre-confirmation replay from final acceptance audit. Require the supplement for applicable bundle versions, discover it in the public CLI, and fail indexing when it is missing. Preserve explicit compatibility for genuinely older bundles.

The m1-live-5 supplement **exists and validates**; this finding does not invalidate its recorded totals.

### N5 — Major: NaN bypasses confirmation-total reconciliation

**Location:** [bundle.py:354](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:354), [bundle.py:423](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:423).

The comparison `abs(float(spent) - expected) > 1e-9` accepts NaN because that comparison is false.

**Reproduction:** the valid m1-live-5 supplement passed. Changing only `final_totals.usd.spent` to NaN also passed; changing it to an incorrect finite value was rejected. The check used the verified existing extraction instead of writing temporary files.

**Fix:** validate all supplement totals as finite, nonnegative numbers before comparison; reject nonstandard JSON constants and inappropriate types. Add NaN/infinity/missing-field regressions. Existing finite-value tampering tests miss this case.

### N6 — Minor: residual-risk documentation contradicts the implementation

**Location:** [M1-slice.md:159](/data/haiyangw/claude/auto-code/notes/postmortems/M1-slice.md:159).

The postmortem still says snapshot import is deferred to M2 and verifier CPU is not interrupted. Both statements describe superseded behavior and conflict with the binding triage.

**Fix:** replace them with the actual remaining limitations, including nested-worker containment and the distinction between incremental address-space limits and resident-memory limits.

### Other requested risk checks

- **Forking with locks/threads — minor for this sequential Phase 1a run; address before concurrent dispatch.** At [bounded.py:165](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:165), an inherited lock held by another parent thread can strand the child. A held-lock reproduction timed out rather than returning false evidence. I found no evidence that this happened in m1-live-5. Use clean executable workers or an appropriately initialized forkserver, and explicitly control inherited database handles before introducing concurrent evaluator work.
- **RLIMIT_AS is not an RSS cap.** [bounded.py:118](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:118) permits inherited virtual address space plus the allowance. A worker touching an inherited 128 MiB mapping succeeded with a 16 MiB allowance and reached approximately 147 MiB RSS. That matches the documented incremental-AS semantics; it does not establish an absolute or aggregate memory ceiling.
- **Not every API invocation is independently bounded.** Admission permits `limits=None` at [service.py:175](/data/haiyangw/claude/auto-code/src/zkqec/admission/service.py:175), and standalone audit replay has inline execution paths. Campaign controls/confirmation supply an enclosing worker, whose containment is precisely the N1 problem.
- **Snapshot isolation passed within its stated trust boundary.** The evaluator imports from the snapshot, but [snapshot.py:156](/data/haiyangw/claude/auto-code/src/zkqec/exec/snapshot.py:156) explicitly adds the invoking environment’s site-packages and [snapshot.py:135](/data/haiyangw/claude/auto-code/src/zkqec/exec/snapshot.py:135) links `.venv-broker`. These are trusted runtime dependencies, not an immutable copy of the entire runtime. Archive override, source origins, commit identity and mtime stamping checked out.
- **Namespace compatibility is fail-closed.** Existing schema-1 ledgers are refused by the new evaluator; historical bundles retain their embedded evaluator. Controls use separate scratch databases, avoiding cross-run reuse despite common scratch labels.
- **Sealed broker evidence passed inspection.** The actual secret scan, broker-log reconciliation and sidecar checks returned no problems. The test-suite live-call barrier is present; the I10 bundle is quarantined and excluded from acceptance evidence.

## C. M1 acceptance and Phase 1a definition of done

The literal experimental §M1 Accept checklist is satisfied. That does **not** close the binding hardening requirements or override D8’s containment-blocker rule.

| §M1 Accept item | Result | Evidence |
|---|---|---|
| qecforge reference table | **PASS** | Independently rerun [test_verify_l2.py:53](/data/haiyangw/claude/auto-code/tests/verify/test_verify_l2.py:53). |
| Steane `t=2` certifies d=3, not d≥5 | **PASS** | Independently rerun [test_verify_l2.py:73](/data/haiyangw/claude/auto-code/tests/verify/test_verify_l2.py:73). |
| Deterministic S-A and S-F through production resolver, without LLM | **PASS** | Both positive test functions rerun with in-memory storage: [test_positive_cases.py:49](/data/haiyangw/claude/auto-code/tests/pipeline/test_positive_cases.py:49) and [test_positive_cases.py:76](/data/haiyangw/claude/auto-code/tests/pipeline/test_positive_cases.py:76). |
| Paired negatives rejected | **PASS** | Both paired-negative functions passed in the same harness. |
| Admission-only proof | **PASS** | Previously accepted proof remains at [test_forgery.py:35](/data/haiyangw/claude/auto-code/tests/admission/test_forgery.py:35); current ledger verification and execution replay passed. |
| Lower-5/upper-3 conflict quarantines and authorizes neither verdict | **PASS** | [test_conflicts.py:26](/data/haiyangw/claude/auto-code/tests/admission/test_conflicts.py:26), retained control evidence, and passing namespace/quarantine checks. |
| Live S-A/S-F within cap, sealed and reconstructable, correctly labelled | **PASS** | [m1-live-5 report](/data/haiyangw/claude/auto-code/runs/campaigns/m1-live-5/report.md:19), [bundle index](/data/haiyangw/claude/auto-code/results/bundles/index.md:11), and verification below. |
| Cost telemetry recorded | **PASS** | Broker evidence and linked final supplement reconcile; final totals below. |
| Postmortem written | **PASS**, with minor N6 | [M1-slice.md](/data/haiyangw/claude/auto-code/notes/postmortems/M1-slice.md:8). |

For **m1-live-5**, I independently verified:

- Bundle SHA-256 `924c2f02e3d7f3553c9bd28e27ebc38684631c1e1c04dbf8b9f598097151ccf2`.
- All **53 regular bundle members** match the existing extraction; evaluator, runtime and wheel closure hashes match.
- Ledger verification, artifact verification, campaign audit, broker reconciliation, sidecar checks and report lint returned no problems.
- Exact execution replay reproduced **five verifier executions and two identity executions**.
- The control artifact records **20 controls**, **0/37 false authorizations**, **0/24 false rejections**, and zero outcome mismatches.
- The confirmation supplement links sealed ledger sequence **50** to final sequence **51**. Wrong bundle/head and incorrect finite totals were rejected.
- Final spending is **$0.6774558**, **2 requests**, **6,503 input tokens**, **33,807 output tokens**, approximately **44.012 CPU-seconds** and **376.222 wall-seconds**, with all reservations closed.

Fresh reconstruction into a newly created environment was **not rerun** because filesystem writes were unavailable. Offline reconstruction is supported by the recorded confirmation/re-indexing, independently verified archive contents, and exact replay from those contents.

The recorded cumulative M1 spend is **$2.19/$5**, including the incident expenditure. m1-live-4 remains failed evidence; the quarantined I10 test-run bundle is not counted as acceptance evidence.

| Phase 1a definition of done | Result |
|---|---|
| Qualification report: each property passes or has an explicit unqualified consequence | **PASS:** [qualification report](/data/haiyangw/claude/auto-code/runs/qualify/20261015T200053Z/report.md:4), 34 qualified / 3 unqualified. Claude K0 qualifies; Codex does not count toward DoD. |
| All enumerated M1 acceptance items pass | **PASS for the checklist above.** Mandatory hardening remains incomplete under the binding triage. |
| Two live tasks have offline-reconstructable bundles | **PASS on recorded reconstruction and independently checked evidence**, subject to the fresh-run limitation above. |
| Every prior review is triaged | **PASS:** [M1-triage.md](/data/haiyangw/claude/auto-code/notes/reviews/M1-triage.md:18). Triage is not equivalent to closure. |

D9’s archived/indexed evidence, D10’s domain and measured feasibility basis, D11’s algebra-only labels, and D16’s exclusion of unqualified Codex results remain satisfied. **Overall M1 acceptance fails because N1 is an open containment blocker and the binding hardening work is incomplete.**

## D. D4 gate and Phase 1b projection

**D4 is closed.** M1 is not accepted, N1 is open, and the stated workload projection does not fit both D5 ceilings using the latest S-A telemetry.

**Major — stale gate projection.** At [M1-slice.md:147](/data/haiyangw/claude/auto-code/notes/postmortems/M1-slice.md:147), the main calculation still uses m1-live-3’s approximately $0.31 S-A call and an unmeasured 30% role overhead. Using m1-live-5’s **$0.6397666** S-A call:

| Workload, retaining the assumed 1.3× overhead | Projection | Cap |
|---|---:|---:|
| Smoke: 8 tasks × 2 proposals | **$13.31** | $10 |
| Pilot: 24 tasks × 2 arms × 6 proposals | **$239.53** | $150 |

Reservation-enforced caps prevent exceeding the budget; they do not demonstrate that the projected workload fits it.

The projection is acceptable as a **provisional planning input**, and the binding triage correctly schedules measured role overhead for M3. It is **not sufficient to open D4 as written**. Resize the M3 smoke workload before dispatch; measure every role and retries; explicitly handle Codex’s unqualified status and missing price basis; then recompute and resize the pilot before any Phase 1b pilot dispatch.