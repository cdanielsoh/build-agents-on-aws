"""Trajectory-level evaluation — TrajectoryEvaluator + trace evaluators.

TrajectoryEvaluator expects extracted tool names; trace evaluators expect
OTEL sessions. These need different trajectory formats, so we run two
experiments from one agent invocation.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from strands_evals import Experiment  # noqa: E402
from strands_evals.evaluators import (  # noqa: E402
    FaithfulnessEvaluator,
    GoalSuccessRateEvaluator,
    HelpfulnessEvaluator,
    OutputEvaluator,
    ToolParameterAccuracyEvaluator,
    ToolSelectionAccuracyEvaluator,
    TrajectoryEvaluator,
)
from strands_evals.extractors import tools_use_extractor  # noqa: E402
from strands_evals.mappers import StrandsInMemorySessionMapper  # noqa: E402
from strands_evals.types import TaskOutput  # noqa: E402

from conftest import (  # noqa: E402
    EvalBuilder,
    assert_all_evaluators_scored,
    load_chats,
    load_rubrics,
    save_report,
    strict_task,
)

TRAJECTORY_RUBRIC = (
    "Evaluate whether the agent used the correct tools in the correct sequence. "
    "Use exact_match_scorer to compare against expected_trajectory. "
    "Score 1.0 for exact match, 0.5 for correct tools in wrong order, 0.0 for wrong tools."
)


def test_trajectory(config, memory_exporter):
    cases = load_chats()
    assert cases, "No chat files found in evals/chats/"

    trajectory_evaluator = TrajectoryEvaluator(rubric=TRAJECTORY_RUBRIC, include_inputs=True)
    cache: dict[str, dict] = {}

    def _invoke(case):
        if case.name in cache:
            return cache[case.name]

        memory_exporter.clear()
        builder = EvalBuilder(
            config,
            trace_attributes={
                "gen_ai.conversation.id": case.session_id,
                "session.id": case.session_id,
            },
        )
        session = builder.build()
        agent = session.agent
        response = agent(case.input)

        trajectory_names = tools_use_extractor.extract_agent_tools_used_from_messages(
            agent.messages
        )
        trajectory_evaluator.update_trajectory_description(
            tools_use_extractor.extract_tools_description(agent)
        )

        finished_spans = memory_exporter.get_finished_spans()
        mapper = StrandsInMemorySessionMapper()
        mapped = mapper.map_to_session(finished_spans, session_id=case.session_id)

        cache[case.name] = {
            "output": str(response),
            "trajectory_names": trajectory_names,
            "trajectory_session": mapped,
        }
        return cache[case.name]

    # Experiment 1: TrajectoryEvaluator + OutputEvaluators (tool-name trajectory)
    def task_fn_trajectory(case):
        result = _invoke(case)
        return TaskOutput(output=result["output"], trajectory=result["trajectory_names"])

    traj_evaluators = [trajectory_evaluator] + [
        OutputEvaluator(rubric=r["rubric"], include_inputs=r.get("include_inputs", True))
        for r in load_rubrics("output")
    ]
    exp1 = Experiment(cases=cases, evaluators=traj_evaluators)
    report1 = exp1.run_evaluations(strict_task(task_fn_trajectory))

    # Experiment 2: Trace-level evaluators (OTEL session trajectory)
    def task_fn_traces(case):
        result = _invoke(case)
        return {"output": result["output"], "trajectory": result["trajectory_session"]}

    trace_evaluators = [
        HelpfulnessEvaluator(),
        GoalSuccessRateEvaluator(),
        FaithfulnessEvaluator(),
        ToolSelectionAccuracyEvaluator(),
        ToolParameterAccuracyEvaluator(),
    ]
    exp2 = Experiment(cases=cases, evaluators=trace_evaluators)
    report2 = exp2.run_evaluations(strict_task(task_fn_traces))

    for label, report in (("trajectory", report1), ("traces", report2)):
        print(f"\n=== {label} ===")
        report.display(include_actual_trajectory=True)
        save_report(f"trajectory_{label}", report)

    grouped = assert_all_evaluators_scored(report1)
    assert_all_evaluators_scored(report2)

    traj_scores = [s for name, ss in grouped.items() if "Trajectory" in name for s in ss]
    assert traj_scores, "TrajectoryEvaluator produced no scores"
    avg = sum(traj_scores) / len(traj_scores)
    assert avg > 0, f"Average trajectory score {avg:.2f} is too low"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
