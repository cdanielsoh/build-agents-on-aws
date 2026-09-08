# Tool Design Patterns

Tools shape agent behavior more than prompts do. Design tools for progressive disclosure and deterministic correctness.

## Table of Contents

1. [Progressive Disclosure (Two-Layer Access)](#progressive-disclosure)
2. [Tool Results as Dynamic Prompts](#tool-results-as-dynamic-prompts)
3. [Closure Factory Pattern](#toolcontext-and-closure-factory-patterns)
4. [Anti-Patterns](#anti-patterns)
5. [Human-in-the-Loop (Interventions)](#human-in-the-loop-interventions)
6. [When NOT to Use Progressive Disclosure](#when-not-to-use-progressive-disclosure)

---

## Progressive Disclosure

Give the agent access to data incrementally: summaries first, details on demand. This prevents context window bloat and reduces token cost.

> **Schema-level progressive disclosure** — a distinct, experimental pattern where tool *schemas* (not data) are disclosed progressively. Instead of loading all tool definitions into the prompt, the agent sees a brief catalog and fetches full schemas on demand. Only reliable with Sonnet/Opus. See [meta-tooling.md](meta-tooling.md).

### Two-Layer Access

**Layer 1 — Summaries** (list views): names, statuses, counts. Enough for the agent to understand the situation and decide what to drill into.

**Layer 2 — Details** (drill-down): full data for a specific item. Called only when the current query requires it.

```python
@tool
def list_orders() -> dict:
    """List the customer's recent orders (summary only).
    Use get_order_details(order_number=N) for full details."""
    orders = fetch_orders(limit=10)
    return {
        "orders": [
            {"number": i, "date": o["date"], "status": o["status"], "total": o["total"]}
            for i, o in enumerate(orders, 1)
        ],
        "hint": "Call get_order_details(order_number=N) for item list, tracking, and return eligibility."
    }

@tool
def get_order_details(order_number: int) -> dict:
    """Get full details for a specific order.
    Call list_orders first to see available orders."""
    if not memory.has_context("orders"):
        return {"error": "Call list_orders first to see available orders"}
    return fetch_details(order_number)
```

### Navigation Hints

The summary response should tell the model what to do next. The `"hint"` field in `list_orders` guides the agent toward `get_order_details`. This is more reliable than hoping the model reads the other tool's docstring.

### Cost Impact

A 10-order summary is ~200 tokens. Full details for all 10 orders could be 5,000+ tokens. If the user asks "what's the status of my recent order?", the agent loads the summary (~200 tokens), finds the answer, and never loads full details. Without progressive disclosure, it would load everything regardless.

---

## Tool Results as Dynamic Prompts

Tools don't have to return just data — they can return targeted instructions that guide the agent for specific situations. The key idea is that the tool result becomes part of the conversation context, so instructions embedded in it steer the agent's next action without touching the cached system prompt.

### Pattern: Embedding Business Rules

```python
@tool
def lookup_rules(categories: list[str]) -> str:
    """Look up processing rules for the given categories.
    Call this before making decisions about returns, refunds, or escalations."""
    relevant = [RULES[cat] for cat in categories if cat in RULES]
    return "\n\n".join(relevant)  # formatted rule text, not raw data
```

The model reads the returned rules and applies them — dynamic injection of instructions that vary by category, region, or product type without bloating the system prompt.

### Pattern: Conditional Instructions Based on Results

Tool results can include different instructions depending on what the data looks like. This is powerful for guiding the agent through branching logic without hardcoding it in the system prompt.

```python
@tool
def search_products(query: str, category: str = "") -> dict:
    """Search the product catalog. Returns matching products with recommendations."""
    results = catalog.search(query=query, category=category)

    if not results:
        return {
            "products": [],
            "instruction": (
                "No products matched this search. Try broadening the query: "
                "remove adjectives, use synonyms, or drop the category filter. "
                "If still no results after retrying, let the customer know and "
                "suggest they browse popular items or contact support."
            ),
        }

    if len(results) > 10:
        return {
            "products": results[:10],
            "total_count": len(results),
            "instruction": (
                f"Showing 10 of {len(results)} results. Ask the customer to "
                "narrow their search if they don't see what they want."
            ),
        }

    return {"products": results, "total_count": len(results)}
```

The agent receives different guidance depending on the search outcome — retry strategies for empty results, narrowing suggestions for too many results, or just the data when the count is manageable. This pattern works well for any tool where the appropriate next step depends on what comes back:

- **Search tools**: empty results -> suggest broadening; too many -> suggest narrowing
- **Eligibility checks**: eligible -> proceed with action; ineligible -> explain why and suggest alternatives
- **Validation tools**: valid -> continue; invalid -> return specific error guidance
- **Inventory lookups**: in stock -> offer to add to cart; out of stock -> suggest similar items or waitlist

### When to Use This Pattern

- **Business rules** that vary by category, region, or product type — too numerous for the system prompt
- **Branching behavior** that depends on the data itself, not the user's query
- **Policy lookups** that the agent needs only in specific situations
- **Error recovery** instructions that guide the agent through fallback strategies

This keeps the system prompt lean (cached, stable) while still providing situation-specific guidance. The system prompt says *what* the agent is; tool results say *what to do right now*.

---

## ToolContext and Closure Factory Patterns

Tools have two ways to access dependencies: **ToolContext** for agent.state and per-request data, and **closures** for heavy external dependencies.

### ToolContext — Agent State and Per-Request Data

Tools decorated with `@tool(context=True)` receive a `ToolContext` parameter that provides direct access to `agent.state` (durable metadata that survives conversation management) and `invocation_state` (per-request data passed via `agent(prompt, **kwargs)`).

```python
from strands import tool
from strands.types.tools import ToolContext

@tool(context=True)
def get_account_info(tool_context: ToolContext) -> dict:
    """Retrieve the current user's account information."""
    user_id = tool_context.agent.state.get("user_id")  # set by builder
    return fetch_account(user_id)
```

No custom context class, no wiring — the agent injects `ToolContext` at call time. Pass
session-level data as the agent's initial state rather than mutating it after construction:

```python
def _build_state(self, token) -> dict:
    return {"user_id": resolve_actor_id(token)}

# ... then Agent(..., state=self._build_state(token))
```

See `templates/strands-agentcore/agent/tools/example_tool.py` and `templates/strands-agentcore/agent/core/builder.py`.

### Closures — Heavy External Dependencies

For dependencies that shouldn't live in a state dict (DB clients, API wrappers, connection pools), closures are still the right pattern:

```python
def create_search_tool(db_client):
    @tool
    def search_records(query: str) -> dict:
        """Search records in the database."""
        return db_client.search(query)  # db_client captured by closure
    return search_records
```

Override `_build_tools()` in a SessionBuilder subclass to inject these:

```python
class MyBuilder(SessionBuilder):
    def _build_tools(self):
        db_client = self._create_db_client()
        return make_tools() + [create_search_tool(db_client)]
```

### When to Use Which

| Access pattern | Use |
|----------------|-----|
| user_id, session metadata, entity mappings | `tool_context.agent.state` |
| Per-request data (request_id, trace context) | `tool_context.invocation_state` |
| DB clients, API wrappers, connection pools | Closure factory |

---

## Anti-Patterns

### God Tools

Tools that handle multiple unrelated operations via a `type` parameter:

```python
# BAD: one tool doing everything
@tool
def do_action(type: str, id: int, reason: str = "") -> dict:
    """Perform an action: 'refund', 'cancel', 'escalate', or 'lookup'."""
    ...
```

```python
# GOOD: separate tools with clear purpose
@tool
def process_refund(order_number: int, reason: str) -> dict: ...

@tool
def cancel_order(order_number: int) -> dict: ...

@tool
def escalate_to_human(reason: str) -> dict: ...
```

God tools make it harder for the model to choose correctly, produce worse tool descriptions (the model sees one vague tool instead of several specific ones), and make debugging harder (which `type` was called?).

### Returning Entire Collections

```python
# BAD: returns all 200 orders with full details
@tool
def get_all_orders() -> list[dict]: ...

# GOOD: returns summaries with pagination hint
@tool
def list_orders(page: int = 1) -> dict:
    """List orders (10 per page). Returns summary only."""
    ...
```

### Duplicating Agent Reasoning

```python
# BAD: the model can do sentiment analysis itself
@tool
def analyze_sentiment(text: str) -> str: ...

# GOOD: tools provide data the model can't access
@tool
def fetch_order_data(order_number: int) -> dict: ...
```

Tools should provide capabilities the model lacks — database access, API calls, calculations. If the model can do it with its own reasoning, a tool adds latency with no benefit.

### Missing Access Guards

```python
# BAD: user_id as a tool parameter — prompt injection can change it
@tool
def get_order(user_id: str, order_id: str) -> dict: ...

# GOOD: user_id from agent.state — not exposed to the agent
@tool(context=True)
def get_order(tool_context: ToolContext, order_number: int) -> dict:
    """Get order details by number."""
    user_id = tool_context.agent.state.get("user_id")
    mapper = EntityIndexMapper(tool_context.agent.state, "orders")
    real_id = mapper.get_real_id(order_number)
    return fetch_order(user_id, real_id)
```

Never include identity-based parameters (user_id, account_id, tenant_id) in tool signatures. A prompt injection attack could manipulate the agent into passing a different identity. Use `agent.state` (via ToolContext) or closures instead.

`agent.state` solves the *input* problem (hiding `user_id` from parameters). But real database IDs in tool *results* are equally dangerous — see [security-patterns.md](security-patterns.md) for EntityIndexMapper (hiding real IDs from the agent). Each tool should explicitly select which fields to return rather than passing through raw data.

---

## Human-in-the-Loop (Interventions)

Some tools perform irreversible or high-impact actions — deleting data, processing payments, sending communications. Rather than trusting the agent unconditionally, gate these tools with a confirmation interrupt that pauses the agent and asks the user before proceeding.

### When to Add a Confirmation Gate

| Signal | Example |
|--------|---------|
| Action is irreversible | Deleting files, canceling subscriptions |
| Action has financial impact | Processing refunds, placing orders |
| Action is externally visible | Sending emails, posting to APIs |
| Compliance requires audit trail | Any regulated domain |
| Confidence in tool selection matters | Ambiguous user intent with destructive options |

Read-only tools (searches, lookups, summaries) should never require confirmation — that adds latency with no safety benefit.

### Pattern: InterventionHandler (Strands 1.51+)

Use `Agent(interventions=[...])` with an `InterventionHandler`. This supersedes the older
"`HookProvider` that calls `event.interrupt()`" pattern — it is declarative, composable,
and covers deny/guide/transform as well as confirm.

```python
from strands import Agent
from strands.interventions import Confirm, InterventionHandler, Proceed

GATED = {"delete_files", "send_email", "process_refund"}


class ApprovalGate(InterventionHandler):
    # `name` is required, and lifecycle methods must be overridden at CLASS level —
    # assigning handler.before_tool_call = fn at runtime is not detected.
    name = "approval-gate"

    def before_tool_call(self, event):
        name = event.tool_use["name"]
        if name not in GATED:
            return Proceed()
        return Confirm(
            prompt=f"Allow {name}?",
            reason={"action": name, "input": event.tool_use["input"]},
        )


agent = Agent(..., interventions=[ApprovalGate()])
```

### Actions

| Action | Effect | Where |
|---|---|---|
| `Proceed(reason=None)` | Allow unchanged | any lifecycle method |
| `Confirm(prompt, reason, response=None, evaluate=...)` | Request human approval | **`before_tool_call` only** |
| `Deny(reason)` | Block; `reason` is shown to the model as the cancellation message | any |
| `Guide(feedback)` | Let it run but steer the model with feedback | any |
| `Transform(apply)` | Mutate the event in place | any |

Lifecycle methods: `before_invocation`, `before_model_call`, `after_model_call`,
`before_tool_call`, `after_tool_call`. Only overridden methods are called.

### Key Mechanics

- **`Confirm` has two modes.** Without `response`, it breaks out of the agent loop and
  pauses for an external resume — this is the interactive case. With `response` supplied,
  the value is fed to the interrupt system preemptively and the agent never pauses, which
  is what you want in tests and batch runs.
- **Handlers may be `async`.** The registry awaits any override that returns an awaitable,
  so you can await a database lookup, an authorization call, or a human approval prompt
  before deciding. This is what makes `Deny` usable for real authorization.
- **`evaluate`** on `Confirm` converts an arbitrary response into a bool, so you are not
  locked into comparing against `"y"`.

### Design Considerations

- **Gate explicitly, or gate by assessed risk — not everything.** A hard-coded set is the
  simple option. Strands 1.51 also ships an LLM-driven risk classifier, which gates on
  what an action *does* rather than on a name you remembered to add to a list; it costs a
  classification call per tool use, so reserve it for genuinely open tool surfaces.
- **Include context in `reason`.** Enough for the user to decide: what gets deleted, how
  much is charged, who receives the email.
- **The agent doesn't see the pause.** From its perspective the tool either executes or
  returns a cancellation message. Write the `Deny` reason so the agent can explain the
  refusal to the user gracefully.
- **Authorization belongs here too.** `before_tool_call` returning `Deny` is the natural
  seam for policy checks — see the `deploy-on-agentcore` skill's `references/policy.md`
  for enforcing the same decisions at the Gateway with Cedar.

For how interrupts flow through AgentCore Runtime's streaming protocol (SSE events, `stopReason`, resume payloads), see the `deploy-on-agentcore` skill's `references/streaming-backend.md`.

---

## When NOT to Use Progressive Disclosure

Progressive disclosure adds one tool call round-trip per drill-down. For some use cases, this latency cost outweighs the token savings.

### Pre-Load When Data Is Always Needed

If every query requires account info, pre-populate it in the user message instead of making the agent call a tool:

```python
# In the app layer, before invoking the agent
account = fetch_account(user_id)
prompt = f"""## Account (pre-populated)
{json.dumps(account)}

## Question
{user_question}"""
```

### Pre-Load When Latency Is Critical

Each tool call costs ~200-500ms. For sub-2-second response targets, minimize round-trips by loading known-needed data in the app layer.

### Keep Progressive Disclosure When

- Data is large and the agent often doesn't need all of it
- The user's intent determines which data is relevant
- You want the agent to decide what to explore (conversational interactions)
- Context window efficiency matters more than response latency
