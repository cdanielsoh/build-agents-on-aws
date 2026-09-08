# Node K — propose, then let the human decide

Needs node G. This is where the assessment becomes something the customer can act on, and where the
positioning is load-bearing: **the assessment is the product.** A customer who learns that their tool
server has no server-side authorization, that their approval gate has zero call sites, or that their
conversation history has no retention policy has received something valuable whether or not they ever
move a workload.

For each gap the useful question is not *"should you migrate?"* but **"which component closes this,
and does it require moving your runtime?"** Usually it does not.

## You do not author the verdicts

The record's editability was supposed to be what made the customer's choices binding rather than
advisory. It was not: the assessor wrote the verdicts and the adoption path unilaterally, and the
customer edited afterwards if they noticed.

**You produce suggestions. The human chooses. The record holds the choices.** Which gives the
instrument a symmetry that is the point rather than a nicety:

> **Survey 1 gates what we may look at. Survey 2 gates what we may change.**

Two files, and the boundary between them is the whole mechanism:

- **`suggestions.yml`** — yours. Grouped, grounded proposals: *"based on what I found, this is the
  change, here is what it buys, here is what it does not fix."*
- **`decisions.yml`** — theirs. `proceed` / `declined` / `deferred`, with the reason captured when
  it is given.

`/plan-agentcore-migration` reads everything and **acts only on decisions.**

## Suggestions: grouped, grounded, and honest

The 41 questions are the **evidence unit**. The **action unit** is coarser — one NetworkPolicy
touches AGENTSEC02, AGENTPERF07 and part of AGENTSEC01; one session-isolation field closes both a
confidentiality and a correctness finding. That translation used to happen invisibly, in the
assessor's head.

```yaml
suggestions:
  - id: S-04
    # Rule 2, made structural: if you cannot write this as ONE change, it is two suggestions.
    change: Set authz.serverSide=true on the Agent chart and write the tool policy it reads
    closed_by: platform_config          # non-AgentCore answers first, always
    requires_runtime_move: false
    closes: [AGENTSEC02/tool_allowlist, AGENTPERF07]   # finding ids. Empty is invalid.
    grounded_in: [r-0007, r-0008]                      # receipt ids. Empty is invalid.
    buys: tool authorization stops living inside the process a prompt injection reaches
    does_not_fix: the approval gate, which has no chart field at all — that is S-05
    cost:
      # SCOPE, not duration. Files and call sites, boundaries crossed, whether a staged rollout is
      # required, what must land first. See "effort is scope" below — a day count is not ours to give
      effort: chart values.yaml plus one policy file; needs a LOG_ONLY period before ENFORCE
      one_off: none | <usd> | open
      recurring: none | <usd/month> | open
    confidence: high | medium | low
    coexists_with: their existing runtime and every tool, unchanged
    assumes: [the field is honoured by the release they run — not yet verified]

    # OPTIONAL, and its absence is a claim: there is no managed answer, so the only way to close
    # this gap is in their own service. Omit it for a wrong system prompt or a wrong README.
    # Present when there IS one, because survey 2 asks *where* and cannot be answered otherwise.
    agentcore_alternative:
      capability: policy                # a `closed_by` AgentCore member. Cedar, here
      closes: partially
      note: still no approval gate — Cedar constrains which tools, not who signs off
      eks_build: a chart field plus a policy file they author and maintain
      eks_you_own: the policy format, its rollout, and keeping it in step with the tool list
      agentcore_config: a Cedar policy on the Gateway, LOG_ONLY then ENFORCE
      agentcore_you_own: the policy content — the engine, evaluation and audit trail are managed
      requires_runtime_move: false      # Policy is callable from an EKS pod. Only `runtime` is not
```

Four rules, and each exists because its absence has produced a specific failure:

1. **A suggestion is not a benefit claim.** It carries which receipts ground it, what it costs,
   **what it does not fix**, and confidence. Without those it is a pitch with citations — the exact
   overclaiming this instrument exists to prevent. `validate` reports a suggestion missing
   `does_not_fix` as incomplete.
2. **A group exists only if one change closes every member.** Two changes means two suggestions,
   however related they feel. Without this rule grouping becomes *packaging* — bundling a weak item
   with strong ones to get it approved.
3. **Grouping must not swallow dissent.** The decision attaches to the group, but per-question state
   survives. *"No to this bundle; AGENTSEC02 remains an open gap"* has to be expressible, which is
   what `residual_gaps` on the decision is for.
4. **"No to everything" is a success state.** `assess_only`. The findings are the deliverable. Say
   so, so the flow does not read as a funnel.

**Ordering: no-runtime-move first, free before paid.** Everything with
`requires_runtime_move: false` comes before anything needing a replatform, because those are the ones
they can act on without a replatform decision. That sort is mechanical; every other judgement in the
file is yours.

**Do not write section-divider comments over that sort.** Runs of this instrument grouped the sorted
list under headers like *"런타임 이동이 필요 없는 것"* / *"no runtime move needed"*, and they do three
jobs of which only one is real:

| The header was doing | Verdict |
|---|---|
| grouping the list | redundant — every suggestion carries `closed_by` and `requires_runtime_move`, and the file is already sorted by them |
| asserting that migration is unnecessary | **misleading.** Negated, on 21 of 22 rows, it reads as "you don't need to migrate" rather than "this particular gap closes without moving" |
| implying a timeframe ("this quarter") | **not ours to claim** — see below |

The sort carries the only part that was information. Leave the list unheaded.

**Label each suggestion with `closed_by`, not with the absence of a runtime move.** A negation invites
the reader to generalise it; a service name states a fact and generalises to nothing. Only one value
mentions moving, because it is the only one where moving is true:

| `closed_by` | Label |
|---|---|
| `customer_code` · `cluster_config` · `platform_config` · `iam_policy` · `upstream_contribution` | the customer's own — name which one |
| `gateway` · `policy` · `identity` · `memory` · `evaluations` · `observability` · `code_interpreter` · `browser` | `AgentCore <capability>` |
| `runtime` | `AgentCore Runtime` — **and this one says the move is required** |

This is derivable from a field, so it belongs in a generator eventually rather than in this
convention. Until then it is a rule here, and a rule here is weaker than a check.

**Grounding is structural, not a habit.** `closes` and `grounded_in` must both be non-empty and must
resolve, or `validate` fails. And `validate` reports the reverse: a finding that needs action and
appears in **no** suggestion. That is the gap a model under context pressure creates, and it is
invisible without the check. Deliberate omission is fine — say so in `assessment.yml`'s `triage`.

## `effort` is scope, not duration

`cost` is required on every suggestion and this file used to demonstrate filling it with *"a day, plus
a LOG_ONLY rollout"* — so the example taught the guess, and runs produced "1~2 days", "2~4 days",
"4~8 weeks" for changes in a repository they had read-only access to.

None of that is knowable from here. Duration is a property of **their** organisation: review process,
test-suite runtime, sprint boundaries, who is on call. One real record estimated the days needed to
change a deployment path while listing *which pipeline actually built the running image* as an open
question.

It also contradicts a rule the instrument enforces elsewhere. `/plan` refuses to write a patch for a
`via: eks_build` item because **a patch you cannot test implies a confidence you do not have** — and
then a day-count says how long their team will take to write and test that same patch. Identical
defect, opposite direction.

So record what the repository shows:

| Record | Not |
|---|---|
| which files, and how many call sites | "half a day" |
| whether it crosses a repo or a team boundary | "1~2 days" |
| whether a staged rollout is required (`LOG_ONLY` → `ENFORCE`) — a **sequence**, not a duration | "days to weeks" |
| what has to land first | — |
| that you could not test it here | — |

One exception: a duration the **customer** gives you is a fact about them, and records like any other
statement of theirs — tagged `stated:customer`. The rule is only that a duration may not originate
with us.

`one_off` and `recurring` are unaffected. Money is sourceable: price it from the Pricing API and cite
the receipt, or write `open`. One record priced endpoints live and corrected $14.60 to $21.90 on
re-observation, which is exactly the standard. It is `effort` alone that has no source.

## Survey 2 — the decisions

Two questions per gap, not one: **do you want it closed, and where.** The second is what decides
whether we write code or write a change request, so it cannot be inferred.

```yaml
decided_at: <iso8601>
decided_with: <name or role>       # absent means no survey 2 happened, and /plan will refuse
decisions:
  - id: D-04
    suggestion: S-04
    choice: proceed | declined | deferred
    via: eks_build | agentcore | both        # required on proceed — see below
    reason: <their words, not ours>          # required for declined and deferred
    revisit_when: <the condition>            # required for deferred
    residual_gaps: [AGENTSEC02/approval_gate]
    stated_by: <name or role>
outcome: migrate | migrate_partially | adopt_components | assess_only | stay | redesign_first
outcome_note: <one line>
```

### Put both paths on the page, or `via` is a coin toss

**Ask `via` only where the suggestion carries an `agentcore_alternative`.** Where it does not — a
system prompt that contradicts the data, a README with the wrong tool count — `eks_build` is the only
truthful answer and asking would be theatre. On a real record that was 8 questions, not 22.

Where there is a choice, show this. The customer cannot choose between two paths they cannot see,
and a one-line "AgentCore provides this" is not a comparison:

```
AGENTSEC03 — inbound authentication is absent          [severity: high, exploitable today]

  Build it yourself                      Use the managed capability
  ─────────────────────────────          ──────────────────────────────────────
  ~7 lines of JWT verification in        Identity: CUSTOM_JWT authorizer —
  FastAPI, plus a NetworkPolicy          discoveryUrl and allowed audience
  ~1 day                                 configuration only
  You then own: JWKS caching, key        You then own: claim extraction
  rotation, the middleware
                                         Requires moving the workload: NO
                                         — callable from your EKS pod today
  Neither closes: authorization. A valid token still reaches all 6 tools
  (AGENTSEC02 stays open either way)

  → eks_build, agentcore, or both?
```

Three parts of that are load-bearing:

- **What they still operate afterwards.** Usually the deciding fact, and the one a cost comparison
  hides. Both paths validate a token; only one leaves somebody owning key rotation at 3am.
- **Whether it requires moving the workload.** *Only `runtime` does.* Gateway, Policy, Identity,
  Memory, Evaluations, Observability, Code Interpreter and Browser are callable from an EKS pod —
  which is what the `adopt_components` outcome is for, and it is frequently the honest
  recommendation. Saying "AgentCore gives you this" while omitting "after you migrate" is the version
  of this conversation that dies in their architecture review.
- **What neither path closes.** Already in `does_not_fix`; quote it rather than re-deriving, and do
  not let the managed column absorb it.

**`both` is not a hedge.** It is the right answer whenever something is exploitable today and its
managed replacement needs the runtime move: nobody waits for a migration to close a live
vulnerability. Only the managed half is ever authored — the interim fix is theirs.

**Do not aggregate this into a migration pitch.** The migration case is the *sum* of these answers.
Five gaps where they chose `agentcore` is a business case made of five capabilities they picked; the
same five asserted up front is a pitch they have to accept or reject whole. And on a record where 21
of 22 accepted suggestions had `requires_runtime_move: false`, the honest total was
`adopt_components`, not `migrate`.

**`deferred` is a first-class answer, not a soft no.** *"Not this quarter, revisit when we have a
second tenant"* is the most common real response, and it used to collapse into `undecided`, which
reads as nobody asked. `revisit_when` takes a **condition**, not a date, unless they gave a date.

**There is deliberately no `undecided` value.** An undecided suggestion has *no entry*, and
`validate` reports it as undecided by absence. That is what stops a column of `undecided` reading as
"we asked and they never replied" on an engagement where nobody was ever asked.

**A factual disagreement is not a decision — it is a new receipt.** If the customer says a finding
is wrong, record `stated:customer` and regenerate. That is why there is no separate `dissent` block
any more: facts go to receipts, choices go to decisions, and neither has two homes.
See [receipts.md](receipts.md).

## Which adoptions need a runtime move

| Adoption shape | Runtime stays put? | Typical fit |
|---|---|---|
| **Gateway** (+ **Policy**) in front of existing tools | **yes** | tool sprawl, no server-side tool authz, no per-identity scoping, missing approval gates |
| **Identity** for outbound credentials / token vault | **yes** | hand-rolled OAuth, per-user tokens in a table |
| **Memory** for long-term or retained state | **yes** | no retention policy, unbounded history, preference extraction |
| **Evaluations** | **yes** | no golden set, no regression gate |
| **Observability** | **yes** | no traces, no per-turn attribution |
| **Runtime** | **no — this is the migration** | session isolation, inbound `CUSTOM_JWT`, scale-to-zero, ceilings met |

Only the last row is a replatform. Say which row each recommendation sits in, and **lead with the
ones that need no platform change** — those are what a customer can act on this quarter. A component
adopted alongside their existing service is a real outcome; so is "assessed, nothing adopted yet,
revisit when X changes."

## Adoptability is `component × who owns the code`

The table above holds when the customer owns the agent process. It does **not** hold on a third-party
platform, and that column has already been wrong once in practice:

| | Customer owns the agent code | Third-party platform runs it |
|---|---|---|
| **Gateway** | add a tool endpoint | **still yes** — usually just a URL on a config object, the cheapest adoption available |
| **Policy** | yes | **yes**, behind Gateway |
| **Memory**, **Identity** | add the SDK integration | **no** — needs an SDK call inside an executor you do not ship. It is an upstream PR or a fork, so say `upstream_contribution`, not "adopt Memory" |
| **Evaluations** | yes | **verify first** — check their trace/export format is one Evaluations accepts before promising it |
| **Observability** | yes | partly — platform-level flags may exist; SDK-level instrumentation does not |

So establish `code_ownership` **before** writing the adoption path — it is determined at
[read-the-shape.md](read-the-shape.md) — and never promise a component that needs a code change
inside software the customer does not maintain. On a third-party platform the honest, valuable answer
is usually: **their platform already ships a field for this and it is switched off** — which costs
nothing and buys the credibility for everything else.

## `requires_runtime_move: false` — say it at the confidence it has earned

The whole reframe rests on this claim, and an assessor found it asserted only as the tables above.

| Claim | Status |
|---|---|
| Gateway/Policy can serve an agent **not** on AgentCore Runtime | `[docs]` + `[reasoned]` — Gateway is an MCP endpoint and Policy attaches to the Gateway; nothing ties either to Runtime. **Not yet demonstrated end to end from a non-Runtime caller by this plugin** |
| **Cost/effort of doing so from inside a cluster** | `[verified]` and **not free** — see the TLS prerequisite below |
| Memory/Identity adoptable without a runtime move | **only if the customer owns the executor** (previous table) |
| Evaluations accepts their traces | **unverified per framework.** The reference names specific SDKs; a different runtime's trace format must be checked, not assumed |

**Do not present the free-adoption path as demonstrated.** Present it as the design the platform
supports, name what you have not verified, and where a customer needs certainty, say a
proof-of-concept is the next step. That is still a far better conversation than a replatform, and it
survives the customer testing it.

### Cheap in architecture, not always in effort

`deploy-on-agentcore/references/gateway-private-targets.md` is explicit: **VPC egress requires the
target endpoint to have a publicly trusted TLS certificate** — not a self-signed cert, not a
private-CA cert. A tool server on a plaintext in-cluster Service, which is the normal shape,
therefore does not satisfy it as deployed. Reaching it means an ingress path with a real certificate,
or relocating the tool behind something that already has one.

So size it honestly: **Gateway is the cheapest adoption on the architecture axis and can still be
weeks of work in a cluster.** Saying "no runtime move" and implying "no work" is the version a
customer catches. Check that reference for the options before quoting effort, and note that its
validated worked example is not an in-cluster one.

## What does not go away

Always present this. It is the section that earns the right to the rest.

| Stays theirs | Why |
|---|---|
| Budget / spend ceilings | No platform bounds a runaway agent loop |
| Session-id issuance + user binding | AgentCore does not map users to sessions `[docs]` |
| Row-level authorization | Pod and runtime identity are per-workload, not per-user |
| Prompt, tool design, evals, grounding | Untouched by the migration |
| The knowledge store | Stays put. Forces VPC mode **only if it is VPC-resident** — check, don't assume |
| Event-loop discipline | Blocking I/O in an async handler is still theirs to get right |

## Reasons to stay, given equal weight

A customer who hears only the migration case does not trust the migration case.

- Non-agent workloads already on the cluster, with the platform team to run it
- Turns that legitimately exceed the request timeout with checkpoint-resume semantics
- Custom isolation (Kata/gVisor), sidecars, or an unsupported inbound protocol. **Not GPU** — the
  Instances compute type supports it
- Node-level runtime threat detection with no managed equivalent
- Sessions longer than the Instances ceiling, or longer than the microVM ceiling if microVMs are
  required
- Sustained high volume where reserved or Spot capacity beats per-session billing
- Deep Kubernetes expertise already paid for, and a working service
- A regulatory posture that forbids the tool gateway from having a **public endpoint at all**, as
  distinct from forbidding public *reachability*. Private reachability is achievable; private
  *placement* is not. **Do not say "not satisfiable"** — that phrasing manufactured a blocker for
  exactly the regulated customer who needs the real answer. The distinction is in
  [constraints.md](constraints.md)

**"It works today" is a real argument. The burden of proof is on the migration.**

**Never frame the result as abandoning what they built.** If they run a platform — theirs or a third
party's — the recommendation is almost never "stop using it." It is "keep it, and put these two
components where the gaps are." An assessment that concludes with an ultimatum gets discounted
entirely, along with the findings that were correct. If `coexists_with` is empty for every
suggestion, re-read them.

## Confidence — two axes, not one

Both live in `assessment.yml`, alongside `recommended_outcome`, `keep_as_is`, `triage` and
`economics` — the file that holds your judgement, as distinct from `findings.yml` which holds only
what was observed.

Earlier versions had a single `confidence` keyed to measurement completeness. That is a
**cost**-confidence rubric, and applying it to the recommendation produced actively harmful output: a
`stay` resting on a hard platform ceiling, or a `redesign_first` resting on a `file:line` credential
leak, both had to report `low` — sitting next to a near-certainty and undermining it.

**`recommendation_confidence`** — how sure are you of migrate/stay/redesign?

- **high** — a Gate 0 outcome, or a topology and blocker read that measurement cannot overturn.
  Measuring CPU-per-turn does not un-break a 9-hour session against an 8-hour ceiling
- **medium** — the structural read is clear but a Gate 3 answer could move it (shared cluster,
  compliance constraint)
- **low** — structure is ambiguous, or key artifacts are missing

**`cost_confidence`** — and **per-turn and monthly are different things.** A deployment with complete
measurements and *no users* — a reference install, a pilot, a pre-launch environment — earns high
confidence per turn and has **no** monthly answer at all: there is no volume to multiply by, so both
crossovers are `open`. A single value forces a choice between implying a price you cannot give and
denying measurements you have.

```yaml
cost_confidence:
  per_turn: high | medium | low | unavailable
  monthly: high | medium | low | unavailable    # unavailable whenever volume is open
```

- **high** — all four numbers measured on their workload, across a concurrency sweep, **and** a real
  volume to multiply them by
- **medium** — partial or extrapolated
- **low / unavailable** — nothing measured. Then say **"the cost verdict is unavailable"**, not
  "probably cheaper", and quote none of this plugin's reference figures as theirs

The two axes are frequently far apart, and saying so is more useful than averaging them into one
misleading word. At `low`, name the measurement that would change it. **A recommendation presented at
unearned confidence is the failure mode that loses the account** — an admitted gap is not.

Record what you could not assess, per question, with the reason. "I could not assess `AGENTSEC09`
because I have no visibility into their pen-testing" is a better answer than silence.
