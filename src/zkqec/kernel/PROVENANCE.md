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
| `gadgets.py` | `1b8a8e00484050ad` | **patched (KD-1)**; upstream `7407055f734587b7` |
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
| KD-1 | `gadgets.py` `analyze_postselected_map` | unitarity guard uses `rtol=0`; "target up to scalar" judged by the relative residual `‖K−cU‖₂/|c| ≤ tol`; reports `relative_residual` (None when c = 0) | `tests/kernel_patches/test_gadgets_patch.py` (3 failed before, pass after) | kernel-0.1.0+1 |
| KD-2 | `schemas.TaskSpec` freezes `trotter_steps` | **superseded, not patched**: zkqec tasks are `zkqec.contracts.task` (no algorithm fields) | `tests/kernel_patches/test_superseded_components.py` | — |
| KD-3 | `evidence.EvidenceLedger` accepts caller-labelled `verified` | **superseded**: `zkqec.admission` stamps issuers from execution records | same guard test | — |
| KD-4 | `agents.Coordinator` conflates infeasible/unverified; trace written at end | **superseded**: zkqec orchestrator + typed outcomes + event log | same guard test | — |
| KD-5 | demo operator bound 0.07 ⇒ observable bias 0.14 > 0.1 target | informational upstream finding; demo config only used by the byte-identical upstream tests | — | — |
