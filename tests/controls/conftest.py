"""One replay campaign with controls, shared by the campaign and audit tests (read-only: tests that
modify it work on a copy)."""
import pytest

from test_campaign_controls import GOOD, ReplayDouble, cfg
from zkqec.orchestrator.campaign import run_campaign


@pytest.fixture(scope="session")
def controls_campaign(tmp_path_factory):
    root = tmp_path_factory.mktemp("controls-campaign")
    out = run_campaign(cfg(), ReplayDouble([GOOD]), root, require_clean=False, seal_bundle=False)
    return out, root / "ctl"
