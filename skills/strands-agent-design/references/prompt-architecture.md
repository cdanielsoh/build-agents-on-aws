# Prompt Architecture

Structure prompts to maximize cache hits and minimize token waste. Use tool results for dynamic context injection.

## Table of Contents

1. [The Prompt Stack](#the-prompt-stack)
2. [File-Based System Prompts](#file-based-system-prompts)
3. [Dynamic Context via Tool Results](#dynamic-context-via-tool-results)
4. [Pre-Populated Context](#pre-populated-context)
5. [Conditional Prompt Sections](#conditional-prompt-sections)

---

## The Prompt Stack

Bedrock caches the longest matching prefix of the conversation. Understanding this determines where to put each piece of information.

```
┌─────────────────────────────────┐
│ System Prompt (STATIC)          │ ← Cached by Bedrock. Load from file.
│  - Persona, role, output format │    Change only on deploy.
│  - Domain rules (from .md files)│
├─────────────────────────────────┤
│ Tool Definitions (AUTO-CACHED)  │ ← Bedrock caches these automatically.
│  - @tool docstrings             │    Change only when tools change.
├─────────────────────────────────┤
│ User Message (DYNAMIC)          │ ← Changes every request.
│  - Pre-populated context        │    Keep minimal — what agent needs NOW.
│  - Conditional sections         │
├─────────────────────────────────┤
│ Tool Results (DYNAMIC)          │ ← Agent-driven discovery.
│  - Progressive disclosure data  │    Loaded on demand.
└─────────────────────────────────┘
```

**Move content UP this stack whenever possible.** Every byte in the system prompt is cached; every byte in user messages is not.

### Enabling Prompt Caching in Strands

Strands Agents supports automatic prompt caching via `BedrockModel` with `CacheConfig`. When enabled, the SDK places a cache point at the end of the last user message on each turn, so all prior content becomes the cached prefix for the next turn.

```python
from strands import Agent
from strands.models.bedrock import BedrockModel, CacheConfig

model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    cache_config=CacheConfig(strategy="auto"),
)

agent = Agent(
    model=model,
    tools=tools,
    system_prompt=build_system_prompt(),
)
```

The `"auto"` strategy handles cache point placement automatically — no manual management needed. This works with Claude models on Bedrock (Sonnet, Haiku, Opus).

### How Prompt Caching Works in Practice

The cache matches the **longest identical prefix** of the full message array — system prompt, tool definitions, and all prior conversation turns up to the cache point.

**Multi-turn sessions benefit the most.** On turn 1, the system prompt and tool definitions form the cached prefix. On turn 2, the prefix extends to include the system prompt + tools + turn 1 messages. By turn 5, the cached prefix covers everything through turn 4. Each new turn only processes the latest user message and any new tool results — the rest is a cache hit.

**Cross-user cache hits are minimal.** Each session has its own conversation history. Even with an identical system prompt, two different users diverge at the first user message, so the cache only covers the system prompt + tool definitions — not the conversation. The real savings come from multi-turn depth within a single session, not from sharing across sessions.

### Why This Matters at Scale

For a 10K-token system prompt, busting the cache means re-processing those 10K tokens on every single turn. In a multi-turn conversation with 20 turns, that's 200K wasted tokens — per session. At thousands of sessions per day, this dominates your token cost.

The system prompt and tool definitions form the base cached prefix. As long as they don't change between turns, Bedrock reuses the cached computation. On subsequent turns, prior conversation messages also become part of the cached prefix — each turn only pays for the new content added since the last turn.

### The Key Insight

If some information is the same across all turns (persona, rules, output format), it belongs in the system prompt. If it varies per user or per session (account data, order details), inject it via tool results — not by templating it into the system prompt.

Templating per-user data into the system prompt doesn't just bust the cross-user cache (which is already minimal) — it forces every turn in that user's session to re-process a different system prompt prefix, eliminating multi-turn caching benefits too. Keep the system prompt identical for all users, and let tool results carry the per-user data.

---

## The Three-Tier Boundary Model

The prompt stack above shows _what_ goes where. The boundary model formalizes _why_, with Bedrock's constraints made explicit.

```
┌────────────────────────────────────────────────────────────────┐
│ TIER 1: System Prompt (Purely Static)                          │
│   Set once at Agent() construction. Cannot change mid-session. │
│   Cached across ALL turns within a session.                    │
│   Persona, rules, output format, static instructions.          │
├────────────────────────────────────────────────────────────────┤
│ TIER 2: First User Message (Session-Specific)                  │
│   Set once in the first agent(prompt) call.                    │
│   Cached from turn 2 onward (part of the prefix by then).     │
│   User tier, pre-populated account data, relevant rule subsets.│
├────────────────────────────────────────────────────────────────┤
│ TIER 3: Tool Results (Per-Query Dynamic)                       │
│   Loaded on demand via tool calls during the agent loop.       │
│   Part of the current turn — cached starting NEXT turn.        │
│   Order details, search results, API responses.                │
└────────────────────────────────────────────────────────────────┘
```

### Tier 1: System Prompt

In Strands, the system prompt is an init-time parameter on `Agent()`. It cannot change mid-session. This is a constraint, but it aligns perfectly with caching — a stable system prompt means the prefix never changes at the system-prompt level.

**Acceptable:** Per-session content in the system prompt (e.g., composing different `.md` files based on the request type). This varies between sessions but stays constant within a session. From turn 2 onward, it's fully cached.

**Danger zone:** Reconstructing the agent each turn to change the system prompt. This is catastrophic — every turn gets a different prefix, eliminating all multi-turn caching benefits. If you need per-turn dynamic content, use Tier 2 or Tier 3.

### Tier 2: First User Message

The first `agent(prompt)` call is an opportunity to inject session-specific context that the model needs on every subsequent turn. Embed it in the first message:

```python
# First invocation — includes session context.
first_prompt = f"""## Context (pre-populated)
Customer tier: {customer.tier}
Account age: {customer.account_age_days} days
Recent orders: {len(customer.recent_orders)}

## Question
{user_question}"""

response = agent(first_prompt)

# Subsequent invocations — just the user's question.
response = agent(follow_up_question)
```

By turn 2, the first message (including its context) is part of the cached prefix. The context is available to the model on every subsequent turn at zero additional cost.

### Tier 3: Tool Results

Data loaded via tool calls during the agent loop. This is the most flexible tier — the model decides what to load and when. But each tool call costs a round-trip (~200-500ms), and results aren't cached until the next turn.

Use Tier 3 for data that:

- Might not be needed (let the model decide)
- Is large (progressive disclosure avoids bloating every message)
- Changes between turns (re-calling the tool gets fresh data)

### Putting It Together

A customer support agent with all three tiers:

```python
# Tier 1: System prompt — static, set at agent init.
agent = Agent(
    model=model,
    tools=tools,
    system_prompt=build_system_prompt(),  # "You are a support agent for Acme Corp..."
)

# Tier 2: First message — session context, cached from turn 2.
response = agent(f"""## Context
Tier: premium | Account: 3 years | Open tickets: 2

## Question
{user_question}""")

# Tier 3: Tool results — loaded on demand during the agent loop.
# The model calls list_orders(), get_order_details(), etc.
```

---

## Cache TTL Awareness

Bedrock's prompt cache has a TTL of approximately **5 minutes**. Anthropic's 1P API is approximately 1 hour. This difference matters for agents with idle gaps between turns.

### What Happens When the Cache Expires

If a user submits a message after being idle for more than the cache TTL, the entire cached prefix is gone. The next API call recomputes everything — system prompt, tool definitions, and all prior conversation turns up to the cache point.

```
Turn 5 (0 min gap):   Cache hit — only processes new content.     ~500 input tokens
Turn 6 (1 min gap):   Cache hit — prefix still warm.              ~500 input tokens
Turn 7 (8 min gap):   Cache MISS — prefix expired, full reprocess. ~90K input tokens
Turn 8 (30 sec gap):  Cache hit — new prefix cached at turn 7.    ~500 input tokens
```

### Mitigation: Clear Before Recomputation

When the cache is cold, the full prefix will be recomputed regardless. This is the optimal time to clear stale tool results — the clearing is free because there's no cache to break.

The `CacheSafeConversationManager` (see [context-management.md](context-management.md)) detects cache expiry and clears old tool results before the API call, shrinking the recomputation payload:

```
Turn 7 without clearing:  Full reprocess of 90K tokens (30 old tool results).
Turn 7 with clearing:     Full reprocess of 40K tokens (5 recent tool results).
```

Same cache miss, 55% fewer tokens to reprocess.

### Practical Guidance

- **High-engagement sessions** (turns every 30s): Cache stays warm. Tiers 1 rarely fires. Rely on Tier 2 (count-based) for pressure management.
- **Idle sessions** (5+ minute gaps): Cache expires between turns. Tier 1 fires regularly, keeping payloads lean.
- **Batch/API agents** (single invocation): No multi-turn caching benefit. Focus on Tier 1 system prompt stability and tool result size.

---

## File-Based System Prompts

Load system prompts from `.md` files at startup. This separates content from code: prompt engineers edit markdown, developers wire the loading.

### Basic Pattern

```python
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent

def build_system_prompt() -> str:
    return (_PROMPT_DIR / "system.md").read_text()
```

The template demonstrates this at `templates/strands-agentcore/agent/prompts/system.py`.

### Multi-Section Composition

For agents with multiple domains or configurable behavior, compose from multiple files:

```python
def build_system_prompt(include_rules: bool = True) -> str:
    parts = [(_PROMPT_DIR / "identity.md").read_text()]
    if include_rules:
        parts.append((_PROMPT_DIR / "rules.md").read_text())
    return "\n\n".join(parts)
```

Benefits:

- **Version-controlled**: prompts live alongside code, diff-able in PRs
- **Reviewable**: non-engineers can review and edit `.md` files
- **Composable**: conditional inclusion of sections based on config
- **Cacheable**: the composed string is constant across turns

### Wiring in the Builder

The `SessionBuilder` constructs the model with caching enabled and passes it along with the file-based system prompt to the Agent. Since the Agent is a singleton for the session, prompt and cache config are set once.

```python
def _build_model(self) -> BedrockModel:
    """Build the model with automatic prompt caching."""
    return BedrockModel(
        model_id=self.config.model_id,
        cache_config=CacheConfig(strategy="auto"),
    )

def _build_agent(self, tools, hooks, session_manager=None):
    return Agent(
        model=self._build_model(),
        tools=tools,
        system_prompt=build_system_prompt(),  # loaded once from .md file
    )
```

See `templates/strands-agentcore/agent/core/builder.py` for the full implementation with `_build_model()` as a separate overridable step.

---

## Dynamic Context via Tool Results

The problem: you need per-user data (account info, order history, preferences) available to the agent. Templating it into the system prompt busts the cache on every request.

The solution: make it a tool. The model calls the tool when it needs context, and the result enters the conversation as a `tool_result` message — after the cached prefix.

### Pattern: Context Tool with Closure

This example uses a **closure** because `data_source` is a heavy external dependency (database client, API wrapper, connection pool) that shouldn't live in a serializable state dict. For simple state like user_id or session metadata, use `agent.state` via ToolContext instead — see [tool-design.md](tool-design.md#toolcontext-and-closure-factory-patterns) for when to use which.

```python
from strands import tool

def create_context_tool(data_source):
    @tool
    def get_context(profile: bool = False, orders: bool = False) -> dict:
        """Load user context. Data is cached — subsequent calls return instantly.

        Args:
            profile: Load user profile (name, email, tier)
            orders: Load recent orders (summary only)
        """
        result = {}
        if profile:
            result["profile"] = data_source.get_profile()
        if orders:
            result["orders"] = data_source.get_recent_orders(limit=5)
            result["hint"] = "Use get_order_details(number=N) for full details"
        return result

    return get_context
```

The closure captures `data_source` at construction time — the tool exposes no user ID parameters and the dependency is explicit in the factory signature.

### Why This Preserves Cache

The system prompt stays identical across all users and turns. Per-user data enters as a `tool_result`, which is part of the conversation messages — after the cached prefix. The model pays the cost of one tool call round-trip, but every subsequent turn benefits from the cached prefix.

### Guiding the Agent to Call Context Tools

Include a brief mention in the system prompt:

```markdown
When a user asks about their account, call get_context(profile=True) first.
When they ask about orders, call get_context(orders=True).
```

This is a static instruction (cached) that triggers dynamic data loading (not cached). The instruction never changes; the data it produces changes per user.

---

## Pre-Populated Context

When a deterministic step (webhook, app routing, prior pipeline stage) has already gathered data before invoking the agent, embed it in the user message. This saves a tool call round-trip.

```python
# The app layer already knows the customer tier from the auth token
context = gather_context(auth_token)  # fast, deterministic

prompt = f"""## Context (pre-populated — no need to call get_context)
{json.dumps(context, indent=2)}

## Customer Question
{user_question}"""

result = agent(prompt)
```

### When to Use Pre-Populated Context

- The data is **already available** before the agent runs (from auth, routing, a prior deterministic step)
- **Latency is critical** — saving a tool call round-trip matters (each tool call costs ~200-500ms)
- The data is **always needed** regardless of what the user asks

### When NOT to Use It

- The data **might not be needed** — let the agent decide via tool calls
- The data is **large** — it would bloat every user message. Use progressive disclosure instead
- The data **varies by query type** — use conditional sections or let the agent call the appropriate tool

### The "Pre-Populated" Signal

Always include an explicit instruction like `"pre-populated — no need to call get_context"`. Without this, the model may redundantly invoke the context tool, wasting a round-trip and duplicating data in the conversation.

---

## Conditional Prompt Sections

Build the user message dynamically based on input characteristics. Only include sections relevant to the current request. This keeps the prompt focused and avoids confusing the model with irrelevant instructions.

```python
def build_user_message(request: str, metadata: dict) -> str:
    parts = [f"## Request\n{request}"]

    if metadata.get("categories"):
        parts.append(f"## Detected Categories\n{metadata['categories']}")

    if metadata.get("is_mutation"):
        parts.append(f"## Mutation Rules\n{MUTATION_RULES}")

    if metadata.get("previous_attempts"):
        parts.append(
            f"## Previous Attempts (failed)\n"
            f"{format_attempts(metadata['previous_attempts'])}"
        )

    return "\n\n".join(parts)
```

### Why This Pattern Matters

Irrelevant instructions add noise. If the user asked a simple account question, including "Mutation Rules" and "Image Analysis Guidelines" wastes tokens and can confuse the model into thinking those capabilities are relevant.

Conditional sections keep the user message tight: only what the current query needs. The system prompt (cached) contains the general persona and capabilities. The user message (dynamic) contains the specific request and its relevant context.

### Combining with Pre-Populated Context

These patterns compose naturally:

```python
def build_user_message(request: str, metadata: dict, pre_loaded: dict | None = None) -> str:
    parts = [f"## Request\n{request}"]

    if pre_loaded:
        parts.append(
            f"## Context (pre-populated — no need to call get_context)\n"
            f"{json.dumps(pre_loaded, indent=2)}"
        )

    if metadata.get("is_mutation"):
        parts.append(f"## Mutation Rules\n{MUTATION_RULES}")

    return "\n\n".join(parts)
```

The agent receives a focused prompt with exactly the context and instructions it needs — nothing more, nothing less.
