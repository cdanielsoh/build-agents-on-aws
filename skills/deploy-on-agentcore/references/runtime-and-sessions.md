# AgentCore Runtime & Session Management

## Table of Contents
1. [Runtime Model: VM-per-Session](#runtime-model-vm-per-session)
2. [AgentCore Runtime Entrypoint](#agentcore-runtime-entrypoint)
3. [Session (Runtime Container)](#session-runtime-container)
4. [SessionBuilder (Construction)](#sessionbuilder-construction)
5. [Configuration](#configuration)
6. [No Graceful Shutdown](#no-graceful-shutdown)
7. [Observability (ADOT)](#observability-adot)
8. [IAM Permissions](#iam-permissions)

---

## Runtime Model: VM-per-Session

AgentCore Runtime allocates **one container per session**. A session is identified by the `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header or the `session_id` on the context object. All requests within the same session route to the same container.

This means:
- **No multi-session management needed** — the container serves exactly one session
- **Global state persists** across invocations within the session
- **No thread-safety concerns** — requests are serial within a session
- **Container is hard-killed** when the session ends (no SIGTERM — see below)

The correct pattern is a **singleton Session** initialized on first request and reused for the lifetime of the container.

---

## AgentCore Runtime Entrypoint

The agent runs as a Docker container on Bedrock AgentCore Runtime (serverless). The entrypoint uses `BedrockAgentCoreApp` with an `@app.entrypoint` async generator.

### Basic Structure

```python
import logging
from contextlib import asynccontextmanager

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from core.config import AgentConfig
from core.builder import SessionBuilder
from core.session import Session

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_builder: SessionBuilder | None = None
_session: Session | None = None


def _extract_token(context) -> str | None:
    if hasattr(context, "request_headers") and context.request_headers:
        header = context.request_headers.get("Authorization", "")
        if header:
            return header
    return None


@asynccontextmanager
async def lifespan(app):
    global _builder
    _builder = SessionBuilder(AgentConfig.from_env())
    logger.info("Container ready.")
    yield


app = BedrockAgentCoreApp(lifespan=lifespan)


@app.entrypoint
async def agent_invocation(payload, context):
    global _session

    prompt = payload.get("prompt", "").strip()
    if not prompt:
        yield {"error": "No prompt provided"}
        return

    token = _extract_token(context)
    session_id = getattr(context, "session_id", "") or ""

    if _session is None:
        _session = _builder.build(token, session_id)
    elif token:
        _session.refresh_token(token)

    for chunk in _session.stream(prompt):
        yield chunk


if __name__ == "__main__":
    app.run()
```

### Key Design Decisions

**Streaming by default**: Uses `agent.stream_async()` — Strands' native async streaming — to yield all events (text chunks, tool calls, tool results) directly to the caller. The agent is created with `callback_handler=None` so events flow through the stream, not stdout.

**Builder in lifespan, Session on first request**: The `SessionBuilder` (which loads config from SSM) is created at container startup. The `Session` itself is lazily created on the first invocation when the OAuth token is available.

**Separation of concerns**: `app.py` is pure wiring — no business logic. `SessionBuilder` handles construction. `Session` handles runtime.

**Token refresh**: On each request, the OAuth token is updated on the existing session via `refresh_token()`. This handles token refresh without recreating the session or MCP connection.

**No cleanup**: The lifespan `yield` block won't run (containers are hard-killed). Don't rely on it for saving state.

---

## Session (Runtime Container)

The `Session` class is a thin runtime container. It holds the assembled Agent and the shared headers dict, but **does not create its own dependencies** — that's the builder's job.

```python
from collections.abc import AsyncIterator
from typing import Any

from strands import Agent


class Session:
    """Runtime container for a single agent session.

    Constructed by SessionBuilder — does not create its own dependencies.
    The _headers dict is shared by reference with the MCPClient transport,
    so refresh_token propagates automatically without reconnecting.
    """

    __slots__ = ("agent", "_headers")

    def __init__(self, agent: Agent, headers: dict[str, str]):
        self.agent = agent
        self._headers = headers

    def invoke(self, message: str) -> str:
        """Invoke the agent and return the full response (blocking)."""
        return str(self.agent(message))

    async def stream_async(self, message: str) -> AsyncIterator[Any]:
        """Yield all agent events (text chunks, tool calls, tool results).

        Uses Strands Agent's native stream_async — no threading needed.
        The agent must be created with callback_handler=None to avoid
        double output (stream + stdout).
        """
        async for event in self.agent.stream_async(message):
            yield event

    def refresh_token(self, token: str):
        """Update the token in-place. The MCPClient picks it up by reference."""
        bearer = token if token.startswith("Bearer ") else f"Bearer {token}"
        self._headers["Authorization"] = bearer
```

### Mutable Headers Pattern

The `_headers` dict is created by the builder and shared by reference with the MCPClient transport. When `refresh_token()` mutates the dict contents, the transport sees the new value on its next request — no reconnection needed. This is standard Python reference semantics (the dict object is shared, not copied).

### Why Session is Thin

Previous patterns had Session create its own MCP client, memory manager, and agent internally. This made it impossible to:
- Swap implementations for testing
- Customize construction without modifying Session
- Understand the dependency graph

By extracting construction into `SessionBuilder`, Session becomes testable and its responsibilities are clear: hold state, invoke, refresh token.

---

## SessionBuilder (Construction)

The `SessionBuilder` assembles all dependencies and constructs a `Session`. Each `_build_*` method handles one concern and can be overridden independently in a subclass.

```python
import base64
import json
import logging
import uuid

from strands import Agent
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client

from core.config import AgentConfig
from core.session import Session
from tools import make_tools
from prompts.system import build_system_prompt

logger = logging.getLogger(__name__)


def _extract_actor_id(token: str) -> str:
    """Extract 'sub' claim from JWT payload without verification."""
    try:
        raw = token.removeprefix("Bearer ").split(".")[1]
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload.get("sub", str(uuid.uuid4()))
    except Exception:
        return str(uuid.uuid4())


class SessionBuilder:
    """Constructs a Session with all dependencies wired up.

    Each _build_* method handles one concern and can be overridden
    independently in a subclass to customise construction without
    rewriting the full pipeline.

    Extension points:
        _build_tools    — return additional local @tool functions
        _build_hooks    — return HookProvider instances (e.g. HITL)
        _build_memory   — configure AgentCore Memory session manager
        _build_agent    — assemble the Strands Agent
    """

    def __init__(self, config: AgentConfig):
        self.config = config

    def build(self, token: str, session_id: str = "") -> Session:
        headers = self._init_headers(token)
        mcp_client = self._build_mcp_client(headers)
        local_tools = self._build_tools()
        hooks = self._build_hooks()
        session_manager = self._build_memory(token, session_id)
        agent = self._build_agent(
            tools=local_tools + [mcp_client],
            hooks=hooks,
            session_manager=session_manager,
        )
        logger.info("Session created.")
        return Session(agent, headers)

    # ── overridable steps ───────────────────────────────────────

    def _init_headers(self, token: str) -> dict[str, str]:
        bearer = token if token.startswith("Bearer ") else f"Bearer {token}"
        return {"Authorization": bearer}

    def _build_mcp_client(self, headers: dict[str, str]) -> MCPClient:
        return MCPClient(
            lambda: streamablehttp_client(
                self.config.gateway_url, headers=headers,
            )
        )

    def _build_tools(self) -> list:
        """Override to inject stateful tools (working memory, entity mapper, etc.)."""
        return make_tools()

    def _build_hooks(self) -> list:
        """Override to add HookProviders (e.g. ConfirmationHook for HITL)."""
        return []

    def _build_memory(self, token: str, session_id: str):
        """Build AgentCore Memory session manager (or None to disable)."""
        if not (self.config.memory_enabled and self.config.memory_id):
            return None

        from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
        from bedrock_agentcore.memory.integrations.strands.session_manager import (
            AgentCoreMemorySessionManager,
        )

        actor_id = _extract_actor_id(token)
        mem_session_id = session_id or str(uuid.uuid4())

        memory_config = AgentCoreMemoryConfig(
            memory_id=self.config.memory_id,
            session_id=mem_session_id,
            actor_id=actor_id,
        )
        mgr = AgentCoreMemorySessionManager(
            agentcore_memory_config=memory_config,
            region_name=self.config.region,
        )
        logger.info("AgentCore Memory enabled (actor=%s, session=%s).", actor_id, mem_session_id)
        return mgr

    def _build_agent(self, tools: list, hooks: list, session_manager=None) -> Agent:
        kwargs: dict = {
            "model": self.config.model_id,
            "tools": tools,
            "system_prompt": build_system_prompt(),
            "callback_handler": None,  # events go through stream_async, not stdout
        }
        if hooks:
            kwargs["hooks"] = hooks
        if session_manager:
            kwargs["session_manager"] = session_manager
        return Agent(**kwargs)
```

### Extending the Builder

To customize construction, subclass `SessionBuilder` and override specific steps:

```python
class MyBuilder(SessionBuilder):
    def _build_tools(self):
        """Add working memory and entity mapper tools."""
        memory = WorkingMemory(...)
        mapper = EntityIndexMapper()
        return make_domain_tools(memory, mapper)

    def _build_hooks(self):
        """Add confirmation hook for destructive actions."""
        return [ConfirmationHook(tools=["perform_action"])]
```

Then use the subclass in `app.py`:

```python
@asynccontextmanager
async def lifespan(app):
    global _builder
    _builder = MyBuilder(AgentConfig.from_env())
    yield
```

---

## Configuration

Configuration is loaded from environment variables (set in CfnRuntime) and SSM Parameter Store.

```python
import os
import boto3
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentConfig:
    gateway_url: str
    model_id: str
    region: str
    memory_id: str
    memory_enabled: bool

    @classmethod
    def from_env(cls):
        region = os.environ.get("AWS_REGION", "us-west-2")
        ssm = boto3.client("ssm", region_name=region)
        prefix = os.environ.get("MCP_SSM_PREFIX", "/mcp/endpoints/gateway")
        gateway_url = ssm.get_parameter(Name=f"{prefix}/unified")["Parameter"]["Value"]

        return cls(
            gateway_url=gateway_url,
            model_id=os.environ.get("LLM_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0"),
            region=region,
            memory_id=os.environ.get("AGENTCORE_MEMORY_ID", ""),
            memory_enabled=os.environ.get("AGENTCORE_MEMORY_ENABLED", "false").lower() == "true",
        )
```

Environment variables are set in the CfnRuntime `environment_variables` property. The Gateway URL comes from SSM (written by the Gateway stack), allowing MCP servers to be redeployed without redeploying the agent.

---

## No Graceful Shutdown

**AgentCore Runtime hard-kills containers.** There is no SIGTERM, no atexit, no lifespan cleanup. When the session ends or the container is scaled down, it is terminated immediately.

This has important implications:

1. **No batch saves**: Don't accumulate data in memory and flush on shutdown — the flush will never happen.
2. **AgentCore Memory `batch_size=1`**: The default `batch_size=1` on `AgentCoreMemorySessionManager` is the only safe option. Each conversation turn is saved immediately.
3. **No cleanup code**: Don't put important logic in `lifespan` teardown, `atexit` handlers, or signal handlers.
4. **Stateless between turns**: Any state that must survive must be written to an external store (Memory, DynamoDB, etc.) on every turn.

The lifespan context manager is still useful for startup initialization (builder creation), but **never rely on the teardown phase**.

---

## Observability (ADOT)

AgentCore Runtime includes an ADOT (AWS Distro for OpenTelemetry) sidecar that collects traces and metrics automatically.

### Setup

1. Add dependencies to `requirements.txt`:
   ```
   strands-agents[otel]
   aws-opentelemetry-distro
   ```

2. Use the ADOT wrapper as the CMD in your Dockerfile:
   ```dockerfile
   CMD ["opentelemetry-instrument", "python", "app.py"]
   ```

3. **Do NOT set OTEL environment variables** (no `OTEL_EXPORTER_*`, no `OTEL_SERVICE_NAME`). The sidecar configures these automatically for AgentCore-hosted agents.

### What You Get

- **Traces**: Strands Agent tool calls, MCP requests, Bedrock model invocations
- **Metrics**: Latency, error rates, token usage
- **Logs**: Container stdout appears in `[runtime-logs]` CloudWatch log streams; structured OTEL data appears in `otel-rt-logs` log streams

### Log Streams in CloudWatch

AgentCore creates log streams under `/aws/bedrock-agentcore/runtimes/{runtime-id}/`:

| Stream Pattern | Content |
|---|---|
| `[runtime-logs]` | Container stdout/stderr (your `logger.info()` calls) |
| `otel-rt-logs` | Structured OTEL telemetry (traces, metrics) |

Note: You may see OTLP connection errors in `[runtime-logs]` (e.g., "Failed to export to localhost:4317"). These are harmless — actual telemetry flows through the sidecar's own mechanism.

### IAM Requirements for Observability

The runtime role needs these permissions (see IAM section below):
- `xray:PutTraceSegments`, `xray:PutTelemetryRecords`, `xray:GetSamplingRules`, `xray:GetSamplingTargets`
- `cloudwatch:PutMetricData` (with namespace condition `bedrock-agentcore`)
- `logs:DescribeLogGroups` on `log-group:*` — **critical, without this no `[runtime-logs]` streams are created**

---

## IAM Permissions

The runtime IAM role must include all of these for a fully functional agent. Missing permissions cause silent failures (e.g., no logs, no traces).

```python
# Trust policy
runtime_role = iam.Role(
    self, "AgentRuntimeRole",
    assumed_by=iam.CompositePrincipal(
        iam.ServicePrincipal(
            "bedrock-agentcore.amazonaws.com",
            conditions={
                "StringEquals": {"aws:SourceAccount": self.account},
                "ArnLike": {
                    "aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:*"
                }
            }
        )
    ),
)

# Bedrock model access
runtime_role.add_to_policy(iam.PolicyStatement(
    actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
    resources=["*"],
))

# ECR image pull
runtime_role.add_to_policy(iam.PolicyStatement(
    actions=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"],
    resources=[f"arn:aws:ecr:{self.region}:{self.account}:repository/*"],
))
runtime_role.add_to_policy(iam.PolicyStatement(
    actions=["ecr:GetAuthorizationToken"],
    resources=["*"],
))

# SSM Parameter Store (for Gateway URL, config)
runtime_role.add_to_policy(iam.PolicyStatement(
    actions=["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"],
    resources=[
        f"arn:aws:ssm:{self.region}:{self.account}:parameter/mcp/endpoints/*",
        f"arn:aws:ssm:{self.region}:{self.account}:parameter/agent/runtime/*",
    ],
))

# AgentCore Memory API
runtime_role.add_to_policy(iam.PolicyStatement(
    actions=[
        "bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent",
        "bedrock-agentcore:ListEvents", "bedrock-agentcore:DeleteEvent",
        "bedrock-agentcore:RetrieveMemoryRecords",
        "bedrock-agentcore:ListMemoryRecords",
        "bedrock-agentcore:GetMemoryRecord",
        "bedrock-agentcore:DeleteMemoryRecord",
    ],
    resources=[f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:memory/*"],
))

# CloudWatch Logs — ALL THREE statements are required
runtime_role.add_to_policy(iam.PolicyStatement(
    actions=["logs:CreateLogGroup", "logs:DescribeLogStreams"],
    resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/*"],
))
runtime_role.add_to_policy(iam.PolicyStatement(
    actions=["logs:DescribeLogGroups"],
    resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:*"],
))
runtime_role.add_to_policy(iam.PolicyStatement(
    actions=["logs:CreateLogStream", "logs:PutLogEvents"],
    resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"],
))

# CloudWatch Metrics
runtime_role.add_to_policy(iam.PolicyStatement(
    actions=["cloudwatch:PutMetricData"],
    resources=["*"],
    conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
))

# X-Ray tracing (AgentCore Observability)
runtime_role.add_to_policy(iam.PolicyStatement(
    actions=[
        "xray:PutTraceSegments", "xray:PutTelemetryRecords",
        "xray:GetSamplingRules", "xray:GetSamplingTargets",
    ],
    resources=["*"],
))

# Workload Identity
runtime_role.add_to_policy(iam.PolicyStatement(
    actions=[
        "bedrock-agentcore:GetWorkloadAccessToken",
        "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
        "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
    ],
    resources=[
        f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default",
        f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default/workload-identity/*",
    ],
))
```

### Common Pitfall: Missing `logs:DescribeLogGroups`

Without `logs:DescribeLogGroups` on `log-group:*`, the AgentCore sidecar cannot discover log groups and **no `[runtime-logs]` streams are created**. Container stdout is silently dropped. This is the most common cause of "no logs" on a new runtime.
