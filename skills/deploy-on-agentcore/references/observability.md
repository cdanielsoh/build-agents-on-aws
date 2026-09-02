# AgentCore Observability

Traces, spans, metrics, and logs for a deployed agent — and the prerequisite for
[AgentCore Evaluations](evaluations.md), which reads the same spans.

> Get this wrong and the failure mode is **silence**, not an error: no traces, no log
> streams, no evaluations, no indication why. Most of this page is about the specific ways
> that happens.

## Table of Contents

1. [What You Get by Default](#what-you-get-by-default)
2. [Setup on AgentCore Runtime](#setup-on-agentcore-runtime)
3. [Setup Outside AgentCore Runtime](#setup-outside-agentcore-runtime)
4. [Unified vs Split Telemetry](#unified-vs-split-telemetry)
5. [Span Model](#span-model)
6. [GenAI Semantic Conventions](#genai-semantic-conventions)
7. [Log Groups and Streams](#log-groups-and-streams)
8. [IAM Requirements](#iam-requirements)
9. [Custom Spans and Attributes](#custom-spans-and-attributes)
10. [Feeding Evaluations](#feeding-evaluations)
11. [Troubleshooting](#troubleshooting)

---

## What You Get by Default

AgentCore emits OTEL-compatible telemetry with no instrumentation work:

| Resource | Default telemetry |
|---|---|
| **Agent runtime** | Session count, latency, duration, token usage, error rates |
| **Gateway** | Built-in metrics, including policy evaluation spans when tracing is on |
| **Memory** | Built-in metrics; spans and logs **only if you enable them** |

Everything lands in CloudWatch. For agent runtime specifically, the CloudWatch console
provides a dashboard with trace visualizations, custom span metric graphs, and error
breakdowns.

---

## Setup on AgentCore Runtime

Runtime ships an ADOT sidecar. Three steps, and a fourth that is easy to miss:

**1. Dependencies**

```toml
dependencies = [
    "strands-agents[otel]~=1.54",
    "aws-opentelemetry-distro>=0.18",   # >=0.18 for unified telemetry
]
```

**2. Launch under the instrumentation wrapper**

```dockerfile
CMD ["opentelemetry-instrument", "python", "app.py"]
```

**3. Do NOT set `OTEL_*` environment variables**

No `OTEL_EXPORTER_*`, no `OTEL_SERVICE_NAME`. The sidecar configures them for
AgentCore-hosted agents, and overriding them silently breaks delivery. (This advice inverts
for agents hosted elsewhere — see the next section.)

**4. Enable CloudWatch Transaction Search**

An account/region-level setting, not part of your stack. **AgentCore Evaluations requires it
in both delivery modes**, and turning it on is what creates the shared `aws/spans` log group.
Forgetting it is the single most common reason evaluations find no sessions.

---

## Setup Outside AgentCore Runtime

Agents on ECS, EKS, Lambda, or elsewhere still work with Observability and Evaluations — the
service consumes telemetry, not a runtime dependency. **Here you *do* set the OTEL
environment variables**, because there is no sidecar to do it for you:

| Variable | Purpose |
|---|---|
| `OTEL_EXPORTER_OTLP_TRACES_HEADERS` | Names the log group that receives spans (unified mode) |
| `OTEL_EXPORTER_OTLP_LOGS_HEADERS` | Names the log group that receives event records (split mode) |

AWS publishes working samples for Strands on EKS, ECS, and Lambda in the
[`agentcore-samples`](https://github.com/awslabs/agentcore-samples) repository.

---

## Unified vs Split Telemetry

Two delivery modes that determine **where the conversation content lives**. This matters
because Evaluations needs model prompts, model completions, and tool inputs/outputs to score
a session.

| | Unified (recommended) | Split |
|---|---|---|
| Content location | Stays **on the span** as attributes | Moved **off the span** into separate event records |
| Spans go to | The agent's own log group, `spans` stream | Shared `aws/spans` log group |
| Content goes to | Same log group as the spans | Agent's log group, `otel-rt-logs` stream |
| Linkage | None needed | Records join back via shared `traceId` + `spanId`; content in `body` (e.g. `body.input.messages`) |
| Requires | `aws-opentelemetry-distro>=0.18.0` | Any version |

### Which mode am I in?

- Agents created **on or after 2026-07-20** default to **unified**.
- Agents created **before** that default to **split**.
- On Runtime, switch with `UNIFIED_TRACES_DESTINATION_ENABLED` (`true` = unified,
  `false` = split).

**A pre-0.18 ADOT silently falls back to split** even with the env var set to `true`. If you
expected unified and your spans are showing up in `aws/spans`, check the distro version
before anything else.

### Why prefer unified

Beyond evaluation: traces and logs sit together for debugging, one log group means one IAM
policy and one CMK encryption boundary per agent, and you can export everything an agent
produces by subscribing to a single log group.

Evaluations reads **both** modes and gives identical results either way — this is not a
correctness decision, it is an operability one.

### Switching does not migrate

Changing the mode leaves already-delivered telemetry where it was written. Old sessions
remain evaluable in their original location, so a switch does not invalidate history — but it
does mean a given time window may span both layouts.

---

## Span Model

The hierarchy Evaluations reconstructs:

```
Session  — related interactions from one user/workflow
  └── Trace  — one complete agent execution (one request)
        ├── invoke agent span   — the top-level agent run
        ├── inference span      — a single model call
        └── execute tool span   — a single tool call
```

The service classifies each span by the attributes the framework set, then reads what it
needs: the user prompt from the user-role message in the agent input, the agent response from
the assistant-role message in the agent output, and tool inputs/outputs from tool spans.

**Identifying attributes always stay on the span in both modes.** Only the conversation
content moves. Where content sits within a span also depends on the instrumentation library —
most record it as span attributes, some attach it as span events.

---

## GenAI Semantic Conventions

AgentCore builds on three OTEL specs:

| Spec | Governs |
|---|---|
| [Trace semconv](https://opentelemetry.io/docs/specs/semconv/general/trace/) | Span/trace structure and meaning |
| [Event semconv](https://opentelemetry.io/docs/specs/semconv/general/events/) | Event record structure (split mode) |
| [GenAI semconv](https://github.com/open-telemetry/semantic-conventions-genai) | The `gen_ai.*` attributes describing agent, model, and tool operations |

Session correlation runs on `gen_ai.conversation.id` and `session.id`. Setting both is what
lets the service group traces into a session — and it is exactly what the eval suite's
`EvalBuilder` injects via `trace_attributes`:

```python
agent = Agent(
    ...,
    trace_attributes={
        "gen_ai.conversation.id": session_id,
        "session.id": session_id,
    },
)
```

Because these are conventions rather than a proprietary format, the same telemetry works with
your existing observability stack.

---

## Log Groups and Streams

### On AgentCore Runtime

Agent log group: `/aws/bedrock-agentcore/runtimes/<agent_id>-<endpoint_name>`

| Stream | Contains |
|---|---|
| `[runtime-logs]` | Container stdout/stderr — your `logger` output |
| `spans` | Spans **with** content (unified mode) |
| `otel-rt-logs` | Event records carrying content (split mode) |

Shared: `aws/spans` — spans in split mode. Created by enabling Transaction Search.

---

## IAM Requirements

The runtime role needs all of the following. Missing any one of them fails **silently**:

```json
{
  "Effect": "Allow",
  "Action": [
    "logs:DescribeLogGroups",
    "logs:CreateLogGroup",
    "logs:CreateLogStream",
    "logs:PutLogEvents",
    "logs:DescribeLogStreams",
    "xray:PutTraceSegments",
    "xray:PutTelemetryRecords",
    "xray:GetSamplingRules",
    "xray:GetSamplingTargets",
    "cloudwatch:PutMetricData"
  ],
  "Resource": "*"
}
```

**`logs:DescribeLogGroups` must be granted on `log-group:*`, not a specific ARN.** Scope it
down and no `[runtime-logs]` streams are created at all — you get an agent that works and
produces no logs, with nothing indicating why. This one costs more debugging time than the
rest combined.

Scope `logs:PutLogEvents` and `CreateLogStream` to your agent's log group in production;
`DescribeLogGroups` is the exception that genuinely needs the wildcard.

---

## Custom Spans and Attributes

Default telemetry covers the agent loop. Add your own for domain events worth tracing —
a downstream API call, a cache decision, a business rule outcome:

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

@tool(context=True)
def process_refund(tool_context: ToolContext, order_number: int, amount: float) -> dict:
    with tracer.start_as_current_span("refund.process") as span:
        span.set_attribute("refund.amount", amount)
        span.set_attribute("refund.currency", "USD")
        result = refunds.process(order_number, amount)
        span.set_attribute("refund.outcome", result["status"])
        return result
```

Two rules:

- **Never put credentials, tokens, or PII in span attributes.** Spans go to CloudWatch Logs
  and are read by evaluation judges. A token on a span is a token in your log retention and in
  a model's context.
- Prefer low-cardinality attribute values. A span attribute per unique order ID is a metric
  explosion; per order *status* is useful.

Custom span metrics are graphed in the CloudWatch agent observability dashboard.

---

## Feeding Evaluations

The dependency chain, in order — each step is useless without the one before it:

```
1. ADOT instrumentation           → spans exist
2. CloudWatch Transaction Search  → spans are searchable (required by Evaluations)
3. Correct IAM                    → spans and logs actually get written
4. gen_ai.conversation.id / session.id → traces group into sessions
5. Delivery mode understood       → you know which log group to point a batch job at
   ↓
AgentCore Evaluations can score sessions
```

Batch evaluation takes a **CloudWatch Logs location**, so knowing your delivery mode is a
prerequisite to configuring it: point a batch job at the agent log group in unified mode, and
the service reads spans from `aws/spans` and joins event records in split mode.

See [evaluations.md](evaluations.md) for what happens after this point.

---

## Troubleshooting

**No `[runtime-logs]` streams at all** — `logs:DescribeLogGroups` is not granted on
`log-group:*`. Fix the resource scope, not the action list.

**No traces anywhere** — either `opentelemetry-instrument` is missing from the Dockerfile
`CMD`, or `OTEL_*` env vars are set on a Runtime-hosted agent and are overriding the sidecar.
Remove them.

**Evaluations reports no sessions found** — CloudWatch Transaction Search is not enabled. It
is required in both delivery modes and is an account/region setting outside your stack.

**Spans in `aws/spans` when you expected unified** — `aws-opentelemetry-distro` is older than
0.18.0, so ADOT fell back to split regardless of
`UNIFIED_TRACES_DESTINATION_ENABLED`. Bump the pin.

**Spans present but Evaluations cannot read the conversation** — in split mode the content is
in separate event records. Confirm `otel-rt-logs` is receiving records and that `traceId`/
`spanId` line up with the spans.

**Traces not grouped into a session** — `gen_ai.conversation.id` and `session.id` are missing
or inconsistent across turns. Session IDs must be stable for the whole conversation, including
across interrupt resumptions (and at least 33 characters — see
[streaming-backend.md](streaming-backend.md)).

**Memory produces metrics but no spans** — spans and logs are opt-in for Memory resources,
unlike for agent runtime.

---

## Related

- [evaluations.md](evaluations.md) — what consumes these spans
- [runtime-and-sessions.md](runtime-and-sessions.md) — the container and IAM context
- [policy.md](policy.md) — policy evaluation spans and `LOG_ONLY` telemetry
- `strands-agent-design` → `references/testing-with-evals.md` — the local in-memory span
  exporter used for pre-deploy trace evals
