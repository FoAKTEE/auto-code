BLOCK M1

The three original blocker mechanisms are fixed. However, a newly reproduced broker failure bypasses containment when logging fails, and several accounting, isolation, durability, and reconstruction requirements remain incomplete.

Reviewed `3753ffa..4f4c4bf`; no files modified. Validation: 178 tests passed; 31 fixture setups were blocked by the read-only filesystem. Separately, 30 regression functions passed using in-memory storage. I verified the live archive’s hash and reproduced five admitted executions and two identities from an extraction whose evidence files match the archive. I did not rerun filesystem crash tests or fresh offline environment construction.

## A. Disposition of the 17 prior findings

Severity below is the original finding’s severity. “Meaningful” means the regression assertions distinguish the reported pre-fix behavior; I did not rerun the entire historical tree.

| # | Status / severity | Implementation and regression assessment | Residual risk and required action |
|---|---|---|---|
| **1. Forged receipts** | **FIXED — blocker** | [ledger.py:273](/data/haiyangw/claude/auto-code/src/zkqec/admission/ledger.py:273) validates execution registration; receipt and closure application enforce ownership and exact correspondence. [ledger.py:635](/data/haiyangw/claude/auto-code/src/zkqec/admission/ledger.py:635) requires the writer capability. `test_forgery.py` meaningfully tests unauthorized writes, open executions, extra receipts, and forged chains. | **Acceptable under §O:** a compromised trusted writer remains outside the guarantee. A self-consistent forged closure is rejected by exact replay. No further fix for the original bypass. |
| **2. Unfenced recovery** | **FIXED — blocker** | [pipeline.py:163](/data/haiyangw/claude/auto-code/src/zkqec/pipeline.py:163) reads settled closures and creates separately reserved retries for abandoned executions. `test_settled_job_without_closure_retries_under_a_new_fenced_job` reproduces the settlement interruption and verifies a new charged job. | **Acceptable for recovery within one namespace.** The separate cross-profile reuse defect is B4. |
| **3. Caller-controlled report authority** | **FIXED — blocker** | [render.py:39](/data/haiyangw/claude/auto-code/src/zkqec/report/render.py:39) derives tasks, membership and spending from ledger events. `test_ledger_anchored.py` meaningfully rejects substituted tasks, fabricated charges and injected labels. | **Acceptable for the original report-input bypass.** Remaining audit and retention problems are #7/#15. |
| **4. Invalid ancestors** | **FIXED — major** | [evidence.py:64](/data/haiyangw/claude/auto-code/src/zkqec/admission/evidence.py:64) requires authorizing, verified parents; [resolver.py:99](/data/haiyangw/claude/auto-code/src/zkqec/admission/resolver.py:99) removes conflict descendants to a fixed point. Both targeted regressions exercise the former failures. | **Acceptable.** Unknown/timed-out parents and excluded conflict premises no longer leave authoritative descendants. |
| **5. Partial usage and NaN** | **FIXED — major**, for the settlement rule | [contracts/jobs.py:60](/data/haiyangw/claude/auto-code/src/zkqec/contracts/jobs.py:60) rejects invalid usage; `charge_for` retains reservations for unknown units and uncertain outcomes. Missing-USD, NaN and interrupted-call regressions are meaningful. | **The stated broker-log assumption is not acceptable:** B1 and B2 can make telemetry incomplete or falsely complete. The settlement arithmetic is fixed; its production inputs still need repair. |
| **6. Job ownership/idempotency** | **FIXED — major**, for `JobBook` | [jobs.py:226](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/jobs.py:226) scopes reservations and checks request identity; lookups and updates enforce namespace ownership. Cross-campaign, arm/split/profile and retry-ownership regressions are meaningful. | **Full isolation is not established:** scientific execution reuse bypasses these namespaces, B4. Fix that separate path before Phase 1b. |
| **7. Incomplete audit** | **PARTIAL — major** | [bundle.py:343](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:343) now traverses artifacts and reconciles settlements. Existing charge-mutation/deleted-artifact regressions meaningfully catch the original examples. | **Not acceptable.** In an in-memory copy of the live ledger, changing proposal `input_sha` values and deleting its reserved confirmation job still produced `verify=True`, `audit_campaign=[]`. [jobs.py:353](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/jobs.py:353) does not replay complete operational history or require the confirmation reservation to exist. Reconcile original intents, inputs, reservations and broker records, including orphan operational records. |
| **8. Raw secret sidecars** | **FIXED — major** | [campaign.py:237](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:237) writes sanitized CAS bytes. [bundle.py:151](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:151) scans bundle evidence. Secret-bearing sidecar/sealing regressions would fail on the predecessor. | **Acceptable for this defect.** Hash-checked software closures are explicitly outside the evidence scan. |
| **9. Sanitizer bypasses** | **FIXED — major** | [sanitize.py:68](/data/haiyangw/claude/auto-code/src/zkqec/store/sanitize.py:68) decodes escaped keys; [sanitize.py:174](/data/haiyangw/claude/auto-code/src/zkqec/store/sanitize.py:174) exempts only exact placeholders. Both original payload forms have meaningful regressions. | **Acceptable for Phase 1a:** pattern-based detection remains limited, but neither reported bypass survives. |
| **10. Canonical-basis contamination** | **FIXED — major** | [pipeline.py:306](/data/haiyangw/claude/auto-code/src/zkqec/pipeline.py:306) obtains and validates the current presentation’s own basis. The equivalent-presentation A→B→A regression exercises the original failure and passed in memory. | **Acceptable.** No further fix for this defect. |
| **11. Memory preflight** | **PARTIAL — major** | [distance.py:217](/data/haiyangw/claude/auto-code/src/zkqec/verify/distance.py:217) enumerates reduced syndrome matrices and includes preprocessing estimates. The redundant-row peak-memory regression is meaningful and passed. | **Estimator defect fixed; containment residual unacceptable.** Preprocessing precedes the budget check, and production verifier execution has no enforced memory limit. The hardening note’s “cgroup only” mitigation does not cover the inline orchestrator verifier process. Apply process-level limits; see B5. |
| **12. Intake/schema/profile bypass** | **FIXED — major** | [intake.py:46](/data/haiyangw/claude/auto-code/src/zkqec/grammar/intake.py:46) performs strict parsing, schema validation, nested owned-field rejection and family checks. [pipeline.py:115](/data/haiyangw/claude/auto-code/src/zkqec/pipeline.py:115) rejects coerced witness indices. Existing regressions target the original malformed inputs. | **Acceptable on the production intake path.** Direct construction of a `Proposal` is not a substitute for that path. |
| **13. Compute ceilings/confirmation** | **PARTIAL — major** | [pipeline.py:192](/data/haiyangw/claude/auto-code/src/zkqec/pipeline.py:192) charges verifier/identity work; [campaign.py:178](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:178) reserves confirmation first. Tests meaningfully establish charging, simple wall interruption and upfront reservation. | **Not acceptable.** CPU is measured rather than bounded; nested timers defeat the outer wall limit; controls and confirmation lack enforced ceilings. Confirmation settlement is absent from the sealed accounting. B5/B6. |
| **14. Executed evaluator freeze** | **PARTIAL — major** | [freeze.py:109](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/freeze.py:109) checks source fingerprints and import paths. Disk-change and task-mutation regressions are meaningful. | **Not acceptable under §O.** Importing from an immutable snapshot is explicitly deferred at [freeze.py:8](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/freeze.py:8), although §O makes it P1a. Path checks do not establish the bytes of already-loaded modules or dependencies. Load the archived closure through controlled `sys.path`; freeze effective runtime/CLI settings and check every required boundary, including controls. |
| **15. Bundle/call completeness** | **PARTIAL — major** | [bundle.py:206](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:206) embeds executable closures. Normal returned calls retain exact sanitized inputs/outputs. Closure-copy and call-retention regressions are meaningful. | **Not acceptable.** Broker logs and effective broker provenance are absent; call inputs are persisted only **after** `provider.call` returns at [campaign.py:222](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:222). A crash during the call loses its input closure. Persist input references before dispatch and seal the missing evidence; B6. |
| **16. Publication durability** | **PARTIAL — major** | Destination-local temporary files, file fsync, rename and directory fsync replace cross-filesystem moves. Existing store-shard regressions meaningfully catch the original store defect. | **Not acceptable.** [durable.py:50](/data/haiyangw/claude/auto-code/src/zkqec/store/durable.py:50) still misses unsynced ancestors when retrying a partially created directory chain. Add a multilevel retry crash test and repair ancestor synchronization; B7. |
| **17. Missing acceptance artifacts** | **FIXED — major**, as an artifact-delivery finding | [feasibility tables](/data/haiyangw/claude/auto-code/results/feasibility/M1-certification.md:3), [G8](/data/haiyangw/claude/auto-code/notes/DECISIONS.md:12), [bundle index](/data/haiyangw/claude/auto-code/results/bundles/index.md:9), control denominators and [postmortem](/data/haiyangw/claude/auto-code/notes/postmortems/M1-slice.md:1) now exist. Report-summary regressions distinguish the former missing sections. | **Acceptable as delivered documentation, not as proof of acceptance.** The live evidence and acceptance checks have the defects below. |

## B. New defects and incomplete hardening

### B1 — Blocker: broker logging failure leaves forbidden requests forwardable

**Location:** [addon.py:263](/data/haiyangw/claude/auto-code/src/zkqec/broker/addon.py:263), [addon.py:87](/data/haiyangw/claude/auto-code/src/zkqec/broker/addon.py:87).

The request hook writes its log **before** applying the block or setting the response. If that write raises, mitmproxy’s exception wrapper catches the exception and continues processing the flow.

I injected `OSError(ENOSPC)` through the actual mitmproxy exception wrapper. Both an ordinary model request and a forbidden `Bash` tool request emerged with:

```text
response=None, error=None, metadata={}, failed=None
```

Thus the forbidden request remains forwardable, lacks its durable decision record, and lacks the metadata used for response accounting. Request-ceiling blocks are bypassed through the same ordering. WebSocket blocking has the analogous log-before-drop ordering. Additionally, `os.write`’s return length is ignored.

**Fix:** Make every inspection/logging failure terminate the flow and disable further dispatch independently of successful logging. Require complete writes and durable intent before allowing forwarding. Add HTTP/WebSocket tests for ENOSPC, EIO, short writes and restart recovery.

The existing qualification’s “logged before forwarding” test checks successful logs; it does not establish fail-closed behavior when persistence fails.

### B2 — Major: incomplete SSE can be reported as complete usage

**Location:** [policy.py:616](/data/haiyangw/claude/auto-code/src/zkqec/broker/policy.py:616), [launcher.py:263](/data/haiyangw/claude/auto-code/src/zkqec/broker/launcher.py:263).

`anthropic_usage` accepts `message_start` alone. A cleanly terminated HTTP response containing an unfinished SSE message need not trigger `error()`.

A direct reproduction containing only initial usage and unfinished content returned:

```text
usage={input_tokens:7, output_tokens:1}
usage_records=1, usage_missing=0
provider_would_mark_usage_known=True
```

There was no final usage delta or message completion. If the CLI retries and completes, the first request’s partial counters can count as complete telemetry, permitting settlement below the unresolved charge.

**Fix:** Validate provider-level terminal events and required final usage fields, correlated to each request. Treat clean HTTP EOF without complete model accounting as unknown. Test truncated plain and compressed SSE followed by a successful retry.

The tee’s normal gzip decoding and transport-error handling are sensible: overflow/decode failure produces unknown usage, and explicit aborts retain missing usage. Those paths do not cover clean HTTP completion of incomplete SSE.

### B3 — Major: required controls may fail or disappear while audit remains clean

**Location:** [campaign.py:320](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:320), [record.py:42](/data/haiyangw/claude/auto-code/src/zkqec/controls/record.py:42).

A suite exception records `controls_failed` but no control summary, then permits reporting/sealing. `audit_controls` accepts zero records without checking the frozen configuration’s required control set. I reproduced `controls.ok=False` alongside ledger verification and campaign/control audits reporting no problems.

The existing [failure test](/data/haiyangw/claude/auto-code/tests/controls/test_campaign_controls.py:116) explicitly expects this behavior. The report misleadingly says controls were “not supplied.”

Separately, `_run_controls` returns `ok=True` whenever the suite returns evidence, even with false authorizations, false rejections or outcome mismatches. Byte-identical replay proves reproducibility, not that the controls passed.

**Fix:** Preserve failed control evidence, require the configured suite’s complete presence, and expose/enforce a separate control-pass result. Required missing controls, unexpected outcomes and failed positives must prevent acceptance, even when the failure is reproducible.

The forged-chain control’s conjunction of resolver acceptance, ledger verification and exact replay is appropriate at [p1a.py:223](/data/haiyangw/claude/auto-code/src/zkqec/controls/p1a.py:223). Its zero count does **not** mean the resolver alone rejected the forged claim.

### B4 — Major: hardening introduces cross-profile scientific execution reuse

**Location:** [pipeline.py:175](/data/haiyangw/claude/auto-code/src/zkqec/pipeline.py:175), [service.py:371](/data/haiyangw/claude/auto-code/src/zkqec/admission/service.py:371).

Execution identity omits campaign/arm/split/profile. When another namespace has a matching closure, the pipeline cancels the new namespace’s job at zero cost and reuses the old evidence.

I reproduced two profiles sharing a ledger: the second received the first execution’s receipts with `replayed=True` and zero CPU charge. [test_recovery.py:69](/data/haiyangw/claude/auto-code/tests/pipeline/test_recovery.py:69) explicitly asserts this cross-profile reuse.

**Fix:** Namespace scientific executions, evidence lookup and caches as well as jobs, or enforce separate ledgers/stores with a checked namespace binding. Replace this test with an isolation regression. Current separate live-run directories limit exposure, but the API violates §N and is unsuitable for paired Phase-1b arms.

### B5 — Major: verifier limits can be overridden; controls and confirmation are unbounded

**Location:** [service.py:50](/data/haiyangw/claude/auto-code/src/zkqec/admission/service.py:50), [distance.py:142](/data/haiyangw/claude/auto-code/src/zkqec/verify/distance.py:142), [campaign.py:318](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:318).

The admission wall timer and L2 timer both use `ITIMER_REAL`. L2 replaces the outer timer and cancels it on exit without restoring its remaining deadline.

With a one-second verifier reservation, `distance_budget.max_seconds=5`, and a deliberately slowed real sector calculation, L2 completed successfully after approximately **2.42 seconds**, returned verified evidence, and only flagged the overrun afterward.

Furthermore, controls and confirmation are invoked without an enforced execution deadline. [live.py:31](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/live.py:31) creates a slice for launched jobs; it does not move the inline verifier/orchestrator process into that slice. The documented aggregate-CPU limitation does not excuse absent per-process enforcement.

**Fix:** Run verifier, identity, control and confirmation work under independently enforced process/scope limits for CPU, wall and memory. If timers remain, compose deadlines using the earliest deadline and restore prior timer state. Add nested-deadline and CPU/memory exhaustion regressions.

### B6 — Major: sealed evidence cannot independently establish broker accounting or final cost

**Location:** [campaign.py:289](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:289), [live.py:43](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/live.py:43), [bundle.py:388](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:388).

The live bundle includes only `calls` as extra evidence. Its **120 archive members contain no broker log**. The broker manifest is written after sealing. The evaluator archive excludes `configs/broker`, so fingerprint entries do not supply the original price/policy bytes.

Audit compares settlement usage with the provider-created call summary, rather than reconstructing authoritative counts from the broker log. It therefore cannot independently establish complete forwarding, ceilings or telemetry.

Confirmation accounting is also outside the sealed ledger. The sealed report records approximately **9.914 CPU-seconds / 167.864 wall-seconds**; the post-confirmation live ledger records **39.902 / 202.452**. The additional settlement is available on this host, not in the indexed scientific bundle.

**Fix:** Seal sanitized broker logs, grants, effective policy/settings and declared prices; reconcile them during audit. Publish a hash-linked confirmation supplement containing its settlement and final totals. Persist call inputs before dispatch, including interrupted attempts.

### B7 — Major: directory-publication retry still loses ancestor durability

**Location:** [durable.py:49](/data/haiyangw/claude/auto-code/src/zkqec/store/durable.py:49).

After a process crashes between multilevel directory creation and parent fsync, those directories may still exist in the running filesystem. On retry, `missing=[]`, so only the immediate parent is synchronized. Higher newly created directory entries remain unprotected against a subsequent host crash.

A mocked existing-chain reproduction synchronized only `/review-new/archive/bundles` for `/review-new/archive/bundles/campaign`.

**Fix:** Reestablish durability to a known durable ancestor, including retries where all directories already exist. Test interruption after each mkdir and before each ancestor fsync, followed by retry.

### B8 — Major: the USD floor is correct, but other reserved units do not cover the grants

**Location:** [providers.py:36](/data/haiyangw/claude/auto-code/src/zkqec/agents/providers.py:36).

The live reservation books **40,000 input tokens and 32,000 output tokens**, while its grant allows **two requests each** at those ceilings: up to **80,000 / 64,000**. Token caps can therefore be exceeded despite successful reservation checks. Also, `requests=0` is converted into permission for one model request.

The monetary calculation itself is correct:

```text
2 × (40,000 × $8/M + 32,000 × $20/M) = $1.92
```

It includes the highest declared cache-write input price, and `run_live` rejects an insufficient USD reservation before side effects.

**Fix:** Separate per-request ceilings from total invocation reservations; reserve the aggregate maximum for each independently capped unit. Reject zero-request live calls. Add cap tests covering retries and zero-request reservations.

I found no normal path that registers `control.fault_injection` in the campaign registry: [env.py:79](/data/haiyangw/claude/auto-code/src/zkqec/controls/env.py:79) creates separate scratch registries/ledgers, and the live ledger contains no such issuer. The task-hash checks before provider dispatch and evaluation also correctly reject the tested mutation. These findings do not repair the missing immutable evaluator import or control acceptance checks.

## C. M1 acceptance and Phase-1a definition of done

| Requirement | Result | Evidence / limitation |
|---|---|---|
| M1 prerequisites: durable dispatch, fencing, crash handling, unresolved charges retained | **FAIL** | Recovery and settlement regressions pass, but broker persistence failures bypass forwarding controls, B1; publication retry remains incomplete, B7. |
| qecforge reference table reproduced | **PASS** | [test_verify_l2.py:53](/data/haiyangw/claude/auto-code/tests/verify/test_verify_l2.py:53); all three parameterized reference cases passed. |
| Steane `t=2` certifies d=3, not d≥5 | **PASS** | [test_verify_l2.py:73](/data/haiyangw/claude/auto-code/tests/verify/test_verify_l2.py:73); passed, including witness checks. |
| Deterministic positive S-A through production resolver | **PASS** | [test_positive_cases.py:49](/data/haiyangw/claude/auto-code/tests/pipeline/test_positive_cases.py:49); passed with in-memory storage and the production pipeline/resolver. |
| Deterministic positive S-F through production resolver | **PASS** | [test_positive_cases.py:76](/data/haiyangw/claude/auto-code/tests/pipeline/test_positive_cases.py:76); same validation. |
| Paired negatives rejected | **PASS** | [test_positive_cases.py:55](/data/haiyangw/claude/auto-code/tests/pipeline/test_positive_cases.py:55) and `:81`; both passed. |
| Admission-only test | **PASS** | [test_forgery.py:35](/data/haiyangw/claude/auto-code/tests/admission/test_forgery.py:35), execution/closure invariants and replay tests. Qualified by §O’s trusted-writer boundary. |
| Lower-5/upper-3 semantic conflict quarantines; neither verdict authorized | **PASS** | [test_conflicts.py:26](/data/haiyangw/claude/auto-code/tests/admission/test_conflicts.py:26); same-batch and separate-admission cases passed, with paired positives. |
| One live S-A and S-F within D5, sealed and offline reconstructable, correctly labelled | **FAIL as a complete acceptance item** | [index.md:9](/data/haiyangw/claude/auto-code/results/bundles/index.md:9) records both targets, $0.3595284 and the required labels. Scientific replay succeeds: S-A `[[60,8,6]]`; S-F `[[14,2,3]]` with SWAP. However, §M broker evidence is missing, B6, and HEAD’s fail-closed qualification is invalidated by B1. |
| Cost telemetry recorded | **PASS for recorded model telemetry** | [live report](/data/haiyangw/claude/auto-code/runs/campaigns/m1-live-3/report.md), host broker log and `.out`: two model requests; S-A $0.306432, S-F $0.0530964. These reconcile under the declared prices. Complete sealed accounting still fails B6. |
| Postmortem written | **PASS** | [M1-slice.md](/data/haiyangw/claude/auto-code/notes/postmortems/M1-slice.md:1). Its assertion that all findings are fixed needs correction. |
| Measured feasibility tables set D10 | **PASS** | [M1-certification.md](/data/haiyangw/claude/auto-code/results/feasibility/M1-certification.md:3), accompanying JSON/script, and [G8](/data/haiyangw/claude/auto-code/notes/DECISIONS.md:12). The tables distinguish measurements, fitted frontiers and limits; L3 cost is explicitly excluded. |
| P1a control evidence and denominators | **PASS for the recorded live evidence** | Twenty controls; 0/37 false authorizations, 0/24 false rejections, no outcome mismatches. Recorded offline audit reproduces the evidence. Enforcement of required controls remains defective, B3. |

Phase-1a definition of done:

| §15 item | Result | Evidence / consequence |
|---|---|---|
| M0 qualification report lists passes or explicit unqualified properties with consequences | **PASS as documentation** | [latest qualification](/data/haiyangw/claude/auto-code/runs/qualify/20261015T123410Z/report.md:1): 33 qualified, three unqualified, consequences stated. Broker qualification must be corrected and rerun for B1. |
| All M1 acceptance items pass | **FAIL** | Above. |
| Both live tasks have offline-reconstructable bundles | **FAIL under complete §M requirements** | Executable/scientific reconstruction is evidenced; authoritative broker provenance and final accounting are incomplete. |
| Every prior Codex review triaged | **PASS** | [M1-triage-partial.md:3](/data/haiyangw/claude/auto-code/notes/reviews/M1-triage-partial.md:3) dispositions all 17 findings. Triage does not establish closure; this review’s findings now require disposition. |

## D. Phase-1b gate and cost projection

**D4 fails.** M1 is not accepted, and B1 is an open containment blocker. Phase 1b is not authorized to advance automatically.

Using the larger measured live call, **$0.306432 per S-A proposal**, and the postmortem’s assumed **30% additional-role overhead**:

| Workload | Calculation | Projection | D5 |
|---|---|---:|---:|
| M3 smoke | 8 tasks × 2 proposals × $0.306432 × 1.3 | **$6.37** | $10 |
| P1b development pilot | 24 tasks × 2 arms × 6 proposals × $0.306432 × 1.3 | **$114.73** | $150 |

These fit **conditionally**, assuming comparable proposal lengths, limited retries, stable declared prices, and all additional paid roles fitting inside that 30%.

The full Phase-1b projection is **NOT EVIDENCED**:

- The 30% role overhead has not been measured. Two additional calls costing as much as the designer would produce **$14.71 smoke / $264.76 pilot**.
- The postmortem assumes a Codex subscription path without declared list-price accounting, while Codex remains K0-disqualified. D16 prevents treating that path as qualifying evidence.
- At the current $1.92 reservation per attempt, unresolved charges across the proposed attempt counts would total **$30.72 / $552.96**. This is not the expected cost, but shows that completing the proposed workload depends on successful settlement and refunds; enforcing a dollar cap alone does not establish feasibility.