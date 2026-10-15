"""Test-only dict-backed artifact sink (the production store belongs to admission)."""
import json

import pytest

from zkqec.contracts import canonical_json, content_hash


class DictSink:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}

    def __call__(self, obj) -> str:
        sha = content_hash(obj)
        self.blobs[sha] = canonical_json(obj)
        return sha

    def get(self, sha: str):
        return json.loads(self.blobs[sha])


@pytest.fixture
def sink():
    return DictSink()
