---
description: Turn the customer's AgentCore migration decisions into a phased plan plus scaffolding; acts only on decisions and makes no live changes
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

## Step 1 — Load the record, and act only on decisions

Read all of `.agentcore-migration/`, or `--record` for a different directory. If it is absent, stop
and say to run `/assess-agentcore-migration` first — do not reconstruct it from conversation.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lens_plan.py" validate
```

**Read everything. Act only on `decisions.yml`.**

| Input | Use |
|---|---|
| `decisions.yml`, `choice: proceed` | **the only things that become plan items** |
| `decisions.yml`, `choice: declined` | listed as **deliberately excluded**, with their reason. Never silently absent |
| `decisions.yml`, `choice: deferred` | a "revisit when X" section, using `revisit_when`. Not a phase |
| suggestions with **no** decision | undecided by absence. Name them as needing survey 2, and plan nothing |
| `suggestions.yml` | the change, the cost, and `does_not_fix` — quote it rather than re-deriving |
| `findings.yml` | evidence to cite, so the plan re-derives nothing |
| `assessment.yml` | `triage.fix_first` is Phase 0 / R1; `cost_confidence` decides whether any phase gets a business case |
| `access.yml` + blocked questions | what the plan's verification steps **cannot** confirm |
| receipt timestamps | staleness, computed rather than judged |

**Refuse if `decisions.yml` is missing or has no `decided_with`.** Then no survey 2 happened, and
every "verdict" in the record is the assessor's own. `assessment.yml`'s `recommended_outcome` is not
a substitute — it sits in the record looking authoritative, which is exactly why planning from it
quietly reintroduces things the customer would have declined. Say what is missing and stop.

**Two invariants, enforced rather than described.** `validate` fails on both, so a plan that violates
them cannot pass the `Stop` hook:

- **A plan item with no decision id is invalid.** Write `plan/items.yml` alongside `plan.md`, one
  entry per item, each naming the decision it comes from. That is also what generates
  `MANIFEST.md`'s reverse index.
- **A suggestion with no finding ids is invalid** — so anything you plan is already grounded, and
  citing the suggestion is enough.

```yaml
# .agentcore-migration/plan/items.yml
items:
  - { id: P-0.1, phase: 0, decision: D-01, title: set authz.serverSide=true,
      artifacts: [scaffold/chart/values.yaml] }
```

**Staleness is now computation, not judgement.** `validate` reports when `findings.yml` is older than
the newest receipt, and when a decision rests on a receipt that has since been superseded. Read both
before planning: a `deferred` decision taken against a measurement that was later reinterpreted may
have been the right answer to the wrong number.

**`findings.yml`'s `gates:` block, and what each result means for planning.** Enumerate every entry;
do not treat anything other than `pass` as fatal:

| `result` | Plan accordingly |
|---|---|
| `pass` | nothing to do |
| `trivial_fix` | a Phase 0 line item (e.g. an amd64 pin). **Not** a refusal |
| `needs_redesign` | plan the redesign as its own phase, and cost it before Phase 1 |
| `fail` | refuse **only if the remedy is a platform capability the customer cannot supply.** If the remedy is a rebuild, a pin or a config change, it is a `trivial_fix` mis-recorded — see below |
| `unknown`, resolvable by you | **do not plan past Phase 0.** An unevaluated gate is not a passed gate — name what would resolve it, and resolve it |
| `unknown`, needs a Gate 3 answer | plan on, with the gate as a named assumption and a stated consequence if it is wrong. Quota headroom needs their request volume; nothing in a repo answers it, so blocking here would block every repo-only assessment |

**Read `fail` against the remedy, not the word.** Records written before `trivial_fix` existed —
and assessors who never reach for it — record `image_architecture: fail` for an amd64 image,
which a rebuild clears and which this command's own Phase 0 calls routine. Observed on a real
record: a header saying "no hard blockers found" above a `fail` entry. If the remedy is a
rebuild, a pin, or a config change, treat it as `trivial_fix`, plan it into Phase 0, and note
that the record used the wrong value. Refuse only where the remedy is a platform capability the
customer cannot supply.

Also check each gate's `compute_type_assumed`. GPU, architecture and session duration differ
between microVMs and Instances, so a `fail` gated against microVMs may be a `pass` on
Instances. If the record does not say, treat the gate as `unknown`.

Refuse, and say why, if `decisions.yml`'s `outcome` is `stay` (unless explicitly overridden — record
that override as a decision, not as prose). `redesign_first` is **not** a refusal — it selects a
different plan; see Step 2b.

**Where `assessment.yml`'s `recommended_outcome` and `decisions.yml`'s `outcome` differ, the
customer's wins, and say so at the top of the plan.** That divergence is the most useful line in the
document: it is a disagreement recorded rather than argued, and it tells whoever reads the plan later
which parts were ours and which theirs.

**Check the record against the current instrument, not just against the system.** Records go
stale two ways. Compare its schema to the current
[record-and-adopt.md](../skills/migrate-eks-to-agentcore/references/record-and-adopt.md) — which
owns the confidence axes and the suggestion rules — and to
[record-schema.yaml](../skills/migrate-eks-to-agentcore/references/record-schema.yaml), and note
divergences at the top of the plan. Observed: a record carrying a single `confidence` field and
quoting a rubric line that no longer exists, written weeks after the reference split it into two
axes. `validate` catches enum and reference drift; it cannot catch a *field* that no longer means
what it used to, so read as well as run it.

**Read `recommendation_confidence` and `cost_confidence` separately.** With
`cost_confidence: unavailable`, every cost statement in the plan is marked unestablished and no
phase gets a business case. That does not weaken a `recommendation_confidence: high`.

Echo back `context`, `topology`, `work_unit`, the decision counts by choice, and the
deliberately-excluded list, so a stale record is caught before it produces a plan.

**Read the fields the schema does not define.** Assessors add blocks of their own — free-text
rationale, a critique of the assessment itself, notes hung off a finding. Observed: a record whose
sharpest planning input was a comment inside another field. Skipping what the template does not name
loses exactly the parts the assessor thought worth writing by hand.

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

**Phase 0 — fixes worth making regardless.** Take `assessment.yml`'s `triage` entries with
`fix_first: true` and order them by `severity`. Every one still needs a `proceed` decision to become
a plan item — `fix_first` says *when*, not *whether*. Do **not** work from a generic checklist: on real records, half the
standard items are structurally inapplicable (there is no event loop to unblock, no store to
race), and the highest-value items are service-specific. Two that are easy to miss and often
top the list:

- **structured request logging, if absent** — frequently one change that closes several audit
  and attribution gaps *and* unblocks Gate 2 measurement
- **pinned, hash-locked dependencies, if absent** — not glamorous, and it is a *prerequisite*
  rather than an improvement: until the build is reproducible, no later phase is a controlled
  experiment and no parallel run proves anything. Resolve them inside the target platform
  (`linux/arm64` if you are rebuilding for microVMs), and do not hand-write version numbers —
  a plausible-looking pin you did not resolve is a guess that reads as authoritative
- **an arm64 NodePool**, if you are rebuilding for ARM64 and the cluster runs EKS Auto Mode —
  its `general-purpose` pool is amd64-only, so "runs on Graviton today" is false by default

**Phase 1 — parallel runtime, no traffic.** Same image behind `BedrockAgentCoreApp`, invoked
directly, answers compared against the EKS service. Fully reversible: delete the runtime.

**"Fully reversible" holds only while nothing calls it — which for a multi-component service
means only the components nothing else calls.** In a supervisor/specialist split, moving a
specialist forces a code change *and* a new IAM grant on a live caller, so it is not Phase 1
work. Migrate **leaves first**, keep Phase 1 strictly out-of-band (you invoke the runtime with
your own credentials, nothing in the service knows it exists), and push the first caller change
into Phase 2 where a rollback path is already required.

**Phase 2 — shadow traffic.** Mirror a sampled share of real requests; discard the responses.

**Discarding the response does not discard the side effects, and "no user impact" is not the
same claim.** A mirrored turn runs the agent's tools for real: a mirrored conversation against a
supervisor really delegates, and a mirrored analytics turn starts a real query against a shared,
account-limited service and holds a worker for its duration. Before mirroring, list every tool a
sampled request can reach and say which are idempotent **in resource consumption**, not just
free of writes — read-only is not the test. Where they are not, mirror against a load-test
tenant or do not mirror.

Also check the sampled share against the **session** quota, not the request rate: with N
runtimes behind a supervisor, one mirrored conversation consumes N concurrent session workloads,
so a "1% shadow" is 1% × N.

**Name the mirroring point explicitly, and treat "there isn't one" as a real outcome.** A
ClusterIP service with no ingress and no mesh — typical of internal tools — has nowhere to
mirror from. Then Phase 2 relocates into the *caller*, which may be a different repo and a
different team. Say that rather than writing a phase nobody can execute.

**Phase 3 — cutover.** Customer-driven, on their sign-off.

**With more than one component, cutover has an order and a steady state, and both need writing
down.** Move them one at a time; **mixed mode is where the service lives for weeks, not a moment
it passes through.** Two rules that make per-component rollback actually independent:

- **Both call paths stay open until Phase 4.** If the old path is removed when a component
  cuts over, reversing the supervisor forces reversing every specialist under it — the rollbacks
  stop being independent exactly when you need them most.
- **Cut over in the same order you migrated: leaves first, callers last.** The caller can then
  be reverted without stranding anything.

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
| **R0** | Recover missing artifacts and get the record decided | every `findings.yml` `missing_artifacts` entry resolved or declared permanent; `decisions.yml` exists with a `decided_with` |
| **R1** | Close what is exploitable today | each `triage` entry at `severity: high` with a `proceed` decision has a merged patch. Pin dependencies **first** — until the build is reproducible, no later phase is a controlled experiment |
| **R2** | Make it measurable | the four Gate 2 numbers land in telemetry, from the running service |
| **R3** | Make it verifiable | a golden set drawn from R2's logged turns, wired as a CI gate |
| **R4** | Forward-compatible changes only | things that improve the service now *and* the migration later — ARM64, an arm64 NodePool, structured logging |
| **R5** | Re-assess | re-run `/assess-agentcore-migration` with R2's measurements and the Gate 3 answers |

R5 is the point of the whole structure: `redesign_first` usually means the record could not see
enough to recommend anything, so the plan's deliverable is a **better record**, not a migration.
Say that plainly — the customer is buying a decision, and this plan defers it on purpose.

**An `unknown` gate does not block the R-phases.** Step 1's "do not plan past Phase 0" governs
the *migration* phases, because those commit to a platform an unevaluated gate might rule out.
R0–R5 commit to nothing and exist precisely to resolve unknowns, so plan all of them. `unknown`
gates belong in R0's recovery list and in R5's re-assessment inputs.

**`StopRuntimeSession` has nowhere to live here.** Step 3 says implement it as code, but a
redesign produces no runtime to call it on. Record it as an R5 input — one of the things the
re-assessment must decide — rather than scaffolding a call into a file nothing invokes.

Each R-phase still needs the Step 2 fields: goal, steps, verification, rollback, owner, `[open]`.
Order them with Step 2's tie-break, since reversibility will not separate them.

## Step 3 — Generate scaffolding

**Lift the container details from the shipped template rather than typing them from memory.**
A migration adapts an *existing* repo, so materializing the whole `strands-agentcore` template
is usually wrong — its `pyproject.toml` + `agent/` package layout will not match theirs. Read it
and take the four things that are routinely got wrong:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scaffold.py" /tmp/ref --template strands-agentcore
```

| Lift | Why not from memory |
|---|---|
| `FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim` | the ECR mirror, not Docker Hub — unauthenticated Hub pulls from CodeBuild are rate-limited, and this is a routine build failure |
| `CMD ["opentelemetry-instrument", "python", "app.py"]` | the wrapper is how tracing works at all |
| `app.run(host="0.0.0.0", port=8080)` | **bind explicitly.** A bare `app.run()` auto-detects and picks `127.0.0.1` unless `/.dockerenv` exists — the runtime then never reaches READY, presenting as an app startup failure |
| the "do NOT set `OTEL_*` yourself" comment | setting them silently breaks the delivery path |

Note the template puts its entrypoint *inside* a package, which is why the naming trap below
does not bite it.

**Scaffold for decisions with `choice: proceed`, and for nothing else.** Under `redesign_first`
that will be mostly suggestions closing `absent` findings rather than runtime work — which is
correct: there, most of the work *is* the gaps, and "no tool authorization exists" and "no
instrumentation exists" are the items that matter, the second of which gates Gate 2 by construction.
Write to new paths; never overwrite working files.

- **AgentCore entrypoint** — reuse their existing agent construction unchanged; the point is
  that only the surface differs. **That thesis fails for a multi-agent service**, and it fails
  quietly because it still holds for most of the components: when a supervisor delegates to
  specialists, the delegation transport is part of the **tool set**, not the serving surface.
  Moving a specialist to its own runtime rewrites the supervisor's tools from in-process calls to
  `InvokeAgentRuntime`. Say that in the plan instead of scaffolding a surface swap and calling
  the agent unchanged. **Do not name it `app.py` if a package or module of that name
  already exists.** Verified failure: a root `app.py` beside an `app/` directory without
  `__init__.py` shadows it, and `from app.agent import ...` raises
  `ModuleNotFoundError: No module named 'app.agent'; 'app' is not a package`. Name it
  `agentcore_app.py` or similar.
- **Decide the artifact before scaffolding a build.** There are two — a container image, or a
  **code zip** with `codeConfiguration`, which needs no Dockerfile, no ECR and no image build. If
  the record raises the zip path, or the agent is small and pure-Python, **price it against the
  build pipeline first**: one plan built a CodeBuild stack, a Dockerfile rewrite and an
  architecture assertion for an agent whose own record twice said the zip removed the question.
  It also dissolves the `CMD` conflict in the next bullet, because `entryPoint` is a property of
  the runtime rather than of the image. Ceilings and arm64-wheel traps:
  [runtime-and-sessions.md](../skills/deploy-on-agentcore/references/runtime-and-sessions.md).
- **Dockerfile**, if you chose the container — ARM64 (microVMs) or the target architecture
  (Instances), `/ping`, port 8080. **One image serving both platforms needs both halves:**
  `CfnRuntime`'s container configuration carries only a container URI and no command override, so
  the image `CMD` must be the AgentCore entrypoint, and the **EKS Deployment must override it**
  with `command:`/`args:`. That override is a change to the running service — put it in Phase 0,
  flag it, and do not apply it here.
- **CDK — three stacks, not two.** Runtime + execution role, ECR, **and an image-build stack**.
  The build stack is not optional: it is how you get an arm64 image without local tooling and
  how you get a content-addressed tag. A mutable tag makes a later deploy a silent no-op. See
  `cdk-infrastructure.md` and `naming.md`.
- **Network** — `networkMode = "VPC"` if **any** dependency is VPC-resident, plus the endpoint
  set from
  [vpc-and-network-isolation.md](../skills/deploy-on-agentcore/references/vpc-and-network-isolation.md).
  **Read it rather than reproducing the list here.** A missing endpoint hangs the agent at
  runtime with nothing in the logs naming what was unreachable — and the copy of the list that
  used to sit in this bullet went stale twice, omitting `bedrock-agent-runtime`, so following it
  exactly produced exactly that hang. Ask *which* dependency forces VPC mode, because it
  is often not the one you expect: a Bedrock Knowledge Base is regional and needs no VPC, while
  the session cache beside it does — so the network design is downstream of the **memory**
  decision, not the retrieval one. Assert the set in a test. **If nothing is VPC-resident, assert
  `PUBLIC` in a test**, so a later switch cannot land without the endpoints.
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

Write to `--out` if given, else `.agentcore-migration/plan/` — a subdirectory, so the six record
files stay one writer each and the plan is visibly downstream of them. Use a fixed layout so outputs
are comparable across engagements:

```
<out>/plan.md              the phased plan
<out>/items.yml            one entry per plan item, each naming its decision. `validate` reads this
<out>/scaffold/            generated files, mirroring the target repo layout
<out>/scaffold/MANIFEST.md both indexes, below
```

**`plan.md` needs two sections that are not phases, and leaving them out is what made earlier plans
read as a funnel:**

- **Deliberately excluded.** Every `choice: declined` decision, with the customer's reason in their
  words. A plan that silently omits what was refused reads as if nothing was — and it loses the one
  direction a human revisiting actually asks about, which is *why didn't we do that?*
- **Revisit when.** Every `choice: deferred` decision with its `revisit_when` condition. Not a phase,
  because it has no start date; a condition, because that is what they actually said.

If `outcome` is `assess_only`, both of those sections *are* the plan, plus whatever `fix_first`
triage the customer accepted. Say that plainly rather than padding it into phases: findings
delivered and nothing adopted yet is a complete and successful result.

**`MANIFEST.md` needs the reverse index, not just the forward one.** Artifact → record line
catches invention. It cannot catch *omission* — and omission is the failure that actually
happened: one plan left three gap rows and the single migrate row untouched, and skipped a
`severity: high` finding entirely, while its summary claimed thirteen high-severity findings
closed. Nothing in the artifact list was wrong; the list simply ended. `validate` now catches the
inverse — a plan item with no decision — but only this table catches a decision with no plan item.

So require both directions, and make the second one exhaustive:

| Index | Row per | Catches |
|---|---|---|
| artifact → record line | each generated file | scaffolding invented from imagination |
| **decision → artifact, or "not addressed, because …"** | **every decision with `choice: proceed`** | silent omission |
| **declined and deferred → the reason** | every other decision | a plan that reads as if nothing was refused |

Every row of the second table needs one of the two. "Not addressed" is a fine answer —
"blocked on Q1", "the customer's working file", "no build definition exists in this repo" — and
writing it turns a gap in the plan into a decision the customer can overrule. Then count: if the
plan claims *n* findings closed, that number must be derived from this table, not from the
record's severity totals. `plan/items.yml` is the machine-readable half of the same thing, and
`validate` reads it: generate this table from that file rather than assembling it by hand.

`MANIFEST.md` replaces any interactive diff review: in a non-interactive run there is no
"before" to diff against, so the auditable artifact is a list of what was written and why.

### Run what you generated, before you write the plan around it

**Non-negotiable, and the single highest-value step here.** An independent review of one
generated plan found seven defects that this one action would have caught, including a
`StopRuntimeSession` call whose wrong parameter name discarded every answer the service
computed, and a test presented in the plan as a working pre-deploy gate that could never pass.

| Artifact | Minimum check |
|---|---|
| every Python file | imports, and `python3 -m py_compile` |
| tests you wrote | **`pytest` actually run.** A test you did not run is not a gate, and a failing one in an `&&` chain silently blocks every command after it |
| CDK | `cdk synth`, all stacks |
| any boto3 call you wrote | parameter names against the botocore model, not memory. `python3 -c "import botocore.session as s; print(s.get_session().get_service_model('<svc>').operation_model('<Op>').input_shape.required_members)"` |
| the first concrete action of every phase | run it, or say in the plan that you did not |

If the environment cannot run something, **say so in the plan at that step** rather than
presenting it as verified. A plan whose first concrete action fails is worse than no plan,
because the customer spends their trust discovering it.

**Passing is not enough — mutation-test every assertion you wrote.** Break the thing each test
guards and confirm the test fails. Measured on one generated suite, three of six posture
assertions **could not fail**: one searched a template that never contained the resource, one
asserted the absence of a config no fixture ever set, and one was parametrized over two env vars
while the plan claimed it enforced five. All three passed, and the plan cited them as evidence
for decisions. An assertion that cannot fail is worse than none, because it is believed.

**The mutation must be the defect, not the absence of your helper.** This is the specific trap,
and it produced the worst outcome observed: a suite reported as "11 failures on the unpatched
tree, so these are real gates" where **9 of the 11 were `AttributeError: no attribute
'key_for'`** — they failed because the patch's new function did not exist yet. An independent
reviewer kept the helper and restored only the vulnerable call site, and the suite reported
**12 passed with a live cross-tenant defect**. Two rules follow:

- **Mutate the fix, not the scaffolding.** Leave every new symbol in place, revert the call site
  to its original form, and require a failure. A red-to-green transition that only tracks a
  symbol appearing tests your import, not their security.
- **The test must exercise the module the defect lives in.** That suite never imported the
  request handler; it re-derived both cache keys through the helper and compared them, which is
  true by construction. If the defect is at `server.py:150`, a test that never imports
  `server` cannot see it.

**Check the build actually contains what the code reads.** Generated `EXCLUDE`/`.dockerignore`
patterns are a live hazard: `"*.md"` in an asset-exclusion list silently strips a prompt file the
agent loads at construction, producing a runtime that reaches READY and 500s on first invoke —
the exact failure the rest of the plan is written to prevent. Synthesize, list the staged
context, and confirm every non-`.py` file the code opens is in it.

**Phase 1 compares against the EKS service, so confirm the EKS service starts.** If the image
cannot be built or the entrypoint does not exist — common, and itself a finding — Phase 1 has no
baseline and its exit criterion is unmeetable. Say that instead of writing the comparison step.

The command applies "verify observed state, never exit codes" to the customer's
infrastructure. Apply it to your own output too.

**Keep the two surfaces behaviourally identical.** If you add input validation, logging or a
guard to the AgentCore entrypoint, the same change lands on the EKS surface **in the same
phase** — otherwise Phase 1 and Phase 2 exit on an answer comparison between two services that
differ by exactly what you added, and the parity finding is void. Setting an env var on the EKS
side that nothing reads does not count.

Per phase: goal, steps, verification, rollback, owner, and what remains `[open]`.

**If the record names no owner — the normal pre-engagement case — do not emit a column of
placeholders.** Drop the field and state once, near the top, that no phase has a named owner and
no cutover approver exists, listing it as the first thing the customer must supply. A table of
`TBD` reads as sloppiness; one sentence reads as a finding, which is what it is.

**Verify observed state, never exit codes.** Three observed cases worth encoding as checks:
`kubectl apply` returns success while a Pod Security violation prevents any pod being created;
`eksctl` exits 0 on a rolled-back cluster; `create-policy` returns 200 and the policy later
reaches `CREATE_FAILED`.

## Step 5 — Hand it over

Summarise the phases, the first concrete action, and the decision points that remain the
customer's. Restate what does not go away. If the plan rests on `open` measurements, say which
numbers would change the recommendation and how to get them.
