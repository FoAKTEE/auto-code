**Verdict: with changes — v0.3 is not ready for owner approval as written.**  
The algebra-only Phase 1 is coherent in principle; L1/L2 and signed L3 can support those restricted claims.  
Several accepted fixes remain incomplete, and the launch specification does not establish K0.  
The Hamming counting formula is correct for nondegenerate binary codes. [Reference](https://arxiv.org/html/2306.00048v1)  
M0–M4 is buildable after narrowing; I would initially authorize a reduced M0–M1.  
Read-only review; mathematical counterexamples were checked in memory. I did not rerun host qualification.

## Triage verification

Below, **00** means `prompt/00-master-prompt.md`; **01** means `prompt/01-technical-contracts.md`. “Verified” concerns the revised specification, not an implementation.

- **CR-2 / CR-33 — partial.** [01 §B1–B3](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:99): “Every other component is explicitly `absent/unsupported`.” The candidate tuple has no explicit Phase-1 operation-witness field while G is apparently inactive. Sparse encoding specifies coordinate pairs but not mandatory matrix shapes; rejecting proposer-supplied `n` leaves isolated columns ambiguous. **Fix:** define operation references, explicit `(rows, columns)` construction fields, and invalidation for code/operation edits.

- **CR-3 — enforcement remains partial.** [01 §A5](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:90): the linter checks scientific statements “in non-prose sections.” A reporter can put unsupported conclusions in a supposedly non-scientific prose section. **Fix:** use controlled templates for the authoritative report; clearly separate unverified commentary. A section label cannot mechanically certify that prose contains no scientific claim.

- **CR-4 / CR-5 — blocker remains.** [00 §2](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:149): “Web search is enabled only by `--search`.” This is wrong: cached web search is enabled by default. [01 §J2–J3](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:410) also substitutes output schemas and ephemeral execution for an actual tool-disable configuration. Those options do not disable shell execution. Credentials in the fresh home consequently remain exposed if tools survive. **Fix:** explicitly disable retrieval and all execution/delegation/app tools, verify the effective tool manifest, and fail closed on any unexpected tool. [Official configuration documentation](https://learn.chatgpt.com/docs/config-file/config-basic)

- **CR-8 — partial.** [01 §D2](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:196): “weight distributions of the given generators, and WL hashes” filter comparisons. These are presentation features, not invariants of stabilizer equivalence. **Fix:** permit them to prioritize comparisons, never exclude pairs or establish nonmatch. Certification must cover every relevant DB entry under the declared relation.

- **CR-11 — missing essential premise.** [01 §C2](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:147): the checklist requires “every same-syndrome pair examined,” but never requires exclusion of nontrivial logicals through weight \(2t\). Steane completes both sectors at \(t=2\) and has distance **3**, not 5; I reproduced this. **Fix:** record the minimum nontrivial XOR weight found, and authorize \(d\ge2t+1\) only when none exists through \(2t\).

- **CR-14 — incorrect fix.** [01 §E](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:227): “S-A tuple is incompatible **iff** …”. Singleton violation is sufficient for exclusion, not necessary. For example, positive \(k\), \(d_{\min}=2\), and weight-one checks are impossible even when Singleton passes. **Fix:** say “excluded by Singleton if”; passing leaves existence unresolved. The Hamming formula itself needs no correction.

- **CR-17 — partial.** [01 §G](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:302): “A witness of w physical faults … proves `d_circ ≤ w`” omits **zero detector syndrome and nonzero logical effect**. An arbitrary detectable fault is not such a witness. Also, “never a general bound” overstates the restriction on graphlike search: a physically replayed witness remains a valid upper bound. **Fix:** require \(Df=0,\ Lf\ne0\), physical compatibility, and explicit counting.

- **CR-21 — internally contradictory.** [01 §F](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:258) requires twins “before any campaign,” but [00 M1–M2](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:501) runs a campaign before building them. Moreover, “small-n distance” twins cannot automatically satisfy §7’s re-verification of **every** algebraic proposition at the larger allowed sizes. **Fix:** move the required twins earlier or explicitly limit pilot claims; publish a confirmation-method/range matrix.

- **CR-22 — durability fix incomplete.** [01 §L](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:477): “write, fsync, rename” omits syncing the destination directory. A crash can preserve the ledger reference while losing the renamed directory entry. **Fix:** specify file fsync, same-filesystem atomic publication, directory fsync, then durable DB commit; test the intervening failure points. [Linux fsync documentation](https://man7.org/linux/man-pages/man2/fsync.2.html)

- **CR-24 — aggregate containment incomplete.** [01 §J4](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:427): per-job scopes do not enforce [D14’s](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:696) campaign-wide limits. MemoryMax/CPUQuota/TasksMax also do not impose a scratch-disk quota. **Fix:** add a campaign parent cgroup/slice, bounded scratch storage and aggregate output accounting, including logs and abandoned jobs.

- **CR-25 — reservation is specified without an enforceable maximum.** [01 §K](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:455): “reserves its worst-case budget” does not identify enforced token, internal-turn, request, or retry ceilings. “No agent-side tools” does not prevent CLI-internal retries or additional requests. **Fix:** bind reservations to enforceable request limits, retain uncertain charges, and account for every provider request within a CLI invocation.

- **CR-26 — reconstruction requirement remains underspecified.** [01 §M](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:495) lists “environment provenance,” then requires rerunning every exact verifier. Hashes and version labels do not supply executable bytes. **Fix:** explicitly archive the verifier/runtime/dependency closure and test reconstruction without the repository, original environment, or network.

- **CR-29 — comparison controls still incomplete.** [00 §10](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:435): B4-C/B4-X are “token- and tool-matched,” but their prompt profiles and grammars are unspecified. [§4.12](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:257) also demands equal proposals **and** equal total cost, which generally require separate analyses. **Fix:** publish the arm capability matrix and distinguish fixed-proposal from fixed-budget experiments; define how CPU and monetary costs are compared.

- **CR-30 — claimed annotation fix is missing.** [00 Phase 3](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:595) promises only “annotations that allow multiple routes.” The triage’s claimed necessary/sufficient/preference distinction is absent, as are annotation uncertainty and matching/scoring rules. **Fix:** add these explicitly; do not measure universal recall against incomplete gold annotations.

- **IA-1 — S-F does not consistently establish A1.** [00 §10](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:423) prescribes “implemented by a physical qubit permutation,” “transversal H,” and “transversal S.” Those inputs already select implementation routes. The rotation example establishes only a structural prerequisite. **Fix:** distinguish route-constrained structural benchmarks from functional tasks with agent-selected routes; exclude unsupported rotation functionality from verified functional feasibility.

- **IA-12 — coverage assertion is not traceable.** [01 §I](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:371): “each covered by at least one control or test.” No mapping establishes this, particularly for task mutation, the six-qubit instrument counterexample, optional stopping, and retry lineage. **Fix:** provide the 15-row mapping to phase, production injection, expected outcome, and paired positive.

- **CR-12 / CR-35 — minor inconsistencies.** [00 §2](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:129) includes a 288-qubit fixture, whereas D10 caps constructors at 200; explicitly exempt trusted fixtures or trim the list. [TRIAGE CR-35](/data/haiyangw/claude/auto-code/prompt/reviews/TRIAGE.md:55) says “D10–D17 added,” but §14 ends at D16; correct the index or name the deferred memory decision.

**All others verified as planning commitments, with the stated phase deferrals.** Further problems introduced by the revised contracts follow.

## New findings

1. **[blocker] CONNECT destination filtering cannot establish the promised API policy.**  
   **Location:** [01 §J1](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:379), “only the model-API hosts.”  
   The namespace–Unix-socket bridge is plausible, but CONNECT forwards an opaque tunnel. Its connection log cannot establish which API operations, hosted retrieval tools, or billable requests occurred. Multiple requests can share one connection. [CONNECT semantics](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.3.6)  
   **Fix:** add application-level request enforcement/accounting, or qualify an immutable CLI configuration with equivalent request-level evidence. Explicitly include the CLI and provider tool semantics in the K0 trust boundary. Missing evidence must prevent the K0 label.

2. **[major] The disjoint-representative theorem uses the wrong sector.**  
   **Location:** [01 §E](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:230), “same-type representatives” followed by \(n\ge\sum wt\ge k d_Z\).  
   This is false for X representatives. An in-memory CSS example with \(H_Z=0\) and \(H_X\) a parity-check matrix of the classical \([7,3,4]\) simplex code has \((n,k,d_X,d_Z)=(7,3,1,4)\) and three disjoint unit-weight X logicals: \(7<3d_Z=12\).  
   **Fix:** restrict the statement to Z representatives or use \(d_{\text{sector}}\).

3. **[major] The proposition registry cannot express the promised report and lifecycle semantics.**  
   **Location:** [01 §A1–A3](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:23), “closed, versioned registry” and “verified receipts.”  
   There are no explicit predicates for block length, scoped NoGo, negative conclusions, or campaign comparison statistics. Nor are guarantee propagation, revocation, contradictory receipts, or consistency between duplicated `args/value/direction` fields defined.  
   **Fix:** supply typed signatures and derivation rules for the actual claims; preserve numerical/statistical guarantees, reject inconsistent fields, and derive only from currently authorized receipts. Historical `verified` must not survive revocation as current authority.

4. **[major] Sharing verification by mathematical identity can transfer false presentation claims.**  
   **Location:** [01 §D3](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:200), identity used “to share verification work”; §A1 attaches check weights/degrees to “code.”  
   With \(H_Z=\{000011\}\), the equivalent X presentations \(\{110000,001100\}\) and \(\{110000,111100\}\) have maximum check weights **2 versus 4** and degrees **1 versus 2**. I checked this.  
   **Fix:** key these predicates by check presentation. Define predicate-specific equivalence bridges: transport witnesses through permutations, exchange sector labels under X/Z swap, and transform logical actions through basis changes.

5. **[blocker] Filesystem/network isolation does not establish process isolation.**  
   **Location:** [01 §J2–J4](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:393), “empty root” and “no network.”  
   The contract never requires a PID namespace, environment allowlist, descriptor policy, or exclusion of host control sockets. Cgroups limit resources; they do not by themselves prevent same-user signaling of outside processes. Bubblewrap protection depends on its exact arguments. [Bubblewrap security model](https://github.com/containers/bubblewrap/blob/main/README.md)  
   **Fix:** freeze complete launch profiles, including PID isolation and sanitized environment/descriptors. Test signaling against a sacrificial outside process, inherited secrets, control-socket access, and descendant survival. I did not perform destructive probes.

6. **[major] Lease fencing protects admission, not execution or spending.**  
   **Location:** [01 §L](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:466), “completion from a stale generation is rejected.”  
   The stale worker can continue consuming resources while its replacement runs. The specified admission transaction also omits job settlement and reservation reconciliation. A crash after provider dispatch but before recording completion is an external-side-effect ambiguity.  
   **Fix:** define a durable dispatch/uncertain/settled state machine, cancel or retain reservations for old executions, reserve every retry separately, and atomically settle admission/job/accounting state. Test duplicate completions within the same generation too.

7. **[major] Export-time scrubbing can invalidate the evidence chain.**  
   **Location:** [01 §M](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:498), “raw model responses,” “re-verifies every hash,” and “Secrets are scrubbed.”  
   Redacting already-hashed artifacts changes their hashes; retaining originals defeats the scrub.  
   **Fix:** sanitize before content addressing and admission, or define a separate protected-original/redacted-public artifact scheme with explicit provenance. Test a deliberately secret-bearing response through sealing and reconstruction.

8. **[major] Phase-1 statistical claims are incorrectly described as absent.**  
   **Location:** [00 §7](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:376), “Phase 1 has no sampling claims”; [§10](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:446) requires feasible fractions and campaign uncertainty.  
   Random tasks and stochastic agents create sampling claims even without physical-noise shots. §H’s failure-probability upper-bound rule does not specify inference for differences in success fractions or censored time-to-success.  
   **Fix:** say “no physical-noise sampling”; freeze task/suite weights, paired contrasts, task clustering, censoring treatment, and multiplicity. D13 needs this analysis decision.

9. **[major] H3’s Phase-1 ablation lacks a meaningful oracle and skeptic estimand.**  
   **Location:** [00 §10](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:469), “ablate skeptic/domains, measure k/N.”  
   With complete deterministic certificates, removing the skeptic should not change authorization correctness. Conversely, “domains off” deliberately weakens admission and must not create authoritative scientific receipts.  
   **Fix:** run weakened policies in a separate experimental ledger scored by the intact resolver. Add wrong-subject/stale-basis controls. Measure skeptic benefit through useful counterexamples, verification cost, or earlier rejection unless a genuine authorization mechanism is specified.

10. **[major] Mandatory B6 conflicts with the universal Phase-1 runtime.**  
    **Location:** [00 §3](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:175), K2 “open retrieval”; [M4](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:584), “(+ B6)”; [01 §J3](/data/haiyangw/claude/auto-code/prompt/01-technical-contracts.md:417), all Phase-1 agents have “no tools.”  
    No qualified retrieval path for B6 is defined.  
    **Fix:** defer B6, or define a separate source-supplied reproduction profile with frozen source bytes and logged exposure. Do not silently relax K0’s runtime.

11. **[major] Phase 1 remains oversized, and its positive end-to-end capability gate is weak.**  
    **Location:** [00 M1–M4](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:501), “minimal live vertical slice,” followed by broad verifier, sandbox, equivalence, role, DB, and eight-arm work.  
    The algebraic scope is sufficient **after the corrections above**, but the combined build is not a modest first run. M3’s “smoke … succeeds” also fails to distinguish operational completion from a verified S-F capability.  
    **Fix / D4 disagreement:** approve a smaller M0–M1 first. Start with explicit matrices, one constructor, tractable distance targets, one provider, a simple baseline, and one signed-Clifford target. Defer generated programs, folds, evolutionary/bandit machinery, broad DB equivalence searches, reporter/route roles, and B6. Require deterministic positive S-A and S-F cases through the production resolver; benchmark stochastic discovery separately. Set D10’s target range from measured certification costs.

12. **[minor] Owner decisions conflict with other binding passages.**  
    **Location:** [00 §14](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:685).  
    D11 forbids “advantage claims,” while §10 permits algebra-level H4 comparisons. D13 omits §7’s stagnation stop. D16 permits a disclosed unenforced run, while §15 requires enforced isolation for completion.  
    **Fix:** distinguish algebraic agent-performance comparisons from application advantage; select one stopping policy; state that a fallback run cannot satisfy the K0 definition of done.

## Top 5 changes before approval

1. Replace the assumed no-tools launch with a qualified, request-accounted runtime and complete process-isolation profiles.
2. Correct distance/circuit-distance premises, Singleton’s “iff,” and the sector-dependent disjoint-support bound.
3. Finish typed entailment, revocation, operation identity, and predicate-specific evidence reuse.
4. Close dispatch/reservation/crash semantics and make bundles reconstructable without breaking hashes during sanitization.
5. Reduce the first approved build and freeze meaningful S-F tasks, confirmation coverage, arm capabilities, and statistical estimands.