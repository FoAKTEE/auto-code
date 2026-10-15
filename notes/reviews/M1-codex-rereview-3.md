BLOCK M1

## A. N1–N6 and the projection finding

The m1-live-6 scientific evidence passes the checks I could independently repeat. **M1 remains blocked by a process-group identity race introduced in N1’s cleanup**, described in §B.

Reviewed `b382e47..c3a0583`. No repository files were modified. `/tmp` was read-only. Validation comprised **96 passing pytest cases**, **31 filesystem-related fixture setup errors**, **17 passing test functions with adapted memory-backed fixtures**, and the targeted reproductions below. I did not independently rerun the reported 795-test suite or a fresh filesystem reconstruction.

| Finding | Disposition | Evidence, regression discrimination, residual and suggested fix |
|---|---|---|
| **N1 — blocker: nested workers survive enclosing timeout** | **PARTIAL** | The original escape is repaired by [bounded.py:134](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:134) and [bounded.py:157](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:157). My pre-fix reproduction left the inner worker runnable, accumulating another 10 CPU ticks after the outer timeout; HEAD killed it and omitted unmeasured CPU from usage. Re-executed Python also stayed in the enclosing group. These assertions discriminate the original defect. **The new post-reap cleanup race remains unacceptable for Phase 1a; fix B1 below.** |
| **N2 — major: missing counters become zero** | **FIXED** | [policy.py:608](/data/haiyangw/claude/auto-code/src/zkqec/broker/policy.py:608), [providers.py:121](/data/haiyangw/claude/auto-code/src/zkqec/agents/providers.py:121), [bundle.py:940](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:940). Complete SSE/JSON missing either mandatory counter now produces unknown usage. Five provider checks passed, including retries, uncertain outcomes and full-reservation charging. Historical execution accepted missing-input SSE and JSON, and missing-output JSON. Missing-output SSE was already rejected. **Acceptable; no further correction for N2.** |
| **N3 — major: incomplete operational replay** | **FIXED** | [jobs.py:393](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/jobs.py:393), [jobs.py:476](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/jobs.py:476), [jobs.py:517](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/jobs.py:517). In separate memory copies of the sealed ledger, `completed.actual.usd = 999999` and settlement `from = uncertain` both produced audit errors at HEAD; the pre-fix implementation accepted both. The clean copy passed. Detection rather than prevention of operational-table corruption is consistent with the TCB. **Acceptable; no further correction.** |
| **N4 — major: final audit omits confirmation** | **FIXED** | [bundle.py:237](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:237), [bundle.py:279](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:279), [cli.py:76](/data/haiyangw/claude/auto-code/src/zkqec/cli.py:76), [bundle_index.py:99](/data/haiyangw/claude/auto-code/scripts/bundle_index.py:99). The actual m1-live-6 supplement passes; supplying an absent supplement fails final acceptance. I exercised the final wrapper with reconstruction replaced, while retaining the real confirmation checks. The missing-supplement assertions would fail before N4 by code inspection; I did not execute historical reconstruction. **Acceptable; no further correction.** |
| **N5 — major: NaN supplement totals pass** | **FIXED** | [bundle.py:352](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:352), [bundle.py:363](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:363), [bundle.py:570](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:570). NaN, Infinity, `1e999`, negative values, strings, booleans, missing/extra fields, missing units and non-object totals are rejected. `-Infinity` elsewhere is rejected during parsing. Historical execution wrongly accepted NaN, numeric strings, boolean zero and extra fields; several other malformed cases already failed. **Acceptable; no further correction.** |
| **N6 — minor: superseded residual-risk description** | **FIXED** | [M1-slice.md:175](/data/haiyangw/claude/auto-code/notes/postmortems/M1-slice.md:175) now identifies inherited locks, incremental address-space limits, trusted runtime dependencies, nested-child limitations and unbounded direct APIs. This is a documentation correction, with no executable regression. **Acceptable for the existing serial, trusted Phase-1a paths**, subject to the recorded M3 prerequisites and B1. |
| **Projection — major: stale cost basis** | **FIXED as a planning correction** | [M1-slice.md:160](/data/haiyangw/claude/auto-code/notes/postmortems/M1-slice.md:160) correctly gives **$13.3071 smoke / $239.5286 pilot**, retaining the explicitly unmeasured 1.3× overhead. The required resizing and measurement gates are present. Retaining the more expensive m1-live-5 observation is conservative despite m1-live-6 being newer. **Acceptable planning input; dispatch readiness remains separate (§D).** Rename “latest telemetry” to describe this conservative basis accurately. |

## B. New defects and composition checks

**B1 — blocker: cleanup can kill an unrelated process group after PID reuse.**

Locations: [bounded.py:289](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:289), [bounded.py:231](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:231), and the exceptional cleanup at [bounded.py:298](/data/haiyangw/claude/auto-code/src/zkqec/exec/bounded.py:298).

Failure sequence:

1. `wait4(pid, 0)` reaps the group leader.
2. The original group disappears, releasing its numeric identifier.
3. Before cleanup resumes, that PID is reused by a process that leads another group.
4. `_group_alive(pid)` observes the replacement group; `_drain_group(pid)` sends it `SIGKILL`.

There is also a check-to-signal window if the original group disappears after `_group_alive` returns. The comment claiming that `killpg` “never reaches another group” does not cover either interval.

**Evidence:** a deterministic process-lifecycle double exercising `run_bounded` recorded `SIGKILL` against the replacement group after leader reaping. I did **not** force actual kernel PID reuse or observe this during m1-live-6. This is a static race with a deterministic interleaving reproduction, rather than a demonstrated production incident. Its impact crosses the job’s containment boundary.

**Suggested fix:** preserve ownership of the identifier through cleanup—for example, observe leader termination using `waitid(..., WNOWAIT)`, retain the unreaped leader while draining live group members, then reap it. Apply the same ordering to exceptional cleanup. Add regressions for identifier reuse and zombie-only membership; existing nested-timeout and stubbed-escape tests do not discriminate this defect.

The other requested composition checks produced these results:

- **Environment inheritance:** the isolated re-executed Python retained `ZKQEC_BOUNDED_PGID` and stayed in the enclosing group. The actual audit command at [bundle.py:318](/data/haiyangw/claude/auto-code/src/zkqec/report/bundle.py:318) neither clears that variable nor creates a new session/PID namespace. A bwrap timeout reproduction also preserved the group when run without `--unshare-net`; the complete command was unavailable because this review sandbox refused its netlink operation. No inheritance defect found.
- **Zombies and accounting:** `_group_alive` means “a live, non-zombie member exists,” **not literal ESRCH**. Ignoring zombies for further CPU execution is reasonable, but they still occupy process-table entries. I also reproduced the documented accounting limitation: an unreaped descendant consumed about **0.25 CPU-s**, while a normally returning enclosing worker reported about **0.007 CPU-s** with `cpu_complete=True`. This is an existing limitation, not a newly introduced escape. Accept it only for the current trusted paths that reap their children; future worker supervision must account for or retain reservations for unobserved descendants.
- **`escaped` settlement:** verifier and identity share [pipeline.py:237](/data/haiyangw/claude/auto-code/src/zkqec/pipeline.py:237); controls use [campaign.py:385](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:385); confirmation uses [campaign.py:463](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:463). These map to uncertain/full reservation and produce no successful evidence. The adapted verifier escape regression passed.
- **Historical classification:** independently checked m1-live-1..3 as pre-B6, m1-live-4/5 as required by sealed-broker-evidence inference, and m1-live-6 as explicitly required. m1-live-4 still fails its confirmation; m1-live-5/6 pass.
- **`final=False`:** appropriate for the internal replay before its own supplement exists. [campaign.py:460](/data/haiyangw/claude/auto-code/src/zkqec/orchestrator/campaign.py:460) subsequently settles, publishes and checks the supplement. The public audit command and index retain final checking. No public CLI bypass found.

**B2 — minor: the hardening write-up overstates regression discrimination.**

Location: [M1-hardening-3.md:3](/data/haiyangw/claude/auto-code/notes/reviews/M1-hardening-3.md:3).

The assertion that every regression failed before its fix is too broad: historical N5 already rejected Infinity, overflow, negative-open and missing-field/unit cases; missing-output SSE also already failed closed. This can mislead reviewers about test coverage.

**Suggested fix:** distinguish regressions exposing the original defect from additional validation cases. Also replace the unconditional ESRCH claim at line 8 with the actual zombie-aware behavior.

## C. M1 acceptance and Phase 1a definition of done

| §M1 Accept item | Result | Evidence |
|---|---|---|
| qecforge reference table reproduced | **PASS** | Independently rerun [test_verify_l2.py:53](/data/haiyangw/claude/auto-code/tests/verify/test_verify_l2.py:53). |
| Steane `t=2` certifies d=3, not d≥5 | **PASS** | Independently rerun [test_verify_l2.py:73](/data/haiyangw/claude/auto-code/tests/verify/test_verify_l2.py:73). |
| Deterministic S-A and S-F through production resolver | **PASS** | Both positive functions passed with memory-backed storage and real bounded workers: [test_positive_cases.py:49](/data/haiyangw/claude/auto-code/tests/pipeline/test_positive_cases.py:49). |
| Paired negatives rejected | **PASS** | Both corresponding negative functions passed. |
| Admission-only proof | **PASS** | Adapted execution of the guarded-projection test at [test_ledger.py:59](/data/haiyangw/claude/auto-code/tests/admission/test_ledger.py:59), plus issuer/authority tests. |
| Lower-5/upper-3 conflict quarantines and authorizes neither verdict | **PASS** | Independently rerun [test_conflicts.py:26](/data/haiyangw/claude/auto-code/tests/admission/test_conflicts.py:26) using memory-backed storage. |
| Live S-A/S-F within D5, sealed, reconstructable, correctly labelled | **PASS on recorded reconstruction and independently checked contents** | [m1-live-6 report:14](/data/haiyangw/claude/auto-code/runs/campaigns/m1-live-6/report.md:14), [bundle index:12](/data/haiyangw/claude/auto-code/results/bundles/index.md:12). |
| Cost telemetry recorded | **PASS** | Broker reconciliation and final supplement pass. |
| Postmortem written | **PASS** | [M1-slice.md](/data/haiyangw/claude/auto-code/notes/postmortems/M1-slice.md:1). |

For acceptance bundle `29ac9d25c92ef6a10aeb97154372963fa185dffb552e651aa44870ae49b3c46a`, I independently verified:

- Content address, **53 regular members**, and evaluator/runtime/wheelhouse closure hashes.
- Ledger, artifact store, campaign accounting, broker evidence, call sidecars and report lint.
- Exact replay of **five verifier executions and two identity executions**.
- Control evidence containing **20 controls**, **0/37 false authorizations**, **0/24 false rejections**, and zero outcome mismatches.
- Confirmation linkage from ledger sequence **50 → 51**, with all reservations closed.

Final totals are **$0.3318684**, **2 requests**, **6,503 input tokens**, **14,766 output tokens**, **44.1646 CPU-s**, and **204.9150 wall-s**. Recorded cumulative M1 expenditure is approximately **$2.52/$5**, including I10.

Fresh reconstruction and a fresh complete control-suite rerun were unavailable in this read-only environment. The reconstruction verdict relies on recorded confirmation/re-indexing plus the independently verified archive and exact execution replay.

| Phase 1a definition of done | Result |
|---|---|
| M0 qualification with explicit unqualified consequences | **PASS:** [20261015T213850Z report](/data/haiyangw/claude/auto-code/runs/qualify/20261015T213850Z/report.md:4), 34 qualified / 3 unqualified; broker hashes match the acceptance implementation. |
| All enumerated M1 acceptance items | **PASS**, as above. |
| Both live tasks have offline-reconstructable bundles | **PASS**, with the fresh-reconstruction limitation stated above. |
| Every preceding review triaged | **PASS:** [M1-triage-2.md](/data/haiyangw/claude/auto-code/notes/reviews/M1-triage-2.md:3). |

D9, D10, D11 and D16 evidence remains satisfactory. **Overall acceptance is FAIL under D8 because B1 is an open containment blocker**, notwithstanding the passing experimental checklist.

## D. D4 gate

**D4 remains closed; M2 cannot advance automatically while B1 is open.**

The recomputed projection and additions are **acceptable provisional gate inputs**:

| Workload | Projection | Cap |
|---|---:|---:|
| Original smoke | $13.31 | $10 |
| Original pilot | $239.53 | $150 |
| Example resized smoke: 5 tasks × 2 proposals | $8.32 | $10 |

The additions correctly require resizing before smoke dispatch, measuring every role and retry, keeping unqualified Codex dev-only with explicit request/token accounting, and resizing the pilot from smoke telemetry. Unknown Codex USD cost must remain unknown.

After B1 is fixed and reviewed, these requirements can support proceeding with M2 while retaining the binding pre-dispatch gates. The original smoke/pilot workloads remain over budget; the example smoke still depends on unmeasured role overhead. Neither the lower m1-live-6 charge nor reservation enforcement demonstrates that the original workloads fit D5.