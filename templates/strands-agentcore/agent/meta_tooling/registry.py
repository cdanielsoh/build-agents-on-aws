"""CategoryRegistry — central manager for tool categories.

Registers ToolCategory instances and generates the brief catalog string
that gets injected into the agent's system prompt (Level 1 disclosure).
"""

from __future__ import annotations

from collections.abc import Callable

from meta_tooling.category import ToolCategory


class CategoryRegistry:
    """Central registry for tool categories. Generates the system prompt catalog."""

    def __init__(self) -> None:
        self._categories: dict[str, ToolCategory] = {}

    def register(self, category: ToolCategory) -> None:
        """Register a category. Overwrites if the name already exists."""
        self._categories[category.name] = category

    def get_category(self, name: str) -> ToolCategory | None:
        """Look up a category by name."""
        return self._categories.get(name)

    def list_category_names(self) -> list[str]:
        """Return all registered category names."""
        return list(self._categories.keys())

    def get_catalog(self) -> str:
        """Generate the brief catalog for the system prompt (Level 1 disclosure).

        Output looks like:
            - orders: Order management
            - support: Help desk & tickets
            - account: Profile & preferences
        """
        return "\n".join(cat.get_catalog_entry() for cat in self._categories.values())

    def get_all_tool_functions(self) -> list[Callable]:
        """Flatten all local tool functions across categories.

        Useful for building a DirectAgent baseline for comparison.
        """
        tools = []
        for cat in self._categories.values():
            tools.extend(cat.get_tool_functions())
        return tools

    def get_total_tool_count(self) -> int:
        """Total number of tools across all categories (local + MCP)."""
        count = 0
        for cat in self._categories.values():
            if cat.is_mcp:
                count += len(cat.get_tool_schemas())
            else:
                count += len(cat.get_tool_functions())
        return count
