# Blockers and constraints — a migration-decision index

**This file deliberately holds no platform documentation.** AgentCore's quotas, limits,
auth model, policy semantics and network requirements live in **deploy-on-agentcore**,
which is the single source for them. Duplicating them here would guarantee the two
diverge, and the copy that drifts is the one someone quotes at a customer.

What this file adds is the *migration* framing: which constraints are decision gates,
what each one costs to resolve, and where to read the detail.

## Gate 0 — check these before anything else

Cheap, binary, and each can end the conversation before a week is spent. Read the source
column for the mechanism; the point here is the verdict.

**Thresholds are not reproduced here, and the reason is stronger than "they drift."**

Quotas vary **by region**, not just by account. Verified: `Active Session Workloads per
Account` is **2500 in ap-northeast-2** and **5000 in us-east-1 / us-west-2**. A customer in
Seoul has half the headroom of the same account in Virginia. Quote the wrong one and you
either invent a blocker or clear a real one.

So read them live, in **the customer's region**, from `list-aws-default-service-quotas`
*and* `list-service-quotas` (applied values differ from defaults). Values and mechanisms live
in `runtime-and-sessions.md`.

Also watch for near-name collisions: `Tool-call/tool-list concurrent connections` is also
5000 and is a **Gateway** quota, not a runtime session cap. Reading the wrong row is an easy
mistake and it was made in an earlier version of this skill.

| Check | Cost to resolve | Detail |
|---|---|---|
| **Single-turn duration** past the request timeout | Redesign as async + `HealthyBusy` polling. Real work, and the timeout is **not adjustable** | `runtime-and-sessions.md` |
| **Image architecture** — amd64-only dependency | Usually a rebuild. Cheap. Do it first | `runtime-and-sessions.md` |
| **Image size** over the cap | Slim the image. Rarely binding — a real agent image measured 482 MB | `runtime-and-sessions.md` |
| **GPU** / local inference | Not resolvable. Stay on EKS | `runtime-and-sessions.md` |
| **Sidecars** required | Restructure, or stay | `runtime-and-sessions.md` |
| **Protocol** not HTTP / MCP / A2A / AG-UI | Front it with HTTP, or stay | `runtime-and-sessions.md` |
| **Concurrency** past the session or creation-rate caps | Quota increase. **Lead time, not a wall** — raise it during assessment | `runtime-and-sessions.md` |
| **Region** absent | Probe with `list-agent-runtimes`; published lists have been stale | `runtime-and-sessions.md` |
| **Inbound auth method** — callers use SigV4, target is JWT | **Breaking.** See below | `identity.md` |
| **VPC-resident knowledge store** | Not a blocker, but forces VPC mode and a full endpoint set | `vpc-and-network-isolation.md` |

## The four that people get wrong

Everything below is *why it matters for a migration decision*. The mechanism is in the
referenced file.

### 1. Inbound auth is a breaking change, not an additive one

`CUSTOM_JWT` **excludes** SigV4 on that runtime — a SigV4 caller gets 403 once a JWT
authorizer is configured. The authorizer is a property of the runtime, not the endpoint.

**Migration consequence:** if the customer's callers sign with SigV4 today (typical for
service-to-service on EKS with Pod Identity) and the target is JWT, that is a coordinated
cutover of every caller, or **two runtimes in parallel** with routing deciding who has
moved. Belongs in Phase 1 of the plan, not Phase 3.

→ `deploy-on-agentcore/references/identity.md`

### 2. The knowledge store decides the network design

If the agent reasons over something VPC-resident with no public endpoint, that is the
forcing function for the whole network section — not an implementation detail. On EKS the
pod already has an ENI in the VPC and reaching it is free. On AgentCore it forces
`networkMode = "VPC"`, and once there is no internet route **every** other dependency
needs an endpoint.

**Migration consequence:** the network workstream is sized by this one fact. Scope it
during assessment, because the failure mode is a runtime hang with nothing in the logs
naming what was unreachable — not a failed deploy.

→ `deploy-on-agentcore/references/vpc-and-network-isolation.md`

### 3. "Nothing reachable from the internet" is not satisfiable

The Gateway cannot be placed in a VPC. Cognito and Memory are managed regional endpoints.
You get private *reachability*, not private *placement*.

**Migration consequence:** if the customer has this requirement, surface it in the
assessment and record it as a known gap in the decision record. Discovering it during
their security review costs the project credibility that the rest of the analysis
depends on.

→ `deploy-on-agentcore/references/vpc-and-network-isolation.md`

### 4. Authentication is not authorization

A working JWT authorizer with no policy engine lets any valid-token holder invoke every
tool, including destructive ones. Cedar at the Gateway boundary is what constrains *what*
a caller may do.

**Migration consequence:** this is a genuine architectural choice the migration makes
*available*, not a free win it delivers. Present it as scoped work — schema, policies,
`LOG_ONLY` then `ENFORCE` — and note that whatever tool-authz the customer has today
keeps working if they defer it.

→ `deploy-on-agentcore/references/policy.md`

## Streaming ceilings: the one genuine two-sided comparison

Kept here because it is the only constraint where the *EKS* side is the interesting half.

| | EKS + ALB | AgentCore |
|---|---|---|
| Ceiling | `idle_timeout`, default **60s** | **15 min** request timeout |
| Applies to | gap between bytes | total request |
| Adjustable | yes | **no** |
| Failure shape | LB closes the stream, pod logs nothing | 504 |

The ALB default is the nastier one, and it is often already broken in the customer's
current deployment: an agent that thinks for 70s before its first token has its stream
closed while the application logs a healthy request. Worth checking during assessment —
finding it is immediate value independent of any migration.

Fixed with `idle_timeout.timeout_seconds` and `X-Accel-Buffering: no`, which raises a
ceiling rather than removing one. AgentCore's 15 minutes is stricter but explicit.

## Verify observed state, never the exit code

Not an AgentCore property — a pattern observed three separate times across both platforms,
and worth encoding as a verification step in every migration phase.

| Observed | What it looked like |
|---|---|
| `eksctl create cluster` exited **0** | Log reported a CloudFormation rollback; no cluster existed |
| `kubectl apply` reported **created** | Pod Security violation forbade all pods; Deployment existed, zero replicas ran |
| `create-policy` returned **200** | Policy later reached `CREATE_FAILED`; reason only in `statusReasons` |

Each would pass a CI step that checks a return code. Every phase in
[playbook.md](playbook.md) therefore verifies behaviour, not command success.
