"""Multi-turn simulation — ActorSimulator with trace evaluators.

Pattern: Build a (persona x case) matrix. Each combination becomes one
eval case with a dynamic multi-turn conversation driven by ActorSimulator.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from strands_evals import Case, Experiment, ActorSimulator  # noqa: E402
from strands_evals.evaluators import (  # noqa: E402
    FaithfulnessEvaluator,
    GoalSuccessRateEvaluator,
    HelpfulnessEvaluator,
    ToolParameterAccuracyEvaluator,
    ToolSelectionAccuracyEvaluator,
)
from strands_evals.mappers import StrandsInMemorySessionMapper  # noqa: E402
from strands_evals.simulation.prompt_templates.actor_system_prompt import (  # noqa: E402
    DEFAULT_USER_SIMULATOR_PROMPT_TEMPLATE,
)
from strands_evals.types.simulation import ActorProfile  # noqa: E402

from conftest import EvalBuilder, load_chats, load_personas, save_reports  # noqa: E402


def test_simulation(config, memory_exporter):
    personas = load_personas()
    assert personas, "No persona files found in evals/personas/"

    cases = load_chats()
    assert cases, "No chat files found in evals/chats/"

    # Build (persona x case) matrix
    sim_cases: list[Case] = []
    sim_meta: list[dict] = []

    for persona in personas:
        for case in cases:
            task_desc = case.metadata.get("task_description", case.input)
            sim_cases.append(
                Case(
                    name=f"{persona['name']}-{case.name}",
                    input=persona.get("initial_message", case.input),
                    metadata={
                        "task_description": task_desc,
                        "persona": persona["name"],
                        "original_case": case.name,
                    },
                )
            )
            sim_meta.append(persona)

    def task_fn(case):
        idx = next(i for i, c in enumerate(sim_cases) if c.name == case.name)
        persona = sim_meta[idx]

        memory_exporter.clear()

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

        builder = EvalBuilder(
            config,
            trace_attributes={
                "gen_ai.conversation.id": case.session_id,
                "session.id": case.session_id,
            },
        )
        session = builder.build()
        agent = session.agent

        user_message = case.input
        conversation: list[dict] = []
        all_spans = []
        last_agent_message = ""

        while user_sim.has_next():
            memory_exporter.clear()

            agent_response = agent(user_message)
            last_agent_message = str(agent_response)
            conversation.append({"role": "agent", "message": last_agent_message})

            turn_spans = list(memory_exporter.get_finished_spans())
            all_spans.extend(turn_spans)

            user_result = user_sim.act(last_agent_message)
            user_message = str(user_result.structured_output.message)
            conversation.append({
                "role": "user",
                "message": user_message,
                "reasoning": user_result.structured_output.reasoning,
            })

        print(f"\n--- {case.name} ({len(conversation) // 2} turns) ---")
        for msg in conversation:
            role = msg["role"].upper()
            print(f"  [{role}] {msg['message'][:120]}")

        mapper = StrandsInMemorySessionMapper()
        mapped = mapper.map_to_session(all_spans, session_id=case.session_id)

        return {"output": last_agent_message, "trajectory": mapped}

    evaluators = [
        HelpfulnessEvaluator(),
        GoalSuccessRateEvaluator(),
        FaithfulnessEvaluator(),
        ToolSelectionAccuracyEvaluator(),
        ToolParameterAccuracyEvaluator(),
    ]

    experiment = Experiment(cases=sim_cases, evaluators=evaluators)
    reports = experiment.run_evaluations(task_fn)

    for report in reports:
        report.display()
        print()

    save_reports("simulation", evaluators, reports)

    assert reports[0].scores, "HelpfulnessEvaluator returned no scores"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
