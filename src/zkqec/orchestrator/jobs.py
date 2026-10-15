"""Operational job book: reservations, dispatch intents, leases, settlement (appendix §K, §L).

Lives in the ledger's SQLite file (same single-writer connection) but in separate, non-hash-chained
tables: operational state is kept apart from the scientific event log. Every state change is also
appended to the immutable ``ops_log``.

Lifecycle: ``reserved → dispatched → {completed | uncertain | failed} → settled``.

* Every job belongs to one **namespace** (campaign / arm / split / profile, §N). Idempotency keys are
  unique per namespace, and every lookup and update is restricted to the book's namespace: a book
  never sees, dispatches, completes or settles another namespace's job.
* ``reserve`` checks every budget unit atomically (open reservations + spent ≤ cap) before anything
  external happens. A repeated idempotency key returns the existing job only for the identical
  request (kind, input hash, reservation, retry_of); anything else is refused. Each retry is a new
  job with its own reservation (``retry_of``: a finished job of the same namespace and kind).
* ``dispatch`` persists the dispatch intent and bumps the lease generation BEFORE the caller launches
  the external action (CLI, verifier). A crash after this point leaves the job ``dispatched``;
  ``recover`` turns it into ``uncertain``, which keeps its full reservation.
* ``complete`` is fenced by the generation: stale or duplicate completions are rejected.
* ``complete`` accepts usage only as finite, non-negative numbers per known unit (NaN, inf, bools
  and strings are refused). A unit that is absent is *unmeasured*, never zero.
* ``settle`` reconciles (:func:`charge_for`): completed/failed jobs are charged their measured usage
  per unit and the full reservation for every unmeasured unit (an overrun beyond the reservation is
  charged in full and flagged); uncertain jobs are charged their full reservation.
* All methods can run inside the ledger's open transaction (e.g. from ``Admission.run_verifier``'s
  ``on_close`` hook) so admission and settlement commit atomically; otherwise they open their own
  ``BEGIN IMMEDIATE`` transaction.
"""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from zkqec.contracts import Reservation, TRANSITIONS

UNITS = ("usd", "requests", "input_tokens", "output_tokens", "wall_seconds", "cpu_seconds")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_jobs (
  job_id TEXT PRIMARY KEY, campaign TEXT NOT NULL, kind TEXT NOT NULL,
  idem_key TEXT NOT NULL, input_sha TEXT, retry_of TEXT,
  state TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0,
  reservation TEXT NOT NULL, actual TEXT, outcome TEXT, result_sha TEXT, overrun INTEGER DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (campaign, idem_key));
CREATE TABLE IF NOT EXISTS ops_caps (campaign TEXT NOT NULL, unit TEXT NOT NULL, cap REAL NOT NULL,
  PRIMARY KEY (campaign, unit));
CREATE TABLE IF NOT EXISTS ops_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
  event TEXT NOT NULL, generation INTEGER NOT NULL, detail TEXT NOT NULL, at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS ops_log_no_update BEFORE UPDATE ON ops_log
BEGIN SELECT RAISE(ABORT, 'ops_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ops_log_no_delete BEFORE DELETE ON ops_log
BEGIN SELECT RAISE(ABORT, 'ops_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ops_caps_no_update BEFORE UPDATE ON ops_caps
BEGIN SELECT RAISE(ABORT, 'budget caps are frozen'); END;
CREATE TRIGGER IF NOT EXISTS ops_caps_no_delete BEFORE DELETE ON ops_caps
BEGIN SELECT RAISE(ABORT, 'budget caps are frozen'); END;
"""


class JobError(RuntimeError):
    pass


class BudgetExceeded(JobError):
    pass


class StaleCompletion(JobError):
    pass


class DuplicateCompletion(JobError):
    pass


class IllegalTransition(JobError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def check_usage(actual: dict | None) -> dict[str, float] | None:
    """Typed usage: known units only, each a finite non-negative int/float (not bool)."""
    if actual is None:
        return None
    if not isinstance(actual, dict):
        raise JobError(f"usage must be a dict of units, got {type(actual).__name__}")
    out = {}
    for unit, v in actual.items():
        if unit not in UNITS:
            raise JobError(f"unknown usage unit {unit!r}")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise JobError(f"usage {unit}={v!r} must be a finite non-negative number")
        out[unit] = float(v)
    return out


def charge_for(state: str, reservation: dict, actual: dict | None) -> dict[str, float]:
    """Settlement rule: uncertain or unmeasured ⇒ the reservation; measured units ⇒ the measure."""
    if state == "uncertain" or actual is None:
        return {u: float(reservation.get(u, 0.0)) for u in UNITS}
    return {u: float(actual[u]) if u in actual else float(reservation.get(u, 0.0)) for u in UNITS}


def _finite_charge(d: object) -> bool:
    return isinstance(d, dict) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0
        for v in d.values())


def _res_dict(r: Reservation) -> dict:
    return {"usd": r.usd, "requests": r.requests, "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens, "wall_seconds": r.wall_seconds,
            "cpu_seconds": r.cpu_seconds}


def namespace(campaign: str, arm: str | None = None, split: str | None = None,
              profile: str | None = None) -> str:
    """The §N namespace key: ``campaign`` alone, or ``campaign/arm/split/profile``."""
    parts = (campaign, arm, split, profile)
    for x in parts:
        if x is not None and (not isinstance(x, str) or not x or "/" in x):
            raise JobError(f"bad namespace component {x!r}")
    if not campaign:
        raise JobError("a namespace needs a campaign")
    return campaign if parts[1:] == (None, None, None) else "/".join(x or "-" for x in parts)


class JobBook:
    def __init__(self, conn: sqlite3.Connection, campaign: str, *, arm: str | None = None,
                 split: str | None = None, profile: str | None = None):
        self.conn = conn
        self.namespace = namespace(campaign, arm, split, profile)
        self.campaign = self.namespace          # column value: the full namespace key
        # sqlite3.executescript would COMMIT an open transaction; run statements one by one.
        for stmt in _SCHEMA.strip().split(";\n"):
            if stmt.strip():
                self.conn.execute(stmt)

    # -- transactions ---------------------------------------------------------------------------
    @contextmanager
    def _tx(self) -> Iterator[None]:
        if self.conn.in_transaction:
            yield                         # caller (e.g. the ledger's closing transaction) owns it
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def _log(self, job_id: str, event: str, generation: int, detail: dict) -> None:
        self.conn.execute("INSERT INTO ops_log (job_id, event, generation, detail, at) VALUES (?,?,?,?,?)",
                          (job_id, event, generation, json.dumps(detail, sort_keys=True), _now()))

    def _row(self, job_id: str) -> sqlite3.Row:
        cur = self.conn.execute("SELECT job_id, state, generation, reservation, actual, outcome, kind "
                                "FROM ops_jobs WHERE job_id=? AND campaign=?",
                                (job_id, self.campaign))
        row = cur.fetchone()
        if row is None:
            raise JobError(f"unknown job {job_id} in namespace {self.campaign}")
        return row

    def _move(self, job_id: str, to: str, **fields) -> None:
        state = self._row(job_id)[1]
        if to not in TRANSITIONS[state]:
            raise IllegalTransition(f"{job_id}: {state} -> {to} is not allowed")
        sets = ", ".join(f"{k}=?" for k in ("state", "updated_at", *fields))
        cur = self.conn.execute(f"UPDATE ops_jobs SET {sets} WHERE job_id=? AND campaign=?",
                                (to, _now(), *fields.values(), job_id, self.campaign))
        if cur.rowcount != 1:
            raise JobError(f"{job_id}: update outside namespace {self.campaign}")

    # -- caps and totals ------------------------------------------------------------------------
    def set_caps(self, caps: dict[str, float]) -> None:
        """Freeze the campaign's budget caps (once; caps are immutable afterwards)."""
        unknown = set(caps) - set(UNITS)
        if unknown:
            raise JobError(f"unknown budget units {sorted(unknown)}")
        with self._tx():
            for unit, cap in caps.items():
                self.conn.execute("INSERT INTO ops_caps (campaign, unit, cap) VALUES (?,?,?)",
                                  (self.campaign, unit, float(cap)))

    def caps(self) -> dict[str, float]:
        return {u: c for u, c in self.conn.execute(
            "SELECT unit, cap FROM ops_caps WHERE campaign=?", (self.campaign,))}

    def totals(self) -> dict[str, dict[str, float]]:
        """Per unit: spent (settled charges) and open (reservations of unsettled jobs)."""
        out = {u: {"spent": 0.0, "open": 0.0} for u in UNITS}
        for state, res, actual, outcome in self.conn.execute(
                "SELECT state, reservation, actual, outcome FROM ops_jobs WHERE campaign=?",
                (self.campaign,)):
            r = json.loads(res)
            if state == "settled":
                charged = json.loads(actual) if actual else r
                for u in UNITS:
                    out[u]["spent"] += float(charged.get(u, 0.0))
            else:
                for u in UNITS:
                    out[u]["open"] += float(r.get(u, 0.0))
        return out

    # -- lifecycle ------------------------------------------------------------------------------
    def reserve(self, kind: str, idem_key: str, reservation: Reservation, *,
                input_sha: str | None = None, retry_of: str | None = None) -> str:
        """Reserve a job's worst-case budget atomically; idempotent on ``idem_key``."""
        want = _res_dict(reservation)
        with self._tx():
            row = self.conn.execute(
                "SELECT job_id, kind, input_sha, reservation, retry_of FROM ops_jobs "
                "WHERE campaign=? AND idem_key=?", (self.campaign, idem_key)).fetchone()
            if row is not None:
                if (row[1], row[2], json.loads(row[3]), row[4]) != (kind, input_sha, want, retry_of):
                    raise JobError(f"idempotency key {idem_key!r} reused for a different request")
                return row[0]
            if retry_of is not None:
                prev = self.conn.execute("SELECT kind, state FROM ops_jobs WHERE job_id=? AND "
                                         "campaign=?", (retry_of, self.campaign)).fetchone()
                if prev is None:
                    raise JobError(f"retry_of {retry_of} is not a job of {self.campaign}")
                if prev[0] != kind:
                    raise JobError(f"retry_of {retry_of} is a {prev[0]} job, not {kind}")
                if prev[1] in ("reserved", "dispatched"):
                    raise JobError(f"retry_of {retry_of} is still {prev[1]}")
            caps, totals = self.caps(), self.totals()
            for unit, cap in caps.items():
                used = totals[unit]["spent"] + totals[unit]["open"]
                if used + want[unit] > cap + 1e-12:
                    raise BudgetExceeded(f"{unit}: {used:g} + {want[unit]:g} > cap {cap:g}")
            job_id = uuid.uuid4().hex
            self.conn.execute(
                "INSERT INTO ops_jobs (job_id, campaign, kind, idem_key, input_sha, retry_of, state, "
                "generation, reservation, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, self.campaign, kind, idem_key, input_sha, retry_of, "reserved", 0,
                 json.dumps(want, sort_keys=True), _now(), _now()))
            self._log(job_id, "reserved", 0, {"reservation": want, "retry_of": retry_of})
            return job_id

    def dispatch(self, job_id: str) -> int:
        """Persist the dispatch intent and return the new lease generation (call BEFORE launch)."""
        with self._tx():
            gen = self._row(job_id)[2] + 1
            self._move(job_id, "dispatched", generation=gen)
            self._log(job_id, "dispatched", gen, {})
            return gen

    def complete(self, job_id: str, generation: int, outcome: str, *, actual: dict | None = None,
                 result_sha: str | None = None) -> None:
        """Record completion of the current lease. ``outcome`` ∈ completed / failed / uncertain."""
        if outcome not in ("completed", "failed", "uncertain"):
            raise JobError(f"bad outcome {outcome!r}")
        with self._tx():
            _, state, gen, *_ = self._row(job_id)
            if generation != gen:
                raise StaleCompletion(f"{job_id}: generation {generation} != current {gen}")
            if state != "dispatched":
                raise DuplicateCompletion(f"{job_id}: already {state}")
            actual = check_usage(actual)
            self._move(job_id, outcome, actual=json.dumps(actual, sort_keys=True) if actual else None,
                       outcome=outcome, result_sha=result_sha)
            self._log(job_id, outcome, gen, {"actual": actual, "result_sha": result_sha})

    def settle(self, job_id: str) -> dict:
        """Charge the job: actual usage, or the full reservation if uncertain/unknown."""
        with self._tx():
            _, state, gen, res, actual, outcome, _ = self._row(job_id)
            if state not in ("completed", "failed", "uncertain"):
                raise IllegalTransition(f"{job_id}: cannot settle a {state} job")
            reservation = json.loads(res)
            charged = charge_for(state, reservation, check_usage(json.loads(actual)) if actual else None)
            overrun = any(charged[u] > reservation.get(u, 0.0) + 1e-12 for u in UNITS)
            self._move(job_id, "settled", actual=json.dumps(charged, sort_keys=True),
                       overrun=int(overrun))
            self._log(job_id, "settled", gen, {"charged": charged, "overrun": overrun,
                                                "from": state})
            return charged

    def cancel(self, job_id: str) -> None:
        """Cancel a reserved (never dispatched) job: it settles with a zero charge."""
        with self._tx():
            state = self._row(job_id)[1]
            if state != "reserved":
                raise IllegalTransition(f"{job_id}: only reserved jobs can be cancelled ({state})")
            zero = {u: 0.0 for u in UNITS}
            self._move(job_id, "settled", actual=json.dumps(zero), outcome="cancelled")
            self._log(job_id, "cancelled", 0, {})

    def expire(self, job_id: str, generation: int, *, killed: bool) -> None:
        """Lease expiry: the old execution becomes uncertain (full reservation retained).
        ``killed`` records whether the caller confirmed the old execution's scope was stopped."""
        with self._tx():
            _, state, gen, *_ = self._row(job_id)
            if generation != gen or state != "dispatched":
                raise StaleCompletion(f"{job_id}: nothing to expire at generation {generation}")
            # Bump the generation so a late completion from the old execution is fenced as stale.
            self._move(job_id, "uncertain", outcome="uncertain", generation=gen + 1)
            self._log(job_id, "expired", gen + 1, {"killed": killed})

    def recover(self) -> list[str]:
        """After a crash: every job still ``dispatched`` becomes ``uncertain`` (charge retained)."""
        with self._tx():
            ids = [r[0] for r in self.conn.execute(
                "SELECT job_id FROM ops_jobs WHERE campaign=? AND state='dispatched'", (self.campaign,))]
            for job_id in ids:
                gen = self._row(job_id)[2] + 1
                self._move(job_id, "uncertain", outcome="uncertain", generation=gen)
                self._log(job_id, "recovered_uncertain", gen, {})
            return ids

    def state(self, job_id: str) -> str:
        return self._row(job_id)[1]

    def find(self, idem_key: str) -> str | None:
        """The job of this namespace with this idempotency key, if any."""
        row = self.conn.execute("SELECT job_id FROM ops_jobs WHERE campaign=? AND idem_key=?",
                                (self.campaign, idem_key)).fetchone()
        return None if row is None else row[0]

    def audit(self) -> list[str]:
        """Replay ops_log and compare with ops_jobs (final state and generation per job)."""
        last: dict[str, tuple[str, int]] = {}
        for job_id, event, gen in self.conn.execute(
                "SELECT job_id, event, generation FROM ops_log WHERE job_id IN "
                "(SELECT job_id FROM ops_jobs WHERE campaign=?) ORDER BY seq", (self.campaign,)):
            state = {"recovered_uncertain": "uncertain", "expired": "uncertain",
                     "cancelled": "settled"}.get(event, event)
            last[job_id] = (state, gen)
        problems = []
        for job_id, state, gen, res, actual in self.conn.execute(
                "SELECT job_id, state, generation, reservation, actual FROM ops_jobs WHERE campaign=?",
                (self.campaign,)):
            if last.get(job_id) != (state, gen):
                problems.append(f"{job_id}: table {(state, gen)} != log {last.get(job_id)}")
            for name, text in (("reservation", res), ("charge", actual)):
                try:
                    ok = text is None or _finite_charge(json.loads(text))
                except ValueError:
                    ok = False
                if not ok:
                    problems.append(f"{job_id}: {name} is not finite non-negative usage")
        return problems
