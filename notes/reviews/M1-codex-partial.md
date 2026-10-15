**Verdict: block M1 acceptance.**  
Reviewed `5b375f7..3753ffa`—HEAD at review start; later concurrent changes excluded.  
166 selected tests passed. No L1/L3 arithmetic or C2 completion error reproduced.  
Additional reproductions used in-memory SQLite/CAS; full filesystem crash and bundle reconstruction tests were not run. No files modified.

1. **[blocker] Forged receipts survive ledger verification and offline audit.**  
   **Location:** [ledger.py:175](/data/haiyangw/claude/auto-code/src/zkqec/admission/ledger.py:175), [bundle.py:231](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:231).  
   **Evidence:** Public `Ledger.append` accepted an unregistered execution and a payload-selected `verified dimension_eq(k=999)`. Resolver returned `verified`; ledger verification and `audit_inner` returned `ok=True`. Audit skips executions without closures. Separately, appending an extra receipt to an already closed registered execution also passed audit: replay compares against closure IDs, never against all actual receipt rows. This demonstrates an internal API/invariant bypass, not agent access to SQLite.  
   **Fix:** Restrict scientific event writes to admission; validate issuer registration, role, version and inputs during replay. Require every receipt to belong to an admitted closure, require exact closure/receipt correspondence, and prohibit receipts after closure.

2. **[blocker] Recovery can run verification without a new reservation or settlement fence.**  
   **Location:** [pipeline.py:115](/data/haiyangw/claude/auto-code/src/zkqec/pipeline.py:115).  
   **Evidence:** Injected failure in `on_close`, then `recover()` and `settle()`. Calling `_run` again admitted five new receipts with `replayed=False`, while job count remained one and charged CPU remained 600. The “settled” branch calls `run_verifier` without `on_close`; an unclosed execution is executed again.  
   **Fix:** That branch must only read an existing completed closure. An unclosed execution requires a separately reserved retry with a new generation and atomic fenced settlement.

3. **[blocker] Authoritative report inputs are not bound to the ledger.**  
   **Location:** [render.py:38](/data/haiyangw/claude/auto-code/src/zkqec/report/render.py:38), [render.py:132](/data/haiyangw/claude/auto-code/src/zkqec/report/render.py:132).  
   **Evidence:** For Steane, the original `d_min=5` task returned `unknown`. Supplying `d_min=3` under the same task ID in `CampaignRecord`, while retaining the original task in its manifest, produced `verified` and `lint=[]`. Fabricated USD totals of −99 and prose injected through `source` also passed lint. Re-rendering authenticates caller-supplied metadata against itself.  
   **Fix:** Ledger-anchor the frozen task/configuration and evaluation membership. Derive accounting and autonomy records from audited events. Restrict labels/sources to typed values and escape metadata; arbitrary prose belongs only in the commentary appendix.

4. **[major] Invalid ancestors can leave descendants authoritative.**  
   **Location:** [evidence.py:62](/data/haiyangw/claude/auto-code/src/zkqec/admission/evidence.py:62), [resolver.py:93](/data/haiyangw/claude/auto-code/src/zkqec/admission/resolver.py:93).  
   **Evidence:** A verified child whose parent is `unknown/timeout` resolves as `verified`: recursive `ok()` never checks parent authority/status. Removing conflicting premises inside `Resolver` likewise leaves their children usable. These were reproduced; ordinary P1a admission currently stamps empty parent lists, so this is latent there.  
   **Fix:** Require every dependency to be currently verified and authorizing. After conflict removal, invalidate descendants transitively before building lookup indexes.

5. **[major] Partial usage refunds unresolved charges; NaN disables budget comparisons.**  
   **Location:** [jobs.py:203](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/jobs.py:203), [jobs.py:218](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/jobs.py:218), [campaign.py:148](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:148).  
   **Evidence:** Settling a $1 reservation with `actual={"requests":1}` charges $0. Campaign maps timeout/resource-killed outcomes to `failed`, enabling this refund with partial telemetry. Separately, `actual={"usd":float("nan")}` produces NaN totals, permits another reservation beyond the cap, and passes `book.audit()`.  
   **Fix:** Validate finite, typed usage. Distinguish unknown measurements from zero and retain the corresponding reservation. Treat interrupted calls as uncertain unless complete accounting establishes their charge.

6. **[major] Job ownership and idempotency are not campaign-scoped.**  
   **Location:** [jobs.py:114](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/jobs.py:114), [jobs.py:166](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/jobs.py:166).  
   **Evidence:** Two `JobBook`s sharing a connection: campaign B, capped at $0, reused campaign A’s reservation using the same idempotency key, then dispatched and settled A’s job. Lookups and updates omit campaign ownership.  
   **Fix:** Scope uniqueness and every lookup/update to the complete campaign/arm/split/profile namespace. Reject idempotent requests whose kind, inputs or reservation differ; validate retry ownership.

7. **[major] Offline audit misses accounting and referenced non-scientific artifacts.**  
   **Location:** [jobs.py:265](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/jobs.py:265), [bundle.py:223](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:223).  
   **Evidence:** Changing settled `ops_jobs.actual` from $1 to $0 passed `book.audit()` and the inner audit. Deleting an `EvaluationRef.call_record` artifact and corrupting its proposal bytes under the original hash also passed `audit_inner`, including two verifier replays.  
   **Fix:** Replay complete operational records—including reservations, actual usage, ownership and caps—and reconcile broker accounting. Traverse every campaign and ledger artifact reference, verify all retained hashes/redaction records before replay, and reject missing references.

8. **[major] Sanitized CAS ingestion is followed by an unsanitized sidecar write.**  
   **Location:** [campaign.py:144](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:144), [bundle.py:147](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:147).  
   **Evidence:** `store.put_json(rec)` sanitizes a copy; the subsequent `write_text(json.dumps(rec))` writes the original. For `rec={"api_key":"opaque-secret-123"}`, CAS contains a redaction while the sidecar retains the secret. Sealing copies that directory verbatim.  
   **Fix:** Write sidecars only from the sanitized stored bytes, retain the redaction reference, and scan every bundle input—not merely CAS ingress.

9. **[major] Sanitizer placeholders and escaped JSON keys bypass redaction.**  
   **Location:** [sanitize.py:37](/data/haiyangw/claude/auto-code/src/zkqec/store/sanitize.py:37), [sanitize.py:134](/data/haiyangw/claude/auto-code/src/zkqec/store/sanitize.py:134).  
   **Evidence:** Both `{"api_key":"[REDACTED:prefix] actual-secret-12345678"}` and raw JSON `{"\u0061pi_key":"actual-secret-12345678"}` survive unchanged with zero redaction counts.  
   **Fix:** Exempt only exact recognized placeholder values. Parse JSON ingress through structured sanitization, including decoded keys, before hashing; ensure residual detection does not share these bypasses.

10. **[major] Equivalent presentations contaminate canonical-basis selection.**  
    **Location:** [pipeline.py:210](/data/haiyangw/claude/auto-code/src/zkqec/pipeline.py:210).  
    **Evidence:** Evaluate Steane presentations with X-row orders `(0,1,2)`, `(1,0,2)`, then `(0,1,2)` again, using an identity S-F operation and omitted basis. Results were `verified, verified, unknown`. The third evaluation selected the second presentation’s basis; L3 reported “basis is tied to a different presentation.”  
    **Fix:** Obtain the canonical basis from the current presentation’s L1 execution and validate its presentation hash. Transport dimension evidence independently of presentation-bound basis artifacts.

11. **[major] Distance memory preflight underestimates redundant syndrome matrices.**  
    **Location:** [distance.py:74](/data/haiyangw/claude/auto-code/src/zkqec/verify/distance.py:74), [distance.py:198](/data/haiyangw/claude/auto-code/src/zkqec/verify/distance.py:198).  
    **Evidence:** For `n=101`, empty `H_X`, 4,096 repeated rows spanning the first 100 unit vectors, and `t=2`: estimate **2,877,432 bytes**, measured enumeration peak **3,996,868 bytes**. Preflight accepts a ceiling equal to the estimate. Kernel syndrome integers encode all rows; the estimate uses rank.  
    **Fix:** Estimate actual syndrome bit width or enumerate an explicitly reduced syndrome matrix. Include preprocessing allocations and enforce the memory ceiling during execution.

12. **[major] Production intake bypasses declared witness schema and grammar profile.**  
    **Location:** [campaign.py:154](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:154), [pipeline.py:165](/data/haiyangw/claude/auto-code/src/zkqec/pipeline.py:165).  
    **Evidence:** A Steane proposal containing `phase_convention="unsigned_v0"`, `logical_qubits=[0.9]`, and nested `basis.status="verified"` still satisfied an S-F task: fields are ignored or coerced. A `two_block_group_algebra` proposal also passes the pipeline used for `P-min/G-open`; `grammar_for()` only controls disclosure.  
    **Fix:** Use the strict duplicate-rejecting parser, validate the complete output schema, require integer indices, and enforce the frozen profile’s allowed families before construction.

13. **[major] Verifier reservations lack enforceable CPU/wall ceilings and omit confirmation.**  
    **Location:** [pipeline.py:111](/data/haiyangw/claude/auto-code/src/zkqec/pipeline.py:111), [pipeline.py:143](/data/haiyangw/claude/auto-code/src/zkqec/pipeline.py:143), [campaign.py:112](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:112).  
    **Evidence:** `_run` reserves 600 CPU-seconds but invokes arbitrary-duration verification synchronously and reserves zero wall time. Identity computation bypasses the job book. No confirmation capacity is reserved before search; offline replay occurs after reported totals are captured.  
    **Fix:** Enforce verifier ceilings, charge identity work, maintain a campaign deadline, and reserve/account confirmation before dispatching proposals.

14. **[major] Evaluator freezing checks names and versions, not the executed closure.**  
    **Location:** [campaign.py:97](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:97), [registry.py:84](/data/haiyangw/claude/auto-code/src/zkqec/admission/registry.py:84).  
    **Evidence:** The runner checks Git cleanliness once, imports the working-tree evaluator, and records registry name/version strings. It neither imports from a frozen snapshot nor checks closure hashes before each call/batch as required by §4.3/§O.  
    **Fix:** Archive and hash the executable closure before execution, load it through a controlled path, and revalidate the frozen manifest at every dispatch boundary.

15. **[major] Bundles depend on an external archive and do not guarantee complete call retention.**  
    **Location:** [bundle.py:150](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:150), [bundle.py:175](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:175), [campaign.py:131](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:131).  
    **Evidence:** Copying the bundle alone to a clean machine leaves runtime/wheel/evaluator files absent; audit looks in external `archive/cas`. The committed runner also stores hashes of requests without requiring their bytes, exact system prompts/schema, or malformed raw responses to be retained.  
    **Fix:** Export a complete relocatable bundle set, including executable closures and bootstrap metadata. Persist sanitized exact call inputs/outputs for every attempt and anchor their references before settlement.

16. **[major] Publication durability is incomplete.**  
    **Location:** [artifacts.py:268](/data/haiyangw/claude/auto-code/src/zkqec/store/artifacts.py:268), [bundle.py:72](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:72), [bundle.py:157](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:157).  
    **Evidence:** After interruption between first-shard `mkdir` and parent fsync, retry treats that shard as existing; `ensure_durable` never fsyncs the store root. Bundle/CAS publication uses `shutil.move` from temporary storage and writes indexes without destination-directory fsync; cross-filesystem moves can leave the copied destination file unsynced.  
    **Fix:** Reestablish durability through every created directory ancestor. Publish archives and indexes using destination-filesystem temporaries, file fsync, atomic rename and directory fsync before returning references.

17. **[major] M1 acceptance evidence is incomplete in the reviewed range.**  
    **Location:** [master prompt:551](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:551), [master prompt:562](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:562), [render.py:84](/data/haiyangw/claude/auto-code/src/zkqec/report/render.py:84).  
    **Evidence:** The reviewed commit contains no measured certification feasibility tables, accepted live S-A/S-F bundle indexes, or `notes/postmortems/M1-slice.md`. Reports do not render §I false-authorization/false-rejection denominators. These omissions do not assess the excluded ongoing provider work.  
    **Fix:** Publish measured tables and resulting D10 ranges, both live bundle indexes with offline-audit and cost evidence, the required control summaries, and the M1 postmortem before accepting M1.