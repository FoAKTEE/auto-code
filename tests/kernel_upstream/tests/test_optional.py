import importlib.util
import numpy as np
import pytest
from qecforge.backends import require, BackendUnavailable


def test_missing_dependency_fails_explicitly():
    with pytest.raises(BackendUnavailable): require('qecforge_nonexistent_backend')


@pytest.mark.optional
def test_stim_css_memory_noiseless():
    pytest.importorskip('stim')
    from qecforge.backends import css_memory_circuit
    from qecforge.codes import steane
    c = css_memory_circuit(steane(),rounds=2,p=0)
    d,o = c.compile_detector_sampler(seed=7).sample(100,separate_observables=True)
    assert not np.any(d) and not np.any(o)


@pytest.mark.optional
def test_dem_hyperedge_preservation():
    stim = pytest.importorskip('stim')
    from qecforge.backends import detector_matrices
    dem = stim.DetectorErrorModel('error(0.1) D0 D1 ^ D2 L0')
    h,l,p = detector_matrices(dem)
    assert h.shape==(3,1) and np.all(h[:,0])
    assert l[0,0] and p[0]==.1


@pytest.mark.optional
def test_bp_osd_basic():
    pytest.importorskip('ldpc')
    from qecforge.backends import bp_osd_decoder
    h = np.array([[1,1,0],[0,1,1]],dtype=np.uint8)
    decoder = bp_osd_decoder(h,np.array([.01,.01,.01]))
    result = decoder.decode(np.array([1,0],dtype=np.uint8))
    assert np.array_equal(h @ result % 2,[1,0])


@pytest.mark.optional
def test_matching_graphlike_only():
    stim = pytest.importorskip('stim'); pytest.importorskip('pymatching')
    from qecforge.backends import matching_decoder
    with pytest.raises(ValueError):
        matching_decoder(stim.DetectorErrorModel('error(0.1) D0 D1 D2'))
    decoder = matching_decoder(stim.DetectorErrorModel('error(0.1) D0 L0'))
    assert np.array_equal(decoder.decode(np.array([1],dtype=np.uint8)),[1])
