"""Kernel deviations KD-2..KD-5 are handled by supersession, not by patching the vendored kernel.

zkqec production code must not depend on the superseded QECForge components: `schemas.TaskSpec`
(freezes trotter_steps), `evidence.EvidenceLedger` (accepts caller-labelled verified records),
`agents.Coordinator` (conflates infeasible/unverified; writes its trace only at the end), the seeded
`requirements` rules, `search` proposers and the `cli`.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "zkqec"
SUPERSEDED = ("schemas", "evidence", "agents", "search", "requirements", "cli")
PATTERNS = [re.compile(rf"kernel\.({'|'.join(SUPERSEDED)})\b"),
            re.compile(rf"from\s+\S*kernel\s+import\s+.*\b({'|'.join(SUPERSEDED)})\b")]


def test_production_code_does_not_import_superseded_kernel_components():
    offenders = []
    for path in SRC.rglob("*.py"):
        if "kernel" in path.relative_to(SRC).parts[:1]:
            continue
        text = path.read_text()
        for pat in PATTERNS:
            for m in pat.finditer(text):
                offenders.append(f"{path.relative_to(SRC)}: {m.group(0)}")
    assert not offenders, offenders
