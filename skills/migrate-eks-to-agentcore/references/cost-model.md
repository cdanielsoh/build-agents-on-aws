# Cost: EKS vs AgentCore Runtime

Everything here was measured on a deployed reference — a Strands agent with three
DynamoDB-backed tools, running on EKS Auto Mode (Graviton `c6g.large`, 2 replicas,
Pod Identity) under 12 concurrent conversations, Sonnet 4.6 — or verified against the
live Pricing and Service Quotas APIs. Where a number is reasoned rather than observed,
it says so.

## Start here: the question is wrong

"Is AgentCore cheaper than EKS?" has no stable answer. On one measured workload the
verdict moves **22×** depending on two configuration choices:

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

## Crossover, not "savings percentage"

EKS's bill at 100k conversations/month was $79.79, of which **$73.00 was the fixed
control plane** — compute was $6.79. So the decision-useful figure is a volume
crossover:

- **Below ~56k conversations/month**: AgentCore cheaper. No fixed floor to amortise.
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
it 4.4× on the tuned config. The limit is not the bill. Measure peak with `kubectl top`
or Container Insights — and peak, not average, because of the high-water mark.

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
| **Peak** container memory | `kubectl top pods`, Container Insights |
| Session shape | turns/conversation, think time, session lifetime |

Then run the model both ways — default and tuned — and report the gap, because the gap
is the actionable part.

## The costs that invert this, and are NOT in the numbers above

Stated before the "not modelled" list because two of them are large enough to reverse the
verdict, and both were missing from earlier versions of this file.

**VPC interface endpoints — an AgentCore-only cost.** If the knowledge store is VPC-resident,
`networkMode = "VPC"` is mandatory, and that needs interface endpoints for `bedrock-runtime`,
`ecr.api`, `ecr.dkr`, `logs`, `xray`, `monitoring`, `ssm`, `sts`. Eight endpoints × 2 AZ ×
$0.01/AZ-hour × 730h ≈ **$117/month**, before data processing. On EKS the pod already has an
ENI in the VPC and needs **none** of them.

Against a tuned AgentCore figure of ~$6/month, that is a ~20× understatement. **For any
customer with a private knowledge store, do not emit a cost verdict until the endpoint count
is priced.** This is the archetype the skill calls the forcing function, and it is precisely
where the naive comparison is most wrong.

**NAT and ALB — EKS-only costs that migration removes.** ~$32/mo NAT + ~$17/mo ALB ≈
**$49/month**, plus cross-AZ data. Excluding these understates the EKS side, i.e. it cuts
*against* migrating. Earlier versions of this file omitted both while including a
percentage-only mention of Spot savings, which flattered the EKS side on the small item and
the AgentCore side on the large ones. Include all three with dollar magnitudes, or none.

Adding NAT + ALB moves the crossover from ~56k to roughly **~94k conversations/month** on the
measured workload — a 68% change in the number this file calls decision-useful. Treat the
crossover as an order of magnitude, not a figure.

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
