"""L0 raw-input caps applied before any JSON parsing (prompt §8 L0, appendix §B3)."""
import json

import pytest

from zkqec.grammar import MalformedProposal, check_raw_json, parse_untrusted_json

CAPS = dict(max_bytes=1000, max_depth=4, max_list_len=5)


def test_well_formed_input_passes_and_parses():
    text = json.dumps({"family": "explicit_sparse", "params": {"hx": [[0, 1], [1, 2]]}})
    check_raw_json(text, **CAPS)
    assert parse_untrusted_json(text, **CAPS)["family"] == "explicit_sparse"


@pytest.mark.parametrize("text,field", [
    ("[" * 5 + "]" * 5, "L0.depth"),                           # depth 5 > 4
    ('{"a": {"b": {"c": {"d": {"e": 1}}}}}', "L0.depth"),
    ("[1,2,3,4,5,6]", "L0.list_len"),                          # 6 elements > 5
    ('{"a":1,"b":2,"c":3,"d":4,"e":5,"f":6}', "L0.list_len"),  # object members count too
    ("[" + "1" * 65 + "]", "L0.token_len"),                    # huge integer never reaches json
    (" " * 1001, "L0.bytes"),
    ("[\"" + "é" * 600 + "\"]", "L0.bytes"),                   # 1200 UTF-8 bytes, 604 chars
])
def test_caps_reject_before_parsing(text, field):
    with pytest.raises(MalformedProposal) as e:
        check_raw_json(text, **CAPS)
    assert e.value.field == field and e.value.outcome == "malformed_proposal"


def test_brackets_and_commas_inside_strings_are_not_structure():
    text = json.dumps(["[[[[[[,,,,,,,]]]]]", "\\\"[[[", "x"])
    check_raw_json(text, **CAPS)
    assert parse_untrusted_json(text, **CAPS)[0].startswith("[[[")


def test_bytes_input_and_invalid_utf8():
    check_raw_json(b"[1,2]", **CAPS)
    with pytest.raises(MalformedProposal):
        check_raw_json(b"[\xff]", **CAPS)
    with pytest.raises(MalformedProposal):
        check_raw_json(b"x" * 1001, **CAPS)


@pytest.mark.parametrize("text", ["[NaN]", "[Infinity]", '{"a":1,"a":2}', "[1,", "{'a': 1}"])
def test_strict_parse_rejects_nonfinite_duplicates_and_syntax(text):
    with pytest.raises(MalformedProposal):
        parse_untrusted_json(text, **CAPS)


def test_caps_must_be_positive_ints():
    with pytest.raises(ValueError):
        check_raw_json("[]", max_bytes=0, max_depth=1, max_list_len=1)
