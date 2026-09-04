---
name: migrate-eks-to-agentcore
description: >
  Assess and execute a migration of an existing agentic service from Amazon EKS
  (or ECS, or any self-managed container platform) to Amazon Bedrock AgentCore
  Runtime. Use this skill whenever the user is deciding whether to move an agent
  off Kubernetes, wants a per-component migrate/keep/delete verdict, needs the
  real cost comparison between self-hosted and AgentCore, or is planning the
  phased execution of such a move. Produces an editable decision record, then a
  phased plan and scaffolding from it.
  Trigger on: migrate agent to AgentCore, EKS to AgentCore, move agent off
  Kubernetes, should we use AgentCore or EKS, AgentCore vs EKS, AgentCore vs
  self-managed, agent platform decision, agentic service on EKS, containerised
  agent migration, replatform agent, agent migration assessment.
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

# Migrating an Agentic Service from EKS to AgentCore Runtime

## What this skill is for

A customer runs an agent on EKS. It works. They are asking whether to move it to
AgentCore Runtime. The honest answer is *per component and conditional*, and the
conditions are measurable.

This skill exists because the available material on this question is feature tables
that assert savings percentages nobody sourced. A customer who catches one
unsupported number discounts everything else you say — including the parts that are
true and would have helped them. **Trust is the deliverable; the migration is
downstream of it.**

## The three rules

**1. Read and measure before asking.** Almost everything decisive is derivable from
the customer's repo and traffic: which session topology they run, their CPU-to-wall
ratio, their peak memory, image architecture, whether they block the event loop. A
questionnaire that asks what you could have read produces a sales script. Ask only
what cannot be derived — data residency, compliance, team depth, roadmap.

**2. Tag every claim with its evidence class.** Non-negotiable.

| Tag | Means |
|---|---|
| `[measured:customer]` | Observed on **their** workload. The only kind you may quote as theirs |
| `[measured:reference]` | Observed on this plugin's reference build — **n=1 agent**. Illustrates shape, never their number |
| `[verified]` | Queried from a live AWS API (Service Quotas, Pricing, SDK) |
| `[docs]` | Stated in AWS documentation, not independently confirmed |
| `[reasoned]` | Follows from the above — argument, not observation |
| `[open]` | Not established. Say so; do not fill the gap |

**3. Never quote a cost figure you did not measure on their workload.** Not even the ones in
this skill — they are one agent, one shape, n=1. Cost figures here exist to show *which levers
matter*, never as the customer's number. A figure carried in from a blog post is worse than no
figure; a figure carried in from this plugin and presented as theirs is worse still, because it
looks sourced.

## Workflow

```
/assess-agentcore-migration [repo]     →  .agentcore-migration/decisions.yml
        Gate 0  hard blockers       (minutes, binary, cheapest first)
        Gate 1  inventory walk      (read the code, against the Lens)
        Gate 2  economics           (measure, then model)
        Gate 3  the underivable     (ask — and only here)
                    ↓
/plan-agentcore-migration              →  phased plan + scaffolding
        no live changes to the running system
```

The **decision record** is the spine. It is a file the customer reads, edits and
disagrees with, and the plan is generated *from* it — so their choices bind. It also
carries a dissent log: when the customer disagrees with a verdict, record it and
proceed. Do not re-argue.

## Gate 0 — hard blockers, checked first

Cheap, binary, and they can end the conversation before anyone wastes a week. Any one is
a *stay* or a *redesign-first*: **single-turn duration** past the request timeout, **GPU**,
**sidecars**, a **non-HTTP/MCP/A2A/AG-UI protocol**, **image size**, an **amd64-only
dependency**, **concurrency** past the session or creation-rate caps, **region**
availability, and **inbound auth method** if callers sign with SigV4.

**Read the thresholds live**; never carry numbers in from this plugin, because several are
adjustable and accounts differ.
→ [constraints.md](references/constraints.md) for the gates, what each costs to resolve, and
which are lead-time rather than walls
→ [assessment.md](references/assessment.md) for the probes

The request timeout bites most often, and it is an escape hatch rather than a wall — but the
escape is a redesign, which is why it belongs in Gate 0 rather than a footnote.

## Gate 1 — walk the inventory

"Does everything have to move?" is answered credibly by walking a published standard, not a
list someone invented. [production-inventory.md](references/production-inventory.md) is
structured on the **[AWS Well-Architected Agentic AI Lens](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentic-ai-lens.html)**
(June 2026) — its **41 questions**, each verbatim, plus two columns the Lens does not have:
**how to detect it in the customer's code** and the **migration verdict**.

**This is a question-level triage, not a Well-Architected review.** The Lens has 41 questions
and **150 best practices**; this covers the questions. Say "41 of 41 Lens *questions*" and
offer the full review (import the published lens JSON into AWS WA Tool) as the follow-on.

Record `state` / `evidence` / `verdict` per practice. Nine carry most of the decision
(starred in the reference); do not stop there, because **the empty rows are the finding** —
"no evals", "no spend ceiling", "no tenant boundary" are the most valuable output, and they
are true whether or not the customer migrates. A gap is not a blocker: most pre-date the
migration and survive it.

Domains most often missed, and usually present: outbound auth (2LO/3LO) and token
propagation, multi-tenant isolation and cost attribution, self-hosted MCP servers, long-term
memory as distinct from session state, RAG and retrieval ACLs, guardrails and PII,
human-in-the-loop, audit / non-repudiation, evals, idempotency on write tools.

**Session topology (`AGENTREL03`) deserves its own framing**, because it determines what migration
actually *deletes*, and getting it wrong is how people overstate the benefit. There are three
points, not two — EKS admits both a stateless and a sticky design — and **comparing the
stateless one to AgentCore overstates the gain** by crediting the platform with the whole cost
of the session store. → [topologies.md](references/topologies.md)

## Gate 2 — economics

Measure four numbers on their workload, then model. Never skip to the model, and never quote a
figure measured on someone else's agent.

The two configuration questions that dominate the bill — whether `StopRuntimeSession` is
called, and whether AgentCore Memory is genuinely required — swung the verdict **22× on one
measured workload**. That multiplier is a property of *that* workload's active-to-idle ratio,
not of the platform; short conversations against a 900s idle timeout maximise it. Do not quote
it as a platform figure.

CPU was **0.85% of the bill at default config — but ~19% once tuned**, because tuning removes
the memory and Memory-event lines it was small against. Quote whichever matches the
configuration you are recommending. → [cost-model.md](references/cost-model.md) for what to
report, [assessment.md](references/assessment.md) for where the numbers come from.

## Gate 3 — ask only what you cannot derive

Six infrastructure questions plus two product questions that decide real verdicts. Kept
deliberately short, because by this point the work has earned specificity.
→ [assessment.md](references/assessment.md)

## What does NOT go away

Always present this. It is the section that earns the right to the rest.

| Stays yours | Why |
|---|---|
| Budget / spend ceilings | No platform bounds a runaway agent loop |
| Session-id issuance + user binding | AgentCore does not map users to sessions `[docs]` |
| Row-level authorization | Pod and runtime identity are per-workload, not per-user |
| Prompt, tool design, evals, grounding | Untouched by the migration |
| The knowledge store | Stays put — and forces VPC mode |
| Event-loop discipline | Blocking I/O in an `async def` is still yours to get right |

## Evidence status of this skill's own claims

Stated so you know which parts to lean on and which to verify with the customer. The
guidance was validated by deploying one agent codebase to both EKS and AgentCore
Runtime and measuring.

**`[measured]` on a real deployment of both platforms:**
session-store deletability (singleton agent held 3-turn context with no store), microVM
session start ~1.96s (`[measured:reference]`, n=3, one prompt), CPU/wall ratio and per-turn CPU,
peak memory, agent-rebuild vs store-I/O split, the cost model, ARM64/NodePool behaviour,
event-loop blocking impact, MCP portability, **inbound JWT auth on the runtime**,
**Gateway with a Lambda target**, **Cedar policy enforcement at the Gateway boundary**.

Those measurements were fed back into **deploy-on-agentcore**, which owns the platform
detail — inbound JWT in `identity.md`, Cedar enforcement and validation behaviour in
`policy.md`, session and quota behaviour in `runtime-and-sessions.md`, and the billing
model in the new `cost-and-billing.md`. This skill points at them rather than restating
them, so there is one place to correct when the platform moves.

The three that change *migration planning* specifically are in
[constraints.md](references/constraints.md) and
[production-inventory.md](references/production-inventory.md): inbound auth is a breaking
cutover (`AGENTSEC03`), the knowledge store forces the network design (`AGENTPERF03`), and tool
authorization is scoped work the migration makes available rather than delivers (`AGENTSEC02`).

**`[docs]` / `[reasoned]` — verify before asserting to a customer:**

| Area | Status |
|---|---|
| EKS-side inbound auth (ALB OIDC) | Not built, so the ALB-vs-authorizer comparison is one-sided |
| Outbound auth / token propagation to tools (3LO, token vault) | Inbound only was tested |
| Row-level authorization | Cedar blocked a *tool*; filtering *rows* by caller identity untested |
| Long-running turns via `HealthyBusy` + polling | Documented escape hatch, not demonstrated |
| GPU / sidecar / protocol blockers | Documented constraints, not tested |

For outbound auth and row-level filtering, defer to **deploy-on-agentcore**
(`identity.md`, `policy.md`, `security.md`) and say plainly that the migration effort
for those components is an estimate.

One process note worth inheriting: four of the Gateway role permissions were rediscovered
the hard way, one error at a time, when they were already documented correctly in
`deploy-on-agentcore/references/policy.md`. **Read the existing references before
building anything.**

## Reference files

| File | Read when |
|---|---|
| [assessment.md](references/assessment.md) | Running the assessment — what to read, probe, and ask |
| [topologies.md](references/topologies.md) | Classifying their session design; A/B/C in depth |
| [cost-model.md](references/cost-model.md) | Any cost conversation. The 22× swing, the crossover |
| [constraints.md](references/constraints.md) | Quotas, hard limits, and the honest counter-list |
| [production-inventory.md](references/production-inventory.md) | **The assessment instrument.** All 41 Agentic AI Lens *questions* (of 150 best practices), how to detect each, and its migration verdict |
| [playbook.md](references/playbook.md) | Sequencing, parallel run, rollback |

## Related skills

- **deploy-on-agentcore** — once the decision is made, how to actually build it
  (runtime, Gateway, Identity, Policy, CDK, VPC isolation)
- **strands-agent-design** — the agent itself, which the migration should not change

## Reasons to stay on EKS

Give these equal weight. A customer who hears only the migration case does not trust
the migration case.

- Non-agent workloads already on the cluster, with the platform team to run it
- Turns that legitimately exceed 15 minutes with checkpoint-resume semantics
- GPU, custom isolation (Kata/gVisor), sidecars, or non-HTTP protocols
- Sustained high volume where reserved or Spot capacity beats per-session billing
- Deep Kubernetes expertise already paid for, and a working service
- A regulatory posture requiring everything, including the tool gateway, to be
  unreachable from the internet — not satisfiable for AgentCore Gateway `[docs]`

"It works today" is a real argument. The burden of proof is on the migration.
