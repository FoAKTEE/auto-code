# Triage — Codex M1 re-review 3 (`M1-codex-rereview-3.md`, range b382e47..c3a0583)

Verdict received: **BLOCK M1** (Codex gpt-6-astra, max, real binary). Both findings are accepted.

Confirmed FIXED: N2, N3, N4, N5, N6 and the projection (accepted as a provisional gate input). N1 is PARTIAL: the
original escape is repaired, but the new cleanup introduced B1. Every §M1 Accept item and the whole Phase 1a
definition of done pass on m1-live-6, which Codex verified independently: the content address, 53 members, the
closures, ledger and artifacts, accounting, broker evidence, report lint, exact replay of 5 verifier and 2 identity
executions, the controls (0/37, 0/24), and the confirmation (50 → 51) with $0.3318684 final.
Codex: "After B1 is fixed and reviewed, these requirements can support proceeding with M2 while retaining the binding
pre-dispatch gates."

| # | Sev. | Finding | Disposition / fix |
|---|---|---|---|
| B1 | blocker | after `wait4` reaps the leader, `_group_alive`/`_drain_group` signal the numeric group id, which a reused pid may now lead | the parent observes the leader's exit with `waitid(P_PID, pid, WEXITED \| WNOWAIT)`, leaving an unreaped zombie that keeps the pid and group id allocated; it drains the group and only then reaps with `wait4`; the exceptional cleanup uses the same order; regressions: a recorded real `killpg`/`wait4` order with a straggler grandchild (no group signal after the reap; failed before), and a zombie-only group is not live |
| B2 | minor | `M1-hardening-3.md` overstated regression discrimination and claimed an unconditional ESRCH wait | note corrected: the exposing regressions are distinguished from additional validation; the zombie-aware drain is described |
| — | minor | the postmortem's "latest telemetry" wording | renamed to the conservative basis (most expensive observed S-A call) |

The accounting limit for descendants that exit unobserved, and zombies occupying process-table entries, are accepted
for the current trusted, serial paths and recorded in the postmortem's residual risks (M3 prerequisite: worker
supervision accounts for unobserved descendants). Next: the full suite and m1-live-7, run from the snapshot of the fixed evaluator. The
acceptance run should come from the accepted evaluator; m1-live-6's scientific evidence is unaffected by B1, which
touches only the cleanup after a child exits. Then a focused Codex review of the B1 fix and m1-live-7, before M1
acceptance.
