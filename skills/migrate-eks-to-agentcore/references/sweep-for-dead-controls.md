# Node E — the intent-versus-effect sweep

Needs A1 or A2. Every search in [read-the-repo.md](read-the-repo.md) is **positive signal**: it
finds a component that exists. On two independent assessments the highest-severity findings were all
*negative* signal, and no positive search could have found any of them.

The classes observed, each on a real service:

- a control whose name claims a safety role and which has **zero call sites**, while comments and a
  prompt claim the action is gated
- a constant naming tools **that were never built**, omitting the one that was
- a cache key omitting a tenant identifier **already in scope**
- a tool declaring a parameter and **never referencing it in the body** — every call silently
  returns the same result
- a config constant that is **dead code**; the behaviour it names never runs
- a field read from an external protocol's response that the **spec makes optional**, unguarded, on
  the one path a long conversation is guaranteed to reach

That last class generalises past dead controls: **the sweep also has to read the controls that
do something, for assumed-present fields.** Any `d["key"]` on a response from an external protocol
is worth one look at whether the spec makes that key optional. On the observed instance the record
documented a *missing* flow in detail and never checked whether the existing one worked.

## A live control can be worse than a dead one

On one service every symbol had exactly one call site — a clean sweep — and the finding was a
budget ceiling that *does* fire and leaves the conversation permanently unusable afterwards: the
turn it interrupts stores a tool call with no result, and every later turn on that conversation
returns a 500.

So for each guard you find alive, ask **what state it leaves behind when it triggers**, and whether
the next request can still succeed. A guard that half-completes is a durability defect wearing a
safety control's name.

## Four checks

The *questions* are language-independent even though the syntax to answer them is not.

**1. Symbols whose name claims a safety, tenancy or approval role — then count call sites.** Zero
call sites beyond the definition is the finding.

```bash
# Match the naming, not one language's keyword. Covers def/func/function/public *.
grep -rnE '(def|func|function|fn|sub|public|private|protected)[ ,a-zA-Z<>\[\]*]*\b[a-zA-Z_]*(approv|redact|guard|sanitiz|validat|authori[sz]|scope|tenant|budget|limit|check)[a-zA-Z_]*\b' .
grep -rn '<symbol>' . | grep -vE '(def|func|function) +<symbol>'   # per hit
```

**2. Declared tool parameters never referenced in the body.** Read each tool definition and check
every declared parameter is used. Find them by however this stack declares a tool — a decorator, a
struct tag, a registration call, a JSON schema, a resource field:

```bash
grep -rlniE '@tool|tool\(|registerTool|addTool|tools:|inputSchema|parameters' .
```

**3. Constants and config keys that name things, and whether those things exist.** Cross-check
every named tool, model, role or flag against something that actually defines it.

**4. Does the tree even build, and is anything referenced but absent?** Use the stack's own
checker — `python -m compileall`, `go build ./...`, `tsc --noEmit`, `mvn -q compile`. On three of
five assessed services something was imported and did not exist, which meant **the image was built
from a different tree than the one under version control**. That single fact blocked inbound-auth
classification, flush cadence and concurrency hygiene at once — one cause, many `unknown`s, and it
is worth finding in the first ten minutes.

## The declarative equivalent

For a platform-defined agent the equivalent of all four is to diff **intent against effect**: what
the resource declares versus what the controller actually created, and whether the tools or guards
it names resolve to anything.

```bash
kubectl get <kind> <name> -o yaml                   # declared
kubectl get deploy,sa,svc,cm -l <selector>          # what actually exists
kubectl get <kind> <name> -o jsonpath='{.status}'   # the controller's own verdict — read it
```

A field the control plane accepted and validated can still be silently ignored by the executor. That
is `state: present_but_ineffective` with `defect_owner: platform`, not a customer failure — see
[record-and-adopt.md](record-and-adopt.md).
