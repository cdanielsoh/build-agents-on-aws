# Node A1 — read the application source

Needs S1 = yes **and** the revision to match what is deployed. If the repo is ahead of production,
pin to the running build or drop to `read:cluster` only — see
[read-the-shape.md](read-the-shape.md). Skip most of this file for a declarative platform.

Translate the intent, not the regex. The intent is the same in every language; only the vocabulary
changes. Every search below is deliberately **unrestricted by file type** where the domain words
are language-independent — narrowing to one extension is how a whole domain gets missed on a
service that is not written in it.

## Core shape

```bash
# Image architecture — amd64 means a rebuild on microVMs. Language-independent.
grep -rn 'platform' Dockerfile* ; grep -rniE 'arch|platform' *.yaml 2>/dev/null

# If there is no Dockerfile — they deploy a prebuilt or third-party image — ask the REGISTRY what
# architectures the image publishes. This is the question the gate asks.
IMG=$(kubectl get deploy -n <ns> <name> -o jsonpath='{.spec.template.spec.containers[0].image}')
crane manifest "$IMG" | jq -r '.manifests[]?.platform | "\(.os)/\(.architecture)"'
# no crane? docker/finch works too:
finch manifest inspect "$IMG" | jq -r '.manifests[]?.platform.architecture'

# Session state and its backing store (AGENTREL03) — search the words, not one language's idiom
grep -rniE 'session.?store|session.?id|conversation.?id|thread.?id' .
grep -rniE 'dynamodb|redis|elasticache|memorystore|postgres|mongo|cosmos' .
# In-process caches, which is the topology-B tell
grep -rniE 'lru|ttlcache|_sessions|sessionCache|agent_?cache|sync\.Map|ConcurrentHashMap' .

# Affinity (topology B) — Kubernetes-level, so language-independent
grep -rniE 'stickiness|sessionAffinity|StatefulSet|headless|consistent.?hash' --include='*.yaml' .
```

**Do not read the NODE's architecture to answer the image-architecture gate.** The node's arch is
what the cluster scheduled onto, not what the image supports. Observed failure: nodes amd64, image
an OCI index publishing `linux/arm64`, so the node read returns "amd64, needs a rebuild" when arm64
already ships and the gate passes. The node read answers a different, still useful question — what
is scheduled today — so keep it for that and label it as such:

```bash
kubectl get pod -n <ns> <pod> -o jsonpath='{.spec.nodeName}{"\n"}' \
  | xargs -I{} kubectl get node {} -o jsonpath='{.status.nodeInfo.architecture}{"\n"}'
```

## Concurrency hygiene, by language

The universal question is: *can one slow call stall unrelated in-flight work, and what bounds
parallelism?* The mechanism differs:

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

**NB on evidence:** do **not** pipe a `grep -rn` into a second `grep -n` — the second numbers the
pipe stream, not the file, and the record requires real `file:line`. Use `-A` context and read the
filenames.

**A clean concurrency model is necessary and not sufficient.** Record *what bounds* concurrency and
how you established it, not a boolean — the limit is usually a pool nobody chose, and finding it
takes [concurrency-sweep.md](concurrency-sweep.md).

## The domains most often missed

These are the ones an assessment skips by default, and where the empty results are the finding.
One search per inventory domain.

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

# Evals (AGENTOPS06) — absence limits what a parallel run can claim
ls -d test* eval* 2>/dev/null || true
grep -rlniE 'golden|expected_response|expected_output|regression.?set|eval' .

# Prompt lifecycle (AGENTOPS02) — versioned artifacts, or inline strings edited in place?
grep -rlniE 'system.?prompt|system.?message|instruction' . ; ls -d prompt* 2>/dev/null

# Agent-to-agent (AGENTOPS04, AGENTREL01) — a second topology axis, easy to miss entirely
grep -rlniE 'a2a|agent.?card|supervisor|delegat|handoff|sub.?agent|swarm|graph' .
```

**For every component the manifests deploy, check whether this repo can build it.** A component
running on a pinned tag with no build definition here means a planned patch has no tree to apply
to — which is a finding about the engagement's scope, not just about the code:

```bash
grep -rhoE 'image: *[^ ]+' --include='*.yaml' . | sort -u; ls Dockerfile* */Dockerfile* 2>/dev/null
```

**A positive result is not a working control.** Everything above finds components that *exist*.
Whether they do anything is [sweep-for-dead-controls.md](sweep-for-dead-controls.md), and on two
independent assessments that is where the highest-severity findings were.
