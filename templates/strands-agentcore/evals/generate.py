"""Generate test cases from scenario YAML files using ExperimentGenerator.

Usage:
    python evals/generate.py                          # all scenarios
    python evals/generate.py evals/scenarios/foo.yaml  # specific file
"""

import asyncio
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from strands_evals.evaluators import OutputEvaluator, TrajectoryEvaluator  # noqa: E402
from strands_evals.generators import ExperimentGenerator  # noqa: E402

EVALS_DIR = Path(__file__).resolve().parent
EVALUATOR_MAP = {
    "OutputEvaluator": OutputEvaluator,
    "TrajectoryEvaluator": TrajectoryEvaluator,
}


async def generate_from_scenario(scenario_path: Path) -> None:
    with open(scenario_path) as f:
        scenario = yaml.safe_load(f)

    num_cases = scenario.get("num_cases", 5)
    evaluator_cls = EVALUATOR_MAP.get(scenario.get("evaluator", ""), None)
    include_trajectory = scenario.get("include_expected_trajectory", False)

    generator = ExperimentGenerator[str, str](
        input_type=str,
        output_type=str,
        include_expected_trajectory=include_trajectory,
        include_expected_output=True,
    )

    topics = scenario.get("topics")
    num_topics = scenario.get("num_topics")

    if topics:
        experiment = await generator.from_scratch_async(
            topics=topics,
            task_description=scenario["task_description"],
            num_cases=num_cases,
            evaluator=evaluator_cls,
        )
    else:
        kwargs = {
            "context": scenario["context"],
            "task_description": scenario["task_description"],
            "num_cases": num_cases,
            "evaluator": evaluator_cls,
        }
        if num_topics:
            kwargs["num_topics"] = num_topics
        experiment = await generator.from_context_async(**kwargs)

    # Convert to the same JSON format as hand-written chats
    cases_json = []
    for case in experiment.cases:
        entry = {"name": case.name, "input": case.input}
        if case.expected_output:
            entry["expected_output"] = case.expected_output
        if case.expected_trajectory:
            entry["expected_trajectory"] = case.expected_trajectory
        if case.metadata:
            entry["metadata"] = case.metadata
        cases_json.append(entry)

    stem = scenario_path.stem.removeprefix("example_")
    out_path = EVALS_DIR / "chats" / f"generated_{stem}.json"
    with open(out_path, "w") as f:
        json.dump(cases_json, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(cases_json)} cases to {out_path}")


async def main():
    if len(sys.argv) > 1:
        paths = [Path(p) for p in sys.argv[1:]]
    else:
        paths = sorted((EVALS_DIR / "scenarios").glob("*.yaml"))

    if not paths:
        print("No scenario files found in evals/scenarios/")
        return

    for path in paths:
        print(f"Generating from {path.name}...")
        await generate_from_scenario(path)


if __name__ == "__main__":
    asyncio.run(main())
