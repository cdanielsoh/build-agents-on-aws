# Agent Topology

Choose the right agent pattern for your use case. Start simple, split only when you have a concrete reason.

## Table of Contents

1. [Single Agent](#single-agent)
2. [Agent-as-Tool (Hierarchical Delegation)](#agent-as-tool)
3. [Graph (Deterministic DAG)](#graph)
4. [Swarm (Autonomous Coordination)](#swarm)
5. [Workflow (Pre-Defined Task DAG)](#workflow)
6. [A2A (Cross-Platform Protocol)](#a2a)
7. [Decision Framework](#decision-framework)

---

## Single Agent

One agent, one set of tools, one domain. **Most agents should start here.**

```python
from strands import Agent

agent = Agent(
    model="us.anthropic.claude-sonnet-4-20250514-v1:0",
    tools=[tool_a, tool_b, tool_c, mcp_client],
    system_prompt=build_system_prompt(),
)

response = agent("What's the status of my recent order?")
```

The scaffold demonstrates this pattern: `SessionBuilder` creates a single Agent with local tools and an optional MCPClient. See `scaffold/agent/core/builder.py`.

### When Single Agent Works

- Tools < ~15 (the model handles tool selection well up to this point)
- Domain is cohesive (all tools serve the same purpose)
- Conversation is interactive (user asks, agent responds, back and forth)
- You need streaming (single agent streams naturally)

### Why Start Here

Multi-agent adds coordination overhead, debugging complexity, and latency. Each handoff between agents costs at least one model invocation. If one agent can do the job, the extra complexity isn't justified.

---

## Deferred Tool Loading

When you have 15–40+ tools but they belong to a single domain, splitting into Agent-as-Tool adds coordination overhead you don't need. Deferred tool loading lets a single agent scale past the ~15 tool limit by loading tool schemas on demand instead of upfront.

### Why This Matters

Every tool in the prompt consumes tokens (name + description + parameter schema). At 15 tools, this is ~3K–5K tokens. At 50 tools (common with MCP servers), it's 15K–25K tokens — a significant chunk of context that also slows down the first model call. Worse, any change to the tool set (MCP server reconnects, new tools added) invalidates the prompt cache.

Deferred loading addresses these problems differently by pattern. Patterns A and B reduce initial token load but break the cache when tools are added mid-session (append-only). Pattern C (meta-tooling) keeps the tool definitions stable at exactly 2 tools, so the cache is never invalidated — schemas are fetched as tool results, not tool definitions.

### Pattern A: Intent-Based Tool Profiles

Classify the user's intent before constructing the agent, then load only the relevant tool subset.

```python
# Tool profiles — subsets of your full tool inventory.
PROFILES = {
    "orders": lambda ctx: [list_orders(ctx), get_order_details(ctx), process_refund(ctx)],
    "account": lambda ctx: [get_account(ctx), update_preferences(ctx), reset_password(ctx)],
    "support": lambda ctx: [search_kb(ctx), escalate(ctx), create_ticket(ctx)],
}

class ProfileBuilder(SessionBuilder):
    def __init__(self, config, intent: str):
        super().__init__(config)
        self._intent = intent

    def _build_tools(self, app_ctx):
        # Load only the tools for the detected intent.
        factory = PROFILES.get(self._intent, PROFILES["support"])
        return factory(app_ctx)
```

**Intent classification** is typically an LLM call that outputs a single number (one intent) or multiple numbers (overlapping intents) mapped to profile keys. A fast, small model (Haiku) with a constrained output format works well — the classification doesn't need to be creative, just accurate.

**Critical: always append, never remove.** When loading additional profiles mid-session (e.g., the user shifts from orders to account), append the new tools to the existing set — never remove previously loaded tools. The model has already seen the prior tool definitions in its context. Removing them creates a mismatch between what the model remembers and what it can call, leading to hallucinated tool calls or confusion.

```python
class ProfileBuilder(SessionBuilder):
    def __init__(self, config, intents: list[str]):
        super().__init__(config)
        self._intents = intents

    def _build_tools(self, app_ctx):
        # Append tools from all detected intents — never remove.
        tools = []
        seen = set()
        for intent in self._intents:
            factory = PROFILES.get(intent)
            if factory:
                for t in factory(app_ctx):
                    if t not in seen:
                        tools.append(t)
                        seen.add(t)
        return tools or PROFILES["support"](app_ctx)
```

**Best for:** 15–30 tools with well-separated intents.

**Trade-off:** Requires accurate intent classification upfront. Misclassification means missing tools — the agent can't call a tool that wasn't loaded. Append-only means the tool set grows over a session, but this is preferable to the model hallucinating calls to removed tools.

### Pattern B: Catalog + Dynamic Loading

Two tools: `search_tools` discovers what's available, `load_tool` dynamically registers the chosen tool with the agent. The agent starts with only these two tools (plus any always-needed ones), keeping the initial prompt lean. Tools are loaded on demand and stay registered for the rest of the session (append-only, same principle as Pattern A).

```python
from strands import tool
from strands.types.tools import ToolContext

# Full tool inventory — functions not yet registered with the agent.
TOOL_REGISTRY = {
    "list_orders": list_orders,
    "get_order_details": get_order_details,
    "search_knowledge_base": search_knowledge_base,
    "create_ticket": create_ticket,
    # ... 40+ entries
}

TOOL_DESCRIPTIONS = {
    name: fn.tool_spec.get("description", "")
    for name, fn in TOOL_REGISTRY.items()
}


@tool
def search_tools(query: str) -> dict:
    """Search for available tools by keyword. Returns matching tool
    names and descriptions. Call load_tool(tool_name) to make a
    tool available for use."""
    query_lower = query.lower()
    matches = {
        name: desc for name, desc in TOOL_DESCRIPTIONS.items()
        if query_lower in name.lower() or query_lower in desc.lower()
    }
    return {
        "matches": matches,
        "total_available": len(TOOL_DESCRIPTIONS),
        "hint": "Call load_tool(tool_name='...') to load a tool, then call it directly.",
    }


@tool(context=True)
def load_tool(tool_context: ToolContext, tool_name: str) -> str:
    """Load a tool so you can call it directly. Call search_tools first
    to discover available tool names."""
    fn = TOOL_REGISTRY.get(tool_name)
    if not fn:
        return f"Unknown tool: {tool_name}. Call search_tools to see available tools."

    # Dynamically register with the agent's tool registry.
    # This works because the event loop calls get_all_tool_specs() fresh
    # on every model invocation — no agent "reassembly" needed.
    # Once loaded, the tool stays registered for the rest of the session
    # (append-only).
    tool_context.agent.tool_registry.register_tool(fn)
    return f"Tool '{tool_name}' loaded. You can now call it directly."
```

**Why this works:** Strands' event loop calls `tool_registry.get_all_tool_specs()` before every model invocation (in `_handle_model_execution()`). When `register_tool()` adds a tool to the registry dict, the next model call picks it up automatically. The tool's schema appears in the prompt on the very next cycle within the same agent loop — no hook or agent reconstruction needed.

**Flow:**

```
User: "What's the status of my recent order?"

Agent → search_tools("orders")
  ← {"matches": {"list_orders": "List recent orders...", "get_order_details": "..."}, ...}

Agent → load_tool("list_orders")
  ← "Tool 'list_orders' loaded. You can now call it directly."

Agent → list_orders()              ← now callable as a first-class tool
  ← {"orders": [...]}

Agent: "Your most recent order #3 was shipped on March 15..."
```

**Cache impact:** Each `load_tool` call changes the tool definitions in the prompt, which breaks the prefix cache on the next model call. This is a one-time cost per tool — once loaded, the tool stays registered and the new prefix gets cached for subsequent turns. For sessions that load 2-3 tools, this is 2-3 cache misses total.

**Best for:** 15–40 tools. Simpler than meta-tooling (Pattern C) — tools become first-class once loaded, so the model uses them natively. No extra routing layer.

**Trade-off:** Each loaded tool permanently increases prompt size (append-only). Over a long session with many different tools, the prompt grows. For 40+ tools or sessions that access many categories, Pattern C (meta-tooling) is more scalable since schemas never enter the tool definitions.

### Pattern C: Meta-Tooling (Experimental)

Register only 2 meta-tools (`get_tool_info`, `use_tool`) and embed a brief category catalog in the system prompt. The agent fetches full tool schemas on demand by calling `get_tool_info(category)`, then executes via `use_tool(category, tool_name, params)`. A `CategoryRegistry` groups tools into named categories; `use_tool` transparently routes to local functions or MCP servers.

Measured savings: ~76% first-cycle token reduction, ~29% total across multi-turn sessions.

**Best for:** 15–50+ tools, framework-agnostic, mixed local/MCP tools.

**Trade-off:** This is **experimental** — LLMs are trained to see tool descriptions upfront, and meta-tooling breaks this assumption. Only works reliably with capable models (Sonnet, Opus). With Haiku, accuracy can drop significantly on multi-step tasks. Adds +2 extra model cycles per category accessed. `ToolContext` is not available inside meta-tool closures — use the closure factory workaround instead.

See [meta-tooling.md](meta-tooling.md) for the full pattern, code examples, and comparison table.

### When to Use Deferred Loading vs. Agent-as-Tool

| Scenario | Deferred Loading | Agent-as-Tool |
|----------|-----------------|---------------|
| 15–40 tools, single domain | Preferred — no coordination overhead | Overkill |
| 40+ tools, single domain | Pattern C (Meta-Tooling) | Also viable |
| Mixed local/MCP, framework-agnostic | Pattern C (Meta-Tooling) | Overkill |
| Distinct specialist domains | Not ideal — model still sees one prompt | Preferred |
| Different models per domain | Not possible | Required |
| Need parallel execution | Not applicable | Use Graph/Workflow |

---

## Agent-as-Tool

An orchestrator agent calls specialist sub-agents wrapped as tools. This creates a hierarchical structure where the orchestrator decides when to delegate.

### Three Approaches in Strands

**1. Direct agent passing** (simplest):

```python
research_agent = Agent(tools=[web_search, summarize], system_prompt="You are a researcher...")
writing_agent = Agent(tools=[format_doc], system_prompt="You are a technical writer...")

orchestrator = Agent(
    tools=[research_agent, writing_agent],
    system_prompt="Delegate research tasks to research_agent, writing to writing_agent.",
)
```

**2. `.as_tool()` for custom descriptions:**

```python
research_tool = research_agent.as_tool(
    name="research",
    description="Research a topic and return findings. Use for fact-gathering tasks.",
)
orchestrator = Agent(tools=[research_tool, writing_agent])
```

**3. `@tool` wrapper for maximum control:**

```python
@tool
def research(query: str) -> str:
    """Research a topic. Returns structured findings."""
    result = research_agent(f"Research: {query}. Return key findings as bullet points.")
    return str(result)

orchestrator = Agent(tools=[research])
```

### When to Use Agent-as-Tool

- Distinct specialist domains (research vs. writing vs. code)
- Each sub-agent needs different tools or system prompts
- The orchestrator needs to decide dynamically who to delegate to
- You want shared conversation context (the orchestrator sees all results)

---

## Graph

Deterministic DAG execution with optional cycles for feedback loops. You define the structure; the framework executes it.

```python
from strands.multiagent.graph import GraphBuilder

graph = (
    GraphBuilder()
    .add_node("research", research_agent)
    .add_node("review", review_agent)
    .add_node("publish", publish_agent)
    .add_edge("research", "review")
    .add_conditional_edge("review", {
        "approved": "publish",
        "needs_revision": "research",  # cycle: feedback loop
    })
    .build()
)

result = graph.execute("Write an article about distributed systems")
```

### Key Features

- **Conditional edges**: routing decisions based on node output
- **Cycle support**: feedback loops with configurable `max_iterations`
- **Shared state**: `invocation_state` dict accessible by all nodes
- **Nested composition**: Graph nodes can be other Graphs or Swarms
- **Timeout control**: per-node and total execution timeouts

### When to Use Graph

- Structured processes with **known branching logic** (approval flows, validation pipelines)
- **Error paths** that route to recovery or escalation nodes
- **Feedback loops** where a reviewer sends work back to a producer
- You need **deterministic execution order** — the developer defines the flow, not the agents

### Key Difference from Swarm

In a Graph, **the developer defines the edges**. In a Swarm, **the agents decide who to hand off to**. Graph is developer-directed; Swarm is agent-directed.

---

## Swarm

Autonomous team coordination. Agents hand off to peers via tools, with a shared working memory.

```python
from strands.multiagent.swarm import Swarm, SharedContext

swarm = Swarm(
    agents=[researcher, architect, coder, reviewer],
    shared_context=SharedContext(initial_request="Build a REST API"),
)

result = swarm.execute("Build a REST API for user management")
```

### Key Features

- **Self-organizing**: agents decide who to hand off to based on the task
- **Shared working memory**: `SharedContext` accessible to all agents
- **Dynamic handoffs**: agents use tool calls to transfer control to peers
- **Handoff limits**: configurable max handoffs to prevent infinite loops
- **Hook support**: `BeforeNodeCallEvent` for human interrupts

### When to Use Swarm

- **Multidisciplinary problems** where the path is emergent (incident response, research synthesis)
- **Exploration** tasks where you don't know the right sequence upfront
- **Brainstorming** with specialized perspectives
- Software development workflows (researcher -> architect -> coder -> reviewer)

### Key Difference from Graph

Swarm agents have **autonomy** — they decide the flow at runtime. This makes Swarm more flexible but less predictable. Use Graph when you want control; use Swarm when you want emergence.

---

## Workflow

Pre-defined task DAG with parallel execution of independent tasks. No cycles. Exposed as a single tool that an agent can invoke.

### Key Features

- **Parallel execution**: independent tasks run concurrently
- **No cycles**: strictly acyclic — if you need feedback loops, use Graph
- **Single action**: the agent invokes the workflow as one tool call
- **Task-specific context**: each task gets curated summaries, not full history

### When to Use Workflow

- **Automated pipelines** (ETL, report generation, deployment scripts)
- **Batch operations** with parallelizable steps
- **Repeatable processes** that don't require agent decision-making at each step

### Key Difference from Graph

Workflow is a **tool** — the agent invokes it as a single action. Graph is an **orchestration pattern** — each node is an agent turn. Workflow is for deterministic pipelines; Graph is for agent-driven processes with branching.

---

## A2A

Agent-to-Agent protocol for cross-platform communication. Remote agent discovery and streaming.

```python
from strands.agent.a2a_agent import A2AAgent

# Connect to a remote agent running on a different platform
remote_agent = A2AAgent(url="https://remote-agent.example.com")

# Use it like any other tool
orchestrator = Agent(tools=[remote_agent, local_tool])
result = orchestrator("Analyze this dataset using the remote analysis service")
```

### When to Use A2A

- Agents built on **different frameworks** need to collaborate
- **Cross-organization** agent communication
- Sub-agents deployed as **independent services** with their own scaling
- You want to compose agents across **different infrastructure boundaries**

---

## Decision Framework

Start with a single agent. Split only when you have a concrete reason.

### Decision Tree

```
1. Can one agent handle all tools (< ~15)?
   → YES: Single Agent. Stop here.
   → NO: Continue.

2. Are tools 15-40+ but in a single domain?
   → YES: Deferred Tool Loading. Which pattern?
     - Need framework-agnostic + mixed local/MCP? → Pattern C (Meta-Tooling, experimental)
     - Can classify intent upfront? → Pattern A (Intent Profiles)
     - Just need tool discovery? → Pattern B (Catalog Tool)
   → NO: Continue.

3. Are domains cleanly separable?
   → YES: Agent-as-Tool (orchestrator delegates to specialists)
   → NO: Continue.

4. Is the process flow known in advance?
   → YES, with cycles/conditions: Graph
   → YES, without cycles, parallelizable: Workflow
   → NO: Continue.

5. Should agents decide the flow autonomously?
   → YES: Swarm
   → NO: Revisit — are the domains really not separable? Try Agent-as-Tool.

6. Are agents on different platforms?
   → YES: A2A (can combine with any pattern above)
```

### Summary Table

| Pattern          | Execution Model        | Who Controls Flow        | Best For                         |
|------------------|------------------------|--------------------------|----------------------------------|
| Single Agent     | One agent, one loop    | The agent                | Most use cases (< ~15 tools)     |
| Deferred Loading | One agent, tool subsets | Developer (profiles) or Agent (catalog) | 15-40+ tools, single domain |
| Meta-Tooling *   | One agent, 2 meta-tools | Agent (on demand)       | 15-50+ tools, mixed local/MCP    |
| Agent-as-Tool    | Hierarchical           | Orchestrator             | Distinct specialist domains      |
| Graph            | Deterministic DAG      | Developer (edges)        | Structured processes, branching  |
| Swarm            | Autonomous handoffs    | Agents (runtime)         | Emergent, multidisciplinary      |
| Workflow         | Parallel DAG (no cycles)| Developer (DAG)         | Repeatable pipelines             |
| A2A              | Cross-platform RPC     | Protocol                 | Cross-framework collaboration    |

\* Experimental — requires Sonnet/Opus; evaluate harshly with less capable models.

### Common Mistake: Premature Multi-Agent

Splitting into multiple agents before a single agent hits its limits adds:
- **Coordination overhead**: each handoff costs a model invocation
- **Debugging complexity**: tracing issues across agents is harder
- **Latency**: more hops mean slower responses
- **State management**: sharing context between agents requires explicit design

Only split when you observe concrete problems: tool selection accuracy drops (too many tools), the system prompt is overloaded with conflicting domains, or you need parallel execution for throughput.
