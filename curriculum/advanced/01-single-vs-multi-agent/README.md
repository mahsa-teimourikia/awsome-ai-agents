# Single Agent vs Multi-Agent Systems

**Level:** Advanced · **Time:** 120 min · **Primary notebook:** [`single_vs_multi_agent.ipynb`](single_vs_multi_agent.ipynb) · **Reusable lab:** [`lab.py`](lab.py) · **Policy:** [`policy.py`](policy.py)

Multi-agent systems are a distributed-systems choice, not a capability badge. Splitting one agent introduces routing, serialization, duplicated work, state synchronization, failure propagation, and additional policy surfaces. The primary lesson is therefore:

> Use the smallest architecture that satisfies the workload's measured quality, security, state, and operational requirements. Split only when a structural boundary justifies the coordination cost.

This course compares six architectures on one identical incident. It does not advertise a team as an upgrade, and the credential-free lab performs no production write.

## Learning outcomes

After completing the lesson, you can:

1. distinguish a single agent, dynamic-tool agent, deterministic pipeline, manager, handoff, and parallel-specialist topology;
2. evaluate architectures on the same labelled workload rather than anecdotes;
3. separate total work from wall-clock and critical-path latency;
4. validate typed specialist artifacts and handoffs before sharing or synthesis;
5. enforce tenant, capability, topology, budget, loop, and completion boundaries in application code;
6. compare routers that support single, multi, unknown, and ambiguous outcomes; and
7. use a multi-metric gate and Pareto analysis to decide whether a split is justified.

## Prerequisites and non-goals

You should be comfortable with typed Python, Pydantic, tool-calling concepts, and basic latency/cost measurement. Course 08 introduces bounded planning; Course 10 introduces governed durable state.

The lab is a deterministic teaching simulation. It does not call a live model, execute a rollback, benchmark any vendor, or claim that fixture results generalize to production. The optional framework adapter section is credentialed and is not required for the core path.

## Northstar incident and success contract

Northstar Commerce asks:

> Why did EU checkout conversion fall after `deploy-1842`, and what should we do?

Every architecture receives the same `TaskCase`, trusted context, evidence registry, and expected outcome. Required evidence is:

- `health`: EU checkout health and conversion telemetry;
- `logs`: 3DS callback failures;
- `deployment`: the exact `deploy-1842` record;
- `customer-impact`: aggregate EU enterprise impact; and
- `current-runbook`: the current response procedure.

Success requires complete evidence recall, grounded findings, no cross-tenant access, no capability escalation, no production write, and a bounded recommendation. A rollback request is an approval-gated route, not authorization to execute.

## Mental model: calls, roles, and control

An LLM call is an execution event. An agent is an application component with owned instructions, capabilities, state/context policy, lifecycle, and control semantics. Multiple calls do not automatically create multiple agents.

`generate → review → revise → deterministic gate` can be a pipeline. Prefer this bounded producer/reviewer flow over open-ended debate unless evaluation shows that debate adds enough quality to pay for its extra turns, latency, and failure modes.

Manager delegation and handoff are also different:

- **Manager / agents-as-tools:** the manager calls specialists for bounded artifacts and retains final-answer control.
- **Handoff:** the active owner changes; the recipient owns the next turn or bounded phase.

Global completion remains application-owned in both cases. A specialist string such as `mission complete` is data, not a state transition.

## Three common structural reasons—and the larger decision space

The original lesson correctly highlighted three useful reasons: tool/capability separation, asymmetric roles, and security boundaries. They are common reasons, not the only valid reasons. A measured boundary may also involve:

- parallelizable independent work;
- different models or modalities;
- fault isolation;
- separate workspaces or sandboxes;
- distinct state or lifecycle ownership; or
- different latency, availability, or cost SLOs.

Tool bloat alone does not prove that another agent is needed. First evaluate tool search, dynamic loading, progressive disclosure, MCP discovery, reusable skills, or programmatic tool calling. Split only if the optimized single-agent baseline still loses on a structural requirement.

## The six architectures

| Architecture | Control owner | Best fit | Coordination cost | Primary risk |
|---|---|---|---|---|
| `SINGLE_GENERALIST` | One agent | Small, cohesive read task | Lowest | Broad tool/context exposure |
| `SINGLE_DYNAMIC_TOOLS` | Same agent | Large discoverable tool catalog | Low | Selection/discovery errors |
| `PIPELINE` | Application workflow | Stable ordered stages and deterministic gates | Predictable | Rigid flow or poor stage contracts |
| `MANAGER_SPECIALISTS` | Manager | Bounded specialist subtasks with one synthesis owner | Medium/high | Duplicate calls and manager bottleneck |
| `HANDOFF` | Active specialist after transfer | Specialist should own the next phase | Medium | Lost facts and unclear ownership |
| `PARALLEL_SPECIALISTS` | Workflow/manager | Independent evidence domains on the critical path | High total work, potentially lower wall clock | Rate limits, shared dependency contention |

The reusable implementation defines these as `ArchitectureType` and executes the exact Northstar case through each path.

## Agent boundary is not a security boundary

Names and prompts do not isolate credentials or tools. Security requires application-owned controls: capability policy, credentials, network boundaries, sandboxes, approval, and authorization. Tool visibility can reduce accidental exposure, but visibility is not authorization.

`AgentDefinition` records allowed capabilities, allowed handoffs, context policy, and concurrency. On delegation, the delegated capability set must be no broader than the intersection of the authorized request and parent capability sets, unless explicit application policy grants an exception. This prevents privilege laundering.

Trusted tenant, user, authorization, incident, and deployment fields live in immutable `TrustedContext`. Model or specialist claims cannot overwrite them.

## Typed coordination contracts

Free-form dialogue is not the authoritative handoff:

- `SpecialistArtifact` binds each finding to evidence IDs and carries assumptions, unresolved questions, confidence, identity, tenant, and time.
- `HandoffEnvelope` binds source, target, task, tenant, required facts, artifacts, reason, depth, and deadline.
- `validate_artifact()` checks task, tenant, agent identity, evidence existence, evidence provenance, capability scope, and claim-level grounding before shared state or synthesis.
- `validate_topology()` checks known agents, allowed edges, self-handoff, depth, cycles, duplicate coordination, capability attenuation, tenant consistency, deadlines, cost, invocation, handoff, and concurrency budgets.

The synthesizer receives validated artifacts—not private reasoning or an unconstrained conversation history.

## State preservation and conflict handling

Handoffs must preserve tenant, region, deploy ID, customer tier, and incident window. `measure_handoff_information()` computes recall from actual structured fields; it does not stage a hallucinated state-loss story.

If validated artifacts assert conflicting values—such as `redis` versus `checkout-api` for the suspected component—`detect_conflicts()` emits `CONFLICT`. The system does not average the claims. It obtains bounded reconciliation evidence or requests human review.

## Routing is a set-valued decision

The labelled routing set covers a simple route, multi-route investigation, unknown request, ambiguous request, and approval-gated production request. Valid route states are:

- `SINGLE_ROUTE` — exactly one bounded destination;
- `MULTI_ROUTE` — multiple independent destinations;
- `UNKNOWN` — out of the supported domain; and
- `AMBIGUOUS` — insufficient information to choose safely.

The notebook compares a transparent rule router with frozen semantic-classifier, LLM-router, and manager-delegation fixtures on the same cases. It reports exact route accuracy, micro agent precision/recall, unknown detection, latency, and cost. Frozen outputs teach metric semantics; they are not live benchmarks.

## Budgets, loops, and failures

`DelegationBudget` limits agent invocations, handoffs, depth, parallel agents, cost, and deadline. A repeated `(agent, task, inputs, artifacts)` call without new information is `DUPLICATE_COORDINATION`; a path such as `A → B → A → B` is stopped by topology validation.

Failures are explicit: `TIMEOUT`, `AUTH_DENIED`, `POLICY_DENIED`, `INVALID_ARTIFACT`, and `SOURCE_UNAVAILABLE`. Recovery can retry, use a bounded fallback, continue degraded, abstain, or request human review. One specialist failure does not automatically fail the run: the required-evidence contract determines whether a compliant result remains possible.

Concurrency is bounded by agent limits, global parallelism, rate-limit groups, and shared downstream dependencies. Fan-out is not permission to overload a shared service.

## Measure work and time correctly

The primary comparison uses deterministic fixtures—input/output tokens, model work, tool work, coordination tokens, serialization, and cost. It never generates random benchmark numbers.

For one parallel batch with task times `60`, `55`, and `80 ms`:

```text
total work = 60 + 55 + 80 = 195 ms
parallel wall clock = max(60, 55, 80) = 80 ms
```

`total_model_work_ms`, `total_tool_work_ms`, and `total_coordination_work_ms` are additive consumption. Their sum is `total_work_ms`; `wall_clock_latency_ms` and `critical_path_ms` describe elapsed time. Parallel specialists can increase total work while reducing wall-clock latency.

## Context projection experiment

The full-transcript condition sends growing history to every specialist. The task-scoped condition sends only trusted required facts and relevant artifact/evidence references. Compare total tokens, sensitive fields exposed, and handoff information recall.

In the deterministic fixture, projection lowers tokens and exposure while preserving required facts. Production results must be re-measured on representative traces.

## Evaluation and architecture gate

The dataset includes a simple FAQ, single-domain incident, cross-domain incident, security-sensitive request, multi-route investigation, and out-of-domain request. Metrics cover:

- task success, grounding, required-evidence recall, handoff-information recall;
- route accuracy, agent precision/recall, unknown detection, and conflict rate;
- model/tool calls, handoffs, coordination and total tokens;
- model/tool/total work, wall-clock latency, critical path, and cost;
- cost per compliant success, privileged-tool exposure, duplicate coordination, and failure-recovery rate.

Do not accept a team because one metric improved. The implemented gate requires either better success or materially lower privileged-tool exposure, while grounding and safety do not regress, cost stays within budget, and latency stays within its SLO.

Pareto analysis is preferable to one composite leaderboard. A single agent, pipeline, manager, handoff, and parallel topology may each be non-dominated on different dimensions. The workload decides; no architecture is a universal winner.

Three anchor decisions:

1. **Simple FAQ:** `KEEP_SINGLE`.
2. **Generate → security review → deterministic gate:** `PIPELINE`, not an open-ended team debate.
3. **Cross-domain incident with independent work, isolated credentials, and large separate contexts:** consider a split only after the measured gate passes.

## Architecture primitives before frameworks

Start with the primitive you need: graph/state machine, manager, handoff, agents-as-tools, shared state/blackboard, parallel fan-out, or message bus. A framework packages primitives; it does not make a topology safe by itself.

| Option | Useful primitives | Position in this course |
|---|---|---|
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) | Graph/state-machine orchestration and durable state | One widely used implementation option; deterministic routing alone is not authorization |
| [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/multi_agent/) | Manager/agents-as-tools, handoffs, code or model orchestration | Current optional adapter; manager and handoff semantics are explicitly different |
| [AutoGen AgentChat](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html) | Teams, group chat, selector, handoff messages | Brief preview; Advanced Course 02 covers AutoGen deeply |
| [CrewAI](https://docs.crewai.com/) | Agents/tasks/crews and flows | Brief preview; Advanced Course 03 covers CrewAI deeply |
| Message bus / blackboard | Decoupled events or shared artifact state | Useful when lifecycle or integration boundaries matter more than chat |

OpenAI Swarm is a historical experimental predecessor, not the current recommendation. The current Agents SDK documentation distinguishes a manager that retains control through agents-as-tools from handoffs that transfer the active conversation owner.

## State of practice and open problems

- **Established practice:** application-owned orchestration, typed tool/artifact contracts, explicit budgets, least privilege, trace-based evaluation, and deterministic gates for hard invariants.
- **Emerging practice:** deferred tool discovery, sandboxed specialists, richer handoff filters, and workload-aware mixes of code and model orchestration.
- **Research frontier:** adaptive topology selection, reliable credit assignment across cooperating components, adversarial coordination evaluation, and learned routing that remains calibrated under distribution shift.
- **Open problems:** comparing systems without label leakage, proving isolation across model/tool boundaries, predicting tail latency under shared limits, and deciding when extra coordination causally improves outcomes.

Framework features evolve faster than the architectural invariants in this lesson. Re-check current official documentation before adopting an adapter.

## Production upgrade path

| Teaching implementation | Production requirement |
|---|---|
| In-memory frozen evidence | Authenticated source adapters, tenant-scoped queries, freshness, lineage, and retention |
| Static capability tuples | Central policy engine, short-lived credentials, network policy, and sandbox enforcement |
| Deterministic fixture timings | Trace-derived distributions by workload, model, region, and failure slice |
| In-process fan-out | Bounded queues, rate-limit groups, cancellation, backpressure, and dependency-aware scheduling |
| Local artifact validation | Signed/attested provenance where needed, schema registry, content controls, and audit trail |
| Simple conflict detector | Source authority policy, bounded reconciliation, human review, and incident escalation |
| Fixed cost estimates | Admission reserves plus actual usage accounting and alerting |
| No production write | Separate propose/approve/execute boundary with idempotency and receipts |

## Run the lab

From the repository root:

```bash
python curriculum/advanced/01-single-vs-multi-agent/lab.py
pytest -q tests/test_single_vs_multi_agent.py
python scripts/execute-notebooks.py --timeout 90 curriculum/advanced/01-single-vs-multi-agent
```

The default path is credential-free, synthetic, deterministic, and read-only.

## Exercises

1. Add a new evidence domain and show when dynamic tool discovery remains sufficient.
2. Create a legal-but-risky explicit capability grant and specify who should approve it.
3. Add a rate-limit group shared by two specialists and recompute the critical path.
4. Introduce an evidence conflict and design a bounded reconciliation step.
5. Calibrate a semantic router on held-out cases without leaking expected labels.
6. Replace fixture measurements with sanitized production traces and re-run the Pareto analysis.

## Checkpoint

1. When is another LLM role not another agent?
2. In manager delegation versus handoff, who owns control?
3. Why does parallel work not imply linear wall-clock cost?
4. Why is an agent boundary not automatically a security boundary?
5. What is capability attenuation, and what attack does it prevent?
6. How is state lost during a handoff measured?
7. Why must a router support `MULTI_ROUTE` and `UNKNOWN`?
8. When is a deterministic pipeline better than a team?
9. Which metrics can justify an agent split, and which guardrails must not regress?
10. What does a Pareto-optimal architecture mean?

## Deep dives and references

- [The Cost of Coordination](THE_COST_OF_COORDINATION.md)
- [When to Split Agents](WHEN_TO_SPLIT_AGENTS.md)
- [Routing and Handoffs](ROUTING_AND_HANDOFFS.md)
- [OpenAI Agents SDK: orchestration](https://openai.github.io/openai-agents-python/multi_agent/)
- [OpenAI Agents SDK: handoffs](https://openai.github.io/openai-agents-python/handoffs/)
- [OpenAI Agents SDK: tools and agents-as-tools](https://openai.github.io/openai-agents-python/tools/)
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [AutoGen AgentChat teams](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html)
- [CrewAI documentation](https://docs.crewai.com/)
