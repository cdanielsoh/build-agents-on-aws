# Resource Naming Rules and Limits

Every pattern below is quoted from the `bedrock-agentcore-control` API model
(`apiVersion 2023-06-05`) and the `bedrock-agentcore` data-plane model — not from
documentation prose. Patterns are **full-match**: use `re.fullmatch`, not `re.search`.

## The one thing to know

**Runtime and Memory forbid hyphens. Gateway and its targets forbid underscores.**

```
Runtime  / Memory      [a-zA-Z][a-zA-Z0-9_]{0,47}      underscores only, ≤48
Gateway  / Target      ([0-9a-zA-Z][-]?){1,100}        hyphens only,     ≤100
```

So a single project name cannot be used verbatim across a stack. `my-agent` is a legal
gateway name and an illegal runtime name; `my_agent` is the reverse. Carry one
canonical name and derive both spellings:

```python
APP_NAME = "agent-scaffold"              # Gateway, targets, Lambdas, ECR, IAM roles
SNAKE    = APP_NAME.replace("-", "_")    # Runtime, Memory, endpoints, sandboxes
```

**CDK does not check these.** L1 `Cfn*` constructs pass strings through untouched, so
a bad name synthesizes cleanly and fails minutes later inside CloudFormation as a
`ValidationException`. Assert the patterns in a unit test instead of learning them one
failed deploy at a time — see [Assert it in a test](#assert-it-in-a-test).

## Full table

| Resource | Field | Pattern | `-` | `_` | Max |
|---|---|---|:-:|:-:|--:|
| Agent runtime | `agentRuntimeName` | `[a-zA-Z][a-zA-Z0-9_]{0,47}` | ✗ | ✓ | 48 |
| Runtime endpoint | `name` | `[a-zA-Z][a-zA-Z0-9_]{0,47}` | ✗ | ✓ | 48 |
| Memory | `name` | `[a-zA-Z][a-zA-Z0-9_]{0,47}` | ✗ | ✓ | 48 |
| Memory strategy | `name` | `[a-zA-Z][a-zA-Z0-9_]{0,47}` | ✗ | ✓ | 48 |
| Browser / Code interpreter | `name` | `[a-zA-Z][a-zA-Z0-9_]{0,47}` | ✗ | ✓ | 48 |
| Browser profile | `name` | `[a-zA-Z][a-zA-Z0-9_]{0,47}` | ✗ | ✓ | 48 |
| Custom evaluator | `evaluatorName` | `Builtin.[a-zA-Z0-9_-]+` or `[a-zA-Z][a-zA-Z0-9_]{0,47}` | ✗ | ✓ | 48 |
| Online eval config | `onlineEvaluationConfigName` | `[a-zA-Z][a-zA-Z0-9_]{0,47}` | ✗ | ✓ | 48 |
| **Gateway** | `name` | `([0-9a-zA-Z][-]?){1,100}` | ✓ | ✗ | 100 |
| **Gateway target** | `name` | `([0-9a-zA-Z][-]?){1,100}` | ✓ | ✗ | 100 |
| Policy / policy engine | `name` | `[A-Za-z][A-Za-z0-9_]*` | ✗ | ✓ | 48 |
| Harness | `harnessName` | `[a-zA-Z][a-zA-Z0-9_]{0,39}` | ✗ | ✓ | **40** |
| Configuration bundle | `bundleName` | `[a-zA-Z][a-zA-Z0-9_]{0,99}` | ✗ | ✓ | 100 |
| Credential provider (API key, OAuth2) | `name` | `[a-zA-Z0-9\-_]+` | ✓ | ✓ | 128 |
| Workload identity | `name` | `[A-Za-z0-9_.-]+` | ✓ | ✓ | 255 (**min 3**) |
| Registry | `name` | `[a-zA-Z0-9][a-zA-Z0-9_\-\./]*` | ✓ | ✓ | 64 |
| Registry record | `name` | `[a-zA-Z0-9][a-zA-Z0-9_\-\./]*` | ✓ | ✓ | 255 |
| Payment connector | `name` | `[a-zA-Z][a-zA-Z0-9_]{0,47}` | ✗ | ✓ | 48 |
| Payment manager | `name` | `[a-zA-Z][a-zA-Z0-9]{0,47}` | ✗ | ✗ | 48 |
| Git branch (bundles) | `branchName` | `[a-zA-Z][a-zA-Z0-9_/-]{0,127}` | ✓ | ✓ | 128 |

Most of the column is "must start with a letter, underscores only, 48 characters".
The exceptions are what bite: **Gateway/Target invert the rule**, **Harness is 40 not
48**, **Payment manager allows neither separator**, and **Workload identity has a
minimum length of 3**.

## Gateway's pattern is stranger than it looks

`([0-9a-zA-Z][-]?){1,100}` is a *repeated group*, not a character class, so each
alphanumeric may be followed by at most one hyphen. That means:

| Name | Valid | Why |
|---|:-:|---|
| `my-gateway` | ✓ | |
| `my_gateway` | ✗ | no underscores |
| `-my-gateway` | ✗ | cannot lead with a hyphen |
| `my--gateway` | ✗ | each hyphen needs an alphanumeric before it |
| `my-gateway-` | ✓ | a trailing hyphen is legal |
| `9lives` | ✓ | may start with a digit, unlike everything else in the table |

The `{1,100}` counts *groups*, so the character ceiling is higher than 100 — but do
not design around that.

## Generated IDs are not your name

Creating a resource returns `<name>-<10 random chars>`:

```
agent_scaffold_runtime  ->  agent_scaffold_runtime-KJYOeu2eJv
agent_scaffold_memory   ->  agent_scaffold_memory-Xbbr2J7GzL
agent-scaffold-gateway  ->  agent-scaffold-gateway-io11mweos6
```

Two consequences:

- **The 48-character name limit is what bounds the ID.** `ResourceId` is
  `[A-Za-z][A-Za-z0-9_]*-[a-z0-9_]{10}` with max 59 = 48 + 1 + 10. A name near the
  ceiling leaves no room for anything you might want to append downstream.
- **Gateway IDs are lowercase-only.** `GatewayId` is `([0-9a-z][-]?){1,100}-[0-9a-z]{10}`
  while `GatewayName` permits uppercase, so a gateway named `MyGateway` does not have
  an ID of `MyGateway-…`. Never reconstruct an ID by string-building from a name —
  read `attr_gateway_identifier` / the create response.

## Session IDs: the request and response shapes disagree

This is the sharpest edge in the data plane.

| Field | Shape | Pattern | Min | Max |
|---|---|---|--:|--:|
| `runtimeSessionId` **in a request** (`InvokeAgentRuntime`, `StopRuntimeSession`, `GetAgentCard`) | `SessionType` | *none* | **33** | 256 |
| `runtimeSessionId` **in the response** | `SessionId` | `[a-zA-Z0-9][a-zA-Z0-9-_]*` | 1 | 100 |
| `sessionId` for Memory (`CreateEvent`, `ListEvents`, …) | `SessionId` | `[a-zA-Z0-9][a-zA-Z0-9-_]*` | 1 | 100 |
| `Mcp-Session-Id` response header | `SessionId` | `[a-zA-Z0-9][a-zA-Z0-9-_]*` | 1 | 100 |

Read that again: **the inbound runtime session ID has a 33-character minimum and no
character restrictions, while every other session field has no minimum and a
character restriction.** So:

- `str(uuid.uuid4())` is 36 characters and works. `uuid4().hex` is 32 and **fails** by
  one character. Truncating a UUID for readable logs fails.
- A session ID that Memory accepts happily can be rejected by `InvokeAgentRuntime` for
  being too short, which presents as a broken caller rather than a bad ID.
- Generate one ID per conversation that satisfies the *strictest* rule — ≥33 chars,
  `[a-zA-Z0-9][a-zA-Z0-9-_]*` — and use it everywhere, including on interrupt
  resumption.

```python
session_id = f"sess-{uuid.uuid4()}"     # 41 chars, alphanumeric + hyphens
```

## Memory namespaces: three placeholders, no more

```
[a-zA-Z0-9\-_\/]*(\{(actorId|sessionId|memoryStrategyId)\}[a-zA-Z0-9\-_\/]*)*
```
1–512 characters. The placeholder set is **closed**: `{actorId}`, `{sessionId}`,
`{memoryStrategyId}`. Anything else — `{userId}`, `{tenantId}`, `{namespace}` — fails
validation, so scope by tenant using a literal prefix (`/tenants/acme/{actorId}/facts`)
or by encoding it into the actor ID itself.

Both `-` and `_` are legal here, along with `/`.

## Other limits worth knowing

| Thing | Constraint |
|---|---|
| Runtime env var key | 1–100 chars, no pattern |
| Runtime env var value | 0–5000 chars |
| `requestHeaderAllowlist` entry | `[A-Za-z][A-Za-z0-9_-]{0,255}` |
| Gateway tool name (`ToolDefinition.name`) | **no pattern or length in the API model** — but the Gateway exposes it as `{target}___{tool}`, so budget for the prefix |
| Registry / record names | may contain `.` and `/`, unlike anything else |

## Assert it in a test

The failure mode is a slow one — synth passes, deploy runs for minutes, CloudFormation
rejects the name. One test removes the whole class:

```python
import re
import pytest

PATTERNS = {
    "runtime":  r"[a-zA-Z][a-zA-Z0-9_]{0,47}",
    "memory":   r"[a-zA-Z][a-zA-Z0-9_]{0,47}",
    "strategy": r"[a-zA-Z][a-zA-Z0-9_]{0,47}",
    "gateway":  r"([0-9a-zA-Z][-]?){1,100}",
    "target":   r"([0-9a-zA-Z][-]?){1,100}",
    "policy":   r"[A-Za-z][A-Za-z0-9_]*",
}

def assert_name(kind: str, name: str) -> None:
    # fullmatch: the service anchors these patterns.
    assert re.fullmatch(PATTERNS[kind], name), f"invalid {kind} name: {name!r}"


def test_stack_names_are_valid(synth_template):
    assert_name("runtime",  APP_NAME.replace("-", "_") + "_runtime")
    assert_name("memory",   APP_NAME.replace("-", "_") + "_memory")
    assert_name("gateway",  APP_NAME + "-gateway")
    assert_name("target",   "support")
```

Better still, read the names back out of the synthesized template so the test checks
what CDK actually emits rather than what you believe it emits.
