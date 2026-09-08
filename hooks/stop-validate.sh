#!/usr/bin/env bash
# Referential integrity, made non-skippable. That is the whole reason it lives in a script.
#
# `validate` exits 1 on referential errors ONLY — a dangling receipt id, a suggestion citing a
# finding that does not exist, a plan item with no decision. Never on coverage. On Stop, exit 2
# does not fail the turn: it prevents Claude from stopping and hands back `reason` as the next
# instruction, so a dangling id gets fixed rather than reported after the fact.
#
# Two things this must never do:
#   - block on an incomplete walk. `unreachable` and `not_permitted` are recordable states, and a
#     hook that punishes incompleteness teaches assessors to fabricate receipts to get past it.
#   - loop. If we already blocked once this turn, say the errors and let the turn end.
set -u
command -v python3 >/dev/null 2>&1 || exit 0

INPUT="$(cat 2>/dev/null || true)"
# Prefer the cwd the hook was given: it follows Claude into worktrees and after a `cd`.
CWD="$(printf '%s' "$INPUT" | python3 -c \
  'import json,sys; print(json.load(sys.stdin).get("cwd") or "")' 2>/dev/null || true)"
[ -n "${CWD:-}" ] && [ -d "$CWD" ] && cd "$CWD" 2>/dev/null || true
[ -d .agentcore-migration ] || exit 0

ROOT="${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}"
if [ -z "$ROOT" ] || [ ! -f "$ROOT/scripts/lens_plan.py" ]; then
  ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)"
fi
[ -f "$ROOT/scripts/lens_plan.py" ] || exit 0

OUT="$(python3 "$ROOT/scripts/lens_plan.py" validate 2>&1)"; RC=$?
[ "$RC" -eq 0 ] && exit 0            # clean, or notes only. Notes are not failures.

# Loop guard: a referential error we cannot fix must not hold the session open.
if printf '%s' "$INPUT" | python3 -c \
   'import json,sys; sys.exit(0 if json.load(sys.stdin).get("stop_hook_active") else 1)' \
   2>/dev/null; then
  printf 'lens_plan.py validate still reports referential errors:\n\n%s\n' "$OUT" >&2
  exit 0
fi

printf '%s\n\nThese are referential errors in .agentcore-migration/, not coverage gaps — fix them before finishing. Coverage is never an error here: `unreachable` and `not_permitted` are recordable states.\n' "$OUT" >&2
exit 2
