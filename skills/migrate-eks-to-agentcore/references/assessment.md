# Assessment method — how to read, probe, measure, and ask

**Scope: the mechanics only.** What the gates are lives in [constraints.md](constraints.md);
what to inventory lives in [production-inventory.md](production-inventory.md); what to report
lives in that file's "Reporting" section. This file answers *how do I find out*.

Governing principle: **read and measure before asking.** Almost everything decisive is
derivable, and a questionnaire that asks what you could have read reads as a sales script —
which costs the trust the whole engagement depends on.

| Source | Yields |
|---|---|
| **Repo** | topology, image arch, protocol, flush cadence, event-loop hygiene, guards, auth shape, whether the knowledge store is VPC-resident |
| **Manifests** | replicas, affinity, resources, HPA/KEDA, grace period, ingress annotations |
| **Live AWS APIs** | quotas (default *and* applied), region availability, prices |
| **Their telemetry** | CPU/turn, wall/turn, peak memory, concurrency, turns per conversation |
| **The customer** | data residency, compliance, team depth, roadmap, SLA, cutover appetite |

Only the last row is a question. Everything above it is work.

## Reading the repo

Read **shape, not craft.** A working vibe-coded service is the normal input; grading quality
leads to wrong conclusions. Read what surfaces exist, where state lives, what guards are
present.

And **check rather than assume** — real services often get the hard parts right and the easy
parts wrong. While building the reference for this skill I got `asyncio.to_thread` wrong where
the customer service being modelled got it right.

### Core shape

```bash
# Image architecture — amd64 means a rebuild
grep -rn 'platform' Dockerfile*

# Session topology (AGENTREL03)
grep -rniE 'session.?store|session.?id|dynamodb|redis|elasticache' --include='*.py'
grep -rniE 'lru|cache\[|_sessions\[|agent_cache' --include='*.py'

# Affinity — tells you topology B
grep -rn 'stickiness\|sessionAffinity\|StatefulSet\|headless' --include='*.yaml'

# Event-loop blocking: blocking calls inside async def, not via to_thread
# NB: do NOT pipe into a second `grep -n` — it numbers the pipe stream, not the file, and the
# decision record requires real file:line evidence. Use -A context and read the filenames:
grep -rn -A15 'async def' --include='*.py' . | grep -E '\.get_item|\.put_item|requests\.|\.invoke_model'
grep -rn 'asyncio.to_thread\|run_in_executor' --include='*.py'
```

### The domains most often missed

These are the ones an assessment skips by default, and where the empty results are the
finding. One grep per inventory domain.

```bash
# Outbound auth (AGENTSEC03) — hand-rolled OAuth is often the largest deletable component
grep -rniE 'client_credentials|refresh_token|code_verifier|pkce|authorization_code' --include='*.py'
grep -rniE 'user_tokens|oauth_tokens|token_store|token_vault' --include='*.py'

# Inbound auth (AGENTSEC03) — and whether it is SigV4, which makes migration breaking
grep -rniE 'authorizer|oidc|jwt|verify_token|cognito' --include='*.py' --include='*.yaml'
grep -rn 'sigv4\|SigV4\|sign_request\|SigV4Auth' --include='*.py'

# Multi-tenancy (AGENTPERF07, AGENTSEC01)
grep -rniE 'tenant|org_id|workspace_id' --include='*.py' --include='*.yaml'

# Long-term memory and RAG (AGENTPERF03) — distinct from session state
grep -rniE 'embedding|vector|opensearch|pgvector|pinecone|knowledge.?base|rerank' --include='*.py'

# Guardrails, PII, HITL (AGENTSEC04, AGENTSEC07, AGENTSEC08)
grep -rniE 'guardrail|redact|pii|moderat|approval|confirm|interrupt' --include='*.py'

# Spend ceilings (AGENTCOST01) and idempotency (AGENTREL06)
grep -rniE 'max_tool_calls|token.?budget|BeforeToolCallEvent|cancel_tool' --include='*.py'
grep -rniE 'idempot|dedup|request_id' --include='*.py'

# Self-hosted MCP servers (AGENTOPS04)
grep -rln 'streamable_http\|FastMCP\|/mcp' --include='*.py' --include='*.yaml'

# Evals (AGENTOPS06) — absence limits what Phase 2 can claim
ls -d test* eval* 2>/dev/null; grep -rln 'strands_evals\|golden\|expected_response' --include='*.py'

# Prompt lifecycle (AGENTOPS02) — versioned files, or inline strings edited in place?
grep -rln 'system_prompt\|SYSTEM_PROMPT' --include='*.py'; ls -d prompts/ 2>/dev/null
```

## Probing AWS — verify, never recall

```bash
# Quotas — defaults, and what this account actually has (they differ)
aws service-quotas list-aws-default-service-quotas --service-code bedrock-agentcore --region <r>
aws service-quotas list-service-quotas --service-code bedrock-agentcore --region <r>

# Region availability — probe; published lists have been stale
aws bedrock-agentcore-control list-agent-runtimes --region <r> --max-results 1

# Node price for the EKS side of the cost model
aws pricing get-products --service-code AmazonEC2 --region us-east-1 \
  --filters "Type=TERM_MATCH,Field=instanceType,Value=<type>" \
            "Type=TERM_MATCH,Field=location,Value=<location>" \
            "Type=TERM_MATCH,Field=operatingSystem,Value=Linux" \
            "Type=TERM_MATCH,Field=tenancy,Value=Shared" \
            "Type=TERM_MATCH,Field=preInstalledSw,Value=NA" \
            "Type=TERM_MATCH,Field=capacitystatus,Value=Used"
```

Read-only throughout. If the account might be production, say which identity you are using
before calling anything, and do not create, modify or delete.

Two traps worth knowing: **`timeout` is not present on macOS**, so a probe loop wrapping AWS
calls in it fails uniformly and looks like "unavailable in every region" — a false negative
that produces a wrong blocker. And **applied quotas differ from defaults**, so check both.

## Measuring — prefer what already exists

Do not instrument if the data is already there.

| Number | Where to look first |
|---|---|
| CPU per turn | Container Insights `pod_cpu_usage_total`, or `kubectl top` deltas ÷ turn count |
| Wall per turn | existing traces, ALB target response time, app logs |
| **Peak** memory | `kubectl top pods`, Container Insights `pod_memory_working_set`. **Peak, not average, and not the manifest limit** |
| Concurrency | in-flight gauge if present; else ALB active connection count |
| Turns per conversation | session store item stats, or conversation logs |

If instrumentation is needed, the minimum is three counters: process CPU
(`resource.getrusage`), summed request wall time, and an in-flight gauge. That answers the
cost question.

**Measure in-cluster, in-region.** Local measurement of the reference gave 1,048 ms of
apparent store I/O; in-cluster it was 11.5 ms. The difference was internet latency and
thread-pool queueing, not real cost — and the local figure would have made the session store
look like the dominant per-turn cost when it is ~10% of it.

## Asking — Gate 3, and why it is short

Only what cannot be derived. Six questions:

- Data residency and compliance constraints
- **Do non-agent workloads share the cluster?** — this one can invert the cost verdict
- Kubernetes depth on the team, and who is on call
- Roadmap — more agents, multi-agent, A2A?
- SLA and error budget
- Appetite for a parallel run, and who signs off on cutover

Two more that are product questions rather than architecture, and that decide real verdicts:

- **Can a user be away longer than the idle timeout and still expect their conversation
  intact?** — decides whether AgentCore Memory (or a retained store) is needed. Ask it this
  way round: "does a returning user resume a *prior* conversation?" gets a false "no" from
  teams who still need durability across a 20-minute gap in the *same* conversation. Getting
  this wrong deletes the session store and loses history at idle expiry
- **How does a conversation end?** — decides whether `StopRuntimeSession` is implementable,
  which is the top cost lever

## Confidence, and saying "I don't know"

Set `confidence` honestly in the record:

- **high** — topology detected from code, all four measurements taken on their workload, no
  unresolved blockers, most Lens practices assessed
- **medium** — measurements partial or extrapolated, or topology ambiguous
- **low** — no measurements; the verdict rests on structure alone

At `low`, say plainly that the cost recommendation is unavailable and name the measurement
that would change it. **A recommendation presented at unearned confidence is the failure mode
that loses the account** — an admitted gap is not.

Record what you could not assess, per practice, with the reason. "I could not assess `AGENTSEC09`
because I have no visibility into their pen-testing" is a better answer than silence.
