"""Scientific executions, evidence lookup and conflict quarantine are namespaced (contracts §N;
Codex M1 milestone review B4): a closure from another campaign/arm/split/profile is never reused."""
from admission_helpers import css, lower, upper

A, B = "camp/-/-/K0.P-min.G-open", "camp/-/-/K0.P-domain.G-open"


def test_the_same_keyed_execution_runs_once_per_namespace(env):
    p = env.presentation("P")
    a = env.admit(css(p), idempotency_key="l1", namespace=A)
    b = env.admit(css(p), idempotency_key="l1", namespace=B)
    assert a.outcome == b.outcome == "admitted" and not a.replayed and not b.replayed
    assert a.execution_id != b.execution_id and set(a.records).isdisjoint(b.records)
    assert len(env.script.calls) == 2                       # B ran its own verifier
    again = env.admit(css(p), idempotency_key="l1", namespace=A)
    assert again.replayed and again.records == a.records    # within a namespace: read only
    assert env.adm.ledger.execution(a.execution_id)["namespace"] == A
    inputs = {"seed": env.seed, "p0": p}
    assert env.adm.execution_id_for("scripted", inputs, env_domain(), "l1",
                                    namespace=A) == a.execution_id


def test_closed_report_is_looked_up_in_the_callers_namespace_only(env):
    p = env.presentation("P")
    inputs = {"seed": env.seed, "p0": p}
    a = env.admit(css(p), inputs=inputs, idempotency_key="l1", namespace=A)
    found = env.adm.closed_report("scripted", inputs, env_domain(), "l1", namespace=A)
    assert found.records == a.records
    assert env.adm.closed_report("scripted", inputs, env_domain(), "l1", namespace=B) is None


def test_evidence_lookup_is_namespaced(env):
    p = env.presentation("P")
    env.admit(css(p), namespace=A)
    prop = css(p).proposition
    assert env.adm.resolver(env_domain(), namespace=A).status(prop).status == "verified"
    assert env.adm.resolver(env_domain(), namespace=B).status(prop).status == "unknown"
    assert env.adm.resolver(env_domain()).status(prop).status == "verified"   # unscoped view


def test_identity_records_are_namespaced(env):
    p = env.presentation("P")
    env.ids.table[p] = "c" * 64
    env.adm.run_identity("table_identity", p, env_domain(), idempotency_key="id", namespace=A)
    assert env.adm.resolver(env_domain(), namespace=A).code_of(p)[0] == "c" * 64
    assert env.adm.resolver(env_domain(), namespace=B).code_of(p) is None


def test_conflicts_are_quarantined_within_a_namespace_only(env):
    code = "d" * 64
    a = env.admit(lower(code, "Z", 5), namespace=A)
    b = env.admit(upper(code, "Z", 3), namespace=B)         # contradicts A, but not within B
    assert a.quarantined == () and b.quarantined == () and b.incidents == ()
    assert env.adm.resolver(env_domain(), namespace=A).status(
        lower(code, "Z", 5).proposition).status == "verified"
    assert env.adm.verify().ok
    c = env.admit(upper(code, "Z", 3), namespace=A)          # within A: quarantined as before
    assert len(c.quarantined) == 2 and c.incidents


def env_domain():
    from admission_helpers import DOMAIN
    return DOMAIN
