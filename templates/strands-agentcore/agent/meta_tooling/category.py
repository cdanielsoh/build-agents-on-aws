"""ToolCategory — groups related tools (local or MCP) under a named category.

Each category carries a name, description, usage instructions, and a short
catalog label. Tools can come from a local @tool-decorated class or from
a remote MCP server.

Use tool_names to expose only a subset from a larger tool source, allowing
one class or MCP server to be split across multiple categories.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from strands.tools.decorator import DecoratedFunctionTool


@dataclass
class ToolCategory:
    """A named group of related tools with metadata for meta-tooling.

    Attributes:
        name: Category identifier used in get_tool_info / use_tool calls.
        description: Detailed description included in get_tool_info responses.
        instructions: Usage guidelines returned alongside tool schemas.
        label: Short catalog label (falls back to description if empty).
        tool_class: Local class containing @tool-decorated methods.
        tool_names: Optional subset filter — only expose these tool names.
        mcp_client: MCPClient instance for remote tools.
    """

    name: str
    description: str
    instructions: str
    label: str = ""
    tool_class: type | None = None
    tool_names: list[str] | None = None
    mcp_client: Any = None
    _instance: Any = field(default=None, repr=False, init=False)

    @property
    def is_mcp(self) -> bool:
        """Whether this category is backed by an MCP server."""
        return self.mcp_client is not None

    def _ensure_instance(self) -> Any:
        if self._instance is None and self.tool_class is not None:
            self._instance = self.tool_class()
        return self._instance

    def get_tool_functions(self) -> list[DecoratedFunctionTool]:
        """Return local DecoratedFunctionTool instances. Empty for MCP categories."""
        if self.is_mcp:
            return []
        instance = self._ensure_instance()
        if instance is None:
            return []
        tools = []
        for attr_name in dir(instance):
            if attr_name.startswith("_"):
                continue
            attr = getattr(instance, attr_name)
            if isinstance(attr, DecoratedFunctionTool):
                if self.tool_names is None or attr.tool_name in self.tool_names:
                    tools.append(attr)
        return tools

    def get_tool_schemas(self) -> list[dict]:
        """Return JSON schemas for all tools in this category."""
        if self.is_mcp:
            return self._get_mcp_schemas()
        return [fn.tool_spec for fn in self.get_tool_functions()]

    def _get_mcp_schemas(self) -> list[dict]:
        """Load tool schemas from the MCP client."""
        schemas = []
        try:
            mcp_tools = self.mcp_client.list_tools_sync()
            for mcp_tool in mcp_tools:
                if self.tool_names and mcp_tool.tool_name not in self.tool_names:
                    continue
                schemas.append(mcp_tool.tool_spec)
        except Exception:
            pass
        return schemas

    def call_mcp_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool via the MCP protocol."""
        if not self.is_mcp:
            return json.dumps({"error": "Not an MCP category"})
        result = self.mcp_client.call_tool_sync(
            tool_use_id=f"meta-{tool_name}",
            name=tool_name,
            arguments=arguments,
        )
        for item in result.get("content", []):
            if "text" in item:
                return item["text"]
        return json.dumps(result, default=str)

    def get_tool_schemas_text(self) -> str:
        """Return tool schemas as a JSON string."""
        return json.dumps(self.get_tool_schemas(), indent=2, default=str)

    def get_catalog_entry(self) -> str:
        """Single catalog line: '- name: label'."""
        return f"- {self.name}: {self.label or self.description}"
