# Deep Dive: The Cost of Coordination

Splitting an agent moves work across an architectural boundary. That boundary adds useful separation only when its measured benefit exceeds serialization, duplicated context, routing, synchronization, and operational cost.

## Work is not wall-clock time

Track these quantities separately:

- `total_model_work_ms`: model processing summed across every invocation;
- `total_tool_work_ms`: tool processing summed across every invocation;
- `total_coordination_work_ms`: handoff and artifact serialization work;
- `total_work_ms`: model, tool, and coordination work;
- `wall_clock_latency_ms`: elapsed user-visible time; and
- `critical_path_ms`: the longest dependency path.

Three independent specialists taking `60`, `55`, and `80 ms` consume `195 ms` of total work but only `80 ms` of conceptual parallel wall-clock time. A dependent synthesis step begins after that batch. Real systems add queueing, network, rate-limit, and scheduling delays.

## Context and token cost

The entire context does not inherently need to be reprocessed. Naive shared-chat systems may repeatedly send growing transcripts; scoped artifact systems can project only the trusted facts, evidence references, and specialist outputs needed by the next step.

Measure input, output, and coordination tokens separately. Coordination tokens include routing descriptions, handoff serialization, artifact wrappers, and review messages. Do not infer quality from token volume.

## State ownership

Single-agent architectures generally have simpler state ownership, but their state can live outside the model context in databases, checkpoints, caches, or application objects. Multi-agent systems require explicit ownership and consistency rules across more boundaries.

Use immutable application context for tenant, user, authorization, and incident identity. Use typed artifacts for specialist findings. Compute handoff-information recall from required structured fields rather than relying on prose.

## Deterministic measurement fixture

The course fixture records input/output tokens, model/tool work, handoff serialization, coordination tokens, and cost for each operation. Sequential batches add their elapsed time; operations inside one batch contribute the maximum elapsed time to wall clock while all work remains additive.

These values are reproducible teaching data, not vendor benchmarks. Production evaluation should use trace-derived distributions and report p50/p95/p99 by workload and failure slice.

## Failure and reliability cost

Coordination creates additional failure edges: timeout, authorization denial, invalid artifact, unavailable source, duplicate delegation, loop, and partial fan-out failure. A required-evidence contract determines whether the run may continue degraded, must abstain, or needs human review.

Budget agent invocations, handoffs, depth, parallelism, total cost, and deadline. Also account for shared downstream limits: three concurrent specialists may still serialize on one database or model quota.

## Cost per compliant success

Raw cost per attempt rewards unsafe shortcuts. Prefer:

```text
cost per compliant success = total actual cost / compliant successful tasks
```

"Compliant" means required evidence and grounding passed, no safety regression occurred, and the run respected budgets and policy. Compare this with task success, latency, and exposure—never alone.

Return to the [main lesson](README.md) or run the deterministic comparison in [`single_vs_multi_agent.ipynb`](single_vs_multi_agent.ipynb).
