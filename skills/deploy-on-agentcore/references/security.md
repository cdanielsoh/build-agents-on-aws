# Security Patterns for AgentCore Infrastructure

## Table of Contents
1. [Multi-Layer Authorization](#multi-layer-authorization)
2. [Token Propagation Chain](#token-propagation-chain)

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
    USING (user_id = current_setting('app.current_user_id'));
```

Set the user context before queries:

```python
# In your Lambda handler
conn.execute("SET app.current_user_id = %s", [user_id_from_token])
result = conn.execute("SELECT * FROM user_orders")  # RLS filters automatically
```

### The Principle

Regardless of database technology, the principle is the same:

1. **Every query is scoped to the authenticated user** at the application level
2. **The database independently enforces** that queries cannot access other users' data
3. **Tokens are validated at every boundary** — not just once at the edge

## Token Propagation Chain

The OAuth token flows through the entire system, validated at each hop:

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
