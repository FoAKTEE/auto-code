# M1 post-mortem — minimal live vertical slice

Scope: M1 (`prompt/00-master-prompt.md` §M1), Phase 1a. Live runs: m1-live-1, m1-live-2 (pre-hardening,
`single-implementation, non-evaluation`, not acceptance evidence) and m1-live-3 (hardened code, the acceptance run).
Everything below is algebra-level (D11).

## 1. Outcome

| run | code | result | spend (USD) | requests | wall | bundle audit |
|---|---|---|---|---|---|---|
| m1-live-1 | pre-hardening | 8/8 launch failures, 0 model requests | 0 | 0 | — | ok |
| m1-live-2 | pre-hardening | both tasks `target_met` on proposal 1 | 0.652 charged (0.290 measured) | 4 | 919 s | ok |
| m1-live-3 | hardened, 7d8c36d (§4) | both tasks `target_met` on proposal 1; P1a controls 0/37 false authorizations, 0/24 false rejections | 0.360 (measured) | 2 | 168 s | ok (bundle v2, self-contained) |

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

## 3. Model performance (single samples — not an evaluation)

| call | output tokens | wall | CLI-reported list price | outcome |
|---|---|---|---|---|
| m1-live-2 sa | 11,856 (+ an aborted first attempt) | 287 s (106 s for the completed request) | 0.2378 | [[60,8,6]] |
| m1-live-2 sf | 2,069 | 19 s | 0.0524 | [[14,2,3]] + SWAP |
| m1-live-3 sa | 14,044 | 129 s | 0.3064 | [[60,8,6]] (same candidate) |
| m1-live-3 sf | 2,105 | 20 s | 0.0531 | [[14,2,3]] + SWAP |

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

## 5. Cost telemetry and the Phase-1b projection (D4/D5)

All list-price charges in M1 are reproduced exactly by `configs/broker/prices.json` × broker usage. One Opus 5.5
(effort high) proposal cost $0.05 (S-F, ~2k output tokens) to $0.31 (S-A, ~14k output tokens); the §K reservation
per call is $1.92. Projection, assuming M2–M4 tasks cost at most the m1-live-3 S-A call and ≤ 1.3× for skeptic,
route and commentator calls (Sonnet/Opus) with Designer-B on the Codex subscription (no list price): M3 smoke of
≤ 8 tasks × 2 proposals ≈ 8 × 2 × 0.31 × 1.3 ≈ $6.5 (cap $10); P1b dev pilot of 24 tasks × 2 arms × 6 proposals
≈ 24 × 2 × 6 × 0.31 × 1.3 ≈ $116 (cap $150). Harder D10 targets lengthen thinking; the hard bound remains the cap
enforced through reservations ($1.92 each), so the pilot cannot exceed $150 but may finish fewer proposals.

## 6. Residual risks carried into Phase 1b

- Codex is K0-disqualified (declares tools via `additional_tools`); Designer-B on Codex is dev-only (G6, D16).
- No aggregate CPU quota on this host (G7): concurrency caps, RLIMIT_CPU and wall limits only.
- Evaluator snapshot import deferred to M2 (hardening #14).
- Verifier CPU is charged, not interrupted mid-run (hardening #13).
- Credential refresh endpoints are blocked: an expired token fails closed mid-campaign.
