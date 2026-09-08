---
name: new-agent
description: Scaffold a runnable Strands Agents SDK project for Amazon Bedrock AgentCore from the plugin's tested template. Use when the user asks to create, initialize, or scaffold a new Strands or AgentCore agent project.
---

# Scaffold a new Strands and AgentCore project

Use the shared Claude Code procedure so every host integration stays aligned:

1. Read [`../../commands/new-agent.md`](../../commands/new-agent.md) completely.
2. Follow it with the Codex adaptations below.

## Codex adaptations

- Treat the user's request after `$new-agent` as the procedure's `$ARGUMENTS`.
- Ignore the command file's Claude-specific `argument-hint` and `allowed-tools` frontmatter.
- Resolve the plugin root as the directory two levels above this `SKILL.md`. Wherever the
  procedure uses `${CLAUDE_PLUGIN_ROOT}`, substitute that absolute plugin-root path.
- A reference to `build-agents-on-aws:strands-agent-design` means the sibling
  `$strands-agent-design` skill in Codex. Likewise,
  `build-agents-on-aws:deploy-on-agentcore` means `$deploy-on-agentcore`.
- Preserve the procedure's dry-run-first and no-overwrite safeguards.

## Kiro CLI adaptations

- Kiro CLI has no plugin-supplied slash commands — this skill is reached from a natural-language
  request ("scaffold a new Strands agent in ./my-agent"). Treat the target directory named in
  that request as the procedure's `$ARGUMENTS`; if none is named, ask for one.
- Ignore the command file's Claude-specific `argument-hint` and `allowed-tools` frontmatter —
  Kiro's own agent config (`tools`, `allowedTools`) governs which tools exist and which run
  without prompting.
- Resolve the plugin root as the directory two levels above this `SKILL.md`. Wherever the
  procedure uses `${CLAUDE_PLUGIN_ROOT}`, substitute that absolute plugin-root path.
- A reference to `build-agents-on-aws:strands-agent-design` means the sibling
  `strands-agent-design` skill, already loaded as a resource — read its `SKILL.md` and follow it
  in place. Likewise for `build-agents-on-aws:deploy-on-agentcore`.
- Run the `scaffold.py` invocations with the `shell` tool exactly as written in the procedure.
- Preserve the procedure's dry-run-first and no-overwrite safeguards.
