# MCP Gateway & Lambda MCP Servers

## Table of Contents
1. [MCP Gateway Architecture](#mcp-gateway-architecture)
2. [Target Types: Not Just Tools](#target-types-not-just-tools)
3. [Interceptors](#interceptors)
4. [Native Tool Search](#native-tool-search-x_amz_bedrock_agentcore_search)
5. [Rate Limits](#rate-limits)
6. [Lambda MCP Server Handler](#lambda-mcp-server-handler)
7. [Direct vs Adapter Pattern](#direct-vs-adapter-pattern)
8. [MCP Client in the Agent](#mcp-client-in-the-agent)

---

## MCP Gateway Architecture

AgentCore Gateway provides a single MCP endpoint that routes tool calls to multiple Lambda targets. This replaces managing N separate MCP server connections.

```
Agent
  |
  v
MCP Gateway (single endpoint)
  |-- OAuth validation (CUSTOM_JWT)
  |-- REQUEST interceptor  (identity -> trusted scope)
  |-- RESPONSE interceptor (redaction / filtering)
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
2. **Interceptors** — REQUEST and/or RESPONSE Lambdas. The REQUEST interceptor is the only way identity reaches a Lambda target; the RESPONSE interceptor is where redaction belongs
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

## Interceptors

Interceptors run your Lambda during a Gateway invocation. Two interception points:

| Type | Runs | Use for |
|------|------|---------|
| **REQUEST** | Before the Gateway calls the target | Custom authorization, request validation, injecting verified identity, short-circuiting |
| **RESPONSE** | After the target responds, before the Gateway replies to the caller | Redaction, PII scrubbing, response filtering, adding headers |

A Gateway may have **at most one of each**. You can configure both on the same Gateway —
often as a single Lambda that branches — but not two of the same type. Interceptors can only
be Lambda functions.

**For any multi-tenant agent the REQUEST interceptor is not optional.** A Lambda target
receives only the tool's `inputSchema` properties and gateway/target/tool IDs — no JWT. The
interceptor is the only supported place to turn a verified token into data the tool receives.
Read [security.md](security.md#request-interceptor-the-only-bridge) for the three rules that
make that safe; the rest of this section is the mechanics.

### The payload shape depends on the target type

This is the first thing to establish, because the two shapes are not interchangeable:

| | MCP targets | HTTP targets |
|---|---|---|
| Top-level key | `mcp` | `http` |
| Applies to | `lambda`, `mcpServer`, `openApiSchema`, `smithyModel`, `apiGateway`, `connector` | AgentCore Runtime, passthrough, **and inference targets** |
| Body format | Parsed JSON (`Map<String, Object>`) | **base64-encoded string** |
| `path` | Always `"/mcp"` | The real path, e.g. `/my-target/invocations` |
| `httpMethod` | Immutable | Immutable |
| `rawGatewayRequest` | Included | Not included |
| RESPONSE in streaming mode | Supported | **Not yet supported** (buffered only) |

Inference targets are a separate target type that happens to share the `http` payload shape —
worth knowing if you are governing the model path through the Gateway.

### The short-circuit divergence — read this one carefully

A REQUEST interceptor that returns `transformedGatewayResponse` makes the Gateway reply
immediately without calling the target, **even if `transformedGatewayRequest` is also
present**. What happens next differs by target type:

| Target type | After a REQUEST short-circuit, does the RESPONSE interceptor run? |
|---|---|
| **MCP** | **Yes** — it still runs |
| **HTTP** | **No** — it does not run |

If you put redaction in the RESPONSE interceptor and denial in the REQUEST interceptor, on MCP
targets your redaction logic will see the denial payload. Handle that case explicitly rather
than assuming the RESPONSE interceptor only ever sees target output.

### MCP payloads

**REQUEST input** (`headers` present only when `passRequestHeaders` is `true`):

```json
{
  "interceptorInputVersion": "1.0",
  "mcp": {
    "rawGatewayRequest": {"body": "<raw_request_body>"},
    "gatewayRequest": {
      "path": "/mcp",
      "httpMethod": "POST",
      "headers": {
        "Authorization": "<bearer_token>",
        "Mcp-Session-Id": "<session_id>",
        "User-Agent": "<client_user_agent>"
      },
      "body": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    }
  }
}
```

**RESPONSE input** — the same, plus `gatewayResponse` with `statusCode`, `headers`, `body`.
A single Lambda distinguishes the two by checking whether `gatewayResponse` is present.

**Output** — for either point:

```json
{
  "interceptorOutputVersion": "1.0",
  "mcp": {
    "transformedGatewayRequest":  {"body": {...}},
    "transformedGatewayResponse": {"statusCode": 200, "body": {...}}
  }
}
```

### RESPONSE interceptors with streaming

When [response streaming](gateway-mcp-streaming.md) is enabled, the RESPONSE interceptor
behaviour changes materially — it is invoked **once per eligible event** rather than once with
a complete response. Check `gatewayResponse.isStreamingResponse` and handle both modes.

Invoked for events carrying a JSON-RPC `id`:

- **First event** — the first JSON-RPC response or server-initiated request (a tool result, or
  an `elicitation/create` / `sampling/createMessage` request).
- **Subsequent events** — any further responses or server-initiated requests, e.g. the final
  tool result after an elicitation is fulfilled.

**Not** invoked for `notifications/progress`, `notifications/message`, or pings — those are
forwarded straight to the client. Do not put anything load-bearing in a path that assumes it
sees every frame.

What you may override depends on position:

| Event | Can override | Ignored if returned |
|---|---|---|
| First | `headers`, `statusCode`, `body` | — |
| Subsequent | `body` only | `headers`, `statusCode` (already sent to the client) |
| Non-streaming | `headers`, `statusCode`, `body` | — |

The practical consequence: **you cannot decide to change the status code based on something
you learn in a later frame.** If a policy decision depends on the full response, either buffer
(disable streaming) or make the decision in the REQUEST interceptor.

### HTTP target payloads

Bodies are base64-encoded strings, not parsed JSON:

```json
{
  "interceptorInputVersion": "1.0",
  "http": {
    "gatewayRequest": {
      "path": "/my-target-name/invocations",
      "httpMethod": "POST",
      "headers": {"Authorization": "<bearer_token>"},
      "body": "<base64_encoded_body>"
    }
  }
}
```

Output mirrors it under `http`, with `transformedGatewayRequest` and/or
`transformedGatewayResponse` (the latter also taking `contentType`). On the response side,
**omitted or `null` fields fall back to the original value**, so to pass a response through
untouched return an empty object:

```json
{"interceptorOutputVersion": "1.0", "http": {}}
```

#### The 6 MB payload limit

Lambda synchronous invocation caps request **and** response combined at 6 MB. A large target
body — routine for inference targets — pushes the base64-encoded payload past that and errors.

Exclude the response body from the interceptor input with a payload filter:

```json
{
  "inputConfiguration": {
    "passRequestHeaders": false,
    "payloadFilter": { "exclude": [{ "field": "RESPONSE_BODY" }] }
  }
}
```

`passRequestHeaders` is **required** in `inputConfiguration` — include it alongside
`payloadFilter` even when false. With the body excluded, the input `body` is `null` and your
function can still read `statusCode`, `contentType`, and `headers`, inject headers, or override
the status code. Return `body: null` and the Gateway uses the original body.

So a RESPONSE interceptor that redacts *content* is incompatible with excluding the body. If
you need both large payloads and content redaction, redact at the target instead.

#### Client context (HTTP targets)

Request metadata arrives through the Lambda invocation's client context: `GATEWAY_ARN`,
`GATEWAY_ACCOUNT_ID`, and `REQUEST_ID` are always present; `SOURCE_IP` only when available, so
treat it as optional.

### Implementation: one Lambda, both points

```python
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    mcp = event.get("mcp") or {}
    if mcp.get("gatewayResponse") is not None:
        return _handle_response(mcp)
    return _handle_request(mcp)


def _handle_request(mcp):
    body = (mcp.get("gatewayRequest") or {}).get("body") or {}

    # tools/list carries no arguments worth scoping, and Cedar already filters the
    # visible tool set. Pass it through.
    if body.get("method") != "tools/call":
        return _passthrough_request(body)

    try:
        claims = verifier.verify(_authorization(mcp.get("gatewayRequest") or {}))
        scope = DataScope.from_claims(claims)
    except Exception as exc:
        # FAIL CLOSED. Never fall back to forwarding the request unscoped.
        logger.warning("denying tools/call: %s", exc)   # never log the token
        return _short_circuit(body, "unauthorized")

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": {"body": _inject_scope(body, scope)}},
    }


def _handle_response(mcp):
    response = mcp.get("gatewayResponse") or {}

    if response.get("isStreamingResponse"):
        # One invocation per event. Only the first may change headers/statusCode.
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {"transformedGatewayResponse": {"body": _redact(response.get("body"))}},
        }

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": response.get("statusCode", 200),
                "body": _redact(response.get("body")),
            }
        },
    }


def _short_circuit(body, message):
    """Deny without calling the target."""
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": 403,
                "body": {
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    # -32603 internal error: do not leak whether a tool exists.
                    "error": {"code": -32603, "message": message},
                },
            }
        },
    }
```

### Configuration

```python
interceptor_configurations=[
    bedrockagentcore.CfnGateway.GatewayInterceptorConfigurationProperty(
        interception_points=["REQUEST", "RESPONSE"],
        interceptor=bedrockagentcore.CfnGateway.InterceptorConfigurationProperty(
            lambda_=bedrockagentcore.CfnGateway.LambdaInterceptorConfigurationProperty(
                arn=interceptor_lambda.function_arn
            )
        ),
        input_configuration=bedrockagentcore.CfnGateway.InterceptorInputConfigurationProperty(
            pass_request_headers=True   # required to see Authorization
        ),
    )
]
```

Equivalent boto3 / AWS CLI shape:

```python
interceptorConfigurations=[{
    "interceptor": {"lambda": {"arn": "arn:aws:lambda:...:function:my-interceptor"}},
    "interceptionPoints": ["REQUEST", "RESPONSE"],
    "inputConfiguration": {"passRequestHeaders": True},
}]
```

The `agentcore` CLI does not configure interceptors: create and deploy the gateway first, then
attach them with `update-gateway` via the CLI or boto3.

### Permissions

The **gateway service role** needs `lambda:InvokeFunction` on the interceptor functions.
**Scope it to those specific function ARNs** — a wildcard on `lambda:InvokeFunction` turns the
gateway role into a general-purpose Lambda invoker.

### Key Points

- **`interceptorOutputVersion` must be `"1.0"`** — the Gateway rejects output without it.
- **`passRequestHeaders` defaults to `false`.** Set it `true` only if you need headers, and
  remember that means live credentials are in your event object: never log the event, the
  headers, or the token. Log derived non-secret values only.
- **Fail closed.** Do *not* "return the original request on error to avoid blocking the
  request" — for an interceptor that establishes identity, that forwards an unscoped call and
  every caller gets every row. Denial is the safe failure.
- **Be idempotent.** The Gateway may retry on failure or timeout. Copy the body rather than
  mutating it, avoid side effects, or use idempotency keys.
- **Keep it fast.** This runs on every request, twice if both points are configured.
- **Interceptors are not a substitute for Cedar.** Cedar decides *which tool*, declaratively
  and reviewably; the interceptor carries identity through to the data layer. See
  [policy.md](policy.md).

---

## Native Tool Search (`x_amz_bedrock_agentcore_search`)

Enable semantic search at gateway creation and the Gateway exposes a built-in tool that finds
tools by natural-language query:

```python
mcp_client.call_tool_sync(
    tool_use_id="tool-123",
    name="x_amz_bedrock_agentcore_search",
    arguments={"query": "find order information"},
)
```

**This is the managed answer to "too many tools to put in the prompt."** Before hand-rolling
schema-level progressive disclosure, check whether this covers the case — it needs no custom
registry, no meta-tool prompt engineering, and the tool inventory cannot drift from what the
gateway actually exposes. The `strands-agent-design` skill's `references/meta-tooling.md`
pattern remains useful for *local* tools, mixed local/MCP fleets, or when you need control over
the disclosure levels; for gateway tools alone, prefer this.

Two constraints:

- **Regional.** Supported in 18 regions at the time of writing (including `us-east-1`,
  `us-west-2`, `eu-west-1`, `ap-northeast-1/2`). Verify yours before designing around it.
- **Protocol version.** The gateway accepts only versions listed in
  `protocolConfiguration.mcp.supportedVersions`. On `2026-07-28` each request additionally
  carries `Mcp-Method` and `Mcp-Name` headers and `_meta` version fields in the body, and
  `MCP-Protocol-Version` must match `_meta.io.modelcontextprotocol/protocolVersion`. Change
  supported versions with `UpdateGateway`.

Note that `list_tools_sync` **paginates** — loop on `pagination_token` or you will silently see
only the first page of a large tool inventory:

```python
tools, token, more = [], None, True
while more:
    page = client.list_tools_sync(pagination_token=token)
    tools.extend(page)
    token = page.pagination_token
    more = token is not None
```

---

## Rate Limits

Control how much traffic individual callers, targets, or tools consume. Define *dimension keys*
that group traffic into buckets, then *entries* giving each bucket a rate.

Use them to protect backends from spikes, enforce per-caller quotas from JWT claims or IAM
identity, **block a specific caller by setting a rate of zero**, cap tokens-per-minute on
inference targets, or limit concurrent connections.

| Component | Notes |
|---|---|
| `rateLimitId` | 2–64 chars. Appears in throttled responses and metrics — set it yourself so alarms are legible |
| `dimensionKeys` | 1–10 keys. **Immutable after creation** |
| `entries` | 1–1,000. Dimension values must match the number of keys |
| Rate value | 0–10,000,000. **0 blocks all matching traffic** |

| Limit | Value |
|---|---|
| Rate limits per gateway | 50 |
| Entries per rate limit | 1,000 |
| Dimension keys per rate limit | 10 |
| Propagation | ≤ 30 seconds |

All rate limits must pass for a request to proceed (**AND** logic), and a customer-defined
limit cannot exceed the service ceiling — the effective rate is the lower of the two.

Status moves `CREATING` → `ACTIVE`, with `UPDATING` keeping the previous configuration enforced
until the update lands, and `DELETING` stopping enforcement on completion.

> **Rate limits fail OPEN by default.** If the rate-limit service is unavailable or a dimension
> cannot be resolved, the request proceeds. They are a capacity and cost control, **not a
> security boundary** — never use a rate limit as the only thing stopping a caller. Set rate 0
> to block if you must, but put the real control in Cedar or IAM.

`ConflictException` on create usually means a rate limit with the same dimension keys already
exists — since keys are immutable, you delete and recreate rather than adjust.

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
