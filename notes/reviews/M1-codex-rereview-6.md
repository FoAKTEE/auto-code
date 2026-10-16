BLOCK M1

## A. B1 and B2

Reviewed `743dd0c..9486904`, read-only.

**Both reported reproductions are fixed.** B1’s combined `ECHILD`/SIGINT/no-pidfd path sends no subsequent numeric signals. B2’s SIGINT during pidfd acquisition and final descriptor cleanup now completes cleanup before delivering the exception.

**All six new regressions discriminate:** they fail against `743dd0c` and pass at HEAD. They do not establish safety for every lifecycle path; the additional findings below remain.

Validation included all **41 bounded/deadline test cases**, using memory-backed substitutes for filesystem markers where necessary; **16 additional lifecycle combinations** across nesting and pidfd availability; all seven deferred signals; and targeted failure probes. I did not independently rerun the reported 808-test suite.

## B. Remaining lifecycle findings

These include previously existing defects exposed by the requested broader review. Fault-injected failures are identified separately from reproductions using ordinary kernel behavior. None of the blockers below requires concurrent campaigns, ignored `SIGCHLD`, or a foreign reaper.

**F1 — blocker: handler restoration can be interrupted, and queued signals can disappear.**  
Locations: [bounded.py:345](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:345), [bounded.py:347](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:347).

Two distinct failures remain:

- A real SIGINT delivered immediately after restoring its handler interrupts restoration of the remaining handlers. I reproduced all six remaining dispositions being left as recorders attached to an abandoned `pending` list.
- If SIGINT and SIGALRM are recorded, a raising SIGINT handler stops the delivery loop. The expired, one-shot alarm is permanently lost. I reproduced catching the interrupt and continuing inside an expired `alarm_deadline`.

This can suppress subsequent termination or deadline enforcement. Installation occurs before resource acquisition, which protects child ownership, but its rollback uses this same interruptible restoration.

**Suggested fix:** make handler handoffs exception-safe, restore every disposition before allowing raising callbacks, and preserve undelivered signals when a callback raises.

**F2 — blocker: deferred SIGALRM reverses composed deadline priority and loses the outer deadline.**  
Locations: [deadline.py:57](/data/haiyangw/claude/auto-code/src/zkqec/exec/deadline.py:57), [deadline.py:71](/data/haiyangw/claude/auto-code/src/zkqec/exec/deadline.py:71).

With an outer `_wall_deadline(0.01)`, inner `_deadline(0.02)`, and `run_bounded`, deferral postpones delivery past both deadlines. `_fire` marks the outer deadline fired, arms the already-expired inner deadline for one microsecond, and can receive that alarm before invoking the outer handler.

**Ten of ten trials raised the inner exception.** Using the actual admission/L2 wrappers, catching `_Deadline` allowed execution to continue past `_WallDeadline`; the outer timer was gone.

**Suggested fix:** deliver the due outer deadline before arming another expired timer, and mark it delivered only after the delivery transition is safe. Add this exact composition regression.

**F3 — blocker: the drain can return `ok`, with complete CPU accounting, while a descendant remains alive.**  
Locations: [bounded.py:245](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:245), [bounded.py:275](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:275), [bounded.py:315](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:315).

Three paths produce the same false absence conclusion:

- **Real descriptor exhaustion:** with sufficient descriptors for `scandir` but insufficient descriptors to open process-stat files, every `EMFILE` is skipped. I reproduced `outcome=ok`, `cpu_complete=True`, and a sleeping grandchild still alive.
- An ownership-query error other than actual ownership loss is treated as the end of successful draining. Injecting an unsupported-query error reproduced the same survivor.
- `/proc` enumeration is not an atomic membership snapshot. A controlled real fork/exit handoff during enumeration left the replacement descendant alive while the drain returned success.

**Suggested fix:** distinguish absence from inability to inspect; uncertainty must prevent `ok` and complete accounting. Kill the owned group before relying on enumeration, and use supervision that reliably tracks membership rather than treating one negative scan as proof.

**F4 — blocker: supervisor death does not contain ordinary descendants; failed PDEATHSIG installation is also ignored.**  
Location: [bounded.py:167](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:167).

I SIGKILLed the supervising parent after setup. Its bounded child died, but an ordinary grandchild remained alive beyond the job’s wall limit. This matters to existing subprocess-producing work, including confirmation’s environment-building subprocesses at [bundle.py:311](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:311). Linux clears PDEATHSIG in a newly forked child; it is not a process-tree guarantee. [Linux documentation](https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html).

Separately, `prctl` returns `-1` on failure; this code neither checks that return nor fails on caught setup errors. Injecting `-1` left even the direct bounded child alive after parent death.

**Suggested fix:** check and require successful installation, retain the existing post-installation parent check, and provide group/tree supervision that survives orchestrator death. The normal parent-dies-before-installation race is correctly addressed by `getppid()`.

**F5 — blocker: exit waits abandon deadline and interrupt enforcement.**  
Locations: [bounded.py:294](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:294), [bounded.py:421](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:421).

After pipe EOF, `_wait_exit` blocks without polling the deadline or `pending`. A worker that closes its result descriptor and continues running bypasses the wall limit. My reproduction remained alive after **0.35 seconds with a 0.05-second limit**, despite a real SIGINT to the parent. Swallowed signal-permission failures can lead into the same indefinite wait.

**Suggested fix:** use nonblocking exit observation with deadline/interrupt checks throughout. An unsuccessful kill must enter an explicit containment-failure path, rather than an unbounded wait.

**F6 — blocker: process-group setup fails open.**  
Locations: [bounded.py:196](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:196), [bounded.py:395](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:395).

Both `setpgid` failures are ignored. Fault-injecting failure in both processes left the worker in its inherited group; the parent subsequently checked the nonexistent group numbered by the child PID. The result was `ok`, complete CPU accounting, and a live descendant.

**Suggested fix:** require verified group membership before executing `fn`, with a startup acknowledgement or equivalent check. A failed containment setup must not execute the workload.

**F7 — blocker: an exception in the child’s initial close escapes into the caller.**  
Location: [bounded.py:384](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:384).

The child’s `os.close(r)` is outside `_child`’s unconditional `_exit` guard. Injecting an `OSError` caused the forked child to unwind through `run_bounded` and execute the caller’s exception handler, before installing limits, the bounded-child marker, or PDEATHSIG. That permits duplicate orchestrator execution without the intended containment.

**Suggested fix:** put the entire `pid == 0` branch inside an unconditional child-only termination guard.

**F8 — minor: cleanup errors leak descriptors or unreaped children.**  
Locations: [bounded.py:426](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:426), [bounded.py:431](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:431).

Real `EMFILE` while opening the `/proc` iterator caused cleanup to fail again, skipping the descriptor loop: two descriptors and an unreaped zombie remained. A separate injected `wait4` error left a zombie because `owned=False` had already disabled cleanup.

These particular reproductions returned no scientific result; their direct consequence is resource leakage.

**Suggested fix:** protect descriptor disposal independently with another `finally`, and track “may still require reaping” separately from permission to signal. Do not restore the old post-reap signaling window.

**F9 — major: controls undercharge child CPU when `run_bounded` raises.**  
Locations: [campaign.py:382](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:382), [campaign.py:407](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:407).

A lifecycle exception prevents `res.usage()` from reaching the caller. `_run_controls` then settles using only the parent’s CPU as though it measured the job. A targeted reporting-failure probe used approximately **0.093 child CPU-seconds**, while settlement supplied approximately **0.0017 CPU-seconds**.

**Suggested fix:** omit unmeasured CPU so the reservation is retained; use `uncertain` when containment is unconfirmed. Confirmation’s initial full-reservation fallback already avoids this particular undercharge.

**F10 — minor for the present campaign: undeferred Python handlers can recreate acquisition gaps.**  
Locations: [bounded.py:77](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:77), [bounded.py:390](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:390).

A non-reaping SIGCHLD handler that raises is consistent with the stated reaping rules but is not deferred. A real SIGCHLD during pidfd acquisition leaked the returned pidfd. The current campaign does not install such a handler, so this is not an additional Phase-1a blocker.

**Suggested fix:** explicitly validate the supported handler environment or cover other installed raising handlers. Reject unsupported configurations before acquiring resources.

**F11 — minor: `alarm_deadline` setup and restoration are not exception-safe.**  
Locations: [deadline.py:64](/data/haiyangw/claude/auto-code/src/zkqec/exec/deadline.py:64), [deadline.py:69](/data/haiyangw/claude/auto-code/src/zkqec/exec/deadline.py:69).

Handler installation precedes the protecting `try`; restoration consists of interruptible timer/handler operations. For example, positive infinity passes validation, `setitimer` raises, and the replacement handler remains installed. `DistanceBudget.max_seconds` also accepts infinity.

**Suggested fix:** validate finite positive durations and protect the complete timer/handler transition, including failed setup and interrupted restoration.

**F12 — minor: late recorded signals can still produce `ok`.**  
Locations: [bounded.py:400](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:400), [bounded.py:472](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:472).

`interrupted` is set only in the read loop. A signal recorded during reaping or closing, whose original handler returns, leaves the result `ok`. I reproduced this with SIGUSR1 after `_reap`.

**Suggested fix:** reconcile pending interruption with the final outcome, or explicitly narrow the documented interrupted-outcome guarantee.

**F13 — minor: nested limit metadata reports requested rather than installed limits.**  
Location: [bounded.py:205](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:205).

Inherited hard limits can clamp `_lower_limit`. A nested reproduction reported `rlimit_cpu=[5,6]` while the actual limits were `(2,2)`. Enforcement was stricter, so this did not create false scientific evidence.

**Suggested fix:** record `getrlimit` after installation and distinguish requested ceilings from effective limits.

**F14 — minor: setup regressions do not explicitly prove reaping.**  
Location: [test_bounded.py:479](/data/haiyangw/claude/auto-code/tests/exec/test_bounded.py:479).

The child-death assertion is conditional on the marker existing, and `_dead` accepts zombies. The tests discriminate the old implementation but could pass with an unobserved child or unreaped zombie.

**Suggested fix:** capture the forked PID independently of worker execution and assert that the direct child has been reaped, alongside descriptor checks.

The remaining requested paths were also assessed:

- Normal completion, wall kill, oversized results, ordinary `ECHILD`, and drain-before-reap ordering passed in top-level/nested and pidfd/fallback modes.
- Pipe/fork failures, parent write-end-close failure, and isolated select/read failures cleaned up in the tested cases. Child limit/setup/reporting failures inside `_child` terminate without returning evidence.
- A basic non-main-thread call and re-entry from a redelivered signal handler passed. Python delivers handlers in the main thread; worker-thread calls therefore have different interruption semantics. The retained M3 worker-start/inheritance gate remains necessary. [Python signal documentation](https://docs.python.org/3/library/signal.html).
- I found no remaining PID-reuse signaling defect under exclusive reaping. Races requiring a foreign reaper between ownership validation and signaling remain outside that invariant.

## C. M1 acceptance evidence and earlier findings

**Every enumerated experimental §M1 Accept item remains supported by m1-live-9.** That does not establish lifecycle acceptance.

| Acceptance item | Result |
|---|---|
| QECForge reference table | PASS; rerun |
| Steane `t=2` certifies d=3 | PASS; rerun |
| Deterministic positive S-A and S-F through production resolver | PASS; rerun with memory-backed storage and real bounded workers |
| Paired negatives | PASS; rerun |
| Admission-only enforcement | PASS; exercised |
| Lower-5/upper-3 quarantine | PASS; exercised |
| Live S-A/S-F, required labels, offline reconstruction | PASS on verified archive and recorded reconstruction |
| Cost telemetry and D5 | PASS |
| Postmortem | Present |

Independent evidence checks established:

- Bundle SHA-256: `ec7b710820de7235edcabece0dec78562f5546b4c58f0912e865cbb1c10a5146`.
- **53 regular members** match the recorded extraction; all three closure hashes verify.
- **97 evaluator files** match HEAD; **101 snapshot fingerprints** match, with no write bits.
- Ledger, artifacts, operational accounting, broker evidence, sidecars and report lint pass.
- Exact replay of **five verifier and two identity executions** passes.
- Recorded control evidence is complete: **20 controls, 0/37 false authorizations, 0/24 false rejections, zero mismatches**.
- Required confirmation links **50 → 51**, validates, and leaves no open reservation.
- Final spend is **$0.1764958**; recorded cumulative M1 spend is approximately **$3.12/$5**.

I verified the existing reconstruction against the archive; I did not create another reconstructed environment or rerun the complete control suite in this read-only review.

The M0 report has **34 qualified / 3 explicitly unqualified** properties with consequences; broker code is unchanged. All prior reviews have triage records. Earlier scientific, broker, ledger, snapshot and supplement findings remain closed on the checked evidence.

**I cannot confirm overall Phase-1a completion or closure of the broader containment/composed-deadline obligations.** Findings F1–F9 expose remaining failures, and this review still requires triage.

## D. D4 and M2

**D4 does not currently permit advancement: containment blockers remain open.**

After their repair and acceptance review, **M2 may proceed without model spend**, retaining every stated gate:

- Resize M3 smoke to **≤$10**, measuring every role and retry.
- Keep Codex dev-only, `K0-unenforced`, with request/token accounting and unknown USD explicitly unknown.
- Recompute and resize the pilot from smoke telemetry to **≤$150**.
- Complete the [M3 prerequisites](/data/haiyangw/claude/auto-code/notes/reviews/M1-triage-2.md:18).
- Retain the [descendant-accounting prerequisite](/data/haiyangw/claude/auto-code/notes/reviews/M1-triage-3.md:19).

The original **$13.31 smoke / $239.53 pilot** workloads remain above their caps. No repository files were modified.