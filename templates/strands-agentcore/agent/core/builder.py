"""SessionBuilder — assembles all dependencies with overridable steps.

Pattern: Each _build_* method handles one concern and can be overridden
independently in a subclass. This is the primary extension point for
customizing agent construction without rewriting the full pipeline.

Extension points:
    _build_tools                 — return local @tool functions
    _build_plugins               — return Plugin instances (e.g. ContextOffloader)
    _build_hooks                 — return HookProvider instances
    _build_interventions         — return InterventionHandlers (e.g. HITL gates)
    _build_conversation_manager  — context pressure strategy
    _build_memory                — configure AgentCore Memory session manager
    _build_model                 — model, caching, guardrails
    _build_state                 — initial agent.state
    _build_agent                 — assemble the Strands Agent
"""

import base64
import json
import logging
import uuid

from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models.bedrock import BedrockModel, CacheConfig
from strands.tools.mcp import MCPClient

from core.config import AgentConfig
from core.conversation import (
    ContextPolicy,
    build_context_offloader,
    build_conversation_manager,
)
from core.session import Session
from prompts.system import build_system_prompt
from tools import make_tools

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

    def __init__(self, config: AgentConfig, context_policy: ContextPolicy | None = None):
        self.config = config
        self.context_policy = context_policy or ContextPolicy()

    def build(self, token: str = "", session_id: str = "") -> Session:
        headers = self._init_headers(token)
        tools = self._build_tools()

        if token:
            tools = tools + [self._build_mcp_client(headers)]

        agent = self._build_agent(
            tools=tools,
            plugins=self._build_plugins(),
            hooks=self._build_hooks(),
            interventions=self._build_interventions(),
            session_manager=self._build_memory(token, session_id),
            state=self._build_state(token),
        )
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

    def _build_plugins(self) -> list:
        """Return Strands Plugin instances.

        ContextOffloader keeps oversized tool results out of the context window by
        storing them and leaving a retrievable preview. It replaces the tool-result
        clearing this template used to hand-roll — see core/conversation.py.

        For results that must survive the container (hard-killed, no SIGTERM), pass
        an S3Storage instead of the default in-memory backend.
        """
        return [build_context_offloader(self.context_policy)]

    def _build_hooks(self) -> list:
        """Override to add HookProviders (metrics, shadow-mode guardrails, logging)."""
        return []

    def _build_interventions(self) -> list:
        """Override to add InterventionHandlers.

        This is where human-in-the-loop confirmation gates belong as of Strands 1.51+.
        Interventions supersede the older "HookProvider that calls event.interrupt()"
        pattern and support LLM-driven risk classification for deciding which tool
        calls need approval. See references/tool-design.md.
        """
        return []

    def _build_conversation_manager(self):
        """Context pressure strategy. See core/conversation.py for the rationale."""
        return build_conversation_manager(self.context_policy)

    def _build_memory(self, token: str, session_id: str):
        """Build AgentCore Memory session manager (or None to disable)."""
        if not (self.config.memory_enabled and self.config.memory_id):
            return None

        from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
        from bedrock_agentcore.memory.integrations.strands.session_manager import (
            AgentCoreMemorySessionManager,
        )

        memory_config = AgentCoreMemoryConfig(
            memory_id=self.config.memory_id,
            session_id=session_id or str(uuid.uuid4()),
            actor_id=_resolve_actor_id(token),
        )
        return AgentCoreMemorySessionManager(
            agentcore_memory_config=memory_config,
            region_name=self.config.region,
        )

    def _build_model(self) -> BedrockModel:
        """Build the model with prompt caching and an explicit context window.

        cache_config strategy="auto" injects cache points to maximize coverage. As of
        Strands 1.53 the system prompt is cached by default (system_prompt_ttl=True) —
        this was a breaking change from earlier versions where it was not.

        context_window_limit lets the conversation manager compute real utilization
        instead of guessing, which is what makes proactive compression accurate.

        To add Bedrock Guardrails, pass guardrail_id and guardrail_version here.
        """
        return BedrockModel(
            model_id=self.config.model_id,
            region_name=self.config.region,
            cache_config=CacheConfig(strategy="auto", system_prompt_ttl=True),
            context_window_limit=self.config.context_window_tokens,
        )

    def _build_state(self, token: str) -> dict:
        """Initial agent.state — durable metadata that survives compaction.

        Tools read these at call time via tool_context.agent.state. Never pass user
        identity as a tool parameter; it belongs here (local tools) or in the
        Authorization header (MCP tools via the Gateway).
        """
        return {"user_id": _resolve_actor_id(token) if token else ""}

    def _build_agent(
        self,
        tools: list,
        plugins: list | None = None,
        hooks: list | None = None,
        interventions: list | None = None,
        session_manager=None,
        state: dict | None = None,
    ) -> Agent:
        kwargs: dict = {
            "model": self._build_model(),
            "tools": tools,
            "system_prompt": build_system_prompt(),
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
