# Node I — measure across a concurrency sweep

**Needs S5 = yes and node H.** Generating load is the same consent class as invoking, and more
intrusive. It is *not* the first thing to do.

> An earlier version of this instrument called the sweep "the single most important addition to this
> method." That was an overcorrection from one workload. Measured across three independent services:
> on one, a store fetch collapsed **670×** between c=6 and c=12 `[measured:customer]` and only the
> sweep could have found it. On the other two, throughput scaled **monotonically with flat latency**
> and the sweep's only unique output was a set of harmless warnings — while the decisive findings on
> both came from a handful of sequential requests and one log grep.

So: read the logs, invoke the agent, and check one tool result against reality **first**. Sweep when
you need per-turn numbers under load, at three or more levels, because one level can produce a
plausible and wrong record.

**Record a clean sweep as a real result.** "No degradation to c=N" is worth stating, and it is
evidence against the cliff this file describes rather than a failure to find one.

## The rule: concurrency is bounded by a pool nobody chose

Every runtime has at least one such pool, sized from a number that is wrong inside a container. It
is invisible in code review because the code is *correct*, and invisible in cluster metrics because
the pod is not short of CPU — it is short of pool slots, and **waiting is not utilization.**

The instance measured `[measured:customer]`: the service correctly offloaded every blocking call to
a thread pool, which both a code review and a grep credited it for. But the pool was the *default*
one, sized `min(32, cpu_count + 4)` — inside a 2-CPU container that is **6**. Twelve concurrent turns
queued behind six threads. It was the largest defect present, it was a few lines to fix, and it
improves the current service as much as the migrated one.

So **"is the concurrency model clean" is the wrong question.** Clean is necessary and not sufficient.
The question is *what integer bounds parallelism, where did that integer come from, and does it
exceed peak concurrency.*

## Find the binding limit for this runtime

| Runtime | The pool that usually binds | Default, and where it comes from |
|---|---|---|
| Python, async handlers offloading work | the default thread-pool executor | `min(32, cpu_count + 4)` |
| Python, **sync** handlers under an ASGI server | the framework's capacity limiter | often **40** — a different number from the above, so identify the handler style first |
| Go | rarely a pool; `GOMAXPROCS`, plus client connection pools and any semaphore | `GOMAXPROCS` from host cores unless set |
| Node / TypeScript | libuv thread pool; HTTP agent max sockets | `UV_THREADPOOL_SIZE` **4** |
| JVM | the servlet/reactive worker pool, and the DB connection pool | framework-specific, usually explicit |
| Any | **DB / HTTP client connection pool** — check whether it *blocks* or merely *discards* | library default, often 10 |

**Record what kind of limit each one is, not just its integer.** An earlier version of this table
said the connection pool "binds before the thread pool on I/O-heavy agents"; that was **falsified**
at c=45 on a real agent, where the AWS SDK's pool size is a *reuse* cap that logs
`Connection pool is full, discarding connection` and proceeds — 42 warnings, **no latency inflation
at all**. Treating it as a ceiling manufactures one.

| Kind | Behaviour at saturation | Record it as |
|---|---|---|
| **hard** | requests queue and wait | the binding limit — this is the number that matters |
| **soft** | excess is discarded or a new resource is created; a warning appears | a warning source, **not** a ceiling |
| **none** | no client-side pool; the server's limit applies | name the server's limit instead |

So `concurrency_limit` needs the *kind* alongside the value. One integer with no kind is how a
warning becomes a fabricated bottleneck in a record.

**The universal trap: pool sizes derived from host CPU count, not from the cgroup limit.** A
1-CPU-limited pod on a 64-core node derives 36, not 5. Read both numbers, always:

```bash
kubectl exec -n <ns> <pod> -- sh -c 'cat /sys/fs/cgroup/cpu.max; nproc'
```

Then ask the runtime what it thinks. Translate for the stack in front of you —
`runtime.NumCPU()`/`GOMAXPROCS(0)` in Go, `UV_THREADPOOL_SIZE` and `os.cpus()` in Node,
`availableProcessors()` on the JVM. The Python form:

```bash
kubectl exec -n <ns> <pod> -- python -c "
import os, concurrent.futures as f
print('runtime cpu count', os.cpu_count(), ' <- HOST cpus, not your cgroup limit')
print('default pool     ', f.ThreadPoolExecutor()._max_workers)
try:
    import anyio.to_thread as a
    print('sync-handler cap ', a.current_default_thread_limiter().total_tokens)
except Exception as e:
    print('sync-handler cap  n/a', e)
"
```

Record **what bounds concurrency and the number**, not a boolean. Where no such mechanism applies —
no event loop, no worker pool, a platform that runs each request elsewhere — record
`not_applicable` rather than `false`, because `false` reads as a clean bill of health for a check
that never ran.

## Two findings only a sweep produces

- **The rebuild-versus-fetch ratio is not a constant.** [topologies.md](topologies.md) states ~8.5×
  with rebuild dominating. Measured: **17× at c=1**, then it **inverts at c=12** (fetch 2,491 ms vs
  rebuild 147 ms) `[measured:customer]`. It is a function of concurrency, so "optimise the rebuild,
  not the store" is only true unloaded.
- **CPU-based autoscaling is unusable, with a number.** Under 8-way concurrent load the pod drew
  **179m against a 1000m limit — 17.9%** — while store latency was already collapsing. A target of
  70% never fires.
