"""Meta-tool factories — create the two meta-tools bound to a CategoryRegistry.

Both factories use the closure pattern to capture the registry at construction
time. The agent sees only these 2 tools regardless of how many actual tools exist.

    get_tool_info  — Level 1 → Level 2 disclosure (catalog → full schemas)
    use_tool       — Level 2 → Level 3 execution (schemas → tool call)

IMPORTANT (Experimental): Language models are trained to see tool descriptions
upfront. Meta-tooling defers schemas, which only works reliably with capable
models (Sonnet, Opus). Evaluate harshly before production use.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from strands import tool

if TYPE_CHECKING:
    from meta_tooling.registry import CategoryRegistry


def create_get_tool_info(registry: CategoryRegistry) -> Callable:
    """Create a get_tool_info tool bound to the given registry.

    Returns category instructions and full tool schemas on demand,
    implementing Level 1 → Level 2 progressive disclosure.
    """

    @tool
    def get_tool_info(category: str) -> str:
        """Get tool names, parameter schemas, and usage instructions for a category.

        You MUST call this before calling use_tool. This returns the exact tool
        names and parameter names you need — do not guess them.

        Args:
            category: The category name from the Tool Categories list
        """
        cat = registry.get_category(category)
        if not cat:
            available = registry.list_category_names()
            return f'{{"error": "Unknown category: {category}", "available": {available}}}'

        schemas_text = cat.get_tool_schemas_text()
        return (
            f'{{"category": "{cat.name}", '
            f'"instructions": {repr(cat.instructions)}, '
            f'"tools": {schemas_text}}}'
        )

    return get_tool_info


def create_use_tool(registry: CategoryRegistry) -> Callable:
    """Create a use_tool tool with transparent local/MCP routing.

    Routes execution based on the category type:
    - Local categories: direct function call (tool_fn(**params))
    - MCP categories: remote dispatch via mcp_client.call_tool_sync()

    The agent is unaware of the execution path difference.

    NOTE: ToolContext is NOT injected when calling local tools through this
    dispatcher. Tools that depend on tool_context.agent.state must capture
    their dependencies via the closure factory pattern instead.
    """

    @tool
    def use_tool(category: str, tool_name: str, parameters: str) -> str:
        """Run a tool. You must call get_tool_info first to get the exact
        tool_name and parameter names.

        Args:
            category: The category name (same value passed to get_tool_info)
            tool_name: The exact tool name returned by get_tool_info
            parameters: JSON string of parameters using exact names from get_tool_info
        """
        entry = registry.get_category(category)
        if not entry:
            return f'{{"error": "Unknown category: {category}"}}'

        try:
            params = json.loads(parameters) if parameters else {}
        except json.JSONDecodeError as e:
            return f'{{"error": "Invalid JSON in parameters: {e}"}}'

        # MCP: dispatch via protocol
        if entry.is_mcp:
            return entry.call_mcp_tool(tool_name, params)

        # Local: find matching function and call directly
        for tool_fn in entry.get_tool_functions():
            if tool_fn.tool_name == tool_name:
                return tool_fn(**params)

        available = [fn.tool_name for fn in entry.get_tool_functions()]
        return (
            f'{{"error": "Unknown tool \'{tool_name}\' in category \'{category}\'", '
            f'"available_tools": {available}}}'
        )

    return use_tool
