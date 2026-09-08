---
name: migrate-eks-to-agentcore
description: >
  Assess an existing agentic service running on Amazon EKS (or ECS, or any
  self-managed container platform) against the AWS Well-Architected Agentic AI
  Lens, then show which Amazon Bedrock AgentCore components would close each gap
  — and which of those need no platform change at all. Migration is one possible
  outcome, not the goal: keeping the current runtime and adopting Gateway,
  Identity, Memory, Policy or Evaluations alongside it is a first-class result,
  and so is deciding nothing yet. Use this skill whenever the user wants to know
  where their agent stands, what a production-grade agent service requires, which
  parts AgentCore actually replaces, the real cost comparison between
  self-hosted and AgentCore, or is planning a phased move. Logs the walk as an
  auditable receipt trail, proposes grouped changes for the customer to accept,
  decline or defer, then generates a phased plan from their decisions alone.
  Trigger on: migrate agent to AgentCore, EKS to AgentCore, move agent off
  Kubernetes, should we use AgentCore or EKS, AgentCore vs EKS, AgentCore vs
  self-managed, agent platform decision, agentic service on EKS, containerised
  agent migration, replatform agent, agent migration assessment.
  Trigger equally on the no-migration framings, which are the common case: assess
  my agent on EKS, is my agent production ready, agent architecture review, agent
  security review, what is missing from my agent, Well-Architected review for an
  agent, can I use AgentCore without migrating, adopt AgentCore incrementally,
  AgentCore Gateway in front of my existing agent, keep my agent on Kubernetes,
  we are not ready to migrate, what would AgentCore give us.
  Trigger on the constraints that decide it: 15-minute request timeout, ARM64
  requirement for AgentCore, active session workload quota, new-session creation
  rate, microVM per session, session affinity for agents, sticky sessions for
  conversational agents, agent session store, ElastiCache session state for
  agents, KEDA autoscaling for agents, HPA does not scale my agent, CPU-based
  autoscaling for LLM workloads, SSE stream cut by ALB idle timeout, agent pod
  cold start, pod-per-session, conversation lost on rollout, flush cadence for
  conversation state, StopRuntimeSession cost, idleRuntimeSessionTimeout billing,
  AgentCore memory billing, is AgentCore cheaper than EKS.
  Trigger on the component domains an assessment must cover: outbound auth 2LO,
  3LO, token propagation, token vault, workload identity, multi-tenant isolation,
  tenant boundary, row-level security for agents, cost attribution per tenant,
  self-hosted MCP servers, consolidating MCP servers behind a gateway,
  short-term vs long-term agent memory, memory namespaces, RAG and retrieval ACLs,
  vector store for agents, guardrails, PII redaction, prompt injection defence,
  human-in-the-loop approval, audit trail for agents, non-repudiation,
  agent evals, golden dataset, regression gate, idempotency for agent tools,
  graceful degradation, prompt lifecycle management, multi-agent orchestration.
  Also trigger when the user asks what a production-grade agent service requires,
  which parts of one AgentCore actually replaces, or asks for an agent architecture
  triage against the AWS Well-Architected Agentic AI Lens questions (AGENTOPS,
  AGENTSEC, AGENTREL, AGENTPERF, AGENTCOST, AGENTSUS).
---

# Assessing an Agentic Service on EKS against AgentCore

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

**1. Read and measure before asking.** Almost everything decisive is derivable from the customer's
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
        Gate 2    economics            (measure, then model)
        Gate 3    the underivable      (ask — and only here)
        survey 2  the customer decides (proceed / decline / defer-with-condition)
                    ↓
/plan-agentcore-migration              →  phased plan + scaffolding
        acts only on decisions; no live changes to the running system
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

**Start with the access survey, not with Gate 0.** Which checks are even reachable is the one thing
that cannot be derived, and a precondition left in prose gets dropped →
[survey-and-plan.md](references/survey-and-plan.md), then resolve the 41 questions and the 15 nodes
against it with `scripts/lens_plan.py resolve`. **That output is the walk.** Nothing writes the
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
| [receipts.md](references/receipts.md) | **Recording anything.** The six artifacts, the eight receipt kinds, and how a correction works |
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
| [playbook.md](references/playbook.md) | Sequencing, parallel run, rollback |
| [provenance.md](references/provenance.md) | What this plugin actually measured, and what to verify before asserting |

## Related skills

- **deploy-on-agentcore** — once the decision is made, how to actually build it (runtime, Gateway,
  Identity, Policy, CDK, VPC isolation). It owns all platform detail; this skill points at it rather
  than restating it, so there is one place to correct when the platform moves.
- **strands-agent-design** — the agent itself, which the migration should not change.
