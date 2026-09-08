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

Record the answers verbatim in `.agentcore-migration/access.yml` before doing anything else. If the
customer cannot answer one, that is itself an answer — record `unknown` and treat the gated checks
as unreachable.

**Two of these answers are enforced, not advisory.** `invocation_permitted` and
`load_generation_permitted` are read by a `PreToolUse` hook, which blocks the matching commands
until the file says yes. Writing the permission down is what makes acting on it auditable — and the
failure that motivates it happened: invocation was made a mandatory step and performed without
asking anyone.

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

### The nodes, with their preconditions, substitutes, and the one file each

**Each node points at exactly one reference.** If a node needs two files to perform, the split is
wrong — say so rather than working around it.

This table is the prose version. The machine-readable one is `nodes:` in
[lens-graph.yaml](lens-graph.yaml), which is what `resolve` walks and what `record --node`
validates against. `lens_plan.py selfcheck` asserts the two agree on the node ids and on the file
per node, because a table in one file and a graph in another have drifted in this project before.

| Node | Needs | Read | If unreachable, substitute |
|---|---|---|---|
| **A** shape: coded, declarative, or managed | S1 or S2 | [read-the-shape.md](read-the-shape.md) | none — if you have neither source nor control plane, say the assessment is not possible and stop |
| **A1** read application source | S1 = yes, revision matches | [read-the-repo.md](read-the-repo.md) | if the repo is ahead of production, pin to the running build or drop to `read:cluster` only |
| **A2** read resource specs and served schemas | S2 | [read-the-cluster.md](read-the-cluster.md) | source, explicitly labelled as possibly-not-deployed |
| **B** logs and status conditions | S2 | [read-the-cluster.md](read-the-cluster.md) | nothing substitutes. This is the cheapest high-yield read and needs no permission — if it is unavailable, say what that costs |
| **C** quotas, region availability, prices | S2 in *their* account | [probe-aws.md](probe-aws.md) | `list-aws-default-service-quotas` only, tagged defaults-only; every applied value `open (wrong account)` |
| **D** Gate 0 blockers + Gate 0.0 liftability | C | [constraints.md](constraints.md) | gate against defaults and mark `compute_type_assumed` |
| **E** intent-vs-effect sweep (dead controls, live controls with bad side effects) | A1 or A2 | [sweep-for-dead-controls.md](sweep-for-dead-controls.md) | on a declarative platform, diff what the resource declares against what the controller created |
| **F** invoke the agent | **S4 = yes** | [invoke-the-agent.md](invoke-the-agent.md) | the tool's own logs; stored session rows; claimed capability vs the config that would implement it. A described feature with no wiring is the same finding |
| **F1** check a tool's output against ground truth | F | [invoke-the-agent.md](invoke-the-agent.md) | the tool's RBAC — what it *could* return — plus its logs |
| **F2** concurrency / cross-session behaviour | F | [invoke-the-agent.md](invoke-the-agent.md) | stored session and event rows, their ordering and timestamps |
| **G** the 41 Lens questions | A | [production-inventory.md](production-inventory.md) | walk it regardless; mark rows `unknown` with the blocking node, never `absent` |
| **H** the four measurements | S3 = deployment exists | [measure.md](measure.md) | all `open`; `cost_confidence.per_turn: unavailable` |
| **I** concurrency sweep | **S5 = yes** and H | [concurrency-sweep.md](concurrency-sweep.md) | single-level numbers, explicitly labelled not-production |
| **J** Gate 3 product questions | S6 = someone to ask | [ask.md](ask.md) | `engagement: internal_reference`; record the questions as `open_questions`, not as `undecided` rows |
| **K** record and adoption path | G | [record-and-adopt.md](record-and-adopt.md) | — |

Cross-cutting, so not nodes: [evidence.md](evidence.md) governs how every one of the above tags
what it found, [receipts.md](receipts.md) governs how each one records it, and
[provenance.md](provenance.md) says which of this plugin's own claims are measured.

### Three properties worth preserving if this is ever restructured

**Unreachable is recorded, not inferred.** Every node that did not run has a receipt naming its
blocker. A reader can then tell "this control is missing" from "we were not allowed to look", which
is the difference between a finding and an omission.

**Consent gates are explicit edges, not prose.** F and I depend on S4 and S5. That is why an
assessor cannot reach them by enthusiasm, and why refusing them costs the findings named in the
substitute column rather than silently producing a thinner record that looks complete. A hook now
enforces both edges against `access.yml`.

**Intent is computed; only what happened is stored.** Anything that records the *plan* in a file
becomes a claim nobody checks. `resolve` derives the intended walk from the graph and the survey
every time it is asked; receipts hold the actual one. If a future version reintroduces a stored
plan, it will reintroduce "done on all 15 nodes, unverifiable" with it.

## Step 3 — Write the survey down, then let the graph compute the walk

Write **only** the survey answers, to `.agentcore-migration/access.yml`:

```yaml
service: <name>
account: <id>
region: <region>
depth: quick | full
surveyed_at: <iso8601>
surveyed_with: <name or role>          # absent means nobody was asked and access was assumed
access:
  source_available: true | false | unknown
  source_matches_deployment: true | false | unknown
  control_plane_read: true | false
  account_is_customers: true | false
  deployment_exists: true | false
  carries_real_traffic: true | false | unknown
  invocation_permitted: true | false | unknown      # S4 — also read by the PreToolUse consent hook
  invocation_environment: production | staging | pilot | none
  load_generation_permitted: true | false | unknown # S5
  product_owner_reachable: true | false             # S6
```

Then:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lens_plan.py" resolve --multi-agent yes|no|unknown
```

**Do not write the intended walk into a file.** Earlier versions had a `plan:` block listing every
node with `state: done | unreachable | not_applicable`, written up front. It was a stored copy of
what `resolve` already computes, and it failed two ways at once: it went stale when access changed,
and an assessor could write `done` on all 15 nodes with nothing to contradict it. That is the same
"assertion that cannot fail" defect this instrument documents in other people's work.

So the intent is `resolve`, computed on demand and never stored. **What actually happened is a node
receipt, written when it happens:**

```bash
lens_plan.py record --node B --state done \
    --tag read:cluster --evidence "3 swallowed exceptions in the tool dispatch path" \
    --source "kubectl logs -n copilot deploy/copilot --since 24h"

lens_plan.py record --node F --state unreachable --blocked-by S4 \
    --substitute-used "the tool server's own logs plus stored session rows" \
    --tag open --evidence "invocation refused at kickoff"
```

`lens_plan.py status` reconciles the two and prints the frontier: what is reachable, minus what has
a receipt. That is what a resumed session needs, and it is what the `SessionStart` hook prints. The
full mechanism, and how a correction works, is in [receipts.md](receipts.md).

A customer reading the resolved walk can see exactly what their access decisions cost them — often
the most actionable thing in the document. A customer reading the node receipts can see what was
actually done, which is a different and previously unavailable claim.
