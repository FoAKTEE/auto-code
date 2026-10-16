# Triage — Codex M1 re-review 6 (`M1-codex-rereview-6.md`, range 743dd0c..9486904; exhaustive pass)

Verdict received: **BLOCK M1** (Codex gpt-6-astra, max, real binary). All findings are accepted.

B1 and B2 of re-review 5 are fixed, and their six regressions discriminate. Every §M1 Accept item passes on m1-live-9
(verified independently). Asked to be exhaustive, Codex listed every remaining lifecycle defect of the in-process
supervisor: blockers F1–F7, major F9, minors F8 and F10–F14.

**Disposition: structural redesign rather than another patch round.** Six reviews of one function show that a
Python-level fork/process-group/`/proc` supervisor cannot be made robust incrementally: membership is not observable
(F3, F6), descendants outlive the supervisor (F4), waits block (F5), and handler and timer composition is fragile (F1,
F2, F11). The plan already lists kernel-level worker supervision as an M3 prerequisite; it is pulled forward into M1.
Feasibility was probed on this host (2026-10-15):
- a process in a delegated systemd scope can move itself into a `main` leaf, enable `+memory +pids`, and create job
  cgroups with `memory.max`/`pids.max`;
- a forked child joins its job cgroup; `cpu.stat` counts descendants; `cgroup.kill` kills the whole tree (populated 0);
- a transient user service with `Delegate=yes`, `KillMode=control-group`: SIGKILL of its main process killed the job
  child and its grandchild;
- a running process can be adopted into a fresh delegated scope (`StartTransientUnit` with `PIDs=[self]`,
  `Delegate=true`).

| # | Sev. | Finding | Disposition under the redesign |
|---|---|---|---|
| F1 | blocker | interruptible handler restoration; queued signals lost | signal deferral removed: the parent never signals a pid, so any exception is handled by `finally` → `cgroup.kill` by path (idempotent) |
| F2 | blocker | deferred SIGALRM reverses composed deadline priority | in-process SIGALRM wall deadlines removed from production parents; wall time is enforced by the supervisor's poll and kill; any timer left runs only inside a child, with no composition |
| F3 | blocker | the drain could return ok with a live descendant | membership = the job cgroup (`cgroup.events populated`); `cgroup.kill` before any conclusion; inability to inspect ⇒ never ok, never complete accounting |
| F4 | blocker | supervisor death left descendants; PDEATHSIG unchecked | live campaigns run as a transient service with `KillMode=control-group` and `RuntimeMaxSec`; adopted scopes for other callers; PDEATHSIG kept only as a checked extra |
| F5 | blocker | exit waits ignored deadline and interrupts | non-blocking supervision loop (populated / exit, CPU usage, wall, interrupts) until the cgroup is empty; a failed kill ⇒ an explicit containment-failure outcome |
| F6 | blocker | process-group setup failed open | the child joins its job cgroup and the parent verifies `/proc/<pid>/cgroup` before releasing it (go byte); failure ⇒ `fn` never runs |
| F7 | blocker | child initial close outside the `_exit` guard | the whole `pid == 0` branch inside an unconditional `os._exit` guard |
| F8 | minor | cleanup errors leak descriptors/zombies | descriptors closed in their own `finally`; "needs reaping" tracked apart from any signalling |
| F9 | major | controls undercharge child CPU when `run_bounded` raises | an exception ⇒ the job settles `uncertain` with CPU unmeasured (charged at the reservation) |
| F10 | minor | undeferred raising handlers | moot (no deferral, no pidfd); unsupported configurations still refused (ignored SIGCHLD) |
| F11 | minor | `alarm_deadline` not exception-safe; infinite durations | removed with F2, or made exception-safe with finite-positive validation if any child-side timer remains |
| F12 | minor | late recorded signals → ok | moot (no deferral); an interrupt propagates after cleanup |
| F13 | minor | requested instead of effective limits reported | report effective limits (`getrlimit`, `memory.max`, `pids.max`) |
| F14 | minor | setup tests did not prove reaping | capture the forked pid independently; assert it is reaped and the cgroup removed |

Next: one engineer implements the redesign test-first; then the full suite, re-qualification if the launch path
changed, m1-live-10, and a focused Codex review. M1 live spend so far: $3.12 of $5.
