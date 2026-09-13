---
name: migrate-eks-to-agentcore
description: >
  Assess an existing agentic service on Amazon EKS, ECS, or another self-managed
  platform against the AWS Well-Architected Agentic AI Lens. Use for EKS-to-AgentCore
  migration assessments, AgentCore vs self-hosted cost comparisons, production
  readiness, architecture or security reviews, session/scaling constraints, and
  incremental adoption of Gateway, Identity, Memory, Policy or Evaluations.
  Keeping the current runtime and assess-only outcomes are supported. Starts with
  an access and permission survey, records evidence, proposes changes for the
  customer to accept, decline or defer, and plans only from their decisions.
---

# Assessing an Agentic Service on EKS against AgentCore

## Entry point — survey before assessment

For an assessment request, perform these steps in order:

1. **Read [survey-and-plan.md](references/survey-and-plan.md) now.** Before the survey,
   read only this skill's instructions and, when resuming, the named assessment's existing
   `access.yml`. Defer customer source inspection, AWS/kubectl calls, Gate 0, and other
   assessment checks until access is established.
2. **Ask the unanswered S1–S6 access questions in your first reply, then wait for the
   user's answers.** Use the user's language and the wording guidance in
   [survey-and-plan.md](references/survey-and-plan.md#presenting-the-survey).
   Reuse explicit answers already supplied in the conversation or an existing survey for
   this service; ask only what is missing.
   If the target repository was not named, ask for it alongside the survey.
   An accessible repo or configured credential is not an answer about permission.
   Do not fill unanswered fields with guesses or `unknown` just to continue;
   `unknown` records a user saying they cannot confirm.
3. **After the answers arrive, write `.agentcore-migration/access.yml`** following the
   reference schema, including `surveyed_with` and `depth`. For a new assessment, default
   to `full` unless the user requests `quick`; when resuming, reuse the recorded depth
   unless the user changes it. Then run `scripts/lens_plan.py resolve`
   with `--multi-agent unknown` until topology is known. An existing survey can be reused
   unless the user changes its scope; update changed answers before resolving again.
4. **Follow [the assessment procedure](../../commands/assess-agentcore-migration.md)**
   using the resolved access and recording receipts as you go; skip the setup and survey
   steps already completed. State the account and region before any API call. When resuming,
   run `status` to find unfinished work within the recorded depth. Apply the mode below,
   subject to the resolved access preconditions and the existing Gate 0 blocker rules.

| Depth | Inventory scope | Where to finish |
|---|---|---|
| `quick` | Starred practices only; state which unstarred practices were not assessed | Gates 0–2, then generate findings, validate, summarise the limited assessment, and stop. No Gate 3, judgement file, suggestions, or survey 2 |
| `full` (default) | All 41 Lens questions, recording access limitations | Gates 0–3, judgement, suggestions, survey 2, then the final report and validation |

For a planning request with an existing assessment, follow
[the planning procedure](../../commands/plan-agentcore-migration.md); it requires the customer's
recorded decisions. Do not restart a completed survey or invent decisions.

**Paths on every host:** resolve links relative to this `SKILL.md`. The plugin root is two
directories above it. Run the scripts from the assessed repository using that root's absolute,
quoted path. References use `${CLAUDE_PLUGIN_ROOT}` as shorthand; in Kiro or another host where
it is unset, substitute the actual plugin root rather than running `/scripts/lens_plan.py`.

A customer runs an agent on EKS. It works. They want to know where it stands, and what AgentCore
would do for them. The honest answer is *per component and conditional*, and the conditions are
measurable.

This skill exists because the available material on this question is feature tables that assert
savings percentages nobody sourced. A customer who catches one unsupported number discounts
everything else you say — including the parts that are true and would have helped them. **Trust is
the deliverable; any adoption is downstream of it.**

**The assessment is the product.** Migration is one outcome among several; `adopt_components` and
`assess_only` are first-class successful results. Never conclude "stop using what you built."
Which components need a runtime move, which do not, and how to say so at the confidence it has
earned → [record-and-adopt.md](references/record-and-adopt.md).

## The three rules

**0. The service in front of you is not the one this skill was written against.** Every concrete
example in these references is `n=1`. Expect any language, any framework or none, agents defined as
**declarative resources on a platform** rather than as code, work shaped as jobs or documents rather
than chat turns, and multi-agent topologies. So: treat every example as an illustration of a *class*
of finding; when a search returns nothing, decide whether the component is **absent** or the
**question was wrong for this stack**, because conflating those manufactures gaps; where a practice
is satisfied **by the platform** rather than by the customer's code, say so. **If this skill's
vocabulary does not fit what you found, describe what you found.** A forced fit reads as
inexperience to a customer whose stack differs.

**1. After the access survey, read and measure before asking product questions.**
The S1–S6 access questions above come first; this rule governs Gate 3.
Almost everything decisive is derivable from the customer's
repo, control plane and traffic. A questionnaire that asks what you could have read produces a sales
script. Ask only what cannot be derived → [ask.md](references/ask.md).

**2. Tag every claim with its evidence class.** Non-negotiable. The nine tags, what each means, and
how to rank a source → [evidence.md](references/evidence.md).

**3. Never quote a cost figure you did not measure on their workload.** Not even the ones in this
plugin. Figures here show *which levers matter*, never the customer's number.

## Workflow

```
/assess-agentcore-migration [repo]     →  .agentcore-migration/
        survey 1  access & permission  (the only thing that cannot be derived)
        Gate 0    hard blockers        (minutes, binary, cheapest first)
        Gate 1    inventory walk       (read the code, against the Lens)
        Gate 2    economics            (measure, then model; quick reports and stops here)
        Gate 3    the underivable      (full only: ask — and only here)
        survey 2  the customer decides (full only: proceed / decline / defer-with-condition)
                    ↓
/plan-agentcore-migration              →  phased plan + scaffolding
        resolve which phases are even available, then act only on decisions
        no live changes to the running system; every artifact it runs gets a receipt
```

**Two surveys, and the symmetry is the point: survey 1 gates what we may look at, survey 2 gates
what we may change.** The assessor does not author the verdicts. It produces *suggestions* — this is
the change, here is what it buys, here is what it does not fix — and the human chooses.

Six files, one writer each. Nothing describes the same fact twice:

| File | Holds | Writer |
|---|---|---|
| `access.yml` | survey 1 | written once, by hand |
| `receipts.jsonl` | append-only log of work actually done | `lens_plan.py record` |
| `findings.yml` | everything observed, projected from receipts | generated. **Never hand-edited** |
| `assessment.yml` | the assessor's judgement | model-authored |
| `suggestions.yml` | grouped proposals, each citing findings | model-authored |
| `decisions.yml` | survey 2 | written with the human |

**Receipts are the spine.** They are written at the moment of observation, not reconstructed at
write-up time, which is where citations used to come from recollection and be wrong. A correction is
a **new receipt** — including a customer saying a finding is wrong, which is just `stated:customer`.
Receipts carry supersession, so reinterpreting old evidence does not require fabricating a new
observation → [receipts.md](references/receipts.md).

Resolve the 41 questions and the 15 nodes against the completed access survey with
`scripts/lens_plan.py resolve`. **That output is the walk.** Nothing writes the
intended walk to a file: a stored plan can be both stale and asserted done.

## The four gates

**Gate 0 — hard blockers.** Cheap, binary, and any one can end the conversation before a week is
spent. **Gate against a compute type, not "AgentCore"** — GPU, architecture and session duration all
differ between microVMs and Instances, and treating GPU as a blocker has produced a wrong customer
verdict. Read every threshold live in the customer's region; never carry numbers in from this plugin.
→ [constraints.md](references/constraints.md) for the gates and what each costs to resolve,
[probe-aws.md](references/probe-aws.md) for the live reads.

**Gate 1 — walk the inventory.** "Does everything have to move?" is answered credibly by walking a
published standard. [production-inventory.md](references/production-inventory.md) is structured on
the **[AWS Well-Architected Agentic AI Lens](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentic-ai-lens.html)** —
its **41 questions**, each verbatim, plus how to detect each in the customer's system and the
migration verdict. This is a **question-level triage, not a Well-Architected review**: the Lens also
has 150 best practices, which this does not cover. Say "41 of 41 Lens *questions*".

**The empty rows are the finding** — "no evals", "no spend ceiling", "no tenant boundary" are the
most valuable output, and they are true whether or not the customer migrates. A gap is not a blocker;
most pre-date the migration and survive it. Session topology deserves its own framing because it
determines what migration actually *deletes* → [topologies.md](references/topologies.md).

**Gate 2 — economics.** Measure on their workload, then model. Never skip to the model. Check first
whether compute is material at all: on one measured customer it was 0.04–1.2% of run rate, dominated
by tokens and the datastore. → [measure.md](references/measure.md) for where the numbers come from,
[cost-model.md](references/cost-model.md) for what to report.

**Gate 3 — ask.** Only what you could not derive → [ask.md](references/ask.md).

## Reference files

The check graph in [survey-and-plan.md](references/survey-and-plan.md) has one node per action, and
each node points at exactly one file below.

| File | Read when |
|---|---|
| [survey-and-plan.md](references/survey-and-plan.md) | **Start here.** The six access questions and the check graph they produce |
| [lens-graph.yaml](references/lens-graph.yaml) | The 15 nodes and the 41 questions as one graph: what each needs, what it depends on, the substitute when access is missing. Resolve with `scripts/lens_plan.py` |
| [receipts.md](references/receipts.md) | **Recording anything.** The six artifacts, the nine receipt kinds, and how a correction works |
| [record-schema.yaml](references/record-schema.yaml) | The receipt vocabulary — every legal value. `record` validates against it, so an invalid receipt is not written |
| [evidence.md](references/evidence.md) | Tagging any claim. The nine tags and the source hierarchy |
| [read-the-shape.md](references/read-the-shape.md) | Node A — coded, declarative or managed, and whether the repo is what runs |
| [read-the-repo.md](references/read-the-repo.md) | Node A1 — the source searches, by language and by domain |
| [read-the-cluster.md](references/read-the-cluster.md) | Nodes A2, B — control-plane reads and the log read. Cheapest decisive step |
| [sweep-for-dead-controls.md](references/sweep-for-dead-controls.md) | Node E — controls that exist and do nothing, and live ones with bad side effects |
| [invoke-the-agent.md](references/invoke-the-agent.md) | Nodes F, F1, F2 — consent-gated. Substitutes when refused |
| [probe-aws.md](references/probe-aws.md) | Node C — quotas, region availability, prices, and whose account you are in |
| [measure.md](references/measure.md) | Node H — the four numbers, and the billing floor to check first |
| [concurrency-sweep.md](references/concurrency-sweep.md) | Node I — consent-gated. What integer bounds parallelism |
| [production-inventory.md](references/production-inventory.md) | **The assessment instrument.** All 41 Lens questions, how to detect each, its verdict, and the reporting order |
| [record-and-adopt.md](references/record-and-adopt.md) | Node K — suggestions and survey 2, what stays theirs, the stay case, the two confidence axes |
| [ask.md](references/ask.md) | Node J — the eight underivable questions |
| [topologies.md](references/topologies.md) | Classifying their session design; and how many runtimes for a multi-agent service |
| [constraints.md](references/constraints.md) | Quotas, hard limits, and the honest counter-list |
| [cost-model.md](references/cost-model.md) | Any cost conversation. The levers, the crossover |
| [plan-graph.yaml](references/plan-graph.yaml) | The plan's phases and their preconditions, as a graph. Resolve with `scripts/lens_plan.py phases` before writing any phase |
| [playbook.md](references/playbook.md) | Sequencing, parallel run, rollback — both tracks, and what each phase state permits you to write |
| [provenance.md](references/provenance.md) | What this plugin actually measured, and what to verify before asserting |

## Related skills

- **deploy-on-agentcore** — once the decision is made, how to actually build it (runtime, Gateway,
  Identity, Policy, CDK, VPC isolation). It owns all platform detail; this skill points at it rather
  than restating it, so there is one place to correct when the platform moves.
- **strands-agent-design** — the agent itself, which the migration should not change.
