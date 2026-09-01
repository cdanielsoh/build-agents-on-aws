---
name: strands-agent-design
description: >
  Design well-architected AI agents using the Strands Agents SDK. Use this
  skill whenever the user wants to design agent prompts, structure system
  prompts for cache efficiency, design tool APIs with progressive disclosure,
  choose between single-agent and multi-agent topologies (Graph, Swarm,
  Workflow, Agent-as-Tool, A2A), manage context window pressure, implement
  deferred tool loading, use agent.state for durable metadata, write agent
  evaluations and tests, use the Strands Evals SDK, build closure factory
  tool patterns, avoid tool anti-patterns, structure prompt stacks, load
  file-based system prompts, inject dynamic context via tool results,
  pre-populate user messages, simulate multi-turn conversations with
  ActorSimulator, generate test cases with ExperimentGenerator, evaluate
  tool selection accuracy, or decide when to split a single agent into
  multiple agents. Trigger on mentions of: Strands agent design, prompt
  architecture, tool design patterns, progressive disclosure, closure
  factory, agent topology, GraphBuilder, Swarm, Workflow, Agent-as-Tool,
  A2A protocol, strands-evals, strands_evals, OutputEvaluator,
  TrajectoryEvaluator, ActorSimulator, ExperimentGenerator, EvalBuilder,
  prompt cache, cache TTL, context window management, context pressure,
  conversation manager, CacheSafeConversationManager, deferred tool loading,
  tool anti-patterns, agent.state, meta-tooling, meta-tool pattern,
  MetaToolingBuilder, ToolCategory, CategoryRegistry, schema-level
  progressive disclosure, get_tool_info / use_tool pattern,
  or "how should I structure my agent".
  Also trigger when users ask about agent architecture decisions, when to
  use multi-agent patterns, how to test agents, how to design agent tool
  APIs, how to manage long conversations, how to handle context window
  limits, how to reduce tool-schema token overhead, how to route between
  local and MCP tools transparently, or how to protect agents from
  prompt injection (entity index mapping, ID hiding).
---

# Designing AI Agents with Strands Agents SDK

> **Validated against `strands-agents` v1.34.1** (April 2026). If your version differs significantly, verify that the APIs and patterns still apply.

This skill covers how to **design** well-architected agents — prompt architecture for cache efficiency, tool APIs that control context window bloat, topology selection (single vs. multi-agent), and evaluation with the Strands Evals SDK.

It does **not** cover deployment infrastructure (CDK, AgentCore Runtime, MCP Gateway, auth). For that, see the `deploy-on-agentcore` skill.

A runnable project template lives at the plugin root, not in this skill. Scaffold it with the
`/new-agent` command rather than retyping files from these references — the command copies the
tested template verbatim.

## The Six Pillars

```
Prompt Architecture → Tool Design → Security → Context Management → Agent Topology → Testing
(cache-efficient      (progressive   (prompt     (three-tier          (single/graph/   (output/trajectory/
 prompt stack)         disclosure)    injection)   compaction)          swarm/workflow)   traces/simulation)
```

Each pillar has a dedicated reference file with patterns, code examples, and rationale.

## When to Read What

| You want to...                                          | Read                              |
|---------------------------------------------------------|-----------------------------------|
| Structure system prompt for cache efficiency            | `references/prompt-architecture.md` |
| Load prompts from .md files, version-control them       | `references/prompt-architecture.md` |
| Inject dynamic context without busting prompt cache     | `references/prompt-architecture.md` |
| Pre-populate user message with gathered data            | `references/prompt-architecture.md` |
| Build conditional prompt sections per request           | `references/prompt-architecture.md` |
| Design two-layer summary-then-detail tool APIs          | `references/tool-design.md`         |
| Build tools via closure factory (make_tools pattern)    | `references/tool-design.md`         |
| Return instructions from tools (tool-as-dynamic-prompt) | `references/tool-design.md`        |
| Avoid tool anti-patterns (god tools, missing guards)    | `references/tool-design.md`         |
| Add human-in-the-loop confirmation gates                | `references/tool-design.md`         |
| Add Bedrock Guardrails (content filtering, PII redaction) | `references/security-patterns.md` |
| Set up shadow mode guardrails for policy tuning        | `references/security-patterns.md`    |
| Write safety-focused system prompt instructions        | `references/security-patterns.md`    |
| Propagate user identity from JWT to tools via context  | `references/security-patterns.md`    |
| Protect tools from prompt injection (ID hiding)        | `references/security-patterns.md`    |
| Map real IDs to sequential indices (EntityIndexMapper)  | `references/security-patterns.md`    |
| Manage context window pressure (tool result clearing)  | `references/context-management.md`  |
| Clear stale tool results when cache expires            | `references/context-management.md`  |
| Use agent.state for durable metadata across compaction | `references/context-management.md`  |
| Understand Bedrock's 5-min cache TTL                   | `references/prompt-architecture.md` |
| Formalize system prompt boundary model                 | `references/prompt-architecture.md` |
| Use deferred tool loading to scale past 15 tools       | `references/agent-topology.md`      |
| Implement schema-level progressive disclosure (meta-tooling) | `references/meta-tooling.md`  |
| Group tools into categories with ToolCategory/Registry | `references/meta-tooling.md`        |
| Route between local tools and MCP tools transparently  | `references/meta-tooling.md`        |
| Choose between single agent, Graph, Swarm, Workflow    | `references/agent-topology.md`      |
| Wrap sub-agents as tools (Agent-as-Tool)                | `references/agent-topology.md`      |
| Build a deterministic DAG with GraphBuilder             | `references/agent-topology.md`      |
| Set up autonomous agent coordination (Swarm)            | `references/agent-topology.md`      |
| Connect agents across platforms (A2A protocol)          | `references/agent-topology.md`      |
| Write output-level evals with rubrics                   | `references/testing-with-evals.md`  |
| Validate tool call sequences (trajectory evals)         | `references/testing-with-evals.md`  |
| Run OTEL-based trace evaluators                         | `references/testing-with-evals.md`  |
| Simulate multi-turn conversations (ActorSimulator)      | `references/testing-with-evals.md`  |
| Auto-generate test cases from scenarios                 | `references/testing-with-evals.md`  |

## Key Patterns (Summary)

### 1. The Prompt Stack (Cache Efficiency)

Bedrock caches the longest matching prefix of the conversation. The system prompt, tool definitions, and prior conversation turns all form the cached prefix — each new turn only processes the latest content.

```
System Prompt     STATIC    — cached by Bedrock
Tool Definitions  AUTO      — auto-cached as part of prefix
User Message      DYNAMIC   — changes every turn
Tool Results      DYNAMIC   — generated during agent loop
```

**Rule:** move stable content UP the stack for cache hits. Multi-turn sessions benefit the most — by turn 5, turns 1-4 are fully cached. Cross-user cache hits are minimal (sessions diverge at the first message), so the real savings come from conversation depth within a session.

### 2. File-Based System Prompts

Load system prompts from `.md` files at startup. They're version-controlled, reviewable in PRs, and composable from multiple files. See `templates/strands-agentcore/agent/prompts/system.py` for the wiring pattern.

### 3. Dynamic Context via Tool Results

Instead of embedding variable data in the system prompt (which busts the cache), use `@tool` functions. The model calls the tool when it needs context, and the result enters the conversation without touching the cached prefix. This is the preferred way to inject per-user or per-session data.

### 4. Context Window Pressure Management

Three-tier compaction adapted from Claude Code for Bedrock's cache constraints:

- **Tier 1 (time-based)**: If idle gap > cache TTL (5 min), cache is cold — clear old tool results for free
- **Tier 2 (count-based)**: If tool results > threshold, clear oldest (trades cache break for space)
- **Tier 3 (message removal)**: If tokens > 80% of context window, remove oldest messages

Uses `agent.state` for durable metadata (timestamps, entity mappings) that survives clearing. See `templates/strands-agentcore/agent/core/conversation.py` for the `CacheSafeConversationManager`.

### 5. Progressive Disclosure (Two-Layer Tools)

Summaries first (`list_orders`), details on demand (`get_order_details`). Include navigation hints in summary responses so the agent knows what to drill into next. This prevents context window bloat — a 10-order summary is ~200 tokens vs. 5,000+ for full details.

When latency is critical and you know what data is needed, pre-populate the user message instead.

### 6. ToolContext and Closure Factory

Tools use `@tool(context=True)` with `ToolContext` to access `agent.state` (durable metadata) and `invocation_state` (per-request data). For heavy external dependencies (DB clients, API wrappers), closures capture them at construction time. See `templates/strands-agentcore/agent/tools/` for the full pattern.

### 7. Tool Anti-Patterns

- **God tools**: one tool handling multiple unrelated operations via a `type` parameter
- **Returning entire collections**: tools should paginate or return summaries
- **Duplicating agent reasoning**: let the model think, tools execute
- **user_id in tool parameters**: use app context or token propagation — exposed parameters are a prompt injection surface

### 8. Human-in-the-Loop (Interrupt Gates)

Gate irreversible or high-impact tools (deletes, payments, external sends) with a confirmation interrupt. Implement via Strands `HookProvider` + `event.interrupt()` — the hook checks the tool name, pauses the agent, and cancels if the user declines. Read-only tools should never require confirmation. See `references/tool-design.md`.

### 9. Security (Defense in Depth)

Four layers, each catching different attack vectors:

| Layer | Mechanism | Catches |
|-------|-----------|---------|
| **Bedrock Guardrails** | Model-level content filtering, PII redaction, topic denial | Harmful content, sensitive data leakage |
| **Prompt instructions** | System prompt boundaries, injection handling rules | Casual misuse, role confusion, scope creep |
| **JWT identity propagation** | Extract `sub` from JWT, pass via `agent.state` / MCP headers | Identity spoofing, user_id as tool parameter |
| **Entity Index Mapping** | Sequential indices instead of real IDs in tool results | Unauthorized data access via prompt injection |

Guardrails integrate directly with `BedrockModel` (`guardrail_id`, `guardrail_version`). Shadow mode via hooks lets you tune policies before enforcement. User identity flows from JWT into `agent.state` (local tools via `ToolContext`) and `Authorization` header (MCP tools via gateway) — never as a tool parameter. No single layer is sufficient — use all of them. See `references/security-patterns.md`.

### 10. Agent Topology Decision Framework

Start with a single agent. Split only when you have a concrete reason:

| Trigger                               | Pattern               |
|---------------------------------------|-----------------------|
| Tools 15-40, single domain            | Deferred Tool Loading |
| Tools 15-50+, framework-agnostic, mixed local/MCP | Meta-Tooling * |
| Tools > 40 or distinct domains        | Agent-as-Tool         |
| Domains truly independent             | Agent-as-Tool / Graph |
| Need parallel execution               | Graph / Workflow      |
| Different agents need different models | Agent-as-Tool        |
| Structured process with branches      | Graph                 |
| Emergent path, peer collaboration     | Swarm                 |
| Repeatable pipeline as single action  | Workflow              |
| Cross-platform agents                 | A2A                   |

\* Experimental — requires Sonnet/Opus; evaluate harshly with less capable models.

### 11. Testing with Strands Evals SDK

Four evaluation types, each catching different failure modes:

| Type       | Evaluates           | Key Evaluator                |
|------------|---------------------|------------------------------|
| Output     | Response quality    | OutputEvaluator (rubric)     |
| Trajectory | Tool call sequence  | TrajectoryEvaluator          |
| Traces     | OTEL span quality   | Helpfulness, Faithfulness, GoalSuccess, ToolSelection, ToolParameter |
| Simulation | Multi-turn behavior | ActorSimulator + trace evals |

**Package**: `strands-agents-evals` (NOT `strands-evals`). The `EvalBuilder` pattern extends SessionBuilder to inject OTEL trace attributes without affecting production code.

### 12. Meta-Tooling (Experimental — Schema-Level Progressive Disclosure)

When tool count exceeds ~15 but the domain is cohesive, meta-tooling reduces schema token overhead by registering only 2 meta-tools (`get_tool_info`, `use_tool`) and embedding a brief category catalog in the system prompt. Schemas are fetched on demand when the agent decides to use a category. Achieves ~76% first-cycle token savings with stable prompt cache (tool definitions never change). Supports both local tools and MCP tools through transparent routing.

**Important:** This is experimental. LLMs are trained to see tool descriptions upfront — meta-tooling defers them, which only works reliably with capable models (Sonnet, Opus). With Haiku, accuracy drops on multi-step reasoning. Always evaluate harshly before production use. See `references/meta-tooling.md`.

## Project Template

Run `/new-agent <dir>` to materialize a runnable project. **Do not hand-write these files
from the references** — the command copies the tested template, which is pinned and
lint-checked; retyping introduces drift.

```
agent/
  app.py              # BedrockAgentCoreApp entrypoint, singleton session
  core/
    config.py         # frozen dataclass config from env + SSM
    builder.py        # SessionBuilder with _build_* extension points
    conversation.py   # context-pressure policy over native Strands features
    session.py        # thin runtime container (shared mutable headers)
  tools/
    __init__.py       # make_tools() registry
    context.py        # ToolContext usage guide
    example_tool.py   # example tool with ToolContext
  meta_tooling/       # optional: schema-level progressive disclosure
  prompts/
    system.md         # the prompt itself
    system.py         # loader
evals/
  conftest.py         # EvalBuilder, fixtures, data loaders
  generate.py         # ExperimentGenerator auto-generation
  test_output.py      # response quality
  test_trajectory.py  # tool-call sequence
  test_traces.py      # OTEL span quality
  test_simulation.py  # multi-turn behaviour
  chats/ rubrics/ personas/ scenarios/   # fixtures
Dockerfile            # ARM64 + ADOT instrumentation
pyproject.toml        # pinned dependencies
```

The references below explain *why* each piece looks the way it does. Read them when
adapting the template, not to reconstruct it.

## Getting Started

For a new agent, work through the references in order:

1. **`references/prompt-architecture.md`** — Structure your prompts for cache efficiency
2. **`references/tool-design.md`** — Design your tool APIs
3. **`references/security-patterns.md`** — Protect tools from prompt injection
4. **`references/context-management.md`** — Manage context window pressure
5. **`references/agent-topology.md`** — Choose your agent pattern
6. **`references/testing-with-evals.md`** — Set up evaluation

For agents with 15+ tools in a single domain, also read **`references/meta-tooling.md`** for the experimental meta-tooling pattern (schema-level progressive disclosure). Requires Sonnet/Opus — evaluate harshly with less capable models.
