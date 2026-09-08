#!/usr/bin/env python3
"""Make the consent gate real: block agent invocation and load generation unless access.yml says yes.

The failure this prevents actually happened. Invoking the agent was documented for weeks and never
done, then made a mandatory step and performed without asking anyone. A precondition in prose gets
dropped; a precondition in a hook does not.

Matching is heuristic — a `curl` to localhost could be anything — but the failure modes are
asymmetric. A false block is annoying and recoverable: record the consent in access.yml and
proceed. A false pass is merely today's behaviour. Strictly better, and worth saying out loud
rather than implying this is a security boundary. It is not: a `command` hook that times out does
not block, and there are a hundred ways to send an HTTP request that this does not match. It is a
gate against forgetting, not against intent.

No-ops silently — exit 0, no output — anywhere without a `.agentcore-migration/` directory. Plugin
hooks fire for every user in every directory.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

try:
    import yaml
except ImportError:
    yaml = None

# Sending a request to the agent. Not a read: it runs their tools, spends model budget, and
# writes state. Gated on S4.
INVOKE = re.compile(
    r"(?:^|[\s|;&(])(?:curl|wget|httpie|http|xh|grpcurl|wscat|websocat)(?:\s|$)"
    r"|aws\s+bedrock-agentcore\s+invoke-agent-runtime"
    r"|invoke_agent_runtime", re.I)

# Read-only endpoints and public hosts, exempted so the gate does not fire on a health probe or on
# fetching documentation. Keep this list short: every entry is a hole.
INVOKE_EXEMPT = re.compile(
    r"/(?:ping|health|healthz|readyz|livez|metrics)\b"
    r"|docs\.aws\.amazon\.com|docs\.claude\.com|code\.claude\.com"
    r"|github\.com|githubusercontent\.com|pypi\.org|registry\.npmjs\.org", re.I)

# Concurrent load. Same consent class as invocation and more intrusive. Gated on S5.
LOAD = re.compile(
    r"(?:^|[\s|;&(])(?:hey|k6|ab|wrk|wrk2|siege|locust|artillery|vegeta|bombardier|oha)(?:\s|$)"
    r"|xargs\s+-[^\s]*P\s*[2-9]"
    r"|concurrent\.futures|ThreadPoolExecutor|multiprocessing\.Pool"
    r"|asyncio\.gather", re.I)

# The command file says "Read-only throughout." These are the writes that would break it.
MUTATE = re.compile(
    r"kubectl\s+(?:[-\w=./]+\s+)*(?:delete|apply|create|patch|replace|edit|scale|annotate|label"
    r"|cordon|drain|uncordon|taint|rollout|set)\b"
    r"|helm\s+(?:install|upgrade|uninstall|delete|rollback)\b"
    r"|eksctl\s+(?:create|delete|upgrade|scale|drain)\b"
    r"|aws\s+[\w-]+\s+(?:create|delete|update|put|modify|terminate|stop|start|reboot|attach"
    r"|detach|associate|disassociate|tag|untag|enable|disable|register|deregister|publish|send"
    r"|run|execute|import|restore|reset|revoke|authorize)-", re.I)


def deny(reason: str) -> None:
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, sys.stdout)
    sys.exit(0)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0

    cwd = pathlib.Path(payload.get("cwd") or ".")
    d = cwd / ".agentcore-migration"
    if not d.is_dir():
        return 0                        # not an assessment; this hook has no opinion

    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not cmd:
        return 0

    access = {}
    f = d / "access.yml"
    if yaml is not None and f.exists():
        try:
            raw = yaml.safe_load(f.read_text()) or {}
            access = raw.get("access", raw) if isinstance(raw, dict) else {}
        except Exception:
            access = {}

    how = ("Record the answer in .agentcore-migration/access.yml before acting on it — writing the "
           "permission down is what makes it auditable, and `lens_plan.py resolve` reads the same "
           "field to decide which of the 41 questions this unlocks.")

    if MUTATE.search(cmd):
        deny("This assessment is read-only throughout, and this command mutates the customer's "
             "infrastructure. If a write is genuinely required, get it agreed explicitly and say so "
             "in the record — but first check whether a describe/list/get answers the question, "
             "which it usually does. Verify observed state, never exit codes.")

    if LOAD.search(cmd) and access.get("load_generation_permitted") is not True:
        deny("Generating concurrent load against the customer's service needs survey answer S5, and "
             "access.yml does not say yes (`load_generation_permitted: "
             f"{access.get('load_generation_permitted')!r}`). Ask, naming what it will touch and at "
             "what concurrency, and prefer a non-production tenant. " + how +
             " If refused, node I has a substitute: single-level numbers, explicitly labelled "
             "not-production. Record the refusal as a node receipt rather than a thinner record "
             "that looks complete.")

    if INVOKE.search(cmd) and not INVOKE_EXEMPT.search(cmd):
        if access.get("invocation_permitted") is not True:
            deny("Sending a turn through the customer's agent is not a read — it runs their tools, "
                 "spends model budget and writes state. That needs survey answer S4, and access.yml "
                 "does not say yes (`invocation_permitted: "
                 f"{access.get('invocation_permitted')!r}`). Expect the answer to be no: a first "
                 "assessment typically has a repo and read-only cluster access and nothing else, "
                 "and that is not a gap in your work. " + how +
                 " If refused, node F's substitutes are read-only and cover most of it — the "
                 "tool's own logs, stored session rows, and a claimed capability checked against "
                 "the config that would implement it. Record `node F unreachable` with "
                 "`--blocked-by S4` and the substitute you used.")
        env = access.get("invocation_environment")
        if env == "production":
            print("access.yml records invocation_environment: production. Permitted, but prefer a "
                  "non-production tenant where one exists, and name in the record what this turn "
                  "touched.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
