# M1 post-mortem — minimal live vertical slice

Scope: M1 (`prompt/00-master-prompt.md` §M1), Phase 1a. Live runs m1-live-1..8, all `single-implementation,
non-evaluation`. m1-live-8 (B1 ownership fix, run from the read-only evaluator snapshot) is the acceptance run;
the earlier runs are superseded or failed (§1).
Everything below is algebra-level (D11).

## 1. Outcome

| run | code | result | spend (USD) | requests | wall | bundle audit |
|---|---|---|---|---|---|---|
| m1-live-1 | pre-hardening | 8/8 launch failures, 0 model requests | 0 | 0 | — | ok |
| m1-live-2 | pre-hardening | both tasks `target_met` on proposal 1 | 0.652 charged (0.290 measured) | 4 | 919 s | ok |
| m1-live-3 | hardened, 7d8c36d (§4) | both tasks `target_met` on proposal 1; P1a controls 0/37 false authorizations, 0/24 false rejections | 0.360 (measured) | 2 | 168 s | ok (bundle v2, self-contained) |
| m1-live-4 | round 2, snapshot, addb11b | both tasks `target_met`; control suite refused in snapshot mode (I9) → no control summary | 0.295 | 2 | — | **fails** (controls required) |
| m1-live-5 | round 2, snapshot, 9458710 | both tasks `target_met` on proposal 1; controls passed 0/37, 0/24 (evidence byte-identical to m1-live-3); superseded by the re-review (N1–N5) | 0.678 | 2 | 331 s | ok, confirmation supplement linked |
| m1-live-6 | round 3, snapshot, 6e7e610 | both tasks `target_met` on proposal 1; controls passed 0/37, 0/24 (same evidence); superseded by re-review 3 (B1) | 0.332 | 2 | 160 s | ok, required supplement linked |
| m1-live-7 | B1 drain-before-reap, snapshot, f70055e | both tasks `target_met` on proposal 1; controls passed 0/37, 0/24 (same evidence); superseded by re-review 4 (residual B1) | 0.217 | 2 | 123 s | ok, required supplement linked |
| m1-live-8 | B1 ownership fix, snapshot, 01d1f32 | both tasks `target_met` on proposal 1; controls passed 0/37, 0/24 (same evidence) | 0.208 | 2 | 115 s | ok, required supplement linked |

Cumulative M1 live spend $2.95 of the $5 D5 cap, including an unintended $0.205 test-suite run (I10).

S-A (m1-live-2 and m1-live-3): [[60,8,6]], w=6, deg=6, d_X=d_Z=6 certified exactly at t=3. m1-live-3 proposed the
identical candidate (`1016c96ed5f742fc`). S-F: a [[14,2,3]] code (w=4, deg=6) with a qubit permutation verified by L3
to implement logical SWAP; the two runs proposed different candidates (`55fc2f3d…`, `e1eafd32…`). Whether any of these
codes is known is undetermined (no known-code database until M4). Bundles, hashes and re-audits:
`results/bundles/index.md`.

## 2. Incidents

**I1. Production schema refused by the CLI (m1-live-1).** `roles/designer/output.schema.json` carried
`"$schema": "https://json-schema.org/draft/2020-12/schema"`; the Claude CLI rejected `--json-schema` at startup
("no schema with key or ref …") on all 8 launches. $0 spent, but every proposal slot was consumed.
*Root cause:* M0 qualified structured output with a toy schema, not the production contract.
*Fixes:* schema without the URI as `designer-proposal-v2` (db28846); campaign stops after 2 consecutive launch
failures (0c861be); new M0 property `J3.production_schema_claude` launches the CLI with the production schema under a
zero-request grant, with the draft-URI schema as paired negative (9da3430, qualified in
`runs/qualify/20261015T123410Z`).

**I2. Buffered responses caused a duplicate, possibly billed request (m1-live-2, `sa-live-1-000`).** Broker log:
request at t=0 (seq 6), `flow_error "Client disconnected."` at t=181 s (seq 10), an identical 13,395-byte request
at t=182 s (seq 12) that completed at t=287 s with 11,856 output tokens. The CLI never received a first byte of the
first response because the broker addon never set `flow.response.stream`, so mitmproxy buffered every SSE body to
the end; the CLI's first-byte watchdog (`CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS`) then aborted and retried.
*Accounting:* the provider marked the call `uncertain` (2 model requests, 1 usage record) and the job kept its full
$0.60 reservation — correct under §K. The CLI's `total_cost_usd` (0.2377666) covers only the completed request;
whether the aborted one was billed is unknown.
*Fix:* model responses stream through an unchanged-chunk tee, usage is parsed from the copy at end of stream, an
interrupted stream logs `response_incomplete` and stays usage-missing (daf36b4; three integration tests that failed
on the buffering addon). M0 re-run on the streaming addon: `runs/qualify/20261015T123117Z`.

**I3. Per-call reservation below the §K bound.** The m1-live config reserved $0.60 per call while its own ceilings
(3 requests × (400 kB input × $8/MTok + 32k output × $20/MTok)) allow ≈ $11.5. The USD cap was therefore enforced
by the CLI's `--max-budget-usd` and by luck, not by reservation. Found while declaring the list-price table
(eed11b3). *Fix:* `check_reservation` refuses any per-call reservation below the bound of its own ceilings under
the declared list prices, before `run_live` creates anything (3f046ed); m1-live-3 reserved $1.92 per call
(2 requests × (40 kB × $8 + 32k × $20)/MTok).

**I4. Pre-hardening code reached live runs.** The Codex review of the committed M1 code
(`notes/reviews/M1-codex-partial.md`) found 3 blockers (forged receipts survive verify + audit; unfenced
re-verification on recovery; report inputs not ledger-bound) and 14 majors. The first hardening round addressed
all 17 in 19 gated commits (`notes/reviews/M1-hardening.md`); the Codex milestone review confirmed 12 fixed and 5
partial (#7, #13–#16) and found a new blocker and seven majors (I7). m1-live-1/2 stay labelled non-evaluation.
*Lesson:* the M1 prerequisite list (§L state machine, crash tests) was met, but the evidence path (admission
invariants, ledger-bound reporting, audit coverage) had only been reviewed in design. From M2 on, a Codex code review
of the evidence path precedes any live campaign of the milestone.

**I5. Hardening regressions caught by the suite.** The closed label set from fix #3 rejected the committed
`M1-live-slice` label (63703d6), and one new test picked a random job (4c1ff1e). Both were caught before any run.

**I6. Unpinned BLAS inflated verifier CPU charges.** Found during the feasibility measurements: with default
OpenBLAS threading, `import numpy, zkqec.pipeline` uses 7.33 CPU-s in 0.28 s wall on this 144-core host (0.27 CPU-s
pinned). Verifier CPU is charged by process time, so every unpinned campaign, confirmation or audit process
over-charged (conservative for the caps, but it distorts the CPU telemetry, including m1-live-3's 9.9 CPU-s).
*Fix:* importing zkqec first pins OpenBLAS/OpenMP/MKL to one thread unless the caller set otherwise (test
`tests/test_blas_pinning.py`). The m1-live-3 bundle keeps its recorded (unpinned) charges.

**I7. The milestone review blocked M1 again** (`notes/reviews/M1-codex-milestone.md`, triage `M1-triage.md`).
New blocker B1: a failed broker log write lets mitmproxy forward the flow, including a forbidden tool request —
the log-before-forward rule had been qualified only for successful writes. Majors: truncated SSE counted as complete
usage (B2), failed controls invisible to the audit (B3), cross-profile execution reuse introduced by hardening #2
(B4), nested timers and unbounded controls/confirmation (B5), broker evidence and the confirmation settlement
missing from bundles (B6), retry ancestor durability (B7), per-request token reservations (B8). The lead had also
mis-triaged the §O snapshot import (P1a) as an M2 item. *Lessons:* qualify failure paths (full disk, short
writes), not only the happy path; a hardening fix needs its own adversarial review before the next live run;
check every deferral against the contract's phase marker.

**I8. Zero mtimes kept the broker addon from loading in the snapshot.** The first m1-live-4 launch stopped before
any model request ("broker did not become ready", $0): the evaluator closure tar stores mtime 0 and mitmproxy loads a
script only once its mtime exceeds its initial `last_mtime = 0.0`. The snapshot tests had run the CLI in `--dry-run`
only, which never starts the broker. *Fix:* snapshot entries carry the commit time; a zero-mtime snapshot is refused;
a test starts the real broker from a snapshot (addb11b).

**I9. The control suite was refused in snapshot mode (m1-live-4).** The evaluator-tamper control's fingerprint guard
over its scratch tree tripped the new rule "a guard must be over the running snapshot", so no control summary was
recorded and the campaign's audit failed — the B3 fix working as intended. *Fix:* the rule binds only guards over the
running evaluator; a test runs the whole suite with an active snapshot and requires byte-identical evidence (997f355).

**I10. A test run made real model calls.** A second m1-live-3 bundle (`294db0eb…`, sealed 11:20 at evaluator commit
813fa62) holds a real campaign: 2 Claude requests, $0.205. The lead's test of the §K refusal called the real
`run_live` and relied on the reservation check to raise; when the zero-request case was added test-first, the check
passed and the test ran a live campaign that sealed into the production archive. *Fix:* the sandbox runner refuses real
credential files while `ZKQEC_FORBID_LIVE=1`, which `tests/conftest.py` sets for every test run (fac66a1); the bundle
is quarantined in `zkqec-archive/_incidents/`. *Lesson:* a refusal test must stub every side effect, and live paths
need a suite-wide barrier, not a per-test one.

**I11. The re-review blocked M1 a third time** (`notes/reviews/M1-codex-rereview.md`, triage `M1-triage-2.md`):
nested bounded workers created their own process groups and survived the enclosing wall kill (N1, blocker); complete
framing with a missing counter counted as zero usage (N2); the audit replay ignored completion usage and settlement
source (N3); the final audit could omit the confirmation supplement (N4); NaN totals passed (N5); the Phase-1b
projection was stale. Third hardening round 92dcf66..c8089ed; re-qualified `runs/qualify/20261015T213850Z`; m1-live-6.
*Lesson:* composing two individually sound mechanisms (bounded children, nested inside bounded children) needs its
own adversarial test.

**I12. Re-review 3 blocked M1 on the N1 cleanup** (`M1-codex-rereview-3.md`, `M1-triage-3.md`): after `wait4` reaped
the leader, the drain signalled its numeric group id, which a reused pid could lead (B1). *Fix:* observe the exit with
`waitid(WNOWAIT)`, drain while the zombie leader keeps the id allocated, reap last (997b815). m1-live-7 re-ran the
acceptance slice on the fixed evaluator.

**I13. Re-review 4 found residual post-reap signalling** on exceptional paths (an interrupt right after the reap;
a child reaped elsewhere). *Fix:* explicit pid ownership — signals only while the child is ours and unreaped,
ownership ends before any reaping wait, `ECHILD` = lost (escaped, no signal), leader signals via pidfd, group signals
gated on the pidfd, ignored `SIGCHLD` refused (5d26221). m1-live-8 re-ran the slice on the fixed evaluator.
*Lesson:* for process lifecycles, reason about every interleaving including asynchronous exceptions, not only the
happy and timeout paths.

## 3. Model performance (single samples — not an evaluation)

| call | output tokens | wall | CLI-reported list price | outcome |
|---|---|---|---|---|
| m1-live-2 sa | 11,856 (+ an aborted first attempt) | 287 s (106 s for the completed request) | 0.2378 | [[60,8,6]] |
| m1-live-2 sf | 2,069 | 19 s | 0.0524 | [[14,2,3]] + SWAP |
| m1-live-3 sa | 14,044 | 129 s | 0.3064 | [[60,8,6]] (same candidate) |
| m1-live-3 sf | 2,105 | 20 s | 0.0531 | [[14,2,3]] + SWAP |
| m1-live-5 sa | 31,956 (ceiling 32,000) | 291 s | 0.6398 | [[60,8,6]] (same candidate, third time) |
| m1-live-5 sf | 1,851 | 17 s | 0.0377 | [[14,2,3]] + SWAP (same code as m1-live-2) |
| m1-live-6 sa | 12,097 | 112 s | 0.2675 | [[60,8,6]] (same candidate, fourth time) |
| m1-live-6 sf | 2,669 | 25 s | 0.0644 | [[14,2,3]] + SWAP |
| m1-live-7 sa | 8,440 | 73 s | 0.1694 | [[60,8,6]] (same candidate, fifth time) |
| m1-live-7 sf | 2,360 | 24 s | 0.0479 | [[14,2,3]] + SWAP (candidate 02f09068…, as in m1-live-5) |
| m1-live-8 sa | 8,409 | 73 s | 0.1688 | [[60,8,6]] (same candidate, sixth time) |
| m1-live-8 sf | 1,912 | 17 s | 0.0389 | [[14,2,3]] + SWAP (candidate d0a4e759…) |

The S-A answer is the identical proposal in all three hardened-era runs and the S-F code recurs, which is consistent
with recall of a fixed construction; known-code labelling waits for M4. The m1-live-5 S-A call came within 44 tokens
of its per-request output ceiling: P1b reservations need headroom above 32k output tokens for effort `high`.

Context for S-A only: the B1 random two-block search hit the (n≤60, k≥8, d≥6, w≤6, deg≤6) target in 2 of 1,500
samples. One sample per arm supports no comparison (H3/H4 need the P1b paired design).

## 4. Hardened-code changes since m1-live-2

- 17 Codex findings (`notes/reviews/M1-hardening.md`).
- Streaming broker (I2), list-price table (I3), production-schema qualification (I1).
- Reservation floor (I3, 3f046ed); bundle index with offline re-audit (`scripts/bundle_index.py`).
- P1a control suite (2ec9572..73c27cf, `notes/build/controls.md`): 20 controls, recorded in the ledger by every
  campaign with `"controls": "p1a"` and re-run byte-identically by the offline audit; new production gate for
  mode 1 (task hash checked before every dispatch and evaluation).
- Feasibility tables (1d78359..1f3fdc1): 548 measured (code, t, sector) rows over 51 real codes in 21 min;
  preflight memory estimates are conservative (1.06–3.12× the measured peak); D10 Tier A (60 CPU-s × 8 GiB per
  task: d_min ≤ 7 to n 200, 8–9 to n 99, 10–11 to n 55) and opt-in Tier B (600 s × 32 GiB) in `notes/DECISIONS.md`
  G8. Both live targets sit well inside Tier A (S-A t=3, M=36,051 per sector).
- BLAS pinning (I6).
- Second hardening round after the milestone review (8979593..94f79e3; `notes/reviews/M1-hardening-2-E1.md`,
  `-E2.md`): fail-closed broker (B1) with the qualification property `J1.log_failure_fail_closed`
  (`runs/qualify/20261015T200053Z`, 34 qualified / 3 unqualified), complete-message usage (B2), control pass/fail
  enforced by the audit (B3), namespaced executions (B4), bounded verifier/identity/control/confirmation children
  (B5), sealed broker evidence and the confirmation supplement (B6), ancestor fsync (B7), invocation-total token
  reservations (B8), full operational replay (#7), campaigns run from a read-only evaluator snapshot (#14), plus the
  lead's fixes for I8–I10.

## 5. Cost telemetry and the Phase-1b projection (D4/D5)

All list-price charges in M1 are reproduced exactly by `configs/broker/prices.json` × broker usage. One Opus 5.5
(effort high) proposal cost $0.04–0.05 (S-F, ~2k output tokens) to $0.24–0.64 (S-A, 12k–32k output tokens); the
§K reservation per call is $1.92.

Projection on the conservative basis of the most expensive observed S-A call (m1-live-5, $0.6397666; m1-live-6 cost
$0.2675), keeping the **unmeasured** assumption of 1.3× for skeptic, route and commentator calls:

| workload | calculation | projection | D5 cap |
|---|---|---|---|
| M3 smoke | 8 tasks × 2 proposals × $0.640 × 1.3 | ≈ $13.31 | $10 |
| P1b dev pilot | 24 tasks × 2 arms × 6 proposals × $0.640 × 1.3 | ≈ $239.53 | $150 |

Both exceed their caps as sized. The caps cannot be exceeded (reservations bind), but a workload that does not fit
is not a plan. D4 therefore also requires (`notes/reviews/M1-triage-2.md`): the M3 smoke is resized before
dispatch to fit $10 at the measured per-proposal cost (e.g. 5 tasks × 2 proposals ≈ $8.3 at 1.3×); every role and
retry is measured in that smoke; Codex's K0-unqualified status (D16) and missing price basis are handled explicitly
(a Codex arm is dev-only and its cost is reported in requests and tokens, not USD); the pilot is recomputed from
the smoke telemetry and resized before any pilot dispatch.

## 6. Residual risks carried into Phase 1b

- Codex is K0-disqualified (declares tools via `additional_tools`); Designer-B on Codex is dev-only (G6, D16).
- No aggregate CPU quota on this host (G7): concurrency caps, per-child RLIMIT_CPU, wall kills and measured charges.
- Bounded children are forked from the orchestrator: an inherited lock held by another thread can stall a child
  (timeout, never false evidence). Before any concurrent dispatch (M3) workers move to a clean-exec or forkserver
  start with controlled inherited handles.
- `RLIMIT_AS` bounds the address space a child adds after fork, not its resident set or an aggregate.
- Nested bounded workers stay in the enclosing job's process group (N1, 92dcf66); a nested run at its own deadline
  kills only its direct child, whose own subprocesses live until the enclosing job ends; CPU of a descendant that
  exits on its own after its parent died is neither measured nor detected.
- The snapshot isolates evaluator code; the runtime dependencies (`.venv` site-packages, `.venv-broker`) are trusted,
  hash-recorded in the manifest, not copied into the snapshot (§O trust boundary).
- Admission still accepts `limits=None` for direct API use and the standalone audit replays inline; production
  campaign paths always pass limits (M3 prerequisite: refuse unbounded production calls).
- Credential refresh endpoints are blocked: an expired token fails closed mid-campaign.
