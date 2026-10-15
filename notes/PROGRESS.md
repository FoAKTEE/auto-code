# Progress log

## 2026-10-15 — M0: analysis and plan (in progress)
- Read `ref-code/qecforge` in full (paper, appendices, docs, all source, tests, configs, schemas, artifacts).
  Verified here: 60 core tests pass, 4 optional skipped (stim/ldpc/pymatching absent; pip can install them).
- Reference-workflow analyses written by three parallel read-only agents:
  `notes/ref-analysis/{qa,chandra,question-gen}.md`.
- Environment probes: headless `claude -p --output-format json` works (reports tokens/cost);
  `codex exec` (gpt-6-astra, max) works when the prompt is passed as an argument with `< /dev/null`;
  `bwrap` isolation works (masking, no network, read-only FS).
- Draft master prompt: `prompt/00-master-prompt.md` (v0.2). Codex blind analysis and prompt review running.
- Build has NOT started; waiting for owner approval of the prompt (decisions D1–D9).
- Commit discipline switched to Chandra's, byte-identical: `.gitmessage`, `_common/hooks/{commit-msg,install.sh,README.md}`,
  `_common/contracts/commit_template.md`, `tests/test_commit_msg_gate.py`, `.claude/skills/commit-gated/SKILL.md`;
  rule restated in `AGENTS.md`; hook installed (`core.hooksPath=_common/hooks`); gate self-test 23 passed.
  The first (non-compliant) M0 commit was unpushed and was re-landed as node-scoped gated commits.
- Codex blind analysis (`prompt/reviews/codex-independent-analysis.md`) and v0.2 review
  (`prompt/reviews/codex-prompt-review.md`, 35 findings, 6 blockers) received. Reproduced here: qecforge
  `analyze_postselected_map` accepts a non-unitary target at tol 1e-10 and passes a 1-rad relative phase on a tiny
  Kraus operator; demo operator bound 0.07 -> observable bias 0.14 > 0.1 target.
- Host facts added: socat present; user cgroup scopes (`systemd-run --user --scope -p MemoryMax -p TasksMax`) work;
  codex web search only with `--search`.
- Prompt v0.3: phased plan (Phase 1 = M0 host qualification, M1 live slice, M2 verifier hardening, M3 agent runtime,
  M4 evaluation; algebra-level claims only), binding appendix `prompt/01-technical-contracts.md`, triage
  `prompt/reviews/TRIAGE.md`. Codex review of v0.3 launched. Build still waits for owner approval (D1–D16).
- Codex review of v0.3 (`prompt/reviews/codex-prompt-review-v0.3.md`): "with changes"; 17 triage gaps + 12 new
  findings (2 blockers: CONNECT cannot enforce API policy; no process isolation). Host check: codex enables
  shell/multi-agent/apps/browser/computer-use/plugins by default.
- Prompt v0.4: Phase 1a (M0 host qualification incl. TLS-inspecting broker, M1 narrowed live slice) approved first;
  Phase 1b (M2–M4) continues only under D4 conditions; appendix v0.4 fixes distance completion rule (Steane t=2),
  Singleton "if", sector-correct disjoint bound, predicate-keyed transport, revocation/quarantine, dispatch state
  machine, fsync-dir publication, sanitize-before-hash, offline executable bundles, 15-mode control mapping.
- Codex v0.4 verification review: "yes-with-listed-edits — Phase 1a"; five majors fixed in v0.5 (input-charge
  reservations, dispatch recovery moved into M1, semantic bound conflicts, slice deadline via supervisor on
  systemd 249, archived runnable runtime).
- M0 probe (mitmproxy 11.0.2 in `.venv-broker`): Claude under normal HOME leaks ~20 claude.ai connector tools even
  with `--tools ""`; fresh HOME + strict empty MCP config gives tools {StructuredOutput, DeferredToolPlaceholder}
  only. Codex speaks WebSocket to chatgpt.com; with features disabled its frames have no tools key. TLS interception
  works for both. `~/.local/bin/codex` silently falls back to Claude on usage limits; use `~/.local/node/bin/codex`.
- Prompt v0.5 is final for Phase 1a; owner instructed to proceed with defaults (G5). Build started: `.venv` (uv,
  pinned + hash lock), kernel vendored byte-identically, 64/64 upstream tests pass.

## 2026-10-15 — Phase 1a build (M0 + M1) in progress
- Landed: env scaffold + hash lock (d54f1a6); kernel vendored (a93de63); standalone CPython 3.10.22 runtime
  (a5fac07); contracts (5b375f7); kernel fix KD-1 + supersession guard (9a91887); designer contracts + prompt
  linter (f4e1b23); B1 random two-block baseline (310f238).
- Running in parallel (separate file ownership, lead commits): (A) store + admission ledger + resolver;
  (B) grammar + identity + verifiers L0–L3; (C) M0 sandbox + cgroup slice + TLS-inspecting broker + CLI launch +
  `qualify-host`.
- Planned M1 live tasks (cheap to certify; final values fixed after B reports measured feasibility):
  S-A `sa-live-1`: n_max 60, k_min 4, d_min 4, w_max 6, deg_max 6 (d ≥ 4 needs exclusion through weight 3:
  t = 2 MITM, C(60,≤2) = 1831 half-patterns). S-F `sf-live-1`: k_req 2, target logical SWAP
  (X1↔X2, Z1↔Z2), allowed class qubit_permutation, n_max 40, k_min 2, d_min 3.
- Deterministic positive fixtures (no LLM): S-A — bicycle_chain(3,7) [[42,6,6]] as explicit_sparse against
  (n_max 50, k_min 6, d_min 6, w_max 8); S-F — B's verified permutation fixture.

## 2026-10-15 — M1 live slice, Codex review, hardening, acceptance evidence
- M0 qualification (`runs/qualify/20261015T105932Z`): Claude K0-OK; Codex not tool-free (G6); slice CPU quota
  unenforceable (G7). Re-qualified on the streaming broker with the production-schema property:
  `runs/qualify/20261015T123410Z`, 33 qualified / 3 unqualified.
- m1-live-1: 8 launch failures (draft-2020-12 `$schema` URI in the designer schema), $0. m1-live-2: both tasks met
  on proposal 1 ([[60,8,6]], [[14,2,3]] + SWAP), $0.652 charged; one call retried after a buffered response (I2).
  Both pre-hardening, not acceptance evidence.
- Codex review of the committed M1 code: block (3 blockers, 14 majors), all accepted
  (`notes/reviews/M1-triage-partial.md`); fixed test-first in 19 commits (`notes/reviews/M1-hardening.md`).
- Lead fixes: streaming broker (daf36b4), list-price table (eed11b3), production-schema qualification (9da3430),
  §K reservation floor (3f046ed), BLAS pinning; bundle index with offline re-audit (`results/bundles/`).
- P1a control suite (20 controls, 0/37, 0/24) recorded by campaigns and reproduced by the audit
  (`notes/build/controls.md`). Feasibility tables (548 rows, 51 codes) and D10 ranges (G8).
- m1-live-3 on hardened code (7d8c36d): both tasks met on proposal 1, $0.360, 2 requests, controls recorded,
  bundle v2 audits offline from the file alone. Postmortem: `notes/postmortems/M1-slice.md`.
- Next: Codex M1 milestone review → `notes/reviews/M1-triage.md`; Phase 1b proceeds only under D4.
- Codex milestone review: BLOCK (B1 broker fail-open on log failure; majors B2–B8; #7, #13–#16 partial; the §O
  snapshot import is P1a, the lead's deferral was wrong) → `notes/reviews/M1-triage.md`. Second hardening round by
  two engineers (E1 evidence/accounting, E2 execution/isolation), 16 commits; lead fixes for the snapshot broker
  (zero mtimes, I8), controls in snapshot mode (I9) and a live-call barrier for test runs after an unintended $0.205
  test-suite campaign (I10). Re-qualified: `runs/qualify/20261015T200053Z` (34 / 3).
- m1-live-4 (snapshot): targets met, controls refused → audit fails (not evidence). m1-live-5 (snapshot, 9458710):
  both targets met, controls passed 0/37, 0/24, supplement linked, $0.68; cumulative M1 live $2.19 of $5.
- Next: Codex re-review → `notes/reviews/M1-triage-2.md`; Phase 1b only under D4.
- Codex re-review: BLOCK (N1 nested bounded workers escape the enclosing kill; N2–N5; stale projection) →
  `notes/reviews/M1-triage-2.md`; third hardening round by E3 (92dcf66..c8089ed); re-qualified
  `runs/qualify/20261015T213850Z` (34 / 3); full suite 795 passed.
- m1-live-6 (snapshot, 6e7e610): both targets met, controls passed, required supplement linked, $0.33; cumulative
  M1 live $2.52 of $5. Next: Codex re-review of round 3 → `notes/reviews/M1-triage-3.md`.
- Codex re-review 3: BLOCK on one new blocker (B1: group cleanup after the leader was reaped could signal a reused
  id); N2–N6 and the projection fixed; every §M1 Accept item passes → `notes/reviews/M1-triage-3.md`. B1 fixed
  (997b815); suite 797 passed; m1-live-7 (snapshot, f70055e): both targets, controls passed, supplement linked,
  $0.22; cumulative M1 live $2.74 of $5. Next: focused Codex review of B1 + m1-live-7.
- Codex re-review 4: BLOCK on residual B1 (post-reap signals on an interrupt after the reap or after ECHILD) →
  `notes/reviews/M1-triage-4.md`; fixed with explicit pid ownership + pidfd (5d26221); suite 802 passed;
  m1-live-8 (snapshot, 01d1f32): both targets, controls passed, supplement linked, $0.21; cumulative M1 live $2.95.
