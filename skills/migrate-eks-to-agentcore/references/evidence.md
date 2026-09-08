# Evidence — the tags, and how to rank a source

Every claim in the record carries one of these tags. Non-negotiable, and it is the thing that
lets a customer argue with a finding rather than take it on faith.

## The tag set

| Tag | Means |
|---|---|
| `[measured:customer]` | Observed on **their running workload**. The only kind you may quote as theirs |
| `[measured:reference]` | Observed on this plugin's reference build — **n=1 agent**. Illustrates shape, never their number |
| `[read:source]` | **Read in their repo at `file:line`.** Most of a pre-deployment assessment is this |
| `[read:cluster]` | Read from the **live control plane** — the Kubernetes API (a resource, its schema, RBAC, a controller's env), or the equivalent API/console of whatever platform runs the agent. Where the agent is defined declaratively, this *is* the authoritative config store, and it **outranks `[read:source]`** wherever the two disagree |
| `[stated:customer]` | Asserted by the customer — a README, a ticket, a conversation. Often the only source for volume and spend, and not independently checkable |
| `[verified]` | Queried from a live AWS API (Service Quotas, Pricing, SDK) — **in their account, or say whose**. For a quota, also say **applied or default**: `2500 [verified: default, eu-west-1]`. Bare `[verified]` on a number that exists in both flavours leaves the reader unable to tell whose limit it is |
| `[docs]` | **AWS** documentation. Not their README — that is `[stated:customer]`, and mislabelling it presents a mid-range guess from an 8-line file as a documented fact |
| `[reasoned]` | Follows from the above — argument, not observation |
| `[open]` | Not established. Say so; do not fill the gap |

**Why `[read:source]` and `[stated:customer]` exist.** Without them assessors had to choose
between overclaiming (`measured`) and underclaiming (`reasoned`) for the evidence they actually
had. A Dockerfile platform flag is neither an observation of a running system nor an inference —
it is a fact read at a line number, and it is strong.

**Why `[read:cluster]` exists.** An assessor with no tag for the live API reached for
`[measured:customer]`, which is defensible but conflates a config read with a performance
observation. Three rows on that assessment would have been **recorded wrong from source** —
concurrency safety, idempotency, and an isolation blocker that existed only at `HEAD` — because
the deployed release and the repo were different software.

## The source hierarchy — stay inside their system

Rank sources by how close they are to what is actually running, and **exhaust each level before
descending**:

| | Source | Tag |
|---|---|---|
| 1 | **Behaviour** — invoke it, read the answer, read the logs | `measured:customer` |
| 2 | **The deployed control plane** — resource specs, served schemas, RBAC, env, status conditions | `read:cluster` |
| 3 | **The customer's own repo** | `read:source` |
| 4 | **Files inside the running image** — `kubectl exec -- cat`, or the mounted config the controller generated | `read:cluster` |
| 5 | Third-party source, **only** if 1–4 cannot answer a question that changes the recommendation | `read:source` + say it is upstream, not theirs |

**Never let public material stand in for a finding about their system.** Reading AWS docs or a
vendor's documentation is fine and often necessary — that is what `[docs]` is for. What is not
fine is inferring what *this deployment does* from what the software is *meant* to do. The gap
between those two is where nearly every real finding lives: a shipped field the runtime ignores,
a control with zero call sites, a proxy that has never served a request. Levels 1 and 2
distinguish them; documentation cannot.

Two habits that follow:

- **Watch how much of your evidence is level 5.** If the record leans on upstream code and docs,
  it describes the software rather than the customer. And a method that depends on the platform
  being well known degrades exactly where customers are most typical — an internal service has no
  public source to read at all.
- **Prefer the running container over the registry or the forge.** `kubectl exec -- cat` on the
  file the controller actually mounted is stronger evidence than the same file on a branch,
  because it is what they deployed. Match upstream reads to the running build first
  (`kubectl logs | grep -iE 'version|git_commit|build'`), and say in the record when a claim rests
  on upstream code rather than on their system.

## Read shape, not craft

A working vibe-coded service is the normal input; grading quality leads to wrong conclusions.
Read what surfaces exist, where state lives, what guards are present.

And **check rather than assume** — real services often get the hard parts right and the easy parts
wrong. While building the reference for this skill the author got `asyncio.to_thread` wrong where
the customer service being modelled got it right.

## Never quote a cost figure you did not measure on their workload

Not even the ones in this plugin — they are one agent, one shape, n=1. Figures here exist to show
*which levers matter*, never as the customer's number. A figure carried in from a blog post is
worse than no figure; a figure carried in from this plugin and presented as theirs is worse still,
because it looks sourced.
