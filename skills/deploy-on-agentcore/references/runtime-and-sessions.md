# AgentCore Runtime & Session Management

## Table of Contents
1. [Runtime Model: VM-per-Session](#runtime-model-vm-per-session)
2. [AgentCore Runtime Entrypoint](#agentcore-runtime-entrypoint)
3. [Two Deployment Artifacts: Container or Code Zip](#two-deployment-artifacts-container-or-code-zip)
4. [Session (Runtime Container)](#session-runtime-container)
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

### Verified

Measured on a deployed runtime, because "global state persists" is the claim everything
else rests on. A module-level agent with `SESSION_BACKEND=memory` and **no external
store** served a three-turn conversation in which turn 2 said "what are **its**
prerequisites" and turn 3 said "which of **those** also lead to X". Both referents
resolved correctly. Neither is answerable without the prior turns in context, so the
microVM retained `agent.messages` across invocations.

Practical consequence: an agent moving onto Runtime can usually **delete** its session
store, its session cache, and its flush policy outright — not replace them.

### Cold start

| | Measured |
|---|---|
| First invoke on a new session (includes microVM provisioning) | **3.46s** |
| Subsequent invokes, same session | **1.50s** |
| **microVM start overhead** | **~1.96s** |

Identical prompt, three runs each. ~2s is invisible against a multi-second agent turn,
which is what makes per-session compute viable at all — but it is not zero, so a client
that fails to send a consistent session ID pays it on **every** request.

A measurement caution: comparing a simple first turn against more complex later turns
produced a *negative* overhead. Hold the prompt constant.

### Quotas that shape the design

Verified via `aws service-quotas list-aws-default-service-quotas --service-code
bedrock-agentcore`. Check `list-service-quotas` too — an account may already have
increases applied.

| Quota | Default | Adjustable |
|---|---|---|
| **Request timeout** | **15 min** | **No** |
| Max payload (request and response) | 100 MB | No |
| Docker image size | 2 GB | No |
| Active session workloads per account | 5,000 | Yes |
| New session creation rate | 25/s | Yes |
| Runtime data plane rate | 1,000/s | Yes |
| Endpoints per agent | 10 | Yes |
| Versions per agent | 1,000 | Yes |

The 15-minute timeout is the one to design around: a turn that can exceed it must become
an async background task reporting `HealthyBusy` from `/ping` and polled separately.

**There is a second non-adjustable ceiling** that is easy to miss: `Streaming maximum duration`
(`L-C91AC63F`) at 60 minutes. A long-lived SSE or WebSocket stream is bounded by it even when
individual requests stay under 15 minutes.

Note also what is **absent** from Service Quotas: there is no per-session CPU or memory quota, so
per-session resource sizing cannot be verified through the quota API.

**Region availability: probe, do not trust a list.** Confirmed present in `us-east-1`,
`us-west-2`, `ap-northeast-1`, `ap-northeast-2`, `eu-central-1`, `eu-west-1`,
`ap-southeast-2` via `list-agent-runtimes`. Widely-repeated material still claims four
regions.

### ARM64 is required, and the error names the wrong culprit

**This applies to the microVM compute type.** The **Instances** compute type supports
**x86_64 and arm64** `[docs]`, so an amd64-only dependency is a blocker on microVMs and not on
Instances — which can invert an architecture gate. This file is the owner of that fact;
`migrate-eks-to-agentcore/references/constraints.md` previously stated it while naming this file
as the source, so a reader checking the citation found nothing.

For microVMs: `linux/arm64` only. An amd64 image does not fail informatively: it **pulls
successfully**, the container is Created and Started, then dies with

```
exec /usr/local/bin/python: exec format error
```

which presents as an application crash loop. Check image architecture against the host
before debugging the application. (The same error appears in reverse on EKS if an arm64
image lands on an amd64 node — EKS Auto Mode's built-in `general-purpose` NodePool is
hardcoded to amd64, so running one image on both platforms needs a Graviton NodePool.)

For what any of this costs, see **[cost-and-billing.md](cost-and-billing.md)** — session
lifetime is the dominant meter, not CPU.

---

## AgentCore Runtime Entrypoint

The agent runs as a Docker container on Bedrock AgentCore Runtime (serverless). The entrypoint
uses `BedrockAgentCoreApp` with an `@app.entrypoint` handler.

**Three handler shapes are supported, not just the async generator** — worth knowing, because a
plain `return` handler is correct and earlier versions of this file implied it was not.
`[verified]` from `BedrockAgentCoreApp._invoke_handler` in `bedrock-agentcore` 1.22.0:

| Handler | Dispatched onto |
|---|---|
| `async def` + `yield` (async generator) | bridged through the worker loop as a sync generator |
| `async def` + `return` | a **dedicated worker event loop** |
| plain `def` (incl. sync generators) | the thread pool |

Stream only if the caller streams; a specialist agent invoked by another agent has no reason to.

**The isolation is load-bearing, and it is one of the hard parts AgentCore removes.** The
docstring's own reason: *"This ensures the main event loop stays responsive for /ping health
checks regardless of whether handlers contain blocking operations."* So a blocking call inside
the entrypoint does **not** stall the health check the way it would in a hand-rolled FastAPI
service. If an assessment found event-loop blocking or an undersized executor on their EKS
service, that defect does not carry across — say so, rather than scheduling a fix for both
platforms.

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

## Two Deployment Artifacts: Container or Code Zip

Everything above and below assumes a container image, because that is the path this skill's
templates and CDK use. **There is a second path, and earlier versions of these references did not
mention it at all** — which led one generated migration plan to build a 138-line CodeBuild stack,
a Dockerfile rewrite and an architecture assertion to solve a problem this path removes.

`agentRuntimeArtifact` is a union: `containerConfiguration` **or** `codeConfiguration`.

```python
agentRuntimeArtifact={
    "codeConfiguration": {
        "code": {"s3": {"bucket": f"bedrock-agentcore-code-{account_id}-{region}",
                        "prefix": f"{agent_name}/deployment_package.zip"}},
        "runtime": "PYTHON_3_13",
        "entryPoint": ["opentelemetry-instrument", "main.py"],  # drop the wrapper if no ADOT dep
    }
},
lifecycleConfiguration={"idleRuntimeSessionTimeout": 300, "maxLifetime": 1800},
```

In CDK: `CfnRuntime.AgentRuntimeArtifactProperty(code_configuration=...)` with
`CodeConfigurationProperty(code, entry_point, runtime)` `[verified]` in aws-cdk-lib.

### Why it matters for a migration, beyond convenience

| | Container | Code zip |
|---|---|---|
| Needs a Dockerfile, ECR, an image build | yes | **no** |
| Command override | **none** — `containerConfiguration` carries only a URI, so the image `CMD` must be the AgentCore entrypoint and any second consumer (an EKS Deployment) must override it | **`entryPoint` is a property of the runtime** |
| Size ceiling | image limit (2048 MB) | **250 MB zipped / 750 MB unzipped**, combined `[docs]` |
| Architecture | arm64 (microVMs) | arm64 — the zip must contain arm64 wheels |

The middle row is the decisive one for a **dual-platform phase**. On the container path, "one
image serving both EKS and AgentCore" forces a change to the running EKS Deployment. On the code
path there is no shared `CMD` to fight over, so that whole problem — and the Phase 0 item it
generates — disappears. For a small pure-Python agent this is usually the cheaper migration.

### Traps specific to the zip

- **Build the wheels for arm64**, not for your laptop. Pure-Python packages are fine anywhere;
  NumPy, Pandas and anything with C extensions are not:
  ```bash
  uv pip install --python-platform aarch64-manylinux2014 --python-version 3.13 \
      --target=deployment_package --only-binary=:all: -r pyproject.toml
  ```
  A package available only as a source distribution must be built on an arm64 Amazon Linux host.
- **Exclude `__pycache__`.** Bytecode compiled on a different architecture may not load `[docs]`.
- **File permissions are enforced:** 644 for non-executable files, 755 for directories and
  executables. Zips built on Windows commonly violate this.
- The zip is decompressed at **`/var/task`**, which is the head of `sys.path` — so a vendored
  dependency in a subfolder is imported as `from <folder> import <pkg>`.

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

> Summary only. For unified vs split telemetry, the span model, GenAI semantic conventions,
> custom spans, and the silent failure modes, see **[observability.md](observability.md)**.

### Setup

1. Add dependencies:
   ```
   strands-agents[otel]
   aws-opentelemetry-distro>=0.18   # <0.18 silently falls back to split telemetry
   ```

2. Use the ADOT wrapper as the CMD in your Dockerfile:
   ```dockerfile
   CMD ["opentelemetry-instrument", "python", "app.py"]
   ```

3. **Do NOT set OTEL environment variables** (no `OTEL_EXPORTER_*`, no `OTEL_SERVICE_NAME`). The sidecar configures these automatically for AgentCore-hosted agents. Agents hosted *outside* Runtime are the opposite case — there you must set them yourself.

4. **Enable CloudWatch Transaction Search.** An account/region-level setting outside your stack. Required by AgentCore Evaluations in both telemetry delivery modes; without it, evaluations find no sessions and report nothing.

### What You Get

- **Traces**: Strands Agent tool calls, MCP requests, Bedrock model invocations
- **Metrics**: Latency, error rates, token usage, session count, duration
- **Logs**: Container stdout appears in `[runtime-logs]` CloudWatch log streams. Where structured OTEL data lands depends on delivery mode — the `spans` stream in unified mode, `otel-rt-logs` in split mode.

### Log Streams in CloudWatch

The log **group** is per runtime *and endpoint*:
`/aws/bedrock-agentcore/runtimes/{runtime-id}-{endpoint-name}`. The endpoint suffix is not
optional — `[measured:reference]`, read off 10 live runtimes, every group ended `-DEFAULT` or
`-<endpoint_name>`. An earlier version of this line omitted it, and a `describe-log-groups`
built from the documented path returns nothing against a perfectly healthy runtime, which
presents as "the agent is not logging".

```bash
aws logs describe-log-groups --log-group-name-prefix /aws/bedrock-agentcore/runtimes \
  --region <r> --query 'logGroups[].logGroupName' --output text
```

Within that group, the streams are:

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
