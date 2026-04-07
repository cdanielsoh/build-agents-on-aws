# Build Agents on AWS

Claude Code plugin for building production-grade AI agents on AWS. Covers the full lifecycle — from agent design to deployment on Bedrock AgentCore.

## Installation

```bash
claude plugin marketplace add cdanielsoh/build-agents-on-aws
claude plugin install build-agents-on-aws@build-agents-on-aws
```

## Skills

| Skill | What it covers |
|-------|---------------|
| **strands-agent-design** | Design well-architected agents with Strands Agents SDK — prompt architecture for cache efficiency, tool API design, security patterns, context window management, agent topology selection, meta-tooling, and evaluation with Strands Evals SDK. Includes a runnable reference scaffold. |
| **deploy-on-agentcore** | Deploy agents on Bedrock AgentCore — runtime containers, MCP Gateway with Lambda-based tool servers, multi-layer authorization with JWT token propagation, AgentCore Memory, streaming protocol, and CDK infrastructure. |

Skills activate automatically when relevant context is detected — mention Strands SDK, agent design, AgentCore, MCP Gateway, CDK, or related topics.

## How the Skills Relate

```
strands-agent-design                    deploy-on-agentcore
(what to build)                         (where to deploy)

Prompt Architecture ──────────────────► AgentCore Runtime
Tool Design ──────────────────────────► MCP Gateway + Lambda MCP Servers
Security Patterns ────────────────────► Multi-Layer Auth + Token Propagation
Context Management ───────────────────► AgentCore Memory
Agent Topology ───────────────────────► CDK Infrastructure
Testing with Evals                      Streaming Protocol
```

Start with **strands-agent-design** when building a new agent from scratch. Move to **deploy-on-agentcore** when you're ready to deploy.

## MCP Servers

| Server | Tools | Purpose |
|--------|-------|---------|
| `strands-agents` | `search_docs`, `fetch_doc` | Search and fetch Strands Agents SDK documentation |

## Strands Agent Design

### The Six Pillars

```
Prompt Architecture → Tool Design → Security → Context Management → Agent Topology → Testing
```

Each pillar has a dedicated reference with patterns, code examples, and rationale:

| Reference | Covers |
|-----------|--------|
| `prompt-architecture` | Cache-efficient prompt stacks, file-based prompts, dynamic context via tool results |
| `tool-design` | Progressive disclosure, closure factory, tool-as-dynamic-prompt, anti-patterns, HITL gates |
| `security-patterns` | Bedrock Guardrails, prompt injection defense, JWT propagation, entity index mapping |
| `context-management` | Three-tier compaction, agent.state for durable metadata, cache-safe clearing |
| `agent-topology` | Single agent, deferred loading, Agent-as-Tool, Graph, Swarm, Workflow, A2A |
| `meta-tooling` | Schema-level progressive disclosure, category registry, transparent local/MCP routing |
| `testing-with-evals` | Output/trajectory/trace/simulation evals with Strands Evals SDK |

### Reference Scaffold

A generalized, runnable agent implementation and eval test suite at `skills/strands-agent-design/scaffold/`:

```
scaffold/
├── agent/
│   ├── app.py              # Production entrypoint (BedrockAgentCoreApp)
│   ├── core/               # Config, builder, conversation manager, session
│   ├── tools/              # Tool registry with ToolContext examples
│   ├── meta_tooling/       # Category registry + meta-tool factories
│   └── prompts/            # File-based prompt builder
└── evals/
    ├── conftest.py         # EvalBuilder, fixtures, data loaders
    ├── generate.py         # Auto-generate test cases
    ├── test_output.py      # Output-level eval
    ├── test_trajectory.py  # Trajectory-level eval
    ├── test_traces.py      # Trace-level eval
    └── test_simulation.py  # Multi-turn simulation eval
```

## AgentCore Agent

### Architecture

```
Client / API Backend
  │
  ▼
AgentCore Runtime (Container)      ── Strands Agent, singleton session, streaming
  │
  ▼
MCP Gateway                        ── Single endpoint, OAuth validation, tool routing
  │              │
  ▼              ▼
Lambda MCP A   Lambda MCP B        ── FastMCP tools, domain-specific servers
  │              │
  ▼              ▼
Data Store(s)                      ── DynamoDB, RDS, or existing APIs
```

### References

| Reference | Covers |
|-----------|--------|
| `runtime-and-sessions` | VM-per-session model, BedrockAgentCoreApp, SessionBuilder, singleton pattern |
| `gateway-and-mcp` | MCP Gateway, interceptor Lambda, Lambda MCP servers, direct vs adapter patterns |
| `security` | Multi-layer authorization, JWT token propagation, DynamoDB LeadingKeys, PostgreSQL RLS |
| `agentcore-memory` | User preferences, semantic memory, conversation summaries, namespace design |
| `cdk-infrastructure` | Cognito, Runtime stack, Gateway stack, Backend stack, SSM parameters |
| `streaming-backend` | SSE event format, interrupt protocol, session ID requirements |

### Getting Started Order

1. **cdk-infrastructure** — Set up Cognito, Runtime stack, Gateway stack
2. **gateway-and-mcp** — Create MCP Gateway and Lambda MCP servers
3. **runtime-and-sessions** — Build the agent container with singleton session
4. **security** — Add multi-layer auth and token propagation
5. **agentcore-memory** — Configure persistent conversation memory
6. **streaming-backend** — Understand the streaming event format and interrupt protocol

## Contributing

Fork the repo, make your changes, and submit a PR. Skills are markdown files — no build steps required.
