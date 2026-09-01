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
| **strands-agent-design** | Design well-architected agents with Strands Agents SDK — prompt architecture for cache efficiency, tool API design, security patterns, context window management, agent topology selection, meta-tooling, and evaluation with Strands Evals SDK. |
| **deploy-on-agentcore** | Deploy agents on Bedrock AgentCore — runtime containers, MCP Gateway (tool/HTTP/inference targets, REQUEST+RESPONSE interceptors, rate limits), Identity (2LO/3LO outbound auth), Cedar Policy authorization, Agent Registry, managed Evaluations, Observability, AgentCore Memory, streaming protocol, and CDK infrastructure. |

Skills activate automatically when relevant context is detected — mention Strands SDK, agent design, AgentCore, MCP Gateway, CDK, or related topics.

## Commands

| Command | What it does |
|---------|--------------|
| `/new-agent [dir]` | Scaffold a runnable Strands + AgentCore project from `templates/` |

## How the Skills Relate

```
strands-agent-design                    deploy-on-agentcore
(what to build)                         (where to deploy)

Prompt Architecture ──────────────────► AgentCore Runtime
Tool Design ──────────────────────────► MCP Gateway + Lambda MCP Servers
Security Patterns ────────────────────► Identity (2LO/3LO) + Policy (Cedar)
Context Management ───────────────────► AgentCore Memory
Agent Topology ───────────────────────► CDK Infrastructure
Testing with Evals ───────────────────► Observability + Evaluations
```

Start with **strands-agent-design** when building a new agent from scratch. Move to **deploy-on-agentcore** when you're ready to deploy.

## MCP Servers

| Server | Tools | Purpose |
|--------|-------|---------|
| `strands-agents` | `search_docs`, `fetch_doc` | Search and fetch Strands Agents SDK documentation |
| `agentcore` | `search_agentcore_docs`, `get_*_guide`, `policy_*`, `identity_*`, `memory_*`, `gateway_*` | AgentCore docs plus control-plane operations |

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
| `context-management` | Native ContextOffloader + proactive compression, cache-economics offload policy, agent.state for durable metadata |
| `agent-topology` | Single agent, deferred loading, Agent-as-Tool, Graph, Swarm, Workflow, A2A |
| `meta-tooling` | Schema-level progressive disclosure, category registry, transparent local/MCP routing |
| `testing-with-evals` | Output/trajectory/trace/simulation evals with Strands Evals SDK |

### Project Template

Skills are documentation. Runnable code lives in `templates/` at the plugin root and is
materialized by a command, so the files land on disk verbatim instead of being retyped
from a skill document:

```bash
/new-agent ./my-agent
```

```
templates/strands-agentcore/
├── agent/
│   ├── app.py              # Production entrypoint (BedrockAgentCoreApp)
│   ├── core/               # Config, builder, conversation policy, session
│   ├── tools/              # Tool registry with ToolContext examples
│   ├── meta_tooling/       # Category registry + meta-tool factories
│   └── prompts/            # system.md + loader
├── evals/                  # Output, trajectory, trace, simulation evals + fixtures
├── Dockerfile              # ARM64 + ADOT instrumentation
└── pyproject.toml          # Pinned dependencies
```

Or drive the script directly:

```bash
python3 scripts/scaffold.py --list
python3 scripts/scaffold.py ./my-agent --dry-run
python3 scripts/scaffold.py ./my-agent
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
| `gateway-and-mcp` | MCP Gateway, **REQUEST + RESPONSE interceptors**, inference targets, native tool search, rate limits, Lambda MCP servers |
| `security` | Multi-layer authorization, JWT token propagation, DynamoDB LeadingKeys, PostgreSQL RLS |
| `identity` | **2LO vs 3LO outbound auth**, credential providers, token vault, per-user downstream tokens |
| `agent-registry` | **Publish & discover** agents/MCP/skills, record lifecycle, discovery boundaries |
| `policy` | **Cedar authorization on tool calls**, the two LOG_ONLY controls, policy generation |
| `evaluations` | **Managed LLM-as-judge over traces** — online, on-demand, batch, dataset |
| `observability` | **ADOT, unified vs split telemetry**, gen_ai semconv, silent failure modes |
| `agentcore-memory` | User preferences, semantic memory, conversation summaries, namespace design |
| `cdk-infrastructure` | Cognito, Runtime stack, Gateway stack, Backend stack, SSM parameters |
| `streaming-backend` | SSE event format, interrupt protocol, session ID requirements |

### Getting Started Order

1. **cdk-infrastructure** — Set up Cognito, Runtime stack, Gateway stack
2. **gateway-and-mcp** — Create MCP Gateway and Lambda MCP servers
3. **runtime-and-sessions** — Build the agent container with singleton session
4. **security** — Add multi-layer auth and token propagation
5. **identity** — Wire outbound auth (2LO/3LO) to downstream APIs
6. **policy** — Add Cedar authorization on tool calls, starting in LOG_ONLY
7. **agentcore-memory** — Configure persistent conversation memory
8. **agent-registry** — Publish agents/tools/skills and set discovery boundaries
9. **streaming-backend** — Understand the streaming event format and interrupt protocol
10. **observability** — Turn on tracing properly; it gates evaluation
11. **evaluations** — Score the deployed agent from its traces

## Contributing

Fork the repo, make your changes, and submit a PR.

Skills are markdown — no build step. The template under `templates/` is real code and is
expected to stay runnable:

```bash
python3 scripts/scaffold.py /tmp/t && cd /tmp/t
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check agent evals
.venv/bin/python -m pytest evals/test_output.py -v -s   # calls a real model; costs money
```
