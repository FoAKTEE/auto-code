"""Append-only evidence graph and content-addressed artifacts."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .schemas import Evidence


def content_hash(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


class EvidenceLedger:
    def __init__(self):
        self._records: dict[str, Evidence] = {}

    def add(self, record: Evidence) -> None:
        if record.evidence_id in self._records:
            raise ValueError("Evidence IDs are append-only and cannot be overwritten.")
        if any(parent not in self._records for parent in record.parents):
            raise ValueError("Parents must exist before a derived record is added.")
        if record.kind == "derived" and not record.parents:
            raise ValueError("Derived claims require explicit premises.")
        if record.kind == "assumption" and record.status == "verified":
            raise ValueError("An assumption is not verified evidence.")
        if any(self._records[p].domain_sha256 != record.domain_sha256 for p in record.parents):
            raise ValueError("Cross-domain evidence requires an explicit validated bridge.")
        self._records[record.evidence_id] = record.model_copy(deep=True)

    def effective_status(self, evidence_id: str, domain_sha256: str) -> str:
        e = self._records[evidence_id]
        if e.domain_sha256 != domain_sha256:
            return "unknown"
        if e.status == "refuted":
            return "refuted"
        # A false premise invalidates this derivation; it does NOT by itself
        # prove the conclusion false (avoid denying the antecedent).
        parents = [self.effective_status(p, domain_sha256) for p in e.parents]
        if e.status != "verified" or any(s != "verified" for s in parents):
            return "unknown"
        return "verified"

    def to_dict(self) -> dict:
        return {k: v.model_dump(mode="json") for k, v in self._records.items()}


class ArtifactStore:
    """Local content-addressed JSON store. No external paths accepted as names."""
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, value) -> str:
        identity = content_hash(value)
        path = self.root/f"{identity}.json"
        if not path.exists():
            path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+"\n")
        return identity

    def get(self, identity: str):
        if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
            raise ValueError("Expected a SHA-256 hex ID.")
        value = json.loads((self.root/f"{identity}.json").read_text())
        if content_hash(value) != identity:
            raise ValueError("Artifact hash mismatch.")
        return value
