# Master self-prompt — zkqec: a modular multi-agent zero-knowledge QEC code-discovery workflow

> **Status:** DRAFT v0.3, awaiting owner approval. Nothing in §5–§11 has been built.
>
> **Files:**
> - This file is the plan.
> - `01-technical-contracts.md` holds the **binding** semantics (§A–§O): claims, distance,
>   equivalence, sandbox, budgets, statistics, retention.
> - `reviews/TRIAGE.md` shows how every review finding on v0.2 was handled.
>
> **Inputs:**
> - all of `ref-code/qecforge`;
> - `notes/ref-analysis/{qa,chandra,question-gen}.md`;
> - Codex (gpt-6-astra, max): a blind analysis (`reviews/codex-independent-analysis.md`) and a review of
>   v0.2 (`reviews/codex-prompt-review.md`, 35 findings, 6 blockers, all triaged).
>
> **Owner decisions needed:** §14.

---

## 0. One-page summary

**What gets built.** `zkqec` (name: D1) is a **deterministic orchestrator** in which LLM agents act as
**untrusted proposers**:
- Agents propose quantum-code *design candidates*.
- Only registered verifiers, run from a frozen environment, can admit **typed propositions** into an
  **append-only, hash-chained evidence ledger**. Examples of propositions: `distance_lower_bound(c, both, 6)`,
  `implements(op, target, basis)`.
- Reports are rendered from propositions, and a resolver checks **entailment**, not just citation.
- Agents never assert facts the verifiers own.
- In Phase 1, agents run headless (`claude -p`, `codex exec`) in **call mode** with no tools. They sit
  inside a bwrap sandbox with an allowlisted filesystem, and a **brokered egress** that reaches only the
  model API.

**What "zero knowledge" means here.**
- It means *zero task-specific expert intervention after the input contract is frozen* (QECForge §1).
- It does **not** mean absence of pretrained knowledge. Pretrained contamination is declared
  unidentifiable.
- It is not cryptographic.
- It is made auditable by **disclosed profiles**: knowledge level × prompt profile × grammar capability
  manifest.
- Held-out tasks and the known-code DB live in a vault that proposers cannot reach.
- Novelty is reported as `database_match` / `certified_nonmatch_under_R` / `audit_inconclusive`, never
  as an unqualified "novel".

**Why this should beat qa, Chandra and question-gen** (§13).
- *What they did:* all three let LLM verdicts or proposer-written checks admit results, and isolated
  workers by prompt or environment variable. In qa a worker edited the gate to admit its own submission.
- *Why we can do better:* QEC claims are mostly *computable*. So:
  - computation decides;
  - isolation is enforced by the OS and qualified with tests;
  - progress means verifier-established improvement;
  - summaries are rendered from the DB.

**Phases.** Only **Phase 1** is the "first full run". Later phases each need an owner go-ahead, informed
by Phase-1 measured costs.

| Phase | Scope | Claims it can support |
|---|---|---|
| **1** | Host qualification → minimal live vertical slice → verifier hardening → agent runtime → held-out evaluation on **S-A** (family-free structural targets, A0+) and **S-F** (functional logical-operation targets, A1) | Algebra-level only: validity, k, certified distance bounds, check weight/degree, verified logical Clifford actions, structural witnesses |
| 2 | Circuit level (S-B memory, schedules, decoders) | Memory-level, within declared noise/decoder domains |
| 3 | Application closure (S-C, G1–G3, instruments, resource closure) | Task-level claims via the resolver |
| 4 | Held-out autonomy comparison at application level (G4) | H1–H4 |

**Process.**
- Every milestone ends with tests, an independent Codex review (gpt-6-astra, max, read-only), and written
  triage.
- Commits follow **Chandra's commit principles exactly**: the gate is installed, one commit per DAG node,
  strict landing, no attribution.

---

## 1. Role and mission

You are Claude Code acting as **lead engineer and research scientist** for the owner. Build and
validate a **modular, multi-agent, zero-knowledge quantum-error-correcting code-discovery workflow**
whose scientific specification is `ref-code/qecforge/`.

QECForge defines what an evidence-gated, application-aware co-design system must and must never do. It
supplies a tested kernel. It explicitly lacks:
- model-driven discovery;
- literature agents;
- scalable distance;
- circuit-level evaluation inside campaigns;
- a campaign-wide claim resolver;
- program sandboxing;
- held-out evaluation.

Deliver these in honest stages.

The target object (QECForge §2) is

`x = (E, A, C, L, G, P, D, F, η)`

for a frozen task `T = (H, ρ0, O, t, S, ε, α, H_hw, Q_max)`. The goal is to minimize `E[T_answer]`
subject to:
- `Q_peak ≤ Q_max`;
- `P(|μ̂ − μ| > ε) ≤ α`;
- the implementation is valid, with evidence in its declared domain.

A code matrix alone is not an answer. Phase 1 deliberately addresses the components C and L, plus
operation witnesses. Every other component is explicitly `absent/unsupported`, and claims that need
those components stay blocked (§B).

## 2. Ground truth (verified on this host, 2026-10-15)

**QECForge kernel** (`ref-code/qecforge/src/qecforge/`, commit as copied):
- **Tests:** 60 core tests pass and 4 optional tests are skipped (stim/ldpc/pymatching absent).
- **Modules:**
  - `gf2`;
  - `codes`: Steane, HGP, general BB, reference chain;
  - `distance`: complete bounded MITM, witnesses, budget exception;
  - `structure`: pure-CSS permutation action, restricted H/S, DSATUR;
  - `workload`, `gadgets` (n ≤ 12), `scheduling`, `decoding`;
  - `backends` (untested), `statistics`, `resources`;
  - `schemas`, `evidence` (standalone ledger), `agents`/`search`.
- **Normative docs:** `docs/{ARCHITECTURE,ASSUMPTIONS_AND_INTERACTIONS,EXPERIMENTS,SECURITY,EXTENDING_AGENTS}.md`
  and paper appendices A–H.
- **Kernel defects reproduced here.** These must be fixed test-first in M1, as recorded deviations:
  1. `gadgets.analyze_postselected_map` uses `np.allclose` with the default `rtol`. It accepts a
     non-unitary target at `tolerance=1e-10`, and an absolute residual on a tiny Kraus operator passes
     a 1-rad relative phase.
  2. `schemas.TaskSpec` freezes `trotter_steps`, which is an implementation choice.
  3. `evidence.EvidenceLedger` accepts caller-labelled `verified` records.
  4. `agents.Coordinator` conflates "infeasible" with "unverified" and writes its trace only at the end.
  5. The demo's operator bound is 0.07, giving an observable bias of 0.14, which exceeds its 0.1 target
     (correctly left `scientific_claim_ready=False`).

**BB fixtures.** n and k were recomputed from the published polynomials (Bravyi et al. 2024, Table 3):
`[[72,12,·]]`, `[[90,8,·]]`, `[[108,8,·]]`, `[[144,12,·]]`, `[[288,12,·]]` all match. Distances are not
yet certified by us.

**Host:**
- Linux, 144 cores, 503 GB RAM, no GPU.
- Python 3.10.12; numpy 2.2.6, scipy 1.15.3, pydantic 2.13.4; networkx, sympy and z3 installed.
- pip reaches PyPI: stim 1.16, ldpc 2.4.1, pymatching 2.4.0, pynauty 2.8.8.1, highspy, ortools, galois
  and numba are all available.

**Isolation primitives:**
- `bwrap` works: masking, `--unshare-net`, read-only FS.
- `socat` is present.
- User cgroup scopes work (`systemd-run --user --scope -p MemoryMax -p TasksMax`).
- Codex reported that `--unshare-all` failed inside *its* sandbox. Host qualification (M0) settles the
  rest.

**CLIs:**
- `claude` 2.1.300: `-p --output-format json` returns tokens, USD and `server_tool_use`. It also accepts
  `--json-schema`, `--tools`, `--disallowedTools`, `--no-session-persistence` and `--effort`.
- `codex` 0.160.1 (gpt-6-astra, max): accepts `--output-schema`, `--json`, `--ephemeral`,
  `--ignore-user-config` and `--ignore-rules`. Web search is enabled only by `--search`.
- The Codex prompt must be passed as an argument with `< /dev/null`; stdin delivery failed here.

## 3. Binding definitions

**Zero knowledge (ZK).** Zero task-specific expert intervention after the input contract is frozen.
- *Disclosed priors that are allowed:*
  - pretraining;
  - task-independent role contracts;
  - the grammar capability manifest;
  - verifier/simulator libraries;
  - the contract's hardware/noise model.
- *A1 functional inputs* (required logical operations, precision, resources) are **disclosed task
  inputs**.
- *Interventions:*
  - solution-bearing requirements, such as named target structures, seed polynomials or reference
    parameters;
  - any human scientific action during a campaign.

  An intervention voids the ZK label for that campaign.

**Profiles** (recorded in every manifest; the axes are separate disclosures, not semantic guarantees):
- *Knowledge:*
  - `K0`: no retrieval. This is *enforced* by egress brokering and verified by telemetry.
  - `K1`: frozen, hashed corpus with the target excluded; spans are logged.
  - `K2`: open retrieval, only in the B6 reproduction control.
- *Prompt:*
  - `P-min`: binary-CSS formalism, verifier interface and objective. No family names and no
    construction recipes.
  - `P-domain`: adds task-independent QEC methodology.
- *Grammar* (versioned **capability manifest**):
  - `G-open`: sandboxed programs plus explicit sparse matrices.
  - `G-struct`: named algebraic families, each with its exact algebra declared.
- *Valid combinations:*
  - **Zero** = `K0 + P-min + G-open`;
  - **Prior** = `K0 + P-domain + G-open + G-struct`;
  - optional prompt-effect arm `K0 + P-domain + G-open`;
  - B6 = `K2 + P-domain + G-open + G-struct`.
  - `P-min + G-struct` is invalid, because the families have names.
- The prompt linter is **syntactic screening**. It cannot certify the absence of an encoded answer.

**Autonomy** (QECForge §1):
- **A0**: humans give the family, constraints and objective.
- **A1**: humans give functional operation requirements.
- **A2**: humans give only the Hamiltonian task, accuracy and hardware.

Suites:
- **S-A** (family-free structural tuples) is A0+.
- **S-F** (functional logical-operation targets) is A1.
- A2 belongs to Phase 3 only.

Code novelty, implementation novelty and autonomy are reported as separate axes.

**Claim vocabulary.**
- Propositions, receipts, entailment and typed outcomes: §A.
- Decision labels: *valid in domain*, *conditionally valid / requires measurement*, *invalid for this
  candidate*, *mathematically incompatible within this route*, *unknown within budget*. The last two
  are never merged.
- "Best" means best verified within the declared space and budget.
- A campaign without a result reports "no verified solution within budget", unless infeasibility itself
  is certified.

## 4. Constitution

1. **The agent proposes; the verifier admits.**
   - Only registered verifiers, with issuer identity taken from execution records, emit `verified` or
     `refuted`.
   - Verifier-owned observations supplied by a proposer are rejected (§B3).
   - Agent agreement, confidence and self-review are never evidence.
   - A skeptic refutes only with a checked witness.
2. **Single gated write path.**
   - One admission service owns all research writes (§L).
   - The event log is append-only (triggers) and hash-chained, with rebuildable projections; checked by
     `ledger verify`.
   - Agents have no DB access, no verifier access and no role variables.
3. **Frozen evaluator.**
   - The verifier snapshot, the pinned environment closure and the CLI versions/settings are hashed
     into the manifest (§O) and re-checked around every agent call and verifier batch.
   - A mismatch aborts the campaign with a typed record.
   - No new verifier or skill is allowed mid-campaign; a new verifier means a new campaign version.
   - A human override may pause, re-scope or version a campaign. It never creates `verified`.
4. **No fabricated science.**
   - Missing capability → `unavailable`/`unsupported`.
   - A resource kill or timeout → `unknown`.
   - No mocks in scientific paths; fakes exist only in tests and are labelled.
   - No past-tense result without artifacts.
5. **Logic hygiene.**
   - unknown ≠ refuted;
   - a timeout is not a certificate;
   - a failed proof is not a counterexample;
   - 0 failures ≠ 0 probability;
   - a refuted premise leaves its derived claim `unknown`;
   - a witness proves only its bound direction.
6. **Domain-scoped evidence with dependency-local invalidation** (§B2). Cross-domain reuse needs a
   checked bridge.
7. **Everything is charged** (§K):
   - reservations come before dispatch;
   - invalid, duplicate, timeout and unsupported attempts are all recorded;
   - confirmation capacity is reserved up front.
8. **Untrusted inputs stay data.** Literature, model output and generated code cannot change the task,
   the evaluator, permissions or budgets.
9. **OS-enforced isolation** (§J), qualified on this host in M0, with canaries. If K0 egress cannot be
   enforced, live campaigns are blocked unless the owner approves the `K0-unenforced` disclosure (D16).
10. **Rendered reporting.** Scientific assertions are rendered from propositions, and the linter checks
    entailment (§A5). Every summary shown to humans or LLMs is generated from the DB at build time.
11. **Statistical discipline** (§H): frozen confirmation families, disjoint selection and confirmation
    data, explicit alpha, campaign replicate as the unit.
12. **Budget fairness:**
    - arms share verifiers, tasks and confirmation;
    - comparisons are made at equal proposals *and* equal total cost;
    - in evaluation arms, a usage limit pauses the arm; the model is never swapped.
13. **Autonomy log.** Every human action is logged.
14. **Two progress notions:**
    - *Scientific success* means verified objective improvement over the frozen baseline/archive.
    - *Blocker progress* is used for scheduling. It means verifier-established closure of an obligation
      or a strengthened bound.
    - Activity is never progress.
    - Approach IDs are owned by the orchestrator.
    - Three failed attempts change the policy; they never create a NoGo. A NoGo needs a scoped
      completeness certificate (§E).
15. **Complete retention.** A sealed campaign is reconstructable from its bundle alone (§M).
16. **Namespacing.** No state crosses campaign, arm, split or profile boundaries. Audit results are not
    fed back before sealing (§N).

## 5. Architecture

```
repo root = /data/haiyangw/claude/auto-code
  src/zkqec/
    kernel/        vendored qecforge 0.1.0 (byte-identical) + PROVENANCE; test-first patches = new evaluator version
    contracts/     pydantic (frozen, extra=forbid): TaskContract (no algorithm/steps), HardwareDomain, Profile,
                   Budget, CampaignManifest, DesignCandidate, ComponentEdit, Proposition, Receipt, WorkItem,
                   ExperimentRequest, AgentCallRecord, SkepticFinding, NoGo; closed versioned registries
                   (predicates, families, theorems, outcomes, scopes)
    admission/     single-writer service: receipts → events (hash chain, triggers), projections, resolver
                   (entailment rules, domains, invalidation, supersession/revocation), `ledger verify|rebuild`
    store/         content-addressed artifacts, publish-before-reference, integrity-checked reads
    grammar/       capability manifests; G-struct (explicit_sparse, two_block_group_algebra over Z_l×Z_m,
                   hypergraph_product of small classical seeds); G-open (`program` → sandbox → CSS matrices)
    verify/        L0 schema/size/byte caps · L1 CSS algebra · L2 distance (§C) · L3 signed logical action,
                   permutation/fold witnesses, batching witnesses (§F) · theorem registry (§E) ·
                   N-version twins (frozen pre-campaign) · [P2] circuits/decoders (§G) · [P3] instruments/resources
    identity/      check-presentation keys, equivalence relations with witnesses, invariants-as-filters,
                   novelty labelling against vault DB (§D)
    sandbox/       bwrap profiles `agent-call` (egress broker only) and `program` (no network); cgroup scopes;
                   canaries; output caps (§J)
    broker/        CONNECT proxy with provider allowlist + socat bridge; connection log; telemetry capture
    agents/        job runner (fingerprints, attempt-scoped files, atomic promotion after schema+semantic
                   validation, generations, reservations, backoff); providers ClaudeCall, CodexCall, Replay,
                   Fake(tests); role contracts (versioned, fingerprinted); digest builders (bounded, receipted,
                   DB-rendered, verdict-free)
    orchestrator/  campaign state machine; scheduler (priority by blocker/objective + simple bandit; LLM
                   planner deferred); budgets; leases; checkpoint/resume; PAUSE file; stopping rules
    firewall/      profile enforcement, prompt linter, vault access control, namespacing, contamination audit
    evalh/         S-A / S-F generators with difficulty calibration and theorem screening; arms; controls; stats
    report/        proposition rendering, entailment linter, autonomy/cost/contamination sections, bundles
    cli.py         zkqec qualify-host | campaign {run,resume,status,pause,abort,seal} | verify | ledger |
                   audit --bundle | eval | report
  roles/           role contracts (≤5 KB: Role · Inputs · Procedure · Deliverable · Verify · Anti-patterns),
                   per profile; fingerprinted
  configs/         tasks, hardware, profiles, budgets, campaigns, eval protocols
  tests/           unit · property · controls (paired) · firewall · sandbox/broker · crash-at-boundary ·
                   docs-drift/fingerprint · integration (fake provider) · slow
  _common/ .gitmessage AGENTS.md   Chandra commit gate (byte-identical; see _common/PROVENANCE.md)
  notes/  prompt/  results/ (bundle index + hashes)   runs/ (gitignored)
```

**Deferred until a measured need appears:**
- broader grammars (lifted/balanced products, quasi-cyclic beyond Z_l×Z_m);
- parallel or sharded MITM (only if the preflight tables show the need);
- local-Clifford equivalence;
- quality-diversity grids and an LLM planner;
- `session` mode (needs credential mediation);
- the logical-ops, decoder and resource roles (Phases 2–3);
- SAT lower bounds with proof checking (start in M2 only if cheap to qualify).

**Scope guard:** no dashboards or editor tooling. The CLI and rendered reports are the whole UI.

## 6. Agent roles (Phase 1)

| Role | Produces (proposals only) | Never authorizes | Default model (D2) |
|---|---|---|---|
| Designer-A | component edits: G-open programs / G-struct params, logical bases, operation witnesses | any verifier-owned fact | Claude Opus 5.5 |
| Designer-B | same, independently | same | Codex gpt-6-astra (max) |
| Route agent (S-F) | conditional structural routes for a functional target, with falsifiers and alternatives; route revision work items | globally necessary constraints | Claude Opus 5.5 |
| Skeptic (cross-family from the proposer under review) | structured counterexample *requests* (candidate witnesses, test instances) run by verifiers | refutation without a checked witness | the other family |
| Reporter | prose in non-scientific sections only | any scientific assertion | Claude Sonnet 5.5 |

**Rules:**
- **Contracts and requests.** Contracts are task-independent, written per profile, and linted. Requests
  are bounded digests rendered from the DB.
  - A digest holds: top candidates per niche with hashes, failure-reason histograms, scoped NoGos, open
    blockers, and skeptic findings stated as facts.
  - It never includes transcripts or verdict prose.
- **Reviewers** receive the frozen candidate plus its assumptions, never the proposer's transcript.
- **Outputs** must validate against a schema; otherwise the attempt is charged as `malformed_proposal`.
- **No self-evidence.** A proposer never files evidence about its own candidate.
- **LLM ranking** is used only for non-computable aspects. It is blinded, cross-family, and never the
  main selector.
- **Topology** is recorded: models, versions, and the absence of shared memory.
- **H4** (multi-agent benefit) is a hypothesis.

## 7. Orchestration (Phase 1)

1. **Freeze.** Load configs, write the manifest (§O), run the M0 qualification self-test plus canaries,
   reserve the confirmation budget, and create a namespaced immutable run dir (§N).
2. **Task intake.**
   - S-A: tuple → theorem screening (§E) → target predicates.
   - S-F: functional target → the route agent proposes routes (conditional, with falsifiers). Route
     revision is a recurring work item whenever downstream verification refutes a route.
3. **Loop**, while budget remains and no stop rule has fired:
   - The scheduler leases one work item: propose, verify(candidate, level), falsify(skeptic request),
     revise-route, or a baseline step.
   - Each proposal goes through these stages:
     1. byte/nesting caps;
     2. L0;
     3. build (sandbox if program);
     4. L1;
     5. presentation key (dedupe the *work*, never the implementation variants);
     6. cheap screening (proxy dominance only reorders priority);
     7. L2 upper witnesses;
     8. promotion;
     9. L2 lower bounds to the *needed* predicate;
     10. L3 for S-F;
     11. resolver.
   - Each admission updates the archive and digests.
4. **Confirmation.** Re-run the frozen confirmation family: N-version re-verification of every claimed
   algebraic proposition. Phase 1 has no sampling claims.
5. **Seal.** Run the post-hoc audit:
   - DB labels (§D4);
   - telemetry and egress audit;
   - autonomy log;
   - contamination report.

   Then render the report, apply the entailment lint, build the bundle (§M), and commit the index.

**Stop rules:**
- the budget is exhausted;
- no *scientific* improvement for K rounds, while the blocker-progress queue is empty;
- the target is met *and* confirmed;
- an integrity failure.

## 8. Verification ladder (Phase 1 levels; P2/P3 noted)

| Level | Content | Contract |
|---|---|---|
| L0 | schema, byte/nesting caps, dimension caps before allocation | §B3, §J4 |
| L1 | `H_X H_Zᵀ = 0` over GF(2); k by ranks; canonical pairing; weights/degrees | §A |
| L2 | preflighted complete MITM lower bounds with completed-range records; ISD witnesses for upper bounds; certify the needed predicate | §C |
| L3 | signed induced action on all 2k logical generators; permutation/fold/transversal circuits; witness vs existence | §F |
| L4 | [P2] circuit memory semantics, decoders, circuit-distance upper witnesses | §G |
| L5 | [P3] instruments: accepted/rejected branches, joint acceptance | qecforge App. D |
| L6 | [P3] resource closure, observable-unit error budget | qecforge App. E |

## 9. Knowledge firewall

- **Vault.** Held-out tasks, annotations, the known-code DB and planted controls are reachable only by
  audit and eval processes (§J2, §N).
- **Known-code DB.** Built by a separate K2 librarian (a different model family from the Zero
  designers, where possible). Its entries are **recomputed from matrices**, and its coverage is recorded
  (§D5).
- **Task generation for S-A/S-F, with difficulty calibration:**
  - Screen each target with the theorem registry; incompatible targets are labelled, not dropped
    silently.
  - Then run the ladder: DB lookup (vault side) → same-grammar random search with budget B → a bounded
    single agent in its own namespace. This produces labels *easy* / *open-under-budget* / *known*.
  - Freeze the procedure before generating held-out tasks.
  - Audit held-out tasks for duplicate and structural overlap with dev.

## 10. Evaluation

**Suites** (Phase 1):
- **S-A:** `(n_max, k_min, d_min, w_max, deg_max)`, optionally with self-dual or common-basis flags.
- **S-F:** functional targets checkable at L3. Examples:
  - a requested logical permutation or CNOT circuit (as a GF(2) matrix) implemented by a physical qubit
    permutation;
  - transversal H with a specified pairwise action;
  - transversal S with specified signs (S vs S†);
  - the structural part of parallel selected-Z-rotation routes (batch count ≤ b with witnesses), which
    is labelled *conditionally valid / requires instrument verification*.

**Arms** (Phase 1, all with shared verifiers and confirmation):
- B0: known reference, where one exists.
- B1: random over G-struct, with specified sampling distributions.
- B1e: evolutionary over G-struct, with specified mutations.
- B4-C and B4-X: single-agent Claude-only and Codex-only, token- and tool-matched.
- B5-Zero and B5-Prior.
- Optional: the prompt-effect arm.
- B6: reproduction control, labelled.

Multi-agent vs single-agent is reported as a **bundled system comparison** unless model mixture and
topology are separated. There is no "random program" baseline over G-open.

**Controls:** paired positive/negative, injected through the production path; the result is reported
as `k/N` (§I).

**Metrics:**
- verified feasible fraction;
- cost, tokens and wall time to the first verified result;
- best verified objective;
- invalid and duplicate fractions;
- novelty labels with DB coverage;
- telemetry-verified K0 compliance;
- total cost per unit.

**Statistics.** The campaign replicate is the unit, with paired seeds. The pilot size comes from M1–M3
cost telemetry. The protocol is frozen before held-out generation, and failed campaigns are kept (§H).

**Hypothesis and gate map** (CR-31):

| Hypothesis / gate | Phase 1 | Later |
|---|---|---|
| G0 (algebra, representation) | yes, algebra part | encoding equivalence P3 |
| G1 (circuits, instruments) | no | circuits P2, instruments P3 |
| G2 (calibration) | no | P2/P3 |
| G3 (task closure) | no | P3 |
| G4 (held-out autonomy) | yes, at algebra level only | application level P4 |
| H1 (application-aware answer time) | **cannot be supported** | P3/P4, with B2 |
| H2 (requirement transfer) | **cannot be supported** | P3/P4, with B3 |
| H3 (evidence domains + skeptic reduce false feasibility) | yes, at algebra level: ablate skeptic/domains, measure k/N on controls | P2/P3 |
| H4 (multi-agent vs matched single) | yes, at algebra level, as a bundled comparison | P4 |

## 11. Build plan

**Every milestone (= DAG node group) follows this procedure:**
1. Implement, committing per node (§12).
2. Tests green.
3. Run the background Codex review (`notes/reviews/Mx-codex.md`).
4. Write triage (`notes/reviews/Mx-triage.md`).
5. **Unresolved scientific or containment blockers stop advancement.**
6. Update `notes/PROGRESS.md`.

### Phase 1 (the first full run)

#### M0 — Host qualification

*Build:* `zkqec qualify-host`, which tests every §J property:
- the egress broker: the model call works, everything else fails, connections are logged;
- the allowlisted filesystem, with canaries;
- fresh CLI homes;
- CLAUDE.md, AGENTS.md, memory, plugins and MCP disabled;
- `call` mode with structured output for both CLIs;
- telemetry completeness;
- cgroup aggregate limits, descendant kill and output caps.

*Also:* pin and hash the environment closure (§O).

*Accept:*
- every property has a passing positive and negative test, saved as a qualification report artifact;
- anything unqualified is listed, and live campaigns stay blocked per D16.

#### M1 — Minimal live vertical slice

*Build:*
- vendored kernel with test-first fixes for the five §2 defects, recorded as kernel deviations;
- contracts subset;
- store;
- admission and event log;
- resolver with entailment rules for L1/L2 predicates;
- L0/L1;
- L2 via the kernel's MITM, with preflight and witness validation;
- the G-struct `two_block_group_algebra` family;
- one same-grammar random baseline;
- the ClaudeCall provider in call mode, via the broker;
- a bare scheduler;
- rendered report with the linter;
- bundle export and `audit --bundle`.

*Run:* **one live end-to-end S-A task** (small, e.g. `n ≤ 60`, `k ≥ 4`, `d ≥ 4`, `w ≤ 6`) under the
D5 cap.

*Accept:*
- the qecforge reference table is reproduced;
- the live run produces a sealed bundle that reconstructs in an empty directory;
- every rendered assertion is entailed;
- a test proves nothing can be admitted without the admission service;
- cost telemetry is recorded;
- `notes/postmortems/M1-slice.md` is written.

#### M2 — Verifier hardening

*Build:*
- §C completeness contract: preflight tables, both sectors, completed-range records;
- ISD upper-bound witnesses;
- §D identity and equivalence, with witnesses;
- §E theorem registry;
- §F signed logical action and folds;
- N-version twins, authored blind by Codex at dev time and frozen;
- BB fixtures (§C5);
- the full P1 paired-control harness (§I).

*Accept:*
- BB fixture witnesses validate deterministically;
- lower bounds are reported as intervals with completed-range records;
- the qecforge example distinguishes presentation from equivalence;
- the twins agree on the declared distributions and the targeted mutations;
- all P1 controls behave as specified, with their positives authorized.

#### M3 — Agent runtime and orchestration

*Build:*
- the CodexCall provider;
- Designer-A/B, route agent, skeptic and reporter contracts, per profile;
- the prompt linter;
- digests;
- job runner with generations and reservations (§K, §L);
- usage-limit backoff;
- the G-open `program` family in the `program` sandbox;
- namespacing (§N);
- the B1e evolutionary baseline;
- the S-F pipeline.

*Accept:*
- crash tests at each commit boundary pass;
- resume duplicates nothing and refunds nothing;
- stale-generation completions are rejected;
- sandbox escape tests fail closed;
- evaluator tamper aborts the campaign;
- schema-invalid output is charged;
- a live 2-task smoke run across both providers succeeds;
- `notes/postmortems/M3-smoke.md` is written.

#### M4 — Phase-1 evaluation

*Build:*
- S-A and S-F generators with difficulty calibration and the vault;
- the known-code DB (librarian), with coverage;
- the pilot matrix, sized from telemetry (D5, D13);
- the frozen protocol (§H);
- held-out runs;
- sealed bundles;
- the rendered report with ablations (skeptic off, domains off) and controls.

*Accept:*
- a report covering B0, B1, B1e, B4-C, B4-X, B5-Zero, B5-Prior (+ B6), with campaign-level
  uncertainty, null results included;
- `k/N` reported for the controls;
- every assertion entailed;
- a Codex audit of the report.

### Later phases (each needs an owner go-ahead, informed by Phase-1 costs)

| Phase | Content |
|---|---|
| **2** | Circuit level per §G. Freeze the memory estimand and horizon first (new decision). Then: stim circuits from schedules, both bases, independent small simulator, BP+OSD / graphlike matching with correlation flags, replayed circuit-distance upper witnesses, the S-B suite, and decoder/schedule roles. |
| **3** | Application closure. Acquire and version-bind arXiv:2606.25011 plus independent transcription checks for any TMR route. Then: task transformations of (H, ρ0, O) with equivalence tests; instruments (L5); queue-aware resources (L6); a **positive reference case** closing the full observable-unit error budget; requirement inference with route revision; B2/B3 on S-C; requirement annotations that allow multiple routes. |
| **4** | G4 at application level; H1–H4; hardware, robustness and transfer suites; the full ablation matrix. |

## 12. Engineering process

**Git: follow Chandra's commit principles exactly** (owner directive).
- The gate files are byte-identical copies, recorded in `_common/PROVENANCE.md`. They are installed
  with `bash _common/hooks/install.sh` and specified in `_common/contracts/commit_template.md`.
- *Title:* `type(scope)[!]: summary`.
- *Body:* typed objects `- why|finding|change|run|result|verify|caveat|next|files:`, with every
  finding/result tagged `[SOLID|PRELIMINARY|HOLE|FUTURE]`.
- *Breaking changes:* `!` appears if and only if there is a `BREAKING CHANGE:` footer.
- *Footers:* `Iter/Run/Refs/Verify/Claim`.
- *Scope:* one commit per DAG node or finer. Tests go in before or with their code.
- *Landing:* stage only the node's files, inspect `git diff --cached`, check the message with
  `COMMIT_GATE_STRICT=1 _common/hooks/commit-msg`, then land with `COMMIT_GATE_STRICT=1 git commit
  --file`. Never use `--no-verify`.
- *Never commit:* tool or model attribution of any kind; large datasets or mirrors.
- *Branch:* `zkqec-dev` (D7). See `.claude/skills/commit-gated/SKILL.md`.

**Codex in parallel:**
- At every milestone, plus every prompt revision, run
  `codex exec -m gpt-6-astra -c model_reasoning_effort='"max"' -s read-only -C <repo> -o <out.md>
  "<prompt>" < /dev/null` in the background.
- Codex also blind-authors the N-version twins at dev time, in a sandbox without access to the first
  implementation.

**Other rules:**
- **Subagents:** parallelize modules only after their contracts are frozen; one owner per file.
- **Verifiers test-first:** positive, negative, adversarial and twin cross-checks before any agent
  depends on them.
- **One owner per fact:** mechanical rules live in code; docs-drift and prompt-fingerprint tests;
  `docs/IMPLEMENTATION_STATUS.md` separates implemented / tested / validated / planned.
- **Never modify `ref-code/`.**
- **Keep long outputs in files** and keep `notes/PROGRESS.md` current, so work survives context
  compaction.

## 13. Lessons from the reference workflows → mechanisms

| Observed failure (source) | Mechanism here |
|---|---|
| "Solid" = LLM refuter + judge; the gate checks provenance, not math (qa `gate.py:628`) | Computable propositions and registered verifiers decide (§4.1, §A) |
| Proposer-written verification command; "a commit exists" counts as evidence (Chandra `admission.py:128,346`) | Registered verifiers from a frozen snapshot (§4.3, §O) |
| Worker edited the gate; workers launched runners (qa `1aa331b`, `ec89f24`) | No agent tools in P1; OS isolation; no DB access; tamper aborts (§J) |
| Role by env var; orchestrator self-promoted rows (Chandra) | Issuer from execution records only; admission-only write path, tested (M1) |
| Validator only in tests (Chandra `main.ts`) | The skeptic and twins are on the production path; a test asserts it (M2/M3) |
| Proposer self-reviews; copied verdicts (qa, question-gen `question_gen.py:1489`) | Proposers never file evidence; digests are verdict-free (§6) |
| Same-family judging and taste monoculture (qa: Claude 102/102 judgements) | Metric selection; the skeptic is cross-family by construction |
| Activity counted as progress; 42% of jobs cancelled (qa `runner.py:82`) | Two progress notions; orchestrator-owned approach IDs (§4.14) |
| Timeout (exit 124) accepted as a refutation (qa `failed_checks`) | Typed outcomes; timeout → `unknown` (§A4) |
| Gates were advisory; validity-blocked finalists were proved anyway (qa T3) | Overrides only re-scope; they never create `verified` (§4.3) |
| Summaries drift; notes live in /tmp or gitignored paths (qa, Chandra) | DB-rendered summaries; complete bundles (§M) |
| Fail-open filter; fixed-fraction taste; calibration on n=2 (question-gen) | Fail-closed gates; absolute predicates; paired controls (§I) |
| No difficulty calibration (question-gen) | Theorem screening plus a solver ladder (§9) |
| 60–90 KB packs; 242 KB of skills; doc drift (qa, Chandra) | ≤5 KB contracts; bounded digests; drift and fingerprint tests |
| Infrastructure outran validation (qa v1→v5 in 4 days) | Host qualification + live slice in M0–M1; deferred-machinery list (§5) |
| BLAS threads × RLIMIT killed checks (qa `6f35327`) | Single-thread BLAS; cgroup scopes; kill = unknown |
| **Reuse:** hash-chained events + projections (qa); executable admission records (Chandra); `codex_jobs` runner (question-gen); move47 sandbox + canaries; receipted digests (qa `derive.py`); evidence-only transport + firewall tests (question-gen) | Adopted in `admission/`, `agents/`, `sandbox/`, `firewall/` |

Chandra's own redesign notes are partly stale (IA-13). Cite a Chandra defect only after re-checking the
source.

## 14. Decisions for the owner (defaults in bold, with the reviewer's qualifications)

- **D1 Name.** **`zkqec`**: an internal name meaning operational autonomy, not cryptographic ZK.
- **D2 Runtime and models.**
  - **`claude -p` + `codex exec` in call mode via the broker**, conditional on M0 qualification.
  - Assignments: **Opus 5.5** for Designer-A and the route agent; **gpt-6-astra (max)** for Designer-B;
    the skeptic is always the other family; **Sonnet 5.5** for the reporter.
  - These are pilot choices, not validated necessities.
- **D3 Profiles.** **Zero and Prior as defined in §3**, plus the optional prompt-effect arm. No
  absence-of-prior-knowledge claim is made.
- **D4 Scope.** **Phase 1 (M0–M4) is the first full run.** Phases 2–4 each need your go-ahead,
  informed by Phase-1 costs.
- **D5 Budget caps.**
  - **M1 live slice ≤ $5; M3 smoke ≤ $10; Phase-1 dev pilot ≤ $150. The held-out run needs separate
    approval, with an estimate taken from pilot telemetry.**
  - These are ceilings, not feasibility estimates.
  - Development usage and Codex reviews are tracked separately and are not charged to campaign budgets.
- **D6 Vendoring.** **Copy qecforge into `zkqec.kernel` byte-identically.** Every tested patch is a new
  evaluator version, recorded in PROVENANCE.
- **D7 Branch.** **`zkqec-dev`**. Merge only on your request.
- **D8 Autonomy.** **Autonomous within Phase 1, with a short report at each milestone.** Unresolved
  scientific or containment blockers stop advancement. Stop and ask about budget, scope, or anything
  irreversible.
- **D9 Artifacts.** **Complete bundles (§M) in `/data/haiyangw/claude/zkqec-archive/`**, which is
  durable and outside the repo. The bundle index and hashes are committed under `results/`. The
  reconstruction test is mandatory.
- **D10 Domain and size caps.** **Binary CSS with +1 signs; n ≤ 200 for constructors; S-A/S-F targets
  with n ≤ 150.** Exact lower bounds are attempted only where the preflight allows.
- **D11 Phase-1 claims.**
  - **Allowed (algebra level only):** validity, k, certified distance bounds, weight/degree, verified
    logical Clifford actions, structural witnesses.
  - **Not allowed:** memory, circuit, application, or advantage claims.
- **D12 Equivalence for novelty labels.** **Same stabilizer group, qubit permutation, X/Z swap.** Local
  Clifford comes later. DB coverage: published BB/two-block tables, small HGP, small
  surface/toric/color instances, Steane/Golay-derived codes, plus the coverage report.
- **D13 Primary comparison and stopping.** **The primary metric is the verified feasible fraction on
  held-out S-A/S-F at matched total cost.** Secondary metrics are cost and time to first verified
  result. A campaign stops on budget, or on target met and confirmed. Confirmation means N-version
  re-verification.
- **D14 Aggregate compute.** **Verifiers ≤ 96 cores, ≤ 256 GB RAM; runs ≤ 200 GB disk; ≤ 6 h wall time
  per campaign; ≤ 4 concurrent model calls per provider.**
- **D15 Trust boundary.** **The TCB is as in §O. Credentials exist only in per-job fresh homes, read by
  the CLI; Phase-1 models have no tools.**
- **D16 If K0 egress cannot be enforced.** **Block live campaigns** (the default). The alternative is to
  run with a `K0-unenforced` disclosure.

## 15. Definition of done for the first full run (Phase 1)

1. M0–M4 acceptance tests pass, every Codex review is triaged in writing, and no open scientific or
   containment blocker remains.
2. `zkqec campaign run configs/campaigns/<x>.yaml` runs a real multi-agent K0 campaign end-to-end, under
   enforced isolation and within budget. It produces:
   - a manifest;
   - the event log;
   - propositions with receipts;
   - an archive;
   - call records with token/cost and telemetry;
   - an autonomy log;
   - novelty labels with DB coverage;
   - a sealed bundle that `audit --bundle` reconstructs.
3. The Phase-1 report compares the arms at campaign level with uncertainty. It includes null or negative
   outcomes and `k/N` control results. It states explicitly that H1 and H2 and any application claim
   are out of scope.
4. Every scientific assertion in the report is entailed by verified propositions.
5. `docs/IMPLEMENTATION_STATUS.md` and `notes/PROGRESS.md` state exactly what is implemented, tested,
   validated and planned.
