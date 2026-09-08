# AgentCore Policy — Cedar Authorization on Gateway Tool Calls

Fine-grained authorization for MCP tool invocations, evaluated at the Gateway before the
tool runs. Policies are Cedar statements validated against a schema **auto-generated from
the Gateway's MCP tool manifest**.

> This is the authorization layer that answers "may *this* caller invoke *this* tool with
> *these* arguments?" It sits above identity (see `references/identity.md`) and below your
> data layer's own enforcement (see `references/security.md`). It does not replace either.

## Table of Contents

1. [Concepts](#concepts)
2. [The Cedar Model](#the-cedar-model)
3. [Writing Policies](#writing-policies)
4. [Cedar Limitations That Will Bite You](#cedar-limitations)
5. [Guardrails in Policy](#guardrails-in-policy)
6. [The Two LOG_ONLY Controls](#the-two-log_only-controls)
7. [Rollout Workflow](#rollout-workflow)
8. [Validation Findings](#validation-findings)
9. [Policy Generation from Natural Language](#policy-generation)
10. [Configuration](#configuration)
11. [Troubleshooting](#troubleshooting)
12. [IAM Permissions](#iam-permissions)

---

## Concepts

| Concept | What it is |
|---|---|
| **Policy engine** | Container holding a set of Cedar policies. Attached to a Gateway to enforce access control on its tools. |
| **Policy** | One Cedar statement. Cedar is **default-deny** with **forbid-wins**: a request is allowed only if some `permit` matches, and any single `forbid` denies it. |
| **Policy generation** | AI translation of natural-language intent into Cedar. Produces candidate *assets* you review and promote — it does not create live policies directly. |

Names for both engines and policies match `[A-Za-z][A-Za-z0-9_]*`, are 1–48 chars, and are
**immutable after creation**. Statements are 35–10,000 chars.

---

## The Cedar Model

The schema is derived from the Gateway's tool manifest, so it is narrow by design.

### Principals

| Type | When | Attributes |
|---|---|---|
| `AgentCore::OAuthUser` | OAuth-authenticated gateway (`CUSTOM_JWT`) | `id` from the JWT `sub`; OAuth claims exposed as **tags** |
| `AgentCore::IamEntity` | IAM-authenticated gateway (`AWS_IAM`) | `id` is the caller's IAM ARN; **no tags** — match on `principal.id` |

Claims are reached with `principal.hasTag("x")` and `principal.getTag("x")`. Custom
attributes on `OAuthUser` do not exist; tags are the only mechanism.

### Resource

Always `AgentCore::Gateway`. Match by type (`principal is`, `resource is`) or by exact ARN
(`resource == AgentCore::Gateway::"<gw-arn>"`). **To scope a policy to specific actions you
must use the specific ARN.**

### Actions

Each MCP tool becomes one action, named `<target-name>___<tool-name>`:

```
AgentCore::Action::"RefundTool___process_refund"
```

**There is no action hierarchy to permit at.** `action in AgentCore::Action::"Mcp"` is
rejected at `CreatePolicy` with `ValidationException: invalid action ID "Mcp"`, and
`"CallTool"` fails the same way. `"ListTools"` fails differently and more informatively —
`Target 'ListTools' does not exist in gateway '<id>'. Available targets: catalogTools` —
which shows why: the action namespace is **target-scoped**, so the only valid action IDs
are `<target-name>___<tool-name>`.

Consequence for a broad permit: enumerate the actions rather than reaching for a parent.

```
permit(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"catalogTools___get_course",
    AgentCore::Action::"catalogTools___search_courses"
  ],
  resource == AgentCore::Gateway::"<gw-arn>"
);
```

Verified against a live gateway in us-east-1.

### Context

**`context.input` is the only available context** — the tool's input parameters as declared
in the MCP manifest. (`context.output` exists only inside guardrail expressions.)

JSON Schema types map to Cedar types:

| JSON Schema | Cedar |
|---|---|
| `string` | `String` |
| `integer` | `Long` |
| `boolean` | `Bool` |
| `number` | `Decimal` |

Always guard before reading: `context has input && context.input has actorId`.

---

## Writing Policies

### Role-based access via an OAuth claim

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"RefundTool___process_refund",
  resource == AgentCore::Gateway::"<gw-arn>"
) when {
  principal.hasTag("role") && principal.getTag("role") == "supervisor"
};
```

### Per-user isolation — the request must target the caller's own data

The core pattern. The tool argument must equal the caller's JWT subject:

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"MemoryTarget___POST:/memories/{memoryId}/actor/{actorId}/sessions/{sessionId}",
  resource == AgentCore::Gateway::"<gw-arn>"
) when {
  principal.hasTag("sub") &&
  context has input && context.input has actorId &&
  context.input.actorId == principal.getTag("sub")
};
```

### Argument-value constraints

Cap what a tool may be called with, rather than whether it may be called:

```cedar
forbid(
  principal,
  action == AgentCore::Action::"RefundTool___process_refund",
  resource == AgentCore::Gateway::"<gw-arn>"
) unless {
  context has input && context.input has amount &&
  context.input.amount <= 500
};
```

`forbid … unless` is the right shape for a ceiling: it fails closed when the attribute is
missing, whereas `permit … when` would simply not match and leave another `permit` free to
allow the call.

### Action scoping — a fixed allowlist

```cedar
permit(
  principal,
  action in [
    AgentCore::Action::"OrdersTarget___list_orders",
    AgentCore::Action::"OrdersTarget___get_order_details"
  ],
  resource == AgentCore::Gateway::"<gw-arn>"
);
```

### IAM-authenticated gateway

No tags available, so match the ARN:

```cedar
permit(
  principal is AgentCore::IamEntity,
  action,
  resource == AgentCore::Gateway::"<gw-arn>"
) when {
  principal.id like "arn:aws:sts::111122223333:assumed-role/BatchRunner/*"
};
```

---

## Cedar Limitations

These are the ones that break otherwise-reasonable designs.

**No string concatenation.** `+` applies to integers only. You **cannot** build a value from
a claim — `("/actors/" + principal.getTag("sub") + "/")` is not valid Cedar. Have your IdP
issue a claim that already contains the full value (e.g. a `namespace` claim holding
`/actors/<sub>/`), map it to a tag, and compare against the tag directly. **This constrains
your token design, so decide it before you build the namespace scheme.**

**`like` patterns must be string literals.** You cannot interpolate a claim into a wildcard
pattern, which rules out the same class of dynamic matching.

**No action-id wildcards.** Enumerate with `action in [...]`. A new tool is therefore *not*
covered by an existing allowlist — adding tools to a target is a policy change too, and
under default-deny the new tool is denied until you add it.

The practical mitigation is **topology**: group tools into targets along the same lines you
want to authorize, so one `action in [...]` list per target stays short and a whole capability
can be granted or withheld coherently. Which makes the **target name load-bearing** — Gateway
exposes tools as `{target}___{tool}` and your Cedar actions must use the same prefix. Rename a
target and every policy naming it becomes dead; default-deny then silently blocks the tool
with no error anywhere. Assert the target-name/action-prefix agreement in a test.

**A policy naming specific actions must name a specific gateway ARN**, which pushes the same
decision up a level: one gateway per authorization domain rather than one gateway holding
every team's tools in a single Cedar namespace and a single tool manifest. Splitting gateways
by domain also makes the boundary an *authentication* boundary, not just a policy one.

**No custom attributes on `OAuthUser`.** Tags only.

**No entity types outside the `AgentCore` namespace**, and you cannot define new ones.

**No context beyond `context.input`.** No time of day, no source IP, no request count. Rules
like "only during business hours" are not expressible against the request context.

---

## Guardrails in Policy

Policies can invoke Bedrock Guardrails inline and branch on the confidence score, which lets
you express content rules as authorization rules:

```cedar
forbid (
  principal,
  action == AgentCore::Action::"MyTarget___submit",
  resource == AgentCore::Gateway::"<gw-arn>"
) when guardrails {
  BedrockGuardrails::ContentFilter(["VIOLENCE"], [context.input.userMessage])["VIOLENCE"]
    .confidenceScore.greaterThan(decimal("0.7"))
};
```

This is the one place `context.output` is available. Choosing the threshold is exactly what
`LOG_ONLY` mode is for — see below.

> **Guardrails-in-policy is not available in every region** — at the time of writing, five,
> which notably excludes `us-west-2` and `ap-northeast-2`. This is a region-selection
> constraint, not a runtime one: discovering it after choosing a region means moving the whole
> stack. Verify availability for your target region before committing to this feature, and
> note that the rest of AgentCore Policy is unaffected.

---

## The Two LOG_ONLY Controls

**Two different settings share the value `LOG_ONLY` at two different layers.** Confusing them
is the most likely way to believe you are enforcing when you are not.

| Control | Where | Values | Scope |
|---|---|---|---|
| **Engine** enforcement mode | `policyEngineConfiguration.mode` on `CreateGateway`/`UpdateGateway` | `ENFORCE` (default), `LOG_ONLY` | The entire engine |
| **Policy** enforcement mode | `enforcementMode` on `CreatePolicy`/`UpdatePolicy` | `ACTIVE` (default), `LOG_ONLY` | One policy |

|  | Policy `ACTIVE` | Policy `LOG_ONLY` |
|---|---|---|
| **Engine `ENFORCE`** | Evaluated **and enforced** | Evaluated, logged only; other `ACTIVE` policies still enforce |
| **Engine `LOG_ONLY`** | Evaluated, logged only | Evaluated, logged only |

**Engine mode takes precedence.** With the engine in `LOG_ONLY`, *no* policy can deny
anything — not even one in `ACTIVE` mode — because the Gateway never acts on the decision.

Use **policy-level** `LOG_ONLY` to shadow-test one new rule against production traffic. Use
**engine-level** `LOG_ONLY` to observe everything before turning enforcement on at all.

`LOG_ONLY` policies are evaluated against the same request but kept in a separate result set;
they can never change what a caller experiences. That is the feature's core guarantee.

---

## Rollout Workflow

1. **Deploy the Gateway first.** The Cedar schema is generated from its tool manifest, so a
   policy referencing tools cannot validate until the Gateway is live.
2. **Attach the engine in `LOG_ONLY`.** Nothing is blocked.
3. **Observe.** Watch these CloudWatch metrics under `AWS/Bedrock-AgentCore`:

   | Metric | Meaning |
   |---|---|
   | `LogOnlyMatches` | Requests where a `LOG_ONLY` policy fired |
   | `LogOnlyDecisionFlips` | Requests where it **would have changed the decision** — the key promotion signal |
   | `ConfidenceScore` | Guardrail score distribution, for threshold selection |
   | `ConfidenceThreshold` | The configured threshold, for comparison |
   | `LogOnlyEvalIncomplete` | Evaluation was partial — alarm on a sustained rate |

   All carry `PolicyEngine` and `OperationName` dimensions; per-policy metrics add `Policy`.

4. **Promote when `LogOnlyDecisionFlips` holds at zero** across a representative window. A
   sustained zero means promoting will not block current traffic. A policy that matches often
   *and* appears in the flip set would have broken production.
5. **Promote in place** — `UpdatePolicy --enforcement-mode ACTIVE`. The ID, name, and
   definition are unchanged, and you can demote back to `LOG_ONLY` without delete/recreate.

For guardrail thresholds, accumulate `ConfidenceScore` over days or weeks, then either compute
precision/recall against a labelled set or sample from the high (0.8–1.0), low (0–0.2), and
ambiguous (0.4–0.7) bands and classify them by hand.

Changes are **eventually consistent** — a few seconds to reach the evaluation path. Plan
observation windows accordingly; do not treat promotion as instantaneous.

### What a denial actually looks like

Measured end to end on a live gateway in `ENFORCE`, with a `forbid` on one tool:

```json
{"jsonrpc":"2.0","id":3,"error":{"code":-32002,
 "message":"Tool Execution Denied: Tool call not allowed due to policy enforcement
            [Policy evaluation denied due to ForbidDelete2-n8e26oesyj]"}}
```

Two properties worth relying on:

- **The backing tool is never invoked.** The Lambda target behind the forbidden tool
  returned a distinctive marker if reached; it never appeared. Enforcement is at the
  Gateway boundary, ahead of the tool and outside the agent's reasoning — which is why it
  holds under prompt injection.
- **The denial names the deciding policy** (`ForbidDelete2-…`), so a denial is auditable
  and debuggable without reconstructing the evaluation.

The contrast is the argument for having a policy engine at all. On the *same* gateway with
a working `CUSTOM_JWT` authorizer and **no** policy engine, any holder of a valid token
invoked the destructive tool successfully. Authentication established who was calling and
constrained nothing about what they could do.

### Gateway role permissions — grant the whole set up front

The failure mode is one missing action per error, so discovering these incrementally costs
a round trip each. Grant all of them, on **both** the engine and gateway ARNs:

```json
{"Action": ["bedrock-agentcore:GetPolicyEngine",
            "bedrock-agentcore:ListPolicies",
            "bedrock-agentcore:GetPolicy",
            "bedrock-agentcore:AuthorizeAction",
            "bedrock-agentcore:PartiallyAuthorizeActions"],
 "Resource": ["arn:aws:bedrock-agentcore:<region>:<acct>:policy-engine/*",
              "arn:aws:bedrock-agentcore:<region>:<acct>:gateway/*"]}
```

`AuthorizeAction` is checked against the policy-engine ARN *and* the gateway ARN
separately — granting it on only one produces a second, near-identical error. `UpdateGateway`
validates the whole set eagerly, so a missing permission fails the attach rather than
failing later at invoke time.

**The policy engine must be in the same region as the gateway.** A cross-region engine
cannot be attached. Note that the AgentCore MCP server defaults to `us-west-2`, so
`policy_engine_create` via MCP tooling can silently build the engine in the wrong region —
pass an explicit region, or create it with the CLI.

---

## Validation Findings

Cedar validates each policy against the Gateway-derived schema on create and update.

| Finding | Meaning |
|---|---|
| `VALID` | Ready to use |
| `INVALID` | Validation errors that must be fixed |
| `NOT_TRANSLATABLE` | Generation could not turn the input into a policy |
| `ALLOW_ALL` | Would allow everything — security risk |
| `ALLOW_NONE` | Would allow nothing — unusable |
| `DENY_ALL` | Would deny everything — over-restrictive |
| `DENY_NONE` | Would deny nothing — ineffective |

`validationMode` is `FAIL_ON_ANY_FINDINGS` (default — rejects on any finding) or
`IGNORE_ALL_FINDINGS`. Reach for the latter only after reading the findings and deciding they
are acceptable.

Note the difference in scope: **create/update validation considers the new policy plus its
interactions with all existing policies** in the engine, whereas **generation validates each
candidate policy in isolation**. A generated asset marked `VALID` can therefore still produce
findings when you promote it.

### The default mode rejects a targeted `forbid`

Measured, and worth expecting: a blanket `forbid` on a single destructive tool is refused
under the default `FAIL_ON_ANY_FINDINGS` with

> Overly Restrictive: Policy Engine will deny every request for the specified principal
> (`AgentCore::OAuthUser`), action (`catalogTools___delete_course`) and resource … if the
> policy is added or updated

— reported once per principal type. The finding is technically accurate and practically
backwards: denying every request for that action is exactly the intent of a forbid. It
creates cleanly with `IGNORE_ALL_FINDINGS`.

So the commonest security pattern in this system — broad permit, targeted forbid on the
dangerous tool — requires an explicit override. Read the findings, confirm the principal
and action named are the ones you meant, then override.

### `CreatePolicy` returns success and then fails asynchronously

A 200 from `create-policy` means accepted, not valid. Policies transition
`CREATING → ACTIVE | CREATE_FAILED`, and the reason appears only in `statusReasons`:

```bash
aws bedrock-agentcore-control get-policy \
  --policy-engine-id <engine> --policy-id <policy> \
  --query '{status:status,reasons:statusReasons}'
```

Measured: two of four policies reached `CREATE_FAILED` after a successful-looking create.
Always poll. A deploy script that checks only the exit code will report a policy engine
that authorizes nothing as successfully configured.

---

## Policy Generation

```python
gen = await policy_generation_start(
    policy_engine_id="ProdAuth-abcdefghij",
    name="BusinessHoursGen",
    content={"rawText": "Allow Admins to invoke any tool. Allow Users to invoke the "
                        "weather tool only between 9am and 5pm UTC."},
    resource={"arn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/my-gw-abc123"},
)
# Poll policy_generation_get until status == "GENERATED", then:
assets = await policy_generation_list_assets(...)   # inspect findings; promote only VALID
await policy_create(
    policy_engine_id="ProdAuth-abcdefghij",
    name="BusinessHoursPolicy",
    definition={"policyGeneration": {
        "policyGenerationId": "BusinessHoursGen-abcdefghij",
        "policyGenerationAssetId": "asset-abcdefghij",
    }},
)
```

- `resource.arn` must be a Gateway ARN the caller can see; natural-language content is
  1–2,000 chars.
- **Generated assets are deleted after 7 days.** Promote anything worth keeping into a real
  policy, and copy it into `agentcore.json` if it should survive redeploys.
- Review before promoting. Note that the example intent above ("between 9am and 5pm") is not
  expressible from request context — expect `NOT_TRANSLATABLE` or a policy that does something
  other than what you asked.

### Measured: generation over-generalises in both directions

Asked for the simplest possible pair — *"Allow any authenticated caller to invoke the
`get_course` tool. Forbid all callers from invoking the `delete_course` tool under any
circumstances"* — generation produced two assets and the reasoning layer flagged **both**:

| Asset | Finding |
|---|---|
| the permit | `ALLOW_ALL` — *"permits all actions for all principals"* |
| the forbid | `DENY_ALL` — *"denies all actions for all principals"* |

Neither matched the intent, and neither was promotable under the default validation mode.
Hand-written Cedar for the same intent was two short statements.

Read that as the reasoning layer working, not the feature failing — it caught the
over-generalisation rather than shipping it. But treat generation as a **draft that
requires review**, not a way to avoid learning Cedar. For anything you could write by
hand in five lines, write it by hand.

One operational note: the CLI may render `definition` as `SDK_UNKNOWN_MEMBER` when the
local botocore model lags the service, so the generated Cedar text is not visible in
`list-policy-generation-assets` output. Inspect via the console or upgrade botocore.

---

## Configuration

Two equally supported paths. Use **CDK** when AgentCore resources live alongside existing CDK
infrastructure — VPC, Aurora, Lambda, IAM — which is the common enterprise case. Use
**`agentcore.json`** when the agent project is self-contained.

### CDK (`aws_cdk.aws_bedrockagentcore`)

L1 constructs exist for the whole surface: `CfnPolicyEngine`, `CfnPolicy`, `CfnGateway`,
`CfnGatewayTarget`, `CfnRuntime`, `CfnRuntimeEndpoint`, `CfnMemory`, `CfnEvaluator`,
`CfnOnlineEvaluationConfig`, `CfnDataset`, `CfnOAuth2CredentialProvider`,
`CfnApiKeyCredentialProvider`, `CfnTokenVault`, `CfnWorkloadIdentity`, `CfnResourcePolicy`,
`CfnBrowser`, `CfnCodeInterpreter`.

```python
from aws_cdk import aws_bedrockagentcore as agentcore

engine = agentcore.CfnPolicyEngine(
    self, "PolicyEngine",
    name="northwind_governance",
    description="Cedar authorization, generated from access_matrix.py",
)

policy = agentcore.CfnPolicy(
    self, f"Policy{name}",
    policy_engine_id=engine.attr_policy_engine_id,
    name=name,
    description=description[:4096],
    definition=agentcore.CfnPolicy.PolicyDefinitionProperty(
        cedar=agentcore.CfnPolicy.CedarPolicyProperty(statement=statement)
    ),
    # Schema checks always run. FAIL_ON_ANY_FINDINGS additionally rejects semantic
    # findings like ALLOW_ALL / DENY_ALL — the mistakes that would quietly neuter
    # your access model. Fail the deploy rather than accept the policy.
    validation_mode="FAIL_ON_ANY_FINDINGS",
)
policy.node.add_dependency(engine)
```

#### Four ordering traps, each of which costs a deploy cycle

**Never construct a gateway ARN by hand — use `attr_gateway_arn`.** AgentCore appends a
generated suffix to the gateway name, so `gw-sales` becomes `gw-sales-knnupyrgso`. A
constructed ARN names a gateway that does not exist, and `CreatePolicy` fails with *"Failed to
confirm existence on AgentCore Gateway … make sure you have GetGateway permissions"* — an IAM
error message for a problem that has nothing to do with IAM. `attr_gateway_arn` is an
unresolved token at synth time; CDK substitutes it into the Cedar statement at deploy.

**Policies validate against the gateway's tool manifest, so the engine *and every target* must
exist first.** Declare the dependencies explicitly; synth order is not deploy order.

**Build the engine before the role that references it.** The role's policy names the engine
ARN and the engine does not reference the role, so that direction is acyclic. Building the
role first leaves the ARN unknown.

**Gateways must depend on the role's inline-policy child, not just the role.** Otherwise
`CreateGateway` races the permission attachment and fails with *"Access denied while
calling…"*. CDK names that child `DefaultPolicy`; look it up and fail at synth if it is
missing rather than deploying a template with the race in it.

#### Gateway role permissions for Policy

```python
actions=["bedrock-agentcore:GetPolicyEngine"]          # on the engine ARN
actions=[
    "bedrock-agentcore:AuthorizeAction",
    "bedrock-agentcore:PartiallyAuthorizeActions",     # on engine ARN + gateway ARNs
]
```

`PartiallyAuthorizeActions` is what filters `tools/list` per caller — it is why two personas
see different tool sets rather than the same list with failures on invocation. Omit it and
authorization still works on invoke, but tool discovery stops being scoped.

Because the gateways reference the role, the role cannot name the gateway ARNs without
creating a cycle. Scope by ARN prefix instead:
`arn:aws:bedrock-agentcore:{region}:{account}:gateway/*`.

### `agentcore.json`

Declare engines and policies in `agentcore.json`; they are created by `agentcore deploy`
(which itself deploys via CDK). There are no `agentcore add policy-engine` / `add policy`
subcommands — edit the file.

```json
{
  "policyEngines": [
    {
      "name": "MyPolicyEngine",
      "description": "Authorization for production tools",
      "encryptionKeyArn": "arn:aws:kms:us-east-1:123:key/abc",
      "tags": { "env": "prod" },
      "policies": [
        {
          "name": "AdminFullAccess",
          "statement": "permit(principal in Group::\"Admins\", action, resource);",
          "validationMode": "FAIL_ON_ANY_FINDINGS"
        },
        {
          "name": "RefundCeiling",
          "sourceFile": "policies/refund-ceiling.cedar"
        }
      ]
    }
  ],
  "gateways": [
    {
      "name": "MyGateway",
      "policyEngineConfiguration": {
        "policyEngineName": "MyPolicyEngine",
        "mode": "LOG_ONLY"
      }
    }
  ]
}
```

`sourceFile` is a CLI convenience — the `.cedar` file is read at deploy time and sent as
`statement`. Prefer it over inline JSON strings: real Cedar in a real file is reviewable in a
PR and does not need escaping.

Attach via the CLI with:

```bash
agentcore add gateway --name MyGateway --runtimes MyAgent \
  --policy-engine MyPolicyEngine --policy-engine-mode LOG_ONLY
agentcore status --type policy-engine
agentcore status --type policy
```

---

## Troubleshooting

**Policy stuck in `CREATE_FAILED`** — call `policy_get` and read `statusReasons`. The usual
cause is that the Gateway is not fully deployed, so the tool schema the policy references
does not exist yet.

**Engine stuck in `CREATING`** — check `statusReasons` via `policy_engine_get`, verify KMS key
permissions if `encryptionKeyArn` was supplied, and check CloudTrail for the
`CreatePolicyEngine` call.

**`ConflictException` on create** — the name exists. Names are immutable and unique per
account/engine; pick a different one or delete the existing resource.

**Engine will not delete** — engines must hold zero policies. Enumerate with `policy_list`,
`policy_delete` each, poll `policy_get` until gone, then delete the engine.

**`ValidationException` from `StartPolicyGeneration`** — `resource.arn` must be a visible
Gateway ARN, and content must be 1–2,000 chars.

**Everything is denied after enabling enforcement** — expected under default-deny if no
`permit` matches. Confirm a `permit` covers the action, and remember that action allowlists do
not cover newly added tools.

**Missing `LOG_ONLY` telemetry** — match and decision-flip lists are capped at 1,000 entries
per request. For engines with many policies, trust the CloudWatch aggregates rather than the
per-request lists.

---

## IAM Permissions

All Policy APIs are control plane (`bedrock-agentcore-control`).

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:CreatePolicyEngine",
    "bedrock-agentcore:GetPolicyEngine",
    "bedrock-agentcore:UpdatePolicyEngine",
    "bedrock-agentcore:DeletePolicyEngine",
    "bedrock-agentcore:ListPolicyEngines",
    "bedrock-agentcore:CreatePolicy",
    "bedrock-agentcore:GetPolicy",
    "bedrock-agentcore:UpdatePolicy",
    "bedrock-agentcore:DeletePolicy",
    "bedrock-agentcore:ListPolicies",
    "bedrock-agentcore:StartPolicyGeneration",
    "bedrock-agentcore:GetPolicyGeneration",
    "bedrock-agentcore:ListPolicyGenerations",
    "bedrock-agentcore:ListPolicyGenerationAssets"
  ],
  "Resource": "arn:aws:bedrock-agentcore:*:*:policy-engine/*"
}
```

With `encryptionKeyArn`, also grant `kms:Encrypt`, `kms:Decrypt`, `kms:GenerateDataKey`, and
`kms:DescribeKey` on that key.

---

## Where Policy Fits

Policy is one layer, not the whole answer:

```
Inbound JWT validated            → identity.md   (who is calling)
Cedar policy at the Gateway      → this file     (may they call this tool, with these args)
Outbound token scoped per user   → identity.md   (what the downstream sees)
RLS / LeadingKeys at the data    → security.md   (what the data layer will hand back)
```

A compromised agent that gets past Cedar should still be stopped by the data layer. Keep
per-user enforcement in the database even with policies in place — Cedar constrains the tool
call, not the query the tool ultimately runs.

**The state worth designing for is ALLOW plus zero rows.** A caller invokes a tool Cedar
permits, and RLS returns nothing because none of the rows are theirs. Both controls did their
job, and they are not redundant: Cedar could not have filtered the rows, and RLS could not have
prevented the call. A demo or test suite that only ever shows a `DENY` has not shown
defence in depth — it has shown one layer working twice.

Which is also why the interceptor matters so much: it is the only thing that carries verified
identity from Cedar's world into RLS's. **Cedar gates tools. RLS gates rows. The interceptor
is the only bridge between them.** See [security.md](security.md#request-interceptor-the-only-bridge).

For enforcing the same decisions *inside* the agent — before a call ever reaches the Gateway —
see `InterventionHandler` returning `Deny` in the `strands-agent-design` skill's
`references/tool-design.md`.
