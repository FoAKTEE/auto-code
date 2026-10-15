"""The single-writer admission service (prompt §4.1–4.2; contracts §A2, §A3, §L, §M).

Flow of :meth:`Admission.run_verifier` (§L "admission in one transaction"):

1. read and hash-verify the input artifacts (the read set);
2. append ``execution_opened`` (issuer, version, inputs, domain) and commit — *before* running;
3. run the registered verifier **outside** the write lock — in a bounded child when the caller
   passes the job's ``limits`` (CPU, wall and memory enforced; :mod:`zkqec.exec`); an exception, an
   exceeded limit or a malformed return value closes the execution as ``crashed`` (status
   unknown), never as a verdict;
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
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal

from zkqec.contracts import Receipt, VerifierResult, canonical_json, content_hash
from zkqec.contracts.registry import REGISTRY_VERSION
from zkqec.exec import BoundedResult, Limits, run_bounded
from zkqec.exec.deadline import alarm_deadline
from zkqec.exec.deadline import usable as deadline_usable
from zkqec.store import (SANITIZER_VERSION, ArtifactNotFound, ArtifactStore, ArtifactTooLarge,
                         IntegrityError, SecretDetected, StoreError, check_sha)

from .evidence import current_identities, current_receipts, find_conflicts
from .ledger import NAMESPACE_RE, SCHEMA_VERSION, Ledger, LedgerError, VerifyReport, evaluation_core
from .registry import ArtifactInput, Registered, VerifierRegistry
from .resolver import Resolver

MAX_RESULTS = 10_000
MAX_MESSAGE = 500
_CATEGORY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
Outcome = Literal["admitted", "crashed", "refused"]


class _WallDeadline(BaseException):
    """Raised from SIGALRM when a verifier exceeds its wall-clock ceiling (never caught by
    verifier code, which only catches Exception)."""


@contextmanager
def _wall_deadline(seconds: float | None):
    """In-process wall-clock ceiling for one unbounded verifier call (§K), enforced with SIGALRM in
    the main thread and composed with any armed timer (the earliest deadline fires; the previous
    timer is restored on exit). Bounded calls (``limits=``) are killed by the parent instead."""
    if not seconds or not deadline_usable():
        yield
        return
    with alarm_deadline(seconds, _WallDeadline):
        yield


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
        self._writer = object()          # the ledger's single-writer capability (§4.2)
        self.ledger = Ledger(ledger_path, writer=self._writer)
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

    def _append(self, kind: str, payload: dict):
        return self.ledger.append(kind, payload, writer=self._writer)

    def _declare(self) -> None:
        manifest = {"schema": SCHEMA_VERSION, "registry_version": REGISTRY_VERSION,
                    "sanitizer": SANITIZER_VERSION, "issuers": self.registry.manifest()}
        first = self.ledger.first_event()
        if first is None:
            with self.ledger.transaction():
                self._append("ledger_opened", manifest)
        elif first.kind != "ledger_opened" or first.payload != manifest:
            raise RegistryMismatch("ledger was opened with a different issuer registry/schema")

    # -- verifiers -------------------------------------------------------------------------------
    def run_verifier(self, name: str, inputs: dict[str, str], domain: str, *,
                     idempotency_key: str | None = None,
                     on_close: Callable[[str], None] | None = None,
                     wall_seconds: float | None = None, limits: Limits | None = None,
                     on_measure: Callable[[BoundedResult], None] | None = None,
                     namespace: str | None = None) -> ExecutionReport:
        """Run a registered verifier and admit its results.

        ``namespace`` (§N: campaign/arm/split/profile) is part of the execution's identity and
        record: a keyed execution is reused (replayed) only within its own namespace, and conflict
        quarantine and :meth:`resolver` lookups can be scoped to it.

        ``on_close(outcome)`` runs INSIDE the closing write transaction (outcome ``admitted``,
        ``refused`` or ``crashed``) so operational job settlement and accounting commit atomically
        with the admission (appendix §L). It is not called for replayed executions.

        With ``limits`` (the job's reservation) the verifier runs in a bounded child
        (:func:`zkqec.exec.run_bounded`: RLIMIT_CPU, RLIMIT_AS, wall kill); ``on_measure`` receives
        the :class:`BoundedResult` (the child's measured CPU, wall and peak RSS) before the
        closure. Any outcome but ``ok`` closes the execution as ``crashed`` (``cpu_exceeded``,
        ``wall_exceeded``, ``memory_exceeded`` or the child's error type), with the measurement in
        the closure detail and no receipt — never a bound. Without ``limits`` the verifier runs in
        this process; ``wall_seconds`` is then an in-process SIGALRM ceiling (``wall_timeout``).
        """
        spec = self.registry.get(name, "verifier")
        loaded = self._load_inputs(inputs)
        ex = self._open(spec, inputs, domain, idempotency_key, namespace)
        done = self._replay(ex)
        if done is not None:
            return done
        raw, crashed = self._execute(ex, spec, loaded, wall_seconds, limits, on_measure, on_close)
        if crashed is not None:
            return crashed
        try:
            results = _revalidate_results(raw)
        except Exception as exc:                                    # noqa: BLE001
            return self._close_crashed(ex, "malformed_result", str(exc), on_close)
        return self._complete(ex, results, on_close)

    def _execute(self, ex: dict, spec: Registered, loaded: dict[str, ArtifactInput],
                 wall_seconds: float | None, limits: Limits | None,
                 on_measure: Callable[[BoundedResult], None] | None,
                 on_close: Callable[[str], None] | None) -> tuple[Any, ExecutionReport | None]:
        """Run the issuer (bounded child or in-process); a failure is closed as ``crashed``."""
        if limits is not None:
            res = run_bounded(spec.fn, limits=limits, **loaded)
            if on_measure is not None:
                on_measure(res)
            if res.ok:
                return res.value, None
            error = res.detail.get("error_type", res.outcome) if res.outcome == "crashed" \
                else res.outcome
            usage = {"cpu_seconds": res.cpu_seconds, "wall_seconds": res.wall_seconds,
                     "peak_rss_bytes": res.peak_rss_bytes}
            return None, self._close_crashed(ex, str(error), str(res.detail.get("message", "")),
                                             on_close, extra={"usage": usage})
        try:
            with _wall_deadline(wall_seconds):
                return spec.fn(**loaded), None
        except _WallDeadline:
            return None, self._close_crashed(ex, "wall_timeout", f"wall-clock ceiling of "
                                             f"{wall_seconds} s reached", on_close)
        except Exception as exc:                                    # noqa: BLE001
            return None, self._close_crashed(ex, type(exc).__name__, str(exc), on_close)

    def _complete(self, ex: dict, results: Iterable[VerifierResult],
                  on_close: Callable[[str], None] | None = None) -> ExecutionReport:
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
        return self._commit(ex, items, refs, problems, on_close)

    # -- identity --------------------------------------------------------------------------------
    def run_identity(self, name: str, presentation: str, domain: str, *,
                     idempotency_key: str | None = None,
                     on_close: Callable[[str], None] | None = None,
                     wall_seconds: float | None = None, limits: Limits | None = None,
                     on_measure: Callable[[BoundedResult], None] | None = None,
                     namespace: str | None = None) -> ExecutionReport:
        """Record ``presentation -> code`` as computed by a registered identity function.

        The mapping comes only from the function's return value and the issuer only from the
        execution record; there is no API that accepts a caller-supplied mapping. ``limits``,
        ``on_measure`` and ``namespace`` work as in :meth:`run_verifier`.
        """
        spec = self.registry.get(name, "identity")
        inputs = {"presentation": presentation}
        loaded = self._load_inputs(inputs)
        ex = self._open(spec, inputs, domain, idempotency_key, namespace)
        done = self._replay(ex)
        if done is not None:
            return done
        code, crashed = self._execute(ex, spec, loaded, wall_seconds, limits, on_measure, on_close)
        if crashed is not None:
            return crashed
        try:
            check_sha(code)
        except ValueError as exc:
            return self._close_crashed(ex, "malformed_result", str(exc), on_close)
        core = {"presentation": presentation, "code": code, "execution_id": ex["execution_id"]}
        iid = content_hash(core)
        payload = {**core, "identity_id": iid, "issuer": ex["issuer"],
                   "issuer_version": ex["issuer_version"], "domain": ex["domain"]}
        return self._commit(ex, [("identity_recorded", iid, payload)], set(inputs.values()), [],
                            on_close)

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
            self._append("revoked", {"target_id": target_id, "reason": reason})
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
        self._append("incident_opened", {"incident_id": iid, "category": category,
                                               "detail": detail})
        return iid

    # -- campaign metadata (ledger-anchored; §A5, §K, §L) -----------------------------------------
    def _durable(self, shas) -> None:
        for sha in sorted({x for x in shas if x}):
            try:
                self.store.ensure_durable(sha)
            except (StoreError, OSError) as exc:
                raise AdmissionRefused(f"artifact {sha} is not durably published: {exc}") from None

    def freeze_campaign(self, campaign_id: str, *, namespace: str, config: dict, tasks: list,
                        caps: dict, labels: list, domain: str, manifest: str,
                        confirmation: dict | None = None) -> None:
        """Record the frozen campaign (config hash, tasks, caps, labels, domain, manifest artifact,
        confirmation reservation) once; everything the report shows about the campaign comes from
        this event."""
        payload = {"campaign_id": campaign_id, "namespace": namespace,
                   "config_sha256": content_hash(config), "config": config, "tasks": list(tasks),
                   "caps": caps, "labels": list(labels), "domain": domain, "manifest": manifest,
                   "confirmation": confirmation}
        with self.ledger.transaction():
            if self.ledger.campaign(campaign_id) is not None:
                raise LedgerError(f"campaign {campaign_id} is already frozen")
            self._durable([manifest])
            self._append("campaign_frozen", payload)

    def record_evaluation(self, campaign_id: str, task_id: str, *, candidate: str,
                          source: str, proposal: str | None = None,
                          call_record: str | None = None,
                          artifacts: dict[str, str] | None = None) -> str:
        """Record that a candidate was evaluated for a frozen task (report membership)."""
        core = {"campaign_id": campaign_id, "task_id": task_id, "candidate": candidate,
                "proposal": proposal, "call_record": call_record, "source": source,
                "artifacts": dict(sorted((artifacts or {}).items()))}
        eid = content_hash(evaluation_core(core))
        with self.ledger.transaction():
            if self.ledger.conn.execute("SELECT 1 FROM evaluations WHERE evaluation_id=?",
                                        (eid,)).fetchone():
                return eid
            self._durable([candidate, proposal, call_record, *core["artifacts"].values()])
            self._append("evaluation_recorded", {"evaluation_id": eid, **core})
        return eid

    def record_autonomy(self, campaign_id: str, actor: str, action: str, detail: str) -> None:
        """Autonomy log (§4.13): one human action on the campaign."""
        with self.ledger.transaction():
            self._append("autonomy_recorded", {"campaign_id": campaign_id, "actor": actor,
                                               "action": action, "detail": detail})

    def record_controls(self, campaign_id: str, control_set: str, results: list[dict], *,
                        evidence: str, on_close: Callable[[], None] | None = None) -> None:
        """Record a §I control summary (per control: N injections with k false authorizations,
        M paired positives with j false rejections) backed by an evidence artifact.
        ``on_close()`` runs inside the same transaction (the control job's settlement, §L)."""
        with self.ledger.transaction():
            self._durable([evidence])
            self._append("controls_recorded", {"campaign_id": campaign_id,
                                               "control_set": control_set,
                                               "results": list(results), "evidence": evidence})
            if on_close is not None:
                on_close()

    def job_book(self, campaign_id: str, **namespace):
        """The campaign's operational job book, bound to this ledger: every settlement appends a
        ``job_settled`` event in the same transaction as the operational update (§L)."""
        from zkqec.orchestrator.jobs import JobBook
        camp = self.ledger.campaign(campaign_id)
        if camp is None:
            raise LedgerError(f"campaign {campaign_id} is not frozen")
        book = JobBook(self.ledger.conn, campaign_id, **namespace, ledger=self.ledger,
                       settlement_sink=lambda payload: self._record_settlement(campaign_id,
                                                                               payload))
        if book.namespace != camp["namespace"]:
            raise LedgerError(f"namespace {book.namespace} is not the frozen {camp['namespace']}")
        return book

    def _record_settlement(self, campaign_id: str, payload: dict) -> None:
        if not self.ledger._in_tx:
            raise LedgerError("a settlement is recorded only inside the ledger transaction")
        self._durable([payload.get("result")])
        self._append("job_settled", {"campaign_id": campaign_id, **payload})

    # -- reading, verification -------------------------------------------------------------------
    def resolver(self, domain: str, *, namespace: str | None = None) -> Resolver:
        """Current authority in ``domain``; with ``namespace`` only that namespace's executions
        count (§N: evidence of another campaign/arm/split/profile is never used). ``None`` is the
        unscoped view over every namespace of this ledger."""
        return Resolver(domain, self.ledger.receipt_records(domain, namespace=namespace),
                        self.ledger.identity_records(domain, namespace=namespace),
                        store=self.store)

    def verify(self) -> VerifyReport:
        """Ledger verify (chain, triggers, artifacts, projections) plus a consistency re-check
        (no unquarantined conflict within any domain and namespace)."""
        report = self.ledger.verify(self.store)
        problems = list(report.problems)
        records = self.ledger.receipt_records()
        for domain, ns in sorted({(r.receipt.domain, r.namespace) for r in records},
                                 key=lambda x: (x[0], x[1] or "")):
            group = [r for r in records if r.namespace == ns]
            for c in find_conflicts(current_receipts(group, domain)):
                problems.append(f"unquarantined {c.category} in domain {domain} namespace {ns}: "
                                f"{c.receipt_ids}")
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

    @staticmethod
    def _execution_id(spec: Registered, inputs: dict[str, str], domain: str, key: str,
                      namespace: str | None = None) -> str:
        core = {"issuer": spec.name, "version": spec.version,
                "inputs": dict(sorted(inputs.items())), "domain": domain, "key": str(key)}
        if namespace is not None:
            core["namespace"] = namespace          # §N: no execution is shared across namespaces
        return content_hash(core)

    def execution_id_for(self, name: str, inputs: dict[str, str], domain: str, key: str, *,
                         namespace: str | None = None) -> str:
        """The deterministic execution id of an idempotency-keyed run in ``namespace``."""
        entry = self.registry._entries.get(name)
        if entry is None:
            raise KeyError(f"unregistered issuer {name!r}")
        return self._execution_id(entry, inputs, domain, key, _namespace(namespace))

    def closed_report(self, name: str, inputs: dict[str, str], domain: str, key: str, *,
                      namespace: str | None = None) -> ExecutionReport | None:
        """Read-only: the recorded closure of a keyed execution of ``namespace``, or ``None`` if it
        is not closed (a closure of another namespace is never returned). Never runs anything
        (recovery reads settled work only through this; §L)."""
        ex = self.ledger.execution(self.execution_id_for(name, inputs, domain, key,
                                                         namespace=namespace))
        return None if ex is None else self._replay(ex)

    def abandon(self, execution_id: str, reason: str) -> bool:
        """Close an OPEN execution as ``crashed`` (abandoned after recovery): a late completion of
        the old run is then a duplicate completion. Returns False if it was already closed."""
        detail = {"error_type": "abandoned",
                  "message": self.store.sanitizer.sanitize_text(str(reason)[:MAX_MESSAGE])[0]}
        with self.ledger.transaction():
            if self.ledger.execution(execution_id) is None:
                raise KeyError(f"no execution {execution_id}")
            if self.ledger.closure(execution_id) is not None:
                return False
            self._append("execution_closed", {
                "execution_id": execution_id, "outcome": "crashed", "completion": "crashed",
                "records": [], "detail": detail})
        return True

    def _open(self, spec: Registered, inputs: dict[str, str], domain: str,
              key: str | None, namespace: str | None = None) -> dict:
        check_sha(domain)
        namespace = _namespace(namespace)
        inputs = dict(sorted(inputs.items()))
        if key is None:
            execution_id = uuid.uuid4().hex
        else:
            execution_id = self._execution_id(spec, inputs, domain, key, namespace)
        with self.ledger.transaction():
            if self.ledger.execution(execution_id) is None:
                for sha in inputs.values():
                    try:
                        self.store.ensure_durable(sha)
                    except (StoreError, OSError) as exc:
                        raise AdmissionRefused(f"input {sha} is not durably published: {exc}") \
                            from None
                payload = {"execution_id": execution_id, "role": spec.role, "issuer": spec.name,
                           "issuer_version": spec.version, "domain": domain, "inputs": inputs}
                if namespace is not None:
                    payload["namespace"] = namespace
                self._append("execution_opened", payload)
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

    def _close_crashed(self, ex: dict, error_type: str, message: str,
                       on_close: Callable[[str], None] | None = None, *,
                       extra: dict | None = None) -> ExecutionReport:
        sanitize = self.store.sanitizer.sanitize_text
        detail = {"error_type": sanitize(error_type[:100])[0],
                  "message": sanitize(message[:MAX_MESSAGE])[0], **(extra or {})}
        with self.ledger.transaction():
            if self.ledger.closure(ex["execution_id"]) is not None:
                raise DuplicateCompletion(f"execution {ex['execution_id']} is already closed")
            self._append("execution_closed", {
                "execution_id": ex["execution_id"], "outcome": "crashed", "completion": "crashed",
                "records": [], "detail": detail})
            if on_close is not None:
                on_close("crashed")
        return self._report(ex, "crashed", detail=detail)

    def _commit(self, ex: dict, items: list[tuple[str, str, dict]], refs: set[str],
                problems: list[str],
                on_close: Callable[[str], None] | None = None) -> ExecutionReport:
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
                self._append("execution_closed", {
                    "execution_id": ex["execution_id"], "outcome": "refused", "completion": None,
                    "records": [], "detail": detail})
                refused = self._report(ex, "refused", incidents=incidents, detail=detail)
                if on_close is not None:
                    on_close("refused")
            else:
                written = set()
                for kind, rid, payload in items:
                    exists = (self.ledger.has_receipt(rid) if kind == "receipt_admitted"
                              else self.ledger.has_identity(rid))
                    if rid in written or exists:
                        continue                                    # idempotent re-admission
                    written.add(rid)
                    self._append(kind, payload)
                self._append("execution_closed", {
                    "execution_id": ex["execution_id"], "outcome": "admitted", "completion": None,
                    "records": list(ids), "detail": {}})
                quarantined, incidents = self._quarantine_conflicts(ex["domain"],
                                                                    ex.get("namespace"))
                report = self._report(ex, "admitted", records=ids, quarantined=quarantined,
                                      incidents=incidents)
                if on_close is not None:
                    on_close("admitted")
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

    def _quarantine_conflicts(self, domain: str, namespace: str | None = None
                              ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Semantic-conflict control (§A3): quarantine every conflicting premise + open incident,
        within the closing execution's domain and namespace (§N: another namespace's receipts are
        neither premises nor quarantined here)."""
        quarantined, incidents = [], []
        records = [r for r in self.ledger.receipt_records(domain) if r.namespace == namespace]
        current = current_receipts(records, domain)
        groups = [(c.category, {"domain": domain, "subject": c.subject, "key": c.key,
                                "receipts": list(c.receipt_ids)}, c.receipt_ids)
                  for c in find_conflicts(current)]
        by_presentation: dict[str, list] = defaultdict(list)
        idents = [r for r in self.ledger.identity_records(domain) if r.namespace == namespace]
        for rec in current_identities(idents, domain):
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
                self._append("quarantined", {"target_id": target, "incident_id": iid,
                                                   "reason": category})
                quarantined.append(target)
        return tuple(quarantined), tuple(incidents)


def _namespace(namespace: str | None) -> str | None:
    """A §N namespace key (``campaign`` or ``campaign/arm/split/profile``), or ``None``."""
    if namespace is not None and (not isinstance(namespace, str)
                                  or not NAMESPACE_RE.match(namespace)):
        raise ValueError(f"bad namespace {namespace!r}")
    return namespace


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
