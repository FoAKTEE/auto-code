"""Sealed campaign bundles and offline audit (appendix §M; owner decision D9).

Archive layout (``ARCHIVE``, outside the repo, durable):
  cas/<sha256>.tar.zst          content-addressed closures: runtime, wheelhouse, evaluator snapshot
  cas/<sha256>.json             sidecar metadata for each closure
  bundles/<campaign>/<sha256>.tar.zst   sealed campaign bundles

A bundle holds the campaign's complete transitive evidence: manifest (with CAS refs and the ledger
head), a consistent ledger snapshot (which carries the frozen campaign, evaluation membership and
settlement events), every stored artifact, the report, and any call records / broker logs.
``audit_bundle`` rebuilds everything in an empty directory from the archived runtime + wheels only
(pip ``--no-index``), then runs the inner audit inside a bwrap network namespace: chain + apply
invariants + projections + event-referenced artifact hashes (``Admission.verify``), every stored
artifact and redaction record re-hashed, every candidate component and call-record artifact
reference traversed, the operational job tables reconciled with the ``job_settled`` events and the
frozen caps, call-record usage reconciled with the settled measurements, byte-level replay of every
execution, and the report lint against the ledger only.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import zstandard

from ..store.durable import atomic_copy, atomic_write_bytes, atomic_writer, makedirs_durable

ARCHIVE = Path(os.environ.get("ZKQEC_ARCHIVE", "/data/haiyangw/claude/zkqec-archive"))
RUNTIME_DIR = Path(os.environ.get(
    "ZKQEC_RUNTIME", "/data/haiyangw/claude/zkqec-runtime/python/cpython-3.10.22-linux-x86_64-gnu"))
REPO = Path(__file__).resolve().parents[3]


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tar_zst(src_dir: Path, out: Path, arcname: str) -> None:
    """Deterministic tar (sorted, zeroed owners/mtimes) + zstd, published durably at ``out``
    (temp in the destination directory → fsync → rename → fsync directory; §L)."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for p in sorted(src_dir.rglob("*")):
            info = tar.gettarinfo(str(p), arcname=f"{arcname}/{p.relative_to(src_dir)}")
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
            if p.is_file() and not p.is_symlink():
                with open(p, "rb") as f:
                    tar.addfile(info, f)
            else:
                tar.addfile(info)
    with atomic_writer(out) as f:
        f.write(zstandard.ZstdCompressor(level=10).compress(raw.getvalue()))


def _untar_zst(path: Path, dest: Path) -> None:
    """Extract with the PEP 706 ``data`` filter: no absolute paths, no escaping links, no special
    files, no setuid bits (relative in-tree symlinks such as python3 -> python3.10 are kept)."""
    data = zstandard.ZstdDecompressor().decompress(path.read_bytes(), max_output_size=8 << 30)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        tar.extractall(dest, filter="data")


def _cas_put(tmp_file: Path, meta: dict, archive: Path) -> str:
    """Publish a closure into the CAS through a destination-filesystem temp (never a move)."""
    sha = _sha_file(tmp_file)
    dst = archive / "cas" / f"{sha}.tar.zst"
    if not (dst.is_file() and _sha_file(dst) == sha):
        atomic_copy(tmp_file, dst)
    atomic_write_bytes(archive / "cas" / f"{sha}.json",
                       json.dumps({**meta, "sha256": sha}, indent=2).encode())
    return sha


def ensure_closures(archive: Path = ARCHIVE, *, commit: str | None = None) -> dict[str, str]:
    """Runtime, wheelhouse and evaluator-snapshot closures in the CAS (cached by content)."""
    makedirs_durable(archive)
    cache = archive / "cas" / "closures-index.json"
    index = json.loads(cache.read_text()) if cache.exists() else {}
    lock_sha = _sha_file(REPO / "requirements.lock")
    commit = commit or subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], check=True,
                                      capture_output=True, text=True).stdout.strip()
    out = {}
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        key = f"runtime:{_sha_file(RUNTIME_DIR / 'bin' / 'python3.10')}"
        if key not in index:
            f = td / "rt.tar.zst"; _tar_zst(RUNTIME_DIR, f, "runtime")
            glibc = subprocess.run(["ldd", "--version"], capture_output=True, text=True).stdout.splitlines()[0]
            index[key] = _cas_put(f, {"kind": "runtime", "python": "3.10.22", "glibc_baseline": glibc}, archive)
        out["runtime"] = index[key]
        key = f"wheelhouse:{lock_sha}"
        if key not in index:
            wh, py = td / "wh", str(RUNTIME_DIR / "bin" / "python3.10")
            # Download exactly the locked artifacts (hash-checked), then build wheels for any
            # sdist-only package (e.g. pynauty 2.8.8.1 has no cp310 wheel on PyPI). The wheelhouse
            # is content-addressed in the CAS; its sha256 is what the audit trusts.
            subprocess.run([py, "-m", "pip", "download", "-q", "--require-hashes", "--no-deps",
                            "-r", str(REPO / "requirements.lock"), "-d", str(wh)], check=True)
            built = []
            for sdist in sorted(list(wh.glob("*.tar.gz")) + list(wh.glob("*.zip"))):
                subprocess.run([py, "-m", "pip", "wheel", "-q", "--no-deps", str(sdist), "-w", str(wh)],
                               check=True)
                built.append(sdist.name)
                sdist.unlink()
            wheels = {w.name: _sha_file(w) for w in sorted(wh.glob("*.whl"))}
            f = td / "wh.tar.zst"; _tar_zst(wh, f, "wheelhouse")
            index[key] = _cas_put(f, {"kind": "wheelhouse", "lock_sha256": lock_sha,
                                      "built_from_sdist": built, "wheels": wheels}, archive)
        out["wheelhouse"] = index[key]
        key = f"evaluator:{commit}"
        if key not in index:
            ev = td / "ev"; ev.mkdir()
            tar_bytes = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", commit,
                                        "src", "pyproject.toml", "requirements.lock"],
                                       check=True, capture_output=True).stdout
            with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as t:
                t.extractall(ev)
            f = td / "ev.tar.zst"; _tar_zst(ev, f, "evaluator")
            index[key] = _cas_put(f, {"kind": "evaluator", "git_commit": commit}, archive)
        out["evaluator"] = index[key]
    atomic_write_bytes(cache, json.dumps(index, indent=2, sort_keys=True).encode())
    return {**out, "git_commit": commit, "lock_sha256": lock_sha}


def scan_bundle_inputs(root: Path, sanitizer=None) -> list[str]:
    """Secret scan of every campaign-derived bundle input (§M: secrets never enter a bundle).

    Text files are scanned with the sanitizer's text and JSON rules, any other file byte-wise
    (latin-1); a hit is reported by relative path and rule names, never by content. Content-addressed
    software closures are verified by hash instead and are not passed here."""
    from zkqec.store.sanitize import DEFAULT_SANITIZER
    sanitizer = sanitizer or DEFAULT_SANITIZER
    hits = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            hits.append(f"{p.relative_to(root)}: symlink in bundle input")
            continue
        if not p.is_file():
            continue
        data = p.read_bytes()
        kind = "json" if p.suffix == ".json" else "text"
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            kind = "binary"
        try:
            _, counts = sanitizer.sanitize_bytes(data, kind)
        except Exception as exc:                                  # SecretDetected for binary
            counts = {type(exc).__name__: 1}
        if counts:
            hits.append(f"{p.relative_to(root)}: {sorted(counts)}")
    return hits


def seal(run_dir: Path, campaign_id: str, ledger_path: Path, store_root: Path, report_md: str,
         extra_dirs: dict[str, Path] | None = None,
         archive: Path = ARCHIVE, commit: str | None = None) -> dict[str, Any]:
    """Write a sealed bundle; return its index entry (bundle sha256, path, ledger head, closures)."""
    closures = ensure_closures(archive, commit=commit)
    with tempfile.TemporaryDirectory() as td:
        b = Path(td) / "bundle"; b.mkdir()
        src = sqlite3.connect(str(ledger_path)); dst = sqlite3.connect(str(b / "ledger.db"))
        src.backup(dst); dst.close()
        head = src.execute("SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        src.close()
        shutil.copytree(store_root, b / "store", symlinks=False)
        (b / "report.md").write_text(report_md)
        for name, d in (extra_dirs or {}).items():
            if d.exists():
                shutil.copytree(d, b / name, symlinks=False)
        manifest = {"campaign_id": campaign_id, "ledger_head": {"seq": head[0], "hash": head[1]},
                    "closures": closures, "format": "zkqec-bundle-v1"}
        (b / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
        hits = scan_bundle_inputs(b)
        if hits:
            from zkqec.store import SecretDetected
            raise SecretDetected(f"refusing to seal: secret-like content in {hits[:10]}")
        out_dir = archive / "bundles" / campaign_id
        tmp = Path(td) / "bundle.tar.zst"; _tar_zst(b, tmp, "bundle")
        sha = _sha_file(tmp)
        final = out_dir / f"{sha}.tar.zst"
        atomic_copy(tmp, final)          # destination temp + fsync + rename + dir fsync (§L)
    entry = {"campaign_id": campaign_id, "bundle_sha256": sha, "path": str(final),
             "ledger_head": manifest["ledger_head"], "closures": closures}
    return entry


def audit_bundle(bundle: Path, archive: Path = ARCHIVE, workdir: Path | None = None,
                 offline: bool = True) -> dict[str, Any]:
    """Reconstruct in an empty directory from archived closures only and run the inner audit."""
    workdir = Path(workdir or tempfile.mkdtemp(prefix="zkqec-audit-")).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    if any(workdir.iterdir()):
        raise ValueError("audit workdir must be empty")
    if bundle.name != f"{_sha_file(bundle)}.tar.zst":
        raise ValueError("bundle file does not match its content address")
    _untar_zst(bundle, workdir)
    manifest = json.loads((workdir / "bundle" / "manifest.json").read_text())
    cl = manifest["closures"]
    for kind in ("runtime", "wheelhouse", "evaluator"):
        f = archive / "cas" / f"{cl[kind]}.tar.zst"
        if _sha_file(f) != cl[kind]:
            raise ValueError(f"CAS closure {kind} does not match its content address")
        _untar_zst(f, workdir)
    py = workdir / "runtime" / "bin" / "python3.10"
    subprocess.run([str(py), "-m", "venv", str(workdir / "venv")], check=True)
    vpy = workdir / "venv" / "bin" / "python"
    pins = [ln.split(" ")[0].strip() for ln in
            (workdir / "evaluator" / "requirements.lock").read_text().splitlines()
            if "==" in ln and not ln.lstrip().startswith(("#", "--"))]
    subprocess.run([str(vpy), "-m", "pip", "install", "-q", "--no-index", "--find-links",
                    str(workdir / "wheelhouse"), *pins], check=True)
    cmd = [str(vpy), "-I", "-c",
           "import sys; sys.path.insert(0, sys.argv[1]); "
           "from zkqec.report.bundle import audit_inner; "
           "import json; r = audit_inner(sys.argv[2]); print(json.dumps(r)); sys.exit(0 if r['ok'] else 1)",
           str(workdir / "evaluator" / "src"), str(workdir / "bundle")]
    if offline:
        cmd = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
               "--bind", str(workdir), str(workdir), "--unshare-net", "--die-with-parent", "--"] + cmd
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:                                            # noqa: BLE001
        result = {"ok": False, "problems": [f"inner audit failed: {proc.stderr[-2000:]}"]}
    result["workdir"] = str(workdir)
    result["offline"] = offline
    return result


def replay_executions(adm, reg, store) -> tuple[list[str], int, int]:
    """Byte-level replay of every execution: an ``admitted`` closure must be reproduced exactly
    (same receipt ids / identity codes); a closure of any other outcome must have no records, and
    an execution without a closure is a problem (it was never settled)."""
    from zkqec.admission.registry import ArtifactInput
    from zkqec.admission.service import _revalidate_results, _stamp
    from zkqec.contracts import content_hash

    problems: list[str] = []
    replayed = identities = 0
    conn = adm.ledger.conn
    for ex_id, in conn.execute("SELECT execution_id FROM executions ORDER BY event_seq").fetchall():
        ex, cl = adm.ledger.execution(ex_id), adm.ledger.closure(ex_id)
        if cl is None:
            problems.append(f"{ex_id}: execution of {ex['issuer']} has no closure")
            continue
        if cl["outcome"] != "admitted":
            if cl["records"]:
                problems.append(f"{ex_id}: {cl['outcome']} closure lists records")
            continue
        try:
            spec = reg.get(ex["issuer"], ex["role"])
        except Exception as exc:                                 # noqa: BLE001
            problems.append(f"{ex_id}: {exc}"); continue
        if spec.version != ex["issuer_version"]:
            problems.append(f"{ex_id}: issuer version differs"); continue
        inputs = {k: ArtifactInput(sha, store.get_bytes(sha)) for k, sha in ex["inputs"].items()}
        if ex["role"] == "verifier":
            results = _revalidate_results(spec.fn(**inputs))
            ids = sorted({content_hash(_stamp(ex, r)) for r in results})
            if ids != sorted(cl["records"]):
                problems.append(f"{ex_id}: replay of {ex['issuer']} gives different receipts")
            replayed += 1
        else:
            code = spec.fn(**inputs)
            rows = conn.execute("SELECT code FROM identities WHERE execution_id=?", (ex_id,)).fetchall()
            if not rows or any(r[0] != code for r in rows):
                problems.append(f"{ex_id}: identity replay differs")
            identities += 1
    return problems, replayed, identities


def _call_record_problems(store, sha: str, what: str) -> tuple[list[str], dict | None]:
    """Verify a call record and every artifact it references (request, prompt, schema, output)."""
    try:
        rec = store.get_json(sha)
    except Exception as exc:                                     # noqa: BLE001
        return [f"{what} call record {sha}: {type(exc).__name__}"], None
    out = []
    arts = rec.get("artifacts", {}) if isinstance(rec, dict) else {}
    for name, ref in sorted(arts.items() if isinstance(arts, dict) else []):
        try:
            store.verify(ref)
        except Exception as exc:                                 # noqa: BLE001
            out.append(f"{what} call record {sha[:12]} artifact {name} {ref}: {type(exc).__name__}")
    return out, rec


def audit_campaign(adm, store, campaign_id: str) -> list[str]:
    """Campaign-level audit over the ledger events: artifact references beyond the events
    themselves, call-record accounting, and the operational tables vs ``job_settled`` (§K, §L, §M)."""
    from zkqec.contracts import DesignCandidate, UsageError, check_usage
    from zkqec.orchestrator.jobs import JobBook

    camp = adm.ledger.campaign(campaign_id)
    if camp is None:
        return [f"campaign {campaign_id} is not frozen in the ledger"]
    problems: list[str] = []
    for ev in adm.ledger.evaluations(campaign_id):
        try:
            cand = DesignCandidate.model_validate(store.get_json(ev["candidate"]))
        except Exception as exc:                                 # noqa: BLE001
            problems.append(f"evaluation {ev['evaluation_id'][:12]}: candidate "
                            f"{type(exc).__name__}"); continue
        refs = [r for r in (cand.E, cand.A, cand.C, cand.L, cand.P, cand.D, cand.F, cand.eta)
                if r not in ("absent", "unsupported")]
        refs += [x for op in cand.G for x in (op.circuit, op.basis, op.target_action)]
        for ref in refs:
            try:
                store.verify(ref)
            except Exception as exc:                             # noqa: BLE001
                problems.append(f"candidate {ev['candidate'][:12]} component {ref}: "
                                f"{type(exc).__name__}")
        if ev["call_record"]:
            problems += _call_record_problems(store, ev["call_record"], "evaluation")[0]
    settlements = adm.ledger.settlements(campaign_id)
    for s in settlements:
        if not s["result"]:
            continue
        found, rec = _call_record_problems(store, s["result"], f"job {s['job_id'][:12]}")
        problems += found
        if not isinstance(rec, dict):
            continue
        if rec.get("job_id") != s["job_id"]:
            problems.append(f"job {s['job_id']}: its call record names job {rec.get('job_id')}")
        if "usage" in rec:
            try:
                usage = check_usage(rec["usage"] or None) or None
            except UsageError as exc:
                usage = f"invalid ({exc})"
            if usage != s["actual"]:
                problems.append(f"job {s['job_id']}: call-record usage differs from the settled "
                                "measurement")
        broker = rec.get("broker") if isinstance(rec.get("broker"), dict) else {}
        if "model_requests" in broker and (s["actual"] or {}).get("requests") is not None \
                and float(broker["model_requests"]) != s["actual"]["requests"]:
            problems.append(f"job {s['job_id']}: broker request count differs from the charge")
    book = JobBook.for_namespace(adm.ledger.conn, camp["namespace"])
    problems += book.reconcile(settlements, camp["caps"], confirmation=camp["confirmation"])
    return problems


def audit_inner(bundle_dir: str) -> dict[str, Any]:
    """Inside the reconstructed environment: verify ledger, store and accounting, replay
    executions, lint the report (all from the bundle's ledger)."""
    from zkqec.admission import Admission
    from zkqec.pipeline import build_registry
    from zkqec.report.render import lint
    from zkqec.store import ArtifactStore

    b = Path(bundle_dir)
    problems: list[str] = []
    manifest = json.loads((b / "manifest.json").read_text())
    store = ArtifactStore(b / "store")
    reg = build_registry(store)
    adm = Admission(b / "ledger.db", store, reg)        # refuses if the evaluator registry differs
    try:
        v = adm.verify()
        if not v.ok:
            problems += [f"ledger: {p}" for p in v.problems]
        head = adm.ledger.head()
        if {"seq": head[0], "hash": head[1]} != manifest["ledger_head"]:
            problems.append("ledger head differs from the sealed manifest (truncation or tamper)")
        problems += [f"store: {p}" for p in store.verify_all()]
        problems += audit_campaign(adm, store, manifest["campaign_id"])
        replay_problems, replayed, identities = replay_executions(adm, reg, store)
        problems += replay_problems
        problems += lint((b / "report.md").read_text(), adm, manifest["campaign_id"])
    finally:
        adm.close()
    return {"ok": not problems, "problems": problems, "replayed_executions": replayed,
            "replayed_identities": identities, "ledger_events": head[0]}
