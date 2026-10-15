Do not approve v0.2 unchanged for the full build.  
The deterministic proposer/verifier separation is the right foundation.  
The evidence vocabulary largely follows qecforge correctly.  
The completion criteria do not ensure qecforge’s application-level mission.  
K0 still has concrete filesystem, network, and cross-run leakage paths.  
Tanner-graph canonicalization cannot support the stated code-equivalence guarantee.  
The four BB parameter triples are correct; the MITM scaling description is misleading.  
The statistical principles are sound, but the measured events and confirmation gates need specification.  
The orchestration needs stronger crash, budget, and artifact-retention contracts.  
This review was read-only; no files were changed.

References below concern [DRAFT v0.2, 707 lines](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:1).

1. **[blocker] Application completion is vacuous.** §7, [lines 388–400](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:388); §11 M6, [lines 599–603](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:599). The execution path skips L5; no milestone explicitly delivers L5/L6. M6 can pass while every application remains blocked. This falls short of [qecforge Appendix G, G1–G3](/data/haiyangw/claude/auto-code/ref-code/qecforge/paper/appendices/G_experiments.tex:11).

   **Change:** “Application completion requires a positive reference case with verified state/observable transformations, compiled operations, preparation/readout, applicable instrument branches, observable-unit error allocation, confidence allocation, and peak-space/answer-time accounting. Unsupported routes remain blocked. M1–M5 completion supports algebraic/memory claims only.”

2. **[major] The main design object has no explicit executable contract.** §5, [lines 264–275](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:264), and §6, lines 342–348. `Proposal (grammar union)` principally describes code constructors; it does not specify how basis, gadget, schedule, decoder, factory, encoding, and algorithm proposals become one immutable implementation. A builder can implement the roles as commentary around code search.

   **Change:** Add `DesignCandidate` with versioned references to every component of \(x\), plus typed component-edit proposals and target-operation contracts. Define dependency invalidation for each edit. Follow [qecforge Appendix B, operation and calibration contracts](/data/haiyangw/claude/auto-code/ref-code/qecforge/paper/appendices/B_interfaces.tex:37).

3. **[blocker] Evidence IDs do not establish that evidence supports a claim.** §0, [lines 32–37](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:32); §3, lines 173–187; §8, lines 427–436. A report could cite a verified \(d\le6\) witness beside “\(d\ge6\), verified.” Numeric citation checking also misses unsupported qualitative claims.

   **Change:** Require typed propositions containing subject/domain, predicate, bound direction, units, scope, and statistical guarantees. Render scientific assertions from those records. Explicitly prohibit unchecked `solver_claim` and `agent_assertion` from authorizing conclusions. Verification must match the requested predicate, as required by [qecforge ARCHITECTURE, evidence semantics](/data/haiyangw/claude/auto-code/ref-code/qecforge/docs/ARCHITECTURE.md:33).

4. **[blocker] The cited wrapper does not enforce “model API only.”** §2, [lines 126–131](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:126); §4.9, lines 235–239. The template explicitly **shares the network**: [worker-sandbox lines 24–25 and 86–87](/data/haiyangw/claude/auto-code/ref-code/Chandra/move47/bin/worker-sandbox:24). Disabling a CLI web tool does not block shell HTTP, another client, or provider-hosted retrieval.

   Basic bwrap isolation succeeded in this review; `--unshare-all` failed with `NETLINK_ROUTE … Operation not permitted`. That may reflect the enclosing sandbox, so host feasibility remains unqualified.

   **Change:** Require a pre-build launch-context test of enforced egress, immutable tool policy, complete tool telemetry, and authenticated CLI operation together. Use a controlled broker/proxy; missing telemetry is `unknown`, never zero.

5. **[blocker] The filesystem denylist leaves answers readable.** §4.9, [lines 236–237](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:236); §9, lines 445–467. Masking `ref-code/` leaves this prompt, reviews, notes, tests, git history, vendored constructors, and possibly sibling checkouts accessible. The vendored kernel itself contains [Steane and BB construction recipes](/data/haiyangw/claude/auto-code/ref-code/qecforge/src/qecforge/codes.py:75). A read-only filesystem does not prevent retrieval.

   **Change:** Build an allowlisted runtime filesystem with fresh per-job home/configuration and only permitted libraries/data. Exclude constructor implementations from Zero-agent access; expose a narrow verification interface. Include schemas, CLI help, tracebacks, plugins, MCP tools, memories, credentials, and inherited descriptors in the audit. Keep reusable provider credentials outside model-controlled tools.

6. **[major] The ZK profiles are useful disclosures, not semantic guarantees.** §0, [lines 39–50](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:39); §3, lines 143–164; §9, lines 464–467. A linter cannot recognize every construction recipe or answer encoded without family names. `P-min × G-struct` is also ambiguous when the constructor interface exposes named families; Prior introduces a grammar union outside the stated two-valued grammar axis.

   **Change:** Define grammar as a versioned capability manifest and enumerate valid profile combinations. Describe linting as syntactic screening. Freeze minimal-profile assets before held-out generation. Clarify that A1 functional inputs are disclosed task inputs, while solution-bearing requirements are interventions. Remove the AlphaZero analogy; retain the explicit pretrained-knowledge qualification.

7. **[major] Known-code matching does not identify reproduction or contamination.** §3, [lines 179–180](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:179); §9, lines 454–462; §10, lines 530–531. Independent rediscovery can match the database; memorized constructions can escape it. An incomplete equivalence search cannot establish “not equivalent to any entry.”

   **Change:** Report `database_match`, `certified_nonmatch_under_relation_R`, or `audit_inconclusive`, with DB coverage and search budgets. Reserve “reproduction” for provenance-supported reproduction arms. Report detected retrieval violations separately from database matches; pretrained contamination remains unidentifiable. This follows [qecforge’s autonomy qualification](/data/haiyangw/claude/auto-code/ref-code/qecforge/paper/sections/01_introduction.tex).

8. **[blocker] Tanner-graph canonicalization is not stabilizer-code canonicalization.** §5, [lines 289–292](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:289); M2, line 568. Generator changes can preserve a code while changing its Tanner graph. For example, \(H_X=\{1100,0011\}\) and \(H'_X=\{1100,1111\}\), both with \(H_Z=\{1111\}\), define the same \([[4,1]]\) CSS code but different check-degree multisets. I checked this example in memory.

   **Change:** Call the proposed key a **colored check-presentation equivalence key**. Separately define generator, qubit-permutation, local-Clifford, and logical-basis equivalence; require checked transformation witnesses. Cheap invariants only filter comparisons. Budget exhaustion yields `unknown`. See [qecforge Appendix B, code equivalence](/data/haiyangw/claude/auto-code/ref-code/qecforge/paper/appendices/B_interfaces.tex:40).

9. **[major] Dedupe and dominance can delete the best implementation.** §7, [lines 392–394](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:392). Equivalent codes can have different measured generators, logical representatives, placements, schedules, and hardware costs. Algebraic dominance before L3/L4 does not imply application dominance.

   **Change:** “Deduplicate algebraic verification work while retaining distinct implementation variants. Treat proxy dominance as a scheduling heuristic, not permanent rejection. Hard pruning requires a domain-compatible dominance certificate or hard-constraint violation.” This is explicitly required by [qecforge Appendix B](/data/haiyangw/claude/auto-code/ref-code/qecforge/paper/appendices/B_interfaces.tex:40) and its [proxy-utility interaction](/data/haiyangw/claude/auto-code/ref-code/qecforge/docs/ASSUMPTIONS_AND_INTERACTIONS.md:31).

10. **[major] MITM scaling is described by the wrong quantity.** §8, [lines 438–441](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:438). “Small \(n\,t\)” is misleading. The reference method stores
    \[
    M=\sum_{j=0}^{t}\binom nj
    \]
    half-patterns and performs within-bucket pair work proportional to \(\sum_s M_s^2\), with implementation-dependent arithmetic costs. \(M(100,4)=4{,}087{,}976\); \(M(144,6)=11{,}642{,}049{,}421\).

    **Replace with:** “Preflight combinatorial pattern counts, memory estimates, and pair budgets. Publish measured feasibility for this implementation/host; do not use a universal \(n,d\) cutoff.” [qecforge ALGORITHMS already gives the correct scaling](/data/haiyangw/claude/auto-code/ref-code/qecforge/docs/ALGORITHMS.md:23).

11. **[major] Distance admission needs an explicit completeness contract.** §5, [lines 279–282](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:279); §8, lines 431–432. L2a’s “no witness below target” permits further investigation, never certification. Parallelization and symmetry reduction introduce additional completeness obligations; “tested proof obligation” is not a discharged proof.

    **Change:** Require complete logical bases, both CSS sectors, all required collision pairs—including cross-shard pairs—and an explicit completed-range record. Exclusion through \(2t\) proves \(d\ge2t+1\); a checked logical of weight \(w\) proves \(d\le w\). SAT lower bounds additionally require a validated original-to-SAT encoding and independently checked UNSAT proof. Target-distance admission requires the certified lower bound to meet the target. [qecforge Appendix A](/data/haiyangw/claude/auto-code/ref-code/qecforge/paper/appendices/A_proofs.tex:8).

12. **[minor] The BB triples are correct, but the acceptance test needs fixtures.** M2, [lines 563–564](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:563). All four triples match [Bravyi et al., Table 3](https://arxiv.org/pdf/2308.07915). I recomputed their \(n,k\) and weight-six checks from the listed polynomials; I did not independently certify their exact distances.

    **Change:** Specify source/version, torus parameters, polynomials, coordinate convention, matrix hashes, witness artifacts, and required lower-bound budgets. Test witness validation deterministically. Benchmark randomized witness discovery separately; do not make a stochastic search success an unspecified milestone requirement.

13. **[major] L3 conflates structural tests, existence searches, and gate verification.** §5, [lines 283–284](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:283); §8, line 433. Tanner automorphisms cover a restricted representation-preserving subgroup. Failure to find disjoint representatives in one basis does not prove none exist. “Fold” has no specified physical circuit or target action.

    **Change:** Separate witness validation from existence claims. Verify stabilizer signs and the induced action on all \(2k\) logical Pauli generators, including phases and basis conventions. Define each fold construction explicitly. Charge compensating Cliffords and transform workload/state/observable coordinates after basis changes. Preserve the kernel’s restricted scope rather than promoting it to general Clifford verification. [qecforge §4, gate requirements](/data/haiyangw/claude/auto-code/ref-code/qecforge/paper/sections/04_codes.tex:21).

14. **[major] Correct bounds need executable applicability conditions.** §7, [lines 383–384](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:383); §9, lines 476–478. Singleton and the stated disjoint-support inequality are correct. However, “nondegenerate” cannot be an unchecked flag, and a route-specific obstruction must not become task impossibility.

    **Change:** Register theorem preconditions and scope. For S-A, Singleton excludes the whole tuple when \(n_{\max}-k_{\min}<2(d_{\min}-1)\). The nondegenerate Hamming counting bound is \(\sum_{j=0}^{\lfloor(d-1)/2\rfloor}3^j\binom nj\le2^{n-k}\); passing it proves no existence. LP infeasibility needs a checked certificate. Disjoint-support bounds concern the specified full same-type set; DSATUR gives a feasible coloring, not optimal batching. Transversal no-go results must retain their fixed-code/error-detection assumptions and must not reject injection or code switching. [Gottesman, Chapter 7](https://arxiv.org/pdf/quant-ph/9705052), [Eastin–Knill](https://arxiv.org/abs/0811.4262).

15. **[major] M5 does not establish circuit semantics.** §11, [lines 592–598](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:592); §10, line 509. Zero noiseless detector flips can hold for empty circuits or an incorrectly annotated implementation. Random initial syndrome outcomes need not themselves be deterministic. Mixed-sector schedules are not inherently invalid.

    **Change:** Check expected detector/observable counts, signed logical input-output flows, actual stabilizer measurement action, boundaries, and directed fault propagation against an independent small simulator. Both bases must use the declared schedule or explicitly separate domains; dualizing and regenerating a circuit can change ordering. Replace the negative control with a **demonstrably invalid** interleaving. [qecforge G1](/data/haiyangw/claude/auto-code/ref-code/qecforge/docs/EXPERIMENTS.md:11).

16. **[major] “BP+OSD runs” and “hyperedges preserved” are insufficient decoder tests.** §5, [lines 284–286](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:284); M5, lines 593–595. Syndrome satisfaction does not establish logical success. Graphlike components separated by `^` can remain correlated; the reference matching adapter checks components but does not by itself establish correlation handling.

    **Change:** Sample the declared original circuit; verify \(H_D\hat e=s\), and score \(L_D\hat e\) against actual sampled logical flips. Retain detector-free logical faults. Record decomposition/approximation flags and whether matching ignores or models correlations. Compare tiny cases with exhaustive decoding. Restrict Stim to supported stabilizer/noise semantics; unsupported analog/coherent models remain unavailable. [qecforge Appendix D](/data/haiyangw/claude/auto-code/ref-code/qecforge/paper/appendices/D_gadgets_decoding.tex:37).

17. **[major] Circuit-distance witnesses establish upper bounds only.** §8, [line 434](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:434); M5, line 597. A witness of \(w\) undetected physical faults proves \(d_{\mathrm{circ}}\le w\). It does not establish a lower bound, decoder performance, or fault tolerance. Graphlike decompositions can introduce edges that lack individual physical-fault realizations.

    **Change:** Define the fault alphabet, location-counting convention, rounds, boundaries, detector/logical maps, and distinction from minimum decoder-failing fault count. Replay every witness in the original circuit with physically compatible faults. Require exhaustive/proof-checked exclusion for lower bounds; heuristic failure remains unknown. [Stim’s search and graphlike-error documentation](https://raw.githubusercontent.com/quantumlib/Stim/main/doc/python_api_reference_vDev.md).

18. **[major] S-B’s statistical estimand is undefined and gameable.** §10, [lines 488–489](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:488). “Logical error per round per logical qubit” is not automatically an IID Bernoulli observation. Dividing end-of-memory block failures by \(kR\) permits dilution through longer runs or unused logical slots and ignores correlations/cancellation.

    **Replace with:** “Freeze memory length \(R\), active logical observables, initialization/readout, noise, decoder, and failure event. Measure per-shot block and specified-observable failure probabilities. Any per-round conversion requires an explicit validated model. Count all simultaneous data/ancilla resources.” Do not apply binomial intervals to pooled correlated logical bits or rounds. [qecforge REPRODUCIBILITY, statistical discipline](/data/haiyangw/claude/auto-code/ref-code/qecforge/docs/REPRODUCIBILITY.md:33).

19. **[major] Confirmation rules remain underspecified despite correct statistical principles.** §4.11, [lines 243–247](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:243); §7, lines 404–422; M5, lines 595–596. Disjoint seeds and producing CP intervals do not prevent repeated confirmation until success, selective reporting, or insufficient sample sizes.

    **Change:** Freeze each confirmation family, shot budget, alpha allocation, retry policy, and acceptance rule before observing confirmation data. Require the appropriate upper confidence bound to meet the target. Zero failures in 10,000 shots gives a two-sided 95% CP upper bound about \(3.69\times10^{-4}\), not zero. Adaptive extensions require a valid sequential procedure. Shared seeds across different circuits require an explicit coupling. [qecforge EXPERIMENTS, statistics](/data/haiyangw/claude/auto-code/ref-code/qecforge/docs/EXPERIMENTS.md:39).

20. **[major] Zero false authorization can be achieved by rejecting everything.** §10, [lines 503–522](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:503); M4, line 589; §15, lines 704–705. Several controls concern circuit/instrument/resource features unavailable at M4. Others could be rejected at schema validation without exercising the scientific verifier.

    **Change:** Give each control a target gate, expected semantic outcome, paired valid positive, and production-path injection method. Distinguish correct refutation, retained unknown, unsupported capability, and false authorization. Require positive capability tests. State “0/N observed false authorizations on these controls,” not a universal guarantee. This addresses the dormant-validator failure in [the Chandra analysis §7.1](/data/haiyangw/claude/auto-code/notes/ref-analysis/chandra.md:312).

21. **[major] N-version authoring conflicts with the frozen-evaluator rule.** §5, [lines 287–288](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:287); M2, lines 566–567; §12, lines 626–627. “Inside campaigns” Codex authors N-version verifiers, while the evaluator must already be frozen. Agreement on 10,000 instances also permits shared-helper or shared-convention bugs.

    **Change:** Author and freeze both implementations before campaigns, in development environments that enforce the claimed blindness. Avoid shared scientific helpers; compare semantic outputs against independent exhaustive truth on tractable cases and targeted mutations. Specify test distributions and convention alignment. Any disagreement quarantines the result; model agreement does not resolve it.

22. **[major] Transaction, lease, and replay semantics are incomplete.** §4.2, [lines 197–203](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:197); §7, lines 411–418. Slow verification outside the lock creates stale-read and artifact-change races. Lease expiry alone does not stop an old worker from submitting after reassignment. An asynchronous scheduler is not reproducible merely because its code is deterministic.

    **Change:** Bind jobs to immutable input/domain/evaluator hashes and monotone lease generations. Revalidate the read set and generation at commit; reject stale completions. Persist request IDs, budget reservations, RNG/policy state, dispatch digests, and completion order. Atomically publish durable artifacts before their ledger references. Define replay over recorded events, and test crashes at each commit boundary.

23. **[major] A git snapshot does not freeze the execution environment or remove trusted components.** §4.3, [lines 207–215](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:207). Mutable dependencies, import paths, CLI configuration, solver binaries, and model settings remain outside the stated freeze. Hash chains and SQLite triggers do not protect against their authorized writer rewriting everything.

    **Change:** Pin and hash the runtime/dependency closure, solver/checker binaries, CLI versions/settings, and resolved model metadata where available. Run from controlled imports and read-only evaluator assets. Declare orchestrator, verifiers, and isolation machinery as trusted. Human overrides may alter scheduling or create a new campaign; they cannot convert unsupported science to `verified`. [qecforge SECURITY](/data/haiyangw/claude/auto-code/ref-code/qecforge/docs/SECURITY.md).

24. **[major] Resource containment and untrusted-output handling need stronger limits.** §5, [lines 272–277](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:272); M3, lines 572–573. Per-process CPU/address-space limits do not bound an entire process tree; `RLIMIT_NPROC` is associated with the real user, not one campaign. Dimension checks do not prevent enormous JSON, output floods, or pre-output program allocations. [Linux resource-limit semantics](https://man7.org/linux/man-pages/man2/getrlimit.2.html).

    **Change:** Qualify aggregate CPU, memory, PID, disk/output, and wall-time containment on the actual host. Kill descendants reliably. Cap bytes/nesting/list lengths before expensive parsing and allocation. Accept only bounded regular-file/pipe outputs copied into trusted storage; reject symlinks and special files. A canary establishes its tested property, not general escape resistance.

25. **[major] Budget accounting can be bypassed or overspent.** §4.7–12, [lines 229–251](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:229); §5, lines 299–301; §13, line 663. Agents can perform unrecorded screening or launch nested model calls unless interfaces enforce accounting. Concurrent calls need reservations. “Everything charged” conflicts with backoff “without burning attempts” unless accounting units are distinguished.

    **Change:** Reserve worst-case call/job budgets atomically, reconcile actual usage, and reserve confirmation capacity. Broker all model and screening calls. Charge generated-program CPU even when it searches internally; outer proposals are not internal candidate counts. Separate transport retries, proposal attempts, wall time, and monetary-equivalent cost. Define subscription versus billed-cost accounting and conservative treatment of interrupted calls.

26. **[major] D9 repeats the prior workflows’ lost-artifact failure.** §5, [lines 322–323](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:322); D9, lines 685–686. Manifests, ledger exports, and reports can contain hashes whose matrices, circuits, proofs, prompts, and observations exist only under ignored `runs/`. A hash is not a recoverable artifact.

    **Change:** Archive the complete transitive artifact closure, including sanitized call records and executable environment provenance, in git or a durable immutable bundle. Treat 20 MB as a chunk size, never permission to omit evidence. Acceptance: reconstruct and audit an exported run without its working directory. [qecforge release checklist](/data/haiyangw/claude/auto-code/ref-code/qecforge/docs/EXPERIMENTS.md:53); [question-gen’s reproducibility failure](/data/haiyangw/claude/auto-code/notes/ref-analysis/question-gen.md:190).

27. **[major] The progress and three-strikes rules can stop useful science prematurely.** §4.14, [lines 254–255](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:254); §7, lines 402–423. Closing a correctness blocker, strengthening a lower bound, or certifying an obstruction may not improve the objective. Conversely, agent-selected “approach” names permit renaming to evade strikes.

    **Change:** Keep objective improvement as the scientific success metric, but separately track bounded, verifier-established blocker/certificate progress for scheduling. Use orchestrator-owned approach IDs. Infrastructure failures consume resources but are not scientific refutations. Three failed attempts trigger a policy change, never a `NoGo`; a no-go requires an explicit scope and completeness certificate.

28. **[major] Held-out isolation is not specified across campaigns.** §5, [lines 289–309](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:289); §9, lines 469–473; §10, line 487. Global dedupe, archives, caches, resumed CLI threads, difficulty-calibration agents, and auditor reports can transmit information between Zero/Prior/B6 or dev/held-out runs. A vault mount restriction does not prevent those channels.

    **Change:** Namespace all state by campaign, arm, split, and profile. Prohibit post-hoc audit feedback until sealing; never reuse B6/librarian sessions in K0. Freeze calibration/split procedures before exposing held-out tasks. Keep solutions, diagnostic labels, hidden identifiers, and linter rejection details out of proposer feedback. Audit duplicate task tuples and relevant structural overlap, not seeds alone.

29. **[major] The comparison arms do not isolate the stated effects.** §10, [lines 493–501](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:493). Multi-agent versus single-model comparison confounds topology with model mixture. Zero versus Prior changes both prompting and grammar. “Random same grammar” is undefined over arbitrary Python programs.

    **Change:** Specify baseline initialization, sampling distributions, mutations, program limits, and accessible tools. Include strong single-agent controls for the model families used, or explicitly label the comparison as a bundled system comparison. Match confirmation, verifier, tool, and cache accounting. Separate prompt and grammar effects where claiming them; otherwise report only the combined intervention. [qecforge Appendix G, ablations](/data/haiyangw/claude/auto-code/ref-code/qecforge/paper/appendices/G_experiments.tex:42).

30. **[major] Requirement inference risks reproducing an expert answer key and cannot revise upstream choices.** §6, [line 343](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:343); §7, lines 379–386; M6, lines 601–603. Requirement abduction occurs once before the loop. Precision/recall against one annotated architecture would penalize valid alternative routes.

    **Change:** Make route/requirement revision a work item, with falsifiers and dependency invalidation while preserving the task. Gold annotations must distinguish necessary constraints, sufficient routes, and preferences, allow multiple routes, and record annotation uncertainty. Define the matching/scoring procedure; incomplete gold sets cannot measure universal recall. [qecforge Appendix G, requirement annotations](/data/haiyangw/claude/auto-code/ref-code/qecforge/paper/appendices/G_experiments.tex:38).

31. **[major] The evaluation roadmap drops central mission comparisons.** §10, [lines 484–501](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:484); M7, lines 604–609. M7 omits B2/B3, although they test application awareness and inferred versus human requirements. Hardware variation, robustness, transfer, and the specified ablation matrix have no concrete acceptance gate. Running B2/B3 on fixed S-A tuples does not test H1/H2.

    **Change:** Map every qecforge hypothesis and gate to an implemented experiment or explicit deferred milestone. Restore B2/B3 on S-C, plus hardware/robustness/transfer evaluation and named ablations when their components exist. State that the first S-A/S-B pilot cannot support H1/H2 or application advantage. [qecforge EXPERIMENTS](/data/haiyangw/claude/auto-code/ref-code/qecforge/docs/EXPERIMENTS.md:3).

32. **[major] The “early vertical slice” arrives after too much infrastructure.** §11, [lines 552–581](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:552); D4–D5, lines 676–679. Full ledger machinery, several grammars, equivalence, parallel distance, and N-version verification precede the first live call. The first smoke also depends on a progress baseline whose implementation is scheduled later. Five headline arms × 5–10 seeds already means 25–50 campaign replicates per task, before controls and ablations; affordability is unmeasured.

    **Change:** Start with host qualification, minimal contracts/store/admission, small exact verification, a baseline, and one live end-to-end task. Defer broad grammars, parallel MITM, general equivalence, QD/bandit machinery, sessions, and extra roles until measured need. Cost the pilot from smoke telemetry before committing its matrix. This addresses [qa’s infrastructure-before-validation failure](/data/haiyangw/claude/auto-code/notes/ref-analysis/qa.md:287).

33. **[minor] Proposal fields and grammar scope need disambiguation.** §4.1, [lines 193–194](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:193); §5, lines 273–275 and 328. Rejecting proposer-supplied `n` or “logical action” can reject necessary sparse-matrix dimensions or requested target actions. `CSSCode` also makes G-open a CSS grammar, not unrestricted stabilizer discovery; a two-block group-algebra constructor does not cover arbitrary quasi-cyclic CSS constructions.

    **Change:** Distinguish construction parameters, requested predicates, untrusted witnesses, and verifier-owned observations. Specify sparse encoding/duplicate-entry conventions. Declare the initial binary-CSS/sign scope and exact algebra of each named constructor; unsupported code classes remain explicitly unsupported.

34. **[major] D1–D9 need qualified defaults.** §14, [lines 668–686](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:668). Recommended decision text:

    | Decision | Position and concrete qualification |
    |---|---|
    | **D1 `zkqec`** | Agree as an internal name; describe operational autonomy explicitly and disclaim cryptographic meaning. |
    | **D2 CLI runtime/models** | Conditional. Qualify isolation, authentication, telemetry, and costs together first. Role/model assignments are pilot choices, not validated necessities. |
    | **D3 Zero/Prior** | Agree as disclosed experimental profiles after fixing leakage and profile semantics; make no absence-of-prior-knowledge claim. |
    | **D4 staged scope** | Prefer the staged option. Run an S-A pilot before expanding to M5/S-B; keep application completion separate. |
    | **D5 caps** | Agree with hard caps and separate held-out authorization. Treat $5/$150 as spending ceilings, not feasibility estimates; identify whether development/reviews are included. |
    | **D6 vendoring** | Agree. Pin the source commit, license and hashes; record any tested patch as a new evaluator version. |
    | **D7 branch** | Agree. Merge only when requested, as stated. |
    | **D8 autonomy** | Agree for implementation. Unresolved scientific/containment blockers must stop milestone advancement; “triaged” alone is insufficient. |
    | **D9 results** | Change to complete artifact retention and reconstruction testing, as in finding 26. |

35. **[major] Several scientific decisions are missing from the approval record.** §14, [lines 666–686](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:666). Names, models, and git policy are specified more concretely than the experiment.

    **Add decisions for:** initial supported code/noise/hardware domain and size caps; the first run’s permitted scientific claims; memory-event/horizon definition; equivalence relation and known-DB coverage; primary comparison and stopping/confirmation protocol; aggregate CPU/RAM/disk/wall budgets; accepted trust boundary and credential mediation; and durable artifact location. Freeze these before the corresponding campaign, with conservative defaults stated explicitly.

**Top five changes required before building**

- Replace the sandbox assertion with an enforceable, tested launch contract covering egress, credentials, filesystem access, and tool accounting.
- Require typed claim entailment and meaningful positive/negative scientific acceptance tests.
- Correct equivalence/novelty semantics and stop pruning implementation variants using algebraic proxies.
- Give L5/L6 and application closure explicit positive milestones, or clearly defer those capabilities and claims.
- Shrink the first vertical slice; then freeze an affordable, matched pilot with independent confirmation and complete artifact retention.