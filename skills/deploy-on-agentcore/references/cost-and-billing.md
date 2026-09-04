# Cost and Billing

Prices verified against the AgentCore pricing page and the Pricing API. The behavioural
claims were measured on a deployed agent (Strands, three DynamoDB tools, Sonnet 4.6,
us-east-1) — the numbers are that agent's, but the *shape* generalises.

For comparing AgentCore against self-hosting, see **migrate-eks-to-agentcore**'s
`cost-model.md`; this file is about operating AgentCore economically.

## The rates

| Component | Rate |
|---|---|
| Runtime / Browser / Code Interpreter CPU | **$0.0895 per vCPU-hour** |
| Runtime / Browser / Code Interpreter memory | **$0.00945 per GB-hour** |
| Memory — short-term events | $0.25 per 1,000 events |
| Memory — long-term storage, built-in strategies | $0.75 per 1,000 records/month |
| Memory — retrieval | $0.50 per 1,000 retrievals |
| Gateway — tool invocations | $0.005 per 1,000 |
| Gateway — Search API | $0.025 per 1,000 |
| Identity | $0.010 per 1,000 tokens/API keys (successful only) |
| Policy — authorization | $0.000025 per request |
| Evaluations — built-in | $0.0024/1k input, $0.012/1k output tokens |

Per-second billing, 1-second minimum, **128 MB memory floor**. No per-invocation charge
on Runtime. Instances tier is EC2 on-demand + ~12% management fee (7.8% for G-series).

## The one thing to understand: CPU and memory bill differently

This asymmetry drives every cost decision on the platform.

**CPU is usage-based.** Charged only while consumed — *"I/O wait and idle time is free,
if no other background process is running."*

**Memory is wall-clock-based across the whole session.** Billing *"spans from microVM
boot, initialization, active processing, idle periods, until session termination"*,
charged on *"the peak memory consumed up to that second."*

Two consequences that surprise people:

- **The peak is a high-water mark that never decays.** A transient spike to 2 GB is
  billed at 2 GB for every remaining second of the session, even after the memory is
  freed. Avoid loading a large object once at startup if the steady state is small.
- **An idle session still bills memory.** The microVM is alive until the idle timeout,
  and every second of it is charged.

Measured on an agent whose turns are ~98% I/O wait: **CPU was 0.85% of the bill at default
configuration.** But once you apply the session-hygiene fix below, CPU becomes **~19%** of a
much smaller bill — because tuning removes the memory and Memory-event lines it was small
against. "Optimising CPU is optimising the rounding error" is true of the default and false of
the tuned configuration; quote whichever matches what you are running.

## Session hygiene is the dominant cost lever

For a conversational agent, the largest line is memory billed *after the conversation
ended*, waiting for `idleRuntimeSessionTimeout` (default **900s**) to fire.

Measured, 3-turn conversation, 235 MiB peak, 30s think time between turns:

| Configuration | Memory cost/conversation | Note |
|---|---|---|
| Default 900s idle timeout | $6.06e-4 | **92% of it is the idle tail** |
| `StopRuntimeSession` at end | $5.11e-5 | **~12× cheaper** |

```python
client.stop_runtime_session(
    agentRuntimeArn=runtime_arn,     # ARN, *not* the id — see below
    runtimeSessionId=session_id,
)
```

One API call. Nothing fails if you omit it — the session simply bills until it expires,
which is why this is easy to miss in review and expensive in production.

**Two traps that turn this saving into an outage.** An earlier version of this file passed
`agentRuntimeId` here, and that error propagated into generated code before it was caught.

- The parameter is `agentRuntimeArn`. `[verified]` from the botocore model:
  `required: ['runtimeSessionId', 'agentRuntimeArn']`. Note `invoke_agent_runtime` also takes
  the ARN, so there is **no** id/ARN asymmetry between the two calls to remember.
- **`ParamValidationError` is not a subclass of `ClientError`** `[verified]`. The "nothing fails
  if you omit it" framing above invites wrapping the call in `except ClientError` to make it
  best-effort — which does not catch a wrong parameter name. Called from a `finally:`, the raise
  then replaces the return value, so a fully computed answer is discarded on every request.
  Catch `(ClientError, ParamValidationError)`, or `Exception`.

Confirm any parameter name against the model rather than memory:

```bash
python3 -c "import botocore.session as s; m=s.get_session().get_service_model(
  'bedrock-agentcore').operation_model('StopRuntimeSession'); print(m.input_shape.required_members)"
```

The hard part is not the call, it is knowing **when a conversation ended**. Users close
tabs; they do not send a goodbye. In practice: an explicit close from the client, or an
`idleRuntimeSessionTimeout` tuned to the observed think-time distribution. Lowering the
timeout trades cost against a cold start for a user who returns mid-conversation.

Session hygiene also protects the **active session workloads** quota — sessions stay Active
until they expire, so a service that never stops sessions accumulates against the cap while
paying for it. One fix, two problems.

**Do not carry the number.** It is region-variant: 5,000 in us-east-1/us-west-2 but **2,500** in
ap-northeast-2, ap-southeast-2 and eu-west-1 `[verified]`, and half is the more common value.
Read the customer's region, and say whether the figure is the applied or the default limit:

```bash
aws service-quotas list-aws-default-service-quotas --service-code bedrock-agentcore \
  --region <r> --query "Quotas[?contains(QuotaName,'Session')].[QuotaName,Value]"
```

## AgentCore Memory: a requirement question, not a toggle

Short-term memory events were **55%** of the measured default bill — the largest single
line, larger than compute.

`batch_size=1` on `AgentCoreMemorySessionManager` is not a tunable you can loosen.
AgentCore hard-kills containers — no SIGTERM, no drain, no lifespan teardown — so there
is no later flush opportunity to defer to. Every turn writes.

So the question is a requirement, not a setting: **does a user resume a prior
conversation?**

- **Yes** → you need it. You would have needed an equivalent store anywhere else too.
- **No, conversations are session-scoped** → the microVM already holds the history in
  `agent.messages`, and Memory is pure cost.

Do not describe AgentCore as "removing your session store." It replaces a store you
operate with a metered service you do not, at every-turn cadence. Often a good trade;
never a free one.

## Right-size memory, not CPU

The inverse of the usual advice, and it follows from the asymmetry above.

Measure **peak** container memory, not average, and not the limit you declared. A
measured 235 MiB against a declared 1 GB limit is a 4× difference in the estimate —
costing from the manifest instead of the meter overstates the bill by that factor.

Latency is also a cost lever, because wall time is the memory meter. Anything that
shortens a turn — fixing blocking I/O on the event loop, trimming tool round trips —
reduces the memory line proportionally. Performance work and cost work are the same work.

## What to check before quoting anyone a number

| Input | How |
|---|---|
| CPU-seconds per turn | process CPU delta ÷ turns over a window |
| Wall-clock per turn | p50 turn latency |
| **Peak** memory | `kubectl top`, Container Insights, or the runtime's own metrics |
| Session lifetime | turns/conversation, think time, and whether sessions are stopped |

**Bedrock token cost is excluded from all of the above and usually dominates the actual
bill.** It is the same on any host, so it does not change a platform comparison — but
quoting compute figures as a total is misleading.
