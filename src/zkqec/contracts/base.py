"""Frozen, strict model base and canonical content hashing (prompt §O, §A)."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict

SHA256_HEX = r"^[0-9a-f]{64}$"


class Frozen(BaseModel):
    """All contracts: immutable, unknown fields rejected, no NaN/inf."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def canonical_json(value: Any) -> bytes:
    """Deterministic JSON bytes used for every content hash."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      ensure_ascii=False).encode("utf-8")


def content_hash(value: Any) -> str:
    """SHA-256 hex of the canonical JSON encoding."""
    return hashlib.sha256(canonical_json(value)).hexdigest()
