# Security Patterns for AgentCore Infrastructure

## Table of Contents
1. [Multi-Layer Authorization](#multi-layer-authorization)
2. [Token Propagation Chain](#token-propagation-chain)
3. [Request Interceptor: The Only Bridge](#request-interceptor-the-only-bridge)
4. [Enforce the Model Path in IAM](#enforce-the-model-path-in-iam-not-just-config)

> Row-level security, the interceptor pattern, and the IAM model-path control here are
> distilled from a deployed, mutation-tested governance reference: inverting `SET LOCAL`,
> disabling signature verification, or letting a tool read its scope from tool arguments each
> break a test that exists to catch exactly that.

---

## Multi-Layer Authorization

Defense-in-depth means that even if one layer is compromised, the others prevent unauthorized access. For agent systems, this is critical because the agent is a potential attack surface for prompt injection.

### The Authorization Chain

```
Layer 1: AgentCore Runtime
  - Validates JWT via CUSTOM_JWT authorizer
  - Rejects requests with invalid/expired tokens

Layer 2: MCP Gateway
  - Validates JWT independently (same Cognito, separate check)
  - Interceptor extracts and forwards Authorization header

Layer 3: Application (MCP Lambda / API)
  - Extracts user identity from token
  - Scopes ALL queries to authenticated user
  - Never trusts agent-provided IDs without mapping

Layer 4: Database
  - IAM-level enforcement prevents cross-user access
  - Even if application code has a bug, DB rejects unauthorized queries
```

### DynamoDB: LeadingKeys Pattern (Recommended)

Use the user's identity (e.g., `cognito:sub`) as the partition key for user-scoped tables. Then enforce access via IAM conditions.

**Table design:**

```
Table: UserOrders
  Partition Key: user_id (cognito:sub)
  Sort Key: order_id

Table: UserTickets
  Partition Key: user_id (cognito:sub)
  Sort Key: ticket_id
```

**IAM policy (attached to Cognito Identity Pool authenticated role):**

```json
{
    "Effect": "Allow",
    "Action": [
        "dynamodb:GetItem",
        "dynamodb:Query",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem"
    ],
    "Resource": [
        "arn:aws:dynamodb:*:*:table/UserOrders",
        "arn:aws:dynamodb:*:*:table/UserTickets"
    ],
    "Condition": {
        "ForAllValues:StringEquals": {
            "dynamodb:LeadingKeys": [
                "${cognito-identity.amazonaws.com:sub}"
            ]
        }
    }
}
```

This ensures that even if the application code tries to query with a different user's ID, IAM blocks it.

**Requirements:**
- Cognito Identity Pool (not just User Pool) for IAM role mapping
- User-scoped partition key design
- Tables must use the user identifier as the leading (partition) key

### PostgreSQL: Row-Level Security

For RDS/Aurora PostgreSQL, use RLS policies:

```sql
-- Enable RLS on the table
ALTER TABLE user_orders ENABLE ROW LEVEL SECURITY;

-- Policy: users can only see their own rows
CREATE POLICY user_isolation ON user_orders
    USING (user_id = current_setting('app.current_user_id', true));
```

Passing `true` as the second argument to `current_setting` makes a missing GUC return NULL
instead of raising. Decide deliberately which you want: raising fails closed and is usually
right, but it turns a misconfiguration into a 500 rather than an empty result.

#### Setting the scope: use `SET LOCAL`, and parameterize it properly

```python
from contextlib import contextmanager

@contextmanager
def scoped_connection(scope, pool):
    """Yield a connection whose session is scoped, and cannot leak that scope."""
    conn = pool.getconn()
    try:
        # conn.transaction() — NOT `with conn:`. In psycopg 3 `with connection:`
        # CLOSES the connection on exit, which hands the next invocation a dead
        # one. transaction() commits on clean exit, rolls back on exception, and
        # leaves the connection open either way.
        with conn.transaction():
            with conn.cursor() as cur:
                for key, value in scope.to_guc().items():
                    # set_config(..., is_local=True) is the parameterizable form of
                    # SET LOCAL. `SET x = %s` cannot be parameterized at all.
                    cur.execute("SELECT set_config(%s, %s, true)", (key, value))
            yield conn
    finally:
        pool.putconn(conn)
```

Two failure modes this closes, both of which the obvious version has:

**`SET` instead of `SET LOCAL` leaks scope across users.** A plain `SET` persists for the
life of the *session*, and Lambda reuses both containers and pooled connections. The next
invocation on that connection inherits the previous caller's scope and RLS happily filters
to the wrong user. `SET LOCAL` is scoped to the transaction, so ending the transaction —
by commit *or* rollback — discards it. That is why the transaction boundary, not the
function boundary, is what makes this safe.

**`conn.execute("SET app.current_user_id = %s", [uid])` does not work.** `SET` takes a
literal, not a bind parameter; the driver cannot parameterize it. Code shaped like that
either errors or gets "fixed" into string interpolation — which is a SQL injection hole in
the one place you least want one. `set_config()` is a function call, so it parameterizes
normally.

**Fail closed when there is no scope.** A caller that reaches the database without a
verified scope is a bug, and unfiltered rows are the worst possible response to it:

```python
if scope is None:
    raise ScopeMissingError("scoped_connection requires a verified DataScope")
```

### The Principle

Regardless of database technology, the principle is the same:

1. **Every query is scoped to the authenticated user** at the application level
2. **The database independently enforces** that queries cannot access other users' data
3. **Tokens are validated at every boundary** — not just once at the edge

## Token Propagation Chain

> **Identity does not reach a Lambda tool target on its own.** When a Gateway invokes a
> Lambda, the **event** object is a map of the `properties` from the tool's `inputSchema` to
> their values, and the **context** object carries only
> `bedrockAgentCoreMessageVersion`, `AwsRequestId`, `McpMessageId`, `GatewayId`, `TargetId`,
> and `ToolName`. There is no JWT in either.
>
> Any design that assumes "the token just arrives" produces a tool that cannot tell users
> apart — **every caller gets every row**, and it looks like it works. A REQUEST interceptor
> with `passRequestHeaders: true` is the supported bridge, and it is load-bearing rather than
> optional. See [Request Interceptor](#request-interceptor-the-only-bridge) below.

With an interceptor in place, the token flows as follows, validated at each hop:

```
Client sends: Authorization: Bearer <JWT>
    |
    v
FastAPI Backend
    - Validates format (Bearer prefix)
    - Forwards to AgentCore: Authorization: Bearer <JWT>
    |
    v
AgentCore Runtime
    - CUSTOM_JWT authorizer validates signature + expiry
    - Token available in context.request_headers['Authorization']
    - Passed to MCP transport layer
    |
    v
MCP Gateway
    - CUSTOM_JWT authorizer validates independently
    - Interceptor Lambda extracts from mcp.gatewayRequest.headers
    - Forwards via transformedGatewayRequest.headers
    |
    v
Lambda MCP Target
    - Receives in context.client_context.custom.bedrockAgentCorePropagatedHeaders
    - Uses token for downstream API calls or extracts user ID for DB queries
    |
    v
Backend API / Database
    - Final validation (API Gateway authorizer or direct token decode)
    - User-scoped query execution
```

Each layer validates independently. If any layer is bypassed (e.g., someone calls a Lambda directly), the remaining layers still enforce authorization.

---

## Request Interceptor: The Only Bridge

Two mechanisms see the caller's identity at the Gateway, and they do different jobs:

| Mechanism | Sees identity? | Decides |
|---|---|---|
| **Cedar policy engine** | Yes — JWT claims via `principal.getTag()` | *Which tool* may be invoked |
| **REQUEST interceptor** (`passRequestHeaders: true`) | Yes — the `Authorization` header | *Which rows*, by injecting claims the tool receives |

**Cedar gates tools. RLS gates rows. The interceptor is the only bridge between them.**
Neither substitutes for the other — see [policy.md](policy.md).

### The three rules

```python
SCOPE_KEY = "_scope"

def lambda_handler(event, context):
    mcp = event.get("mcp") or {}
    gateway_request = mcp.get("gatewayRequest") or {}
    body = gateway_request.get("body") or {}

    # tools/list carries no arguments worth scoping; Cedar already filters the
    # visible tool set. Pass it through untouched.
    if body.get("method") != "tools/call":
        return _passthrough(body)

    try:
        claims = verifier.verify(_authorization_header(gateway_request))
        scope = DataScope.from_claims(claims)
    except (ClaimsError, GovernanceError) as exc:
        logger.warning("denying tools/call: %s", exc)      # never log the token
        return _short_circuit(body, "unauthorized")
    except KeyError as exc:
        logger.error("interceptor misconfigured: missing %s", exc)
        return _short_circuit(body, "interceptor misconfigured")

    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": {"body": _inject_scope(body, scope)}},
    }


def _inject_scope(body, scope):
    new_body = dict(body)                        # copy: the gateway may retry
    params = dict(new_body.get("params") or {})
    arguments = dict(params.get("arguments") or {})

    # RULE 1 in action — pop unconditionally, never merge.
    forged = arguments.pop(SCOPE_KEY, None)
    if forged is not None:
        logger.warning("discarded caller-supplied %s (injection attempt?)", SCOPE_KEY)

    arguments[SCOPE_KEY] = scope.to_wire()
    params["arguments"] = arguments
    new_body["params"] = params
    return new_body
```

**1. Strip before inject.** The model controls tool arguments, so it can emit its own
`_scope`. `pop` it unconditionally rather than merging — otherwise a prompt-injected model
widens its own data boundary and the whole chain above is decorative. This is the single
most important line in the interceptor.

**2. Verify, never decode.** Signature, issuer, audience, and expiry. A base64 decode of the
payload is *not* verification. (Decoding without verifying is acceptable in the agent
container, where the Runtime authorizer already validated the token — but not here, because
the interceptor is itself a trust boundary.)

**3. Fail closed.** Any problem short-circuits with a `transformedGatewayResponse` and the
target is never called. Return a generic message: distinguishing "no such tool" from "not
permitted" leaks the tool inventory.

### Interceptor implementation notes

- **Never log the event, the headers, or the token.** `passRequestHeaders: true` means live
  credentials are in the event object. Log only derived non-secret values — subject, team.
- **Be idempotent.** The Gateway may retry on failure or timeout, so keep the handler a pure
  function of its input. Copy the body rather than mutating it, or a retry behaves
  differently from the first attempt.
- **Accept multiple audiences.** Tokens may be minted by a browser SPA *or* a server-side
  BFF. Pinning one `aud` means a BFF-minted token that the Gateway already accepted gets
  rejected here — a fail-closed denial that presents as a broken tool rather than as a
  config mismatch. A list is not a loosening: `aud` must still match one entry exactly. An
  empty audience disables the check entirely, so treat that as misconfiguration and deny.
- **Cache JWKS at module level** so it survives warm invocations.

### Never pass the token as a tool argument

The tempting shortcut — have the agent pass the user's token as a tool parameter — puts a
live credential into the model's context and makes the boundary forgeable by the model.
Identity belongs in `agent.state` (local tools), the `Authorization` header (MCP tools), or
interceptor-injected `_scope` (Lambda targets). Never in a parameter the model can write.

---

## Enforce the Model Path in IAM, Not Just Config

If the agent is supposed to reach Bedrock through a gateway or proxy — for budget metering,
a model allowlist, chargeback — then **remove `bedrock:InvokeModel` from the runtime role**.

Routing through a proxy that the role could bypass is a convention. Routing through a proxy
the role *cannot* bypass is a control. The same reasoning applies to any egress you intend to
be mandatory: if IAM still permits the direct path, a prompt injection or a code change can
take it.
