"""Context-pressure policy.

Strands handles the mechanics natively as of 1.54. This module configures those
features and adds exactly one thing the SDK does not model: Bedrock's prompt-cache
economics.

## What the SDK now does for you

| Concern                        | Native feature                                          |
|--------------------------------|---------------------------------------------------------|
| Oversized tool results         | `ContextOffloader` plugin (`vended_plugins`)             |
| Token counting                 | `model.count_tokens()` — tiktoken or provider-native     |
| Context-window ceiling         | `context_window_limit` on the model config               |
| Compress before overflow       | `proactive_compression` on any `ConversationManager`     |
| Summarize instead of drop      | `SummarizingConversationManager`                         |
| Protect the opening turns      | `pin_first` on the conversation manager                  |

Earlier revisions of this template shipped a 340-line `CacheSafeConversationManager`
that reimplemented the first four rows by hand. It has been deleted. Its
char-count/4 token estimator was strictly worse than `count_tokens()`, and its
"replace the result with `[Tool result cleared]`" step destroyed data that
`ContextOffloader` keeps retrievable behind a preview. Do not bring it back.

## What is left to us

Bedrock caches the longest matching prefix for ~5 minutes. If the idle gap since the
last turn exceeds that TTL, the cache is already cold — the whole prefix is going to
be recomputed on the next call regardless. Shrinking the payload in that window is
therefore *free*, so it is worth being more aggressive than a fixed token threshold
would be. `CacheAwareOffloadPolicy` below is that adjustment, and nothing more.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from strands.agent.conversation_manager import (
    ConversationManager,
    SummarizingConversationManager,
)
from strands.vended_plugins.context_offloader import (
    ContextOffloader,
    InMemoryStorage,
    Storage,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContextPolicy:
    """Tuning knobs for context pressure. See references/context-management.md."""

    # --- Offloading (ContextOffloader) ---

    # Results above this many tokens are offloaded to storage and replaced with a
    # preview plus a retrieval reference.
    max_result_tokens: int = 2500

    # Size of the preview left in context. Must be large enough that the model can
    # tell whether retrieval is worth a round trip.
    preview_tokens: int = 1000

    # Offloaded blobs are dropped from storage after this many event-loop cycles.
    # None keeps them for the life of the session.
    evict_after_cycles: int | None = 20

    # Tools whose results are never offloaded. Use for small, high-value results the
    # model needs verbatim on every turn — an offload round trip would cost more than
    # the tokens saved.
    never_offload: frozenset[str] = frozenset()

    # --- Cache economics (the one non-native piece) ---

    # Bedrock prompt-cache TTL. After an idle gap this long the cache is cold.
    cache_ttl_minutes: float = 5.0

    # Threshold applied instead of max_result_tokens while the cache is cold.
    # Lower means more aggressive. Set equal to max_result_tokens to disable the
    # cache-aware behaviour entirely.
    cold_cache_result_tokens: int = 600

    # --- Compaction (ConversationManager) ---

    # Fraction of the context window at which proactive compression kicks in, before
    # an overflow error rather than after it.
    compression_threshold: float = 0.7

    # Recent messages a summarization pass must leave untouched.
    preserve_recent_messages: int = 10

    # Fraction of the conversation summarized per pass.
    summary_ratio: float = 0.3

    # Leading messages pinned out of summarization. 1 keeps the opening user turn,
    # which usually carries the task framing.
    pin_first: int | None = 1


class CacheAwareOffloadPolicy:
    """`ShouldOffload` implementation that tightens the threshold on a cold cache.

    Implements the protocol `ContextOffloader(should_offload=...)` expects:
    `(tool_name: str, token_count: int, **kwargs) -> bool`.

    Cold-cache detection is deliberately approximate. Tool calls within one agent turn
    land milliseconds apart, so a gap longer than the cache TTL between two
    consecutive calls means a new turn began after the user went idle — the point at
    which the server-side cache has expired. This costs one timestamp and no hooks. If
    you need an exact signal, register a `BeforeModelCallEvent` hook instead and set
    `_cold` from there.
    """

    def __init__(self, policy: ContextPolicy) -> None:
        self._policy = policy
        self._last_call: float | None = None

    def __call__(self, tool_name: str, token_count: int, **kwargs: object) -> bool:
        now = time.time()
        gap_minutes = (now - self._last_call) / 60.0 if self._last_call is not None else 0.0
        self._last_call = now

        if tool_name in self._policy.never_offload:
            return False

        cold = gap_minutes >= self._policy.cache_ttl_minutes
        threshold = (
            self._policy.cold_cache_result_tokens if cold else self._policy.max_result_tokens
        )

        if token_count <= threshold:
            return False

        logger.info(
            "Offloading %s result (%d tokens > %d, cache=%s after %.1f min idle).",
            tool_name,
            token_count,
            threshold,
            "cold" if cold else "warm",
            gap_minutes,
        )
        return True


def build_conversation_manager(policy: ContextPolicy | None = None) -> ConversationManager:
    """Summarize before the window fills, rather than dropping messages after it does.

    `proactive_compression` makes the manager act at `compression_threshold` of the
    context window using real token counts, instead of waiting for the provider to
    raise a context-overflow error.

    Swap in `SlidingWindowConversationManager` if summarization latency matters more
    than retaining early context, or `NullConversationManager` when a session is
    guaranteed short.
    """
    policy = policy or ContextPolicy()
    return SummarizingConversationManager(
        summary_ratio=policy.summary_ratio,
        preserve_recent_messages=policy.preserve_recent_messages,
        pin_first=policy.pin_first,
        proactive_compression={"compression_threshold": policy.compression_threshold},
    )


def build_context_offloader(
    policy: ContextPolicy | None = None,
    storage: Storage | None = None,
) -> ContextOffloader:
    """Offload oversized tool results at execution time, before they enter context.

    Storage backends: `InMemoryStorage` (default — per-container, dies with the
    session), `FileStorage(artifact_dir=...)`, or `S3Storage(bucket=...)`. On
    AgentCore Runtime the container is hard-killed with no SIGTERM, so anything that
    must outlive the session belongs in S3.
    """
    policy = policy or ContextPolicy()
    return ContextOffloader(
        storage=storage or InMemoryStorage(evict_after_turns=policy.evict_after_cycles),
        max_result_tokens=policy.max_result_tokens,
        preview_tokens=policy.preview_tokens,
        evict_after_cycles=policy.evict_after_cycles,
        should_offload=CacheAwareOffloadPolicy(policy),
    )
