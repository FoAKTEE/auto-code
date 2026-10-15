# Master self-prompt — zkqec: a modular multi-agent zero-knowledge QEC code-discovery workflow

> **Status:** DRAFT v0.4, awaiting owner approval. Nothing in §5–§11 has been built.
>
> **Companion files:**
> - `01-technical-contracts.md` holds the **binding** semantics (§A–§O).
> - `reviews/TRIAGE.md` disposes of every review finding (rounds 1 and 2).
>
> **Inputs:**
> - all of `ref-code/qecforge`;
> - `notes/ref-analysis/{qa,chandra,question-gen}.md`;
> - three Codex reviews (gpt-6-astra, reasoning max): a blind analysis, a review of v0.2 (35 findings),
>   and a review of v0.3 (17 triage gaps plus 12 new findings).
>
> **Owner decisions:** §14.

---

## 0. One-page summary

**What gets built.** `zkqec` (name: D1) is a **deterministic orchestrator** in which LLM agents are
**untrusted proposers** of quantum-code *design candidates*.
- Only registered verifiers, running from a frozen and archived environment, can admit **typed
  propositions**, keyed to the right subject (presentation, code, basis, operation, …), into an
  **append-only, hash-chained ledger**.
- A resolver derives current authority by **entailment**, with revocation and quarantine.
- The authoritative report is generated entirely from templates.
- Agents never assert facts that verifiers own.

**How agents run (Phase 1).** Agents run headless in **call mode, with no tools**, inside a bwrap
sandbox with:
- PID, IPC, network and session isolation;
- an allowlisted filesystem;
- a **request-inspecting egress broker**. It terminates TLS locally, allows only model-API requests
  that declare no tools, and counts every request.

**What "zero knowledge" means.**
- It means *zero task-specific expert intervention after the input contract is frozen* (QECForge §1).
- It does not mean absence of pretrained knowledge, which is unidentifiable, and it is not
  cryptographic.
- It is made auditable through disclosed profiles: knowledge level × prompt profile × grammar
  capability manifest.
- Held-out tasks and the known-code DB live in a vault the proposers cannot reach.
- Novelty is reported only as `database_match`, `certified_nonmatch_under_R`, or
  `audit_inconclusive`.

**Why this beats qa, Chandra and question-gen.** All three admitted results on LLM verdicts or on
checks the proposer wrote, and isolated workers by prompt. QEC claims are computable, so here:
- computation decides;
- isolation is enforced by the OS and qualified on this host;
- progress means verifier-established improvement (details in §13).

**Phases.** Each row needs its own approval (D4).

| Phase | Scope | Claims it can support |
|---|---|---|
| **1a** (approve now) | M0 host qualification → M1 minimal live vertical slice: explicit matrices + one algebraic family, tractable distance targets, one provider, random baseline, one signed-Clifford target, deterministic positive S-A and S-F cases | Algebra level only; results labelled `single-implementation, non-evaluation` |
| **1b** | M2 verifier hardening (twins, identity, theorem registry, controls) → M3 multi-agent runtime → M4 held-out evaluation on **S-A** (structural, A0+) and **S-F** (functional logical operations, A1) | Algebra-level facts plus statistical agent-performance comparisons |
| 2 | Circuit level (memory, schedules, decoders) | Memory-level, within declared domains |
| 3 | Application closure (G1–G3, instruments, resources) | Task-level, via the resolver |
| 4 | Held-out autonomy at application level (G4) | H1–H4 |

**Process.**
- Every milestone ends with tests, an independent Codex review, and written triage.
- Any unresolved scientific or containment blocker stops advancement.
- Commits follow **Chandra's commit principles exactly**.

---

## 1. Role and mission

You are Claude Code acting as **lead engineer and research scientist** for the owner. Build and
validate a **modular, multi-agent, zero-knowledge quantum-error-correcting code-discovery workflow**
whose specification is `ref-code/qecforge/`.

QECForge defines what an evidence-gated, application-aware co-design system must and must never do, and
it supplies a tested kernel. It lacks the following, which you must deliver in honest stages:
- model-driven discovery;
- literature agents;
- scalable distance computation;
- circuit-level evaluation inside campaigns;
- a campaign-wide claim resolver;
- program sandboxing;
- held-out evaluation.

The target object (QECForge §2) is `x = (E, A, C, L, G, P, D, F, η)` for a frozen task
`T = (H, ρ0, O, t, S, ε, α, H_hw, Q_max)`. Minimize `E[T_answer]` subject to:
- `Q_peak ≤ Q_max`;
- `P(|μ̂−μ|>ε) ≤ α`;
- a valid implementation, with evidence in its declared domain.

Phase 1 addresses only these components:
- **C**, the presentation;
- **L**, the basis;
- **G**, restricted to logical Clifford operations from the task's declared physical-operation class.

Every other component is `absent/unsupported` (§B1).

## 2. Ground truth (verified on this host, 2026-10-15)

**QECForge kernel** (`ref-code/qecforge/src/qecforge/`):
- 60 core tests pass; 4 optional tests are skipped.
- Modules:
  - `gf2`
  - `codes`
  - `distance` (complete bounded MITM)
  - `structure` (pure-CSS permutation action, restricted H/S, DSATUR)
  - `workload`
  - `gadgets` (n ≤ 12)
  - `scheduling`
  - `decoding`
  - `backends` (untested)
  - `statistics`
  - `resources`
  - `schemas`
  - `evidence`
  - `agents`
  - `search`
- Normative docs: `docs/*.md` and paper appendices A–H.

**Reproduced kernel defects.** These are fixed test-first in M1 and recorded as kernel deviations:
1. `analyze_postselected_map` uses `np.allclose`'s default `rtol`. As a result it:
   - accepts a non-unitary target at `tolerance=1e-10`;
   - passes a 1-rad relative phase on a tiny Kraus operator, judging only by absolute residual.
2. `TaskSpec` freezes `trotter_steps`, which is an implementation choice.
3. `EvidenceLedger` accepts records that the caller labels `verified`.
4. `Coordinator` conflates "infeasible" with "unverified", and writes its trace only at the end.
5. The demo's operator bound 0.07 implies an observable bias of 0.14, above its 0.1 target. It is
   correctly not claim-ready.

**BB fixtures.** I recomputed n and k from the polynomials in Bravyi et al. 2024, Table 3:
`[[72,12]]`, `[[90,8]]`, `[[108,8]]`, `[[144,12]]`, `[[288,12]]`. The distances are not yet certified
by us. Fixtures are trusted test data and are exempt from the D10 caps.

**Host:**
- Linux, 144 cores, 503 GB RAM, Python 3.10.12.
- PyPI is reachable: stim, ldpc, pymatching, pynauty, highspy, ortools, galois and numba are all
  available.

**Isolation primitives:**
- `bwrap` works: masking, `--unshare-net`, read-only filesystem.
- `socat` is present.
- User cgroup scopes work.
- Codex reported `--unshare-all` failing inside *its* sandbox.

**Claude CLI** (`claude` 2.1.300):
- `-p --output-format json` returns tokens, USD and `server_tool_use`.
- It supports `--json-schema`, `--tools`, `--disallowedTools`, `--no-session-persistence` and
  `--effort`.

**Codex CLI** (`codex` 0.160.1, gpt-6-astra, max):
- It supports `--output-schema`, `--json`, `--ephemeral`, `--ignore-user-config`, `--ignore-rules`
  and `--disable <feature>`.
- `codex features list` shows these tool-bearing features **enabled by default**: `shell_tool`,
  `multi_agent`, `apps`, `browser_use`, `computer_use`, `in_app_browser`, `plugins`,
  `remote_plugin`.
- Per Codex's review citing the official docs, **cached web search may be on by default**. An absent
  `--search` flag is therefore not proof that web search is off.
- The prompt must be passed as an argument with `< /dev/null`.

**Unknown until M0:**
- whether each CLI can be made tool-free;
- whether TLS interception works for each CLI.

## 3. Binding definitions

**Zero knowledge (ZK).** Zero task-specific expert intervention after the input contract is frozen.

Disclosed priors that are allowed:
- pretraining;
- task-independent role contracts;
- the grammar capability manifest;
- verifier and simulator libraries;
- the hardware/noise domain in the contract, including its **allowed physical-operation class**.

A1 functional inputs are also disclosed task inputs:
- required logical actions;
- precision;
- resources.

Interventions (any of these voids ZK for that campaign):
- solution-bearing requirements (named structures, seed polynomials, reference parameters);
- human scientific action during a campaign.

**Profiles.** These are disclosures, not semantic guarantees.

| Axis | Values |
|---|---|
| Knowledge | `K0`: no retrieval, enforced by the J1 broker; a campaign lacking broker qualification is labelled `K0-unenforced` and cannot count toward evaluation (D16). `K1`: a frozen corpus with the target excluded. `K2`: reproduction profile with frozen source bytes, deferred (§J6). |
| Prompt | `P-min`: binary-CSS formalism, verifier interface, objective, operation class. `P-domain`: adds task-independent QEC methodology. |
| Grammar (capability manifest) | `G-open`: explicit sparse matrices; sandboxed programs from P1b. `G-struct`: named families with exact algebra, e.g. `two_block_group_algebra`. |

Valid combinations:
- **Zero** = `K0 + P-min + G-open`.
- **Prior** = `K0 + P-domain + G-open + G-struct`.
- Optional **prompt-effect** arm = `K0 + P-domain + G-open`.
- `P-min + G-struct` is invalid, because the families are named.

The prompt linter is **syntactic screening only**.

**Autonomy and suites.**

| Level | Humans supply | Suite |
|---|---|---|
| A0 | family, constraints, objective | — |
| A0+ | family-free structural targets `(n_max, k_min, d_min, w_max, deg_max)` | **S-A**, including route-constrained structural benchmarks |
| A1 | functional requirements | **S-F** |
| A2 | Hamiltonian task, accuracy, hardware | Phase 3 only |

What an **S-F** task specifies:
- a required logical Clifford action U on `k_req` logical qubits, as a signed tableau;
- the hardware's allowed physical-operation class, with costs;
- code constraints.

The agent chooses the code, the basis and the physical operation (route) within that class.

What is excluded from S-F:
- Tasks that prescribe the route ("must be a permutation") are S-A route-constrained benchmarks.
- Non-Clifford functionality (e.g. parallel small-angle rotations) has no verified functional
  feasibility in Phase 1.

**Claims.**
- Propositions, receipts, entailment and outcomes follow §A.
- The decision labels follow QECForge. *Mathematically incompatible within this route* is never
  merged with *unknown within budget*.
- "Best" means the best verified result in the declared space and budget.
- Without a certificate of infeasibility, the outcome is "no verified solution within budget".
- Code novelty, implementation novelty and autonomy are separate axes.

## 4. Constitution

1. **The agent proposes; the verifier admits.**
   - Issuer identity comes from execution records.
   - Verifier-owned fields supplied by a proposer are rejected (§B3).
   - Agent agreement and self-review are never evidence.
   - A skeptic refutes only with a checked witness.
2. **Single gated write path.**
   - One admission service with hash-chained append-only events.
   - Rebuildable projections.
   - `ledger verify` checks it.
   - Agents have no DB access, no verifier access and no role variables.
3. **Frozen, archived evaluator** (§O).
   - Every agent call and every verifier batch is checked against the manifest. A mismatch aborts the
     campaign.
   - No new verifier mid-campaign. A new verifier means a new campaign version.
   - Overrides never create `verified`.
4. **No fabricated science.**
   - A missing capability yields `unavailable` or `unsupported`.
   - A kill or timeout yields `unknown`.
   - No mocks in scientific paths.
5. **Logic hygiene.**
   - Unknown ≠ refuted.
   - A timeout is not a certificate.
   - A witness proves only its own direction.
   - A complete enumeration certifies `d ≥ 2t+1` only if it found no nontrivial logical through `2t`
     (§C2).
   - A refuted premise leaves its derived claims `unknown`.
6. **Predicate-keyed, domain-scoped evidence** with explicit transport bridges (§A3) and
   dependency-local invalidation (§B2).
7. **Everything is charged.**
   - Reservations are bound to enforceable ceilings.
   - Uncertain charges are retained.
   - Confirmation is reserved up front (§K).
8. **Untrusted inputs stay data.**
9. **OS-enforced isolation and request-level egress control** (§J0–J2), qualified in M0.
   - A provider that cannot run tool-free is disqualified for K0.
   - Missing telemetry is `unknown`.
10. **Templated reporting.**
    - The authoritative report is generated only from propositions.
    - LLM commentary is quarantined in a labelled appendix that does not count as evidence (§A5).
    - Every summary is rendered from the DB.
11. **Statistical discipline** (§H).
    - Phase 1 has no physical-noise sampling, but its arm comparisons *are* statistical, with frozen
      estimands.
12. **Budget fairness.**
    - Fixed-proposal and fixed-budget comparisons are separate declared analyses.
    - Cost units are never summed (§K).
    - In evaluation arms, a usage limit pauses the arm; it never swaps the model.
13. **Autonomy log** of every human action.
14. **Two notions of progress.**
    - *Scientific success*: a verified objective improvement.
    - *Blocker progress*: a verifier-established closure or a strengthened bound, used for scheduling.
    - Approach IDs are owned by the orchestrator.
    - Three failures of an approach change the policy, never create a NoGo.
    - A NoGo needs a scoped completeness certificate.
15. **Complete retention.** Bundles can be reconstructed offline from their archived executable
    closure (§M).
16. **Namespacing.** No state crosses campaign, arm, split or profile boundaries, and there is no audit
    feedback before sealing (§N).

## 5. Architecture

```
repo root = /data/haiyangw/claude/auto-code
  src/zkqec/
    kernel/        vendored qecforge 0.1.0 (byte-identical) + PROVENANCE; tested patches = new evaluator versions
    contracts/     pydantic (frozen, extra=forbid): TaskContract (no algorithm/steps), HardwareDomain (incl.
                   allowed operation class), Profile, Budget, CampaignManifest, DesignCandidate, OperationRef,
                   ComponentEdit, Proposition (signature registry), Receipt, WorkItem, AgentCallRecord, NoGo
    admission/     single writer: receipts → hash-chained events, projections, resolver (entailment, transport,
                   revocation, quarantine, guarantee propagation), settlement of jobs/reservations in one txn
    store/         sanitize-then-address; fsync file + rename + fsync dir before referencing
    grammar/       capability manifests: explicit_sparse (P1a), two_block_group_algebra (P1a), program (P1b)
    verify/        L0 caps · L1 CSS algebra · L2 preflight + completed MITM + witness checks (P1a), ISD (P1b) ·
                   L3 signed tableau action for permutations / transversal 1q Cliffords (P1a), folds (P1b) ·
                   theorem registry (P1b) · twins (P1b, frozen before evaluation) · [P2] circuits · [P3] L5/L6
    identity/      presentation keys (P1a); relations, invariants vs features, novelty labels (P1b)
    sandbox/       launch profiles agent-call / program (J0); campaign cgroup slice; canaries; output caps
    broker/        TLS-terminating request-inspecting proxy, per-campaign CA, socat bridge, policy, accounting
    agents/        job runner (state machine reserved→dispatched→{completed|uncertain|failed}→settled,
                   generations, idempotency, backoff); providers ClaudeCall (P1a), CodexCall (P1b), Replay,
                   Fake(tests); role contracts; digest builders
    orchestrator/  campaign state machine, simple priority scheduler, budgets, resume, PAUSE, stop rules
    firewall/      profiles, prompt linter, vault access, namespacing, contamination audit (P1b)
    evalh/         S-A/S-F generators + difficulty calibration (P1b), arms, controls, analysis (§H)
    report/        templates, linter, commentary quarantine, bundles, offline `audit --bundle`
    cli.py         qualify-host | campaign {run,resume,status,pause,abort,seal} | verify | ledger | audit | eval
  roles/  configs/  tests/  _common/ .gitmessage AGENTS.md  notes/  prompt/  results/  runs/(gitignored)
```

**Deferred** until a measured need appears, or until Phase 1b or later:
- generated programs;
- folds;
- ISD;
- evolutionary search and bandits;
- broad DB equivalence searches;
- route-agent and reporter roles;
- B6 and the librarian runtime;
- quality-diversity grids and an LLM planner;
- `session` mode;
- local-Clifford equivalence;
- SAT lower bounds.

There are no dashboards.

## 6. Agent roles

| Role | Phase | Produces (proposals only) | Default model (D2) |
|---|---|---|---|
| Designer-A | 1a | component edits: explicit matrices or two-block params, bases, operation witnesses | Claude Opus 5.5 |
| Designer-B | 1b | same, independently | Codex gpt-6-astra (max), if qualified tool-free |
| Skeptic (always the other family from the reviewed proposer) | 1b | structured counterexample *requests* that verifiers execute | the other family |
| Route agent (S-F) | 1b | conditional routes within the operation class, falsifiers, revisions | Claude Opus 5.5 |
| Commentator | 1b | prose only in the quarantined commentary appendix | Claude Sonnet 5.5 |

**Contracts and requests.**
- Role contracts are task-independent, written per profile, and ≤5 KB.
- Requests are bounded, verdict-free digests rendered from the DB. They contain:
  - top candidates per niche, with hashes;
  - failure-reason histograms;
  - scoped NoGos;
  - open blockers;
  - skeptic findings stated as facts.

**Review and output rules.**
- Reviewers receive the frozen candidate and its assumptions, never the proposer's transcript.
- Schema-invalid output is charged as `malformed_proposal`.
- A proposer never files evidence about its own candidate.
- LLM ranking is used only for non-computable aspects, and is blinded and cross-family.
- The topology is recorded.

## 7. Orchestration

1. **Freeze.**
   - Load configs and write the manifest (§O).
   - Run qualification self-test and canaries.
   - Reserve confirmation capacity.
   - Create a namespaced run directory.
2. **Task intake.**
   - S-A: theorem screening (P1b), then the target predicates.
   - S-F: target action plus operation class. In P1b, the route agent proposes routes. Route revision
     becomes a work item whenever verification refutes a route.
3. **Loop.** The scheduler leases one of: propose, verify(candidate, level), falsify (P1b),
   revise-route (P1b), or a baseline step. Each proposal goes through:
   1. caps;
   2. L0;
   3. build;
   4. L1;
   5. presentation key (deduplicate *work*, never variants);
   6. L2 witnesses;
   7. L2 completed lower bounds to the *needed* predicate;
   8. L3 for S-F;
   9. resolver;
   10. settlement.
4. **Confirmation.**
   - P1a: re-execution from the sealed bundle.
   - P1b: per the confirmation-method/range matrix (§F), using the twins.
5. **Seal.**
   - Run the post-hoc audit (DB labels in P1b, broker/telemetry audit, autonomy log).
   - Render the templated report and run the linter.
   - Build the bundle, run the offline audit, and commit the index.

**Stop rules** (D13). A run stops on:
- budget exhaustion;
- the target met *and* confirmed;
- no scientific improvement for K rounds while the blocker queue is empty;
- an integrity failure.

## 8. Verification ladder

| Level | Content | Phase | Contract |
|---|---|---|---|
| L0 | byte/nesting/list caps; shape/dimension caps before allocation | 1a | §B3 |
| L1 | `H_X H_Zᵀ=0` over GF(2), k by ranks, canonical pairing, weights/degrees | 1a | §A |
| L2 | preflight; completed MITM with `w*` record; witness validation; ISD witnesses (1b) | 1a/1b | §C |
| L3 | signed tableau action on all 2k generators for permutations / transversal 1q Cliffords; folds (1b) | 1a/1b | §F |
| L4 | circuit memory semantics, decoders, replayed circuit-distance witnesses | 2 | §G |
| L5–L6 | instruments; resource closure | 3 | QECForge App. D–E |

## 9. Knowledge firewall (P1b)

- **Vault.** Holds held-out tasks, annotations, the known-code DB and the planted controls. Only
  audit and eval processes can reach it.
- **Known-code DB.** Built by the librarian under its own declared profile (§J6). Entries are
  recomputed from matrices, and coverage is recorded.
- **Task generation.** Order of steps:
  1. Screen with the theorem registry; incompatible tasks are labelled, not silently dropped.
  2. Run the difficulty ladder: DB lookup → random same-grammar search → bounded single agent, each
     in its own namespace.
  3. Freeze the procedure before generating held-out tasks.
  4. Audit held-out tasks for overlap with dev tasks.

## 10. Evaluation (P1b, M4)

**Arm capability matrix.** All arms share the verifiers, the confirmation family and the task set,
and run in call mode with no tools.

| Arm | Proposer(s) | Profile | Grammar | Analyses |
|---|---|---|---|---|
| B0 | known reference/fixtures | — | — | reference only |
| B1 | random sampler, declared distribution | — | two_block subset | fixed-proposal, fixed-budget |
| B1e | evolutionary, declared mutations | — | two_block subset | both |
| B4-C / B4-X | single Claude / single Codex | run separately under Zero and under Prior | per profile | both |
| B5-Zero | Designer-A + Designer-B + cross skeptic (+ route agent for S-F) | Zero | G-open | both |
| B5-Prior | same | Prior | G-open + G-struct | both |
| B6 | deferred (§J6) | — | — | — |

**Comparison caveats.**
- B5 vs B4 is a **bundled system comparison**: topology and model mixture are confounded, unless the
  owner funds the separating arms.
- B1/B1e use a different grammar from B5-Zero. That difference is labelled.

**Controls.** Run as paired positive/negative controls through the production path, and report `k/N`
(§I).

**H3 at algebra level.** Weakened policies (skeptic off, domains off) run in a **separate experimental
ledger** that the intact resolver scores. They never produce authoritative receipts. Their estimand is
useful counterexamples, verification cost and time-to-rejection. It is not authorization correctness,
which deterministic certificates already fix.

**Analysis.** Estimands, clustering, censoring, multiplicity and cost units follow §H, and are frozen
before held-out generation.

**Hypothesis and gate map.**

| Item | Phase 1b | Later |
|---|---|---|
| G0 | algebra part | encoding equivalence (P3) |
| G1 / G2 / G3 | — | P2 / P2–P3 / P3 |
| G4 | algebra-level held-out | application level (P4) |
| H1, H2 | **cannot be supported** | P3/P4, with B2/B3 |
| H3 | algebra-level, as defined above | P2/P3 |
| H4 | algebra-level bundled comparison | P4 |

## 11. Build plan

**Every milestone:**
1. Implement, with one commit per DAG node (§12).
2. Tests green.
3. Background Codex review → `notes/reviews/Mx-codex.md`.
4. Written triage → `notes/reviews/Mx-triage.md`.
5. **Open scientific or containment blockers stop advancement.**
6. Update `notes/PROGRESS.md`.

### Phase 1a (approved first)

**M0 — Host qualification.**

Build `zkqec qualify-host`, which tests each property in §J0–J5, §K and §O:
- process isolation, including signalling a sacrificial outside process, inherited secrets and
  control sockets;
- TLS-intercepting broker per CLI: allowlist, request-body tool policy, accounting;
- filesystem allowlist with canaries;
- fresh CLI homes;
- Claude tool-free mode;
- Codex feature-disable and web-search-disable, verified from intercepted request bodies;
- structured output for each CLI;
- enforceable token and request ceilings;
- campaign cgroup slice: aggregate memory, tasks, CPU, wall time, tmpfs disk;
- output caps;
- pinning and archiving of wheels.

*Accept:*
- Every property has a passing positive test and a passing negative test.
- A qualification report artifact is produced.
- Unqualified properties are listed. A provider that is not tool-free is disqualified for K0. If the
  broker is unqualified, live runs are dev-only and labelled `K0-unenforced` (D16).

**M1 — Minimal live vertical slice.** Build:
- the vendored kernel, with test-first fixes for the five §2 defects;
- contracts subset;
- store, admission and events;
- resolver with the P1a signatures and rules;
- L0, L1, L2 (preflight, corrected completion rule, witnesses), L3 (signed tableau for permutations
  and transversal single-qubit Cliffords);
- grammars `explicit_sparse` and `two_block_group_algebra`;
- a random baseline;
- ClaudeCall via the broker;
- a bare scheduler;
- templated report with linter;
- bundles and the offline `audit --bundle`;
- measured **certification feasibility tables**, which set the D10 target ranges.

*Accept:*
- The qecforge reference table is reproduced.
- The Steane `t=2` case certifies d=3, not d≥5.
- **Deterministic positive cases** go through the production resolver with no LLM: one S-A (a fixture
  meets its predicates) and one S-F (a fixture code with a known permutation logical action is
  verified).
- Paired negatives are rejected.
- A test proves that nothing is admitted except through admission.
- **One live S-A task and one live S-F task**, within the D5 cap, produce sealed bundles that
  reconstruct offline. Their results are labelled `single-implementation, non-evaluation`.
- Cost telemetry is recorded.
- `notes/postmortems/M1-slice.md` is written.

### Phase 1b (continues automatically only if D4 conditions hold)

**M2 — Verifier hardening.**
- §C: ISD, completeness records.
- §D: identity, relations, invariants vs features.
- §E: theorem registry.
- §F: folds and the **twins**, blind-authored by Codex and frozen, plus the confirmation-method/range
  matrix.
- BB fixtures (§C5).
- The full P1 paired controls, plus the 15-mode mapping (§I).

*Accept:*
- Fixture witnesses validate.
- Lower bounds are reported as intervals with records.
- Presentation and code identities are distinguished; for example, the weight-transport control is
  rejected.
- Twins agree on the declared distributions and on mutations.
- All P1 controls meet expectations, with their positives authorized.

**M3 — Multi-agent runtime.** Build:
- CodexCall, if qualified;
- Designer-B, skeptic, route agent and commentator contracts;
- digests;
- the full job state machine (§K, §L);
- the `program` family (J4);
- namespacing;
- B1e;
- the S-F route pipeline.

*Accept:*
- Crash tests pass at every boundary, including the dispatch-uncertain case.
- Stale and duplicate completions are rejected.
- Uncertain charges are retained.
- Escape tests fail closed.
- Tampering aborts the campaign.
- A live smoke run across the qualified providers yields **at least one verified S-F capability**
  (`implements` admitted for an agent-chosen route), not just operational completion.
- `notes/postmortems/M3-smoke.md` is written.

**M4 — Phase-1 evaluation.** Build:
- the librarian DB with coverage (once J6 is qualified; otherwise novelty is `audit_inconclusive`);
- S-A and S-F generators with calibration and the vault;
- the pilot, sized from telemetry (D5);
- the frozen analysis (§H, D13);
- held-out runs;
- bundles;
- a templated report including the H3 experimental ledger and the controls;
- a Codex audit.

*Accept:* the report covers the §10 arms at campaign level, with null results included, `k/N`, and
every assertion entailed.

### Later phases (each needs owner go-ahead with Phase-1 costs)

| Phase | Content |
|---|---|
| **2** | §G circuit level. First freeze the estimand (D17). Then: stim circuits from schedules; both bases; an independent small simulator; BP+OSD / graphlike matching with flags; replayed `Df=0, Lf≠0` witnesses; S-B; decoder and schedule roles. |
| **3** | Acquire and version-bind arXiv:2606.25011, with transcription checks for any TMR route. Then: (H, ρ0, O) transformations with equivalence tests; L5 instruments; L6 queue-aware resources. **A positive reference case must close the observable-unit error budget.** Requirement inference with route revision. B2/B3 on S-C. Annotations that separate *necessary* constraints from *sufficient routes* and *preferences*, allow multiple routes, record annotation uncertainty, and use declared matching/scoring rules. No universal recall is claimed against incomplete gold annotations. |
| **4** | G4 at application level; H1–H4; hardware, robustness and transfer suites; the full ablation matrix. |

## 12. Engineering process

**Git: Chandra's commit principles, exactly** (owner directive).
- The gate files are byte-identical copies, recorded in `_common/PROVENANCE.md`.
- Install with `bash _common/hooks/install.sh`. The spec is `_common/contracts/commit_template.md`.
- Title: `type(scope)[!]: summary`.
- Body: typed objects `- why|finding|change|run|result|verify|caveat|next|files:`. Every finding and
  result is tagged `[SOLID|PRELIMINARY|HOLE|FUTURE]`.
- `!` appears if and only if there is a `BREAKING CHANGE:` footer.
- Footers: `Iter/Run/Refs/Verify/Claim`.
- One commit per DAG node or finer; tests go in before or with their code.
- Stage only the node's files, then inspect `git diff --cached`.
- Check the message with `COMMIT_GATE_STRICT=1 _common/hooks/commit-msg`, then land it with
  `COMMIT_GATE_STRICT=1 git commit --file`.
- Never use `--no-verify`. No tool or model attribution. No large data or mirrors.
- Branch `zkqec-dev` (D7). Procedure: `.claude/skills/commit-gated/SKILL.md`.

**Codex in parallel.**
- Run it in the background at every milestone and every prompt revision:
  `codex exec -m gpt-6-astra -c model_reasoning_effort='"max"' -s read-only -C <repo> -o <out.md> "<prompt>" < /dev/null`.
- Codex also blind-authors the twins in M2, inside a sandbox without the first implementation.

**Other rules.**
- **Subagents:** use them only after contracts are frozen, with one owner per file.
- **Verifiers are test-first:** positive, negative, adversarial, and twin tests.
- **One owner per fact:** docs-drift and fingerprint tests; `docs/IMPLEMENTATION_STATUS.md` separates
  implemented / tested / validated / planned.
- **Never modify `ref-code/`.**
- **Keep long outputs in files** and keep `notes/PROGRESS.md` current.

## 13. Lessons from the reference workflows → mechanisms

| Observed failure (source) | Mechanism here |
|---|---|
| "Solid" means an LLM refuter plus a judge; the gate checks provenance, not math (qa `gate.py:628`) | Computable typed propositions and registered verifiers (§4.1, §A) |
| A proposer-written verification command; "a commit exists" counts as evidence (Chandra `admission.py:128,346`) | Registered verifiers from a frozen, archived snapshot (§O) |
| A worker edited the gate; workers launched runners (qa `1aa331b`, `ec89f24`) | Call mode without tools; J0 isolation; no DB access; tampering aborts |
| Role carried in an env var; the orchestrator self-promoted rows (Chandra) | Issuer taken from execution records; the admission-only path is tested (M1) |
| The validator existed only in tests (Chandra `main.ts`) | The skeptic and twins sit on the production path; tests assert this (M2, M3) |
| Proposer self-reviews and copied verdicts (qa; question-gen `question_gen.py:1489`) | No self-evidence; verdict-free digests |
| Same-family judging; taste monoculture (qa: Claude judged 102/102) | Metric selection; the skeptic is cross-family by construction |
| Activity counted as progress; 42 % of jobs cancelled (qa `runner.py:82`) | Two notions of progress; approach IDs owned by the orchestrator |
| A timeout (exit 124) was accepted as a refutation (qa `failed_checks`) | Typed outcomes; a timeout is `unknown` (§A4) |
| Gates were advisory; blocked finalists were proved anyway (qa T3) | Overrides only re-scope; they never create `verified` |
| Summaries drift; notes in /tmp (qa, Chandra) | Summaries rendered from the DB; complete offline bundles (§M) |
| Fail-open filter; fixed-fraction taste; calibration on n=2 (question-gen) | Fail-closed gates; absolute predicates; paired controls |
| No difficulty calibration (question-gen) | Theorem screening plus a solver ladder (§9) |
| 60–90 KB packs and 242 KB of skills; doc drift | ≤5 KB contracts; bounded digests; drift tests |
| Infrastructure outran validation (qa schema v1→v5 in 4 days) | M0–M1 slice first; deferred list (§5) |
| BLAS threads × RLIMIT killed checks (qa `6f35327`) | Single-threaded BLAS; cgroup slices; a kill yields `unknown` |
| **Reuse:** hash-chained events + projections (qa); executable admission records (Chandra); `codex_jobs` runner (question-gen); move47 sandbox + canaries; receipted digests (qa `derive.py`); evidence-only transport + firewall tests (question-gen) | `admission/`, `agents/`, `sandbox/`, `firewall/` |

Chandra's redesign notes are partly stale. Cite a Chandra defect only after re-checking the source.

## 14. Decisions for the owner (defaults in bold)

- **D1 Name.** **`zkqec`**, an internal name. It means operational autonomy, not cryptographic ZK.
- **D2 Runtime and models.**
  - **`claude -p` / `codex exec` in call mode through the J1 broker**, conditional on M0.
  - A provider that cannot run tool-free is disqualified for K0.
  - Models: **Opus 5.5** for Designer-A and the route agent; **gpt-6-astra (max)** for Designer-B;
    **cross-family** skeptic; **Sonnet 5.5** for the commentator.
  - These are pilot choices.
- **D3 Profiles.** **Zero and Prior as in §3, plus an optional prompt-effect arm.** No
  absence-of-prior-knowledge claim is made.
- **D4 Scope.**
  - **Approve Phase 1a (M0–M1) now.**
  - Phase 1b continues automatically **only if** all of the following hold:
    - M0–M1 are accepted;
    - no blockers are open;
    - the projected Phase-1b cost, taken from M1 telemetry, fits D5.

    Otherwise I stop and ask.
  - Phases 2–4 each need a separate approval.
- **D5 Budgets.**
  - **M1 live ≤ $5; M3 smoke ≤ $10; Phase-1b dev pilot ≤ $150. The held-out run needs separate
    approval, with an estimate.**
  - These are ceilings. Development work and Codex reviews are tracked separately.
- **D6 Vendoring.** **Byte-identical copy; every tested patch is a new evaluator version.**
- **D7 Branch.** **`zkqec-dev`**, merged only on your request.
- **D8 Autonomy.**
  - **Autonomous within an approved phase, with a report per milestone.**
  - Open scientific or containment blockers stop advancement.
  - I ask before budget changes, scope changes, or anything irreversible.
- **D9 Artifacts.** **Bundles (§M) in `/data/haiyangw/claude/zkqec-archive/`**, with the bundle index
  and hashes committed in `results/`. Offline reconstruction is mandatory.
- **D10 Domain.**
  - **Binary CSS codes with +1 signs.**
  - **Constructor size n ≤ 200** (fixtures exempt).
  - **S-A/S-F target ranges are set from the M1 measured certification tables.**
- **D11 Claims.**
  - **Allowed:** algebra-level facts, and statistical **agent-performance comparisons at algebra
    level** (H3, H4).
  - **Not allowed:** memory, circuit, application or advantage claims.
- **D12 Equivalence.** **Same stabilizer group, qubit permutation, X/Z swap.** Local-Clifford
  equivalence comes later. The DB covers published BB/two-block tables, small HGP and
  surface/toric/color codes, and Steane/Golay-derived codes, with a coverage report.
- **D13 Analysis and stopping.**
  - **Primary:** fixed-budget verified-feasible fraction on held-out S-A/S-F, task-clustered and paired.
  - **Secondary:** fixed-proposal results; censored time-to-first-verified and cost-to-first-verified;
    Holm correction.
  - **Stop rules as in §7.**
- **D14 Compute.** **Campaign slice: ≤ 96 CPUs, ≤ 256 GB RAM, ≤ 50 GB tmpfs per campaign, ≤ 6 h wall
  per campaign, ≤ 4 concurrent calls per provider.**
- **D15 Trust and credentials.**
  - **The TCB is as in §O.**
  - **The broker locally decrypts your model traffic, holds credentials in memory only, and logs
    sanitized metadata.**
  - **Credentials live in fresh per-job homes.**
  - **Phase-1 models have no tools.**
- **D16 If K0 cannot be enforced.** **Live runs are dev-only, labelled `K0-unenforced`, and do not
  count toward evaluation or the definition of done.**
- **D17 (Phase 2, deferred).** The memory estimand and horizon, to be frozen before any S-B campaign.

## 15. Definition of done

**Phase 1a:**
- M0 has a qualification report in which every property passes or is explicitly listed as unqualified,
  with its consequence.
- All M1 acceptance items pass.
- The two live tasks have offline-reconstructable bundles.
- Every Codex review is triaged.

**Phase 1b (the first full run):**
1. M2–M4 acceptance items pass, and no blocker is open.
2. A K0-enforced, multi-agent campaign runs end-to-end within budget. It produces:
   - a manifest and the event log;
   - propositions and receipts;
   - broker-level call accounting;
   - the autonomy log;
   - novelty labels with coverage, or `audit_inconclusive`;
   - a bundle that reconstructs offline.
3. A templated report compares the arms at campaign level with uncertainty. It includes null results
   and `k/N` controls, and it states that H1, H2 and application claims are out of scope.
4. Every scientific assertion is entailed.
5. `docs/IMPLEMENTATION_STATUS.md` and `notes/PROGRESS.md` are exact.
