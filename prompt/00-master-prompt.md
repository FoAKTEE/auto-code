# Master self-prompt — ZK-QEC: a modular multi-agent zero-knowledge code-discovery workflow

> **Status: DRAFT v0.2, awaiting owner approval. Nothing in Sections 5–11 has been built yet.**
> Written 2026-10-15 by Claude (Opus 5.5).
>
> **Inputs read:**
> - all of `ref-code/qecforge`: paper sections and appendices, docs, source, tests, configs, schemas,
>   artifacts;
> - detailed analyses of the three reference workflows in `notes/ref-analysis/{qa,chandra,question-gen}.md`.
>
> **Reviews:** Codex (gpt-6-astra, reasoning=max) runs in parallel. Its blind independent analysis
> and its review of this prompt go in `prompt/reviews/`.
>
> **Owner decisions:** the decisions needed before the build starts are in **Section 14**.

---

## 0. One-page summary (read this first)

**What I will build.**
- A Python package, roles, and configs called `zkqec` (name open, D1).
- A **deterministic orchestrator**. Claude and Codex run headless as **untrusted proposers**, each
  inside an OS sandbox.
- Proposals cover codes and their implementations: code, logical basis, gates, syndrome schedule,
  decoder, and resource policy.
- A **single gated write path** carries every proposal through a **verification ladder**:
  1. exact algebra
  2. distance certificates
  3. logical structure
  4. circuit-level simulation
  5. resource closure
- Only **registered verifiers run from a frozen code snapshot** may write `verified` or `refuted`
  into an **append-only, hash-chained evidence ledger**.
- **Agents can never assert gate-owned facts** such as n, k, d, logical action, or error rates. The
  verifiers compute them.
- **Reports are rendered from the ledger.** A claim linter rejects any number that lacks evidence
  IDs.

**What "zero knowledge" means here.** It means *zero task-specific expert intervention after the
input contract is frozen* (QECForge §1). I make it measurable with a **ZK profile** made of three
parts:
- a knowledge level K0/K1/K2;
- a prompt profile P-min/P-domain;
- a grammar G-open/G-struct.

The strictest arm, **"Zero" = K0 + P-min + G-open**, is the AlphaZero-like reading: only the rules
of the game (stabilizer formalism plus verifier) and pretrained knowledge. Two further guards:
- Held-out tasks and a known-code database sit in a vault that agents cannot see; the vault is
  enforced by bwrap, not by prompt.
- "New" always means *not in frozen known-code DB vX*.

**Why this should beat qa, Chandra, and question-gen.** Section 13 gives the evidence. All three
admitted results on LLM judgement or proposer-written checks, and isolated workers through prompts
and environment variables (workers edited the gate in qa).

In QEC almost every claim is *computable*. The fixes are:
- the verifier computes and the agent proposes;
- OS-level isolation with canary tests;
- progress = gate-verified improvement over a frozen baseline, not activity;
- metric-based selection instead of LLM taste;
- digests rendered from the DB instead of drifting summaries;
- small, size-capped, domain-rich role contracts instead of tens of KB of process prose.

**Build order.**
- M1 foundation
- M2 verifier ladder
- M3 agent runtime plus the **first real smoke campaign** (an early vertical slice, then a
  post-mortem)
- M4 search, baselines, and task suites
- M5 circuit level
- M6 application layer
- M7 pilot, then held-out evaluation

Every milestone ends with tests, an independent Codex review, written triage, and a git commit with
no Claude co-author trailer.

---

## 1. Role and mission

You are Claude Code acting as **lead engineer and research scientist** for the owner. Build and validate a
**modular, multi-agent, zero-knowledge quantum-error-correcting code-discovery workflow** whose scientific
specification is `ref-code/qecforge/` (the *mission*). QECForge specifies what an evidence-gated,
application-aware code co-design system must and must never do, and supplies a tested reference kernel. It
explicitly does **not** implement: model-driven discovery, literature agents, scalable distance, circuit-level
evaluation inside campaigns, a campaign-wide claim resolver, program-constructor sandboxing, held-out
evaluation. Turn the specification into a working discovery workflow, staged so each stage is honest on its own.

Target design object (QECForge §2): `x = (E encoding, A algorithm, C code, L logical basis, G gadgets,
P placement/schedule, D decoder, F factory/retry policy, η error allocation)` for a frozen task
`T = (H, ρ0, O, t, S, ε, α, H_hw, Q_max)`; minimize `E[T_answer(x)]` s.t. `Q_peak ≤ Q_max`,
`P(|μ̂−μ|>ε) ≤ α`, and *valid implementation with evidence in its declared domain*. A code matrix alone is not
an answer; rate/distance/overlap are intermediate features.

## 2. Ground truth you can rely on (verified 2026-10-15)

- **QECForge kernel** (`ref-code/qecforge/src/qecforge/`, 60 core tests pass here, 4 optional skipped):
  - `gf2.py`: GF(2) algebra.
  - `codes.py`: `CSSCode` and constructors for Steane, HGP, general BB, and the reference chain.
  - `distance.py`: complete bounded MITM distance certification with witnesses and a
    `EnumerationBudgetExceeded` exception.
  - `structure.py`: structural reports, CSS permutation logical action, global H/S conditions, DSATUR
    batching.
  - `workload.py`: Pauli IR, first-order compilation, Z-only symmetries, Jordan–Wigner.
  - `gadgets.py`: dense accepted maps for n ≤ 12.
  - `scheduling.py`: serialized CSS schedule.
  - `decoding.py`: lookup decoder and code-capacity experiments.
  - `backends.py`: stim/ldpc/pymatching adapters, not yet integration-tested.
  - `statistics.py`, `resources.py`: statistics and resource helpers.
  - `schemas.py`: strict pydantic contracts.
  - `evidence.py`: standalone `EvidenceLedger` and `ArtifactStore`.
  - `agents.py`, `search.py`: sequential `Coordinator`, `Proposer`/`JSONModel` protocols.
- **Normative QECForge rules:**
  - `docs/ARCHITECTURE.md`;
  - `docs/ASSUMPTIONS_AND_INTERACTIONS.md`: 29 interaction rows, the six-qubit falsifier, and the
    decision labels;
  - `docs/EXPERIMENTS.md`: hypotheses H1–H4, gates G0–G4, arms B0–B6, budget fairness, statistics,
    ablations, and planted falsifiers;
  - `docs/SECURITY.md`, `docs/EXTENDING_AGENTS.md`, and paper appendices A–H.
- **Machine:** Linux, 144 cores, 503 GB RAM, no GPU.
- **Python stack:**
  - Python 3.10.12, numpy 2.2.6, scipy 1.15.3, pydantic 2.13.4;
  - networkx, sympy, and z3 are installed;
  - pip works: stim 1.16 is downloadable, and ldpc, pymatching, and solvers are installable as
    needed.
- **Sandbox:** `bwrap` **works here**. A tested run masked a directory, gave no network
  (`ENETUNREACH`), and made the filesystem read-only. `ref-code/Chandra/move47/bin/worker-sandbox`
  is a proven template for running `claude` inside bwrap with only the model API reachable.
  Chandra reports that Codex's *internal* sandbox failed for its validators on this host, although
  Codex `-s read-only` did run shell reads for me. Isolation must therefore come from our own
  wrapper, whatever the CLI sandbox flags say.
- **LLM CLIs:**
  - `claude` CLI 2.1.300 works headless. `claude -p --output-format json` returns tokens, USD cost,
    and `web_search_requests`.
  - `codex` CLI 0.160.1 runs `gpt-6-astra` at reasoning `max`. It supports `--output-schema`, `-o`,
    and `--json`. The prompt must be passed as an argument with `< /dev/null`, because the stdin
    form failed here.
- **Reference workflows:** `ref-code/{qa,Chandra,question-gen}`. Their mechanisms and failures are
  analysed in `notes/ref-analysis/` and summarized in §13.

## 3. Binding definitions

**Zero knowledge (ZK).** Zero task-specific expert intervention after the input contract is frozen. Disclosed
priors that do not violate ZK: model pretraining, task-independent role prompts, constructor grammar, verifier
and simulator libraries, the hardware/noise model in the contract. Anything encoding a specific task's answer
(named target family, hand-written requirements, seed polynomials, reference parameters) violates ZK for that
campaign and is logged as an intervention.

**ZK profile** (recorded in every manifest):
- Knowledge level:
  - `K0`: no retrieval. Web tools are disabled in the CLI config, and the usage records are audited
    to confirm zero web requests.
  - `K1`: a frozen, hashed corpus with the target excluded; every retrieved span is logged.
  - `K2`: open retrieval, allowed only in the reproduction control arm B6.
- Prompt profile:
  - `P-min`: CSS/stabilizer formalism, verifier API, objective. No family names, no construction
    recipes.
  - `P-domain`: adds task-independent QEC methodology (construction families, search operators,
    known trade-offs). This is a disclosed prior and is ablated.
- Grammar:
  - `G-open`: sandboxed programs plus explicit sparse matrices.
  - `G-struct`: named algebraic families.

The headline **Zero arm** is `K0+P-min+G-open`. The **Prior arm** is `K0+P-domain+(G-struct ∪ G-open)`.

**Autonomy levels** (QECForge §1):
- A0: humans give family, constraints, and objective.
- A1: humans give functional requirements.
- A2: humans give only Hamiltonian task, accuracy, and hardware budget.

Requirement-tuple suites (S-A) are A0/A1 stepping stones and never support an A2 claim.

**Claim vocabulary.** A proposition's status is `verified`, `refuted`, or `unknown`.
- *Measurement admitted* is distinct from *claim supported*. Negative results are first-class
  outcomes.
- Decision labels: *valid in domain*; *conditionally valid / requires measurement*; *invalid for
  this candidate*; *mathematically incompatible within this route*; *unknown within budget*. The
  last two are never merged.
- "Best" means best verified in the declared space and budget.
- "New" means not equivalent to any entry of known-code DB vX.

Evidence kinds:
- `exact_algebra`, `numerical_check`, `statistical_estimate`;
- `solver_claim`: unchecked solver output;
- `proof_checked`: certificate checked by an independent checker;
- `assumption`, `derived`;
- `agent_assertion`: can never become `verified`.

## 4. Constitution — invariants every module and commit must respect

1. **The agent proposes; the verifier admits.** Only registered, versioned verifiers and statistical
   procedures write `verified` or `refuted`.
   - Gate-owned fields are rejected if a proposer supplies them: n, k, d, weights, logical action,
     error rates, and novelty.
   - Agent agreement, confidence, and self-reviews are never evidence.
   - A skeptic refutes only with a direct witness.
2. **Single gated write path.** All research state changes go through `ledger.submit()`.
   - Steps in order: shape validation, then slow observations outside the lock, then
     `BEGIN IMMEDIATE`, then state rules, then apply, then append the event.
   - The event log is append-only: triggers refuse UPDATE and DELETE.
   - It is hash-chained.
   - Projections are rebuildable from it, and `ledger verify` re-checks the chain and the artifact
     hashes.
   - Agents never get DB write access, verifier write access, or a role variable.
   - The orchestrator stamps run id, role, model, and provider from its own records, never from
     agent output.
3. **Frozen evaluator.**
   - Verifiers run from a snapshot extracted with `git archive <commit>` into the run directory and
     hashed.
   - A campaign refuses to start from a dirty evaluator tree.
   - The manifest hashes: task, hardware domain, budget, grammar, verifier snapshot, role prompts,
     model IDs, ZK profile, corpus, and vault manifest. These are re-checked before and after every
     agent call and verifier batch.
   - A mismatch aborts the campaign with a typed record.
   - A gate override is allowed only as a recorded human event.
4. **No fabricated science.**
   - A missing backend produces a typed `unavailable` outcome.
   - No mocks in scientific paths; fake providers exist only in tests and are labelled.
   - A resource-limit kill is `unknown`, never a verdict on the claim.
   - No past-tense results without artifacts.
5. **Logic hygiene.**
   - Unknown ≠ refuted.
   - A timeout is not a certificate.
   - A failed proof is not a counterexample.
   - 0 failures ≠ 0 probability.
   - A refuted premise makes the derived claim `unknown`.
6. **Domain-scoped evidence** (QECForge App. B domain fields). Cross-domain reuse needs a verified
   bridge, and repairs create new domains.
7. **Everything is charged and recorded.**
   - Invalid, duplicate, timeout, unavailable, and refused attempts all consume budget.
   - Every model call records provider, model, prompt hash, response hash, tokens, cost, latency,
     retries, errors, and tool usage.
8. **Untrusted inputs stay data.** Literature, model outputs, earlier messages, and comments in
   generated code cannot change the task, evaluator, permissions, or budgets.
9. **OS-enforced isolation.** Agent sessions and generated programs run under our bwrap wrapper.
   - Masked: the vault, ledger DB, verifier sources' write paths, other runs, `$HOME` except the
     needed CLI credentials, `/tmp`, and `ref-code/`.
   - Programs get no network. Agents get only the model API.
   - A canary test proves the masking on every campaign start.
   - Generated code is never `eval`/`exec`'d in the orchestrator.
10. **Ledger-backed reporting.** Every human-readable or LLM-readable summary is rendered from DB
    queries at build time, and the claim linter gates every report.
11. **Statistical discipline.**
    - Selection data and confirmation data are disjoint.
    - Fixed-shot Clopper–Pearson applies only to fixed-shot designs.
    - Families of tests get an explicit alpha allocation.
    - The campaign replicate is the unit when comparing arms.
12. **Budget fairness.**
    - Arms share verifiers, tasks, and hardware domain.
    - Arms are compared at equal proposals and at equal total cost.
    - In evaluation arms, a usage limit pauses the run; it never silently swaps the model.
13. **Autonomy log.** Every human action during a campaign is logged. A task-specific intervention
    removes the ZK label.
14. **Progress = a gate-verified improvement over the frozen baseline/archive**, never activity.
    Circuit breakers and stopping rules count only that.

## 5. Architecture (modules and interfaces)

```
repo root = /data/haiyangw/claude/auto-code
  src/zkqec/
    kernel/       vendored qecforge 0.1.0 core, byte-identical + PROVENANCE (hashes, MIT notice);
                  fixes only with a failing test + note; extensions live elsewhere.
    contracts/    frozen pydantic (extra=forbid): TaskContract, HardwareDomain, ZKProfile, Budget,
                  CampaignManifest, Proposal (grammar union), Requirement, ExperimentRequest, WorkItem,
                  Evidence, DomainManifest, AgentCallRecord, SkepticFinding, NoGo, ReportClaim.
                  Closed, versioned registries for families, noise models, decoders, metrics.
    ledger/       SQLite WAL: hash-chained append-only events + projections (candidates, evidence,
                  domains, work items, calls, no-gos); claim resolver (effective status, dependency-local
                  invalidation); `verify`, `rebuild`, export to JSONL; ledger-head checkpoint in git.
    store/        content-addressed artifacts (sharded), integrity-checked reads.
    grammar/      constructor registry: params schema + size precheck (before allocation) + builder.
                  G-struct: explicit_sparse, two_block_group_algebra (abelian groups; covers BB/GB/QC),
                  hypergraph_product (classical seeds from a sub-grammar), lifted_product (cyclic),
                  quasi_cyclic. G-open: `program` (sandboxed Python → matrices JSON) + explicit_sparse.
    sandbox/      bwrap wrapper (move47 pattern): profiles `program` (no net) and `agent` (model API
                  only); rlimits; single-threaded BLAS; canary + escape tests; typed outcomes.
    verify/       registered verifiers (name, version, domain fields, cost model) → Evidence:
                  L0 schema/size · L1 algebra · L2a distance upper bound (randomized information-set
                  witness search; every witness re-verified) · L2b lower bound (complete MITM, parallel;
                  symmetry reduction only with a stated, tested proof obligation; optional SAT with proof
                  logging + independent proof check ⇒ proof_checked, else solver_claim) · L3 logical
                  structure (Tanner-graph automorphisms, induced logical action, H/S/fold structure,
                  disjoint supports + batching bound) · L4 circuit (stim memory both bases, detector
                  determinism, DEM hyperedges preserved, BP+OSD / graphlike matching, fixed-shot CP,
                  circuit-distance witness search) · L5 small-n instruments · L6 resource closure.
                  N-version: second implementations (written blind by Codex from the spec) for rank/k,
                  small-n distance, logical action; cross-checked on random + planted-broken instances.
    archive/      canonical equivalence key (Tanner-graph canonical certificate up to qubit/check
                  permutation + cheap invariants); global dedupe; kill-only filters; quality-diversity
                  grid (n, k, d-interval, max weight, degree, structure flags); per-fidelity Pareto
                  fronts (never mixing fidelities); NoGo records (exhaustive negative results).
    search/       non-LLM engines (baselines and agent tools): random same-grammar, evolutionary/QD,
                  local search; later SAT-guided construction.
    agents/       job runner (question-gen `codex_jobs` pattern: fingerprinted jobs, attempt-scoped files,
                  atomic promotion after schema+semantic validation, resume, wall budgets, stdin=DEVNULL,
                  usage-limit backoff 5/15/45 min); providers ClaudeCLI, CodexCLI, Replay, Fake(tests);
                  role registry (contract files, versioned + fingerprinted, CI-checked); digest/pack
                  builders (bounded, receipted, DB-rendered, verdict-stripped); modes `call` and
                  `session` (bounded tool-using session in the sandbox with a read-only screening CLI;
                  its findings are agent_assertion until the gate re-runs them).
    orchestrator/ campaign state machine; work queue (queued, leased, running, verified, failed, unknown,
                  budget_exhausted, unavailable); leases + heartbeat + orphan adoption; scheduler
                  (feasibility-bottleneck/information-value policy vs bandit baseline); budget accountant;
                  async LLM pool + process pool for verifiers; checkpoint/resume; PAUSE/STEER files;
                  three-strikes per (niche, approach); stopping rules.
    firewall/     ZK-profile enforcement: prompt linter, CLI tool policies (web off in K0) + post-hoc usage
                  audit, corpus manifests, vault (held-out tasks, requirement annotations, known-code DB,
                  planted controls) mounted only for auditor/eval processes, contamination report.
    evalh/        task-suite generators with difficulty calibration; arms B0–B6; negative controls;
                  metrics; campaign-level statistics; ablations.
    report/       ledger → Markdown/JSON; claim linter; autonomy, contamination, cost sections.
    cli.py        zkqec campaign {run,resume,status,pause,abort} | verify | ledger {verify,rebuild} |
                  audit | report | eval | sandbox-selftest
  roles/          role contracts (≤ 5 KB each; skeleton Role · Inputs · Procedure · Deliverable · Verify ·
                  Anti-patterns); profiles P-min / P-domain; fingerprinted into every manifest
  configs/        tasks, hardware domains, ZK profiles, budgets, campaigns, eval protocols
  tests/          unit · property · negative-control · firewall · sandbox-escape · docs-drift ·
                  integration (fake provider) · slow (exact distance)
  notes/          PROGRESS.md, DECISIONS.md, ref-analysis/, reviews/, postmortems/
  prompt/         this prompt + Codex reviews
  results/        tracked: sealed campaign manifests, ledger exports (compressed, size-capped), reports
  runs/           (gitignored) working campaign directories
```

Plugin interfaces, each registered with name, version, and hash so that domain manifests pin them:
- `Provider.generate(role, request, schema) -> (json, usage)`
- `Constructor.build(params) -> CSSCode`
- `Verifier.run(refs, budget) -> [Evidence]`
- `SearchPolicy.propose(ctx) -> [Proposal]`
- `Stage.promote(cand, ledger) -> decision`
- `TaskSuite.generate(split, seed) -> [TaskContract]`
- `Role.render(digest) -> request`, together with `Role.output_schema`

**Scope guard:** no dashboards, editor tooling, or self-improvement subsystems. The CLI and the
rendered reports are the whole UI. Complexity is added only when a real run shows it is needed.

## 6. Agent roles (proposal authority only)

| Role | May produce | Must not authorize | Default model |
|---|---|---|---|
| Task analyst | workload analysis (via deterministic compiler), equivalent representations, verification obligations | changed target; unverified sector restriction | Opus 5.5 |
| Requirement abductor | functional needs, route-conditional structures, falsifiers, alternatives | globally necessary constraints without proof | Opus 5.5 |
| Code designers (≥2, heterogeneous, niche-targeted) | grammar proposals / sandboxed programs | distance, novelty, gate correctness | Opus 5.5 + gpt-6-astra |
| Logical-operations specialist | basis changes, automorphism gates, batching plans | product action from supports | Opus 5.5 |
| Circuit/schedule specialist | extraction orders, flag/ancilla plans | circuit distance from a graph | gpt-6-astra |
| Decoder specialist | decoder configs, benchmark requests | real-time feasibility from offline accuracy | Sonnet 5.5 |
| Resource analyst | error conversion, resource-model inputs, requests for missing measurements | task feasibility with missing evidence | Sonnet 5.5 |
| Skeptic (always a different family from the reviewed proposer) | counterexample requests and adversarial tests, in structured form | refutation without a witness | cross-family |
| Planner (optional; ablated vs bandit) | next-experiment choice from digest | evaluator changes; consensus → evidence | Opus 5.5 |
| Auditor (post-hoc, vault access) | novelty/contamination labels | — (deterministic matching decides) | deterministic + LLM summary |
| Reporter | prose around ledger tables | anything the linter rejects | Sonnet 5.5 |

**Rules for all roles:**
- Role contracts are task-independent, short, and domain-rich within their P-profile, and the
  linter checks this.
- Inputs are bounded **digests rendered from the DB** and passed as facts without verdicts. A digest
  contains:
  - top archive entries per niche, with hashes;
  - failure-reason histograms;
  - NoGo records;
  - unresolved blockers;
  - skeptic findings.
- Raw transcripts are never passed, and proposers never see their checkers' verdict prose.
- Outputs must validate against a JSON Schema, or the attempt is charged as invalid.
- A proposer never files evidence about its own candidate.
- LLM taste or ranking is used only for genuinely non-computable aspects. It is blinded and
  cross-family, and it is never the main selector.
- Topology, shared memory, and model versions are recorded.
- H4 (multi-agent beats a token-matched single agent) is a hypothesis to be tested.

## 7. Orchestration runtime

Campaign = QECForge Algorithm 1 made concrete:
1. **Freeze.**
   - Load configs, snapshot the evaluator, and write the `CampaignManifest`.
   - Run the sandbox canary.
   - Start an immutable run directory.
2. **Task analysis:** deterministic compile, then the task-analyst role. Output: workload,
   alternatives, obligations.
3. **Requirement abduction.** Produce `Requirement` records, plus deterministic pre-checks where
   theory allows. Examples:
   - full disjoint same-type representatives need `n ≥ k·d_Z`;
   - transversal-universality restrictions.
4. **Loop** while budget remains and no stopping rule has fired:
   - The scheduler leases one of: propose (designer × niche/route), verify (candidate × fidelity),
     falsify (skeptic request), or baseline step.
   - Every proposal follows this path:
     1. L0
     2. construct (in the sandbox if it is a program)
     3. L1
     4. canonical dedupe
     5. kill-only filters: invalid, duplicate/equivalent, dominated within the campaign archive,
        bound-infeasible
     6. L2a
     7. promotion rules
     8. L2b
     9. L3
     10. L4 (elite only)
     11. L6
   - The ledger, archive, and digests update after every item.
   - Three failed attempts of one (niche, approach) force a switch. NoGo records feed back into
     digests.
5. **Confirmation.** Re-evaluate on fresh seeds and shots, and cross-check with the N-version
   verifiers.
6. **Post-hoc audit.** Match against the known-code DB, scan all proposals for contamination, audit
   tool usage (web_search_requests must be 0 in K0), and check the autonomy log.
7. **Report:** render, run the claim linter, seal the run (file-hash manifest), export to
   `results/`.

**Persistence, concurrency, and stopping.**
- Persistence: SQLite WAL.
- `resume` rebuilds all state from the DB. Leases with heartbeats survive crashes, and orphaned work
  is adopted.
- Concurrency:
  - async LLM pool, with per-provider caps;
  - process pool for verifiers, with a core cap and `OMP/MKL/OPENBLAS_NUM_THREADS=1`;
  - per-job subprocess timeouts.
- Stopping rules:
  - budget exhausted;
  - no verified improvement for K scheduler rounds;
  - the target is met *and* confirmed.
- Human channel: `PAUSE` file; `STEER.md` is logged as an intervention.

## 8. Verification ladder, promotion, and evidence

| Level | Checks | Evidence kind | Promotion |
|---|---|---|---|
| L0 | schema, grammar, size caps before allocation | exact | if valid |
| L1 | `H_X H_Zᵀ = 0` over GF(2); k by ranks; canonical logical pairing; weights/degrees | exact_algebra | k ≥ 1 and hard caps |
| L2a | randomized information-set search for low-weight logicals; each witness re-verified | exact_algebra (upper bound + witness) | no witness below target d |
| L2b | complete MITM to weight 2t (parallel); proved symmetry reduction; optional SAT + proof check | exact_algebra / proof_checked / solver_claim | elite or requirement-critical |
| L3 | automorphisms; induced logical action; H/S/fold; disjoint supports + batching bound | exact_algebra | route requires it |
| L4 | stim memory (both bases); determinism; DEM; BP+OSD / graphlike matching; fixed-shot CP; circuit-distance witnesses | numerical_check / statistical_estimate | elite per niche, fixed shots |
| L5 | small-n accepted/rejected instrument maps | numerical_check | parallel-injection routes |
| L6 | conditional resource model; task claims only via resolver with closed domains | derived | — |

**Scaling reality.** Exact MITM lower bounds are practical only for small `n·t`, roughly `n ≲ 100`
with `d ≲ 8`. Larger codes such as `n = 144, d = 12` will usually end as certified **intervals**
unless proved symmetry reduction or proof-checked solvers succeed. Report intervals; never round
them to a point.

## 9. Knowledge firewall, contamination, novelty

**Vault.**
- Contents:
  - held-out task seeds;
  - requirement annotations, used to score requirement inference;
  - the known-code DB;
  - planted negative controls.
- It is mounted only into auditor and eval processes, never into proposers.
- A canary test proves that proposers cannot list it.

**Known-code DB.**
- It is built before campaigns by a separate librarian step at K2. Ideally a different model family
  builds it, and every entry's parameters are **recomputed from matrices by our verifiers**, never
  copied from papers.
- It is versioned and fingerprinted.
- Its entries hold: family, params, `[[n,k,d]]` (as an interval if needed), canonical key,
  invariants, source.
- It is never shown to K0 proposers. It is used post-hoc to label results *reproduction* versus
  *not in DB vX*.

**Prompt linter.** It scans role contracts and every rendered request for:
- the current task's hidden targets;
- held-out identifiers;
- family names in P-min arms.

**Difficulty-calibrated task generation for the S-A suites.** This fills question-gen's missing
piece. Each candidate target is run up a ladder:
1. known-DB lookup;
2. random/greedy same-grammar search with budget B;
3. a bounded agent.

Then targets are labelled:
- Targets that violate the quantum Singleton bound (`n − k ≥ 2(d − 1)`) are labelled
  *incompatible*. The quantum Hamming bound is applied only to tasks flagged nondegenerate. LP
  bounds may be added later.
- Targets the cheap rungs solve are labelled *easy*.
- Each label is reported separately.

## 10. Evaluation harness

**Suites.**
- **S-A:** algebraic requirement tuples `(n_max, k_min, d_min, w_max, deg_max, structure flags)`.
  - Items are labelled *known-feasible*, *unknown*, or *incompatible*.
  - Dev and held-out splits use disjoint seed families.
- **S-B:** circuit-memory targets (logical error per round per logical qubit ≤ ε_L at p), under a
  declared noise model and decoder, with a qubit budget.
- **S-C:** application workloads: TFIM 1D/2D, real spinless hopping via JW, random local Pauli
  workloads, each with a hardware domain and Q_max.

**Arms.**
- B0: expert/known reference.
- B1: random, same grammar.
- B1e: evolutionary/QD, no LLM.
- B2: code-only rate/distance objective.
- B3: human requirements plus automatic search.
- B4: single model, token- and tool-matched.
- B5: full multi-agent, in two variants: Zero and Prior.
- B6: retrieval/reproduction control (K2, labelled).

**Negative controls** (QECForge EXPERIMENTS.md), planted through the same write path:
- rank computed over the reals;
- ℓ=2 chain claimed to have d=6;
- S vs S†;
- wrong automorphism action;
- incomplete basis;
- colliding or mixed-sector schedule;
- zero acceptance;
- non-graphlike matching input;
- stale calibration hash;
- noisy data labelled exact;
- source text requesting evaluator changes;
- unheralded failure treated as a retry.

Plus workflow-level attacks:
- a proposer supplying gate-owned fields;
- an agent trying to write the DB or read the vault;
- a role variable spoofed in the environment.

Target: false authorization = 0.

**Metrics.**
- verified feasible fraction;
- time, tokens, and cost to the first verified feasible result;
- best verified objective;
- Pareto hypervolume (fixed reference and normalization);
- invalid and duplicate fractions;
- requirement precision/recall against the vault annotations;
- contamination rate;
- total cost.

**Statistics.**
- The campaign replicate is the unit, with paired seeds.
- Campaign-level bootstrap only where runs are exchangeable.
- A pilot of about 5–10 seeds per arm, then freeze the protocol and sample size, then the held-out
  run.
- Failed and censored campaigns are kept.

## 11. Build plan — milestones and acceptance tests

Each milestone runs this loop:
1. implement;
2. tests green;
3. background Codex review (read-only, gpt-6-astra, max), saved to `notes/reviews/Mx-codex.md`;
4. written triage: fix or justify;
5. commit with no Claude co-author;
6. update `notes/PROGRESS.md`.

- **M1 Foundation.**
  - Build: scaffold; `.venv`; pinned deps (adding stim, ldpc, pymatching); vendored kernel with
    provenance; contracts and registries; ledger (hash-chained events, triggers, projections,
    resolver, verify/rebuild); store; evaluator snapshot plus manifest hashing; CLI skeleton.
  - *Accept:*
    - all 64 kernel tests pass, with the optional ones un-skipped;
    - ledger tests: append-only, chain tamper detection, domain mismatch, unknown propagation, the
      refuted-premise rule, assumption ≠ verified, gate-owned fields rejected;
    - a dirty evaluator tree refuses to start.
- **M2 Verifier ladder L0–L3, grammar, dedupe, N-version.**
  - *Accept:*
    - the qecforge reference table is reproduced exactly;
    - known BB parameters `[[72,12,6]]`, `[[90,8,10]]`, `[[108,8,10]]`, `[[144,12,12]]` are
      recovered as verified upper-bound witnesses with honest lower bounds;
    - Steane and HGP checks pass;
    - the N-version implementations agree on ≥10⁴ random small instances and on the planted broken
      ones;
    - canonical dedupe matches qubit-permuted copies and separates non-equivalent codes;
    - the in-scope negative controls are rejected.
- **M3 Agent runtime, sandbox, firewall, minimal orchestrator, then the FIRST REAL SMOKE CAMPAIGN.**
  - *Accept:*
    - sandbox escape tests fail closed: network, FS writes, vault read, DB write, fork bomb,
      memory, CPU, and wall time;
    - the canary passes;
    - a deterministic fake-provider campaign runs end-to-end;
    - resume after SIGKILL reproduces the state;
    - evaluator tampering aborts the run;
    - schema-invalid responses are charged;
    - **a test fails if anything can be admitted without the gate**;
    - a live K0 Zero-arm smoke campaign on 2–3 tiny S-A tasks with real Claude and Codex, within
      the D5 cap, produces a ledger-backed report;
    - `notes/postmortems/M3-smoke.md` records what to change, decided by evidence.
- **M4 Search engines, baselines, multi-fidelity, QD archive, task-suite generator, negative-control
  harness.**
  - *Accept:*
    - B1, B1e, B2, and B3 run on S-A dev;
    - per-fidelity fronts and diversity metrics are produced;
    - budgets are matched;
    - false authorization is 0 on all planted controls.
- **M5 Circuit level (L4) and schedule search.**
  - *Accept:*
    - stim memory circuits are deterministic in both bases;
    - non-graphlike input to matching is refused;
    - BP+OSD runs;
    - CP intervals are produced;
    - selection and confirmation seeds are disjoint;
    - circuit-distance witnesses are verified;
    - schedule proposals are verified before use.
- **M6 Application layer (A2-lite) on S-C.**
  - *Accept:*
    - requirements carry falsifiers and alternatives;
    - requirement P/R is scored against the vault;
    - no task-level claim is authorized while blockers are open.
- **M7 Evaluation.**
  - Run the pilot on dev, then freeze the protocol, then run held-out S-A/S-B (and S-C if M6
    passes).
  - Arms: B1, B1e, B4, B5-Zero, B5-Prior, plus B0 and B6 labelled.
  - The report is rendered from the ledger, covers ablations and negative controls, and gets a
    Codex audit.

## 12. Engineering process

- **Git.**
  - Work on branch `zkqec-dev` (D7).
  - Commit at coherent steps and at each milestone, with plain messages.
  - **Never** add `Co-Authored-By: Claude …` or "Generated with Claude Code".
  - Do not commit `ref-code/` (it contains nested repos; it is gitignored), `runs/`, `.venv/`,
    secrets, or large binaries.
  - Checkpoint the ledger head into git at milestones.
- **Codex in parallel.** Run this in the background at each milestone, and for this prompt:

  ```
  codex exec -m gpt-6-astra -c model_reasoning_effort='"max"' -s read-only -C <repo> -o <out.md> "<prompt>" < /dev/null
  ```

  Inside campaigns, Codex also serves as a heterogeneous proposer, the cross-family skeptic, and the
  blind author of the N-version verifiers.
- **Subagents.** Parallelize modules only after their contracts are frozen. One owner per file.
  Integrate centrally.
- **Verifiers are test-first.** Every verifier needs positive, negative, adversarial, and
  cross-check tests before any agent may depend on it.
- **One owner per fact.**
  - Rules that can be enforced mechanically live in code; docs point to them.
  - Docs-drift tests keep README, CLI help, and role contracts consistent.
  - Prompt fingerprints are checked by tests.
- **Honesty.**
  - `docs/IMPLEMENTATION_STATUS.md` separates implemented, tested, validated, and planned.
  - Failures and skips are quoted verbatim.
  - No ceremony that is not enforced.
- **Never modify `ref-code/`.**
- **Self context hygiene.** Keep long outputs in files, and keep `notes/PROGRESS.md` current so the
  work survives compaction.

## 13. Lessons from the reference workflows → mechanisms here

| Observed failure (source) | Mechanism in zkqec |
|---|---|
| Acceptance = LLM refuter + LLM judge; "solid" verifies provenance, not math (qa `gate.py:628-662`) | Deterministic verifiers compute every gate-owned fact; LLM review only for non-computable aspects (§4.1, §8) |
| Verifier command written by the proposer; "a commit exists" = evidence (Chandra `admission.py:128,346`) | Registered verifiers from a frozen `git archive` snapshot; proposer cannot choose checks (§4.3) |
| Worker edited the gate to admit its own submission; workers launched their own runners (qa `1aa331b`, `ec89f24`) | bwrap isolation; no DB/verifier write access; orchestrator stamps identity; tamper ⇒ abort (§4.2, §4.9) |
| Role separation by env var `CHANDRA_ROLE`; orchestrator self-promoted 16 rows (Chandra) | No role variables; identity from orchestrator records only; test that admission without gate fails (M3) |
| Adversarial validator existed only in tests (Chandra `main.ts`) | Skeptic and N-version checks wired into the main loop; a test asserts the path is live (M3) |
| Proposer files its own novelty/kill reviews; copies its checkers' verdicts (qa `propose_batch.md:44-47`; question-gen `question_gen.py:1489-1506`) | Proposers never write evidence; orchestrator reads checker outputs directly; verdicts stripped from digests (§6) |
| Same-family judging; 5 Opus taste samples = one opinion (qa: Claude did 102/102 judgements) | Metric selection; cross-family skeptic enforced by orchestrator; taste only blinded + cross-family + non-primary (§6) |
| "Progress" = any new row; circuit breaker blind to churn; 42% jobs cancelled (qa `runner.py:82`) | Progress = verified improvement; breakers and stopping on that only; three-strikes per (niche, approach) (§4.14, §7) |
| Gates advisory: validity-blocked finalists proved anyway (qa T3) | Binding gates; overrides only as recorded human events (§4.3) |
| Summaries/digests drift from the DB (qa `T1/REPORT.md`); notes in /tmp or gitignored (Chandra) | All summaries rendered from DB at build time; durable tracked `results/` + `notes/` (§4.10) |
| Filter fails open; fixed-fraction taste cuts; calibration on n=2 (question-gen) | Fail-closed gates; absolute thresholds; held-out evaluation; negative controls with 0 false authorization (§10) |
| No difficulty calibration; solve probes saturate (question-gen) | Difficulty ladder + bound checks in task generation (§9) |
| 60–90 KB packs; 242 KB of skills; docs drift from code (qa, Chandra) | ≤5 KB role contracts; bounded receipted digests; docs-drift + fingerprint tests (§5, §12) |
| Infrastructure grew faster than validation (qa schema v1→v5 in 4 days; Chandra self-tooling) | Vertical slice + real smoke run at M3 before expanding; scope guard (§5, §11) |
| BLAS threads × RLIMIT_CPU killed checks and blocked promotion forever (qa `6f35327`) | Single-threaded BLAS in verifier jobs; resource kill = `unknown`, retried under a larger budget (§4.4, §7) |
| Usage limits stalled 6/10 sub-missions (Chandra) | Backoff 5/15/45 min without burning attempts; dev-mode family fallback recorded; eval arms pause (§4.12) |
| **Worth reusing:** hash-chained event log + projections + `db verify` (qa); executable admission with stored observations (Chandra); `codex_jobs` runner (question-gen); move47 bwrap worker-sandbox + canary; receipted graph-walk packs (qa `derive.py`); evidence-only transport + firewall tests (question-gen); NoGo/obstruction memory (qa); held-out acceptance of learned heuristics (move47) | Adopted in `ledger/`, `agents/`, `sandbox/`, `archive/`; heuristic-book learning is a post-M7 extension |

## 14. Decisions for the owner (my defaults in **bold**; approve or change)

- **D1 Name:** **`zkqec`** (package and CLI).
- **D2 Agent runtime:** **headless `claude -p` + `codex exec` inside our bwrap wrapper (both
  CLIs are already authenticated here; no API keys)**.
  - Models: **Opus 5.5** for task, requirements, designers, and planner; **Sonnet 5.5** for
    decoder, resource, and reporter; **gpt-6-astra (max)** for the second designer, skeptic,
    schedule, and N-version verifiers.
- **D3 Headline ZK arm:** **Zero = K0 + P-min + G-open**, reported next to **Prior =
  K0 + P-domain + all grammars**. K2 only in B6.
- **D4 First full build scope:** **M1–M5 + M7 pilot on S-A/S-B**, then M6 + S-C. The alternative is
  all of M1–M7 in one go.
- **D5 LLM budget caps:** **M3 smoke ≤ $5; M7 dev pilot ≤ $150 total; held-out run approved
  separately after the pilot.** These are placeholders for your real numbers.
- **D6 QECForge reuse:** **vendor the core into `zkqec.kernel` unchanged, with provenance.**
  `ref-code/` stays untracked.
- **D7 Branch:** **`zkqec-dev`**, merged to `main` at milestones on your request.
- **D8 Check-ins:** **autonomous through milestones, with a short report at each milestone end.**
  Stop and ask only for budget increases, scope changes, or anything irreversible.
- **D9 Results in git:** **track sealed manifests, compressed ledger exports (≤ 20 MB each), and
  reports under `results/`; keep `runs/` untracked.**

## 15. Definition of done for the first full run

1. M1–M5 acceptance tests pass, the M7 pilot has been executed, and every Codex review is triaged in
   writing.
2. `zkqec campaign run configs/campaigns/<x>.yaml` runs a real multi-agent K0 campaign end-to-end
   within budget. It produces:
   - manifest;
   - ledger;
   - archive;
   - per-fidelity fronts;
   - call records with token and cost accounting;
   - autonomy log;
   - contamination and tool-usage audit;
   - a ledger-rendered report that passes the claim linter.
3. On the S-A/S-B pilot suites, the report gives campaign-level comparisons of B1, B1e, B4,
   B5-Zero, and B5-Prior with uncertainty, including null or negative outcomes.
4. False authorization on all planted controls is zero, and every number in the report resolves to
   evidence IDs.
5. `docs/IMPLEMENTATION_STATUS.md` and `notes/PROGRESS.md` state exactly what is implemented,
   tested, validated, and still planned.
