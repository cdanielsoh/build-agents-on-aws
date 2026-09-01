# MCP Gateway & Lambda MCP Servers

## Table of Contents
1. [MCP Gateway Architecture](#mcp-gateway-architecture)
2. [Target Types: Not Just Tools](#target-types-not-just-tools)
3. [Interceptor Lambda](#interceptor-lambda)
4. [Lambda MCP Server Handler](#lambda-mcp-server-handler)
5. [Direct vs Adapter Pattern](#direct-vs-adapter-pattern)
6. [MCP Client in the Agent](#mcp-client-in-the-agent)

---

## MCP Gateway Architecture

AgentCore Gateway provides a single MCP endpoint that routes tool calls to multiple Lambda targets. This replaces managing N separate MCP server connections.

```
Agent
  |
  v
MCP Gateway (single endpoint)
  |-- OAuth validation (CUSTOM_JWT)
  |-- Interceptor Lambda (identity -> trusted scope)
  |
  +-- Target A: Lambda MCP (user-data tools)
  +-- Target B: Lambda MCP (catalog tools)
  +-- Target C: Lambda MCP (operations tools)
```

### Why Gateway over Direct Connections

| Approach | Pros | Cons |
|----------|------|------|
| **Gateway** | Single endpoint, centralized auth, pay-per-invocation Lambda targets, no idle cost | Extra hop through Gateway |
| **Direct Runtime** | Lower latency per call | N separate connections, each needs auth, always-on containers cost more |

Gateway is the recommended pattern for most use cases. Direct connections make sense only when you need sub-100ms tool call latency and are willing to pay for always-on MCP servers.

### Gateway Components

1. **Gateway Resource** — CfnGateway with `CUSTOM_JWT` authorizer pointing to Cognito
2. **Interceptor Lambda** — The only way identity reaches a Lambda target. Verifies the JWT and injects a trusted scope into the tool call; see `security.md`
3. **Lambda Targets** — Each target is a Lambda function with tool schema definitions
4. **Tool Schemas** — Declared in CDK, Gateway knows all available tools for semantic routing

### Tool Naming Convention

Gateway exposes tools with the format: `{target-name}___{tool-name}`

For example, if target is `user-data-mcp-target` and tool is `get_profile`, the Gateway exposes it as `user-data-mcp-target___get_profile`. The agent sees and calls this full name; the Gateway strips the prefix before invoking the Lambda.

**The target name is load-bearing beyond cosmetics.** Cedar actions are named with this same
prefix (`AgentCore::Action::"user-data-mcp-target___get_profile"`), so renaming a target makes
every policy that names it dead — and under Cedar's default-deny the tool is then silently
blocked with no error anywhere. Group tools into targets along the lines you intend to
authorize, and assert the target-name/action-prefix agreement in a test. See
[policy.md](policy.md#cedar-limitations).

---

## Target Types: Not Just Tools

`TargetConfiguration` accepts three kinds of target, and most treatments only mention the
first:

| Type | Fronts | Sub-types |
|---|---|---|
| `mcp` | Tools | `lambda`, `mcpServer`, `openApiSchema`, `smithyModel`, `apiGateway`, `connector` |
| `http` | A plain HTTP endpoint | — |
| `inference` | **The model path** | `connector`, `provider` |

### Inference targets

A Gateway can front **model** calls, not only tool calls — `InferenceTargetConfiguration`
takes either a connector or a provider (`endpoint`, `modelMapping`, `operations`), with
per-operation `InferenceConfiguration` for `maxTokens`, `temperature`, `topP`, and
`stopSequences`, and a per-operation model list.

That means one governed endpoint for both halves of the agent's egress: the same
`CUSTOM_JWT` authorizer, the same Cedar policy engine, and the same guardrails apply to
inference as to tools. Architecturally this is the cleaner default — before reaching for a
third-party LLM proxy, check whether an inference target covers what you need.

### When a proxy still earns its place

Inference targets give you multi-provider routing, per-model token limits, and guardrails.
What they do not give you is **dollar-denominated budgets and chargeback**. If the requirement
is "each person gets $100 of model spend per month, and finance needs per-team attribution",
that is still a metering proxy's job (LiteLLM or equivalent), with per-user virtual keys handed
out through the Identity token vault — see [identity.md](identity.md).

Two things to get right if you go that way:

**Enforce the routing in IAM, not configuration.** Remove `bedrock:InvokeModel` from the
runtime role. Otherwise the proxy is a convention the agent could bypass rather than a control
it cannot. See [security.md](security.md#enforce-the-model-path-in-iam-not-just-config).

**Budget per person, not per team.** A shared team pool where each member's key ceiling equals
the whole pool means one member can exhaust it for everyone. Give each person a ceiling and
derive the team cap as the sum.

---

## Interceptor Lambda

Interceptors are Lambda functions that execute during each Gateway invocation. They let you run custom logic at specific points in the request/response lifecycle — validation, transformation, access control, or response filtering.

**For any multi-tenant agent, the REQUEST interceptor is not optional.** A Lambda target
receives only the tool's `inputSchema` properties and gateway/target/tool IDs — no JWT. The
interceptor is the only supported place to turn a verified token into data the tool receives.
Read [security.md](security.md#request-interceptor-the-only-bridge) before writing one; the
strip-before-inject rule there is what stops the model forging its own scope.

### Types and Lifecycle

A Gateway supports up to two interceptors — one per interception point:

| Type | When It Runs | Use Cases |
|------|-------------|-----------|
| **REQUEST** | Before the Gateway calls the target Lambda | Header forwarding, request validation, custom authorization, fine-grained tool access control |
| **RESPONSE** | After the target responds, before the Gateway returns to the caller | Response filtering, redaction of sensitive data, adding custom headers |

Both types can be configured on the same Gateway. A single Lambda function can handle both by checking whether `gatewayResponse` is present in the event.

```
Caller → [REQUEST interceptor] → Target Lambda → [RESPONSE interceptor] → Caller
```

If the REQUEST interceptor returns a `transformedGatewayResponse`, the Gateway short-circuits — it skips the target call and returns the response directly. The RESPONSE interceptor still runs even in this case.

### Event Structure

**REQUEST interceptor input:**

```json
{
    "interceptorInputVersion": "1.0",
    "requestContext": {"identity": {...}, "requestId": "..."},
    "mcp": {
        "rawGatewayRequest": {"body": "<raw_request_body>"},
        "gatewayRequest": {
            "path": "/mcp",
            "httpMethod": "POST",
            "headers": {...},
            "body": {"method": "tools/call", "params": {...}}
        }
    }
}
```

- `headers` is only present if `passRequestHeaders` is `true` in the interceptor configuration
- `body.method` contains the MCP method being called (e.g., `tools/call`, `tools/list`)

**RESPONSE interceptor input** — same structure plus `gatewayResponse`:

```json
{
    "interceptorInputVersion": "1.0",
    "mcp": {
        "rawGatewayRequest": {...},
        "gatewayRequest": {...},
        "gatewayResponse": {
            "statusCode": 200,
            "headers": {...},
            "body": {...}
        }
    }
}
```

### Output Format

Both types must return `interceptorOutputVersion: "1.0"`.

**REQUEST interceptor** — forward (optionally modified) request to target:

```json
{
    "interceptorOutputVersion": "1.0",
    "mcp": {
        "transformedGatewayRequest": {
            "headers": {"Authorization": "Bearer ..."},
            "body": {...}
        }
    }
}
```

**REQUEST interceptor** — short-circuit (skip target, return response directly):

```json
{
    "interceptorOutputVersion": "1.0",
    "mcp": {
        "transformedGatewayResponse": {
            "statusCode": 403,
            "body": {"error": "Access denied"}
        }
    }
}
```

**RESPONSE interceptor** — return (optionally modified) response:

```json
{
    "interceptorOutputVersion": "1.0",
    "mcp": {
        "transformedGatewayResponse": {
            "statusCode": 200,
            "body": {...}
        }
    }
}
```

### Implementation: Combined REQUEST + RESPONSE Interceptor

A single Lambda can handle both interception points by checking for the presence of `gatewayResponse`:

```python
import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    """
    Gateway Interceptor handling both REQUEST and RESPONSE interception.

    - REQUEST: Extracts Authorization header and forwards to targets.
               Can also validate requests or enforce tool-level access control.
    - RESPONSE: Can filter/redact sensitive data before returning to caller.
    """
    mcp_data = event.get('mcp', {})

    if mcp_data.get('gatewayResponse') is not None:
        # ===== RESPONSE interceptor =====
        return _handle_response(mcp_data)
    else:
        # ===== REQUEST interceptor =====
        return _handle_request(event, mcp_data)


def _handle_request(event, mcp_data):
    """Forward Authorization header and add correlation headers."""
    request_context = event.get('requestContext', {})
    gateway_request = mcp_data.get('gatewayRequest', {})
    original_headers = gateway_request.get('headers', {})
    request_body = gateway_request.get('body', {})

    # Log the MCP method for observability
    mcp_method = request_body.get('method', 'unknown') if isinstance(request_body, dict) else 'unknown'
    logger.info(f"REQUEST interceptor - MCP method: {mcp_method}")

    # Extract user identity (populated by Gateway authorizer)
    identity = request_context.get('identity', {})
    user_id = identity.get('userId') or identity.get('sub')

    # Build headers to forward to target Lambda
    forward_headers = {}

    # Pass through the Authorization header (already validated by Gateway)
    auth_header = original_headers.get('Authorization') or original_headers.get('authorization')
    if auth_header:
        forward_headers['Authorization'] = auth_header

    # Add correlation headers
    forward_headers['x-correlation-id'] = request_context.get('requestId', '')
    if user_id:
        forward_headers['x-user-id'] = user_id

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": {
                "headers": forward_headers,
                "body": request_body,
            }
        }
    }


def _handle_response(mcp_data):
    """Pass through the response unchanged (customize for redaction/filtering)."""
    gateway_response = mcp_data.get('gatewayResponse', {})

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "body": gateway_response.get('body', {}),
                "statusCode": gateway_response.get('statusCode', 200),
            }
        }
    }
```

### CDK Configuration

```python
# Single Lambda handling both REQUEST and RESPONSE
interceptor_configurations=[
    bedrockagentcore.CfnGateway.GatewayInterceptorConfigurationProperty(
        interception_points=["REQUEST", "RESPONSE"],  # Can be one or both
        interceptor=bedrockagentcore.CfnGateway.InterceptorConfigurationProperty(
            lambda_=bedrockagentcore.CfnGateway.LambdaInterceptorConfigurationProperty(
                arn=interceptor_lambda.function_arn
            )
        ),
        input_configuration=bedrockagentcore.CfnGateway.InterceptorInputConfigurationProperty(
            pass_request_headers=True  # Required to access Authorization header
        ),
    )
]
```

### Key Points

- **One per type**: A Gateway can have at most one REQUEST and one RESPONSE interceptor. You cannot have multiple interceptors of the same type.
- **`passRequestHeaders` must be `true`** for the interceptor to receive request headers (including Authorization). Without this, headers are stripped for security.
- **`interceptorOutputVersion` must be `"1.0"`** — the Gateway rejects responses without this.
- **Idempotency**: The Gateway may retry interceptor calls on failures. Implement idempotent logic (no side effects, or use idempotency keys).
- **Keep it fast**: Interceptors run on every request. Avoid expensive operations.
- **Headers forwarded by the REQUEST interceptor** appear in the target Lambda's `context.client_context.custom.bedrockAgentCorePropagatedHeaders`.
- **Short-circuiting**: A REQUEST interceptor can return `transformedGatewayResponse` to skip the target call entirely — useful for blocking unauthorized tool calls or returning cached responses.
- **On error**: Return the original request body with empty headers to avoid blocking the entire request.

---

## Lambda MCP Server Handler

Each Lambda MCP server needs a handler that translates AgentCore Gateway invocations to FastMCP tool calls.

### Handler Structure

```python
import json
import asyncio
from tools import mcp  # Your FastMCP server instance

def get_tool_name(context):
    """Extract tool name from Lambda context (set by Gateway)."""
    if hasattr(context, 'client_context') and context.client_context:
        if hasattr(context.client_context, 'custom'):
            tool_name = context.client_context.custom.get('bedrockAgentCoreToolName', '')
            # Tool name format: target___toolname, extract just the tool name
            if '___' in tool_name:
                tool_name = tool_name.split('___')[-1]
            return tool_name
    return 'unknown'

def extract_headers(context):
    """Extract propagated headers from Lambda context."""
    headers = {}
    if hasattr(context, 'client_context') and context.client_context:
        if hasattr(context.client_context, 'custom') and context.client_context.custom:
            propagated = context.client_context.custom.get(
                'bedrockAgentCorePropagatedHeaders', {}
            )
            if 'Authorization' in propagated:
                headers['Authorization'] = propagated['Authorization']
            if 'x-user-id' in propagated:
                headers['x-user-id'] = propagated['x-user-id']
    return headers

def extract_arguments(event):
    """Extract tool arguments from event (Gateway unwraps them into the event)."""
    exclude_keys = {'headers', 'requestContext', 'Authorization'}
    return {k: v for k, v in event.items() if k not in exclude_keys}

def lambda_handler(event, context):
    tool_name = get_tool_name(context)
    headers = extract_headers(context)
    arguments = extract_arguments(event)

    # Execute FastMCP tool
    loop = asyncio.get_event_loop()
    result = loop.run_until_complete(execute_tool(tool_name, arguments, headers))

    # Return MCP response format
    return {
        'statusCode': 200,
        'body': json.dumps({
            'content': [{'type': 'text', 'text': result}]
        })
    }
```

### Response Format

Gateway expects responses in this format:

```json
{
    "statusCode": 200,
    "body": "{\"content\": [{\"type\": \"text\", \"text\": \"...\"}]}"
}
```

The `body.content[0].text` contains the actual tool output (usually JSON-serialized).

---

## Direct vs Adapter Pattern

### Direct Pattern (Greenfield)

The MCP Lambda contains business logic and talks directly to the database. This is the recommended approach for new projects.

```python
from fastmcp import FastMCP
import boto3

mcp = FastMCP("user-data-server")
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('Users')

@mcp.tool()
async def get_user_profile(ctx) -> str:
    """Get the authenticated user's profile."""
    # Extract user ID from propagated headers
    token = _get_auth_token(ctx)
    user_id = decode_user_id(token)

    # Query database directly
    response = table.get_item(Key={'user_id': user_id})
    profile = response.get('Item', {})

    return json.dumps(profile)

@mcp.tool()
async def update_user_email(ctx, email: str) -> str:
    """Update the user's email address."""
    token = _get_auth_token(ctx)
    user_id = decode_user_id(token)

    table.update_item(
        Key={'user_id': user_id},
        UpdateExpression='SET email = :e',
        ExpressionAttributeValues={':e': email}
    )
    return json.dumps({"status": "updated", "email": email})
```

**Latency**: ~60-110ms (Lambda cold/warm + DB query)

### Adapter Pattern (Brownfield)

The MCP Lambda wraps an existing REST API. Use this when a backend with business logic already exists.

```python
from fastmcp import FastMCP
import requests

mcp = FastMCP("user-data-server")
API_URL = os.environ['API_URL']  # Existing REST API

@mcp.tool()
async def get_user_profile(ctx) -> str:
    """Get the authenticated user's profile."""
    token = _get_auth_token(ctx)

    # Call existing API (which handles business logic, validation, etc.)
    response = requests.get(
        f"{API_URL}/profile",
        headers={"Authorization": f"Bearer {token}"}
    )
    return response.text

@mcp.tool()
async def update_user_email(ctx, email: str) -> str:
    """Update the user's email address."""
    token = _get_auth_token(ctx)

    response = requests.patch(
        f"{API_URL}/profile/contact",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": email}
    )
    return response.text
```

**Latency**: ~100-200ms (Lambda + API Gateway + backend Lambda + DB)

### When to Use Which

| Situation | Pattern | Why |
|-----------|---------|-----|
| New project, no existing API | Direct | Fewer hops, less complexity |
| Existing API with business logic | Adapter | Reuse code, single source of truth |
| API serves multiple consumers (frontend + agent) | Adapter | Don't duplicate business logic |
| Performance-critical tool calls | Direct | ~2x faster |
| Separate teams (API team vs agent team) | Adapter | Clean ownership boundaries |

---

## MCP Client in the Agent

Since AgentCore Runtime runs one session per container, a single `MCPClient` is sufficient — no pool needed.

### Mutable Headers Pattern

The key pattern is passing a **mutable dict** to the MCPClient transport. When the token is refreshed (e.g., on subsequent requests), updating the dict contents automatically propagates to the transport without reconnecting.

```python
# In SessionBuilder._build_mcp_client():
def _build_mcp_client(self, headers: dict[str, str]) -> MCPClient:
    return MCPClient(
        lambda: streamablehttp_client(
            self.config.gateway_url, headers=headers,
        )
    )

# In SessionBuilder.build():
def build(self, token: str, session_id: str = "") -> Session:
    headers = self._init_headers(token)         # creates the dict
    mcp_client = self._build_mcp_client(headers) # shares it by reference
    # ... build tools, agent ...
    return Session(agent, headers)               # Session.refresh_token() mutates the same dict
```

The `Session` then refreshes the token in-place:

```python
class Session:
    def refresh_token(self, token: str):
        bearer = token if token.startswith("Bearer ") else f"Bearer {token}"
        self._headers["Authorization"] = bearer
```

### Why This Works

The `lambda` captures `headers` by reference. When `refresh_token()` mutates the dict's contents (`self._headers["Authorization"] = ...`), the transport's reference to the same dict object sees the new value. This is standard Python reference semantics — the dict is shared, not copied.

### Why Not MCPClientPool

Previous patterns used an `MCPClientPool` class that managed multiple connections and handled tool name translation. With Gateway as a single endpoint and VM-per-session containers, the Strands `MCPClient` handles everything directly:
- **Single endpoint**: Gateway routes all tool calls, so one MCPClient suffices
- **Tool names**: Gateway exposes tools as `{target}___{tool}`; the agent sees and calls them directly
- **No pool management**: One session = one container = one MCPClient
