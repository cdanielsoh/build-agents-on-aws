# Onboarding Private Targets to AgentCore Gateway

How to put a Gateway target on a resource that lives inside a VPC: an OpenAPI/REST API,
or a self-hosted MCP server.

Everything below was **deployed and invoked end-to-end** in us-east-1 (aws-cdk-lib
2.268.0, FastMCP 4.0.2). Every error message quoted is one this deployment actually
produced. Two tool calls through the finished Gateway:

```
private-openapi___listPrivateOrders -> {"private": true, "reached_via":
  "AgentCore Gateway -> VPC Lattice -> execute-api VPCE -> API Gateway -> Lambda", ...}

private-mcp___whoami -> {"server": "self-hosted FastMCP on Fargate", "private": true,
  "task": "ip-10-70-3-116.ec2.internal", ...}
```

## Contents

1. [Which targets need this](#which-targets-need-this)
2. [The prerequisite that blocks most people](#the-prerequisite-that-blocks-most-people)
3. [routingDomain: read it, do not construct it](#routingdomain-read-it-do-not-construct-it)
4. [Worked example](#worked-example)
5. [Five things that will bite](#five-things-that-will-bite)
6. [Debugging a target that will not work](#debugging-a-target-that-will-not-work)
7. [Teardown](#teardown)

## Which targets need this

| Target type | Private connectivity |
|---|---|
| **Lambda** | **Not needed.** The Gateway invokes it through the Lambda API, so a Lambda with a `VpcConfig` reaches your VPC with no `privateEndpoint` at all |
| **AgentCore Runtime** | Not needed — traffic stays on the AWS backbone. Docs explicitly say *avoid* a VPCE URL here; it only adds a hop |
| **OpenAPI schema** | `privateEndpoint` + VPC Lattice |
| **Self-hosted MCP server** | `privateEndpoint` + VPC Lattice |
| **API Gateway (private REST)** | Not natively supported — export to OpenAPI and use an OpenAPI target |
| **Smithy** | Not supported. Open an AWS Support case |

`privateEndpoint` exists for targets the Gateway must reach *over the network*. If your
tool code can live in a Lambda, that is by far the shortest path and you can stop
reading here.

## The prerequisite that blocks most people

> VPC egress requires your target endpoint to have a **publicly trusted TLS
> certificate**.

Not a self-signed cert, not a private-CA cert. This is the first thing to solve, because
it constrains everything else. Three options:

### Option A — front it with a private API Gateway (no domain needed)

`https://<api-id>.execute-api.<region>.amazonaws.com` already carries an AWS-issued,
publicly trusted certificate. A **private** REST API is reachable only through an
`execute-api` VPC endpoint, so you get a private endpoint with a valid certificate and
**no domain, no ACM certificate, and no DNS work**.

This is the option to reach for first. It is what the worked example below uses.

```
Gateway --Lattice--> execute-api VPCE --> private REST API --> Lambda
                                                          --> VPC Link --> NLB --> Fargate
```

### Option B — internal ALB with a public ACM certificate

Needs a domain you own **and have delegated**. See the trap below; this is where an
afternoon disappears.

### Option C — internal ALB in front of a private-CA resource

Option B plus a listener host-header transform, so the ALB presents the public cert
while the backend keeps its private one. Documented under "Workaround for private
certificates".

> **The delegation trap.** A Route 53 hosted zone existing is *not* the same as the
> domain being delegated to it. If the registrar's NS records do not point at that zone,
> ACM writes the validation CNAME, no public resolver can see it, and the certificate
> sits in `PENDING_VALIDATION` until it expires in 72 hours. CloudFormation does not
> fail — it **hangs**, and `cdk deploy` looks like a slow deploy rather than a broken
> one.
>
> Check before you build:
> ```bash
> dig +short NS example.com @8.8.8.8    # empty => not delegated => Option B is closed
> ```
> To unstick a hung stack, delete the pending certificate in ACM. That fails the
> resource fast and lets the rollback proceed.

## routingDomain: read it, do not construct it

`routingDomain` names an intermediate hop (a VPC endpoint or internal load balancer)
that AgentCore builds the Lattice resource configuration against. It then sends the
request with your *target* domain as the TLS SNI — which is what makes the certificate
match.

The docs render the execute-api endpoint's name as
`<vpce-id>.execute-api.<region>.vpce.amazonaws.com`. **The real name has an extra
hash segment:**

```
vpce-07d225f0406007474-4vybazvk.execute-api.us-east-1.vpce.amazonaws.com
                      ^^^^^^^^^
```

String-building it from the endpoint ID gives you a domain that does not resolve. Read
the real one:

```bash
aws ec2 describe-vpc-endpoints --vpc-endpoint-ids "$VPCE" \
  --query "VpcEndpoints[0].DnsEntries[0].DnsName" --output text
```

`DnsEntries[0]` is the regional name; later entries are AZ-specific
(`...-us-east-1a...`), which you do not want. In CDK this means a two-phase deploy —
create the endpoint, read its DNS name, pass it in as context — which is better than
guessing, because a wrong `routingDomain` fails target creation minutes later.

## Worked example

### 1. The private endpoint block, shared by both targets

```python
private_endpoint = CfnGatewayTarget.PrivateEndpointProperty(
    managed_vpc_resource=CfnGatewayTarget.ManagedVpcResourceProperty(
        vpc_identifier=vpc_id,
        subnet_ids=private_subnet_ids,
        endpoint_ip_address_type="IPV4",
        security_group_ids=[resource_gateway_sg_id],
        routing_domain=execute_api_vpce_regional_dns_name,
    )
)
```

Managed mode needs **no VPC Lattice IAM permissions and no SCP changes** — AgentCore
uses Lattice as an internal dependency. Your principal needs
`iam:CreateServiceLinkedRole` for `bedrock-agentcore.amazonaws.com` plus
`ec2:CreateNetworkInterface`, `ec2:DescribeVpcs`, `ec2:DescribeSecurityGroups`,
`ec2:DescribeSubnets`.

AgentCore creates one resource gateway and **reuses it** across targets whose VPC,
subnets, security group and IP type match. Both targets in this test shared
`rgw-03e633d8e3c2ceab2`, and `GetGatewayTarget` reports it:

```json
"privateEndpointManagedResources": [
  {"domain": "vpce-...-4vybazvk.execute-api.us-east-1.vpce.amazonaws.com",
   "resourceGatewayArn": "arn:aws:vpc-lattice:...:resourcegateway/rgw-03e633d8e3c2ceab2"}
]
```

It appears in your account as a read-only `agentcore-*` resource gateway you cannot
modify. Resource *configurations* live in the AgentCore service account, so they will
not show up in your VPC Lattice console.

### 2. OpenAPI target

The `servers[].url` must be the host whose certificate you are relying on — the
`execute-api` URL, not the private hostname. `operationId` becomes the tool name.

```python
CfnGatewayTarget(
    self, "OpenApiTarget",
    gateway_identifier=gateway.attr_gateway_identifier,
    name="private-openapi",
    private_endpoint=private_endpoint,
    target_configuration=CfnGatewayTarget.TargetConfigurationProperty(
        mcp=CfnGatewayTarget.McpTargetConfigurationProperty(
            open_api_schema=CfnGatewayTarget.ApiSchemaConfigurationProperty(
                inline_payload=json.dumps({
                    "openapi": "3.0.1",
                    "info": {"title": "Private Orders API", "version": "1.0.0"},
                    "servers": [{"url": f"https://{api_id}.execute-api.{region}.amazonaws.com/prod"}],
                    "paths": {...},
                })
            )
        )
    ),
)
```

One `privateEndpoint` covers one domain. A schema with several server domains needs
`privateEndpointOverrides`, which is gated behind a support case.

### 3. Self-hosted MCP server target

```python
target_configuration=CfnGatewayTarget.TargetConfigurationProperty(
    mcp=CfnGatewayTarget.McpTargetConfigurationProperty(
        mcp_server=CfnGatewayTarget.McpServerTargetConfigurationProperty(
            endpoint=f"https://{api_id}.execute-api.{region}.amazonaws.com/prod/mcp",
            listing_mode="DYNAMIC",
        )
    )
)
```

Watch the nesting: `mcp_server` goes *inside* `McpTargetConfigurationProperty`. Passing
`McpServerTargetConfigurationProperty` straight to `mcp=` fails at synth with
`Wired struct has type '...McpServerTargetConfigurationProperty', which does not match
expected type`.

## Five things that will bite

### `listingMode=DYNAMIC` and `searchType=SEMANTIC` are mutually exclusive

```
Dynamic MCP targets (listingMode=DYNAMIC) are not supported on gateways with
semantic search enabled.
```

The alternative, `listingMode=DEFAULT` with an explicit `mcpToolSchema`, is only
supported when the credential provider uses an authorization-code grant. So for a
gateway fronting a self-hosted MCP server you choose **built-in semantic tool search or
dynamic tool discovery, not both**. Lambda and OpenAPI targets declare schemas up front
and are unaffected, so they can live on a `SEMANTIC` gateway.

`SynchronizeGatewayTargets` is also rejected for DYNAMIC targets — there is no cached
tool list to refresh.

### `tools/list` paginates, one page per target

This is the one most likely to look like a broken deployment. With a DYNAMIC MCP target,
a single unpaginated `tools/list` returned **only the OpenAPI tools**:

```
page 1: ['private-openapi___getPrivateOrder', 'private-openapi___listPrivateOrders']
        nextCursor='AQICAHifui3Nd7d+...'
page 2: ['private-mcp___whoami', 'private-mcp___add']
        nextCursor=None
```

The MCP tools were invokable by name the whole time — they simply were not on page one.
Loop on `nextCursor` (or `pagination_token` in the Strands client) or you will conclude
the target is broken when it is working.

### Cognito: send the ID token, not the access token

With `allowedAudience` set to the app client ID, the access token is rejected:

```
403 {"code": -32002, "message": "insufficient_scope - The request requires higher
     privileges than provided by the access token."}
```

Cognito access tokens carry `client_id`; only ID tokens carry `aud`. The ID token works.
If you must accept access tokens, the audience check needs to match `client_id` instead
— an interceptor is the place to do that.

Also note: a target with a `privateEndpoint` **cannot** use `NO_AUTH` inbound unless an
interceptor Lambda is configured. A real authorizer is mandatory.

### NLB + VPC Link: turn off PrivateLink SG enforcement

Only relevant if you front the MCP server with an NLB (REST API VPC Links front an
**NLB only** — not an ALB). API Gateway reaches that NLB over PrivateLink, and
PrivateLink traffic has no in-VPC source address you can write a security group rule
for. Leave enforcement at its default and every request fails:

```
API Gateway execution log: "There was an internal error while executing your request"
Method completed with status: 500
```

with the NLB target **healthy** and the container logs **empty**, because the connection
never lands. In CDK:

```python
elbv2.NetworkLoadBalancer(
    ..., security_groups=[nlb_sg],
    enforce_security_group_inbound_rules_on_private_link_traffic=False,
)
```

### MCP over API Gateway must not stream

API Gateway REST cannot stream, so an MCP server behind it has to answer with
`application/json` rather than `text/event-stream`. With FastMCP:

```python
mcp.run(transport="http", host="0.0.0.0", port=8000, path="/mcp",
        json_response=True, stateless_http=True)
```

`stateless_http=True` matters too — nothing in the Lattice/API Gateway path guarantees
connection pinning, so do not depend on session state. Verify locally before deploying:
a `POST /mcp` `initialize` should come back `content-type: application/json`.

If you need real streaming, keep API Gateway out of the path and use Option B with an
internal ALB.

## Debugging a target that will not work

Work outward from the target, in this order.

**1. Is the target `READY`?**

```bash
aws bedrock-agentcore-control get-gateway-target \
  --gateway-identifier "$GW" --target-id "$TID" \
  --query "{status:status,reasons:statusReasons,managed:privateEndpointManagedResources}"
```

`CREATING` can take a few minutes while Lattice is set up. `FAILED` puts the cause in
`statusReasons` — usually a missing `iam:CreateServiceLinkedRole` or a bad resource
configuration identifier.

**2. Does the tool call reach your endpoint at all?**

Turn on API Gateway execution logging and look for the outbound request:

```bash
aws apigateway update-stage --rest-api-id "$API" --stage-name prod \
  --patch-operations 'op=replace,path=/*/*/logging/loglevel,value=INFO' \
                     'op=replace,path=/*/*/logging/dataTrace,value=true'
```

Seeing `Endpoint request URI: ...` proves Gateway → Lattice → VPCE → API Gateway works
and narrows the fault to the last hop. That log is also how you confirm the Gateway is
speaking MCP correctly — you will see it send an `initialize` with
`"clientInfo":{"name":"genesis-gateway"}`.

**3. Read the error's own vocabulary.** The distinction is informative:

| Symptom | Means |
|---|---|
| `OpenAPIClientException ... status: 502` | Your backend returned a malformed response |
| `McpException - MCP invocation failed ... statusCode=500` | The hop *behind* API Gateway failed |
| Timeout with nothing logged anywhere | Network: security group, missing endpoint, or wrong `routingDomain` |
| `isError: true` with a TLS complaint | Certificate does not match the SNI (the target domain) |

A 502 with **clean Lambda logs** — `START`/`END`/`REPORT`, no exception — means the
response *shape* is wrong, not the code. API Gateway proxy integration wants
`{statusCode, headers, body, isBase64Encoded}`; an ALB additionally wants
`statusDescription`. Moving a Lambda from behind an ALB to behind API Gateway without
changing that shape produces exactly this.

## Teardown

Managed Lattice resource gateways are deleted by AgentCore when the last target using
them goes away — you do not delete them yourself, and you cannot. After destroying the
stacks, confirm nothing is left:

```bash
aws vpc-lattice list-resource-gateways --query "items[?starts_with(name,'agentcore-')]"
```

Do not forget the expensive parts of a test rig like this: a NAT gateway, an NLB, a
Fargate service, and VPC Lattice data processing charges.
