#!/usr/bin/env bash
# A resumed assessment sees the frontier instead of restarting from nothing.
#
# Context exhaustion mid-assessment used to mean starting over, because nothing recorded where the
# walk had got to. `status` answers that from receipts. On SessionStart, stdout is added to
# context, so this is the one place the frontier arrives without anyone remembering to ask.
#
# Silent and exit 0 outside an assessment: this plugin's hooks fire in every directory of every
# user who installs it.
set -u
command -v python3 >/dev/null 2>&1 || exit 0

# Prefer the cwd the hook was given: it follows Claude into worktrees and after a `cd`, which the
# process cwd does not.
CWD="$(cat 2>/dev/null | python3 -c \
  'import json,sys;
d=json.load(sys.stdin); print(d.get("cwd") or "")' 2>/dev/null || true)"
[ -n "${CWD:-}" ] && [ -d "$CWD" ] && cd "$CWD" 2>/dev/null || true
[ -d .agentcore-migration ] || exit 0

ROOT="${CLAUDE_PLUGIN_ROOT:-${CODEX_PLUGIN_ROOT:-}}"
if [ -z "$ROOT" ] || [ ! -f "$ROOT/scripts/lens_plan.py" ]; then
  ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)"
fi
[ -f "$ROOT/scripts/lens_plan.py" ] || exit 0

OUT="$(python3 "$ROOT/scripts/lens_plan.py" status 2>/dev/null)" || exit 0
[ -n "$OUT" ] || exit 0
printf 'An AgentCore migration assessment is in progress here. Where the walk stands:\n\n%s\n' "$OUT"
exit 0
