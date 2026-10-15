"""Sealed campaign bundles and offline audit (appendix §M; owner decision D9).

Archive layout (``ARCHIVE``, outside the repo, durable):
  cas/<sha256>.tar.zst          content-addressed closures: runtime, wheelhouse, evaluator snapshot
                                (a build cache; every bundle embeds its own copies)
  cas/<sha256>.json             sidecar metadata for each closure
  bundles/<campaign>/<sha256>.tar.zst   sealed campaign bundles

Bundle format ``zkqec-bundle-v2`` is self-contained (§M): ``closures/{runtime,wheelhouse,
evaluator}.tar.zst`` are embedded with their sha256 and sidecar metadata (glibc baseline, wheel
hashes, git commit) in the manifest, so ``audit_bundle`` needs the bundle file alone: no repo, no
venv, no archive and no network. v1 bundles (closures only in the archive CAS) are still audited
through the archive.

A bundle holds the campaign's complete transitive evidence: manifest (with CAS refs and the ledger
head), a consistent ledger snapshot (which carries the frozen campaign, evaluation membership and
settlement events), every stored artifact, the report, the call records and, for a live campaign,
the broker evidence (``broker/``: sanitized log, effective policy, declared prices, issued grants,
markers, broker manifest).
``audit_bundle`` rebuilds everything in an empty directory from the archived runtime + wheels only
(pip ``--no-index``), then runs the inner audit inside a bwrap network namespace: chain + apply
invariants + projections + event-referenced artifact hashes (``Admission.verify``), every stored
artifact and redaction record re-hashed, every candidate component and call-record artifact
reference traversed, the operational job tables reconciled with the ``job_settled`` events and the
frozen caps, call-record usage reconciled with the settled measurements, byte-level replay of every
execution, the §I control suite re-run from the frozen campaign's inputs with byte-identical evidence
required, the broker evidence reconciled with the frozen manifest, the call records and the
settlements (:func:`audit_broker`), and the report lint against the ledger only.

The confirmation runs after sealing. Its settlement and the campaign's final totals are published
next to the bundle as ``<bundle sha256>.confirmation.json`` (:func:`confirmation_supplement`): the
ledger events appended after the sealed head, hash-chained from it, so :func:`check_confirmation`
verifies them against the bundle alone (Codex M1 milestone review B6).
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


def _tar_zst(src_dir: Path, out: Path, arcname: str, *, level: int = 10) -> None:
    """Deterministic tar (sorted, zeroed owners/mtimes) streamed through zstd, published durably at
    ``out`` (temp in the destination directory → fsync → rename → fsync directory; §L)."""
    with atomic_writer(out) as f:
        with zstandard.ZstdCompressor(level=level).stream_writer(f, closefd=False) as zf:
            with tarfile.open(fileobj=zf, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                for p in sorted(src_dir.rglob("*")):
                    info = tar.gettarinfo(str(p), arcname=f"{arcname}/{p.relative_to(src_dir)}")
                    info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
                    if p.is_file() and not p.is_symlink():
                        with open(p, "rb") as fin:
                            tar.addfile(info, fin)
                    else:
                        tar.addfile(info)


def _untar_zst(path: Path, dest: Path) -> None:
    """Stream-extract with the PEP 706 ``data`` filter: no absolute paths, no escaping links, no
    special files, no setuid bits (relative in-tree symlinks such as python3 -> python3.10 are
    kept)."""
    with open(path, "rb") as f:
        reader = zstandard.ZstdDecompressor().stream_reader(f)
        with tarfile.open(fileobj=reader, mode="r|") as tar:
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


CLOSURE_KINDS = ("runtime", "wheelhouse", "evaluator")


def scan_bundle_inputs(root: Path, sanitizer=None, *, skip: tuple[str, ...] = ("closures",)
                       ) -> list[str]:
    """Secret scan of every campaign-derived bundle input (§M: secrets never enter a bundle).

    Text files are scanned with the sanitizer's text and JSON rules, any other file byte-wise
    (latin-1); a hit is reported by relative path and rule names, never by content. Content-addressed
    software closures are verified by hash instead and are not passed here."""
    from zkqec.store.sanitize import DEFAULT_SANITIZER
    sanitizer = sanitizer or DEFAULT_SANITIZER
    hits = []
    for p in sorted(root.rglob("*")):
        if p.relative_to(root).parts[:1] and p.relative_to(root).parts[0] in skip:
            continue                                  # content-addressed software, hash-verified
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
         archive: Path = ARCHIVE, commit: str | None = None,
         out_dir: Path | None = None) -> dict[str, Any]:
    """Write a sealed, self-contained bundle (closures embedded) to ``out_dir`` (default
    ``archive/bundles/<campaign>``); return its index entry."""
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
        hits = scan_bundle_inputs(b)              # campaign evidence, before anything is added
        if hits:
            from zkqec.store import SecretDetected
            raise SecretDetected(f"refusing to seal: secret-like content in {hits[:10]}")
        (b / "closures").mkdir()
        closure_files, closure_meta = {}, {}
        for kind in CLOSURE_KINDS:
            sha = closures[kind]
            src = archive / "cas" / f"{sha}.tar.zst"
            if _sha_file(src) != sha:
                raise ValueError(f"CAS closure {kind} does not match its content address")
            shutil.copyfile(src, b / "closures" / f"{kind}.tar.zst")
            closure_files[kind] = f"closures/{kind}.tar.zst"
            side = archive / "cas" / f"{sha}.json"
            closure_meta[kind] = json.loads(side.read_text()) if side.exists() else {}
        manifest = {"campaign_id": campaign_id, "ledger_head": {"seq": head[0], "hash": head[1]},
                    "closures": closures, "closure_files": closure_files,
                    "closure_meta": closure_meta, "format": "zkqec-bundle-v2"}
        (b / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
        if scan_bundle_inputs(b):                 # the manifest (closures are hash-verified)
            from zkqec.store import SecretDetected
            raise SecretDetected("refusing to seal: secret-like content in the manifest")
        out_dir = Path(out_dir) if out_dir is not None else archive / "bundles" / campaign_id
        tmp = Path(td) / "bundle.tar.zst"; _tar_zst(b, tmp, "bundle", level=3)
        sha = _sha_file(tmp)
        final = out_dir / f"{sha}.tar.zst"
        atomic_copy(tmp, final)          # destination temp + fsync + rename + dir fsync (§L)
    entry = {"campaign_id": campaign_id, "bundle_sha256": sha, "path": str(final),
             "ledger_head": manifest["ledger_head"], "closures": closures,
             "format": manifest["format"]}
    return entry


def audit_bundle(bundle: Path, archive: Path | None = None, workdir: Path | None = None,
                 offline: bool = True, supplement: Path | None = None) -> dict[str, Any]:
    """Reconstruct in an empty directory from the bundle file alone and run the inner audit.

    v2 bundles embed their closures; ``archive`` is consulted only for v1 bundles. A confirmation
    ``supplement`` is checked against the bundle as well."""
    workdir = Path(workdir or tempfile.mkdtemp(prefix="zkqec-audit-")).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    if any(workdir.iterdir()):
        raise ValueError("audit workdir must be empty")
    if bundle.name != f"{_sha_file(bundle)}.tar.zst":
        raise ValueError("bundle file does not match its content address")
    _untar_zst(bundle, workdir)
    manifest = json.loads((workdir / "bundle" / "manifest.json").read_text())
    cl = manifest["closures"]
    for kind in CLOSURE_KINDS:
        if "closure_files" in manifest:
            if manifest["closure_files"].get(kind) != f"closures/{kind}.tar.zst":
                raise ValueError(f"bundle closure path for {kind} is not closures/{kind}.tar.zst")
            f = workdir / "bundle" / "closures" / f"{kind}.tar.zst"
        else:                                                     # v1: closures in the CAS
            f = Path(archive or ARCHIVE) / "cas" / f"{cl[kind]}.tar.zst"
        if not f.is_file() or f.is_symlink() or _sha_file(f) != cl[kind]:
            raise ValueError(f"closure {kind} does not match its content address")
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
    if supplement is not None:
        extra = [f"confirmation: {p}" for p in check_confirmation(bundle, supplement)]
        result["problems"] = list(result.get("problems", [])) + extra
        result["ok"] = bool(result.get("ok")) and not extra
    return result


# --- confirmation supplement (§K, §M) ---------------------------------------------------------------
CONFIRMATION_FORMAT = "zkqec-confirmation-v1"


def confirmation_supplement(adm, campaign_id: str, entry: dict, job_id: str, totals: dict,
                            audit: dict) -> dict[str, Any]:
    """The confirmation's settlement and the final totals, linked to the sealed bundle: every event
    appended after the sealed ledger head (hash-chained from it) plus the totals per unit."""
    head = entry["ledger_head"]
    events = [{"seq": e.seq, "prev_hash": e.prev_hash, "hash": e.hash, "kind": e.kind,
               "payload": e.payload} for e in adm.ledger.events() if e.seq > head["seq"]]
    last = adm.ledger.head()
    return {"format": CONFIRMATION_FORMAT, "campaign_id": campaign_id,
            "bundle_sha256": entry["bundle_sha256"], "bundle_ledger_head": head,
            "confirmation_job": job_id, "events": events,
            "ledger_head": {"seq": last[0], "hash": last[1]},
            "audit": {"ok": bool(audit.get("ok")), "problems": list(audit.get("problems", []))[:50]},
            "final_totals": totals}


def publish_confirmation(entry: dict, doc: dict) -> tuple[Path, str]:
    """Publish the supplement next to the bundle (durably, secret-scanned); return (path, sha256)."""
    from zkqec.contracts import canonical_json
    from zkqec.store import SecretDetected
    from zkqec.store.sanitize import DEFAULT_SANITIZER
    if DEFAULT_SANITIZER.detect_obj(doc):
        raise SecretDetected("refusing to publish a confirmation supplement with secret-like content")
    data = canonical_json(doc)
    path = Path(entry["path"]).with_name(f"{entry['bundle_sha256']}.confirmation.json")
    atomic_write_bytes(path, data)
    return path, hashlib.sha256(data).hexdigest()


def _bundle_members(bundle: Path, names: set[str], dest: Path) -> dict[str, Path]:
    """Stream the bundle once and extract only ``names`` (regular files) into ``dest``."""
    out: dict[str, Path] = {}
    with open(bundle, "rb") as f:
        reader = zstandard.ZstdDecompressor().stream_reader(f)
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for m in tar:
                if m.name in names and m.isfile():
                    target = dest / Path(m.name).name
                    with open(target, "wb") as fout:
                        shutil.copyfileobj(tar.extractfile(m), fout)
                    out[m.name] = target
    return out


def check_confirmation(bundle: Path, supplement: Path) -> list[str]:
    """A confirmation supplement against its sealed bundle: same bundle hash and sealed head, events
    chained from that head, exactly the frozen confirmation job settled under the settlement rule,
    and final totals equal to the sealed settlements plus the supplement's."""
    from zkqec.admission.ledger import event_hash
    from zkqec.contracts import UNITS, UsageError, check_usage
    from zkqec.contracts.jobs import charge_for
    try:
        doc = json.loads(Path(supplement).read_text())
    except (OSError, ValueError) as exc:
        return [f"supplement unreadable: {type(exc).__name__}"]
    if not isinstance(doc, dict) or doc.get("format") != CONFIRMATION_FORMAT:
        return ["supplement has an unknown format"]
    problems: list[str] = []
    sha = _sha_file(bundle)
    if bundle.name != f"{sha}.tar.zst" or doc.get("bundle_sha256") != sha:
        problems.append("supplement does not name this bundle's sha256")
    with tempfile.TemporaryDirectory() as td:
        got = _bundle_members(bundle, {"bundle/manifest.json", "bundle/ledger.db"}, Path(td))
        if len(got) != 2:
            return problems + ["bundle lacks its manifest or ledger"]
        manifest = json.loads(got["bundle/manifest.json"].read_text())
        conn = sqlite3.connect(f"file:{got['bundle/ledger.db']}?mode=ro", uri=True)
        try:
            sealed = [(k, json.loads(p)) for k, p in conn.execute(
                "SELECT kind, payload FROM events ORDER BY seq")]
            confirm_jobs = [r[0] for r in conn.execute(
                "SELECT job_id FROM ops_jobs WHERE kind='confirm' AND state='reserved'")]
        finally:
            conn.close()
    cid = manifest.get("campaign_id")
    head = manifest.get("ledger_head") or {}
    if doc.get("campaign_id") != cid or doc.get("bundle_ledger_head") != head:
        problems.append("supplement names another campaign or sealed ledger head")
    frozen = next((p for k, p in sealed if k == "campaign_frozen" and p.get("campaign_id") == cid), None)
    if frozen is None:
        return problems + ["the sealed ledger has no frozen campaign"]
    prev, seq = head.get("hash"), head.get("seq", 0)
    settled = []
    for ev in doc.get("events") or []:
        seq += 1
        if ev.get("seq") != seq or ev.get("prev_hash") != prev \
                or ev.get("hash") != event_hash(prev, ev.get("kind"), ev.get("payload")):
            problems.append(f"supplement event {ev.get('seq')} does not chain from the sealed head")
            break
        prev = ev["hash"]
        if ev["kind"] != "job_settled":
            problems.append(f"supplement event {seq} is a {ev['kind']}, not a settlement")
            continue
        settled.append(ev["payload"])
    if doc.get("ledger_head") != {"seq": seq, "hash": prev}:
        problems.append("supplement ledger head is not its last event")
    want = {u: float((frozen.get("confirmation") or {}).get(u, 0.0)) for u in UNITS}
    confirm = [p for p in settled if p.get("kind") == "confirm"]
    if len(settled) != 1 or len(confirm) != 1:
        problems.append(f"supplement settles {len(settled)} jobs, not exactly the confirmation")
    for p in confirm:
        if p.get("campaign_id") != cid or p.get("namespace") != frozen.get("namespace") \
                or p.get("job_id") not in confirm_jobs or p.get("job_id") != doc.get("confirmation_job"):
            problems.append("supplement settles a job that is not the sealed confirmation job")
        if p.get("reservation") != want:
            problems.append("confirmation settlement reservation differs from the frozen one")
        try:
            rule = charge_for(p.get("from_state"), p.get("reservation") or {},
                              check_usage(p.get("actual")))
        except (UsageError, TypeError, ValueError):
            rule = None
        if rule != p.get("charged"):
            problems.append("confirmation charge does not follow the settlement rule (§K)")
    if not (doc.get("audit") or {}).get("ok"):
        problems.append("the confirmation audit did not pass")
    spent = {u: 0.0 for u in UNITS}
    for p in [p for k, p in sealed if k == "job_settled" and p.get("campaign_id") == cid] + settled:
        for u in UNITS:
            spent[u] += float((p.get("charged") or {}).get(u, 0.0))
    totals = doc.get("final_totals") or {}
    for u in UNITS:
        t = totals.get(u) or {}
        if abs(float(t.get("spent", -1)) - spent[u]) > 1e-9 or float(t.get("open", -1)) != 0.0:
            problems.append(f"final totals for {u} differ from the sealed and confirmation "
                            "settlements (or leave a reservation open)")
    return problems


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
        if s["kind"] != "propose":            # e.g. a failed control suite's evidence (§I)
            try:
                store.verify(s["result"])
            except Exception as exc:                             # noqa: BLE001
                problems.append(f"job {s['job_id'][:12]} result {s['result']}: {type(exc).__name__}")
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
    problems += audit_operations(adm, store, campaign_id)
    return problems


def _unit_dict(d: dict) -> dict[str, float]:
    from zkqec.contracts import UNITS
    return {u: float(d.get(u, 0.0)) for u in UNITS}


def audit_operations(adm, store, campaign_id: str) -> list[str]:
    """The campaign's operational intents against the frozen campaign (Codex M1 milestone review
    #7; §K, §L, §M): the frozen confirmation and control reservations exist exactly once with their
    frozen reservations; every proposal job is an attempt of a frozen task under the frozen per-call
    reservation, and its input hash names the ``call_inputs`` intent its call record cites (request,
    system prompt and schema as sent, the frozen schema and contract); every verifier job names an
    execution with its inputs; every evaluation cites a settled call record with the same artifacts
    and a proposal that re-parses from that call's output. Anything else is an orphan."""
    from zkqec.contracts import content_hash
    from zkqec.contracts import Profile
    from zkqec.grammar.intake import parse_proposal
    from zkqec.orchestrator.campaign import CALL_INPUTS, grammar_for

    camp = adm.ledger.campaign(campaign_id)
    cfg = camp.get("config") or {}
    conn = adm.ledger.conn
    keys = ("job_id", "kind", "idem_key", "input_sha", "state", "reservation")
    rows = [dict(zip(keys, r)) for r in conn.execute(
        f"SELECT {', '.join(keys)} FROM ops_jobs WHERE campaign=? ORDER BY created_at",
        (camp["namespace"],))]
    for r in rows:
        r["reservation"] = _unit_dict(json.loads(r["reservation"]))
    settled = {s["job_id"]: s for s in adm.ledger.settlements(campaign_id)}
    problems: list[str] = []
    try:
        manifest = store.get_json(camp["manifest"])
    except Exception as exc:                                     # noqa: BLE001
        return [f"frozen manifest {camp['manifest']}: {type(exc).__name__}"]

    def required(what: str, match, reservation: dict | None) -> None:
        found = [r for r in rows if match(r)]
        if len(found) != 1:
            problems.append(f"the frozen {what} reservation has no operational job"
                            if not found else f"the frozen {what} reservation has {len(found)} jobs")
        elif reservation is not None and found[0]["reservation"] != _unit_dict(reservation):
            problems.append(f"job {found[0]['job_id']}: the {what} reservation differs from the frozen one")

    if camp.get("confirmation") is not None:
        required("confirmation", lambda r: r["kind"] == "confirm", camp["confirmation"])
    verifier = {"wall_seconds": cfg.get("verifier_wall_seconds", 0),
                "cpu_seconds": cfg.get("verifier_cpu_seconds", 0)}
    if cfg.get("controls"):
        key = f"controls:{cfg['controls']}"
        required(key, lambda r: r["idem_key"] == key and r["kind"] == "verify", verifier)
    tasks = {t["task_id"] for t in camp["tasks"]}
    executions: dict[tuple[str, str], int] = {}
    for issuer, inputs in conn.execute("SELECT issuer, inputs FROM executions"):
        k = (issuer, content_hash(json.loads(inputs)))
        executions[k] = executions.get(k, 0) + 1
    results: dict[str, dict] = {}
    for r in rows:
        job, idem = r["job_id"], r["idem_key"]
        if r["kind"] == "confirm" or idem == f"controls:{cfg.get('controls')}":
            continue
        if r["kind"] == "verify":
            if r["reservation"] != _unit_dict(verifier):
                problems.append(f"job {job}: verifier reservation differs from the frozen ceilings")
            if (idem.split(":", 1)[0], r["input_sha"]) not in executions:
                problems.append(f"job {job}: verifier job {idem!r} names no execution with its inputs")
            continue
        if r["kind"] != "propose":
            problems.append(f"job {job}: a {r['kind']} job is not part of this campaign's plan")
            continue
        task, _, attempt = idem.rpartition(":")
        if task not in tasks or not attempt.isdigit() \
                or int(attempt) >= int(cfg.get("max_proposals_per_task", 0)):
            problems.append(f"job {job}: {idem!r} is not an attempt of a frozen task")
            continue
        if "per_call" in cfg and r["reservation"] != _unit_dict(cfg["per_call"]):
            problems.append(f"job {job}: reservation differs from the frozen per-call reservation")
        s = settled.get(job)
        if s is None or not s["result"]:
            continue                                  # unsettled: flagged by the reconciliation
        try:
            rec = store.get_json(s["result"])
        except Exception:                                        # noqa: BLE001 (traversed above)
            continue
        results[s["result"]] = rec
        if "inputs" not in rec:                       # pre-intent records: the request hash itself
            if r["input_sha"] != rec.get("request_sha256"):
                problems.append(f"job {job}: input hash differs from its call record's request")
            continue
        if rec["inputs"] != r["input_sha"]:
            problems.append(f"job {job}: input hash is not the call-inputs intent its record cites")
            continue
        try:
            intent = store.get_json(r["input_sha"])
            arts = intent["artifacts"]
            request = store.get_bytes(arts["request"]).decode()
            contract = store.get_bytes(arts["system_prompt"]).decode()
        except Exception as exc:                                 # noqa: BLE001
            problems.append(f"job {job}: input intent {r['input_sha']}: {type(exc).__name__}")
            continue
        want = {"kind": CALL_INPUTS, "campaign_id": campaign_id, "task_id": task,
                "attempt": int(attempt), "reservation": cfg.get("per_call")}
        if any(intent.get(k) != v for k, v in want.items()) or {k: rec["artifacts"].get(k) for k in arts} != arts:
            problems.append(f"job {job}: input intent does not match its call record or attempt")
        if content_hash(request) != rec.get("request_sha256") \
                or arts.get("schema") != manifest.get("output_schema") \
                or content_hash(contract) != manifest.get("contract_sha256"):
            problems.append(f"job {job}: inputs as sent differ from the frozen schema, contract or "
                            "recorded request")
    families = tuple(grammar_for(Profile.model_validate(cfg["profile"]))) if "profile" in cfg else None
    for ev in adm.ledger.evaluations(campaign_id):
        if not ev["call_record"] or not ev["source"].startswith("designer:"):
            continue
        rec = results.get(ev["call_record"])
        if rec is None:
            problems.append(f"evaluation {ev['evaluation_id'][:12]}: cites no settled call record")
            continue
        if ev["artifacts"] != rec.get("artifacts"):
            problems.append(f"evaluation {ev['evaluation_id'][:12]}: artifacts differ from its call")
        try:
            schema = json.loads(store.get_bytes(manifest["output_schema"]))
            text = store.get_bytes(rec["artifacts"]["output_text"]).decode()
            again = parse_proposal(text, schema=schema, families=families).model_dump(mode="json")
            same = again == store.get_json(ev["proposal"])
        except Exception:                                        # noqa: BLE001
            same = False
        if not same:
            problems.append(f"evaluation {ev['evaluation_id'][:12]}: its proposal does not re-parse "
                            "from the call's output")
    return problems


def audit_sidecars(calls_dir: Path, adm, campaign_id: str) -> list[str]:
    """Each call-record sidecar is the stored bytes of a settled call's record, and each settled
    call has its sidecar (§M; orphan operational records, #7)."""
    if not calls_dir.is_dir():
        return []
    results = {s["result"] for s in adm.ledger.settlements(campaign_id)
               if s["kind"] == "propose" and s["result"]}
    found = {_sha_file(p): p.name for p in sorted(calls_dir.glob("*.json"))}
    problems = [f"calls/{name}: not the record of a settled call" for sha, name in sorted(found.items())
                if sha not in results]
    problems += [f"settled call record {sha} has no sidecar" for sha in sorted(results - set(found))]
    return problems


BROKER_PINS = ("addon_sha256", "policy_module_sha256", "policy_sha256", "prices_sha256",
               "ca_cert_sha256", "mitmproxy_version")
BROKER_SUMMARY = ("upstream_requests", "model_requests", "blocked", "models", "declared_tools", "usage",
                  "usage_records", "usage_missing", "failed")


def audit_broker(adm, store, campaign_id: str, broker_dir: Path) -> list[str]:
    """The sealed broker evidence against the frozen manifest, the call records and the settlements
    (§J5, §K, §M): the broker the campaign froze is the broker whose files were sealed; each call's
    model requests and usage are reconstructed from the sealed log (the authoritative telemetry) and
    must equal its call record and its settled measurement; incomplete usage must have settled
    uncertain; each call's issued grant is the per-request derivation of its reservation and the log
    shows it respected; a broker job without a call is an orphan."""
    from zkqec.agents.providers import call_ceilings
    from zkqec.broker.launcher import summarize_job
    from zkqec.contracts import Reservation

    camp = adm.ledger.campaign(campaign_id)
    manifest = store.get_json(camp["manifest"]) if camp else {}
    frozen = manifest.get("broker") if isinstance(manifest, dict) else None
    broker_dir = Path(broker_dir)
    if not frozen:
        return ["broker evidence sealed for a campaign that froze no broker"] \
            if broker_dir.exists() else []
    if not (broker_dir / "broker-manifest.json").is_file():
        return ["the frozen manifest names a broker but the bundle holds no broker evidence"]
    problems: list[str] = []
    bm = json.loads((broker_dir / "broker-manifest.json").read_text())
    listed = bm.get("files") or {}
    present = {str(p.relative_to(broker_dir)) for p in broker_dir.rglob("*") if p.is_file()}
    for rel in sorted(present - set(listed) - {"broker-manifest.json"}):
        problems.append(f"{rel}: not listed in the broker manifest")
    for rel, sha in sorted(listed.items()):
        f = broker_dir / rel
        if not f.is_file() or f.is_symlink() or _sha_file(f) != sha:
            problems.append(f"{rel}: missing or differs from the broker manifest")
    for key in BROKER_PINS:
        if bm.get(key) != frozen.get(key):
            problems.append(f"broker {key} differs from the frozen manifest")
    for rel, key in (("policy.json", "policy_sha256"), ("prices.json", "prices_sha256")):
        if not (broker_dir / rel).is_file() or _sha_file(broker_dir / rel) != frozen.get(key):
            problems.append(f"{rel} is not the frozen {key}")
    records, last = [], 0
    for n, line in enumerate((broker_dir / "broker-log.jsonl").read_bytes().splitlines(), 1):
        try:
            rec = json.loads(line)
            assert isinstance(rec, dict) and rec.get("seq", 0) > last
        except (ValueError, AssertionError):
            problems.append(f"broker log line {n} is unreadable or out of sequence")
            continue
        last = rec["seq"]
        records.append(rec)
        if rec.get("event") == "broker_start" and rec.get("policy_sha256") != frozen.get("policy_sha256"):
            problems.append(f"broker log line {n}: broker started under another policy")
    by_job: dict[str, list[dict]] = {}
    for rec in records:
        if rec.get("job_id"):
            by_job.setdefault(rec["job_id"], []).append(rec)
    calls: set[str] = set()
    for s in adm.ledger.settlements(campaign_id):
        if s["kind"] != "propose" or not s["result"]:
            continue
        tag = f"job {s['job_id']}"
        try:
            rec = store.get_json(s["result"])
        except Exception:                                        # noqa: BLE001 (audit_campaign)
            continue
        sj = rec.get("sandbox_job_id") if isinstance(rec, dict) else None
        if not isinstance(sj, str) or not sj:
            problems.append(f"{tag}: its call record names no broker job")
            continue
        calls.add(sj)
        summ = summarize_job(by_job.get(sj, []))
        mine = rec.get("broker") if isinstance(rec.get("broker"), dict) else {}
        for key in BROKER_SUMMARY:
            if mine.get(key) != summ[key]:
                problems.append(f"{tag}: call record broker {key} differs from the sealed broker log")
        actual = s["actual"] or {}
        if "requests" in actual and actual["requests"] != float(summ["model_requests"]):
            problems.append(f"{tag}: settled requests differ from the sealed broker log")
        complete = summ["usage"] != "unknown" and summ["usage_missing"] == 0
        if complete:
            u = summ["usage"]
            tokens = {"input_tokens": float(u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                                            + u.get("cache_read_input_tokens", 0)),
                      "output_tokens": float(u.get("output_tokens", 0))}
            if summ["model_requests"] and any(actual.get(k) != v for k, v in tokens.items()):
                problems.append(f"{tag}: settled tokens differ from the sealed broker usage")
        elif summ["model_requests"] and (s["from_state"] != "uncertain"
                                         or {"input_tokens", "output_tokens"} & set(actual)):
            problems.append(f"{tag}: usage is incomplete in the broker log but the call did not "
                            "settle uncertain at its reservation")
        try:
            grant = json.loads((broker_dir / "grants" / f"{sj}.json").read_text())
        except (OSError, ValueError):
            problems.append(f"{tag}: no issued grant for broker job {sj}")
            continue
        r = s["reservation"]
        try:
            c = call_ceilings(Reservation(usd=r["usd"], requests=int(r["requests"]),
                                          input_tokens=int(r["input_tokens"]),
                                          output_tokens=int(r["output_tokens"]),
                                          wall_seconds=int(r["wall_seconds"]),
                                          cpu_seconds=int(r["cpu_seconds"])))
            want = (c.max_model_requests, c.max_total_requests, c.max_input_bytes, c.max_output_tokens)
        except Exception:                                        # noqa: BLE001
            want = None
        if want != tuple(grant.get(k) for k in ("max_model_requests", "max_total_requests",
                                                "max_input_bytes", "max_output_tokens")):
            problems.append(f"{tag}: issued grant is not the per-request derivation of its reservation")
        allowed = [x for x in by_job.get(sj, []) if x.get("decision") == "allow"
                   and x.get("event") in ("request", "ws_frame") and x.get("model_request")]
        if len(allowed) > grant.get("max_model_requests", 0) or any(
                (x.get("input_bytes") or 0) > grant.get("max_input_bytes", 0)
                or (x.get("max_output_tokens") or 0) > grant.get("max_output_tokens", 0) for x in allowed):
            problems.append(f"{tag}: the sealed broker log shows a request beyond its grant")
    for sj in sorted(set(by_job) - calls):
        problems.append(f"broker job {sj} has broker records but no settled call")
    return problems


def audit_inner(bundle_dir: str) -> dict[str, Any]:
    """Inside the reconstructed environment: verify ledger, store and accounting, replay
    executions, reproduce the recorded controls, lint the report (all from the bundle's ledger)."""
    from zkqec.admission import Admission
    from zkqec.controls.record import audit_controls
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
        problems += [f"broker: {p}" for p in audit_broker(adm, store, manifest["campaign_id"],
                                                          b / "broker")]
        problems += audit_sidecars(b / "calls", adm, manifest["campaign_id"])
        replay_problems, replayed, identities = replay_executions(adm, reg, store)
        problems += replay_problems
        # scratch beside the bundle: the audit workdir is the only writable path offline
        control_problems, reproduced = audit_controls(
            adm, store, manifest["campaign_id"], b.parent / "controls-rerun")
        problems += [f"controls: {p}" for p in control_problems]
        problems += lint((b / "report.md").read_text(), adm, manifest["campaign_id"])
    finally:
        adm.close()
    return {"ok": not problems, "problems": problems, "replayed_executions": replayed,
            "replayed_identities": identities, "controls_reproduced": reproduced,
            "ledger_events": head[0]}
