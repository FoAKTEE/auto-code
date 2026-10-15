import pytest
from pydantic import ValidationError

from zkqec.search.random_baseline import RandomTwoBlockProposer, RandomTwoBlockSpec


def spec(**kw):
    base = dict(dims=((3, 3), (6, 6), (12, 6)), weights_a=(3,), weights_b=(3,), n_cap=80, seed=7)
    base.update(kw)
    return RandomTwoBlockSpec(**base)


def test_deterministic_stream():
    a = [RandomTwoBlockProposer(spec()).propose() for _ in range(1)]
    s1 = RandomTwoBlockProposer(spec()); s2 = RandomTwoBlockProposer(spec())
    assert [s1.propose() for _ in range(20)] == [s2.propose() for _ in range(20)]


def test_respects_caps_and_distinct_monomials():
    p = RandomTwoBlockProposer(spec())
    for _ in range(200):
        prop = p.propose()
        l, m = prop.params["l"], prop.params["m"]
        assert 2 * l * m <= 80
        for key in ("a", "b"):
            mons = [tuple(x) for x in prop.params[key]]
            assert len(set(mons)) == len(mons) == 3
            assert all(0 <= i < l and 0 <= j < m for i, j in mons)


def test_invalid_specs_rejected():
    with pytest.raises(ValidationError):
        spec(n_cap=10)                      # no usable dims
    with pytest.raises(ValidationError):
        spec(dims=((1, 2),), n_cap=10, weights_a=(3,))   # weight > l*m
