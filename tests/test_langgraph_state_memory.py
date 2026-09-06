"""Course 10 invariants: persisted state is governed, not ambient authority."""

from __future__ import annotations

from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError


COURSE_DIR = (
    Path(__file__).resolve().parents[1]
    / "curriculum"
    / "intermediate"
    / "10-langgraph-state-memory"
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


policy = _load("course10_policy", COURSE_DIR / "policy.py")
previous_policy = sys.modules.get("policy")
sys.modules["policy"] = policy
lab = _load("course10_lab", COURSE_DIR / "lab.py")
if previous_policy is None:
    sys.modules.pop("policy", None)
else:
    sys.modules["policy"] = previous_policy


@pytest.fixture
def context():
    return lab.build_context()


@pytest.fixture
def repository(tmp_path):
    repo = policy.SQLiteCheckpointRepository(tmp_path / "checkpoints.sqlite")
    yield repo
    repo.close()


@pytest.fixture
def interrupted_runtime(repository, context):
    runtime = lab.IncidentRuntime(repository, context)
    runtime.run_to_interrupt()
    return runtime


def preference_request(
    *,
    version: int,
    expires_in_days: int,
    supersedes: str | None = None,
    source_id: str = "user-confirmation-17",
) -> policy.MemoryWriteRequest:
    return policy.MemoryWriteRequest(
        tenant_id="northstar",
        subject_id="checkout-eu",
        memory_type=policy.MemoryType.PREFERENCE,
        content=f"Preference version {version}.",
        source_id=source_id,
        source_version=f"v{version}",
        origin=policy.MemoryOrigin.USER_CONFIRMED,
        verified=True,
        verified_by="operator-17",
        expires_at=lab.FIXED_TIME + timedelta(days=expires_in_days),
        sensitivity=policy.Sensitivity.INTERNAL,
        version=version,
        supersedes=supersedes,
    )


def test_contracts_forbid_extra_fields_and_state_is_frozen():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        policy.ThreadContext(
            tenant_id="northstar",
            user_id="operator-17",
            thread_id="thread-1",
            authorization_scope=("checkpoint:read",),
            policy_version="policy-v7",
            model_owned_tenant="globex",
        )
    state = policy.IncidentState(
        request="test",
        service="checkout-eu",
        remaining_budget=1,
        deadline_at=lab.FIXED_TIME + timedelta(hours=1),
        logical_operation_id="operation-1",
        run_id="run-1",
    )
    with pytest.raises(ValidationError, match="frozen"):
        state.remaining_budget = 99


def test_same_authorized_thread_resumes(repository, context):
    runtime = lab.IncidentRuntime(repository, context)
    runtime.start()
    runtime.read_health()
    runtime.read_logs()
    before = runtime.state

    reconstructed = lab.IncidentRuntime(repository, context)
    after = reconstructed.resume_existing()

    assert after.last_checkpoint_id == before.last_checkpoint_id
    assert after.tool_call_count == 2
    assert after.remaining_budget == 3


def test_thread_id_is_not_authority_across_tenants(tmp_path):
    repo = policy.SQLiteCheckpointRepository(tmp_path / "tenant.sqlite")
    globex = lab.build_context(
        tenant_id="globex",
        user_id="globex-user",
        thread_id="thread-globex-incident",
    )
    runtime = lab.IncidentRuntime(repo, globex)
    runtime.start()
    attacker = lab.build_context(
        tenant_id="northstar",
        user_id="globex-user",
        thread_id="thread-globex-incident",
    )

    with pytest.raises(policy.PolicyError, match="CHECKPOINT_NOT_FOUND_OR_DENIED"):
        repo.load(attacker, now=lab.FIXED_TIME)
    repo.close()


def test_wrong_user_and_missing_scope_are_denied(repository, context):
    lab.IncidentRuntime(repository, context).start()
    wrong_user = lab.build_context(user_id="operator-99")
    no_read = lab.build_context(scopes=("checkpoint:write",))

    with pytest.raises(policy.PolicyError, match="CHECKPOINT_NOT_FOUND_OR_DENIED"):
        repository.load(wrong_user, now=lab.FIXED_TIME)
    with pytest.raises(policy.PolicyError, match="AUTHORIZATION_SCOPE_DENIED"):
        repository.load(no_read, now=lab.FIXED_TIME)


def test_real_process_reconstruction_resumes_without_repeating_work(tmp_path):
    result = lab.run_process_restart_experiment(tmp_path / "restart.sqlite")

    assert result.phase_one_pid != result.phase_two_pid
    assert result.calls_before_restart == 2
    assert result.calls_after_resume == 3
    assert result.repeated_completed_nodes == ()
    assert result.completed_before_restart == ("read-health", "read-logs")
    assert "read-deployment" in result.completed_after_resume


def test_evidence_reducer_and_confidence_are_replay_safe():
    item = lab.logs_evidence()
    once = lab.merge_evidence((), (item,))
    twice = lab.merge_evidence(once, (item,))

    assert twice == once
    assert len(twice) == 1
    assert lab.confidence_from_independent_evidence(once, "deploy-1842") == 0.5
    assert lab.confidence_from_independent_evidence(twice, "deploy-1842") == 0.5


def test_budget_survives_resume(repository, context):
    runtime = lab.IncidentRuntime(repository, context)
    runtime.start(tool_budget=5)
    runtime.read_health()
    runtime.read_logs()

    reconstructed = lab.IncidentRuntime(repository, context)
    state = reconstructed.resume_existing()
    assert state.tool_call_count == 2
    assert state.remaining_budget == 3
    reconstructed.read_deployment()
    assert reconstructed.state.remaining_budget == 2


def test_retry_history_survives_resume(repository, context):
    runtime = lab.IncidentRuntime(repository, context)
    runtime.start()
    runtime.read_logs(timeout=True)
    assert runtime.state.retry_budget_remaining == 1

    reconstructed = lab.IncidentRuntime(repository, context)
    reconstructed.resume_existing()
    reconstructed.read_logs(timeout=True)
    attempts = {item.node: item.attempts for item in reconstructed.state.attempts_by_node}
    assert reconstructed.state.retry_budget_remaining == 0
    assert attempts["read-logs"] == 2


def test_interrupt_checkpoints_structured_approval(interrupted_runtime):
    state = interrupted_runtime.state
    assert state.terminal_status == policy.TerminalStatus.INTERRUPTED
    assert isinstance(state.pending_approval, policy.ApprovalProposal)
    assert state.pending_approval.target == "deploy-1842"
    assert "approval-validated" not in state.completed_nodes


def test_approval_is_bound_to_exact_target(interrupted_runtime):
    proposal = interrupted_runtime.state.pending_approval
    decision = lab.build_decision(proposal)
    revised = interrupted_runtime.revise_proposal_target("deploy-1843")

    assert revised.target == "deploy-1843"
    assert revised.digest != proposal.digest

    with pytest.raises(policy.PolicyError, match="APPROVAL_TARGET_MISMATCH"):
        interrupted_runtime.resume_with_approval(
            decision,
            lab.build_approver(),
            now=lab.FIXED_TIME + timedelta(minutes=10),
        )


def test_expired_approval_is_denied(interrupted_runtime):
    proposal = interrupted_runtime.state.pending_approval
    decision = lab.build_decision(proposal)

    with pytest.raises(policy.PolicyError, match="APPROVAL_EXPIRED"):
        interrupted_runtime.resume_with_approval(
            decision,
            lab.build_approver(),
            now=proposal.expires_at + timedelta(seconds=1),
        )


def test_approval_decision_cannot_predate_proposal(interrupted_runtime):
    proposal = interrupted_runtime.state.pending_approval
    decision = lab.build_decision(
        proposal, decided_at=proposal.created_at - timedelta(seconds=1)
    )

    with pytest.raises(policy.PolicyError, match="APPROVAL_DECISION_BEFORE_PROPOSAL"):
        policy.validate_approval_decision(
            interrupted_runtime.state,
            decision,
            lab.build_approver(),
            interrupted_runtime.context,
            now=proposal.created_at,
        )


def test_approval_decision_cannot_be_issued_after_expiry(interrupted_runtime):
    proposal = interrupted_runtime.state.pending_approval
    decision = lab.build_decision(
        proposal, decided_at=proposal.expires_at + timedelta(seconds=1)
    )

    with pytest.raises(policy.PolicyError, match="APPROVAL_DECISION_AFTER_EXPIRY"):
        policy.validate_approval_decision(
            interrupted_runtime.state,
            decision,
            lab.build_approver(),
            interrupted_runtime.context,
            now=proposal.expires_at + timedelta(seconds=2),
        )


def test_approval_decision_cannot_be_from_the_future(interrupted_runtime):
    proposal = interrupted_runtime.state.pending_approval
    decision = lab.build_decision(
        proposal, decided_at=lab.FIXED_TIME + timedelta(minutes=10)
    )

    with pytest.raises(policy.PolicyError, match="APPROVAL_DECISION_IN_FUTURE"):
        policy.validate_approval_decision(
            interrupted_runtime.state,
            decision,
            lab.build_approver(),
            interrupted_runtime.context,
            now=lab.FIXED_TIME + timedelta(minutes=9),
        )


def test_wrong_approver_is_denied(interrupted_runtime):
    proposal = interrupted_runtime.state.pending_approval
    decision = lab.build_decision(proposal)

    with pytest.raises(policy.PolicyError, match="APPROVER_ID_MISMATCH"):
        interrupted_runtime.resume_with_approval(
            decision,
            lab.build_approver(approver_id="commander-9"),
            now=lab.FIXED_TIME + timedelta(minutes=10),
        )


def test_cancelled_run_cannot_resume_with_late_approval(interrupted_runtime):
    proposal = interrupted_runtime.state.pending_approval
    decision = lab.build_decision(proposal)
    interrupted_runtime.cancel()

    with pytest.raises(policy.PolicyError, match="CANCELLED_RUN"):
        interrupted_runtime.resume_with_approval(
            decision,
            lab.build_approver(),
            now=lab.FIXED_TIME + timedelta(minutes=10),
        )


def test_valid_approval_completes_handoff_but_executes_nothing(interrupted_runtime):
    proposal = interrupted_runtime.state.pending_approval
    decision = lab.build_decision(proposal)
    state = interrupted_runtime.resume_with_approval(
        decision,
        lab.build_approver(),
        now=lab.FIXED_TIME + timedelta(minutes=10),
    )

    assert state.terminal_status == policy.TerminalStatus.APPROVED_FOR_EXECUTION
    assert state.pending_approval is None
    assert state.external_action_receipt_id is None
    assert state.approval_audit.decision_id == decision.decision_id
    assert state.approval_audit.validated_approver_id == "commander-8"
    assert "approval-validated" in state.completed_nodes
    reloaded = interrupted_runtime.repository.load(
        interrupted_runtime.context,
        state.last_checkpoint_id,
        now=lab.FIXED_TIME + timedelta(minutes=10),
    )
    assert reloaded.state.approval_audit == state.approval_audit
    assert reloaded.state.external_action_receipt_id is None


def test_resume_revalidates_policy_version(repository, context):
    lab.IncidentRuntime(repository, context).start()
    changed_policy = lab.build_context(policy_version="policy-v8")

    with pytest.raises(policy.PolicyError, match="POLICY_REVALIDATION_REQUIRED"):
        repository.load(changed_policy, now=lab.FIXED_TIME)


def test_schema_and_graph_mismatches_are_explicit(repository, context):
    lab.IncidentRuntime(repository, context).start()

    with pytest.raises(policy.PolicyError, match="MIGRATION_REQUIRED"):
        repository.load(
            context,
            expected_state_schema_version="state-v3",
            now=lab.FIXED_TIME,
        )
    with pytest.raises(policy.PolicyError, match="GRAPH_VERSION_MISMATCH"):
        repository.load(
            context,
            expected_graph_version="graph-v5",
            now=lab.FIXED_TIME,
        )


def test_historical_checkpoints_are_immutable_and_linked(repository, context):
    runtime = lab.IncidentRuntime(repository, context)
    first = runtime.start()
    first_record = repository.load(context, first.last_checkpoint_id, now=lab.FIXED_TIME)
    first_json = first_record.record.model_dump_json()
    runtime.read_health()
    history = repository.history(context, now=lab.FIXED_TIME)

    assert history[1].parent_checkpoint_id == history[0].checkpoint_id
    reloaded = repository.load(context, first.last_checkpoint_id, now=lab.FIXED_TIME)
    assert reloaded.record.model_dump_json() == first_json


def test_time_travel_creates_replay_fork_and_disables_writes(repository, context):
    runtime = lab.IncidentRuntime(repository, context)
    state = runtime.start()
    fork_context = lab.build_context(thread_id="thread-northstar-eu-fork")
    fork = repository.fork(
        context,
        state.last_checkpoint_id,
        fork_context,
        now=lab.FIXED_TIME,
    )
    executor = policy.IdempotentMockExecutor()
    receipt = executor.execute(
        fork.state.logical_operation_id,
        "attempt-replay-fork",
        mode=fork.state.execution_mode,
    )

    assert fork.record.parent_checkpoint_id == state.last_checkpoint_id
    assert fork.state.execution_mode == policy.ExecutionMode.REPLAY
    assert receipt.status == policy.ReceiptStatus.DRY_RUN
    assert executor.external_commit_count == 0


def test_replay_uses_stable_logical_identity_and_unique_attempts():
    result = policy.run_replay_experiment()
    assert result.logical_operation_id == "rollback-northstar-deploy-1842"
    assert len(set(result.attempt_ids)) == 2
    assert result.bad_external_commit_count == 2
    assert result.safe_external_commit_count == 1


def test_unverified_cross_tenant_and_expired_memory_are_excluded(context):
    store = policy.GovernedMemoryStore()
    lab.seed_memory_store(store, context)
    results = store.retrieve(context, subject_id="checkout-eu", now=lab.FIXED_TIME)
    ids = {item.memory_id for item in results}

    assert "memory-redis-hunch" not in ids
    assert "memory-globex-preference" not in ids
    assert "memory-expired" not in ids


def test_memory_supersession_returns_only_active_version(context):
    store = policy.GovernedMemoryStore()
    old, current = lab.seed_memory_store(store, context)
    results = store.retrieve(
        context,
        subject_id="checkout-eu",
        memory_types=(policy.MemoryType.PREFERENCE,),
        now=lab.FIXED_TIME,
    )

    assert current in results
    assert old not in results
    assert [item.version for item in results] == [2]


def test_supersession_is_permanent_after_successor_expiry_or_delete(context):
    verifier = lab.build_verifier()

    expired_store = policy.GovernedMemoryStore()
    assert (
        expired_store.policy.supersession_policy
        == policy.SupersessionPolicy.PERMANENT
    )
    old = expired_store.write(
        context,
        preference_request(version=1, expires_in_days=90),
        verifier=verifier,
        now=lab.FIXED_TIME,
        memory_id="memory-permanent-old-expiry",
    )
    expired_store.write(
        context,
        preference_request(
            version=2, expires_in_days=1, supersedes=old.memory_id
        ),
        verifier=verifier,
        now=lab.FIXED_TIME,
        memory_id="memory-permanent-new-expiry",
    )
    assert expired_store.retrieve(
        context,
        subject_id="checkout-eu",
        now=lab.FIXED_TIME + timedelta(days=2),
    ) == ()

    deleted_store = policy.GovernedMemoryStore()
    old = deleted_store.write(
        context,
        preference_request(version=1, expires_in_days=90),
        verifier=verifier,
        now=lab.FIXED_TIME,
        memory_id="memory-permanent-old-delete",
    )
    current = deleted_store.write(
        context,
        preference_request(
            version=2, expires_in_days=90, supersedes=old.memory_id
        ),
        verifier=verifier,
        now=lab.FIXED_TIME,
        memory_id="memory-permanent-new-delete",
    )
    deleted_store.delete(context, current.memory_id, now=lab.FIXED_TIME)
    assert deleted_store.retrieve(
        context, subject_id="checkout-eu", now=lab.FIXED_TIME
    ) == ()


def test_supersession_rejects_malformed_or_branched_lineage(context):
    store = policy.GovernedMemoryStore()
    verifier = lab.build_verifier()
    old = store.write(
        context,
        preference_request(version=1, expires_in_days=90),
        verifier=verifier,
        now=lab.FIXED_TIME,
        memory_id="memory-lineage-v1",
    )
    with pytest.raises(policy.PolicyError, match="INVALID_MEMORY_SUPERSESSION"):
        store.write(
            context,
            preference_request(
                version=3,
                expires_in_days=90,
                supersedes=old.memory_id,
                source_id="different-source",
            ),
            verifier=verifier,
            now=lab.FIXED_TIME,
        )
    store.write(
        context,
        preference_request(version=2, expires_in_days=90, supersedes=old.memory_id),
        verifier=verifier,
        now=lab.FIXED_TIME,
        memory_id="memory-lineage-v2",
    )
    with pytest.raises(policy.PolicyError, match="MEMORY_LINEAGE_ALREADY_SUPERSEDED"):
        store.write(
            context,
            preference_request(version=2, expires_in_days=90, supersedes=old.memory_id),
            verifier=verifier,
            now=lab.FIXED_TIME,
        )


def test_memory_poisoning_is_denied(context):
    store = policy.GovernedMemoryStore()
    with pytest.raises(policy.PolicyError, match="MEMORY_WRITE_DENIED"):
        store.write(
            context,
            lab.memory_poisoning_request(),
            verifier=lab.build_verifier(),
            now=lab.FIXED_TIME,
        )


def test_memory_boolean_cannot_self_certify_without_trusted_verifier(context):
    store = policy.GovernedMemoryStore()
    request = lab.self_certified_memory_request()

    with pytest.raises(policy.PolicyError, match="MEMORY_VERIFIER_REQUIRED"):
        store.write(context, request, now=lab.FIXED_TIME)
    assert store.retrieve(
        context, subject_id="checkout-eu", now=lab.FIXED_TIME
    ) == ()


def test_deleted_memory_is_unavailable(context):
    store = policy.GovernedMemoryStore()
    _old, current = lab.seed_memory_store(store, context)
    store.delete(context, current.memory_id, now=lab.FIXED_TIME)
    results = store.retrieve(context, subject_id="checkout-eu", now=lab.FIXED_TIME)
    assert current.memory_id not in {item.memory_id for item in results}


def test_memory_soft_delete_requires_explicit_physical_purge(context):
    store = policy.GovernedMemoryStore()
    old, current = lab.seed_memory_store(store, context)
    store.delete(context, current.memory_id, now=lab.FIXED_TIME)

    assert current.memory_id in store._records
    with pytest.raises(policy.PolicyError, match="AUTHORIZATION_SCOPE_DENIED"):
        store.purge_expired_or_deleted(context, now=lab.FIXED_TIME)
    purged = store.purge_expired_or_deleted(
        lab.build_retention_context(), now=lab.FIXED_TIME
    )
    assert purged == 2  # the soft-deleted preference and seeded expired memory
    assert current.memory_id not in store._records
    assert "memory-expired" not in store._records
    assert old.memory_id in store._records  # retained bytes, but permanently superseded
    assert store.retrieve(
        context, subject_id="checkout-eu", now=lab.FIXED_TIME
    ) == ()


def test_memory_cannot_become_incident_evidence_implicitly(repository, context):
    runtime = lab.IncidentRuntime(repository, context)
    runtime.start()
    runtime.read_health()
    store = policy.GovernedMemoryStore()
    lab.seed_memory_store(store, context)
    memories = store.retrieve(context, subject_id="checkout-eu", now=lab.FIXED_TIME)
    projected = lab.incident_context(runtime.state, memories)

    assert projected["memory_used_as_incident_evidence"] is False
    assert all(
        isinstance(item, policy.EvidenceRecord) for item in projected["evidence"]
    )


def test_safe_stream_projection_hides_raw_logs_secrets_and_pii(repository, context):
    runtime = lab.IncidentRuntime(repository, context)
    runtime.start()
    runtime.read_health()
    projections = runtime.safe_stream()
    rendered = json.dumps([item.model_dump() for item in projections])

    assert "secret-token" not in rendered
    assert "customer@example.com" not in rendered
    assert set(projections[0].model_dump()) == {
        "node",
        "status",
        "elapsed_ms",
        "safe_summary",
    }


def test_checkpoint_size_budget_rejects_oversized_state(tmp_path, context):
    repository = policy.SQLiteCheckpointRepository(
        tmp_path / "small.sqlite", max_size_bytes=400
    )
    state = policy.IncidentState(
        request="x" * 350,
        service="checkout-eu",
        remaining_budget=1,
        deadline_at=lab.FIXED_TIME + timedelta(hours=1),
        logical_operation_id="operation-large",
        run_id="run-large",
    )

    with pytest.raises(policy.PolicyError, match="CHECKPOINT_SIZE_EXCEEDED"):
        repository.save(context, state, now=lab.FIXED_TIME)
    repository.close()


def test_deleted_checkpoint_is_unavailable(repository, context):
    runtime = lab.IncidentRuntime(repository, context)
    state = runtime.start()
    repository.delete(context, state.last_checkpoint_id, now=lab.FIXED_TIME)

    with pytest.raises(policy.PolicyError, match="CHECKPOINT_DELETED"):
        repository.load(context, state.last_checkpoint_id, now=lab.FIXED_TIME)


def test_expired_checkpoint_is_unavailable(repository, context):
    runtime = lab.IncidentRuntime(repository, context)
    runtime.start()
    short_lived = repository.save(
        context,
        runtime.state,
        now=lab.FIXED_TIME,
        retention_seconds=1,
    )

    with pytest.raises(policy.PolicyError, match="CHECKPOINT_EXPIRED"):
        repository.load(
            context,
            short_lived.record.checkpoint_id,
            now=lab.FIXED_TIME + timedelta(seconds=2),
        )


def test_audit_checkpoint_cannot_be_deleted_or_purged(repository, context):
    runtime = lab.IncidentRuntime(repository, context)
    runtime.start()
    audit = repository.save(
        context,
        runtime.state,
        now=lab.FIXED_TIME,
        retention_class=policy.RetentionClass.AUDIT,
        retention_seconds=1,
    )

    with pytest.raises(policy.PolicyError, match="AUDIT_RETENTION_REQUIRED"):
        repository.delete(context, audit.record.checkpoint_id, now=lab.FIXED_TIME)
    assert repository.purge_expired_or_deleted(
        lab.build_retention_context(), now=lab.FIXED_TIME + timedelta(seconds=2)
    ) == 0
    row = repository.connection.execute(
        "SELECT state_json FROM checkpoints WHERE checkpoint_id = ?",
        (audit.record.checkpoint_id,),
    ).fetchone()
    assert row is not None


def test_checkpoint_soft_delete_requires_explicit_physical_purge(repository, context):
    runtime = lab.IncidentRuntime(repository, context)
    state = runtime.start()
    repository.delete(context, state.last_checkpoint_id, now=lab.FIXED_TIME)
    retained = repository.connection.execute(
        "SELECT state_json FROM checkpoints WHERE checkpoint_id = ?",
        (state.last_checkpoint_id,),
    ).fetchone()

    assert retained is not None
    with pytest.raises(policy.PolicyError, match="AUTHORIZATION_SCOPE_DENIED"):
        repository.purge_expired_or_deleted(context, now=lab.FIXED_TIME)
    assert repository.purge_expired_or_deleted(
        lab.build_retention_context(), now=lab.FIXED_TIME
    ) == 1
    erased = repository.connection.execute(
        "SELECT state_json FROM checkpoints WHERE checkpoint_id = ?",
        (state.last_checkpoint_id,),
    ).fetchone()
    assert erased is None
    with pytest.raises(policy.PolicyError, match="CHECKPOINT_NOT_FOUND_OR_DENIED"):
        repository.load(context, state.last_checkpoint_id, now=lab.FIXED_TIME)


def test_conflicting_independent_evidence_requires_human_review(repository, context):
    runtime = lab.IncidentRuntime(repository, context)
    runtime.start()
    runtime.read_logs()
    runtime.read_deployment()
    runtime.add_evidence(lab.conflicting_redis_evidence())
    state = runtime.form_hypothesis()

    assert state.terminal_status == policy.TerminalStatus.NEEDS_HUMAN_REVIEW
    assert state.hypothesis is None
    with pytest.raises(policy.PolicyError, match="CONFLICT_REQUIRES_HUMAN_REVIEW"):
        runtime.prepare_rollback_proposal()


def test_no_persistence_comparison_uses_observed_restart_counts(tmp_path):
    restart = lab.run_process_restart_experiment(tmp_path / "compare.sqlite")
    no_persistence, durable = lab.compare_persistence(restart)

    assert no_persistence.tool_calls == 5
    assert durable.tool_calls == 3
    assert no_persistence.cost_usd > durable.cost_usd
    assert no_persistence.latency_ms > durable.latency_ms


def test_evaluation_metrics_are_derived_from_observed_counters(tmp_path):
    metrics = lab.run_evaluation(tmp_path / "evaluation.sqlite")

    assert metrics.resume_success_rate == 1.0
    assert metrics.duplicate_work_rate == 0.0
    assert metrics.replay_side_effect_rate == 0.0
    assert metrics.checkpoint_latency_ms > 0
    assert metrics.checkpoint_size_bytes > 0
    assert metrics.memory_contamination_rate == 0.0
    assert metrics.cross_tenant_memory_rate == 0.0
    assert metrics.stale_approval_resume_rate == 0.0
    assert metrics.stream_redaction_failure_rate == 0.0


def test_state_serialization_excludes_credentials(context):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        policy.IncidentState(
            request="test",
            service="checkout-eu",
            remaining_budget=1,
            deadline_at=lab.FIXED_TIME + timedelta(hours=1),
            logical_operation_id="operation-1",
            run_id="run-1",
            api_token="secret",
        )


def test_optional_langgraph_adapter_uses_current_interrupt_api():
    pytest.importorskip("langgraph")
    from langgraph.types import Command

    graph = lab.build_optional_langgraph_adapter()
    config = {"configurable": {"thread_id": "course-10-adapter"}}
    first = graph.invoke(
        {
            "request": "Prepare rollback proposal",
            "safe_review_payload": {},
            "approval_decision": None,
        },
        config=config,
    )
    assert first["__interrupt__"]
    resumed = graph.invoke(
        Command(resume={"decision": "APPROVE"}),
        config=config,
    )
    assert resumed["approval_decision"] == {"decision": "APPROVE"}
