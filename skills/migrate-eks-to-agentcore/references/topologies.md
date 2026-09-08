# Session topologies: the axis that decides what migration removes

The most common analytical error in this decision is comparing the wrong pair. "EKS vs
AgentCore" is not one comparison, because EKS admits two structurally different session
designs and only one of them is a fair opponent.

Classify the customer's design first. Everything downstream — what gets deleted, what
the cost delta is, what the migration risk is — follows from it.

## The three points

| | **A** stateless + store | **B** sticky + affinity | **C** AgentCore Runtime |
|---|---|---|---|
| Session state lives in | external store | pod memory | the microVM |
| Routing requirement | none, any pod | affinity, end to end | platform, via session header |
| Per-turn overhead | store I/O + agent rebuild | none | none |
| Cold start | none (pod warm) | none when affinity holds | microVM start |
| Rollout | safe | **destroys live conversations** | old version drains |
| Concurrent-turn race | last-write-wins on the store | shared `messages` list | serial within a session |
| You operate | store, TTL, serialization, races | router, capacity, eviction, flush, drain | session-id issuance only |

**Nobody runs pod-per-session.** EKS cold start measured **61s** `[measured]` — apply
to ready, including Karpenter provisioning a Graviton node (node at 23s, pods at 61s).
That is why a pod cannot be a session, and it is the structural reason AgentCore's
microVM-per-session model has no Kubernetes equivalent.

## Why B vs C is the honest comparison

**A vs C overstates AgentCore's benefit**, because it credits the platform choice with
the entire cost and complexity of the session store — which a team could have avoided
on EKS by choosing B.

**B vs C is the defensible argument.** Both keep the agent warm in memory, keyed by
session. Same latency profile. The question becomes: *who operates the router, the
capacity model, the eviction policy, the flush cadence and the drain?* On C, the
platform. On B, you.

That is a real and substantial argument. It does not need exaggeration, and a skeptical
customer will take the A-vs-C version apart.

## First, a pre-check: is a unit of work a user *turn*?

Ask this before classifying, because the taxonomy below assumes conversational traffic and will
confidently mis-classify a batch service — every topology-A signature matches a long-running job
that checkpoints to a store, and then every A verdict is wrong.

**Fourth point: long-running resumable job.** A unit of work is a document, a dataset or a task,
not a turn. Signals: a loop over work items rather than a request handler; checkpoint/resume
keyed on a job id; `terminationGracePeriodSeconds` in the hundreds; no conversation history.

For that shape the A verdicts invert:

| A verdict | Reality for a batch job |
|---|---|
| "delete the store, it is per-turn overhead" | **the store IS the reliability mechanism** — it is the resume point when the pod dies |
| "removes ~110ms/turn of rebuild" | there is no per-turn rebuild; the agent is built once per job |
| "risk: low, also a latency improvement" | deleting the checkpoint store removes crash recovery |

The schema fields inherit the bug too: `flush_cadence`, `concurrent_turn_safety` and
`turns_per_conversation` all make the record read as a chat agent. Record
`work_unit: turn | job | document` first and let it gate the rest.

## Detecting which one they run

**Topology A signatures**
- A session store class with `load`/`save` called per turn (DynamoDB, Redis,
  ElastiCache)
- The agent object constructed per request from loaded history
- `replicas: N` with no affinity annotation and a plain `ClusterIP` Service
- Conversation history serialized as JSON in a table or key

**Topology B signatures**
- A dict or LRU keyed by `session_id` holding live agent objects
- `alb.ingress.kubernetes.io/target-group-attributes: stickiness.enabled=true`, a
  consistent-hash router, a headless Service, or a StatefulSet
- Eviction logic, idle timeouts, or a `max_sessions` cap
- A `preStop` hook that persists state

**Single-turn signatures** — no store, no cache, history supplied in the request. The
easiest migration by a wide margin; say so.

## What topology A actually costs per turn

Measured in-cluster, and the result corrects the usual framing `[measured]`:

| Component | Time |
|---|---|
| Session store fetch (DynamoDB, in-region) | **11.5 ms** |
| **Agent reconstruction** | **98 ms** |

The rebuild is **8.5× more expensive than the session fetch**. Topology A is normally
described as paying "a session store round trip" per turn; that understates it by an
order of magnitude. Anyone optimising the store to fix stateless latency is optimising
the wrong 5% — the cost is reassembling the agent (tool registration, model client
construction).

A caution on measuring this: local measurements gave 1,048 ms of apparent store I/O.
That was internet latency to DynamoDB plus thread-pool queueing, not real cost. Measure
in-cluster, in-region.

## What topology B actually costs to operate

Each item is a number a human picks, cannot derive, and pays for when wrong. AgentCore
supplies all of them.

**Affinity has to hold end to end, and an ALB cannot do it for a non-browser client.**
ALB stickiness is a cookie. If the agent is invoked by another service over MCP — a
common shape — the cookie is meaningless. Affinity must key on a session header, and
nothing in an ALB routes on a header value. You need a router tier that resolves pod
IPs and hashes the session id, which means a headless Service and your own
consistent-hash ring that rebalances on every scale event.

**Capacity.** Each cached conversation holds full history, which grows per turn, so
per-session memory is effectively unbounded and the cap is a guess. Too high is an
OOMKill; too low evicts live conversations.

**Idle eviction.** The analogue of `idleRuntimeSessionTimeout`, except you pick the
number.

**Rollout.** A deploy replaces every pod. AgentCore versions the runtime and lets
running sessions finish on the old version; a Kubernetes rolling update has no concept
of a conversation. `preStop` plus a long `terminationGracePeriodSeconds` protects
in-flight *requests*, not in-flight *conversations*.

**Ungraceful exit.** `preStop` does not run on OOMKill, node loss, or a drain that
outran the grace period. A process holding N growing conversations is an OOMKill
candidate by construction — so the exits that lose data are the likely ones.

**Hot shards.** Consistent hashing spreads session ids evenly, not *load*. A deep tool
traversal costs many times a one-shot lookup, so pods diverge in utilization and the
fleet gets sized for the unluckiest pod.

**Karpenter consolidation.** Cost-effective node consolidation moves pods, and moving a
pod destroys the conversations in its memory. `consolidateAfter` becomes a
conversation-length guess.

## Flush cadence is a decision in every topology, including C

The tempting story — "A flushes every turn, B never flushes" — is wrong, and the error
flatters AgentCore.

A sticky pod usually has to persist, because a user who closes the tab and returns tomorrow has
no pod to come back to. But **check rather than assert it** — real teams ship `none` on purpose.
Observed in a production service, in a comment: *"the conversation restarts — we accept that
today."* Telling that team they "have to" persist argues with a decision they already made.
The axis is not *whether* but *when*:

| Mode | Writes/session | Turns lost to a hard kill |
|---|---|---|
| `every_turn` | one per turn | 0 |
| `every_n_turns` | turns/N | up to N-1 |
| `interval` | wall-time bounded | everything since last flush |
| `on_evict` | one per session | **the whole conversation** |
| `none` | **zero** — nothing is persisted at all | the whole conversation, on every restart, by design |

`on_evict` means "durable unless the process dies badly" — for a process that dies
badly. A periodic flush is what closes the hole, which makes `interval` or
`every_n_turns` the honest sticky default rather than `on_evict`.

**On C there is no deferred-flush option at all**, because there is no evict hook —
containers are hard-killed, so `AgentCoreMemorySessionManager` has to write every turn.
That makes C's short-term memory writes topology A's volume, not naive B's — which is a
cost consequence, not a footnote.

Why that is forced, and what it costs, are in
[deploy-on-agentcore/references/cost-and-billing.md](../../deploy-on-agentcore/references/cost-and-billing.md)
and `runtime-and-sessions.md`. The migration point is narrower: **whichever flush cadence
the customer runs today, migrating pins it to every-turn.** If they are on `on_evict`,
that is a write-volume increase to price; if they are already `every_turn`, it is a
wash. **If they are on `none`, it is a durability *improvement* they did not ask for** — a
write-volume increase from zero, and the one case where the migration makes the service
behave differently in a way a user would notice. Do not report it as a cost regression
without saying it is also the fix for a known accepted loss.

## Migration implications by topology

| From | What migration deletes | Risk |
|---|---|---|
| **A** | store, TTL policy, per-turn load/save, rebuild cost, write races | Low. Also a latency *improvement* — 110ms/turn of overhead disappears |
| **B** | router, hash ring, capacity cap, eviction, drain, rollout data loss | Low-medium. Behaviourally closest to C; verify session-header mapping |
| **single-turn** | almost nothing — there was no session layer | Lowest |

In all three, what does **not** transfer: session-id issuance and binding it to an
authenticated user. AgentCore does not map users to sessions `[docs]`. The store
disappears; the session *broker* does not.

## A second axis for multi-agent services: how many runtimes?

The three topologies above are about *session* state and say nothing about a supervisor
delegating to specialists. That is an independent decision, and it has to be made before any
runtime is created because it determines the IAM and network shape.

**Be careful with the first argument you reach for.** On EKS, splitting specialists into separate
Deployments buys blast-radius and noisy-neighbour isolation. **Session** isolation is free on
AgentCore — a microVM per session provides it by construction — so a case built only on that
evaporates on the target platform.

**But do not over-apply it: session isolation is not tool-scope isolation.** Measured on a real
multi-agent service, this correction mattered more than the original point. Two specialists held
*different* tool sets; collapsing them into one runtime unions those sets **inside one microVM**,
so every tool becomes reachable from every prompt. A microVM boundary does nothing about that,
because the boundary is between *sessions*, not between *capabilities*. An assessor who dismissed
the separation case lost a real finding.

**The scope argument, stated carefully.** One runtime hosting all specialists in-process holds the
union of their permissions — at the tool layer always, and at the IAM layer *if* their roles
actually differ. **Check that they do** rather than assuming: observed three distinct
ServiceAccounts all associated to **one** IAM role, so the IAM regression had already happened
before any migration was discussed, and the only live regression was at the tool layer. Record it
as `regress` where it is real, and where the roles are already shared, record that as its own
finding — `available_unconfigured`, since the platform gave them per-agent ServiceAccounts and
nobody scoped the roles.

| | One runtime, agents-as-tools | One runtime per agent |
|---|---|---|
| Execution roles | 1, holding the union | N, each scoped |
| Session workloads per conversation | 1 | **N**, and the multiplier is `[open]` — see below |
| Memory billing | one peak | each runtime bills its own wall-clock, and a specialist's clock runs *inside* the supervisor's |
| Delegation | in-process call | `InvokeAgentRuntime` — a tool-set change, plus an endpoint if VPC-resident |
| Per-component rollback | no | yes, and it is what makes a phased cutover possible |

Neither is a default. Cost the options rather than inheriting the current shape — and if the
record leaves it open, say the plan *chose* it and flag it, because it is not reversible after
create.

### The session-workload multiplier: what to record, since it cannot be derived

An earlier version of this file said to "check N against the account quota" without defining the
unit, which is not actionable — **"session workload" is not defined in this skill**, and three
questions decide the number:

- does in-process fan-out to a sub-agent consume more than one?
- does a sub-agent invoked with the **same** session id share one, or mint another?
- can one microVM host concurrent runs of the same session?

None is answerable from the docs this plugin cites. So: derive the arithmetic from the
**delegation fan-out** you can see, tag it `[reasoned]`, and mark the unit `[open]` rather than
implying a verified capacity figure. Two things you *can* state:

- **Per-delegation session isolation multiplies the creation rate, not just the cap.** Where a
  delegation mints a fresh session per *call* rather than per conversation, the binding limit
  becomes the new-session **rate**, and it scales with tool-call volume rather than user volume.

  **Read that as a capacity note, never as a reason to turn isolation off.** Stated alone it is
  an argument for sharing sessions, and sharing is the more dangerous default by a wide margin.
  Measured on a real multi-agent service: with isolation off, one sub-agent session was shared
  across **every conversation and every user** reaching that pod — and the specialist answered a
  brand-new conversation from another conversation's history, reporting infrastructure that did
  not exist. So:

  | Per-delegation isolation | Costs | Risks |
  |---|---|---|
  | **on** | more sessions, and the creation *rate* becomes the binding quota | none of the below |
  | **off** | fewer sessions | **cross-conversation and cross-tenant history bleed**, and answers served from another conversation's state — which presents as confident, well-formatted, wrong |

  Isolation is the safe default. Treat the quota as a thing to raise, not a reason to share
  state, and if a customer has it off, check whether a shared session spans tenants before
  anything else — that is a confidentiality finding, not a tuning choice.
- **Shadow traffic inherits the same multiplier**, so a sampled share of mirrored conversations
  costs sample × N — which is the number to check before mirroring, not after.
