---
description: Turn an AgentCore migration decision record into a phased plan plus scaffolding; makes no live changes
argument-hint: "[--record <path>] [--out <dir>] [--phase <n>]"
allowed-tools: Bash, Read, Glob, Grep, Write, Edit
---

# Plan an AgentCore Runtime migration

Arguments: `$ARGUMENTS`

Load the **migrate-eks-to-agentcore** skill, and **deploy-on-agentcore** for the build details
of anything you scaffold.

**This command writes files; it changes nothing that is running.** It does not deploy, shift
traffic, or modify the customer's live service. If asked to go further, say that cutover is
deliberately manual — it is where judgment matters most and varies too much per customer to
template.

One honest exception to flag rather than hide: keeping one image serving both platforms
**does** require a change to the running EKS Deployment (see Step 3). Schedule it in Phase 0;
do not perform it here.

## Step 1 — Load and validate the record

Read `.agentcore-migration/decisions.yml`, or `--record`. If absent, stop and say to run
`/assess-agentcore-migration` first — do not reconstruct it from conversation.

**`gate0` results, and what each means for planning.** Enumerate every entry; do not treat
anything other than `pass` as fatal:

| `result` | Plan accordingly |
|---|---|
| `pass` | nothing to do |
| `trivial_fix` | a Phase 0 line item (e.g. an amd64 pin). **Not** a refusal |
| `needs_redesign` | plan the redesign as its own phase, and cost it before Phase 1 |
| `fail` | **refuse to plan the migration.** Say which gate and why |
| `unknown` | **do not plan past Phase 0.** An unevaluated gate is not a passed gate — name what would resolve it |

**Read `fail` against the remedy, not the word.** Records written before `trivial_fix` existed —
and assessors who never reach for it — record `image_architecture: fail` for an amd64 image,
which a rebuild clears and which this command's own Phase 0 calls routine. Observed on a real
record: a header saying "no hard blockers found" above a `fail` entry. If the remedy is a
rebuild, a pin, or a config change, treat it as `trivial_fix`, plan it into Phase 0, and note
that the record used the wrong value. Refuse only where the remedy is a platform capability the
customer cannot supply.

Also check `gate0[].compute_type_assumed`. GPU, architecture and session duration differ
between microVMs and Instances, so a `fail` gated against microVMs may be a `pass` on
Instances. If the record does not say, treat the gate as `unknown`.

Refuse, and say why, if `recommendation` is `stay` (unless explicitly overridden, which goes in
`dissent`). `redesign_first` is **not** a refusal — it selects a different plan; see Step 2b.

**Check the record against the current instrument, not just against the system.** Records go
stale two ways. Compare its schema to the current
[assessment.md](../skills/migrate-eks-to-agentcore/references/assessment.md) and the
`/assess-agentcore-migration` template, and note divergences at the top of the plan. Observed:
a record carrying a single `confidence` field and quoting a rubric line that no longer exists,
written weeks after the reference split it into two axes. A stale field silently reintroduces
the reasoning error the split was made to prevent — so echoing content back does not catch it.

**Read `recommendation_confidence` and `cost_confidence` separately.** With
`cost_confidence: unavailable`, every cost statement in the plan is marked unestablished and no
phase gets a business case. That does not weaken a `recommendation_confidence: high`.

**Check the customer actually engaged with the record.** If every `customer_agrees` is
`undecided` and `dissent` is empty, the customer has confirmed nothing — say so at the top of
the plan and treat every verdict as provisional. That is the stale-record condition, and it is
common.

Echo back topology, `work_unit`, verdict counts, any `regress`, and dissent, so a stale record
is caught before it produces a plan.

## Step 2 — Order the work by reversibility

Cheap and reversible first. Each phase must be independently valuable, so the customer can stop
after any of them and still be better off.

**Reversibility only discriminates once infrastructure is involved, so carry a tie-break.** On a
single-service plan every phase is `git revert` plus the previous image tag — minutes, no data
risk — and the organizing principle ranks nothing. Then order by: **exploitable today → makes it
measurable → makes it verifiable → what the platform move needs.** Measurable precedes
verifiable because a golden set drawn from imagination tests the author's guesses; one drawn from
a week of real logged turns tests the service. State which axis you used, because a reader will
assume reversibility.

One ordering constraint that is *not* a tie-break and must be honoured: **where a change makes
existing stored data unreadable by the old code — turning on encryption at rest, changing a
serialization format — that patch reverts last.** Rolling it back first orphans every row written
while it was live.

**Phase 0 — fixes worth making regardless.** Read the record's own Phase-0 / fix-first entries
and order them by severity. Do **not** work from a generic checklist: on real records, half the
standard items are structurally inapplicable (there is no event loop to unblock, no store to
race), and the highest-value items are service-specific. Two that are easy to miss and often
top the list:

- **structured request logging, if absent** — frequently one change that closes several audit
  and attribution gaps *and* unblocks Gate 2 measurement
- **an arm64 NodePool**, if you are rebuilding for ARM64 and the cluster runs EKS Auto Mode —
  its `general-purpose` pool is amd64-only, so "runs on Graviton today" is false by default

**Phase 1 — parallel runtime, no traffic.** Same image behind `BedrockAgentCoreApp`, invoked
directly, answers compared against the EKS service. Fully reversible: delete the runtime.

**Phase 2 — shadow traffic.** Mirror a sampled share of real requests; discard the responses.

**Name the mirroring point explicitly, and treat "there isn't one" as a real outcome.** A
ClusterIP service with no ingress and no mesh — typical of internal tools — has nowhere to
mirror from. Then Phase 2 relocates into the *caller*, which may be a different repo and a
different team. Say that rather than writing a phase nobody can execute.

**Phase 3 — cutover.** Customer-driven, on their sign-off.

**Do not start Phase 3 without a numeric rollback trigger.** Error rate, p95, cost/day, and the
name of who pulls it. If those are `open` in the record, say plainly that Phase 3 cannot begin
until they are set — a phase with no abort condition should not start.

Scale in-flight advice to `work_unit`. If a unit of work is one request, there are no in-flight
*conversations* to drain and the pin-existing-sessions advice is a non-question — drop it
rather than shipping it as boilerplate.

**Phase 4 — decommission.** Only after an agreed observation window, stated as a date. List
exactly what gets deleted and what must be kept.

## Step 2b — If the recommendation is `redesign_first`, these are the phases

Everything in Step 2 assumes a migration. Under `redesign_first` there is no parallel runtime,
no shadow traffic, no cutover and no CDK — and the playbook's Phase 0 is a pre-migration
checklist, not a plan that stands alone. Use R0–R5, then re-enter Step 2 at Phase 1 only if R5
says so:

| | Phase | Exit criterion |
|---|---|---|
| **R0** | Recover missing artifacts and get the record reviewed | every `missing_artifacts` entry resolved or declared permanent; `customer_agrees` no longer uniformly `undecided` |
| **R1** | Close what is exploitable today | each `severity: high` gap has a merged patch. Pin dependencies **first** — until the build is reproducible, no later phase is a controlled experiment |
| **R2** | Make it measurable | the four Gate 2 numbers land in telemetry, from the running service |
| **R3** | Make it verifiable | a golden set drawn from R2's logged turns, wired as a CI gate |
| **R4** | Forward-compatible changes only | things that improve the service now *and* the migration later — ARM64, an arm64 NodePool, structured logging |
| **R5** | Re-assess | re-run `/assess-agentcore-migration` with R2's measurements and the Gate 3 answers |

R5 is the point of the whole structure: `redesign_first` usually means the record could not see
enough to recommend anything, so the plan's deliverable is a **better record**, not a migration.
Say that plainly — the customer is buying a decision, and this plan defers it on purpose.

Each R-phase still needs the Step 2 fields: goal, steps, verification, rollback, owner, `[open]`.
Order them with Step 2's tie-break, since reversibility will not separate them.

## Step 3 — Generate scaffolding

**Start from the shipped template, do not hand-type it.** `scripts/scaffold.py` materializes a
`strands-agentcore` template that already has the ARM64 base, the `opentelemetry-instrument`
CMD, `/ping`, and the "do NOT set `OTEL_*` yourself" comment — the details most often got wrong
from memory:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scaffold.py" <out>/scaffold --template strands-agentcore --dry-run
```

Then delete what does not apply and adapt the rest. Note the template puts the entrypoint at
`agent/app.py`, inside a package — which is why the naming trap below does not bite it.

Scaffold for components the record marks `migrate` or `migrate_plus`. **Under `redesign_first`,
scaffold across `gap` rows too** — that verdict means most of the work *is* the gaps, and a
`migrate`-only filter would skip "no tool authorization exists" and "no instrumentation exists",
the second of which gates Gate 2 by construction. Write to new paths; never overwrite working
files.

- **AgentCore entrypoint** — reuse their existing agent construction unchanged; the point is
  that only the surface differs. **Do not name it `app.py` if a package or module of that name
  already exists.** Verified failure: a root `app.py` beside an `app/` directory without
  `__init__.py` shadows it, and `from app.agent import ...` raises
  `ModuleNotFoundError: No module named 'app.agent'; 'app' is not a package`. Name it
  `agentcore_app.py` or similar.
- **Dockerfile** — ARM64 (microVMs) or the target architecture (Instances), `/ping`, port 8080.
  **One image serving both platforms needs both halves:** `CfnRuntime`'s container
  configuration carries only a container URI and no command override, so the image `CMD` must
  be the AgentCore entrypoint, and the **EKS Deployment must override it** with `command:`/
  `args:`. That override is a change to the running service — put it in Phase 0, flag it, and
  do not apply it here.
- **CDK — three stacks, not two.** Runtime + execution role, ECR, **and an image-build stack**.
  The build stack is not optional: it is how you get an arm64 image without local tooling and
  how you get a content-addressed tag. A mutable tag makes a later deploy a silent no-op. See
  `cdk-infrastructure.md` and `naming.md`.
- **Network** — if the knowledge store is VPC-resident, `networkMode = "VPC"` plus the full
  endpoint set (`bedrock-runtime`, `ecr.api`, `ecr.dkr`, S3 **gateway**, `logs`, `xray`,
  `monitoring`, `ssm`, `sts`, and `eks-auth` if Pod Identity is in play). A missing endpoint
  hangs the agent at runtime with nothing in the logs naming it — assert the set in a test.
  **If it is not VPC-resident, assert `PUBLIC` in a test**, so a later switch cannot land
  without the endpoints.
- **Session lifecycle** — implement `StopRuntimeSession` where the record recommends it, as
  code and not a comment. Where the record shows the cost/latency tension (stopping sessions
  versus warm reuse), make it one switch with the trade documented at the switch.
- **Verification harness** — Phase 1 and Phase 2 both have equivalence exit criteria, and if
  `AGENTOPS06` is a `gap` there is nothing to compare against. Do not silently over-build an
  eval suite the record says they do not have. Follow the playbook: offer it, and if declined,
  record parity as *spot-checked, not demonstrated*.
- **Deletions listed, not performed.** Enumerate what becomes dead code and let the customer
  delete it after cutover. If that list is nearly empty, say so — it is an honest headline, not
  a failure of the plan.

## Step 4 — Write the plan

Write to `--out` if given, else `.agentcore-migration/`. Use a fixed layout so outputs are
comparable across engagements:

```
<out>/plan.md              the phased plan
<out>/scaffold/            generated files, mirroring the target repo layout
<out>/scaffold/MANIFEST.md every generated file, its purpose, and the record line it traces to
```

`MANIFEST.md` replaces any interactive diff review: in a non-interactive run there is no
"before" to diff against, so the auditable artifact is a list of what was written and why.

Per phase: goal, steps, verification, rollback, owner, and what remains `[open]`.

**Verify observed state, never exit codes.** Three observed cases worth encoding as checks:
`kubectl apply` returns success while a Pod Security violation prevents any pod being created;
`eksctl` exits 0 on a rolled-back cluster; `create-policy` returns 200 and the policy later
reaches `CREATE_FAILED`.

## Step 5 — Hand it over

Summarise the phases, the first concrete action, and the decision points that remain the
customer's. Restate what does not go away. If the plan rests on `open` measurements, say which
numbers would change the recommendation and how to get them.
