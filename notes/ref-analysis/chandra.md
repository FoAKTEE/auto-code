# Reference analysis: `ref-code/Chandra/`, the general research-automation workflow

Studied read-only on 2026-10-15. All paths below are relative to `ref-code/Chandra/` unless they are absolute. "Line N" means the line in the file as it is checked out. The owner says this workflow is "not good enough yet". This report explains what it does, how it is wired, what evidence shows about how it performed, and which parts carry over to a zero-knowledge QEC code-discovery workflow.

---

## 1. Purpose and intended use

Chandra describes itself as "a methodology and runtime for running LLM agents on long-horizon theoretical-research missions — parsing papers, deriving results, writing and running code, transferring theorems, drafting a paper" (README.md). Every component hangs off one invariant:

> "The agent proposes; the verifier admits. Nothing an agent *says* counts as progress." (README.md; alignment.md §0)

The target use case is reproducing and extending an arXiv paper. A mission goes through four stages:

1. **acquire**: mirror the paper and code with provenance.
2. **decompose**: turn the paper into a claim ledger and a DAG of nodes.
3. **work**: for each ready node, produce evidence, validate it, and append an admitted ledger row.
4. **write**: render a REVTeX paper from admitted rows.

Research happens in a "consumer repo" that adopts the methodology. That repo holds `results/` (ledgers plus artifacts), `progress/<mission>/` (notes), and gitignored `ref-paper/` and `ref-code/` mirrors.

The problem it tries to solve is narrower than "research automation". The real target is **LLM self-report**: agents claim success they have not achieved, tweak parameters in loops, lose context, and write prose that hides gaps. Most of the machinery exists to make "progress" mean "a row that passed an executable gate", not "an agent said so".

In practice the repo also became its own main consumer. It contains three missions:

- `paper_self`: Chandra optimizing itself.
- `paper_vibe`: a VS Code fork with a ledger-DAG viewer, in `vibe/`.
- `paper_move47`: an LLM + MCTS Go player tested against KataGo, in `move47/`.

None of the three is a theoretical-physics paper reproduction (§6).

**Scale:** about 562 files. The orchestrator is about 3.0k lines of TS source plus 3.3k lines of TS tests. `_common` holds about 6.2k lines of Python ledger, loop, skill, and visualization code, and `tests/` holds 4.8k lines of pytest. There are 33 skills totalling about 242 KB of prose. Git has 106 commits between 2026-08-08 (public squash) and 2026-10-08.

---

## 2. Architecture

### 2.1 Components and directory roles

| Dir / file | Role |
|---|---|
| `alignment.md` | ~2.4 KB "kernel" (§0–§6) injected at every session start. Capped by design. |
| `INDEX.md` | Canonical index and workflow contract; "wins when any doc disagrees". |
| `AGENTS.md` | Entry point for Codex and other non-Claude agents. |
| `.delegation-policy` | One word, `strict`; read by the admission gate. |
| `.claude/settings.json` | SessionStart hook (`inject_infra.sh`) and a SubagentStop hook that only prints a reminder. |
| `.claude/skills/` | 33 SKILL.md procedures, a generated `INDEX.md`, and `admitted.json` receipts. There are no `agents/` or `commands/`. |
| `pipelines/{0-acquire,1-decompose,2-work,3-write}/spec.md` | Stage contracts: role, I/O, gates, anti-patterns. 2-work also has a 16-section `template.md`. |
| `_common/ledgers/` | Four append-only hash-chained JSONL ledgers (`result`, `knowledge`, `claims`, `error`), `admission.py` (the executable gate), and `txn.py` (repository flock). |
| `_common/loop/` | `loop_policy.py` (crash-triage, check-pivot, simplification status, paper-refresh cadence) and `loop_gate.py` (a legacy Python circuit breaker). |
| `_common/skills/skill_registry.py` | Validates skills, executes each `## Verify` block against the candidate's bytes, and writes receipts. |
| `_common/visualization/` | Mermaid DAG renderer and a self-contained HTML dashboard. |
| `_common/contracts/` | Admission contract, markers vocabulary, note discipline, progress principles, and a 16 KB commit-message grammar. |
| `_common/hooks/commit-msg` | Git hook enforcing the commit-title grammar. |
| `orchestrator/src/` | TypeScript runtime: `main.ts` (wave loop), `dag.ts`, `scheduler.ts`, `jobs.ts`, `agents.ts` (prompts and runners), `validator.ts`, `observer.ts`, `gate.ts`, `digest.ts`, `gitops.ts`, `checkpoint.ts`, `codex.ts`, `ledger.ts` (the Python bridge). |
| `notes/` | Three-note template, an opt-in "PUA" pressure skill, and `knowledgebase_redesign.md`, a 44 KB self-diagnosis refuted by three reviewers. |
| `results/ledgers/` | Real ledgers for `self`, `vibe`, and `move47`. |
| `results/move47/.../evidence/` | Run evidence bundles. |
| `vibe/`, `move47/` | Consumer missions kept in the same repo. |

### 2.2 Data and state flow

```
ledgers (JSONL, hash-chained, git-tracked)
   │  query via python CLIs (ledger.ts bridge, 16 MiB cap)
   ▼
buildMission(knowledge, claims) ─► readyFrontier (all preds solid) ─► extractPackets (chains)
   │                                                                     │
   │       readyJobs: decompose | acquire(owner=0-acquire) | work-packet | write-refresh
   ▼                                                                     ▼
Promise.all(workers)  ── each worker session appends rows itself via gated CLIs (CHANDRA_ROLE=worker)
   │
snapshot diff (before/after) ─► per-node outcome: admitted / promoted / rejected / failed / no_progress
   │
observer (notes rewrite + 10 KB cap) ─► skill harvest ─► gate (no-progress streak) ─► verify chains
   ─► digest (every 5 context windows) ─► commitWave (CHECKPOINT.json + git add -A + gated commit)
```

**Canonical state** is the four ledgers only. `results.md`, `claims.md`, `summary.csv`, the dashboard, and the Mermaid DAG are all regenerated views (contract: "never hand-authored").

**Operational diaries** live outside the repo under `/tmp/chandra/<repo>-<sha8>/` (`runtime.ts`). These include the journal JSONL, packet WALs, validation review files, and Codex transcripts.

**Mission memory** is three notes under `progress/<mission>/`, rewritten by the observer:

- iteration note (current wave),
- nodal note (last 10 waves),
- `RESEARCH_STATE.md`, hard-capped at 10,240 bytes.

### 2.3 Launch and resume

**Launch:** `npm run plan` / `npm run run-mission -- --repo-root <consumer>`, configured by `mission.json` (paper, maxWorkers, maxWaves, packetSize, digestThreshold, per-role models). Prefixing a model with `codex:` runs that role on the Codex CLI.

**Resume:** rerun the command. `main.ts:162` reads the last `wave_finished` from the journal and restarts the wave count from there. Because all scheduling state is recomputed from the ledgers each wave, resuming is cheap. Wave numbering, gate state, and packet size partly reset; the drift obligations say so.

**Human control** (read between waves):

- `progress/.../PAUSE` halts gracefully with exit 7.
- `STEER.md` free text is injected into packet-worker prompts.
- `HUMAN_DIGEST.md` is written every 5 context windows.

**Exit codes** are typed: 0 complete, 2 wave budget exhausted, 3 no ready jobs, 4 circuit breaker, 6 commit rejected, 7 pause, 8 tampered ledger, 9 unreadable ledger.

### 2.4 What persists between steps

- Ledgers and `results/ledgers/CHECKPOINT.json` (ledger heads anchored in each wave commit; `checkpoint.ts`).
- Notes (but `progress/` is gitignored, so "history in git" is false; obligation `drift-progress-gitignored`).
- Admitted skills.
- The journal and WALs, but only until `/tmp` is cleared.

Worker transcripts never enter the orchestrator's context: "the orchestrator holds only the journal tail + frontier" (`main.ts:1-3`).

---

## 3. Agent roles and orchestration mechanics

### 3.1 Roles

| Role | How invoked | Tools / sandbox | Writes |
|---|---|---|---|
| **Worker** (stage-2 packet) | Fresh Claude Agent SDK `query()` per packet (`agents.ts:71-105`), or `codex exec` (`agents.ts:108-126`) | Read/Bash/Write/Edit/Glob/Grep, `acceptEdits`, `settingSources: []`, cwd = repo root, `CHANDRA_ROLE=worker` | Ledger appends via CLI |
| **Job agents** (decompose / acquire / write) | Same runners with `jobPrompt` (`agents.ts:148-184`), maxTurns 120, WebFetch allowed | Same | Ledger rows, artifacts |
| **Refuter** | Fresh session, cwd = temporary context-pack dir (`validator.ts:362-399`) | Read/Grep/Glob/Bash, `CHANDRA_ROLE=validator`; Codex validators default to read-only | Free-text findings only |
| **Judge** | Fresh session in the same pack, given the refuter findings | Same | One JSON verdict; the orchestrator executes it |
| **Observer** | In code (`observer.ts`); an LLM is used only to compress prose to fit the 10 KB cap (`SdkObserverRunner`, no tools, maxTurns 4) | No tools | Notes |
| **Orchestrator** | The TS wave loop, or an interactive Claude Code session following `mission-run` / `codex-gpt6-max` | Roleless `Ledgers` (read-only by policy) | Wave commits |

### 3.2 How work is delegated: `.delegation-policy`

The file contains `strict`. `admission.check_actor_role` (`_common/ledgers/admission.py:67-80`) rejects any ledger append whose process lacks `CHANDRA_ROLE ∈ {worker, validator, observer, human-override}` and records the role on the row as `actor_role`.

The intent, from kernel §6, is that the launching session "ORCHESTRATES; it does not produce evidence, write research code, or append rows." This is enforced by an **environment variable only**. Any process can set it, and §6 below shows the orchestrating session doing exactly that.

### 3.3 Parallelism and independence

- **Parallel waves:** "ONE Promise.all over the whole packet set — parallel by construction" (`scheduler.ts:95-117`). Non-packet jobs run concurrently with the packets (`main.ts:259-262`).
- **Packets:** `extractPackets` (`scheduler.ts:34-58`) extends each frontier root along its *unique* successor chain, stopping at branch points, so that "the reasoning stays warm across the chain." Packet size adapts: it halves if a packet used more than 3 context windows and doubles (up to 8) if every packet fit in one window (`main.ts:274-275`).
- **Compaction survival:** the worker keeps an ungated WAL (`packet_log.jsonl`). "If your context auto-compacts, RE-READ your packet_log.jsonl … then CONTINUE" (`agents.ts:52-54`).
- **Independence:** parallel workers are isolated only by being separate sessions on a shared working tree. There is no worktree per worker. Outcomes are attributed per node by ledger diff "so parallel workers cannot claim each other's progress" (`scheduler.ts:119`). A concurrent acquire job can still be credited to a packet (obligation `kb-progress-attribution`).
- **Writer races:** before the redesign, parallel appends corrupted the hash chain in 3/3 runs (`notes/knowledgebase_redesign.md` P11). This was fixed by the flock and transaction protocol in `txn.py`: verify outside the lock, re-validate inside it, fsync.

### 3.4 How agents communicate

Agents never message each other directly. All communication is files:

- **Ledgers** carry rows with a JSON schema; the `schema` and `describe-fields` CLIs print the spec, and validation errors quote the allowed enum values back so the agent can self-correct.
- **Journal** (`journal.ts`): typed `Message` union in `types.ts:79-96`, one JSONL line each.
- **Context packs** for validators: allowlisted files plus `CLAIM.md` plus `MANIFEST.json` of sha256 hashes.
- **Validation review files** under the runtime directory.
- **Human files:** `STEER.md`, `PAUSE`, `HUMAN_DIGEST.md`.

The worker's final message is explicitly thrown away: "Your final message is ignored — only the ledger diff counts" (`agents.ts:17`, `agents.ts:58-59`).

### 3.5 How it was actually operated

No real mission was run by the TS orchestrator:

- There are no `notes(wave)` commits in the history.
- There is no `mission.json`, journal, `CHECKPOINT.json`, or `HUMAN_DIGEST.md` anywhere.

The real operating mode is visible in `results/ledgers/knowledge/paper_self/nodes.jsonl` and the `codex-gpt6-max` skill. An interactive Claude Code session writes an XML-block prompt and launches a Codex GPT-6 (effort max) worker in the background with `run_codex_max.sh` (prompt passed on stdin from a file, bounded wait, transcript log). The session then reviews the diff, re-runs the tests in an isolated worktree at HEAD, commits, and appends the `solid` row itself with `CHANDRA_ROLE=validator`. Row notes say so directly: "reviewed, verified in an isolated worktree at HEAD, and adjudicated by the orchestrating session".

---

## 4. Quality control

**Executable admission gate** (`_common/ledgers/admission.py`). This is the strongest part of the repo.

- A row's `verification: {command, timeout_s, cwd}` is **run at append time**. Exit 0 is required. The observed `exit_code`, `duration_s`, `output_sha256`, a 2000-character output tail, and `ran_at` are written onto the row (`admission.py:102-141`).
- The verifier runs **without** the appender's `CHANDRA_ROLE` (`admission.py:123-126`), under an RLIMIT_CPU cap.
- `checked` results and `solid` nodes require "verifiable evidence": an existing file (content-hashed), a git commit that resolves, or a passing command (`admission.py:342-351`, `admission.py:391-398`).
- A `solid` node needs `solid` predecessors.
- `::` dependencies must resolve.
- Bypass flags (`--skip-exec`, `--allow-missing-deps`) are recorded in `admission_flags`.

**Schema-as-code ledgers.**

- Result statuses separate `checked` from `conditional`, `approximate`, `empirical`, `conjectural`, `refuted`, `unchecked`, and `existence_only`.
- There are 12 evidence types (`result_database.py`). The contract says: "Use distinct evidence types instead of scalar confidence".
- Claims, obligations, and assumptions have settling references that must exist: `result_ref`, `discharged_by`, `reduction_obligation`.
- Corrections use `supersedes` with a prior `row_hash` whenever a semantic key changes.

**Tamper evidence.**

- Each row's `row_hash = sha256(prev_hash + canonical(row))` (`ledger_common.py:183-229`).
- Chains are verified before every scheduling decision and terminal exit (`Ledgers.preflight`).
- `CHECKPOINT.json` in each wave commit detects deleted tails and edited legacy prefixes.

**Adversarial validation** (`validator.ts`). The design is careful:

- The candidate is deep-frozen and hashed as canonical JSON.
- Binding checks ensure the row matches the claimed paper, claim, and node.
- Evidence paths are realpath-contained in the repo.
- A fresh pack directory holds only `ALWAYS_PACKED` plus evidence plus `CLAIM.md`.
- `checkIsolation` re-audits the pack hashes after construction, after the refuter, and after the judge.
- The refuter must run first and return non-empty findings.
- The judge sees only the pack and the findings, never the defender's transcript.
- A malformed judge output, or empty `reasons`, fails closed to `reject`.
- **Admit** is executed by the orchestrator as a gated append, which the gate can still refuse (`gate_rejected`).
- **Reject** becomes an open blocking repair obligation.
- The full review is retained as `<node>-w<N>.json`.
- Refuter and judge can be different models, including different vendors through `codex:`.

**Loop controls.**

- `crash-triage`: two failures in the same mode means pivot structurally; three means escalate (alignment §2).
- `check-pivot` blocks "scalar" retries.
- `simplification-status` requires a refactor pass after each new best metric.
- Per-domain failure-mode taxonomies (symbolic, numerical, proof, software) carry a self-extension rule: the first unknown failure is logged as `uncategorized_<domain>`; the second forces a schema edit.

**Circuit breaker** (`gate.ts`). It halts after 8 waves without any rise in solid nodes, verified results, or discharged obligations. The comparison is component-wise. `refuted`, `conjectural`, and `unchecked` results deliberately do not reset the counter, because they would otherwise be "a clean defeat vector".

**Skills as gated tools.** `skill_registry.py validate --exec` executes exactly one `## Verify` block against the candidate's bytes (`${CLAUDE_SKILL_DIR}`) and writes a hash receipt. Unreceipted or modified skills are not discoverable. Workers' skill drafts are harvested through this gate every wave.

**Commit gate.** A hard regex on the commit title; body claim tags `[SOLID|PRELIMINARY|HOLE|FUTURE]` are advisory.

**Hooks.** SessionStart injects the kernel and admission contract (6.7 KB). SubagentStop only prints `<alignment-reminder>Before returning, verify: (1) closed-loop evidence pasted …` (`.claude/settings.json:18`). It is a nudge, not a check.

**Tests.** 297 pytest and 128 node tests were reported after stage 0 (`knowledgebase_redesign.md` §11). These include rejection tests for each ledger rule and an end-to-end toy mission (`orchestrator/test/mission.test.ts`) covering parallel frontier, reject → repair → admit, notes, digest, and completion. In that test, though, the *stub worker* calls `adjudicateCandidate` itself.

**Design-level adversarial review.** `notes/knowledgebase_redesign.md` was refuted in four passes:

1. a fresh-context Claude session,
2. a sealed GPT-6 analysis of the data model,
3. a sealed GPT-6 analysis of the agent-knowledge layer,
4. a GPT-6 refutation of the committed note.

Each defect carries reproduction marks [A][R1][G-D][G-K][R2]. Every defect became a ledger obligation, and stage-0 fixes were landed test-first by GPT-6 workers.

**Stopping criteria:** mission complete (every non-retired node solid), no ready jobs, no-progress streak, `maxWaves`, PAUSE, or integrity failure. There is no stopping criterion tied to research quality or novelty.

---

## 5. Prompts and prompt-engineering patterns

All runtime prompts are short and are built inside the TS code.

**Worker** (`agents.ts:33-67`):

> "You are a stage-2 WORK agent for paper ${paper}, leased the packet: ${chain}. Read first (your context pack — do not roam): alignment.md … pipelines/2-work/spec.md and template.md …
> CONTINUOUS WORK CONTRACT (do not fragment): 1. Work the packet nodes IN ORDER, in this one session … 3. FLUSH at the packet boundary … The appends ARE the deliverable; your final text is ignored — only the ledger diff counts. 4. Every trial (pass or fail) becomes an error-ledger row at flush. 5. Interrupt the packet ONLY for: structural failure needing escalation …"

**Codex preamble** (`agents.ts:13-19`):

> "Land outcomes ONLY through the gated ledger CLIs. Never run git commit. Your final message is ignored — only the ledger diff counts."

**Skill-suggestion contract** (`agents.ts:21-31`): "If you discover a reusable procedure (used twice, or a verifier you had to find), draft it as a skill at $CHANDRA_RUNTIME/… never write into .claude/skills/ yourself".

**Refuter** (`validator.ts:79-90`):

> "You are an adversarial REFUTER. This directory is your ENTIRE context (see MANIFEST.json). … try HARD to refute the claim … Never say "looks good" without listing the specific refutation attempts that failed."

**Judge** (`validator.ts:92-100`):

> "Weigh the refutation against the evidence and the validation gates … Your final message must be EXACTLY one JSON object: {"verdict": "admit"|"reject", "reasons": "<one paragraph>"}."

**Observer compressor** (`observer.ts:78-86`): "Rewrite this research-state note to AT MOST ${maxBytes} bytes … Delete or compress anything restorable from the ledgers … Reply with ONLY the rewritten note."

**Codex operator contract** (`codex-gpt6-max/SKILL.md`): prompts are written as XML blocks `<task>`, `<output_contract>`, `<constraints>` (touch only named paths; never `git add`/`commit`; no attribution), and `<verification_loop>` (the exact command that must exit 0, with its tail pasted). The `kb_redesign/prompts/*.prompt.md` files follow the same shape and add `<method>`, which demands measurements, file:line evidence, three designs across a cost spectrum, and a migration DAG whose verifier commands exit 0 if and only if each node is done.

**Patterns used:**

1. A tiny always-on kernel plus progressive disclosure: "Read on demand (do NOT inline)".
2. Prompts as contracts with explicit deliverables, where the deliverable is a side effect (ledger rows) rather than prose.
3. Fail-closed structured output for the judge.
4. Mandatory negative evidence ("list the refutation attempts that failed").
5. Role assigned through the environment, not the prompt.
6. An instruction to survive compaction plus an external WAL.
7. Human steering as raw text appended to the prompt.
8. Markers vocabulary (`[HYPOTHESIS]`/`[PRELIMINARY]`/`[SOLID]`/`[OPEN]`/`[UNCHECKED]`…) instead of hedging prose.
9. An opt-in "PUA" pressure persona (`notes/pua_skill.md`, 28 KB of corporate-pressure role-play) quarantined behind `disable-model-invocation`. Its three "red lines" (verify before claiming, attribute failures with tools, exhaust methods before giving up) duplicate the kernel.

**What is missing from the prompts:** domain strategy and research heuristics. The worker prompt says nothing about *how* to make progress on a node. It hands the model about 23–63 KB of contracts, specs, templates, and skills to read and trusts it to work out the procedure. Prompt quality is spent on bookkeeping, not on science.

---

## 6. Evidence of how well it worked

**Ledger census.** In every mission, `solid` was established by a commit citation plus a verifier command the proposer wrote.

| Mission | Knowledge rows / latest nodes | Status | Trials (error ledger) | Claims | Result rows |
|---|---|---|---|---|---|
| `self` | 44 / 22 | 22 solid | 23 (20 pass, 2 crash, 1 fail) | 53 entries: 14 discharged, **39 open** (1 blocking) | **0** |
| `vibe` | 29 / 14 | 14 solid | 22 (14 pass, 8 fail: 3 build, 3 spec_drift, 1 test, 1 uncategorized) | none | **0** |
| `move47` | 38 / 18 | 14 solid, 4 hypothesis ([FUTURE]) | **0 logged** | none | **0** |

Key observations:

- **The result ledger, the "accepted result" at the centre of the admission contract, was never used.** `results/ledgers/result/` does not exist. Evidence-type semantics, `checked` versus `conditional`, and claim settlement were never exercised on real work. Every mission measured progress as knowledge nodes going `hypothesis → solid`.
- **No mission was a paper reproduction.** All three are software or engineering missions using the `software` domain. The symbolic, numerical, and proof machinery (failure taxonomies, decomposition artifacts, the write stage, the REVTeX scaffold) shows no use.
- **Discipline slipped in the largest mission.** move47 logged no error trials even though real failures happened. The mcts-llm smoke chose a root move with no model evaluation, and that failure was recorded only in node `notes` and a new `mcts-calib` node. This breaks kernel §1 ("Every trial lands in the error ledger").
- **Commit-citation evidence is brittle.** move47 needed "re-anchor" rows after a message-only history rewrite changed the hashes ("Re-anchors seq 2 … fc556c1 -> cc6d22e").
- **"solid" conflates "experiment executed" with "hypothesis supported".**
  - `move47::mcts-strength` went solid on evidence that the full system went **1–5** (pooled Elo 1801 [1466, 2136] against ladders rated 1850/2064), at a cost of $873.55 for 122 decisions.
  - `move47::mcts-game` went solid on a **loss** to k1-full at $34.98 per move.
  - The reporting itself is honest and thorough (`results/move47/paper_move47/evidence/*/report.md`, cross-run judge reproduction in `judge-ply22/README.md`). But nothing in the status model records that the scientific aim, "let real KataGo play our harness" and compete, failed.
- **The self-optimisation mission produced more open problems than it closed.** 22 nodes are solid, but 39 obligations stay open. 27 of them are `drift-*`: places where the prose (spec, contract, skill, README) claims behaviour the code does not have. They surfaced when GPT-6 workers wrote the skills. The knowledge-base redesign lists 30 problems (P1–P30) and an 18-node migration DAG. Stage 0 (4 nodes) and three stage-1 nodes landed. `kb::frontier-ownership-repair` and most of stage 1–3 are still open, including downstream invalidation, proposer-defined verifiability, settlement compatibility, evidence lifecycle, and progress attribution.
- **Errors found by the trial ledger were real and specific.** Examples:
  - a self-referential Verify block recursed 80+ processes;
  - `codex exec` blocked on an open stdin;
  - the verifier inherited `CHANDRA_ROLE` and so admitted a row that should have been rejected;
  - vibe's packaging globs did not cross dot-directories.

  These rows have useful expected/observed/root_cause/fix_hypothesis fields. The trial ledger is a good post-mortem store.
- **move47's heuristic-learning loop is the best example of "model proposes, code verifies".** Per game, 40–51 model-proposed rules led to 3–5 accepted, each with a held-out gain (e.g. "R19 … held-out gain 0.0048"). Acceptance was gated by a regression set and versioned with provenance (`mcts-strength-20261007/report.md`).

---

## 7. Weaknesses: why "not good enough yet"

The core critique: **the gates are strong where they check bookkeeping and weak where they check truth.** Specifically:

1. **Adversarial validation is not in the production loop.**
   - `main.ts` never calls `adjudicateCandidate`.
   - The `adversarial-validate` skill's Verify block *asserts* this: `assert 'adjudicateCandidate' not in Path('orchestrator/src/main.ts').read_text()` (`.claude/skills/adversarial-validate/SKILL.md:176`).
   - The worker prompt never tells the worker to validate.
   - Yet `pipelines/2-work/spec.md` and the README present "process-isolated adversarial validation … must attempt rejection before a judge may admit" as the mechanism (open obligation `drift-validator-not-wired`).
   - In production a worker can promote its own node to `solid`. The only separation between generator and judge appears in tests, where the stub worker calls the validator (`orchestrator/test/mission.test.ts:110`).

2. **Verifiability is defined by the proposer.**
   - The worker writes the code, writes the test, and picks `verification.command`.
   - The gate runs that string with `shell=True` in the same working tree (`admission.py:128`).
   - A resolvable commit hash alone satisfies "verifiable evidence" for `solid` (`admission.py:346`, `admission.py:393`).
   - The design's own diagnosis: "a proposer-chosen `verification.command` plus free text yields `solid`" (P13; obligation `kb-proposer-defined-verifiability`).
   - "Exit 0" proves the command ran. It does not prove the claim. Nothing checks that the command actually tests the claim.

3. **Delegation is an honour system.**
   - `CHANDRA_ROLE` is an environment variable (P5: "`actor_role` is an environment variable").
   - The orchestrating session routinely set `CHANDRA_ROLE=validator` and appended promotions itself: 16 `self` rows say "adjudicated by the orchestrating session as validator".
   - This is what kernel §6 forbids, and it contradicts `ledger-result-admit/SKILL.md` ("Do not impersonate a worker from an orchestration session").

4. **Validator isolation is cwd plus prompt.**
   - The SDK refuter has `Bash` and no filesystem sandbox (`validator.ts:375`).
   - Codex's bwrap fails on this host, so the documented workaround is `danger-full-access`. The README admits: "Validator isolation then rests on cwd + prompt only."
   - By default refuter and judge are the same model family as the worker.
   - The refuter's output is unstructured prose, with no counterexample or minimal-repro format. The judge's "reasons" field is free text.

5. **The scheduler's semantics are wrong in ways the spec hides.**
   - `readyFrontier` ignores open obligations (`dag.ts:67-75`), so a rejected node with a blocking repair stays ready.
   - Interior packet nodes bypass readiness checks (`scheduler.ts:45-51`).
   - Cross-paper predecessors are never ready (P1).
   - There is no downstream invalidation: demoting a support leaves its dependents solid (P12).
   - A `decompose` job counts as "admitted" when the number of knowledge rows grew (`jobs.ts:131`), whatever the quality of the decomposition.
   - Outcomes are count deltas, not events (P19).

6. **The prose surface is bigger than the code and drifts from it.**
   - 33 skills total 242 KB; the median skill is 7.7 KB.
   - Authored prose is 368 KB against 315 KB of code that owns the facts (G-K measurement).
   - A one-node worker with three work skills carries 50–63 KB of instructions.
   - Each skill is full of caveats about code limits, such as "The current mission entrypoint does not call it automatically" and "Declared but not forwarded by the CLI". In effect the skills record bugs.
   - The same rule (role policy, ledger layout, packet contract) is restated in 9 to 30 files.
   - `INDEX.md` and `AGENTS.md` each claim authority.

7. **Half-wired configuration and signals.**
   - `noProgressLimit` and `maxWallSeconds` are declared in `missionspec.ts`, but `main.ts:303` passes `DEFAULT_BUDGETS`.
   - STEER does not reach decompose, acquire, or write jobs.
   - `--dry-run` falls back to real SDK job runners and still commits (`main.ts:215-217`).
   - `commitWave` does `git add -A` (`gitops.ts:73`).
   - Codex runners hard-code `windowsUsed: 1` (`agents.ts:123`, `agents.ts:232`), so packet sizing and digest cadence do nothing on that backend.

8. **Memory is mechanical and not durable.**
   - The observer writes counts tables and prunes `RESEARCH_STATE.md` to 10 KB. It never synthesizes understanding.
   - The prune originally cut generated blocks and text that had never been committed (P28; partly fixed).
   - `progress/` is gitignored, so notes are lost.
   - The journal, WALs, validation reviews, and transcripts live in `/tmp`, so a mission cannot be reproduced or audited after a reboot.

9. **The workflow suits paper reproduction, not discovery.**
   - The work unit is a node in a DAG decomposed from a *known* paper.
   - "Done" means "every node solid".
   - There is no search space, candidate population, objective, novelty test, exploration budget, or way to say "this branch is dead, try another".
   - Acquisition means "fetch the missing paper". For zero-knowledge discovery that is exactly the leakage path to block.

10. **Ceremony crowds out science.**
    - The repo has a 15-type commit grammar with 9 body-object kinds and trailers, promise tags, six marker kinds, a three-note hierarchy, a 16-section task template, and a PUA persona.
    - Most of it is advisory, or enforced only on syntax such as the commit-title regex.
    - Much of the history is the system working on itself: skills for its own skills, a VS Code fork to view its DAG, a dashboard, refuting its own knowledge-base design.
    - Measurable scientific output (move47) is small next to the infrastructure.

11. **Evidence has no lifecycle.**
    - Artifact hashes are taken once at append time.
    - An artifact that is later modified or deleted still leaves `checked`/`solid` standing (P25).
    - Settlement checks that a reference exists, not that it is compatible: an `exact_proof` claim can be admitted on a `conjectural`/`fail` result (P20).

---

## 8. Reusable patterns and anti-patterns for the QEC workflow

### Carry over (with concrete mechanisms)

1. **Ledger-as-truth, rendered views.** Use append-only JSONL with `row_hash` chaining (`ledger_common.py:183-266`) and a schema module whose validation errors quote the allowed enums back (`knowledge_database.py` docstring: "the agent self-corrects from the error message"). Use `supersedes` with a prior `row_hash` for corrections, and add a git-committed checkpoint of ledger heads (`checkpoint.ts`). For QEC, candidate codes, evaluations, and trials map naturally onto this.
2. **Executable admission at append, with the observation stored on the row** (exit code, output hash, output tail, duration, ran_at) and with the verifier's environment scrubbed (`admission.py:102-141`). In QEC the verifier can be fully mechanical: stabilizer commutation, rank and k, exact or bounded distance, logical-operator independence, and decoder-threshold simulation with confidence intervals. **The verifier must be registered and owned by the orchestrator, not written by the worker.** That fixes Chandra's main hole (§7.2).
3. **Outcome = state diff, never agent prose** ("Your final message is ignored — only the ledger diff counts"). Diff *events tagged with the task id*, not counts.
4. **Refuter first, then judge, with fail-closed parsing.** Keep these parts of `validator.ts`: frozen and hashed candidate submission, binding checks, a manifest-audited context pack re-checked after every phase, mandatory non-empty findings, judge JSON that rejects when unparseable, a retained review file, and cross-model role selection. **Wire it into the main loop** and enforce isolation with a real sandbox.
5. **Firewall the oracle (move47).** `move47/bin/worker-sandbox` uses bwrap to mount the root read-only, mask the workspace, `$HOME`, `/tmp`, engines, judge, `ref-code`, `progress`, and run dirs, pass an allowlisted environment, disable CLAUDE.md and auto-memory, and run a canary test that "the session cannot list engines/". For a zero-knowledge QEC workflow, the same mechanism should hide known-code tables (codetables.de dumps, literature), the distance oracle's internals, and held-out evaluation sets from generator agents.
6. **Model proposes, held-out regression accepts (move47 heuristic learning).** Proposals are kept only on held-out gain, each fixed case becomes a regression test, and rules live in a versioned book with provenance (`docs/prompts/hl-heuristics.md`: "Keep the change only if top5 or value_corr improves on positions you did NOT look at"). This is the right shape for learned code-construction heuristics.
7. **Circuit breaker on verified progress only** (`gate.ts`): component-wise, and failing or refuted outcomes cannot reset it. Add **no-tweak triage**: two failures in the same mode means pivot, three means escalate, using stable attempt ids rather than task labels (P27).
8. **Packets plus WAL for long sessions.** Lease a coherent chain of work to one session, keep an ungated scratch WAL to survive compaction, and flush at the boundary. Also use adaptive work quanta driven by context-window telemetry.
9. **A small always-on kernel** (≤2.5 KB, injected by SessionStart) with progressive disclosure, and a rule that "rules whose enforcement is mechanical belong in code, with the kernel only pointing at them".
10. **Transaction protocol for concurrent writers** (`txn.py`): run slow verification outside the lock, re-validate the read set inside it, append and fsync, regenerate derived views, release.
11. **Codex worker launcher** (`run_codex_max.sh`): prompt on stdin from a file (never an open pipe), background process plus bounded wait, transcript and final memo files, and an XML-block operator contract with a `<verification_loop>`.
12. **Multi-reviewer design refutation** with sealed independent analyses and reproduction marks (`notes/kb_redesign/`). Use it for the design of the new workflow itself.
13. **Typed exit codes and integrity preflight** before every decision and every terminal exit (`main.ts:116-123`, `main.ts:170-187`).
14. **Verification from a clean extract of a commit.** move47 verifiers run `git archive <sha> | tar -x` into `mktemp` and rerun the tests there, so uncommitted worktree state cannot leak into evidence.

### Avoid

- A validator that exists in tests and docs but not in the production path. The QEC workflow should have a test that fails if the main loop can admit without independent verification.
- Verifiers authored by the proposer, and "a commit exists" counted as evidence.
- Role or provenance enforcement through environment variables the orchestrator can set itself. Use separate OS users, containers, or sandboxed processes with credentials only the verifier holds.
- One status (`solid`) that merges "the experiment ran" with "the hypothesis holds". Keep *measurement admitted* separate from *claim supported*, and record negative results as first-class outcomes, e.g. "construction X does not beat [[n,k,d]] bound".
- Large prose skills that restate code, and specs that state intended behaviour as if implemented. Prefer one owner per fact, generated reference blocks, and behaviour tests (§G6 of the redesign).
- Generic bookkeeping prompts with no domain strategy. For QEC the generator prompt should carry the construction families, search operators, and the objective. It should not carry 50 KB of process contracts.
- A static paper-decomposition DAG as the unit of work for discovery. Use a candidate pool, search, and evaluation instead, with explicit exploration budgets and kill criteria.
- Memory that lives in `/tmp` or gitignored paths, and notes that are only pruned, never synthesized.
- Readiness or completion rules that ignore open obligations or blocking repairs.
- Ceremony with no enforcement: elaborate commit grammars, markers, and personas.
- Letting the system's self-improvement and tooling (dashboards, editor forks) absorb most of the effort.

---

## 9. Key files

| File | One-line description |
|---|---|
| `README.md` | Pitch, architecture claims, launch commands. Overstates what is wired (validator). |
| `alignment.md` | 2.4 KB always-on kernel §0–§6 (verifier admits, no tweak loops, stall→acquire, delegation-only). |
| `INDEX.md` | Canonical index and workflow contract (four job kinds under one scheduler). |
| `AGENTS.md` | Read-first order and binding rules for Codex / SDK agents. |
| `.delegation-policy` | `strict`; makes the gate reject roleless appends. |
| `.claude/settings.json`, `.claude/inject_infra.sh` | SessionStart kernel injection; SubagentStop reminder text. |
| `_common/ledgers/admission.py` | Executable admission gate: run verifier, hash evidence, resolve deps, solid-on-solid, role policy. |
| `_common/ledgers/ledger_common.py` | JSONL I/O, hash chain, torn-tail recovery, latest-per-node collapse. |
| `_common/ledgers/txn.py` | Repository flock and the verify-outside/commit-inside protocol. |
| `_common/ledgers/{result,knowledge,claims,error}_database.py` | Schema-as-code ledgers, status and evidence-type enums, failure-mode taxonomies. |
| `_common/loop/loop_policy.py` | crash-triage, check-pivot, simplification status, paper-refresh cadence. |
| `_common/contracts/research_admission_contract.md` | What an admitted result is; the gates in prose. |
| `pipelines/2-work/spec.md` | Stage-2 contract: node → evidence → validation → admitted row; the seven validation gates. |
| `orchestrator/src/main.ts` | Wave loop: preflight, jobs, parallel packets, observer, harvest, gate, chains, digest, commit. |
| `orchestrator/src/agents.ts` | Worker and job prompts; SDK and Codex runners; `CHANDRA_ROLE` injection. |
| `orchestrator/src/validator.ts` | Refuter→judge adjudication with frozen candidate, context pack, isolation audit. Not called by `main.ts`. |
| `orchestrator/src/scheduler.ts`, `dag.ts`, `jobs.ts` | Ready frontier, packet leasing, ledger-diff outcome attribution. |
| `orchestrator/src/observer.ts`, `digest.ts`, `gate.ts`, `gitops.ts`, `checkpoint.ts` | Notes and 10 KB cap; human digest; no-progress breaker; wave commit; ledger-head checkpoint. |
| `.claude/skills/adversarial-validate/SKILL.md` | How to call the validator manually; Verify asserts it is not wired. |
| `.claude/skills/codex-gpt6-max/{SKILL.md,run_codex_max.sh}` | The delegation path actually used: background Codex workers with an XML contract. |
| `notes/knowledgebase_redesign.md` | 30-problem self-diagnosis, refuted four times; the best single source on the system's flaws. |
| `results/ledgers/*/paper_{self,vibe,move47}/` | Real mission ledgers. No result ledger anywhere. |
| `move47/bin/worker-sandbox` | bwrap firewall that hides the oracle, judge, and references from LLM workers. |
| `move47/docs/prompts/hl-heuristics.md` | Model-proposes / held-out-accepts heuristic-learning prompt. |
| `results/move47/paper_move47/evidence/*/report.md` | Honest experimental reports. The full system lost (1–5; one game lost to k1-full). |
