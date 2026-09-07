#!/usr/bin/env python3
"""Resolve the Lens question graph against a survey, and print the walk.

The point is that reachability stops being a judgement call. Given what access you actually have,
this says which of the 41 questions you can answer, which you cannot and why, and what to do
instead — so a question nobody could answer is recorded `unknown` with its blocker rather than
`absent`, which would be a false finding about the customer.

Usage:
    python3 lens_plan.py --access access.yaml [--format text|yaml]
    python3 lens_plan.py --interactive          # answer the six survey questions inline

`access.yaml` is the `access:` block from the decision record. Anything omitted counts as false,
because assuming access you did not confirm is the failure this exists to prevent.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

try:
    import yaml
except ImportError:
    sys.exit("needs pyyaml:  pip install pyyaml")

GRAPH = pathlib.Path(__file__).parent.parent / "skills/migrate-eks-to-agentcore/references/lens-graph.yaml"

# Survey answer -> which access classes it grants.
GRANTS = {
    "source_available":          ["source"],
    "control_plane_read":        ["cluster", "logs"],
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


def granted(access: dict) -> set[str]:
    have = set()
    for key, classes in GRANTS.items():
        if access.get(key) is True:
            have.update(classes)
    # `aws` needs cluster-era credentials AND the customer's own account: reads in the wrong
    # account describe your infrastructure, which is worse than no answer.
    if access.get("control_plane_read") and access.get("account_is_customers"):
        have.add("aws")
    # Source is only evidence if it matches what is deployed.
    if access.get("source_available") and access.get("source_matches_deployment") is False:
        have.discard("source")
    return have


def resolve(graph: dict, access: dict, multi_agent: bool | None) -> list[dict]:
    have = granted(access)
    qs = graph["questions"]
    state: dict[str, str] = {}
    out = []

    # Iterate to a fixed point: `after` edges can block a question whose own access is fine.
    for _ in range(len(qs) + 1):
        changed = False
        for qid, spec in qs.items():
            if qid in state:
                continue
            if spec.get("only_if") == "multi_agent" and multi_agent is False:
                state[qid] = "not_applicable"; changed = True; continue
            missing = [c for c in spec.get("needs", []) if c not in have]
            unmet = [d for d in spec.get("after", []) if state.get(d) not in (None, "reachable")
                     and state.get(d) is not None]
            pending = [d for d in spec.get("after", []) if d not in state]
            if pending:
                continue
            if missing:
                state[qid] = "blocked"; changed = True
            elif unmet:
                state[qid] = "degraded"; changed = True
            else:
                state[qid] = "reachable"; changed = True
        if not changed:
            break

    for qid in sorted(qs, key=lambda s: (s[:8], int("".join(filter(str.isdigit, s))))):
        spec = qs[qid]
        missing = [c for c in spec.get("needs", []) if c not in have]
        unmet = [d for d in spec.get("after", []) if state.get(d) in ("blocked", "not_applicable")]
        out.append({
            "id": qid,
            "state": state.get(qid, "reachable"),
            "missing_access": missing,
            "blocked_by_question": unmet,
            "substitute": spec.get("substitute"),
            "record_as": ("not_applicable" if state.get(qid) == "not_applicable"
                          else "unknown" if missing and not spec.get("substitute")
                          else None),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--access")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--multi-agent", choices=["yes", "no", "unknown"], default="unknown")
    ap.add_argument("--format", choices=["text", "yaml"], default="text")
    a = ap.parse_args()

    if a.interactive:
        access = {}
        print("Six access questions. Anything you cannot confirm, answer n.\n")
        for key, prompt in SURVEY_PROMPTS:
            access[key] = input(f"{prompt} [y/n] ").strip().lower().startswith("y")
        print()
    elif a.access:
        raw = yaml.safe_load(pathlib.Path(a.access).read_text())
        access = raw.get("access", raw)
    else:
        return ap.error("give --access <file> or --interactive")

    graph = yaml.safe_load(GRAPH.read_text())
    ma = {"yes": True, "no": False, "unknown": None}[a.multi_agent]
    rows = resolve(graph, access, ma)

    if a.format == "yaml":
        print(yaml.dump({"plan": rows}, sort_keys=False))
        return 0

    have = sorted(granted(access))
    print(f"access granted: {', '.join(have) or 'NOTHING — the assessment is not possible'}\n")
    buckets = {"reachable": [], "degraded": [], "blocked": [], "not_applicable": []}
    for r in rows:
        buckets[r["state"]].append(r)

    print(f"{'reachable':<16}{len(buckets['reachable']):>3}  answer these normally")
    print(f"{'degraded':<16}{len(buckets['degraded']):>3}  a prerequisite question is unanswerable")
    print(f"{'blocked':<16}{len(buckets['blocked']):>3}  access missing")
    print(f"{'not_applicable':<16}{len(buckets['not_applicable']):>3}  shape does not apply\n")

    for name in ("blocked", "degraded", "not_applicable"):
        if not buckets[name]:
            continue
        print(f"── {name} ──")
        for r in buckets[name]:
            why = ", ".join(r["missing_access"] or r["blocked_by_question"]) or "shape"
            print(f"  {r['id']:<13} needs: {why}")
            if r["substitute"]:
                print(f"                → {r['substitute']}")
            elif name == "blocked":
                print(f"                → nothing substitutes. Record `unknown` with the blocker, NOT `absent`.")
        print()

    print("Everything above that is not `reachable` belongs in the record with its blocker.")
    print("None of it is a finding about the customer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
