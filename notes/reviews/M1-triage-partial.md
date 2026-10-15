# Triage — Codex review of committed M1 code (`M1-codex-partial.md`, range 5b375f7..3753ffa)

Verdict received: **block M1 acceptance** (3 blockers, 14 majors). All accepted; none rejected. M1 is not accepted
until every item below is fixed with a regression test reproducing the reported failure. Runs made before the fixes
(m1-live-1, m1-live-2) remain labelled `single-implementation, non-evaluation` and are not acceptance evidence.

| # | Sev. | Finding | Disposition / planned fix |
|---|---|---|---|
| 1 | blocker | forged receipts survive verify + audit | apply-time invariants: issuer/version/role must be in the ledger_opened registry manifest; receipts only for OPEN executions; admitted closure records must equal exactly the execution's receipts; nothing after closure; audit checks every receipt ↔ admitted closure and flags open executions |
| 2 | blocker | recovery re-runs verification unfenced | settled-job branch only reads an existing closure; unclosed execution ⇒ new reserved retry job (new generation, retry_of) with fenced settlement |
| 3 | blocker | report inputs not ledger-bound | ledger events `campaign_frozen` (config, tasks, manifest) and `evaluation_recorded` (membership); report derives tasks/evaluations from the ledger; accounting from ledger-anchored settlement events; typed/escaped labels and sources |
| 4 | major | descendants of unverified/quarantined parents stay authoritative | parents must be currently verified + authorizing; transitive invalidation after conflict removal |
| 5 | major | partial usage refunds charges; NaN | finite typed usage; unmeasured units charged at reservation; interrupted/blocked/timeout ⇒ uncertain |
| 6 | major | job ownership not campaign-scoped | UNIQUE(campaign, idem_key); every lookup/update campaign-scoped; idempotent reserve rejects mismatched kind/input/reservation; retry ownership checked |
| 7 | major | audit misses accounting + non-scientific artifacts | settlement recorded as hash-chained ledger events and reconciled with ops tables and call records; audit traverses every referenced artifact and verifies hashes |
| 8 | major | unsanitized call-record sidecar | sidecars written from sanitized stored bytes; seal scans every bundle input |
| 9 | major | sanitizer bypasses (placeholder prefix, escaped keys) | exact-placeholder exemption only; JSON text parsed and structurally sanitized; residual detection without these bypasses |
| 10 | major | canonical basis from a different presentation | basis taken from the current presentation's own L1 execution, presentation hash validated |
| 11 | major | distance memory preflight underestimates redundant rows | enumerate on row-basis-reduced matrices (same code); estimate on actual syndrome width |
| 12 | major | intake bypasses schema/profile | strict duplicate-rejecting parse + full output-schema validation + profile family enforcement before construction; integer indices |
| 13 | major | verifier ceilings/identity/confirmation unaccounted | bounded verifier work (deterministic budgets) with wall/CPU measured and charged; identity charged; confirmation reserved at start; campaign wall-clock deadline in the loop |
| 14 | major | evaluator freeze by name/version only | evaluator fingerprint (all imported zkqec sources, roles, broker policy) checked against the git commit at start and re-checked at every dispatch boundary; snapshot-import deferred to M2 and recorded as a residual |
| 15 | major | bundle depends on external archive; call inputs not retained | closures embedded in the bundle; exact request bytes, system prompt, schema and raw outputs stored as artifacts for every attempt |
| 16 | major | publication durability incomplete | fsync every created ancestor; bundle/CAS/index publication via destination temp + fsync + rename + dir fsync |
| 17 | major | M1 acceptance evidence incomplete | feasibility tables, live bundle indexes with audit + cost, control summary in the report (k/N, j/M), M1 postmortem — after the fixes |
