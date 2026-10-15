BLOCK M1

## A. B1 is not fixed on every requested path

Reviewed `63ace07..363302d`, read-only.

The original interrupt-after-`wait4` defect is fixed: `owned = False` precedes reaping. Normal completion, wall kill, oversized results, ordinary `ECHILD`, and interrupts around the normal `waitid`/`wait4` operations passed my checks, including nested mode and pidfd fallback.

**B1 — blocker: interrupted `ECHILD` handling still permits post-reap numeric signals without a pidfd.**

Locations: [bounded.py:210](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:210), [bounded.py:265](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:265), [bounded.py:354](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:354).

**Failure scenario:** pidfd acquisition fails; another reaper takes the child; `_wait_exit` receives `ECHILD`; an interrupt lands before ownership is cleared. Cleanup still sees `owned=True`. `_still_ours(None)` returns `True`, and the fallback sends numeric signals.

I reproduced these traces with real child reaping, intercepting the subsequent signals:

```text
top-level: reap → ECHILD → interrupt → killpg(pid, SIGKILL) → kill(pid, SIGKILL)
nested:    reap → ECHILD → interrupt → kill(pid, SIGKILL)
```

Actual PID reuse was not forced.

**Suggested fix:** make loss of ownership safe across interruption, and require positive ownership validation before either fallback signal. Fail closed when ownership cannot be established. Add combined `ECHILD`/interrupt/fallback regressions for both modes.

This reproduction exercises the explicitly requested ownership-loss case. **Under the maintained exclusive-reaping invariant, I found no remaining check-to-signal reuse window.** Retaining the unreaped leader and using pidfds are sound within that invariant. The relevant semantics are documented by [Linux wait](https://man7.org/linux/man-pages/man2/waitpid.2.html) and [pidfd signaling](https://man7.org/linux/man-pages/man2/pidfd_send_signal.2.html).

All five added regressions **fail against `63ace07` and pass at HEAD**. They discriminate their individual defects, but omit the combined case above.

## B. Lifecycle cleanup still has a containment gap

**B2 — blocker: interruption during pidfd setup bypasses child cleanup.**

Locations: [bounded.py:313](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:313), [bounded.py:324](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:324), [bounded.py:362](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:362).

**Failure scenario:** the child exists and `pidfd_open` has returned, but the lifecycle `try/finally` has not begun. A single interrupt escapes without killing or reaping the child or closing the descriptors.

I delivered a real, parent-directed `SIGINT` at line 316. In both top-level and nested mode, a sleeping child remained alive after **0.15 seconds despite a 0.05-second wall limit**; the read pipe and pidfd remained open. This requires neither a foreign reaper nor ignored `SIGCHLD`. I killed and reaped the test children afterward.

The underlying unprotected setup interval predates this patch; `5d26221` adds pidfd acquisition and its descriptor leak to that interval. I also reproduced a pidfd leak when interruption occurs before the final close.

**Suggested fix:** establish cleanup protection across child/resource acquisition, defer interrupts across the short ownership transitions, and protect descriptor disposal independently of child cleanup. Add interruption tests during setup and final cleanup.

Otherwise, I found no new outcome or accounting defect:

- Actual `ownership_lost` results produce no verifier receipts or identity records and retain the full reservation.
- Controls fail and settle `uncertain`; confirmation fails and retains its full reservation.
- Ordinary paths showed no pidfd leaks or hangs.
- Real nested deadline/outer-kill checks passed.
- **R1 is fixed:** the module documentation now describes draining before reaping.

Validation included **39 passing focused pytest cases**, **32 additional lifecycle combinations**, regression comparisons, and memory-backed acceptance/accounting checks. I did not independently rerun the reported 802-test suite.

## C. m1-live-8 acceptance evidence passes

Every enumerated [§M1 Accept item](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:565) remains supported:

| Item | Result |
|---|---|
| qecforge reference table | PASS; rerun |
| Steane `t=2` certifies d=3 | PASS; rerun |
| Deterministic positive S-A/S-F through production resolver | PASS; memory-backed storage, real bounded workers |
| Paired negatives | PASS; both rerun |
| Admission-only enforcement | PASS; admission guards exercised |
| Lower-5/upper-3 quarantine | PASS; neither verdict authorized |
| Live S-A/S-F, required labels, offline reconstruction | PASS on verified archive and recorded reconstruction |
| Cost telemetry and D5 cap | PASS |
| Postmortem | PASS |

Independent verification established:

- Bundle SHA-256: `6848dfb8b41407a522a962745407095877dcd424b63eef5a2fe61c5531ecd283`.
- **53 regular members**, matching the recorded extraction; all three closure hashes verified.
- **97 embedded evaluator files** match HEAD; **101 snapshot fingerprints** match, with no write bits.
- Ledger chain/projections, artifacts, operational accounting, broker evidence, sidecars and report lint pass.
- Exact replay of **five verifier executions and two identity executions**.
- Complete evidence for **20 controls**, **0/37 false authorizations**, **0/24 false rejections**, and zero mismatches.
- Required confirmation linkage **50 → 51**, with no open reservations. Missing confirmation and invalid totals are rejected.

Final totals are **$0.2077358**, **2 requests**, **6,503 input tokens**, **10,321 output tokens**, **43.361815433 CPU-s**, and **154.616345788 wall-s**. Cumulative recorded M1 expenditure, including I10, is approximately **$2.95/$5**.

The qualification report remains **34 qualified / 3 explicitly unqualified**, and its broker hashes match this implementation. N2–N6, the projection correction, and earlier documentation corrections remain closed; both N3 accounting mutations were rejected again.

Fresh filesystem reconstruction and a complete fresh control-suite execution were unavailable under read-only permissions. Reconstruction is supported by the recorded confirmation, independently verified archive contents, and exact execution replay.

The four enumerated Phase-1a definition-of-done evidence items remain supported, including triage of preceding reviews. **Overall acceptance still fails the open-containment-blocker gate.**

## D. D4 advancement

**M2 cannot yet proceed automatically, including without model spend.** Closing B1 alone would leave B2. These are the open blockers identified in this review.

After repair and review, retain the previously accepted gates:

- Resize M3 smoke to **$10**, measuring every role and retry.
- Keep Codex dev-only, `K0-unenforced`, with request/token accounting and unknown USD reported as unknown.
- Recompute and resize the pilot from smoke telemetry to **$150**.
- Complete the [M3 prerequisites](/data/haiyangw/claude/auto-code/notes/reviews/M1-triage-2.md:18): controlled worker inheritance, refusal of unbounded production admission, and bounded audit replay.
- Retain the [descendant-accounting prerequisite](/data/haiyangw/claude/auto-code/notes/reviews/M1-triage-3.md:19).

The original **$13.31 smoke / $239.53 pilot** workloads remain above their caps. No repository files were modified.