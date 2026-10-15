# Build note: grammar, identity and verifiers L0–L3 (Phase 1a / M1)

**Built.**
- `zkqec.grammar`: the L0 raw-JSON caps, the families `explicit_sparse` and `two_block_group_algebra` (each with a `CAPABILITY_MANIFEST`), and `build`.
- `zkqec.identity`: three identity keys.
- `zkqec.verify`: L1 `css_algebra`, L2 `distance` plus `logical_witness`, and L3 `logical_action`, which includes an exact signed-Pauli algebra.
- Fixtures in `tests/verify/fixtures/`:
  - `bb72_weight6_logicals.json`: one weight-6 logical per sector, found once at dev time by complete kernel enumeration;
  - `sf_positive_chain_shift.json`: the deterministic positive S-F case.

**Public API.**
- **Grammar.**
  - `build(proposal, GrammarCaps) -> (Presentation, hx, hz)`. It raises `MalformedProposal` (outcome `malformed_proposal`), or `UnsupportedFamily` for `program`.
  - Also: `check_raw_json(text, max_bytes, max_depth, max_list_len[, max_token_len])`, `parse_untrusted_json`, `MANIFESTS`, `to_dense`/`from_dense`, `reduce_polynomial`.
- **Identity.** `presentation_sha(Presentation)`, `code_sha(hx, hz)` (RREF row bases plus n), `presentation_key(hx, hz)` (pynauty certificate, tagged with the pynauty version).
- **Verifiers.** All are listed in `zkqec.verify.REGISTRY`, version `1.0.0` each, and every one returns `list[VerifierResult]`.
  - `zkqec.verify.css_algebra`: `analyze`, `css_valid`, `block_length_eq`, `dimension_eq` (subject = code), `max_check_weight_eq`, `max_qubit_degree_eq`, `canonical_basis -> LogicalBasis`.
  - `zkqec.verify.distance`: `certify_distance(hx, hz, t, artifact_sink=, budget=DistanceBudget(), sectors=)`. Each completed sector yields a lower bound and an upper bound. Upper bounds cite a re-verified witness; both cite a `distance_completed_range_v1` record holding t, half_patterns, pairs_checked, w_star and completion. A refusal, budget exhaustion, timeout or crash yields only `unknown` on `distance_lower_bound(s, 2t+1)`, plus the record.
  - `zkqec.verify.logical_witness`: `validate_logical_witness(hx, hz, sector, v, artifact_sink=)`.
  - `zkqec.verify.logical_action`: `implements(presentation, basis, op, target, logical_qubits, artifact_sink=)`. The subject is `content_hash(OperationRef)`. A refuted result cites a `logical_action_tableau_v1` artifact. `compute_logical_action` is also public.
- **Refusals** have status `unknown` and carry `detail.outcome` ∈ {`malformed_proposal`, `premise_refuted`, `unsupported`, `budget_exhausted`, `timeout`, `crashed`}.

**Tests.** `130 passed in 12.45s` (`.venv/bin/python -m pytest -q tests/grammar tests/verify tests/identity`). No test needs `slow`. Full repo suite: `379 passed`. Covered:
- **Reference table:** `[[28,4,5]]`, `[[42,6,6]]`, `[[56,8,6]]` at t=3.
- **Steane at t=2:** lower = upper = 3.
- **"ℓ=2 declared d=6" control:** a verified upper bound of 5, and no lower bound ≥ 6.
- **Other controls:** real-field rank, torus wrap-cancellation, budget (preflight M, memory, pair-work lower bound, kernel pair cap mid-run), and timeout → unknown.
- **Code sizes:** n and k for BB `[[72,12]]` and `[[144,12]]`.
- **BB72 witnesses:** both validate with upper bound 6. Complete t=3 also certifies d_X = d_Z = 6.
- **L3 controls:** S vs S†, wrong permutation (both a non-automorphism and a wrong claimed action), sign flip, incomplete, unpaired or foreign basis, phase-convention mismatch.
- **Dense oracle:** about 2.3k random cases plus chain(1,7) (n=14). L3 always agrees with V†UV.
- **Identity:** the `[[4,1]]` pair; agreement with networkx colored isomorphism.

**Findings.**
1. **S vs S† (qecforge §4) is confirmed, but basis-specific.** In the common block basis of chain(3,7), physical S^⊗42 implements logical S† on all six qubits, and S† implements S. The dense oracle independently confirms the same on Steane and chain(1,7) (weight-7 logicals, 7 ≡ 3 mod 4). In the kernel's generic canonical basis, global S still preserves the code but acts non-diagonally (e.g. X̄₁ → X̄₁Z̄₂Z̄₃Z̄₄).
2. **chain_shift(3,7) implements the logical permutation (0 1 2)(3 4 5), with all signs +.** This is the positive S-F fixture, and kernel `permutation_action` agrees.
3. **The memory estimate is 1.25–1.3× the tracemalloc peak**, measured on BB72 and BB144 at t=3.
4. **[[72,12]] d=6 is certified exactly in 0.1 s per sector.** d=12 for [[144,12]] needs t=6 (M ≈ 1.16·10¹⁰), and preflight refuses it.

**Ambiguities and resolutions.**
- **Clifford names:** `HS` means the matrix product H·S (S acts first).
- **Local Clifford indices** refer to qubit positions *after* the permutation.
- **Qubit degree** counts X-checks plus Z-checks.
- **Sector `both`** is left to the resolver.
- **Invalid witnesses** give `unknown`, never `refuted`.
- **Two-block monomials:** any integer exponents are accepted; duplicate monomials cancel, as the declared algebra says. (`explicit_sparse` rejects duplicates instead.)
- **Preflight pair work** is a provable lower bound, used only for early refusal. Exact enforcement is the kernel's `max_pairs`.
- **`max_seconds`** uses SIGALRM and works in the main thread only. The deterministic budgets are primary.
- **w\***: I derive it from the kernel's `upper_bound` (w* = best if best ≤ 2t). This is sound because a complete run finds every logical of weight ≤ 2t. I cross-check it against the kernel's `lower_bound`.

**Requested contract changes.**
1. Pin the Clifford-name and local-index conventions in `contracts/design.py`.
2. Add a typed `outcome` field to `VerifierResult` for refusals, instead of using `detail.outcome`.
3. Export `ONE_QUBIT_CLIFFORDS` and `MAX_DIM` from `zkqec.contracts`.

**Known gaps.**
- No twins (M2).
- No sharded or parallel enumeration.
- No folds.
- Untrusted witnesses (basis, operation, target) are not parsed from `Proposal.witnesses`; that needs admission adapters.
- The memory model is specific to CPython 3.10.
- Every result is labelled `single-implementation`.
