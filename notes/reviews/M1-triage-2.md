# Triage — Codex M1 re-review (`M1-codex-rereview.md`, range 5b7cf89..af00955)

Verdict received: **BLOCK M1** (Codex gpt-6-astra, max, real binary). All findings are accepted; none is rejected.
Confirmed FIXED: B1, B3, B4, B7, B8, #14, #15, #16. Every literal §M1 Accept item passes, and Codex verified m1-live-5
independently: the bundle hash, 53 members, the closures, the ledger, artifacts, broker reconciliation, report lint,
exact replay of 5 verifier and 2 identity executions, the controls (0/37, 0/24) and the supplement (seq 50 → 51).
M1 stays blocked on N1 (containment, D8) and the incomplete binding hardening. D4 is closed.

| # | Sev. | Finding | Disposition / required fix (binding) |
|---|---|---|---|
| N1 | blocker | nested `run_bounded` children leave the enclosing process group, so an outer wall kill leaves inner workers running | a bounded child marks the process; nested runs stay in the enclosing group (no new group) and are killed by pid at their own deadline; after any kill the parent waits until the whole group is gone (killpg probe) before returning; if members survive a grace period the outcome is `escaped` and the job keeps its full reservation; regression: an outer control/confirmation-style job expires while an inner worker is busy → no survivor, unresolved charge retained |
| N2 | major | complete SSE/JSON framing with missing counters reported as complete, missing input counted as zero | usage is `reported` only with mandatory counters: `input_tokens` (message_start or JSON usage) and `output_tokens` (final delta or JSON usage); provider and auditor never default a missing mandatory counter to 0; complete-framing/missing-counter SSE and JSON cases, with retry sequences |
| N3 | major | audit replay ignores completion `actual` and settlement `from` | the audit reconciles every operational completion (outcome, actual usage) and settlement (from-state, charged, charging rule) with the hash-chained `job_settled` event and the transition history; Codex's two mutations (actual.usd = 999999; from = uncertain) must fail the audit |
| N4 | major | final audit and index can omit the confirmation supplement | bundles sealed with broker evidence declare `confirmation: required` in their manifest; `zkqec audit` discovers `<sha>.confirmation.json` and fails without it for such bundles; the index fails on a missing required supplement and labels only genuinely older bundles as pre-B6 |
| N5 | major | NaN totals pass supplement reconciliation | supplement JSON parsed with non-standard constants rejected; every total finite, non-negative, of the right type; NaN/Infinity/missing-field regressions |
| D | major | stale Phase-1b projection | postmortem recomputed from m1-live-5 telemetry (smoke ≈ $13.31, pilot ≈ $239.53 at 1.3× > caps); D4 additionally requires: M3 smoke resized before dispatch to fit $10 at the measured per-proposal cost, every role and retry measured, Codex's unqualified status and missing price basis handled, the pilot recomputed and resized before any pilot dispatch |
| N6 | minor | postmortem residual-risk section superseded | rewritten: actual remaining limits (nested-worker containment until N1 lands, RLIMIT_AS is incremental address space not RSS, trusted runtime site-packages / `.venv-broker`, fork with locks) |
| — | minor | fork with inherited locks/threads; `limits=None` in admission; inline audit replay | recorded as M3 prerequisites (before any concurrent dispatch): clean-exec or forkserver workers with controlled inherited handles; production admission without limits refused; audit replay bounded |

**Plan.** One engineer fixes N1–N5 test-first. The lead fixes N6 and D. Then the full suite and m1-live-6
from the snapshot, with the bundle-index check now requiring the supplement. A Codex re-review comes before M1
is accepted.
