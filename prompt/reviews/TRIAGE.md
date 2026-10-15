# Triage of reviews of master prompt v0.2 → v0.3

Sources: `codex-prompt-review.md` (CR-1…CR-35), `codex-independent-analysis.md` (IA-…), owner
instructions (OWN-…), and my own post-draft checks (SELF-…). Disposition: **A** accepted as proposed,
**M** accepted with modification (reason given), **R** rejected (reason given). "Where" = location in v0.3
(`00` = master prompt, `01` = technical contracts appendix).

## Owner and self-found

| ID | Item | Disp. | Where |
|---|---|---|---|
| OWN-1 | Git commit principles follow Chandra exactly (gate files byte-identical, hook installed, one commit per DAG node, strict landing, no attribution) | A — already landed (`dfcfa8b`) | 00 §12 |
| SELF-1 | Tanner-graph key is generator-dependent | A — merged with CR-8 | 01 §D |
| SELF-2 | Agent sandbox must not expose kernel constructors (reference polynomial), tests, configs, notes | A — merged with CR-5 | 01 §J |
| SELF-3 | `claude -p` has `--json-schema`, `--tools`, `--disallowedTools`, `--no-session-persistence`; codex has `--output-schema`, `--ephemeral`, `--ignore-user-config`, web search only with `--search`; `socat` present; user cgroup scopes (`systemd-run --user --scope -p MemoryMax -p TasksMax`) work | A — recorded as ground truth; host qualification still required | 00 §2, 01 §J |

## Codex prompt review (CR)

| ID | Sev. | Finding (short) | Disp. | Where / note |
|---|---|---|---|---|
| CR-1 | blocker | Application completion vacuous; L5/L6 not delivered | M — application closure (G1–G3, S-C) moved out of Phase 1 into Phase 3 with an explicit positive-reference-case acceptance; Phase 1 permits algebra-level claims only | 00 §0, §11, §15 |
| CR-2 | major | No executable contract for the full design object x | A — `DesignCandidate` with versioned component refs, component-edit proposals, per-edit invalidation; inactive components explicitly `absent/unsupported` | 01 §B |
| CR-3 | blocker | Evidence IDs ≠ entailment | A — typed propositions + entailment check; assertions rendered from records; solver_claim/agent_assertion never authorize | 01 §A |
| CR-4 | blocker | Wrapper shares network; tool-disable ≠ egress control | A — egress broker (no-net namespace + socat bridge to an allowlisting CONNECT proxy); telemetry required, missing telemetry = unknown; host qualification milestone M0 | 01 §J, 00 §11 |
| CR-5 | blocker | Denylist leaves answers readable | A — allowlisted runtime FS, fresh per-job HOME/CODEX_HOME, verification-only toolbox without constructors; Phase-1 agents run in `call` mode with no tools (session mode deferred until credential mediation is qualified) | 01 §J |
| CR-6 | major | ZK profiles are disclosures; ambiguous combos; AlphaZero analogy | A — grammar = versioned capability manifest; valid combos enumerated; linter described as syntactic screening; analogy removed | 00 §3 |
| CR-7 | major | DB matching ≠ reproduction/contamination | A — labels `database_match` / `certified_nonmatch_under_R` / `audit_inconclusive`; "reproduction" only for provenance-supported arms | 01 §D |
| CR-8 | blocker | Tanner canonicalization ≠ code canonicalization | A — "check-presentation key" for exact-presentation dedupe only; equivalence relations defined separately with witnesses; budget exhaustion = unknown | 01 §D |
| CR-9 | major | Dedupe/dominance deletes implementations | A — dedupe verification work only; proxy dominance is a scheduling heuristic; hard pruning needs a dominance certificate or hard-constraint violation (also IA) | 00 §7, 01 §D |
| CR-10 | major | MITM scaling misdescribed | A — preflight pattern/memory/pair counts; measured host feasibility; no universal cutoff | 01 §C |
| CR-11 | major | Distance completeness contract | A | 01 §C |
| CR-12 | minor | BB fixtures; stochastic search not a milestone requirement | A — fixtures with source/polynomials/coordinates/hashes/witness artifacts; deterministic witness validation is the acceptance; discovery speed benchmarked separately | 00 M2 |
| CR-13 | major | L3 conflation | A — witness validation vs existence claims; signed action on all 2k logical generators; folds defined explicitly; compensating Cliffords charged (also IA) | 01 §F |
| CR-14 | major | Bounds need applicability conditions | A — theorem registry with preconditions/scope | 01 §E |
| CR-15 | major | M5 circuit semantics | A — Phase 2 acceptance | 01 §G |
| CR-16 | major | Decoder tests | A — Phase 2 acceptance | 01 §G |
| CR-17 | major | Circuit-distance witnesses are upper bounds only | A (also IA) | 01 §G |
| CR-18 | major | S-B estimand undefined/gameable | A — Phase 2 | 01 §G |
| CR-19 | major | Confirmation rules underspecified | A — frozen confirmation families, budgets, alpha, retry and acceptance rules; zero-failure CP bound stated (also IA) | 01 §H |
| CR-20 | major | Zero false authorization achievable by rejecting everything | A — each control: target gate, expected outcome, paired positive, production-path injection; report 0/N | 01 §I |
| CR-21 | major | N-version authoring vs frozen evaluator | A — authored and frozen pre-campaign at dev time; no shared helpers; disagreement quarantines | 01 §F, 00 M2 |
| CR-22 | major | Transactions/leases/replay | A — immutable job input hashes, monotone lease generations, read-set revalidation, publish-before-reference, crash tests at each commit boundary | 01 §L |
| CR-23 | major | Environment freeze; trusted base | A — pinned dependency closure, CLI versions/settings, solver binaries; TCB declared; overrides cannot create `verified` | 01 §O |
| CR-24 | major | Resource containment | A — cgroup scopes (verified available), descendant kill, byte/nesting caps before parsing, regular files only | 01 §J |
| CR-25 | major | Budget bypass/overspend | A — reservations, brokered calls, separate accounting units, subscription vs billed cost | 01 §K |
| CR-26 | major | D9 lost artifacts | A — complete transitive artifact closure in a durable bundle + reconstruction test | 01 §M, 00 D9 |
| CR-27 | major | Progress/three-strikes can stop science | A — separate scientific success metric from verifier-established blocker progress; orchestrator-owned approach IDs; strikes change policy, never create NoGo | 00 §4.14, §7 |
| CR-28 | major | Cross-campaign leakage | A — namespacing per campaign/arm/split/profile; no audit feedback until sealed; no session reuse | 01 §N |
| CR-29 | major | Arms confound effects | A — baselines defined; per-family single-agent controls; bundled comparisons labelled; random baseline only over G-struct | 00 §10 |
| CR-30 | major | Requirement inference: revision + gold annotations | A — route revision is a work item; annotations distinguish necessary/sufficient/preference, allow multiple routes (Phase 1 S-F uses functional inputs; full A2 inference is Phase 3) | 00 §7, §10 |
| CR-31 | major | Roadmap drops mission comparisons | A — every hypothesis/gate mapped to a phase; Phase 1 cannot support H1/H2 | 00 §10, §11 |
| CR-32 | major | Vertical slice too late | A — Phase 1 restructured: host qualification → minimal live slice first; deferred machinery listed | 00 §11 |
| CR-33 | minor | Proposal field semantics; CSS scope | A — construction params / requested predicates / untrusted witnesses / verifier observations; binary-CSS scope declared | 01 §B |
| CR-34 | major | Qualified D1–D9 | A — adopted with the reviewer's qualifications | 00 §14 |
| CR-35 | major | Missing scientific decisions | A — D10–D17 added | 00 §14 |

## Codex independent analysis (IA) items not already covered above

| ID | Item | Disp. | Where |
|---|---|---|---|
| IA-1 | S-A tuples are A0 (family-free structural targets), not A1; A1 needs functional-operation tasks | A — new S-F suite in Phase 1 | 00 §3, §10 |
| IA-2 | Report code novelty, implementation novelty, autonomy as separate axes | A | 01 §D |
| IA-3 | Certify the needed predicate (d ≥ d_min), not the exact distance | A | 01 §C |
| IA-4 | Separate operational job status from scientific status; typed termination (launch failure / timeout / unsupported / counterexample) | A | 01 §A, §L |
| IA-5 | Reviewers get frozen candidate + assumptions, not proposer transcript | A | 00 §6 |
| IA-6 | No new verifiers/skills mid-campaign; new verifier ⇒ new campaign version | A | 00 §4.3 |
| IA-7 | Reserve confirmation budget before exploration; "no verified solution within budget" unless infeasibility certified | A | 01 §H, §K |
| IA-8 | qa accepted a timeout (exit 124) as refutation | A — anti-pattern row | 00 §13 |
| IA-9 | Kernel defects: `analyze_postselected_map` rtol + conditioning; `TaskSpec.trotter_steps` frozen; `EvidenceLedger` accepts caller-labelled `verified`; `Coordinator` conflates infeasible/unverified and writes trace at end; demo bound 0.07 ⇒ bias 0.14 > 0.1 | A — first, second and fifth **reproduced 2026-10-15**; fixed test-first in M1 as recorded kernel deviations; TaskContract moves algorithm/steps into the candidate | 00 §2, M1 |
| IA-10 | One complete modest route early; open a search dimension only when its evaluator exists | A | 00 §11 |
| IA-11 | arXiv:2606.25011 source not bundled; TMR route needs acquisition, version binding, transcription checks | A — Phase 3 prerequisite | 00 §11 |
| IA-12 | 15 must-not-happen failure modes | A — folded into negative controls / tests | 01 §I |
| IA-13 | `notes/knowledgebase_redesign.md` diagnoses partly stale | A — do not cite Chandra defects without checking source | 00 §13 note |

## Rejected

None. CR-1 is modified rather than rejected: closing the application objective in the first run would
require faithful TMR/RUS instruments from a source paper that is not bundled, plus calibrated resource
closure. Doing it badly would produce exactly the vacuous completion the reviewer warns against, so
the first run's claims are scoped to the algebra level instead.

---

# Round 2 — review of v0.3 (`codex-prompt-review-v0.3.md`) → v0.4

Verdict received: "with changes — not ready for owner approval". All items accepted; none rejected.
Host check added for this round: `codex features list` shows `shell_tool`, `multi_agent`, `apps`, `browser_use`,
`computer_use`, `in_app_browser`, `plugins`, `remote_plugin` enabled by default (so "no tools" must be enforced and
verified, not assumed).

## Triage-verification gaps (R2-T)

| ID | Gap | Disp. | Where in v0.4 |
|---|---|---|---|
| R2-T1 (CR-2/33) | No operation field; sparse shape ambiguous | A — `OperationRef` in `G`; explicit `shape` per matrix; code/operation edit invalidation | 01 §B1–B3 |
| R2-T2 (CR-3) | Prose sections can smuggle claims | A — authoritative report fully templated; LLM text only in a quarantined "Unverified commentary" appendix | 01 §A5, 00 §4.10 |
| R2-T3 (CR-4/5) | Codex web search may be on by default; schemas ≠ tool disable | A — explicit feature/web-search disable, effective tool manifest verified from intercepted request bodies; non-tool-free provider disqualified for K0 | 01 §J1–J2, 00 §2 |
| R2-T4 (CR-8) | Presentation features used as filters | A — true invariants vs presentation features; features only prioritize; nonmatch must cover every relevant DB entry | 01 §D2a–D2b, D4 |
| R2-T5 (CR-11) | Lower-bound rule omits "no nontrivial logical through 2t" | A — `w*` recorded; `w* ≤ 2t` ⇒ exact; Steane t=2 control | 01 §C2, §I |
| R2-T6 (CR-14) | Singleton stated as iff | A — "excluded if"; passing proves nothing | 01 §E |
| R2-T7 (CR-17) | Circuit witness lacks `Df=0, Lf≠0`; graphlike overstatement | A | 01 §G |
| R2-T8 (CR-21) | Twins vs M1 campaign contradiction; small-n twins vs "every proposition" | A — twins in M2 before evaluation; M1 labelled non-evaluation; confirmation-method/range matrix | 01 §F, 00 §11 |
| R2-T9 (CR-22) | Missing directory fsync | A — temp → fsync → rename → fsync dir → DB commit; crash tests between steps | 01 §L |
| R2-T10 (CR-24) | Per-job scopes don't enforce campaign limits; no disk quota | A — campaign parent slice; tmpfs `size=`; aggregate output accounting | 01 §J4, D14 |
| R2-T11 (CR-25) | Reservations lack enforceable maxima | A — broker-enforced request/token ceilings; uncertain charges retained | 01 §K |
| R2-T12 (CR-26) | Bundles lack executable closure | A — archived wheels, verifier snapshot, interpreter hash; offline reconstruction | 01 §M |
| R2-T13 (CR-29) | Arm capabilities unspecified; proposals-and-cost conflict | A — arm capability matrix; separate fixed-proposal / fixed-budget analyses; cost units never summed | 00 §10, §4.12 |
| R2-T14 (CR-30) | Annotation semantics missing | A — necessary / sufficient / preference, multiple routes, uncertainty, scoring rules (Phase 3) | 00 §11 Phase 3 |
| R2-T15 (IA-1) | S-F prescribed routes | A — S-F = target action + hardware operation class; agent picks route; route-constrained tasks moved to S-A; rotations excluded | 00 §3 |
| R2-T16 (IA-12) | 15-mode coverage untraceable | A — mapping table | 01 §I |
| R2-T17 (CR-12/35) | 288-qubit fixture vs cap; D17 index | A — fixtures exempt; D17 = Phase-2 memory estimand | 00 §2, §14 |

## New findings (R2-N)

| ID | Sev. | Finding | Disp. | Where |
|---|---|---|---|---|
| R2-N1 | blocker | CONNECT tunnel cannot establish API-level policy | A — TLS-terminating request-inspecting broker with per-campaign CA; request-level enforcement and accounting; unqualified ⇒ `K0-unenforced`, dev-only | 01 §J1, D15–D16 |
| R2-N2 | major | Disjoint-representative bound uses wrong sector | A — `k·d_P` for P-type representatives | 01 §E |
| R2-N3 | major | Proposition registry incomplete; no revocation/guarantee semantics | A — subject-typed signatures, `block_length_eq`, `target_refuted`, `nogo`, `arm_contrast`; revocation, quarantine, guarantee propagation, field consistency | 01 §A1–A3 |
| R2-N4 | major | Verification sharing by code identity transfers presentation claims | A — predicate-keyed subjects and transport bridges | 01 §A3, §D3 |
| R2-N5 | blocker | No process isolation | A — J0 launch profile (PID/IPC/UTS/net/session, clearenv, fd hygiene, no host sockets) with signalling and secret tests | 01 §J0 |
| R2-N6 | major | Lease fencing doesn't stop execution/spend | A — dispatch state machine, kill-or-retain reservation, per-retry reservations, atomic settlement, same-generation duplicate rejection | 01 §L |
| R2-N7 | major | Scrubbing after hashing breaks evidence | A — sanitize before content addressing; secret-bearing ingest test | 01 §M |
| R2-N8 | major | Phase-1 statistical claims mis-described | A — "no physical-noise sampling"; frozen estimands, clustering, censoring, Holm, separate analyses | 01 §H, D13 |
| R2-N9 | major | H3 ablation lacks oracle/estimand | A — separate experimental ledger scored by intact resolver; estimand = useful counterexamples / cost / time-to-rejection | 00 §10 |
| R2-N10 | major | B6 conflicts with no-tools runtime | A — B6 and librarian deferred to a separately qualified profile (§J6) | 01 §J6, 00 §10 |
| R2-N11 | major | Phase 1 oversized; weak smoke gate | A — Phase 1a (M0–M1) approved first with narrowed scope and deterministic positive S-A/S-F cases; M3 smoke must admit a verified S-F capability; D4 conditional continuation | 00 §0, §11, D4 |
| R2-N12 | minor | D11/D13/D16 conflicts | A — algebra-level agent-performance comparisons allowed; single stop policy; unenforced runs cannot satisfy DoD | 00 §14, §15 |

---

# Round 3 — verification review of v0.4 (`codex-prompt-review-v0.4.md`) → v0.5 (final for Phase 1a)

Verdict received: "yes-with-listed-edits — Phase 1a only"; five MAJOR items, all accepted.

| ID | Finding | Disp. | Where in v0.5 |
|---|---|---|---|
| R3-1 | Reservations omit input/cache charges | A — broker-enforced input bound (UTF-8 bytes ≥ tokens) and maximum-total-charge reservation incl. cache-write premiums | 01 §K |
| R3-2 | Dispatch/crash recovery scheduled after M1 | A — durable dispatch state machine, intent-before-launch, broker log-before-forward, crash tests and no-release-on-kill are M1 prerequisites before any live call; M3 keeps only concurrency extensions | 00 M1, M3; 01 §L |
| R3-3 | Semantic conflicts across bounds undetected | A — interval-consistency check per (subject, quantity) at admission; quarantine + descendant invalidation; P1a control (lower 5 with upper 3) | 01 §A3, §I; 00 M1 |
| R3-4 | `RuntimeMaxSec` unsupported on slices (systemd 249, confirmed on host) | A — slice keeps aggregate limits; campaign deadline via supervisor timer stopping child scopes and disabling broker; per-job `RuntimeMaxSec` on scopes | 01 §J4; 00 M0 |
| R3-5 | Bundle lacks runtime bytes | A — archive a relocatable uv-managed standalone CPython 3.10.22 (available here) + wheels; reconstruction uses only the archived runtime (glibc baseline recorded) | 01 §M; 00 M0 |

Also recorded in v0.5 from the M0 probe (not review findings): Claude MCP-connector tool leakage under the normal
HOME and its fix; Codex WebSocket transport and tool-free frames; TLS interception working for both CLIs; the
`~/.local/bin/codex` wrapper's silent fallback to Claude (all four Codex reviews verified as genuine gpt-6-astra runs).
