# Provenance of this skill's own claims

Which parts to lean on, and which to verify with the customer before asserting. The guidance was
validated by deploying one agent codebase to both EKS and AgentCore Runtime and measuring. **That is
n=1** — see the generalization rule in [SKILL.md](../SKILL.md).

## `[measured]` on a real deployment of both platforms

Session-store deletability (a singleton agent held 3-turn context with no store), microVM session
start ~1.96s (`[measured:reference]`, n=3, one prompt), CPU/wall ratio and per-turn CPU, peak memory,
the agent-rebuild vs store-I/O split, the cost model, ARM64/NodePool behaviour, event-loop blocking
impact, MCP portability, **inbound JWT auth on the runtime**, **Gateway with a Lambda target**, and
**Cedar policy enforcement at the Gateway boundary**.

Those measurements were fed back into **deploy-on-agentcore**, which owns the platform detail —
inbound JWT in `identity.md`, Cedar enforcement and validation behaviour in `policy.md`, session and
quota behaviour in `runtime-and-sessions.md`, the billing model in `cost-and-billing.md`. This skill
points at them rather than restating them, so there is one place to correct when the platform moves.

## The three that change migration planning specifically

In [constraints.md](constraints.md) and [production-inventory.md](production-inventory.md):

- **Inbound auth is a breaking cutover for SigV4 callers and additive for the other three cases**
  (`AGENTSEC03`). The distinction decides whether you open with the cheapest or the most expensive
  news.
- **Whether the knowledge store forces the network design depends on where it lives**
  (`AGENTPERF03`).
- **Tool authorization is scoped work the migration makes available rather than delivers**
  (`AGENTSEC02`).

## `[docs]` / `[reasoned]` — verify before asserting to a customer

| Area | Status |
|---|---|
| EKS-side inbound auth (load-balancer OIDC) | Not built, so the ALB-vs-authorizer comparison is one-sided |
| Outbound auth / token propagation to tools (3LO, token vault) | Inbound only was tested |
| Row-level authorization | Cedar blocked a *tool*; filtering *rows* by caller identity untested |
| Long-running turns via `HealthyBusy` + polling | Documented escape hatch, not demonstrated |
| Sidecar blocker | `[reasoned]` from the single-container-URI contract, not documented — see [constraints.md](constraints.md) |
| Inbound protocol list (HTTP / MCP / A2A / AG-UI) | `[docs]` — each has a published protocol contract; cited in [constraints.md](constraints.md) |
| GPU on the Instances compute type | `[docs]` — supported families confirmed in the devguide, not deployed by us |

For outbound auth and row-level filtering, defer to **deploy-on-agentcore** (`identity.md`,
`policy.md`, `security.md`) and say plainly that the migration effort for those components is an
estimate.

## One process note worth inheriting

Four of the Gateway role permissions were rediscovered the hard way, one error at a time, when they
were already documented correctly in `deploy-on-agentcore/references/policy.md`. **Read the existing
references before building anything.**
