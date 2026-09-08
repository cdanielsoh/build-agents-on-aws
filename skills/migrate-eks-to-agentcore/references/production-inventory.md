# Production inventory — question-level triage against the Agentic AI Lens

The assessment instrument. It walks the **41 questions** of the
[AWS Well-Architected Agentic AI Lens](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentic-ai-lens.html)
(June 2026), adding two columns the Lens does not: **how to detect it in the customer's
code**, and **the migration verdict**.

## Contents

1. [How to fill a row — read this before walking the questions](#how-to-fill-a-row--read-this-before-walking-the-questions)
2. [This is a triage, not a Well-Architected review](#this-is-a-triage-not-a-well-architected-review)
3. [Provenance](#provenance)
4. [Two rules](#two-rules)
5. [Prioritising — not all 41 decide a migration](#prioritising--not-all-41-decide-a-migration)
6. [Operational excellence (AGENTOPS)](#operational-excellence-agentops)
7. [Security (AGENTSEC)](#security-agentsec)
8. [Reliability (AGENTREL)](#reliability-agentrel)
9. [Performance efficiency (AGENTPERF)](#performance-efficiency-agentperf)
10. [Cost optimization (AGENTCOST)](#cost-optimization-agentcost)
11. [Sustainability (AGENTSUS)](#sustainability-agentsus)
12. [§Memory (AGENTREL03 + AGENTSEC01 + AGENTCOST03) — two tiers, opposite verdicts](#memory-agentrel03--agentsec01--agentcost03--two-tiers-opposite-verdicts)
13. [§Tools and MCP (AGENTOPS04 + AGENTSEC02 + AGENTPERF06)](#tools-and-mcp-agentops04--agentsec02--agentperf06)
14. [Three rows that need discussion, not a verdict](#three-rows-that-need-discussion-not-a-verdict)
15. [Fix before migrating](#fix-before-migrating)
16. [Sequencing the deletions](#sequencing-the-deletions)
17. [Reporting](#reporting)

## How to fill a row — read this before walking the questions

**You do not fill rows. You write receipts, and `findings.yml` is generated from them.** Walking a
question produces one or two `lens_plan.py record` calls, made *at the moment you observe something*
rather than at write-up time:

```bash
lens_plan.py record --question AGENTSEC02 --component tool_allowlist \
    --state present_but_ineffective --ineffective-because partially_covers \
    --defect-owner customer \
    --tag read:source --evidence "tools/registry.py:88 — allowlist is client-side" \
    --source "grep -rn ALLOWED_TOOLS tools/"

lens_plan.py record --remedy AGENTSEC02 --component tool_allowlist \
    --closed-by platform_config --remedy-verified false --requires-runtime-move false \
    --tag read:cluster --evidence "chart/values.yaml:210 authz.serverSide unset" \
    --source "helm get values <release>"
```

| Field | Answers | Note |
|---|---|---|
| `state` | does the control exist, and does it work? | `platform_provides` and `available_unconfigured` exist for mechanisms **the customer neither built nor switched on** — do not record an upstream success as their failure |
| `ineffective_because` | *why* it does not work | `never_invoked` is the severe class: exists, reads as present in review, zero call sites. **Required** whenever `state` is `present_but_ineffective` — `record` rejects the receipt without it |
| `defect_owner` | whose bug is it — `customer`, `platform`, `operator_config`? | required when a **platform-supplied** mechanism is ineffective. Without it the record aims the fix at the wrong people |
| `--component` | which of several rows sharing this practice | This is how "41 questions, 66 rows" stops being a coverage dispute: both numbers are now computed from the same receipts |
| `closed_by` | what closes it — **a separate `remedy` receipt** | **Non-AgentCore answers first** — `platform_config`, `customer_code`, `cluster_config`, `iam_policy`, `upstream_contribution`. Reaching for a component to make a row look productive is the failure mode |
| `remedy_verified` | did you confirm the field takes effect? | on the remedy receipt, because the remedy is its own observation with its own evidence |
| `requires_runtime_move` | does acting on it need a replatform? | `false` for almost everything. These are what the customer can do this quarter, and they lead the report |

Three rules that follow, and that the tables below cannot express on their own:

- **An empty detection result may mean the question does not apply to this stack**, not that the
  control is absent. Recording `absent` there manufactures a finding. Use `not_applicable` or
  `unknown` and say which. `status` cross-checks this for you: a question recorded `absent` when
  `resolve` says you had no access to answer it gets reported as divergence.
- **A remedy field the platform ships may itself be inert.** Observed: a field the CRD accepted,
  validated, and the runtime silently ignored — reported only in a status condition. So
  `closed_by: platform_config` is a claim to verify, not a conclusion; record
  `--remedy-verified false` if you could not confirm the field takes effect on the runtime they
  actually run.
- **`Verdict` below is this file's prior opinion, not a field you record.** It used to be a per-row
  field, and half its values duplicated `state` (`gap` ≈ `absent`, `correctly-absent` ≈
  `absent_by_design`) while the other half — migrate / keep / delete / regress / stay — was
  migration-shape opinion that belongs on a **suggestion**, where it has to carry a cost and a
  `does_not_fix`. Read the column; do not copy it into the record.

`record-schema.yaml` is the authoritative field list, and `record` validates against it — so a value
this file describes but the schema does not accept cannot be written. If the two disagree, the schema
is authoritative and this file has drifted; say so.

## This is a triage, not a Well-Architected review

Read this before quoting coverage to anyone.

The Lens contains **41 questions and 150 best practices** — its
[Appendix A](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/appendix-a.html)
summary table reads `Total 41 150`. This file covers the **41 questions**. It does **not**
cover the 150 named best practices (`AGENTSEC03-BP01` … `AGENTSUS03-BP04`), the intermediate
focus-area layer, or the per-BP **Risk ratings** AWS publishes (90 High / 59 Medium / 1 Low).

So:

- **Say "41 of 41 Lens questions", never "41 of 41 practices."** The second is false and a
  customer who has run a Well-Architected review will catch it in a minute — which, by this
  skill's own thesis, discredits everything else you told them.
- **A migration triage is not a Well-Architected review.** If the customer wants the review,
  import the published lens JSON into AWS WA Tool:
  [`aws-samples/sample-well-architected-custom-lens`](https://github.com/aws-samples/sample-well-architected-custom-lens/blob/main/agentic-ai-lens/agentic-ai-lens.json).
  Offer that as the follow-on; do not substitute this for it.
- **The Lens does have its own detection guidance** — each question page carries "Common
  issues to watch for" and a five-level maturity rubric. The Detect column here is a
  one-line shortcut for a code read, not a replacement. When a question matters to the
  decision, open its page.

**Verdicts** — **Migrate** (moves unchanged) · **Migrate+** (moves, needs work) ·
**Delete** (platform replaces it) · **Keep** (yours on either platform) ·
**Regress** (worse on AgentCore — size the loss) · **Stay** (should not move) ·
**Gap** (absent, and it should not be) · **Correctly-absent** (absent by design — say so)

`Correctly-absent` matters: forcing a deliberate absence into `Gap` manufactures a finding, and
forcing it into `Keep` hides that it was considered. A single-turn service has no session store
and needs none; a graph with no per-user data needs no retrieval ACL. **In a receipt that
distinction is `state: absent_by_design` versus `state: absent`** — same judgement, recorded where
it can be argued with.

`Regress` and `Stay` exist because without them the instrument can only ever conclude
"migrate or neutral." If you never record one, suspect the instrument rather than the
platform.

## Provenance

- **Questions are verbatim** from Appendix A, so a row can be matched to the source without
  interpretation. Each ID links to its own Lens page.
- **All 41 questions are present**, including ones that rarely apply. A question you skip is
  recorded `unknown` with a reason rather than dropped.
- **Count check:** AGENTOPS 7 + AGENTSEC 9 + AGENTREL 8 + AGENTPERF 7 + AGENTCOST 7 +
  AGENTSUS 3 = **41 questions**. Re-derive from Appendix A if a Lens revision changes it.
- **The verdict column is this plugin's opinion, not AWS's.** The Lens asks the question; the
  migrate/keep/delete call is ours, and several are `[reasoned]` rather than `[measured]`.
  Say which is which when a customer pushes back.
- **Use the full `AGENT*` IDs.** Bare `REL03`/`SEC01` are live IDs in the *core* Well-Architected
  Framework for entirely different questions (`REL 3` is Service Quotas), so a customer with a
  review on file will map them wrong.

## Two rules

**1. Absence is the most valuable finding.** "No evals", "no spend ceiling", "no audit
trail", "no tenant boundary" are findings about the *current* system, true whether or not
they migrate. Report them first. An assessment that surfaces real gaps has earned a
migration conversation; one that produces only a cost table has not.

**2. A gap is not a migration blocker.** Most gaps here pre-date any migration and survive
it. Flag, size, and say plainly that migrating neither creates nor fixes them.

## Prioritising — not all 41 decide a migration

For a first pass, the practices that actually move the decision are marked **★** below:
AGENTSEC01, AGENTSEC02, AGENTSEC03, AGENTREL03, AGENTPERF07, AGENTCOST03, AGENTCOST05, AGENTOPS04, AGENTOPS06. They cover session
and memory state, tool access, identity, multi-tenancy, memory cost, cost attribution, tool
management and evaluation — which is where the verdicts and the risks concentrate.

---

## Operational excellence (AGENTOPS)

| # | Lens question | Detect in their system | Verdict |
|---|---|---|---|
| **[AGENTOPS01](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentops01.html)** | How do you establish operational practices for agentic AI systems? | Runbooks, on-call, defined agent scope and success criteria | **Keep** |
| **[AGENTOPS02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentops02.html)** | How do you manage prompt and configuration lifecycle? | Are prompts versioned, reviewed, revertible? Or inline strings edited in place? | **Gap** usually. **Keep** — no platform versions prompts for you |
| **[AGENTOPS03](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentops03.html)** | How do you manage agent lifecycle and deployment processes? | CI/CD, image build, rollout strategy | **Migrate+** — ARM64 + runtime versions/endpoints replace rolling updates |
| **[AGENTOPS04](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentops04.html)** ★ | How do you establish tool integration and management practices? | Where tools live: in-process, self-hosted MCP servers, Lambdas. How many. Who owns them | **Migrate+** → Gateway consolidates. See §Tools below |
| **[AGENTOPS05](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentops05.html)** | How do you implement comprehensive observability and monitoring for agentic systems? | OTel spans, dashboards, alarms — and is anyone paged? | **Migrate+** — ADOT sidecar. **Inverted config rule**, see below |
| **[AGENTOPS06](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentops06.html)** ★ | How do you implement testing, evaluation, and validation frameworks? | An eval suite? A golden dataset? A regression gate in CI? | **Migrate** (transfers). If **Gap**, state the limitation — see below |
| **[AGENTOPS07](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentops07.html)** | How do you establish operational recovery and consumption monitoring? | Token/spend dashboards, quota alarms | **Gap** usually |

**AGENTOPS05, the inverted rule:** on EKS nothing configures OTel exporters, so the app must. On
Runtime the ADOT sidecar configures them and setting `OTEL_*` **silently breaks** delivery.
Easy to get backwards when one image serves both. → `observability.md`

**AGENTOPS06 — no golden set is a stated limitation, not a blocker.** Without one you cannot
*prove* the migrated agent behaves identically, so say exactly that and let the customer
decide whether it matters: "we can compare latency, cost and error rate, but we cannot
demonstrate equivalent answer quality, because there is no reference set to compare against."

Plenty of teams migrate on spot-checks and are fine. Building a golden set is worth offering
as Phase 0 value, not imposing as a gate — and pretending otherwise turns an honest caveat
into a manufactured dependency, which is exactly the credibility problem this skill exists to
avoid.

AgentCore Evaluations additionally needs CloudWatch Transaction Search enabled — an
account-level setting outside any stack; without it evaluations silently find no sessions.
→ `evaluations.md`, `strands-agent-design/testing-with-evals.md`

---

## Security (AGENTSEC)

The pillar I most under-weighted. Nine practices, and the customer's answer to AGENTSEC03 alone
can reshape the plan.

| # | Lens question | Detect | Verdict |
|---|---|---|---|
| **[AGENTSEC01](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec01.html)** ★ | How do you secure agentic memory and securely manage state between agents? | Can tenant A's session reach tenant B's context? Is long-term memory partitioned? | **Delete** (session isolation → microVM). **Keep** memory partitioning |
| **[AGENTSEC02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec02.html)** ★ | How do you control and secure agent tool usage? | Can any authenticated caller invoke every tool, including destructive ones? | **Gap** usually → Cedar at the Gateway |
| **[AGENTSEC03](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec03.html)** ★ | How do you manage agent identities, permissions, and prevent privilege escalation? | Inbound auth; outbound 2LO; outbound 3LO; token propagation; workload identity | **Migrate+.** Breaking **only** for SigV4 callers — see §Identity before saying the word |
| **[AGENTSEC04](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04.html)** | How do you support agent goal alignment and prevent manipulation? | Prompt-injection defences. Is authorization *inside* the model's reasoning? | **Migrate+** — Cedar moves it outside. Guardrails for content |
| **[AGENTSEC05](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec05.html)** | How do you implement observability and prevent repudiation? | Can you reconstruct what the agent did, for whom, on what data? | **Gap** usually. Cedar decisions log the deciding policy |
| **[AGENTSEC06](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec06.html)** | How do you secure multi-agent orchestration and coordination? | Only if multi-agent: does user identity survive an agent hop? | **Migrate+** → A2A + workload/user binding |
| **[AGENTSEC07](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec07.html)** | How do you protect human oversight from manipulation and detect rogue agents? | HITL approval on destructive actions; anomaly detection | **Keep** — interventions are agent-side |
| **[AGENTSEC08](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec08.html)** | How do you validate and secure agent inputs and outputs? | Input length caps, injection screening, PII redaction, blocked topics | **Gap** usually → Bedrock Guardrails |
| **[AGENTSEC09](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec09.html)** | How do you perform vulnerability scanning and penetration testing for agentic AI systems? | Red-teamed? Image scanning? **GuardDuty EKS Runtime Monitoring or Falco?** | **Keep** — but **Regress** if they run node-level runtime threat detection: there is no equivalent on a managed microVM |

### §Identity (AGENTSEC03) — four independent problems, routinely conflated

| Sub-component | Detect | Verdict |
|---|---|---|
| **Inbound auth** | which of four? see the split below | **depends — only one case is breaking** |
| **Outbound 2LO** (agent as itself) | client_credentials, static secrets, hand-rolled refresh | **Migrate+** → credential provider |
| **Outbound 3LO** (agent as the user) | per-user OAuth tokens, a `user_tokens` table, PKCE/state/callback code | **Migrate+** → token vault. Where it exists this is often the largest single deletion — but on the one customer service measured, **there was none at all**. Check before pitching it |
| **Token propagation** | is the caller's identity carried to the tool, or does the tool trust the agent? | **Keep**, or move to a REQUEST interceptor |
| **Workload identity** | a stable agent identity distinct from its IAM role? | **Delete** — Runtime issues one |

**Establish which of the four cases before saying "breaking" — one is, three are not.** An
earlier version of this table gave all four a single `BREAKING` verdict, which made the
instrument produce the error [constraints.md](constraints.md) calls "simply wrong to the
customer's face": the cheapest of the four cases reported as the most expensive.

| What they have today | Verdict |
|---|---|
| **Nothing** (internal ClusterIP, no authorizer) | **additive greenfield** — there is no cutover, you are adding auth that did not exist. The *easiest* case |
| **In-app JWT validation** already | **Migrate+, not breaking** — the same token, validated by the platform instead of in-process. Delete their verification code |
| **ALB OIDC / API GW authorizer** | **Migrate+** — the authorizer moves; callers keep sending the same token. **Read what the app does with that token before calling this a relocation** — see below |
| **SigV4 callers** | **BREAKING**, and only this one |

The breaking case, precisely: `CUSTOM_JWT` *excludes* SigV4 — a SigV4 caller gets 403 once a JWT
authorizer is configured `[measured]`. Callers signing with SigV4 (typical service-to-service on
EKS with Pod Identity) mean a coordinated cutover of every caller, or **two runtimes in
parallel**. Phase 1, not Phase 3.

**And in the proxy case, check whether the app validates the token at all — because
`CUSTOM_JWT` is often the *first* real validation in the chain, which is a much stronger argument
than "the authorizer moves."** Read the auth middleware; do not infer from the mode's name.
Observed on a real platform, at `file:line`. Its auth mode was **named** for the fact that a proxy
in front had already validated the token — and it decoded the payload without verifying anything,
with a source comment stating that validation happened upstream. **No signature, issuer, audience
or expiry check** existed anywhere in the release. Identity was additionally readable from a
request **query parameter**, taking precedence over the token's own subject claim. Two compounding
facts: the sub-agents ran **no auth middleware at all** on their own ports, and the proxy named as
the root of trust was configured against a backend address that did not resolve — so it reported
healthy while having served zero user requests.

The generalizable check: **a mode named for an upstream guarantee is a claim about deployment, not
a control.** Verify the guarantee holds — that the proxy is genuinely in the path, and that
nothing else can reach the service directly — rather than trusting the name.

That inverts the pitch. Not "we relocate your authorizer" but **"today nothing verifies these
tokens; the platform would."** It is also a live finding to report before any migration framing,
per rule 4 — and the three cheap fixes (a NetworkPolicy, auth on the sub-agents, correcting the
proxy's upstream) need no AgentCore at all, which is what makes the rest credible.

**3LO is where the biggest win usually hides.** Hand-rolled per-user OAuth — PKCE, state,
code exchange, refresh, encrypted token storage, revocation — is typically hundreds of lines
and a recurring security-review finding. Count the lines; it is often the strongest single
argument for migrating.

**Row-level authorization stays yours.** Runtime identity is per-workload, not per-user, so
no IAM policy can express "return only user X's rows." Also watch the
**identity-in-tool-signature anti-pattern**: a tool taking `user_id`/`tenant_id` as a
parameter is prompt-injectable — identity belongs in a closure or `agent.state`.

→ `identity.md`, `security.md`, `policy.md`, `strands-agent-design/security-patterns.md`

---

## Reliability (AGENTREL)

| # | Lens question | Detect | Verdict |
|---|---|---|---|
| **[AGENTREL01](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentrel01.html)** | How do I develop reliable agentic systems? | Durable messaging, fault isolation | **Keep** |
| **[AGENTREL02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentrel02.html)** | How do you develop agentic systems that reliably execute tasks with predictable outcomes? | Atomic tasks, least-privilege tools, clear instructions | **Keep** — prompt/tool design |
| **[AGENTREL03](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentrel03.html)** ★ | How do you support agent memory and state remaining reliably accessible throughout the agent lifecycle? | Session topology; flush cadence; concurrent-turn safety | **Delete** the store. See §Memory |
| **[AGENTREL04](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentrel04.html)** | How do you orchestrate multi-agent systems to reliably execute tasks? | Only if multi-agent: arbiter, capability taxonomy, fallbacks | **Migrate** — framework-level |
| **[AGENTREL05](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentrel05.html)** | How do you implement reliable agent cognition that accesses the right data at the right time? | Retrieval correctness, grounding rules, freshness | **Keep** |
| **[AGENTREL06](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentrel06.html)** | How do agents integrate effectively with existing systems without impacting the reliability of established processes? | Retries, **idempotency on write tools**, circuit breakers, timeouts | **Keep** — agents retry; are writes safe? |
| **[AGENTREL07](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentrel07.html)** | How do fault tolerant agent systems recover? | What happens on Bedrock throttling? Tool failure? | **Keep** |
| **[AGENTREL08](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentrel08.html)** | How do agents determine when and where graceful degradation is appropriate? | Does one tool failure fail the whole turn? | **Keep** |

AGENTREL06's idempotency question is worth asking explicitly. An agent that retries a
non-idempotent write tool double-books, double-refunds, or double-sends. Nothing in either
platform prevents it.

---

## Performance efficiency (AGENTPERF)

| # | Lens question | Detect | Verdict |
|---|---|---|---|
| **[AGENTPERF01](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentperf01.html)** | How do you plan strategically for agent performance and establish measurement practices? | Are p50/p95 turn latency and TTFT tracked? | **Keep** — and you need this for Gate 2 |
| **[AGENTPERF02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentperf02.html)** | How do you optimize core agent processing and cognitive pipelines? | Tool-call counts per turn, model choice, redundant hops | **Keep** |
| **[AGENTPERF03](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentperf03.html)** | How do you optimize memory management, context windows, and retrieval-augmented generation? | Vector store, embeddings, chunking, reindexing, retrieval ACLs | **Keep in place.** See §Knowledge |
| **[AGENTPERF04](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentperf04.html)** | How do you achieve efficient communication and protocol usage across agent interactions? | MCP round trips, payload sizes, streaming | **Migrate+** |
| **[AGENTPERF05](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentperf05.html)** | How do you optimize workflow orchestration and multi-agent collaboration for performance? | Only if multi-agent: coordination overhead | **Migrate** |
| **[AGENTPERF06](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentperf06.html)** | How do you optimize tool integrations and framework usage for agent performance? | Tool catalogue size in one context; result sizes unbounded? | **Migrate+** → tool search / progressive disclosure |
| **[AGENTPERF07](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentperf07.html)** ★ | How do you manage multitenant performance isolation and optimize resource utilization? | Per-tenant rate limits; noisy-neighbour control | **Migrate+** → Gateway rate limits |

### §Knowledge and retrieval (AGENTPERF03) — the network forcing function

| Sub-component | Detect | Verdict |
|---|---|---|
| Vector store | OpenSearch, pgvector, Pinecone, Bedrock KB | **Keep in place** |
| Embedding/ingestion pipeline | chunking, re-embedding on change | **Keep** |
| Retrieval tool | a `search`/`retrieve` tool | **Migrate** unchanged |
| Structured knowledge store | graph DB, relational, internal API | **Keep in place** |
| **Retrieval-scoped ACLs** | does retrieval respect the caller's permissions? | **Keep** — common, serious gap |

**If the knowledge store is VPC-resident with no public endpoint, it decides the network
workstream.** On EKS the pod already has an ENI in the VPC. On AgentCore it forces
`networkMode = "VPC"`, cascading into a set of interface endpoints plus an S3 **gateway**
endpoint for image layers. **The list is not reproduced here** — it grows, and the copies that
used to sit in this file and in `/plan-agentcore-migration` both went stale and omitted
`bedrock-agent-runtime`. A missing endpoint does not fail the deploy — it hangs the agent at
runtime with nothing in the logs naming it. →
[vpc-and-network-isolation.md](../../deploy-on-agentcore/references/vpc-and-network-isolation.md)

Retrieval ACLs deserve a direct question: a store that returns any chunk to any caller is a
leak path no amount of *tool* authorization fixes, because filtering must happen inside
retrieval.

---

## Cost optimization (AGENTCOST)

| # | Lens question | Detect | Verdict |
|---|---|---|---|
| **[AGENTCOST01](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost01.html)** | How do you optimize agent reasoning and execution costs? | Tool-call budget? Token ceiling per request? | **Keep** — **no platform provides this** |
| **[AGENTCOST02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost02.html)** | How do you optimize agent model invocation and token consumption costs? | Prompt caching, model right-sizing. **Usually dominates the bill** | **Keep** — identical on both platforms |
| **[AGENTCOST03](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost03.html)** ★ | How do you manage agent memory and state costs efficiently? | Session lifetime, idle timeout, is `StopRuntimeSession` called? | **Migrate+** — becomes the top lever |
| **[AGENTCOST04](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost04.html)** | How do you optimize agent tool invocation? | Redundant tool calls, result caching | **Keep** |
| **[AGENTCOST05](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost05.html)** ★ | How do you implement cost attribution? | Can spend be attributed per tenant/user/agent? | **Gap** usually |
| **[AGENTCOST06](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost06.html)** | How do you optimize agent discovery registry and deployment costs? | Registry usage, image storage | **Migrate** |
| **[AGENTCOST07](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentcost07.html)** | How do you establish agent cost governance and continuous optimization? | Budgets, anomaly alerts, review cadence | **Gap** usually |

**AGENTCOST01 survives everything.** Neither Kubernetes nor AgentCore bounds a runaway reasoning
loop. An absent tool-call or token budget is a live production risk to raise regardless of
platform.

**AGENTCOST02 is excluded from the migration comparison on purpose** — token cost is identical on
both hosts, so it does not change the verdict, but it usually dominates the customer's actual
bill. Say so, or compute figures get read as a total they are not.

**AGENTCOST03 changes character on migration.** On EKS a session TTL is housekeeping; on AgentCore
memory bills for the whole session including idle, so conversation-end detection becomes the
dominant cost lever. → `cost-and-billing.md`

---

## Sustainability (AGENTSUS)

| # | Lens question | Detect | Verdict |
|---|---|---|---|
| **[AGENTSUS01](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus01.html)** | How do you build sustainable and repeatable frameworks for managing compute, memory, and other shareable agent resources? | Resource reuse, right-sizing, pooling | **Migrate+**, with a caveat — serverless removes *node* idle, but an unstopped session bills memory through its idle tail (92% of the default bill). It relocates idle cost rather than removing it |
| **[AGENTSUS02](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02.html)** | How do I establish sustainable frameworks for agent dependencies? | Dependency pinning, upgrade cadence, SDK drift | **Keep** — agent SDKs move fast in minor versions whatever the framework, and an unpinned build makes every later phase uncontrolled |
| **[AGENTSUS03](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus03.html)** | How do I establish durable patterns for agent interactions with users and business processes? | Is the agent understood by more than its author? Documented? | **Keep** |

AGENTSUS03 is easy to skip and worth asking: a working agent nobody but its author understands is
a migration risk, because nobody can say whether the migrated version behaves the same.

---

## §Memory (AGENTREL03 + AGENTSEC01 + AGENTCOST03) — two tiers, opposite verdicts

Conflating the tiers is the most common inventory error.

### Short-term (within a conversation)

| Component | Verdict |
|---|---|
| Conversation history / session store | **Delete** — the microVM holds it `[measured]`. **Two size ceilings, and AgentCore's is the tighter one:** `Message size` = **9 KB, non-adjustable** (`L-1D35AE05`) per Memory event, versus DynamoDB's 400 KB item. 44× smaller, on the target platform — so a service storing large tool results in history hits it. **Ask for the item-size ceiling:** measured growth was ~7.6 KB/turn against DynamoDB's 400 KB item cap, i.e. a hard **~50-turn conversation limit** (~12 for tool-heavy turns). Many topology-A services have this and do not know it |
| History compaction, conversation manager | **Keep** — context pressure is not platform-specific |
| Flush cadence | **Regress** if they flush less often than every turn — `batch_size=1` is forced, so write volume and cost go up. **Neutral** if already every-turn |

### Long-term (across conversations)

| Component | Verdict |
|---|---|
| User preferences (a profile table read into the prompt) | **Migrate+** → user-preference strategy |
| Semantic recall (vector store of past conversations) | **Migrate+** → semantic strategy |
| Rolling summaries | **Migrate+** → summarisation strategy |
| Extraction pipeline (code deciding what to remember) | **Delete** — strategies do this |
| Retention / right-to-deletion | **Keep** — a compliance requirement |

### The three recommendations are NOT independent — read this before deleting anything

Taken separately, each of these is defensible. Taken together they destroy conversation
history:

1. **Delete the session store** — the microVM holds history
2. **Call `StopRuntimeSession`** at end-of-conversation — worth ~12× on memory
3. **Skip AgentCore Memory** — 55% of the default bill, and unnecessary if nobody resumes an
   old conversation

Apply all three and history exists **only** in a microVM that you are now explicitly
terminating, with no store to recover from. The idle timeout does the same thing on its own
after 900s. That is a **regression against topology A**, where DynamoDB held it.

The evidence does not support "delete" at full strength either: what was measured was a
**3-turn conversation inside one warm session** `[measured:reference]`. That shows a microVM
retains state across three requests. It says nothing about the 8-hour session ceiling, idle
expiry, concurrent turns in one session, or a runtime version update mid-conversation.

**So ask this, not the softer version:** *can a user be away longer than the idle timeout and
still expect their conversation intact?* "Does a returning user resume a prior conversation?"
gets a "no" from teams who nonetheless need durable state across a 20-minute coffee break —
the ordinary chat-UI case.

Record the answer explicitly as `history_durability_across_idle`:

| Value | When | Consequence |
|---|---|---|
| `microvm_only` | truly ephemeral, single-sitting conversations | cheapest; history gone at idle expiry or `StopRuntimeSession` |
| `agentcore_memory` | users return, or sessions outlive the idle timeout | pay per event, every turn |
| `external_store` | durability needed and Memory is not wanted | you kept topology A's store; do not claim migration deleted it |

Do not set `microvm_only` unless the customer has confirmed in writing that losing history at
idle expiry is acceptable. `cost-and-billing.md` frames the idle-timeout trade as "a cold start
for a user who returns mid-conversation" — with the store deleted it is not a cold start, it is
data loss.

**Short-term disappears; long-term is a build-or-adopt decision with a price.** But note the
qualifier above: short-term disappears *only* if you accept the durability consequence.
`AgentCoreMemorySessionManager` is forced to `batch_size=1`, so events bill every turn — in one
measured config, **55% of the bill**.

Namespaces accept only `{actorId}`, `{sessionId}`, `{memoryStrategyId}` — not `{tenantId}`.
Scope with a literal prefix or encode the tenant into the actor id. → `agentcore-memory.md`,
`naming.md`

---

## §Tools and MCP (AGENTOPS04 + AGENTSEC02 + AGENTPERF06)

| Component | Detect | Verdict |
|---|---|---|
| In-process tools | `@tool` functions | **Migrate** unchanged |
| Self-hosted MCP servers | separate deployments serving `/mcp` | **Migrate+** → Gateway targets, or keep and front them |
| MCP client wiring | `MCPClient`, header plumbing | **Migrate+** — Gateway consolidates |
| Tool-level authz | any per-caller restriction? | **Gap** usually → Cedar |
| Rate limits | per-caller/tool/token | **Migrate+** → Gateway rate limits |

Self-hosted MCP servers are a commonly missed component: teams run three or four as separate
services with their own deployments, scaling and auth. Gateway can consolidate them behind one
endpoint with uniform auth and throttling, or front them where they are — both valid, and the
choice belongs in the decision record. Verified portable: the MCP surface worked unchanged on
the cluster `[measured]`. → `gateway-and-mcp.md`

---

## Three rows that need discussion, not a verdict

**Tool-level authorization (AGENTSEC02).** The only genuinely open architectural choice. Keeping it
in-process means it works today but sits inside the process a prompt injection reaches, and it
is scattered and hard to audit. Moving to Cedar makes it deterministic, external and logged —
at the cost of adopting Gateway and Policy, writing a schema, and a `LOG_ONLY` → `ENFORCE`
rollout. Scoped work that migration makes *available*, not a free win.

**The cluster itself.** Ask early: **do non-agent workloads share it?** If yes it stays
regardless, its fixed control-plane cost is not attributable to the agent, and EKS's marginal
cost per conversation is very hard to beat. This one answer can invert the cost verdict. →
[cost-model.md](cost-model.md)

**Conversation-end detection (AGENTCOST03).** A nice-to-have on EKS, the top cost lever on
AgentCore. An unusual case where migrating makes an existing weakness expensive rather than
irrelevant — so it belongs in the plan, not the backlog.

## Fix before migrating

Several verdicts above are "fix this first" rather than "migrate this" — event-loop blocking
(AGENTPERF02), ARM64 (AGENTOPS03), a spend ceiling (AGENTCOST01), concurrent-turn safety (AGENTREL03), and a
golden eval set (AGENTOPS06). They share a property worth flagging to the customer: each improves
the current service *and* the post-migration outcome, so they are justifiable even if the
migration never happens.

They are sequenced as Phase 0 in [playbook.md](playbook.md), which owns the list and the exit
criteria. Mark them in the record as you find them; do not plan them here.

## Sequencing the deletions

Delete nothing during migration. Enumerate, migrate, run in parallel, cut over, observe,
*then* delete. The session store in particular is the rollback path: while it still works,
reverting to EKS is a routing change rather than a recovery.

## Reporting

Five buckets, in this order. The order is the message: findings before products, and things
they can adopt without a replatform before things that need one.

1. **Gaps in the current system** — true whether or not they adopt anything. Lead here; it is
   the proof the assessment was real work, and it is the part with value independent of any
   AWS purchase. Anything exploitable today goes at the top, as a vulnerability rather than as
   a migration consideration.
2. **What is already right** — name it. An assessment that finds only faults reads as a pretext,
   and a customer who recognises their own good decisions in your list believes the rest of it.
3. **Gaps closable with no platform change** — Gateway, Policy, Identity, Memory, Evaluations,
   Observability alongside their existing runtime. This is usually the largest and most
   actionable bucket, and it is the one that gets acted on this quarter.
4. **What a runtime move would additionally change** — deletions with line counts where you
   can, because they are concrete; and the ceilings or isolation properties that only Runtime
   provides. Clearly marked as requiring a replatform.
5. **What stays regardless** — the datastore, the prompt, the tools' business logic, their
   cluster if other workloads use it.

**Do not present bucket 4 as the destination.** "Assessed, adopting two components, revisiting
the runtime next year" is a complete and successful outcome. So is "assessed, changing nothing
yet" — with the condition that would change it named.
