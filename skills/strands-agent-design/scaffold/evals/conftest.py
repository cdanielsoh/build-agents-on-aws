"""Shared fixtures and helpers for Strands Evals.

Key patterns:
- EvalBuilder: SessionBuilder subclass injecting trace_attributes for OTEL evals
- Data loaders: load_chats(), load_personas(), load_rubrics() auto-discover files
- Report persistence: save_reports() writes timestamped JSON to evals/reports/
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

# Make agent/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from core.config import AgentConfig  # noqa: E402
from core.builder import SessionBuilder  # noqa: E402
from strands_evals import Case  # noqa: E402

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


def save_reports(test_name: str, evaluators: list, reports: list) -> Path:
    """Save evaluation reports to evals/reports/<test_name>_<timestamp>.json."""
    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = REPORTS_DIR / f"{test_name}_{ts}.json"

    data = {
        "test": test_name,
        "timestamp": ts,
        "evaluators": [],
    }
    for evaluator, report in zip(evaluators, reports):
        entry = {
            "evaluator": evaluator.get_type_name(),
            "overall_score": report.overall_score,
            "pass_rate": (
                sum(report.test_passes) / len(report.test_passes)
                if report.test_passes
                else 0.0
            ),
            "scores": report.scores,
            "test_passes": report.test_passes,
            "reasons": report.reasons,
        }
        data["evaluators"].append(entry)

    with open(out_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nReport saved: {out_path}")
    return out_path


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

    def _build_agent(self, tools, hooks, session_manager=None):
        agent = super()._build_agent(tools, hooks, session_manager)
        if self._trace_attributes:
            agent.trace_attributes = self._trace_attributes
        return agent
