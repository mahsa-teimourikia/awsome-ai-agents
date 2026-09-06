# Deep Dive: When to Split Agents

Start with the smallest architecture that works. A split is justified only when a measured structural boundary remains after improving the baseline.

## Three common structural reasons

The original three categories remain useful, but they are common reasons—not an exhaustive list.

1. **Tool or capability separation:** different work needs meaningfully different capabilities or credentials.
2. **Asymmetric roles:** producer and independent reviewer have incompatible objectives that benefit from separate context and evaluation.
3. **Security boundaries:** the application enforces separate credentials, authorization, network, or sandbox boundaries.

Other legitimate boundaries include independent parallel work, different models/modalities, fault isolation, workspace/sandbox isolation, state/lifecycle ownership, and different SLOs.

## Optimize tool disclosure before splitting

A large catalog does not automatically need more agents. Evaluate:

- tool search and deferred loading;
- dynamic tool selection;
- progressive disclosure by workflow phase;
- MCP discovery;
- reusable skills; and
- programmatic tool calling.

These approaches can shrink the model-visible surface while one agent and one application policy retain control. Tool visibility reduces exposure; it does not grant or revoke authorization.

## Distinguish pipelines from teams

Multiple model calls may form a deterministic pipeline:

```text
producer → independent reviewer → revision → deterministic gate
```

That design does not require agents to debate or choose the next speaker. Prefer it when the stages and completion rule are known. Use open-ended collaboration only when a labelled evaluation shows added benefit.

## Decision procedure

1. Build and measure a single-generalist baseline.
2. Try dynamic context and tool disclosure.
3. Identify the exact remaining boundary: quality, capability, isolation, concurrency, modality, state, fault, or SLO.
4. Choose the smallest matching primitive: pipeline, manager, handoff, or fan-out.
5. Run the identical task/evidence cases through both architectures.
6. Require success or meaningful exposure improvement, no grounding/safety regression, cost within budget, and latency within SLO.
7. Keep the simpler design if the gate fails.

## Anchor decisions

- A simple runbook FAQ should remain single-agent: **KEEP SINGLE**.
- Generation followed by security review and a deterministic gate is a **PIPELINE**, not a team by default.
- A cross-domain incident may justify parallel specialists when work is independent, contexts/credentials are isolated, and measurements pass the gate.

No fixed agent count or tool count establishes the answer. Re-evaluate as workloads, models, policies, and operational constraints change.

Return to the [main lesson](README.md) or the [coordination-cost deep dive](THE_COST_OF_COORDINATION.md).
