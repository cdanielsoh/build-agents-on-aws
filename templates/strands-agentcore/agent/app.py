"""Production entrypoint — BedrockAgentCoreApp with singleton session.

Pattern: The builder is created once at container startup (lifespan).
The session is created lazily on the first request and reused for
subsequent requests. Token is refreshed in-place on every request.
"""

import logging
from contextlib import asynccontextmanager

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from core.builder import SessionBuilder
from core.config import AgentConfig
from core.session import Session

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_builder: SessionBuilder | None = None
_session: Session | None = None


def _extract_token(context) -> str | None:
    if hasattr(context, "request_headers") and context.request_headers:
        header = context.request_headers.get("Authorization", "")
        if header:
            return header
    return None


@asynccontextmanager
async def lifespan(app):
    global _builder
    _builder = SessionBuilder(AgentConfig.from_env())
    logger.info("Container ready.")
    yield


app = BedrockAgentCoreApp(lifespan=lifespan)


@app.entrypoint
async def agent_invocation(payload, context):
    global _session

    prompt = payload.get("prompt", "").strip()
    if not prompt:
        yield {"error": "No prompt provided"}
        return

    token = _extract_token(context)
    session_id = getattr(context, "session_id", "") or ""

    if _session is None:
        _session = _builder.build(token, session_id)
    elif token:
        _session.refresh_token(token)

    async for event in _session.stream_async(prompt):
        yield event


if __name__ == "__main__":
    # Bind explicitly. `app.run()` with no host auto-detects, and it only picks 0.0.0.0 when
    # `/.dockerenv` exists or DOCKER_CONTAINER is set — otherwise it binds 127.0.0.1 and the
    # runtime never reaches READY, presenting as an application startup failure.
    app.run(host="0.0.0.0", port=8080)  # nosec B104 - the runtime contract requires this
