"""Sealed campaign bundles and offline audit (appendix §M; owner decision D9).

Archive layout (``ARCHIVE``, outside the repo, durable):
  cas/<sha256>.tar.zst          content-addressed closures: runtime, wheelhouse, evaluator snapshot
  cas/<sha256>.json             sidecar metadata for each closure
  bundles/<campaign>/<sha256>.tar.zst   sealed campaign bundles

A bundle holds the campaign's complete transitive evidence: manifest (with CAS refs and the ledger
head), a consistent ledger snapshot, every stored artifact, the campaign record, the report, and any
call records / broker logs. ``audit_bundle`` rebuilds everything in an empty directory from the
archived runtime + wheels only (pip ``--no-index``), then runs the inner audit inside a bwrap
network namespace: chain + projections + artifact hashes (``Admission.verify``), byte-level replay of
every admitted verifier execution and identity, and the report lint.
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
    tmp = out.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        f.write(zstandard.ZstdCompressor(level=10).compress(raw.getvalue()))
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, out)


def _untar_zst(path: Path, dest: Path) -> None:
    """Extract with the PEP 706 ``data`` filter: no absolute paths, no escaping links, no special
    files, no setuid bits (relative in-tree symlinks such as python3 -> python3.10 are kept)."""
    data = zstandard.ZstdDecompressor().decompress(path.read_bytes(), max_output_size=8 << 30)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        tar.extractall(dest, filter="data")


def _cas_put(tmp_file: Path, meta: dict, archive: Path) -> str:
    sha = _sha_file(tmp_file)
    (archive / "cas").mkdir(parents=True, exist_ok=True)
    dst = archive / "cas" / f"{sha}.tar.zst"
    if not dst.exists():
        shutil.move(str(tmp_file), dst)
    (archive / "cas" / f"{sha}.json").write_text(json.dumps({**meta, "sha256": sha}, indent=2))
    return sha


def ensure_closures(archive: Path = ARCHIVE, *, commit: str | None = None) -> dict[str, str]:
    """Runtime, wheelhouse and evaluator-snapshot closures in the CAS (cached by content)."""
    archive.mkdir(parents=True, exist_ok=True)
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
    cache.write_text(json.dumps(index, indent=2, sort_keys=True))
    return {**out, "git_commit": commit, "lock_sha256": lock_sha}


def seal(run_dir: Path, campaign_id: str, ledger_path: Path, store_root: Path, report_md: str,
         campaign_record: dict, extra_dirs: dict[str, Path] | None = None,
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
        (b / "campaign.json").write_text(json.dumps(campaign_record, indent=2, sort_keys=True))
        for name, d in (extra_dirs or {}).items():
            if d.exists():
                shutil.copytree(d, b / name, symlinks=False)
        manifest = {"campaign_id": campaign_id, "ledger_head": {"seq": head[0], "hash": head[1]},
                    "closures": closures, "format": "zkqec-bundle-v1"}
        (b / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
        out_dir = archive / "bundles" / campaign_id; out_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(td) / "bundle.tar.zst"; _tar_zst(b, tmp, "bundle")
        sha = _sha_file(tmp)
        final = out_dir / f"{sha}.tar.zst"
        shutil.move(str(tmp), final)
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


def audit_inner(bundle_dir: str) -> dict[str, Any]:
    """Inside the reconstructed environment: verify ledger, replay executions, lint the report."""
    from zkqec.admission import Admission
    from zkqec.admission.registry import ArtifactInput
    from zkqec.admission.service import _revalidate_results, _stamp
    from zkqec.contracts import content_hash
    from zkqec.pipeline import build_registry
    from zkqec.report.render import CampaignRecord, lint
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
        replayed = identities = 0
        conn = adm.ledger.conn
        for ex_id, in conn.execute("SELECT execution_id FROM executions ORDER BY rowid"):
            ex, cl = adm.ledger.execution(ex_id), adm.ledger.closure(ex_id)
            if cl is None or cl["outcome"] != "admitted":
                continue
            spec = reg.get(ex["issuer"], ex["role"])
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
                if any(r[0] != code for r in rows):
                    problems.append(f"{ex_id}: identity replay differs")
                identities += 1
        camp = CampaignRecord.model_validate(json.loads((b / "campaign.json").read_text()))
        domain = camp.manifest.get("domain")
        problems += lint((b / "report.md").read_text(), adm, domain, camp)
    finally:
        adm.close()
    return {"ok": not problems, "problems": problems, "replayed_executions": replayed,
            "replayed_identities": identities, "ledger_events": head[0]}
