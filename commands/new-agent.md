---
description: Scaffold a new Strands + AgentCore agent project from the bundled template
argument-hint: "[directory] [--template <name>]"
allowed-tools: Bash(python3:*), Read, Edit, Write, Glob
---

# Scaffold a new agent project

Arguments: `$ARGUMENTS`

## Step 1 — Pick the destination and template

If `$ARGUMENTS` names a directory, use it. If it is empty, ask the user for a
target directory before writing anything — do not guess.

List what is available first:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scaffold.py" --list
```

## Step 2 — Preview, then materialize

Always dry-run first so the user sees the file list:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scaffold.py" <dest> --template <name> --dry-run
```

Then write the files:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/scaffold.py" <dest> --template <name>
```

The script refuses to overwrite existing files. If it exits with code 2, show the
conflicting paths and let the user decide whether to pass `--force` — do not add
`--force` on your own initiative.

Do **not** hand-write these files. The script copies them verbatim; retyping them
introduces drift from the tested template.

## Step 3 — Adapt to the user's domain

The template ships with a placeholder customer-support agent. After copying, read
`TEMPLATE.md` in the destination and work through its checklist with the user:

1. `agent/prompts/system.md` — replace the placeholder system prompt
2. `agent/tools/` — replace `example_tool.py` with real domain tools
3. `agent/core/config.py` — set the SSM prefix, model ID, and region defaults
4. `evals/chats/`, `evals/rubrics/`, `evals/personas/` — replace the example fixtures

Load the `build-agents-on-aws:strands-agent-design` skill for design guidance on
prompts, tools, and topology, and `build-agents-on-aws:deploy-on-agentcore` for
the deployment, Gateway, Policy, and Identity wiring.
