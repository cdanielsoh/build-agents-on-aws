"""Immutable agent configuration — loaded once at container startup.

Pattern: Frozen dataclass ensures config cannot change after creation.
Environment variables provide deployment-time overrides.
SSM Parameter Store provides infrastructure-derived values (e.g. gateway URL).
"""

import os
from dataclasses import dataclass

import boto3

# Cross-region inference profile. `global.` routes to the lowest-latency region with
# capacity; `us.` pins to US regions. Verify availability in your account with:
#   aws bedrock list-inference-profiles --query \
#     'inferenceProfileSummaries[?contains(inferenceProfileId, `claude`)].inferenceProfileId'
# Use global.anthropic.claude-opus-5 where reasoning quality matters more than cost.
DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-5"

# Claude 5 models expose a 1M-token window. Setting this explicitly is what lets the
# conversation manager compute real utilization for proactive compression rather than
# estimating — do not leave it at a stale value after changing models.
DEFAULT_CONTEXT_WINDOW_TOKENS = 1_000_000


@dataclass(frozen=True)
class AgentConfig:
    gateway_url: str
    model_id: str
    region: str
    memory_id: str
    memory_enabled: bool
    context_window_tokens: int

    @classmethod
    def from_env(cls) -> "AgentConfig":
        region = os.environ.get("AWS_REGION", "us-west-2")

        # The Gateway URL is written to SSM by the infrastructure deploy, so the
        # container does not need it baked into its image. Set GATEWAY_URL directly
        # to skip the lookup (useful for local runs).
        gateway_url = os.environ.get("GATEWAY_URL", "")
        if not gateway_url:
            prefix = os.environ.get("MCP_SSM_PREFIX", "/mcp/endpoints/my-gateway")
            ssm = boto3.client("ssm", region_name=region)
            gateway_url = ssm.get_parameter(Name=f"{prefix}/unified")["Parameter"]["Value"]

        return cls(
            gateway_url=gateway_url,
            model_id=os.environ.get("LLM_MODEL_ID", DEFAULT_MODEL_ID),
            region=region,
            memory_id=os.environ.get("AGENTCORE_MEMORY_ID", ""),
            memory_enabled=os.environ.get("AGENTCORE_MEMORY_ENABLED", "false").lower() == "true",
            context_window_tokens=int(
                os.environ.get("CONTEXT_WINDOW_TOKENS", DEFAULT_CONTEXT_WINDOW_TOKENS)
            ),
        )
