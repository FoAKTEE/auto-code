import itertools
import json
from pathlib import Path
import numpy as np
import pytest
from scipy.linalg import expm
from qecforge import gf2
from qecforge.codes import CSSCode, bicycle_chain, steane, hypergraph_product, repetition_checks, shift_matrix
from qecforge.distance import certify, EnumerationBudgetExceeded
from qecforge.structure import structural_report, chain_shift, permutation_action, injection_batches, dsatur_coloring
from qecforge.workload import PauliTerm, anticommutes, jw_spinless, z2_symmetries, tfim_chain, first_order, first_order_operator_bound
from qecforge.gadgets import css_isometry, projected_z_rotations, logical_rz, analyze_postselected_map
from qecforge.statistics import clopper_pearson, hoeffding_shots, frechet_all_success
from qecforge.resources import ResourceInputs, answer_resources, pareto_front, union_failure_bound
from qecforge.decoding import code_capacity, SyndromeLookup
from qecforge.scheduling import css_schedule, validate_schedule, CNOTLayer
from qecforge.schemas import Evidence, CampaignSpec, Proposal
from qecforge.evidence import EvidenceLedger, ArtifactStore, content_hash
from qecforge.agents import Coordinator, OfflineProposer, ReplayProposer, ModelProposer


@pytest.mark.parametrize('shape', [(0,3),(3,0),(3,5),(7,7),(12,8)])
def test_nullspace_and_rank(shape):
    a = np.random.default_rng(sum(shape)).integers(0,2,shape,dtype=np.uint8)
    basis = gf2.nullspace(a)
    assert not np.any(a @ basis.T % 2)
    assert len(basis)+gf2.rank(a) == shape[1]
    assert gf2.rank(basis) == len(basis)


def test_rank_is_not_real_rank():
    a = [[1,1,0],[1,0,1],[0,1,1]]
    assert gf2.rank(a) == 2
    assert np.linalg.matrix_rank(a) == 3


@pytest.mark.parametrize('bad', [[[2,0]], [0,1], [[0,np.nan]]])
def test_bad_binary_rejected(bad):
    with pytest.raises(ValueError):
        gf2.matrix(bad)


def test_inverse():
    a = np.array([[1,1,0],[0,1,1],[0,0,1]],dtype=np.uint8)
    assert np.array_equal(a @ gf2.inverse(a) % 2, np.eye(3))
    with pytest.raises(ValueError):
        gf2.inverse([[1,1],[1,1]])


@pytest.mark.parametrize('length', [2,3,4])
def test_hgp_canonical(length):
    h = repetition_checks(length)
    c = hypergraph_product(h,h)
    assert c.n == length**2+(length-1)**2
    assert c.k == 1
    assert np.array_equal(c.lx @ c.lz.T % 2, np.eye(1))
    assert not c.hx.flags.writeable


def test_wrap_cancellation():
    assert not np.any(shift_matrix(2,3,[(0,0),(2,3)]))


def test_invalid_css():
    with pytest.raises(ValueError):
        CSSCode(np.array([[1,0]]),np.array([[1,1]]))


@pytest.mark.parametrize('ell,d', [(2,5),(3,6),(4,6)])
def test_reference_chain_distance(ell,d):
    c = bicycle_chain(ell,7)
    cert = certify(c,3)
    assert cert['exact'] and cert['lower_bound'] == d == cert['upper_bound']
    assert c.k == 2*ell
    assert structural_report(c)['disjoint_logical_Z']
    assert structural_report(c)['global_S_actions'] == ['S_dagger']*c.k


def test_distance_small_lower_bound_and_witness():
    c = steane()
    assert certify(c,0)['lower_bound'] == 1
    assert not certify(c,0)['exact']
    assert certify(c,1)['lower_bound'] == 3
    assert certify(c,2)['exact']
    assert certify(c,2)['lower_bound'] == 3


def test_distance_matches_independent_bruteforce():
    c = hypergraph_product(repetition_checks(3),repetition_checks(3))
    values = []
    for h, dual in [(c.hz,c.lz),(c.hx,c.lx)]:
        found = None
        for w in range(1,c.n+1):
            for support in itertools.combinations(range(c.n),w):
                e = np.zeros(c.n,dtype=np.uint8); e[list(support)] = 1
                if not np.any(h @ e % 2) and np.any(dual @ e % 2):
                    found = w; break
            if found:
                break
        values.append(found)
    cert = certify(c,2)
    assert cert['lower_bound'] == min(values) == 3


def test_enumeration_budget_not_proof():
    with pytest.raises(EnumerationBudgetExceeded):
        certify(steane(),2,max_patterns=2)
    with pytest.raises(EnumerationBudgetExceeded):
        certify(steane(),2,max_pairs=0)


def test_shift_logical_action():
    c = bicycle_chain(3,7)
    action = permutation_action(c,chain_shift(3,7))
    expected = np.zeros((6,6),dtype=int)
    for half in range(2):
        for i in range(3): expected[half*3+i,half*3+(i+1)%3] = 1
    assert action['preserves_code']
    assert np.array_equal(action['logical_X'],expected)
    assert action['logical_X'] == action['logical_Z']


def test_invalid_permutation():
    with pytest.raises(ValueError):
        permutation_action(steane(),[0]*7)


def test_color_batches():
    r = np.array([[1,0,0],[1,1,0],[0,1,1],[0,0,1]],dtype=np.uint8)
    b = injection_batches(r)
    assert b['feasible_batch_count'] == 2
    assert not b['optimal']
    with pytest.raises(ValueError):
        dsatur_coloring([{1},set()])


@pytest.mark.parametrize('a,b,answer', [('XI','ZI',True),('XX','ZZ',False),('XY','YX',False),('II','ZZ',False)])
def test_commutation(a,b,answer):
    assert anticommutes(a,b) is answer


def test_jw_parity_and_no_taper():
    terms = jw_spinless(3,[(0,1,1.),(1,2,1.)],[.2,.3,.4])
    sym = z2_symmetries(terms)
    assert sym == ['ZZZ']
    assert len(terms[0].pauli) == 3
    assert not z2_symmetries(tfim_chain(3))


def dense_pauli(p):
    mats = {'I':np.eye(2),'X':np.array([[0,1],[1,0]]),'Y':np.array([[0,-1j],[1j,0]]),'Z':np.diag([1,-1])}
    out = np.array([[1.]])
    for q in p: out = np.kron(out,mats[q])
    return out


def test_jw_against_fermionic_fock_space():
    n = 3
    cs = []
    for q in range(n):
        a = np.zeros((2**n,2**n))
        for state in range(2**n):
            bit = n-1-q
            if state >> bit & 1:
                parity = sum((state >> (n-1-j)) & 1 for j in range(q))
                a[state ^ (1<<bit),state] = (-1)**parity
        cs.append(a)
    edges = [(0,1,1.2),(0,2,.7)]
    onsite = [.1,-.2,.3]
    direct = sum(-t*(cs[i].T@cs[j]+cs[j].T@cs[i]) for i,j,t in edges)
    direct += sum(mu*cs[i].T@cs[i] for i,mu in enumerate(onsite))
    encoded = sum(t.coefficient*dense_pauli(t.pauli) for t in jw_spinless(n,edges,onsite))
    assert np.allclose(direct,encoded)


def test_product_formula_bound_and_order():
    terms = tfim_chain(2)
    t, r = .3, 4
    sequence = first_order(terms,t,r)
    u = np.eye(4,dtype=complex)
    last = [-1]*2
    for op in sequence:
        for q,p in enumerate(op.pauli):
            if p!='I':
                assert op.layer > last[q]; last[q]=op.layer
        u = expm(-.5j*op.angle*dense_pauli(op.pauli)) @ u
    exact = expm(-1j*t*sum(x.coefficient*dense_pauli(x.pauli) for x in terms))
    assert np.linalg.norm(u-exact,ord=2) <= first_order_operator_bound(terms,t,r)+1e-12


def test_commuting_bound_zero():
    assert first_order_operator_bound([PauliTerm(1,'ZI'),PauliTerm(.5,'IZ')],1,2) == 0


def test_isometry_and_identity_map():
    c = steane(); v = css_isometry(c)
    assert np.allclose(v.conj().T @ v,np.eye(2))
    k = projected_z_rotations(c,[0]*7)
    report = analyze_postselected_map(k,logical_rz([0]))
    assert report['target_up_to_scalar']
    assert report['state_independent_acceptance']


def test_disjoint_support_does_not_certify_instrument():
    h = np.array([[1,1,1,1,0,0],[0,0,1,1,1,1]],dtype=np.uint8)
    l = np.array([[1,0,1,0,1,0],[0,1,0,1,0,1]],dtype=np.uint8)
    c = CSSCode(h,h,l,l)
    assert structural_report(c)['disjoint_logical_Z']
    assert certify(c,1)['lower_bound'] == 2
    k = projected_z_rotations(c,[.2,.3,.2,.3,.2,.3])
    report = analyze_postselected_map(k,logical_rz([.2,.3]))
    assert not report['state_independent_acceptance']
    assert report['acceptance_spread'] == pytest.approx(.1649116019899135)


def test_zero_acceptance_not_success():
    assert not analyze_postselected_map(np.zeros((2,2)),np.eye(2))['target_up_to_scalar']


def test_dense_guard():
    with pytest.raises(ValueError):
        css_isometry(bicycle_chain(3,7))


@pytest.mark.parametrize('failures,shots', [(0,100),(5,100),(100,100)])
def test_confidence_interval(failures,shots):
    lo,hi = clopper_pearson(failures,shots)
    assert 0 <= lo <= failures/shots <= hi <= 1
    if failures == 0: assert hi > 0


def test_hoeffding_and_frechet():
    assert hoeffding_shots(1,.1,.05) == 738
    assert frechet_all_success([.9,.9]) == pytest.approx((.8,.9))
    assert .9*.9 != min(frechet_all_success([.9,.9]))


def resource_input(**overrides):
    values = dict(data_qubits=7,factory_qubits_each=7,factories=1,syndrome_ancillas=6,buffer_qubits=0,
                  preparations_per_shot=2,preparation_attempt_seconds=1.,joint_acceptance_lower=.5,
                  fixed_circuit_seconds=1.,state_prep_seconds=1.,readout_seconds=1.,decoder_feedback_seconds=1.,
                  logical_failure_upper=.001,systematic_bias_bound=.01,observable_norm=1.,
                  target_precision=.1,confidence_alpha=.05)
    return ResourceInputs(**(values|overrides))


def test_resource_latency_no_fake_parallelism():
    a = answer_resources(resource_input())
    b = answer_resources(resource_input(factories=2))
    assert a['per_accepted_shot_expected_seconds'] == 8
    assert a['time_to_answer_expected_seconds'] == b['time_to_answer_expected_seconds']
    assert b['peak_qubits']-a['peak_qubits'] == 7
    assert not a['scientific_claim_ready']


def test_impossible_bias_budget():
    assert not answer_resources(resource_input(systematic_bias_bound=.2))['feasible']
    with pytest.raises(ValueError):
        resource_input(joint_acceptance_lower=0)


def test_pareto_and_union():
    data = [{'q':1,'t':3},{'q':2,'t':4},{'q':3,'t':1}]
    assert pareto_front(data,('q','t')) == [data[0],data[2]]
    assert union_failure_bound([.1,.2],[2,1]) == pytest.approx(.4)


def test_perfect_capacity_and_determinism():
    a = code_capacity(steane(),0,100,7)
    b = code_capacity(steane(),.02,100,7)
    c = code_capacity(steane(),.02,100,7)
    assert a['failures'] == 0
    assert a['confidence_interval'][1] > 0
    assert b['failures'] == c['failures']
    assert not b['real_time_decoder_claim']


def test_missing_syndrome_explicit():
    decoder = SyndromeLookup(steane().hx,0)
    assert decoder.decode(np.array([1,0,0],dtype=np.uint8)) is None


@pytest.mark.parametrize('code',[steane(),bicycle_chain(3,7)])
def test_serial_css_schedule(code):
    schedule = css_schedule(code)
    assert validate_schedule(code,schedule)
    assert not validate_schedule(code,schedule+schedule[:1])
    assert not validate_schedule(code,list(reversed(schedule)))


def evidence(id,**kw):
    return Evidence(evidence_id=id,claim=id,domain_sha256='domain',status='verified',kind='exact_algebra',method='test',**kw)


def test_evidence_unknown_propagation():
    ledger = EvidenceLedger()
    a = Evidence(evidence_id='a',claim='a',domain_sha256='domain',status='unknown',kind='assumption',method='unmeasured')
    ledger.add(a)
    ledger.add(Evidence(evidence_id='b',claim='b',domain_sha256='domain',status='verified',kind='derived',method='composition',parents=('a',)))
    assert ledger.effective_status('b','domain') == 'unknown'
    assert ledger.effective_status('b','other') == 'unknown'


def test_evidence_immutability_and_domain():
    ledger = EvidenceLedger(); ledger.add(evidence('a'))
    with pytest.raises(ValueError): ledger.add(evidence('a'))
    with pytest.raises(ValueError): ledger.add(evidence('b',parents=('missing',)))
    with pytest.raises(ValueError):
        ledger.add(Evidence(evidence_id='b',claim='b',domain_sha256='other',status='verified',kind='derived',method='test',parents=('a',)))


def test_artifact_integrity(tmp_path):
    store = ArtifactStore(tmp_path)
    key = store.put({'a':1})
    assert store.get(key) == {'a':1}
    (tmp_path/f'{key}.json').write_text('{"a":2}')
    with pytest.raises(ValueError): store.get(key)
    with pytest.raises(ValueError): store.get('../escape')


def demo_spec():
    return CampaignSpec.model_validate_json((Path(__file__).parents[1]/'configs/demo.json').read_text())


def test_coordinator_fails_closed(tmp_path):
    spec = demo_spec().model_copy(update={'requirements':demo_spec().requirements.model_copy(update={'min_distance':6})})
    result = Coordinator(spec,OfflineProposer(),tmp_path).run()
    candidates = result['candidates']
    assert candidates[2]['distance']['upper_bound'] == 5
    assert candidates[2]['status'] == 'infeasible_or_unverified'
    assert candidates[3]['status'] == 'algebraically_feasible'
    assert not result['scientific_claim_ready']
    assert (tmp_path/'trace.jsonl').exists()


def test_untrusted_proposal_size_guard(tmp_path):
    class Oversized:
        def propose(self,spec,history):
            if history: return None
            return Proposal(family='bicycle_chain',parameters={'ell':100000,'m':100000})
    result = Coordinator(demo_spec(),Oversized(),tmp_path).run()
    assert result['candidates'][0]['status'] == 'invalid_candidate'


def test_replay(tmp_path):
    path = tmp_path/'proposals.jsonl'
    path.write_text(Proposal(family='steane').model_dump_json()+'\n')
    proposer = ReplayProposer(path)
    assert proposer.propose(demo_spec(),[]).family == 'steane'
    assert proposer.propose(demo_spec(),[]) is None


def test_model_contract():
    class Model:
        def generate(self,**kwargs): return '{"family":"steane","parameters":{}}'
    assert ModelProposer(Model()).propose(demo_spec(),[]).family == 'steane'
    with pytest.raises(ValueError):
        Proposal.model_validate({'family':'steane','exec':'rm -rf /'})


def test_task_schema_rejects_bad_observable():
    obj = demo_spec().model_dump()
    obj['task']['observable']='ZI'
    with pytest.raises(ValueError): CampaignSpec.model_validate(obj)


def test_counterexample_analytic_acceptance():
    c = CSSCode(np.array([[1,1,1,1,0,0],[0,0,1,1,1,1]]),
                np.array([[1,1,1,1,0,0],[0,0,1,1,1,1]]),
                np.array([[1,0,1,0,1,0],[0,1,0,1,0,1]]),
                np.array([[1,0,1,0,1,0],[0,1,0,1,0,1]]))
    a,b=.2,.3
    f=lambda s:(np.exp(-1.5j*s)+3*np.exp(.5j*s))/4
    k=projected_z_rotations(c,[a,b,a,b,a,b])
    assert np.allclose(k,np.diag([f(a+b),f(b-a),f(a-b),f(-a-b)]))
    assert np.allclose(np.abs(np.diag(k))**2,[(5+3*np.cos(2*s))/8 for s in [a+b,b-a,a-b,-a-b]])


def test_random_grammar_and_portfolio_reproducible():
    from qecforge.search import RandomBBProposer,RoundRobinProposer
    spec=CampaignSpec.model_validate_json((Path(__file__).parents[1]/'configs/demo.json').read_text())
    p1,p2=RandomBBProposer(4),RandomBBProposer(4)
    assert p1.propose(spec,[]) == p2.propose(spec,[])
    portfolio=RoundRobinProposer([OfflineProposer(),RandomBBProposer(8)])
    assert portfolio.propose(spec,[]).family == 'steane'
    assert portfolio.propose(spec,[]).family == 'bivariate_bicycle'
    with pytest.raises(ValueError):
        RandomBBProposer(0,monomials=100)


def test_refuted_premise_does_not_refute_conclusion():
    ledger=EvidenceLedger()
    ledger.add(Evidence(evidence_id='p',claim='premise',domain_sha256='d',status='refuted',kind='exact_algebra',method='counterexample'))
    ledger.add(Evidence(evidence_id='c',claim='conclusion',domain_sha256='d',status='verified',kind='derived',method='conditional proof',parents=('p',)))
    assert ledger.effective_status('p','d')=='refuted'
    assert ledger.effective_status('c','d')=='unknown'


def test_identity_phase_not_silently_unitary_error():
    from qecforge.workload import identity_phase_radians
    terms=[PauliTerm(2.0,'I'),PauliTerm(.3,'X')]
    assert identity_phase_radians(terms,.4)==-.8
    rotations=first_order(terms,.4,2)
    assert len(rotations)==2 and all(r.pauli=='X' for r in rotations)
    assert first_order_operator_bound(terms,.4,2)==0
    x=np.array([[0,1],[1,0]],complex)
    exact=expm(-.4j*(2*np.eye(2)+.3*x))
    ir=expm(-.4j*.3*x)
    assert np.allclose(exact,np.exp(1j*identity_phase_radians(terms,.4))*ir)
