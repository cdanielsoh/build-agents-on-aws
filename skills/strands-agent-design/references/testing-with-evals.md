# Testing with Strands Evals SDK

Evaluate agents across four dimensions: output quality, tool call sequences, OTEL traces, and multi-turn simulated conversations.

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

The scaffold's `conftest.py` provides auto-discovery:

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

```python
experiment = Experiment(cases=cases, evaluators=[OutputEvaluator(rubric=rubric)])
reports = experiment.run_evaluations(task_fn)

for report in reports:
    report.display()  # use display() (static), NOT run_display() (interactive)
```

The `task_fn` receives a `Case` and returns `{"output": str, "trajectory": ...}`.

---

## EvalBuilder

The scaffold's key testing pattern: subclass `SessionBuilder` to inject `trace_attributes` for OTEL-based evaluators without affecting production code.

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

See `scaffold/evals/conftest.py` for the full implementation with fixtures.

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
    reports = experiment.run_evaluations(task_fn)
```

See `scaffold/evals/test_output.py` for the complete implementation.

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

See `scaffold/evals/test_trajectory.py` for the complete dual-experiment pattern.

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

See `scaffold/evals/test_traces.py` for the complete implementation.

---

## Multi-Turn Simulation

ActorSimulator creates synthetic users with personality profiles that drive multi-turn conversations with your agent.

### Persona Format (YAML)

Store personas in `evals/personas/`:

```yaml
name: impatient_expert
traits:
  expertise_level: expert
  communication_style: terse
  patience_level: low
  detail_preference: minimal
context: >
  An experienced power user who expects quick, accurate answers.
  Gets frustrated with unnecessary explanations.
goal: "Get order status information quickly without extra detail"
initial_message: "order status, last order"
max_turns: 4
```

### The Persona x Case Matrix

Run every persona against every test case for coverage:

```python
personas = load_personas()
cases = load_chats()

sim_cases = []
for persona in personas:
    for case in cases:
        sim_cases.append(Case(
            name=f"{persona['name']}-{case.name}",
            input=persona.get("initial_message", case.input),
            metadata={"task_description": case.metadata.get("task_description", case.input)},
        ))
```

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
    user_message = str(user_result.structured_output.message)
```

See `scaffold/evals/test_simulation.py` for the complete implementation with span collection.

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

Generated cases are saved to `evals/chats/generated_<name>.json` in the same format as hand-written cases. See `scaffold/evals/generate.py`.

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
