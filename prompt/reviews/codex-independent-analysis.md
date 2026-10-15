**Independent review**

The next build should center on executable verification and evidence composition. Qecforge provides a useful scientific foundation; the earlier projects provide reusable orchestration mechanisms. None currently closes the chain from an independently proposed design to a defensible application-level result.

I reviewed qecforge’s paper sections and appendices, documentation, source, tests, configurations and schemas, and traced the relevant orchestration and admission paths in the other projects. I excluded qa’s large results/progress database. No files were changed.

Read-only verification reproduced the three reference distances and the Steane result of 26 failures in 5,000 shots. **56 tests passed, four optional-backend tests were skipped, and four file-writing tests were excluded.** The release manifest’s 132 entries and 23 demo artifact hashes matched. These checks establish component behavior and integrity, not autonomous discovery.

**1. What the mission actually demands**

The scientific target in [qecforge’s problem formulation](/data/haiyangw/claude/auto-code/ref-code/qecforge/paper/sections/02_problem.tex) is

\[
\mu=\operatorname{Tr}(Oe^{-itH}\rho_0e^{itH}),
\]

with a fixed accuracy/confidence requirement and hardware budget. The optimization variable is the implementation: encoding, algorithm, code, logical basis, operations, extraction schedule, decoder, factories and error allocation. A good parity-check matrix is one component.

**A1 must deliver autonomous implementation search from functional requirements.** Per [the autonomy definitions](/data/haiyangw/claude/auto-code/ref-code/qecforge/paper/sections/01_introduction.tex), humans may specify required operations. They should specify matters such as selected logical rotations, angle ranges, admissible inputs, precision, connectivity and resource limits—not preselect “self-dual BB code with disjoint representatives” and call the resulting parameter search A1.

The minimum credible A1 system needs:

- A frozen functional contract and declared search space spanning meaningful structural, basis or implementation choices.
- Automated proposal, construction, verification, repair and route abandonment without task-specific human edits.
- A complete implementation artifact for every claimed capability, including corrections, extraction, decoding and resource assumptions.
- Explicit outcomes distinguishing verified feasibility, refutation, unsupported capabilities and exhausted budgets.
- Held-out functional tasks, complete cost accounting, and an intervention record demonstrating no scientific assistance after freezing.

A1 does not inherently require discovering a new code family. However, replaying a known constructor is reproduction; selecting parameters within a human-prescribed family and structural recipe remains A0 under qecforge’s definitions. Report code novelty, implementation novelty and autonomy separately.

**A2 adds autonomous derivation of operational requirements from the scientific task.** Given \((H,\rho_0,O,t,\epsilon,\alpha)\) and hardware constraints, the system must construct and verify representation/compiler alternatives, identify needed logical actions, propose conditional implementation routes, and revise those routes when downstream verification fails. A reduction must transform the state and observable as well as the Hamiltonian.

Requirement inference must distinguish:

- Scientific necessities, such as accuracy and peak-space limits.
- Conditions of a selected route, such as a particular factory’s support structure.
- Preferences used to rank valid implementations.

A2 is not achieved by presenting `requirements.py::infer_seed_requirements()` as an agent. That function explicitly supplies human-written rules.

**Autonomy and evidence maturity are separate axes.** An autonomous algebraic search may earn a narrowly scoped claim before application closure. An answer-time or application advantage claim requires G3; held-out autonomy and multi-agent comparisons require G4. Agent superiority is a hypothesis, not part of the definition of autonomy.

Later capabilities include arbitrary chemistry inputs, compact mappings, lifted products, unrestricted constructor programs, several simulation algorithms and realistic AOD control. The first credible system can support a narrow native-Pauli task distribution and one explicitly declared hardware/noise model. Within that scope, its entire claimed computation must be supported.

**2. The hardest scientific and technical risks**

**Distance certification will dominate some search regimes.** In `distance.py::sector_distance()`, half-pattern storage scales as

\[
M=\sum_{w=0}^{t}\binom nw,
\]

with additional work over same-syndrome pairs. Increasing agent throughput cannot remove this combinatorial bottleneck. Search for the certificate actually needed—often \(d\ge d_{\min}\), not the exact distance. Maintain separate witness searches and lower-bound procedures. For a Z logical, check \(H_Xe=0\), \(L_Xe\ne0\), and its weight; check the other sector separately. A scalable extension needs SAT/MILP encodings, independently checked certificates where supported, and trustworthy timeout semantics. Preserve qecforge’s exhaustive solver as small-instance ground truth.

**Logical action requires signs, basis and selectivity.** Preserving stabilizer row spaces does not establish the requested operation. `structure.py::permutation_action()` handles pure CSS permutations; `structural_report()` handles restricted H/S cases. Neither is a general Clifford verifier. Add signed symplectic/tableau propagation, target-action comparison modulo stabilizers, correction-frame tracking and small dense cross-checks. Test the chain’s physical S versus logical \(S^\dagger\), changed logical bases, inactive slots and misaligned blockwise CNOTs.

**Postselection can invalidate an otherwise attractive architecture.** For an accepted arbitrary-input gate branch, the relevant ideal condition is \(C_aK_a=c_aU\). A complete instrument must satisfy \(\sum_{a,j}K_{a,j}^\dagger K_{a,j}=I\). A factory instead needs the specified joint output state on its specified preparation, followed by a verified injection procedure. Implement branch-labelled instruments, rejection flags, feedforward and retry policies. Test unequal angles, correlations and calibration exits during angle doubling. Larger gadgets require analytic/compositional certificates or controlled approximations; tiny dense simulations cannot alone certify their scaled versions.

**Code-capacity evidence cannot certify extraction or computation.** `decoding.py::code_capacity()` uses perfect syndromes. The optional memory adapter is also narrower than a logical algorithm. Required tools include noiseless measurement-action checks, both memory bases, physical-fault propagation, decoder integration and circuit-specific fault witnesses. Preserve correlated fault mechanisms and detector-free logical faults. Graphlike searches need special care: Stim documents that its shortest-graphlike-error method can ignore non-graphlike mechanisms. Such a result cannot silently become a general circuit-distance lower bound. [Stim API documentation](https://github.com/quantumlib/Stim/blob/main/doc/python_api_reference_vDev.md)

**Calibration, decoding and scheduling form a coupled system.** A fitted \(p_L(\theta)\) cannot migrate across codes, schedules or decoders merely because distances match. Decoder backlog changes waiting exposure; waiting changes reliability. Build domain-scoped calibration records and a discrete-event simulator with decoder service distributions, deadlines, factory synchronization, storage decay and retry tails. Validate serial limits and simple independent cases analytically. Synthetic timing assumptions must remain conditional inputs.

**Adaptive search creates statistical selection bias.** Use independent final confirmation, including for calibration fits selected during search. Allocate confidence across calibration, validation and observable sampling. With zero failures in \(N\) fixed IID trials, the two-sided Clopper–Pearson upper endpoint is \(1-(\alpha/2)^{1/N}\), not zero. At \(N=10{,}000,\alpha=.05\), it is about \(3.69\times10^{-4}\). An adaptive sampling controller needs a validated confidence-sequence method or a frozen sampling plan. Agent comparisons use task/campaign replicates, with unsuccessful campaigns retained. [Howard et al., confidence sequences](https://arxiv.org/abs/1810.08240)

**Known-answer leakage can masquerade as requirement discovery.** Freeze source bytes, retrieval policy, prompts and reusable memory. Separate paper-assisted reconstruction from discovery. Generate held-out workloads and hardware changes that alter useful routes, rather than merely relabeling a familiar answer. Record source exposure and every intervention. Retrieval exclusion cannot certify absence from pretraining; the defensible claim concerns observable intervention and demonstrated transfer.

**Proxy optimization and novelty require separate controls.** A high rate, low support overlap or favorable LLM ranking must not authorize feasibility. Promote candidates through progressively stronger evaluators while retaining structural diversity. Maintain two identities: mathematical-code identity and complete implementation identity. Canonical row-space checks, permutation witnesses and local-Clifford equivalence procedures can identify rediscoveries; unresolved equivalence remains unknown. Equivalent codes can have different costs under fixed hardware embeddings, so equivalence must not automatically discard implementation alternatives.

**3. Weaknesses and reusable mechanisms in the earlier workflows**

**qa / ChandraQ**

The strongest reusable components are transactional event admission in [chandraq/db.py](/data/haiyangw/claude/auto-code/ref-code/qa/ChandraQ/chandraq/db.py:405), idempotent job generation and leases in `jobs.py`, proof-byte-bound reviews, explicit handoffs, and event-derived run outcomes. The distinction between research events and operational queue state is useful.

Its admission semantics are insufficient for QEC:

- [`gate.py::_check_solid()`](/data/haiyangw/claude/auto-code/ref-code/qa/ChandraQ/chandraq/gate.py:628) permits promotion with passing model reviews and **zero attached executable checks**. Cross-family review is useful scrutiny, not a distance or channel certificate.
- `dah.py::_is_root()` admits quote-verified literature statements into the established set. Quote matching checks textual occurrence; it does not mechanically establish that the submitted statement follows from the quote.
- `actor_role()` and reviewer/model identifiers are caller-provided labels. [runner.py::build_argv()](/data/haiyangw/claude/auto-code/ref-code/qa/ChandraQ/chandraq/runner.py:282) launches broadly privileged workers. Environment variables and instructions are not authority isolation.
- There is a concrete timeout/refutation defect: `failed_checks()` excludes a resource-limit failure only after a later identical command passes. Before that, `_check_edge_refuted()` accepts the timeout as refuting evidence. An in-memory probe reproduced refutation from exit code 124.
- The three-failure rule is a useful spending heuristic, but it must distinguish malformed proposals, infrastructure failures and genuine scientific counterexamples.

Reuse its transactional machinery and provenance patterns; replace its scientific truth rules and execution boundary.

**Chandra**

[validator.ts](/data/haiyangw/claude/auto-code/ref-code/Chandra/orchestrator/src/validator.ts) provides useful submission freezing, claim/row binding, evidence-path containment, manifest auditing and retained review records. Packet scheduling and compact observer summaries can reduce orchestration overhead.

The production path has a major gap: `adjudicateCandidate()` exists and is tested, but the normal mission loop does not call it automatically. Workers are instructed to append through ledger CLIs. In [`admission.py::_check_result_state()`](/data/haiyangw/claude/auto-code/ref-code/Chandra/_common/ledgers/admission.py:325), a checked result can be accepted using an existing hashed file plus a claimed passing verdict; executable verification is optional. Artifact existence does not establish its scientific content.

Isolation also depends on runner configuration; `orchestrator/README.md` explicitly acknowledges configurations where validator isolation reduces to working directory and prompt. Automatic skill harvesting changes the procedures available during a mission, which is incompatible with a frozen evaluation unless confined to a separate development phase.

Progress counters can reward additional classified results without measuring closure of the mission’s critical obligations. Use certificate-backed frontier advancement instead.

The historical `notes/knowledgebase_redesign.md` identifies useful concerns, but several cited defects have since received fixes, including locking and validation binding. Its diagnoses should not be copied as current findings without checking the source.

**question-gen**

[codex_jobs.py](/data/haiyangw/claude/auto-code/ref-code/question-gen/src/question_gen/codex_jobs.py) offers strong reusable engineering: semantic job fingerprints, attempt-specific files, atomic publication, recovery of interrupted sessions and local output validation. Fresh versus persistent probes, candidate lineage and blinded randomized comparisons also transfer well as experimental mechanisms.

Its scientific objective differs fundamentally. It develops and selects interesting questions, sometimes changing the central question and removing solved questions. QEC discovery must preserve the scientific task and seek valid implementations.

In [`question_rounds.py::run_verification_stage()`](/data/haiyangw/claude/auto-code/ref-code/question-gen/src/question_gen/pipelines/question_rounds.py:2973), scientific acceptance ultimately rests on a model’s `verified_complete_solution` Boolean. Local validation checks consistency and nonempty explanation, not the proof. The three-judge pairwise gate measures taste, not mathematical validity.

The runner also kills the immediate process on timeout rather than an explicitly managed process group, and `max_attempts` applies per invocation. Reuse requires descendant cancellation and campaign-wide budget enforcement. Fresh context and multiple judgments should remain proposal-quality mechanisms, never certificate issuers.

**4. Recommended modular architecture**

Use a deterministic Python control plane with typed artifacts and isolated execution services. LLMs propose designs and experiments; the control plane owns state transitions, budgets and authorization.

| Module | Responsibilities and artifacts | Authority |
|---|---|---|
| Campaign registry | `TaskContract`, hardware/noise manifest, search grammar, corpus, evaluation protocol, intervention log | Freeze campaign identity; reject drift |
| Task/compiler service | Encoding maps, transformed \((H,\rho_0,O)\), workload IR, error obligations | Certify supported transformations |
| Proposal services | Requirements, code/basis candidates, instruments, schedules, decoder configurations | Submit proposals and experiment requests |
| Verification services | Algebra, distance, signed action, instruments, circuits, decoding | Emit checked, scoped evidence |
| Statistical/resource service | Calibration bounds, confidence allocation, queues, observable bias, resources | Derive conditional feasibility |
| Evidence resolver | Claim predicates, dependencies, revocations, domain bridges | Authorize only supported claims |
| Experiment/report service | Held-out evaluation, ablations, audit manifests | Publish reproducible results and limitations |

The initial agent roles can be task/requirement analyst, literature analyst, code/basis designer, operation designer, hardware/decoder planner and skeptic. Resource arithmetic belongs in code; an agent may identify missing inputs or propose experiments. Roles need not correspond one-to-one with model instances.

**Contracts.** Replace free-form scientific claims at critical boundaries with typed predicates such as `distance_lower_bound(code, 5)`, `implements(operation, target, basis)` and `failure_upper_bound(event, domain, value, confidence)`. Each evidence receipt identifies inputs, verifier/version, method, completion status, output artifact, units and statistical design.

An `ExperimentRequest` should specify the proposition to resolve, necessary artifacts, maximum cost, permitted method and promotion criterion. A failed launch, timeout, unsupported operation and mathematical counterexample must be different outcomes.

**Persistence.** Start with SQLite WAL, transactional event append and a content-addressed artifact store. A single admission service owns writes. Maintain explicit job leases, idempotency keys and budget reservations. Persist results incrementally; reconstruct projections from events after crashes. Use immutable candidate versions rather than editing matrices, circuits or reports in place.

Separate operational job status from scientific status. A completed job may establish only “unknown.” A failed experiment can produce a useful counterexample. Neither should be inferred from process exit alone.

**Domain handling.** Compute claim-specific dependency domains. A decoder change invalidates performance descendants but need not invalidate CSS commutation. A basis change may preserve distance while invalidating logical-operation evidence. Cross-domain reuse requires an explicit checked bridge. Add supersession and revocation; retaining an old verified record must not keep a corrected claim authorized.

**Independence.** Proposers receive read-only inputs and writable scratch space. They cannot access verifier implementation files, admission credentials or held-out confirmation data. Reviewers receive the frozen candidate and necessary assumptions, without the proposer’s persuasive transcript. Runtime identities bind receipts to actual executions; agents cannot select their own issuer identity.

Codex/Claude subagents may write constructor programs, adapters or exploratory checks in isolated workspaces. During a frozen evaluation, their code is untrusted candidate material. A new shared verifier requires development, independent admission and a new campaign version. Multiple model families may improve skepticism, but independent computational checks supply scientific authority.

**Orchestration and budgets.** Schedule a DAG of unresolved obligations, prioritizing failures that block feasibility. Do not spend extensive decoding samples on a candidate lacking a valid required gate. Reserve final-confirmation resources before exploration. Charge tokens, calls, retries, solver time, memory, samples, invalid proposals and duplicates under one declared policy.

Stop on budget exhaustion, an authenticated integrity failure, completion of the preregistered objective, or a defined lack of certificate-backed progress. Abandon individual routes using scoped counterexamples. Report “no verified solution within budget” unless infeasibility itself has a certificate. Human scientific rescue terminates the uninterrupted-autonomy claim for that campaign.

**5. Staged build plan and acceptance tests**

| Stage | Reuse or build | Acceptance |
|---|---|---|
| **0: Harden the foundation** | Reuse qecforge algebra, constructors, bounded distance, scheduling, statistics and fixtures. Fix numerical issues below. Build typed receipts and admission ownership. | Reproduce existing results; reject forged issuer/status fields, malformed schemas, interrupted certificates and changed artifacts. Crash/restart must neither duplicate admission nor restore spent budget. |
| **1: G0 closure in a narrow domain** | Reuse `gf2.py`, `codes.py`, `distance.py`, `workload.py`; add signed logical-action verification and explicit task/implementation contracts. | Independent brute-force checks for small distances; basis-change and permutation tests; S/S† negatives; Hamiltonian/state/observable equivalence; reject invalid sector reductions and torus-collision assumptions. |
| **2: G1–G2 for one complete route** | Validate optional backends; implement the selected gate/factory route, extraction circuit, decoder and calibration records. Retain dense tools as independent small references. | Both memory bases; ideal branch completeness; unequal-angle and correlated-output checks; physical-fault witnesses; decoder correction validity; disjoint calibration/validation points; explicit unavailable outcomes. |
| **3: G3 task closure** | Extend the compiler through preparation/readout and add queue-aware resources. Keep `answer_resources()` as a serial regression baseline. | Compare small-task observables against exact evolution. Close the observable error/confidence budget. Recover analytic serial limits; detect unstable queues, nonexistent heralds, excessive space and out-of-domain calibrations. |
| **4: A1 evaluation** | Integrate bounded proposal agents through the new service boundary. Preserve replay/random controls. | Freeze functional tasks and tools; run without scientific intervention; independently confirm selected designs. Compare expert/reference, non-LLM, human-requirement, single-agent and multi-agent arms at matched budgets. |
| **5: A2 evaluation** | Add source-grounded requirement inference and upstream route revision. | Held-out changes to boundaries, sectors, observables, coefficients and hardware must produce justified requirement changes. Require alternatives and falsifiers. Run requirement, family/basis, uncertainty, skeptic and evidence-domain ablations. |

Do not rewrite functioning small-instance mathematics to fit a new agent framework. Rewrite the sequential `Coordinator` as orchestration services, while preserving it as a regression baseline.

Do not initially implement every proposed code family or algorithm. Open a search dimension only when its evaluator exists. If the first route is the reference parallel TMR architecture, faithfully implementing its full protocol is mandatory; other validated routes can establish earlier end-to-end controls.

G4 must freeze the full study after pilot variance/cost estimation. The proposed ten seeds and 10,000 shots in `configs/experiment_plan.json` are pilot defaults, not statistical justification. A negative multi-agent result can still demonstrate autonomy; it simply fails the superiority hypothesis.

**6. Top 15 “must not happen” failure modes**

| # | Failure | Detection mechanism |
|---:|---|---|
| 1 | The optimizer changes the observable, sector or target accuracy. | Frozen task hash plus checked transformation of the complete task. |
| 2 | A familiar or equivalent code is reported as newly discovered. | Source-exposure records, equivalence witnesses and separate reproduction/novelty labels. |
| 3 | Invalid CSS algebra or incomplete logical bases enter evaluation. | GF(2) commutation, rank, kernel and full pairing checks. |
| 4 | A timeout becomes a distance lower bound or refutation. | Typed termination status; completed proof or checked witness required. |
| 5 | A logical phase or basis mismatch passes as the intended gate. | Signed action checks and independent small dense tests. |
| 6 | Disjoint supports certify parallel injection. | Complete instrument checks, including the six-qubit negative control. |
| 7 | Code-capacity or memory results become algorithm reliability. | Evidence-scope compatibility enforced by the resolver. |
| 8 | Correlated detector mechanisms are split into independent faults. | Mechanism-preserving conversion tests and direct fault-sampling comparisons. |
| 9 | Undetected logical errors are counted as retryable rejection. | Every retry must reference an actual observable branch flag. |
| 10 | Marginal factory successes are multiplied without justification. | Joint measurements, factorization certificates or conservative bounds. |
| 11 | Old calibration survives a changed schedule, decoder or angle range. | Domain matching and dependency invalidation tests. |
| 12 | Zero observed failures or favorable stopping produces false certainty. | Denominators, confidence allocation and audited stopping rules. |
| 13 | A proxy winner bypasses task feasibility. | Separate screening and authorized frontiers; mandatory G3 closure. |
| 14 | A proposer edits its evaluator or issues its own certificate. | Process permissions, pinned verifier images and authenticated receipts. |
| 15 | Retries disappear from cost accounting or become independent replicates. | Durable attempt lineage, reservations and campaign-level analysis identifiers. |

**7. Problems, omissions and likely misreadings in qecforge itself**

**The numerical postselection checker needs correction.** In [`gadgets.py::analyze_postselected_map()`](/data/haiyangw/claude/auto-code/ref-code/qecforge/src/qecforge/gadgets.py:64), `np.allclose(..., atol=tolerance)` retains NumPy’s default relative tolerance. With

\[
U=\operatorname{diag}\!\left(\sqrt{1+10^{-6}},\sqrt{1-10^{-6}}\right),\qquad K=U/2,
\]

the checker accepts the nonunitary target at declared tolerance \(10^{-10}\), returning `target_up_to_scalar=True`. I reproduced this.

There is also a conditioning problem: absolute residual alone is insufficient for accepted-gate accuracy. For \(K=2\times10^{-10}\operatorname{diag}(1,e^{i})\) and target \(I\), both success Booleans return true, although the conditional gate has a one-radian relative phase. Use explicit relative/absolute tolerances and an acceptance-aware error contract. Preserve unnormalized instrument error separately from conditional output error.

**The executable task schema freezes an implementation choice.** [`schemas.py::TaskSpec`](/data/haiyangw/claude/auto-code/ref-code/qecforge/src/qecforge/schemas.py:16) puts `trotter_steps` inside the frozen task. The scientific formulation correctly treats discretization as an optimization choice. Move algorithm and step count into candidate artifacts. Add units, state/observable artifacts, sector/boundary provenance and hardware budgets before claiming the schema represents the complete mission.

**The ledger is a dependency-status helper, not a scientific admission engine.** [`EvidenceLedger.add()`](/data/haiyangw/claude/auto-code/ref-code/qecforge/src/qecforge/evidence.py:20) accepts caller-labelled verified records without checking their method or requiring a certificate artifact. Domains are opaque strings; all parent domains must match. It lacks authenticated issuers, typed inference rules, revocation and dependency-local domain projection. Documentation acknowledges the missing resolver, but builders may overread the tested ledger as that resolver.

**The coordinator still conflates outcomes.** [`Coordinator.run()`](/data/haiyangw/claude/auto-code/ref-code/qecforge/src/qecforge/agents.py:72) uses `infeasible_or_unverified` for both demonstrated constraint failures and insufficient distance bounds. Production logic must split them. Its trace is written at completion, so a crash can lose campaign history; this is unsuitable for long autonomous runs.

**The demo does not close even its supplied conservative algorithmic error budget.** Its operator bound is \(0.07\). For its unit-norm observable, the documented conversion gives \(0.14\), exceeding target precision \(0.1\) before noise or sampling. This demonstrates insufficient certification under that bound, not actual task infeasibility. Keeping `scientific_claim_ready=False` is correct.

**The roadmap should distinguish route requirements from universal prerequisites.** Compact encoding and full reference TMR are essential when claiming those routes. They are not logically necessary for every scoped A1/A2 demonstration. Establishing one complete, modest route can expose orchestration and error-composition failures earlier.

Finally, the reference archive is identified by hashes but is not bundled. I cannot establish faithful implementation of its full protocol from this repository; that implementation is explicitly absent. Source acquisition, version binding and independent transcription checks belong in the build plan before reproducing its operation or circuit claims.