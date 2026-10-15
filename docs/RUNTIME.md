# Runtime (prompt §O, §M)

- Interpreter: uv-managed standalone CPython **3.10.22** (python-build-standalone), installed at
  `/data/haiyangw/claude/zkqec-runtime/python/cpython-3.10.22-linux-x86_64-gnu/` (outside the repo, durable). Binary sha256 `473dd9c9a03e0b41ba35f986afd9bb13162d7ac7aa5d44d4dfdf75a2e19c9989`.
  Dynamic dependencies: glibc only (libc, libm, libpthread, libdl, libutil, librt). Host glibc baseline: **2.35**.
- Project venv: `.venv` (gitignored), created with `uv venv --python <that interpreter>` and populated with
  `uv pip install --require-hashes -r requirements.lock` plus `uv pip install --no-deps -e .`.
- Broker venv: `.venv-broker` (gitignored) holds mitmproxy 11.0.2 for the TLS-inspecting egress broker (§J1);
  it is part of the trusted computing base and is pinned separately in M0.
- Bundles (§M) archive this interpreter tree and the locked wheels, so audits rebuild offline without this host's
  Python. `python3 -m venv` is unusable on this host (no ensurepip); uv 0.13.1 is installed with `pip --user`.

Rebuild:

```bash
uv python install 3.10.22 --install-dir /data/haiyangw/claude/zkqec-runtime/python
uv venv --python /data/haiyangw/claude/zkqec-runtime/python/cpython-3.10.22-linux-x86_64-gnu/bin/python3.10 .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
uv pip install --python .venv/bin/python --no-deps -e .
.venv/bin/python -m pytest -q tests
```
