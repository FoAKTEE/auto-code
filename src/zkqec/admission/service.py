"""The single-writer admission service (prompt §4.1–4.2; contracts §A2, §A3, §L, §M).

Flow of :meth:`Admission.run_verifier` (§L "admission in one transaction"):

1. read and hash-verify the input artifacts (the read set);
2. append ``execution_opened`` (issuer, version, inputs, domain) and commit — *before* running;
3. run the registered verifier **outside** the write lock; an exception or a malformed return value
   closes the execution as ``crashed`` (status unknown), never as a verdict;
4. ``BEGIN IMMEDIATE``: check the execution is still open (an identical repeat is a no-op, anything
   else is a duplicate completion), re-validate every referenced artifact (exists, regular file,
   hash, then fsync — so no event references an artifact that is not durably published), stamp each
   result into a :class:`Receipt` with issuer/version/execution/inputs/domain taken from the
   execution record, append the receipts and the closure, quarantine semantic conflicts, commit.

A failed re-validation appends a ``refused`` closure (plus an integrity incident when an artifact
changed or vanished) and raises :class:`AdmissionRefused`.
"""
from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from zkqec.contracts import Receipt, VerifierResult, canonical_json, content_hash
from zkqec.contracts.registry import REGISTRY_VERSION
from zkqec.store import (SANITIZER_VERSION, ArtifactNotFound, ArtifactStore, ArtifactTooLarge,
                         IntegrityError, SecretDetected, StoreError, check_sha)

from .evidence import current_identities, current_receipts, find_conflicts
from .ledger import SCHEMA_VERSION, Ledger, VerifyReport
from .registry import ArtifactInput, Registered, VerifierRegistry
from .resolver import Resolver

MAX_RESULTS = 10_000
MAX_MESSAGE = 500
_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
Outcome = Literal["admitted", "crashed", "refused"]


class AdmissionRefused(Exception):
    """Admission refused to write receipts; ``report`` describes the recorded refusal (if any)."""

    def __init__(self, message: str, report: "ExecutionReport | None" = None):
        super().__init__(message)
        self.report = report


class DuplicateCompletion(AdmissionRefused):
    """A second, different completion for an already closed execution (§L)."""


class RegistryMismatch(Exception):
    """The ledger was opened with a different issuer registry or schema (frozen evaluator, §4.3)."""


@dataclass(frozen=True)
class ExecutionReport:
    execution_id: str
    issuer: str
    issuer_version: str
    outcome: Outcome
    records: tuple[str, ...] = ()          # receipt ids (verifier) or identity ids (identity)
    quarantined: tuple[str, ...] = ()
    incidents: tuple[str, ...] = ()
    replayed: bool = False                 # True: already closed identically; nothing written
    detail: dict[str, Any] = field(default_factory=dict)


class Admission:
    """The only writer of the scientific event log."""

    def __init__(self, ledger_path, store: ArtifactStore, registry: VerifierRegistry):
        registry.freeze()
        self.store, self.registry = store, registry
        self.ledger = Ledger(ledger_path)
        try:
            self._declare()
        except BaseException:
            self.ledger.close()
            raise

    def close(self) -> None:
        self.ledger.close()

    def __enter__(self) -> "Admission":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _declare(self) -> None:
        manifest = {"schema": SCHEMA_VERSION, "registry_version": REGISTRY_VERSION,
                    "sanitizer": SANITIZER_VERSION, "issuers": self.registry.manifest()}
        first = self.ledger.first_event()
        if first is None:
            with self.ledger.transaction():
                self.ledger.append("ledger_opened", manifest)
        elif first.kind != "ledger_opened" or first.payload != manifest:
            raise RegistryMismatch("ledger was opened with a different issuer registry/schema")

    # -- verifiers -------------------------------------------------------------------------------
    def run_verifier(self, name: str, inputs: dict[str, str], domain: str, *,
                     idempotency_key: str | None = None) -> ExecutionReport:
        spec = self.registry.get(name, "verifier")
        loaded = self._load_inputs(inputs)
        ex = self._open(spec, inputs, domain, idempotency_key)
        done = self._replay(ex)
        if done is not None:
            return done
        try:
            raw = spec.fn(**loaded)
        except Exception as exc:                                    # noqa: BLE001
            return self._close_crashed(ex, type(exc).__name__, str(exc))
        try:
            results = _revalidate_results(raw)
        except Exception as exc:                                    # noqa: BLE001
            return self._close_crashed(ex, "malformed_result", str(exc))
        return self._complete(ex, results)

    def _complete(self, ex: dict, results: Iterable[VerifierResult]) -> ExecutionReport:
        items, refs, problems = [], set(ex["inputs"].values()), []
        inputs = set(ex["inputs"].values())
        for res in results:
            receipt = _stamp(ex, res)
            rid = content_hash(receipt)
            prop = receipt.proposition
            if prop.subject_type == "presentation" and prop.subject not in inputs:
                problems.append(f"{prop.predicate}: presentation subject {prop.subject} "
                                "is not an input of this execution")
            refs |= set(receipt.witnesses) | set(receipt.artifacts)
            payload = {"receipt_id": rid, "receipt": receipt.model_dump(mode="json")}
            if self.store.sanitizer.detect_obj(payload):
                problems.append(f"{prop.predicate}: secret-like content in the result")
            items.append(("receipt_admitted", rid, payload))
        return self._commit(ex, items, refs, problems)

    # -- identity --------------------------------------------------------------------------------
    def run_identity(self, name: str, presentation: str, domain: str, *,
                     idempotency_key: str | None = None) -> ExecutionReport:
        """Record ``presentation -> code`` as computed by a registered identity function.

        The mapping comes only from the function's return value and the issuer only from the
        execution record; there is no API that accepts a caller-supplied mapping.
        """
        spec = self.registry.get(name, "identity")
        inputs = {"presentation": presentation}
        loaded = self._load_inputs(inputs)
        ex = self._open(spec, inputs, domain, idempotency_key)
        done = self._replay(ex)
        if done is not None:
            return done
        try:
            code = spec.fn(**loaded)
        except Exception as exc:                                    # noqa: BLE001
            return self._close_crashed(ex, type(exc).__name__, str(exc))
        try:
            check_sha(code)
        except ValueError as exc:
            return self._close_crashed(ex, "malformed_result", str(exc))
        core = {"presentation": presentation, "code": code, "execution_id": ex["execution_id"]}
        iid = content_hash(core)
        payload = {**core, "identity_id": iid, "issuer": ex["issuer"],
                   "issuer_version": ex["issuer_version"], "domain": ex["domain"]}
        return self._commit(ex, [("identity_recorded", iid, payload)], set(inputs.values()), [])

    # -- revocation and incidents ----------------------------------------------------------------
    def revoke(self, target_id: str, reason: str) -> bool:
        """Revoke a receipt or identity record; its descendants lose authority. Idempotent."""
        check_sha(target_id)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError("revocation needs a non-empty reason of at most 2000 characters")
        if self.store.sanitizer.detect_obj(reason):
            raise SecretDetected("revocation reason contains secret-like content")
        with self.ledger.transaction():
            if not self.ledger.is_known_record(target_id):
                raise KeyError(f"no receipt or identity record {target_id}")
            if self.ledger.is_revoked(target_id):
                return False
            self.ledger.append("revoked", {"target_id": target_id, "reason": reason})
        return True

    def open_incident(self, category: str, detail: dict) -> str:
        if not isinstance(category, str) or not _CATEGORY_RE.match(category):
            raise ValueError(f"bad incident category {category!r}")
        with self.ledger.transaction():
            return self._incident(category, detail)

    def _incident(self, category: str, detail: dict) -> str:
        if self.store.sanitizer.detect_obj(detail):
            raise SecretDetected("incident detail contains secret-like content")
        iid = uuid.uuid4().hex
        self.ledger.append("incident_opened", {"incident_id": iid, "category": category,
                                               "detail": detail})
        return iid

    # -- reading, verification -------------------------------------------------------------------
    def resolver(self, domain: str) -> Resolver:
        return Resolver(domain, self.ledger.receipt_records(domain),
                        self.ledger.identity_records(domain), store=self.store)

    def verify(self) -> VerifyReport:
        """Ledger verify (chain, triggers, artifacts, projections) plus a consistency re-check."""
        report = self.ledger.verify(self.store)
        problems = list(report.problems)
        records = self.ledger.receipt_records()
        for domain in sorted({r.receipt.domain for r in records}):
            for c in find_conflicts(current_receipts(records, domain)):
                problems.append(f"unquarantined {c.category} in domain {domain}: {c.receipt_ids}")
        return VerifyReport(not problems, report.events, tuple(problems))

    def rebuild(self) -> None:
        self.ledger.rebuild()

    # -- internals -------------------------------------------------------------------------------
    def _load_inputs(self, inputs: dict[str, str]) -> dict[str, ArtifactInput]:
        if not isinstance(inputs, dict) or not inputs:
            raise ValueError("an execution needs at least one input artifact")
        loaded = {}
        for name, sha in sorted(inputs.items()):
            if not (isinstance(name, str) and name.isidentifier()):
                raise ValueError(f"bad input name {name!r}")
            check_sha(sha)
            try:
                loaded[name] = ArtifactInput(sha, self.store.get_bytes(sha))
            except ArtifactNotFound:
                raise AdmissionRefused(f"input {name}={sha} is not published") from None
            except (IntegrityError, ArtifactTooLarge) as exc:
                with self.ledger.transaction():
                    self._incident("artifact_integrity", {"artifact": sha, "error": str(exc)[:200]})
                raise AdmissionRefused(f"input {name}={sha} failed verification: {exc}") from None
        return loaded

    def _open(self, spec: Registered, inputs: dict[str, str], domain: str,
              key: str | None) -> dict:
        check_sha(domain)
        inputs = dict(sorted(inputs.items()))
        if key is None:
            execution_id = uuid.uuid4().hex
        else:
            execution_id = content_hash({"issuer": spec.name, "version": spec.version,
                                         "inputs": inputs, "domain": domain, "key": str(key)})
        with self.ledger.transaction():
            if self.ledger.execution(execution_id) is None:
                for sha in inputs.values():
                    try:
                        self.store.ensure_durable(sha)
                    except (StoreError, OSError) as exc:
                        raise AdmissionRefused(f"input {sha} is not durably published: {exc}") \
                            from None
                self.ledger.append("execution_opened", {
                    "execution_id": execution_id, "role": spec.role, "issuer": spec.name,
                    "issuer_version": spec.version, "domain": domain, "inputs": inputs})
        return self.ledger.execution(execution_id)

    def _report(self, ex: dict, outcome: Outcome, **kw) -> ExecutionReport:
        return ExecutionReport(ex["execution_id"], ex["issuer"], ex["issuer_version"], outcome, **kw)

    def _replay(self, ex: dict) -> ExecutionReport | None:
        closure = self.ledger.closure(ex["execution_id"])
        if closure is None:
            return None
        report = self._report(ex, closure["outcome"], records=tuple(closure["records"]),
                              replayed=True, detail=closure["detail"])
        if report.outcome == "refused":
            raise AdmissionRefused("execution was refused earlier", report)
        return report

    def _close_crashed(self, ex: dict, error_type: str, message: str) -> ExecutionReport:
        sanitize = self.store.sanitizer.sanitize_text
        detail = {"error_type": sanitize(error_type[:100])[0],
                  "message": sanitize(message[:MAX_MESSAGE])[0]}
        with self.ledger.transaction():
            if self.ledger.closure(ex["execution_id"]) is not None:
                raise DuplicateCompletion(f"execution {ex['execution_id']} is already closed")
            self.ledger.append("execution_closed", {
                "execution_id": ex["execution_id"], "outcome": "crashed", "completion": "crashed",
                "records": [], "detail": detail})
        return self._report(ex, "crashed", detail=detail)

    def _commit(self, ex: dict, items: list[tuple[str, str, dict]], refs: set[str],
                problems: list[str]) -> ExecutionReport:
        ids = tuple(sorted({rid for _, rid, _ in items}))
        refused = None
        with self.ledger.transaction():
            closure = self.ledger.closure(ex["execution_id"])
            if closure is not None:
                if closure["outcome"] == "admitted" and tuple(closure["records"]) == ids:
                    return self._report(ex, "admitted", records=ids, replayed=True)
                raise DuplicateCompletion(f"execution {ex['execution_id']} is already closed "
                                          f"({closure['outcome']}) with different content")
            problems = list(problems)
            changed = self._revalidate_artifacts(ex, refs, problems)
            if problems:
                incidents = ()
                if changed:
                    incidents = (self._incident("artifact_integrity", {
                        "execution_id": ex["execution_id"], "artifacts": changed}),)
                detail = {"problems": problems}
                self.ledger.append("execution_closed", {
                    "execution_id": ex["execution_id"], "outcome": "refused", "completion": None,
                    "records": [], "detail": detail})
                refused = self._report(ex, "refused", incidents=incidents, detail=detail)
            else:
                written = set()
                for kind, rid, payload in items:
                    exists = (self.ledger.has_receipt(rid) if kind == "receipt_admitted"
                              else self.ledger.has_identity(rid))
                    if rid in written or exists:
                        continue                                    # idempotent re-admission
                    written.add(rid)
                    self.ledger.append(kind, payload)
                self.ledger.append("execution_closed", {
                    "execution_id": ex["execution_id"], "outcome": "admitted", "completion": None,
                    "records": list(ids), "detail": {}})
                quarantined, incidents = self._quarantine_conflicts(ex["domain"])
                report = self._report(ex, "admitted", records=ids, quarantined=quarantined,
                                      incidents=incidents)
        if refused is not None:
            raise AdmissionRefused("; ".join(refused.detail["problems"]), refused)
        return report

    def _revalidate_artifacts(self, ex: dict, refs: set[str], problems: list[str]) -> list[str]:
        """Inside the write lock: every referenced artifact must verify and be made durable."""
        inputs, changed = set(ex["inputs"].values()), []
        for sha in sorted(refs):
            try:
                self.store.ensure_durable(sha)
            except ArtifactNotFound:
                problems.append(f"artifact {sha} is not published")
                if sha in inputs:
                    changed.append(sha)
            except (IntegrityError, ArtifactTooLarge) as exc:
                problems.append(f"artifact {sha} failed verification: {exc}")
                changed.append(sha)
            except OSError as exc:
                problems.append(f"artifact {sha} could not be made durable: {exc}")
        return changed

    def _quarantine_conflicts(self, domain: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Semantic-conflict control (§A3): quarantine every conflicting premise + open incident."""
        quarantined, incidents = [], []
        current = current_receipts(self.ledger.receipt_records(domain), domain)
        groups = [(c.category, {"domain": domain, "subject": c.subject, "key": c.key,
                                "receipts": list(c.receipt_ids)}, c.receipt_ids)
                  for c in find_conflicts(current)]
        by_presentation: dict[str, list] = defaultdict(list)
        for rec in current_identities(self.ledger.identity_records(domain), domain):
            by_presentation[rec.presentation].append(rec)
        for pres, recs in sorted(by_presentation.items()):
            if len({r.code for r in recs}) > 1:
                ids = tuple(sorted(r.identity_id for r in recs))
                groups.append(("identity_conflict", {"domain": domain, "presentation": pres,
                                                     "identities": list(ids)}, ids))
        for category, detail, targets in groups:
            iid = self._incident(category, detail)
            incidents.append(iid)
            for target in targets:
                self.ledger.append("quarantined", {"target_id": target, "incident_id": iid,
                                                   "reason": category})
                quarantined.append(target)
        return tuple(quarantined), tuple(incidents)


def _revalidate_results(raw: Any) -> list[VerifierResult]:
    """Re-run contract validation (a ``model_construct``-ed result bypasses validators)."""
    if not isinstance(raw, (list, tuple)):
        raise TypeError("a verifier must return a list of VerifierResult")
    if len(raw) > MAX_RESULTS:
        raise ValueError(f"more than {MAX_RESULTS} results")
    out = []
    for r in raw:
        if not isinstance(r, VerifierResult):
            raise TypeError(f"verifier returned {type(r).__name__}, not VerifierResult")
        canonical_json(r.model_dump(mode="python"))  # NaN/inf or non-JSON detail -> malformed
        out.append(VerifierResult.model_validate(r.model_dump(mode="json")))
    return out


def _stamp(ex: dict, result: VerifierResult) -> Receipt:
    """Build a receipt: result content + issuer/version/execution/inputs/domain from the record."""
    body = {k: v for k, v in result.model_dump(mode="json").items()
            if k in VerifierResult.model_fields}
    return Receipt.model_validate({
        **body, "issuer": ex["issuer"], "issuer_version": ex["issuer_version"],
        "execution_id": ex["execution_id"], "input_hashes": sorted(set(ex["inputs"].values())),
        "domain": ex["domain"], "parents": []})
