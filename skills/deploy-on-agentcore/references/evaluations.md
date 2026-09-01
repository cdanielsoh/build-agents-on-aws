# AgentCore Evaluations — Managed LLM-as-Judge over Agent Traces

Managed scoring of agent behaviour from OTEL traces: built-in and custom evaluators, run
against live production traffic, specific historical spans, or a curated dataset.

> **Not the same thing as `strands-evals`.** Both score agents; they run at different points
> in the lifecycle and you want both.
>
> | | `strands-agents-evals` | AgentCore Evaluations |
> |---|---|---|
> | Runs | In your pytest suite, pre-deploy | Managed service, against traces |
> | Input | Cases you write, agent invoked in-process | OTEL/OpenInference traces from a deployed agent |
> | Gate | CI, before merge | Production monitoring, regression audits |
> | Judge | Your model, your rubric | Managed built-ins, or your own |
>
> See the `strands-agent-design` skill's `references/testing-with-evals.md` for the pre-deploy
> half. `bedrock_agentcore.evaluation.convert_strands_to_adot` bridges Strands traces into the
> ADOT format this service consumes.

## Table of Contents

1. [How It Works](#how-it-works)
2. [Prerequisite: Instrumentation](#prerequisite-instrumentation)
3. [Built-in Evaluators](#built-in-evaluators)
4. [Evaluator Levels and Placeholders](#evaluator-levels-and-placeholders)
5. [Evaluation Types](#evaluation-types)
6. [Dataset Evaluation](#dataset-evaluation)
7. [Ground Truth Mapping](#ground-truth-mapping)
8. [Custom Evaluators](#custom-evaluators)
9. [Access Control](#access-control)
10. [Quotas](#quotas)
11. [Choosing an Approach](#choosing-an-approach)

---

## How It Works

Traces from your agent are converted to a unified format and scored with LLM-as-a-judge.
Works for agents on AgentCore Runtime **and** agents hosted anywhere else — the input is
telemetry, not a runtime dependency.

```
Agent (Strands, LangGraph, …)
   │  OTEL / OpenInference instrumentation
   ▼
ADOT  ──▶ CloudWatch Logs (traces, spans, sessions)
              │
              ▼
      AgentCore Evaluations
        ├── online     — sample live traffic continuously
        ├── on-demand  — score specific span/trace IDs
        ├── batch      — async job over a CloudWatch Logs range
        └── dataset    — replay or simulate scenarios, then score
              │
              ▼
      Scores + per-session detail → CloudWatch, dashboards
```

Manage resources via the AgentCore CLI, the Python SDK (`bedrock_agentcore.evaluation`), the
console, or AWS SDKs.

### Key terms

| Term | Meaning |
|---|---|
| **Session** | Related interactions from one user or workflow; contains one or more traces |
| **Trace** | One complete agent execution/request; contains spans |
| **Tool call** | A span representing invocation of a tool — name, params, timing, output |
| **Skill** | A loaded instruction file ([Agent Skills](https://agentskills.io) standard). Skill invocations are detected from traces and have their own evaluators |

Evaluators are **reference-free by default** — they judge from the model's own knowledge and
need no ground truth. Supplying ground truth switches the relevant evaluators into a
reference-based mode.

---

## Prerequisite: Instrumentation

No traces, no evaluations. Requirements:

- **Frameworks:** Strands Agents, LangGraph, and others — see the supported-frameworks doc.
- **Libraries:** OpenTelemetry or OpenInference semantic conventions.
- **Instrumentation agent:** **ADOT** is the supported agent.

On AgentCore Runtime this is the same ADOT setup described in the observability section:
add `strands-agents[otel]` and `aws-opentelemetry-distro`, launch with
`opentelemetry-instrument python app.py`, and **do not set `OTEL_*` env vars** — the sidecar
configures them, and overriding them silently breaks delivery. Getting observability right is
therefore a hard prerequisite for evaluation, not a parallel nice-to-have.

---

## Built-in Evaluators

Referenced by ID as `Builtin.<Name>`, e.g. `Builtin.Helpfulness`. Their models and prompt
templates are fixed and **cannot be modified** — that is deliberate, so scores stay comparable
over time. AWS adds to and improves them.

### Session-level

| Evaluator | Scores |
|---|---|
| `Builtin.GoalSuccessRate` | Whether the session achieved the user's goal. Consumes `assertions` as ground truth |
| `Builtin.TrajectoryExactOrderMatch` | Tool sequence matches `expected_trajectory` exactly |
| `Builtin.TrajectoryInOrderMatch` | Expected tools appear in order; extras between them allowed |
| `Builtin.TrajectoryAnyOrderMatch` | All expected tools present, order irrelevant |

The three trajectory variants matter: **exact-order match will fail an agent that did the
right thing plus one extra lookup.** Pick the loosest one that still encodes your requirement.

### Trace-level

| Evaluator | Scores |
|---|---|
| `Builtin.Correctness` | Response accuracy (uses `expected_response` when supplied) |
| `Builtin.Faithfulness` | Grounded in retrieved/tool context rather than invented |
| `Builtin.Helpfulness` | Actually helps with what was asked |
| `Builtin.Coherence` | Internally consistent, follows logically |
| `Builtin.Conciseness` | Free of padding |
| `Builtin.ContextRelevance` | Retrieved passages were relevant to the query |
| `Builtin.ResponseRelevance` | Response addresses the query |
| `Builtin.InstructionFollowing` | Adheres to instructions given |
| `Builtin.Refusal` | Whether the agent refused |
| `Builtin.Harmfulness` | Harmful content |
| `Builtin.Stereotyping` | Stereotyped content |

`Builtin.Refusal` is worth calling out: a refusal is correct behaviour for an out-of-scope or
injected request, so read it as a *rate to explain*, not a defect count.

### Tool-level

| Evaluator | Scores |
|---|---|
| `Builtin.ToolSelectionAccuracy` | Right tool chosen |
| `Builtin.ToolParameterAccuracy` | Parameters faithful to the request |
| `Builtin.SkillSelectionAccuracy` | Right skill loaded from the catalog |
| `Builtin.SkillInstructionFollowing` | Followed the loaded `SKILL.md` |

### Third-party

Evaluators from the **DeepEval** and **AutoEval** open-source libraries, managed exactly like
built-ins — select by ID, no model or config needed.

---

## Evaluator Levels and Placeholders

The level determines what the judge sees. This is what to reason about when a score looks
wrong — the evaluator may simply not have the context you assumed.

| Level | Placeholders available |
|---|---|
| **Session** | `context` (all user prompts, assistant responses, tool calls across every turn), `available_tools` |
| **Trace** | `context` (all previous turns + the current turn's prompt and tool calls), `assistant_turn` (the response under evaluation) |
| **Tool** | `available_tools`, `context` (previous turns + current prompt + tool calls preceding this one), `tool_turn` (the call under evaluation) |
| **Skill** (tool-level subset) | `invoked_skill`, `skill_content` (the full `SKILL.md`), `available_skills` (the catalog, when the trace exposes one) |

`available_skills` is populated only when the framework exposes a catalog. Without it,
skill-selection scoring has nothing to compare against.

---

## Evaluation Types

| Type | Input | Runs | Use for |
|---|---|---|---|
| **Online** | Live traffic, sampled | Continuous | Production quality monitoring |
| **On-demand** | Specific span/trace IDs | Immediate | Investigating one interaction; validating a fix; trying a new evaluator; build-time testing |
| **Batch** | CloudWatch Logs location + time window | Async job | Baselines, pre/post comparisons, regression sets, periodic audits |

### Online

Three parts to configure:

1. **Sampling and filtering** — percentage-based (e.g. 10% of sessions) or conditional filters
   for targeted evaluation. Sampling is a cost control; every scored session is judge-model
   inference you pay for.
2. **Evaluator selection** — built-in, existing custom, or new custom.
3. **Monitoring** — aggregated dashboards, trends over time, drill-down into low scorers.

### On-demand

You supply the exact span or trace IDs. Cheapest way to iterate on a custom evaluator, because
you re-score the same fixed traces instead of generating new traffic.

### Batch

You submit a job naming a CloudWatch Logs location and the evaluators to run; **the service
handles session discovery, span collection, and scoring**. Returns aggregate per-evaluator
averages plus session counts, with per-session detail written to CloudWatch Logs. Ground truth
comes from session metadata.

---

## Dataset Evaluation

Both the on-demand and batch dataset runners take the same format. A dataset holds scenarios;
each scenario is one session.

`FileDatasetProvider` auto-detects the type: a `turns` field means **predefined**, an
`actor_profile` field with no `turns` means **simulated**.

### Predefined — replayed exactly as written

```json
{
  "scenarios": [
    {
      "scenario_id": "math-then-weather",
      "turns": [
        { "input": "What is 15 + 27?", "expected_response": "15 + 27 = 42" },
        { "input": "What's the weather?", "expected_response": "The weather is sunny" }
      ],
      "expected_trajectory": ["calculator", "weather"],
      "assertions": [
        "Agent used the calculator tool for the math question",
        "Agent used the weather tool when asked about weather"
      ]
    }
  ]
}
```

Turns run sequentially in one session, preserving context. `expected_response` is **positional
per turn** — turn 0 maps to trace 0. `expected_trajectory` and `assertions` apply to the whole
session.

### Simulated — an LLM actor generates the turns

```json
{
  "scenarios": [
    {
      "scenario_id": "geography-student",
      "scenario_description": "A curious student asks geography questions",
      "actor_profile": {
        "traits": { "expertise": "novice", "tone": "curious" },
        "context": "A student studying world geography who wants to learn about capitals",
        "goal": "Find out the capital cities of at least two different countries"
      },
      "input": "Hi! I'm studying geography. Can you help me learn about world capitals?",
      "max_turns": 5,
      "assertions": [
        "Agent provides accurate capital city information",
        "Agent is helpful and encouraging to the student"
      ]
    }
  ]
}
```

`actor_profile` requires `context` and `goal`; `traits` is optional and is a **mapping**.
`max_turns` defaults to 10.

**Simulated scenarios cannot use `expected_trajectory` or per-turn `expected_response`** —
the conversation is not known in advance. Use `assertions` as ground truth instead. Note the
symmetry with `strands-evals`' `ActorSimulator`: same idea, same trait-mapping shape, same
constraint that dynamic conversations can only be graded against assertions.

### In Python

```python
from bedrock_agentcore.evaluation import (
    Dataset, FileDatasetProvider, PredefinedScenario, Turn,
)

dataset = Dataset(scenarios=[
    PredefinedScenario(
        scenario_id="math-question",
        turns=[Turn(input="What is 15 + 27?", expected_response="15 + 27 = 42")],
        expected_trajectory=["calculator"],
        assertions=["Agent used the calculator tool"],
    ),
])

# or
dataset = FileDatasetProvider("dataset.json").get_dataset()
```

Relevant SDK surface in `bedrock_agentcore.evaluation`:
`OnDemandEvaluationDatasetRunner`, `BatchEvaluationRunner`, `EvaluationClient`,
`SimulatedScenario`, `ActorProfile`, `SimulationConfig`,
`CloudWatchAgentSpanCollector`, `fetch_spans_from_cloudwatch`,
`custom_code_based_evaluator`, and `convert_strands_to_adot`.

---

## Ground Truth Mapping

Runners route dataset fields to the evaluators that consume them:

| Evaluator | Field | Level |
|---|---|---|
| `Builtin.Correctness` | `turns[].expected_response` | Trace |
| `Builtin.GoalSuccessRate` | `assertions` | Session |
| `Builtin.TrajectoryExactOrderMatch` | `expected_trajectory` | Session |
| `Builtin.TrajectoryInOrderMatch` | `expected_trajectory` | Session |
| `Builtin.TrajectoryAnyOrderMatch` | `expected_trajectory` | Session |

All ground truth is optional; put every field in one dataset and each runner picks what it
needs. Evaluators with no ground truth available **fall back to reference-free mode rather
than failing** — which means a typo in a field name yields a plausible score computed a
different way, not an error. Verify your ground truth is actually being consumed before
trusting a comparison.

---

## Custom Evaluators

| Kind | Use when |
|---|---|
| **LLM-as-a-judge** | Domain-specific quality bars. You define the model, instructions, criteria, and scoring schema |
| **Code-based (Lambda)** | Deterministic checks — regex, schema validation, external API lookups, business rules. No judge model, no judge nondeterminism |
| **Derived from a base evaluator** | Run a built-in or third-party evaluator's logic on **your** model/inference instead of the service's pick |

Reach for **code-based** whenever the property is actually decidable. An LLM judge for "is
this valid JSON" or "is the refund under $500" adds cost, latency, and variance to a question
`json.loads` answers exactly. Save the judge for genuinely qualitative dimensions.

The derived kind is the escape hatch for two common needs: pinning a judge model so scores
stay comparable across a migration, and meeting a data-residency constraint on where judging
runs.

---

## Access Control

Evaluator ARNs:

```
arn:aws:bedrock-agentcore:::evaluator/Builtin.Helpfulness        # built-in — public
arn:aws:bedrock-agentcore:region:account:evaluator/my-eval-id    # custom — private
```

Built-in evaluators are public to all users. Custom evaluation resources are private and
shared explicitly: **IAM resource-based policies** on evaluators and evaluation configurations,
**identity-based policies** for users and roles.

---

## Quotas

| Limit | Default |
|---|---|
| Evaluation configurations per region per account | 1,000 |
| Input + output tokens per minute per account | 1,000,000 (large regions) |

The token quota is the practical constraint on online evaluation. Judge inference consumes it
per scored session, so a high sampling rate on high-volume traffic will throttle. Start low,
measure, then raise.

---

## Choosing an Approach

| Situation | Use |
|---|---|
| Gate a PR before merge | `strands-evals` in CI |
| Baseline before a prompt or model change | Batch evaluation over a curated session set |
| Confirm a change did not regress quality | Batch, pre/post, same sessions |
| Watch production continuously | Online with sampling |
| Investigate one bad customer interaction | On-demand with its trace ID |
| Iterate on a new custom evaluator | On-demand over fixed traces (cheapest loop) |
| Test multi-turn behaviour against personas | Dataset evaluation, simulated scenarios |
| Assert an exact tool sequence | Dataset with `expected_trajectory` + the right trajectory variant |
| Check something decidable in code | Custom code-based evaluator, not a judge |

A workable division of labour: `strands-evals` gates merges on a small, fast, deterministic
suite; batch evaluation establishes baselines and catches regressions on realistic sessions;
online evaluation watches a sampled slice of production and tells you when reality diverges
from both.

---

## Related

- `strands-agent-design` → `references/testing-with-evals.md` — the pre-deploy suite
- `references/runtime-and-sessions.md` — ADOT setup, without which there are no traces
- `references/policy.md` — `LOG_ONLY` shadow-testing, the same observe-then-enforce shape
  applied to authorization
