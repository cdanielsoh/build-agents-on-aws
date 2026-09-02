# Strands agent on Bedrock AgentCore Runtime

A runnable starting point for a production agent: `Agent` assembled by an overridable
builder, deployed as a container on AgentCore Runtime, tools reached through an MCP
Gateway, and a four-layer eval suite.

Everything here is a placeholder customer-support agent. Work the checklist below to
make it yours.

## Layout

```
agent/
  app.py                 BedrockAgentCoreApp entrypoint, singleton session
  core/
    config.py            frozen config from env + SSM
    builder.py           SessionBuilder — one _build_* hook per concern
    conversation.py      context-pressure policy
    session.py           thin runtime container (agent + shared headers)
  tools/                 local @tool functions
  meta_tooling/          optional schema-level progressive disclosure
  prompts/
    system.md            the system prompt (edit this, not the .py)
    system.py            loader
evals/
  conftest.py            EvalBuilder + fixtures + data loaders
  generate.py            case generation from scenarios
  test_output.py         response quality
  test_trajectory.py     tool-call sequence
  test_traces.py         OTEL span quality
  test_simulation.py     multi-turn behaviour
  chats/ rubrics/ personas/ scenarios/    fixtures — replace these
Dockerfile               ARM64 runtime image with ADOT instrumentation
pyproject.toml           pinned dependencies
```

There is deliberately no `agentcore.json` here — the `agentcore` CLI owns that file and
writes it for you (see Deploy below). Hand-writing a partial one fails `agentcore validate`.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

## Run locally

```bash
export AWS_REGION=us-west-2
export MCP_SSM_PREFIX=/mcp/endpoints/my-gateway   # or unset GATEWAY_URL usage in config.py
python agent/app.py
```

## Run the evals

Evals call a real model and cost money. Start with one file:

```bash
pytest evals/test_output.py -v -s
```

## Checklist

- [ ] `agent/prompts/system.md` — write the real system prompt
- [ ] `agent/tools/` — replace `example_tool.py`; register in `tools/__init__.py`
- [ ] `agent/core/config.py` — set `MCP_SSM_PREFIX`, model, region defaults
- [ ] `evals/chats/*.json` — real cases with `expected_trajectory`
- [ ] `evals/rubrics/output.yaml` — grade what actually matters for your domain
- [ ] `evals/personas/*.yaml` — the users you expect, including difficult ones
- [ ] Decide on `meta_tooling/` — delete it unless you have 15+ tools in one domain

## Deploy

The `agentcore` CLI generates and owns `agentcore/` config, then deploys it via CDK.

```bash
npm install -g @aws/agentcore
agentcore create --framework Strands --build Container --language Python
agentcore add gateway --name MyGateway --runtimes MyAgent
agentcore validate
agentcore deploy -y
```

Once `agentcore.json` exists, declarative resources go in it — including
`policyEngines[]` for Cedar authorization on Gateway tool calls. The
`build-agents-on-aws:deploy-on-agentcore` skill covers the Gateway, Policy, Identity
(2LO/3LO), and observability wiring in depth.
