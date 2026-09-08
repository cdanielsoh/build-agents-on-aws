# Nodes F, F1, F2 — send requests to the agent

**Needs S4 = yes. This is an action on a running system, not a read.** It runs their tools for real,
spends their model budget, writes conversation state, and on a multi-agent service really delegates.

So: ask, name what it will touch, prefer a non-production tenant, and if the answer is no then it
does not happen. **Expect no more often than yes** — a first engagement usually has a repo and
read-only cluster access and nothing else.

Record `invocation: not_permitted` when that is the case, distinct from `absent` or `unknown`, and
say which findings were therefore out of reach. Do not plan the assessment around this node.

> An earlier version of this instrument called invoking "the highest-yield step" and made it
> mandatory before the inventory walk. That was over-fitted to fixtures in a dev account with no
> users, no real tool side effects and nobody to ask — where invoking is free. It is not free on a
> customer's system.

## What it finds that nothing else does

Where it *is* available, these classes are unreachable by any configuration read:

- **A capability claimed in the agent's own description that has never worked.** Observed: config,
  resource status and the database schema all agreed long-term memory was configured; it had a 100%
  write-failure rate.
- **A fabricated citation** — a source appended for a document never retrieved, invited by the
  agent's own "cite what you used" prompt.
- **Concurrent-turn contamination, measured rather than argued.** Two simultaneous turns on one
  conversation id, and the second turn's model call saw the first turn's prompt. On that service this
  was the one finding that genuinely required a runtime move, and it came from two `curl`s.

## The five probes

With the customer's permission, against a non-production tenant where possible:

1. **One clean turn.** Read the **answer**, not the status code.
2. **A second turn with a fresh conversation id.** Reusing one returns history-influenced answers
   that look like broken tool calls. This wasted real time.
3. **Two turns on the same conversation id, concurrently.** Does either answer reflect the other's
   input? Then read the stored events: are they interleaved?
   **If `replicas > 1`, port-forward to each pod individually** — `port-forward svc/<name>` resolves
   to a single pod, so this otherwise silently measures within-pod behaviour only. Observed: every
   probe landed on one replica while the other served zero, and the cross-replica defect was
   invisible until each pod was addressed directly.
4. **One turn that forces a tool call, then check that tool's output against ground truth** (node
   F1). Observed: a cluster-inspection tool returned a resource that the control plane reported as
   `NotFound` — a read-only tool serving deleted state. Plausible-looking output is not evidence, and
   a structured, confident table can still be stale or wrong.
5. **Compare what you saw against what the service claims** — its description, its agent card, its
   README. **A false capability claim is a finding**, and it is one customers act on immediately
   because it is embarrassing rather than theoretical.

## When you cannot invoke

These substitute for most of it, and they are read-only:

| Instead of | Read |
|---|---|
| watching a turn succeed or fail | the logs — [read-the-cluster.md](read-the-cluster.md) — and error-rate metrics if any exist |
| checking a tool's output against reality (F1) | the tool's own logs, and its RBAC — what it *could* return |
| concurrency and session behaviour (F2) | the stored session and event rows, their ordering and timestamps |
| a false capability claim | the claimed capability against the config that would implement it. A described feature with no wiring is the same finding |
