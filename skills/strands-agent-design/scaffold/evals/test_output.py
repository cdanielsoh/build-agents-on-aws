"""Output-level evaluation — OutputEvaluator + all trace-level evaluators."""

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
)
from strands_evals.mappers import StrandsInMemorySessionMapper  # noqa: E402

from conftest import EvalBuilder, load_chats, load_rubrics, save_reports  # noqa: E402


def test_output_quality(config, memory_exporter):
    cases = load_chats()
    assert cases, "No chat files found in evals/chats/"

    def task_fn(case):
        memory_exporter.clear()

        builder = EvalBuilder(
            config,
            trace_attributes={
                "gen_ai.conversation.id": case.session_id,
                "session.id": case.session_id,
            },
        )
        session = builder.build()
        response = session.invoke(case.input)

        finished_spans = memory_exporter.get_finished_spans()
        mapper = StrandsInMemorySessionMapper()
        mapped = mapper.map_to_session(finished_spans, session_id=case.session_id)

        return {"output": str(response), "trajectory": mapped}

    evaluators = [
        OutputEvaluator(
            rubric=r["rubric"],
            include_inputs=r.get("include_inputs", True),
        )
        for r in load_rubrics("output")
    ] + [
        HelpfulnessEvaluator(),
        GoalSuccessRateEvaluator(),
        FaithfulnessEvaluator(),
        ToolSelectionAccuracyEvaluator(),
        ToolParameterAccuracyEvaluator(),
    ]

    experiment = Experiment(cases=cases, evaluators=evaluators)
    reports = experiment.run_evaluations(task_fn)

    for report in reports:
        report.display()
        print()

    save_reports("output", evaluators, reports)

    avg = sum(reports[0].scores) / len(reports[0].scores)
    assert avg > 0, f"Average output score {avg:.2f} is too low"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
