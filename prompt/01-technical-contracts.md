# Technical contracts (binding appendix to `00-master-prompt.md`)

> **Status:** DRAFT v0.3. Every rule here is normative for the build.
>
> **Phase tag.** Each contract says when it must exist: **P1** is Phase 1 (the first full run), **P2**
> is circuit level, **P3** is application level.
>
> **Conflicts.** If code or docs disagree with this file, the code is wrong or this file must be
> revised first. A revision means a new prompt version, a commit, and a note in `notes/DECISIONS.md`.

---

## A. Typed propositions, entailment, and outcomes (P1)

### A1. Propositions

A **proposition** is a typed record:

```
Proposition(subject_ref, predicate, args, direction, value, units, scope, domain_ref, guarantee)
```

The predicates are a closed, versioned registry.

| Predicate | Arguments |
|---|---|
| `css_valid` | (code) |
| `dimension_eq` | (code, k) |
| `distance_lower_bound` | (code, sector∈{X,Z,both}, d) |
| `distance_upper_bound` | (code, sector, d) |
| `distance_exact` | (code, d), derived only from matching lower and upper bounds |
| `max_check_weight_le` | (code, w) |
| `max_qubit_degree_le` | (code, q) |
| `implements` | (operation, target_action, basis, phase_convention) |
| `presentation_equal` | (code_a, code_b) |
| `equivalent_under` | (code_a, code_b, relation), witness required |
| `disjoint_representatives_exist_in_basis` | (code, basis) |
| `batch_count_le` | (code, basis, b), witness = coloring |
| `failure_upper_bound` | (event, domain, value, confidence) |
| `satisfies_task` | (candidate, task), derived only |

Field rules:
- `guarantee` is one of `exact`, `numerical(tol, method)`, or `statistical(alpha, design)`.
- `scope` carries the qecforge scope labels, e.g. `algebraic_code_distance_not_circuit_fault_distance`.

### A2. Evidence receipts

An **evidence receipt** binds one proposition to:
- the issuer: a registered verifier name and version;
- the execution id;
- the input artifact hashes;
- the method;
- a completion status: `completed`, `budget_exhausted`, `timeout`, `crashed`, or `unsupported`;
- output artifacts and witnesses;
- the status of the proposition: `verified`, `refuted`, or `unknown`.

Two constraints apply:
- Only `completed` receipts from a registered issuer can carry `verified` or `refuted`.
- The issuer identity comes from the orchestrator's execution record. It is never taken from the
  payload.

### A3. Entailment

A claim may be **asserted** only if the resolver can derive it from verified receipts in a matching
domain, using registered inference rules. Examples:
- `distance_lower_bound(c, both, 6)` entails `distance_lower_bound(c, both, 5)`.
- A lower bound of 6 together with an upper bound of 6 entails `distance_exact(c, 6)`.
- A witness for `distance_upper_bound(c, ·, 6)` **never** entails a lower bound.
- Sector bounds combine into `both` only through the min rule.

Restrictions:
- `solver_claim`, `agent_assertion`, and `assumption` receipts never authorize a conclusion.
- `satisfies_task` requires every component predicate demanded by the TaskContract.

### A4. Outcome types

These are distinct, typed outcomes and are never inferred from a process exit code:
- operational job status;
- `launch_failure`;
- `timeout`;
- `resource_killed`;
- `unsupported`;
- `malformed_proposal`;
- scientific `counterexample`.

A completed job may establish only `unknown`.

### A5. Reports

- Every scientific assertion in a report is **rendered** from a proposition record.
- Free prose is allowed only in sections the linter marks as non-scientific.
- A linter rejects any numeric or qualitative scientific statement that has no proposition binding
  in non-prose sections.

## B. Design objects and proposal semantics (P1 core, P2/P3 extensions)

### B1. DesignCandidate

`DesignCandidate` is an immutable record. It holds versioned references to every component of x:

```
encoding E, algorithm A, code C, logical basis L, gadgets G, placement/schedule P, decoder D, factory policy F, error allocation η
```

Rules:
- Each component ref is one of: an artifact hash, `absent`, or `unsupported`.
- Phase 1 activates C and L, plus operation witnesses for S-F tasks.
- Every other component is explicitly `absent/unsupported`. A claim that needs one stays blocked.

### B2. Proposals

Proposals are **component edits** against a parent candidate. The resolver performs dependency-local
invalidation:
- A basis change invalidates operation and injection evidence. It keeps distance.
- A schedule change invalidates circuit and timing evidence. It keeps CSS commutation.
- A decoder change invalidates performance evidence.

### B3. Proposal fields

Proposal fields fall into four disjoint classes:

| Class | Meaning | Status |
|---|---|---|
| (a) construction parameters | e.g. torus dims, polynomials, a sparse matrix in a declared encoding (sorted unique (row, col) pairs over GF(2); duplicates rejected, not XORed) | input |
| (b) requested predicates | what the proposer claims to target | non-binding |
| (c) untrusted witnesses | e.g. a claimed low-weight logical, a permutation, a coloring | rechecked by the gate |
| (d) verifier-owned observations | n, k, d bounds, weights, action, error rates | **rejected if supplied** |

### B4. Scope

- Phase 1 scope is **binary CSS codes with +1 stabilizer signs**.
- Every named constructor documents its exact algebra. For example, the two-block group-algebra
  constructor is defined over abelian groups `Z_l × Z_m` and does not claim arbitrary quasi-cyclic
  coverage.
- Non-CSS and subsystem classes are `unsupported`.
- G-open emits binary CSS matrices. It is a CSS grammar, not unrestricted stabilizer discovery.

## C. Distance certification contract (P1)

**C1. Preflight.**
- Before running, compute the half-pattern count `M = Σ_{j≤t} C(n,j)` and estimates for memory and
  for bucket-pair work `Σ_s M_s²`.
- Refuse with `budget_exhausted` if any budget would be exceeded.
- Publish measured feasibility tables for this host and implementation. There is no universal (n, d)
  cutoff. Reference sizes: `M(100,4) = 4,087,976` and `M(144,6) ≈ 1.16·10¹⁰`.

**C2. Lower bounds.** A lower bound `d ≥ 2t+1` requires *all* of the following:
- the complete canonical logical basis;
- both CSS sectors;
- every same-syndrome pair examined, including pairs across shards, for parallel runs;
- a stored completed-range record that names t, the counts, and the completion status.

Further rules:
- Symmetry reduction is allowed only with a written proof plus a test against unreduced enumeration on
  instances where both run. "Tested" is not "proved"; both are required.
- A SAT/ILP lower bound needs a validated encoding from the original problem into SAT (tested against
  exhaustive search on small codes) **and** an independently checked UNSAT proof (DRAT or LRAT checked
  by a separate checker). Otherwise the result is `solver_claim`.

**C3. Upper bounds.**
- A logical operator of weight w proves `d ≤ w` once the kernel re-verifies it in the claimed sector:
  zero syndrome and a nonzero logical signature.
- Randomized information-set search yields witnesses only. Failing to find one proves nothing.

**C4. Admission.**
- A target `d ≥ d_min` is admitted only when the certified lower bound is at least `d_min`.
- Certify the needed predicate rather than the exact distance.
- Intervals are reported as intervals.

**C5. Fixtures.** The fixtures for the M2 known codes record:
- source and version (Bravyi et al. 2024, arXiv:2308.07915, Table 3);
- torus dims and polynomials;
- the coordinate convention;
- matrix hashes;
- stored witness artifacts.

Deterministic validation of the witnesses is the acceptance test. The success rate of randomized
witness *discovery* is benchmarked separately.

## D. Code identity, equivalence, novelty (P1)

**D1. Check-presentation key.**
- This is a canonical form of the colored Tanner graph of the *given* generators (pynauty).
- It deduplicates exact presentations up to qubit and check permutation.
- It is generator-dependent: `H_X={1100,0011}` and `{1100,1111}` with `H_Z={1111}` give the same code
  but different keys.

**D2. Relations** (each defined separately, with a checked witness):
- **same stabilizer group**: equal row spaces, by RREF;
- **qubit-permutation equivalence**: an explicit permutation, verified by row-space equality after
  applying it;
- **X/Z swap**;
- **local-Clifford equivalence** (later);
- **logical-basis change**.

Cheap invariants, such as `[[n,k]]`, distance intervals, weight distributions of the given
generators, and WL hashes, only *filter* which pairs get compared. A differing *true* invariant
certifies non-equivalence; an exhausted search is `unknown`.

**D3. Two identities.**
- *Mathematical-code identity* is used for novelty labels and to share verification work.
- *Implementation identity* is the full DesignCandidate.

Equivalence never discards implementation variants. Hard pruning needs a hard-constraint violation or
a domain-compatible dominance certificate. Proxy dominance only affects scheduling priority.

**D4. Novelty labels.** Each candidate against known-DB vX gets one label: `database_match(entry,
relation, witness)`, `certified_nonmatch_under_R(coverage, budget)`, or `audit_inconclusive`.
Other labelling rules:
- "Reproduction" is used only for arms whose provenance shows source exposure (B6, K1 retrieval of the
  target).
- Detected retrieval violations are reported separately.
- Pretrained contamination is declared unidentifiable.
- Code novelty, implementation novelty, and autonomy level are reported as separate axes.

**D5. DB coverage.** The known-code DB records which families, (n, k) ranges, and sources it covers.
- Its entries are recomputed from matrices by our verifiers.
- It is built by a separate librarian process (K2) in its own namespace.

## E. Theorem registry and bounds (P1)

Each theorem is registered with its preconditions, scope, and the predicate it can certify. The
theorems are used only where their preconditions are checked:

| Theorem | Precondition / statement | Use |
|---|---|---|
| Quantum Singleton | any stabilizer code; `n − k ≥ 2(d − 1)` | S-A tuple is *incompatible* iff `n_max − k_min < 2(d_min − 1)` |
| Quantum Hamming (counting) | **nondegenerate** codes only; `Σ_{j=0}^{⌊(d−1)/2⌋} 3^j C(n,j) ≤ 2^{n−k}` | Excludes nondegenerate codes only. Passing it proves nothing. Degeneracy must be a task constraint, not an unchecked flag |
| LP / shadow bounds | stabilizer codes | Infeasibility counts only with a checked dual certificate (later) |
| Full disjoint same-type representatives | the specified full set of k same-type representatives | `n ≥ Σ wt ≥ k·d_Z`. Route-specific; never a task impossibility |
| Batching bound | b support-disjoint batches | `b ≥ ⌈Σ wt / n⌉`. DSATUR gives a *feasible* coloring, not an optimal one |
| Eastin–Knill | fixed code with error detection, transversal gate set | Excludes a universal transversal gate set only. Never rejects injection, code switching, or measurement routes |

A route-specific obstruction becomes a scoped `NoGo` for that route, with a completeness certificate.
It never becomes task infeasibility.

## F. Logical-action verification (L3; P1)

- **Witness validation and existence claims are separate.**
  - Checking a supplied permutation, basis, or coloring is a witness check.
  - "No disjoint basis exists" is an existence claim. It needs an exhaustive or proof-checked search;
    otherwise it is `unknown`.
- **Signed action.** For a proposed physical Clifford operation (permutation, transversal H/S,
  fold-type map):
  - verify that the stabilizer group is preserved, including signs;
  - compute the induced action on all **2k** logical Pauli generators, with phases, modulo stabilizers,
    via a signed symplectic tableau;
  - compare it to the requested target in the declared basis and phase convention.
  - Negative controls: physical S inducing logical S† on the m = 7 chain; a wrong logical
    permutation; an incomplete basis.
- **Folds.** Each fold-type construction is defined explicitly as a physical circuit (qubit pairing
  plus gate assignment) with a target action. "Fold-transversal" is never a label without a circuit.
- **Compensation.** Compensating Cliffords and basis changes are charged. Workload, state, and
  observable coordinates are transformed accordingly in P3.
- **Kernel scope.** The kernel's `permutation_action` (pure CSS permutations) and
  `structural_report` keep their restricted scope labels. They are not promoted to a general Clifford
  verifier.
- **N-version.** Second implementations of rank/k, small-n distance, and signed logical action are
  written **before any campaign**, at development time, by Codex from the written spec only, in a
  sandbox without access to the first implementation.
  - They share no scientific helpers.
  - They are cross-checked against independent exhaustive truth on tractable cases, targeted
    mutations, and declared random distributions, with conventions aligned explicitly.
  - Both are then frozen.
  - Any disagreement during a campaign quarantines the result as `unknown`. Agreement of models never
    resolves a disagreement.

## G. Circuit level (P2; spec now, build later)

**Memory estimand.** Freeze all of the following:
- memory length R;
- active logical observables;
- initialization and readout;
- noise model;
- decoder and settings;
- the failure event.

Measure per-shot block failure and per-specified-observable failure. Any per-round or per-qubit
conversion needs an explicit validated model. Never apply binomial intervals to pooled correlated
rounds or logical bits. Count all simultaneous data and ancilla qubits.

**Circuit semantics.** Check:
- expected detector and observable counts;
- signed logical input→output flows;
- the actual stabilizer-measurement action;
- boundaries;
- directed fault propagation against an independent small simulator.

Both memory bases use the declared schedule, or else they are separate domains (dualizing can change
ordering). The schedule negative control is a *demonstrably invalid* interleaving; mixed-sector
schedules are not invalid per se.

**Decoding.**
- Sample the original circuit.
- Verify `H_D ê = s` and score `L_D ê` against the actual sampled logical flips.
- Keep detector-free logical faults.
- Record decomposition and approximation flags, and whether matching ignores correlations.
- Use exhaustive decoding on tiny cases.
- Stim is used only within its supported stabilizer and noise semantics; anything else is
  `unavailable`.

**Circuit distance.**
- A witness of w physical faults, replayed in the original circuit with physically compatible faults,
  proves `d_circ ≤ w` and nothing more.
- Lower bounds need exhaustive or proof-checked exclusion.
- `shortest_graphlike_error` ignores non-graphlike mechanisms and is never a general bound.
- Define the fault alphabet, the location-counting convention, rounds, and boundaries. Keep this
  quantity distinct from the minimum number of faults that makes the decoder fail.

## H. Statistics and confirmation (P1 for any sampled quantity; central in P2/P3)

- **Freeze before looking.** Before any confirmation data is observed, freeze:
  - each confirmation family;
  - shot budgets;
  - the alpha allocation (Bonferroni across the declared family);
  - the retry policy (none by default);
  - the acceptance rule.

  The acceptance rule is that the relevant one-sided upper confidence bound meets the target.
- **Zero failures.** With zero failures in N fixed IID shots, the two-sided CP upper endpoint is
  `1 − (α/2)^{1/N}`. For N = 10⁴ and α = 0.05 that is ≈ 3.69·10⁻⁴, never 0.
- **Separate data.** Selection data and confirmation data are disjoint. Calibration fits chosen during
  search are confirmed on held-out points.
- **Adaptive sampling** uses a validated confidence sequence, or else is forbidden.
- **Shared seeds** across different circuits need an explicit coupling definition.
- **Confirmation budget** is reserved at campaign start. If confirmation fails, the candidate is
  reported as failed, not re-confirmed.
- **Agent comparisons** use the campaign replicate as the unit, with paired seeds. Failed and censored
  campaigns are kept. A campaign with no result reports "no verified solution within budget" unless
  infeasibility itself is certified.

## I. Negative and positive controls (P1 subset, extended per phase)

**Specification.** Each control specifies:
- (1) the target gate or verifier;
- (2) the injection path. It goes through the **production write path**, as a proposal or artifact,
  not as a schema-only rejection unless the schema is the target;
- (3) the expected semantic outcome: correct refutation, retained unknown, or unsupported;
- (4) a **paired valid positive** that must be authorized.

**Reporting.** Report `k/N` false authorizations and `j/M` false rejections of positives. Never claim
a universal guarantee.

**P1 controls:**
- rank computed over the reals;
- ℓ = 2 chain claimed to have d = 6;
- incomplete logical basis;
- S vs S†;
- wrong automorphism action;
- torus monomial collision;
- timeout presented as a lower bound;
- `solver_claim` presented as a proof;
- a witness of weight w claimed as a lower bound;
- a proposer supplying verifier-owned fields;
- spoofed issuer or role;
- tampered artifact or stale hash;
- source text requesting evaluator changes;
- an agent attempting a vault read, egress, or a DB write (run as sandbox tests).

**P2/P3 controls:**
- colliding or invalid interleaved schedule;
- non-graphlike input to matching;
- split hyperedges;
- zero acceptance;
- marginal factory successes multiplied;
- an unheralded failure treated as a retry;
- stale calibration;
- noisy data labelled exact;
- a code-capacity result promoted to algorithm reliability.

The 15 "must-not-happen" modes of `reviews/codex-independent-analysis.md` §6 are each covered by at
least one control or test.

## J. Launch and sandbox contract (P1, gate for any live campaign)

All of the following are qualified on this host in milestone M0. Each property has a positive test and
a negative test.

**J1. Egress.**
- The agent sandbox has its own network namespace (`bwrap --unshare-net`).
- Inside it, a `socat` bridge forwards a loopback port to a bound Unix socket. That socket leads to an
  orchestrator-owned CONNECT proxy whose allowlist contains only the model-API hosts of the configured
  providers.
- The CLIs use `HTTPS_PROXY`.
- Tests:
  - the model call succeeds;
  - a direct connect, any other host through the proxy, DNS to other hosts, and shell HTTP from a
    generated program all fail;
  - every proxied connection is logged.
- **Fallback.** If this cannot be qualified, live campaigns may still run only with the profile
  labelled `K0-unenforced`, and only with owner approval (D16).

**J2. Filesystem allowlist.**
- Start from an empty root plus read-only system libraries and the Python runtime.
- Add only:
  - the job directory (contract, request, schemas);
  - a **verification-only toolbox** (rank, k, witness checks; no constructor catalog, no fixtures, no
    tests);
  - a fresh per-job `HOME`, `CODEX_HOME` and `CLAUDE_CONFIG_DIR` (or equivalent), holding only the
    credential file;
  - an output directory.
- Not visible:
  - the repo;
  - git history;
  - prompts, notes, and reviews;
  - `ref-code/`;
  - vault, ledger, and runs;
  - other jobs;
  - host memories, plugins, skills, MCP servers, `CLAUDE.md`/`AGENTS.md`, and session history.
- Codex runs with `--ephemeral --ignore-user-config --ignore-rules` and without `--search`.
- Claude runs with `--no-session-persistence`, an empty or explicit `--tools`, web tools disallowed,
  and CLAUDE.md and auto-memory disabled.
- Canary files placed in every forbidden location must be unreadable. The canary establishes only the
  property it tests.

**J3. Modes.**
- Phase 1 agents run in **`call` mode**: no tools, structured output via `--json-schema` /
  `--output-schema`.
- The model therefore cannot read files, credentials, or anything else, and cannot make unbrokered
  calls.
- `session` mode (tool use) is deferred until credential mediation is qualified, meaning credentials
  are unreadable by model-controlled tools.

**J4. Generated programs (G-open).**
- They run in a separate bwrap profile: no network, no credentials, read-only runtime, scratch output
  only.
- Containment is aggregate, via `systemd-run --user --scope`: MemoryMax, TasksMax, CPUQuota,
  RuntimeMaxSec. This is qualified here.
- `--die-with-parent`, and the whole scope is killed on timeout.
- Output caps: bytes, JSON nesting and list lengths are capped *before* parsing; outputs must be
  regular files (symlinks and special files are rejected); outputs are copied into trusted storage.
- Program CPU is charged to the proposing call.

**J5. Telemetry.**
- Claude: `usage.server_tool_use.*`, cost, model, and session id.
- Codex: `--json` events, including tool calls.
- Proxy: connection logs.
- Missing telemetry is recorded as `unknown`, never as zero.

## K. Budget accounting (P1)

**Separate units:**
- transport retries;
- proposal attempts;
- verifier CPU-seconds;
- program CPU-seconds;
- wall time;
- tokens;
- monetary-equivalent cost.

Monetary-equivalent cost is the CLI-reported list-price USD for Claude and a declared price table for
Codex tokens; subscription billing is recorded separately.

**Reservations.**
- Every model call and verifier job reserves its worst-case budget atomically before dispatch.
  Actual usage is reconciled afterwards.
- Interrupted calls are charged their full reservation.
- Usage-limit backoff (5/15/45 min) consumes wall time but not proposal attempts.
- Confirmation capacity is reserved at campaign start.

**Coverage.** All model and screening calls go through the broker. There are no agent-side tools in
Phase 1, so no unrecorded calls are possible.

## L. Transactions, leases, replay (P1)

**Jobs and leases.**
- Each job is bound to immutable hashes of its input, domain, and evaluator.
- Leases carry **monotone generations**. A completion from a stale generation is rejected.

**Admission** (single writer):
1. Run the slow verification outside the lock.
2. Inside `BEGIN IMMEDIATE`, revalidate the read set, the generation, and the artifact hashes.
3. Append the event, which is hash-chained.
4. Update projections.
5. Commit.

**Artifacts.** An artifact is durably published (write, fsync, rename) *before* any event references
it.

**Persisted state:**
- request ids;
- reservations;
- scheduler and RNG state;
- dispatch digests;
- completion order.

**Replay and recovery.**
- Replay is defined over recorded events.
- Crash tests run at each commit boundary.
- Resume must neither duplicate admissions nor restore spent budget.
- Operational job state (queue and leases) is kept separate from the scientific event log.

## M. Artifact retention and reconstruction (P1)

**Sealing.** A sealed campaign exports its complete transitive artifact closure:
- manifest;
- event log;
- every referenced artifact (matrices, witnesses, certificates, programs, prompts as sent, raw model
  responses, sanitized call records);
- environment provenance;
- report.

The export goes into an immutable bundle (tar + zstd, sha256), chunked if large. A size limit is a
chunk size, never permission to omit evidence.

**Storage** follows D9: the bundle hash and index go in git, and the bundle goes to durable storage.

**Acceptance.**
- `zkqec audit --bundle X` reconstructs the ledger in an empty directory, re-verifies every hash, and
  re-runs every exact verifier receipt.
- Secrets are scrubbed and the scrub is tested.

## N. Namespacing and cross-campaign isolation (P1)

**Namespacing.**
- All state is namespaced by campaign, arm, split, and profile: archives, caches, digests, and CLI
  homes.
- There is no global archive shared across arms or splits.

**Isolation.**
- Post-hoc audit results (DB matches, linter details) are never fed back to proposers until the
  campaign is sealed.
- B6 and librarian sessions are never reused by K0 arms.
- Split and calibration procedures are frozen before held-out tasks are generated.
- Held-out generation is audited for duplicate tuples and structural overlap with dev, not only for
  seed overlap.
- Linter rejection details, hidden identifiers, and diagnostic labels are excluded from proposer
  feedback. A proposer sees only "rejected: policy".

## O. Environment freeze and trusted computing base (P1)

**Pinned and hashed into the manifest:**
- the Python dependency closure (lock with hashes);
- the vendored kernel and verifier snapshot;
- solver and checker binaries;
- `claude` and `codex` CLI versions and their effective settings;
- resolved model ids, where reported.

Evaluator assets are imported from a read-only snapshot directory with controlled `sys.path`.

**Trusted computing base (declared):**
- the orchestrator;
- admission and ledger;
- registered verifiers;
- sandbox and broker;
- the host OS.

Hash chains detect accidental or third-party tampering. They do not defend against a compromised
trusted writer.

**Human overrides** may pause, re-scope, or start a new campaign version. They can never turn
`unknown` or `unsupported` into `verified`.
