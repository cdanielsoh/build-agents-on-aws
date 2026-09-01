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
---

# Building Agents on AWS Bedrock AgentCore

> **Validated against `bedrock-agentcore` v1.4.6, `aws-cdk-lib` v2.243.0** (April 2026). If your version differs significantly, verify that the APIs and CDK constructs still apply.

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

### 7. AgentCore Memory

Three strategy types: User Preferences (cross-session), Semantic Memory (extracted facts), Conversation Summaries (per-session). Namespace design with `{actorId}` from Cognito JWT `sub` claim (extracted via base64, no PyJWT needed). **Must use `batch_size=1`** (the default) because containers are hard-killed without SIGTERM — larger batches risk data loss.

### 8. Observability (ADOT)

AgentCore Runtime includes an ADOT sidecar for traces and metrics. Setup:
1. Add `strands-agents[otel]` and `aws-opentelemetry-distro` to requirements
2. Use `CMD ["opentelemetry-instrument", "python", "app.py"]` in Dockerfile
3. **Do NOT set OTEL env vars** — the sidecar configures these for hosted agents

Critical IAM: `logs:DescribeLogGroups` on `log-group:*` is required or no `[runtime-logs]` streams are created.

### 9. CDK Infrastructure

Two agent-specific stacks:
- **MCPGatewayStack**: Gateway + interceptor + Lambda targets + tool schemas
- **AgentRuntimeStack**: ECR + CfnRuntime + CfnMemory + IAM (with full permissions for logs, X-Ray, CloudWatch Metrics, Workload Identity)

Plus minimal Cognito User Pool if no OIDC provider exists.

### 10. Streaming Protocol & Interrupts

AgentCore streams responses as SSE with nested JSON events (`contentBlockDelta`, `messageStop`, etc.). Key behaviors: `stopReason=end_turn` means normal completion; `stopReason=interrupt` means HITL pause — caller must collect user response and resume with `interrupt_responses` payload. Session IDs must be minimum 33 characters and consistent across the entire conversation including interrupt resumptions.

## Getting Started

For a new agent project, work through the references in this order:

1. **`references/cdk-infrastructure.md`** — Set up Cognito, Runtime stack, Gateway stack
2. **`references/gateway-and-mcp.md`** — Create MCP Gateway and Lambda MCP servers
3. **`references/runtime-and-sessions.md`** — Build the agent container with singleton session
4. **`references/security.md`** — Add multi-layer auth and token propagation
5. **`references/identity.md`** — Wire outbound auth (2LO/3LO) to downstream APIs
6. **`references/agentcore-memory.md`** — Configure persistent conversation memory
7. **`references/streaming-backend.md`** — Understand the streaming event format and interrupt protocol
