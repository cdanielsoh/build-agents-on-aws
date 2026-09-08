# Node H — the four measurements

Needs S3 = a running deployment. **Without one, Gate 2 is unavailable, full stop.** Every
measurement is `open`, the sweep cannot run, and the correct output is
`cost_confidence.per_turn: unavailable`. Say that at the start rather than letting an assessor
discover it three steps in and reach for this plugin's reference figures to fill the hole. A
repo-only assessment is still valuable — Gate 0 and the inventory are most of the findings — it just
cannot price anything.

## Check the billing floor before investing in precision

AgentCore bills memory against a **128 MB minimum**. Measured on a real agent: peak was 13.3 MB —
9.6× *below* the floor — so the billed figure is 0.128 GB no matter how precisely you measure, and
the whole high-water-mark apparatus below was wasted effort on that workload.

One cheap read first. If peak is comfortably under 128 MB, record it, note that memory right-sizing
is **not an available lever** post-move, and skip the rest of this file.

## Prefer what already exists

Do not instrument if the data is already there.

| Number | Where to look first |
|---|---|
| CPU per turn | cgroup v2 `/sys/fs/cgroup/cpu.stat` `usage_usec` deltas ÷ turns |
| Wall per turn | their own request telemetry, existing traces, load-balancer target response time |
| **Peak** memory | cgroup v2 `/sys/fs/cgroup/memory.peak` — a true monotonic high-water mark |
| Concurrency | in-flight gauge if present; else active connection count at the load balancer |
| Turns per conversation | session store item stats, or conversation logs |

**`kubectl top` cannot answer the memory question.** Measured against cgroup ground truth on a live
pod: it reported **4m CPU while 8 conversations were in flight**, repeated a stale value for four
consecutive samples (18s), and its best memory reading was **3.7% below the true peak**. It is a
~60s-window average sampled on a delay.

```bash
kubectl exec -n <ns> <pod> -- sh -c 'cat /sys/fs/cgroup/memory.peak; grep usage_usec /sys/fs/cgroup/cpu.stat'
```

`memory.peak` is exactly the quantity AgentCore bills on — a high-water mark that never decays — so
it is not merely more accurate, it is the *right* metric. Subtract measured idle drift from the CPU
delta (an idle replica drew ~3.6 millicores).

## When the image has no shell

That command needs a shell in the container, and a **distroless image has none**: `kubectl exec -- sh`
returns `sh: executable file not found in $PATH`. `kubectl debug` would work but **mutates the pod**
(`ephemeralContainers`), which a read-only engagement forbids.

Fall back to the kubelet summary API — read-only, and needs nothing in the image:

```bash
kubectl get --raw "/api/v1/nodes/<node>/proxy/stats/summary" \
  | python3 -c "import json,sys
d=json.load(sys.stdin)
for p in d['pods']:
  if p['podRef']['name'].startswith('<prefix>'):
    print(p['podRef']['name'], p.get('cpu',{}).get('usageCoreNanoSeconds'),
          p.get('memory',{}).get('workingSetBytes'))"
```

Its CPU counter is an exact cumulative value, so CPU-per-turn stays trustworthy. **Its memory is
sampled, so it is a maximum-observed — a floor on the true peak.** Record it as such and do not call
it a peak, because understating peak memory understates the AgentCore bill. That is what the record's
`peak_memory_gb.source` and `is_true_high_water_mark` fields exist to carry.

Container Insights is listed in a lot of guidance as the first stop; on the customer cluster it
**was not enabled**, so plan for the cgroup fallback rather than assuming it.

If instrumentation is needed, the minimum is three counters: process CPU, summed request wall time,
and an in-flight gauge.

## Measure in-cluster, in-region

Local measurement of the reference gave 1,048 ms of apparent store I/O; in-cluster it was 11.5 ms.
The difference was internet latency and thread-pool queueing, not real cost — and the local figure
would have made the session store look like the dominant per-turn cost when it is ~10% of it.

**Numbers taken at one concurrency level are not the production numbers.** Whether you need a sweep,
and what it costs to run one, is [concurrency-sweep.md](concurrency-sweep.md). What to do with the
four numbers once you have them is [cost-model.md](cost-model.md).
