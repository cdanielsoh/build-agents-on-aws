# Testing with Strands Evals SDK

Evaluate agents across four dimensions: output quality, tool call sequences, OTEL traces, and multi-turn simulated conversations.

> **Validated against `strands-agents-evals` 1.2.0** (September 2026). Every trap called out
> below was hit while running the bundled suite — they are not hypothetical.
>
> For the *managed* alternative — LLM-as-judge scoring over OTEL traces, run by AWS with
> built-in and custom evaluators, online/batch/dataset modes — see AgentCore Evaluations in
> the `deploy-on-agentcore` skill. The two are complementary: `strands-evals` is your
> pre-deploy test suite, AgentCore Evaluations watches production traffic.

## Table of Contents

1. [Package and Imports](#package-and-imports)
2. [Case / Experiment Pattern](#case--experiment-pattern)
3. [EvalBuilder (Extending SessionBuilder)](#evalbuilder)
4. [Output Evaluators](#output-evaluators)
5. [Trajectory Evaluators](#trajectory-evaluators)
6. [Trace Evaluators (OTEL-Based)](#trace-evaluators)
7. [Multi-Turn Simulation (ActorSimulator)](#multi-turn-simulation)
8. [Auto-Generating Test Cases](#auto-generating-test-cases)

---

## Package and Imports

**Package**: `strands-agents-evals` (NOT `strands-evals` — that's a PyPI squatter)

```bash
pip install strands-agents-evals
```

```python
from strands_evals import Case, Experiment, ActorSimulator
from strands_evals.evaluators import (
    OutputEvaluator,
    TrajectoryEvaluator,
    HelpfulnessEvaluator,
    GoalSuccessRateEvaluator,
    FaithfulnessEvaluator,
    ToolSelectionAccuracyEvaluator,
    ToolParameterAccuracyEvaluator,
)
from strands_evals.extractors import tools_use_extractor
from strands_evals.mappers import StrandsInMemorySessionMapper
from strands_evals.telemetry import StrandsEvalsTelemetry
from strands_evals.generators import ExperimentGenerator
from strands_evals.types import TaskOutput
from strands_evals.types.simulation import ActorProfile
from strands_evals.simulation.prompt_templates.actor_system_prompt import (
    DEFAULT_USER_SIMULATOR_PROMPT_TEMPLATE,
)
```

---

## Case / Experiment Pattern

Cases are the unit of evaluation. Each has an input prompt, optional expected output and trajectory, and metadata.

### Case Format (JSON)

Store cases in `evals/chats/*.json`:

```json
[
  {
    "name": "check-order-status",
    "input": "What's the status of my most recent order?",
    "expected_output": "A response that mentions the order status",
    "expected_trajectory": ["list_orders"],
    "metadata": {
      "category": "order_inquiry",
      "difficulty": "easy",
      "task_description": "User wants to know their recent order status"
    }
  }
]
```

| Field | Required | Used By |
|-------|----------|---------|
| `name` | Yes | Case identifier |
| `input` | Yes | Prompt sent to agent |
| `expected_output` | No | OutputEvaluator reference |
| `expected_trajectory` | No | TrajectoryEvaluator comparison |
| `metadata.task_description` | No | GoalSuccessRateEvaluator, ActorSimulator |

### Loading Cases

The template's `conftest.py` provides auto-discovery:

```python
def load_chats(pattern="*.json") -> list[Case]:
    cases = []
    for path in sorted((EVALS_DIR / "chats").glob(pattern)):
        with open(path) as f:
            raw = json.load(f)
        for item in raw:
            cases.append(Case(
                name=item["name"],
                input=item["input"],
                expected_output=item.get("expected_output"),
                expected_trajectory=item.get("expected_trajectory"),
                metadata=item.get("metadata", {}),
            ))
    return cases
```

### Running an Experiment

> **Changed in `strands-agents-evals` 1.2:** `run_evaluations()` returns **one**
> `EvaluationReport` covering every (case, evaluator) pair. It used to return a list with
> one report per evaluator. Code written against the old shape fails with
> `AttributeError: 'tuple' object has no attribute 'display'`.

```python
experiment = Experiment(cases=cases, evaluators=[OutputEvaluator(rubric=rubric), ...])
report = experiment.run_evaluations(strict_task(task_fn))

report.display(include_actual_output=True)  # static; NOT run_display() (interactive)
report.to_file("evals/reports/output.json")  # serializes itself — no manual json.dump
```

The `task_fn` receives a `Case` and returns `{"output": str, "trajectory": ...}`.

`report.scores` is **flat across all evaluators**, in the same order as `report.cases`.
Each row in `report.cases` carries an `evaluator` field. Group before asserting, or a
regression in one evaluator hides behind a win in another:

```python
def scores_by_evaluator(report) -> dict[str, list[float]]:
    grouped = {}
    for row, score in zip(report.cases, report.scores, strict=False):
        grouped.setdefault(str(row.get("evaluator", "unknown")), []).append(score)
    return grouped
```

### Trap: Experiment swallows task exceptions

`run_evaluations` catches whatever the task raises and records the case as `0.0`. That is
reasonable for a flaky model call, but it makes a genuine bug — a renamed SDK field, a
malformed fixture — indistinguishable from a badly-performing agent. The symptom is a suite
that reports **"1 passed" in 0.04s with every score at 0.00**.

Always wrap the task:

```python
import functools, traceback

def strict_task(fn):
    @functools.wraps(fn)
    def wrapper(case):
        try:
            return fn(case)
        except Exception:
            print(f"!!! task failed for {case.name!r} — a bug, not a low score")
            traceback.print_exc()
            raise
    return wrapper
```

### Trap: tool-accuracy evaluators and no-tool cases

`ToolSelectionAccuracyEvaluator` and `ToolParameterAccuracyEvaluator` score `0.0` on a case
that makes no tool call — **even when making no call was correct**. A suite that mixes
"should call `get_account_info`" with "should answer without tools" therefore shows a
depressed tool-accuracy mean that is not a regression. Either split those cases into their
own `Experiment` without the tool evaluators, or average only over cases whose
`expected_trajectory` is non-empty.

---

## EvalBuilder

The template's key testing pattern: subclass `SessionBuilder` to inject `trace_attributes` for OTEL-based evaluators without affecting production code.

```python
class EvalBuilder(SessionBuilder):
    def __init__(self, config, trace_attributes=None):
        super().__init__(config)
        self._trace_attributes = trace_attributes

    def _build_agent(self, tools, hooks, session_manager=None):
        kwargs = {
            "model": self.config.model_id,
            "tools": tools,
            "system_prompt": build_system_prompt(),
            "callback_handler": None,
        }
        if self._trace_attributes:
            kwargs["trace_attributes"] = self._trace_attributes
        return Agent(**kwargs)
```

Usage in tests:

```python
builder = EvalBuilder(config, trace_attributes={
    "gen_ai.conversation.id": case.session_id,
    "session.id": case.session_id,
})
session = builder.build()
response = session.invoke(case.input)
```

See `templates/strands-agentcore/evals/conftest.py` for the full implementation with fixtures.

---

## Output Evaluators

Rubric-based evaluation of response quality using an LLM judge.

### Rubric Format (YAML)

Store rubrics in `evals/rubrics/`:

```yaml
name: output_quality
evaluator: OutputEvaluator
include_inputs: true
rubric: |
  Evaluate the response based on:
  1. Accuracy - Is the information factually correct?
  2. Completeness - Does it fully answer the question?
  3. Clarity - Is it easy to understand?
  4. Tool integration - Did it properly use tool results?

  Score 1.0 if all criteria met excellently.
  Score 0.5 if partially met.
  Score 0.0 if inadequate.
```

### Test Pattern

```python
def test_output_quality(config, memory_exporter):
    cases = load_chats()

    def task_fn(case):
        memory_exporter.clear()
        builder = EvalBuilder(config, trace_attributes={...})
        session = builder.build()
        response = session.invoke(case.input)

        spans = memory_exporter.get_finished_spans()
        mapper = StrandsInMemorySessionMapper()
        mapped = mapper.map_to_session(spans, session_id=case.session_id)

        return {"output": str(response), "trajectory": mapped}

    evaluators = [
        OutputEvaluator(rubric=r["rubric"], include_inputs=True)
        for r in load_rubrics("output")
    ]
    experiment = Experiment(cases=cases, evaluators=evaluators)
    report = experiment.run_evaluations(strict_task(task_fn))
```

See `templates/strands-agentcore/evals/test_output.py` for the complete implementation.

---

## Trajectory Evaluators

Validate that the agent called the correct tools in the correct sequence.

### Extraction and Dual-Experiment Pattern

TrajectoryEvaluator expects tool name lists; trace evaluators expect OTEL sessions. Since these are different formats, run two experiments from one invocation:

```python
# Extract tool names from agent messages
trajectory_names = tools_use_extractor.extract_agent_tools_used_from_messages(agent.messages)
trajectory_evaluator.update_trajectory_description(
    tools_use_extractor.extract_tools_description(agent)
)

# Map OTEL spans to session format
mapper = StrandsInMemorySessionMapper()
mapped = mapper.map_to_session(spans, session_id=case.session_id)

# Experiment 1: TrajectoryEvaluator (tool-name trajectory)
return TaskOutput(output=str(response), trajectory=trajectory_names)

# Experiment 2: Trace evaluators (OTEL session trajectory)
return {"output": str(response), "trajectory": mapped}
```

See `templates/strands-agentcore/evals/test_trajectory.py` for the complete dual-experiment pattern.

---

## Trace Evaluators

Five built-in evaluators that analyze OTEL spans for different quality dimensions:

| Evaluator | Measures | Scale |
|-----------|----------|-------|
| `HelpfulnessEvaluator` | Response usefulness | 1-7 |
| `GoalSuccessRateEvaluator` | Task completion | Binary |
| `FaithfulnessEvaluator` | Hallucination detection | 0-1 |
| `ToolSelectionAccuracyEvaluator` | Correct tool choice | 0-1 |
| `ToolParameterAccuracyEvaluator` | Context-grounded params | 0-1 |

### Telemetry Setup

Trace evaluators require OTEL in-memory exporter:

```python
@pytest.fixture(scope="session")
def telemetry_setup():
    from strands_evals.telemetry import StrandsEvalsTelemetry
    return StrandsEvalsTelemetry().setup_in_memory_exporter()

@pytest.fixture(scope="session")
def memory_exporter(telemetry_setup):
    return telemetry_setup.in_memory_exporter
```

In each test case, clear the exporter before invocation and map spans after:

```python
memory_exporter.clear()
# ... invoke agent ...
spans = memory_exporter.get_finished_spans()
mapper = StrandsInMemorySessionMapper()
mapped = mapper.map_to_session(spans, session_id=case.session_id)
```

See `templates/strands-agentcore/evals/test_traces.py` for the complete implementation.

---

## Multi-Turn Simulation

ActorSimulator creates synthetic users with personality profiles that drive multi-turn conversations with your agent.

### Persona Format (YAML)

Store personas in `evals/personas/`:

```yaml
name: impatient_expert

# ActorProfile.traits must be a MAPPING (dict[str, Any]). A YAML list raises a pydantic
# ValidationError inside the task, which Experiment converts to a 0.0 score — see the
# "Experiment swallows task exceptions" trap above. Keys are free-form.
traits:
  expertise_level: expert
  communication_style: terse
  patience_level: low
  detail_preference: minimal

context: >
  An experienced power user who expects quick, accurate answers.
  Gets frustrated with unnecessary explanations.

goal: "Get order status information quickly without extra detail"
max_turns: 4
```

### The Persona x Case Matrix

Run every persona against every test case for coverage. **The case supplies the opening
message; the persona only shapes behaviour from turn 2 onward.**

```python
personas = load_personas()
cases = load_chats()

sim_cases, persona_by_case = [], {}
for persona in personas:
    for case in cases:
        name = f"{persona['name']}-{case.name}"
        sim_cases.append(Case(
            name=name,
            input=case.input,   # NOT persona["initial_message"] — see below
            metadata={"task_description": case.metadata.get("task_description", case.input)},
        ))
        persona_by_case[name] = persona
```

If the persona supplies `input`, every case in that persona's row starts with the same
message and the case dimension collapses — N cases become one conversation repeated N
times, with matching scores that read as agreement rather than as a bug. Drop
`initial_message` from persona files entirely so it cannot be reintroduced by accident.

### The Simulation Loop

```python
profile = ActorProfile(
    traits=persona["traits"],
    context=persona["context"],
    actor_goal=persona["goal"],
)
user_sim = ActorSimulator(
    actor_profile=profile,
    initial_query=case.input,
    system_prompt_template=DEFAULT_USER_SIMULATOR_PROMPT_TEMPLATE,
    max_turns=persona.get("max_turns", 5),
)

user_message = case.input
while user_sim.has_next():
    agent_response = agent(user_message)
    user_result = user_sim.act(str(agent_response))

    reply = user_result.structured_output.message
    if not reply:          # actor returns None once its goal is met
        break
    user_message = str(reply)
```

**Do not `str()` the reply before checking it.** The actor sets `message=None` when it
considers the goal achieved; `str(None)` appends a literal `"None"` as the next user turn,
which the agent then dutifully answers — inflating turn counts and polluting the trajectory
the evaluators grade.

See `templates/strands-agentcore/evals/test_simulation.py` for the complete implementation with span collection.

---

## Auto-Generating Test Cases

`ExperimentGenerator` creates cases from scenario YAML templates using an LLM.

### Scenario Format (YAML)

Store scenarios in `evals/scenarios/`:

```yaml
task_description: "Customer support agent with order and account tools"
context: |
  Available tools:
  - get_account_info() -> dict: Returns account details
  - list_orders() -> dict: Returns recent orders summary
  - get_order_details(order_number: int) -> dict: Returns full order details
  The agent helps users with account inquiries and order tracking.
num_cases: 5
num_topics: 2
include_expected_trajectory: true
```

### Two Generation Modes

```python
generator = ExperimentGenerator[str, str](
    input_type=str,
    output_type=str,
    include_expected_trajectory=True,
    include_expected_output=True,
)

# Mode 1: From explicit topics
experiment = await generator.from_scratch_async(
    topics=["order tracking", "account management"],
    task_description=scenario["task_description"],
    num_cases=5,
)

# Mode 2: From context description (LLM discovers topics)
experiment = await generator.from_context_async(
    context=scenario["context"],
    task_description=scenario["task_description"],
    num_cases=5,
    num_topics=2,
)
```

### Running Generation

```bash
python evals/generate.py                          # all scenarios
python evals/generate.py evals/scenarios/foo.yaml  # specific file
```

Generated cases are saved to `evals/chats/generated_<name>.json` in the same format as hand-written cases. See `templates/strands-agentcore/evals/generate.py`.

### Report Persistence

Reports are saved to `evals/reports/<test_name>_<timestamp>.json`:

```json
{
  "test": "output",
  "timestamp": "20260316T051338Z",
  "evaluators": [
    {
      "evaluator": "OutputEvaluator",
      "overall_score": 0.73,
      "pass_rate": 0.67,
      "scores": [0.2, 1.0, 1.0],
      "test_passes": [false, true, true],
      "reasons": ["Explanation per case..."]
    }
  ]
}
```

Use `report.display()` for static output. Do NOT use `report.run_display()` — it starts an interactive server that fails in CI/pytest environments.
