"""MetaToolingBuilder — extends SessionBuilder to use meta-tooling.

Overrides _build_tools() and _build_agent() to wire the meta-tooling
pattern into the existing scaffold. All other SessionBuilder features
(conversation manager, MCP client, agent.state, hooks) are inherited.

Usage:
    registry = CategoryRegistry()
    registry.register(ToolCategory(name="orders", ...))
    registry.register(ToolCategory(name="support", ...))

    builder = MetaToolingBuilder(config, registry)
    session = builder.build(token=token)

IMPORTANT (Experimental): Meta-tooling defers tool schemas, which breaks
the standard pattern LLMs are trained on. Only use with capable models
(Sonnet, Opus). Evaluate harshly with less capable models.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from strands import Agent

from core.builder import SessionBuilder
from core.config import AgentConfig
from meta_tooling.meta_tools import create_get_tool_info, create_use_tool
from prompts.system import build_system_prompt

if TYPE_CHECKING:
    from meta_tooling.registry import CategoryRegistry


class MetaToolingBuilder(SessionBuilder):
    """SessionBuilder subclass that uses meta-tooling instead of direct tool loading.

    Instead of registering all tools directly, this builder:
    1. Registers only 2 meta-tools (get_tool_info, use_tool)
    2. Injects a brief category catalog into the system prompt
    3. Appends rules that force the agent to call get_tool_info before use_tool
    """

    def __init__(self, config: AgentConfig, registry: CategoryRegistry):
        super().__init__(config)
        self._registry = registry

    def _build_tools(self) -> list:
        """Return only the 2 meta-tools instead of all domain tools."""
        return [
            create_get_tool_info(self._registry),
            create_use_tool(self._registry),
        ]

    def _build_system_prompt(self) -> str:
        """Base prompt plus the category catalog and the rules that make deferral work.

        The catalog is Level 1 disclosure: enough for the model to pick a category,
        not enough to call a tool. The rules exist because a model that has never
        seen the schemas will otherwise invent tool names.
        """
        return (
            f"{build_system_prompt()}\n\n"
            f"## Tool Categories\n"
            f"{self._registry.get_catalog()}\n\n"
            f"## Rules\n"
            f"- You MUST call get_tool_info with a category name BEFORE calling use_tool.\n"
            f"- You do NOT know the tool names or parameter names until get_tool_info tells you.\n"
            f"- NEVER guess tool names or parameters. Always call get_tool_info first.\n"
            f"- After get_tool_info returns the tool schema, call use_tool with the exact "
            f"tool name, parameter names, and values from the schema.\n"
        )

    def _build_agent(
        self,
        tools: list,
        plugins: list | None = None,
        hooks: list | None = None,
        interventions: list | None = None,
        session_manager=None,
        state: dict | None = None,
    ) -> Agent:
        """Same assembly as SessionBuilder, with the catalog-augmented system prompt."""
        kwargs: dict = {
            "model": self._build_model(),
            "tools": tools,
            "system_prompt": self._build_system_prompt(),
            "conversation_manager": self._build_conversation_manager(),
            "state": state or {},
            "callback_handler": None,
        }
        if plugins:
            kwargs["plugins"] = plugins
        if hooks:
            kwargs["hooks"] = hooks
        if interventions:
            kwargs["interventions"] = interventions
        if session_manager:
            kwargs["session_manager"] = session_manager
        return Agent(**kwargs)
