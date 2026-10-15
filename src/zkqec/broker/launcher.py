"""Orchestrator-side control of a campaign's egress broker (contracts §J1, §K, §L, §N).

One broker (``mitmdump`` + ``addon.py``) per campaign, with a per-campaign CA in its own confdir. Jobs
are authorized by *grants* written before launch (dispatch intent precedes dispatch, §L) and identified
by a random per-job token that only the host-side bridge and the grant digest know.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import policy as P
from .bridge import free_loopback_port

REPO = Path(__file__).resolve().parents[3]
MITMDUMP = REPO / ".venv-broker" / "bin" / "mitmdump"
ADDON = Path(__file__).resolve().with_name("addon.py")
POLICY_MODULE = Path(__file__).resolve().with_name("policy.py")
BASE_POLICY = REPO / "configs" / "broker" / "policy.json"
PRICES = REPO / "configs" / "broker" / "prices.json"
CA_CERT_NAME = "mitmproxy-ca-cert.pem"


def sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, mode)
    try:
        view = memoryview(data)
        while view:                     # a short write is continued, never ignored
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


@dataclass(frozen=True)
class Ceilings:
    """Per-invocation ceilings enforced at the broker (§K)."""
    max_model_requests: int
    max_total_requests: int
    max_input_bytes: int
    max_output_tokens: int
    ttl_seconds: int


class Broker:
    """A running campaign broker. Use as a context manager or call start()/stop()."""

    def __init__(self, campaign: str, root: str | os.PathLike[str], *, base_policy: Path = BASE_POLICY,
                 deadline_unix: float | None = None, mitmdump: Path = MITMDUMP,
                 overrides: dict[str, Any] | None = None) -> None:
        if not campaign.replace("_", "").replace("-", "").isalnum():
            raise ValueError("campaign id must be alphanumeric/_/-")
        self.campaign = campaign
        self.root = Path(root).resolve() / "broker"
        self.confdir = self.root / "conf"
        self.state = self.root / "state"
        self.log_path = self.root / "broker-log.jsonl"
        self.policy_path = self.root / "policy.json"
        self.switch_path = self.root / "DISPATCH_DISABLED"
        self.stdout_path = self.root / "mitmdump.out"
        self.mitmdump = mitmdump
        self.port: int | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self.prices = PRICES
        for d in (self.root, self.confdir, self.state, self.state / "grants", self.state / "blocked",
                  self.state / "issued"):
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o700)
        doc = json.loads(Path(base_policy).read_text())
        doc.pop("_comment", None)
        if overrides:
            doc.update(overrides)
        doc.update({"campaign": campaign, "log_path": str(self.log_path), "state_dir": str(self.state),
                    "dispatch_disabled_path": str(self.switch_path), "deadline_unix": deadline_unix})
        raw = json.dumps(doc, indent=2, sort_keys=True).encode()
        P.parse_policy(json.loads(raw), raw)  # validate before the broker sees it
        _atomic_write(self.policy_path, raw)

    # -- process ---------------------------------------------------------------------------------
    def argv(self, port: int) -> list[str]:
        return [str(self.mitmdump), "--listen-host", "127.0.0.1", "--listen-port", str(port),
                "-s", str(ADDON), "--set", f"zkqec_policy={self.policy_path}",
                "--set", f"confdir={self.confdir}", "--set", "flow_detail=0",
                "--set", "termlog_verbosity=warn", "--set", "http3=false",
                "--set", "body_size_limit=16m", "--set", "connection_strategy=eager",
                "--set", "ssl_insecure=false", "--set", "upstream_cert=true"]

    def start(self, timeout: float = 30.0, extra_args: list[str] | None = None) -> "Broker":
        self.port = free_loopback_port()
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.root), "LANG": "C.UTF-8"}
        out = open(self.stdout_path, "ab")
        self.proc = subprocess.Popen(self.argv(self.port) + (extra_args or []), stdin=subprocess.DEVNULL,
                                     stdout=out, stderr=subprocess.STDOUT, env=env, close_fds=True,
                                     start_new_session=True)
        out.close()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"broker exited early ({self.proc.returncode}); see {self.stdout_path}")
            if self.ca_cert.exists() and _port_open(self.port) and self._started_logged():
                os.chmod(self.confdir, 0o700)
                return self
            time.sleep(0.1)
        self.stop()
        raise RuntimeError("broker did not become ready")

    def _started_logged(self) -> bool:
        try:
            return b'"broker_start"' in self.log_path.read_bytes()[-4096:]
        except OSError:
            return False

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self.proc = None

    def __enter__(self) -> "Broker":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- CA ----------------------------------------------------------------------------------------
    @property
    def ca_cert(self) -> Path:
        """The per-campaign CA *certificate* (public). The key stays in the 0700 confdir."""
        return self.confdir / CA_CERT_NAME

    # -- grants (dispatch intent) ------------------------------------------------------------------
    def grant(self, job_id: str, provider: str, models: list[str], ceilings: Ceilings) -> str:
        """Authorize one job; returns its proxy token (give it only to the job's bridge)."""
        token = secrets.token_urlsafe(32)
        g = P.Grant(job_id=job_id, provider=provider, models=frozenset(models),
                    max_model_requests=ceilings.max_model_requests,
                    max_total_requests=ceilings.max_total_requests,
                    max_input_bytes=ceilings.max_input_bytes, max_output_tokens=ceilings.max_output_tokens,
                    expires_unix=time.time() + ceilings.ttl_seconds)
        P.parse_grant(g.to_json())
        body = json.dumps(g.to_json(), sort_keys=True).encode()
        # The issued copy outlives revocation: it is the sealed record of the job's ceilings (§M).
        _atomic_write(self.state / "issued" / f"{job_id}.json", body)
        _atomic_write(self.state / "grants" / (P.token_digest(token) + ".json"), body)
        return token

    def revoke(self, token: str) -> None:
        try:
            os.unlink(self.state / "grants" / (P.token_digest(token) + ".json"))
        except FileNotFoundError:
            pass

    def blocked_reason(self, job_id: str) -> str | None:
        path = self.state / "blocked" / f"{job_id}.json"
        try:
            return json.loads(path.read_text()).get("reason") or "blocked"
        except FileNotFoundError:
            return None
        except ValueError:
            return "blocked"

    # -- dispatch switch -----------------------------------------------------------------------------
    def disable_dispatch(self, reason: str = "manual") -> None:
        _atomic_write(self.switch_path, json.dumps({"reason": reason, "ts": time.time()}).encode())

    def dispatch_disabled(self) -> bool:
        return self.switch_path.exists()

    # -- log -----------------------------------------------------------------------------------------
    def records(self, job_id: str | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            fh = open(self.log_path, "rb")
        except FileNotFoundError:
            return out
        with fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if job_id is None or rec.get("job_id") == job_id:
                    out.append(rec)
        return out

    def manifest(self) -> dict[str, Any]:
        """Hashes that pin the broker into the campaign manifest (§O)."""
        return {"mitmdump": str(self.mitmdump), "addon_sha256": sha256_file(ADDON),
                "policy_module_sha256": sha256_file(POLICY_MODULE),
                "policy_sha256": sha256_file(self.policy_path), "ca_cert_sha256": sha256_file(self.ca_cert)
                if self.ca_cert.exists() else None, "mitmproxy_version": mitmproxy_version(self.mitmdump),
                "prices_sha256": sha256_file(self.prices)}

    def export_evidence(self, dest: str | os.PathLike[str]) -> dict[str, Any]:
        """Copy the campaign's broker evidence into ``dest`` for sealing (§M): the log, the effective
        policy, the declared prices, every issued grant, the blocked markers and a broker manifest
        (``broker-manifest.json``: :meth:`manifest` plus the sha256 of every exported file). The log
        holds sanitized metadata only; the seal secret-scans every exported file."""
        dest = Path(dest)
        for d in (dest, dest / "grants", dest / "blocked"):
            d.mkdir(parents=True, exist_ok=True)
        files = {"broker-log.jsonl": self.log_path, "policy.json": self.policy_path,
                 "prices.json": self.prices}
        for sub in ("issued", "blocked"):
            for f in sorted((self.state / sub).glob("*.json")):
                files[f"{'grants' if sub == 'issued' else sub}/{f.name}"] = f
        shas = {}
        for rel, src in sorted(files.items()):
            data = Path(src).read_bytes() if Path(src).exists() else b""
            (dest / rel).write_bytes(data)
            shas[rel] = hashlib.sha256(data).hexdigest()
        out = {**self.manifest(), "files": shas}
        (dest / "broker-manifest.json").write_text(json.dumps(out, indent=2, sort_keys=True))
        return out

    def remove_ca_key(self) -> None:
        """End of campaign: delete the CA private key material."""
        shutil.rmtree(self.confdir, ignore_errors=True)


def mitmproxy_version(mitmdump: Path = MITMDUMP) -> str | None:
    py = mitmdump.with_name("python")
    try:
        out = subprocess.run([str(py), "-I", "-c", "import mitmproxy.version as v; print(v.VERSION)"],
                             capture_output=True, text=True, timeout=30, env={"PATH": "/usr/bin:/bin"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def summarize_job(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Telemetry for one job from broker records (§J5). Missing usage is ``unknown``, never zero."""
    allowed = [r for r in records if r.get("event") in ("request", "ws_frame") and r.get("decision") == P.ALLOW]
    model = [r for r in allowed if r.get("model_request")]
    usages = [r for r in records if r.get("event") in ("response", "ws_server") and "usage" in r]
    reported = [r["usage"] for r in usages if r.get("usage")]
    tools: set[str] = set()
    for r in model:
        tools.update(t for t in (r.get("tools") or []) if t)
    totals: dict[str, int] | None = None
    if reported:
        totals = {}
        for u in reported:
            for k, v in u.items():
                totals[k] = totals.get(k, 0) + int(v)
    return {
        "upstream_requests": len(allowed),
        "model_requests": len(model),
        "blocked": [{"host": r.get("host"), "path": r.get("path"), "reason": r.get("reason"),
                     "decision": r.get("decision")} for r in records
                    if r.get("decision") in (P.BLOCK, P.BLOCK_EXPECTED)],
        "models": sorted({r.get("model") for r in model if r.get("model")}),
        "declared_tools": sorted(tools),
        "tools_key_present": any(r.get("tools_key") for r in model),
        "output_field": sorted({r.get("output_field") for r in model if r.get("output_field")}),
        "max_input_bytes_seen": max((r.get("input_bytes") or 0 for r in model), default=0),
        "usage": totals if totals is not None else "unknown",
        "usage_records": len(reported),
        "usage_missing": len(model) - len(reported) if len(model) >= len(reported) else 0,
        "failed": next((r.get("reason") for r in records if r.get("event") == "job_failed"), None),
    }
