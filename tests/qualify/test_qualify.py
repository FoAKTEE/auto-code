"""qualify-host helpers (offline) and the full live run (``@pytest.mark.live``; set ZKQEC_LIVE=1)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from zkqec import qualify as Q

REPO = Path(__file__).resolve().parents[2]


def test_property_verdict():
    p = Q.Property("X", "t", "§", "d", Q.Check(True, "ok"), Q.Check(True, "ok"))
    assert p.verdict == "qualified"
    assert Q.Property("X", "t", "§", "d", Q.Check(True, "ok"), Q.Check(False, "no")).verdict == "unqualified"
    assert Q.Property("X", "t", "§", "d", Q.Check(None, "-"), Q.Check(True, "ok")).verdict == "unqualified"


def test_lock_parsing_on_real_lock():
    pins, unhashed = Q._parse_lock((REPO / "requirements.lock").read_text())
    assert len(pins) > 10 and not unhashed and "numpy" in pins
    _, bad = Q._parse_lock("foo==1.0\nbar==2.0 \\\n    --hash=sha256:" + "0" * 64 + "\n")
    assert bad == ["foo"]


def test_caps_reject_and_validate():
    assert Q._caps_reject()
    assert Q._validate({"answer": 5}, Q.SCHEMA) and not Q._validate({"answer": "5"}, Q.SCHEMA)


def test_markdown_render():
    rep = {"created_utc": "T", "host": "h", "campaign": "c", "seconds": 1, "live": False, "spend": {},
           "k0_enforceable": {}, "summary": {"qualified": 1, "unqualified": 1},
           "unqualified": [{"id": "B", "consequence": "c"}],
           "properties": [Q.Property("A", "a", "§", "d", Q.Check(True, "p"), Q.Check(True, "n"), note="nb").to_json(),
                          Q.Property("B", "b", "§", "d", Q.Check(False, "p"), Q.Check(None, "n")).to_json()]}
    md = Q._markdown(rep)
    assert "| `A` a | **qualified** |" in md and "**unqualified**" in md and "- `B`: c" in md and "nb" in md


def test_secret_scan_list_has_no_short_values():
    assert all(len(s) >= 20 for s in Q._secret_values())


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("ZKQEC_LIVE") != "1", reason="live: set ZKQEC_LIVE=1 (spends model calls)")
def test_qualify_host_live(tmp_path):
    rc = Q.main(["--out", str(tmp_path)])
    assert rc in (0, 2)
    assert list(tmp_path.glob("*/report.json"))
