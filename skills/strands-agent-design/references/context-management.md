# Context Window Pressure Management

Manage context window growth in multi-turn agentic loops. Three-tier compaction system adapted from Claude Code's microcompact architecture for Bedrock's prefix-matching cache constraints.

## Table of Contents

1. [The Problem: Context Growth in Agentic Loops](#the-problem)
2. [Bedrock Cache Constraints](#bedrock-cache-constraints)
3. [Tier 1: Time-Based Tool Result Clearing](#tier-1-time-based-clearing)
4. [Tier 2: Count-Based Tool Result Clearing](#tier-2-count-based-clearing)
5. [Tier 3: Message Removal](#tier-3-message-removal)
6. [Using agent.state for Durable Metadata](#durable-metadata)
7. [Configuration Guide](#configuration-guide)
8. [Integration with SessionBuilder](#integration)

---

## The Problem

In agentic loops, tool results accumulate rapidly. A 10-turn conversation with 3 tool calls per turn produces 30 tool results, each ranging from 500 to 5,000+ tokens. Without management, context grows to 50K–150K tokens while most older results are no longer relevant.

```
Turn  1:  system + tools + user + 3 tool results     ~15K tokens
Turn  5:  ... + 15 tool results                       ~45K tokens
Turn 10:  ... + 30 tool results                       ~90K tokens
Turn 15:  ... + 45 tool results → approaching limit   ~135K tokens
```

The model needs recent context to make decisions, but tool results from turn 2 rarely matter at turn 15. Context management reclaims space from stale results while preserving what matters.

### Why Not Just Use SlidingWindowConversationManager?

Strands' default `SlidingWindowConversationManager` (40 messages) handles the extreme case — it removes the oldest messages when the window is full. But it's a blunt instrument:

- It removes entire messages (including useful user/assistant exchanges)
- It has no awareness of cache TTL — it clears content that might have been cacheable
- It doesn't differentiate between a 5,000-token file read result and a 50-token status check

The `CacheSafeConversationManager` in the template (`templates/strands-agentcore/agent/core/conversation.py`) adds cache-aware, tool-result-specific clearing before falling back to message removal.

---

## Bedrock Cache Constraints

These constraints shape every design decision in this document:

| Constraint | Impact |
|------------|--------|
| **Prefix-matching cache only** | No `cache_control` breakpoints, no surgical deletion. The cache matches the longest identical prefix of `[system prompt, tools, messages...]`. |
| **~5-minute TTL** | Much shorter than Anthropic 1P (~1 hour). If a user goes idle for 5+ minutes, the cache expires and the entire prefix is recomputed on the next turn. |
| **No `cache_edits`** | Cannot surgically delete KV pages while preserving the cache key. Modifying any message content changes the prefix, which invalidates the cache. |
| **No global cache scope** | Each session has its own cache. Cross-session sharing is minimal (sessions diverge at the first user message). |
| **`CacheConfig(strategy="auto")`** | Strands automatically places a cache point at the end of the last user message. Prior content is the cached prefix. |

### The Key Insight: When Clearing Is Free

Modifying message content breaks the prefix cache — **except when the cache is already cold**. If the user has been idle for longer than the cache TTL, the cached prefix has already expired. Clearing old tool results in this window costs nothing — the prefix would be fully recomputed regardless.

This is the foundation of Tier 1: detect cache expiry, clear aggressively while it's free.

```
Cache warm (< 5 min gap):
  Clearing content → breaks cache → costs extra tokens next turn
  Only clear if context pressure demands it (Tier 2).

Cache cold (> 5 min gap):
  Clearing content → no cache to break → free optimization
  Clear aggressively to shrink the recomputation payload (Tier 1).
```

---

## Tier 1: Time-Based Clearing

**Trigger:** Gap since last model call exceeds `cache_ttl_minutes` (default: 5.0 minutes).

**Action:** Replace old tool result content with `"[Tool result cleared]"`, keeping the last `keep_recent` results.

**Cost:** Zero — the cache is already cold.

### How It Works

The `CacheSafeConversationManager` stores the timestamp of each model call via `agent.state.set("_cm_last_model_call", ...)`. On the next invocation, it computes the gap:

```python
last_call = agent.state.get("_cm_last_model_call")
gap_minutes = (time.time() - last_call) / 60.0
if gap_minutes >= config.cache_ttl_minutes:
    # Cache is cold — clear old tool results for free.
    clear_old_tool_results(messages, keep_recent=config.keep_recent)
```

### Why agent.state?

The timestamp must survive across invocations. `agent.state` is persistent state that lives independently of message history — conversation management (clearing, summarization) never touches it. Even if Tier 3 removes old messages, the timestamp remains.

### What Gets Cleared

Only tool results from "compactable" tools are cleared. By default, all tool results are eligible. To protect critical context (e.g., a `get_context` tool whose result guides every turn), set `compactable_tool_names`:

```python
ContextManagerConfig(
    compactable_tool_names=frozenset({
        "read_file", "search_code", "list_items", "web_search",
    }),
    # get_context, get_account_info are NOT in the set — never cleared.
)
```

### What Gets Preserved

The last `keep_recent` (default 5) tool results are always preserved, regardless of age. This ensures the model has enough recent context to continue working. Floor of 1 — the model always sees at least the most recent result.

---

## Tier 2: Count-Based Clearing

**Trigger:** Total compactable tool results exceed `trigger_threshold` (default: 15).

**Action:** Same as Tier 1 — replace old content with placeholder, keep last `keep_recent`.

**Cost:** Cache break on the next turn. Use this only when context pressure justifies it.

### Trade-Off

Tier 2 fires when the cache is warm, so clearing content changes the prompt prefix and invalidates the cached KV computation. The next turn pays the full recomputation cost. But:

- If you're at 15+ tool results and approaching the context limit, a cache miss is cheaper than running out of context window entirely.
- The miss is one-time — after the next turn, the new (shorter) prefix gets cached and subsequent turns benefit.

### When Tier 2 Fires vs. Doesn't

```
12 tool results, cache warm → Tier 2 skipped. No action.
18 tool results, cache warm → Tier 2 fires. Clears 13, keeps 5. Cache breaks.
18 tool results, cache cold → Tier 1 already fired. Tier 2 skipped.
```

Tier 1 short-circuits Tier 2: if the cache is cold, Tier 1 handles clearing (for free) and Tier 2 doesn't need to fire.

---

## Tier 3: Message Removal

**Trigger:** Estimated token count exceeds `summarize_threshold_pct * context_window_tokens` (default: 80% of 200K = 160K).

**Action:** Remove the oldest messages until under the threshold.

### Relationship to Strands' Built-In Managers

The template's Tier 3 does simple message removal (like `SlidingWindowConversationManager`). For smarter handling, consider:

- **`SummarizingConversationManager`**: Uses the model itself to summarize older messages before removing them. Preserves more context but costs an extra model call.
- **Custom override**: Subclass `CacheSafeConversationManager` and override `_maybe_trim_messages()` to implement model-based summarization.

```python
class SummarizingCacheSafeManager(CacheSafeConversationManager):
    def _maybe_trim_messages(self, agent, messages):
        # Model-based summarization instead of simple removal.
        ...
```

### Why Tier 3 Runs Independently

Tiers 1 and 2 target tool result content. Tier 3 targets message count. A conversation can have moderate tool results but many short user/assistant exchanges that accumulate. Tier 3 catches this case even when Tiers 1 and 2 don't fire.

---

## Using agent.state for Durable Metadata

`agent.state` is a dict that persists across all invocations of the same agent instance. Unlike message history, it is never modified by conversation management. This makes it the right place for metadata that must survive clearing and summarization.

### What the ConversationManager Stores

| Key | Type | Purpose |
|-----|------|---------|
| `_cm_last_model_call` | `float` | Timestamp of last model call (Tier 1 gap calculation) |
| `_cm_cleared_count` | `int` | Running total of cleared tool results (observability) |
| `_cm_tier1_fires` | `int` | How many times Tier 1 triggered |
| `_cm_tier2_fires` | `int` | How many times Tier 2 triggered |

All keys are prefixed with `_cm_` to avoid collisions with your application state.

### Your Application State

Use `agent.state` for any data that must outlive conversation management:

```python
# Entity index mappings — survive even if the list_orders tool result is cleared.
agent.state.set("entity_mappings", mapper.to_dict())

# Session context — survives summarization.
agent.state.set("customer_tier", "premium")
agent.state.set("conversation_topic", "billing_dispute")
```

When a tool result gets cleared, the data it contained is gone from the model's context. But if you stored the critical metadata in `agent.state`, you can re-inject it via a tool call or pre-populated context on the next turn.

### Pattern: Durable Entity Mappings

The `EntityIndexMapper` from [security-patterns.md](security-patterns.md) maps real IDs to sequential indices. Because the mapper uses `agent.state` as its backing store, mappings automatically survive tool result clearing — no manual serialization needed.

```python
@tool(context=True)
def list_orders(tool_context: ToolContext) -> dict:
    state = tool_context.agent.state
    mapper = EntityIndexMapper(state, namespace="orders")
    orders = fetch_orders(state.get("user_id"))
    mapper.store_entities(orders, id_field="order_id")
    # Mappings are already in agent.state — nothing extra to persist.
    return {"orders": [...], "hint": "Use get_order_details(order_number=N)"}

@tool(context=True)
def get_order_details(tool_context: ToolContext, order_number: int) -> dict:
    mapper = EntityIndexMapper(tool_context.agent.state, namespace="orders")
    # Mappings survive even if list_orders tool result was cleared.
    real_id = mapper.get_real_id(order_number)
    ...
```

This works because `agent.state` is never touched by conversation management. When Tier 1 or Tier 2 clears the `list_orders` tool result, the model loses the displayed order list — but the index-to-ID mappings remain in `agent.state`, so `get_order_details` still resolves correctly.

---

## Configuration Guide

| Parameter | Default | When to Change |
|-----------|---------|----------------|
| `cache_ttl_minutes` | `5.0` | Set to `60.0` if using Anthropic 1P API instead of Bedrock. |
| `keep_recent` | `5` | Increase if your agent frequently references older results. Decrease if tools return very large results. |
| `trigger_threshold` | `15` | Increase if cache breaks are costly (long system prompts). Decrease if context window is tight. |
| `summarize_threshold_pct` | `0.80` | Lower (e.g., 0.70) for safety margin. Higher for maximum context utilization. |
| `context_window_tokens` | `200_000` | Set to your model's actual context window (200K for Sonnet, 200K for Opus, etc.). |
| `compactable_tool_names` | `None` (all) | Set to protect critical context tools from clearing. |
| `cleared_placeholder` | `"[Tool result cleared]"` | Customize if you want the model to know what was cleared (e.g., `"[File content cleared — re-read if needed]"`). |

### Example: High-Throughput API Agent

```python
ContextManagerConfig(
    cache_ttl_minutes=5.0,
    keep_recent=3,          # Aggressive — only keep last 3
    trigger_threshold=10,   # Clear early to stay lean
    compactable_tool_names=frozenset({"search_api", "list_records", "read_document"}),
)
```

### Example: Long-Running Research Agent

```python
ContextManagerConfig(
    cache_ttl_minutes=5.0,
    keep_recent=10,          # Keep more context for research continuity
    trigger_threshold=25,    # Tolerate more accumulation
    summarize_threshold_pct=0.70,  # Safety margin for large tool results
)
```

---

## Integration with SessionBuilder

The template's `SessionBuilder` wires in the conversation manager via `_build_conversation_manager()`:

```python
class SessionBuilder:
    def _build_conversation_manager(self):
        return CacheSafeConversationManager(
            ContextManagerConfig(cache_ttl_minutes=5.0, keep_recent=5)
        )

    def _build_agent(self, tools, hooks, session_manager=None):
        conversation_mgr = self._build_conversation_manager()
        kwargs = {"model": self._build_model(), "tools": tools, ...}
        if conversation_mgr:
            kwargs["conversation_manager"] = conversation_mgr
        return Agent(**kwargs)
```

Override `_build_conversation_manager()` in a subclass to customize:

```python
class MyBuilder(SessionBuilder):
    def _build_conversation_manager(self):
        return CacheSafeConversationManager(
            ContextManagerConfig(
                keep_recent=3,
                trigger_threshold=10,
                compactable_tool_names=frozenset({"search_api", "read_document"}),
            )
        )
```

Return `None` to fall back to Strands' default `SlidingWindowConversationManager`:

```python
class SimpleBuilder(SessionBuilder):
    def _build_conversation_manager(self):
        return None  # Use Strands default (40-message sliding window).
```
