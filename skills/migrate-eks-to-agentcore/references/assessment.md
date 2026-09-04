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

## Two things to establish before anything else

**Is there a running deployment you can reach?** If not — the common pre-engagement case —
then **Gate 2 is unavailable, full stop.** Every measurement is `open`, the concurrency sweep
below cannot be run, and the correct output is `cost_confidence: unavailable`. Say that at the
start rather than letting an assessor discover it three steps in and reach for this plugin's
reference figures to fill the hole. A repo-only assessment is still valuable — Gate 0 and the
inventory are most of the findings — it just cannot price anything.

**Whose account are you in?** Run `aws sts get-caller-identity` and compare it to the
customer's account id. If they differ:

- `list-service-quotas` returns **your** applied values, not theirs. Record only
  `list-aws-default-service-quotas` output, tagged `[verified: defaults only]`, and mark every
  applied value `open (wrong account)`.
- Every `describe-vpc-*`, `describe-route-tables` and ECR/Budgets read describes *your*
  infrastructure. Running them and recording the result produces a confident wrong answer —
  the worst failure mode available here.
- Region availability and prices are account-independent, so those remain valid.

This matters because this file elsewhere insists on applied-vs-default. From outside the
account that instruction actively causes the error it was written to prevent.

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
# Then: does each have a build definition HERE? On a real repo two MCP servers ran as separate
# Deployments on pinned tags, and the only Dockerfile copied just the main service — so a patch
# planned against them had no tree to apply to. Diff manifest images against what you can build:
grep -rhoE 'image: *[^ ]+' --include='*.yaml' . | sort -u; ls Dockerfile* */Dockerfile* 2>/dev/null

# Evals (AGENTOPS06) — absence limits what Phase 2 can claim
ls -d test* eval* 2>/dev/null || true; grep -rln 'strands_evals\|golden\|expected_response' --include='*.py' .

# Prompt lifecycle (AGENTOPS02) — versioned files, or inline strings edited in place?
grep -rln 'system_prompt\|SYSTEM_PROMPT' --include='*.py'; ls -d prompts/ 2>/dev/null
```

### The dead-control sweep — negative signal, and it outperforms the greps

Every grep above is **positive signal**: it finds a component that exists. On two independent
assessments the highest-severity findings were all *negative* signal, and no grep above could
have found any of them:

- a `requires_human_approval()` function with **zero call sites**, while three comments and a
  prompt claimed destructive actions were gated
- a `DESTRUCTIVE_TOOLS` constant naming two tools **that were never built**, omitting the one
  that was
- a cache key omitting a `tenant_id` **already in scope two lines above**
- a tool declaring a `query` parameter and **never referencing it in the body** — every call
  silently returned the same rows
- an `IDLE_EVICT_SECONDS` constant that was **dead code**; no eviction ran
- a token-refresh path indexing `tok["refresh_token"]`, which **RFC 6749 §5.1 makes optional**
  on a refresh response and many IdPs omit. `KeyError` → unhandled 500, on the one path a long
  conversation is guaranteed to reach. The record documented the *missing* refresh flow in
  detail and never checked whether the existing one worked

That last one generalises: **the sweep finds controls that do nothing, but also read the ones
that do something for assumed-present fields.** Any `d["key"]` on a response from an external
protocol is worth one look at whether the spec makes that key optional.

So sweep for controls that exist and do nothing:

```bash
# Symbols whose name claims a safety/tenancy/approval role — then check call sites
grep -rnE 'def [a-z_]*(approve|approval|redact|guard|sanitiz|validat|authori[sz]|scope|tenant|budget|limit)[a-z_]*' --include='*.py' .
# For each hit, count call sites. Zero (beyond the definition) is a finding:
grep -rn '<symbol>' --include='*.py' . | grep -v 'def <symbol>'

# Declared tool parameters never used in the body
grep -rn -A25 '@tool' --include='*.py' .   # then read each: is every parameter referenced?

# Constants that name things, and whether those things exist
grep -rnE '^[A-Z_]+ *= *[{\[]' --include='*.py' .

# Modules imported but absent — an unbuildable tree blocks half an assessment
python3 -m compileall -q . 2>&1 | head
grep -rhoE '^from \.[a-z_.]+ import|^from [a-z_.]+ import' --include='*.py' . | sort -u
```

The last one matters more than it looks: on three of five assessed services a module was
imported and did not exist, which meant the image was built from a different tree than the one
under version control. That single fact blocked inbound-auth classification, flush cadence and
event-loop hygiene at once — one cause, many `unknown`s.

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
| CPU per turn | cgroup v2 `/sys/fs/cgroup/cpu.stat` `usage_usec` deltas ÷ turns |
| Wall per turn | their own request telemetry, existing traces, ALB target response time |
| **Peak** memory | cgroup v2 `/sys/fs/cgroup/memory.peak` — a true monotonic high-water mark |
| Concurrency | in-flight gauge if present; else ALB active connection count |
| Turns per conversation | session store item stats, or conversation logs |

**`kubectl top` cannot answer the memory question, and naming it here was wrong.** Measured
against cgroup ground truth on a live pod: `kubectl top` reported **4m CPU while 8 conversations
were in flight**, repeated a stale value for four consecutive samples (18s), and its best memory
reading was **3.7% below the true peak**. It is a ~60s-window average sampled on a delay.

Use the cgroup files, read from inside the container:

```bash
kubectl exec -n <ns> <pod> -- sh -c 'cat /sys/fs/cgroup/memory.peak; grep usage_usec /sys/fs/cgroup/cpu.stat'
```

`memory.peak` is exactly the quantity AgentCore bills on — a high-water mark that never decays
— so it is not merely more accurate, it is the *right* metric. Subtract measured idle drift from
the CPU delta (an idle replica drew ~3.6 millicores).

Container Insights is listed in a lot of guidance as the first stop; on the customer cluster it
**was not enabled**, so plan for the cgroup fallback rather than assuming it.

If instrumentation is needed, the minimum is three counters: process CPU
(`resource.getrusage`), summed request wall time, and an in-flight gauge.

## Measure across a concurrency sweep, never at one level

Non-negotiable, and the single most important addition to this method. Every important finding
on the customer's agent required at least three load levels; one level would have produced a
plausible and wrong record.

Measured on their agent, session-store fetch p50:

| Concurrency | Store fetch p50 | Per-pod throughput |
|---|---|---|
| 1 | 3.7 ms | — |
| 6 | 4.7 ms | 0.397 turns/s |
| 12 | **2,491 ms** (p90 9,487 ms) | **0.326 turns/s** — throughput *inverts* |

A ~670× degradation, invisible at c=1 and c=6. Any per-turn number taken at low concurrency is
not the production number.

### The cause, and why no code read or Kubernetes metric finds it

Their event loop is clean — every blocking call correctly wrapped in `asyncio.to_thread`, which
both a code review and this method's greps credited them for. But **`asyncio.to_thread` uses the
default executor**, and inside a 2-CPU container `ThreadPoolExecutor()._max_workers` is
`min(32, cpu_count + 4)` = **6**. Twelve concurrent turns queue behind six threads.

`event_loop_blocking: true|false` is therefore the wrong question — a clean loop is necessary
and not sufficient.

### First: which pool is even in play?

The earlier version of this section prescribed one probe and it was **wrong for most services**.
Branch on the handler style:

| Handler | Concurrency is bounded by | Default |
|---|---|---|
| `async def` + `asyncio.to_thread` | asyncio's default `ThreadPoolExecutor` | `min(32, cpu_count + 4)` |
| **plain `def`** (FastAPI/Starlette) | **anyio's capacity limiter** | **40** |
| no web framework (batch, gRPC worker) | whatever the code sets explicitly | read it |

A service with **no `async def` at all** has no event loop to block — record
`event_loop_blocking: not_applicable`, not `false`, because `false` reads as a clean bill of
health for a check that never applied.

```bash
# Which pools exist, and what actually bounds them
kubectl exec -n <ns> <pod> -- python -c "
import os, concurrent.futures as f
print('cpu_count()      ', os.cpu_count(), '  <- HOST cpus, not your cgroup limit')
print('cgroup cpu.max   ', open('/sys/fs/cgroup/cpu.max').read().strip())
print('asyncio executor ', f.ThreadPoolExecutor()._max_workers)
try:
    import anyio.to_thread as a
    print('anyio limiter    ', a.current_default_thread_limiter().total_tokens)
except Exception as e:
    print('anyio limiter     n/a', e)
"
```

**`os.cpu_count()` reports the host's CPUs, not `limits.cpu`.** So the asyncio pool on a
1-CPU-limited pod on a 64-core node is sized 36, not 5 — the derived number is meaningless
without `cpu.max`. Read both.

Record `executor_sized` alongside `event_loop_blocking`. On the one customer service where this
was measured it was the largest defect in the codebase, it is a few lines to fix, and it improves
the current service as much as the migrated one.

### Two more findings that only a sweep produces

- **The rebuild-vs-fetch ratio is not a constant.** This plugin states ~8.5× (rebuild dominates).
  Measured: **17× at c=1**, then it **inverts at c=12** (fetch 2,491 ms vs rebuild 147 ms). It is
  a function of concurrency, so "optimise the rebuild, not the store" is only true unloaded.
- **CPU-based HPA is unusable, with a number.** Under 8-way concurrent load the pod drew
  **179m against a 1000m limit — 17.9%** — while store latency was already collapsing. An HPA
  targeting 70% never fires. This is the concrete version of the claim.

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

## Confidence — two axes, not one

Earlier versions had a single `confidence` keyed to measurement completeness. That is a
**cost**-confidence rubric, and applying it to the recommendation produced actively harmful
output: a `stay` resting on a hard platform ceiling, or a `redesign_first` resting on a
`file:line` credential leak, both had to report `low` — sitting next to a near-certainty and
undermining it. Record both:

**`recommendation_confidence`** — how sure are you of migrate/stay/redesign?
- **high** — a Gate 0 outcome, or a topology and blocker read that measurement cannot overturn.
  Measuring CPU-per-turn does not un-break a 9-hour session against an 8-hour ceiling
- **medium** — the structural read is clear but a Gate 3 answer could move it (shared cluster,
  compliance constraint)
- **low** — structure is ambiguous, or key artifacts are missing from the repo

**`cost_confidence`** — how sure are you of the economics?
- **high** — all four numbers measured on their workload, across a concurrency sweep
- **medium** — partial or extrapolated
- **low / unavailable** — nothing measured. Then say **"the cost verdict is unavailable"**, not
  "probably cheaper", and quote none of this plugin's reference figures as theirs

The two are frequently far apart, and saying so is more useful than averaging them into one
misleading word.

At `low`, say plainly that the cost recommendation is unavailable and name the measurement
that would change it. **A recommendation presented at unearned confidence is the failure mode
that loses the account** — an admitted gap is not.

Record what you could not assess, per practice, with the reason. "I could not assess `AGENTSEC09`
because I have no visibility into their pen-testing" is a better answer than silence.
