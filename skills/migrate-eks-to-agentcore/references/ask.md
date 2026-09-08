# Node J — Gate 3, ask only what you cannot derive

Needs S6 = someone who can answer. If there is nobody, Gate 3 is **structurally unavailable**:
record `engagement: internal_reference` and put the questions in `open_questions`, not as
`undecided` rows.

Governing rule: almost everything decisive is derivable from their repo, their control plane, their
telemetry and live AWS APIs. **A questionnaire that asks what you could have read reads as a sales
script**, and that costs the trust the whole engagement depends on. By this point the work has
earned specificity.

Not to be confused with the **access survey** (S1–S6) in
[survey-and-plan.md](survey-and-plan.md) — that runs first and asks only what you are *permitted*
to do. This node asks about the business.

## Six infrastructure questions

- Data residency and compliance constraints
- **Do non-agent workloads share the cluster?** — this one can invert the cost verdict
- Kubernetes depth on the team, and who is on call
- Roadmap — more agents, multi-agent, agent-to-agent protocols?
- SLA and error budget
- Appetite for a parallel run, and who signs off on cutover

## Two product questions that decide real verdicts

- **Can a user be away longer than the idle timeout and still expect their conversation intact?**
  Decides whether AgentCore Memory or a retained store is needed. **Ask it this way round** — "does
  a returning user resume a *prior* conversation?" gets a false "no" from teams who still need
  durability across a 20-minute gap in the *same* conversation. Getting this wrong deletes the
  session store and loses history at idle expiry.
- **How does a conversation end?** Decides whether `StopRuntimeSession` is implementable, which is
  the top cost lever. For most services nothing tells you, and that is a legitimate answer — see
  [cost-model.md](cost-model.md).
