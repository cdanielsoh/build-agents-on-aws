"""Multi-turn simulation — ActorSimulator with trace evaluators.

Pattern: Build a (persona x case) matrix. Each combination becomes one eval case with
a dynamic multi-turn conversation driven by ActorSimulator.

Two things to get right, both of which silently produce a useless suite:

1. **The case owns the opening message; the persona owns the behaviour.** If the
   persona supplies the first message, every case in a persona's row starts
   identically and the case dimension collapses — N cases become one conversation
   repeated N times, with matching scores that look like agreement rather than a bug.

2. **`ActorProfile.traits` is a `dict`, not a list.** A list raises a pydantic
   ValidationError inside the task, which `Experiment` converts to a 0.0 score, so the
   whole suite "passes" in milliseconds with everything zeroed. `strict_task` from
   conftest exists to turn that back into a visible failure.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from strands_evals import ActorSimulator, Case, Experiment  # noqa: E402
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

from conftest import (  # noqa: E402
    EvalBuilder,
    assert_all_evaluators_scored,
    load_chats,
    load_personas,
    save_report,
    strict_task,
)


def test_simulation(config, memory_exporter):
    personas = load_personas()
    assert personas, "No persona files found in evals/personas/"

    cases = load_chats()
    assert cases, "No chat files found in evals/chats/"

    # Build the (persona x case) matrix. `input` comes from the case so each row is a
    # genuinely different conversation; the persona only shapes how the simulated user
    # behaves from the second turn onward.
    sim_cases: list[Case] = []
    persona_by_case: dict[str, dict] = {}

    for persona in personas:
        for case in cases:
            name = f"{persona['name']}-{case.name}"
            sim_cases.append(
                Case(
                    name=name,
                    input=case.input,
                    metadata={
                        "task_description": case.metadata.get(
                            "task_description", case.input
                        ),
                        "persona": persona["name"],
                        "original_case": case.name,
                    },
                )
            )
            persona_by_case[name] = persona

    def task_fn(case):
        persona = persona_by_case[case.name]

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
            reply = user_result.structured_output.message

            # The actor returns message=None once it considers its goal met. Stringify
            # it and you append the literal "None" as the next user turn, which the
            # agent then dutifully answers — inflating turn counts and polluting the
            # trajectory the evaluators grade.
            if not reply:
                break

            user_message = str(reply)
            conversation.append({
                "role": "user",
                "message": user_message,
                "reasoning": user_result.structured_output.reasoning,
            })

        agent_turns = sum(1 for m in conversation if m["role"] == "agent")
        print(f"\n--- {case.name} ({agent_turns} agent turns) ---")
        for msg in conversation:
            print(f"  [{msg['role'].upper():5s}] {msg['message'][:120]}")

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
    report = experiment.run_evaluations(strict_task(task_fn))

    report.display(include_actual_output=True)
    print()
    save_report("simulation", report)

    assert_all_evaluators_scored(report)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
