"""Immutable agent configuration — loaded once at container startup.

Pattern: Frozen dataclass ensures config cannot change after creation.
Environment variables provide deployment-time overrides.
SSM Parameter Store provides infrastructure-derived values (e.g. gateway URL).
"""

import os
from dataclasses import dataclass

import boto3


@dataclass(frozen=True)
class AgentConfig:
    gateway_url: str
    model_id: str
    region: str
    memory_id: str
    memory_enabled: bool

    @classmethod
    def from_env(cls) -> "AgentConfig":
        region = os.environ.get("AWS_REGION", "us-west-2")
        ssm = boto3.client("ssm", region_name=region)
        prefix = os.environ.get("MCP_SSM_PREFIX", "/mcp/endpoints/my-gateway")
        gateway_url = ssm.get_parameter(Name=f"{prefix}/unified")["Parameter"]["Value"]

        return cls(
            gateway_url=gateway_url,
            model_id=os.environ.get("LLM_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0"),
            region=region,
            memory_id=os.environ.get("AGENTCORE_MEMORY_ID", ""),
            memory_enabled=os.environ.get("AGENTCORE_MEMORY_ENABLED", "false").lower() == "true",
        )
