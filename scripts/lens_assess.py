"""The /assess subcommands: resolve, record, findings, status.

These walk lens-graph.yaml — the 41 Lens questions and the 15 check-graph nodes — against the
access survey. The plan side walks a different graph and lives in lens_phases.py; they share the
receipt model in lens_core.py and nothing else, which is why they are separate files.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import yaml

from lens_core import (GRANTS, KIND_FLAGS, NODE_KINDS, coerce, err, finding_key, load_access,
                       load_receipts, load_yaml, next_id, now, split_supersession, valid_subjects,
                       validate_receipt, _sort_key)


# ── resolve ─────────────────────────────────────────────────────────────────────────────────

def granted(access: dict) -> set[str]:
    have: set[str] = set()
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


def _resolve_specs(specs: dict, have: set[str], multi_agent: bool | None) -> dict[str, str]:
    """Fixed-point over `after` edges, shared by questions and nodes."""
    state: dict[str, str] = {}
    for _ in range(len(specs) + 1):
        changed = False
        for qid, spec in specs.items():
            if qid in state:
                continue
            if spec.get("only_if") == "multi_agent" and multi_agent is False:
                state[qid] = "not_applicable"; changed = True; continue
            missing = [c for c in spec.get("needs", []) if c not in have]
            any_of = spec.get("needs_any") or []
            if any_of and not any(c in have for c in any_of):
                missing = missing + [" or ".join(any_of)]
            deps = spec.get("after", [])
            if [d for d in deps if d not in state]:
                continue
            unmet = [d for d in deps if state.get(d) != "reachable"]
            if missing:
                state[qid] = "blocked"
            elif unmet:
                state[qid] = "degraded"
            else:
                state[qid] = "reachable"
            changed = True
        if not changed:
            break
    return state


def _rows(specs: dict, state: dict, have: set[str]) -> list[dict]:
    out = []
    for qid in sorted(specs, key=_sort_key):
        spec = specs[qid]
        missing = [c for c in spec.get("needs", []) if c not in have]
        any_of = spec.get("needs_any") or []
        if any_of and not any(c in have for c in any_of):
            missing = missing + [" or ".join(any_of)]
        unmet = [d for d in spec.get("after", []) if state.get(d) in ("blocked", "not_applicable")]
        out.append({
            "id": qid,
            "state": state.get(qid, "reachable"),
            "missing_access": missing,
            "blocked_by": unmet,
            "substitute": spec.get("substitute"),
            "record_as": ("not_applicable" if state.get(qid) == "not_applicable"
                          else "unknown" if missing and not spec.get("substitute")
                          else None),
        })
    return out


def resolve(graph: dict, access: dict, multi_agent: bool | None) -> dict:
    have = granted(access)
    qstate = _resolve_specs(graph["questions"], have, multi_agent)
    nstate = _resolve_specs(graph.get("nodes", {}), have, multi_agent)
    return {
        "access_granted": sorted(have),
        "nodes": _rows(graph.get("nodes", {}), nstate, have),
        "questions": _rows(graph["questions"], qstate, have),
    }


def print_resolve(res: dict) -> None:
    have = res["access_granted"]
    print(f"access granted: {', '.join(have) or 'NOTHING — the assessment is not possible'}\n")
    for label, rows in (("check-graph nodes", res["nodes"]), ("Lens questions", res["questions"])):
        buckets = {"reachable": [], "degraded": [], "blocked": [], "not_applicable": []}
        for r in rows:
            buckets[r["state"]].append(r)
        print(f"── {label} ({len(rows)}) ──")
        print(f"  {'reachable':<16}{len(buckets['reachable']):>3}  answer these normally")
        print(f"  {'degraded':<16}{len(buckets['degraded']):>3}  a prerequisite is unanswerable")
        print(f"  {'blocked':<16}{len(buckets['blocked']):>3}  access missing")
        print(f"  {'not_applicable':<16}{len(buckets['not_applicable']):>3}  shape does not apply")
        for name in ("blocked", "degraded", "not_applicable"):
            if not buckets[name]:
                continue
            print(f"\n  ── {name} ──")
            for r in buckets[name]:
                why = ", ".join(r["missing_access"] or r["blocked_by"]) or "shape"
                print(f"    {r['id']:<13} needs: {why}")
                if r["substitute"]:
                    print(f"                  → {r['substitute']}")
                elif name == "blocked":
                    print("                  → nothing substitutes. Record `unknown` with the "
                          "blocker, NOT `absent`.")
        print()
    print("Everything above that is not `reachable` belongs in a receipt with its blocker.")
    print("None of it is a finding about the customer.")


def cmd_record(a, graph: dict, schema: dict) -> int:
    d = a.dir
    receipts = load_receipts(d)
    known = {r.get("id") for r in receipts}
    pending: list[dict] = []

    if a.batch:
        src = sys.stdin.read() if a.batch == "-" else pathlib.Path(a.batch).read_text()
        for n, line in enumerate(src.splitlines(), 1):
            if line.strip():
                try:
                    pending.append(json.loads(line))
                except json.JSONDecodeError as e:
                    return err(f"--batch line {n}: {e}")
    else:
        chosen = [(k, getattr(a, k)) for k in NODE_KINDS if getattr(a, k, None)]
        if len(chosen) != 1:
            return err("give exactly one of " + ", ".join(KIND_FLAGS.values()) + ", or --batch")
        kind, subject = chosen[0]
        rec = {"kind": kind, "subject": subject}
        for flag, field in (("state", "state"), ("tag", "tag"), ("evidence", "evidence"),
                            ("source", "source"), ("note", "note"), ("component", "component"),
                            ("blocked_by", "blocked_by"), ("substitute_used", "substitute_used"),
                            ("closed_by", "closed_by"), ("remedy_verified", "remedy_verified"),
                            ("requires_runtime_move", "requires_runtime_move"),
                            ("ineffective_because", "ineffective_because"),
                            ("defect_owner", "defect_owner"),
                            ("blocked_by_artifact", "blocked_by_artifact"),
                            ("compute_type_assumed", "compute_type_assumed"),
                            ("source_of_value", "source_of_value"),
                            ("is_true_high_water_mark", "is_true_high_water_mark"),
                            ("derived_from", "derived_from"), ("how_to_get_it", "how_to_get_it"),
                            ("verified_by", "verified_by"), ("mutation_tested", "mutation_tested"),
                            ("artifacts", "artifacts"),
                            ("supersedes", "supersedes"), ("reason", "reason")):
            v = getattr(a, flag, None)
            if v is not None:
                rec[field] = coerce(v) if field in (
                    "remedy_verified", "requires_runtime_move", "is_true_high_water_mark",
                    "mutation_tested") else v
        # A comma-separated list is what a shell can produce; a list is what MANIFEST.md needs.
        if isinstance(rec.get("artifacts"), str):
            rec["artifacts"] = [p.strip() for p in rec["artifacts"].split(",") if p.strip()]
        if a.value is not None:
            # `coerce` turns "none" into Python None, which is right for a free number or a boolean
            # and wrong for an enum whose legal value is the STRING "none". flush_cadence already
            # had that value, so `--value none` was silently unwritable before this: the receipt was
            # rejected as "must be one of: ..., none, ..." while none was visibly in the list.
            # An enum wins over the general rule.
            legal = schema["topology_fields"].get(subject) if kind == "topology" else None
            rec["value"] = a.value if isinstance(legal, list) and a.value in [str(v) for v in legal] \
                else coerce(a.value)
        if a.by:
            rec["by"] = a.by
        pending.append(rec)

    stamped, problems = [], []
    for rec in pending:
        rec.pop("id", None)
        rec.pop("at", None)
        rid = next_id(receipts + stamped)
        # id and `at` are the script's, never the model's. That is what makes staleness
        # computable, and what makes a batch written at write-up time visible as a cluster of
        # identical timestamps a long way from the tool calls that should have produced it.
        full = {"id": rid, "at": now(), **rec}
        errs = validate_receipt(full, graph, schema, known | {r["id"] for r in stamped})
        if errs:
            problems.append((rec.get("kind"), rec.get("subject"), errs))
        else:
            stamped.append(full)

    if problems:
        for kind, subject, errs in problems:
            print(f"rejected {kind} {subject}:", file=sys.stderr)
            for e in errs:
                print(f"  - {e}", file=sys.stderr)
        print("\nNothing was written. An invalid receipt is worse than none — it looks like "
              "evidence.", file=sys.stderr)
        return 1

    d.mkdir(parents=True, exist_ok=True)
    with (d / "receipts.jsonl").open("a") as fh:
        for full in stamped:
            fh.write(json.dumps(full, ensure_ascii=False) + "\n")
    for full in stamped:
        print(f"{full['id']}  {full['kind']} {full['subject']}"
              + (f" → {full['state']}" if "state" in full else "")
              + (f" = {full['value']}" if "value" in full else "")
              + (f"  (supersedes {full['supersedes']})" if "supersedes" in full else ""))
    return 0


def build_findings(receipts: list[dict], graph: dict) -> dict:
    effective, superseded_by = split_supersession(receipts)
    by_id = {r["id"]: r for r in receipts}

    def latest(kind: str, keyfn) -> dict:
        out: dict = {}
        for r in effective:
            if r.get("kind") == kind:
                out[keyfn(r)] = r
        return out

    q_latest = latest("question", finding_key)
    remedies: dict[str, list[dict]] = {}
    for r in effective:
        if r.get("kind") == "remedy":
            remedies.setdefault(finding_key(r), []).append(r)

    def history(key: str, kinds: tuple[str, ...]) -> list[dict]:
        out = []
        for r in receipts:
            if r.get("kind") in kinds and finding_key(r) == key and r["id"] in superseded_by:
                nxt = superseded_by[r["id"]]
                out.append({"receipt": r["id"], "superseded_by": nxt["id"],
                            "reason": nxt.get("reason"),
                            "was": r.get("state", r.get("value")),
                            "note": nxt.get("note")})
        return out

    findings = []
    for key in sorted(set(q_latest) | set(remedies), key=_sort_key):
        q = q_latest.get(key)
        rs = remedies.get(key, [])
        row = {"id": key,
               "practice": (q or rs[0])["subject"],
               "component": (q or rs[0]).get("component")}
        if q:
            row.update({k: q[k] for k in ("state", "ineffective_because", "defect_owner",
                                          "evidence", "tag", "note", "blocked_by_artifact")
                        if k in q})
        else:
            # A remedy with no state receipt: a claim about a fix for something never observed.
            row["state"] = "unknown"
            row["note"] = "remedy recorded with no observation of the question itself"
        if rs:
            row["closed_by"] = sorted({r["closed_by"] for r in rs})
            verified = [r.get("remedy_verified") for r in rs]
            row["remedy_verified"] = (True if all(v is True for v in verified)
                                      else "unverifiable" if "unverifiable" in verified else False)
            rrm = [r.get("requires_runtime_move") for r in rs if "requires_runtime_move" in r]
            if rrm:
                row["requires_runtime_move"] = any(rrm)
        row["receipts"] = [r["id"] for r in ([q] if q else []) + rs]
        hist = history(key, ("question", "remedy"))
        if hist:
            row["history"] = hist
        findings.append({k: v for k, v in row.items() if v is not None})

    artifacts = []
    for key, r in latest("artifact", lambda x: x["subject"]).items():
        blocks = sorted({e["subject"] for e in effective
                         if e.get("blocked_by_artifact") == key}, key=_sort_key)
        artifacts.append({"artifact": key, "blocks": blocks, "receipt": r["id"],
                          "how_to_get_it": r.get("how_to_get_it"), "evidence": r.get("evidence")})

    def val_block(kind: str, keyfn=lambda r: r["subject"]) -> dict:
        out = {}
        for key, r in latest(kind, keyfn).items():
            entry = {"value": r.get("value"), "tag": r.get("tag"), "evidence": r.get("evidence"),
                     "receipts": [r["id"]] + [h["receipt"] for h in history(key, (kind,))]}
            for extra in ("source_of_value", "is_true_high_water_mark", "derived_from", "component"):
                if extra in r:
                    entry[extra] = r[extra]
            hist = history(key, (kind,))
            if hist:
                entry["history"] = hist
            out[key] = {k: v for k, v in entry.items() if v is not None}
        return out

    assessed = sorted({r["subject"] for r in effective if r.get("kind") == "question"}, key=_sort_key)
    all_q = set(graph["questions"])

    walk = []
    for nid, r in sorted(latest("node", lambda x: x["subject"]).items(), key=lambda kv: _sort_key(kv[0])):
        walk.append({k: v for k, v in {
            "node": nid, "state": r.get("state"), "blocked_by": r.get("blocked_by"),
            "substitute_used": r.get("substitute_used"), "evidence": r.get("evidence"),
            "receipt": r["id"]}.items() if v is not None})

    return {
        "generated_at": now(),
        "generated_by": "lens_plan.py findings — DO NOT HAND-EDIT. A correction is a new receipt.",
        "generated_from": {"receipts": len(receipts), "superseded": len(superseded_by),
                           "newest_receipt_at": max((r.get("at", "") for r in receipts), default=None)},
        "context": {k: v.get("value") for k, v in val_block("context").items()},
        "walk": walk,
        "gates": [{"check": k, "result": r.get("state"), "tag": r.get("tag"),
                   "evidence": r.get("evidence"),
                   "compute_type_assumed": r.get("compute_type_assumed"),
                   "receipt": r["id"]}
                  for k, r in sorted(latest("gate", lambda x: x["subject"]).items())],
        "topology": val_block("topology", finding_key),
        "measurements": val_block("measurement"),
        "network": {},
        "missing_artifacts": artifacts,
        "lens_coverage": {
            "scope": "questions_only",
            "questions_assessed": len(assessed),
            "questions_total": len(all_q),
            "inventory_rows": len(findings),
            "best_practices_assessed": 0,
            "no_receipt": sorted(all_q - set(assessed), key=_sort_key),
        },
        "findings": findings,
    }


def cmd_findings(a, graph: dict, schema: dict) -> int:
    receipts = load_receipts(a.dir)
    if not receipts:
        return err("no receipts.jsonl — nothing to derive findings from")
    doc = build_findings(receipts, graph)
    # network is a view over measurements, not a separate observation class.
    net = {k: v for k, v in doc["measurements"].items()
           if k.startswith(("vpc_", "existing_"))}
    doc["network"] = net
    for k in net:
        doc["measurements"].pop(k)
    out = a.dir / "findings.yml"
    a.dir.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.dump(doc, sort_keys=False, allow_unicode=True, width=100))
    c = doc["lens_coverage"]
    print(f"{out}: {c['inventory_rows']} rows over {c['questions_assessed']} of "
          f"{c['questions_total']} questions, from {doc['generated_from']['receipts']} receipts "
          f"({doc['generated_from']['superseded']} superseded)")
    if c["no_receipt"]:
        print(f"  no receipt yet: {len(c['no_receipt'])} questions — "
              "that is the frontier, not a finding about the customer")
    return 0


# ── status ──────────────────────────────────────────────────────────────────────────────────

def cmd_status(a, graph: dict, schema: dict) -> int:
    d = a.dir
    receipts = load_receipts(d)
    effective, _ = split_supersession(receipts)
    access = load_access(a.access or (d / "access.yml"))
    res = resolve(graph, access, {"yes": True, "no": False, "unknown": None}[a.multi_agent])

    def seen(kind: str) -> dict[str, str]:
        return {r["subject"]: r.get("state", "recorded") for r in effective if r.get("kind") == kind}

    q_seen, n_seen = seen("question"), seen("node")
    lines = []
    for label, rows, got in (("nodes", res["nodes"], n_seen), ("questions", res["questions"], q_seen)):
        reach = [r["id"] for r in rows if r["state"] in ("reachable", "degraded")]
        frontier = [i for i in reach if i not in got]
        lines.append(f"{label}: {len(got)}/{len(rows)} receipted, {len(frontier)} reachable and open")
        if frontier:
            lines.append(f"  next: {' '.join(frontier[:12])}" + (" …" if len(frontier) > 12 else ""))

    unreachable = [r for r in res["nodes"] + res["questions"] if r["state"] == "blocked"]
    unrecorded = [r["id"] for r in unreachable if r["id"] not in q_seen and r["id"] not in n_seen]

    # Divergence, reported and not judged: a state recorded on something access could not reach.
    # This is the failure the whole instrument exists to prevent, and it is now computable.
    blocked = {r["id"] for r in res["nodes"] + res["questions"] if r["state"] == "blocked"}
    overclaimed = [(i, s) for i, s in {**q_seen, **n_seen}.items()
                   if i in blocked and s not in ("unknown", "not_applicable", "unreachable")]

    print(f"access: {', '.join(res['access_granted']) or 'NOTHING'}")
    for l in lines:
        print(l)
    if unrecorded:
        print(f"unreachable and not yet recorded as such: {' '.join(sorted(unrecorded, key=_sort_key)[:12])}")
    if overclaimed:
        print("\n── divergence (reported, not judged) ──")
        for i, s in overclaimed:
            print(f"  {i} recorded `{s}` but access for it is missing. If that came from a "
                  "substitute, say which in the receipt; if not, it is a claim about your access.")
    fy = d / "findings.yml"
    if receipts and fy.exists():
        gen = (load_yaml(fy) or {}).get("generated_at", "")
        newest = max((r.get("at", "") for r in receipts), default="")
        if newest > gen:
            print("\nfindings.yml is older than the newest receipt — run `lens_plan.py findings`")
    return 0
