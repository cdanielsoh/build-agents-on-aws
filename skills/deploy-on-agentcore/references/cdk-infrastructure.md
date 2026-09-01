# CDK Infrastructure for AgentCore Agents

## Table of Contents
1. [Authentication (Cognito)](#authentication-cognito)
2. [Agent Runtime Stack](#agent-runtime-stack)
3. [MCP Gateway Stack](#mcp-gateway-stack)
4. [Backend Stack](#backend-stack)
5. [Stack Dependencies and Wiring](#stack-dependencies-and-wiring)

---

## Authentication (Cognito)

AgentCore Runtime and Gateway both require a `CUSTOM_JWT` authorizer. If you don't have an existing OIDC provider, create a minimal Cognito User Pool:

```python
from aws_cdk import (
    Stack, CfnOutput,
    aws_cognito as cognito,
)
from constructs import Construct

class AuthStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # User Pool
        user_pool = cognito.UserPool(
            self, "AgentUserPool",
            user_pool_name="agent-user-pool",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
        )

        # App Client (used as allowed_audience in authorizers)
        client = user_pool.add_client(
            "AgentAppClient",
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True,
            ),
            generate_secret=False,
        )

        # Both Runtime and Gateway reference these values
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id, export_name="UserPoolId")
        CfnOutput(self, "UserPoolClientId", value=client.user_pool_client_id, export_name="UserPoolClientId")

        self.user_pool = user_pool
        self.client = client
```

Both the Runtime and Gateway stacks use the same Cognito User Pool. The `discovery_url` is constructed from the User Pool ID:

```
https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration
```

And the `allowed_audience` is the App Client ID.

---

## Agent Runtime Stack

This stack creates the AgentCore Runtime resource — the serverless container that runs your agent.

### Components

1. **ECR Repository** — Docker image storage for the agent container
2. **IAM Role** — Permissions for the runtime (Bedrock, MCP, S3, SSM, CloudWatch, Memory)
3. **CfnMemory** — AgentCore Memory for conversation persistence
4. **CfnRuntime** — The runtime resource itself with OAuth authorizer

### Implementation

```python
from aws_cdk import (
    Stack, CfnOutput, RemovalPolicy,
    aws_ecr as ecr, aws_iam as iam, aws_logs as logs,
    aws_bedrockagentcore as bedrockagentcore,
)
from constructs import Construct

class AgentRuntimeStack(Stack):
    def __init__(self, scope, construct_id, cognito_user_pool_id=None, cognito_client_id=None, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # ========== ECR Repository ==========
        agent_repository = ecr.Repository.from_repository_name(
            self, "AgentRepository", repository_name="my-agent-runtime"
        )

        # ========== IAM Role ==========
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

        # SSM Parameter Store (for reading MCP endpoints and config at startup)
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

        # CloudWatch Logs
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:DescribeLogStreams"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock*"],
        ))
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock*:log-stream:*"],
        ))

        # ========== AgentCore Memory ==========
        stack_name_clean = Stack.of(self).stack_name.replace("-", "_")

        memory = bedrockagentcore.CfnMemory(
            self, "AgentMemory",
            name=f"{stack_name_clean}_memory",
            event_expiry_duration=90,  # Days
            memory_strategies=[
                bedrockagentcore.CfnMemory.MemoryStrategyProperty(
                    user_preference_memory_strategy=bedrockagentcore.CfnMemory.UserPreferenceMemoryStrategyProperty(
                        name="user_preferences",
                        namespaces=["/users/{actorId}/preferences"],
                        description="User preferences and communication style"
                    )
                ),
                bedrockagentcore.CfnMemory.MemoryStrategyProperty(
                    semantic_memory_strategy=bedrockagentcore.CfnMemory.SemanticMemoryStrategyProperty(
                        name="user_facts",
                        namespaces=["/users/{actorId}/facts"],
                        description="User history and extracted facts"
                    )
                ),
                bedrockagentcore.CfnMemory.MemoryStrategyProperty(
                    summary_memory_strategy=bedrockagentcore.CfnMemory.SummaryMemoryStrategyProperty(
                        name="conversation_summaries",
                        namespaces=["/summaries/{actorId}/{sessionId}"],
                        description="Session-specific conversation summaries"
                    )
                ),
            ]
        )

        # ========== AgentCore Runtime ==========
        runtime_environment = {
            "MCP_SSM_PREFIX": "/mcp/endpoints/agentcore",
            "MCP_SERVERS": "user-data,operations",  # Comma-separated server names
            "LLM_MODEL_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
            "AWS_REGION": self.region,
            "AGENTCORE_MEMORY_ID": memory.attr_memory_id,
            "AGENTCORE_MEMORY_ENABLED": "true",
        }

        # NEVER a mutable tag here — see "Image tags must be content-addressed" below.
        # Pass the tag in from a build step that hashes what actually goes in the image.
        image_tag = self.node.try_get_context("image_tag")
        if not image_tag or image_tag == "latest":
            raise ValueError(
                "image_tag context value is required and must not be 'latest'. "
                "Pass -c image_tag=$(./scripts/image_tag.sh)."
            )

        runtime_config = {
            "agent_runtime_name": "my_agent_runtime",
            "agent_runtime_artifact": bedrockagentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=bedrockagentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=f"{agent_repository.repository_uri}:{image_tag}"
                )
            ),
            "network_configuration": bedrockagentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC"
            ),
            "role_arn": runtime_role.role_arn,
            "environment_variables": runtime_environment,
        }

        # OAuth authorizer (requires Cognito)
        if cognito_user_pool_id and cognito_client_id:
            discovery_url = (
                f"https://cognito-idp.{self.region}.amazonaws.com"
                f"/{cognito_user_pool_id}/.well-known/openid-configuration"
            )
            runtime_config["authorizer_configuration"] = (
                bedrockagentcore.CfnRuntime.AuthorizerConfigurationProperty(
                    custom_jwt_authorizer=bedrockagentcore.CfnRuntime.CustomJWTAuthorizerConfigurationProperty(
                        discovery_url=discovery_url,
                        allowed_audience=[cognito_client_id],
                    )
                )
            )

        # Allow Authorization header to pass through to the container
        runtime_config["request_header_configuration"] = (
            bedrockagentcore.CfnRuntime.RequestHeaderConfigurationProperty(
                request_header_allowlist=["Authorization"]
            )
        )

        runtime = bedrockagentcore.CfnRuntime(self, "AgentRuntime", **runtime_config)
        runtime.node.add_dependency(memory)

        # ========== Outputs ==========
        CfnOutput(self, "RuntimeArn", value=runtime.attr_agent_runtime_arn, export_name="AgentRuntimeArn")
        CfnOutput(self, "RuntimeId", value=runtime.attr_agent_runtime_id, export_name="AgentRuntimeId")
        CfnOutput(self, "MemoryId", value=memory.attr_memory_id, export_name="AgentMemoryId")
```

### Key Configuration Notes

**`request_header_configuration`**: The `Authorization` header must be explicitly allowlisted. Without this, the Runtime strips it before reaching your container. The session ID header (`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`) is built-in and does NOT need to be allowlisted.

**Environment variables**: The Runtime passes these to the container. Use SSM parameter names (not values) so MCP servers can be redeployed without redeploying the Runtime.

**Memory dependency**: The CfnMemory resource must be created before CfnRuntime, hence the explicit `add_dependency`.

---

## MCP Gateway Stack

This stack creates the Gateway that routes tool calls to Lambda MCP servers.

### Components

1. **Gateway execution IAM role** — Permission to invoke Lambda targets
2. **Shared Lambda Layer** — Common dependencies (fastmcp, requests, etc.)
3. **Lambda MCP functions** — One per domain (user-data, operations, catalog, etc.)
4. **Interceptor Lambda** — Extracts and forwards Authorization header
5. **CfnGateway** — The Gateway resource with OAuth authorizer
6. **CfnGatewayTarget** — One per Lambda, with tool schema definitions
7. **SSM Parameter** — Stores Gateway URL for agent discovery

### Implementation

```python
from aws_cdk import (
    Stack, CfnOutput, Duration, Fn, BundlingOptions, DockerImage, RemovalPolicy,
    aws_ssm as ssm, aws_lambda as lambda_, aws_iam as iam, aws_logs as logs,
    aws_bedrockagentcore as bedrockagentcore,
)
from constructs import Construct

class MCPGatewayStack(Stack):
    def __init__(self, scope, construct_id, api_url=None, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # ========== Gateway Execution Role ==========
        gateway_role = iam.Role(
            self, "GatewayExecutionRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:gateway/*"
                    }
                }
            ),
        )

        # Permission to invoke all MCP Lambda functions + interceptor
        gateway_role.add_to_policy(iam.PolicyStatement(
            actions=["lambda:InvokeFunction"],
            resources=[f"arn:aws:lambda:{self.region}:{self.account}:function:*-mcp-lambda",
                       f"arn:aws:lambda:{self.region}:{self.account}:function:mcp-gateway-interceptor"],
        ))

        # ========== Shared Lambda Layer ==========
        dependencies_layer = lambda_.LayerVersion(
            self, "MCPDependenciesLayer",
            layer_version_name="mcp-gateway-dependencies",
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            code=lambda_.Code.from_asset(
                ".",
                bundling=BundlingOptions(
                    image=DockerImage.from_registry("public.ecr.aws/lambda/python:3.12"),
                    command=["bash", "-c",
                             "pip install -r layer-requirements.txt -t /asset-output/python --quiet"],
                )
            ),
        )

        # ========== Lambda MCP Functions ==========
        # Create one Lambda per MCP server domain
        user_data_lambda = self._create_mcp_lambda(
            "user-data", "./mcp-servers/user-data",
            api_url, dependencies_layer, gateway_role.role_arn
        )
        operations_lambda = self._create_mcp_lambda(
            "operations", "./mcp-servers/operations",
            api_url, dependencies_layer, gateway_role.role_arn
        )

        # ========== Interceptor Lambda ==========
        interceptor_lambda = lambda_.Function(
            self, "GatewayInterceptor",
            function_name="mcp-gateway-interceptor",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("interceptor"),
            timeout=Duration.seconds(10),
            memory_size=256,
        )
        interceptor_lambda.grant_invoke(iam.ArnPrincipal(gateway_role.role_arn))

        # ========== AgentCore Gateway ==========
        cognito_user_pool_id = Fn.import_value("UserPoolId")
        cognito_client_id = Fn.import_value("UserPoolClientId")

        discovery_url = Fn.sub(
            "https://cognito-idp.${AWS::Region}.amazonaws.com/${UserPoolId}/.well-known/openid-configuration",
            {"UserPoolId": cognito_user_pool_id}
        )

        gateway = bedrockagentcore.CfnGateway(
            self, "MCPGateway",
            authorizer_type="CUSTOM_JWT",
            name="agent-mcp-gateway",
            protocol_type="MCP",
            role_arn=gateway_role.role_arn,
            authorizer_configuration=bedrockagentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=bedrockagentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(
                    discovery_url=discovery_url,
                    allowed_audience=[cognito_client_id],
                )
            ),
            protocol_configuration=bedrockagentcore.CfnGateway.GatewayProtocolConfigurationProperty(
                mcp=bedrockagentcore.CfnGateway.MCPGatewayConfigurationProperty(
                    search_type="SEMANTIC"
                )
            ),
            interceptor_configurations=[
                bedrockagentcore.CfnGateway.GatewayInterceptorConfigurationProperty(
                    interception_points=["REQUEST"],
                    interceptor=bedrockagentcore.CfnGateway.InterceptorConfigurationProperty(
                        lambda_=bedrockagentcore.CfnGateway.LambdaInterceptorConfigurationProperty(
                            arn=interceptor_lambda.function_arn
                        )
                    ),
                    input_configuration=bedrockagentcore.CfnGateway.InterceptorInputConfigurationProperty(
                        pass_request_headers=True
                    ),
                )
            ],
        )

        # ========== Gateway Targets ==========
        self._create_target(gateway, "user-data", user_data_lambda, self._user_data_tools())
        self._create_target(gateway, "operations", operations_lambda, self._operations_tools())

        # ========== SSM Parameter ==========
        ssm.StringParameter(
            self, "GatewayUrlParameter",
            parameter_name="/mcp/endpoints/gateway/unified",
            string_value=gateway.attr_gateway_url,
        )

        CfnOutput(self, "GatewayUrl", value=gateway.attr_gateway_url)

    def _create_mcp_lambda(self, name, code_path, api_url, layer, gateway_role_arn):
        """Create a Lambda function for an MCP server."""
        fn = lambda_.Function(
            self, f"{name}-lambda",
            function_name=f"{name}-mcp-lambda",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(code_path),
            timeout=Duration.seconds(30),
            memory_size=512,
            layers=[layer],
            environment={"API_URL": api_url or "", "LOG_LEVEL": "INFO"},
        )
        fn.grant_invoke(iam.ArnPrincipal(gateway_role_arn))
        return fn

    def _create_target(self, gateway, name, lambda_fn, tool_definitions):
        """Create a Gateway target for a Lambda MCP server."""
        target = bedrockagentcore.CfnGatewayTarget(
            self, f"{name}-target",
            gateway_identifier=gateway.attr_gateway_identifier,
            name=f"{name}-mcp-target",
            target_configuration=bedrockagentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=bedrockagentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=bedrockagentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=lambda_fn.function_arn,
                        tool_schema=bedrockagentcore.CfnGatewayTarget.ToolSchemaProperty(
                            inline_payload=tool_definitions
                        )
                    )
                )
            ),
            credential_provider_configurations=[
                bedrockagentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE"
                )
            ],
        )
        target.add_dependency(gateway)
        return target

    def _user_data_tools(self):
        """Define tool schemas for user-data target."""
        TD = bedrockagentcore.CfnGatewayTarget.ToolDefinitionProperty
        SD = bedrockagentcore.CfnGatewayTarget.SchemaDefinitionProperty

        return [
            TD(
                name="get_profile",
                description="Get the authenticated user's profile",
                input_schema=SD(type="object", properties={})
            ),
            TD(
                name="list_items",
                description="List user's items",
                input_schema=SD(
                    type="object",
                    properties={
                        "limit": SD(type="number", description="Max results (default: 10)")
                    }
                )
            ),
            TD(
                name="get_details",
                description="Get details for a specific item",
                input_schema=SD(
                    type="object",
                    properties={
                        "id": SD(type="string", description="Item ID")
                    },
                    required=["id"]
                )
            ),
        ]

    def _operations_tools(self):
        """Define tool schemas for operations target."""
        TD = bedrockagentcore.CfnGatewayTarget.ToolDefinitionProperty
        SD = bedrockagentcore.CfnGatewayTarget.SchemaDefinitionProperty

        return [
            TD(
                name="process_action",
                description="Process an action on an item",
                input_schema=SD(
                    type="object",
                    properties={
                        "id": SD(type="string", description="Item ID"),
                        "action": SD(type="string", description="Action type"),
                        "reason": SD(type="string", description="Reason for action"),
                    },
                    required=["id", "action", "reason"]
                )
            ),
            TD(
                name="create_ticket",
                description="Create a support ticket",
                input_schema=SD(
                    type="object",
                    properties={
                        "subject": SD(type="string", description="Subject"),
                        "description": SD(type="string", description="Description"),
                        "priority": SD(type="string", description="Priority: low, medium, high"),
                    },
                    required=["subject", "description"]
                )
            ),
        ]
```

### Tool Schema Pattern

Tool schemas are declared in CDK using `ToolDefinitionProperty` with typed `SchemaDefinitionProperty` inputs. The Gateway uses these schemas for semantic routing — it matches the agent's tool call to the right target based on tool name and description.

```python
bedrockagentcore.CfnGatewayTarget.ToolDefinitionProperty(
    name="tool_name",
    description="What this tool does",
    input_schema=bedrockagentcore.CfnGatewayTarget.SchemaDefinitionProperty(
        type="object",
        properties={
            "param_name": bedrockagentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                type="string",
                description="Parameter description"
            )
        },
        required=["param_name"]  # Optional: list of required params
    )
)
```

---

## Backend Stack

The Backend Stack deploys a FastAPI proxy on ECS Fargate behind an ALB. This sits between the frontend and AgentCore Runtime.

```python
from aws_cdk import (
    Stack, CfnOutput, Duration,
    aws_ec2 as ec2, aws_ecs as ecs, aws_ecr as ecr,
    aws_iam as iam, aws_elasticloadbalancingv2 as elbv2,
    aws_logs as logs, RemovalPolicy,
)
from constructs import Construct

class BackendStack(Stack):
    def __init__(self, scope, construct_id, agent_runtime_arn=None, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # VPC
        vpc = ec2.Vpc(self, "BackendVpc", max_azs=2, nat_gateways=0)

        # ECR repository (pre-created)
        ecr_repo = ecr.Repository.from_repository_name(
            self, "BackendRepo", repository_name="agent-fastapi-backend"
        )

        # ECS Cluster
        cluster = ecs.Cluster(self, "BackendCluster", vpc=vpc)

        # IAM Task Role — key permission: invoke AgentCore Runtime
        task_role = iam.Role(
            self, "BackendTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        task_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:GetAgentRuntime"],
            resources=[f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/*"],
        ))

        # Task Definition
        task_def = ecs.FargateTaskDefinition(
            self, "BackendTaskDef",
            memory_limit_mib=512, cpu=256,
            task_role=task_role,
        )

        container = task_def.add_container(
            "BackendContainer",
            image=ecs.ContainerImage.from_ecr_repository(ecr_repo, tag="latest"),
            environment={
                "AGENT_RUNTIME_ARN": agent_runtime_arn or "NOT_CONFIGURED",
                "AWS_REGION": self.region,
                "PORT": "8000",
            },
            logging=ecs.LogDrivers.aws_logs(stream_prefix="backend"),
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8000))

        # ALB
        alb = elbv2.ApplicationLoadBalancer(self, "BackendALB", vpc=vpc, internet_facing=True)
        listener = alb.add_listener("HttpListener", port=80)

        # Fargate Service
        service = ecs.FargateService(
            self, "BackendService",
            cluster=cluster, task_definition=task_def,
            desired_count=1, assign_public_ip=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        listener.add_targets(
            "BackendTarget", port=8000, targets=[service],
            health_check=elbv2.HealthCheck(path="/health", interval=Duration.seconds(30)),
        )

        CfnOutput(self, "BackendUrl", value=f"http://{alb.load_balancer_dns_name}")
```

The critical IAM permission is `bedrock-agentcore:InvokeAgentRuntime` — without it, the backend cannot call the AgentCore REST API.

---

## Stack Dependencies and Wiring

### Deployment Order

```
1. AuthStack (Cognito)
   ↓ exports: UserPoolId, UserPoolClientId
2. MCPGatewayStack (Gateway + Lambda targets)
   ↓ writes: SSM /mcp/endpoints/gateway/unified
3. AgentRuntimeStack (Agent container + Memory)
   ↓ exports: AgentRuntimeArn
4. BackendStack (ECS Fargate + ALB)
```

### Cross-Stack References

| From | To | Via |
|------|----|-----|
| MCPGatewayStack | AuthStack | `Fn.import_value("UserPoolId")`, `Fn.import_value("UserPoolClientId")` |
| AgentRuntimeStack | AuthStack | Constructor params: `cognito_user_pool_id`, `cognito_client_id` |
| AgentRuntimeStack | MCPGatewayStack | SSM parameter read at container startup (not CDK synthesis time) |
| BackendStack | AgentRuntimeStack | Constructor param: `agent_runtime_arn` |

### CDK App Entry Point

```python
import aws_cdk as cdk
import boto3, os

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("AWS_REGION", "us-west-2"),
)

# Read Cognito outputs from deployed AuthStack
cfn = boto3.client("cloudformation")
auth_outputs = {}
try:
    stack = cfn.describe_stacks(StackName="AuthStack")
    auth_outputs = {o["OutputKey"]: o["OutputValue"] for o in stack["Stacks"][0].get("Outputs", [])}
except Exception:
    pass  # Will use SigV4 instead of OAuth

AgentRuntimeStack(
    app, "AgentRuntimeStack",
    cognito_user_pool_id=auth_outputs.get("UserPoolId"),
    cognito_client_id=auth_outputs.get("UserPoolClientId"),
    env=env,
)

app.synth()
```

### SSM for Dynamic Config

The agent container reads MCP endpoint URLs from SSM at startup, not at CDK synthesis time. This decouples deployment — MCP servers can be redeployed and their endpoints updated in SSM without redeploying the agent runtime.

```python
# In MCPGatewayStack: write the URL
ssm.StringParameter(self, "GatewayUrl",
    parameter_name="/mcp/endpoints/gateway/unified",
    string_value=gateway.attr_gateway_url,
)

# In AgentRuntimeStack: grant read access (agent reads at startup)
runtime_role.add_to_policy(iam.PolicyStatement(
    actions=["ssm:GetParameter"],
    resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/mcp/endpoints/*"],
))
```

---

## Image Tags Must Be Content-Addressed

`:latest` — or any fixed tag — makes a deploy that changes nothing while reporting success.

The failure is worth understanding precisely, because every individual step looks fine:

1. The stack builds the container URI as `{repo}:{fixed-tag}`.
2. With the tag fixed, that string is **byte-identical on every deploy**.
3. CloudFormation diffs the template, finds no change, and reports `(no changes)`.
4. Your freshly built images sit in ECR, unadopted.
5. **AgentCore pins a runtime to the image digest it resolved at create time**, so
   re-pushing the same tag does not roll it either.

The result is a green build, a green deploy, and a live runtime still serving last week's
code. Nothing short of a template change will move it.

### The fix: hash what goes into the image

```bash
#!/usr/bin/env bash
# Print a content-addressed image tag for the paths this Dockerfile copies.
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-agent}" in
  agent) PATHS=(agent packages) ;;
  bff)   PATHS=(services/bff packages) ;;
  *) echo "usage: $0 [agent|bff]" >&2; exit 2 ;;
esac

{
  # Tracked + staged + untracked-but-not-ignored, deduplicated and sorted so the
  # order cannot vary across machines or git versions.
  { git ls-files -- "${PATHS[@]}"
    git ls-files --others --exclude-standard -- "${PATHS[@]}"; } | sort -u |
  while IFS= read -r file; do
    [ -f "$file" ] || continue     # in the index but deleted from the worktree
    printf '%s\n' "$file"          # hash the path too, so a rename moves the tag
    cat -- "$file"
  done
} | shasum -a 256 | cut -c1-12
```

```bash
cdk deploy -c image_tag="$(./scripts/image_tag.sh)"
```

Three properties that matter, each learned by getting it wrong:

**Hash file *content*, not git metadata.** Mixing `git rev-parse HEAD:<path>` (a tree object)
with `git diff` output (patch text) makes the tag depend on the *shape of history* rather than
on the bytes going into the image. Building with uncommitted edits produces one tag; committing
those same edits produces another — identical content, two tags. It surfaces right after a
merge, when the deployed image holds exactly `main`'s code and the staleness check still fires.
A staleness check that fires on a no-op trains you to ignore it, which is how the `:latest` bug
survives in the first place.

**Scope the hash per image.** Hashing all of `services/` for two images couples them: editing
the BFF changes the agent's tag and demands a rebuild of something it cannot possibly affect.
Hash exactly the subtrees that image's Dockerfile copies; shared packages count for both.

**Include the file list, not just the bytes.** A rename then moves the tag even when content is
unchanged, and a deletion moves it because the list shrinks.

### Assert it in a deployment test

Two checks worth having, because this class of bug is invisible at deploy time:

- No runtime is on a mutable tag.
- Every pinned image digest actually exists in ECR.
