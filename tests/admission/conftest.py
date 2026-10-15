"""Fixtures for admission tests (helpers live in ``admission_helpers``)."""
import pytest

from admission_helpers import make_env


@pytest.fixture
def env(tmp_path):
    e = make_env(tmp_path)
    yield e
    e.adm.close()
