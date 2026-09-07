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

### If there is a running deployment, it outranks the repo

This whole section assumes the repo describes what is running. When both are available and they
disagree, **the cluster is the fact and the repo is a claim.**

`[measured:reference]` on a deployed third-party agent platform: the source tree at `HEAD` served
one API version with one set of resource kinds, while the released chart actually deployed served
an *earlier* version with **differently named** kinds. A manifest written from the source tree was
rejected by the API server outright (`no matches for kind`). Nothing in the repo signalled it.

Same class as the "a module was imported and did not exist, so the image was built from a
different tree" finding below, but failing the other way round — the repo is *newer* than
production, not older, and looks internally consistent, so there is no broken reference to trip
over. Expect it wherever the customer deploys a pinned release of something they also track at
head: their own chart, a vendored dependency, a platform they did not write.

So when a deployment exists, take these from the cluster and not from source:

```bash
kubectl get crd <name> -o jsonpath='{range .spec.versions[*]}{.name} served={.served} storage={.storage}{"\n"}{end}'
kubectl get deploy <name> -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'   # the running tag
kubectl get deploy <name> -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}{"\n"}{end}'
```

Record the divergence itself — a repo ahead of production means the assessment's `file:line`
evidence describes code the customer is not running, which silently invalidates every
`[read:source]` tag in the record.

### First: what is this written in, and is the logic even in a repo?

**Establish the language and the shape before running any search.** Every pattern below is
written for a source tree; two common inputs are not one:

```bash
# What are we actually reading? Do not assume.
git ls-files | sed 's/.*\.//' | sort | uniq -c | sort -rn | head
ls Dockerfile* */Dockerfile* go.mod package.json pom.xml build.gradle* pyproject.toml 2>/dev/null
```

| Shape | Where the answers live |
|---|---|
| Application code (any language) | the repo — the searches below, with the column for that language |
| **Declarative platform** (agent defined as a CRD, config, or DSL; a shared engine executes it) | the **CRs and the platform's own docs**, not application source. `kubectl get <kind> -o yaml` is the read |
| **Managed/third-party agent runtime** | its configuration; the agent logic may not be yours at all |

For the declarative case the whole "read the source" premise weakens: there may be no handler, no
session code and no tool definitions to find, because the platform supplies them. **An empty
search result then means "wrong question", not "gap"** — recording `absent` there is the error.
Read the resource spec and the platform's guarantees instead, and say in the record that the
component is supplied by the platform rather than by the customer.

### Core shape

Translate the intent, not the regex. The intent is the same in every language; only the
vocabulary changes.

```bash
# Image architecture — amd64 means a rebuild. Language-independent.
grep -rn 'platform' Dockerfile* ; grep -rniE 'arch|platform' *.yaml 2>/dev/null

# Session state and its backing store (AGENTREL03) — search the words, not one language's idiom
grep -rniE 'session.?store|session.?id|conversation.?id|thread.?id' .
grep -rniE 'dynamodb|redis|elasticache|memorystore|postgres|mongo|cosmos' .
# In-process caches, which is the topology-B tell
grep -rniE 'lru|ttlcache|_sessions|sessionCache|agent_?cache|sync\.Map|ConcurrentHashMap' .

# Affinity (topology B) — Kubernetes-level, so language-independent
grep -rniE 'stickiness|sessionAffinity|StatefulSet|headless|consistent.?hash' --include='*.yaml' .
```

**Concurrency hygiene, by language.** The universal question is: *can one slow call stall
unrelated in-flight work, and what bounds parallelism?* The mechanism differs:

| Language | What to look for | Failure mode |
|---|---|---|
| Python | blocking calls inside `async def` not wrapped in `to_thread`/`run_in_executor`; then the pool that bounds it | a blocked event loop, or a small default thread pool |
| Go | shared maps without a mutex; unbounded goroutines; a missing `context` deadline | data races, unbounded fan-out |
| Node/TypeScript | sync I/O (`*Sync`), CPU-bound work on the main loop, no worker threads | one request stalls all |
| Java/JVM | a fixed thread pool sized smaller than concurrency; blocking inside a reactive chain | queueing invisible at low load |

```bash
# Python
grep -rn -A15 'async def' --include='*.py' . | grep -E '\.get_item|\.put_item|requests\.|urlopen'
grep -rn 'asyncio.to_thread\|run_in_executor' --include='*.py' .
# Go — shared state and deadlines
grep -rnE 'map\[string\]|sync\.(Mutex|RWMutex|Map)|go func\(' --include='*.go' .
grep -rn 'context.WithTimeout\|context.Background()' --include='*.go' .
# Node / TypeScript
grep -rnE 'readFileSync|execSync|await .*forEach|new Worker' --include='*.ts' --include='*.js' .
```

**NB on evidence:** do **not** pipe a `grep -rn` into a second `grep -n` — the second numbers
the pipe stream, not the file, and the record requires real `file:line`. Use `-A` context and
read the filenames.

Whatever the language, record **what bounds concurrency** and how you established it. A clean
concurrency model is necessary and not sufficient — see the sweep section below, where the limit
turned out to be a pool size nobody had set.

### The domains most often missed

These are the ones an assessment skips by default, and where the empty results are the
finding. One search per inventory domain, **unrestricted by file type** — the domain words are
the same in every language, and narrowing to one extension is how a whole domain gets missed on
a service that is not written in it.

```bash
# Outbound auth (AGENTSEC03) — hand-rolled OAuth is often the largest deletable component
grep -rniE 'client_credentials|refresh_token|code_verifier|pkce|authorization_code' .
grep -rniE 'user_tokens|oauth_tokens|token_store|token_vault|credential.?provider' .

# Inbound auth (AGENTSEC03) — and whether it is SigV4, which is the one breaking case
grep -rniE 'authorizer|oidc|jwt|verify_token|introspect|cognito|entra|okta|auth0' .
grep -rniE 'sigv4|sign_request|signature.?v4|aws.?sign' .

# Multi-tenancy (AGENTPERF07, AGENTSEC01)
grep -rniE 'tenant|org_id|organization_id|workspace_id|account_id|namespace' .

# Long-term memory and RAG (AGENTPERF03) — distinct from session state
grep -rniE 'embedding|vector|opensearch|pgvector|pinecone|qdrant|weaviate|knowledge.?base|rerank' .

# Guardrails, PII, HITL (AGENTSEC04, AGENTSEC07, AGENTSEC08)
grep -rniE 'guardrail|redact|pii|moderat|approval|confirm|interrupt|human.?in.?the.?loop' .

# Spend ceilings (AGENTCOST01) and idempotency (AGENTREL06)
grep -rniE 'max_tool_calls|max_iterations|token.?budget|rate.?limit|cancel_tool|intervention' .
grep -rniE 'idempot|dedup|request_id|correlation_id' .

# MCP servers the customer runs themselves (AGENTOPS04)
grep -rlniE 'streamable.?http|fastmcp|mcp.?server|/mcp' .
# Then: does each have a build definition HERE? Observed on a real repo — MCP servers ran as
# separate Deployments on pinned tags, and the only Dockerfile copied just the main service, so
# a planned patch had no tree to apply to. Diff manifest images against what you can build:
grep -rhoE 'image: *[^ ]+' --include='*.yaml' . | sort -u; ls Dockerfile* */Dockerfile* 2>/dev/null

# Evals (AGENTOPS06) — absence limits what a parallel run can claim
ls -d test* eval* 2>/dev/null || true
grep -rlniE 'golden|expected_response|expected_output|regression.?set|eval' .

# Prompt lifecycle (AGENTOPS02) — versioned artifacts, or inline strings edited in place?
grep -rlniE 'system.?prompt|system.?message|instruction' . ; ls -d prompt* 2>/dev/null

# Agent-to-agent (AGENTOPS04, AGENTREL01) — a second topology axis, easy to miss entirely
grep -rlniE 'a2a|agent.?card|supervisor|delegat|handoff|sub.?agent|swarm|graph' .
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

So sweep for controls that exist and do nothing. Four checks; the *questions* are
language-independent even though the syntax to answer them is not.

**1. Symbols whose name claims a safety, tenancy or approval role — then count call sites.**
Zero call sites beyond the definition is the finding.

```bash
# Match the naming, not one language's keyword. Covers def/func/function/public *.
grep -rnE '(def|func|function|fn|sub|public|private|protected)[ ,a-zA-Z<>\[\]*]*\b[a-zA-Z_]*(approv|redact|guard|sanitiz|validat|authori[sz]|scope|tenant|budget|limit|check)[a-zA-Z_]*\b' .
grep -rn '<symbol>' . | grep -vE '(def|func|function) +<symbol>'   # per hit
```

**2. Declared tool parameters never referenced in the body.** Read each tool definition and check
every declared parameter is used. Find them by however this stack declares a tool — a decorator,
a struct tag, a registration call, a JSON schema, a CR field:

```bash
grep -rlniE '@tool|tool\(|registerTool|addTool|tools:|inputSchema|parameters' .
```

**3. Constants and config keys that name things, and whether those things exist.** Cross-check
every named tool, model, role or flag against something that actually defines it.

**4. Does the tree even build, and is anything referenced but absent?** Use the stack's own
checker — `python -m compileall`, `go build ./...`, `tsc --noEmit`, `mvn -q compile`. On three of
five assessed services something was imported and did not exist, which meant **the image was
built from a different tree than the one under version control**. That single fact blocked
inbound-auth classification, flush cadence and concurrency hygiene at once — one cause, many
`unknown`s, and it is worth finding in the first ten minutes.

For a **declarative platform** the equivalent of all four is to diff intent against effect: what
the resource *declares* versus what the controller actually created, and whether the tools or
guards it names resolve to anything.

```bash
kubectl get <kind> <name> -o yaml            # declared
kubectl get deploy,sa,svc,cm -l <selector>   # what actually exists
kubectl get <kind> <name> -o jsonpath='{.status}'   # the controller's own verdict — read it
```

## Reading the cluster — Auto Mode's defaults are not what a chart expects

Two traps, and they share a shape: **an EKS Auto Mode default that is absent or narrower than
assumed, surfacing as an error that names the symptom rather than the cause.** Both cost real
diagnostic time on deployed clusters, and both are one `kubectl get` away.

| Assumed | Actual on Auto Mode | Presents as |
|---|---|---|
| a default StorageClass exists | **none is marked default.** The only class may be `gp2` on the *legacy in-tree* `kubernetes.io/aws-ebs` provisioner, which no longer exists in 1.34 | a PVC `Pending` forever, and whatever depends on it crash-looping. Observed: `database migration failed ... connection refused` — the dependent component's error, three steps from the cause |
| the built-in NodePool schedules anything | `general-purpose` is hardcoded **amd64** | an arm64 pod stuck `Pending`, or `exec format error` read as an application crash |

```bash
kubectl get sc                                            # is ANY class annotated default?
kubectl get sc <name> -o jsonpath='{.metadata.annotations}'
kubectl get pvc -A --field-selector=status.phase==Pending
```

The generalizable rule: **when a component crash-loops on a connection to another component,
check the other component's scheduling before reading either one's code.** A stateful dependency
that never got a volume looks exactly like a misconfigured connection string.

Relevant to an assessment because a customer agent with a PVC — Postgres, a vector store, a
checkpoint volume — has a storage dependency that does **not** transfer to AgentCore, and is
worth recording as its own inventory row rather than folded into "the session store".

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

### The cause, and why neither a code read nor a Kubernetes metric finds it

**The general rule: concurrency is bounded by a pool nobody chose, sized from a number that is
wrong inside a container.** Every runtime has at least one such pool. It is invisible in code
review because the code is *correct*, and invisible in cluster metrics because the pod is not
short of CPU — it is short of pool slots, and waiting is not utilization.

The specific instance measured here `[measured:customer]`: the service correctly offloaded every
blocking call to a thread pool, which both a code review and the searches above credited it for.
But the pool it offloaded to was the *default* one, sized `min(32, cpu_count + 4)` — and inside a
2-CPU container that is **6**. Twelve concurrent turns queued behind six threads.

So **"is the concurrency model clean" is the wrong question.** Clean is necessary and not
sufficient. The question is *what integer bounds parallelism, where did that integer come from,
and does it exceed peak concurrency.*

### Find the binding limit for this runtime

| Runtime | The pool that usually binds | Default, and where it comes from |
|---|---|---|
| Python, async handlers offloading work | the default thread-pool executor | `min(32, cpu_count + 4)` |
| Python, **sync** handlers under an ASGI server | the framework's capacity limiter | often **40** — a different number from the above, so identify the handler style first |
| Go | rarely a pool; `GOMAXPROCS`, plus client connection pools and any semaphore | `GOMAXPROCS` from host cores unless set |
| Node / TypeScript | libuv thread pool; HTTP agent max sockets | `UV_THREADPOOL_SIZE` **4** |
| JVM | the servlet/reactive worker pool, and the DB connection pool | framework-specific, usually explicit |
| Any | **DB / HTTP client connection pool**, which binds before the thread pool on I/O-heavy agents | library default, typically 10 |

**The universal trap: pool sizes derived from host CPU count, not from the cgroup limit.** A
1-CPU-limited pod on a 64-core node derives 36, not 5. Read both numbers, always:

```bash
# Language-independent: what the container is actually limited to
kubectl exec -n <ns> <pod> -- sh -c 'cat /sys/fs/cgroup/cpu.max; nproc'
```

Then ask the runtime what it thinks. Python example — translate for the stack in front of you
(`runtime.NumCPU()`/`GOMAXPROCS(0)` in Go, `UV_THREADPOOL_SIZE` and `os.cpus()` in Node,
`availableProcessors()` on the JVM):

```bash
kubectl exec -n <ns> <pod> -- python -c "
import os, concurrent.futures as f
print('runtime cpu count', os.cpu_count(), ' <- HOST cpus, not your cgroup limit')
print('default pool     ', f.ThreadPoolExecutor()._max_workers)
try:
    import anyio.to_thread as a
    print('sync-handler cap ', a.current_default_thread_limiter().total_tokens)
except Exception as e:
    print('sync-handler cap  n/a', e)
"
```

Record **what bounds concurrency and the number**, not a boolean. Where no such mechanism applies
— no event loop, no worker pool, a platform that runs each request elsewhere — record
`not_applicable` rather than `false`, because `false` reads as a clean bill of health for a check
that never ran. On the one service where this was measured it was the largest defect present, it
was a few lines to fix, and it improves the current service as much as the migrated one.

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
