"""§I controls: production-path injections with paired positives (contracts §I).

``run_suite(control_set, scratch, inputs)`` runs a control set in scratch evaluators and returns its
canonical evidence (k/N false authorizations, j/M false rejections per control, deferred controls
listed with reasons); ``verdict(evidence)`` is its pass result. Campaigns record it through
``Admission.record_controls``; the offline audit re-runs it, requires byte-identical evidence, the
frozen config's control set and a passing result.
"""
from . import p1a
from .env import SuiteInputs
from .p1a import CONTROL_SETS, FAILED_KIND, ControlSuiteError, run_suite, verdict
from .record import audit_controls, campaign_inputs

__all__ = ["CONTROL_SETS", "FAILED_KIND", "ControlSuiteError", "SuiteInputs", "audit_controls",
           "campaign_inputs", "p1a", "run_suite", "verdict"]
