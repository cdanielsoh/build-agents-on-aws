# Nodes A2 and B — read the control plane, and read the logs

Needs S2. Two nodes share this file because they share an access class and neither needs anyone's
permission beyond the read itself.

**Node B is the cheapest decisive read available and it has no substitute.** Three assessments each
found more here than in source searches, one of which ran against a tree that did not exist. Do it
early; if it is unavailable, say what that costs.

## Node B — what the running system already emitted

```bash
kubectl logs -n <ns> <pod> --tail=200 | grep -iE 'error|exception|failed|traceback|warn'
```

**Per pod, not per Service.** A swallowed exception is invisible in config, in the resource specs,
and in a successful-looking answer — it shows up only here. A bare `except` that logs and continues
turns a total failure into a component that reports healthy and serves requests: on one service the
store whose only job was repopulating a cold cache was failing **100% of reads**, and the logs were
the only place that appeared.

Also read `status` conditions. Platforms record their own verdict there, including fields they
accepted and then ignored:

```bash
kubectl get <kind> <name> -o jsonpath='{.status}'
```

## Node A2 — resource specs and served schemas

On a declarative platform this *is* the authoritative config store, and it outranks
`[read:source]`. Tag it `[read:cluster]`.

```bash
kubectl get <kind> <name> -o yaml                    # what is declared
kubectl get crd <name> -o json                       # what the API server will accept
kubectl get deploy,sa,svc,cm -l <selector>           # what actually exists
kubectl auth can-i --list --as=system:serviceaccount:<ns>:<sa>   # what the agent COULD do
```

The substitute when A2 is unreachable is source, **explicitly labelled as possibly-not-deployed**.

## Two Auto Mode traps that cost real diagnostic time

They share a shape: **an EKS Auto Mode default that is absent or narrower than assumed, surfacing
as an error that names the symptom rather than the cause.** Both are one `kubectl get` away.

| Assumed | Actual on Auto Mode | Presents as |
|---|---|---|
| a default StorageClass exists | **none is marked default.** The only class may be `gp2` on the *legacy in-tree* `kubernetes.io/aws-ebs` provisioner, which no longer exists in 1.34 | a PVC `Pending` forever, and whatever depends on it crash-looping. Observed: `database migration failed ... connection refused` — the dependent component's error, three steps from the cause |
| the built-in NodePool schedules anything | `general-purpose` is hardcoded **amd64** | an arm64 pod stuck `Pending`, or `exec format error` read as an application crash |

```bash
kubectl get sc                                            # is ANY class annotated default?
kubectl get sc <name> -o jsonpath='{.metadata.annotations}'
kubectl get pvc -A --field-selector=status.phase==Pending
```

**The generalizable rule: when a component crash-loops on a connection to another component, check
the other component's scheduling before reading either one's code.** A stateful dependency that
never got a volume looks exactly like a misconfigured connection string.

Relevant to the assessment because a customer agent with a PVC — a database, a vector store, a
checkpoint volume — has a storage dependency that does **not** transfer to AgentCore. Record it as
its own inventory row rather than folding it into "the session store".
