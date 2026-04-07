# Meta-Tooling: Schema-Level Progressive Disclosure

> **Experimental.** Language models are trained to see tool descriptions upfront in the prompt. Meta-tooling breaks this assumption by deferring tool schemas until the agent requests them. This pattern only works reliably with capable models (Sonnet, Opus). With less capable models (Haiku), accuracy can drop significantly — especially on multi-step reasoning tasks (observed 0.5 on complex cases in evaluation). **Harsh evaluation is required before production use.** Always run trajectory and output evaluations (see [testing-with-evals.md](testing-with-evals.md)) to validate that deferred schemas don't degrade tool selection accuracy for your specific use case.

## Table of Contents

1. [The Problem: Schema Token Bloat](#the-problem)
2. [How Meta-Tooling Works](#how-meta-tooling-works)
3. [ToolCategory — Grouping Related Tools](#toolcategory)
4. [CategoryRegistry — Generating the Catalog](#categoryregistry)
5. [The Two Meta-Tools](#the-two-meta-tools)
6. [Transparent Local/MCP Routing](#transparent-localmcp-routing)
7. [Cache Efficiency](#cache-efficiency)
8. [Trade-Offs and Model Requirements](#trade-offs-and-model-requirements)
9. [Comparison with Other Deferred Loading Patterns](#comparison)

---

## The Problem

Every tool registered with the agent consumes prompt tokens — its name, description, and full parameter schema are serialized into the conversation. At small scale this is fine, but it grows linearly:

| Tools | Approximate schema tokens |
|-------|--------------------------|
| 5     | ~1,500–2,500             |
| 15    | ~4,500–7,500             |
| 30    | ~9,000–15,000            |
| 50    | ~15,000–25,000           |

These tokens are sent on **every turn**, even when the agent only needs one or two tools to answer the current query. Worse, any change to the tool set (MCP server reconnect, new tools added) invalidates the prompt cache.

The existing deferred loading patterns (Pattern A–B in [agent-topology.md](agent-topology.md)) address this to varying degrees, but each has limitations: Pattern A requires upfront intent classification, Pattern B dynamically loads tools but they permanently increase prompt size. Meta-tooling offers an alternative where schemas never enter the tool definitions — they're fetched and used through a routing layer.

---

## How Meta-Tooling Works

Meta-tooling applies progressive disclosure at the **tool-schema level** — the same principle as data-level progressive disclosure (see [tool-design.md](tool-design.md)), but applied to tool definitions themselves.

Instead of registering all tools directly, the agent sees:

1. **A brief catalog** in the system prompt — category names and short labels (~1 line each)
2. **Two meta-tools** — `get_tool_info` and `use_tool`

The agent discovers and executes tools through three levels:

```
Level 1 (System Prompt)       Level 2 (get_tool_info)         Level 3 (use_tool)
┌──────────────────────┐     ┌──────────────────────────┐    ┌─────────────────────┐
│ Tool Categories:     │     │ Category: "orders"       │    │ Executes the tool   │
│ - orders: Order mgmt │ ──► │ Instructions: "..."      │ ─► │ with the exact      │
│ - support: Help desk │     │ Tools:                   │    │ params from Level 2 │
│ - account: Profile   │     │   - list_orders(page)    │    │                     │
│                      │     │   - get_order_details(n) │    │ Routes to local fn  │
│ Tools:               │     └──────────────────────────┘    │ or MCP server       │
│ - get_tool_info      │                                     └─────────────────────┘
│ - use_tool           │
└──────────────────────┘
~200 tokens                   ~300-500 tokens per category    Result tokens vary
```

**Flow example:**

```
User: "What's the status of my recent order?"

Agent thinks: "I need order tools"
  → Calls: get_tool_info("orders")
  → Receives: full schema for list_orders, get_order_details + instructions

Agent thinks: "I should list orders first"
  → Calls: use_tool("orders", "list_orders", '{"page": 1}')
  → Receives: order summaries

Agent: "Your most recent order #4 was shipped on March 15..."
```

The agent loaded schemas for only one category (orders), ignoring support and account entirely.

---

## ToolCategory

A `ToolCategory` groups related tools with metadata that supports both local and MCP tools.

```python
from dataclasses import dataclass, field
from typing import Any

from strands.tools.decorator import DecoratedFunctionTool


@dataclass
class ToolCategory:
    """Groups related tools under a named category with instructions.

    Supports both local @tool-decorated functions and remote MCP tools.
    Use tool_names to expose only a subset from a larger tool class or MCP server.
    """

    name: str                          # Category identifier (e.g., "orders")
    description: str                   # Detailed description for get_tool_info response
    instructions: str                  # Usage guidelines returned with schemas
    label: str = ""                    # Short label for catalog (falls back to description)
    tool_class: type | None = None     # Local tool class with @tool methods
    tool_names: list[str] | None = None  # Subset filter — only expose these tools
    mcp_client: Any = None             # MCPClient for remote tools
    _instance: Any = field(default=None, repr=False, init=False)

    @property
    def is_mcp(self) -> bool:
        return self.mcp_client is not None

    def _ensure_instance(self) -> Any:
        """Lazily instantiate the tool_class. Cached for reuse."""
        if self._instance is None and self.tool_class is not None:
            self._instance = self.tool_class()
        return self._instance

    def get_tool_functions(self) -> list[DecoratedFunctionTool]:
        """Return local tool functions. Empty list for MCP categories."""
        if self.is_mcp:
            return []
        instance = self._ensure_instance()
        if instance is None:
            return []
        tools = []
        for attr_name in dir(instance):
            if attr_name.startswith("_"):
                continue
            attr = getattr(instance, attr_name)
            if isinstance(attr, DecoratedFunctionTool):
                if self.tool_names is None or attr.tool_name in self.tool_names:
                    tools.append(attr)
        return tools

    def get_tool_schemas(self) -> list[dict]:
        """Return JSON schemas for all tools in this category."""
        if self.is_mcp:
            return self._get_mcp_schemas()
        return [fn.tool_spec for fn in self.get_tool_functions()]

    def call_mcp_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool via the MCP protocol."""
        result = self.mcp_client.call_tool_sync(
            tool_use_id=f"meta-{tool_name}",
            name=tool_name,
            arguments=arguments,
        )
        for item in result.get("content", []):
            if "text" in item:
                return item["text"]
        return json.dumps(result, default=str)

    def get_catalog_entry(self) -> str:
        """Single catalog line: '- name: label'."""
        return f"- {self.name}: {self.label or self.description}"
```

### Splitting One Tool Source Across Categories

A single tool class with 20 methods can be split into focused categories using `tool_names`:

```python
# One class, three categories
ToolCategory(name="orders", tool_class=EcommTools, tool_names=["list_orders", "get_order_details"], ...)
ToolCategory(name="returns", tool_class=EcommTools, tool_names=["initiate_return", "check_return_status"], ...)
ToolCategory(name="account", tool_class=EcommTools, tool_names=["get_profile", "update_preferences"], ...)
```

### MCP Categories

For remote tools served by an MCP server, pass the `MCPClient` instead of a `tool_class`:

```python
from strands.tools.mcp import MCPClient

mcp = MCPClient(lambda: streamablehttp_client("http://localhost:8080"))

ToolCategory(
    name="inventory",
    description="Real-time warehouse stock levels",
    instructions="Check inventory before confirming availability.",
    label="Stock levels",
    mcp_client=mcp,
    tool_names=["check_inventory", "find_store"],
)
```

---

## CategoryRegistry

The registry manages all categories and generates the brief catalog for the system prompt.

```python
class CategoryRegistry:
    """Central registry for tool categories. Generates the system prompt catalog."""

    def __init__(self):
        self._categories: dict[str, ToolCategory] = {}

    def register(self, category: ToolCategory) -> None:
        self._categories[category.name] = category

    def get_category(self, name: str) -> ToolCategory | None:
        return self._categories.get(name)

    def list_category_names(self) -> list[str]:
        return list(self._categories.keys())

    def get_catalog(self) -> str:
        """Generate the brief catalog for the system prompt (Level 1)."""
        return "\n".join(cat.get_catalog_entry() for cat in self._categories.values())

    def get_all_tool_functions(self) -> list:
        """Flatten all local tools (useful for DirectAgent comparison)."""
        tools = []
        for cat in self._categories.values():
            tools.extend(cat.get_tool_functions())
        return tools
```

The catalog output looks like:

```
- orders: Order management
- support: Help desk & tickets
- account: Profile & preferences
- inventory: Stock levels
```

This is ~40 tokens for 4 categories with 12+ tools — versus ~4,500+ tokens if all schemas were loaded.

---

## The Two Meta-Tools

Both meta-tools are created via closure factories that capture the registry at construction time.

### get_tool_info

```python
from strands import tool


def create_get_tool_info(registry: CategoryRegistry):
    """Factory that creates a get_tool_info tool bound to the registry."""

    @tool
    def get_tool_info(category: str) -> str:
        """Get tool names, parameter schemas, and usage instructions for a category.

        You MUST call this before calling use_tool. This returns the exact tool
        names and parameter names you need — do not guess them.

        Args:
            category: The category name from the Tool Categories list
        """
        cat = registry.get_category(category)
        if not cat:
            available = registry.list_category_names()
            return f'{{"error": "Unknown category: {category}", "available": {available}}}'

        schemas_text = json.dumps(cat.get_tool_schemas(), indent=2, default=str)
        return (
            f'{{"category": "{cat.name}", '
            f'"instructions": {repr(cat.instructions)}, '
            f'"tools": {schemas_text}}}'
        )

    return get_tool_info
```

### use_tool

```python
def create_use_tool(registry: CategoryRegistry):
    """Factory that creates a use_tool tool with transparent local/MCP routing."""

    @tool
    def use_tool(category: str, tool_name: str, parameters: str) -> str:
        """Run a tool. You must call get_tool_info first to get the exact
        tool_name and parameter names.

        Args:
            category: The category name (same value passed to get_tool_info)
            tool_name: The exact tool name returned by get_tool_info
            parameters: JSON string of parameters using exact names from get_tool_info
        """
        entry = registry.get_category(category)
        if not entry:
            return f'{{"error": "Unknown category: {category}"}}'

        params = json.loads(parameters) if parameters else {}

        # MCP: dispatch via protocol
        if entry.is_mcp:
            return entry.call_mcp_tool(tool_name, params)

        # Local: find matching function and call directly
        for tool_fn in entry.get_tool_functions():
            if tool_fn.tool_name == tool_name:
                return tool_fn(**params)

        available = [fn.tool_name for fn in entry.get_tool_functions()]
        return f'{{"error": "Unknown tool \'{tool_name}\'", "available": {available}}}'

    return use_tool
```

### ToolContext Limitation

Because `use_tool` calls local tool functions directly (`tool_fn(**params)`), `ToolContext` is **not injected** by the agent's tool dispatch. Tools that depend on `tool_context.agent.state` won't work inside meta-tooling out of the box.

**Workaround:** Capture dependencies via the closure factory pattern. Instead of accessing `agent.state` inside the tool, capture the needed values when constructing the tool:

```python
def create_order_tools(user_id: str, db_client):
    @tool
    def list_orders(page: int = 1) -> dict:
        """List orders for the current user."""
        return db_client.query_orders(user_id=user_id, page=page)
    return [list_orders]
```

Then register the pre-bound tools in the category.

---

## Transparent Local/MCP Routing

A key feature of `use_tool` is that the agent doesn't know whether a tool runs locally or remotely. The routing logic checks `entry.is_mcp`:

- **Local tools**: `tool_fn(**params)` — direct Python function call
- **MCP tools**: `mcp_client.call_tool_sync()` — remote server dispatch

This means you can start with local dummy tools for development, then swap to MCP-backed implementations without changing the agent's system prompt or behavior. The category definition changes, but the meta-tool interface stays identical.

```python
# Development: local tools
ToolCategory(name="inventory", tool_class=DummyInventory, ...)

# Production: same category, now backed by MCP
ToolCategory(name="inventory", mcp_client=production_mcp, ...)
```

---

## Cache Efficiency

Meta-tooling creates exceptionally stable prompt cache conditions:

1. **Tool definitions never change** — the agent always sees exactly 2 tools (`get_tool_info`, `use_tool`), regardless of how many actual tools exist
2. **System prompt is stable** — the catalog (category names + labels) rarely changes
3. **Schema tokens are conversational** — schemas fetched via `get_tool_info` enter as tool results, not as tool definitions, so they don't affect the prefix cache

With Bedrock's `CacheConfig(strategy="auto")`, the prompt cache covers the system prompt + 2 tool definitions on every turn. Multi-turn sessions benefit progressively as prior turns become part of the cached prefix.

Compare this with Pattern A (Intent Profiles) where the cache depends on which profile was selected, or Pattern B (Catalog Tool) where all tool definitions are still in the prompt.

---

## Trade-Offs and Model Requirements

### Model Requirements

> **This is the most important trade-off.** Meta-tooling requires the model to reason about tool schemas presented as data (tool results) rather than as first-class tool definitions in the prompt. This is an unnatural task for models trained on standard tool-use formats.

| Model Class | Suitability | Notes |
|-------------|-------------|-------|
| Opus        | Reliable    | Handles multi-step meta-tool chains well |
| Sonnet      | Reliable    | Good balance of cost and accuracy |
| Haiku       | Use with caution | Accuracy drops on complex multi-step reasoning; observed 0.5 on multi-category tasks in evaluation. Must evaluate harshly. |

**Recommendation:** Always run trajectory evaluations comparing meta-tooling accuracy against a DirectAgent baseline (all tools registered directly). If accuracy drops below your threshold, meta-tooling is not appropriate for that model/task combination.

### Token Savings

Measured with 9 tools across 9 evaluation queries:

- **First cycle**: ~76% reduction (2 tool schemas vs. 9)
- **Total across multi-turn**: ~29% reduction (additional cycles add conversation overhead)
- **Best case** (simple, single-tool query): ~54% reduction
- **Worst case** (multi-step, multi-category): savings reduced by extra get_tool_info/use_tool cycles

### Extra Latency

Each tool use requires 2 calls instead of 1:
1. `get_tool_info(category)` — fetches the schema
2. `use_tool(category, tool_name, params)` — executes the tool

This adds one model round-trip per category accessed. For queries touching a single category, it's +1 round-trip. For queries spanning 3 categories, it's +3 round-trips.

### When Meta-Tooling Is a Good Fit

- 15–50+ tools in a single domain
- Mixed local and MCP tools that need uniform access
- Framework-agnostic requirement (no dependency on Bedrock beta features)
- Token cost matters more than response latency
- Model is Sonnet or Opus class

### When to Avoid Meta-Tooling

- Fewer than ~10 tools (overhead not justified — just register them directly)
- Latency-critical, single-tool queries (the extra round-trip hurts)
- Using a less capable model without extensive evaluation
- Tools require `ToolContext` / `agent.state` access (requires closure workaround)

---

## Comparison

| Dimension | Pattern A (Intent Profiles) | Pattern B (Catalog + Dynamic) | Pattern C (Meta-Tooling) |
|-----------|---------------------------|-------------------------------|--------------------------|
| Classification | Upfront required | None | None |
| Schema loading | Per-profile subset (append-only) | On demand via load_tool (append-only) | On demand via get_tool_info |
| Prompt growth | Fixed per profile | Grows as tools are loaded | Stable (always 2 tools) |
| Framework dependency | None | None | None |
| MCP support | Manual wiring | Local tools only | Transparent local/MCP routing |
| Cache stability | Profile-dependent | Breaks once per loaded tool | Stable (always 2 tools) |
| Extra cycles | 0 | +1 per tool loaded (one-time) | +2 per category accessed |
| ToolContext support | Full | Full (tools are first-class) | Requires closure workaround |
| Model requirement | Any | Any | Sonnet/Opus recommended |
| Status | Production-ready | Production-ready | **Experimental** |
