"""TLS-terminating, request-inspecting egress broker (contracts §J1, §K, §L).

* :mod:`.policy` -- stdlib-only decision logic shared with the mitmproxy addon;
* :mod:`.addon` -- the mitmproxy addon (runs inside ``.venv-broker``; not importable here);
* :mod:`.bridge` -- host side of the sandbox egress bridge (Unix socket → broker, token injection);
* :mod:`.launcher` -- start/stop a campaign broker, per-campaign CA, grants, dispatch switch, logs.
"""
from .bridge import Bridge
from .launcher import Broker, Ceilings, summarize_job

__all__ = ["Bridge", "Broker", "Ceilings", "summarize_job"]
