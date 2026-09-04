# Handoff — the walk is auditable and the customer decides. Next: run it against something real.

**Path convention:** bare `*.md` / `*.yaml` filenames below live in
`skills/migrate-eks-to-agentcore/references/`. Anything else is from the worktree root.

## What shipped this session

The instrument decided *what* to check and *how*. It now also records **what happened**, and hands
the customer a proposal to decide on instead of a verdict to notice.

```
.agentcore-migration/
  access.yml       survey 1 — what we were permitted to do        written once, by hand
  receipts.jsonl   append-only log of work actually done          appended by `record`
  findings.yml     everything observed, projected from receipts    generated. Never hand-edited
  assessment.yml   the assessor's judgement                        model-authored
  suggestions.yml  grouped proposals, each citing findings         model-authored
  decisions.yml    survey 2 — proceed / decline / defer            written with the human
  plan/            plan.md + items.yml + scaffold/ + MANIFEST.md   written by /plan
```

Six subcommands in `scripts/lens_plan.py` (was one bare-flag mode):

```
resolve    graph + access → what is reachable, for the 15 nodes AND the 41 questions
record     one validated, script-timestamped observation. Eight kinds, --batch for JSONL
status     the frontier: reachable, minus what has a receipt. Plus divergence
findings   receipts → findings.yml, applying the supersedes chain
validate   referential integrity across the six artifacts. Never fails on coverage
selfcheck  the instrument itself — graph vs inventory vs prose. Needs no record
```

Three hooks in `hooks/hooks.json`, all silent outside a directory holding `.agentcore-migration/`:
`SessionStart` prints `status`, `PreToolUse` on Bash gates invocation and load against `access.yml`
(and blocks mutations outright), `Stop` runs `validate`.

New references: **`receipts.md`** (cross-cutting, like `evidence.md`) and **`record-schema.yaml`**
(the receipt vocabulary as data, so `record` can reject an invented value at write time).

## Verified by running, not by recollection

| Check | Result |
|---|---|
| `selfcheck` | clean — 41 questions, 15 nodes, 9 tags, every node file present |
| `resolve`, repo-only profile | 8/41 questions — **identical to the pre-change baseline** (8 / 0 / 30 / 3) |
| `resolve`, full access | 41/41 |
| Repo-wide broken-link scan | exactly 1, the known false positive (`app_ctx`, Python call syntax) |
| `record` rejections | bad tag, missing evidence, missing `--source` on an observational tag, typo'd subject, invented field, `present_but_ineffective` with no reason, `--supersedes` with no `--reason`, `--supersedes` a nonexistent id — all rejected, nothing written |
| `findings` | supersession chain, per-component rows, computed `blocks:` and computed `lens_coverage` all correct on a 27-receipt fixture |
| `validate` | fires on every referential error listed below; exits 0 with notes on every coverage gap |
| Consent gate | 7 cases — invoke/deny, health-probe/pass, docs-fetch/pass, load/deny, mutate/deny, read-only kubectl/pass, outside-an-assessment/silent |
| `Stop` hook | exit 2 with the errors; exit 0 when `stop_hook_active` (loop guard); silent outside an assessment |
| Back-compat | `lens_plan.py --access f.yml` still resolves |
| `py_compile`, `bash -n`, JSON and YAML parse | all clean |

## Four things the previous handoff had wrong or incomplete

Carry these; they were not re-derivable from the files.

**1. The five-file layout had no home for ~200 lines of the old schema.** `gate0`, `topology`,
`measurements`, `network`, `missing_artifacts`, `economics`, `keep_as_is`, both confidence axes,
`current_gaps`, `lens_coverage`. Resolved by splitting on **kind**: observations became receipts and
project into `findings.yml`; judgements went to a sixth file, `assessment.yml`. The tell that this
was right: **your worked supersession example is a measurement, not a question** — so measurements
*had* to be receipt-backed or the one example of the mechanism had nowhere to live.

**2. `plan:` should be deleted, not reconciled.** The handoff framed Gap 1 as "nothing reconciles
`plan:` against what happened". But `plan:` was a stored copy of what `resolve` computes, so it could
be stale *and* falsely claimed — two failure modes where a checker addresses one. It is gone. Intent
is `resolve`, computed on demand; actual is node receipts; `status` is the reconciliation. Its one
unique field, `substitute` (which one you *used*, not which was available), moved onto the node
receipt, i.e. to the moment it becomes true.

**3. `closed_by` cannot live in a generated `findings.yml` unless it is receipt-grounded.** The
layout line put it there, but findings derive from receipts. There is now a **`remedy` receipt kind**:
"their chart ships `authz.serverSide` and it is unset, `values.yaml:210`" is an observation with its
own tag and its own `--source`. Side effect worth having — `remedy_verified: false` is now visible
rather than a field nobody filled.

`verdict` went the same way and was **dropped from the record**. Half its values duplicated `state`
(`gap` ≈ `absent`, `correctly_absent` ≈ `absent_by_design`); the other half — migrate / keep / delete
/ regress / stay — is migration-shape opinion that belongs on a suggestion, where it has to carry a
cost and a `does_not_fix`. `production-inventory.md`'s Verdict *column* stays: that is the
instrument's prior, which is a different thing from a per-record field.

**4. `dissent` is fully absorbed and is gone.** A factual disagreement is a new receipt tagged
`stated:customer` (you had already settled this). A disagreement about a *choice* is `decisions.yml`
with `choice: declined` and their reason. Nothing was left, and keeping the block gave corrections
two homes — which is how the same disagreement got recorded twice and differently.

**5. The `Stop` hook contract is not what the handoff assumed.** Checked against the live docs:
**`Stop` exit 2 does not fail the turn — it prevents Claude from stopping and hands back `reason` as
the next instruction.** Better than described (the dangling id gets *fixed* rather than reported),
but it needs a loop guard on `stop_hook_active`, or an unfixable error holds the session open
forever. That guard is in `hooks/stop-validate.sh` and tested. Also: on `PreToolUse`, plain-text
stdout at exit 0 reaches only the debug log, so the consent gate uses the JSON
`permissionDecision: "deny"` form. And a `command` hook that **times out does not block** — say so
in the reference rather than letting a reader over-trust the gate.

## Design decisions worth carrying

**Every number in `lens_coverage` is computed.** `questions_assessed` (distinct question subjects),
`inventory_rows` (finding keys) and `no_receipt` all come from the same receipts. The
"41 of 41 while carrying 66 rows" dispute cannot recur, because a finding key is
`<practice>/<component>` and both counts derive from it.

**`--component` closed a known schema gap for free.** Because a topology field is now one receipt
with an optional component, a hybrid design records two values for `detected` (`cache`, `store`) and
a multi-agent service gets a per-edge `concurrent_turn_safety`. Both were on the known-unfixed list.

**`decisions.yml` has no `undecided` value.** An undecided suggestion has *no entry*, and `validate`
reports it as undecided by absence. That is what stops a column of `undecided` reading as "we asked
and they never replied" on an engagement where nobody was ever asked. Same reasoning gives
`decided_with`: absent means no survey 2 happened, and `/plan` refuses.

**`validate` fails on referential errors only.** Dangling receipt id, empty or dangling
`closes`/`grounded_in`, a decision citing a missing suggestion, a plan item with no decision id or
citing a non-`proceed` one, a finding with no receipt, `findings.yml` stale *while decisions exist*.
Everything else is a note: uncovered findings, a finding in two suggestions, a missing
`does_not_fix`, questions with no receipt, a decision resting on superseded evidence, and
resolve-vs-receipt divergence. **Never coverage.** A check that punished an incomplete walk would
teach assessors to fabricate receipts, which is worse than no check.

**The best new check is one nobody asked for.** `status` and `validate` both report a question
recorded `absent` when `resolve` says you had no access to answer it. That is the single most common
way this instrument manufactured false gaps, and it is now computable rather than a matter of
vigilance.

**The principle, restated because it went wrong three times before:** put a step in the script when
the reason is that a model under context pressure will skip it — *not* when the script seems
smarter. `resolve` earns it (41 preconditions do not fit in working memory). `findings` earns it
(supersession chains fail silently). `suggest` earns nothing, because the hard part is judgement, and
a script attempting it would bundle by pillar or severity — the "packaging" failure grouping rule 2
exists to prevent. There is no `suggest`.

**`--batch` was allowed deliberately**, against the instinct that batching is the defect. A model
holding six observations from one file read and no cheap way to write them will keep them in its head
instead, and in-head batching is what produced citations from recollection. The script stamps `at`,
so a genuinely late batch is visible as a cluster of identical timestamps far from the tool calls.

## Carried forward — these cannot be re-derived

**"The examples are the bloat and the bloat is the bug."** Every overcorrection found across six
assessments lived in a worked example carried from a single earlier one. Three times a measured `n=1`
finding became a stated law and misfired. Cut anecdotes, keep rules. **Keep evidence tags and
measured provenance** — they are what makes a claim arguable rather than assertable, and `record` now
enforces the tag set and requires `--source` behind any observational tag.

**Positioning is load-bearing.** AgentCore go-to-market. The **assessment is the product**; migration
is one outcome. `adopt_components` and `assess_only` are first-class successes — `assess_only` with
every decision `declined` validates clean, deliberately. Never conclude "stop using what you built."
Lives in `record-and-adopt.md`.

**Generalize past the specimens.** Findings from the agents we built and the platform we deployed are
*evidence*, never the rule, and their vocabulary must not reach the instrument. Test: *would this
sentence still be correct for a service in another language, with another framework, that we have
never seen?*

**`constraints.md` and `cost-model.md` were each ~60% inapplicable per assessment** (no GPU, no
sidecar, no VPC-resident store, no traffic). A conditional-loading signal, not a delete.

## Known-unfixed, in priority order

1. **None of this has been run against a real assessment.** It is tested on a 27-receipt synthetic
   fixture and on two access profiles. The next session's job is a live run — that is where the
   schema gaps will show, as they did every previous time.
2. **`selfcheck` cannot check the gate ids.** `constraints.md` names Gate 0 checks in prose, so
   `record-schema.yaml`'s `gate_ids` are hand-maintained and will drift. Everything else it checks
   (question ids ↔ `production-inventory.md`, node ids and files ↔ `survey-and-plan.md`, tags ↔
   `evidence.md`, access classes ↔ what a survey answer can grant) is now automated — that was
   known-unfixed #1 and it is closed.
3. **7 of 9 `deploy-on-agentcore` references carry no evidence tags**, despite holding some of the
   strongest measured material. `evidence.md` defines the tag set, so there is somewhere to point.
4. **Neither `receipts.md` nor the new `assessment.yml` schema has been read by a fresh assessor.**
   The command file grew to 399 lines and `plan-agentcore-migration.md` to 451. If a live run shows
   an assessor skipping Step 6 or 7, suspect the length before the design.
5. **The previous handoff's items 4 and 5 name paths that do not exist.**
   `eks-agent-reference/fixtures/score.py` and
   `eks-agent-reference/skill-eval-workspace/trimmed-skill/` are absent from this worktree, from the
   main checkout, and from the entire git history. Either they live somewhere else on another machine
   or they are gone. **Do not carry those two items forward as actionable** without first finding the
   directory. The full-vs-trimmed comparison being open is probably still true; the path is not.

## Traps

- **Read the files; do not trust recollection, including this document.** Every previous handoff has
  contained at least one wrong claim, and this one found four in its predecessor.
- The broken-link checker reports **1 known false positive** — `app_ctx` in
  `strands-agent-design/references/agent-topology.md` is Python call syntax.
- **Do not edit the instrument while an assessment runs against it.** Done once; the agent had to
  remap its citations mid-run.
- **No local-marketplace shims.** Tried, broke within one rebase. Merge to `main`, then
  `claude plugin update build-agents-on-aws`. Version is now `2.3.0` in all three manifests.
- **`findings.yml` is generated. If you find yourself editing it, you have broken the audit trail** —
  `validate` will catch a row with no receipt, but only that row.
- Live fixtures cost ~$27/day: five EKS clusters plus Aurora. `kagent-{a,b,c}-ref` are the fixtures;
  **`woongjin-agent-ref` is a different customer's cluster — do not touch it.** Kept running
  deliberately for further iteration.

---

# The prompt

```
This worktree holds a Claude Code plugin that assesses agentic AI services on EKS against the AWS
Well-Architected Agentic AI Lens and shows which Amazon Bedrock AgentCore components would close
each gap. Read HANDOFF.md first — it carries design decisions you cannot re-derive from the files,
and it lists which of its predecessor's claims turned out to be wrong.

The instrument now decides what to check, how to check it, logs the walk as receipts, and hands the
customer suggestions to accept, decline or defer. None of that has been run against a real service.

Your task is a live end-to-end run, and then to fix what it breaks.

1. Pick a fixture cluster (kagent-a-ref, kagent-b-ref or kagent-c-ref — NOT woongjin-agent-ref,
   which is a different customer's) and run /assess-agentcore-migration against it for real: the
   access survey, the receipt trail written at observation time, findings generated rather than
   authored, suggestions grouped under the four rules, and a survey-2 pass where you play the
   customer honestly — including declining something.

2. Then /plan-agentcore-migration from the decisions alone, and check the two invariants hold in
   practice rather than only in `validate`: a plan item with no decision cannot exist, and a
   declined suggestion appears as deliberately excluded rather than silently absent.

3. Report what the schema could not express. Every previous iteration found real gaps this way —
   three of them are recorded in HANDOFF.md as having been closed by `--component` alone.

Read the files rather than trusting HANDOFF.md's description of them. Do not edit the instrument
while the assessment is running against it; collect the defects and fix them afterwards, which is
a trap this project has already fallen into once.

After: `lens_plan.py selfcheck`, the repo-wide broken-link scan (expect exactly 1 known false
positive), and `lens_plan.py validate` against the assessment you produced.
```
