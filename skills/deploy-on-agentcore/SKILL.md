---
name: deploy-on-agentcore
description: >
  Build production-grade AI agents on AWS Bedrock AgentCore with Strands Agents,
  MCP Gateway, Lambda-based MCP servers, and CDK infrastructure. Use this skill
  whenever the user wants to build an agent on AgentCore, connect tools via MCP Gateway,
  set up OAuth authentication for agents, add conversation memory with AgentCore Memory,
  deploy Lambda-based MCP servers, understand the AgentCore streaming/interrupt protocol,
  create secure agent architectures with row-level security, or use Strands Agents SDK
  with BedrockAgentCoreApp. Trigger on mentions of: AgentCore, Strands Agents,
  MCP Gateway, AgentCore Memory, BedrockAgentCoreApp, CfnRuntime, CfnGateway,
  or agent + Lambda + MCP patterns. Also trigger when users ask about
  multi-layer authorization in agent systems or token propagation chains.
  Trigger on outbound authorization topics: AgentCore Identity, 2LO, 3LO,
  two-legged or three-legged OAuth, client credentials vs authorization code,
  USER_FEDERATION, M2M auth flow, token vault, workload identity,
  OAuth2 credential provider, requires_access_token, requires_api_key,
  CompleteResourceTokenAuth, GetResourceOauth2Token, per-user downstream
  tokens, offloading a hand-rolled OAuth implementation, or connecting an
  agent to ServiceNow / Okta / Google / GitHub / Salesforce on a user's behalf.
  Trigger on authorization-policy topics: AgentCore Policy, policy engine,
  Cedar policy, Cedar schema, permit/forbid statements, AgentCore::OAuthUser,
  AgentCore::Gateway, tool-level authorization, fine-grained access control
  for tools, LOG_ONLY vs ENFORCE, enforcementMode, policy generation,
  LogOnlyDecisionFlips, or guardrails in policy.
  Trigger on evaluation topics: AgentCore Evaluations, built-in evaluators,
  Builtin.Helpfulness, Builtin.GoalSuccessRate, trajectory match evaluators,
  online / on-demand / batch / dataset evaluation, custom evaluator,
  code-based evaluator, LLM-as-a-judge for agents, evaluating a deployed
  agent from traces, agent quality monitoring, DeepEval or AutoEval on
  AgentCore, simulated scenarios, actor profile, or convert_strands_to_adot.
  Trigger on observability topics: AgentCore Observability, ADOT,
  aws-opentelemetry-distro, opentelemetry-instrument, unified vs split
  telemetry, UNIFIED_TRACES_DESTINATION_ENABLED, CloudWatch Transaction
  Search, aws/spans, otel-rt-logs, runtime-logs, gen_ai semantic conventions,
  invoke agent / inference / execute tool spans, custom spans for agents,
  or missing agent logs and traces.
---

# Building Agents on AWS Bedrock AgentCore

> **Validated against `bedrock-agentcore` 1.22.0 and `aws-cdk-lib` 2.267.0** (September 2026).
> Grant types, tool shapes, and evaluator IDs were checked against the live
> `bedrock-agentcore-control` API model and the AWS devguide. If your version differs, verify
> before trusting these APIs.
>
> **CDK is fully current.** What AWS deprecated is the Python
> `bedrock-agentcore-starter-toolkit`, not CDK — the `agentcore` CLI's own `deploy` command is
> documented as deploying "via CDK", so CDK is the mechanism underneath, not the thing being
> replaced. `aws_cdk.aws_bedrockagentcore` carries L1 constructs for every component in this
> skill: `CfnRuntime`, `CfnRuntimeEndpoint`, `CfnGateway`, `CfnGatewayTarget`, `CfnMemory`,
> `CfnPolicyEngine`, `CfnPolicy`, `CfnEvaluator`, `CfnOnlineEvaluationConfig`, `CfnDataset`,
> `CfnOAuth2CredentialProvider`, `CfnApiKeyCredentialProvider`, `CfnTokenVault`,
> `CfnWorkloadIdentity`, `CfnResourcePolicy`, `CfnBrowser`, `CfnCodeInterpreter`.
>
> Choose **CDK** when AgentCore resources live alongside existing infrastructure (VPC, Aurora,
> Lambda, IAM) — the common enterprise case. Choose **`agentcore.json` + `agentcore deploy`**
> when the agent project is self-contained. The `bedrock-agentcore` SDK is the runtime library
> either way.

This skill covers the full architecture for deploying AI agents on Bedrock AgentCore — from the agent container through MCP Gateway to Lambda-based tool servers, with OAuth authentication, security patterns, and CDK infrastructure.

## Architecture

```
Client / API Backend
  |
  v
AgentCore Runtime (Container)      -- Strands Agent, singleton session, streaming
  |
  v
MCP Gateway                        -- Single endpoint, OAuth validation, tool routing
  |              |
  v              v
Lambda MCP A   Lambda MCP B        -- FastMCP tools, domain-specific servers
  |              |
  v              v
Data Store(s)                      -- DynamoDB, RDS, or existing APIs
```

**Auth flow**: Cognito JWT --> AgentCore OAuth authorizer --> Gateway interceptor --> propagated headers to Lambda targets --> backend authorization

**Runtime model**: One container per session (VM-per-session). No multi-session management needed.

**Assumption**: Backend APIs and data layer already exist. This skill covers the agent layer built on top.

## When to Read What

| You want to...                                     | Read                              |
|----------------------------------------------------|-----------------------------------|
| Set up the AgentCore Runtime container              | `references/runtime-and-sessions.md` |
| Understand VM-per-session model, singleton pattern  | `references/runtime-and-sessions.md` |
| Configure ADOT observability or IAM permissions     | `references/runtime-and-sessions.md` |
| Create MCP Gateway with Lambda targets              | `references/gateway-and-mcp.md`      |
| Write Lambda MCP server handlers                    | `references/gateway-and-mcp.md`      |
| Implement row-level security / multi-layer auth     | `references/security.md`             |
| Understand the token propagation chain              | `references/security.md`             |
| Integrate AgentCore Memory for persistence          | `references/agentcore-memory.md`     |
| Call a downstream API as the agent itself (2LO)     | `references/identity.md`             |
| Call a downstream API as the end user (3LO)         | `references/identity.md`             |
| Offload OAuth (PKCE, state, code exchange, refresh) | `references/identity.md`             |
| Set up an OAuth2 credential provider / token vault  | `references/identity.md`             |
| Store per-user downstream tokens                    | `references/identity.md`             |
| Authorize individual tool calls with Cedar          | `references/policy.md`               |
| Constrain tool arguments (refund ceilings, scoping) | `references/policy.md`               |
| Shadow-test an authorization rule on real traffic   | `references/policy.md`               |
| Generate Cedar policies from natural language       | `references/policy.md`               |
| Score a deployed agent from its traces              | `references/evaluations.md`           |
| Monitor production agent quality continuously       | `references/evaluations.md`           |
| Run a batch regression audit over past sessions     | `references/evaluations.md`           |
| Build a dataset of predefined or simulated scenarios | `references/evaluations.md`          |
| Write a custom or code-based evaluator              | `references/evaluations.md`           |
| Set up ADOT tracing, spans, and CloudWatch          | `references/observability.md`         |
| Choose between unified and split telemetry          | `references/observability.md`         |
| Debug missing logs, traces, or evaluation sessions  | `references/observability.md`         |
| Add custom spans and attributes                     | `references/observability.md`         |
| Instrument an agent hosted outside AgentCore        | `references/observability.md`         |
| Write CDK stacks for Runtime, Gateway, Backend      | `references/cdk-infrastructure.md`   |
| Understand AgentCore streaming event format          | `references/streaming-backend.md`    |
| Implement the interrupt resume protocol              | `references/streaming-backend.md`    |

## Key Patterns (Summary)

### 1. AgentCore Runtime (VM-per-Session)

The agent runs as a Docker container on AgentCore Runtime (serverless). **Each container serves exactly one session** — no multi-session management, no thread-safety concerns. Use `BedrockAgentCoreApp` as the entrypoint with an `@app.entrypoint` async generator that yields streaming events.

- `SessionBuilder` created at startup (lifespan), `Session` built lazily on first request
- Extract OAuth token from `context.request_headers['Authorization']`
- Get session ID from `context.session_id`
- **No graceful shutdown** — containers are hard-killed (no SIGTERM, no atexit)

### 2. Session + SessionBuilder

**Session** is a thin runtime container — it holds the Agent and the shared headers dict, but doesn't create its own dependencies.

**SessionBuilder** handles all construction: MCP client, tools, hooks, memory, agent assembly. Each `_build_*` method can be overridden in a subclass to customize one concern without rewriting the full pipeline.

The **mutable headers pattern** is key: the builder creates a headers dict and passes it to both the MCPClient transport and the Session. When `refresh_token()` mutates the dict, the transport sees the new value automatically.

### 3. MCP Gateway

A single Gateway endpoint replaces N separate MCP server connections:
- Lambda targets behind Gateway (pay-per-invocation, no idle cost)
- Tool schemas declared in CDK with typed input schemas
- **Interceptor Lambda** extracts Authorization header from request and forwards to targets
- OAuth validation at Gateway level via `CUSTOM_JWT` with Cognito OIDC discovery URL

### 4. Lambda MCP Servers

Two deployment patterns:

- **Direct (greenfield)**: Lambda contains business logic + talks directly to database. Fewer hops, lower latency. Use for new builds.
- **Adapter (brownfield)**: Lambda wraps an existing API. Reuses existing business logic but adds a network hop. Use when a backend already exists.

Both use FastMCP for tool definitions and a handler that translates Gateway invocations.

### 5. Security

**Multi-Layer Authorization**: JWT validated at every boundary (Runtime, Gateway, API). Database-level enforcement as defense-in-depth:
- DynamoDB: user ID as partition key + IAM `dynamodb:LeadingKeys`
- PostgreSQL: Row-Level Security (RLS) policies
- Even a fully compromised agent can only access the authenticated user's data

**Token Propagation Chain**: OAuth token flows through the entire system — Client → Backend → Runtime → Gateway → Lambda → Database — validated independently at each hop.

### 6. Identity — Outbound Auth (2LO vs 3LO)

**Inbound** auth answers "who is calling my agent" (`CUSTOM_JWT` on Runtime/Gateway).
**Outbound** auth answers "how does my agent authenticate to a downstream API" — that is
AgentCore Identity, and it is a separate concern.

| | 2LO | 3LO |
|---|---|---|
| Grant | `CLIENT_CREDENTIALS` / `auth_flow="M2M"` | `AUTHORIZATION_CODE` / `auth_flow="USER_FEDERATION"` |
| Acts as | The application | The end user |
| Token scope | One per workload | One **per user**, keyed by inbound JWT `sub` |
| Downstream ACLs | Service account's | The real user's |

Use 3LO whenever the downstream system has per-user permissions you must respect — a service
account sees everything and misattributes every action. A third grant, `TOKEN_EXCHANGE`,
swaps the inbound token for a downstream one with no interactive consent.

Adopting this deletes your PKCE pair generation, `state` store, authorize-URL builder,
code-exchange POST, token persistence, and refresh logic. Maximum offload is a Gateway
MCP-server target with `grantType: AUTHORIZATION_CODE` — the Gateway injects the per-user
token and the MCP server holds no credentials at all. **Gateway does not manage 3LO for
Lambda targets**; those use the `@requires_access_token` decorator in-process instead.

See `references/identity.md` — including the gotchas that fail confusingly (service-linked
workload identities, `userToken` vs `userId` session binding, the required MCP protocol
version, and FastMCP stripping the `authorization` header).

### 7. Policy — Cedar Authorization on Tool Calls

A **policy engine** attached to a Gateway evaluates Cedar policies on every `tools/call`,
against a schema **auto-generated from the Gateway's tool manifest**. Cedar is default-deny
with forbid-wins. Each tool becomes an action (`<target>___<tool>`); the principal is
`AgentCore::OAuthUser` (JWT claims exposed as **tags**) or `AgentCore::IamEntity`; the only
context is `context.input` — the tool's arguments. That makes argument-level rules expressible
("refunds ≤ $500", "actorId must equal the caller's `sub`"), and anything needing time of day
or source IP not expressible.

**Two separate settings share the value `LOG_ONLY`,** and confusing them is the likeliest way
to think you are enforcing when you are not: engine-level `policyEngineConfiguration.mode`
(`ENFORCE`|`LOG_ONLY`) governs the whole engine and **takes precedence**, while per-policy
`enforcementMode` (`ACTIVE`|`LOG_ONLY`) shadow-tests one rule inside an enforcing engine.
Promote when the `LogOnlyDecisionFlips` metric holds at zero.

Two Cedar limits shape your design up front: **no string concatenation** (so you cannot build
`"/actors/" + sub` — the IdP must issue a claim already holding the full value) and **no action
wildcards** (so adding a tool to a target is also a policy change; under default-deny the new
tool is denied until listed). See `references/policy.md`.

### 8. Evaluations — Managed Scoring from Traces

Managed LLM-as-judge scoring over OTEL traces. Works for agents on AgentCore Runtime **and
anywhere else** — the input is telemetry, not a runtime dependency. **This is distinct from
`strands-evals`**, which is the pre-deploy pytest suite; you want both, and
`bedrock_agentcore.evaluation.convert_strands_to_adot` bridges between them.

Four ways to run it: **online** (sample live traffic continuously), **on-demand** (score
specific span/trace IDs — the cheapest loop when iterating on an evaluator), **batch** (async
job over a CloudWatch Logs window; the service discovers sessions itself), and **dataset**
(replay predefined turns, or let an LLM actor drive simulated ones).

Built-in evaluators come in session, trace, and tool levels — the level decides what the judge
actually sees, which is the first thing to check when a score looks wrong. Note three
trajectory variants (`ExactOrderMatch`, `InOrderMatch`, `AnyOrderMatch`): exact-order will fail
an agent that did the right thing plus one extra lookup, so pick the loosest that still encodes
the requirement. Prefer a **code-based** custom evaluator whenever the property is decidable —
an LLM judge for "is this valid JSON" adds cost and variance to a question `json.loads` answers
exactly.

**ADOT instrumentation is a hard prerequisite** — no traces, no evaluations. Ground truth is
optional and missing fields **fall back to reference-free scoring rather than erroring**, so a
mistyped field name yields a plausible-but-different score instead of a failure. See
`references/evaluations.md`.

### 9. AgentCore Memory

Three strategy types: User Preferences (cross-session), Semantic Memory (extracted facts), Conversation Summaries (per-session). Namespace design with `{actorId}` from Cognito JWT `sub` claim (extracted via base64, no PyJWT needed). **Must use `batch_size=1`** (the default) because containers are hard-killed without SIGTERM — larger batches risk data loss.

### 10. Observability (ADOT)

AgentCore Runtime includes an ADOT sidecar. Setup is four steps, and the fourth is the one
people miss:

1. Add `strands-agents[otel]` and `aws-opentelemetry-distro>=0.18` to requirements
2. Use `CMD ["opentelemetry-instrument", "python", "app.py"]` in the Dockerfile
3. **Do NOT set `OTEL_*` env vars** — the sidecar configures them for hosted agents.
   (This inverts for agents hosted outside Runtime, where you must set them yourself.)
4. **Enable CloudWatch Transaction Search** — an account/region setting outside your stack,
   required by AgentCore Evaluations in both delivery modes

**Unified vs split telemetry** decides where conversation content lives. Unified keeps it on
the span in the agent's own log group; split moves it into separate event records in
`otel-rt-logs` while spans go to the shared `aws/spans`. Agents created on or after
2026-07-20 default to unified; toggle with `UNIFIED_TRACES_DESTINATION_ENABLED`. **ADOT older
than 0.18.0 silently falls back to split** regardless of that variable.

Two failure modes are silent and cost real time: `logs:DescribeLogGroups` scoped to anything
narrower than `log-group:*` means **no `[runtime-logs]` streams are created at all**, and
missing Transaction Search means **Evaluations finds no sessions**. Neither raises an error.

Session grouping runs on `gen_ai.conversation.id` and `session.id` — the same attributes the
eval suite injects via `trace_attributes`. See `references/observability.md`.

### 11. CDK Infrastructure

Two agent-specific stacks:
- **MCPGatewayStack**: Gateway + interceptor + Lambda targets + tool schemas
- **AgentRuntimeStack**: ECR + CfnRuntime + CfnMemory + IAM (with full permissions for logs, X-Ray, CloudWatch Metrics, Workload Identity)

Plus minimal Cognito User Pool if no OIDC provider exists.

### 12. Streaming Protocol & Interrupts

AgentCore streams responses as SSE with nested JSON events (`contentBlockDelta`, `messageStop`, etc.). Key behaviors: `stopReason=end_turn` means normal completion; `stopReason=interrupt` means HITL pause — caller must collect user response and resume with `interrupt_responses` payload. Session IDs must be minimum 33 characters and consistent across the entire conversation including interrupt resumptions.

## Getting Started

For a new agent project, work through the references in this order:

1. **`references/cdk-infrastructure.md`** — Set up Cognito, Runtime stack, Gateway stack
2. **`references/gateway-and-mcp.md`** — Create MCP Gateway and Lambda MCP servers
3. **`references/runtime-and-sessions.md`** — Build the agent container with singleton session
4. **`references/security.md`** — Add multi-layer auth and token propagation
5. **`references/identity.md`** — Wire outbound auth (2LO/3LO) to downstream APIs
6. **`references/policy.md`** — Add Cedar authorization on tool calls (start in LOG_ONLY)
7. **`references/agentcore-memory.md`** — Configure persistent conversation memory
8. **`references/streaming-backend.md`** — Understand the streaming event format and interrupt protocol
9. **`references/observability.md`** — Turn on tracing properly; it gates everything below
10. **`references/evaluations.md`** — Score the deployed agent from its traces, in batch then online
