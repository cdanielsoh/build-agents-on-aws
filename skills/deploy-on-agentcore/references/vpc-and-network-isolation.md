# VPC and Network Isolation

Everything here was verified against the `bedrock-agentcore-control` API model
(`apiVersion 2023-06-05`), `aws-cdk-lib` 2.268.0, and a synthesized six-stack app in
both created-VPC and BYO-VPC modes.

## Start here: "everything in a VPC" is not achievable, and the gap matters

Three AgentCore-adjacent components can hold ENIs in your subnets. Two cannot, and no
amount of configuration changes that.

| Component | In a VPC? | Mechanism |
|---|---|---|
| Agent runtime | **Yes** | `networkConfiguration.networkMode = "VPC"` + `networkModeConfig` |
| Lambda MCP target | **Yes** | ordinary Lambda `VpcConfig` |
| CodeBuild image build | **Yes** | ordinary CodeBuild `VpcConfig` |
| Browser / code interpreter | **Yes** | `BrowserNetworkMode` / `CodeInterpreterNetworkMode` also accept `VPC` |
| **Gateway** | **No** | `CreateGateway` has no network field at all |
| **Cognito** | **No** | managed regional endpoint |
| **Memory** | **No** | managed; reached over the `bedrock-agentcore` endpoint |

What you get for the second group is **private reachability**, not **private
placement**. The agent reaches them through interface endpoints, so traffic stays on
the AWS network and the VPC needs no internet route — but the Gateway remains a
regional URL whose protection is its JWT authorizer, Cedar policies, and optionally
WAF (`CfnGateway.waf_configuration`), not its network position.

Say this out loud early. A requirement phrased as "nothing is reachable from the
internet" is not satisfiable for the Gateway, and discovering that during a security
review is much worse than discussing it during design.

**The Gateway's VPC fields run the other direction.** `GatewayTarget.privateEndpoint`
and `CustomJWTAuthorizerConfiguration.privateEndpoint` each take a
`managedVpcResource` (AgentCore creates the VPC Lattice resource gateway for you) or a
`selfManagedLatticeResource`. Those let the *Gateway* reach a private target or a
private OIDC issuer inside your VPC. They do not make the Gateway private. A Lambda
target needs neither: the Gateway invokes it through the Lambda API, so a Lambda in
your VPC works with no Lattice configuration.

For the mechanics of putting a target on a private resource — the publicly-trusted-cert
prerequisite, `routingDomain`, and the traps — see
[gateway-private-targets.md](gateway-private-targets.md), which was verified by
deploying both target types end to end.

## Putting the runtime in a VPC

```python
network_configuration=bedrockagentcore.CfnRuntime.NetworkConfigurationProperty(
    network_mode="VPC",
    network_mode_config=bedrockagentcore.CfnRuntime.VpcConfigProperty(
        subnets=agent_subnet_ids,                       # 1-16
        security_groups=[agent_sg.security_group_id],   # 1-16
    ),
),
```

`NetworkMode` is `PUBLIC | VPC`. The API's `VpcConfig` also accepts
`requireServiceS3Endpoint`, which asserts the S3 gateway endpoint that ECR image pulls
depend on — **aws-cdk-lib 2.268.0 does not expose it**. Add it with an escape hatch if
you want the assertion:

```python
runtime.add_property_override(
    "NetworkConfiguration.NetworkModeConfig.RequireServiceS3Endpoint", True
)
```

`CfnRuntime` is an L1 and wants subnet ID *strings*. Resolve them once where you build
the network rather than passing `SubnetSelection` around — and note that
`vpc.select_subnets()` is keyword-only through jsii, so `select_subnets(selection)`
raises `TypeError: takes 1 positional argument but 2 were given`.

## Endpoints an isolated agent needs

With no internet route, every one of these must exist or the corresponding call hangs
until timeout:

| Endpoint | Why |
|---|---|
| `bedrock-agentcore` | Memory data plane (`CreateEvent`, `ListEvents`), Gateway MCP calls |
| `bedrock-agentcore-gateway` | the Gateway data path |
| `bedrock-runtime` | `InvokeModel` / `InvokeModelWithResponseStream` |
| `ecr.api` + `ecr.dkr` | pulling the agent image |
| **S3 (gateway endpoint)** | ECR image *layers* are served from S3 |
| `logs`, `xray`, `monitoring` | the ADOT sidecar's three destinations |
| `ssm` | Gateway URL read at container startup |
| `sts` | SigV4 / role credentials |

Use a **gateway** endpoint for S3, not an interface endpoint: it is free, where an
interface endpoint bills per-AZ-hour plus per-GB.

The failure mode is the important part. A missing endpoint does not fail the deploy —
it fails the agent at runtime, as a hang with nothing in the logs naming what was
unreachable. Assert the set in a test.

## Two CDK defaults that quietly undo the isolation

### `InterfaceVpcEndpoint(open=True)` opens endpoints to the whole VPC CIDR

CDK's default adds an ingress rule permitting the **entire VPC CIDR** to reach every
interface endpoint. If you carefully built per-source security groups, that rule
discards the work: anything with an ENI in the VPC can call Bedrock through your
endpoints.

```python
ec2.InterfaceVpcEndpoint(
    self, "Endpoint-bedrock-runtime",
    vpc=vpc, service=..., subnets=..., security_groups=[endpoint_sg],
    private_dns_enabled=True,
    open=False,          # then add ingress per source security group yourself
)
```

On an imported VPC the insecure default is not even reachable — resolving the CIDR
raises `Cannot perform this operation: 'vpcCidrBlock' was not supplied when creating
this VPC`, because `from_vpc_attributes` has no reason to know it. Worth knowing,
because the error names a CIDR you never asked for and sends you looking in the wrong
place.

`private_dns_enabled=True` matters just as much: without it the SDK resolves the
public hostname and the call leaves through a route that does not exist.

### `SecurityGroup(allow_all_outbound=True)` is the default

Every group you create is egress-to-anywhere until you say otherwise. Set
`allow_all_outbound=False` and add the rules you mean.

## `add_gateway_endpoint` fails on an imported VPC

Same root cause, different call. On a VPC from `from_vpc_attributes`,
`vpc.add_gateway_endpoint(...)` resolves subnets to route tables and wants the CIDR.
Name the route tables on the L1 instead — whoever supplied the VPC already had to tell
you what they are:

```python
ec2.CfnVPCEndpoint(
    self, "Endpoint-s3",
    vpc_id=vpc.vpc_id,
    service_name=f"com.amazonaws.{self.region}.s3",
    vpc_endpoint_type="Gateway",
    route_table_ids=route_table_ids,
)
```

## Designing the BYO-VPC contract

### Use `from_vpc_attributes`, not `from_lookup`

`from_lookup` makes synthesis depend on live AWS state and on the synthesizing
principal holding `ec2:Describe*`. The same source then produces different templates in
different hands, and `cdk synth` stops working in CI without VPC read access. Take
explicit context instead:

```json
{
  "vpc": {
    "id": "vpc-0123456789abcdef0",
    "availabilityZones": ["us-west-2a", "us-west-2b"],
    "privateSubnetIds": ["subnet-aaa", "subnet-bbb"],
    "privateSubnetRouteTableIds": ["rtb-aaa", "rtb-bbb"],
    "buildSubnetIds": ["subnet-ccc"],
    "createEndpoints": true
  }
}
```

### `-c` passes a string, `cdk.json` passes a dict

This breaks first, every time. A context object in `cdk.json`'s `"context"` block
arrives as a parsed dict; the same object passed as `-c vpc='{...}'` arrives as a
**string**, because `-c` only ever passes strings. Documenting the CLI form and then
calling `.get()` on the value gives whoever follows the docs
`AttributeError: 'str' object has no attribute 'get'`. Accept both:

```python
raw = self.node.try_get_context("vpc")
if isinstance(raw, str):
    raw = json.loads(raw)      # and catch JSONDecodeError with a message showing the quoting
```

### Do not mutate a VPC you do not own

Additive only: endpoints (with an opt-out) and security groups. No flow logs, no NAT,
no subnets, no route changes. Flow logs in particular may duplicate one that already
exists, and they are billed.

### Make `createEndpoints: false` a real mode

A VPC that centralizes endpoints elsewhere needs to opt out. When it does, output the
exact list of services that VPC must already reach, so a missing one is a documented
handoff instead of a runtime hang.

### Validate the contract with messages that say what to fix

Every required key, checked at synth, named in the error, with the expected shape
shown. `privateSubnetRouteTableIds` is conditionally required — only when you are
creating the S3 gateway endpoint — and the error should say so along with the
`createEndpoints: false` alternative.

## The build needs egress; nothing else does

If the agent Dockerfile runs `pip install`, the build reaches PyPI and a fully isolated
VPC cannot complete it. The runtime container does not — its dependencies are baked
into the image — so the two have genuinely different requirements. Four options:

| Option | Trade |
|---|---|
| NAT gateway on a build-only subnet | Everything in the VPC. ~$32/mo per AZ plus data processing, and one egress path exists |
| Keep CodeBuild outside the VPC | No NAT, no egress hole in the data path. The build touches no application data |
| CodeArtifact with a PyPI upstream | Fully private and auditable supply chain. Most moving parts |
| Vendor wheels into the source asset | No egress, no extra service; you now own dependency updates |

Whichever you choose, keep the egress scoped to the build path and assert that in a
test — one internet egress rule, on the build's security group, and a failure if a
second appears.

## Test the posture, and mutation-test the tests

These assertions are what a security review asks for, and they are only worth having if
they fail when the posture regresses. Check each one by breaking the thing it guards:

- runtime `NetworkMode` is `VPC`, not `PUBLIC`
- Lambda and CodeBuild have a `VpcConfig`
- exactly one `0.0.0.0/0` egress rule, on the build group
- no group has allow-all egress
- endpoint ingress comes from security groups, never a CIDR
- every required endpoint present, all with private DNS
- `bedrock:InvokeModel` is not on `"*"`
- BYO: no VPC/NAT/flow log created, the supplied subnets reach the runtime, each
  malformed context fails with a message naming the problem

One gotcha in writing them: **CDK puts security group rules in two places.** Inline in
the group's `SecurityGroupEgress` array when it can, and as standalone
`AWS::EC2::SecurityGroupEgress` / `Ingress` resources when an inline rule would create a
circular dependency — typically any SG-to-SG reference. A test that reads only one of
the two passes while seeing nothing. Read both:

```python
def egress_rules(template):
    rules = []
    for sg in template.find_resources("AWS::EC2::SecurityGroup").values():
        rules += sg["Properties"].get("SecurityGroupEgress", [])
    for r in template.find_resources("AWS::EC2::SecurityGroupEgress").values():
        rules.append(r["Properties"])
    return rules
```

## Network isolation is not authorization

Worth stating in any document that describes the isolation. A VPC controls *where
traffic can go*.
It says nothing about *which caller may invoke which tool*, or *whose rows a tool
returns*. An agent in a perfectly isolated VPC with no interceptor and no Cedar policy
engine still lets any holder of a valid JWT call every tool, and still gives every
caller the same data.

The CUSTOM_JWT authorizers on the Runtime and Gateway authenticate. Nothing in the
network layer authorizes. See [security.md](security.md) for the REQUEST interceptor
that bridges verified identity to a target, and [policy.md](policy.md) for Cedar. If you
ship network isolation without them, document it as a known gap rather than leaving it
to be discovered.
