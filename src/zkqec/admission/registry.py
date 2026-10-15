"""Registered issuers: verifiers and identity functions (prompt §4.1, §4.3; contracts §A2).

A verifier is ``fn(**inputs) -> list[VerifierResult]`` and an identity function is
``fn(presentation=...) -> code_sha``; each ``input`` is an :class:`ArtifactInput` (the hash-verified
bytes admission read for that artifact). Issuer names and versions come only from this registry,
via the execution record — never from a result. The registry is frozen when an admission service
opens it: no new verifier mid-campaign.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal

_NAME_RE = re.compile(r"^[a-z][a-z0-9_.\-]{0,63}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+\-]{0,63}$")
Role = Literal["verifier", "identity"]


class RegistryError(ValueError):
    """Bad registration, unknown issuer, or a registration after freezing."""


@dataclass(frozen=True)
class ArtifactInput:
    """An input artifact as admission read it: its hash and the hash-verified bytes."""
    sha: str
    data: bytes

    def json(self) -> Any:
        return json.loads(self.data.decode("utf-8"))


@dataclass(frozen=True)
class Registered:
    name: str
    version: str
    role: Role
    fn: Callable[..., Any]


class VerifierRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, Registered] = {}
        self._frozen = False

    def _add(self, name: str, version: str, fn: Callable, role: Role) -> None:
        if self._frozen:
            raise RegistryError("registry is frozen: no new issuer mid-campaign")
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise RegistryError(f"bad issuer name {name!r}")
        if not isinstance(version, str) or not _VERSION_RE.match(version):
            raise RegistryError(f"bad issuer version {version!r}")
        if not callable(fn):
            raise RegistryError("issuer must be callable")
        if name in self._entries:
            raise RegistryError(f"issuer {name!r} already registered")
        self._entries[name] = Registered(name, version, role, fn)

    def register(self, name: str, version: str, fn: Callable) -> None:
        """Register a verifier ``fn(**inputs: ArtifactInput) -> list[VerifierResult]``."""
        self._add(name, version, fn, "verifier")

    def register_identity(self, name: str, version: str, fn: Callable) -> None:
        """Register an identity function ``fn(presentation: ArtifactInput) -> code_sha``."""
        self._add(name, version, fn, "identity")

    def get(self, name: str, role: Role) -> Registered:
        entry = self._entries.get(name)
        if entry is None:
            raise RegistryError(f"unregistered issuer {name!r}")
        if entry.role != role:
            raise RegistryError(f"issuer {name!r} is a {entry.role}, not a {role}")
        return entry

    def freeze(self) -> None:
        self._frozen = True

    @property
    def frozen(self) -> bool:
        return self._frozen

    def manifest(self) -> dict[str, dict[str, str]]:
        return {n: {"version": e.version, "role": e.role} for n, e in sorted(self._entries.items())}
