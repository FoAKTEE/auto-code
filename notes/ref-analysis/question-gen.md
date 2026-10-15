# Reference analysis: `ref-code/question-gen` (the problem-generation workflow)

Studied read-only at `/data/haiyangw/claude/auto-code/ref-code/question-gen` (git HEAD `e2ce50c`, 56 commits, 2026-07-10 → 2026-08-20; ~36k lines across 225 tracked files). To check the unit suite I ran it on a copy in my scratchpad, not in the repo: **225 tests, all pass (1 skipped), 4.8 s, network-free**. Citations below use `path:line` relative to the repo root.

---

## 0. TL;DR

`question-gen` is an **LLM-agent pipeline that writes open research questions** in a chosen subfield (probability phase transitions, ML-proxy statistical inference, online algorithms). It does not produce benchmark problems with answers you can check. What it outputs is a ranked set of open problems that are meant to be novel, valid, and worth working on, and a human expert reviews the top of that list. It runs on the OpenAI stack: `codex exec` agent sessions plus the Responses API, all with `gpt-5.6-sol`. The pipeline has four stages: a frozen literature "research pack"; a proposer session with literature and solve subagents; a multi-round selection funnel (filter → blinded taste ranking → solve/verify → development → unanimous pairwise gate → fresh validity audit); and a human gold set.

The code is carefully engineered around **information firewalls, resumable fingerprinted jobs, and blinded multi-sample judging**. On the other hand, it has **no mechanical oracle**. Every quality gate is an LLM judgment. The hard filter almost never kills anything. Selection comes mostly from fixed-fraction taste cuts, and that taste prompt was calibrated against two expert-confirmed items. For a QEC workflow, the orchestration machinery is worth copying. The epistemics are not: QEC has real verifiers (rank, commutation, distance computation, simulation), and they should carry the load that LLM taste carries here.

---

## 1. Purpose and quality bar

**What it generates.** It generates candidate *open research questions*, each in a fixed seven-field format: `title, background, problem_statement, intuition_and_implications, related_work_and_gap, why_interesting, heuristic_and_proof_routes` (`data/prompt_families/math/question_schema.json`; format prose at `src/question_gen/pipelines/question_gen.py:285-331`). Example outputs are in `data/expert_reviewed/probabilistic_phase_transitions.json`, e.g. "Sparse Shortcuts on Critical Two-Dimensional Percolation". That item is a conjectured three-regime crossover with exponents 5/86 and 5/91 and a "prove or disprove" target.

**For whom.** The audience is research mathematicians and statisticians. The README frames the end product as a subfield "gold set" that a human expert has confirmed (`README.md:13-14`). The project also intends these as ICL exemplars and as an evaluation track record (`data/expert_reviewed/README.md`).

**Quality bar** (`data/prompt_families/math/proposer_standards.md`):
- *Substantively novel*, as a hard requirement: "A known result transported to another object class … is **not** admissible."
- *Bold but natural*, *conceptually focused* ("a small number of essential objects support one clear central tension"), *well-posed*, *motivated*, *consequential*.
- *Statement economy*, also a hard requirement, and *TeX notation*, a repo-wide hard requirement (`CLAUDE.md`).
- Anti-patterns: vacuous agendas, mechanical generalisation, "questions whose contribution type is fully predictable from the statement (known technique × known setting with a known-shaped answer)", and decorative interdisciplinarity.

**Difficulty bar.** Questions must be *not already solved* (K2) and *not readily solvable* (KS: a probe solved it and an independent check confirmed the solution). The taste stage, however, is explicitly told "Difficulty is not a negative signal" (`data/prompt_families/math/taste.md`). Answers are not checkable, because the targets are open problems. The only checkable artefacts are claimed complete solutions, and those are used to *remove* questions as too easy, not to grade answers.

**Leakage.** Leakage in the benchmark sense (answer contamination) does not apply here. The analogous concern is **prior-art leakage**: re-proposing something already solved or already posed. It is handled by literature probes, pack inventories, and a negative-conditioning ledger.

---

## 2. Architecture

### 2.1 Components

| Layer | Implementation |
|---|---|
| Literature snapshot ("research pack") | Agentic skill `.claude/skills/research-pack/SKILL.md`. Steps: scout → partition (human gate) → parallel two-pass area search → merge + live-arXiv spot-check → adversarial completeness audit → synthesis → seal. Output goes to `data/contexts/<slug>/vN_<date>/{pack.json, proposer_context.md, manifest.json, trace/}`. Packs are local-only and not in the repo. |
| Domain contract | `data/subfields/<slug>.md`: free-form spec plus optional `## Domain invariants`, `## Domain-specific standards`, `## Investigation guidance`, and `## Generation domain tags`. Parsed by `src/question_gen/subfields.py`. |
| Prompt families | `data/prompt_families/{math,general,statistics,tcs}/`. A `family.json` maps orchestration roles to prompt files and workflow vocabulary (`solve` vs `investigation`, verdict enums). Loaded by `src/question_gen/prompt_families.py`. |
| Versioned critic guidelines | `data/guidelines/v0…v7*.md` plus `CHANGELOG.md`, fingerprinted by `src/question_gen/critic/guidelines.py:68`. |
| Proposer | `src/question_gen/pipelines/question_gen.py`: one `codex exec` lead session that spawns subagents. |
| Oversampler | `src/question_gen/pipelines/question_oversample.py`: runs proposer batches in waves of 3, with a domain-coverage scheduler. |
| Multi-round selector | `src/question_gen/pipelines/question_rounds.py` (4.6k lines) plus `question_agents.py` (per-role workspaces and schemas). |
| Job runner | `src/question_gen/codex_jobs.py`: fingerprinted, resumable, schema-validated `codex exec` jobs. |
| Taste machinery | `src/question_gen/critic/taste_rank.py` (blinding, API sampler) and `critic/taste_groups.py` (balanced groups, normalised percentiles, fixed-fraction cut). |
| Ledger | `.claude/skills/research-pack/scripts/reconcile_ledger.py`: derives `ledger.json` and `questions_seen.json` from `run_config.json` provenance. |
| Rendering | `render_question_gen_report.py`, `render_question_pdf.py`, `render_question_tex.py`, `tools/html_to_pdf_playwright.js`. |
| Agent-CLI skills | `.claude/skills/*` mirrored in `.agents/skills/*` for Codex (`question-gen`, `question-pipeline`, `question-refine`, `question-validity`, `research-pack`, `style-pass`, `question-pdf`; plus a generated `research-critic`, Codex-only). There are no Claude Code hooks or settings. `.claude/commands/critic.md` is the only slash command. |

### 2.2 Pipeline (large-scale path)

```
research-pack (Claude-built, frozen, fingerprinted)
   │
question_oversample: waves of 3 proposer batches × 10 questions; reconcile ledger between waves
   │   each batch = ONE codex lead session:
   │     [P0 scenario map → N scouts → fresh judge → approved cards]   (statistics family)
   │     P1 draft N questions (pack inventories hidden)
   │     P1b collision check vs inventories + bounded prior-question search
   │     P2 one literature-check subagent per draft
   │     P3 ≤1 replacement round for verified kills
   │     P4 one solve subagent per survivor → lead repairs fixable_flaw / drops ill_posed|trivial
   │     P5 final JSON (schema-constrained)
   │   script post-processing → questions_all / questions_for_critic / audit_table / repairs
   ▼
question_rounds (resumable):
  Round 0: merge → hard filter (groups ≤20; lit + optional resolution probe; K1/K2/KS)
           → blinded taste (3 judgments/question) → keep floor(0.5·N)
  Round r ≥ 1, per question, streaming with no barrier:
       [r=1: continuation + fresh solve → independent verify]
       → develop (lead + 8 identical scouts, ≤30 min) → pairwise gate (3 blinded Codex judges, must win 3/3)
       → post-develop solve + verify → polish (resume developer) → fresh validity audit (valid/repaired/fatal)
     → delta literature filter (K2 only) for central-idea changes → pool taste cut (barrier)
  Final: ranking → final_ranking.{json,md} + questions.html/pdf
   ▼
human expert review → data/expert_reviewed/<slug>.json
```

### 2.3 State, launch, resume, persistence

- **Everything is files.** Each run has `artifacts/runs/<stamp>_<task>_<slug>/` containing `run_config.json`, which records the fingerprints of the subfield spec, standards, context, pack, guideline, and prompt family (`question_gen.py:1902-2012`). It also holds `command.txt`, `events.jsonl`, `final.json`, derived JSONs, and a `workspace/` with `AGENTS.md`, drafts, reports, and scratch.
- **Resume = rerun the same command.** `question_rounds` stores a semantic-config fingerprint and refuses a mismatched `--run-dir` (`question_rounds.py:3936-3952`). Every stage writes `*_result.json` plus a candidate list and short-circuits when both exist (e.g. `:608-611`, `:1203-1206`). Every Codex call is a `CodexJobSpec` whose fingerprint covers prompt, schema, model, effort, sandbox, timeouts, and caller-supplied semantic inputs (`codex_jobs.py:136-159`). A stale job dir raises an error and is never silently reused (`:564-568`). Thread IDs are recovered from the JSONL event log, so a retry *resumes the same Codex thread* (`:464-477`, `:595`). Writes are atomic with fsync (`:162-184`).
- **Oversampler** freezes resume-sensitive settings and input fingerprints in `oversample_config.json` and refuses to mix configurations (`README.md:260-264`).
- **Live status**: `RUN_STATUS.json` holds the global stage, per-question stage counts, and the last 50 transitions (`question_rounds.py:245-329`). Full histories live in `roundNN/question_streams/<key>/`.
- **Cross-run memory**: the ledger links pack versions to the proposer and critic runs that consumed them. A consumed pack version is frozen, and `add`/`refresh` then performs copy-on-write to vN+1. `questions_seen.json` records each question's status (`generated|survived|killed|self_check_killed|solve_killed`) and is used for negative conditioning. `question_conditioning_exclusions/<slug>.json` removes bad development runs from that conditioning while keeping them on disk for audit.
- **Launch**: plain Python CLIs under `scripts/` (`scripts/_run_module.py` wrappers), or agent skills that ask a few launch questions, narrate progress, and pause at human gates (pack partition, new-domain confirmation, and next step after a batch).

---

## 3. Agent and LLM roles, orchestration, contracts

**Providers and models.** OpenAI only. `codex exec` (CLI agent with shell, web search, and sandbox) runs the proposer, filter, solver, verifier, developer, validity auditor, and pairwise judge. The Responses API (`gpt-5.6-sol`, reasoning `xhigh`, or `max` plus `mode: pro` for standalone taste; `common.py`, `taste_rank.py:44-53`) runs pool taste and falls back to Codex when it fails. **The research pack is deliberately built by Claude ("Fable") so the pack's blind spots do not correlate with the GPT-family proposer and critic** (`data/contexts/README.md`, "Builder" section). No other cross-family check exists, although the design doc recommends a cross-model judging panel (`docs/codex_critic_design.md` §1.3, §2.3).

| Role | Where | Sees | Must not see | Output contract |
|---|---|---|---|---|
| Proposer lead | `question_gen.py:513-1166` | standards, spec, narrative context, pack index (inventories removed) | inventories until drafts exist; full prior archive (bounded mode) | `final_schema.json` (`:393-510`): items with `literature_check`, `solve_probe`, `revised_statement` |
| Literature-check subagent | Phase 2 | one draft, pack | other drafts | `CHECKER_REPORT_FIELDS` (`:333-344`), verdict ∈ {high_similarity_match, posed_as_open_problem, nearby_but_distinct, nothing_nearby} |
| Proposer solve subagent | Phase 4 | one draft | — | health ∈ {well_posed_promising, fixable_flaw, ill_posed, trivial_or_solved} |
| Filter lead + probe subagents | `codex_critic.py:548-731`, run by `question_rounds.py:598-815` | ≤20 questions, context, pack | taste instructions | `FILTER_V2_FINAL_SCHEMA` (`codex_critic.py:335-417`), plus a disjoint-cover validator (`question_rounds.py:558-582`) |
| Taste judge | `taste_rank.run_sample` / `_run_codex_taste_sample` (`question_rounds.py:910-1026`) | blinded cards (opaque `prob_<hash>` IDs, fresh shuffle per sample) + scope-only literature card | web, probes, verdicts, other runs | `{ranked_ids, top_ids}` with permutation and prefix validation (`taste_rank.py:173-213`) |
| Solver (continuation + fresh) | `question_rounds.py:2743-2970` | question; continuation solver also gets trajectory | — | `SOLVE_SCHEMA` (`question_agents.py:104-132`): factual summary, `question_issues`, optional `solution_claim{file}` |
| Verifier | `:2973-3164` | question + claimed solution file copied into its own workspace | solver reasoning or scratch (by workspace design) | `{verified_complete_solution, verification_summary, fatal_errors}`; validator rejects true together with fatal errors (`:1862-1869`) |
| Developer lead + 8 scouts | `:1872-2144`, contract `math/developer_contract.md` | question, factual solve evidence, factual literature record | prior versions, critic feedback, ranks, other candidates | `REFINE_SCHEMA` keep/revise; `addressed_issue_ids` must be empty (`:1843-1847`) |
| Pairwise gate | `:2613-2718` | both full seven-field versions, blinded | web | developed version is accepted only if it wins 3/3 |
| Validity auditor | `:2367-2556`, `question-validity` skill | selected question + version-scoped solve evidence + domain invariants | development rationale, scout memos, history | `VALIDITY_SCHEMA`: valid/repaired/fatal, with a strict status/field consistency validator (`:2339-2364`) |

**Parallelism.** Inside a Codex session, subagents run via `collaboration.spawn_agent` with `agents.max_threads`. Across sessions, asyncio semaphores apply: `question_concurrency=16`, `filter_session_concurrency=2`, `development_session_concurrency=4`, and so on (`question_rounds.py:127-173`). Round development is a **per-question stream with no stage barrier** (`:3167-3443`). Only pool taste is a barrier.

**How generator and checker are kept independent.**
1. **Separate fresh processes/threads** for the solver, verifier, validity auditor, and taste judges (`resume_thread_id=None`). The developer always gets a fresh thread "so critic feedback or version history … cannot leak" (`:2013-2015`).
2. **Evidence-only transport.** `build_solve_evidence` passes factual attempt reports and artefact contents and lists what it deliberately omits ("prior question versions", "critic feedback", "prior refiner decisions", "pool rank") (`question_agents.py:355-439`). `build_literature_evidence` passes opened sources and drops verdicts, similarity labels, and confidence (`:442-488`). When proposer runs are imported into rounds, **sentences carrying health or progress judgments are stripped by regex** (`question_rounds_input.py:55-66, 167-175`).
3. **The scope card for taste is sanitised.** It is 35–60 words naming at most two opened sources and what each covers, with no "absence claim, novelty judgment, similarity/verdict label…" (`codex_critic.py:164-171`). A deny-list regex discards unsafe cards (`taste_rank.py:56-87, 369-386`).
4. **Taste blinding.** Each sample gets a fresh shuffle and fresh salted opaque IDs (`taste_rank.py:122-132`).
5. **Tests enforce the firewall**: e.g. `test_refiner_gets_solve_evidence_without_history_or_critic`, `test_validity_workspace_contains_candidate_and_factual_evidence_only`, `test_agent_web_search_switch_reaches_solve_and_never_taste` (`tests/test_question_agents.py`, `tests/test_question_round_backends.py`).

**Where independence is weak.** Inside the proposer session the **lead writes the questions, reads its own subagents' reports, verifies kills, applies repairs, and copies verdicts into `final.json`**. The script trusts `literature_check` and `solve_probe` from `final.json` (`question_gen.py:1489-1506`) and never cross-checks them against `reports/*.json`. The prompt asks the lead to copy "faithfully" and says "Never fabricate a report" (`:950-953`), but that is honour-system enforcement. Workspace isolation is also mostly **prompt-enforced**: AGENTS.md says "Do not inspect parent or sibling directories" (`question_rounds.py:935-949`; `math/dossier_workspace.md`). Codex `workspace-write` sandboxes restrict writes, not reads, and verifier and taste workspaces sit next to solver workspaces under `run_dir/questions/<key>/`.

---

## 4. Verification and quality gates

| Gate | Mechanism | Kind | Strength |
|---|---|---|---|
| Pack integrity | A paper enters only if its abstract page was fetched; random live-arXiv title spot-check (`research-pack/scripts/spot_check.py`); adversarial "find what we MISSED" auditors | LLM + small deterministic check | Good hygiene; recall is still partial |
| Proposer literature self-check | Per-draft subagent; vocabulary-translation queries; deep-read of the 2–3 closest PDFs; lead re-verifies kill citations; ≤1 replacement | LLM | Kills few. Per the README, ~141/150 survive |
| `in_pack` / pack-miss diagnosis | Computed by script against `pack.json`, "never trusted from the model" (`question_gen.py:1306-1331, 1567-1606`) | Deterministic | Feeds `audit_table.json` → pack `add` |
| Proposer solve probe | "Adversarial referee whose job is to BREAK the question"; lead repairs `fixable_flaw` | LLM, generator-side | The highest-value step in practice (§6) |
| TeX/notation | Count of unbalanced `$`, no TeX in titles (`question_gen.py:1352-1372`) | Deterministic, **warn-only**, math family only (`:2059`) | Weak |
| Hard filter (Round 0) | K1 no viable core / K2 no new contribution (basis enum) / KS resolved and independently checked; lead must open the decisive source for K2 (`codex_critic.py:687-700`) | LLM | Near-zero kill rate (§6). A group that fails twice is **forced to pass** (`question_rounds.py:739-751`) |
| Dedup | Lexical BM25 over prior questions in SQLite, queried by the proposer after drafting (`prior_question_search.py`); K3 "batch duplicate" only within one filter group of ≤20 | Deterministic retrieval, LLM decision | **No global dedup** across the 141-question pool |
| Difficulty | One or two 20–30 min solve attempts per question per round; claimed full solutions go to an independent adversarial verifier | LLM | **Kill-on-verified-solution is OFF by default** (`question_rounds.py:141, 4350-4359`) |
| Pool taste | 3 blinded judgments per question. Pools ≤20 get the complete group judged 3×; larger pools get 3 distinct opponent partitions. Ranks normalised to percentiles; mean, then worst as tiebreak; keep `floor(f·N)` (`taste_groups.py`) | LLM | Fail-closed: <2 successful judgments halts the run (`question_rounds.py:1410-1427`) |
| Development acceptance | Developed version must win **3/3** blinded pairwise judgments, otherwise the original is kept (`:2672-2693`) | LLM | Conservative by design |
| Validity | Fresh auditor; defects require "a concrete derivation, counterexample, computation, or unambiguous logical inconsistency"; claims treated as known must be cited, re-derived *in the question*, or demoted; fatal removes the question; an audit failure also removes it (`:2507-2526`) | LLM | Fail-closed |
| Delta literature filter | Re-check only questions whose central idea changed, K2 only, `probe_minutes=1` (`:4054-4062`) | LLM | Shallow |
| Style rewrite fidelity | Deterministic fail-closed guard: math spans, numbers, citations, author-year, epistemic markers (conjecture/heuristic/open), and scope markers (negation, quantifiers, bound directions) must be preserved (`style_pass.py:172-226`) | Deterministic | Good pattern |
| Schema and semantic validation | Local JSON-Schema subset validator plus per-role semantic validators run before a result is cached (`codex_jobs.py:264-352, 480-493`) | Deterministic | Good |
| Unit tests | 225 network-free tests: contracts, firewalls, resume, defaults, prompt-family parity, skill-mirror parity | Deterministic | Tests check plumbing, not output quality |

There is **no numeric or symbolic verification by the pipeline itself**. Agents may run code in scratch, but nothing deterministic checks a question's mathematical claims. Difficulty is never calibrated against a solve rate across multiple models or attempts.

---

## 5. Prompts: key passages and patterns

**Key excerpts**

- Literature probe, vocabulary translation (`codex_critic.py:436-444`): *"rewrite the question using NONE of its own method/technique phrases, describing instead the setting and the phenomenon in the words an adjacent community would use … Question authors search in their own vocabulary; your job is to find what they could not."* It also says *"`nothing_nearby`: … Do NOT auto-reward this as novelty; flag for extra validity and naturalness scrutiny."* (`:467-468`). Probes must read the intro and future-work sections of the closest PDFs, because *"generated problems are often already posed there even when no title or abstract matches."*
- Integrity rules (`codex_critic.py:497-507`): *"A citation may be reported only if you actually opened the source … Numerical claims must correspond to code and outputs actually present in your scratch directory … Judge the problem statement, not its sales pitch."*
- Subagent honesty (`question_gen.py:665-675`): *"never invent, simulate, or silently replace a required independent report. If a spawn call fails, retry it once; if it still fails, report the workflow failure honestly instead of proceeding past that gate."* Also (`:1161-1164`): *"a killed question reported honestly improves the research pack; a killed question hidden poisons the pipeline downstream."*
- Proposer solve check (`math/proposer_solve_check.md`): *"as an adversarial referee whose job is to BREAK the question, not defend it … A stated exponent your own back-of-envelope contradicts is a flaw, not a detail."*
- Round solver (`math/solver.md`): *"do not use evaluative labels such as promising, substantial progress, or no traction. Failure to solve is not evidence against the question."*
- Verifier (`math/verifier.md`): *"A correct toy case, heuristic, conditional reduction, or partial result is not a complete solution."* The statistics version adds *"suggestive simulation … asymptotic argument for a finite-sample claim … is not a complete answer."*
- Developer (`math/developer_contract.md`): *"Treat `keep` as a successful outcome."*; on scouts, *"Natural agreement is evidence, not a failure of diversity."*; *"Do not revise merely by making the question longer, broader, harder, more technical, more fashionable…"*; *"do not pre-emptively replace an interesting target with a safer or more conventional one for the sake of the later validity audit."*
- Validity (`question-validity/SKILL.md`): *"Report a defect only when supported by a concrete derivation, counterexample, computation…"*, *"an argument that exists only in the solve record does not count until it is stated there"*, *"Do not substitute a safer problem."*
- Taste (`math/taste.md`): *"Difficulty is not a negative signal … Do not reward a routine A+B combination whose likely theorem and method are already predictable … Judge the central mathematical idea in its natural canonical reading, not its rhetorical polish."*
- Pack rules (`research-pack/SKILL.md:58-66`): *"Facts only … No goal decomposition, no 'what's missing' editorializing — gap-finding is the proposer's job"*; *"Floor, not ceiling … 'not in the pack' never means 'not in the literature'."*
- Web grounding (`math/web_grounding.md`): *"treat 'not found' as absence of evidence, not evidence of absence"*, with per-role suffixes. The solver note reads *"Do not search for an answer to the question"* (`question_agents.py:738-755`).

**Patterns**

1. **Negative-space specification.** Every judging role gets an explicit list of things that are *not* defects: difficulty, timeouts, breadth, possibly-false conjectures, omitted standard boilerplate. This was learned from critics that over-killed (CHANGELOG v3) and from critics that rewarded verifiability.
2. **Role contracts as standalone files**, either skills or `family.json` roles. The orchestrator assembles workspaces from them, and protocol blocks are marked "forward verbatim to every subagent".
3. **Enum verdicts plus free-text evidence** in strict schemas, with semantic validators layered on top.
4. **Factual/evaluative separation.** Solvers report facts. Judges judge. Evaluative words are stripped by regex in transit.
5. **Three-layer prompt composition**: universal standards + domain spec + task instance (`data/subfields/README.md`). Domain content flows by role: invariants go to every role; standards go to proposer, filter, taste, and development but not validity.
6. **Every prompt change is an experiment**: preregistered predictions and falsifiers in `CHANGELOG.md`, outcomes recorded against them.
7. **The less rubric, the better for ranking.** Scored rubrics (v2–v6) added bias and variance. A short holistic prompt plus multi-sample aggregation did better (CHANGELOG v7).

---

## 6. Evidence of outcomes

`artifacts/` contains only `.gitkeep`, and `data/contexts/` contains only its README. Run outputs and packs are gitignored (`.gitignore`), so all outcome evidence is indirect.

- **Funnel** (`README.md:39`): "15 batches → 141 survivors → 3 rounds → ~33 ranked keepers". That is about 150 generated, ~94% passing the proposer self-check, and ~22% kept overall. With `advance_fraction=0.5` the cut is mostly mechanical: ⌊141/2⌋ = 70 → 35 → final.
- **The hard filter rarely kills.** Full v7 filter on 20 questions: *"killed 0/20"*. The literature verdicts were 13 nearby-but-distinct and 7 posed-as-open (`CHANGELOG.md:585-598`). The authors flag a semantic gap: a "core phenomenon already substantially studied" case survives because the exact theorem is open.
- **Solve probes don't discriminate**: *"solve probes 20/20 substantial_partial_progress (60/60 across the three isolated runs)"* (`CHANGELOG.md:307-308`).
- **Literature recall is stochastic**: the decisive prior-art paper (arXiv:2505.21475) was found in *"2 of 3 lead-format runs"* (`CHANGELOG.md:184`).
- **Scored critics were unstable and miscalibrated.** v3 judged 8/8 worthy (`:127`). v4 rated 13/20 and 17/20 worthy across two isolated runs. v5 cut that to 8–9/20 but lowered cross-run Spearman from 0.511 to 0.319 and top-2 overlap from 1/2 to 0/2 (`:249-256`). The fine-grained 0–10 components had cross-run r = 0.227/0.287 (`:397`). The expert's top choice was ranked first by v4 but cratered to 20/20 under v5 when penalty P1 fired in only one of two runs (`:270-280`).
- **The bare model beat the rubric critic.** An instruction-free `gpt-5.6-sol` beat the v4 guideline+probe critic on every comparison the expert could check (`:528-535`). v7's revised taste prompt put b1_q01 at rank 1 in 5/5 samples and b2_q08 at ranks [2,2,2,2,4] (`:600-606`). Restoring "Do not score separate criteria" gave 10/10 vs 9/10 (`:576-580`). **All of this calibration rests on two expert-confirmed items in one 20-question pool.**
- **Gold set**: `data/expert_reviewed/probabilistic_phase_transitions.json` holds **2 questions**, and **both needed repairs to quantitative content**. ppt-0001 was "Substantially revised by gpt-5.5-pro after a solve attempt exposed unreasonable scaling". ppt-0002 was "Repaired in-run by the proposer's solve probe (fixable_flaw)". The generator often produces wrong exponents, scalings, or regimes, and adversarial solve-then-repair is what makes questions usable. Note also that ppt-0002's stored statement violates the repo's own TeX rule.
- **Failures of development batches**: five PPI runs are excluded from conditioning for "poor expert feedback" or for having "over-indexed on application-specific applied-statistics settings" (`data/question_conditioning_exclusions/machine_proxy_inference.json`). The PPI spec has reached v16.
- **Silent orchestration bugs found late.** Composed subfield domain rules were "never rendered into the filter AGENTS.md" until commit `947f426`. A taste-stage output cap of 16384 tokens "advanced 115 candidates instead of 67" (fail-open incident; `question-pipeline/SKILL.md:217-219`). A validity audit left an unsupported claim because its proof lived only in the solve record (commit `b1818b6`).

---

## 7. Weaknesses and limitations

1. **No mechanical oracle.** Every gate (novelty, validity, difficulty, value) is an LLM judgment. Deterministic checks cover only plumbing: schemas, `in_pack`, TeX balance, fidelity of style rewrites.
2. **Selection is mostly fixed-fraction taste.** The filter kills about 0% (`CHANGELOG.md:590`) and fails open (`question_rounds.py:739-751`). Each round keeps ⌊0.5·N⌋ by blinded taste (`taste_groups.py:48-60`) regardless of absolute quality. A weak pool still yields its "top 50%".
3. **Calibration overfits to n=2.** v7 was promoted to default because it "reproduced the expert top 2" in one 20-item pool (`CHANGELOG.md:610-618`). There is no held-out set, and the ~50-example expert set planned in `docs/codex_critic_design.md` §2.5 does not exist.
4. **No difficulty calibration.** There is no solve-rate measurement across solvers or attempts. The solve-outcome enum saturates at `substantial_partial_progress`. Killing on a verified solution is off by default (`question_rounds.py:141, 4350-4359`), which contradicts `README.md:470` ("A verified complete solution removes a candidate only before the final round").
5. **Generator grades and repairs itself in the proposer.** The lead consumes its subagents' verdicts and applies repairs, and the script trusts `final.json` without reconciling it against `reports/*.json` (`question_gen.py:1489-1506`). The downstream critic re-probes, but `self_check_killed`/`solve_killed` statuses still drive future negative conditioning via the ledger.
6. **One model family judges its own output.** GPT proposer, GPT filter, GPT taste, GPT verifier. A cross-family panel was recommended (`docs/codex_critic_design.md` §2.3) but not built. Only the pack builder differs (Claude).
7. **Isolation is prompt-enforced.** "Do not inspect parent or sibling directories" (`question_rounds.py:935-949`). Codex sandboxes don't block reads, and judge workspaces are siblings of solver and developer workspaces.
8. **Dedup is local and lexical.** BM25 over titles and statements (`prior_question_search.py`), queried by the proposer itself. K3 operates only within ≤20-item filter groups (`question_rounds.py:621-623`). The three batches in a wave share the same pre-wave history and can duplicate each other. The embedding plus LLM-adjudication dedup in `docs/codex_critic_design.md` §2.1 was never implemented.
9. **Shallow re-checks after big changes**: the delta literature filter runs with `probe_minutes=1` and only K2 (`question_rounds.py:4059, 4226`).
10. **Process drift.** The guideline "never edited in place" rule (`CHANGELOG.md:5-10`) is violated: `v7_filter_taste.md` was edited in 4 later commits. The CHANGELOG lists fingerprint `986d718289da` while the live file hashes to `52e64d8e8ebf`. Other drift: `README.md:470` is stale; the dossier prompts (`math/family.json` → `dossier_workspace.md`, `dossier_bypass_workspace.md`) are not wired into any code; `run_pipeline` and `run_pipeline_from_round0_filter` duplicate the round loop (`question_rounds.py:3898-4131` vs `4134-4301`).
11. **Hard-coded, machine-specific values**: `DEFAULT_CODEX_BIN = "/Applications/ChatGPT.app/…/codex"` (`question_rounds.py:100-102`); `.claude/commands/critic.md:13` points at `/Users/niefandemac/...`; the final report title is hard-coded as `"# PPT 141 multi-round question ranking"` (`question_rounds.py:3811`).
12. **Monolithic code and hidden coupling.** `question_rounds.py` is 4.6k lines and `question_gen.py` 2.4k. Prompts are assembled from Python string fragments with `.replace()` patches (e.g. `question_gen.py:980-983`, `codex_critic.py:511-517`), so composition bugs ship silently (the `947f426` fix).
13. **Notation and format checks are warn-only** and disabled for non-math families (`question_gen.py:1362-1363, 2059`).
14. **Cost and latency.** About 1–2 h per 10-question batch, many hours per rounds run, and xhigh effort everywhere. There is no token or cost accounting beyond counting web searches (`codex_jobs.py:187-217`).
15. **Not reproducible bit-for-bit.** Packs and artefacts are local-only (`README.md:142-147`), so none of the reported results can be re-audited from the repo.

---

## 8. Reusable patterns for a QEC code-discovery workflow

### 8.1 Patterns to adopt (with QEC mapping)

1. **A frozen, fingerprinted "known-results pack" with copy-on-write versions and a ledger.** Map it to a *known-codes ledger*: codetables-style bounds, families (surface, colour, BB/qLDPC, HGP, lifted product, subsystem), best-known `[[n,k,d]]` per n, thresholds and noise models. Record its fingerprint in every run, and freeze it once a run consumes it (`research-pack/SKILL.md:36-71`). Have a different model family build it.
2. **Hide the collision inventory until after drafting, then use it as a hard negative** (`question_gen.py:1176-1194, 1242-1272`). Generators should invent targets first and check against "already achieved" afterwards, so the inventory doesn't anchor them.
3. **A domain-tag coverage scheduler for diversity.** Sample one combination (code family × noise model × decoder × hardware constraint) per batch, maximise new pair coverage, and keep one untagged batch per wave (`question_oversample.py:75-246`).
4. **Generate targets, then calibrate their difficulty against a solver ladder.** This is the gap in question-gen, and it is the most important addition for QEC. For each auto-generated target (e.g. "find a [[n,k,≥d]] CSS code with weight ≤w"), run a ladder: known-construction lookup → random/greedy search → an agent with a fixed budget. Record the solve rate. Drop targets the cheap tier hits, and targets that LP, quantum Singleton/Hamming, or BPT-type bounds show to be infeasible. Keep targets in a band. Question-gen's "solve probe → KS kill" is a degenerate one-rung version of this.
5. **The verifier gets only the claim artefact and the target, in a fresh process** (`question_rounds.py:2992-3033`). For QEC this becomes **a deterministic verifier**: parse the check matrices, assert commutation (H_X·H_Zᵀ = 0), compute k by rank, compute or bound distance exactly (SAT/ILP or a probabilistic upper bound, labelled as such), and simulate logical error rate under the declared noise and decoder with fixed seeds and confidence intervals. Never trust the generator's claimed `d`. An LLM verifier is only for things that cannot be computed.
6. **Evidence-only transport between agents, with the firewall tested.** Copy `build_solve_evidence` / `build_literature_evidence` (`question_agents.py:355-488`), the judgment-sentence stripper (`question_rounds_input.py:55-175`), the scope-card deny-list (`taste_rank.py:56-87`), and the firewall tests.
7. **The `codex_jobs` runner, nearly verbatim**: semantic fingerprint, attempt-scoped files, atomic promotion after schema and semantic validation, thread-resume on retry, total wall-clock budgets shared across retries, caller-owned semaphores, and `stdin=DEVNULL` (`codex_jobs.py:136-159, 595-755, 656-666`). It is provider-agnostic in spirit; swap the CLI.
8. **Per-question streaming without stage barriers** plus a compact `RUN_STATUS.json` (`question_rounds.py:3167-3443, 245-329`). This suits long-running search and verification jobs per candidate code.
9. **Blinded multi-sample ranking with failure handling** (`taste_groups.py`): opaque salted IDs, fresh shuffles, balanced groups, normalised percentiles, a minimum judgment count, and reuse of successful deterministic sample slots on retry. Use it only for the parts of QEC value that are genuinely subjective, such as "interestingness of a family" or "hardware plausibility".
10. **Unanimous gate for LLM "improvements"** (3/3 pairwise; `:2613-2718`) and **"keep is success"**. In QEC, an LLM rewrite of a construction is accepted only if the verifier confirms its parameters are equal or better.
11. **A fidelity guard on any rewrite** (`style_pass.py:172-226`). Generalise it to "the n, k, d, w, generators, and noise parameters in the artefact are byte-identical before and after presentation edits".
12. **Choose fail-open vs fail-closed per stage, explicitly.** Validity failure and taste coverage failure are fail-closed. Development failure keeps the original. Make every such choice visible in config and docs; the filter's fail-open default is the one to avoid (see 8.2).
13. **Versioned guideline and prompt files, fingerprinted into `run_config`, with preregistered predictions and falsifiers** (`CHANGELOG.md`). Enforce in CI that the file hash matches the changelog entry, which is the step question-gen skipped.
14. **Three-layer prompt composition plus domain invariants routed by role** (`data/subfields/README.md`). Example QEC invariants: "the code must be a stabilizer code with an explicit generator matrix", "the noise model and decoder must be stated", "claimed thresholds need a simulation spec".
15. **Integrity rules for agents**: cite only opened sources; numbers must come from files in scratch; "not found" ≠ absence; never simulate a missing subagent report (`codex_critic.py:497-507`, `question_gen.py:665-675`).
16. **A negative-conditioning ledger with statuses and exclusions.** Previously proposed or killed targets become negatives for future batches, and known-bad development runs can be excluded without deleting them.

### 8.2 Anti-patterns to avoid

- **Using LLM taste as the main selector** with fixed-fraction cuts when a computable objective exists. In QEC, rank on verified metrics (rate, distance, weight, threshold, overhead) and use LLM judgment only to break ties or assess novelty.
- **A fail-open hard filter** (`failed_forced_pass`). An unverifiable candidate must not advance.
- **Letting the generator copy its checkers' verdicts into its own output.** Have the orchestrator read checker outputs directly from their files and keep the generator out of that path.
- **Calibrating judges on two examples.** Build a labelled set of known-good and known-bad QEC targets from codetables and the literature, and keep a held-out split.
- **Single-family judging of single-family generation.** Use at least one judge from a different model family, or better, deterministic checks.
- **Prompt-only isolation.** Put verifiers in separate directory trees, containers, or read-only mounts that contain only the artefact.
- **Coarse outcome enums that saturate** (`substantial_partial_progress` 60/60). Record quantitative solve metrics instead, such as best d found and time-to-target.
- **Lexical-only, generator-driven dedup.** For codes, canonicalise: code-equivalence invariants (weight enumerators, automorphism-invariant hashes) and a global dedup pass over the pool.
- **Shallow re-checks after major revisions** (delta filter `probe_minutes=1`). If a construction changes, re-verify it fully.
- **Monolithic 4–5k-line orchestrators with prompts patched by `.replace()`.** Keep prompts as files and assemble them with a template engine plus snapshot tests.
- **Hard-coded machine paths and docs that drift from code defaults.**

---

## 9. Key files

| File | One-line description |
|---|---|
| `README.md` | Overview, two scales (single batch vs oversample+rounds), quickstart, defaults |
| `CLAUDE.md` | Repo-wide TeX notation rule (the only Claude Code project instruction) |
| `src/question_gen/pipelines/question_gen.py` | Proposer: builds AGENTS.md phases 0–5, workspace, schema; post-processes `final.json` into critic hand-off, audit table, repairs |
| `src/question_gen/pipelines/question_rounds.py` | Multi-round selector: hard filter, taste, solve/verify, develop, pairwise gate, polish, validity, delta filter, resume |
| `src/question_gen/pipelines/question_agents.py` | Per-role workspaces, schemas, evidence builders (the information firewall), refinement/validity application |
| `src/question_gen/codex_jobs.py` | Fingerprinted, resumable, schema-validated `codex exec` job runner |
| `src/question_gen/critic/codex_critic.py` | Lead+subagent critic/filter AGENTS.md builder; literature/solve/resolution probe and integrity prompt constants |
| `src/question_gen/critic/taste_rank.py` | Blinding, ranking schema/validation, API taste sampler, scope-card sanitiser |
| `src/question_gen/critic/taste_groups.py` | Balanced groups, normalised-percentile aggregation, exact-fraction selection, retry semantics |
| `src/question_gen/pipelines/question_oversample.py` | Wave scheduler with domain-combination coverage and strict resume identity |
| `src/question_gen/pipelines/question_rounds_input.py` | Imports proposer runs into the rounds manifest; provenance checks; judgment-sentence stripping |
| `src/question_gen/prior_question_search.py` | SQLite + BM25 bounded duplicate search exposed to the proposer |
| `src/question_gen/pipelines/style_pass.py` | Optional presentation rewrite with a deterministic fidelity guard |
| `src/question_gen/subfields.py`, `prompt_families.py` | Spec parsing (invariants, standards, domain tags) and family/role/schema loading |
| `data/prompt_families/math/*` | Canonical math prompts: standards, probes, developer/validity contracts, solver/verifier, taste, filter |
| `data/guidelines/v7_filter_taste.md`, `CHANGELOG.md` | Current filter+taste guideline; full experimental history with preregistered predictions |
| `data/subfields/*.md` | Domain specs (PPT v5, PPI v16, online algorithms v1) |
| `data/expert_reviewed/*.json` | Human-confirmed gold set (2 items, both repaired) |
| `.claude/skills/research-pack/` (+ `scripts/`) | Pack build/add/refresh/reconcile workflow; ledger, spot-check, merge, audit distillation |
| `.claude/skills/question-{gen,pipeline,refine,validity}/SKILL.md` | Operator-facing skill wrappers and the developer/validity role contracts |
| `docs/codex_critic_design.md` | Original survey and design (funnel, Swiss tournament, eval plan); partly unimplemented |
| `tests/` | 225 network-free tests for contracts, firewalls, resume, defaults, skill parity |
