# Triage — Codex M1 milestone review (`M1-codex-milestone.md`, range 3753ffa..4f4c4bf)

Verdict received: **BLOCK M1**. Reviewer: Codex gpt-6-astra, reasoning max (real binary; log header
`model: gpt-6-astra`); prompt in `M1-codex-milestone-prompt.md`. All findings are accepted; none is rejected.

Codex confirmed FIXED: #1–#4, #5 (settlement rule), #6 (JobBook), #8, #9, #10, #12, #17 (artifacts). It
accepted the qecforge reference table, Steane t=2, the deterministic positives and paired negatives, the
admission-only test, the semantic-conflict control, the feasibility tables and G8, the recorded control
evidence, the postmortem and the triage of the earlier review. It also checked that the fault-injection issuer
never reaches a campaign registry and that the forged-chain counting rule (resolver ∧ verify ∧ replay) is right.

**Lead correction.** The first triage accepted deferring #14's snapshot import to M2. That was wrong: contracts §O
is P1a and says "Evaluator code is imported from a read-only snapshot with a controlled `sys.path`."

M1 stays not accepted. D4 fails, so Phase 1b does not start. m1-live-3 remains valid **scientific** evidence: the
replay of its executions passed independently. It is not complete §M evidence (B6) and must be re-run after the fixes.

| # | Sev. | Finding | Disposition / required fix (binding) |
|---|---|---|---|
| B1 | blocker | broker log failure leaves forbidden requests forwardable | fail closed: the response/drop is set before any logging; forwarding only after a complete, durable write (short-write loop + fsync); any exception in a hook blocks the flow, fails the job and disables dispatch in memory; same for WebSocket frames; tests under the real mitmproxy addon with injected ENOSPC/EIO/short writes, HTTP and WebSocket; re-qualify |
| B2 | major | truncated SSE counted as complete usage | usage is `reported` only with `message_start`, a final `message_delta` carrying usage and `message_stop` (JSON: non-null `stop_reason`); a clean EOF without them is unknown; tests for truncated plain and gzip SSE followed by a successful retry |
| B3 | major | failed or missing controls leave the audit clean | evidence preserved on failure; the audit requires the frozen config's control set to be recorded completely; a separate pass result (k=0, j=0, no outcome mismatch); failure blocks acceptance and is shown as FAILED in the report; the test expecting the old behaviour is replaced |
| B4 | major | cross-profile scientific execution reuse | execution identity, evidence lookup and caches carry the namespace; reuse only within a namespace; the reuse test becomes an isolation regression |
| B5 | major (+#11, #13 residuals) | nested ITIMER_REAL; controls and confirmation unbounded; no process limits on inline verifiers | verifier, identity, control and confirmation work runs in a child process with RLIMIT_CPU, RLIMIT_AS and a wall kill derived from the job reservation; any in-process timers compose (earliest deadline, prior timer restored); nested-deadline, CPU and memory exhaustion regressions |
| B6 | major (+#15) | sealed evidence lacks broker log, grants, policy and prices; confirmation settlement outside the bundle; call inputs persisted after the call | seal sanitized broker log, grants, effective policy, prices and broker manifest; the audit reconstructs model-request counts and usage from the broker log and reconciles them with settlements; a hash-linked confirmation supplement with its settlement and final totals; call inputs stored and referenced before dispatch |
| B7 | major (+#16) | publication retry loses ancestor durability | every publication fsyncs each directory from the leaf up to a known durable root, whether or not it was just created; crash/retry tests at every mkdir/fsync step |
| B8 | major | token reservations are per-request, grants allow `requests ×` | a reservation is the invocation total; per-request broker ceilings are total ÷ requests (floor); USD floor from totals; live calls need requests ≥ 1; cap tests with retries |
| #7 | major (partial) | audit misses input_sha edits and a deleted confirmation reservation | the audit replays the full operational history: every intent, input hash, reservation (confirmation and controls included) and broker record reconciles; orphan operational records are flagged |
| #14 | major (partial) | evaluator not imported from an immutable snapshot | `zkqec campaign run` extracts the committed evaluator closure read-only and re-executes from it in isolated mode with a controlled `sys.path`; effective runtime/CLI versions (claude CLI binary hash, mitmproxy) are frozen in the manifest; boundary checks cover controls and confirmation |
| D | — | the Phase-1b projection's role overhead is unmeasured, and Codex has no list price | M3 measures role overhead in its smoke run before the pilot; the projection is recomputed from M3 telemetry before the P1b pilot starts (D4 re-check) |

**Plan.** Second hardening round, test-first, one gated commit per finding. Then the full suite and a
re-qualification (B1), a fresh live run (m1-live-4) whose bundle carries the broker evidence, a re-indexed
bundle list, a postmortem correction, and a Codex re-review of the fixes before M1 is accepted.
