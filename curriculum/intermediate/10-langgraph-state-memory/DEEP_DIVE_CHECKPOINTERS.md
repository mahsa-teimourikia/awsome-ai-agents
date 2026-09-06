# Deep dive: LangGraph checkpointers, recovery, and safe time travel

A checkpointer persists **serialized graph state plus execution metadata** for a
thread. LangGraph writes checkpoints at graph-step boundaries (super-steps), not
at an unconditional “every edge” hook. Pending writes can also preserve
successful work from a partially failed step. Exact behavior depends on the
graph, checkpointer, and execution path.

## Persistence changes the trust boundary

A durable checkpoint can outlive the worker, model call, deploy, policy, user
session, and credentials that created it. A resume therefore needs all of these:

- authenticated tenant, user, and thread ownership;
- current read/resume authorization;
- retention/deletion eligibility and an intact state digest;
- compatible state-schema and graph versions, or an explicit migration;
- current policy validation and fresh runtime credentials; and
- a replay-safe plan for code that may execute again.

The thread ID selects persistent execution state. It is not a bearer token. An
attacker who guesses or obtains a thread ID must still fail the application’s
tenant, owner, scope, and retention checks.

## Checkpointer choices

| Backend | Appropriate use | Important boundary |
| --- | --- | --- |
| `InMemorySaver` | tests and local experimentation | state disappears with the process; it is not SQLite |
| `SqliteSaver` / `AsyncSqliteSaver` | local durable workflows | separate `langgraph-checkpoint-sqlite` package; limited production concurrency |
| `PostgresSaver` / `AsyncPostgresSaver` | durable production workflows | separate package, setup/migrations, pooling, backup, and access control |
| custom `BaseCheckpointSaver` | organization-specific storage | implement checkpoint, intermediate-write, list/history, serialization, and async contracts correctly |

LangGraph’s default `JsonPlusSerializer` uses msgpack/JSON-compatible handling
for supported types. Do not enable arbitrary untrusted object deserialization.
Where required, use an encrypted serializer and manage keys outside graph state.

## Recovery is persisted execution state, not “resume at line 8”

Recovery loads the latest authorized checkpoint and continues from the runtime’s
persisted execution state. It is subject to graph scheduling and replay
semantics; it does not promise to resume at a Python source line. Completed work
is avoided only when the graph state and side-effect design make that work
observable and idempotent.

The Course 10 lab proves this with two OS processes and one temporary SQLite
repository:

1. process A reads health and logs, checkpointing after each node;
2. process A exits, destroying its runtime and database connection;
3. process B creates a new repository and runtime;
4. the application authorizes and loads the same thread;
5. completed health/log reads are skipped, while the deployment read consumes
   the next persisted budget unit.

Retry counters, deadlines, tool budgets, and replan/reflection budgets must live
inside persisted state. Otherwise a restart becomes a way to evade limits.

## Interrupts pause control flow; they do not grant authority

Current LangGraph human-in-the-loop flow uses `interrupt(...)` inside a node and
`Command(resume=...)` with the same configured thread ID:

```python
from langgraph.types import Command, interrupt

def approval_node(state):
    decision = interrupt(state["safe_review_payload"])
    return {"approval_decision": decision}

graph.invoke(initial_state, config=thread_config)
graph.invoke(Command(resume=validated_decision), config=thread_config)
```

The application must authenticate the approver and validate a structured
decision against the current proposal. Bind tenant, action, target, proposal
digest, policy version, expiry, and approver. Never treat `approved=True` or
knowledge of the thread ID as sufficient authority.

An interrupted node restarts from its beginning on resume. Code before
`interrupt()` can execute again. Keep pre-interrupt work pure or idempotent; put
consequential writes behind validated approval and a stable logical operation
ID. Attempt IDs remain unique for observability, while the logical operation ID
stays stable across retries and resumes.

## Versioned execution-state history and forks

Checkpoint history is an immutable lineage of execution-state records. Do not
describe it as the agent’s “brain,” and do not edit a historical record in place.
Time travel creates a fork:

```text
checkpoint A → checkpoint B → checkpoint C
                   └────────→ fork checkpoint D (REPLAY / DRY_RUN)
```

The fork records `parent_checkpoint_id`, has a new thread/run identity, clears
old approval, and disables external writes by default. Live execution from a
historical point requires current policy validation and a newly bound approval.
With LangGraph, history is available through `get_state_history()` and historical
configs can be used to replay or fork; interrupts on the replayed path occur
again.

## Checkpoints and stores solve different problems

| Property | Checkpointer | Long-term store |
| --- | --- | --- |
| Primary purpose | resume one thread’s execution | retrieve governed data across threads |
| Typical namespace | authorized `thread_id` | tenant + subject + memory type |
| Contents | typed graph state and execution metadata | verified preferences, episodes, facts, or procedures |
| Lifecycle | checkpoint retention and lineage | provenance, verification, expiry, supersession, deletion |
| Storage options | memory, SQLite, PostgreSQL, other savers | KV, relational, document, vector, or hybrid |

A vector database is one retrieval mechanism, not the definition of long-term
memory. Semantic memory is never globally available merely because it embeds
well. Every query still needs namespace and authorization checks.

## Production checklist

- Keep raw logs and large payloads in artifact storage; persist handles, hashes,
  summaries, and provenance.
- Enforce checkpoint-size budgets and externalize oversized state.
- Encrypt storage and transport; apply RBAC and tenant isolation.
- Test migrations, backup/restore, retention, deletion, and audit controls.
- Reacquire secrets and short-lived credentials on resume.
- Emit correlated events but project only safe node/status/timing summaries to
  users.
- Measure resume success, duplicate work, replayed side effects, save latency,
  state size, memory contamination, cross-tenant results, stale approvals, and
  stream-redaction failures.

## Official references

- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel)
- [LangGraph streaming](https://docs.langchain.com/oss/python/langgraph/streaming)
