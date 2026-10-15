# Technical contracts (binding appendix to `00-master-prompt.md`)

> **Status:** DRAFT v0.5. Every rule here is normative.
>
> **Phase tags** say when each contract must exist:
> - **P1a**: first approved build (M0–M1);
> - **P1b**: M2–M4;
> - **P2**: circuit level;
> - **P3**: application.
>
> **Conflicts.** If code or docs disagree with this file, the code is wrong, or this file must first be
> revised. A revision means a new prompt version, a gated commit, and an entry in `notes/DECISIONS.md`.

---

## A. Typed propositions, receipts, entailment, outcomes (P1a)

### A1. Propositions

`Proposition(subject_ref, predicate, args)` uses a predicate from a closed, versioned **signature
registry**. Each signature fixes four things:
- the subject type;
- the argument types and units;
- the bound direction (it is implied by the predicate and never a free field);
- the allowed guarantee kinds.

A record whose fields are inconsistent with its signature is rejected at admission.

**Subject types.** Every predicate is keyed to the object that actually carries the property.

| Subject type | Identity |
|---|---|
| `presentation` | exact `(H_X, H_Z)` matrices, by hash |
| `code` | stabilizer group, given by the RREF of the row spaces |
| `basis` | logical representatives, tied to a code |
| `operation` | physical circuit artifact, target action, basis, phase convention |
| `candidate` | `DesignCandidate` |
| `task` | the task contract |
| `campaign` | one campaign run |
| `arm` | one comparison arm |

**Phase-1 signatures:**

| Predicate | Subject and arguments |
|---|---|
| `css_valid` | presentation |
| `block_length_eq` | presentation, n |
| `dimension_eq` | code, k |
| `distance_lower_bound` | code, sector ∈ {X, Z, both}, d |
| `distance_upper_bound` | code, sector, d |
| `distance_exact` | code, sector, d (derived only) |
| `max_check_weight_le` | presentation, w |
| `max_qubit_degree_le` | presentation, q |
| `implements` | operation, target_action, basis, phase_convention |
| `same_stabilizer_group` | code_a, code_b |
| `equivalent_under` | code_a, code_b, relation; witness required |
| `batch_count_le` | presentation + basis, b; witness = coloring |
| `target_refuted` | candidate, task-predicate; derived from a verified bound in the opposite direction |
| `nogo` | route, scope, certificate_ref |
| `satisfies_task` | candidate, task (derived only) |
| `arm_contrast` | estimand, value, interval, design (statistical; §H) |

### A2. Receipts

A receipt carries the following, and stores **no** free-form status field from the payload:
- issuer: registered verifier name and version, taken from the orchestrator's execution record;
- execution id;
- input hashes;
- method;
- completion: `completed`, `budget_exhausted`, `timeout`, `resource_killed`, `crashed`, or
  `unsupported`;
- witnesses and artifacts;
- proposition status: `verified`, `refuted`, or `unknown`.

Only a `completed` receipt from a registered issuer can carry `verified` or `refuted`.

### A3. Resolver

The resolver derives **current authority** only from receipts that are currently authorized. It
supports supersession and **revocation**.

**Revocation.** When a receipt is revoked, its descendants become `unknown`. A historical `verified`
never survives revocation as current authority.

**Contradictions.** Two kinds of contradiction quarantine their premises:
- a direct one: verified X and refuted X coexist;
- a **semantic** one: currently authorized records are jointly unsatisfiable across bounds,
  equalities and sectors. Example: `distance_lower_bound(c, Z, 5)` together with
  `distance_upper_bound(c, Z, 3)`. Another: a `both` bound inconsistent with the sector bounds.

The resolver checks the interval consistency of every (subject, quantity) pair at each admission.
On a conflict, every conflicting premise is **quarantined**, their descendants are invalidated, an
integrity incident is opened, and neither `satisfies_task` nor `target_refuted` can be derived from
them.

**Guarantee propagation.**
- A derivation inherits the weakest guarantee among its premises, in the order
  exact > numerical(tol) > statistical(α).
- Statistical alphas combine by an explicit union bound.

**Registered inference rules** (examples):
- a lower bound of d entails every lower bound of d′ ≤ d;
- a lower bound and an upper bound of equal value derive `distance_exact`;
- sector bounds combine into `both` by taking the minimum;
- an upper bound below `d_min` derives `target_refuted`;
- `satisfies_task` is derived from all predicates the task demands.

A witness never entails a bound in the opposite direction. `solver_claim`, `agent_assertion` and
`assumption` never authorize anything.

**Transport across identities** uses explicit bridges per predicate.

| Predicate kind | Transport rule |
|---|---|
| code-level (`dimension_eq`, distance bounds) | transfers between presentations with `same_stabilizer_group`. Transfers under a permutation witness by relabelling. Transfers under an X/Z swap by exchanging sector labels |
| presentation-level (`max_check_weight_le`, `max_qubit_degree_le`, `css_valid`, `batch_count_le`) | never transfers |
| operation-level (`implements`) | transfers only through a verified basis-change or permutation conjugation |

### A4. Outcome types

The following are distinct typed outcomes, never inferred from an exit code:
- operational job status;
- `launch_failure`;
- `timeout`;
- `resource_killed`;
- `unsupported`;
- `malformed_proposal`;
- scientific `counterexample`.

A completed job may establish only `unknown`.

### A5. Reports

The **authoritative report** is generated entirely from templates over propositions and receipts.

LLM-written commentary may only go in a separate appendix headed "Unverified commentary — not
evidence". That appendix is excluded from every claim count, and the report linter refuses to pass a
report that has any free text outside it.

## B. Design objects and proposal semantics (P1a core)

### B1. DesignCandidate

`DesignCandidate` is an immutable record. It holds component refs for `E, A, C, L, G, P, D, F, η`, and
each ref is an artifact hash, `absent` or `unsupported`.

In Phase 1, the active refs are:
- `C`: a presentation ref;
- `L`: a basis ref;
- `G`: a list of **OperationRef** entries.

An `OperationRef` holds:
- the target logical action;
- the physical circuit artifact, drawn from the task's allowed operation class (§F);
- the basis ref;
- the phase convention.

Every other ref is `absent` or `unsupported`, and any claim that needs one stays blocked.

### B2. Edits and invalidation

Proposals are **component edits** on a parent candidate. Invalidation is dependency-local:

| Edit | Invalidates | Keeps |
|---|---|---|
| Code (presentation) | basis, operation, presentation predicates, batching | Code-level predicates, only if `same_stabilizer_group` is verified |
| Basis | operations, batching | Distance |
| Operation | that operation only | — |
| [P2] Schedule | circuit and timing evidence | — |
| [P2] Decoder | performance evidence | — |

### B3. Field classes

Proposal fields fall into four disjoint classes:

| Class | Content | Status |
|---|---|---|
| (a) Construction parameters | For `explicit_sparse`: an explicit `shape: (rows, cols)` per matrix, with a common `cols`, plus sorted unique `(row, col)` pairs over GF(2). Duplicates and out-of-range entries are rejected. Isolated qubits are allowed and counted | input |
| (b) Requested predicates | what the candidate is aimed at | non-binding |
| (c) Untrusted witnesses | logical vectors, permutations, colorings, circuits | rechecked |
| (d) Verifier-owned observations | n, k, distance bounds, weights, actions, error rates | **rejected if supplied** |

Byte, nesting and list-length caps are applied before parsing, and dimension caps before allocation.

### B4. Scope

**Supported:** binary CSS codes with +1 signs.

**Grammar capability manifests** declare the exact algebra of each family:
- `explicit_sparse`;
- `two_block_group_algebra` over abelian `Z_l × Z_m`, with `H_X=[A|B]`, `H_Z=[Bᵀ|Aᵀ]`, and monomial
  wrap-cancellation over GF(2);
- `program` (P1b).

**Unsupported:** non-CSS codes, subsystem codes, and anything outside these families.

## C. Distance certification (P1a)

**C1. Preflight.** Before any enumeration, compute and check:
- `M = Σ_{j≤t} C(n,j)`;
- memory estimates;
- estimates of the bucket-pair work `Σ_s M_s²`.

Results:
- Exceeding any budget gives `budget_exhausted`, which is `unknown`.
- M1 publishes **measured feasibility tables** for this host and implementation, and D10 target ranges
  are set from them.
- Reference sizes: `M(100,4) = 4,087,976`; `M(144,6) = 11,642,049,421`.

**C2. Lower bounds.** A completed enumeration at half-weight t, in one sector, requires *all* of the
following:
1. a complete canonical logical basis for that sector;
2. every half-error of weight ≤ t enumerated;
3. every same-syndrome pair examined, including pairs across shards when the run is parallel;
4. the minimum weight `w*` of any nontrivial logical XOR found;
5. a stored record of `t`, the counts, the completion status and `w*`.

Conclusions for that sector:
- If `w* ≤ 2t`, then `d_sector = w*`, **exactly** (completeness gives the lower bound, and the witness
  gives the upper bound).
- If no nontrivial logical was found through `2t`, then `d_sector ≥ 2t + 1`.

Example: Steane at `t = 2` is complete, finds `w* = 3`, and certifies `d = 3`, **not** `d ≥ 5`.

Further rules:
- **Two-sector bound.** `both` is the minimum over the two sectors.
- **Symmetry reduction** requires both a written proof and agreement with unreduced enumeration on
  every instance where both can run.
- **SAT/ILP lower bounds** require two things: a validated encoding, tested against exhaustive search;
  and an UNSAT proof checked by an independent checker (DRAT/LRAT). Without both, the result is only a
  `solver_claim` (P1b, optional).

**C3. Upper bounds.**
- A logical of weight w, re-verified in its sector (zero syndrome, nonzero logical signature), proves
  `d_sector ≤ w`.
- Information-set decoding (ISD) search yields witnesses only. Failing to find one proves nothing.

**C4. Admission.**
- `d ≥ d_min` is admitted only when the certified lower bound is at least `d_min`.
- Certify the predicate that is needed.
- Report intervals as intervals.

**C5. Fixtures.**
- Source: Bravyi et al. 2024, arXiv:2308.07915, Table 3.
- Contents: torus dims, polynomials, coordinate convention, matrix hashes, witness artifacts.
- Acceptance is deterministic witness validation. Discovery speed is benchmarked separately.
- Fixtures are trusted test data, exempt from the D10 caps. That includes `[[288,12,18]]`.

## D. Code identity, equivalence, novelty (P1a keys; P1b DB labels)

**D1. Check-presentation key.** The canonical colored Tanner graph of the *given* generators
(pynauty). It deduplicates exact presentations up to qubit and check permutation. It is
generator-dependent.

**D2. Relations**, each with a checked witness:
- same stabilizer group (RREF equality);
- qubit permutation (permute, then compare RREF);
- X/Z swap;
- logical-basis change;
- local Clifford (later).

**D2a. True invariants** of the stabilizer group under the declared relation. Examples:
- n and k;
- certified distance intervals;
- the stabilizer-group weight enumerator, when computed completely.

A difference in a true invariant certifies non-equivalence.

**D2b. Presentation features.** Examples:
- generator weight distributions;
- Tanner-graph WL hashes.

These only **prioritize** comparisons. They never exclude a pair and never certify a non-match.

**D3. Two identities.**
- *Mathematical-code identity* is used for novelty and for code-level evidence transport (§A3).
- *Implementation identity* is the full candidate.

Equivalence never discards implementation variants. Proxy dominance only changes scheduling priority.
Hard pruning needs a hard-constraint violation or a domain-compatible dominance certificate.

**D4. Novelty labels against known-DB vX** (P1b):
- `database_match(entry, relation, witness)`;
- `certified_nonmatch_under_R(coverage)`: this requires certification against **every** DB entry with
  matching `(n, k)` and a compatible distance interval;
- `audit_inconclusive`.

Other reporting rules:
- "Reproduction" is reserved for arms with provenance-supported exposure to the source.
- Retrieval violations are reported separately.
- Pretrained contamination is declared unidentifiable.
- Code novelty, implementation novelty and autonomy are separate axes.

**D5. DB.**
- Entries are recomputed from matrices by our verifiers.
- Coverage is recorded: families, (n, k) ranges, sources.
- The DB is built by a separate librarian process (P1b; its runtime and exposure profile are declared
  separately, §J6).

## E. Theorem registry (P1a)

| Theorem | Preconditions / statement | Permitted use |
|---|---|---|
| Quantum Singleton | Any stabilizer code: `n − k ≥ 2(d − 1)`. | The tuple is **excluded if** `n_max − k_min < 2(d_min − 1)`. Passing proves nothing about existence. |
| Quantum Hamming (counting) | Nondegenerate binary stabilizer codes: `Σ_{j=0}^{⌊(d−1)/2⌋} 3^j C(n,j) ≤ 2^{n−k}`. | Excludes only *nondegenerate* codes. Applies only when the task demands nondegeneracy. Passing proves nothing. |
| LP / shadow | Stabilizer codes. | Infeasibility needs a checked dual certificate (later). |
| Disjoint representatives | A full set of k **P-type** representatives (P ∈ {X, Z}) with pairwise disjoint supports gives `n ≥ Σ wt ≥ k·d_P`, where `d_P` is the minimum weight of a nontrivial P-type logical. | Route-specific; never task impossibility. |
| Batching | b support-disjoint batches of P-type representatives: `b ≥ ⌈Σ wt / n⌉`. | DSATUR gives a *feasible* coloring, not an optimal one. |
| Eastin–Knill | Fixed code that detects errors; transversal gate set. | Excludes only a universal transversal set. Never rejects injection, code switching or measurement. |

A route-specific obstruction becomes a scoped `nogo` with a completeness certificate. It never becomes
task infeasibility.

## F. Logical-action verification (L3; P1a for permutations and transversal single-qubit Cliffords; P1b folds)

**Witness checks vs existence claims.**
- Checking a supplied witness is a witness check.
- A "none exists" claim needs an exhaustive or proof-checked search; otherwise it is `unknown`.

**Allowed operation class.** The task's hardware domain declares which physical operations are
available and what they cost. This is a **disclosed task input**, not a route. Examples:
- qubit permutations;
- transversal single-qubit Cliffords;
- [P1b] pairwise fold circuits.

**Signed action check.** For a proposed physical operation:
1. Verify that the stabilizer group is preserved, **including signs**.
2. Compute the induced action on all **2k** logical Pauli generators, with phases, modulo stabilizers,
   using a signed symplectic tableau.
3. Compare the result with the target in the declared basis and phase convention.

Negative controls:
- physical S inducing logical S† on the m=7 chain;
- a wrong permutation;
- an incomplete basis;
- a mismatched phase convention.

**Folds.** Each fold is an explicit circuit: a pairing plus a gate assignment, with a target action.

**Charges.** Compensating Cliffords and basis changes are charged.

**Kernel scope.** The kernel's `permutation_action` and `structural_report` keep their restricted
scope labels.

**Twins (N-version).** Independent second implementations exist for:
- rank and k;
- small-n distance;
- witness validation (logical vectors, permutations);
- signed action.

They are written by Codex from the spec only, in a sandbox without access to the first implementation,
**during M2, before any evaluation campaign**. Further rules:
- No shared scientific helpers.
- Cross-checks cover exhaustive truth on tractable cases, targeted mutations, and declared
  distributions, with conventions aligned explicitly.
- Twins are frozen once the cross-checks pass.
- A disagreement quarantines the result.
- M1 results are labelled `single-implementation, non-evaluation`.

**Confirmation-method/range matrix.** Published in M2. For each predicate it gives the confirmation
method and the size range in which that method applies:
- witness checks are cheap at any size and are confirmed by a twin;
- complete lower bounds are confirmed by a twin only up to its range;
- beyond that range, results are labelled `single-implementation lower bound`, with the completed-range
  record re-executed from the sealed bundle.

## G. Circuit level (P2; specified now, built later)

**Estimand.** Freeze all of the following:
- R;
- the active observables;
- initialization and readout;
- the noise model;
- decoder and settings;
- the failure event.

Measure per-shot block and observable failure. Any per-round conversion needs a validated model. Do
not apply binomial intervals to pooled correlated rounds or bits. Count every simultaneous qubit.

**Semantics.** Check:
- detector and observable counts;
- signed logical flows;
- the measurement action;
- boundaries;
- directed fault propagation, against an independent small simulator.

Both bases use the declared schedule, or else form separate domains. A negative schedule control must
be *demonstrably invalid*.

**Decoding.**
- Sample the original circuit.
- Check `H_D ê = s`.
- Score `L_D ê` against the actual flips.
- Keep detector-free logical faults.
- Record decomposition and correlation flags.
- Decode exhaustively on tiny cases.
- Stim is used only within its supported semantics.

**Circuit distance.** A fault set f is a witness for `d_circ ≤ |f|` only if all of the following hold:
- f is physically compatible, under the declared fault alphabet and counting convention, when replayed
  in the original circuit;
- `D f = 0` (no detector fires);
- `L f ≠ 0` (a logical observable flips).

A witness found by graphlike search is valid once it has been replayed this way. Lower bounds need
exhaustive or proof-checked exclusion. Keep this quantity distinct from the minimum number of faults
that makes the decoder fail.

## H. Statistics, confirmation, and analysis (P1b for arm comparisons; P2/P3 for physical sampling)

**Phase 1 sampling.** Phase 1 has **no physical-noise sampling**. It does sample tasks, seeds and
stochastic agents, so its arm comparisons are statistical claims, and the following are frozen before
held-out generation:
- suite and task weights;
- the estimands:
  - per-task verified-feasible indicator;
  - paired arm differences;
  - time and cost to first verified result, treated as **censored** at the budget, analysed with
    Kaplan–Meier or restricted-mean methods;
- task-clustered inference, paired by task and seed;
- multiplicity correction: Holm across the declared contrasts;
- **fixed-proposal** and **fixed-budget** comparisons, run as separate, declared analyses;
- the cost metric: wall time, monetary-equivalent USD, and verifier CPU, each reported separately and
  never summed.

**Confirmation.**
- Confirmation families, budgets, alpha, retry policy (none) and acceptance rule are frozen before any
  confirmation data is observed.
- Confirmation capacity is reserved at campaign start.
- A failed confirmation is reported as failed.
- In Phase 1, confirmation follows the §F confirmation-method/range matrix.

**Physical sampling (P2/P3).**
- Fixed-shot two-sided CP intervals.
- With zero failures, the upper endpoint is `1 − (α/2)^{1/N}`, which is ≈ 3.69·10⁻⁴ at N = 10⁴ and
  α = 0.05.
- Adaptive sampling only with a validated confidence sequence.
- Coupling across circuits must be explicit.

**Outcome rule.** "No verified solution within budget" is the outcome unless infeasibility is
certified.

## I. Controls (P1a subset; full P1 set in M2; extended per phase)

**Specification.** Each control specifies:
1. the target gate;
2. a **production-path** injection;
3. the expected semantic outcome;
4. a **paired positive** that must be authorized.

Report `k/N` false authorizations and `j/M` false rejections.

**P1 controls:**
- real-field rank;
- the ℓ = 2 chain declared to have d = 6;
- incomplete basis;
- S vs S†;
- wrong automorphism;
- torus collision;
- timeout presented as a bound;
- `solver_claim` presented as proof;
- a witness used as a lower bound;
- the Steane `t = 2` "d ≥ 5" misreading;
- presentation-level weights transported across equivalent presentations;
- proposer-supplied verifier-owned fields;
- spoofed issuer;
- tampered or stale artifact;
- source text requesting evaluator changes;
- revoked premise still used;
- semantically conflicting bounds (lower 5 with upper 3 on the same sector) must quarantine, and
  neither `satisfies_task` nor `target_refuted` may be authorized (P1a control).

**Sandbox, broker and accounting tests** (§J, §K):
- vault read;
- egress;
- DB write;
- tool declaration in a request;
- signal to an outside process;
- inherited secret.

**P2/P3 controls:**
- invalid interleaved schedule;
- non-graphlike matching;
- split hyperedges;
- zero acceptance;
- multiplied factory marginals;
- unheralded retry;
- stale calibration;
- noisy data labelled exact;
- code capacity promoted to algorithm reliability;
- the six-qubit instrument counterexample.

**Mapping of the 15 must-not-happen modes** (`reviews/codex-independent-analysis.md` §6):

| # | Mode | Phase | Injection | Expected outcome | Paired positive |
|---|---|---|---|---|---|
| 1 | Task mutated (observable, sector, accuracy) | P1a (task hash) / P3 (transform) | edit the task file mid-campaign; a proposal that alters the target | abort with integrity record / rejected | unchanged task proceeds |
| 2 | Familiar code reported as new | P1b | Zero-arm proposal of a DB code | `database_match` | verified DB-absent code → `certified_nonmatch` or `inconclusive` |
| 3 | Invalid CSS or incomplete basis | P1a | non-commuting presentation; truncated basis | refuted / rejected | valid presentation admitted |
| 4 | Timeout as bound or refutation | P1a | killed enumeration | `unknown` | completed enumeration authorizes |
| 5 | Phase or basis mismatch | P1a | S vs S† target | refuted | correct S† target verified |
| 6 | Disjoint supports certify injection | P3 | six-qubit counterexample | not authorized | full-instrument positive (P3) |
| 7 | Memory result promoted to algorithm reliability | P2/P3 | code-capacity receipt cited for a task claim | resolver blocks | in-scope claim allowed |
| 8 | Correlated mechanisms split | P2 | DEM with hyperedges | preserved / refused | graphlike DEM accepted |
| 9 | Undetected error counted as a retry | P3 | retry without a flag reference | rejected | flagged retry accepted |
| 10 | Marginal factory successes multiplied | P3 | product of marginals | rejected / bounded | joint measurement accepted |
| 11 | Stale calibration | P2 | changed schedule hash | `unknown` | matching domain accepted |
| 12 | Zero failures / optional stopping | P2 (physical); P1b (stop-early on arm stats) | 0/N claimed as 0; unplanned stopping | rejected | frozen plan accepted |
| 13 | Proxy winner bypasses feasibility | P1b | proxy-dominant candidate without the certificate | not authorized | certified candidate authorized |
| 14 | Proposer edits evaluator or issues a certificate | P1a | snapshot tamper; forged issuer | abort / rejected | genuine receipt accepted |
| 15 | Retries vanish or become replicates | P1a | duplicate completion; uncounted retry | rejected / charged | single settled completion |

## J. Launch, isolation and broker contract (P1a gate; qualified in M0)

Each property has a positive test and a negative test on this host.

### J0. Process isolation

The launch profile is frozen and hashed. It uses bwrap with:
- `--unshare-pid --unshare-ipc --unshare-uts --unshare-net --new-session --die-with-parent`;
- `--clearenv`, plus an explicit environment allowlist;
- no inherited file descriptors beyond stdio (closed before exec);
- no host control sockets (D-Bus, SSH agent, systemd user bus, X11, `/run/user`);
- a private `/proc` and `/dev`.

Tests:
- signaling a sacrificial process outside the sandbox fails;
- no inherited secret is visible in the environment or in descriptors;
- descendants die with the scope.

### J1. Request-inspecting egress broker

Inside the sandbox, traffic reaches a broker owned by the orchestrator through a loopback port that
`socat` bridges to a Unix socket. The broker **terminates TLS** using a per-campaign CA that only the
sandboxed CLI trusts (`NODE_EXTRA_CA_CERTS` for Claude; the CA-bundle setting for Codex, to be
qualified).

For every request, the broker:
- allows only provider API hosts and endpoints;
- parses the request body and enforces that the model matches the manifest;
- requires the declared tool list to be **empty**, or exactly the allowlisted set, and allows **no
  server-side tools** (web search/fetch, code execution, connectors);
- enforces the per-request and per-invocation ceilings (§K);
- logs the endpoint, model, declared tools and the usage reported in the response. Logs are sanitized:
  no auth headers and no credentials.

Any policy violation blocks the request and fails the job closed.

**Probe facts** (2026-10-15, mitmproxy 11.0.2; still to be qualified as tests in M0):
- *Claude CLI 2.1.300 with `--tools ""`, under the user's normal HOME:* it called
  `/v1/mcp_servers`, `mcp-proxy.anthropic.com` and `api.githubcopilot.com`, and its `/v1/messages`
  request declared about 20 claude.ai connector tools, including Google Drive search.
- *Claude with a fresh HOME holding only `.credentials.json`, plus
  `--strict-mcp-config --mcp-config '{"mcpServers":{}}'`:* only three requests, all to
  `api.anthropic.com`: `GET /api/oauth/profile`, `GET /v1/mcp_servers`, and `POST /v1/messages`
  declaring only `StructuredOutput` and `DeferredToolPlaceholder` (`max_tokens` 128000).
- *Codex 0.160.1 (ChatGPT auth):*
  - It uses `chatgpt.com/backend-api/codex/responses` over a **WebSocket**, so the broker must
    inspect client frames.
  - With the feature-disable set, the `response.create` frames carry **no `tools` key**.
  - It also sends analytics (`/backend-api/codex/analytics-events/events`) and OTLP metrics
    (`ab.chatgpt.com`). M0 tests whether blocking these breaks calls.
- *Broker policy follows from these facts.* Allowlist exact (host, method, path) triples; the tool
  set must be ⊆ {`StructuredOutput`, `DeferredToolPlaceholder`} for Claude, and absent for Codex.

If TLS interception cannot be qualified (for example, because of pinning), a campaign **cannot claim
K0** (D16). In that case only CLI configuration plus telemetry is available, and it is labelled
`K0-unenforced`.

Credentials pass through the broker, so the broker is part of the TCB (§O).

### J2. Filesystem allowlist and CLI configuration

**Filesystem.** The sandbox has an empty root plus a read-only runtime, and additionally only:
- the job directory;
- the verification-only toolbox (no constructors, fixtures or tests);
- a fresh per-job CLI home containing only credentials;
- an output directory.

Everything else is invisible:
- the repo and git history;
- prompts, notes and reviews;
- `ref-code`;
- the vault, ledger and runs;
- other jobs;
- host memories, plugins, skills, MCP, `CLAUDE.md`/`AGENTS.md`, and session history.

Canaries placed in the forbidden locations must stay unreadable.

**Claude CLI** runs with:
- `--tools ""`, or the minimal equivalent qualified in M0;
- web tools disallowed;
- `--no-session-persistence`;
- CLAUDE.md and auto-memory disabled.

**Codex CLI** runs with:
- `--ephemeral --ignore-user-config --ignore-rules`;
- `--disable` for every tool-bearing feature: shell_tool, multi_agent, apps, browser_use,
  computer_use, in_app_browser, plugins, remote_plugin, and others found in `codex features list`;
- web search set to disabled in its config. Note that cached web search may be on by default.

**Effective tool manifest.** The effective tool manifest is verified **from intercepted request
bodies** (J1). Any unexpected tool fails the job closed. If a CLI cannot run without a tool, that
provider is disqualified for K0.

### J3. Modes

- Phase 1 is call-mode only: structured output, no tools.
- `session` mode is deferred until credential mediation is qualified.

### J4. Generated programs (P1b)

Generated programs run in a separate profile: J0 isolation, no network, no credentials, read-only
runtime, and a size-limited tmpfs scratch directory.

Resource and output limits:
- Each job, whether a program or an agent call (P1a), gets a cgroup scope **inside a campaign parent
  slice**. The slice enforces the D14 aggregate limits MemoryMax, TasksMax and CPUQuota.
- The **campaign deadline** is not a slice property: systemd 249 does not support `RuntimeMaxSec` on
  slices. A supervisor timer enforces it. On expiry the timer stops every child scope and disables
  further broker dispatch.
- Per-job wall time is `RuntimeMaxSec` on the job's scope.
- M0 qualifies all of this with concurrent jobs and with abandoned jobs.
- Disk is bounded by tmpfs `size=`.
- Output is accounted in aggregate, including logs and abandoned jobs.
- Outputs are capped before parsing. Only regular files are accepted; they are copied into trusted
  storage.
- Program CPU time is charged to the call that proposed the program.

### J5. Telemetry

Telemetry comes from three sources:
- the broker log, which is authoritative;
- Claude JSON usage;
- Codex `--json` events.

Missing telemetry is recorded as `unknown`, never as zero.

### J6. Librarian and B6

The librarian (known-code DB) and any reproduction arm use a **separate declared profile** with frozen
source bytes and logged exposure. They never share homes, sessions or namespaces with K0 arms. B6 is
deferred until this profile is qualified.

## K. Budget accounting (P1a)

**Units.** These are kept separate and never summed:
- transport retries;
- provider requests (counted at the broker);
- proposal attempts;
- verifier CPU-seconds;
- program CPU-seconds;
- wall time;
- tokens;
- monetary-equivalent USD: Claude's reported list price, and a declared price table for Codex.

Subscription billing is recorded separately.

**Enforceable ceilings, per call:**
- an **input bound**: before forwarding, the broker computes a conservative input-token bound from
  the request body (UTF-8 bytes ≥ tokens) and rejects requests above the per-call input ceiling;
- maximum requests per CLI invocation, enforced at the broker (call mode expects 1, plus CLI retries
  up to a cap);
- maximum output tokens, enforced via CLI or provider settings qualified in M0 and checked at the
  broker;
- maximum wall time, enforced by killing the scope.

The reservation is the **maximum total charge** under these ceilings: input bound × the highest
applicable input price (including cache-write premiums), plus the output ceiling × the output
price, plus the request-count ceiling. Prices are taken from a declared price table.

**Settlement.**
- Each retry is reserved separately.
- An uncertain charge (dispatched, but completion not recorded) **keeps its full reservation**.
- Usage-limit backoff consumes wall time but not proposal attempts.
- Confirmation capacity is reserved at the start.

## L. Dispatch, transactions, durability (P1a)

**Job state machine.**

```
reserved → dispatched → {completed | uncertain | failed} → settled
```

- Dispatch intent is persisted durably **before** the CLI is launched.
- The broker logs every upstream request, against the job's reservation, **before** forwarding it.
- Killing a CLI never releases an unresolved charge: the job becomes `uncertain` and keeps its full
  reservation.
- Each job carries an idempotency key and a **monotone lease generation**.
- On lease expiry the old execution's scope is killed. If killing cannot be confirmed, its reservation
  is retained.
- Duplicate completions are rejected, whether they come from a stale generation or from the same
  generation.

**Admission, settlement and accounting** happen in one transaction:
1. Run slow verification outside the lock.
2. Inside `BEGIN IMMEDIATE`, revalidate the read set, the generation and the artifact hashes.
3. Append the event, which is hash-chained.
4. Update the projections.
5. Settle the job and reconcile the reservation.
6. Commit.

**Publication order.**
1. Write a temporary file in the destination filesystem.
2. fsync the file.
3. Rename.
4. fsync the directory.
5. Commit the DB transaction that references it.

Crash tests sit between each step.

**Replay and recovery.**
- Replay runs over recorded events.
- Resume never duplicates admissions and never refunds spent or uncertain budget.
- Operational state (jobs, leases, reservations) is kept separate from the scientific event log.

## M. Artifact retention and reconstruction (P1a)

**Sanitize before addressing.**
- Secrets never enter content-addressed storage.
- The broker strips auth headers.
- Raw provider responses are scanned and redacted **at ingest, before hashing**, with a redaction
  record.
- A deliberately secret-bearing response is tested through ingest, sealing and reconstruction.

**Bundle.** The bundle holds the complete transitive closure:
- manifest;
- events;
- every referenced artifact;
- prompts as sent;
- sanitized responses;
- call records;
- broker logs;
- report;
- the **executable closure**: a runnable relocatable CPython (uv-managed standalone build:
  interpreter, stdlib and bundled shared libraries; the glibc baseline is recorded), the locked
  dependency wheels with hashes (native extensions included), the verifier snapshot source, and
  the reconstruction scripts.

The bundle is stored as tar + zstd with a sha256, and is chunked if large. A chunk size is never
permission to omit evidence.

**Acceptance.** `zkqec audit --bundle X` must work on a clean directory with no repo, no original venv
and **no network**:
1. build a venv from the archived wheels;
2. rebuild the ledger;
3. re-verify every hash;
4. re-run every exact receipt.

The archived runtime is used for this, not any host installation. The only host requirement is a
glibc at least as new as the recorded baseline.

## N. Namespacing and isolation across campaigns (P1a)

**Namespacing.**
- All state is namespaced by campaign, arm, split and profile: archives, caches, digests, CLI homes.
- There is no global archive across arms or splits.

**Feedback to proposers.** Proposers receive **no** audit feedback before sealing. That excludes:
- DB matches;
- linter details;
- hidden identifiers;
- diagnostic labels.

A rejected proposal sees only "rejected: policy".

**Held-out data.**
- Split and calibration procedures are frozen before held-out tasks are generated.
- Held-out tasks are audited for duplicates and structural overlap with dev.

## O. Environment freeze and trusted computing base (P1a)

**Pinned and hashed into the manifest:**
- the dependency closure (a lock with hashes, plus archived wheels);
- the vendored kernel and the verifier snapshot;
- solver and checker binaries;
- CLI versions and effective settings, including the qualified flag sets and the feature-disable list;
- the broker version and policy;
- the launch profiles;
- resolved model ids.

Evaluator code is imported from a read-only snapshot with a controlled `sys.path`.

**Trusted computing base:**
- the orchestrator;
- admission and the ledger;
- registered verifiers and twins;
- the sandbox launcher;
- the broker, which sees credentials and plaintext model traffic;
- the host OS.

Hash chains detect accidental or third-party tampering. They do not protect against a compromised
trusted writer.

**Overrides.** A human override may pause, re-scope or version a campaign. It never creates
`verified`.
