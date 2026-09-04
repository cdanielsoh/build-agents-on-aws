---
description: Turn an AgentCore migration decision record into a phased plan plus scaffolding; makes no live changes
argument-hint: "[--record <path>] [--phase <n>]"
allowed-tools: Bash, Read, Glob, Grep, Write, Edit
---

# Plan an AgentCore Runtime migration

Arguments: `$ARGUMENTS`

Load the **migrate-eks-to-agentcore** skill, and **deploy-on-agentcore** for the
build details of anything you scaffold.

**This command changes nothing that is running.** It writes a plan and generates
scaffolding into new paths. It does not deploy, does not shift traffic, and does not
modify the customer's live service. If asked to go further, say that cutover is
deliberately manual: it is where judgment matters most and it varies too much per
customer to template.

## Step 1 — Load and validate the record

Read `.agentcore-migration/decisions.yml` (or `--record`). If it does not exist, stop
and tell the user to run `/assess-agentcore-migration` first — do not reconstruct it
from conversation, because the record is what makes the customer's choices binding.

Refuse to plan, and say why, if:
- `recommendation` is `stay` — unless the user explicitly overrides, which goes in
  `dissent`
- any `gate0` entry is `fail` and unresolved
- `recommendation` is `redesign_first` — plan the redesign, not the migration

If measurements are `[open]`, proceed but mark every cost statement in the plan as
unestablished. Do not quietly substitute this plugin's reference numbers.

Echo back what you loaded — topology, verdicts, dissent — so a stale record is caught
before it produces a plan.

## Step 2 — Order the work by reversibility

Cheap and reversible first. Each phase must be independently valuable, so the customer
can stop after any of them and still be better off.

**Phase 0 — fixes worth making regardless of the outcome.** Frame it that way: if the
customer stops here, they still gained. Only include items the record actually flagged.
- Event-loop blocking → `asyncio.to_thread`. Improves the current service *and* cuts
  the future AgentCore memory bill, since wall time is the meter.
- ARM64 image rebuild. Runs on EKS Graviton today — but needs a custom NodePool,
  because Auto Mode's `general-purpose` pool is hardcoded to amd64 `[measured]`.
- Concurrent-turn safety on the session store, if `last_write_wins`.
- A per-request budget ceiling if absent — no platform provides one.

**Phase 1 — parallel AgentCore runtime, no traffic.** Deploy the same image behind
`BedrockAgentCoreApp`, invoke it directly, compare answers against the EKS service on
the same prompts. Fully reversible: delete the runtime.

**Phase 2 — shadow traffic.** Mirror a sampled share of real requests. Compare
answers, latency, and cost. Still no user impact.

**Phase 3 — cutover.** Customer-driven, per their sign-off. Keep EKS warm and
scaled-down, not deleted. Name the rollback trigger and who pulls it.

**Phase 4 — decommission.** Only after an agreed observation window. List exactly what
gets deleted, and what must be kept (the knowledge store, IAM roles still in use).

## Step 3 — Generate scaffolding

Only for components the record marks `migrate` or `migrate_plus`. Write to new
paths; never overwrite the customer's working files. Show a diff summary before writing.

- **`app.py`** — `BedrockAgentCoreApp` entrypoint reusing their existing agent
  construction. The point is that the agent itself is unchanged; only the surface
  differs.
- **`Dockerfile`** — ARM64, `/ping`, port 8080. Keep their existing entrypoint working
  so one image serves both platforms during phases 1-3.
- **CDK** — runtime, IAM execution role, ECR. Follow `deploy-on-agentcore`'s
  `cdk-infrastructure.md` and `naming.md`.
- **VPC mode + endpoint set** — *only* if their knowledge store is VPC-resident. Then
  it is mandatory and cascading: `bedrock-runtime`, `ecr.api`, `ecr.dkr`, an S3
  **gateway** endpoint for image layers, `logs`, `xray`, `monitoring`, `ssm`, `sts`.
  A missing endpoint does not fail the deploy — it hangs the agent at runtime with
  nothing in the logs naming what was unreachable. Assert the set in a test.
- **Session lifecycle** — `StopRuntimeSession` at end-of-conversation if the record
  recommends it. This is the largest cost lever; do not leave it as a comment.
- **Deletions listed, not performed.** Enumerate what becomes dead code (session
  store, agent cache, HPA/KEDA, ingress, probes) and let the customer delete it after
  cutover.

## Step 4 — Write the plan

Write `.agentcore-migration/plan.md`. Per phase: goal, steps, verification, rollback,
owner, and what is still `[open]`.

Include a **verification** section per phase, because on both platforms success at the
tool boundary does not mean the thing runs. Two observed examples worth encoding as
checks: `kubectl apply` returns success while a Pod Security violation silently
prevents any pod from being created, and `eksctl` exits 0 on a rolled-back cluster.
Verify observed state, never the exit code.

## Step 5 — Hand it over

Summarise the phases, the first concrete action, and the decision points that remain
the customer's. Restate what does not go away. If the plan rests on `[open]`
measurements, say which numbers would change the recommendation and how to get them.
