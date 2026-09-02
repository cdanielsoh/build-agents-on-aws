"""Shared fixtures and helpers for Strands Evals.

Key patterns:
- EvalBuilder: SessionBuilder subclass injecting trace_attributes for OTEL evals
- Data loaders: load_chats(), load_personas(), load_rubrics() auto-discover files
- Report persistence: save_reports() writes timestamped JSON to evals/reports/
"""

import functools
import json
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

# Make agent/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from strands_evals import Case  # noqa: E402

from core.builder import SessionBuilder  # noqa: E402
from core.config import AgentConfig  # noqa: E402

EVALS_DIR = Path(__file__).resolve().parent
REPORTS_DIR = EVALS_DIR / "reports"


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def config():
    return AgentConfig.from_env()


@pytest.fixture(scope="session")
def telemetry_setup():
    """Set up OTEL in-memory exporter for trace-level evals."""
    from strands_evals.telemetry import StrandsEvalsTelemetry

    telemetry = StrandsEvalsTelemetry().setup_in_memory_exporter()
    return telemetry


@pytest.fixture(scope="session")
def memory_exporter(telemetry_setup):
    return telemetry_setup.in_memory_exporter


# ── Data loaders ────────────────────────────────────────────────────


def load_chats(pattern: str = "*.json") -> list[Case]:
    """Load all chat JSON files from evals/chats/ into Case objects."""
    cases: list[Case] = []
    for path in sorted((EVALS_DIR / "chats").glob(pattern)):
        with open(path) as f:
            raw = json.load(f)
        for item in raw:
            cases.append(
                Case(
                    name=item["name"],
                    input=item["input"],
                    expected_output=item.get("expected_output"),
                    expected_trajectory=item.get("expected_trajectory"),
                    metadata=item.get("metadata", {}),
                )
            )
    return cases


def load_personas(pattern: str = "*.yaml") -> list[dict]:
    """Load all persona YAML files from evals/personas/."""
    personas: list[dict] = []
    for path in sorted((EVALS_DIR / "personas").glob(pattern)):
        with open(path) as f:
            personas.append(yaml.safe_load(f))
    return personas


def load_rubrics(evaluator_keyword: str) -> list[dict]:
    """Load rubric YAMLs whose name contains *evaluator_keyword*."""
    results: list[dict] = []
    for path in sorted((EVALS_DIR / "rubrics").glob("*.yaml")):
        if evaluator_keyword.lower() in path.stem.lower():
            with open(path) as f:
                results.append(yaml.safe_load(f))
    if not results:
        raise FileNotFoundError(
            f"No rubric matching '{evaluator_keyword}' in evals/rubrics/"
        )
    return results


# ── Report persistence ──────────────────────────────────────────────
#
# As of strands-agents-evals 1.2, `Experiment.run_evaluations()` returns ONE
# EvaluationReport covering every (case, evaluator) pair, not a list of reports —
# one per evaluator. Each row in `report.cases` is tagged with the evaluator that
# produced it. The report serializes itself, so there is no hand-rolled JSON here.


def save_report(test_name: str, report) -> Path:
    """Write the report to evals/reports/<test_name>_<timestamp>.json."""
    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = REPORTS_DIR / f"{test_name}_{ts}.json"
    report.to_file(str(out_path))
    print(f"\nReport saved: {out_path}")
    return out_path


def strict_task(fn):
    """Wrap a task function so a crash fails the test instead of scoring 0.0.

    `Experiment.run_evaluations` catches exceptions raised by the task and records the
    case as a zero. That is reasonable for a flaky model call, but it makes a genuine
    bug — a renamed SDK field, a malformed fixture — indistinguishable from an agent
    that answered badly, and the whole suite completes in milliseconds with every
    score at 0.00 while still reporting "passed".

    Wrapping the task means the first real error surfaces with its traceback. Remove
    the wrapper only when you deliberately want a partial run to survive transient
    failures.
    """

    @functools.wraps(fn)
    def wrapper(case):
        try:
            return fn(case)
        except Exception:
            print(f"\n!!! task failed for case {case.name!r} — this is a bug, not a low score")
            traceback.print_exc()
            raise

    return wrapper


def _row_field(row, key: str):
    """Read a field from a report row, which may be a dict or a model."""
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def scores_by_evaluator(report) -> dict[str, list[float]]:
    """Group scores by the evaluator that produced them.

    `report.scores` is flat across all evaluators, so asserting on it directly mixes
    a rubric score with, say, tool-selection accuracy. Group first, then assert per
    evaluator — a regression in one should not be maskable by a win in another.
    """
    grouped: dict[str, list[float]] = {}
    for row, score in zip(report.cases, report.scores, strict=False):
        name = _row_field(row, "evaluator") or "unknown"
        grouped.setdefault(str(name), []).append(score)
    return grouped


def assert_all_evaluators_scored(report) -> dict[str, list[float]]:
    """Fail loudly if an evaluator produced no rows.

    A silently-skipped evaluator looks identical to a passing one otherwise.

    Watch out for one trap when reading the per-evaluator means this prints:
    ToolSelectionAccuracyEvaluator and ToolParameterAccuracyEvaluator score 0.0 on a
    case that makes NO tool call — even when making no call was the correct behaviour.
    A suite that mixes tool cases with "should not call anything" cases will therefore
    show a depressed tool-accuracy mean that is not a regression. Either split those
    cases into their own Experiment without the tool evaluators, or read the mean only
    across cases whose expected_trajectory is non-empty.
    """
    grouped = scores_by_evaluator(report)
    assert grouped, "No evaluator produced any scores"
    for name, scores in sorted(grouped.items()):
        assert scores, f"Evaluator {name} produced no scores"
        print(f"  {name:38s} n={len(scores):<3d} mean={sum(scores) / len(scores):.2f}")
    return grouped


# ── EvalBuilder ─────────────────────────────────────────────────────


class EvalBuilder(SessionBuilder):
    """SessionBuilder subclass that injects trace_attributes for OTEL-based evals.

    Inherits the full production agent configuration (model with CacheConfig,
    conversation manager, etc.) and adds trace_attributes so evaluators can
    access span data.
    """

    def __init__(self, config: AgentConfig, trace_attributes: dict | None = None):
        super().__init__(config)
        self._trace_attributes = trace_attributes

    def _build_agent(self, tools, **kwargs):
        # **kwargs so this override survives new SessionBuilder._build_agent params
        # without needing an edit every time the base signature grows.
        agent = super()._build_agent(tools, **kwargs)
        if self._trace_attributes:
            agent.trace_attributes = self._trace_attributes
        return agent
