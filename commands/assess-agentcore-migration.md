---
description: Assess an EKS-hosted agent for migration to AgentCore Runtime; emits an editable decision record
argument-hint: "[repo-path] [--region <region>] [--depth quick|full]"
allowed-tools: Bash, Read, Glob, Grep, Write, Edit
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

**Say what `quick` costs, and pick on evidence rather than by default.** The stars mark what most
often decides a *migration*, not what most often turns out to be broken — so `quick` can miss the
findings a customer would act on. Measured both ways: on one service the three sharpest findings
(a fabricated citation, a platform feature failing 100% of writes, a false capability claim in its
own description) were all on **unstarred** practices and `quick` would have produced a
clean-looking record. On another, the four highest-severity findings were all on **starred**
practices, and `quick` would have found them in a fraction of the time.

So: `quick` is a legitimate first pass when the customer wants a fast read or you are triaging
several services — **and say in the record that unstarred practices were not walked**, so nobody
mistakes it for a clean bill. Choose `full` when the service is the one they are betting on, when
anything looks off, or when a `quick` pass already found something.

## Step 0 — Locate the service and the account

If `$ARGUMENTS` names a repo path, use it. If empty, ask which repository holds the agent —
do not guess or scan the filesystem.

State the AWS account and region you are inspecting before any API call. **Read-only
throughout.** If the identity might be production, say so and confirm before proceeding.

**Findings come from their code and their deployment — not from the internet.** Every claim in the
record traces to their repo, their cluster's control plane, their telemetry, a live AWS API, or
something they told you. Do not research the customer's stack on the web to fill gaps.

Two reasons this matters more than it sounds:

- **It manufactures confidence.** A blog post or a vendor page describes what software is *meant*
  to do. The record is supposed to say what *this deployment actually does*, and those differ
  constantly — a shipped field the runtime ignores, a control with zero call sites, a proxy that
  has never served a request. Web reading cannot distinguish them; reading the cluster can.
- **Most customers are not researchable.** An internal service has no documentation you can find.
  A method that leans on public material silently works only for well-known open-source
  platforms and degrades exactly where the customer is most typical.

Where the deployed software is third-party open source, the legitimate move is **not** its
marketing or docs: pin its *published source to the release they run* (see
`references/evidence.md`, level 5 of the source hierarchy) and cite `file:line`. AgentCore
capability facts you need are already verified in
`deploy-on-agentcore/references/` and carry `[docs]` tags — read those rather than re-researching
them, and if one is missing or looks wrong, record it `[open]` and say so.

## Step 0.5 — Survey access, then emit the plan. Before any other step.

→ Owner: **`references/survey-and-plan.md`** — the six questions and the check graph they produce.

Six questions, all about **access and permission**. Asking them is not the questionnaire failure this
skill warns about: you cannot derive whether you are allowed to do something. Everything derivable
stays derivable — do not ask about their architecture here.

Then **write the `access` and `plan` blocks into the record before walking anything.** The graph
decides which checks are reachable; a check whose precondition is unmet is recorded `unreachable`
with its blocker and the substitute you used — never dropped, and never turned into a finding about
the customer.

**Why this is a step and not advice.** Every previous version put the conditionals in prose and the
same thing happened each time: checks that did not apply were performed, checks that did apply were
skipped, and "I was not permitted to look" was recorded as `absent`. A precondition in prose gets
dropped by anyone running low on context. A precondition in a graph does not.


## Step 1 — Gate 0: hard blockers

→ Owner: **`references/constraints.md`** (what the gates are, cost to resolve, escape hatches)
→ Method: **`references/probe-aws.md`** (the live quota, region and price reads — node C) and
**`references/read-the-repo.md`** (image architecture, protocol, session shape — node A1)

Read thresholds live; never carry numbers in from the plugin. Record each gate with an evidence
tag and one of the `gate0[].result` values in the schema below — **`fail` is not the only
non-pass**, and treating it that way turns cheap rebuilds into refusals.

**Gate 0.0 first: is the agent a liftable unit at all?** Every other gate assumes it is. See
`references/constraints.md`.

**On a real blocker, stop the *cost and planning* work — not the inventory.** Reporting a
blocker in ten minutes beats a thorough assessment of an impossible migration, and you should
say so immediately. But do not skip Gate 1: on one assessed service, obeying an unqualified
"stop" would have suppressed a live cross-client data exposure, a prompt-injection path into
legal findings and a total absence of audit trail — all true whether or not they ever migrate,
and all of it the value the customer actually gets from the engagement.

So: report the blocker first and prominently, skip Gate 2 entirely, and still walk the
inventory. Note in `gate0` which compute type you gated against — three gates (GPU,
architecture, session duration) differ between microVMs and Instances.

**Exception — `not_a_liftable_unit` is not that kind of blocker.** Where Gate 0.0 finds the
deployable unit is the platform rather than the agent, **do not skip Gate 2**: the recommendation
becomes "keep the runtime, adopt what needs no move", and the customer still needs their current
run rate to judge it. Skipping the economics there withholds the numbers from precisely the case
where the answer is to stay. Record `result: not_a_liftable_unit`, keep going, and let the
adoption path be the deliverable.

## Step 1.5 — Read what the running system already emitted

→ Owner: **`references/read-the-cluster.md`** (nodes A2 and B).

**Do this always. It is read-only, needs nobody's permission, and it has been the single most
decisive read available.** A swallowed exception is invisible in config, in the resource specs, and
in a successful-looking answer — it shows up only in the logs and in `status` conditions.

## Step 1.6 — Invoking the agent: ask first, and expect the answer to be no

→ Owner: **`references/invoke-the-agent.md`** (nodes F, F1, F2) — the five probes, and the
read-only substitutes for each when consent is refused.

Sending a turn through a customer's agent is **not a read.** Get explicit permission, name what it
will touch, and prefer a non-production tenant. If you cannot get that, this step does not happen.

Expect it to be unavailable more often than not — a first assessment typically has a repo and
read-only cluster access and nothing else. **That is not a gap in your work.** Record
`invocation: not_permitted` (distinct from `absent` or `unknown`) and note which findings could not
be reached, so the record shows the *class* of evidence missing rather than implying the controls
were fine.

## Step 2 — Gate 1: walk the inventory

**Resolve the graph first — do not decide reachability by eye.** The 41 questions have access
preconditions and depend on each other, and nobody holds that in their head:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lens_plan.py" --access .agentcore-migration/decisions.yml \
    --multi-agent yes|no|unknown
```

It prints four buckets — `reachable`, `degraded` (a prerequisite question is unanswerable),
`blocked` (access missing), `not_applicable` (shape) — each with its blocker and the substitute to
use. **That output is the walk.** Measured on real profiles: a repo-only engagement reaches 8 of 41;
cluster plus logs plus invocation reaches 20. Handing a customer "your access decisions mean we can
answer 8 of 41 questions" is often the most actionable line in the document.

Anything not `reachable` goes in the record with its blocker, as `unknown` or `not_applicable`.
**None of it is `absent`** — absent is a claim about their system, and this is a claim about your
access. Conflating them is how this instrument has repeatedly manufactured gaps.

→ Graph: **`references/lens-graph.yaml`** — preconditions, dependencies and substitutes per question.
→ Owner: **`references/production-inventory.md`** — the 41 Lens *questions* (the Lens also has 150
best practices, which this does not cover), how to detect each, and a verdict per question. **Read
its "How to fill a row" block first** — the verdict column is one field of several, and not the one
the customer acts on.
→ Session topology has its own reference: **`references/topologies.md`**
→ Searches for the commonly-missed domains: **`references/read-the-repo.md`**
→ Controls that exist and do nothing: **`references/sweep-for-dead-controls.md`** (node E). Every
search is positive signal; on two assessments the highest-severity findings were all negative.

Record per row: `state`, `evidence` (`file:line`), `closed_by`, `requires_runtime_move`, `verdict`,
`note` — plus `defect_owner` where a platform-supplied control is ineffective.

**Do not shortcut to the components you expect to find.** Empty rows are the most valuable
output. If you cannot assess a practice, record it `unknown` with the reason — never omit it.

## Step 3 — Gate 2: measure, then model

→ Owner: **`references/cost-model.md`** (what to report, and what not to)
→ Method: **`references/measure.md`** (node H — where the four numbers come from, and the 128 MB
billing floor to check before investing in precision)
→ Under load: **`references/concurrency-sweep.md`** (node I — consent-gated, and not the first step)

If the numbers cannot be measured, mark them `open` and say the cost verdict is unavailable.
**Do not substitute this plugin's reference figures as if they were the customer's.**

## Step 4 — Gate 3: ask only the underivable

→ Owner: **`references/ask.md`** (node J — the question list, and why it is short)

Now, and only now, ask. You have earned specificity by doing the work.

## Step 5 — Write the decision record

`.agentcore-migration/decisions.yml`. This schema is the one thing this file owns:

```yaml
assessed_at: <iso8601>
service: <name>
account: <id>
region: <region>
depth: quick | full

# Survey answers, verbatim, from Step 0.5. These gate the check graph — see survey-and-plan.md.
# Recording them first is what stops an unasked question later reading as an answered one.
access:
  source_available: true | false | unknown
  source_matches_deployment: true | false | unknown
  control_plane_read: true | false
  account_is_customers: true | false
  deployment_exists: true | false
  carries_real_traffic: true | false | unknown
  invocation_permitted: true | false | unknown        # gates node F
  invocation_environment: production | staging | pilot | none
  load_generation_permitted: true | false | unknown   # gates node I
  product_owner_reachable: true | false               # gates node J

# One entry per node in the check graph. `unreachable` is a statement about what evidence was
# available, NOT a finding about the customer — conflating the two manufactures gaps.
plan:
  - node: <A|A1|A2|B|C|D|E|F|F1|F2|G|H|I|J|K>
    state: done | unreachable | not_applicable
    blocked_by: <survey id or node id>    # required when unreachable
    substitute: <what you did instead, or none available>

# Who owns the code, because it decides whether `redesign_first` is even actionable and whether
# `customer_agrees` means anything. On a third-party platform the remedies are chart config, an
# upstream contribution or fork, and operator-owned cluster controls — none of which is
# "redesign the service", and repeating `undecided` on every row reads as "we asked and they
# have not replied" when in fact there is nobody to ask.
engagement: customer | internal_reference | upstream_project
code_ownership: customer_owned | third_party_oss | vendor_managed | mixed

gate0:
  - check: single_turn_duration        # see constraints.md for the gate list
    # trivial_fix = a real gate that a one-line change clears (e.g. an amd64 pin).
    # Do NOT bucket it with an unresolvable blocker; see constraints.md's cost column.
    # not_a_liftable_unit: the deployable unit is the platform, not the agent (Gate 0.0).
    # It is NOT `fail` — it reframes the engagement rather than ending it, and unlike `fail`
    # it does not skip Gate 2.
    result: pass | trivial_fix | needs_redesign | not_a_liftable_unit | fail | unknown
    compute_type_assumed: microvm | instances   # three gates flip between them
    # Full tag set is defined in references/evidence.md — keep these in sync.
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
  # per_event is finer than every_turn: state written synchronously mid-turn, several rows per
  # turn. It inverts a conclusion — AgentCore Memory's per-turn write is then a WASH rather
  # than the write-volume increase the topology reference prices.
  flush_cadence: per_event | every_turn | every_n_turns | interval | on_evict | none
  # `none` is a real answer, not an omission — a team shipping it may have accepted the loss
  # deliberately. Migration then *improves* durability rather than costing writes.
  #
  # Name the EFFECT, not the mechanism. last_write_wins understates the common case: two
  # concurrent turns on one conversation is not a lost write but interleaved history, and no
  # store-level fix addresses it. Measured two ways on two platforms — a shared in-memory list,
  # and an unserialized append-only table with no uniqueness constraint — so an
  # implementation-named value fits one and not the other.
  concurrent_turn_safety: safe | serialized | last_write_wins | interleaved_history | unknown

# evidence: <tag> means the full tag set from references/evidence.md — measured:customer, measured:reference,
# read:source, read:cluster, stated:customer, verified, docs, reasoned, open. A two-value
# measured|open enum here could not say WHOSE workload a number came from, which is the whole
# point of the tag table.
measurements:
  # Order-of-magnitude context is REQUIRED next to any compute verdict: on a real customer
  # the compute delta was 0.04-1.2% of run rate, dominated by tokens and the datastore.
  monthly_token_cost_estimate: { value: <usd>, evidence: <tag> }
  monthly_datastore_cost: { value: <usd>, evidence: <tag> }
  # Required WHENEVER you state a compute verdict — but it is a ratio over monthly run rate, so
  # at zero volume it is unsatisfiable and `open` is the correct answer. Do not invent a
  # denominator, and do not let its absence license a compute verdict: no share means no verdict.
  compute_share_of_run_rate: <pct> | open
  tokens_per_turn: { input: <n>, output: <n>, invocations_per_turn: <n> }
  cpu_seconds_per_turn:  { value: <x>, evidence: <tag> }
  wall_seconds_per_turn: { value: <y>, evidence: <tag> }
  # cgroup_memory_peak is the only true high-water mark and the quantity AgentCore bills on —
  # prefer it. But it needs a shell in the container, and a DISTROLESS image has none
  # (`kubectl exec -- sh` returns "executable file not found in $PATH"), while `kubectl debug`
  # mutates the pod. Allow the fallbacks and label them, because an assessor forced to choose
  # between a wrong label and an invalid one will write an invalid one.
  peak_memory_gb:
    value: <z>
    evidence: <tag>
    source: cgroup_memory_peak | kubelet_stats_summary | container_insights | unavailable
    # true ONLY for cgroup_memory_peak. Everything else is a sampled maximum, i.e. a floor on
    # the real peak — so it may understate the bill and must not be called a peak.
    is_true_high_water_mark: true | false
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
    # platform_provides / available_unconfigured matter on a declarative or managed platform,
    # where a mechanism exists that the customer neither built nor switched on. Without them,
    # "upstream built this well and it ships off by default" is recorded as the customer's
    # failure — which misattributes the fix as well as the fault.
    #
    # platform_provides asserts a WORKING guarantee. Do not use it for something configured but
    # unproven: observed a platform feature Accepted=True, Ready=True, indexed in the schema,
    # with a 100% write-failure rate from a bug in the platform's own translation layer. That is
    # present_but_ineffective + ineffective_because, and `defect_owner` says whose bug it is —
    # otherwise the record aims the fix at the customer.
    state: present | present_but_ineffective | platform_provides | available_unconfigured
         | absent | absent_by_design | not_applicable | unknown
    # Required when present_but_ineffective on a platform-supplied mechanism.
    defect_owner: customer | platform | operator_config | unknown
    # Required when present_but_ineffective. `effective: false` alone was near-useless —
    # measured across five records, 13 of 14 such rows said `false`, restating the state.
    # never_invoked is the severe class: the control exists, reads as present in review, and
    # has zero call sites. It is what makes a record `redesign_first` rather than migrate_plus.
    # fails_at_runtime: it IS invoked, it runs, and it throws every time. Not never_invoked
    # (it runs), not partially_covers (it covers nothing), not misconfigured (no setting is
    # wrong — it is a code defect), not unverifiable (you verified it). Two assessments hit
    # exactly this and the enum could not hold it.
    ineffective_because: never_invoked | fails_at_runtime | partially_covers | misconfigured
                       | unverifiable
    evidence: <file:line, or why unknown>
    verdict: migrate | migrate_plus | keep | delete | regress | stay | gap | correctly_absent
    # What would close this, and whether it requires moving the runtime. The field the customer
    # acts on. Do NOT reach for an AgentCore component to make a row look productive — and do
    # not fall back to `none`, which reads as "nothing can fix this".
    #
    # Measured on a real assessment: an AgentCore-only enum forced 36 of 60 rows to `none`,
    # when most were one already-shipped platform field away. Non-AgentCore answers first,
    # because they are cheaper for the customer and they are what earns the rest a hearing:
    #   platform_config       — a field/flag their platform already ships, switched off
    #   customer_code         — their own code; no purchase, no platform change
    #   cluster_config        — RBAC, NetworkPolicy, secrets, resource limits
    #   iam_policy            — an AWS role, policy or encryption setting. NOT cluster_config;
    #                           two independent assessments had to invent this value
    #   upstream_contribution — third-party OSS with no such field yet; a PR or fork
    #   none                  — genuinely nothing closes it; say why in `note`
    closed_by: platform_config | customer_code | cluster_config | iam_policy
             | upstream_contribution
             | gateway | policy | identity | memory | evaluations | observability
             | code_interpreter | browser | runtime | none
    # For platform_config: did you confirm the field takes effect on the runtime they run?
    # A field can be accepted and validated by the control plane and silently ignored by the
    # executor. Unverified remedies are how a cheap recommendation becomes a wrong one.
    remedy_verified: true | false | unverifiable
    # false for everything except runtime-coupled items (session isolation, inbound CUSTOM_JWT,
    # scale-to-zero, the duration ceilings). Report these FIRST — they are actionable now.
    requires_runtime_move: true | false
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

# The headline. `adopt_components` is the most common honest answer and is NOT a lesser
# outcome: keep the runtime where it is, adopt the components that close real gaps.
# `assess_only` is legitimate too — findings delivered, no change recommended yet, with the
# condition that would change it named.
recommendation: migrate | migrate_partially | adopt_components | assess_only
               | stay | redesign_first

# Ordered adoption path, most valuable first. Everything with requires_runtime_move: false
# comes before anything that needs a replatform, because it is what they can do this quarter.
adoption_path:
    # MUST accept every closed_by value, not just the AgentCore ones. When this enum was
    # AgentCore-only, a record whose gaps mostly closed with a config flag could not put those
    # first — so obeying it literally placed a purchase at position 1 on a service where almost
    # nothing needed one, contradicting this file's own "lead with no-platform-change" rule.
  - component: platform_config | customer_code | cluster_config | iam_policy
             | upstream_contribution
             | gateway | policy | identity | memory | evaluations | observability | runtime
    closes: [<practice ids>]
    requires_runtime_move: true | false
    # What their existing platform keeps doing afterwards. If this is empty for every entry,
    # the recommendation has become an ultimatum — re-read it.
    coexists_with: <what stays, and keeps working>
    effort: <one line>

# What is NOT recommended to change, stated explicitly so the customer can see the assessment
# was not a pretext. An empty list here is a red flag, not a clean bill.
keep_as_is: [<component>: <why it is already right>]
# Two axes. A Gate 0 certainty with no telemetry is high/unavailable, not "low".
recommendation_confidence: high | medium | low
# Two axes. Complete per-turn measurements on a deployment with NO USERS means per_turn: high
# and monthly: unavailable — there is no volume to multiply by. One combined value forces a
# choice between implying a price you cannot give and denying measurements you have.
cost_confidence:
  per_turn: high | medium | low | unavailable
  monthly: high | medium | low | unavailable
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
