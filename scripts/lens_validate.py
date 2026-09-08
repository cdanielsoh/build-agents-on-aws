"""Referential integrity across every artifact, including the plan.

Its own module because it is the one subcommand that spans both commands: it checks the
assessment's receipts and findings AND the plan's items and verification. Putting it with either
side would have made that side's module the owner of the other's invariants.

The rule that shapes all of it: **referential errors fail, coverage never does.** An id that points
at nothing is a broken record; an unanswered question is an honest one. A checker that fails on
incompleteness teaches people to fabricate receipts to get past it, which is the opposite of what
the log is for.
"""
from __future__ import annotations

import pathlib
import datetime as _dt

from lens_core import (load_receipts, load_yaml, split_supersession, validate_receipt, _sort_key)


# ── validate ────────────────────────────────────────────────────────────────────────────────

def cmd_validate(a, graph: dict, schema: dict) -> int:
    d = a.dir
    errors: list[str] = []
    notes: list[str] = []

    receipts = load_receipts(d)
    effective, superseded_by = split_supersession(receipts)
    ids = {r.get("id") for r in receipts}
    findings_doc = load_yaml(d / "findings.yml") or {}
    findings = {f["id"]: f for f in findings_doc.get("findings", [])}
    suggestions_doc = load_yaml(d / "suggestions.yml") or {}
    suggestions = {s["id"]: s for s in suggestions_doc.get("suggestions", [])}
    decisions_doc = load_yaml(d / "decisions.yml") or {}
    decisions = {x["id"]: x for x in decisions_doc.get("decisions", [])}
    # /plan writes to .agentcore-migration/plan/ by default, but --out can put it elsewhere; accept
    # a top-level items.yml too rather than silently checking nothing.
    plan_doc = (load_yaml(d / "plan" / "items.yml") or load_yaml(d / "items.yml") or {})
    assessment = load_yaml(d / "assessment.yml") or {}

    # ── referential errors. These, and only these, fail. ──
    for r in receipts:
        errs = validate_receipt(r, graph, schema, ids - {r.get("id")})
        for e in errs:
            errors.append(f"receipts.jsonl {r.get('id')}: {e}")

    for f in findings.values():
        if not f.get("receipts"):
            errors.append(f"findings.yml {f['id']}: no receipt. findings.yml is generated — "
                          "if this was hand-edited, the audit trail is broken. Re-run `findings`")

    for s in suggestions.values():
        for field in schema["suggestion_required_fields"]:
            if field not in s or s[field] in (None, [], ""):
                if field in ("closes", "grounded_in"):
                    errors.append(f"suggestions.yml {s.get('id')}: `{field}` is empty. "
                                  "Grounding is structural, not optional")
                else:
                    notes.append(f"suggestions.yml {s.get('id')}: missing `{field}` — incomplete. "
                                 "A suggestion without it is a pitch with citations")
        for fid in s.get("closes") or []:
            if fid not in findings:
                near = [k for k in findings if k.split("/")[0] == str(fid).split("/")[0]]
                errors.append(f"suggestions.yml {s.get('id')}: closes `{fid}`, which is not a finding id"
                              + (f" — did you mean {', '.join(near)}?" if near else ""))
        for rid in s.get("grounded_in") or []:
            if rid not in ids:
                errors.append(f"suggestions.yml {s.get('id')}: grounded_in `{rid}`, not a receipt id")
        if s.get("confidence") and s["confidence"] not in schema["suggestion_confidence"]:
            errors.append(f"suggestions.yml {s.get('id')}: confidence `{s['confidence']}` is not "
                          + "/".join(schema["suggestion_confidence"]))
        alt = s.get("agentcore_alternative")
        if alt is not None:
            if not isinstance(alt, dict):
                errors.append(f"suggestions.yml {s.get('id')}: agentcore_alternative must be a "
                              "mapping with capability/closes/instead_of")
            else:
                cap = alt.get("capability")
                if cap not in schema["closed_by_families"]["agentcore"]:
                    errors.append(f"suggestions.yml {s.get('id')}: agentcore_alternative capability "
                                  f"`{cap}` is not an AgentCore capability — it must be one of "
                                  + ", ".join(schema["closed_by_families"]["agentcore"]))
                if alt.get("closes") not in ("fully", "partially"):
                    errors.append(f"suggestions.yml {s.get('id')}: agentcore_alternative needs "
                                  "`closes: fully|partially`")
                if alt.get("closes") == "partially" and not alt.get("note"):
                    errors.append(f"suggestions.yml {s.get('id')}: `closes: partially` needs a "
                                  "`note` saying what is left — a partial answer presented as a "
                                  "whole one is how a migration story fails a security review")
                if "requires_runtime_move" not in alt:
                    errors.append(f"suggestions.yml {s.get('id')}: agentcore_alternative needs "
                                  "`requires_runtime_move`. Only `runtime` actually requires moving "
                                  "the workload — the rest are callable from an EKS pod, and "
                                  "omitting this turns an adoptable capability into an implied "
                                  "migration")
                for side in ("eks_build", "eks_you_own", "agentcore_config", "agentcore_you_own"):
                    if not alt.get(side):
                        notes.append(f"suggestions.yml {s.get('id')}: agentcore_alternative has no "
                                     f"`{side}`. Survey 2 asks where to close this gap, and that is "
                                     "unanswerable without both sides — what they build versus "
                                     "configure, and what they still operate either way")

    for x in decisions.values():
        choice = x.get("choice")
        rules = schema["decision_choices"]
        if choice not in rules:
            errors.append(f"decisions.yml {x.get('id')}: choice `{choice}` is not one of "
                          + ", ".join(rules) + ". There is deliberately no `undecided` — "
                          "an undecided suggestion has no entry")
            continue
        for field in (rules[choice] or {}).get("requires", []):
            if not x.get(field):
                errors.append(f"decisions.yml {x['id']}: choice `{choice}` requires `{field}`. "
                              "Captured now, not reconstructed later — this is the direction a "
                              "human revisiting actually asks about")
        if x.get("suggestion") not in suggestions:
            errors.append(f"decisions.yml {x.get('id')}: suggestion `{x.get('suggestion')}` does not exist")
        for fid in x.get("residual_gaps") or []:
            if fid not in findings:
                errors.append(f"decisions.yml {x['id']}: residual_gaps `{fid}` is not a finding id")
        # `via` — survey 2's second question, and the thing that decides what /plan may write.
        via = x.get("via")
        if via is not None:
            rules_v = schema["decision_via"]
            if via not in rules_v:
                errors.append(f"decisions.yml {x['id']}: via `{via}` is not one of "
                              + ", ".join(rules_v))
            elif (rules_v[via] or {}).get("requires_alternative"):
                s = suggestions.get(x.get("suggestion")) or {}
                own = s.get("closed_by") in schema["closed_by_families"]["agentcore"]
                if not s.get("agentcore_alternative") and not own:
                    errors.append(
                        f"decisions.yml {x['id']}: via `{via}` but {x.get('suggestion')} names no "
                        "agentcore_alternative and its closed_by is not an AgentCore capability. "
                        "Choosing the platform path for a gap the platform does not close is the "
                        "specific claim this field exists to prevent — the record's own "
                        "does_not_fix usually already says a runtime move leaves it untouched")

    if decisions_doc:
        out = decisions_doc.get("outcome")
        if out and out not in schema["outcomes"]:
            errors.append(f"decisions.yml: outcome `{out}` is not one of " + ", ".join(schema["outcomes"]))
        if not decisions_doc.get("decided_with"):
            notes.append("decisions.yml has no `decided_with` — nobody is recorded as having chosen, "
                         "so every entry is the assessor's own verdict wearing a decision's clothes")

    item_ids = set()
    for item in plan_doc.get("items", []):
        item_ids.add(item.get("id"))
        did = item.get("decision")
        if not did:
            errors.append(f"plan/items.yml {item.get('id')}: no decision id. A plan item with no "
                          "decision quietly reintroduces something the customer declined")
        elif did not in decisions:
            errors.append(f"plan/items.yml {item.get('id')}: decision `{did}` does not exist")
        elif decisions[did].get("choice") != "proceed":
            errors.append(f"plan/items.yml {item.get('id')}: decision {did} is "
                          f"`{decisions[did].get('choice')}`, not `proceed`")
        # Verification is not storable here. items.yml is written by the same pass that would claim
        # the verification, so a status field in it is a self-assertion with nothing to check it —
        # the defect that deleting the old `plan:` block removed from the assessment side.
        for banned in ("verified", "built", "status", "state", "tested", "passing"):
            if banned in item:
                errors.append(f"plan/items.yml {item.get('id')}: has `{banned}`. Verification lives "
                              "in a plan_item receipt, not here — this field can only ever restate "
                              "what the writer hoped. Use `record --plan-item`")
        # An item whose decision is `via: eks` must carry no artifacts. We have read-only access to
        # their cluster and do not know their pipeline, so a manifest we invented cannot be applied,
        # tested, or trusted. Observed: a plan that wrote a NetworkPolicy and a Deployment command
        # override for a service deployed by a Jenkinsfile it never read. State the change and its
        # acceptance criteria instead; the customer owns the edit.
        if did in decisions and decisions[did].get("via") == "eks_build" and item.get("artifacts"):
            errors.append(f"plan/items.yml {item.get('id')}: decision {did} is `via: eks_build`, so this "
                          "item must carry no artifacts — it names "
                          f"{', '.join(map(str, item['artifacts']))}. Write acceptance criteria in "
                          "plan.md instead: we cannot run a change to their cluster, and a patch we "
                          "cannot test implies a confidence we do not have")

    # `via: eks` cannot reach `built` or `failing`: both assert we executed something, and what we
    # would have executed is a change to a service we can only read. This is the rule that makes the
    # untestable-artifact case structurally impossible rather than merely discouraged.
    item_via = {}
    for item in plan_doc.get("items", []):
        dec = decisions.get(item.get("decision")) or {}
        item_via[item.get("id")] = dec.get("via")
    for r in effective:
        if r.get("kind") == "plan_item" and item_via.get(r["subject"]) == "eks_build" \
                and r.get("state") in ("built", "failing"):
            errors.append(f"receipts.jsonl {r['id']}: plan_item {r['subject']} is `{r['state']}`, but "
                          "its decision is `via: eks_build`. Running their cluster change is not something "
                          "we did — `planned` is the honest state, and the customer reports the result")

    for r in effective:
        if r.get("kind") != "plan_item":
            continue
        if plan_doc and r["subject"] not in item_ids:
            near = [i for i in sorted(item_ids) if str(i)[:3] == str(r["subject"])[:3]]
            errors.append(f"receipts.jsonl {r['id']}: plan_item `{r['subject']}` is in no "
                          "plan/items.yml entry"
                          + (f" — did you mean {', '.join(map(str, near[:3]))}?" if near else ""))

    # ── reports. Never fail on these. `unreachable` and `not_permitted` are recordable states,
    # and a hook that punishes an incomplete walk teaches assessors to fabricate receipts. ──
    needs_action = {k: f for k, f in findings.items()
                    if f.get("state") in ("absent", "present_but_ineffective",
                                          "available_unconfigured")}
    covered = {fid for s in suggestions.values() for fid in (s.get("closes") or [])}
    for k in needs_action:
        if k not in covered:
            notes.append(f"uncovered: {k} is `{needs_action[k]['state']}` and appears in no "
                         "suggestion. Deliberate is fine — say so in assessment.yml's triage")
    seen_twice = {}
    for s in suggestions.values():
        for fid in s.get("closes") or []:
            seen_twice.setdefault(fid, []).append(s.get("id"))
    for fid, sids in seen_twice.items():
        if len(sids) > 1:
            notes.append(f"{fid} is closed by {' and '.join(sids)}. Not necessarily wrong — but if "
                         "one change closes it, they are one suggestion (grouping rule 2)")
    for sid in suggestions:
        if not any(x.get("suggestion") == sid for x in decisions.values()):
            notes.append(f"{sid} has no decision — undecided by absence, which is honest. "
                         "Survey 2 has not covered it")

    # A suggestion the customer must build themselves, with no agentcore_alternative, is ambiguous in
    # exactly the way `absent` versus `unknown` was: either no managed capability closes this gap, or
    # nobody checked. The first is a finding worth stating; the second is an omission that costs the
    # customer a choice they were entitled to. Prompt for it rather than assume — some genuinely have
    # none, which is why this is a note.
    own = set(schema["closed_by_families"]["customers_own"])
    for sid, s in sorted(suggestions.items()):
        if s.get("closed_by") in own and "agentcore_alternative" not in s:
            notes.append(f"{sid} is closed by `{s['closed_by']}` and names no agentcore_alternative. "
                         "If no managed capability closes it, say so — otherwise survey 2 asks "
                         "`where` with only one column on the page")

    # Decisions taken before the suggestions they rest on were last edited. Not a referential error —
    # both files are internally consistent — but the choice was made against different text, which no
    # timestamp comparison inside a single file can reveal.
    dpath, spath = d / "decisions.yml", d / "suggestions.yml"
    if dpath.exists() and spath.exists() and decisions \
            and dpath.stat().st_mtime < spath.stat().st_mtime:
        notes.append("decisions.yml is older than suggestions.yml — the customer chose against text "
                     "that has since changed. Re-confirm any decision whose suggestion was edited, "
                     "or the record shows consent to something nobody read")

    # Plan-side coverage. Notes, not errors: the same reasoning as the assessment walk — punishing
    # an incomplete record teaches people to write receipts for work they did not do, and an item
    # nobody could run is an honest outcome that `unverifiable` exists to carry.
    pi = {}
    for r in effective:
        if r.get("kind") == "plan_item":
            pi.setdefault(r["subject"], []).append(r)
    for iid in sorted(item_ids, key=lambda x: _sort_key(str(x))):
        if iid not in pi:
            notes.append(f"plan item {iid} has no plan_item receipt — nothing records whether it was "
                         "built, ran, or could not be run. plan.md must not describe it as verified")
    for iid, rs in sorted(pi.items(), key=lambda kv: _sort_key(str(kv[0]))):
        last = rs[-1]
        if last.get("state") == "built" and "mutation_tested" not in last:
            notes.append(f"plan item {iid} is `built` with no mutation_tested. Measured on one "
                         "generated suite, 3 of 6 assertions could not fail — and all 3 passed")
        elif last.get("state") == "built" and last.get("mutation_tested") is False:
            notes.append(f"plan item {iid} is `built` and mutation_tested: false. Passing is not "
                         "evidence until you have broken it once — say so in plan.md at that step")
        if last.get("state") == "failing":
            notes.append(f"plan item {iid} is `failing` ({last.get('verified_by')}). A plan whose "
                         "first concrete action fails is worse than no plan — fix or restate it")

    # A scaffold with no items.yml is a referential error, not a coverage gap. Files were written and
    # nothing says which decision authorised them — the reverse index cannot run, so an artifact
    # invented from nothing is indistinguishable from one the customer asked for. Observed twice on
    # real plans: fourteen generated files, no items.yml, no MANIFEST.md, zero plan_item receipts,
    # and validate exited 0 because it only ever checked items that already existed.
    scaffold_dirs = [p for p in (d / "plan" / "scaffold", d / "scaffold") if p.is_dir()]
    if scaffold_dirs and not plan_doc.get("items"):
        n_files = sum(1 for p in scaffold_dirs[0].rglob("*")
                      if p.is_file() and p.suffix not in (".pyc",))
        errors.append(f"{scaffold_dirs[0].name}/ holds {n_files} generated file(s) but plan/items.yml "
                      "has no items. Nothing records which decision authorised them, so neither "
                      "index can be built and invention cannot be told from instruction")

    # MANIFEST.md was absent entirely on the one real plan produced, along with items.yml — the two
    # files whose only job is catching omission. Absence is not a referential error, so this is a
    # note; but it is the note most worth printing, because nothing else in the record misses it.
    if item_ids:
        man = next((p for p in (d / "plan" / "scaffold" / "MANIFEST.md",
                                d / "scaffold" / "MANIFEST.md") if p.exists()), None)
        if man is None:
            notes.append("plan items exist but no scaffold/MANIFEST.md — run `lens_plan.py "
                         "manifest`. Its reverse index is the only thing that catches a decision "
                         "with no plan item, which is how one plan skipped a severity: high finding "
                         "while claiming thirteen closed")
        elif pi:
            newest_pi = max(r.get("at", "") for rs in pi.values() for r in rs)
            written = _dt.datetime.fromtimestamp(man.stat().st_mtime, _dt.timezone.utc) \
                         .strftime("%Y-%m-%dT%H:%M:%SZ")
            if newest_pi > written:
                notes.append("scaffold/MANIFEST.md is older than the newest plan_item receipt — "
                             "re-run `lens_plan.py manifest` so its verified column matches the log")

    triaged = {t.get("finding") for t in (assessment.get("triage") or [])}
    for k, f in findings.items():
        if f.get("state") == "absent" and k not in triaged:
            notes.append(f"{k} is absent with no severity in assessment.yml's triage")

    if receipts and findings_doc:
        newest = max((r.get("at", "") for r in receipts), default="")
        if newest > findings_doc.get("generated_at", ""):
            msg = "findings.yml is older than the newest receipt — run `lens_plan.py findings`"
            (errors if decisions else notes).append(
                msg + (". Decisions rest on it, so this is not cosmetic" if decisions else ""))

    for x in decisions.values():
        s = suggestions.get(x.get("suggestion")) or {}
        stale = [r for r in (s.get("grounded_in") or []) if r in superseded_by]
        if stale:
            notes.append(f"{x.get('id')} rests on {', '.join(stale)}, since superseded "
                         f"({superseded_by[stale[0]].get('reason')}). Re-check before planning it")

    if effective and not any(r.get("kind") == "context" and r["subject"] == "code_ownership"
                             for r in effective):
        notes.append("no code_ownership receipt. It decides whether a remedy is actionable at all — "
                     "on a third-party platform `customer_code` is not available to recommend")

    for line in errors:
        print(f"ERROR  {line}")
    if errors and notes:
        print()
    for line in notes:
        print(f"note   {line}")
    if not errors and not notes:
        print("validate: clean")
    elif not errors:
        print(f"\nvalidate: {len(notes)} note(s), no referential errors. Coverage is never an error — "
              "`unreachable` and `not_permitted` are recordable states.")
    return 1 if errors else 0
