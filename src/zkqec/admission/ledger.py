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

Operational job/dispatch state is *not* kept here (§L: separate from the scientific log).
Hash chains detect accidental or third-party tampering, not a compromised trusted writer (§O).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from zkqec.contracts import Receipt, canonical_json, content_hash
from zkqec.store.sanitize import DEFAULT_SANITIZER, SecretDetected

from .evidence import IdentityRecord, ReceiptRecord

GENESIS = "0" * 64
SCHEMA_VERSION = "zkqec-ledger-1"
EVENT_KINDS = ("ledger_opened", "execution_opened", "execution_closed", "receipt_admitted",
               "identity_recorded", "revoked", "incident_opened", "quarantined")

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
        inputs TEXT NOT NULL""", ("execution_opened",), ""),
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
    "artifact_refs": ("""sha TEXT NOT NULL, event_seq INTEGER NOT NULL REFERENCES events(seq),
        role TEXT NOT NULL, PRIMARY KEY (sha, event_seq, role)""",
        ("execution_opened", "receipt_admitted"), ""),
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
    return []


# --- apply (the only writer of projections) ---------------------------------------------------------
def _apply_ledger_opened(conn, seq, p):
    if seq != 1:
        raise LedgerError("ledger_opened must be the first event")


def _apply_execution_opened(conn, seq, p):
    conn.execute("INSERT INTO executions VALUES (?,?,?,?,?,?,?)",
                 (p["execution_id"], seq, p["role"], p["issuer"], p["issuer_version"], p["domain"],
                  canonical_json(p["inputs"]).decode()))


def _apply_execution_closed(conn, seq, p):
    conn.execute("INSERT INTO execution_closures VALUES (?,?,?,?,?,?)",
                 (p["execution_id"], seq, p["outcome"], p.get("completion"),
                  canonical_json(p["records"]).decode(), canonical_json(p["detail"]).decode()))


def _apply_receipt_admitted(conn, seq, p):
    receipt = Receipt.model_validate(p["receipt"])
    rid = content_hash(receipt)
    if rid != p["receipt_id"]:
        raise LedgerError(f"event {seq}: receipt_id does not match receipt content")
    ex = conn.execute("SELECT issuer, issuer_version, domain FROM executions WHERE execution_id=?",
                      (receipt.execution_id,)).fetchone()
    if ex is None or tuple(ex) != (receipt.issuer, receipt.issuer_version, receipt.domain):
        raise LedgerError(f"event {seq}: receipt issuer/domain differ from its execution record")
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
    ex = conn.execute("SELECT role, issuer, issuer_version, domain FROM executions "
                      "WHERE execution_id=?", (p["execution_id"],)).fetchone()
    if ex is None or tuple(ex) != ("identity", p["issuer"], p["issuer_version"], p["domain"]):
        raise LedgerError(f"event {seq}: identity not backed by an identity execution record")
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


_APPLY: dict[str, Callable] = {
    "ledger_opened": _apply_ledger_opened, "execution_opened": _apply_execution_opened,
    "execution_closed": _apply_execution_closed, "receipt_admitted": _apply_receipt_admitted,
    "identity_recorded": _apply_identity_recorded, "revoked": _apply_revoked,
    "incident_opened": _apply_incident_opened, "quarantined": _apply_quarantined,
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

    def __init__(self, path: str | os.PathLike):
        self.path = str(path)
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

    def append(self, kind: str, payload: dict) -> Event:
        """Append one event and apply it to the projections (inside :meth:`transaction`)."""
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
        row = self.conn.execute("SELECT execution_id, role, issuer, issuer_version, domain, inputs "
                                "FROM executions WHERE execution_id=?", (execution_id,)).fetchone()
        if row is None:
            return None
        keys = ("execution_id", "role", "issuer", "issuer_version", "domain", "inputs")
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

    def receipt_records(self, domain: str | None = None) -> list[ReceiptRecord]:
        sql = ("SELECT r.receipt_id, r.body, "
               "EXISTS (SELECT 1 FROM revocations v WHERE v.target_id = r.receipt_id), "
               "EXISTS (SELECT 1 FROM quarantines q WHERE q.target_id = r.receipt_id) "
               "FROM receipts r" + (" WHERE r.domain = ?" if domain else "") + " ORDER BY r.event_seq")
        rows = self.conn.execute(sql, (domain,) if domain else ())
        return [ReceiptRecord(rid, Receipt.model_validate_json(body), bool(rv), bool(q))
                for rid, body, rv, q in rows]

    def identity_records(self, domain: str | None = None) -> list[IdentityRecord]:
        sql = ("SELECT i.identity_id, i.presentation, i.code, i.domain, i.execution_id, i.issuer, "
               "i.issuer_version, "
               "EXISTS (SELECT 1 FROM revocations v WHERE v.target_id = i.identity_id), "
               "EXISTS (SELECT 1 FROM quarantines q WHERE q.target_id = i.identity_id) "
               "FROM identities i" + (" WHERE i.domain = ?" if domain else "")
               + " ORDER BY i.event_seq")
        rows = self.conn.execute(sql, (domain,) if domain else ())
        return [IdentityRecord(*row[:7], revoked=bool(row[7]), quarantined=bool(row[8]))
                for row in rows]

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
        fresh.close()
        return VerifyReport(not problems, len(rows), tuple(problems))
