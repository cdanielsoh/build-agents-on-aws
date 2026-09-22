# Hooks

Three hooks, and all three no-op silently unless the working directory holds an
`.agentcore-migration/` directory. Plugin hooks fire for everyone who installs the plugin, in
every directory — a hook that errors in an unrelated repo is a bug affecting every user, and
the kind that gets a plugin uninstalled rather than reported.

A hook is the only mechanism in this plugin that a model under context pressure cannot skip,
which makes it the right home for exactly the things that kept getting skipped. It is also why
none of them blocks on an INCOMPLETE walk: `unreachable` and `not_permitted` are recordable
states, and a hook that punishes an incomplete walk teaches assessors to fabricate receipts.

| Hook | Script | Fires on |
|---|---|---|
| SessionStart | `session-start.sh` | startup, resume, clear |
| PreToolUse | `consent-gate.py` | Bash |
| Stop | `stop-validate.sh` | every stop |

Note: `hooks.json` accepts only the `hooks` key — extra keys such as `_comment` are rejected
with a warning at load time, which is why this rationale lives here instead.
