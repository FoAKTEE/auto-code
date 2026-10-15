"""OS-level isolation for agent calls and generated programs (contracts §J0, §J2, §J4).

* :mod:`.profiles` -- frozen bwrap launch profiles ``agent-call`` and ``program`` and the argv builder;
* :mod:`.nsexec` -- standalone pre-exec helper (private size-limited tmpfs, credential copy, collection);
* :mod:`.cgroup` -- campaign slice, per-job scopes, deadline supervisor;
* :mod:`.runner` -- run one job and capture capped outputs;
* :mod:`.outputs` -- trusted-side caps for generated outputs.
"""
from .cgroup import CampaignSlice, DeadlineSupervisor, controllers
from .outputs import OutputRejected, load_json_file_capped, parse_json_capped, read_regular_capped
from .profiles import JobLayout, LaunchProfile, agent_call_profile, build_bwrap_argv, program_profile
from .runner import HomeFile, JobResult, JobSpec, Limits, run_job

__all__ = ["CampaignSlice", "DeadlineSupervisor", "controllers", "OutputRejected", "load_json_file_capped",
           "parse_json_capped", "read_regular_capped", "JobLayout", "LaunchProfile", "agent_call_profile",
           "build_bwrap_argv", "program_profile", "HomeFile", "JobResult", "JobSpec", "Limits", "run_job"]
