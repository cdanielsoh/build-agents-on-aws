"""SessionBuilder — assembles all dependencies with overridable steps.

Pattern: Each _build_* method handles one concern and can be overridden
independently in a subclass. This is the primary extension point for
customizing agent construction without rewriting the full pipeline.

Extension points:
    _build_tools                 — return local @tool functions
    _build_hooks                 — return HookProvider instances (e.g. HITL)
    _build_conversation_manager  — context pressure management strategy
    _build_memory                — configure AgentCore Memory session manager
    _build_agent                 — assemble the Strands Agent
    _init_agent_state            — populate agent.state after construction
"""

import base64
import json
import logging
import uuid

from strands import Agent
from strands.models.bedrock import BedrockModel, CacheConfig
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client

from core.config import AgentConfig
from core.conversation import CacheSafeConversationManager, ContextManagerConfig
from core.session import Session
from tools import make_tools
from prompts.system import build_system_prompt

logger = logging.getLogger(__name__)


def _resolve_actor_id(token: str) -> str:
    """Extract actor ID from JWT sub claim (no verification — runtime already validated)."""
    try:
        raw = token.removeprefix("Bearer ").split(".")[1]
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload.get("sub", str(uuid.uuid4()))
    except Exception:
        return str(uuid.uuid4())


class SessionBuilder:
    """Constructs a Session with all dependencies wired up."""

    def __init__(self, config: AgentConfig):
        self.config = config

    def build(self, token: str = "", session_id: str = "") -> Session:
        headers = self._init_headers(token)
        local_tools = self._build_tools()

        if token:
            mcp_client = self._build_mcp_client(headers)
            local_tools = local_tools + [mcp_client]

        hooks = self._build_hooks()
        session_manager = self._build_memory(token, session_id)
        agent = self._build_agent(
            tools=local_tools,
            hooks=hooks,
            session_manager=session_manager,
        )
        # Populate agent.state with session-level data.
        # Tools access this at call time via tool_context.agent.state.
        self._init_agent_state(agent, token)
        logger.info("Session created (MCP=%s).", "on" if token else "off")
        return Session(agent, headers)

    # ── overridable steps ───────────────────────────────────────

    def _init_headers(self, token: str) -> dict[str, str]:
        bearer = token if token.startswith("Bearer ") else f"Bearer {token}"
        return {"Authorization": bearer}

    def _build_mcp_client(self, headers: dict[str, str]) -> MCPClient:
        """Build MCP client. The headers dict is shared by reference with Session."""
        return MCPClient(
            lambda: streamablehttp_client(
                self.config.gateway_url, headers=headers,
            )
        )

    def _build_tools(self) -> list:
        """Build local tools. Tools access agent.state via ToolContext."""
        return make_tools()

    def _build_hooks(self) -> list:
        """Override to add HookProviders (e.g. ConfirmationHook for HITL)."""
        return []

    def _build_conversation_manager(self) -> CacheSafeConversationManager | None:
        """Build the conversation manager for context pressure management.

        Returns a CacheSafeConversationManager with Bedrock defaults.
        Override to customize thresholds or return None to fall back to
        Strands' default SlidingWindowConversationManager.

        See references/context-management.md for tuning guidance.
        """
        return CacheSafeConversationManager(
            ContextManagerConfig(
                cache_ttl_minutes=5.0,  # Bedrock prompt cache TTL
                keep_recent=5,
                trigger_threshold=15,
            )
        )

    def _build_memory(self, token: str, session_id: str):
        """Build AgentCore Memory session manager (or None to disable)."""
        if not (self.config.memory_enabled and self.config.memory_id):
            return None

        from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
        from bedrock_agentcore.memory.integrations.strands.session_manager import (
            AgentCoreMemorySessionManager,
        )

        actor_id = _resolve_actor_id(token)
        mem_session_id = session_id or str(uuid.uuid4())

        memory_config = AgentCoreMemoryConfig(
            memory_id=self.config.memory_id,
            session_id=mem_session_id,
            actor_id=actor_id,
        )
        return AgentCoreMemorySessionManager(
            agentcore_memory_config=memory_config,
            region_name=self.config.region,
        )

    def _build_model(self) -> BedrockModel:
        """Build the model with automatic prompt caching (Claude models only)."""
        return BedrockModel(
            model_id=self.config.model_id,
            cache_config=CacheConfig(strategy="auto"),
        )

    def _build_agent(self, tools: list, hooks: list, session_manager=None) -> Agent:
        conversation_mgr = self._build_conversation_manager()
        kwargs: dict = {
            "model": self._build_model(),
            "tools": tools,
            "system_prompt": build_system_prompt(),
            "callback_handler": None,
        }
        if hooks:
            kwargs["hooks"] = hooks
        if session_manager:
            kwargs["session_manager"] = session_manager
        if conversation_mgr:
            kwargs["conversation_manager"] = conversation_mgr
        return Agent(**kwargs)

    def _init_agent_state(self, agent: Agent, token: str) -> None:
        """Populate agent.state with session-level data after construction.

        Override to add custom state. Tools access these values at call time
        via tool_context.agent.state.
        """
        agent.state.set("user_id", _resolve_actor_id(token) if token else "")
