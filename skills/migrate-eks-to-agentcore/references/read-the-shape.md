# Node A — establish the shape before running any search

**This node gates every other read.** Needs S1 or S2. If you have neither source nor control
plane, the assessment is not possible; say so and stop.

Two questions, in this order: *is the repo the thing that is running?* and *is the logic even in a
repo?*

## If a deployment exists, it outranks the repo

When both are available and they disagree, **the cluster is the fact and the repo is a claim.**

`[measured:reference]` on a deployed third-party agent platform: the source tree at `HEAD` served
one API version with one set of resource kinds, while the released chart actually deployed served
an *earlier* version with **differently named** kinds. A manifest written from the source tree was
rejected by the API server outright (`no matches for kind`). Nothing in the repo signalled it.

Expect this wherever the customer deploys a pinned release of something they also track at head:
their own chart, a vendored dependency, a platform they did not write. It fails in both
directions — the repo can be newer than production and look internally consistent, so there is no
broken reference to trip over.

So take these from the cluster, not from source:

```bash
kubectl get crd <name> -o jsonpath='{range .spec.versions[*]}{.name} served={.served} storage={.storage}{"\n"}{end}'
kubectl get deploy <name> -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'   # the running tag
kubectl get deploy <name> -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}{"\n"}{end}'
```

**Record the divergence itself.** A repo ahead of production means the assessment's `file:line`
evidence describes code the customer is not running, which silently invalidates every
`[read:source]` tag in the record. That is survey answer S1's `source_matches_deployment: false`,
and it withdraws the `source` access class in `scripts/lens_plan.py`.

## What is this written in, and is the logic in a repo at all?

```bash
git ls-files | sed 's/.*\.//' | sort | uniq -c | sort -rn | head
ls Dockerfile* */Dockerfile* go.mod package.json pom.xml build.gradle* pyproject.toml 2>/dev/null
```

| Shape | Where the answers live |
|---|---|
| Application code (any language) | the repo — [read-the-repo.md](read-the-repo.md), with the column for that language |
| **Declarative platform** (agent defined as a resource, config, or DSL; a shared engine executes it) | the **resource specs and the schema the cluster serves**, not application source — [read-the-cluster.md](read-the-cluster.md). Where behaviour is visible in neither, read the platform's source **pinned to the release they run**, not its documentation |
| **Managed / third-party agent runtime** | its configuration; the agent logic may not be yours at all |

For the declarative case the whole "read the source" premise weakens: there may be no handler, no
session code and no tool definitions to find, because the platform supplies them. **An empty
search result then means "wrong question", not "gap"** — recording `absent` there is the error.
Read the resource spec and the platform's guarantees instead, and say in the record that the
component is supplied by the platform rather than by the customer.

This fork is also what decides `code_ownership` in the record, which decides whether a remedy is
`customer_code`, `platform_config` or `upstream_contribution` — see
[record-and-adopt.md](record-and-adopt.md). Establish it here, not after writing the adoption path.
