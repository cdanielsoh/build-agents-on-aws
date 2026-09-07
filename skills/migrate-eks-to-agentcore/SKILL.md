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
  self-hosted and AgentCore, or is planning a phased move. Produces an editable
  decision record, then a phased plan and scaffolding from it.
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

## What this skill is for

A customer runs an agent on EKS. It works. They want to know where it stands, and what
AgentCore would do for them. The honest answer is *per component and conditional*, and the
conditions are measurable.

This skill exists because the available material on this question is feature tables
that assert savings percentages nobody sourced. A customer who catches one
unsupported number discounts everything else you say — including the parts that are
true and would have helped them. **Trust is the deliverable; any adoption is
downstream of it.**

### Migration is an outcome, not the objective

**The assessment is the product.** A customer who learns that their tool server has no
server-side authorization, that their approval gate has zero call sites, or that their
conversation history has no retention policy has received something valuable whether or not they
ever move a workload. Deliver that first and separately.

Then, for each gap, the useful question is not *"should you migrate?"* but **"which AgentCore
component closes this, and does it require moving your runtime?"** Because usually it does not:

| Adoption shape | Runtime stays on EKS? | Typical fit |
|---|---|---|
| **Gateway** (+ **Policy**) in front of existing tools | **yes** | tool sprawl, no server-side tool authz, no per-identity scoping, missing approval gates |
| **Identity** for outbound credentials / token vault | **yes** | hand-rolled OAuth, per-user tokens in a table |
| **Memory** for long-term or retained state | **yes** | no retention policy, unbounded history, preference extraction |
| **Evaluations** | **yes** | no golden set, no regression gate |
| **Observability** | **yes** | no traces, no per-turn attribution |
| **Runtime** | **no — this is the migration** | session isolation, inbound `CUSTOM_JWT`, scale-to-zero, ceilings met |

Only the last row is a replatform. Say which row each recommendation sits in, and **lead with the
ones that need no platform change**, because those are the ones a customer can act on this
quarter. A component adopted alongside their existing service is a real outcome; so is
"assessed, nothing adopted yet, revisit when X changes."

**Adoptability is `component × who owns the code`, not the component alone.** The table above
holds when the customer owns the agent process. It does **not** hold on a third-party platform,
and that column has already been wrong once in practice:

| | Customer owns the agent code | Third-party platform runs it |
|---|---|---|
| **Gateway** | add a tool endpoint | **still yes** — usually just a URL on a config object, the cheapest adoption available |
| **Policy** | yes | **yes**, behind Gateway |
| **Memory**, **Identity** | add the SDK integration | **no** — needs an SDK call inside an executor you do not ship. It is an upstream PR or a fork, so say `upstream_contribution`, not "adopt Memory" |
| **Evaluations** | yes | **verify first** — check their trace/export format is one Evaluations accepts before promising it |
| **Observability** | yes | partly — platform-level flags may exist; SDK-level instrumentation does not |

So establish `code_ownership` **before** writing the adoption path, and never promise a component
that needs a code change inside software the customer does not maintain. On a third-party
platform the honest, valuable answer is usually: **their platform already ships a field for this
and it is switched off** — which costs nothing and buys the credibility for everything else.

**Never frame the result as abandoning what they built.** If they run a platform — theirs or a
third party's — the recommendation is almost never "stop using it." It is "keep it, and put these
two AgentCore components where the gaps are." An assessment that concludes with an ultimatum gets
discounted entirely, along with the findings that were correct.

## The three rules

**0. The service in front of you is not the one this skill was written against.** Every concrete
example here is `n=1`. Expect any language (Go, TypeScript, Java, Python), any framework or none,
agents defined as **declarative resources on a platform** rather than as code, work shaped as
jobs or documents rather than chat turns, and multi-agent topologies. So:

- Treat every example as an illustration of a *class* of finding, never as the expected answer.
- When a search here returns nothing, decide whether the component is **absent** or the **question
  was wrong for this stack** — those are different record entries, and conflating them
  manufactures gaps that do not exist.
- Where a practice is satisfied **by the platform** rather than by the customer's code, say so
  rather than crediting or faulting them for it.
- If this skill's vocabulary does not fit what you found, describe what you found. A forced fit
  reads as inexperience to a customer whose stack differs, and it loses the account faster than
  an admitted gap.

**1. Read and measure before asking.** Almost everything decisive is derivable from
the customer's repo and traffic: which session topology they run, their CPU-to-wall
ratio, their peak memory, image architecture, what bounds their concurrency. A
questionnaire that asks what you could have read produces a sales script. Ask only
what cannot be derived — data residency, compliance, team depth, roadmap.

**2. Tag every claim with its evidence class.** Non-negotiable.

| Tag | Means |
|---|---|
| `[measured:customer]` | Observed on **their running workload**. The only kind you may quote as theirs |
| `[measured:reference]` | Observed on this plugin's reference build — **n=1 agent**. Illustrates shape, never their number |
| `[read:source]` | **Read in their repo at `file:line`.** Most of a pre-deployment assessment is this |
| `[read:cluster]` | Read from the **live Kubernetes API** — a CR, a resource schema, RBAC, a controller's env. For a declarative platform this *is* the authoritative config store, and it **outranks `[read:source]`** wherever the two disagree |
| `[stated:customer]` | Asserted by the customer — a README, a ticket, a conversation. Often the only source for volume and spend, and not independently checkable |
| `[verified]` | Queried from a live AWS API (Service Quotas, Pricing, SDK) — **in their account, or say whose**. For a quota, also say **applied or default**: `2500 [verified: default, eu-west-1]`. Bare `[verified]` on a number that exists in both flavours leaves the reader unable to tell whose limit it is |
| `[docs]` | **AWS** documentation. Not their README — that is `[stated:customer]`, and mislabelling it presents a mid-range guess from an 8-line file as a documented fact |
| `[reasoned]` | Follows from the above — argument, not observation |
| `[open]` | Not established. Say so; do not fill the gap |

`[read:source]` and `[stated:customer]` exist because they were missing and assessors had to
choose between overclaiming (`measured`) and underclaiming (`reasoned`) for the evidence they
actually had. A Dockerfile platform flag is neither an observation of a running system nor an
inference — it is a fact read at a line number, and it is strong.

`[read:cluster]` exists for the same reason and was added later: an assessor with no tag for the
live API reached for `[measured:customer]`, which is defensible but conflates a config read with a
performance observation. Three rows on that assessment would have been **recorded wrong from
source** — concurrency safety, idempotency, and an isolation blocker that existed only at
`HEAD` — because the deployed release and the repo were different software.

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
a *stay* or a *redesign-first*: **turn duration** past the request timeout, **sidecars**, a
**non-HTTP/MCP/A2A/AG-UI protocol**, **image size**, **concurrency** past the session or
creation-rate caps, **region** availability, **custom isolation** (Kata/gVisor), **session
lifetime** past the compute type's ceiling, and the **inbound auth method**.

**Gate against a compute type, not "AgentCore".** GPU, architecture and session duration all
differ between microVMs and Instances — GPU is *not* a blocker on Instances, and treating it as
one has produced a wrong customer verdict. See [constraints.md](references/constraints.md).

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
| The knowledge store | Stays put. Forces VPC mode **only if it is VPC-resident** — check, don't assume |
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
cutover **for SigV4 callers and additive for the other three cases** (`AGENTSEC03`) — the
distinction decides whether you open with the cheapest or the most expensive news; whether the
knowledge store forces the network design depends on where it lives (`AGENTPERF03`); and tool
authorization is scoped work the migration makes available rather than delivers (`AGENTSEC02`).

**`[docs]` / `[reasoned]` — verify before asserting to a customer:**

| Area | Status |
|---|---|
| EKS-side inbound auth (ALB OIDC) | Not built, so the ALB-vs-authorizer comparison is one-sided |
| Outbound auth / token propagation to tools (3LO, token vault) | Inbound only was tested |
| Row-level authorization | Cedar blocked a *tool*; filtering *rows* by caller identity untested |
| Long-running turns via `HealthyBusy` + polling | Documented escape hatch, not demonstrated |
| Sidecar / protocol blockers | Documented constraints, not tested |
| GPU on the Instances compute type | `[docs]` — supported families confirmed in the devguide, not deployed by us |

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
- Custom isolation (Kata/gVisor), sidecars, or non-HTTP protocols. **Not GPU** — Instances supports it
- Node-level runtime threat detection (GuardDuty EKS Runtime Monitoring, Falco) with no managed equivalent
- Sessions longer than 14 days, or longer than 8 hours if microVMs are required
- Sustained high volume where reserved or Spot capacity beats per-session billing
- Deep Kubernetes expertise already paid for, and a working service
- A regulatory posture requiring everything, including the tool gateway, to be
  unreachable from the internet — not satisfiable for AgentCore Gateway `[docs]`

"It works today" is a real argument. The burden of proof is on the migration.
