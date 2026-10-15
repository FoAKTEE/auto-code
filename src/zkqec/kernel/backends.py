"""Optional QEC backends. Import errors are explicit, never replaced by mock data.

Adapters in this release could not be integration-tested in the delivery
container (backends not installed and installation network unavailable).
Run `pytest -m optional` after installing the qec extra.
"""
from __future__ import annotations
import importlib
import numpy as np
from .codes import CSSCode
from .scheduling import css_schedule


class BackendUnavailable(RuntimeError):
    pass


def require(name: str):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise BackendUnavailable(f"Optional backend {name!r} is missing; install qecforge[qec].") from exc


def css_memory_circuit(code: CSSCode, rounds: int = 3, p: float = 0.001,
                       idle_p: float = 0.0):
    """Complementary-Z memory test with ideal initial reference syndrome.

    No encoded preparation cost is inferred from this initialization. All X
    checks precede all Z checks. Noise: reset/measurement flips, CNOT depolarizing,
    and optional idle depolarizing per CNOT layer. Final logical-Z observables
    are tracked; run a dual code for a complementary-X memory test. Neither
    test represents a full logical algorithm.
    """
    stim = require("stim")
    if not isinstance(rounds, int) or rounds < 1 or not 0 <= p <= 1 or not 0 <= idle_p <= 1:
        raise ValueError("Invalid round count or noise probability.")
    if code.k == 0:
        raise ValueError("Memory observables require k > 0.")
    n, nx, nz = code.n, len(code.hx), len(code.hz)
    anc_x, anc_z = list(range(n, n+nx)), list(range(n+nx, n+nx+nz))
    schedule = css_schedule(code)
    circuit = stim.Circuit()
    circuit.append("R", list(range(n)))
    total = 0
    previous = None
    for cycle in range(rounds+1):
        noise = 0.0 if cycle == 0 else p
        if anc_x:
            circuit.append("RX", anc_x)
            if noise:
                circuit.append("Z_ERROR", anc_x, noise)
        if anc_z:
            circuit.append("R", anc_z)
            if noise:
                circuit.append("X_ERROR", anc_z, noise)
        for layer in schedule:
            targets = [q for pair in layer.pairs for q in pair]
            circuit.append("CX", targets)
            if noise:
                circuit.append("DEPOLARIZE2", targets, noise)
            idle = [q for q in range(n+nx+nz) if q not in set(targets)]
            if idle and cycle and idle_p:
                circuit.append("DEPOLARIZE1", idle, idle_p)
            circuit.append("TICK")
        if anc_x:
            if noise:
                circuit.append("Z_ERROR", anc_x, noise)
            circuit.append("MX", anc_x)
        if anc_z:
            if noise:
                circuit.append("X_ERROR", anc_z, noise)
            circuit.append("M", anc_z)
        current = list(range(total, total+nx+nz))
        total += nx+nz
        if previous is not None:
            for j in range(nx+nz):
                circuit.append("DETECTOR", [stim.target_rec(current[j]-total),
                                             stim.target_rec(previous[j]-total)])
        previous = current
    if p:
        circuit.append("X_ERROR", list(range(n)), p)
    circuit.append("M", list(range(n)))
    data_records = list(range(total, total+n))
    total += n
    for j, row in enumerate(code.hz):
        records = [stim.target_rec(previous[nx+j]-total)]
        records += [stim.target_rec(data_records[int(q)]-total) for q in np.flatnonzero(row)]
        circuit.append("DETECTOR", records)
    for i, row in enumerate(code.lz):
        circuit.append("OBSERVABLE_INCLUDE",
                       [stim.target_rec(data_records[int(q)]-total) for q in np.flatnonzero(row)], i)
    # Raises on nondeterministic detectors; do not hide this failure.
    circuit.detector_error_model(decompose_errors=False)
    return circuit


def detector_matrices(dem) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One column per independent DEM error mechanism, retaining hyperedges.

    Separators do NOT split one correlated mechanism into independent faults.
    Repeated detector or logical targets are XORed. This conversion does not
    implement an arbitrary correlated physical noise model.
    """
    require("stim")
    columns = []
    for instruction in dem.flattened():
        if instruction.type != "error":
            continue
        ds, ls = set(), set()
        for target in instruction.targets_copy():
            if target.is_relative_detector_id():
                s = ds
            elif target.is_logical_observable_id():
                s = ls
            else:
                continue
            if target.val in s:
                s.remove(target.val)
            else:
                s.add(target.val)
        columns.append((float(instruction.args_copy()[0]), ds, ls))
    h = np.zeros((dem.num_detectors, len(columns)), dtype=np.uint8)
    logicals = np.zeros((dem.num_observables, len(columns)), dtype=np.uint8)
    probs = np.array([p for p, _, _ in columns])
    for j, (_, ds, ls) in enumerate(columns):
        h[list(ds), j] = 1
        logicals[list(ls), j] = 1
    return h, logicals, probs


def bp_osd_decoder(checks: np.ndarray, error_probabilities: np.ndarray):
    ldpc = require("ldpc")
    return ldpc.BpOsdDecoder(checks, error_channel=list(error_probabilities),
                             bp_method="minimum_sum", max_iter=30,
                             osd_method="OSD_CS", osd_order=2)


def matching_decoder(dem):
    matching = require("pymatching")
    # A graphlike model is a contract. Conversion/decomposition may fail and
    # is not silently approximated here.
    for instruction in dem.flattened():
        if instruction.type == "error":
            component = 0
            for target in instruction.targets_copy():
                if target.is_separator():
                    if component > 2:
                        raise ValueError("DEM contains a nongraphlike component.")
                    component = 0
                elif target.is_relative_detector_id():
                    component += 1
            if component > 2:
                raise ValueError("DEM contains a nongraphlike component.")
    return matching.Matching.from_detector_error_model(dem)
