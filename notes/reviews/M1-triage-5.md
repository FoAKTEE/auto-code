# Triage — Codex M1 re-review 5 (`M1-codex-rereview-5.md`, range 63ace07..363302d)

Verdict received: **BLOCK M1** (Codex gpt-6-astra, max, real binary). All findings are accepted.

The original interrupt-after-`wait4` defect, normal completion, wall kill, oversized results, plain `ECHILD`, and
interrupts around the normal `waitid`/`wait4` calls are fixed, including nested mode and the pidfd fallback; R1 is
fixed. Every §M1 Accept item passes on m1-live-8, which Codex verified independently: the bundle, 53 members, 97
evaluator files, exact replay, the controls, the supplement, and $0.2077358 final.

| # | Sev. | Finding | Disposition / fix |
|---|---|---|---|
| B1 | blocker | without a pidfd, an interrupt landing between `ECHILD` and clearing ownership still let the fallback send numeric signals | (root cause shared with B2: asynchronous exceptions between bytecodes) in the main thread the whole parent-side lifecycle runs with the Python handlers of SIGINT/TERM/HUP/QUIT/ALRM/USR1/USR2 replaced by a recorder; a recorded signal makes the wait loop kill and clean up through the ordered path; the original handlers are restored and the signals re-delivered only after the child is reaped and every descriptor closed; without a pidfd, a numeric signal requires a positive `waitid(P_PID, WNOHANG \| WNOWAIT)` ownership check (fails closed) |
| B2 | blocker | an interrupt during setup (after fork/`pidfd_open`, before the lifecycle `try`) skipped child cleanup and leaked the pipe and pidfd; also a pidfd leak on an interrupt before the final close | the lifecycle `try/finally` now starts before the pipe and fork and closes every descriptor; interrupts are deferred as above; the child restores the original handlers and sets `PR_SET_PDEATHSIG` so it dies with its parent |

Regressions (all six failed against the previous `bounded.py` and pass now): ownership loss + interrupt + no pidfd
(top-level, nested); a real SIGINT during setup (top-level, nested: child dead, descriptors restored, prompt
return); an interrupt during the final cleanup (no descriptor leak); a bounded child dies with its parent. Next: the
full suite, m1-live-9 from the snapshot of the fixed evaluator, and a focused Codex review.
