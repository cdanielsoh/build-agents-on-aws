---
description: Assess an EKS-hosted agent for migration to AgentCore Runtime; emits an editable decision record
argument-hint: "[repo-path] [--region <region>] [--depth quick|full]"
allowed-tools: Bash, Read, Glob, Grep, Write, Edit, WebFetch
---

# Assess an agentic service for AgentCore Runtime migration

Arguments: `$ARGUMENTS`

**Load the `migrate-eks-to-agentcore` skill first.** This file is the *procedure* — step
order, what to write, when to stop. All substance lives in the skill's references, and each
step names the one that owns it. Do not restate their content here or in your output; read
the owner and apply it.

Output is `.agentcore-migration/decisions.yml`. Write the file — do not answer only
conversationally. `/plan-agentcore-migration` consumes it, and its editability is what makes
the customer's choices binding rather than advisory.

`--depth quick` walks only the starred practices and stops after Gate 2. Default is `full`.

## Step 0 — Locate the service and the account

If `$ARGUMENTS` names a repo path, use it. If empty, ask which repository holds the agent —
do not guess or scan the filesystem.

State the AWS account and region you are inspecting before any API call. **Read-only
throughout.** If the identity might be production, say so and confirm before proceeding.

## Step 1 — Gate 0: hard blockers

→ Owner: **`references/constraints.md`** (what the gates are, cost to resolve, escape hatches)
→ Method: **`references/assessment.md`** (the repo greps and the live AWS probes)

Read thresholds live; never carry numbers in from the plugin. Record each gate as
`pass | fail | needs_redesign` with an evidence tag.

**On a real blocker, stop the *cost and planning* work — not the inventory.** Reporting a
blocker in ten minutes beats a thorough assessment of an impossible migration, and you should
say so immediately. But do not skip Gate 1: on one assessed service, obeying an unqualified
"stop" would have suppressed a live cross-client data exposure, a prompt-injection path into
legal findings and a total absence of audit trail — all true whether or not they ever migrate,
and all of it the value the customer actually gets from the engagement.

So: report the blocker first and prominently, skip Gate 2 entirely, and still walk the
inventory. Note in `gate0` which compute type you gated against — three gates (GPU,
architecture, session duration) differ between microVMs and Instances.

## Step 2 — Gate 1: walk the inventory

→ Owner: **`references/production-inventory.md`** — all 41 Well-Architected Agentic AI Lens
*questions* (the Lens also has 150 best practices, which this does not cover), detection
guidance, and a verdict per question. Start with the starred ones.
→ Session topology has its own reference: **`references/topologies.md`**
→ Greps for the commonly-missed domains: **`references/assessment.md`**

Record per practice: `state`, `evidence` (`file:line`), `verdict`, `note`.

**Do not shortcut to the components you expect to find.** Empty rows are the most valuable
output. If you cannot assess a practice, record it `unknown` with the reason — never omit it.

## Step 3 — Gate 2: measure, then model

→ Owner: **`references/cost-model.md`** (what to report, and what not to)
→ Method: **`references/assessment.md`** (where the four numbers come from)

If the numbers cannot be measured, mark them `open` and say the cost verdict is unavailable.
**Do not substitute this plugin's reference figures as if they were the customer's.**

## Step 4 — Gate 3: ask only the underivable

→ Owner: **`references/assessment.md`** (the question list, and why it is short)

Now, and only now, ask. You have earned specificity by doing the work.

## Step 5 — Write the decision record

`.agentcore-migration/decisions.yml`. This schema is the one thing this file owns:

```yaml
assessed_at: <iso8601>
service: <name>
account: <id>
region: <region>
depth: quick | full

gate0:
  - check: single_turn_duration        # see constraints.md for the gate list
    # trivial_fix = a real gate that a one-line change clears (e.g. an amd64 pin).
    # Do NOT bucket it with an unresolvable blocker; see constraints.md's cost column.
    result: pass | trivial_fix | needs_redesign | fail | unknown
    compute_type_assumed: microvm | instances   # three gates flip between them
    # Full tag set is defined in SKILL.md — keep these in sync.
    evidence: measured:customer | measured:reference | read:source | stated:customer | verified | docs | reasoned | open
    note: <one line>

topology:
  detected: A_stateless | B_sticky | single_turn
  evidence: <file:line>
  # The taxonomy assumes conversational traffic. Set this FIRST — it gates the rest.
  work_unit: turn | job | document
  # Language-independent. `not_applicable` where the runtime has no such mechanism at all —
  # `false` would read as a clean pass for a check that never ran.
  request_isolation_risk: true | false | not_applicable | unknown   # can one slow call stall others?
  # WHAT bounds parallelism, and the integer. Name the mechanism in whatever the stack calls it
  # (thread pool, worker pool, capacity limiter, GOMAXPROCS, connection pool, semaphore).
  concurrency_bound_by: <mechanism name> | none | unknown
  concurrency_limit: { value: <n>, derived_from: cgroup | host_cpus | explicit | library_default | unknown }
  # true only if concurrency_limit >= measured peak concurrency. The common defect is a limit
  # derived from HOST cpu count inside a smaller container.
  limit_exceeds_peak: true | false | unknown
  # Leave EMPTY unless you actually ran a sweep. Do not copy this example.
  measured_at_concurrency: []
  flush_cadence: every_turn | every_n_turns | interval | on_evict | none
  # `none` is a real answer, not an omission — a team shipping it may have accepted the loss
  # deliberately. Migration then *improves* durability rather than costing writes.
  #
  # last_write_wins understates the common case and made records read milder than the code:
  # two concurrent turns mutating ONE in-memory messages list is not a lost write, it is
  # interleaved history inside a single conversation, and no store-level fix addresses it.
  concurrent_turn_safety: safe | serialized | last_write_wins | shared_object_race | unknown

measurements:
  # Order-of-magnitude context is REQUIRED next to any compute verdict: on a real customer
  # the compute delta was 0.04-1.2% of run rate, dominated by tokens and the datastore.
  monthly_token_cost_estimate: { value: <usd>, evidence: measured | open }
  monthly_datastore_cost: { value: <usd>, evidence: measured | open }
  compute_share_of_run_rate: <pct>
  tokens_per_turn: { input: <n>, output: <n>, invocations_per_turn: <n> }
  cpu_seconds_per_turn:  { value: <x>, evidence: measured | open }
  wall_seconds_per_turn: { value: <y>, evidence: measured | open }
  peak_memory_gb:        { value: <z>, evidence: measured | open, source: cgroup_memory_peak }
  turns_per_conversation: <n>

# Endpoint cost is conditional on the VPC's egress design, not on the platform.
network:
  vpc_has_internet_egress: true | false | unknown   # describe-route-tables for 0.0.0.0/0
  existing_nat_gateways: <n>
  existing_load_balancers: <n>
  existing_vpc_endpoints: [<service names>]

economics:
  agentcore_default_monthly: <usd>
  agentcore_tuned_monthly: <usd>
  eks_monthly: <usd>
  volume_crossover_conversations: <n>
  cluster_stays_for_other_workloads: true | false
  levers:
    stop_runtime_session: recommended | already_done | not_applicable
    agentcore_memory_required: true | false     # does a returning user resume a thread?

# One entry per Lens practice walked. Absent rows are findings, not omissions.
inventory:
  - practice: AGENTSEC03
    component: outbound_3lo_oauth
    # Binary present/absent hid six real findings on a live assessment — a component
    # that is thoroughly built and delivers nothing still reads as "present".
    state: present | present_but_ineffective | absent | absent_by_design | not_applicable | unknown
    # Required when present_but_ineffective. `effective: false` alone was near-useless —
    # measured across five records, 13 of 14 such rows said `false`, restating the state.
    # never_invoked is the severe class: the control exists, reads as present in review, and
    # has zero call sites. It is what makes a record `redesign_first` rather than migrate_plus.
    ineffective_because: never_invoked | partially_covers | misconfigured | unverifiable
    evidence: <file:line, or why unknown>
    verdict: migrate | migrate_plus | keep | delete | regress | stay | gap | correctly_absent
    note: <one line>
    customer_agrees: true | false | undecided

# Gaps in the CURRENT system, true whether or not they migrate. Reported first.
current_gaps:
  - practice: AGENTOPS06
    gap: no golden eval set
    severity: high | medium | low
    in_scope: true | false | undecided
    # Severity does not imply sequence. Without this, /plan reconstructs Phase 0 from the
    # free-text `recommendation` comment — which on a real record omitted one of its own
    # two TOP FINDINGS, so the generated plan shipped with no destructive-action gate.
    fix_first: true | false              # belongs in Phase 0 / R1, before any platform move

# Artifacts you needed and could not read. Often one cause behind many `unknown`s: on a real
# record an unversioned ConfigMap was the sole source of 16 env vars and produced three
# blocking unknowns. That is the most actionable line in the record — do not bury it in a
# comment. Absent here means "I had everything", so leaving it off is a claim.
missing_artifacts:
  - artifact: <what, e.g. ConfigMap copilot-config>
    blocks: [<practice ids or gate names>]
    how_to_get_it: <one line>

lens_coverage:
  scope: questions_only                # NOT a Well-Architected review
  questions_assessed: <n of 41>        # DISTINCT practices, not rows
  # State both. inventory carries one row per *component*, so several rows share a practice:
  # measured across five records, 41 distinct practices spanned 55-66 rows. A reader who
  # counts rows and sees 66 against "41" concludes the coverage claim is inflated.
  inventory_rows: <n>
  best_practices_assessed: 0           # the Lens has 150; this triage covers none of them
  not_assessed: [AGENTSEC09]           # with a reason per entry in open_questions

recommendation: migrate | migrate_partially | stay | redesign_first
# Two axes. A Gate 0 certainty with no telemetry is high/unavailable, not "low".
recommendation_confidence: high | medium | low
cost_confidence: high | medium | low | unavailable
open_questions: []

dissent:
  - topic: <what>
    our_view: <what we said>
    customer_view: <what they said>
    resolution: proceeding_with_customer_choice
```

## Step 6 — Present it

→ Owner: **`references/production-inventory.md`**, "Reporting" — the bucket order, and why
gaps come before benefits.

State Lens coverage explicitly and accurately: how many **questions** of 41, which not, and
why — and that the Lens's 150 best practices were not walked. Lead with any `regress` or
`stay` verdicts.

If the customer disagrees, write it to `dissent` and move on. Recording disagreement is what
makes the record theirs; winning the argument is not the goal.

**Do not run `/plan-agentcore-migration`.** The customer edits the record first.
