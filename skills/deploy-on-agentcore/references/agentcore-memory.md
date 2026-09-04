# AgentCore Memory Integration

AgentCore Memory provides persistent conversation memory across sessions. It automatically extracts preferences, facts, and summaries from conversations.

## Short-Term Only: Omit `memory_strategies`

`memory_strategies` is optional, and leaving it off is the entire difference between
short-term and long-term memory:

| | Short-term (no strategies) | Long-term (strategies) |
|---|---|---|
| Stored | Raw conversation events | Events **plus** extracted records |
| Survives `event_expiry_duration` | Nothing | The records |
| Extraction cost / latency | None | Per-turn LLM extraction |
| IAM needed | Event APIs + `GetMemory` | Event APIs + the four record APIs |

```python
memory = bedrockagentcore.CfnMemory(
    self, "AgentMemory",
    name="my_agent_memory",
    description="Short-term conversation memory (raw events, no extraction).",
    event_expiry_duration=30,
    # No memory_strategies. That is the switch.
)
```

Verify on the deployed resource rather than trusting the template —
`get-memory` should report `"strategies": []` and `"status": "ACTIVE"`:

```bash
aws bedrock-agentcore-control get-memory --memory-id <id> \
  --query 'memory.{status:status,strategies:strategies}'
```

Scope the runtime role to match. Short-term needs only:

```python
actions=[
    "bedrock-agentcore:GetMemory",      # session manager resolves config at startup
    "bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent",
    "bedrock-agentcore:ListEvents", "bedrock-agentcore:DeleteEvent",
    "bedrock-agentcore:ListSessions", "bedrock-agentcore:ListActors",
]
```

Adding strategies later needs `RetrieveMemoryRecords`, `ListMemoryRecords`,
`GetMemoryRecord`, and `DeleteMemoryRecord` on top. **No agent code changes either
way** — `AgentCoreMemorySessionManager` uses whatever the memory resource declares,
so short-term first and strategies later is a pure infrastructure change.

## Memory Strategies

For long-term memory, configure strategy types in CDK:

```python
memory = bedrockagentcore.CfnMemory(
    self, "AgentMemory",
    name="my_agent_memory",
    event_expiry_duration=90,  # Keep events for 90 days
    memory_strategies=[
        # 1. User Preferences — persists across sessions
        bedrockagentcore.CfnMemory.MemoryStrategyProperty(
            user_preference_memory_strategy=bedrockagentcore.CfnMemory.UserPreferenceMemoryStrategyProperty(
                name="user_preferences",
                namespaces=["/users/{actorId}/preferences"],
                description="User preferences and communication style"
            )
        ),
        # 2. Semantic Memory — extracted facts and knowledge
        bedrockagentcore.CfnMemory.MemoryStrategyProperty(
            semantic_memory_strategy=bedrockagentcore.CfnMemory.SemanticMemoryStrategyProperty(
                name="user_facts",
                namespaces=["/users/{actorId}/facts"],
                description="User history, interests, and extracted facts"
            )
        ),
        # 3. Conversation Summaries — per-session recaps
        bedrockagentcore.CfnMemory.MemoryStrategyProperty(
            summary_memory_strategy=bedrockagentcore.CfnMemory.SummaryMemoryStrategyProperty(
                name="conversation_summaries",
                namespaces=["/summaries/{actorId}/{sessionId}"],
                description="Session-specific conversation summaries"
            )
        )
    ]
)
```

## Namespace Design

Namespaces use `{actorId}` and `{sessionId}` placeholders:

| Strategy | Namespace | Scope |
|----------|-----------|-------|
| Preferences | `/users/{actorId}/preferences` | Cross-session, per-user |
| Facts | `/users/{actorId}/facts` | Cross-session, per-user |
| Summaries | `/summaries/{actorId}/{sessionId}` | Per-session, per-user |

**The placeholder set is closed.** Only `{actorId}`, `{sessionId}`, and
`{memoryStrategyId}` are permitted — `{userId}`, `{tenantId}`, or anything else fails
validation against `[a-zA-Z0-9\-_\/]*(\{(actorId|sessionId|memoryStrategyId)\}[a-zA-Z0-9\-_\/]*)*`.
To scope by tenant, use a literal prefix (`/tenants/acme/{actorId}/facts`) or encode
the tenant into the actor ID. Strategy `name` follows the 48-character
underscores-only rule; see [naming.md](naming.md).

## Actor ID from JWT

The `actorId` is the Cognito JWT `sub` claim — a stable, immutable user identifier. Extract it without a JWT library (the Runtime already validated the token):

```python
import base64
import json
import uuid

def _extract_actor_id(token: str) -> str:
    """Extract 'sub' claim from JWT payload without verification."""
    try:
        raw = token.removeprefix("Bearer ").split(".")[1]
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload.get("sub", str(uuid.uuid4()))
    except Exception:
        return str(uuid.uuid4())
```

No PyJWT dependency needed — standard library `base64` + `json` is sufficient since we're not verifying the signature.

## Session Manager Setup

Plug the memory into the Strands Agent via `session_manager`:

```python
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

# In SessionBuilder._build_memory():
actor_id = _extract_actor_id(token)
mem_session_id = session_id or str(uuid.uuid4())

memory_config = AgentCoreMemoryConfig(
    memory_id=config.memory_id,
    session_id=mem_session_id,
    actor_id=actor_id,
)

session_manager = AgentCoreMemorySessionManager(
    agentcore_memory_config=memory_config,
    region_name=config.region,
)

# Pass to Strands Agent
agent = Agent(
    tools=tools,
    system_prompt=prompt,
    session_manager=session_manager  # Enables memory
)
```

## Why batch_size=1 (the Default)

The `AgentCoreMemorySessionManager` default `batch_size=1` saves each conversation turn immediately. This is the only safe option because **AgentCore Runtime hard-kills containers without SIGTERM**. If you set a larger batch size, unsaved turns would be lost when the container is terminated.

The session manager automatically:
- Loads relevant memories at conversation start
- Saves new events after each turn (with `batch_size=1`)
- Extracts preferences, facts, and summaries from conversation events
