"""Append-only, hash-chained scientific event log with guarded projections (prompt §4.2; contracts
§A2, §A3, §L).

* ``events``: ``hash = sha256(bytes.fromhex(prev_hash) || canonical_json({"kind", "payload"}))``;
  the genesis ``prev_hash`` is 64 zeros. Triggers ABORT every UPDATE and DELETE, and every INSERT
  that does not extend the chain (next ``seq``, ``prev_hash`` = previous ``hash``).
* Projections (executions, closures, receipts, receipt parents, identities, revocations, incidents,
  quarantines, artifact references) are written **only** by applying an event: a BEFORE INSERT trigger
  requires an ``apply_guard`` row naming the event being applied, of the matching kind (and, for
  receipts, the matching ``receipt_id``); UPDATE and DELETE on projections always abort.
  :meth:`Ledger.rebuild` drops and recreates them from the events.
* :meth:`Ledger.verify` recomputes the chain, re-hashes every referenced artifact, checks the
  triggers are present, and compares projections with a fresh in-memory rebuild.
* **Apply-time invariants** (checked on append, on :meth:`Ledger.rebuild` and in the verify
  replay, so a forged chain extension cannot be re-applied): an execution's issuer, version and role
  must be in the ``ledger_opened`` registry manifest; receipts and identities attach only to an
  OPEN execution of the matching role, with the execution's issuer/version/domain and exactly its
  input hashes; an ``admitted`` closure must list exactly the execution's receipts (or identities),
  a ``crashed``/``refused`` closure none; nothing attaches to a closed execution.
* Only records of executions with an ``admitted`` closure are returned to the resolver; ``verify``
  reports open executions and records without an admitted closure.
* :meth:`Ledger.append` requires the writer capability the admission service created the ledger
  with: scientific events have a single writer (§4.2).

Campaign metadata is ledger-anchored too (§A5, §K, §L): ``campaign_frozen`` (configuration hash,
frozen tasks, caps, typed labels, domain, manifest artifact, confirmation reservation),
``evaluation_recorded`` (evaluation membership: task, candidate, proposal, call record, typed
source), ``job_settled`` (one per settled job: reservation, measured usage and the charge, which
apply re-derives with the §K settlement rule), ``autonomy_recorded`` (human actions) and
``controls_recorded`` (§I control summary: k/N false authorizations, j/M false rejections). Their apply
step validates types, references to the frozen campaign and its tasks, finite non-negative units and
closed label/source vocabularies, so the report and the audit derive tasks, membership and accounting
from these events only.

Operational job/dispatch state (reservations, leases, generations) is *not* kept here (§L: separate
from the scientific log); its settlement is recorded by ``job_settled`` in the same transaction as
the operational update, and the audit reconciles the two.
Hash chains detect accidental or third-party tampering, not a compromised trusted writer (§O).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from zkqec.contracts import (CAMPAIGN_LABELS, UNITS, Receipt, TaskSA, TaskSF, UsageError,
                             canonical_json, charge_for, check_usage, content_hash)
from zkqec.store import check_sha
from zkqec.store.sanitize import DEFAULT_SANITIZER, SecretDetected

from .evidence import IdentityRecord, ReceiptRecord

GENESIS = "0" * 64
SCHEMA_VERSION = "zkqec-ledger-2"
EVENT_KINDS = ("ledger_opened", "execution_opened", "execution_closed", "receipt_admitted",
               "identity_recorded", "revoked", "incident_opened", "quarantined",
               "campaign_frozen", "evaluation_recorded", "job_settled", "autonomy_recorded",
               "controls_recorded")
CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
NAMESPACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:/-]{0,255}$")
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
LABELS = CAMPAIGN_LABELS
JOB_KINDS = frozenset({"propose", "verify", "falsify", "revise_route", "baseline", "confirm"})
SETTLED_FROM = frozenset({"completed", "failed", "uncertain", "cancelled"})
AUTONOMY_ACTIONS = frozenset({"pause", "resume", "abort", "rescope", "note"})
CONTROL_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
CONTROL_FIELDS = frozenset({"control", "injections", "false_authorizations", "paired_positives",
                            "false_rejections"})

_EVENTS_SQL = f"""
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY,
  prev_hash TEXT NOT NULL CHECK (length(prev_hash) = 64),
  hash TEXT NOT NULL UNIQUE CHECK (length(hash) = 64),
  kind TEXT NOT NULL CHECK (kind IN ({",".join(repr(k) for k in EVENT_KINDS)})),
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only: UPDATE refused'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only: DELETE refused'); END;
CREATE TRIGGER IF NOT EXISTS events_extend_chain BEFORE INSERT ON events
WHEN NEW.seq IS NOT (SELECT COALESCE(MAX(seq), 0) + 1 FROM events)
  OR NEW.prev_hash IS NOT COALESCE((SELECT hash FROM events WHERE seq = NEW.seq - 1), '{GENESIS}')
BEGIN SELECT RAISE(ABORT, 'events must extend the hash chain'); END;
"""

# table -> (columns DDL, event kinds allowed to write it, extra guard condition)
_PROJECTIONS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "executions": ("""execution_id TEXT PRIMARY KEY,
        event_seq INTEGER NOT NULL UNIQUE REFERENCES events(seq),
        role TEXT NOT NULL CHECK (role IN ('verifier', 'identity')),
        issuer TEXT NOT NULL, issuer_version TEXT NOT NULL, domain TEXT NOT NULL,
        inputs TEXT NOT NULL, namespace TEXT""", ("execution_opened",), ""),
    "execution_closures": ("""execution_id TEXT PRIMARY KEY REFERENCES executions(execution_id),
        event_seq INTEGER NOT NULL UNIQUE REFERENCES events(seq),
        outcome TEXT NOT NULL CHECK (outcome IN ('admitted', 'crashed', 'refused')),
        completion TEXT, records TEXT NOT NULL, detail TEXT NOT NULL""",
        ("execution_closed",), ""),
    "receipts": ("""receipt_id TEXT PRIMARY KEY,
        event_seq INTEGER NOT NULL UNIQUE REFERENCES events(seq),
        execution_id TEXT NOT NULL REFERENCES executions(execution_id),
        domain TEXT NOT NULL, predicate TEXT NOT NULL, subject_type TEXT NOT NULL,
        subject TEXT NOT NULL, prop_key TEXT NOT NULL, status TEXT NOT NULL,
        evidence_kind TEXT NOT NULL, completion TEXT NOT NULL, issuer TEXT NOT NULL,
        issuer_version TEXT NOT NULL, body TEXT NOT NULL""",
        ("receipt_admitted",), "AND json_extract(e.payload, '$.receipt_id') = NEW.receipt_id"),
    "receipt_parents": ("""receipt_id TEXT NOT NULL REFERENCES receipts(receipt_id),
        parent_id TEXT NOT NULL REFERENCES receipts(receipt_id),
        event_seq INTEGER NOT NULL REFERENCES events(seq),
        PRIMARY KEY (receipt_id, parent_id)""", ("receipt_admitted",), ""),
    "identities": ("""identity_id TEXT PRIMARY KEY,
        event_seq INTEGER NOT NULL UNIQUE REFERENCES events(seq),
        execution_id TEXT NOT NULL REFERENCES executions(execution_id),
        domain TEXT NOT NULL, presentation TEXT NOT NULL, code TEXT NOT NULL,
        issuer TEXT NOT NULL, issuer_version TEXT NOT NULL""", ("identity_recorded",), ""),
    "revocations": ("""target_id TEXT PRIMARY KEY,
        event_seq INTEGER NOT NULL UNIQUE REFERENCES events(seq),
        reason TEXT NOT NULL""", ("revoked",), ""),
    "incidents": ("""incident_id TEXT PRIMARY KEY,
        event_seq INTEGER NOT NULL UNIQUE REFERENCES events(seq),
        category TEXT NOT NULL, detail TEXT NOT NULL""", ("incident_opened",), ""),
    "quarantines": ("""target_id TEXT NOT NULL,
        incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
        event_seq INTEGER NOT NULL UNIQUE REFERENCES events(seq),
        PRIMARY KEY (target_id, incident_id)""", ("quarantined",), ""),
    "campaigns": ("""campaign_id TEXT PRIMARY KEY,
        event_seq INTEGER NOT NULL UNIQUE REFERENCES events(seq),
        namespace TEXT NOT NULL, domain TEXT NOT NULL, config_sha TEXT NOT NULL,
        manifest TEXT NOT NULL, body TEXT NOT NULL""", ("campaign_frozen",), ""),
    "campaign_tasks": ("""campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
        task_id TEXT NOT NULL, event_seq INTEGER NOT NULL REFERENCES events(seq),
        task_sha TEXT NOT NULL, body TEXT NOT NULL,
        PRIMARY KEY (campaign_id, task_id)""", ("campaign_frozen",), ""),
    "evaluations": ("""evaluation_id TEXT PRIMARY KEY,
        event_seq INTEGER NOT NULL UNIQUE REFERENCES events(seq),
        campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id), task_id TEXT NOT NULL,
        candidate TEXT NOT NULL, source TEXT NOT NULL, body TEXT NOT NULL""",
        ("evaluation_recorded",), ""),
    "settlements": ("""job_id TEXT PRIMARY KEY,
        event_seq INTEGER NOT NULL UNIQUE REFERENCES events(seq),
        campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id), kind TEXT NOT NULL,
        charged TEXT NOT NULL, body TEXT NOT NULL""", ("job_settled",), ""),
    "autonomy": ("""event_seq INTEGER PRIMARY KEY REFERENCES events(seq),
        campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id), body TEXT NOT NULL""",
        ("autonomy_recorded",), ""),
    "controls": ("""event_seq INTEGER PRIMARY KEY REFERENCES events(seq),
        campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id), body TEXT NOT NULL""",
        ("controls_recorded",), ""),
    "artifact_refs": ("""sha TEXT NOT NULL, event_seq INTEGER NOT NULL REFERENCES events(seq),
        role TEXT NOT NULL, PRIMARY KEY (sha, event_seq, role)""",
        ("execution_opened", "receipt_admitted", "campaign_frozen", "evaluation_recorded",
         "job_settled", "controls_recorded"), ""),
}
PROJECTION_TABLES = tuple(_PROJECTIONS)


def _projection_sql() -> str:
    parts = ["CREATE TABLE IF NOT EXISTS apply_guard (id INTEGER PRIMARY KEY CHECK (id = 1), "
             "seq INTEGER NOT NULL REFERENCES events(seq));"]
    for table, (cols, kinds, extra) in _PROJECTIONS.items():
        kind_list = ",".join(repr(k) for k in kinds)
        parts.append(f"CREATE TABLE IF NOT EXISTS {table} ({cols});")
        parts.append(f"""CREATE TRIGGER IF NOT EXISTS {table}_apply_only BEFORE INSERT ON {table}
WHEN NOT EXISTS (SELECT 1 FROM apply_guard g JOIN events e ON e.seq = g.seq
                 WHERE g.seq = NEW.event_seq AND e.kind IN ({kind_list}) {extra})
BEGIN SELECT RAISE(ABORT, 'projection {table} is written only by the admission apply path'); END;""")
        parts.append(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT, 'projection {table} is immutable'); END;""")
        parts.append(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table}
BEGIN SELECT RAISE(ABORT, 'projection {table} is immutable (use rebuild)'); END;""")
    return "\n".join(parts)


REQUIRED_TRIGGERS = ("events_no_update", "events_no_delete", "events_extend_chain",
                     *(f"{t}_{s}" for t in _PROJECTIONS for s in ("apply_only", "no_update",
                                                                    "no_delete")))


class LedgerError(Exception):
    """A ledger invariant was violated."""


class LedgerBusy(LedgerError):
    """Another writer holds the single-writer lock."""


@dataclass(frozen=True)
class Event:
    seq: int
    prev_hash: str
    hash: str
    kind: str
    payload: dict


@dataclass(frozen=True)
class VerifyReport:
    ok: bool
    events: int
    problems: tuple[str, ...]


def event_hash(prev_hash: str, kind: str, payload: Any) -> str:
    return hashlib.sha256(bytes.fromhex(prev_hash)
                          + canonical_json({"kind": kind, "payload": payload})).hexdigest()


def referenced_artifacts(kind: str, payload: dict) -> list[tuple[str, str]]:
    """(sha, role) pairs an event references; every one must be durably published."""
    if kind == "execution_opened":
        return [(sha, f"input:{name}") for name, sha in sorted(payload["inputs"].items())]
    if kind == "receipt_admitted":
        r = payload["receipt"]
        refs = [(sha, "input") for sha in r["input_hashes"]]
        refs += [(sha, "witness") for sha in r["witnesses"]]
        refs += [(sha, "artifact") for sha in r["artifacts"]]
        return sorted(set(refs))
    if kind == "campaign_frozen":
        return [(payload["manifest"], "manifest")]
    if kind == "evaluation_recorded":
        refs = [(payload["candidate"], "candidate")]
        refs += [(payload[k], k) for k in ("proposal", "call_record") if payload.get(k)]
        refs += [(sha, f"call:{name}") for name, sha in sorted(payload["artifacts"].items())]
        return sorted(set(refs))
    if kind == "job_settled":
        return [(payload["result"], "result")] if payload.get("result") else []
    if kind == "controls_recorded":
        return [(payload["evidence"], "evidence")]
    return []


# --- apply (the only writer of projections) ---------------------------------------------------------
def _manifest(conn) -> dict:
    row = conn.execute("SELECT kind, payload FROM events WHERE seq = 1").fetchone()
    if row is None or row[0] != "ledger_opened":
        raise LedgerError("the ledger has no ledger_opened registry manifest")
    return json.loads(row[1])


def _open_execution(conn, seq: int, execution_id: str, role: str) -> tuple:
    """The execution record a receipt/identity attaches to: it must exist, be OPEN and have the
    matching role. Returns (issuer, issuer_version, domain, inputs dict)."""
    ex = conn.execute("SELECT role, issuer, issuer_version, domain, inputs FROM executions "
                      "WHERE execution_id=?", (execution_id,)).fetchone()
    if ex is None:
        raise LedgerError(f"event {seq}: no execution record {execution_id}")
    if ex[0] != role:
        raise LedgerError(f"event {seq}: execution {execution_id} is a {ex[0]}, not a {role}")
    if conn.execute("SELECT 1 FROM execution_closures WHERE execution_id=?",
                    (execution_id,)).fetchone():
        raise LedgerError(f"event {seq}: execution {execution_id} is attached to after its closure")
    return ex[1], ex[2], ex[3], json.loads(ex[4])


def _apply_ledger_opened(conn, seq, p):
    if seq != 1:
        raise LedgerError("ledger_opened must be the first event")
    if not isinstance(p.get("issuers"), dict):
        raise LedgerError("ledger_opened must carry the issuer registry manifest")


def _apply_execution_opened(conn, seq, p):
    entry = _manifest(conn)["issuers"].get(p["issuer"])
    if not isinstance(entry, dict) or entry.get("version") != p["issuer_version"] \
            or entry.get("role") != p["role"]:
        raise LedgerError(f"event {seq}: issuer {p['issuer']!r} {p['issuer_version']!r} "
                          f"({p['role']}) is not in the frozen registry manifest")
    check_sha(p["domain"])
    inputs = p["inputs"]
    if not isinstance(inputs, dict) or not inputs or any(
            not (isinstance(k, str) and k.isidentifier()) for k in inputs):
        raise LedgerError(f"event {seq}: execution inputs must be a non-empty name -> sha map")
    for sha in inputs.values():
        check_sha(sha)
    namespace = p.get("namespace")
    if namespace is not None:
        _match(seq, NAMESPACE_RE, namespace, "namespace")
    conn.execute("INSERT INTO executions VALUES (?,?,?,?,?,?,?,?)",
                 (p["execution_id"], seq, p["role"], p["issuer"], p["issuer_version"], p["domain"],
                  canonical_json(p["inputs"]).decode(), namespace))


def _apply_execution_closed(conn, seq, p):
    ex = conn.execute("SELECT role FROM executions WHERE execution_id=?",
                      (p["execution_id"],)).fetchone()
    if ex is None:
        raise LedgerError(f"event {seq}: closure of unknown execution {p['execution_id']}")
    table, col = (("receipts", "receipt_id") if ex[0] == "verifier"
                  else ("identities", "identity_id"))
    actual = sorted(r[0] for r in conn.execute(
        f"SELECT {col} FROM {table} WHERE execution_id=?", (p["execution_id"],)))
    records = p["records"]
    expect = sorted(set(records)) if p["outcome"] == "admitted" else []
    if not isinstance(records, list) or sorted(records) != expect or actual != expect \
            or (p["outcome"] != "admitted" and records):
        raise LedgerError(f"event {seq}: an {p['outcome']} closure must list exactly the "
                          f"execution's records ({len(actual)} admitted, {len(records)} listed)")
    conn.execute("INSERT INTO execution_closures VALUES (?,?,?,?,?,?)",
                 (p["execution_id"], seq, p["outcome"], p.get("completion"),
                  canonical_json(p["records"]).decode(), canonical_json(p["detail"]).decode()))


def _apply_receipt_admitted(conn, seq, p):
    receipt = Receipt.model_validate(p["receipt"])
    rid = content_hash(receipt)
    if rid != p["receipt_id"]:
        raise LedgerError(f"event {seq}: receipt_id does not match receipt content")
    issuer, version, domain, inputs = _open_execution(conn, seq, receipt.execution_id, "verifier")
    if (issuer, version, domain) != (receipt.issuer, receipt.issuer_version, receipt.domain):
        raise LedgerError(f"event {seq}: receipt issuer/domain differ from its execution record")
    if list(receipt.input_hashes) != sorted(set(inputs.values())):
        raise LedgerError(f"event {seq}: receipt input hashes are not its execution's inputs")
    prop = receipt.proposition
    conn.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (rid, seq, receipt.execution_id, receipt.domain, prop.predicate, prop.subject_type,
                  prop.subject, prop.key, receipt.status, receipt.evidence_kind, receipt.completion,
                  receipt.issuer, receipt.issuer_version, canonical_json(receipt).decode()))
    for parent in sorted(set(receipt.parents)):
        conn.execute("INSERT INTO receipt_parents VALUES (?,?,?)", (rid, parent, seq))


def _apply_identity_recorded(conn, seq, p):
    expect = content_hash({k: p[k] for k in ("presentation", "code", "execution_id")})
    if expect != p["identity_id"]:
        raise LedgerError(f"event {seq}: identity_id does not match its content")
    issuer, version, domain, inputs = _open_execution(conn, seq, p["execution_id"], "identity")
    if (issuer, version, domain) != (p["issuer"], p["issuer_version"], p["domain"]) \
            or inputs != {"presentation": p["presentation"]}:
        raise LedgerError(f"event {seq}: identity not backed by its identity execution record")
    conn.execute("INSERT INTO identities VALUES (?,?,?,?,?,?,?,?)",
                 (p["identity_id"], seq, p["execution_id"], p["domain"], p["presentation"],
                  p["code"], p["issuer"], p["issuer_version"]))


def _known_target(conn, target: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM receipts WHERE receipt_id=? UNION ALL "
                             "SELECT 1 FROM identities WHERE identity_id=?",
                             (target, target)).fetchone())


def _apply_revoked(conn, seq, p):
    if not _known_target(conn, p["target_id"]):
        raise LedgerError(f"event {seq}: revocation of unknown record {p['target_id']}")
    conn.execute("INSERT INTO revocations VALUES (?,?,?)", (p["target_id"], seq, p["reason"]))


def _apply_incident_opened(conn, seq, p):
    conn.execute("INSERT INTO incidents VALUES (?,?,?,?)",
                 (p["incident_id"], seq, p["category"], canonical_json(p["detail"]).decode()))


def _apply_quarantined(conn, seq, p):
    if not _known_target(conn, p["target_id"]):
        raise LedgerError(f"event {seq}: quarantine of unknown record {p['target_id']}")
    conn.execute("INSERT INTO quarantines VALUES (?,?,?)", (p["target_id"], p["incident_id"], seq))


def _keys(seq: int, kind: str, p: dict, keys: set[str]) -> None:
    if not isinstance(p, dict) or set(p) != keys:
        raise LedgerError(f"event {seq}: {kind} needs exactly the fields {sorted(keys)}")


def _match(seq: int, pattern: re.Pattern, value, what: str) -> None:
    if not isinstance(value, str) or not pattern.match(value):
        raise LedgerError(f"event {seq}: bad {what} {value!r}")


def _sha_or_none(seq: int, value, what: str) -> None:
    if value is not None:
        try:
            check_sha(value)
        except ValueError:
            raise LedgerError(f"event {seq}: {what} is not an artifact hash") from None


def _usage(seq: int, value, what: str, *, complete: bool = False, optional: bool = False):
    if value is None and optional:
        return None
    try:
        return check_usage(value, complete=complete)
    except UsageError as exc:
        raise LedgerError(f"event {seq}: {what}: {exc}") from None


def _campaign_row(conn, seq: int, campaign_id) -> tuple:
    row = conn.execute("SELECT namespace, body FROM campaigns WHERE campaign_id=?",
                       (campaign_id,)).fetchone()
    if row is None:
        raise LedgerError(f"event {seq}: campaign {campaign_id!r} was never frozen")
    return row


def _apply_campaign_frozen(conn, seq, p):
    _keys(seq, "campaign_frozen", p, {"campaign_id", "namespace", "config_sha256", "config",
                                      "tasks", "caps", "labels", "domain", "manifest",
                                      "confirmation"})
    _match(seq, CAMPAIGN_RE, p["campaign_id"], "campaign id")
    _match(seq, NAMESPACE_RE, p["namespace"], "namespace")
    if p["namespace"].split("/")[0] != p["campaign_id"]:
        raise LedgerError(f"event {seq}: namespace must start with the campaign id")
    if not isinstance(p["config"], dict) or content_hash(p["config"]) != p["config_sha256"]:
        raise LedgerError(f"event {seq}: config_sha256 does not match the frozen config")
    tasks = p["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise LedgerError(f"event {seq}: a campaign freezes at least one task")
    if "tasks" in p["config"] and p["config"]["tasks"] != tasks:
        raise LedgerError(f"event {seq}: frozen tasks differ from the frozen config's tasks")
    seen = set()
    for t in tasks:
        model = TaskSF if isinstance(t, dict) and t.get("kind") == "S-F" else TaskSA
        try:
            parsed = model.model_validate(t)
        except ValueError as exc:
            raise LedgerError(f"event {seq}: invalid task: {str(exc)[:200]}") from None
        if parsed.model_dump(mode="json") != t:
            raise LedgerError(f"event {seq}: task is not in canonical form")
        _match(seq, TASK_ID_RE, parsed.task_id, "task id")
        if parsed.task_id in seen:
            raise LedgerError(f"event {seq}: duplicate task id {parsed.task_id}")
        seen.add(parsed.task_id)
    _usage(seq, p["caps"], "caps")
    labels = p["labels"]
    if not isinstance(labels, list) or len(set(labels)) != len(labels) or \
            any(x not in LABELS for x in labels):
        raise LedgerError(f"event {seq}: labels must be distinct values from {sorted(LABELS)}")
    _sha_or_none(seq, p["domain"], "domain")
    _sha_or_none(seq, p["manifest"], "manifest")
    if p["domain"] is None or p["manifest"] is None:
        raise LedgerError(f"event {seq}: domain and manifest are required")
    _usage(seq, p["confirmation"], "confirmation reservation", complete=True, optional=True)
    conn.execute("INSERT INTO campaigns VALUES (?,?,?,?,?,?,?)",
                 (p["campaign_id"], seq, p["namespace"], p["domain"], p["config_sha256"],
                  p["manifest"], canonical_json(p).decode()))
    for t in tasks:
        conn.execute("INSERT INTO campaign_tasks VALUES (?,?,?,?,?)",
                     (p["campaign_id"], t["task_id"], seq, content_hash(t),
                      canonical_json(t).decode()))


def evaluation_core(p: dict) -> dict:
    return {k: p[k] for k in ("campaign_id", "task_id", "candidate", "proposal", "call_record",
                              "source", "artifacts")}


def _apply_evaluation_recorded(conn, seq, p):
    _keys(seq, "evaluation_recorded", p, {"evaluation_id", "campaign_id", "task_id", "candidate",
                                          "proposal", "call_record", "source", "artifacts"})
    if content_hash(evaluation_core(p)) != p["evaluation_id"]:
        raise LedgerError(f"event {seq}: evaluation_id does not match its content")
    _campaign_row(conn, seq, p["campaign_id"])
    if not conn.execute("SELECT 1 FROM campaign_tasks WHERE campaign_id=? AND task_id=?",
                        (p["campaign_id"], p["task_id"])).fetchone():
        raise LedgerError(f"event {seq}: task {p['task_id']!r} is not frozen in the campaign")
    if p["candidate"] is None:
        raise LedgerError(f"event {seq}: an evaluation needs a candidate")
    for k in ("candidate", "proposal", "call_record"):
        _sha_or_none(seq, p[k], k)
    _match(seq, SOURCE_RE, p["source"], "source")
    arts = p["artifacts"]
    if not isinstance(arts, dict) or any(not (isinstance(k, str) and k.isidentifier())
                                         for k in arts):
        raise LedgerError(f"event {seq}: artifacts must be a name -> sha map")
    for name, sha in arts.items():
        _sha_or_none(seq, sha, f"artifact {name}")
    conn.execute("INSERT INTO evaluations VALUES (?,?,?,?,?,?,?)",
                 (p["evaluation_id"], seq, p["campaign_id"], p["task_id"], p["candidate"],
                  p["source"], canonical_json(p).decode()))


def _apply_job_settled(conn, seq, p):
    _keys(seq, "job_settled", p, {"campaign_id", "namespace", "job_id", "kind", "idem_key",
                                  "retry_of", "generation", "from_state", "reservation", "actual",
                                  "charged", "overrun", "result"})
    namespace, _ = _campaign_row(conn, seq, p["campaign_id"])
    if p["namespace"] != namespace:
        raise LedgerError(f"event {seq}: job namespace {p['namespace']!r} is not the campaign's")
    _match(seq, JOB_ID_RE, p["job_id"], "job id")
    if p["retry_of"] is not None:
        _match(seq, JOB_ID_RE, p["retry_of"], "retry_of")
    if p["kind"] not in JOB_KINDS or p["from_state"] not in SETTLED_FROM:
        raise LedgerError(f"event {seq}: bad job kind or settled-from state")
    if not isinstance(p["idem_key"], str) or not 0 < len(p["idem_key"]) <= 1024:
        raise LedgerError(f"event {seq}: bad idempotency key")
    if type(p["generation"]) is not int or p["generation"] < 0:
        raise LedgerError(f"event {seq}: bad generation")
    reservation = _usage(seq, p["reservation"], "reservation", complete=True)
    actual = _usage(seq, p["actual"], "actual usage", optional=True)
    charged = _usage(seq, p["charged"], "charge", complete=True)
    if charged != charge_for(p["from_state"], reservation, actual):
        raise LedgerError(f"event {seq}: the charge does not follow the settlement rule (§K)")
    if p["overrun"] is not any(charged[u] > reservation[u] + 1e-12 for u in UNITS):
        raise LedgerError(f"event {seq}: overrun flag is inconsistent")
    _sha_or_none(seq, p["result"], "result")
    conn.execute("INSERT INTO settlements VALUES (?,?,?,?,?,?)",
                 (p["job_id"], seq, p["campaign_id"], p["kind"], canonical_json(charged).decode(),
                  canonical_json(p).decode()))


def _apply_autonomy_recorded(conn, seq, p):
    _keys(seq, "autonomy_recorded", p, {"campaign_id", "actor", "action", "detail"})
    _campaign_row(conn, seq, p["campaign_id"])
    _match(seq, SOURCE_RE, p["actor"], "actor")
    if p["action"] not in AUTONOMY_ACTIONS:
        raise LedgerError(f"event {seq}: autonomy action must be one of {sorted(AUTONOMY_ACTIONS)}")
    if not isinstance(p["detail"], str) or len(p["detail"]) > 500 or "\n" in p["detail"]:
        raise LedgerError(f"event {seq}: autonomy detail must be one line of at most 500 chars")
    conn.execute("INSERT INTO autonomy VALUES (?,?,?)",
                 (seq, p["campaign_id"], canonical_json(p).decode()))


def _apply_controls_recorded(conn, seq, p):
    """§I control summary: per control, k/N false authorizations over N production-path
    injections and j/M false rejections over M paired positives, with an evidence artifact."""
    _keys(seq, "controls_recorded", p, {"campaign_id", "control_set", "results", "evidence"})
    _campaign_row(conn, seq, p["campaign_id"])
    _match(seq, SOURCE_RE, p["control_set"], "control set")
    results = p["results"]
    if not isinstance(results, list) or not results:
        raise LedgerError(f"event {seq}: a control summary needs at least one control")
    seen = set()
    for r in results:
        if not isinstance(r, dict) or set(r) != CONTROL_FIELDS:
            raise LedgerError(f"event {seq}: control rows need exactly {sorted(CONTROL_FIELDS)}")
        _match(seq, CONTROL_RE, r["control"], "control name")
        if r["control"] in seen:
            raise LedgerError(f"event {seq}: duplicate control {r['control']}")
        seen.add(r["control"])
        counts = [r[k] for k in ("injections", "false_authorizations", "paired_positives",
                                 "false_rejections")]
        if any(type(x) is not int or x < 0 for x in counts) \
                or r["false_authorizations"] > r["injections"] \
                or r["false_rejections"] > r["paired_positives"]:
            raise LedgerError(f"event {seq}: control {r['control']}: counts must be integers "
                              "with 0 <= k <= N and 0 <= j <= M")
    _sha_or_none(seq, p["evidence"], "evidence")
    if p["evidence"] is None:
        raise LedgerError(f"event {seq}: a control summary needs an evidence artifact")
    conn.execute("INSERT INTO controls VALUES (?,?,?)",
                 (seq, p["campaign_id"], canonical_json(p).decode()))


_APPLY: dict[str, Callable] = {
    "ledger_opened": _apply_ledger_opened, "execution_opened": _apply_execution_opened,
    "execution_closed": _apply_execution_closed, "receipt_admitted": _apply_receipt_admitted,
    "identity_recorded": _apply_identity_recorded, "revoked": _apply_revoked,
    "incident_opened": _apply_incident_opened, "quarantined": _apply_quarantined,
    "campaign_frozen": _apply_campaign_frozen, "evaluation_recorded": _apply_evaluation_recorded,
    "job_settled": _apply_job_settled, "autonomy_recorded": _apply_autonomy_recorded,
    "controls_recorded": _apply_controls_recorded,
}


def _apply(conn: sqlite3.Connection, seq: int, kind: str, payload: dict) -> None:
    conn.execute("INSERT INTO apply_guard (id, seq) VALUES (1, ?)", (seq,))
    try:
        _APPLY[kind](conn, seq, payload)
        for sha, role in referenced_artifacts(kind, payload):
            conn.execute("INSERT INTO artifact_refs VALUES (?,?,?)", (sha, seq, role))
    finally:
        conn.execute("DELETE FROM apply_guard")


def _exec_script(conn: sqlite3.Connection, sql: str) -> None:
    """Run a multi-statement script *without* ``executescript`` (which commits implicitly)."""
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            conn.execute(buf)
            buf = ""
    if buf.strip():
        raise LedgerError("incomplete SQL statement in schema script")


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA busy_timeout = 10000")
    _exec_script(conn, _EVENTS_SQL + _projection_sql())
    return conn


class Ledger:
    """SQLite-backed ledger. One writer per database file (enforced with an exclusive flock)."""

    def __init__(self, path: str | os.PathLike, *, writer: object | None = None):
        self.path = str(path)
        self._writer = writer           # capability held by the single writer (admission)
        self._lock_fd: int | None = None
        if self.path != ":memory:":
            self._lock_fd = os.open(self.path + ".writer.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(self._lock_fd)
                self._lock_fd = None
                raise LedgerBusy(f"{self.path} already has a writer") from None
        self.conn = _connect(self.path)
        self._in_tx = False

    def close(self) -> None:
        self.conn.close()
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    # -- transactions and appends ----------------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._in_tx:
            raise LedgerError("nested transaction")
        self.conn.execute("BEGIN IMMEDIATE")
        self._in_tx = True
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")
        finally:
            self._in_tx = False

    def append(self, kind: str, payload: dict, *, writer: object | None = None) -> Event:
        """Append one event and apply it to the projections (inside :meth:`transaction`).

        ``writer`` must be the capability this ledger was opened with: only the admission service
        appends events."""
        if self._writer is None or writer is not self._writer:
            raise LedgerError("events are appended only by the admission service")
        if not self._in_tx:
            raise LedgerError("append outside a transaction")
        if kind not in _APPLY:
            raise LedgerError(f"unknown event kind {kind!r}")
        payload = json.loads(canonical_json(payload))          # canonical, JSON-typed copy
        if DEFAULT_SANITIZER.detect_obj(payload):               # §M backstop: fail closed
            raise SecretDetected(f"{kind} payload contains secret-like content")
        seq, prev = self.head()
        seq += 1
        h = event_hash(prev, kind, payload)
        self.conn.execute("INSERT INTO events (seq, prev_hash, hash, kind, payload, created_at) "
                          "VALUES (?,?,?,?,?,?)",
                          (seq, prev, h, kind, canonical_json(payload).decode(),
                           datetime.now(timezone.utc).isoformat(timespec="microseconds")))
        _apply(self.conn, seq, kind, payload)
        return Event(seq, prev, h, kind, payload)

    def head(self) -> tuple[int, str]:
        row = self.conn.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return (0, GENESIS) if row is None else (row[0], row[1])

    def events(self) -> Iterator[Event]:
        for seq, prev, h, kind, payload in self.conn.execute(
                "SELECT seq, prev_hash, hash, kind, payload FROM events ORDER BY seq"):
            yield Event(seq, prev, h, kind, json.loads(payload))

    # -- projection queries ----------------------------------------------------------------------
    def execution(self, execution_id: str) -> dict | None:
        row = self.conn.execute("SELECT execution_id, role, issuer, issuer_version, domain, inputs, "
                                "namespace FROM executions WHERE execution_id=?",
                                (execution_id,)).fetchone()
        if row is None:
            return None
        keys = ("execution_id", "role", "issuer", "issuer_version", "domain", "inputs", "namespace")
        out = dict(zip(keys, row))
        out["inputs"] = json.loads(out["inputs"])
        return out

    def closure(self, execution_id: str) -> dict | None:
        row = self.conn.execute("SELECT outcome, completion, records, detail FROM "
                                "execution_closures WHERE execution_id=?",
                                (execution_id,)).fetchone()
        if row is None:
            return None
        return {"outcome": row[0], "completion": row[1], "records": json.loads(row[2]),
                "detail": json.loads(row[3])}

    def has_receipt(self, receipt_id: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM receipts WHERE receipt_id=?",
                                      (receipt_id,)).fetchone())

    def has_identity(self, identity_id: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM identities WHERE identity_id=?",
                                      (identity_id,)).fetchone())

    def is_known_record(self, target_id: str) -> bool:
        return _known_target(self.conn, target_id)

    def is_revoked(self, target_id: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM revocations WHERE target_id=?",
                                      (target_id,)).fetchone())

    _ADMITTED = ("EXISTS (SELECT 1 FROM execution_closures c WHERE c.execution_id = {t}.execution_id "
                 "AND c.outcome = 'admitted')")

    @staticmethod
    def _scope(t: str, domain: str | None, namespace: str | None) -> tuple[str, tuple]:
        """SQL filter: optional domain, optional §N namespace of the record's execution."""
        sql, args = "", ()
        if domain:
            sql, args = f" AND {t}.domain = ?", (domain,)
        if namespace is not None:
            sql += " AND e.namespace = ?"
            args += (namespace,)
        return sql, args

    def receipt_records(self, domain: str | None = None, *,
                        namespace: str | None = None) -> list[ReceiptRecord]:
        """Receipts of executions with an ``admitted`` closure (an open execution's receipts carry
        no authority). ``namespace`` restricts them to executions of that §N namespace; ``None``
        is the unscoped view over every namespace. Each record carries its execution's namespace."""
        where, args = self._scope("r", domain, namespace)
        sql = ("SELECT r.receipt_id, r.body, "
               "EXISTS (SELECT 1 FROM revocations v WHERE v.target_id = r.receipt_id), "
               "EXISTS (SELECT 1 FROM quarantines q WHERE q.target_id = r.receipt_id), e.namespace "
               "FROM receipts r JOIN executions e ON e.execution_id = r.execution_id WHERE "
               + self._ADMITTED.format(t="r") + where + " ORDER BY r.event_seq")
        return [ReceiptRecord(rid, Receipt.model_validate_json(body), bool(rv), bool(q), ns)
                for rid, body, rv, q, ns in self.conn.execute(sql, args)]

    def identity_records(self, domain: str | None = None, *,
                         namespace: str | None = None) -> list[IdentityRecord]:
        where, args = self._scope("i", domain, namespace)
        sql = ("SELECT i.identity_id, i.presentation, i.code, i.domain, i.execution_id, i.issuer, "
               "i.issuer_version, "
               "EXISTS (SELECT 1 FROM revocations v WHERE v.target_id = i.identity_id), "
               "EXISTS (SELECT 1 FROM quarantines q WHERE q.target_id = i.identity_id), "
               "e.namespace FROM identities i JOIN executions e ON e.execution_id = i.execution_id "
               "WHERE " + self._ADMITTED.format(t="i") + where + " ORDER BY i.event_seq")
        return [IdentityRecord(*row[:7], revoked=bool(row[7]), quarantined=bool(row[8]),
                               namespace=row[9])
                for row in self.conn.execute(sql, args)]

    # -- campaign projections ---------------------------------------------------------------------
    def campaign(self, campaign_id: str) -> dict | None:
        row = self.conn.execute("SELECT body FROM campaigns WHERE campaign_id=?",
                                (campaign_id,)).fetchone()
        return None if row is None else json.loads(row[0])

    def _bodies(self, table: str, campaign_id: str) -> list[dict]:
        return [json.loads(b) for b, in self.conn.execute(
            f"SELECT body FROM {table} WHERE campaign_id=? ORDER BY event_seq", (campaign_id,))]

    def evaluations(self, campaign_id: str) -> list[dict]:
        return self._bodies("evaluations", campaign_id)

    def settlements(self, campaign_id: str) -> list[dict]:
        return self._bodies("settlements", campaign_id)

    def autonomy(self, campaign_id: str) -> list[dict]:
        return self._bodies("autonomy", campaign_id)

    def controls(self, campaign_id: str) -> list[dict]:
        return self._bodies("controls", campaign_id)

    def first_event(self) -> Event | None:
        return next(self.events(), None)

    # -- rebuild and verify ----------------------------------------------------------------------
    def rebuild(self) -> None:
        """Drop the projections and recreate them by re-applying every event."""
        with self.transaction():
            for table in (*reversed(PROJECTION_TABLES), "apply_guard"):   # children first (FKs)
                self.conn.execute(f"DROP TABLE IF EXISTS {table}")
            _exec_script(self.conn, _projection_sql())
            for ev in list(self.events()):
                _apply(self.conn, ev.seq, ev.kind, ev.payload)

    def snapshot(self, conn: sqlite3.Connection | None = None) -> dict[str, list[tuple]]:
        conn = conn or self.conn
        out = {}
        for table in PROJECTION_TABLES:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            out[table] = sorted(conn.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall())
        return out

    def verify(self, store=None) -> VerifyReport:
        """Recompute the chain, check triggers, referenced artifacts and projections (§4.2)."""
        problems: list[str] = []
        present = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        for name in REQUIRED_TRIGGERS:
            if name not in present:
                problems.append(f"missing trigger {name}")
        rows = self.conn.execute("SELECT seq, prev_hash, hash, kind, payload, created_at "
                                 "FROM events ORDER BY seq").fetchall()
        prev, referenced = GENESIS, set()
        fresh = _connect(":memory:")
        replay_ok = True
        for i, (seq, prev_hash, h, kind, text, created) in enumerate(rows, start=1):
            if seq != i:
                problems.append(f"event seq gap: expected {i}, found {seq}")
            if prev_hash != prev:
                problems.append(f"event {seq}: prev_hash does not link to event {seq - 1}")
            try:
                payload = json.loads(text)
            except ValueError:
                problems.append(f"event {seq}: payload is not JSON")
                replay_ok = False
                prev = h
                continue
            if canonical_json(payload).decode() != text:
                problems.append(f"event {seq}: payload is not canonical JSON")
            if kind not in _APPLY:
                problems.append(f"event {seq}: unknown kind {kind!r}")
            elif event_hash(prev_hash, kind, payload) != h:
                problems.append(f"event {seq}: hash mismatch (payload or kind altered)")
            prev = h
            if kind in _APPLY:
                referenced.update(sha for sha, _ in referenced_artifacts(kind, payload))
            if replay_ok:
                try:
                    fresh.execute("BEGIN")
                    fresh.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                                  (i, prev_hash, h, kind, text, created))
                    _apply(fresh, i, kind, payload)
                    fresh.execute("COMMIT")
                except Exception as exc:                               # noqa: BLE001
                    fresh.execute("ROLLBACK")
                    problems.append(f"event {seq}: cannot be re-applied ({exc})")
                    replay_ok = False
        if store is not None:
            for sha in sorted(referenced):
                try:
                    store.verify(sha)
                except Exception as exc:                               # noqa: BLE001
                    problems.append(f"referenced artifact {sha}: {type(exc).__name__}: {exc}")
        if replay_ok:
            live, rebuilt = self.snapshot(), self.snapshot(fresh)
            for table in PROJECTION_TABLES:
                if live[table] != rebuilt[table]:
                    problems.append(f"projection {table} differs from a fresh rebuild")
            problems += _closure_problems(fresh)
        fresh.close()
        return VerifyReport(not problems, len(rows), tuple(problems))


def _closure_problems(conn: sqlite3.Connection) -> list[str]:
    """Every execution closed; every receipt/identity belongs to an admitted closure listing it."""
    out = [f"execution {eid} ({issuer}) is open: no closure was recorded"
           for eid, issuer in conn.execute(
               "SELECT execution_id, issuer FROM executions WHERE execution_id NOT IN "
               "(SELECT execution_id FROM execution_closures) ORDER BY event_seq")]
    for table, col in (("receipts", "receipt_id"), ("identities", "identity_id")):
        for rid, eid in conn.execute(f"SELECT {col}, execution_id FROM {table} ORDER BY event_seq"):
            row = conn.execute("SELECT outcome, records FROM execution_closures WHERE "
                               "execution_id=?", (eid,)).fetchone()
            if row is None or row[0] != "admitted" or rid not in json.loads(row[1]):
                out.append(f"{table[:-1]} {rid} is without an admitted closure listing it")
    return out
