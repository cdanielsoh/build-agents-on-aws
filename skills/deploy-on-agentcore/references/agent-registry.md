# AWS Agent Registry

Publish and discover agents, MCP servers, and skills. Records carry a protocol descriptor
validated against the official schema, move through a review lifecycle, and are searchable by
natural language — with discovery scoped by per-registry inbound authorization.

> **GA, under the `agent-registry` namespace.** The public-preview `bedrock-agentcore`
> namespace is **discontinued on 2026-09-17**. If you built against the preview, migrate now —
> `SearchRegistryRecords` was renamed `SearchDiscoverableRegistryRecords`, and
> `ListDiscoverableRegistryRecords` / `BatchGetDiscoverableRegistryRecord` exist only in the
> new namespace. See the AWS registry migration guide.

## Table of Contents

1. [Concepts](#concepts)
2. [Record Types and Descriptors](#record-types-and-descriptors)
3. [Record Lifecycle](#record-lifecycle)
4. [Inbound Authorization](#inbound-authorization)
5. [Discovery](#discovery)
6. [Search Behaviour](#search-behaviour)
7. [Eventual Consistency](#eventual-consistency)
8. [Registry Topology](#registry-topology)
9. [Writing Discoverable Records](#writing-discoverable-records)

---

## Concepts

| Term | Meaning |
|---|---|
| **Registry** | The container. Inbound authorization is configured **per registry** — this is the discovery boundary. |
| **Record** | One published resource. Has metadata plus exactly one primary descriptor. |
| **Descriptor** | The record's actual content, validated against a protocol schema (MCP `server.json`, A2A agent card, AgentSkills). |
| **Revision** | Records are versioned; editing an approved record creates a new draft while the approved revision stays discoverable. |

Record metadata: `name` (unique per registry), `displayName`, `description`, `recordVersion`,
`recordType`, `tags`.

The registry validates content against official protocol schemas and supports all versions of
the MCP protocol schema and the A2A schema.

---

## Record Types and Descriptors

Two independent choices per record: the **record type** (a semantic classification, which
constrains the descriptor) and the **descriptor** (the content shape). **Exactly one primary
descriptor key may be populated.**

| Record type | Valid descriptors |
|---|---|
| `AGENT` | `a2aAgentCard`, `mcpServer`, `custom` |
| `MCP` | `mcpServer`, `custom` |
| `SKILL` | `agentSkillsDefinition`, `custom` |
| `CUSTOM` | `custom` |

An agent that does not speak A2A still belongs under `AGENT` — use `mcpServer` if it speaks
MCP, or `custom` for anything else including a bare HTTP endpoint. The record type is about
*what the thing is*, not which protocol it happens to use.

### MCP server records

Server definition in `descriptors.mcpServer.data`, schema version in
`descriptors.mcpServer.dataSchemaVersion`. **Tools nest separately**, under
`descriptors.mcpServer.additionalData.tools`.

```json
{ "name": "my-org/weather-server",
  "description": "Weather data and forecasts via OpenWeatherMap API",
  "version": "1.0.0" }
```

```json
{ "tools": [{
    "name": "get_weather",
    "description": "Get the current weather for a given location",
    "inputSchema": {
      "type": "object",
      "properties": {"location": {"type": "string",
                                  "description": "City name, postal code, or lat,long"}},
      "required": ["location"]
    }}]}
```

Server content is validated against the [official MCP registry](https://registry.modelcontextprotocol.io/)
`server.json` schema (versions `2025-12-11` back to `2025-07-09`); tools against the MCP
protocol specification (`2025-11-25` back to `2024-11-05`). If you have no `server.json`, write
one against the newest schema.

### A2A agent records

Agent card in `descriptors.a2aAgentCard.data`, `dataSchemaVersion` `0.3`. Validated against
`#/definitions/AgentCard` in the A2A JSON schema — not the whole document, which matters if you
are generating it.

### Skill records

`agentSkillsDefinition` is the primary descriptor. Both parts are optional:

- `descriptors.agentSkillsDefinition.additionalData.skillMd.data` — the `SKILL.md` body,
  validated against the [AgentSkills spec](https://agentskills.io/home).
- `descriptors.agentSkillsDefinition.data` — a structured definition (`repository`,
  `websiteUrl`, `packages`), `dataSchemaVersion` `0.1.0`.

**The registry stores only the markdown, not the rest of a skill's files, and treats it as
discovery metadata.** It is a catalogue, not a distribution mechanism — point `packages` or
`repository` at wherever the skill actually lives.

---

## Record Lifecycle

```
Create → DRAFT → Submit → PENDING_APPROVAL → Approve → APPROVED
                              │                          │
                              │ Reject                   │ Edit (new DRAFT revision;
                              ▼                          │  approved stays discoverable)
                         REJECTED ── Approve (direct) ───┘
                              │
                              └── Edit → DRAFT

        Any status → DEPRECATED (terminal, cannot be undone)
```

- **Submit** → `PENDING_APPROVAL`, with an EventBridge notification. Auto-approval skips
  straight to `APPROVED`.
- **Approve / Reject** via `UpdateRegistryRecordStatus`. A rejected record can be approved
  directly.
- **Deprecate** is terminal from any status and **cannot be reversed**.

### How edits affect status

| Current | Effect of an edit |
|---|---|
| `DRAFT` | Updated in place, stays `DRAFT` |
| `PENDING_APPROVAL` | New `DRAFT` revision; the pending one is discarded and was never discoverable |
| `APPROVED` | New `DRAFT` revision; **the approved revision stays discoverable** until the new one is approved |
| `REJECTED` | New `DRAFT`; must go through submit-and-approve again |
| `DEPRECATED` | Cannot be edited |

**To temporarily hide an approved record, reject it — do not deprecate it.** Rejected records
are not returned by discovery, and you can edit and re-approve later. Deprecation is
irreversible, so reaching for it as an off switch is a one-way door.

### Dual revisions

Editing an approved record leaves two live revisions, and the API you call determines which you
see:

| API surface | Returns |
|---|---|
| `SearchDiscoverableRegistryRecords`, `ListDiscoverableRegistryRecords`, `GetDiscoverableRegistryRecord`, `BatchGetDiscoverableRegistryRecord`, `InvokeRegistryMcp` | **Approved revisions only** |
| `GetRegistryRecord`, `ListRegistryRecords` | **Latest revision, any status** |

A management-plane read showing your edit while consumers still see the old content is correct
behaviour, not a bug.

---

## Inbound Authorization

Controls which consumers can discover records — search, browse, invoke the MCP endpoint.
Configured **per registry**, either IAM or JWT.

**Both are immutable after creation.** You cannot change the authorization type later, and for
JWT registries you cannot change the discovery URL either. Decide before you create.

### IAM

SigV4 with the caller's AWS credentials. Grant on the registry ARN:

```json
{
  "Effect": "Allow",
  "Action": [
    "agent-registry:SearchDiscoverableRegistryRecords",
    "agent-registry:ListDiscoverableRegistryRecords",
    "agent-registry:GetDiscoverableRegistryRecord",
    "agent-registry:InvokeRegistryMcp"
  ],
  "Resource": "arn:aws:agent-registry:us-east-1:123456789012:registry/<REGISTRY_ID>"
}
```

Omitting the resource scope (or using `"*"`) grants access to **every registry in the
account** — which defeats the point of splitting registries as a boundary.

### JWT

Tokens from your own IdP (Cognito, Okta, Entra ID, Auth0, any OAuth 2.0 provider). Requires a
discovery URL plus **at least one** authorization rule:

| Rule | Matches |
|---|---|
| Allowed audiences | `aud` claim — stops token reuse across APIs |
| Allowed clients | `client_id` claim |
| Allowed scopes | at least one scope in the token must match |
| Custom claims | a named claim against a required value (`STRING` or `STRING_ARRAY`) |

Configure more than one and **all** are verified.

### Authorization scope

The setting governs **data-plane discovery only**. Every control-plane API — `CreateRegistry`,
`CreateRegistryRecord`, `UpdateRegistryRecordStatus` — **always requires IAM**, regardless. So
a JWT registry still needs an IAM path for publishing and curation; JWT is for consumers.

---

## Discovery

| API | Purpose |
|---|---|
| `SearchDiscoverableRegistryRecords` | Natural-language hybrid search |
| `ListDiscoverableRegistryRecords` | Browse the approved catalogue with no query |
| `BatchGetDiscoverableRegistryRecord` | Fetch specific records |
| `InvokeRegistryMcp` | **The registry as an MCP endpoint** |

`InvokeRegistryMcp` exposes the three discovery APIs as MCP tools, so an agent can discover
capabilities using the same client it uses for everything else — no registry-specific SDK code.
That makes "find me a tool for X" a tool call, which is the natural way to wire discovery into
an agent rather than baking a catalogue into its prompt.

---

## Search Behaviour

```python
client = boto3.client("agent-registry")
resp = client.search_discoverable_registry_records(
    registryIds=["<registryARN>"],       # exactly ONE
    searchQuery="weather forecast",       # 1–256 chars
    maxResults=10,                        # 1–20, default 10
    filters={"recordType": {"$eq": "MCP"}},
)
```

**`registryIds` takes exactly one registry.** There is no cross-registry search — the single
most important constraint for topology, below.

### Filters vs query text

Filters apply to `name`, `recordType`, `recordVersion`, with `$eq`, `$ne`, `$in` and `$and` /
`$or`. They are applied **before** scoring, so they shrink the candidate set rather than
trimming ranked output.

**Do not put filter-like constraints in the query text.** `"find all MCP servers for weather
forecasts"` sends the whole sentence through semantic matching, so "MCP servers" is read as
conceptual intent and you get agent records about weather ranked alongside MCP servers. Use a
filter for the attribute and keep the query on the topic:

```json
{"searchQuery": "weather forecast", "filters": {"recordType": {"$eq": "MCP"}}}
```

### How ranking works

Semantic and keyword search run in parallel over the same indexed records and merge. A record
ranking well in both outranks one strong in only one. Within keyword search, **`name` has the
strongest influence**, then `description` and descriptor content equally.

Descriptor content is indexed for semantic matching — tool names, tool descriptions, and input
parameter names all contribute. A thin `server.json` is a discoverability problem, not just a
documentation one.

Short specific queries (`"weather-api-v2"`) favour keyword matching; natural-language
descriptions (`"extract structured data from PDF documents"`) favour semantic.

---

## Eventual Consistency

Discovery is eventually consistent. After `UpdateRegistryRecordStatus` approves a record it
typically indexes within seconds, **but can take minutes**.

During that window search, list, batch-get, and `InvokeRegistryMcp` may all omit the record,
while `GetRegistryRecord` and `ListRegistryRecords` return it immediately — **control-plane
reads are strongly consistent; only discovery is not**.

Consequences worth designing for:

- Confirm discoverability with a retry and exponential backoff after approving; don't assert on
  the first call.
- A record missing from search is not a record missing from the registry. Call
  `GetRegistryRecord` before concluding anything.
- If EventBridge triggers downstream systems on approval, add a delay before they query
  discovery — otherwise the automation races the index.

---

## Registry Topology

**One registry per authorization boundary, not one shared registry.** This is forced by three
properties acting together, not a stylistic preference:

1. Search takes **exactly one** registry per call.
2. Metadata filters cover only `name`, `recordType`, `recordVersion` — nothing team- or
   owner-shaped.
3. Inbound authorization is **per registry**, with no per-record policy.

In one shared registry, every authenticated consumer can therefore search every record, and
scoping degrades to convention: "an analyst cannot see that the payroll MCP server exists"
becomes "an analyst is asked not to look". Splitting by boundary makes it an **authentication**
boundary instead — you cannot filter your way into a registry you hold no credential for.

The costs are real and worth stating to whoever asks for one registry:

- Consumers spanning two boundaries need client-side fan-out, one call per registry.
- One `allowedClients` / IAM policy set per registry to maintain.
- Genuinely org-wide utilities get published more than once.

Mirroring the same boundary in your Gateway topology (one gateway per domain, which Cedar
already pushes you toward) means "this team cannot reach that tool" is one idea enforced in
discovery *and* invocation, rather than two configurations that can drift apart. See
[policy.md](policy.md#cedar-limitations).

---

## Writing Discoverable Records

- **Describe what it does and what problem it solves**, in natural language. `"helps customers
  track package deliveries"` is discoverable; `"delivery-status-endpoint"` is not.
- **Publish complete tool definitions.** Tool descriptions and input parameter descriptions
  feed semantic relevance, so an MCP record with bare tool names is close to invisible.
- **Put the terms consumers will actually type into the name and description**, since keyword
  search matches exact text and `name` dominates keyword ranking.
- **Use `recordVersion` deliberately** — it is one of only three filterable fields, so it is
  how consumers pin to a known-good revision.

---

## Related

- [gateway-and-mcp.md](gateway-and-mcp.md) — the Gateway's own `x_amz_bedrock_agentcore_search`
  finds tools *within* a gateway; the registry finds resources *across* an organization
- [policy.md](policy.md) — Cedar authorization, and why gateway topology mirrors registry
  topology
- [identity.md](identity.md) — JWT inbound authorization for registry consumers
