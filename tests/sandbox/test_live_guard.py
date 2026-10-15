"""An unintended live campaign ran from the test suite while the zero-request reservation test was
being written (bundle 294db0eb… sealed under m1-live-3, 2 model requests, $0.2047): a test reached
run_live with the real provider. The sandbox runner is the one place credentials enter a job home,
so it refuses real credential files whenever the suite forbids live calls."""
import os
from pathlib import Path

import pytest

from zkqec.agents.launch import CLAUDE_CREDENTIALS, CODEX_AUTH
from zkqec.sandbox.runner import HomeFile, JobSpec, LiveCallForbidden, run_job
from zkqec.sandbox.profiles import agent_call_profile


def test_the_suite_forbids_live_calls():
    assert os.environ.get("ZKQEC_FORBID_LIVE") == "1"


@pytest.mark.parametrize("cred,provider", [(CLAUDE_CREDENTIALS, "claude"), (CODEX_AUTH, "codex")])
def test_a_job_with_real_credentials_is_refused_before_anything_starts(tmp_path, cred, provider):
    spec = JobSpec("live-guard", agent_call_profile(provider), ("/bin/true",), {},
                   home_files=(HomeFile(str(cred), ".cred.json"),), broker_port=1, token="t")
    with pytest.raises(LiveCallForbidden):
        run_job(spec, tmp_path, slice_=None)
    assert list(tmp_path.iterdir()) == []                 # no job directory was created
