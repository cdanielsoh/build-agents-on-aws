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
        #
        # logs:DescribeLogGroups MUST be on log-group:* — not a narrower ARN. Scope it
        # down and no [runtime-logs] streams are created at all: the agent runs fine and
        # produces no logs, with nothing anywhere indicating why. This is the single
        # most expensive silent failure in an AgentCore deployment.
        # See references/observability.md.
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:DescribeLogGroups"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:*"],
        ))
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:DescribeLogStreams"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock*"],
        ))
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock*:log-stream:*"],
        ))

        # X-Ray + metrics, for traces and the observability dashboard.
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "xray:PutTraceSegments", "xray:PutTelemetryRecords",
                "xray:GetSamplingRules", "xray:GetSamplingTargets",
            ],
            resources=["*"],
        ))
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["cloudwatch:PutMetricData"],
            resources=["*"],
        ))

        # ========== AgentCore Memory ==========
        # The replace() is not cosmetic. Memory and Runtime names must match
        # [a-zA-Z][a-zA-Z0-9_]{0,47} — hyphens are rejected — while Gateway and target
        # names must match ([0-9a-zA-Z][-]?){1,100}, which rejects underscores. One
        # project name therefore needs two spellings. CDK does not validate either, so
        # a wrong one synthesizes fine and fails minutes into the deploy. See
        # references/naming.md for the full table.
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
        # Take it as a constructor argument, derived from the hash of the staged
        # source, and assert only that it is not mutable:
        if not image_tag or image_tag == "latest":
            raise ValueError(f"refusing to deploy a mutable image tag: {image_tag!r}")

        # Do NOT reach for context (`self.node.try_get_context("image_tag")`) and
        # raise when it is absent. `cdk deploy <any-stack>` synthesizes the WHOLE app,
        # so a synth-time raise in one stack blocks deploying every other stack in it
        # — including the ECR stack you have to deploy first to have anywhere to push.
        # That deadlocks a first deploy, and the traceback points at the runtime stack
        # while the command you ran named a different one.
        #
        # The same root cause has a nastier second form. If context decides whether a
        # stack is *created at all* —
        #
        #     if app.node.try_get_context("routing_domain"):
        #         GatewayStack(app, "gateway", ...)
        #
        # — then any later `cdk deploy` that omits the flag synthesizes an app with no
        # gateway stack, concludes the exports it consumed are now unused, and tries to
        # remove them:
        #
        #     Cannot delete export my-network:ExportsOutput...ResourceGatewaySg... as it
        #     is in use by my-gateway
        #
        # which rolls the *network* stack back over a change you made somewhere else.
        # If you must gate a stack on context, pass that context on every invocation.

        runtime_config = {
            "agent_runtime_name": "my_agent_runtime",
            "agent_runtime_artifact": bedrockagentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=bedrockagentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=f"{agent_repository.repository_uri}:{image_tag}"
                )
            ),
            # PUBLIC is the simple case. For network isolation use network_mode="VPC"
            # with network_mode_config — and read
            # references/vpc-and-network-isolation.md first, because two components in
            # this file cannot be placed in a VPC at all.
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

        # Depend on the ROLE CONSTRUCT, not just role_arn. Omitting this is a hard
        # deploy failure with a misleading error — see "The IAM DefaultPolicy race"
        # below.
        runtime.node.add_dependency(runtime_role)

        # ========== Outputs ==========
        CfnOutput(self, "RuntimeArn", value=runtime.attr_agent_runtime_arn, export_name="AgentRuntimeArn")
        CfnOutput(self, "RuntimeId", value=runtime.attr_agent_runtime_id, export_name="AgentRuntimeId")
        CfnOutput(self, "MemoryId", value=memory.attr_memory_id, export_name="AgentMemoryId")
```

### Key Configuration Notes

**`request_header_configuration`**: The `Authorization` header must be explicitly allowlisted. Without this, the Runtime strips it before reaching your container. The session ID header (`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`) is built-in and does NOT need to be allowlisted.

**Environment variables**: The Runtime passes these to the container. Use SSM parameter names (not values) so MCP servers can be redeployed without redeploying the Runtime.

**Memory dependency**: The CfnMemory resource must be created before CfnRuntime, hence the explicit `add_dependency`.

### The IAM DefaultPolicy race

Passing `role_arn=runtime_role.role_arn` makes CloudFormation wait for the
`AWS::IAM::Role`. It does **not** make it wait for that role's inline
`DefaultPolicy`, which CDK emits as a *sibling* resource. So CloudFormation creates
the runtime and the policy in parallel, the control plane validates the ECR URI
using a role that has no permissions attached yet, and the deploy dies with:

```
Resource handler returned message: "Invalid request provided: Access denied while
validating ECR URI '<acct>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>'. The
execution role requires permissions for ecr:GetAuthorizationToken,
ecr:BatchGetImage, and ecr:GetDownloadUrlForLayer operations."
```

The error names three ECR actions, so it reads as a missing grant — and you will go
add ECR permissions that are already there. `repository.grant_pull(runtime_role)`
covers all three. The problem is *when*, not *what*.

```python
runtime.node.add_dependency(runtime_role)   # the construct, not role_arn
```

`node.add_dependency` on a construct covers its entire subtree, `DefaultPolicy`
included. Confirmed both ways on a real deploy: fails without the line, succeeds
with it, nothing else changed.

The same trap applies to any `Cfn*` resource that takes a role ARN and is validated
eagerly by its control plane — `CfnGateway` with its execution role is the other one
in this file.

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
        # add_resource_dependency, not add_dependency — the latter is deprecated on
        # CfnResource in current aws-cdk-lib and warns on every synth.
        target.add_resource_dependency(gateway)
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
            # Always set this. Without a circuit breaker, a task that cannot start —
            # bad image, missing env var, crash on boot — takes up to THREE HOURS to
            # fail the deployment, and `cdk deploy` just sits there looking slow. CDK
            # warns about it, and the warning is worth obeying.
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
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
1. EcrStack (repository)
   ↓ must exist before anything pushes to it
2. ImageBuildStack (CodeBuild) — then run the build
   ↓ image is in ECR at the derived tag
3. AuthStack (Cognito)
   ↓ exports: UserPoolId, UserPoolClientId
4. MCPGatewayStack (Gateway + Lambda targets)
   ↓ writes: SSM /mcp/endpoints/gateway/unified
5. AgentRuntimeStack (Agent container + Memory)
   ↓ exports: AgentRuntimeArn
6. BackendStack (ECS Fargate + ALB)
```

Steps 1-2 exist because **CfnRuntime resolves the image digest when it is created**.
The image must be in ECR before step 5, and re-pushing the same tag afterwards will
not roll the runtime. Keep the ECR repository in its own stack from the runtime's, or
there is no deploy that creates the repository early enough to push to.

### Cross-Stack References

| From | To | Via |
|------|----|-----|
| MCPGatewayStack | AuthStack | Constructor params: `user_pool`, `user_pool_client` (CDK emits the export/import) |
| AgentRuntimeStack | AuthStack | Same — pass the constructs |
| AgentRuntimeStack | EcrStack | Constructor param: `repository` |
| AgentRuntimeStack | ImageBuildStack | Constructor param: `image_tag` — a synth-time string, so it lands in the container URI directly |
| AgentRuntimeStack | MCPGatewayStack | SSM parameter read at container startup (not CDK synthesis time) |
| BackendStack | AgentRuntimeStack | Constructor param: `agent_runtime_arn` |

`Fn.import_value("UserPoolId")` also works, but hard-codes an export name and gives up
the dependency ordering CDK would have derived for you.

### CDK App Entry Point

Pass constructs, not looked-up strings. CDK turns a cross-stack reference into an
export/import pair itself and derives the deploy order from it:

```python
import os
import aws_cdk as cdk

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("AWS_REGION", "us-west-2"),
)

ecr_stack = EcrStack(app, "EcrStack", env=env)
build_stack = ImageBuildStack(app, "ImageBuildStack", repository=ecr_stack.repository, env=env)
auth_stack = AuthStack(app, "AuthStack", env=env)
gateway_stack = MCPGatewayStack(
    app, "MCPGatewayStack",
    user_pool=auth_stack.user_pool,
    user_pool_client=auth_stack.user_pool_client,
    env=env,
)
AgentRuntimeStack(
    app, "AgentRuntimeStack",
    repository=ecr_stack.repository,
    image_tag=build_stack.image_tag,        # synth-time str, not a token
    user_pool=auth_stack.user_pool,
    user_pool_client=auth_stack.user_pool_client,
    mcp_ssm_prefix=gateway_stack.ssm_prefix,
    env=env,
)

app.synth()
```

Interpolating `user_pool.user_pool_id` into an f-string for the discovery URL works —
CDK resolves the token inside the string at synth. Verified on a real deploy.

Avoid the alternative of a `boto3.describe_stacks` lookup at synth time to read the
Auth stack's outputs. It makes synthesis depend on deployed state, so a fresh account
silently synthesizes a *different template* (usually one with no authorizer at all)
instead of failing, and `cdk diff` stops being a function of your source.

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

### The fix, option A: let CDK hash it and build in CodeBuild

The least code, and the option to reach for first. `s3_assets.Asset` already hashes
exactly the files it stages, and `asset_hash` is a **plain Python string at synth
time** — not a token — so the same value can be handed to the build and embedded in
the runtime's container URI:

```python
source_asset = s3_assets.Asset(self, "AgentSource", path=project_root, exclude=[...])
self.image_tag = f"src-{source_asset.asset_hash[:16]}"     # concrete str

project = codebuild.Project(
    self, "AgentImageBuild",
    source=codebuild.Source.s3(bucket=source_asset.bucket, path=source_asset.s3_object_key),
    environment=codebuild.BuildEnvironment(
        # ARM host: plain `docker build` emits linux/arm64, which is what AgentCore
        # Runtime requires. No buildx, no qemu, no local container runtime.
        build_image=codebuild.LinuxArmBuildImage.AMAZON_LINUX_2_STANDARD_3_0,
        compute_type=codebuild.ComputeType.SMALL,
        privileged=True,                                   # to run the Docker daemon
    ),
    environment_variables={
        # Baked at synth time, so `start-build` needs no overrides and cannot
        # disagree with what the runtime stack deployed.
        "IMAGE_TAG": codebuild.BuildEnvironmentVariable(value=self.image_tag),
        ...
    },
    ...
)
repository.grant_pull_push(project)
source_asset.grant_read(project)
repository.grant(project, "ecr:DescribeImages")   # for the skip check below
```

Then pass `build_stack.image_tag` into the runtime stack as a constructor argument.
No `-c image_tag=`, no shell script, no git dependency, and no way for the tag that
was built to differ from the tag that was deployed.

Two details worth copying:

- **Exclude everything the Dockerfile does not copy** (`infra`, `evals`, `.venv`,
  `cdk.out`, `**/__pycache__`, `*.md`). Anything that leaks into the asset churns the
  hash, which rebuilds and redeploys a byte-identical image.
- **Make the buildspec skip an existing tag.** With `TagMutability.IMMUTABLE` on the
  repository, re-pushing an existing tag is an error rather than a no-op, and the same
  tag means the same source means the same image:

  ```bash
  if aws ecr describe-images --repository-name ${IMAGE_REPO##*/} \
       --image-ids imageTag=${IMAGE_TAG} >/dev/null 2>&1; then
    echo "tag already present, skipping build"
  else
    docker build -t ${IMAGE_REPO}:${IMAGE_TAG} . && docker push ${IMAGE_REPO}:${IMAGE_TAG}
  fi
  ```

Deploy order is then: ECR + build stacks, `aws codebuild start-build` and wait for
`SUCCEEDED`, then the remaining stacks. The runtime stack must be last regardless of
how the image gets built, because AgentCore pins the digest at create time.

### The fix, option B: hash what goes into the image yourself

For local builds, where you want the tag without a CodeBuild round trip.

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

**The obvious implementation of the first one cannot work, so here is the working one.** If the
container URI is built from `repository.repository_uri` — the normal way, and mandatory across a
stack boundary — the synthesized value is a `Fn::Join`/`Fn::ImportValue` token, never a string:

```python
uri = runtime["Properties"]["AgentRuntimeArtifact"]["ContainerConfiguration"]["ContainerUri"]
assert not isinstance(uri, str)          # a plain string here means it was hardcoded
```

So `assert isinstance(uri, str)` followed by `uri.rsplit(":")` fails for every correctly built
stack, and the lines after it are unreachable. Assert on the tag **before** it is embedded:

```python
# Pass the tag in as a synth-time value (e.g. asset.asset_hash) and test that value.
assert IMAGE_TAG not in ("latest", "main", "prod")
assert re.fullmatch(r"(src-)?[0-9a-f]{16,64}", IMAGE_TAG), IMAGE_TAG
# Or, if you must read it out of the template, take the trailing literal of the Fn::Join:
parts = uri["Fn::Join"][1]
assert isinstance(parts[-1], str) and ":latest" not in parts[-1]
```

**And do not compose the skip-existing-tag recipe above with an architecture assertion
naively.** `docker image inspect` after the `if/fi` runs on the skip path too, where no local
image exists — so an unchanged re-run reports `FATAL: image is not arm64` and the build fails
naming the wrong cause, on exactly the path the skip exists to make cheap. Put the assertion
**inside the `else`**, or `docker pull` first.
