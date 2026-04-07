"""Session — thin runtime container.

Pattern: Does NOT create its own dependencies — receives them from
SessionBuilder. The _headers dict is shared by reference with MCPClient,
so in-place updates propagate without reconnecting.

Tools access agent.state directly via ToolContext — no separate context
object needed for state propagation.
"""

from collections.abc import AsyncIterator
from typing import Any

from strands import Agent


class Session:
    __slots__ = ("agent", "_headers")

    def __init__(self, agent: Agent, headers: dict[str, str]):
        self.agent = agent
        self._headers = headers

    def invoke(self, message: str, **kwargs) -> str:
        """Invoke the agent and return the full response (blocking).

        Extra kwargs are passed as invocation_state, accessible via
        tool_context.invocation_state in tools.
        """
        return str(self.agent(message, **kwargs))

    async def stream_async(self, message: str, **kwargs) -> AsyncIterator[Any]:
        """Yield all agent events (text chunks, tool calls, tool results)."""
        async for event in self.agent.stream_async(message, **kwargs):
            yield event

    def refresh_token(self, token: str):
        """Update the token in-place. MCPClient picks it up by reference."""
        bearer = token if token.startswith("Bearer ") else f"Bearer {token}"
        self._headers["Authorization"] = bearer
