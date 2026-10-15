# Provenance of `zkqec.kernel`

Vendored **byte-identically** from QECForge 0.1.0 (`ref-code/qecforge/src/qecforge/`, MIT, see `LICENSE.qecforge`)
on 2026-10-15. Reference sdist sha256: `4252d6da7236a6d6d9298b8d730f3494f4bcb892b9c4271c4c640c9173edbed4`. Policy (prompt D6): files here are never edited in place
except by a test-first patch recorded in the *Deviations* table, and every patch is a new evaluator version.

| file | sha256 (first 16) | status |
|---|---|---|
| `agents.py` | `6eac7f0dcc11743c` | upstream |
| `backends.py` | `e5444bf922a58551` | upstream |
| `cli.py` | `c6015b829a2e307c` | upstream |
| `codes.py` | `efb6865617146a9b` | upstream |
| `decoding.py` | `5c03e5e6ad40a054` | upstream |
| `distance.py` | `f9c224f469354d22` | upstream |
| `evidence.py` | `46b4ac0a2858da1d` | upstream |
| `gadgets.py` | `7407055f734587b7` | upstream |
| `gf2.py` | `bfa0a3794264fc83` | upstream |
| `__init__.py` | `78ad33482f566807` | upstream |
| `__main__.py` | `79ee6f134c5c5193` | upstream |
| `requirements.py` | `b552489706262602` | upstream |
| `resources.py` | `f4b41dcfff325e1a` | upstream |
| `scheduling.py` | `0da4828c32bd42ac` | upstream |
| `schemas.py` | `5893ebd8d64680e3` | upstream |
| `search.py` | `ae164dcf22295268` | upstream |
| `statistics.py` | `cf135020792217b3` | upstream |
| `structure.py` | `39b417b34ab601eb` | upstream |
| `workload.py` | `4c6045970d710d06` | upstream |

Upstream tests: `tests/kernel_upstream/tests/{test_core,test_optional}.py` and `tests/kernel_upstream/configs/demo.json`
are byte-identical copies; `tests/kernel_upstream/conftest.py` aliases `qecforge` → `zkqec.kernel`.

## Deviations

| id | file | change | failing test first | evaluator version |
|---|---|---|---|---|
| — | — | none yet | — | kernel-0.1.0+0 |
