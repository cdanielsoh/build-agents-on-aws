"""CacheSafeConversationManager — three-tier context pressure management.

Inspired by Claude Code's microcompact system, adapted for Bedrock's
prefix-matching cache and ~5-minute TTL.

Tier 1: Time-based clearing — when cache is cold, clear old tool results (free).
Tier 2: Count-based clearing — when results accumulate past threshold (cache break).
Tier 3: Message removal — when approaching context window limit.

Uses agent.state for durable metadata that survives conversation management:
  _cm_last_model_call  — timestamp of last model call
  _cm_cleared_count    — running total of cleared results
  _cm_tier1_fires      — Tier 1 trigger count
  _cm_tier2_fires      — Tier 2 trigger count
"""

import logging
import time
from dataclasses import dataclass

from strands import Agent
from strands.agent.conversation_manager import ConversationManager

logger = logging.getLogger(__name__)

# Key prefix for state stored in agent.state — avoids collisions with user state.
_STATE_PREFIX = "_cm_"
_KEY_LAST_CALL = f"{_STATE_PREFIX}last_model_call"
_KEY_CLEARED = f"{_STATE_PREFIX}cleared_count"
_KEY_TIER1 = f"{_STATE_PREFIX}tier1_fires"
_KEY_TIER2 = f"{_STATE_PREFIX}tier2_fires"


@dataclass(frozen=True)
class ContextManagerConfig:
    """Configuration for CacheSafeConversationManager.

    Tune these based on your model, tool design, and latency requirements.
    See references/context-management.md for guidance.
    """

    # Bedrock prompt cache TTL in minutes. If the gap since last model call
    # exceeds this, the cache is cold — clearing old tool results is free
    # because the prefix will be fully recomputed anyway.
    cache_ttl_minutes: float = 5.0

    # Number of most-recent tool results to preserve when clearing.
    # Minimum 1 — the model always needs recent context.
    keep_recent: int = 5

    # Tier 2 fires when compactable tool results exceed this count.
    # Unlike Tier 1, this DOES break the cache — it's a trade-off
    # between cache efficiency and context window pressure.
    trigger_threshold: int = 15

    # Tier 3: remove oldest messages when estimated tokens exceed this
    # fraction of the context window.
    summarize_threshold_pct: float = 0.80

    # Model context window size in tokens. Used for Tier 3 threshold.
    context_window_tokens: int = 200_000

    # Optional: only clear tool results from these tools. If None, all
    # tool results are eligible. Use this to protect critical context
    # tools (e.g., get_context) while clearing read/search results.
    compactable_tool_names: frozenset[str] | None = None

    # Placeholder text that replaces cleared tool result content.
    cleared_placeholder: str = "[Tool result cleared]"


class CacheSafeConversationManager(ConversationManager):
    """Context-aware conversation manager for Bedrock agents.

    Drop-in replacement for SlidingWindowConversationManager that adds
    cache-TTL-aware tool result clearing before falling back to message
    removal.

    Usage:
        from core.conversation import CacheSafeConversationManager, ContextManagerConfig

        manager = CacheSafeConversationManager(
            ContextManagerConfig(cache_ttl_minutes=5.0, keep_recent=5)
        )
        agent = Agent(..., conversation_manager=manager)

    The manager uses agent.state to track timestamps and clearing stats.
    These survive across turns and across conversation management events
    (message clearing/summarization never touches agent.state).
    """

    def __init__(self, config: ContextManagerConfig | None = None):
        super().__init__()
        self._config = config or ContextManagerConfig()

    # ── Strands ConversationManager interface ──────────────────────

    def apply_management(self, agent: Agent, **kwargs) -> None:
        """Called before each model invocation. Runs tiers in order."""
        messages = agent.messages
        if not messages:
            self._record_model_call(agent)
            return

        # Tier 1: time-based clearing (cache is cold → free to clear).
        if self._maybe_time_based_clear(agent, messages):
            self._record_model_call(agent)
            return  # Short-circuit — Tier 2 is unnecessary when cache is cold.

        # Tier 2: count-based clearing (cache break, but context pressure).
        self._maybe_count_based_clear(agent, messages)

        # Tier 3 runs independently — it addresses total context size,
        # not just tool result bloat. Tier 1/2 may have freed enough
        # space to skip it.
        self._maybe_trim_messages(agent, messages)

        # Record current time AFTER tier checks so the next invocation's
        # gap calculation reflects the true idle period between calls.
        self._record_model_call(agent)

    def reduce_context(self, agent: Agent, e: Exception | None = None, **kwargs) -> None:
        """Called on context window overflow. Aggressive clearing."""
        messages = agent.messages
        if not messages:
            return

        # First: clear ALL tool results except the most recent keep_recent.
        positions = self._collect_tool_results(messages)
        keep = max(1, self._config.keep_recent)
        to_clear = positions[:-keep] if len(positions) > keep else []

        for msg_idx, block_idx, _tool_id in to_clear:
            self._clear_tool_result(messages, msg_idx, block_idx)

        # If still over, remove oldest messages.
        estimated = self._estimate_tokens(messages)
        target = int(self._config.context_window_tokens * self._config.summarize_threshold_pct)
        while len(messages) > 2 and estimated > target:
            removed = messages.pop(0)
            estimated -= self._estimate_message_tokens(removed)
            self.removed_message_count += 1

    # ── Tier implementations ──────────────────────────────────────

    def _maybe_time_based_clear(self, agent: Agent, messages: list) -> bool:
        """Tier 1: clear old tool results when the cache has expired.

        The key insight from Claude Code: if the gap since last interaction
        exceeds the cache TTL, the server-side cache is cold. The entire
        prefix will be recomputed on the next call. Clearing old tool
        results shrinks the recomputation payload at zero additional cost
        — there's no cache to break.
        """
        last_call = agent.state.get(_KEY_LAST_CALL)
        if last_call is None:
            return False

        gap_minutes = (time.time() - last_call) / 60.0
        if gap_minutes < self._config.cache_ttl_minutes:
            return False

        positions = self._collect_tool_results(messages)
        keep = max(1, self._config.keep_recent)
        to_clear = positions[:-keep] if len(positions) > keep else []

        if not to_clear:
            return False

        for msg_idx, block_idx, _tool_id in to_clear:
            self._clear_tool_result(messages, msg_idx, block_idx)

        cleared_count = len(to_clear)
        agent.state.set(_KEY_CLEARED, (agent.state.get(_KEY_CLEARED) or 0) + cleared_count)
        agent.state.set(_KEY_TIER1, (agent.state.get(_KEY_TIER1) or 0) + 1)

        logger.info(
            "[Tier 1] Cache cold (%.1f min > %.1f min TTL). "
            "Cleared %d tool results, kept %d.",
            gap_minutes,
            self._config.cache_ttl_minutes,
            cleared_count,
            min(keep, len(positions)),
        )
        return True

    def _maybe_count_based_clear(self, agent: Agent, messages: list) -> bool:
        """Tier 2: clear old tool results when count exceeds threshold.

        Unlike Tier 1, this fires when the cache is warm. Clearing content
        WILL break the prefix cache on the next turn. But the trade-off is
        worthwhile when context pressure is high — a cache miss is cheaper
        than running out of context window.
        """
        positions = self._collect_tool_results(messages)
        if len(positions) <= self._config.trigger_threshold:
            return False

        keep = max(1, self._config.keep_recent)
        to_clear = positions[:-keep] if len(positions) > keep else []

        if not to_clear:
            return False

        for msg_idx, block_idx, _tool_id in to_clear:
            self._clear_tool_result(messages, msg_idx, block_idx)

        cleared_count = len(to_clear)
        agent.state.set(_KEY_CLEARED, (agent.state.get(_KEY_CLEARED) or 0) + cleared_count)
        agent.state.set(_KEY_TIER2, (agent.state.get(_KEY_TIER2) or 0) + 1)

        logger.info(
            "[Tier 2] %d tool results > threshold %d. "
            "Cleared %d, kept %d. (Cache break expected.)",
            len(positions),
            self._config.trigger_threshold,
            cleared_count,
            min(keep, len(positions)),
        )
        return True

    def _maybe_trim_messages(self, agent: Agent, messages: list) -> bool:
        """Tier 3: remove oldest messages when approaching context limit.

        This is a simpler version of Strands' SummarizingConversationManager.
        For model-based summarization (which preserves more context), use
        SummarizingConversationManager as a fallback or override this method.
        """
        estimated = self._estimate_tokens(messages)
        threshold = int(
            self._config.context_window_tokens * self._config.summarize_threshold_pct
        )

        if estimated <= threshold:
            return False

        removed_count = 0
        while len(messages) > 2 and estimated > threshold:
            removed = messages.pop(0)
            estimated -= self._estimate_message_tokens(removed)
            removed_count += 1
            self.removed_message_count += 1

        if removed_count > 0:
            logger.info(
                "[Tier 3] Estimated %d tokens > threshold %d. Removed %d oldest messages.",
                estimated + removed_count * 500,  # approximate pre-removal
                threshold,
                removed_count,
            )
        return removed_count > 0

    # ── Helpers ────────────────────────────────────────────────────

    def _record_model_call(self, agent: Agent) -> None:
        """Store current timestamp in agent.state for next turn's gap calc."""
        agent.state.set(_KEY_LAST_CALL, time.time())

    def _collect_tool_results(
        self, messages: list
    ) -> list[tuple[int, int, str]]:
        """Walk messages and collect (msg_idx, block_idx, tool_use_id) for
        all compactable tool_result blocks, in encounter order.

        Shared by Tier 1 and Tier 2 to avoid duplicate traversals.
        """
        # First pass: collect tool_use names from assistant messages so we
        # can filter by compactable_tool_names.
        tool_id_to_name: dict[str, str] = {}
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            for block in msg.get("content", []):
                if "toolUse" in block:
                    tu = block["toolUse"]
                    tool_id_to_name[tu["toolUseId"]] = tu["name"]

        # Second pass: collect tool_result positions.
        positions: list[tuple[int, int, str]] = []
        compactable = self._config.compactable_tool_names

        for msg_idx, msg in enumerate(messages):
            if msg.get("role") != "user":
                continue
            for block_idx, block in enumerate(msg.get("content", [])):
                if "toolResult" not in block:
                    continue
                tr = block["toolResult"]
                tool_id = tr.get("toolUseId", "")
                tool_name = tool_id_to_name.get(tool_id, "")

                # Skip already-cleared results.
                content = tr.get("content", [])
                if (
                    len(content) == 1
                    and isinstance(content[0], dict)
                    and content[0].get("text") == self._config.cleared_placeholder
                ):
                    continue

                # Filter by compactable tool names if configured.
                if compactable is not None and tool_name not in compactable:
                    continue

                positions.append((msg_idx, block_idx, tool_id))

        return positions

    def _clear_tool_result(
        self, messages: list, msg_idx: int, block_idx: int
    ) -> None:
        """Replace a tool_result's content with the placeholder string.

        Preserves toolUseId and status — only the content payload changes.
        The message structure stays valid for the API.
        """
        block = messages[msg_idx]["content"][block_idx]
        tr = block["toolResult"]
        tr["content"] = [{"text": self._config.cleared_placeholder}]

    def _estimate_tokens(self, messages: list) -> int:
        """Rough token estimate: chars / 4, padded by 4/3 for safety."""
        total = sum(self._estimate_message_tokens(msg) for msg in messages)
        return int(total * 4 / 3)

    @staticmethod
    def _estimate_message_tokens(msg: dict) -> int:
        """Estimate tokens for a single message."""
        total = 0
        for block in msg.get("content", []):
            if "text" in block:
                total += len(block["text"]) // 4
            elif "toolUse" in block:
                tu = block["toolUse"]
                total += len(str(tu.get("input", {}))) // 4
            elif "toolResult" in block:
                for item in block["toolResult"].get("content", []):
                    if isinstance(item, dict) and "text" in item:
                        total += len(item["text"]) // 4
                    elif isinstance(item, dict) and "image" in item:
                        total += 2000  # approximate image token cost
        return total
