# Receipts — logging the walk, and how a correction works

Cross-cutting, like [evidence.md](evidence.md): every node writes receipts. `evidence.md` says how
to *tag* what you found; this says how to *record* it, and where the record goes afterwards.

## Why this exists

The instrument decided what to check and how, and then nothing recorded what happened. The old
`plan:` block was written once, up front, declaring intent — so an assessor could write
`state: done` on all 15 nodes and nothing caught it. That is the same "assertion that cannot fail"
defect this instrument documents in other people's work, reproduced in its own schema.

Three consequences were observed, not predicted:

- coverage asserted as "41 of 41" while the record carried 55–66 rows
- citations written from recollection at write-up time, and wrong — line numbers, and facts
  attributed to files that did not contain them
- no resumability, so a context-exhausted assessment restarted from nothing

**A receipt is written at the moment of observation.** That is the whole mechanism. If you are
writing receipts at write-up time you have reintroduced the defect, and the timestamps will show
it: `lens_plan.py` stamps them, not you, so a batch written late appears as a cluster of identical
times a long way from the tool calls that should have produced it.

## The six artifacts, one writer each

```
.agentcore-migration/
  access.yml       survey 1 — what we were permitted to do       written once, by hand
  receipts.jsonl   append-only log of work actually done         appended by `record`
  findings.yml     everything observed, projected from receipts   generated — never hand-edited
  assessment.yml   the assessor's judgement                       model-authored
  suggestions.yml  grouped proposals, each citing findings        model-authored
  decisions.yml    survey 2 — proceed / decline / defer           written with the human
  plan/            plan.md + items.yml + scaffold/                written by /plan
```

One writer per file is what stops the drift this project keeps hitting when two files describe the
same facts. The split is by **kind**, not by convenience: an observation is a receipt, a projection
of observations is `findings.yml`, an opinion is `assessment.yml`, a proposal is `suggestions.yml`,
a choice is `decisions.yml`.

**There is no stored `plan:` block.** The *intended* walk is `lens_plan.py resolve`, computed on
demand from the graph and the survey, so it cannot go stale. The *actual* walk is node receipts.
`lens_plan.py status` is the reconciliation. A stored copy of the intent could be both stale and
asserted done, which is two failure modes where a checker would only have addressed one.

## Chain of custody, both directions

```
receipt  →  finding  →  suggestion  →  decision  →  plan item
 r-0143     AGENTSEC02    S-04         proceed      phase 1, item 3
```

- *"Why did we do this?"* → plan item → decision → suggestion → findings → receipts → the command
  and its output
- *"Why didn't we do that?"* → decision `declined`, with the reason captured **when it was
  declined** rather than reconstructed later. This is the direction that was lost, and it is what a
  human revisiting actually asks.

## Recording

```bash
lens_plan.py record --question AGENTSEC02 --component tool_allowlist \
    --state present_but_ineffective --ineffective-because partially_covers \
    --defect-owner customer \
    --tag read:source --evidence "tools/registry.py:88 — allowlist is client-side" \
    --source "grep -rn ALLOWED_TOOLS tools/"
```

`id` and `at` are the script's, never yours. Every enum, and every subject id, is validated against
[lens-graph.yaml](lens-graph.yaml) and [record-schema.yaml](record-schema.yaml) — **an invalid
receipt is not written**, because an invalid receipt is worse than none: it looks like evidence.

`record-schema.yaml` is the authoritative field list. The eight kinds and what each is for:

| Kind | Records | Note |
|---|---|---|
| `question` | one of the 41 Lens questions | the **evidence unit**. `--component` separates rows that share a practice |
| `remedy` | what would close a question, and whether you confirmed it | separate receipt, separate evidence — which is what stops `closed_by` being an unsourced guess |
| `node` | a check-graph node: `done`, `unreachable`, `not_applicable` | `--blocked-by` and `--substitute-used` replace the old `plan:` entry |
| `gate` | a Gate 0 check | carry `--compute-type-assumed`: three gates flip between microVMs and Instances |
| `measurement` | one number, with where it came from | `--source-of-value` and `--is-true-high-water-mark` are the fields that stop a sampled maximum being reported as a peak |
| `topology` | one field of the topology read | `--component` gives a multi-agent service a per-edge answer, and a hybrid design two values for one field |
| `artifact` | something you needed and could not read | `blocks:` is then **computed** from the questions that cite it, not remembered |
| `context` | `engagement`, `code_ownership` | decides whether a remedy is actionable at all |

**`--source` is required whenever the tag asserts an observation** (`measured:*`, `read:*`,
`verified`). A receipt that claims an observation while recording no way to repeat it is the defect
this file exists to fix.

`--batch -` takes JSONL on stdin, for the case where one file read produced six observations.
Legitimate; batching *at write-up time* is not.

### What a receipt does not carry

Severity, `fix_first`, cost, effort, ordering, and what a change does *not* fix. Those are
judgements, and they live in `assessment.yml` and `suggestions.yml`. A receipt says what you saw.

## Corrections: a new receipt, from any source

`findings.yml` is generated and **never hand-edited.** A correction is a new receipt.

The evidence tag system already handles the human case: a customer saying *"that's wrong, we do
have retention"* is `stated:customer`, an existing class. So no new mechanism is needed for human
corrections, and the chain to receipts never breaks. If findings were hand-editable that link would
break silently and the audit trail would become decorative.

It also matches how the errors actually happened. Several assessor claims were wrong because they
were written from recollection at write-up time. The honest fix is another observation, not a quiet
overwrite — and a dated, attributed correction is more useful to someone revisiting than a value
that changed.

### Supersession, because not every correction is a new observation

Sometimes the reading was right and the interpretation was wrong. Without supersession, append-only
forces you to fabricate an observation you never made.

The clean example from this project: `kubectl top` reported 88 MiB accurately, and the conclusion
"this is the peak" was false, because it is a windowed average. Nobody looked again.

```jsonl
{"id":"r-0143","kind":"measurement","subject":"peak_memory_gb","value":0.088,
 "source_of_value":"kubelet_stats_summary","is_true_high_water_mark":false,
 "tag":"measured:customer","evidence":"kubectl top pod → 88Mi","source":"kubectl top pod -n copilot"}
{"id":"r-0207","supersedes":"r-0143","reason":"reinterpretation",
 "kind":"measurement","subject":"peak_memory_gb","value":0.091,"tag":"reasoned",
 "evidence":"same reading; a windowed average, not a peak — 3.7% low against cgroup ground truth",
 "note":"nobody re-observed; this is a floor on the real peak"}
```

Two reasons, and keep them distinct:

- **`reinterpretation`** — same evidence, different conclusion. Nobody re-observed.
- **`re-observation`** — we looked again. The world may have changed, or we misread it.

**Supersession is visible, not silent.** The superseded receipt stays in the log, and
`findings.yml` carries a `history:` block showing the current value has one. Otherwise append-only
produces the *appearance* of an audit trail while the reader only ever sees the winner.

**Regeneration is a command.** Every correction means re-running `lens_plan.py findings`. If that
were manual it would rot within a week, which is why `status` and `validate` both say when
`findings.yml` is older than the newest receipt.

## What the script owns, and what you own

Put a step in the script when the reason is that a model under context pressure will skip it —
**not** when the script seems smarter.

**The script owns set arithmetic and referential integrity:**

```
lens_plan.py resolve   --access access.yml     # graph + access → reachability
lens_plan.py record    --question AGENTSEC02 --state absent --tag read:source --evidence … --source …
lens_plan.py status                            # the frontier: reachable, minus what has a receipt
lens_plan.py findings                          # receipts → findings.yml, applying supersession
lens_plan.py validate                          # referential integrity across the six artifacts
lens_plan.py selfcheck                         # the instrument itself, no record needed
```

**You own everything requiring judgement:** what a finding *means* (`absent` versus
`not_applicable`), the grouping of findings into suggestions, what a suggestion does *not* fix, cost
and effort, severity, and any ordering beyond the mechanical `requires_runtime_move` sort.

**There is deliberately no `suggest` subcommand.** Grouping requires knowing that one NetworkPolicy
closes three questions — knowledge of the system, not set arithmetic. A script attempting it would
bundle by pillar or by severity, which is precisely the "packaging" failure the grouping rules in
[record-and-adopt.md](record-and-adopt.md) exist to prevent.

`resolve` earns its place because 41 access preconditions do not fit in working memory. `findings`
earns it because supersession chains are fiddly and fail silently. `suggest` earns nothing, because
the hard part is judgement. The same error has been made three times in this project's history: a
mandatory invocation step that ignored consent, a `plan:` block that recorded intent nobody checked,
and a proposed `suggest`. Each time the reach was for **enforcement** where the requirement was
**honesty**.

## `validate` never fails on coverage

It exits non-zero on **referential** errors only: a dangling receipt id, a suggestion citing a
finding that does not exist, a plan item with no decision, a finding with no receipt.

Everything else it *reports*: uncovered findings, a finding in two suggestions, a suggestion missing
`does_not_fix`, questions with no receipt, a decision resting on superseded evidence, and
divergence between what `resolve` says was reachable and what receipts claim.

`unreachable` and `not_permitted` are **recordable states, not errors** — that is the point of the
whole graph. A check that punished an incomplete walk would teach assessors to fabricate receipts to
get past it, which is worse than having no check.

**Divergence is information, not error.** It can mean work claimed but not evidenced, evidence
gathered and dropped, or a verdict changed after the draft. Interpreting a mismatch is your job.
One divergence is worth reading every time: a question recorded `absent` when `resolve` says you
had no access to answer it. If a substitute produced it, name the substitute in the receipt. If not,
that is a claim about your access wearing a finding's clothes, and it is the single most common way
this instrument has manufactured false gaps.

## Hooks

Three ship with the plugin, in `hooks/hooks.json`. All three no-op silently outside a directory
containing `.agentcore-migration/`.

| Event | Does |
|---|---|
| `SessionStart` | prints `status`, so a resumed session sees the frontier instead of restarting from nothing |
| `PreToolUse` on Bash | blocks invocation unless `access.yml` says `invocation_permitted: true`, load generation unless `load_generation_permitted: true`, and mutating commands always |
| `Stop` | runs `validate`; on a referential error, hands it back rather than letting the turn end |

The consent gate's matching is heuristic — a `curl` to localhost could be anything — but the failure
modes are asymmetric. A false block is annoying and recoverable: record the consent and proceed. A
false pass is merely the old behaviour. **It is not a security boundary**: a hook that times out
does not block, and there are many ways to send a request it does not match. It is a gate against
forgetting.
