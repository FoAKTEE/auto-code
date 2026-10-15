"""Run the byte-identical QECForge tests against the vendored kernel.

The upstream tests import ``qecforge``; alias that name to ``zkqec.kernel`` so the
test files stay unmodified (see src/zkqec/kernel/PROVENANCE.md).
"""
import importlib
import sys

_MODULES = ("agents", "backends", "cli", "codes", "decoding", "distance", "evidence", "gadgets",
            "gf2", "requirements", "resources", "scheduling", "schemas", "search", "statistics",
            "structure", "workload")

sys.modules.setdefault("qecforge", importlib.import_module("zkqec.kernel"))
for _name in _MODULES:
    sys.modules.setdefault(f"qecforge.{_name}", importlib.import_module(f"zkqec.kernel.{_name}"))
