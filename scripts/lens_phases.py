"""The /plan subcommands: phases, manifest.

The plan's own graph. `phases` resolves plan-graph.yaml against the record and prints
executable / degraded / blocked / not_applicable; `manifest` generates both MANIFEST.md indexes
from items.yml and the plan_item receipts.

Both exist for the same reason as their assessment counterparts: the preconditions and the two
indexes were prose, and prose cannot enforce a precondition or derive a count.
"""
from __future__ import annotations

import pathlib
import textwrap

import yaml

from lens_core import (REFS, err, load_access, load_receipts, load_schema, load_yaml, now,
                       split_supersession)


# ── phases ──────────────────────────────────────────────────────────────────────────────────
#
# The plan's preconditions, computed. Every one of these was a bold warning inside 450 lines of
# prose, which is the arrangement we already know does not hold: the assessment side lost steps
# exactly this way before the check graph existed.
#
# Each predicate returns True, False, or None for unknown. The three are genuinely different and
# collapsing unknown into False is the specific error that turns "we could not check" into a claim
# about the customer — the same conflation that made `unreachable` read as `absent`.

PLAN_GRAPH_PATH = REFS / "plan-graph.yaml"
GATE2_FIELDS = ("cpu_seconds_per_turn", "wall_seconds_per_turn", "peak_memory_gb",
                "turns_per_conversation")


def load_plan_graph() -> dict:
    return yaml.safe_load(PLAN_GRAPH_PATH.read_text())


def _tri(v):
    """access.yml carries true/false/unknown; unknown must not read as false."""
    return None if v in (None, "unknown", "") else bool(v)


def compute_predicates(d: pathlib.Path, access: dict) -> dict[str, bool | None]:
    decisions_doc = load_yaml(d / "decisions.yml") or {}
    decisions = decisions_doc.get("decisions") or []
    effective, _ = split_supersession(load_receipts(d))
    # load_access already unwraps the `access:` block, so this is the flat mapping. Reaching for
    # access["access"] here returned nothing and read every survey answer as unknown — which
    # degraded four phases on a record that had answered all of them.
    acc = access or {}

    def latest_of(kind: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in effective:
            if r.get("kind") == kind:
                out[r["subject"]] = r
        return out

    q, gates = latest_of("question"), latest_of("gate")
    meas, topo = latest_of("measurement"), latest_of("topology")

    def q_present(qid: str):
        r = q.get(qid)
        if r is None or r.get("state") == "unknown":
            return None
        return r["state"] in ("present", "platform_provides")

    lift = gates.get("liftable_unit")
    mp = topo.get("mirroring_point")
    schema_gates = set(load_schema()["gate_ids"])

    return {
        "record_decided": bool(decisions_doc.get("decided_with")),
        "has_proceed": any(x.get("choice") == "proceed" for x in decisions),
        "gates_evaluated": bool(gates) and schema_gates <= set(gates)
                           and not any(g.get("state") == "unknown" for g in gates.values()),
        "liftable": None if lift is None else lift.get("state") != "not_a_liftable_unit",
        "build_reproducible": q_present("AGENTSUS02"),
        "deployment_exists": _tri(acc.get("deployment_exists")),
        "measurable": all(f in meas for f in GATE2_FIELDS),
        "golden_set_exists": q_present("AGENTOPS06"),
        "tool_inventory_known": None if "AGENTOPS04" not in q
                               else q["AGENTOPS04"].get("state") != "unknown",
        "mirroring_point": None if mp is None or mp.get("value") == "unknown"
                           else mp.get("value") != "none",
        "can_invoke": _tri(acc.get("invocation_permitted")),
        "can_load": _tri(acc.get("load_generation_permitted")),
        "owner_named": _tri(acc.get("product_owner_reachable")),
        "single_component": None if "detected" not in topo
                            else topo["detected"].get("value") == "single_turn",
    }


def resolve_phases(pg: dict, preds: dict, outcome: str | None) -> dict[str, dict]:
    """Fixed point over `after`, so a phase whose predecessor is blocked cannot read executable."""
    state: dict[str, dict] = {}
    phases = pg["phases"]
    for _ in range(len(phases) + 1):
        for pid, spec in phases.items():
            allowed = (spec.get("only_if") or {}).get("outcome")
            if allowed and outcome and outcome not in allowed:
                state[pid] = {"state": "not_applicable",
                              "why": f"outcome is `{outcome}`, this phase is for {'/'.join(allowed)}"}
                continue
            if allowed and not outcome:
                state[pid] = {"state": "degraded", "why": "decisions.yml names no outcome, so the "
                                                          "track is a guess"}
                continue
            false_needs = [n for n in spec.get("needs") or [] if preds.get(n) is False]
            unknown_needs = [n for n in spec.get("needs") or [] if preds.get(n) is None]
            blocked_after = [p for p in spec.get("after") or []
                             if state.get(p, {}).get("state") == "blocked"]
            if false_needs:
                state[pid] = {"state": "blocked", "why": "false: " + ", ".join(false_needs),
                              "unmet": " ".join((spec.get("unmet") or "").split())}
            elif blocked_after:
                state[pid] = {"state": "blocked",
                              "why": f"depends on {', '.join(blocked_after)}, which is blocked"}
            elif unknown_needs:
                # No `unmet` here on purpose. `unmet` explains a predicate that is FALSE, and
                # printing it against an unknown told the reader the wrong story — a phase degraded
                # because nobody answered S3 was shown the text about unevaluated gates.
                state[pid] = {"state": "degraded", "why": "unknown: " + ", ".join(unknown_needs),
                              "assume": "state each unknown as a named assumption with the "
                                        "consequence if it is wrong, and say who can settle it"}
            else:
                soft = [n for n in spec.get("degraded_if") or [] if preds.get(n) is not True]
                state[pid] = ({"state": "degraded", "why": "assumption: " + ", ".join(soft)}
                              if soft else {"state": "executable", "why": ""})
    return state


def cmd_phases(a, graph: dict, schema: dict) -> int:
    d = a.dir
    pg = load_plan_graph()
    access = load_access(pathlib.Path(a.access) if a.access else d / "access.yml")
    preds = compute_predicates(d, access)
    outcome = a.outcome or (load_yaml(d / "decisions.yml") or {}).get("outcome")
    state = resolve_phases(pg, preds, outcome)

    if a.format == "yaml":
        print(yaml.safe_dump({"outcome": outcome, "predicates": preds, "phases": state},
                             sort_keys=False, default_flow_style=False))
        return 0

    print(f"── plan phases ── outcome: {outcome or 'UNSET'}")
    print("\npredicates (unknown is not false — it degrades a phase, it does not block it):")
    for k, v in preds.items():
        print(f"  {'true ' if v is True else 'FALSE' if v is False else '  ?  '}  {k}")
    order = {"executable": 0, "degraded": 1, "blocked": 2, "not_applicable": 3}
    print()
    for pid, spec in pg["phases"].items():
        st = state[pid]
        print(f"  {pid:3} {st['state']:15} {spec['title']}")
        if st.get("why"):
            print(f"        └─ {st['why']}")
        for extra in ("unmet", "assume"):
            if st.get(extra):
                print(textwrap.fill(st[extra], 94, initial_indent=" " * 11,
                                    subsequent_indent=" " * 11))
    counts: dict[str, int] = {}
    for st in state.values():
        counts[st["state"]] = counts.get(st["state"], 0) + 1
    print("\n" + ", ".join(f"{k} {counts[k]}" for k in sorted(counts, key=lambda x: order.get(x, 9))))
    print("\nWrite plan items only for phases that are `executable` or `degraded`. A `degraded` phase")
    print("is planned with its unknown stated as a named assumption and a consequence if it is")
    print("wrong — not silently, which is what prose produced. `blocked` phases go in the plan as")
    print("what is not available and why. Nothing here is a finding about the customer.")
    return 0


# ── manifest ────────────────────────────────────────────────────────────────────────────────

VERIFIED_LABEL = {
    "built": "built, ran, passed",
    "failing": "**ran and FAILED**",
    "unverifiable": "generated, not runnable here",
    "planned": "not executed by design",
}


def cmd_manifest(a, graph: dict, schema: dict) -> int:
    """MANIFEST.md, generated. The forward index catches invention; the reverse index catches
    omission, which is the failure that actually happened — one plan skipped a severity: high
    finding entirely while claiming thirteen closed. Nothing in its artifact list was wrong; the
    list simply ended. Assembling this by hand is how that survives, so it is generated."""
    d = a.dir
    out = pathlib.Path(a.out) if a.out else d / "plan"
    plan_doc = load_yaml(out / "items.yml") or load_yaml(d / "plan" / "items.yml") \
        or load_yaml(d / "items.yml") or {}
    items = plan_doc.get("items", [])
    if not items:
        return err(f"no items.yml under {out} — /plan writes it before this runs. A manifest with "
                   "no items would report full coverage of nothing")

    decisions = {x["id"]: x for x in (load_yaml(d / "decisions.yml") or {}).get("decisions", [])}
    suggestions = {s["id"]: s for s in (load_yaml(d / "suggestions.yml") or {}).get("suggestions", [])}
    effective, _ = split_supersession(load_receipts(d))
    latest_pi: dict[str, dict] = {}
    for r in effective:
        if r.get("kind") == "plan_item":
            latest_pi[r["subject"]] = r

    by_decision: dict[str, list[dict]] = {}
    for it in items:
        by_decision.setdefault(it.get("decision"), []).append(it)

    L = [f"# Plan manifest — generated by `lens_plan.py manifest` at {now()}", "",
         "Do not hand-edit. The verified column is projected from `plan_item` receipts, so a row",
         "cannot claim verification that was never recorded.", ""]

    L += ["## Artifact → decision (catches invention)", "",
          "| Artifact | Item | Decision | Closes | Verified |", "|---|---|---|---|---|"]
    for it in items:
        rec = latest_pi.get(it.get("id")) or {}
        arts = rec.get("artifacts") or it.get("artifacts") or ["—"]
        sug = suggestions.get((decisions.get(it.get("decision")) or {}).get("suggestion")) or {}
        closes = ", ".join(sug.get("closes") or []) or "—"
        label = VERIFIED_LABEL.get(rec.get("state"), "**no receipt**")
        if rec.get("state") == "built" and rec.get("mutation_tested") is not True:
            label += " (not mutation-tested)"
        for n, art in enumerate(arts):
            L.append(f"| `{art}` | {it.get('id') if n == 0 else ''} "
                     f"| {it.get('decision') if n == 0 else ''} | {closes if n == 0 else ''} "
                     f"| {label if n == 0 else ''} |")

    L += ["", "## Decision → artifact (catches omission)", "",
          "Every `proceed` decision appears here. `not addressed` is a fine answer — writing it",
          "turns a gap in the plan into something the customer can overrule.", "",
          "| Decision | Choice | Plan items | Or why not |", "|---|---|---|---|"]
    n_proceed = n_addressed = 0
    for did, x in sorted(decisions.items()):
        if x.get("choice") != "proceed":
            continue
        n_proceed += 1
        got = by_decision.get(did) or []
        if got:
            n_addressed += 1
        L.append(f"| {did} | proceed | {', '.join(str(i.get('id')) for i in got) or '—'} "
                 f"| {'' if got else '**NOT ADDRESSED — state the reason here**'} |")

    other = [(k, v) for k, v in sorted(decisions.items()) if v.get("choice") != "proceed"]
    if other:
        L += ["", "## Declined and deferred (so the plan does not read as if nothing was refused)",
              "", "| Decision | Choice | In their words |", "|---|---|---|"]
        for did, x in other:
            why = x.get("reason") or x.get("revisit_when") or x.get("rationale") or "—"
            L.append(f"| {did} | {x.get('choice')} | {str(why).replace(chr(10), ' ')} |")

    counts: dict[str, int] = {}
    for it in items:
        counts[(latest_pi.get(it.get("id")) or {}).get("state") or "no receipt"] = \
            counts.get((latest_pi.get(it.get("id")) or {}).get("state") or "no receipt", 0) + 1
    L += ["", "## Counts, derived — not asserted", "",
          f"- plan items: **{len(items)}**",
          f"- `proceed` decisions: **{n_proceed}**, with at least one plan item: **{n_addressed}**",
          "- item states: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())), "",
          "Any claim in `plan.md` about how many findings were closed must be derived from the",
          "second table above, not from the record's severity totals."]

    out.mkdir(parents=True, exist_ok=True)
    target = out / "scaffold" / "MANIFEST.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(L) + "\n")
    print(f"wrote {target}")
    print(f"  {len(items)} items, {n_addressed}/{n_proceed} proceed decisions addressed, "
          + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if n_addressed < n_proceed:
        print("  fill in the `Or why not` column before handing this over — a blank there is the "
              "omission this table exists to surface")
    return 0
