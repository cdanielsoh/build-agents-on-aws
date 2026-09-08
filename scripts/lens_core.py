"""Shared model: loading, the receipt vocabulary, and referential primitives.

Everything here is used by more than one subcommand, which is the only reason it is here. A helper
that one command uses belongs beside that command — this module exists so the receipt model has
exactly one definition, not as a bucket for utilities.

Nothing in here prints, and nothing in here decides anything. `validate_receipt` returns a list of
problems rather than exiting, so its caller chooses between refusing to write (`record`) and
reporting (`validate`) — the same rule applied twice with opposite consequences.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import re
import sys

try:
    import yaml
except ImportError:
    sys.exit("needs pyyaml:  pip install pyyaml")


REFS = pathlib.Path(__file__).parent.parent / "skills/migrate-eks-to-agentcore/references"
GRAPH_PATH = REFS / "lens-graph.yaml"
SCHEMA_PATH = REFS / "record-schema.yaml"
DEFAULT_DIR = pathlib.Path(".agentcore-migration")

# Survey answer -> which access classes it grants.
GRANTS = {
    "source_available":          ["source"],
    "control_plane_read":        ["cluster", "logs"],
    "deployment_exists":         ["deployment"],
    "carries_real_traffic":      ["telemetry"],
    "invocation_permitted":      ["behaviour"],
    "load_generation_permitted": ["load"],
    "product_owner_reachable":   ["human"],
}

SURVEY_PROMPTS = [
    ("source_available", "S1  Is application source available to read?"),
    ("source_matches_deployment", "S1b Does that source match the deployed revision?"),
    ("control_plane_read", "S2  Do you have control-plane / cluster read access?"),
    ("account_is_customers", "S2b Are you in the customer's own AWS account?"),
    ("deployment_exists", "S3  Is there a running deployment?"),
    ("carries_real_traffic", "S3b Does it carry real user traffic?"),
    ("invocation_permitted", "S4  May you send requests to the agent?"),
    ("load_generation_permitted", "S5  May you generate concurrent load?"),
    ("product_owner_reachable", "S6  Is there someone who can answer product questions?"),
]

NODE_KINDS = ("question", "remedy", "node", "gate", "measurement", "topology", "artifact", "context",
              # written by /plan rather than /assess, into the same log. One log, because staleness
              # is only computable when the plan's verification and the observations it rests on
              # carry comparable timestamps.
              "plan_item")


# ── loading ─────────────────────────────────────────────────────────────────────────────────

def load_graph() -> dict:
    return yaml.safe_load(GRAPH_PATH.read_text())


def load_schema() -> dict:
    return yaml.safe_load(SCHEMA_PATH.read_text())


def load_yaml(path: pathlib.Path):
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text()) or {}


def load_access(path: pathlib.Path) -> dict:
    raw = load_yaml(path)
    if raw is None:
        return {}
    return raw.get("access", raw) if isinstance(raw, dict) else {}


def load_receipts(d: pathlib.Path) -> list[dict]:
    f = d / "receipts.jsonl"
    if not f.exists():
        return []
    out = []
    for n, line in enumerate(f.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            sys.exit(f"receipts.jsonl:{n}: not valid JSON — {e}")
    return out


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sort_key(s: str) -> tuple:
    digits = "".join(filter(str.isdigit, s))
    return (s[:8], int(digits) if digits else 0, s)


def err(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 2


# ── record ──────────────────────────────────────────────────────────────────────────────────

KIND_FLAGS = {
    "question": "--question", "remedy": "--remedy", "node": "--node", "gate": "--gate",
    "measurement": "--measurement", "topology": "--topology", "artifact": "--artifact",
    "context": "--context", "plan_item": "--plan-item",
}


def valid_subjects(kind: str, graph: dict, schema: dict) -> set[str] | None:
    """None means free text."""
    return {
        "question": set(graph["questions"]),
        "remedy": set(graph["questions"]),
        "node": set(graph.get("nodes", {})),
        "gate": set(schema["gate_ids"]),
        "measurement": set(schema["measurement_fields"]),
        "topology": set(schema["topology_fields"]),
        "context": set(schema["context_fields"]),
        "artifact": None,
        # Free text here, cross-checked against plan/items.yml by `validate` instead. The id is
        # authored by /plan, so there is no fixed vocabulary to validate against at record time —
        # and requiring items.yml to exist first would make the receipt unwritable during the run
        # that produces it.
        "plan_item": None,
    }[kind]


def blocked_by_vocabulary(graph: dict) -> set[str]:
    """Everything `blocked_by` may legally name — the union of what a blocker can be.

    Survey ids are the canonical form the commands' examples use (`--blocked-by S2`); the rest are
    admitted because `resolve` itself reports blockers as node ids and access classes, so a receipt
    copying what `resolve` printed must validate.
    """
    survey = {label.split()[0] for _key, label in SURVEY_PROMPTS}
    access_keys = {key for key, _label in SURVEY_PROMPTS}
    access_classes = set(graph.get("access_classes", {})) | {c for cs in GRANTS.values() for c in cs}
    return survey | access_keys | access_classes | set(graph.get("nodes", {})) | set(graph["questions"])


def coerce(text: str):
    """`record` takes strings; findings.yml should carry numbers and booleans as themselves."""
    low = text.strip().lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("none", "null"):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def validate_receipt(rec: dict, graph: dict, schema: dict, known_ids: set[str]) -> list[str]:
    errs = []
    kind = rec.get("kind")
    spec = schema["kinds"].get(kind)
    if spec is None:
        return [f"kind `{kind}` is not one of {', '.join(NODE_KINDS)}"]

    subject = rec.get("subject")
    allowed = valid_subjects(kind, graph, schema)
    if not subject:
        errs.append(f"kind {kind} needs a subject")
    elif allowed is not None and subject not in allowed:
        stem = (re.match(r"[A-Za-z_]+", str(subject)) or re.match("", "")).group(0).lower()
        near: list[str] = []
        while stem and not near:
            near = [s for s in sorted(allowed) if s.lower().startswith(stem)]
            stem = stem[:-1]
        errs.append(f"`{subject}` is not a known {kind} subject"
                    + (f" — did you mean {', '.join(near[:4])}?" if near else ""))

    tag = rec.get("tag")
    if tag not in schema["evidence_tags"]:
        errs.append(f"tag `{tag}` is not in evidence.md's tag set")
    if not rec.get("evidence"):
        errs.append("every receipt needs `evidence` — what you saw, at file:line where there is one")
    if tag in schema["tags_requiring_source"] and not rec.get("source"):
        errs.append(f"tag `{tag}` asserts an observation, so `source` is required: "
                    "the command you ran or the artifact you opened")

    states = spec.get("state")
    if states:
        if rec.get("state") not in states:
            errs.append(f"kind {kind} needs --state one of: {', '.join(states)}")
    elif "state" in rec:
        errs.append(f"kind {kind} takes no --state")

    if spec.get("takes_value") and "value" not in rec:
        errs.append(f"kind {kind} needs --value")

    if kind == "remedy" and rec.get("closed_by") not in schema["enums"]["closed_by"]:
        errs.append("kind remedy needs --closed-by one of: " + ", ".join(schema["enums"]["closed_by"]))

    for field, enum in (("ineffective_because", "ineffective_because"),
                        ("defect_owner", "defect_owner"),
                        ("compute_type_assumed", "compute_type_assumed"),
                        ("source_of_value", "source_of_value"),
                        ("by", "by")):
        if field in rec and rec[field] not in schema["enums"][enum]:
            errs.append(f"{field}=`{rec[field]}` not in: {', '.join(map(str, schema['enums'][enum]))}")

    if kind == "topology" and subject in schema["topology_fields"]:
        legal = schema["topology_fields"][subject]
        if isinstance(legal, list) and rec.get("value") not in legal:
            errs.append(f"topology {subject} must be one of: {', '.join(map(str, legal))}")

    if rec.get("state") == "present_but_ineffective" and "ineffective_because" not in rec:
        errs.append("`present_but_ineffective` needs --ineffective-because. "
                    "`effective: false` alone restated the state on 13 of 14 rows measured")

    if kind == "plan_item":
        state = rec.get("state")
        if state in ("built", "failing") and not rec.get("verified_by"):
            errs.append(f"`{state}` needs --verified-by: the command you actually ran. Without it "
                        "the receipt asserts execution and records nothing about it, which is the "
                        "defect this kind exists to close")
        if state == "unverifiable" and not rec.get("blocked_by"):
            errs.append("`unverifiable` needs --blocked-by. An artifact nobody could run is a fine "
                        "outcome; one with no stated reason is indistinguishable from untried")
        if state in ("unverifiable", "planned") and rec.get("verified_by"):
            errs.append(f"`{state}` takes no --verified-by — it says nothing was run. Use `built` "
                        "or `failing` if something was")

    # `blocked_by` names what stopped you, so it has to be a thing the record can resolve. Presence
    # was checked above for plan items and validity was checked nowhere, so `--blocked-by NOPE` was
    # accepted — a real probe on a real run, which then had to be superseded. Free text here is worse
    # than a missing field: it reads as a citation and resolves to nothing. Applies to every kind,
    # not just plan items, because an unreachable question or node carries a blocker too.
    if "blocked_by" in rec:
        legal = blocked_by_vocabulary(graph)
        for token in ([rec["blocked_by"]] if isinstance(rec["blocked_by"], str)
                      else list(rec["blocked_by"] or [])):
            # `AGENTSEC03/inbound_auth` is how a component-scoped question is named everywhere else,
            # so accept it here rather than making the blocker the one place it is spelled differently.
            if str(token).split("/")[0] not in legal:
                errs.append(
                    f"blocked_by=`{token}` resolves to nothing. Give a survey id (S1, S2b, S4 …), "
                    "a check-graph node (A, F1, K …), a Lens question id, an access key "
                    "(`invocation_permitted` …) or an access class (`behaviour`, `human` …)")

    if "supersedes" in rec:
        if rec["supersedes"] not in known_ids:
            errs.append(f"supersedes `{rec['supersedes']}`, which is not an existing receipt id")
        if rec.get("reason") not in schema["enums"]["supersede_reason"]:
            errs.append("a superseding receipt needs --reason reinterpretation|re-observation. "
                        "Keep them distinct: reinterpretation means nobody looked again")

    known_fields = {"id", "at", "by", "kind", "subject", "state", "value", "tag", "evidence",
                    "source", "note", "supersedes", "reason"} | set(spec.get("optional", [])) \
                   | set(spec.get("requires", []))
    for k in rec:
        if k not in known_fields:
            errs.append(f"kind {kind} has no field `{k}` (record-schema.yaml lists what it takes)")
    return errs


def next_id(receipts: list[dict]) -> str:
    n = 0
    for r in receipts:
        m = re.fullmatch(r"r-(\d+)", str(r.get("id", "")))
        if m:
            n = max(n, int(m.group(1)))
    return f"r-{n + 1:04d}"


# ── findings ────────────────────────────────────────────────────────────────────────────────

def split_supersession(receipts: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """Returns (effective, superseded_by). A chain works because each link names one predecessor."""
    superseded_by = {}
    for r in receipts:
        if r.get("supersedes"):
            superseded_by[r["supersedes"]] = r
    effective = [r for r in receipts if r.get("id") not in superseded_by]
    return effective, superseded_by


def finding_key(r: dict) -> str:
    return f"{r['subject']}/{r['component']}" if r.get("component") else r["subject"]
