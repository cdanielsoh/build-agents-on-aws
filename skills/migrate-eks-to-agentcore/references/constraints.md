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

### Gate 0 is per-compute-type. Check which one you are gating against.

**This was wrong in earlier versions and it inverted a customer verdict.** AgentCore Runtime
has two compute types and the constraint set differs:

| | microVMs (default) | **Instances** |
|---|---|---|
| Max session | 8 h | **14 days** |
| Architecture | **arm64 only** | **x86_64 and arm64** |
| GPU | not supported | **supported** — `g4dn, g5, g6, g6e, gr6, g6f, gr6f, g7e`, `inf2`. Drivers provisioned; stock CUDA images work `[docs]` |
| Networking | `PUBLIC` or VPC | **VPC only** |
| Agents per session | 1:1 | **1:N**, shared filesystem |
| Persistent storage | session storage | **EBS re-attached across session stops** |
| Pricing | consumption, billed by AgentCore | **EC2 in your account** — your Savings Plans, RIs, ODCRs apply |

So **three Gate 0 rows flip on Instances**: GPU, architecture, and session duration. Gating a
GPU workload as "not resolvable" is wrong; the honest answer is *possible but expensive, and
it becomes an EC2 cost conversation rather than a consumption one*. Compute type cannot be
changed after a runtime is created.

| Check | Cost to resolve | Detail |
|---|---|---|
| **Single-turn duration** past the request timeout | Redesign as async + `HealthyBusy` polling. Real work, and the timeout is **not adjustable** | `runtime-and-sessions.md` |
| **Image architecture** — amd64-only dependency | On microVMs: a rebuild (cheap, do it first). On Instances: **not an issue**, x86_64 is supported | `runtime-and-sessions.md` |
| **Image size** over the cap | Slim the image. A plain Strands agent measured 482 MB — but **any CUDA/ML base image blows the 2 GB cap on its base layer alone** (`nvidia/cuda:12.4-cudnn-runtime` is ~2.1 GB compressed). "Rarely binding" is false for exactly the workloads that need GPU | `runtime-and-sessions.md` |
| **GPU** / local inference | **Not** a blocker — use the **Instances** compute type (supported families above). Blocker only if you require microVMs for another reason | `runtime-and-sessions.md` |
| **Sidecars** required | Restructure, or stay | `runtime-and-sessions.md` |
| **Protocol** not HTTP / MCP / A2A / AG-UI | Front it with HTTP, or stay | `runtime-and-sessions.md` |
| **Concurrency** past the session or creation-rate caps | Quota increase. **Lead time, not a wall** — raise it during assessment | `runtime-and-sessions.md` |
| **Region** absent | Probe with `list-agent-runtimes`; published lists have been stale | `runtime-and-sessions.md` |
| **Inbound auth: in-app JWT validation** | **Like-for-like, the cheapest case.** The platform authorizer replaces ~7 lines of verification. Claim *extraction* stays yours | `identity.md` |
| **Inbound auth: SigV4 today, JWT target** | **Breaking.** Coordinated caller cutover, or two runtimes | `identity.md` |
| **Inbound auth: NONE today** | **Not breaking — additive greenfield.** Simultaneously the highest-severity *current* gap | `identity.md` |
| **Inbound auth: proxy-terminated mTLS** (Envoy/Istio sidecar) | **Loss of a compliance control.** Runtime offers SigV4 or CUSTOM_JWT only; there is no mTLS equivalent, and the sidecar's outbound policy goes too. A security-team decision, not an engineering one | `identity.md` |
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

**Four cases, and only one of them is breaking. Establish which before saying the word.**
Leading with "BREAKING" against a service that already validates a JWT in-process is simply
wrong, and it is the cheapest case of the four.

**Check for the no-auth case, because it inverts the framing.** A service with **no inbound
authentication at all** — internal ClusterIP, no Ingress, trusting the network — is common and
was the case on the customer service assessed here. Then `CUSTOM_JWT` is **additive greenfield
work, not a cutover**: there is no existing credential to migrate, and the migration framing is
the opposite of breaking.

It is also the highest-severity finding in the current system. On that service it was
exploitable: replaying another conversation's `session_id` returned that conversation's content,
because `session_id` is accepted verbatim from the caller and the `X-Employee-Id` header their
own clients already send is never read. **Report that as a live vulnerability, not as a
migration consideration.**

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

### 3. "Nothing reachable from the internet" — private *reachability* yes, private *placement* no

Earlier versions said this was "not satisfiable", which is **wrong and manufactured a blocker
for exactly the regulated customer who needs the answer.** A Gateway interface endpoint
exists `[verified]`:

```
com.amazonaws.<region>.bedrock-agentcore.gateway
  → *.gateway.bedrock-agentcore.<region>.amazonaws.com
```

plus `bedrock-agentcore` and `bedrock-agentcore-control`. So a customer whose control is "all
egress via interface endpoints, audited" **can** satisfy it for the data path.

What remains true is narrower: `CreateGateway` has no network *placement* field, so the
Gateway is a regional service reached privately rather than a resource inside your VPC. Its
protection is the JWT authorizer, Cedar policies and optionally WAF — not its network
position. State that distinction; do not state "not satisfiable".

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

AgentCore has **three** non-adjustable duration ceilings, not one. Earlier versions of this
file documented only the first, which made the async escape hatch look unconstrained:

| Ceiling | Value | Quota code | Applies to |
|---|---|---|---|
| Request timeout | **15 min** | `L-3ED45A13` | one synchronous request |
| **Streaming maximum duration** | **60 min** | `L-C91AC63F` | a streaming response — the operative cap for SSE/WebSocket |
| **Asynchronous job maximum duration** | **8 h** | `L-FDE792EE` | the `HealthyBusy` background-task pattern |

All three `[verified]` non-adjustable in us-east-1. **The async redesign this file recommends
as the escape hatch is itself capped at 8 hours** — say so, or a customer redesigns into a
second wall.

Against EKS:

| | EKS + ALB | AgentCore |
|---|---|---|
| Ceiling | `idle_timeout`, default **60s** | 15 min request / 60 min streaming / 8 h async |
| Applies to | gap between bytes | total request / stream / job |
| Adjustable | yes | **no, none of the three** |
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
