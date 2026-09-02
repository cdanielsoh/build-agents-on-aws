"""Shared context utilities for local tools.

Tools access agent.state directly via ToolContext — no custom context
class needed. This module provides helper utilities for common patterns.

agent.state is a JSONSerializableDict — use .get()/.set()/.delete(),
not subscript syntax:

Usage in tools:
    @tool(context=True)
    def my_tool(tool_context: ToolContext, ...) -> dict:
        user_id = tool_context.agent.state.get("user_id")
        mapper = EntityIndexMapper(tool_context.agent.state, "orders")
        ...

The builder sets agent.state via .set() after Agent construction.
Per-request data can be passed via invocation_state:
    agent("question", request_id="abc-123")
    # tool accesses: tool_context.invocation_state["request_id"]
"""
