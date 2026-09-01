# Context Window Pressure Management

Manage context growth in multi-turn agentic loops on Bedrock.

> **Validated against `strands-agents` 1.54.0.** Earlier revisions of this document
> described a hand-rolled `CacheSafeConversationManager` with a three-tier compaction
> scheme. **That code has been deleted** — Strands now does all of it natively, and
> better. If you are carrying a copy of it, migrate using the table below.

## Table of Contents

1. [The Problem: Context Growth in Agentic Loops](#the-problem)
2. [Bedrock Cache Constraints](#bedrock-cache-constraints)
3. [What the SDK Does Natively](#what-the-sdk-does-natively)
4. [Tool Result Offloading](#tool-result-offloading)
5. [Proactive Compression](#proactive-compression)
6. [The One Thing Left to You: Cache Economics](#cache-economics)
7. [Using agent.state for Durable Metadata](#durable-metadata)
8. [Configuration Guide](#configuration-guide)
9. [Migrating off a Custom Manager](#migrating)

---

## The Problem

In agentic loops, tool results accumulate rapidly. A 10-turn conversation with 3 tool
calls per turn produces 30 tool results, each ranging from 500 to 5,000+ tokens.
Without management, context grows while most older results are no longer relevant.

```
Turn  1:  system + tools + user + 3 tool results     ~15K tokens
Turn  5:  ... + 15 tool results                       ~45K tokens
Turn 10:  ... + 30 tool results                       ~90K tokens
Turn 15:  ... + 45 tool results → approaching limit   ~135K tokens
```

The model needs recent context to make decisions, but tool results from turn 2 rarely
matter at turn 15. The goal is to reclaim space from stale results while preserving
what matters — and, ideally, to keep the discarded content *retrievable* rather than
destroyed.

### Why Not Just Use SlidingWindowConversationManager?

`SlidingWindowConversationManager` handles the extreme case — it drops the oldest
messages when the window is full. On its own it is a blunt instrument:

- It removes whole messages, including useful user/assistant exchanges.
- It reacts *after* the provider raises a context-overflow error.
- It does not differentiate a 5,000-token file read from a 50-token status check.

The fix is not to replace it. It is to (a) stop oversized results from entering the
context in the first place, and (b) give whichever manager you use a real token budget
and a proactive trigger. Both are native features now.

---

## Bedrock Cache Constraints

These constraints shape every decision here:

| Constraint | Impact |
|------------|--------|
| **Prefix-matching cache only** | The cache matches the longest identical prefix of `[system prompt, tools, messages...]`. No surgical deletion. |
| **~5-minute TTL** | Much shorter than Anthropic 1P. If a user idles 5+ minutes, the prefix is recomputed on the next turn. |
| **No `cache_edits`** | Cannot delete KV pages while preserving the cache key. Modifying any message content changes the prefix and invalidates it. |
| **No global cache scope** | Each session has its own cache; sessions diverge at the first user message, so cross-session sharing is minimal. |
| **`CacheConfig(strategy="auto")`** | Strands places cache points to maximize coverage. As of 1.53 the system prompt is cached by default (`system_prompt_ttl=True`) — a breaking change from earlier versions. |

### The Key Insight: When Modifying History Is Free

Modifying message content breaks the prefix cache — **except when the cache is already
cold**. If the idle gap exceeds the TTL, the cached prefix has expired and the next
call recomputes it regardless. Shrinking the payload in that window costs nothing.

```
Cache warm (< 5 min gap):
  Modifying history → breaks cache → costs extra tokens next turn.
  Only do it when context pressure demands it.

Cache cold (> 5 min gap):
  Modifying history → no cache to break → free.
  Be aggressive; you are shrinking a recomputation that was going to happen anyway.
```

This is the one piece of the old three-tier design worth keeping, because the SDK does
not model cache economics. See [Cache Economics](#cache-economics).

---

## What the SDK Does Natively

| Concern | Native feature | Where |
|---|---|---|
| Oversized tool results | `ContextOffloader` plugin | `strands.vended_plugins.context_offloader` |
| Token counting | `model.count_tokens()` | tiktoken, or provider-native |
| Context window ceiling | `context_window_limit` | `BedrockModel` config |
| Compress before overflow | `proactive_compression` | any `ConversationManager` |
| Summarize instead of drop | `SummarizingConversationManager` | `strands.agent.conversation_manager` |
| Protect opening turns | `pin_first` | conversation managers |
| Agentic self-management | `context_manager="auto" \| "agentic"` | `Agent(...)` — experimental |

Do not reimplement any row of this table.

---

## Tool Result Offloading

`ContextOffloader` is a `Plugin` that intercepts tool results at execution time — via
`AfterToolCallEvent`, *before* the result enters the conversation — and offloads any
result over a token threshold to a storage backend, leaving a text preview plus a
retrieval reference in context.

```python
from strands import Agent
from strands.vended_plugins.context_offloader import ContextOffloader, S3Storage

agent = Agent(
    plugins=[
        ContextOffloader(
            storage=S3Storage(bucket="my-agent-artifacts", prefix="offload/"),
            max_result_tokens=2500,   # offload above this
            preview_tokens=1000,      # keep this much in context
            evict_after_cycles=20,
        )
    ],
)
```

This is strictly better than clearing results in a conversation manager:

| Hand-rolled clearing | `ContextOffloader` |
|---|---|
| Fires reactively, after the result is already in history | Fires at tool execution, before it enters history |
| Overwrites content with `"[Tool result cleared]"` — **data is gone** | Stores content; model can call `retrieve_offloaded_content` |
| Estimates tokens as `len(text) / 4` | Uses `model.count_tokens()` |
| Text only | Text, JSON, images, and documents, each stored in its native media type |

It registers a `retrieve_offloaded_content` tool automatically. Pass
`include_retrieval_tool=False` to suppress that if you never want retrieval.

### Storage backends

| Backend | Lifetime | Use when |
|---|---|---|
| `InMemoryStorage(evict_after_turns=20)` | Dies with the process | Default; single-session agents |
| `FileStorage(artifact_dir=...)` | Container filesystem | Local development, or a sandbox mount |
| `S3Storage(bucket=..., prefix=...)` | Durable | **AgentCore Runtime** — containers are hard-killed with no SIGTERM, so anything that must outlive the session belongs here |

### Selective offloading

Small, high-value results should stay verbatim — a retrieval round trip can cost more
than the tokens saved. Use `should_offload`:

```python
ContextOffloader(
    storage=InMemoryStorage(),
    should_offload=lambda tool_name, token_count, **kw: (
        tool_name not in {"get_context", "get_account_info"} and token_count > 2500
    ),
)
```

The callback receives `(tool_name, token_count, **kwargs)` and may be sync or async.
Always accept `**kwargs` — the signature is documented as forward-extensible.

---

## Proactive Compression

Every `ConversationManager` accepts `proactive_compression`. With it, the manager acts
when context utilization crosses a threshold instead of waiting for the provider to
raise a context-overflow error.

```python
from strands.agent.conversation_manager import SummarizingConversationManager

manager = SummarizingConversationManager(
    summary_ratio=0.3,            # summarize the oldest 30%
    preserve_recent_messages=10,  # never touch the last 10
    pin_first=1,                  # keep the opening turn — it carries the task framing
    proactive_compression={"compression_threshold": 0.7},
)
```

This only works properly if the model knows its own budget. Set it explicitly:

```python
BedrockModel(
    model_id="global.anthropic.claude-sonnet-5",
    context_window_limit=1_000_000,
)
```

Without `context_window_limit`, utilization is inferred and the threshold fires at the
wrong time. **Update this value whenever you change models** — a stale 200K limit on a
1M-context model wastes 80% of the window.

### Choosing a manager

| Manager | Behaviour | Cost |
|---|---|---|
| `SummarizingConversationManager` | Model-summarizes the oldest slice | One extra model call per compaction; preserves the most context |
| `SlidingWindowConversationManager` | Drops oldest messages at complete tool pairs | Free; loses early context outright |
| `NullConversationManager` | Does nothing | Free; only for guaranteed-short sessions |

`SlidingWindowConversationManager` trims at **complete tool-use/tool-result pairs** as
of 1.52, so it no longer leaves orphaned `toolUse` blocks that some providers reject.

---

## Cache Economics

The SDK models token budgets. It does not model Bedrock's prompt-cache TTL. That gap is
the only place a custom component still earns its keep: when the cache is cold, be more
aggressive than a fixed token threshold would be.

```python
class CacheAwareOffloadPolicy:
    """Tighten the offload threshold while the prompt cache is cold."""

    def __init__(self, warm_tokens=2500, cold_tokens=600, ttl_minutes=5.0):
        self._warm, self._cold, self._ttl = warm_tokens, cold_tokens, ttl_minutes
        self._last: float | None = None

    def __call__(self, tool_name: str, token_count: int, **kwargs) -> bool:
        now = time.time()
        gap = (now - self._last) / 60.0 if self._last is not None else 0.0
        self._last = now
        threshold = self._cold if gap >= self._ttl else self._warm
        return token_count > threshold
```

Wire it in as `ContextOffloader(should_offload=CacheAwareOffloadPolicy())`.

Cold-cache detection here is deliberately approximate: tool calls inside one agent turn
land milliseconds apart, so a gap longer than the TTL between consecutive calls means a
new turn began after the user idled. That costs one timestamp and no hooks. For an exact
signal, register a `BeforeModelCallEvent` hook and set the flag from there.

See `templates/strands-agentcore/agent/core/conversation.py` for the full version.

---

## Durable Metadata

`agent.state` persists across invocations and is **never modified by conversation
management**. That makes it the right place for metadata that must survive compaction,
summarization, and offloading.

```python
# Entity index mappings — survive even if the list_orders result is offloaded.
agent.state.set("entity_mappings", mapper.to_dict())

# Session context — survives summarization.
agent.state.set("customer_tier", "premium")
```

Pass initial state at construction rather than mutating afterwards:

```python
agent = Agent(..., state={"user_id": user_id})
```

### Pattern: Durable Entity Mappings

`EntityIndexMapper` from [security-patterns.md](security-patterns.md) maps real IDs to
sequential indices. Because it uses `agent.state` as its backing store, mappings survive
offloading with no manual serialization:

```python
@tool(context=True)
def list_orders(tool_context: ToolContext) -> dict:
    state = tool_context.agent.state
    mapper = EntityIndexMapper(state, namespace="orders")
    orders = fetch_orders(state.get("user_id"))
    mapper.store_entities(orders, id_field="order_id")
    return {"orders": [...], "hint": "Use get_order_details(order_number=N)"}

@tool(context=True)
def get_order_details(tool_context: ToolContext, order_number: int) -> dict:
    mapper = EntityIndexMapper(tool_context.agent.state, namespace="orders")
    real_id = mapper.get_real_id(order_number)  # resolves even after offloading
    ...
```

When the `list_orders` result is offloaded, the model loses the displayed list from
context — but the index-to-ID mappings remain in `agent.state`, so resolution still
works. This is why index mapping and offloading compose cleanly.

---

## Configuration Guide

| Parameter | Where | Default | When to change |
|---|---|---|---|
| `max_result_tokens` | `ContextOffloader` | `2500` | Lower for chatty tools; raise if retrieval round trips hurt latency |
| `preview_tokens` | `ContextOffloader` | `1000` | Must be large enough for the model to judge whether to retrieve |
| `evict_after_cycles` | `ContextOffloader` | `20` | `None` to keep blobs for the whole session |
| `should_offload` | `ContextOffloader` | `None` (all oversized) | Protect small critical results; add cache awareness |
| `storage` | `ContextOffloader` | — | `S3Storage` on AgentCore Runtime |
| `compression_threshold` | `proactive_compression` | `0.7` | Lower for a safety margin on bursty tool output |
| `context_window_limit` | `BedrockModel` | model-table lookup | **Set explicitly and update on every model change** |
| `preserve_recent_messages` | `SummarizingConversationManager` | `10` | Raise if the agent references older turns |
| `summary_ratio` | `SummarizingConversationManager` | `0.3` | Raise to reclaim more per compaction |
| `pin_first` | conversation managers | `None` | `1` keeps the opening turn's task framing |

### Example: high-throughput API agent

Latency-sensitive, many small results. Avoid summarization cost; offload aggressively.

```python
Agent(
    model=BedrockModel(model_id=..., context_window_limit=1_000_000),
    conversation_manager=SlidingWindowConversationManager(
        window_size=40,
        pin_first=1,
        proactive_compression={"compression_threshold": 0.8},
    ),
    plugins=[ContextOffloader(storage=InMemoryStorage(), max_result_tokens=1200)],
)
```

### Example: long-running research agent

Early findings still matter at turn 40. Summarize rather than drop; keep blobs durable.

```python
Agent(
    model=BedrockModel(model_id=..., context_window_limit=1_000_000),
    conversation_manager=SummarizingConversationManager(
        summary_ratio=0.3,
        preserve_recent_messages=15,
        pin_first=2,
        proactive_compression={"compression_threshold": 0.65},
    ),
    plugins=[
        ContextOffloader(
            storage=S3Storage(bucket="research-artifacts"),
            max_result_tokens=3000,
            evict_after_cycles=None,
        )
    ],
)
```

---

## Migrating

If you have a copy of the old three-tier manager, replace it as follows and delete it.

| Old | New |
|---|---|
| Tier 1 — time-based tool result clearing | `ContextOffloader` + `should_offload` with cache awareness |
| Tier 2 — count-based tool result clearing | `ContextOffloader(max_result_tokens=...)` |
| Tier 3 — oldest-message removal | `proactive_compression` on a `SummarizingConversationManager` |
| `ContextManagerConfig.keep_recent` | `preserve_recent_messages` |
| `ContextManagerConfig.trigger_threshold` | `max_result_tokens` (token-based, not count-based) |
| `ContextManagerConfig.summarize_threshold_pct` | `proactive_compression={"compression_threshold": ...}` |
| `ContextManagerConfig.context_window_tokens` | `context_window_limit` on the model |
| `compactable_tool_names` (allowlist) | `should_offload` callback |
| `cleared_placeholder` | Not needed — offloaded content is retrievable, not replaced |
| `_estimate_tokens()` (chars / 4) | `model.count_tokens()` |
| `_cm_last_model_call` in `agent.state` | Local timestamp in the `should_offload` callable |

### Wiring it in the builder

```python
class SessionBuilder:
    def _build_plugins(self) -> list:
        return [build_context_offloader(self.context_policy)]

    def _build_conversation_manager(self):
        return build_conversation_manager(self.context_policy)

    def _build_agent(self, tools, plugins=None, **kwargs) -> Agent:
        return Agent(
            model=self._build_model(),
            tools=tools,
            conversation_manager=self._build_conversation_manager(),
            plugins=plugins,
            **kwargs,
        )
```

Override either method in a subclass to change one concern without touching the rest.
