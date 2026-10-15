BLOCK M1

## A. B1 is partially fixed

Reviewed `f039905..eb05968`, read-only. **The normal cleanup ordering is repaired, but exceptional paths can still signal an identifier after its child has been reaped.**

**B1 — blocker: post-reap signaling remains possible.**

Locations: [bounded.py:301](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:301), [bounded.py:305](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:305), and [bounded.py:235](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:235).

Two failure scenarios remain:

1. **Interruption after reaping.** `wait4` reaps the child at line 301, but `reaped` is assigned at line 302. A `KeyboardInterrupt` between those operations enters `finally` with `reaped is None`. Line 306 consequently sends `SIGKILL` to the released PID and, for top-level runs, its numeric PGID. PID reuse can redirect either signal outside the job.
2. **Child already reaped elsewhere.** `_wait_exit` suppresses `ChildProcessError`, although it then holds no zombie protecting the identifier. Top-level execution proceeds to `_drain_group`; the subsequent failing `wait4` also triggers `_kill` in `finally`. Nested execution reaches the same unsafe PID kill. Another reaper can likewise invalidate the ownership assumption between successful `WNOWAIT` observation and signaling.

I reproduced both scenarios with real child reaping, for top-level and nested runs. For the first, I delivered `SIGINT` at the line-302 boundary. The top-level trace was:

```text
wait4 reaps → SIGINT → killpg(pid, SIGKILL) → kill(pid, SIGKILL) → killpg(pid, 0)
```

Post-reap signals were intercepted, not delivered. **Actual kernel PID reuse was not forced.** The interruption scenario requires no competing reaper.

**Suggested fix:** enter an irreversible “no further signals” state **before** calling a reaping wait; handle reaping separately from cleanup eligibility. Treat `ECHILD` as lost ownership, with no subsequent numeric signaling. Establish exclusive reaping ownership throughout signaling, and preserve cleanup/reaping across asynchronous exceptions. Add regressions for both scenarios, covering PID and PGID signals.

The other path checks passed:

| Path | Result |
|---|---|
| Normal completion | Observe exit → drain → reap |
| Wall timeout | Kill → observe exit → drain → reap |
| Oversized result | Same safe ordering; returns `crashed` |
| Interruption before final reap | Kill → observe exit → drain → reap |
| Nested deadline | PID-only kill before reap; enclosing group remains intact |
| Group probe → kill, with exclusively owned zombie retained | Identifier remains protected |

`WNOWAIT` correctly leaves the child waitable for the subsequent `wait4`. [Linux wait documentation](https://man7.org/linux/man-pages/man2/waitpid.2.html) supports that ordering. Independent execution confirmed:

- Repeated signals with a non-raising handler did not break `waitid`.
- `wait4` retained child and waited-descendant CPU usage, charged once.
- The new ordering regression **fails before 997b815 and passes at HEAD**.
- The zombie-only regression passes at both revisions; it validates compatibility with the new ordering, rather than exposing the original defect.

## B. Other changes and regressions

**No separate new runtime defect found beyond residual B1.** Ordinary nonterminating work reached its wall kill; oversized results, CPU/memory limits, nested containment and `escaped` outcomes behaved as expected. Normal cleanup reaped the leader. The additional exit observation did not consume or double-count its resource usage.

The previously accepted limitations concerning unobserved descendant CPU and reparented zombies remain; they are not repaired by this change.

**R1 — minor: lifecycle documentation still describes the unsafe ordering.**

Location: [bounded.py:22](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:22).

**Failure scenario:** a maintainer following the module documentation would drain the group after reaping, recreating B1.

**Suggested fix:** describe draining while the leader remains unreaped, followed by the final reap.

Independent validation included **56 passing pytest cases**, adapted memory-backed acceptance/regression tests, and the targeted lifecycle reproductions. Eight snapshot-test fixtures could not start because temporary directories are read-only. I did not independently rerun the reported 797-test suite.

## C. Acceptance evidence

**Every enumerated §M1 Accept item still passes**, subject to the reconstruction limitation below.

| Acceptance item | Result and evidence |
|---|---|
| qecforge reference table | PASS; reference tests rerun |
| Steane `t=2`: d=3, not d≥5 | PASS; test rerun |
| Deterministic positive S-A and S-F through production resolver | PASS; memory-backed fixtures with real bounded workers |
| Paired negatives rejected | PASS; both rerun |
| Admission-only proof | PASS; SQL admission guards exercised |
| Lower-5/upper-3 conflict quarantined; neither verdict authorized | PASS; control rerun |
| Live S-A/S-F, D5 cap, sealed reconstruction evidence and required labels | PASS on verified contents and recorded offline reconstruction |
| Cost telemetry | PASS; accounting, broker and supplement reconciliation |
| Postmortem | PASS; [M1-slice.md](/data/haiyangw/claude/auto-code/notes/postmortems/M1-slice.md:1) |

For m1-live-7, I independently verified:

- Bundle SHA-256: `78d2f0a8667beda9e2dcfb80070c206d35731b59e757a827da0adb62057af26b`.
- **53 regular members**, matching the recorded extraction; all three closure hashes.
- All **97 embedded evaluator files** match `f70055e` and current HEAD. The snapshot’s **101 fingerprinted files** match their hashes and have no write bits.
- Ledger chain/projections, artifact hashes, campaign and operational accounting, broker evidence, call sidecars and report lint.
- Exact replay of **five verifier executions and two identity executions**.
- Complete evidence for **20 controls**, **0/37 false authorizations**, **0/24 false rejections**, and zero outcome mismatches.
- Required confirmation linkage **50 → 51**, with no open reservations. An absent required supplement is rejected.
- Supplement SHA-256: `e90efa82f76ec3534881fab37a8650532f518333b439c1763067aa55a7a46e29`.

Final totals are **$0.2173158**, **2 requests**, **6,503 input tokens**, **10,800 output tokens**, **43.384299294 CPU-s**, and **160.59941984340549 wall-s**. Indexed M1 charges plus the recorded I10 expenditure total approximately **$2.738772/$5**.

Fresh filesystem reconstruction and a complete fresh control-suite rerun were unavailable in this read-only environment. Their verdict relies on the recorded confirmation/re-indexing, supported by independent archive verification, control-evidence validation and exact execution replay.

The four enumerated Phase-1a definition-of-done items remain supported: qualification reports **34 qualified / 3 explicitly unqualified**, its broker hashes match the acceptance implementation, the M1 checklist passes, both tasks have reconstruction evidence, and preceding reviews are triaged.

**N2–N6, the projection correction, and the previous B2 documentation correction remain closed.** Missing-counter checks passed; both N3 operational mutations were rejected; required-supplement and malformed-total checks remained effective. Earlier findings outside this change have not regressed. **Overall M1 acceptance nevertheless fails D8 because B1 remains open.**

## D. D4 advancement

**M2 cannot proceed automatically while residual B1 remains open**, including with no model spend. This follows [D4/D8](/data/haiyangw/claude/auto-code/prompt/00-master-prompt.md:705).

After B1 is completely repaired and reviewed, I identify no additional blocker preventing M2 under the previously accepted staged gates. Retain:

- Resized M3 smoke fitting **$10**, with every role and retry measured.
- Codex dev-only, `K0-unenforced`, with explicit request/token accounting; unknown USD cost remains unknown.
- Pilot recomputed and resized from smoke telemetry to fit **$150**.
- The [M3 prerequisites](/data/haiyangw/claude/auto-code/notes/reviews/M1-triage-2.md:18): clean-exec or forkserver workers with controlled inherited handles, refusal of unbounded production admission, and bounded audit replay.
- Worker supervision accounting for unobserved descendants, as retained in [triage 3](/data/haiyangw/claude/auto-code/notes/reviews/M1-triage-3.md:19).

The original **$13.31 smoke / $239.53 pilot** workloads remain over their caps. The lower m1-live-7 charge does not remove those dispatch gates.