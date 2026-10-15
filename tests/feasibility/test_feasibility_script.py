"""``scripts/feasibility_tables.py`` in tiny mode: output schema and measured invariants (§C1, §C2).

The kernel reports ``half_patterns`` from the formula ``Σ_{j<=t} C(n, j)``, computed before the
enumeration, not from a counter. The script's audit run reads the kernel's bucket table when
``sector_distance`` returns. These tests check the measured invariants: patterns stored = M, and
``Σ_s C(M_s, 2)`` = the kernel's pair counter = the timing run's pairs.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

from zkqec.verify import half_patterns
from zkqec.verify.distance import VERIFIER

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "feasibility_tables.py"

ROW_KEYS = {"code_id", "family", "subfamily", "n", "k", "t", "sector", "syndrome_rank",
            "half_patterns_M", "memory_bytes_estimate", "pair_work_lower_bound",
            "pair_work_upper_bound", "outcome", "kernel_half_patterns", "pairs_checked", "w_star",
            "interval", "measured", "audit", "production_preflight_ok", "production_outcome",
            "measurement_budget", "skip_reason"}
MEASURED_KEYS = {"cpu_s", "wall_s", "overhead_cpu_s", "peak_rss_bytes", "peak_rss_delta_bytes",
                 "child_cpu_s", "child_wall_s", "child_maxrss_bytes"}


def _load_script():
    spec = importlib.util.spec_from_file_location("feasibility_tables", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    out = tmp_path_factory.mktemp("feasibility")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--mode", "tiny", "--out-dir", str(out)],
                          capture_output=True, text=True, timeout=600, cwd=REPO)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    text = (out / "M1-certification.json").read_text()
    return json.loads(text), text, (out / "M1-certification.md").read_text()


def test_schema_host_facts_and_canonical_json(tiny):
    doc, text, _ = tiny
    assert doc["schema"] == "zkqec.feasibility.certification/v1"
    assert {"host", "config", "codes", "rows", "tasks", "fits", "frontier_measured",
            "measurement", "anomalies"} <= set(doc)
    host = doc["host"]
    for key in ("cpu_model", "logical_cpus", "physical_cores", "ram_bytes", "python", "numpy",
                "git_commit", "source_fingerprints", "child_env"):
        assert host[key], key
    assert host["l2_verifier"] == {"name": VERIFIER.name, "version": VERIFIER.version}
    assert host["child_env"]["OPENBLAS_NUM_THREADS"] == "1"
    assert doc["config"]["mode"] == "tiny" and doc["config"]["workers"] <= 32
    assert text == json.dumps(doc, sort_keys=True, indent=1) + "\n"         # canonical, sorted
    keys = [(r["family"], r["subfamily"], r["n"], r["code_id"], r["t"], r["sector"])
            for r in doc["rows"]]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    for r in doc["rows"]:
        assert ROW_KEYS <= set(r), ROW_KEYS - set(r)
    assert doc["anomalies"] == []


def test_completed_rows_meet_the_measured_invariants(tiny):
    doc, _, _ = tiny
    done = [r for r in doc["rows"] if r["outcome"] == "completed"]
    assert len(done) >= 6 and {r["code_id"] for r in done} == {"steane7", "bb72"}
    for r in done:
        n, t, m = r["n"], r["t"], r["half_patterns_M"]
        assert m == half_patterns(n, t)                       # preflight M
        assert r["kernel_half_patterns"] == m                 # formula, reported by the kernel
        a = r["audit"]
        assert a is not None and a["completion"] == "completed"
        assert a["patterns_enumerated"] == m                  # counted in the bucket table
        assert a["pairs_from_bucket_sizes"] == a["kernel_pair_counter"] == r["pairs_checked"]
        assert 0 <= r["pair_work_lower_bound"] <= r["pairs_checked"] <= r["pair_work_upper_bound"]
        assert 1 <= a["distinct_syndromes"] <= min(m, 2 ** r["syndrome_rank"])
        assert a["interval"] == r["interval"]
        lo, hi = r["interval"]                                 # §C2 completion rule
        if r["w_star"] is not None:
            assert r["w_star"] <= 2 * t and lo == hi == r["w_star"]
        else:
            assert lo == 2 * t + 1 <= hi
        meas = r["measured"]
        assert MEASURED_KEYS <= set(meas)
        assert meas["cpu_s"] > 0 and meas["wall_s"] > 0 and meas["peak_rss_bytes"] > 0
        assert meas["child_cpu_s"] >= meas["cpu_s"] * 0.9


def test_reference_values(tiny):
    doc, _, _ = tiny
    rows = {(r["code_id"], r["t"], r["sector"]): r for r in doc["rows"]}
    for s in "XZ":
        assert rows[("steane7", 2, s)]["interval"] == [3, 3]          # d = 3, never d >= 5
        assert rows[("steane7", 2, s)]["w_star"] == 3
        assert rows[("bb72", 2, s)]["interval"][0] == 5               # nothing through 4
    assert {r["k"] for r in doc["rows"] if r["code_id"] == "bb72"} == {12}


def test_cases_beyond_the_caps_are_skipped_with_their_estimates(tiny):
    doc, _, _ = tiny
    caps = doc["config"]
    skipped = [r for r in doc["rows"] if r["outcome"] == "skipped"]
    assert skipped, "tiny mode must exercise the preflight skip"
    for r in skipped:
        assert r["measured"] is None and r["interval"] is None and r["skip_reason"]
        assert (r["half_patterns_M"] > caps["max_patterns"]
                or r["memory_bytes_estimate"] > caps["case_mem_gib"] * 2**30
                or "predicted CPU" in r["skip_reason"])


def test_task_mode_matches_sector_runs(tiny):
    doc, _, md = tiny
    checked = 0
    for task in doc["tasks"]:
        run = task["measured_task_run"]
        if run is None:
            continue
        checked += 1
        for s in "XZ":
            row = next(r for r in doc["rows"] if r["code_id"] == task["code_id"]
                       and r["t"] == task["t"] and r["sector"] == s)
            assert run["intervals"][s] == row["interval"]
            assert run["completion"][s] == "completed"
    assert checked == 2
    for heading in ("## Host and method", "## Throughput and estimate-vs-actual",
                    "## Largest certifiable (n, t)", "## Per-family tables", "## Codes"):
        assert heading in md


def test_plan_is_deterministic():
    mod = _load_script()
    caps, prod = mod.MODES["tiny"], mod.production_budget()
    first = mod.plan(mod.catalogue("tiny"), caps, prod)
    second = mod.plan(mod.catalogue("tiny"), caps, prod)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
