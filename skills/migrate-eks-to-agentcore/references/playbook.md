# Migration playbook: sequencing, parallel run, rollback

Ordered by reversibility, cheapest first. Every phase must be independently valuable —
the customer can stop after any one and still be better off. That property is what makes
the plan safe to start, and it removes the all-or-nothing framing that stalls these
decisions.

## Which of these phases you may actually write

**Resolve before you sequence.** Every phase below has preconditions, and they used to live here
as prose — which is the arrangement that lost steps on the assessment side until the check graph
existed.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lens_plan.py" phases
```

[plan-graph.yaml](plan-graph.yaml) holds the phases, their `after` edges and fourteen predicates
computed from the record. Each phase resolves to one of four states, and the distinction between
the middle two is the one worth keeping:

| State | Write |
|---|---|
| `executable` | the phase, normally |
| `degraded` | the phase, with each unknown named as an assumption, its consequence if wrong, and who can settle it |
| `blocked` | **not a phase.** A section on what is unavailable and why |
| `not_applicable` | nothing — the outcome selected the other track |

**Unknown is not false.** A phase degraded because nobody answered S3 is a different claim from one
blocked because the mirroring point is `none`. Collapsing them is the same conflation that made
`unreachable` read as `absent` on the assessment side, and it manufactures blockers.

`blocked` cascades through `after`, so "do not plan past Phase 0 while a gate is `unknown`" is
enforced rather than remembered.

**Two tracks, selected by the record's `outcome`.** Phases 0–4 below are the migration track. Under
`redesign_first` or `stay` they all resolve `not_applicable` and [the R-track](#the-redesign-track--r0r5)
is the plan instead.

## Phase 0 — fixes worth making regardless

Frame it exactly that way. If the migration is cancelled at the end of Phase 0, the
customer has still gained, which makes this the easiest phase to get approved and the
best evidence that the engagement is not a sales exercise.

| Fix | Lens | Value now | Value after |
|---|---|---|---|
| Blocking I/O → `asyncio.to_thread` | AGENTPERF02 | 39% p50 latency, 67% throughput `[measured]` | wall time is the AgentCore memory meter, so it cuts the future bill by the same margin |
| ARM64 image | AGENTOPS03 | runs on EKS Graviton (needs a Graviton NodePool — Auto Mode's default is amd64-only) | de-risks the hard platform requirement before committing |
| Per-request tool-call / token ceiling | AGENTCOST01 | bounds runaway loops today | no platform provides one |
| Concurrent-turn safety | AGENTREL03 | fixes a real, usually-unnoticed data-loss bug | migrating masks it, does not solve it |
| Conversation-end detection | AGENTCOST03 | session hygiene, quota headroom | becomes the largest cost lever (`StopRuntimeSession`) |
| A golden eval set, if absent | AGENTOPS06 | you can finally tell when the agent regresses | lets Phase 2 prove equivalence instead of asserting it |

**Only include what the decision record actually flagged.** A generic checklist reads as
boilerplate, and the customer can tell.

The eval row is **optional, and offered rather than imposed.** If there is no golden set,
Phase 2 can still compare latency, cost and error rate — it just cannot demonstrate equivalent
answer quality. State that limitation plainly and let the customer decide whether it is worth
building one first. Many migrate on spot-checks.

**Exit criteria:** current service measurably better; ARM64 image running in production on
EKS. (A golden set if the customer chose to build one — not required to proceed.)

## If there is more than one agent, phase by dependency — leaves first

Every phase below is written for **one** deployable unit, and an assessor found this file has no
multi-agent content at all. When a supervisor delegates to specialists:

1. **Move leaves first.** A specialist nothing else calls can be stood up in parallel and compared
   without touching a caller.
2. **A non-leaf move changes a live caller**, because the delegation transport is part of the
   caller's tool set — a code change plus a new permission on something already serving traffic.
   That is Phase 2 work, not Phase 1.
3. **Cut over in the same order**, callers last, and keep both delegation paths open until
   decommission, or reversing the supervisor forces reversing every specialist under it.
4. **Mixed mode is the steady state** for weeks, not a moment. Say which agents are where.

→ the axis, the tool-scope-versus-session-isolation correction, and the session-workload
multiplier live in [topologies.md](topologies.md).

## Phase 1 — parallel runtime, no traffic

Deploy the same image behind `BedrockAgentCoreApp`. Invoke it directly. Compare answers
against EKS on the same prompts.

- Same container, two entrypoints — theirs for EKS, a new one for Runtime. Keeping both
  working is what makes phases 1-3 reversible. **Do not assume their entrypoint is
  `server.py`**; read the Dockerfile `CMD`, because a repo whose CMD names a module that
  does not exist tells you the image is built from a different tree, which is a finding.
- **Only one of the two can be the image `CMD`.** `CfnRuntime`'s container configuration
  carries a URI and no command override, so the CMD must be the AgentCore entrypoint and
  the **EKS Deployment overrides it** with `command:`/`args:`. That override is a change
  to the running service — schedule it in Phase 0.
- Set `SESSION_BACKEND=memory` on Runtime — the microVM holds session state, so an
  external store would be a round trip for data already in process.
- **Do not** set `OTEL_*`. The ADOT sidecar configures the exporters, and overriding
  them silently breaks trace delivery. The opposite of the EKS rule.
- If the knowledge store is VPC-resident: `networkMode = "VPC"` plus the full endpoint
  set. Assert it in a test — a missing endpoint hangs at runtime with nothing in the
  logs naming it.

**Verify observed state, not exit codes.** Invoke it and read the answer; check
`[runtime-logs]` streams exist (a missing `logs:DescribeLogGroups` permission silently
produces no logs at all).

**Measure here:** microVM session-start latency against the EKS cold start (**61s**
measured, node provisioning included). Per-turn CPU and peak memory on Runtime, to
replace the modelled cost figures with observed ones.

**Rollback:** delete the runtime. Nothing else touched.

**Exit criteria:** answers equivalent on a representative prompt set; observed cost per
conversation replaces the estimate.

## Phase 2 — shadow traffic

Mirror a sampled share of real requests to Runtime. Discard its responses; serve users
from EKS.

- Start at 1-5%. Sample whole conversations, not individual turns — a mid-conversation
  sample has no history and will look broken for the wrong reason.
- Compare: answer equivalence, p50/p95 latency, error rate, cost per conversation.
- Watch the two quotas that bind — active session workloads, and new-session creation
  rate. Shadow traffic consumes both on top of production, so headroom that looked fine
  in assessment may not be. Check applied values, not defaults.

**Rollback:** stop mirroring.

**Exit criteria:** latency within budget; error rate at parity; cost confirmed at real volume.
Answer equivalence *if* a golden set exists — otherwise record explicitly that quality parity
was spot-checked rather than demonstrated, so nobody later believes it was proven.

## Phase 3 — cutover

Customer-driven, on their sign-off. Not automated in this playbook — cutover is where
judgment matters most and it varies too much per customer to template.

- Incremental: 5% → 25% → 50% → 100%, with an observation window at each step.
- **Keep EKS warm and scaled down, not deleted.** As long as the session store still
  works, reverting is a routing change rather than a recovery.
- Name the rollback trigger numerically before starting (error rate, p95, cost/day) and
  name who pulls it.
- Existing conversations: decide explicitly whether in-flight sessions drain on EKS or
  restart on Runtime. Pinning existing session ids to EKS and routing only new
  conversations to Runtime avoids the question entirely.

**Rollback:** shift routing back. Minutes, if EKS is still warm.

## Phase 4 — decommission

Only after an agreed observation window — at least one full traffic cycle including a
peak.

Delete: session store table, agent cache code, HPA/KEDA, ingress, ALB, the FastAPI
serving layer, probes, `preStop` hooks, node pools.

**Keep:** the knowledge store, IAM roles still referenced, the cluster itself if other
workloads use it, and enough of the old deployment manifests to reconstruct the service
if a latent problem appears months later.

## The redesign track — R0–R5

Selected when the record's `outcome` is `redesign_first` or `stay`. **Everything above assumes a
migration; none of it applies here.** There is no parallel runtime, no shadow traffic, no cutover
and no CDK — and Phase 0 is not a substitute, because it is a pre-migration checklist rather than a
plan that stands alone.

The reason this track exists: `redesign_first` usually means the record could not see enough to
recommend anything. So **the deliverable is a better record, not a migration.** Say that plainly —
the customer is buying a decision, and this plan defers it on purpose.

| | Phase | Exit criterion |
|---|---|---|
| **R0** | Recover missing artifacts, get the record decided | every `missing_artifacts` entry resolved or declared permanent; `decisions.yml` exists with a `decided_with` |
| **R1** | Close what is exploitable today | every `severity: high` triage entry with a `proceed` decision has a merged patch |
| **R2** | Make it measurable | the four Gate 2 numbers land in telemetry, from the running service |
| **R3** | Make it verifiable | a golden set drawn from **R2's logged turns**, wired as a CI gate |
| **R4** | Forward-compatible changes only | things that improve the service now *and* the migration later — ARM64, an arm64 NodePool, structured logging |
| **R5** | Re-assess | re-run the assessment with R2's measurements and the Gate 3 answers |

Three ordering facts that are not preferences:

- **Pin dependencies in R1, before anything else in it.** Until the build is reproducible no later
  phase is a controlled experiment and no comparison proves anything. It is a prerequisite, not an
  improvement, which is why `build_reproducible` degrades R1 rather than gating it — you can start,
  but the plan should say the first item makes the rest meaningful.
- **R3 depends on R2, not the reverse.** A golden set drawn from imagination tests the author's
  guesses; one drawn from a week of real logged turns tests the service.
- **An `unknown` gate does not block any R-phase.** The migration phases block on unevaluated gates
  because they commit to a platform the gate might rule out. R0–R5 commit to nothing and exist
  precisely to resolve unknowns, so plan all of them — the unknowns belong in R0's recovery list and
  R5's inputs.

**`StopRuntimeSession` has nowhere to live here.** A redesign produces no runtime to call it on.
Record it as an R5 input rather than scaffolding a call into a file nothing invokes.

Each R-phase still needs the same fields as a numbered phase: goal, steps, verification, rollback,
owner, and what remains `[open]`. Reversibility will not separate them — order by **exploitable
today → makes it measurable → makes it verifiable → what a later move would need** — and say which
axis you used, because a reader will assume reversibility.

## Rollback summary

| Phase | Rollback | Time | Data risk |
|---|---|---|---|
| 0 | revert commits | minutes | none |
| 1 | delete runtime | minutes | none |
| 2 | stop mirroring | minutes | none |
| 3 | shift routing to EKS | minutes, if warm | in-flight conversations only |
| 4 | rebuild from manifests | hours-days | **this is the irreversible one** |

Phase 4 is the only step that is expensive to undo, which is why it comes after an
observation window rather than immediately after cutover.

## Things that go wrong

Symptoms observed during this migration, with where the fix is documented. The pattern
worth internalising: **most of these present as something other than their cause.**

| Symptom | Actual cause | Detail |
|---|---|---|
| `exec format error`, CrashLoopBackOff | amd64 image. It pulls fine and the container *starts*, so it reads as an app crash | `runtime-and-sessions.md` |
| Agent hangs, nothing in logs | missing VPC endpoint. Does not fail the deploy | `vpc-and-network-isolation.md` |
| No logs at all on Runtime | missing `logs:DescribeLogGroups` on `log-group:*` | `runtime-and-sessions.md` |
| Traces missing on Runtime | `OTEL_*` set, overriding the ADOT sidecar | `observability.md` |
| 504 on Runtime | 15-min request timeout, not adjustable — redesign as async | `runtime-and-sessions.md` |
| Conversations restart | session id not sent as the session header, so every request gets a new microVM | `runtime-and-sessions.md` |
| Cost far above estimate | `StopRuntimeSession` not called; memory bills to the idle timeout | `cost-and-billing.md` |
| SigV4 caller suddenly 403s | a JWT authorizer was configured; it excludes SigV4 | `identity.md` |
| Policy attach fails on IAM | Gateway role needs the full authorize set on **both** engine and gateway ARNs | `policy.md` |
| Stream truncated on EKS | ALB `idle_timeout` (60s default) applies to the gap *between bytes* | [constraints.md](constraints.md) |
| Deployment "created", zero pods | Pod Security violation; `kubectl apply` still returns success | [constraints.md](constraints.md) |

Unqualified paths above are in `deploy-on-agentcore/references/`.

## Where deploy-on-agentcore takes over

Once the decision is made, that skill covers the build: runtime and session details,
Gateway and MCP, Identity 2LO/3LO, Cedar policy, observability, CDK, and VPC isolation.
This playbook is the sequencing; that skill is the construction.
