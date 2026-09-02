"""Tool registry — collects local tools for the agent.

Tools use @tool(context=True) to access agent.state and invocation_state
via ToolContext. MCP tools from the Gateway are added separately by the builder.
"""

from tools.example_tool import get_account_info


def make_tools():
    return [
        get_account_info,
    ]
