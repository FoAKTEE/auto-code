"""M1 deterministic positive S-A and S-F cases through the production resolver (no LLM), with
paired negatives (prompt §11 M1 acceptance; appendix §I)."""
import json
from pathlib import Path

import numpy as np
import pytest

from zkqec.admission import Admission
from zkqec.contracts import (HardwareDomain, Proposal, SignedPauliImages, StructuralTargets, TaskSA,
                             TaskSF)
from zkqec.grammar import GrammarCaps
from zkqec.kernel.codes import bicycle_chain
from zkqec.orchestrator.jobs import JobBook
from zkqec.pipeline import Pipeline, build_registry, evaluator_domain
from zkqec.store import ArtifactStore

FIX = json.loads((Path(__file__).resolve().parents[1] / "verify/fixtures/sf_positive_chain_shift.json").read_text())


def sparse(a):
    a = np.asarray(a)
    return {"shape": list(a.shape), "entries": [[int(i), int(j)] for i, j in zip(*np.nonzero(a))]}


@pytest.fixture
def pipe(tmp_path):
    store = ArtifactStore(tmp_path / "store")
    reg = build_registry(store)
    adm = Admission(tmp_path / "ledger.db", store, reg)
    book = JobBook(adm.ledger.conn, "m1-fixtures")
    book.set_caps({"cpu_seconds": 10_000})
    p = Pipeline(adm, store, book, evaluator_domain(reg), caps=GrammarCaps())
    yield p
    adm.close()


def chain_proposal(**witnesses):
    c = bicycle_chain(3, 7)
    return Proposal(family="explicit_sparse", params={"hx": sparse(c.hx), "hz": sparse(c.hz)},
                    witnesses=witnesses, rationale="fixture")


def sa(d_min):
    return TaskSA(task_id=f"sa-fixture-d{d_min}",
                  targets=StructuralTargets(n_max=50, k_min=6, d_min=d_min, w_max=8, deg_max=8))


def test_sa_positive_fixture_satisfies(pipe):
    ev = pipe.evaluate(chain_proposal(), sa(6))
    assert ev.outcome == "evaluated" and ev.satisfies == "verified", ev
    assert ev.facts["n"] == 42 and ev.facts["k"] == 6 and ev.facts["d_both"] == [6, 6]


def test_sa_paired_negative_is_refuted_not_satisfied(pipe):
    ev = pipe.evaluate(chain_proposal(), sa(7))
    assert ev.satisfies != "verified"
    assert "verified" in ev.refuted.values()          # verified upper bound 6 < 7


def sf_task(target=None):
    tgt = SignedPauliImages(**(target or FIX["target"]))
    return TaskSF(task_id="sf-fixture", k_req=6, target_action=tgt,
                  targets=StructuralTargets(n_max=50, k_min=6, d_min=6, w_max=8, deg_max=8),
                  hardware=HardwareDomain(name="perm-only", allowed_operations=("qubit_permutation",)))


def sf_proposal():
    op = {k: v for k, v in FIX["operation"].items() if k != "n"}
    hx, hz = FIX["presentation"]["hx"], FIX["presentation"]["hz"]
    return Proposal(family="explicit_sparse", params={"hx": hx, "hz": hz},
                    witnesses={"basis": FIX["basis_rows"], "operation": op,
                               "logical_qubits": FIX["logical_qubits"]}, rationale="fixture")


def test_sf_positive_fixture_satisfies(pipe):
    ev = pipe.evaluate(sf_proposal(), sf_task())
    assert ev.satisfies == "verified", (ev.detail, ev.facts, ev.refuted)


def test_sf_paired_negative_wrong_target(pipe):
    bad = dict(FIX["target"])
    x, z = list(bad["x_images"]), list(bad["z_images"])
    x[0], x[1], z[0], z[1] = x[1], x[0], z[1], z[0]      # a different logical permutation
    ev = pipe.evaluate(sf_proposal(), sf_task({"x_images": x, "z_images": z}))
    assert ev.satisfies != "verified"


def test_malformed_proposal_is_typed(pipe):
    p = Proposal(family="explicit_sparse", params={"hx": {"shape": [1, 2], "entries": [[0, 5]]},
                                                    "hz": {"shape": [1, 2], "entries": []}})
    assert pipe.evaluate(p, sa(2)).outcome == "malformed_proposal"


def test_non_commuting_presentation_never_satisfies(pipe):
    p = Proposal(family="explicit_sparse", params={"hx": {"shape": [1, 2], "entries": [[0, 0]]},
                                                    "hz": {"shape": [1, 2], "entries": [[0, 0]]}})
    ev = pipe.evaluate(p, sa(2))
    assert ev.facts["css_valid"] == "refuted" and ev.satisfies != "verified"


# --- Codex M1 review #10: the canonical basis comes from the presentation being evaluated ----------
def _steane_rows(order):
    from zkqec.kernel.codes import steane
    h = np.asarray(steane().hx)
    return Proposal(family="explicit_sparse",
                    params={"hx": sparse(h[list(order)]), "hz": sparse(h)},
                    witnesses={"operation": {"permutation": list(range(7))}}, rationale="fixture")


def test_equivalent_presentations_each_use_their_own_canonical_basis(pipe):
    task = TaskSF(task_id="sf-steane-identity", k_req=1,
                  target_action=SignedPauliImages(x_images=("X",), z_images=("Z",)),
                  targets=StructuralTargets(n_max=7, k_min=1, d_min=3),
                  hardware=HardwareDomain(name="perm", allowed_operations=("qubit_permutation",)))
    results = [pipe.evaluate(_steane_rows(o), task) for o in ((0, 1, 2), (1, 0, 2), (0, 1, 2))]
    assert [ev.satisfies for ev in results] == ["verified"] * 3, [ev.detail for ev in results]
    for ev in results:
        cand = pipe.store.get_json(ev.candidate)
        basis = pipe.store.get_json(cand["L"])
        assert basis["presentation"] == ev.presentation == cand["C"]
