"""Example tool demonstrating ToolContext usage.

Pattern: Tools decorated with @tool(context=True) receive a ToolContext
parameter that provides access to agent.state (durable metadata) and
invocation_state (per-request data). No closures needed for state access.

Closures are still useful for heavy external dependencies (DB clients,
API wrappers) that shouldn't live in a state dict.
"""

from strands import tool
from strands.types.tools import ToolContext

_MOCK_ACCOUNT = {
    "name": "Jane Doe",
    "email": "jane@example.com",
    "tier": "premium",
    "member_since": "2024-03-15",
}


@tool(context=True)
def get_account_info(tool_context: ToolContext) -> dict:
    """Retrieve the current user's account information.

    Returns account details including name, tier, and membership date.
    No parameters needed — uses the authenticated session context.
    """
    # Access user_id from agent.state (set by SessionBuilder).
    # user_id = tool_context.agent.state.get("user_id")
    # TODO: Replace with real API call using user_id
    return _MOCK_ACCOUNT
