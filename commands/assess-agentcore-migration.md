---
description: Assess an EKS-hosted agent against the Agentic AI Lens; logs the walk, then proposes changes for the customer to decide on
argument-hint: "[repo-path] [--region <region>] [--depth quick|full]"
allowed-tools: Bash, Read, Glob, Grep, Write, Edit
---

# Assess an agentic service for AgentCore Runtime migration

Arguments: `$ARGUMENTS`

**Load the `migrate-eks-to-agentcore` skill first.** This file is the *procedure* — step
order, what to write, when to stop. All substance lives in the skill's references, and each
step names the one that owns it. Do not restate their content here or in your output; read
the owner and apply it.

Output is a directory, `.agentcore-migration/`, with one writer per file:

| File | Holds | Written by |
|---|---|---|
| `access.yml` | survey 1 — what we were permitted to do | you, once, at Step 0.5 |
| `receipts.jsonl` | append-only log of work actually done | `lens_plan.py record`, during the walk |
| `findings.yml` | everything observed, projected from receipts | `lens_plan.py findings`. **Never hand-edit** |
| `assessment.yml` | your judgement: triage, economics, confidence | you, at Step 5 |
| `suggestions.yml` | grouped proposals, each citing findings | you, at Step 6 |
| `decisions.yml` | survey 2 — proceed / decline / defer | **the customer**, at Step 7 |

**You do not author the verdicts.** You produce suggestions; the human chooses; the record holds the
choices. Survey 1 gates what we may look at, survey 2 gates what we may change — and
`/plan-agentcore-migration` acts only on decisions. The mechanism, and how a correction works, is
[receipts.md](../skills/migrate-eks-to-agentcore/references/receipts.md); the suggestion and decision
schemas are
[record-and-adopt.md](../skills/migrate-eks-to-agentcore/references/record-and-adopt.md).

Write the files — do not answer only conversationally.

`--depth quick` walks only the starred practices and stops after Gate 2. Default is `full`.
Record the chosen depth in `access.yml`; a resumed assessment keeps that depth unless the
user changes it. `quick` produces `access.yml`, `receipts.jsonl`, `findings.yml`, and a short
summary. The judgement, suggestions, and customer decisions in Steps 4–7 belong to `full`.

**Say what `quick` costs, and pick on evidence rather than by default.** The stars mark what most
often decides a *migration*, not what most often turns out to be broken — so `quick` can miss the
findings a customer would act on. Measured both ways: on one service the three sharpest findings
(a fabricated citation, a platform feature failing 100% of writes, a false capability claim in its
own description) were all on **unstarred** practices and `quick` would have produced a
clean-looking record. On another, the four highest-severity findings were all on **starred**
practices, and `quick` would have found them in a fraction of the time.

So: `quick` is a legitimate first pass when the customer wants a fast read or you are triaging
several services — **and say in the record that unstarred practices were not walked**, so nobody
mistakes it for a clean bill. Choose `full` when the service is the one they are betting on, when
anything looks off, or when a `quick` pass already found something.

## Step 0 — Locate the service and the account

If `$ARGUMENTS` names a repo path, use it. If empty, ask which repository holds the agent
alongside the Step 0.5 access questions. This step only identifies the target from the user's
request; do not inspect source, credentials or the control plane before the survey is answered.

After Step 0.5, state the AWS account and region you are inspecting before any API call. **Read-only
throughout.** If the identity might be production, say so and confirm before proceeding.

**Findings come from their code and their deployment — not from the internet.** Every claim in the
record traces to their repo, their cluster's control plane, their telemetry, a live AWS API, or
something they told you. Do not research the customer's stack on the web to fill gaps.

Two reasons this matters more than it sounds:

- **It manufactures confidence.** A blog post or a vendor page describes what software is *meant*
  to do. The record is supposed to say what *this deployment actually does*, and those differ
  constantly — a shipped field the runtime ignores, a control with zero call sites, a proxy that
  has never served a request. Web reading cannot distinguish them; reading the cluster can.
- **Most customers are not researchable.** An internal service has no documentation you can find.
  A method that leans on public material silently works only for well-known open-source
  platforms and degrades exactly where the customer is most typical.

Where the deployed software is third-party open source, the legitimate move is **not** its
marketing or docs: pin its *published source to the release they run* (see
`references/evidence.md`, level 5 of the source hierarchy) and cite `file:line`. AgentCore
capability facts you need are already verified in
`deploy-on-agentcore/references/` and carry `[docs]` tags — read those rather than re-researching
them, and if one is missing or looks wrong, record it `[open]` and say so.

## Step 0.5 — Survey access. Before any other step.

→ Owner: **`references/survey-and-plan.md`** — the six questions and the check graph they produce.

Six questions, all about **access and permission**. Asking them is not the questionnaire failure this
skill warns about: you cannot derive whether you are allowed to do something. Everything derivable
stays derivable — do not ask about their architecture here.

Ask the unanswered S1–S6 questions in the first reply and wait for the user's response.
Reuse explicit answers from this conversation or an existing survey for the same service.
Follow the skill entry point for missing answers, resumption, and resolving the plugin path.

After the answers arrive, write `.agentcore-migration/access.yml` and run
`scripts/lens_plan.py resolve --multi-agent unknown` before proceeding to Step 0.6.
That survey is the only thing written up front.

**Do not write the intended walk anywhere.** Earlier versions had you emit a `plan:` block listing
every node's state before walking it. It was a stored copy of what `resolve` computes, and an
assessor could write `state: done` on all 15 nodes with nothing to contradict it — an assertion that
cannot fail, which is the defect this instrument documents in other people's work. The intended walk
is computed on demand; what actually happened is a **node receipt**, written when it happens:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lens_plan.py" record --node F --state unreachable \
    --blocked-by S4 --substitute-used "the tool server's own logs plus stored session rows" \
    --tag open --evidence "invocation refused at kickoff"
```

**Why this is a step and not advice.** Every previous version put the conditionals in prose and the
same thing happened each time: checks that did not apply were performed, checks that did apply were
skipped, and "I was not permitted to look" was recorded as `absent`. A precondition in prose gets
dropped by anyone running low on context. A precondition in a graph does not — and `invocation_permitted`
and `load_generation_permitted` are now enforced by a `PreToolUse` hook, which blocks the matching
commands until `access.yml` says yes.
The survey reference describes host coverage; the permission requirements apply on every host.

## Step 0.6 — Record as you go. This is not optional bookkeeping.

→ Owner: **`references/receipts.md`**

Every step below produces receipts, written **at the moment of observation**. Not at write-up time —
that is where citations get reconstructed from recollection, which has produced wrong line numbers
and facts attributed to files that did not contain them.

```bash
L="python3 ${CLAUDE_PLUGIN_ROOT}/scripts/lens_plan.py"
$L status      # the frontier: what is reachable, minus what already has a receipt
$L record …    # one observation. Validated against the graph and record-schema.yaml
$L findings    # receipts → findings.yml. Re-run after every correction
$L validate    # referential integrity. Never fails on coverage
```

`id` and `at` are stamped by the script, never by you. `--source` — the command you ran or the
artifact you opened — is **required** whenever the tag asserts an observation (`measured:*`,
`read:*`, `verified`). An invalid receipt is rejected rather than written, because an invalid receipt
is worse than none: it looks like evidence.

If you are resuming a context-exhausted assessment, `status` is where you start. It is also what the
`SessionStart` hook prints.


## Step 1 — Gate 0: hard blockers

→ Owner: **`references/constraints.md`** (what the gates are, cost to resolve, escape hatches)
→ Method: **`references/probe-aws.md`** (the live quota, region and price reads — node C) and
**`references/read-the-repo.md`** (image architecture, protocol, session shape — node A1)

Read thresholds live; never carry numbers in from the plugin. One receipt per gate — **`fail` is not
the only non-pass**, and treating it that way turns cheap rebuilds into refusals:

```bash
$L record --gate image_architecture --state trivial_fix --compute-type-assumed microvm \
    --tag read:source --evidence "Dockerfile:1 no platform pin, amd64 base" \
    --source "sed -n 1p Dockerfile"
```

The gate ids are in `references/record-schema.yaml`; `record` rejects one that is not on the list.

**Gate 0.0 first: is the agent a liftable unit at all?** Every other gate assumes it is. See
`references/constraints.md`.

**On a real blocker, stop the *cost and planning* work — not the inventory.** Reporting a
blocker in ten minutes beats a thorough assessment of an impossible migration, and you should
say so immediately. But do not skip Gate 1: on one assessed service, obeying an unqualified
"stop" would have suppressed a live cross-client data exposure, a prompt-injection path into
legal findings and a total absence of audit trail — all true whether or not they ever migrate,
and all of it the value the customer actually gets from the engagement.

So: report the blocker first and prominently, skip Gate 2 entirely, and still walk the
inventory. Note in `gate0` which compute type you gated against — three gates (GPU,
architecture, session duration) differ between microVMs and Instances.

**Exception — `not_a_liftable_unit` is not that kind of blocker.** Where Gate 0.0 finds the
deployable unit is the platform rather than the agent, **do not skip Gate 2**: the recommendation
becomes "keep the runtime, adopt what needs no move", and the customer still needs their current
run rate to judge it. Skipping the economics there withholds the numbers from precisely the case
where the answer is to stay. Record `result: not_a_liftable_unit`, keep going, and let the
adoption path be the deliverable.

## Step 1.5 — Read what the running system already emitted

→ Owner: **`references/read-the-cluster.md`** (nodes A2 and B).

**Do this always. It is read-only, needs nobody's permission, and it has been the single most
decisive read available.** A swallowed exception is invisible in config, in the resource specs, and
in a successful-looking answer — it shows up only in the logs and in `status` conditions.

## Step 1.6 — Invoking the agent: ask first, and expect the answer to be no

→ Owner: **`references/invoke-the-agent.md`** (nodes F, F1, F2) — the five probes, and the
read-only substitutes for each when consent is refused.

Sending a turn through a customer's agent is **not a read.** Get explicit permission, name what it
will touch, and prefer a non-production tenant. If you cannot get that, this step does not happen.

Expect it to be unavailable more often than not — a first assessment typically has a repo and
read-only cluster access and nothing else. **That is not a gap in your work.** Record node F
`unreachable` with `--blocked-by S4` and the substitute you used, which is distinct from `absent` or
`unknown` and shows the *class* of evidence missing rather than implying the controls were fine.

**A hook enforces this, and it will block you.** `invocation_permitted` in `access.yml` gates every
request-sending command; `load_generation_permitted` gates load tools. If you get consent mid-session,
write it into `access.yml` first — that is the point. The gate is heuristic and not a security
boundary, but a false block is recoverable in one edit and a false pass is the old behaviour.

## Step 2 — Gate 1: walk the inventory

**Resolve the graph first — do not decide reachability by eye.** The 41 questions have access
preconditions and depend on each other, and nobody holds that in their head:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lens_plan.py" resolve --multi-agent yes|no|unknown
```

It prints four buckets, for the 15 check-graph nodes and the 41 questions alike — `reachable`,
`degraded` (a prerequisite is unanswerable), `blocked` (access missing), `not_applicable` (shape) —
each with its blocker and the substitute to use. **That output is the walk.** Measured on real
profiles: a repo-only engagement reaches 8 of 41 questions; full access reaches 41. Handing a
customer "your access decisions mean we can answer 8 of 41 questions" is often the most actionable
line in the document.

Anything not `reachable` goes in the record with its blocker, as `unknown` or `not_applicable`.
**None of it is `absent`** — absent is a claim about their system, and this is a claim about your
access. Conflating them is how this instrument has repeatedly manufactured gaps.

→ Graph: **`references/lens-graph.yaml`** — preconditions, dependencies and substitutes per question.
→ Owner: **`references/production-inventory.md`** — the 41 Lens *questions* (the Lens also has 150
best practices, which this does not cover), how to detect each, and a verdict per question. **Read
its "How to fill a row" block first** — the verdict column is one field of several, and not the one
the customer acts on.
→ Session topology has its own reference: **`references/topologies.md`**
→ Searches for the commonly-missed domains: **`references/read-the-repo.md`**
→ Controls that exist and do nothing: **`references/sweep-for-dead-controls.md`** (node E). Every
search is positive signal; on two assessments the highest-severity findings were all negative.

Per question, one `question` receipt and — where you found what would close it — one `remedy`
receipt. `production-inventory.md`'s "How to fill a row" has the two commands and the field table.

**Do not shortcut to the components you expect to find.** Empty rows are the most valuable
output. If you cannot assess a practice, record it `unknown` with the reason — never omit it.
`status` will tell you which questions still have no receipt, and it will flag a question recorded
`absent` when `resolve` says you had no access to answer it.

Then regenerate and check:

```bash
$L findings && $L validate
```

`lens_coverage` in `findings.yml` is **computed** — `questions_assessed`, `inventory_rows` and the
list with no receipt all come from the same receipts, so the "41 of 41 while carrying 66 rows"
dispute cannot recur. Quote those numbers; do not assert your own.

## Step 3 — Gate 2: measure, then model

→ Owner: **`references/cost-model.md`** (what to report, and what not to)
→ Method: **`references/measure.md`** (node H — where the four numbers come from, and the 128 MB
billing floor to check before investing in precision)
→ Under load: **`references/concurrency-sweep.md`** (node I — consent-gated, and not the first step)

One `measurement` receipt per number, carrying where the number came from:

```bash
$L record --measurement peak_memory_gb --value 0.088 \
    --source-of-value kubelet_stats_summary --is-true-high-water-mark false \
    --tag measured:customer --evidence "kubectl top pod copilot-7f4 → 88Mi" \
    --source "kubectl top pod -n copilot"
```

`--is-true-high-water-mark` is `true` **only** for `cgroup_memory_peak`. Everything else is a sampled
maximum, i.e. a floor on the real peak, and must not be called a peak — the worked supersession
example in `receipts.md` is exactly this mistake, made and then corrected.

If the numbers cannot be measured, record the receipt with `--tag open` and say the cost verdict is
unavailable. **Do not substitute this plugin's reference figures as if they were the customer's.**

**Finish `quick` here.** Regenerate `findings.yml` with `lens_plan.py findings`, run
`lens_plan.py validate`, and summarise the findings, available cost evidence, and limitations.
State that unstarred practices were not assessed; missing access or measurements stay explicit.
Then end the assessment. Do not enter Steps 4–7, create `assessment.yml`, `suggestions.yml` or
`decisions.yml`, or start planning. Continue below only for `full`; expand a `quick` assessment
only when the user requests it.

## Step 4 — Gate 3: ask only the underivable

→ Owner: **`references/ask.md`** (node J — the question list, and why it is short)

Now, and only now, ask. You have earned specificity by doing the work.

An answer is a receipt like any other, tagged `stated:customer`. So is a correction: if they tell you
a finding is wrong, record the correction and re-run `findings` — never edit `findings.yml`.

## Step 5 — Write your judgement

`.agentcore-migration/assessment.yml`. **Everything observed is already in `findings.yml`, generated
from receipts.** This file holds only what requires judgement, which is why it is separate: one
writer per file is what stops two files describing the same facts and drifting.

```yaml
assessed_at: <iso8601>

# Read out of findings.yml rather than restated. Listed here so you check they are there:
#   context.engagement, context.code_ownership   — record these as `context` receipts at node A.
#     code_ownership decides whether a remedy is actionable at all: on a third-party platform the
#     remedies are chart config, an upstream PR or a fork — none of which is "redesign the service",
#     and repeating `undecided` on every row reads as "we asked and they have not replied" when in
#     fact there is nobody to ask.
#   gates, topology, measurements, network, missing_artifacts, lens_coverage, findings

# Severity and sequence, per finding id. NOT in findings.yml, because both are judgements.
# `validate` reports any `absent` finding with no entry here.
triage:
    # Severity does not imply sequence. Without fix_first, /plan reconstructs Phase 0 from free
    # text — which on a real record omitted one of its own two TOP FINDINGS, so the generated plan
    # shipped with no destructive-action gate.
  - { finding: AGENTOPS06, severity: high | medium | low, fix_first: true | false }

# Derived from findings.yml's measurements plus a model. Judgement, so it lives here.
economics:
  agentcore_default_monthly: <usd>
  agentcore_tuned_monthly: <usd>
  eks_monthly: <usd>
  volume_crossover_conversations: <n>
  cluster_stays_for_other_workloads: true | false
  levers:
    stop_runtime_session: recommended | already_done | not_applicable
    agentcore_memory_required: true | false     # does a returning user resume a thread?

# What is NOT recommended to change, stated explicitly so the customer can see the assessment was
# not a pretext. An empty list here is a red flag, not a clean bill.
keep_as_is: [<component>: <why it is already right>]

# Your recommendation. The customer's answer is `outcome` in decisions.yml, and the two differing
# is legitimate and visible — that is what replaced the old free-text dissent block.
recommended_outcome: migrate | migrate_partially | adopt_components | assess_only
                   | stay | redesign_first

# Two axes. A Gate 0 certainty with no telemetry is high/unavailable, not "low".
recommendation_confidence: high | medium | low
# Complete per-turn measurements on a deployment with NO USERS means per_turn: high and
# monthly: unavailable — there is no volume to multiply by. One combined value forces a choice
# between implying a price you cannot give and denying measurements you have.
cost_confidence:
  per_turn: high | medium | low | unavailable
  monthly: high | medium | low | unavailable
open_questions: []
```

**There is no `dissent` block, and its absence is deliberate.** A disagreement about a *fact* is a
new receipt tagged `stated:customer` — regenerate and the finding changes, with its history visible.
A disagreement about a *choice* is `decisions.yml` with `choice: declined` and their reason. Neither
needs a third home, and giving them one is how the same disagreement ended up recorded twice and
differently.

## Step 6 — Turn findings into suggestions

→ Owner: **`references/record-and-adopt.md`** — the four grouping rules and the schema.

`.agentcore-migration/suggestions.yml`. The 41 questions are the **evidence unit**; the action unit
is coarser — one NetworkPolicy touches three of them. That translation used to happen invisibly, in
your head.

Each suggestion is **one change** that closes every finding it cites, and carries: the receipts that
ground it, what it buys, **what it does not fix**, its cost, and your confidence. A suggestion
without `does_not_fix` is a pitch with citations. Order `requires_runtime_move: false` first.

Three things about how that list reads to the customer, all of them owner-documented:

- **No section-divider comments over the sort.** A header saying "no runtime move needed" reads as
  "you don't need to migrate" when it sits over 21 of 22 rows. The sort already carries it.
- **Label each row with `closed_by`** — the service or the customer's own component. Only
  `closed_by: runtime` mentions moving, because it is the only one where moving is required.
- **`effort` is scope, not a duration.** Files, call sites, boundaries crossed, whether a staged
  rollout is needed. A day count for work in a repository you have read-only access to is a guess
  wearing the same font as the measurements.

Then `$L validate`. It fails on ungrounded suggestions — empty or dangling `closes` / `grounded_in` —
and it *reports* a finding that needs action and appears in no suggestion. That last one is the gap a
model under context pressure creates, and it is invisible without the check. Deliberate omission is
fine: say so in `triage`.

**Do not group to get something approved.** Two changes are two suggestions however related they
feel; bundling a weak item with strong ones is packaging, not grouping.

## Step 7 — Survey 2: the customer decides

→ Owner: **`references/record-and-adopt.md`** — the decision schema.

Present the suggestions and record their answers in `.agentcore-migration/decisions.yml`:
`proceed`, `declined`, or `deferred` with the **condition** that would revisit it. Capture the reason
in their words, now — reconstructed later it is the one thing a human revisiting actually asks for
and the one thing that was always missing.

**Survey 1 gated what we could look at. This gates what we may change.** Do not fill this in on
their behalf: an undecided suggestion has *no entry*, `decided_with` names who chose, and
`/plan-agentcore-migration` refuses without it.

**"No to everything" is a success state.** `outcome: assess_only` — findings delivered, nothing
adopted yet, with the condition that would change it named. Say so plainly, so the flow does not read
as a funnel that leaked.

If they dispute a *fact* rather than a choice, that is a receipt, not a decision. Record it, re-run
`findings`, and let the value change with its history attached.

## Step 8 — Present it

→ Owner: **`references/production-inventory.md`**, "Reporting" — the bucket order, and why
gaps come before benefits.

State Lens coverage from `findings.yml`'s computed `lens_coverage`: how many **questions** of 41,
which not, and why — and that the Lens's 150 best practices were not walked. Lead with the findings
that are exploitable today, then with anything the customer already gets right.

If the customer disagrees with a fact, record the receipt. If they disagree with a proposal, that is
survey 2's job. Either way, do not re-argue: recording the disagreement is what makes the record
theirs, and winning the argument is not the goal.

Finish with `$L validate` and say what it reported. Uncovered findings and questions with no receipt
are **not** failures — they are the honest shape of what this access allowed, and often the most
actionable lines in the document.

**Do not run `/plan-agentcore-migration` before Step 7 has real answers in it.** It refuses without
`decided_with`, and it should: a plan built on your own verdicts is the thing this whole structure
exists to prevent.
