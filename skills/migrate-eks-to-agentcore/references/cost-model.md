# Cost: EKS vs AgentCore Runtime

Numbers here are **`n=1` illustrations of shape, never a customer's figures.** They came from one
deployed reference agent (three tool calls against a key-value store, two Graviton replicas, 12
concurrent conversations) or from the live Pricing and Service Quotas APIs. A different agent, in
a different language, with a different retrieval backend, will produce different absolute values —
what transfers is *which lines dominate*, not the lines.

## Start here: check whether compute is material at all

**Do this before opening the model.** Measured end to end on one production agent:

| Line | Monthly | Moves on migration? |
|---|---|---|
| Model tokens | **$12,000 – $29,000** | no — identical on both platforms |
| The datastore the agent reasons over | **$240** | no — stays exactly where it is |
| **The entire EKS-vs-AgentCore compute delta being argued about** | **~$19** | yes |

The compute line was **0.04%–1.2% of that agent's run rate.** Everything below this section —
the levers, the crossover, the endpoint arithmetic — was deciding about one part in a hundred.

The middle row is the one assessors get wrong: **whatever the agent reads from — a graph, a vector
index, a warehouse, a search cluster, a relational database — is untouched by the migration and is
usually an order of magnitude above the compute delta.** Price it, put it in the table, and say it
does not move.

So the first output of a cost conversation is often: **"cost is not your deciding factor, and
here is the arithmetic showing it."** That is a more valuable and more credible answer than a
crossover figure, and it redirects the decision to operations and security where it belongs.

Compute *is* material when: token spend is small (short prompts, cheap model, low volume), the
cluster exists solely for this agent, or volume is high enough that node count dominates. Check
which world you are in before modelling.

**Always state the compute figure next to the token and datastore bill.** A compute comparison
presented alone implies compute matters, and usually it does not.

## Then: the question is still wrong

"Is AgentCore cheaper than EKS?" has no stable answer. On one measured workload the
verdict moves **22×** depending on two configuration choices — and on a second, independently
measured customer workload the same two choices moved it **13.4×**. The multiplier is a
property of the workload's active-to-idle ratio, not of the platform. Measure theirs.

| Configuration (100k conversations/mo) | AgentCore | EKS | Verdict |
|---|---|---|---|
| Default: 900s idle timeout + AgentCore Memory | **$136.78** | $79.79 | AgentCore 1.7× **worse** |
| Tuned: `StopRuntimeSession` + no AgentCore Memory | **$6.26** | $79.79 | AgentCore 12.7× **better** |

Same code, same workload, same platform. So the useful output of a cost conversation
is not a number — it is **the two levers and where the crossover sits**.

## The billing models are different shapes

**How AgentCore bills — including the CPU/memory asymmetry, the high-water-mark peak,
the `StopRuntimeSession` lever and the AgentCore Memory decision — is documented in
[deploy-on-agentcore/references/cost-and-billing.md](../../deploy-on-agentcore/references/cost-and-billing.md).**
Read that first; it is the source for every AgentCore rate and behaviour used below, and
it is where any correction belongs.

What matters for the *comparison* is that the two platforms bill on different axes:

| | AgentCore | EKS |
|---|---|---|
| Compute | per-second, CPU only while consumed | node-hours, running or blocked |
| Memory | wall-clock across the **whole session**, on peak | included in the node |
| Fixed cost | none | **$0.10/hr control plane per cluster** |
| Scales to zero | yes | no |

Two consequences specific to a migration decision:

1. **AgentCore has no fixed floor; EKS has a large one.** At realistic volumes the $73/mo
   control plane is most of the EKS bill, which is why the crossover below is a *volume*
   question rather than a utilization one.
2. **On AgentCore, latency and session lifetime become cost.** Wall time is the memory
   meter. That means performance work on the customer's agent has a direct billing
   payoff after migration — a useful thing to be able to promise.

## The measured split, for calibration

One deployed agent (3-turn conversation, 30s think time, default 900s idle timeout):

| Line | Share |
|---|---|
| AgentCore Memory events | **55%** |
| Memory GB-seconds | **44%** |
| CPU | **0.85%** |

Quote this only as an illustration of *shape*, never as the customer's number.

**Note the CPU share is config-dependent:** 0.85% at this default config, but **~19% once
tuned**, because tuning removes the memory and Memory-event lines it was small against. So
"CPU is a rounding error" is true of the default and not of the configuration this skill
recommends. Quote whichever matches what you are proposing.

## The top cost lever and warm reuse are mutually exclusive

Not previously stated anywhere, and it is the central economic question for short-turn services.

`StopRuntimeSession` saves ~12× on memory by ending the session. Warm reuse avoids microVM
start latency by *keeping* the session. **You cannot have both at the extremes.** AWS is explicit:
"Without a consistent session ID, each request may be routed to a new microVM, which may result
in additional latency due to cold starts" `[docs]`.

**But it is a dial, not a switch, and calling it a binary was wrong.** `CfnRuntime` takes
`lifecycle_configuration`, whose `LifecycleConfigurationProperty` carries
`idle_runtime_session_timeout` and `max_lifetime` `[verified]` in aws-cdk-lib — so the 900s
default that produces the idle tail is itself tunable. A third shape:

> **Sticky reuse with a short idle timeout.** Keep the session across a burst of turns, then let
> it expire in tens of seconds rather than fifteen minutes. Pays one start per burst, and bills
> an idle tail sized to the observed think-time distribution instead of to the default.

Size the timeout from the p90 gap between turns, not from a round number. That makes the real
question "how long should the tail be", which has an answer per workload, rather than
"stop or reuse", which does not.

So for a **single-turn** service — where every request is its own conversation — the "tuned"
configuration this file recommends means **paying microVM start on every request**. Measured
start overhead was ~1.96s `[measured:reference, n=3]`, against turns of a few seconds. That is
a latency regression traded for a cost saving, and the trade has to be stated rather than
having both columns claimed.

| Shape | `StopRuntimeSession` | Consequence |
|---|---|---|
| Single-turn | every request | pays start latency every time. Cost-optimal, latency-worst |
| Short conversation (3-10 turns) | at conversation end | the sweet spot — one start amortised over the turns |
| Long / shift-length | at conversation end | start cost is negligible; but check the session ceiling |
| **Conversation end is unobservable** | not implementable | tune `idleRuntimeSessionTimeout` down instead. This is the common case, because users close tabs rather than saying goodbye |

That last row is the one to reach for most often: `StopRuntimeSession` requires knowing a
conversation ended, and for most services nothing tells you.

For single-turn, also note the binding quota changes: the **new-session creation rate** (25/s
default) becomes the limit rather than the concurrent-session cap, because every request
creates a session.

## Crossover, not "savings percentage"

EKS's bill at 100k conversations/month was $79.79, of which **$73.00 was the fixed
control plane** — compute was $6.79. So the decision-useful figure is a volume
crossover:

**The unit is load-bearing and it is not "conversations".** ~56k *conversations*/month silently
assumes the reference workload's 3-turn, ~80-second-active shape. A "conversation" that is a
9-hour analyst shift bills three orders of magnitude more memory, so a plausible-sounding volume
reads as "AgentCore cheaper" when the truth is the opposite. **Denominate in billed
session-seconds** (active + think + idle tail) × peak GB, or state the session shape inline
every time you quote a crossover.

- **Below ~56k conversations/month** *at the reference's 3-turn shape*: AgentCore cheaper. No
  fixed floor to amortise.
- **Above it**: EKS cheaper — *at AgentCore's default configuration*. Tuned, AgentCore
  won at every volume tested.

**Ask whether the cluster stays anyway.** If other workloads keep the cluster alive,
the $73 is not attributable to the agent, and EKS's marginal cost per conversation
($6.79/mo here) is very hard to beat. This single question can invert the verdict, and
it is the first thing to establish.

A caution on my own working: I initially computed a "1.5% utilization break-even."
That figure compares marginal costs only and ignores the control plane, so quoting it
alone is misleading. The model now labels it as marginal-only.

## Two mistakes to avoid

**Costing from the manifest instead of the meter.** I first used the container's 1 GB
memory *limit* and concluded memory was 5.6× CPU. Measured peak was **235 MiB**, making
it 4.4× on the tuned config. The limit is not the bill. Measure **peak**, not average, because of
the high-water mark — and **not with `kubectl top`**, which is a ~60s windowed average sampled on
a delay and read 3.7% below true peak when checked against ground truth. Use cgroup v2
`/sys/fs/cgroup/memory.peak`, a monotonic high-water mark and the exact quantity AgentCore bills
on. Fallbacks when the image has no shell, and how to label them, are in
[measure.md](measure.md).

**Ignoring latency as a cost lever.** Wall time is the memory meter. Fixing blocking
I/O on the event loop cut p50 latency 39% `[measured]`, which cuts the AgentCore memory
line by the same 39%. Performance work and cost work are the same work here.

## What to measure on a customer's workload

Four numbers. Without them there is no cost answer, and reference numbers from this
document are **not** a substitute.

| Number | Source |
|---|---|
| CPU-seconds per turn | container CPU delta ÷ turns over a window |
| Wall-clock seconds per turn | p50 turn latency from traces |
| **Peak** container memory | cgroup v2 `/sys/fs/cgroup/memory.peak`. **Not `kubectl top`** — see above |
| Session shape | turns/conversation, think time, session lifetime |

Then run the model both ways — default and tuned — and report the gap, because the gap
is the actionable part.

## The costs that invert this, and are NOT in the numbers above

Stated before the "not modelled" list because two of them are large enough to reverse the
verdict, and both were missing from earlier versions of this file.

**VPC interface endpoints — conditional on the VPC's egress design, NOT on the platform.**

An earlier version of this file called these "an AgentCore-only cost … on EKS the pod already
has an ENI in the VPC and needs **none** of them." **That is false**, and it was falsified on a
real customer VPC: the VPC had no internet gateway, no NAT, and no `0.0.0.0/0` route anywhere,
so running **EKS** there required `ecr.api`, `ecr.dkr`, `sts`, `logs`, `bedrock-runtime` and
`eks-auth` before a single pod would start. Endpoints were a pre-existing air-gap cost, and
migration added roughly **$0**.

Probe it instead of assuming — one call:

```bash
aws ec2 describe-route-tables --filters Name=vpc-id,Values=<vpc> \
  --query 'RouteTables[].Routes[?DestinationCidrBlock==`0.0.0.0/0`]'
```

| VPC has internet egress | Endpoint cost is |
|---|---|
| Yes (IGW/NAT) | genuinely **AgentCore-only** if it moves to VPC mode. ~8 × AZs × $0.01/hr ≈ $117/mo in us-east-1 |
| No (air-gapped) | **shared** — already paid for EKS. Migration adds ~$0, and may add only `xray`/`monitoring` for the ADOT sidecar |

**NAT and ALB — EKS-only, if they exist.** ~$32/mo NAT + ~$17/mo ALB. On the customer VPC
above there were **zero of each**, so this line was also wrong — in the opposite direction.

That is the trap worth naming: applying both defaults uncritically produced a roughly correct
total out of two individually false lines. **Count the customer's actual NAT gateways, load
balancers and endpoints. Do not apply either default.**

**Prices are region-variant too, not just quotas.** This file argues that quotas vary by region
and then treats prices as fixed. Spot-checked against one Asia-Pacific region: roughly **+30% on
VPC endpoints, +21% on the managed datastore, +13% on the compute instance**, and one instance
family in the comparison **was not offered there at all** — which silently invalidates a
node-price comparison rather than making it slightly wrong. Price in the customer's region, and
confirm the instance types you are comparing exist in it.

**EKS Auto Mode carries a per-instance management fee** (~$0.00918/hr on a large instance,
about +12%) that earlier versions omitted — so the EKS side of the published comparison was
understated.

## Not modelled

- **Bedrock token cost.** Identical on both platforms so it does not affect the
  comparison, but it usually dominates the customer's actual bill. Say so, or the
  figures look like a total that they are not.
- Engineering time to build and operate the EKS session, routing and scaling layers.
- Spot and Savings Plans on EKS: 40-70% off node cost — but on the measured workload that is
  at most **~$4.75/mo** (−6% of the EKS bill). Stated with its magnitude, because the
  percentage alone sounds larger than the dollars.
- ElastiCache and EBS, if used. (NAT, ALB and VPC endpoints are priced in the section above,
  not here — they are too large to defer.)
- The AgentCore **Instances** tier (EC2 on-demand + ~12% management fee, 7.8% for
  G-series; sessions up to 14 days) `[verified]` — relevant when sessions exceed the
  8-hour microVM lifetime.
- Gateway ($0.005/1,000 invocations), Identity ($0.010/1,000 tokens), Policy
  ($0.000025/request) `[verified]` — small, but real if those services are in scope.
