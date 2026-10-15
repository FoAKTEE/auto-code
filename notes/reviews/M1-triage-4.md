# Triage — Codex M1 re-review 4 (`M1-codex-rereview-4.md`, range f039905..eb05968)

Verdict received: **BLOCK M1** (Codex gpt-6-astra, max, real binary). Both findings are accepted.

B1 is PARTIAL. The normal-completion, wall-kill, oversized-result, interrupted-before-reap and nested-deadline paths
are safe. Two exceptional paths still signal a released id: (1) an interrupt landing after `wait4` reaped the child
but before `reaped` was assigned, so `finally` killed the released pid and group; (2) a child reaped elsewhere
(`ECHILD`), after which cleanup kept signalling the id. Every §M1 Accept item passes on m1-live-7, which Codex
verified independently: the bundle, 53 members, 97 embedded evaluator files matching f70055e, exact replay, the
controls, and the supplement (50 → 51) with $0.2173158 final. Once B1 is fully repaired and reviewed, Codex sees no
further blocker to M2 under the retained gates.

| # | Sev. | Finding | Disposition / fix |
|---|---|---|---|
| B1 | blocker | post-reap signals on the exceptional paths | explicit ownership: the parent signals only while it owns the unreaped child; ownership ends before any reaping wait (an interrupt after the reap sends nothing); `ECHILD` = ownership lost → never signalled again, outcome `escaped` with `ownership_lost`, CPU unmeasured (charged at the reservation); leader signals via a pidfd (`os.pidfd_open`, `signal.pidfd_send_signal`); group signals only while `waitid(P_PIDFD, …, WNOHANG \| WNOWAIT)` shows the leader unreaped; `run_bounded` refuses to start while `SIGCHLD` is ignored (kernel auto-reap); regressions: interrupt right after the reap (top-level and nested), child reaped elsewhere (top-level and nested), ignored SIGCHLD — all five failed before |
| R1 | minor | module doc described draining after the reap | doc rewritten: drain while the leader is an unreaped zombie, reap last; ownership rules |

Remaining invariant (documented): no code in the process reaps children it does not own (no `waitpid(-1)` or
`os.wait()` in `src/zkqec`; `subprocess` reaps only its own pids). Next: the full suite, m1-live-8 from the snapshot
of the fixed evaluator, and a focused Codex review.
