# 10 — LangGraph state, persistence, and memory

**Level:** Intermediate · **Time:** 2–3 hours · **Scenario:** Northstar Cloud's
incident investigator must diagnose European checkout failures, survive a worker
restart, pause before a proposed rollback, and avoid carrying an unverified
diagnosis into the next incident.

| Learn | Build | Test |
| --- | --- | --- |
| [Chapter](README.md) | [Guided notebook](10_langgraph_state.ipynb) · [shared implementation](lab.py) | [32 policy invariants](../../../tests/test_langgraph_state_memory.py) |


## Why this topic matters

A plain tool loop holds its state in one process. That is often enough for a
short, read-only task. It fails as soon as the task must survive a restart, wait
for human approval, expose progress to a UI, or safely remember a preference
across separate conversations. LangGraph is a low-level orchestration runtime
for long-running stateful agents: it deliberately combines deterministic graph
steps with model-driven decisions rather than hiding both behind one agent
abstraction ([LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)).

The key distinction is not "memory or no memory." It is **what information is
allowed to persist, under which identity and scope, for how long, and how it is
validated before it influences an action**.

> **Persistence changes the safety model.** State can outlive the process, model
> call, deployment, policy version, credentials, and person who started the run.
> Resume is therefore a new authorization decision over old state—not a fresh run
> and not automatic permission to continue.

![Diagram](assets/diagram.svg)

## Learning outcomes

By the end you can:

1. Model an agent as a typed state machine: state schema, nodes, edges,
   conditional routes, and bounded loops.
2. Choose between a **checkpointer** (short-term, thread-scoped graph state) and
   a **store** (application-defined cross-thread data).
3. Resume safely after a failure or an approval interruption using a stable
   `thread_id` and idempotent side effects.
4. Stream state changes to an operator interface without exposing secrets or
   raw untrusted content.
5. Design long-term memory as a governed write → manage → retrieve subsystem,
   rather than a conversational transcript or vector database dump.
6. Detect schema, graph, policy, approval, retention, and replay changes before
   old state can influence current execution.

## The scenario and its boundaries

At 09:04, Northstar sees European checkout errors after `deploy-1842`. The
agent may read health, logs, and deployment facts. It may prepare a rollback
proposal, but it never restarts or rolls back production. A human operator is
the only actor permitted to authorize an external action.

**Non-goals:** this module does not teach model prompting, browser automation,
or persistent database setup. It teaches the execution substrate those systems
need once the task is stateful.

**Risk to design against:** a previous customer note says *"Checkout incidents
are usually Redis."* That is an unverified historical hunch, not evidence for a
new incident. The lab marks it unverified and excludes it from retrieval.

## 1. State is the contract between nodes

In a `StateGraph`, each node reads a state snapshot and returns a partial update.
The graph merges updates according to the state schema and its reducers. Keep
the schema deliberate:

| Field | Why it belongs in thread state | What not to put here |
| --- | --- | --- |
| `request`, `service` | identifies this investigation | unrelated user history |
| `evidence` | auditable inputs to the hypothesis | full raw logs indefinitely |
| `hypothesis`, `confidence` | makes routing inspectable | hidden chain-of-thought |
| `attempts_by_node`, tool/retry/replan/reflection budgets, deadline | bounds looping and cost across restart | a reset-on-resume counter |
| `pending_approval` | binds an immutable proposal to the pause | `approved=True` |
| `state_schema_version`, `graph_version`, `policy_version` | makes compatibility and revalidation explicit | credentials or bearer approval tokens |

```python
from typing import TypedDict
from langgraph.graph import END, START, StateGraph

class IncidentState(TypedDict):
    request: str
    evidence: list[dict]
    confidence: float
    attempts: int
    recommendation: str | None

builder = StateGraph(IncidentState)
builder.add_node("triage", triage)
builder.add_node("collect_evidence", collect_evidence)
builder.add_node("analyze", analyze)
builder.add_node("recommend", recommend)
builder.add_edge(START, "triage")
builder.add_edge("triage", "collect_evidence")
builder.add_conditional_edges("analyze", route_after_analysis,
                              {"collect_evidence": "collect_evidence", "recommend": "recommend"})
builder.add_edge("recommend", END)
```

Use deterministic code for authorization, budgets, routing thresholds, and tool
input validation. Reserve an LLM for genuinely ambiguous interpretation. This
makes the graph reviewable and lets evaluators assert a trajectory rather than
only inspect a final answer.

## 2. Conditional routing, retries, and bounded recovery

An agentic graph has loops, but no production loop should be open-ended. The lab
routes back to `collect_evidence` only while confidence is below `0.80` **and**
the independent-evidence budget remains. A robust route function also considers:

- a deadline and max node/tool-call count;
- a retry class: transient timeout vs invalid request vs permission denial;
- idempotency keys for any external side effect;
- a fallback terminal state such as `needs_human_review`.

![Diagram](assets/diagram_2.svg)

Do not retry a mutation blindly. If a node may be replayed, move its
non-idempotent side effect after the interrupt or record an idempotency key in
durable state. LangGraph's interrupt guidance specifically warns that code
before an interrupt runs again when the node resumes
([interrupt rules](https://docs.langchain.com/oss/python/langgraph/interrupts)).

## 3. Checkpoints are short-term memory, not a user profile

A checkpointer writes graph state snapshots under a `thread_id`. That gives a
run continuity, fault tolerance, inspection/time-travel capabilities, and a
place to resume an approval pause. A production run must use a durable backend:
the in-memory saver is useful for development but disappears on process restart
([persistence guide](https://docs.langchain.com/oss/python/langgraph/persistence)).

```python
from langgraph.checkpoint.memory import InMemorySaver

checkpointer = InMemorySaver()  # development only
graph = builder.compile(checkpointer=checkpointer)
config = {"configurable": {"thread_id": "incident-eu-1842"}}
graph.invoke({"request": "Investigate EU checkout"}, config=config)
```

For production, use a persistent checkpointer, bound checkpoint retention, and
make `thread_id` an opaque, authorization-checked identifier. The persistence
documentation distinguishes SQLite for local development and durable
alternatives such as PostgreSQL for production. Never derive it directly from
an email address, tenant name, or other exposed identifier.

### Durable recovery experiment

The notebook runs two separate Python processes against one temporary SQLite
repository. Process one saves health and logs, then exits. Process two creates a
new repository and runtime, authorizes the same thread, and resumes from the
stored state. It proves that completed reads are skipped and that `3/5` remaining
calls become `2/5` only after the next new tool call. Retry, deadline, replan, and
reflection budgets live in the same persisted state.

This is the difference between recovering a state machine and simply starting a
fresh chat with a pasted summary.

`SQLiteCheckpointRepository` is a framework-neutral teaching implementation.
It stores typed JSON plus execution metadata and a digest, enforces a checkpoint
size budget, and keeps historical records immutable. `InMemorySaver` is useful
for development/tests and is lost with the process; it is not SQLite. LangGraph's
SQLite and PostgreSQL savers are separate packages/backends.

### Replay-safe reducers and evidence independence

Evidence is compact: stable ID, source/version, timestamp, summary, artifact
handle, hash, and correlation group. Large logs stay behind the handle. The
reducer de-duplicates by `evidence_id` and rejects the same ID with a different
hash. Confidence uses independent correlation groups, so replaying one log item
cannot masquerade as corroboration.

Conflicting independent root-cause evidence terminates as
`NEEDS_HUMAN_REVIEW`. Other explicit terminal states are `COMPLETED`,
`INTERRUPTED`, `BUDGET_EXHAUSTED`, `MIGRATION_REQUIRED`, `CANCELLED`, and
`FAILED`.

## 4. Long-term memory needs a write policy

LangGraph separates checkpointers from **stores**. A store holds
application-defined data across threads; it is appropriate for user preferences,
verified facts, or shared knowledge, not for every message or transient
hypothesis ([checkpointer vs store](https://docs.langchain.com/oss/python/langgraph/persistence)).

| Memory class | Example | Write rule | Retrieval rule |
| --- | --- | --- | --- |
| Working / short-term | evidence gathered today | graph node | current `thread_id` only |
| Episodic | approved incident postmortem | reviewed, retention-limited | semantic + temporal match |
| Semantic | reviewed fact about a stable service property | reviewed source with provenance | tenant/subject namespace + relevance |
| Preference | concise incident-update preference | user confirmed | exact tenant + subject |
| Procedural | verified rollback checklist version | change-controlled artifact | explicit version and access policy |

![Diagram](assets/diagram_3.svg)

The lab’s `GovernedMemoryStore.retrieve()` excludes an unverified Redis hunch
even though it has high semantic relevance. It also excludes cross-tenant,
expired, superseded, and deleted records. A memory write is accepted only from a
permitted origin: user-confirmed preference, reviewed postmortem, or approved
procedure. Retrieved instructions, model hypotheses, authorization claims, and
temporary credentials are denied. Memory may influence presentation or search
context; it does not become current-incident evidence without independent
verification for this run.

### Memory experiment

1. Store a verified Northstar preference: “prioritize clear impact updates.”
2. Store an unverified claim: “Checkout problems are usually Redis.”
3. Run the same evidence collection twice—once with the verification gate, once
   after deliberately bypassing it.
4. Explain why the evidence-backed deployment hypothesis should win, and add a
   policy test that prevents the hunch entering the prompt/context.

## 5. Human interrupts and stateful approval

An interrupt pauses a graph at a dynamic point, persists state, and resumes when
the caller supplies a JSON-serializable response. The resume call must use the
same `thread_id`. This is a control-flow feature, not authorization by itself:
your application must still enforce who can approve what.

```python
from langgraph.types import Command, interrupt

def approval_node(state):
    decision = interrupt(state["safe_review_payload"])
    return {"approval_decision": decision}

# The application authenticates the approver and validates the typed decision.
graph.invoke(Command(resume=validated_decision), config=same_thread_config)
```

The notebook simulates this with `pending_approval`; the code block above shows
the direct LangGraph equivalent. The correct production sequence is:

1. validate the proposal and required evidence;
2. checkpoint the proposal and a non-sensitive review payload;
3. authenticate and authorize the approver in the application layer;
4. verify tenant, approver, action, target, proposal digest, policy version, and
   expiry against the current state;
5. resume the same authorized thread with `Command(resume=...)`;
6. perform an idempotent external action only after approval.

The core lab stops after validation and never executes rollback. A decision for
`deploy-1842` cannot authorize a changed `deploy-1843` proposal; an expired
decision or a decision arriving after `CANCELLED` is denied. Course 03 owns the
full executor boundary.

### Replay and time travel

LangGraph resumes an interrupted node from its beginning, so code before
`interrupt()` can run again. The notebook contrasts a bad pre-interrupt side
effect, which commits twice, with a deterministic receipt fixture: the
`logical_operation_id` remains stable across resume/retry, while every
`attempt_id` is unique. See Courses 01 and 03 for the complete executor pattern.

Time travel is a **fork**, not an edit to historical state. A fork records its
`parent_checkpoint_id`, receives a new thread/run identity, clears stale
approval, and defaults to `REPLAY`/`DRY_RUN`, where external writes are disabled.
Moving a historical fork to `LIVE` requires fresh policy and approval checks.

### Version and retention boundaries

Every resume compares `state_schema_version`, `graph_version`, and the current
application policy. A schema-v1 checkpoint under a schema-v2 runtime returns
`MIGRATION_REQUIRED`; an incompatible graph version is rejected; a changed
policy must be revalidated. Expired or deleted records are unavailable to normal
retrieval, while audit-retained records cannot be casually deleted. Acquire
fresh credentials at resume time—never serialize API tokens, passwords,
temporary cloud credentials, or bearer approval tokens.

## 6. Streaming, observability, and evaluation

Streaming is useful for an operator console when it exposes intentional events:
node name, safe state projection, tool status, interrupt payload, timing, and
budget. Do not stream raw credentials, unredacted logs, or hidden reasoning.
LangGraph supports streamed values, updates, messages, custom events,
checkpoints, tasks, and debug data; pair
that with trace/evaluation tooling to inspect the trajectory
([streaming guide](https://docs.langchain.com/oss/python/langgraph/streaming)).

The shared `StreamEvent` records run/thread/checkpoint correlation, sequence,
timestamp, node, and type. `project_stream_event()` exposes only node, status,
elapsed time, and a safe summary. Raw logs, PII, credentials, and hidden reasoning
remain outside the UI projection.

For the Northstar system, test more than the final diagnosis:

| Dimension | Test |
| --- | --- |
| Outcome | diagnosis names the deployment only when independent evidence supports it |
| Trajectory | health → logs → deployment; no production action tool is invoked |
| Recovery | a restart resumes from the latest checkpoint without duplicate work |
| Memory | an unverified or cross-tenant item cannot alter the evidence set |
| HITL | approval/rejection is persisted, authenticated, and replay-safe |
| Operations | node count, latency, checkpoint size, retries, and cost remain within budget |

The executable evaluation derives resume success, duplicate-work,
replay-side-effect, memory-contamination, cross-tenant-memory, stale-approval,
and stream-redaction rates from observed counters. Checkpoint latency and size
come from actual saves. The baseline experiment shows why persistence is not
free but avoids repeating completed tool calls, cost, and latency after restart.

## Main LangGraph capabilities applied here

| Capability | What it solves in this scenario | Use it carefully |
| --- | --- | --- |
| `StateGraph`, nodes, edges | explicit, testable orchestration | keep state small and typed |
| conditional edges | evidence loop and terminal routing | add attempt/deadline caps |
| checkpointers | recovery and thread continuity | durable backend + retention policy |
| store | verified cross-thread preferences/facts | namespace, provenance, deletion |
| interrupts + `Command` | approval and review/edit pauses | idempotent pre-interrupt code |
| streaming | operator progress and UI | redact and project only safe fields |
| subgraphs | encapsulate a specialist workflow | define state/store boundary explicitly |

### Technology landscape

| Need | Development/local | Durable production direction |
| --- | --- | --- |
| Thread checkpoints | `InMemorySaver` | PostgreSQL or another supported persistent checkpointer |
| Local durable experiment | separate `langgraph-checkpoint-sqlite` saver | treat SQLite as local/single-process infrastructure |
| Cross-thread store | `InMemoryStore` | PostgreSQL, MongoDB, Redis, or an application store with explicit namespaces |
| Serialization | typed state and default safe serializer | encrypted serializer, key management, migrations, and restore tests |

Storage mechanism does not define memory semantics: long-term memory may use KV,
relational, document, vector, or hybrid retrieval. Namespace and authorize it by
tenant, subject, and memory type. Production storage also needs encryption at
rest and in transit, RBAC, tenant isolation, backup/restore, retention,
deletion, and immutable audit evidence.

Established practice is durable state plus idempotent operations. Current
LangGraph packages add pluggable checkpointers/stores, interrupts, history, and
typed streaming. Emerging work explores automated memory extraction and tiered
memory, but the open problem is still governance: relevance alone does not prove
truth, scope, consent, or authority.

## Exercises

1. Change a replayed evidence hash while keeping its ID and explain why the
   reducer fails closed.
2. Write an explicit state-v1 → state-v2 migration and preserve the old record.
3. Replace the optional in-memory adapter with `SqliteSaver`; document setup,
   concurrency, retention, and deletion limits.
4. Add a reviewed procedural memory and prove it still cannot authorize action.
5. Add a second independent conflicting source and inspect the terminal route
   and redacted event stream.

## Watch For

- **Thread hijacking:** an opaque `thread_id` is mistaken for authorization.
- **Schema/graph drift:** old state resumes under incompatible code.
- **Replayed side effects:** pre-interrupt code commits twice.
- **Stale approval:** target, evidence, policy, expiry, or approver no longer matches.
- **Memory poisoning:** a hypothesis or retrieved instruction becomes durable truth.
- **Cross-tenant memory:** a namespace omits tenant/subject/type boundaries.
- **Checkpoint growth:** raw logs or transcripts exceed storage budgets.
- **Persisted secrets:** old credentials survive past their intended lifetime.
- **Changed policy:** a resume inherits authority that no longer exists.

## Checkpoint

1. How does a checkpoint differ from long-term memory?
2. Why does knowing a `thread_id` not authorize checkpoint access?
3. Why can code before a LangGraph interrupt execute again?
4. What stays stable, and what changes, across a logical retry?
5. Why are historical checkpoints immutable and time travel represented as a fork?
6. What should happen when state-schema or graph versions change?
7. Why can a model hypothesis not automatically become durable memory?
8. How do tenant, subject, and memory-type namespaces constrain retrieval?
9. Why must policy and credentials be revalidated on resume?
10. What data must never appear in persisted state or operator streams?

## References

- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph persistence: checkpointers and stores](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph memory concepts](https://docs.langchain.com/oss/python/concepts/memory)
- [LangGraph interrupts and resume semantics](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph streaming](https://docs.langchain.com/oss/python/langgraph/streaming)
- [LangGraph time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel)
- [LangGraph workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
- [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560) — useful context for tiered memory, not a substitute for access control or data governance.

## Further Deep Dives

- [LangGraph checkpointers, recovery, and safe time travel](DEEP_DIVE_CHECKPOINTERS.md)
