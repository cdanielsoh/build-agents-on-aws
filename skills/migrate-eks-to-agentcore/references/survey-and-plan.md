# The access survey, and the check graph it produces

## Why this exists

Every step order this skill has shipped was linear prose, and the same failure recurred: a step that
did not apply was performed anyway, or a step that did apply was silently skipped. Invoking the
agent was documented for weeks and never done, then made mandatory and done without asking. The
declarative-platform fork sat inside "reading the repo", so assessors read 180 lines of source
method before learning there was no source. Coverage claims were recorded as `absent` when the
honest answer was "I was not permitted to look."

Prose cannot enforce a precondition. A graph can. So:

1. **Survey first.** Establish what is *possible* — this is the one thing that cannot be derived.
2. **Derive the check graph from the survey.** Each check declares what it needs. Checks whose
   preconditions are unmet are **marked unreachable with the reason**, never dropped and never
   converted into findings about the customer.
3. **Walk it.** What you could not reach is part of the deliverable.

## Step 1 — The survey. Ask these, and only these.

Six questions. They are all about **access and permission**, which is why asking them is not the
sales-script failure this skill warns about — you cannot derive whether you are allowed to do
something. Everything *derivable* stays derivable; do not ask about architecture here.

| # | Ask | Why it gates something |
|---|---|---|
| S1 | Is there application source we can read, and is it the revision that is deployed? | Decides whether source reading is evidence or fiction. A repo ahead of production invalidates every `file:line` claim |
| S2 | Do we have read access to the cluster or platform control plane, and in whose account? | The control plane outranks source. Wrong account means quota and infra reads describe *us* |
| S3 | Is there a running deployment, and does it carry real traffic? | No deployment ⇒ no measurements. Traffic ⇒ telemetry may already hold the four numbers. No traffic ⇒ no monthly cost answer exists |
| S4 | **May we send requests to the agent? In which environment?** | Invoking runs their tools, spends model budget, writes state. Not a read. Ask; expect no |
| S5 | May we generate load, and against what? | A concurrency sweep is load on a live service. Same consent class as S4, and more intrusive |
| S6 | Who can answer product questions — retention needs, conversation end, compliance, on-call? | If nobody, Gate 3 is structurally unavailable and should say so rather than sitting `undecided` |

Record the answers verbatim in the record's `access` block before doing anything else. If the
customer cannot answer one, that is itself an answer — record `unknown` and treat the gated checks
as unreachable.

**A survey is not a questionnaire about their architecture.** If you find yourself asking what their
session store is, stop: that is derivable, and asking it is the thing that costs trust.

## Step 2 — The check graph

Nodes are checks. Edges are preconditions. Nothing here is optional to *consider*; each node ends up
in exactly one state:

- **`done`** — performed, evidence recorded
- **`unreachable`** — a precondition is unmet. Record the node, its blocker, and the substitute you
  used instead
- **`not_applicable`** — the question does not apply to this architecture. Say why

`unreachable` and `not_applicable` are **not** findings about the customer. Conflating them with
`absent` is the single most common way this instrument has produced false gaps.

```
                          ┌─────────────┐
                          │ S: survey   │  MUST run first; gates everything
                          └──────┬──────┘
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
  ┌───────────┐          ┌──────────────┐         ┌──────────────┐
  │ A: shape  │          │ B: logs +    │         │ C: quotas &  │
  │ coded vs  │          │ status       │         │ region       │
  │declarative│          │ (read-only)  │         │ (own acct)   │
  └─────┬─────┘          └──────┬───────┘         └──────┬───────┘
        │ needs S1 or S2         │ needs S2               │ needs S2
        │                        │                        │
   ┌────┴─────┐                 │                        ▼
   ▼          ▼                 │                 ┌──────────────┐
┌───────┐ ┌─────────┐           │                 │ D: Gate 0    │
│A1 src │ │A2 CR/   │           │                 │ blockers +   │
│ read  │ │ schema  │           │                 │ liftability  │
│needs  │ │ read    │           │                 └──────┬───────┘
│  S1   │ │needs S2 │           │                        │
└───┬───┘ └────┬────┘           │                        │
    └─────┬────┘                │                        │
          ▼                     ▼                        │
   ┌──────────────┐      ┌──────────────┐                 │
   │ E: dead-     │      │ F: invoke    │                 │
   │ control /    │      │ the agent    │                 │
   │ intent-vs-   │      │ needs S4     │                 │
   │ effect sweep │      └──────┬───────┘                 │
   └──────┬───────┘             │                         │
          │              ┌──────┴───────┐                 │
          │              ▼              ▼                 │
          │       ┌────────────┐ ┌────────────┐           │
          │       │F1 tool vs  │ │F2 concur-  │           │
          │       │ground truth│ │rency/leak  │           │
          │       └────────────┘ └────────────┘           │
          │                                               │
          └───────────────┬───────────────────────────────┘
                          ▼
                   ┌─────────────┐
                   │ G: inventory│  the 41 Lens questions
                   └──────┬──────┘
                          ▼
                   ┌─────────────┐      ┌─────────────┐
                   │ H: measure  │─────▶│ I: sweep    │ needs S5
                   │ needs S3    │      └─────────────┘
                   └──────┬──────┘
                          ▼
                   ┌─────────────┐
                   │ J: Gate 3   │ needs S6
                   └──────┬──────┘
                          ▼
                   ┌─────────────┐
                   │ K: record + │
                   │ adoption    │
                   │ path        │
                   └─────────────┘
```

### The nodes, with their preconditions and substitutes

| Node | Needs | If unreachable, substitute |
|---|---|---|
| **A** shape: coded, declarative, or managed | S1 or S2 | none — if you have neither source nor control plane, say the assessment is not possible and stop |
| **A1** read application source | S1 = yes, revision matches | if the repo is ahead of production, pin to the running build or drop to `read:cluster` only |
| **A2** read resource specs and served schemas | S2 | source, explicitly labelled as possibly-not-deployed |
| **B** logs and status conditions | S2 | nothing substitutes. This is the cheapest high-yield read and needs no permission — if it is unavailable, say what that costs |
| **C** quotas, region availability, prices | S2 in *their* account | `list-aws-default-service-quotas` only, tagged defaults-only; every applied value `open (wrong account)` |
| **D** Gate 0 blockers + Gate 0.0 liftability | C | gate against defaults and mark `compute_type_assumed` |
| **E** intent-vs-effect sweep (dead controls, live controls with bad side effects) | A1 or A2 | on a declarative platform, diff what the resource declares against what the controller created |
| **F** invoke the agent | **S4 = yes** | the tool's own logs; stored session rows; claimed capability vs the config that would implement it. A described feature with no wiring is the same finding |
| **F1** check a tool's output against ground truth | F | the tool's RBAC — what it *could* return — plus its logs |
| **F2** concurrency / cross-session behaviour | F | stored session and event rows, their ordering and timestamps |
| **G** the 41 Lens questions | A | walk it regardless; mark rows `unknown` with the blocking node, never `absent` |
| **H** the four measurements | S3 = deployment exists | all `open`; `cost_confidence.per_turn: unavailable` |
| **I** concurrency sweep | **S5 = yes** and H | single-level numbers, explicitly labelled not-production |
| **J** Gate 3 product questions | S6 = someone to ask | `engagement: internal_reference`; record the questions as `open_questions`, not as `undecided` rows |
| **K** record and adoption path | G | — |

### Two properties worth preserving if this is ever restructured

**Unreachable is recorded, not inferred.** Every node that did not run appears in the record with
its blocker. A reader can then tell "this control is missing" from "we were not allowed to look",
which is the difference between a finding and an omission.

**Consent gates are explicit edges, not prose.** F and I depend on S4 and S5. That is why an
assessor cannot reach them by enthusiasm, and why refusing them costs the findings named in the
substitute column rather than silently producing a thinner record that looks complete.

## Step 3 — Emit the plan before walking it

Write the graph state into the record up front, so the shape of the assessment is visible before any
conclusion is:

```yaml
access:                      # verbatim survey answers
  source_available: true | false | unknown
  source_matches_deployment: true | false | unknown
  control_plane_read: true | false
  account_is_customers: true | false
  deployment_exists: true | false
  carries_real_traffic: true | false | unknown
  invocation_permitted: true | false | unknown      # S4
  invocation_environment: production | staging | pilot | none
  load_generation_permitted: true | false | unknown # S5
  product_owner_reachable: true | false             # S6

plan:                        # one entry per node above
  - node: F
    state: done | unreachable | not_applicable
    blocked_by: S4            # required when unreachable
    substitute: <what you did instead, or none available>
```

An assessor who writes this first cannot later mistake an unasked question for an answered one, and
a customer reading it can see exactly what their access decisions cost them — which is often the
most actionable thing in the document.
