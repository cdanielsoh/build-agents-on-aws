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

A sticky pod still has to persist, because a user who closes the tab and returns
tomorrow has no pod to come back to. The axis is not *whether* but *when*:

| Mode | Writes/session | Turns lost to a hard kill |
|---|---|---|
| `every_turn` | one per turn | 0 |
| `every_n_turns` | turns/N | up to N-1 |
| `interval` | wall-time bounded | everything since last flush |
| `on_evict` | one per session | **the whole conversation** |

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
wash.

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

**Reject the argument you will reach for first.** On EKS, splitting specialists into separate
Deployments buys blast-radius and noisy-neighbour isolation. On AgentCore **that isolation is
already free** — a microVM per session isolates by construction — so a "keep them separate for
isolation" case built on their current Deployment layout evaporates on the target platform. A
plan that leads with it is arguing from the source architecture.

**The argument that survives is IAM scope.** One runtime hosting all specialists in-process means
one execution role holding the union of every specialist's permissions — a log-reading agent that
can also query the knowledge base and call every model. If they run N ServiceAccounts today,
collapsing to one runtime is a **regression** in least privilege, and rebuilds a confused deputy
inside the process. Record it as `regress` rather than letting the platform move quietly widen a
boundary.

| | One runtime, agents-as-tools | One runtime per agent |
|---|---|---|
| Execution roles | 1, holding the union | N, each scoped |
| Session workloads per conversation | 1 | **N** — check against the account quota, and it multiplies shadow traffic too |
| Memory billing | one peak | each runtime bills its own wall-clock, and a specialist's clock runs *inside* the supervisor's |
| Delegation | in-process call | `InvokeAgentRuntime` — a tool-set change, plus an endpoint if VPC-resident |
| Per-component rollback | no | yes, and it is what makes a phased cutover possible |

Neither is a default. Cost the options rather than inheriting the current shape — and if the
record leaves it open, say the plan *chose* it and flag it, because it is not reversible after
create.
