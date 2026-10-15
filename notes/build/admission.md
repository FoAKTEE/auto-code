# Build note — store + admission + ledger + resolver (Phase 1a / M1)

**Built.** `zkqec.store`: a sharded, content-addressed store (`root/ab/cd/<sha>`). It sanitizes before
hashing (§M) and records each redaction as a sibling artifact holding rule names and counts, never
the secret. Publication is durable (§L: temp file in the destination dir → fsync → `os.replace` →
fsync dir). Reads re-hash the bytes, refuse symlinks and non-regular files, and enforce a byte cap.
`zkqec.admission`: the single-writer SQLite ledger (WAL, foreign keys, an flock writer lock) with a hash chain and
append-only triggers. Its projections can only be written through an apply guard. The package also has the
verifier/identity registry, the admission service (§L transaction), and the resolver (§A3), which covers inference
rules, transport, conflict quarantine and S-A/S-F task derivations.

**Files.** `src/zkqec/store/{__init__,sanitize,artifacts}.py`;
`src/zkqec/admission/{__init__,evidence,ledger,registry,service,resolver}.py`;
`tests/store/test_store_{sanitize,artifacts}.py`;
`tests/admission/{conftest,admission_helpers,test_ledger,test_admission,test_resolver,test_conflicts,test_tasks}.py`.

**Public API.** `ArtifactStore(root).put_json/put_bytes/ingest_*/get_json/get_bytes/exists/verify/ensure_durable/redactions`.
`VerifierRegistry.register(name, version, fn)` / `.register_identity(...)`.
`Admission(db, store, registry).run_verifier(name, inputs, domain, idempotency_key=None)`, `.run_identity(name, presentation_sha, domain)`, `.revoke(id, reason)`, `.open_incident(cat, detail)`, `.verify()`, `.rebuild()`, `.resolver(domain)`.
`Resolver.status(prop)`, `.code_of(p)`, `.satisfies_task(cand, task)`, `.target_refuted(cand, task, req)`. Each returns a `Resolution` with its rule, premise ids, guarantee and per-requirement explanation.

**Tests.** 110 tests, positive and negative. Exact summary: `110 passed in 1.86s` (`.venv/bin/python -m pytest -q tests/store tests/admission`). The full repo suite also passes (377).

**Ambiguities and how they were resolved (conservative readings).**
1. Verifier inputs are `ArtifactInput(sha, data)` holding the hash-verified bytes admission read, not bare hashes. This pins the read set. The `zkqec.verify` functions take typed objects, so registering them needs thin adapters.
2. A presentation-subject result must name one of the execution's inputs; otherwise the whole batch is refused.
3. `satisfies_task` also requires a verified `css_valid(C)`. This premise is not in the brief's list.
4. S-F operations must also satisfy: `op.basis == candidate.L`, `LogicalBasis.presentation == candidate.C`, and `k_req` distinct logical qubits. The operation subject is `content_hash(OperationRef)`, which matches `zkqec.verify`.
5. Refuted receipts entail nothing beyond their own proposition. They do feed conflict detection. Derived-only predicates are never `refuted`. `target_refuted` uses only verified opposite bounds. `target_refuted("operation")` is always `unknown` in P1a (§F).
6. Identities are domain-scoped. A presentation mapped to two codes quarantines both and opens an incident.
7. Quarantine is per receipt. A later, independent consistent receipt on the same subject stands.
8. Receipt idempotency is keyed on `receipt_id`, which includes the execution id. An optional `idempotency_key` gives deterministic execution ids, so a replay returns the recorded outcome without re-running the verifier.
9. Malformed verifier output (a `model_construct` bypass, NaN, a non-list) closes the execution as `crashed`. Failed re-validation closes it as `refused` and raises `AdmissionRefused`; a changed artifact also opens an `artifact_integrity` incident.
10. Every ledger append is scanned for secrets and fails closed. Crash messages are redacted first.

**Requested contract changes.** None are blocking. Suggestion: `VerifierResult.detail: dict[str, Any]` lets NaN through, and pydantic's JSON dump silently turns it into `null`. Admission rejects it, but the contract could reject it too.

**Known gaps.**
- Truncating the chain tail together with the projections is undetectable without an external head anchor; the bundle manifest should record the head hash.
- `same_stabilizer_group`, permutation and X/Z-swap transport are not implemented (deliberately conservative; `equivalent_under` is P1b).
- The apply guard stops accidental and naive writes, not a compromised trusted writer (§O).
- Numerical and statistical guarantee propagation is only exercised through the pure resolver core, because no P1a signature allows a non-exact guarantee.
