"""Governed state, checkpoint, memory, approval, and replay contracts for Course 10.

The deterministic control plane in this module is framework-neutral. LangGraph is
introduced later as an optional orchestration adapter; identity, authorization,
retention, version checks, and approval validation remain application owned.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


IDENTIFIER_PATTERN = r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$"
HEX_64_PATTERN = r"^[a-f0-9]{64}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PolicyError(ValueError):
    """A deterministic state, authorization, retention, or safety failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class TerminalStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    MIGRATION_REQUIRED = "MIGRATION_REQUIRED"
    CANCELLED = "CANCELLED"
    APPROVED_FOR_EXECUTION = "APPROVED_FOR_EXECUTION"
    FAILED = "FAILED"


class ExecutionMode(str, Enum):
    LIVE = "LIVE"
    REPLAY = "REPLAY"
    DRY_RUN = "DRY_RUN"


class DecisionType(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class MemoryType(str, Enum):
    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"
    PREFERENCE = "PREFERENCE"
    PROCEDURAL = "PROCEDURAL"


class SupersessionPolicy(str, Enum):
    PERMANENT = "PERMANENT"


class MemoryOrigin(str, Enum):
    USER_CONFIRMED = "USER_CONFIRMED"
    REVIEWED_POSTMORTEM = "REVIEWED_POSTMORTEM"
    APPROVED_PROCEDURE = "APPROVED_PROCEDURE"
    MODEL_HYPOTHESIS = "MODEL_HYPOTHESIS"
    RETRIEVED_CONTENT = "RETRIEVED_CONTENT"
    TEMPORARY_CREDENTIAL = "TEMPORARY_CREDENTIAL"


class Sensitivity(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class RetentionClass(str, Enum):
    EPHEMERAL = "EPHEMERAL"
    OPERATIONAL = "OPERATIONAL"
    AUDIT = "AUDIT"


class StreamEventType(str, Enum):
    NODE_STARTED = "NODE_STARTED"
    NODE_COMPLETED = "NODE_COMPLETED"
    CHECKPOINT_SAVED = "CHECKPOINT_SAVED"
    TOOL_STATUS = "TOOL_STATUS"
    INTERRUPTED = "INTERRUPTED"
    RESUMED = "RESUMED"
    TERMINAL = "TERMINAL"


class ReceiptStatus(str, Enum):
    COMMITTED = "COMMITTED"
    ALREADY_COMMITTED = "ALREADY_COMMITTED"
    DRY_RUN = "DRY_RUN"


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone")
    return value


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ThreadContext(FrozenModel):
    """Trusted application context; a model never proposes these values."""

    tenant_id: str = Field(pattern=IDENTIFIER_PATTERN)
    user_id: str = Field(pattern=IDENTIFIER_PATTERN)
    thread_id: str = Field(pattern=IDENTIFIER_PATTERN)
    authorization_scope: tuple[str, ...] = Field(min_length=1)
    policy_version: str = Field(pattern=IDENTIFIER_PATTERN)

    @field_validator("authorization_scope")
    @classmethod
    def unique_scopes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Authorization scopes must be unique")
        return values


class EvidenceRecord(FrozenModel):
    evidence_id: str = Field(pattern=IDENTIFIER_PATTERN)
    source: str = Field(pattern=IDENTIFIER_PATTERN)
    source_version: str = Field(min_length=1, max_length=80)
    observed_at: datetime
    summary: str = Field(min_length=1, max_length=500)
    artifact_handle: str = Field(min_length=1, max_length=240)
    hash: str = Field(pattern=HEX_64_PATTERN)
    correlation_group: str = Field(pattern=IDENTIFIER_PATTERN)
    claim_key: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    claim_value: str | None = Field(default=None, max_length=120)

    _aware_observed_at = field_validator("observed_at")(_require_aware)

    @model_validator(mode="after")
    def claim_fields_are_paired(self) -> "EvidenceRecord":
        if (self.claim_key is None) != (self.claim_value is None):
            raise ValueError("claim_key and claim_value must be supplied together")
        return self


class NodeAttempts(FrozenModel):
    node: str = Field(pattern=IDENTIFIER_PATTERN)
    attempts: int = Field(ge=0)


class ApprovalProposal(FrozenModel):
    proposal_id: str = Field(pattern=IDENTIFIER_PATTERN)
    tenant_id: str = Field(pattern=IDENTIFIER_PATTERN)
    action: str = Field(pattern=IDENTIFIER_PATTERN)
    target: str = Field(pattern=IDENTIFIER_PATTERN)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    policy_version: str = Field(pattern=IDENTIFIER_PATTERN)
    created_by: str = Field(pattern=IDENTIFIER_PATTERN)
    created_at: datetime
    expires_at: datetime
    logical_operation_id: str = Field(pattern=IDENTIFIER_PATTERN)

    _aware_created_at = field_validator("created_at")(_require_aware)
    _aware_expires_at = field_validator("expires_at")(_require_aware)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Proposal evidence IDs must be unique")
        return values

    @model_validator(mode="after")
    def expiry_follows_creation(self) -> "ApprovalProposal":
        if self.expires_at <= self.created_at:
            raise ValueError("Proposal expiry must follow creation")
        return self

    @property
    def digest(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return _sha256(payload)


class ApprovalDecision(FrozenModel):
    decision_id: str = Field(pattern=IDENTIFIER_PATTERN)
    decision: DecisionType
    tenant_id: str = Field(pattern=IDENTIFIER_PATTERN)
    action: str = Field(pattern=IDENTIFIER_PATTERN)
    target: str = Field(pattern=IDENTIFIER_PATTERN)
    proposal_digest: str = Field(pattern=HEX_64_PATTERN)
    proposal_expires_at: datetime
    policy_version: str = Field(pattern=IDENTIFIER_PATTERN)
    approver_id: str = Field(pattern=IDENTIFIER_PATTERN)
    decided_at: datetime
    reason: str = Field(min_length=1, max_length=300)

    _aware_proposal_expires_at = field_validator("proposal_expires_at")(_require_aware)
    _aware_decided_at = field_validator("decided_at")(_require_aware)


class ApproverContext(FrozenModel):
    tenant_id: str = Field(pattern=IDENTIFIER_PATTERN)
    approver_id: str = Field(pattern=IDENTIFIER_PATTERN)
    roles: tuple[str, ...] = Field(min_length=1)
    authorization_scope: tuple[str, ...] = Field(min_length=1)


class VerifierContext(FrozenModel):
    """Trusted application identity authorized to verify a memory write."""

    tenant_id: str = Field(pattern=IDENTIFIER_PATTERN)
    verifier_id: str = Field(pattern=IDENTIFIER_PATTERN)
    roles: tuple[str, ...] = Field(min_length=1)
    authorization_scope: tuple[str, ...] = Field(min_length=1)


class ApprovalAuditReference(FrozenModel):
    approval_record_id: str = Field(pattern=IDENTIFIER_PATTERN)
    decision_id: str = Field(pattern=IDENTIFIER_PATTERN)
    proposal_digest: str = Field(pattern=HEX_64_PATTERN)
    validated_approver_id: str = Field(pattern=IDENTIFIER_PATTERN)
    logical_operation_id: str = Field(pattern=IDENTIFIER_PATTERN)
    validated_at: datetime

    _aware_validated_at = field_validator("validated_at")(_require_aware)


class IncidentState(FrozenModel):
    request: str = Field(min_length=1, max_length=500)
    service: str = Field(pattern=IDENTIFIER_PATTERN)
    evidence: tuple[EvidenceRecord, ...] = ()
    hypothesis: str | None = Field(default=None, max_length=500)
    confidence: float = Field(default=0.0, ge=0, le=1)
    attempts_by_node: tuple[NodeAttempts, ...] = ()
    tool_call_count: int = Field(default=0, ge=0)
    remaining_budget: int = Field(ge=0)
    retry_budget_remaining: int = Field(default=2, ge=0)
    replan_budget_remaining: int = Field(default=1, ge=0)
    reflection_budget_remaining: int = Field(default=1, ge=0)
    deadline_at: datetime
    pending_approval: ApprovalProposal | None = None
    approval_audit: ApprovalAuditReference | None = None
    external_action_receipt_id: str | None = Field(
        default=None, pattern=IDENTIFIER_PATTERN
    )
    terminal_status: TerminalStatus = TerminalStatus.RUNNING
    last_checkpoint_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    completed_nodes: tuple[str, ...] = ()
    state_schema_version: str = Field(default="state-v2", pattern=IDENTIFIER_PATTERN)
    graph_version: str = Field(default="graph-v4", pattern=IDENTIFIER_PATTERN)
    execution_mode: ExecutionMode = ExecutionMode.LIVE
    logical_operation_id: str = Field(pattern=IDENTIFIER_PATTERN)
    run_id: str = Field(pattern=IDENTIFIER_PATTERN)

    _aware_deadline_at = field_validator("deadline_at")(_require_aware)

    @model_validator(mode="after")
    def state_collections_are_unique(self) -> "IncidentState":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Evidence IDs must be unique")
        nodes = [item.node for item in self.attempts_by_node]
        if len(nodes) != len(set(nodes)):
            raise ValueError("Node attempt counters must be unique")
        if len(self.completed_nodes) != len(set(self.completed_nodes)):
            raise ValueError("Completed nodes must be unique")
        return self


class CheckpointRecord(FrozenModel):
    checkpoint_id: str = Field(pattern=IDENTIFIER_PATTERN)
    thread_id: str = Field(pattern=IDENTIFIER_PATTERN)
    tenant_id: str = Field(pattern=IDENTIFIER_PATTERN)
    owner_user_id: str = Field(pattern=IDENTIFIER_PATTERN)
    parent_checkpoint_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    state_schema_version: str = Field(pattern=IDENTIFIER_PATTERN)
    graph_version: str = Field(pattern=IDENTIFIER_PATTERN)
    policy_version: str = Field(pattern=IDENTIFIER_PATTERN)
    created_at: datetime
    state_digest: str = Field(pattern=HEX_64_PATTERN)
    checkpoint_size_bytes: int = Field(gt=0)
    expires_at: datetime
    deleted_at: datetime | None = None
    retention_class: RetentionClass
    execution_mode: ExecutionMode
    sequence: int = Field(gt=0)

    _aware_created_at = field_validator("created_at")(_require_aware)
    _aware_expires_at = field_validator("expires_at")(_require_aware)
    _aware_deleted_at = field_validator("deleted_at")(
        lambda value: _require_aware(value) if value is not None else value
    )


class CheckpointSnapshot(FrozenModel):
    record: CheckpointRecord
    state: IncidentState


class SQLiteCheckpointRepository:
    """Small durable repository using typed JSON, never arbitrary object loading."""

    def __init__(self, database_path: str | Path, *, max_size_bytes: int = 32_000):
        self.database_path = str(database_path)
        self.max_size_bytes = max_size_bytes
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self.last_save_latency_ms = 0.0
        self.saved_sizes: list[int] = []
        self._setup()

    def _setup(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                owner_user_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                state_json TEXT NOT NULL,
                record_json TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoint_tombstones (
                checkpoint_id TEXT PRIMARY KEY,
                deleted_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_checkpoint_thread "
            "ON checkpoints(thread_id, sequence)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_checkpoint_owner "
            "ON checkpoints(thread_id, tenant_id, owner_user_id, sequence)"
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _require_scope(context: ThreadContext, scope: str) -> None:
        if scope not in context.authorization_scope:
            raise PolicyError(f"AUTHORIZATION_SCOPE_DENIED:{scope}")

    def save(
        self,
        context: ThreadContext,
        state: IncidentState,
        *,
        parent_checkpoint_id: str | None = None,
        now: datetime | None = None,
        retention_class: RetentionClass = RetentionClass.OPERATIONAL,
        retention_seconds: int = 86_400,
    ) -> CheckpointSnapshot:
        self._require_scope(context, "checkpoint:write")
        if retention_seconds <= 0:
            raise PolicyError("INVALID_RETENTION")
        created_at = now or datetime.now(timezone.utc)
        _require_aware(created_at)
        checkpoint_id = f"cp-{uuid.uuid4().hex}"
        next_sequence = self.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM checkpoints "
            "WHERE thread_id = ? AND tenant_id = ? AND owner_user_id = ?",
            (context.thread_id, context.tenant_id, context.user_id),
        ).fetchone()[0]
        saved_state = state.model_copy(update={"last_checkpoint_id": checkpoint_id})
        state_json = saved_state.model_dump_json()
        size = len(state_json.encode("utf-8"))
        if size > self.max_size_bytes:
            raise PolicyError("CHECKPOINT_SIZE_EXCEEDED")
        record = CheckpointRecord(
            checkpoint_id=checkpoint_id,
            thread_id=context.thread_id,
            tenant_id=context.tenant_id,
            owner_user_id=context.user_id,
            parent_checkpoint_id=parent_checkpoint_id or state.last_checkpoint_id,
            state_schema_version=saved_state.state_schema_version,
            graph_version=saved_state.graph_version,
            policy_version=context.policy_version,
            created_at=created_at,
            state_digest=_sha256(state_json),
            checkpoint_size_bytes=size,
            expires_at=created_at + timedelta(seconds=retention_seconds),
            retention_class=retention_class,
            execution_mode=saved_state.execution_mode,
            sequence=next_sequence,
        )
        started = time.perf_counter()
        self.connection.execute(
            "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.checkpoint_id,
                record.thread_id,
                record.tenant_id,
                record.owner_user_id,
                record.sequence,
                record.created_at.isoformat(),
                state_json,
                record.model_dump_json(),
            ),
        )
        self.connection.commit()
        self.last_save_latency_ms = (time.perf_counter() - started) * 1_000
        self.saved_sizes.append(size)
        return CheckpointSnapshot(record=record, state=saved_state)

    def _row_for_context(
        self, context: ThreadContext, checkpoint_id: str | None
    ) -> sqlite3.Row:
        if checkpoint_id:
            row = self.connection.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id = ? "
                "AND thread_id = ? AND tenant_id = ? AND owner_user_id = ?",
                (
                    checkpoint_id,
                    context.thread_id,
                    context.tenant_id,
                    context.user_id,
                ),
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT * FROM checkpoints WHERE thread_id = ? "
                "AND tenant_id = ? AND owner_user_id = ? "
                "ORDER BY sequence DESC LIMIT 1",
                (context.thread_id, context.tenant_id, context.user_id),
            ).fetchone()
        if row is None:
            raise PolicyError("CHECKPOINT_NOT_FOUND_OR_DENIED")
        return row

    def _authorize_record(
        self,
        context: ThreadContext,
        record: CheckpointRecord,
        *,
        scope: str,
        now: datetime,
    ) -> None:
        self._require_scope(context, scope)
        if record.thread_id != context.thread_id or record.tenant_id != context.tenant_id:
            raise PolicyError("THREAD_ACCESS_DENIED")
        if record.owner_user_id != context.user_id:
            raise PolicyError("USER_ACCESS_DENIED")
        tombstone = self.connection.execute(
            "SELECT deleted_at FROM checkpoint_tombstones WHERE checkpoint_id = ?",
            (record.checkpoint_id,),
        ).fetchone()
        if tombstone is not None:
            raise PolicyError("CHECKPOINT_DELETED")
        if now > record.expires_at:
            raise PolicyError("CHECKPOINT_EXPIRED")

    def load(
        self,
        context: ThreadContext,
        checkpoint_id: str | None = None,
        *,
        expected_state_schema_version: str | None = None,
        expected_graph_version: str | None = None,
        now: datetime | None = None,
    ) -> CheckpointSnapshot:
        current_time = now or datetime.now(timezone.utc)
        self._require_scope(context, "checkpoint:read")
        row = self._row_for_context(context, checkpoint_id)
        record = CheckpointRecord.model_validate_json(row["record_json"])
        self._authorize_record(
            context, record, scope="checkpoint:read", now=current_time
        )
        if record.policy_version != context.policy_version:
            raise PolicyError("POLICY_REVALIDATION_REQUIRED")
        if (
            expected_state_schema_version
            and record.state_schema_version != expected_state_schema_version
        ):
            raise PolicyError("MIGRATION_REQUIRED")
        if expected_graph_version and record.graph_version != expected_graph_version:
            raise PolicyError("GRAPH_VERSION_MISMATCH")
        state_json = row["state_json"]
        if _sha256(state_json) != record.state_digest:
            raise PolicyError("CHECKPOINT_DIGEST_MISMATCH")
        return CheckpointSnapshot(
            record=record,
            state=IncidentState.model_validate_json(state_json),
        )

    def history(
        self, context: ThreadContext, *, now: datetime | None = None
    ) -> tuple[CheckpointRecord, ...]:
        current_time = now or datetime.now(timezone.utc)
        self._require_scope(context, "checkpoint:read")
        rows = self.connection.execute(
            "SELECT * FROM checkpoints WHERE thread_id = ? "
            "AND tenant_id = ? AND owner_user_id = ? ORDER BY sequence",
            (context.thread_id, context.tenant_id, context.user_id),
        ).fetchall()
        records: list[CheckpointRecord] = []
        for row in rows:
            record = CheckpointRecord.model_validate_json(row["record_json"])
            try:
                self._authorize_record(
                    context, record, scope="checkpoint:read", now=current_time
                )
            except PolicyError as error:
                if error.code in {"CHECKPOINT_DELETED", "CHECKPOINT_EXPIRED"}:
                    continue
                raise
            records.append(record)
        return tuple(records)

    def fork(
        self,
        source_context: ThreadContext,
        checkpoint_id: str,
        fork_context: ThreadContext,
        *,
        now: datetime | None = None,
    ) -> CheckpointSnapshot:
        source = self.load(source_context, checkpoint_id, now=now)
        if source_context.tenant_id != fork_context.tenant_id:
            raise PolicyError("THREAD_ACCESS_DENIED")
        if source_context.user_id != fork_context.user_id:
            raise PolicyError("USER_ACCESS_DENIED")
        if source_context.thread_id == fork_context.thread_id:
            raise PolicyError("FORK_THREAD_MUST_DIFFER")
        fork_state = source.state.model_copy(
            update={
                "execution_mode": ExecutionMode.REPLAY,
                "pending_approval": None,
                "approval_audit": None,
                "external_action_receipt_id": None,
                "terminal_status": TerminalStatus.RUNNING,
                "last_checkpoint_id": None,
                "run_id": f"run-{uuid.uuid4().hex}",
            }
        )
        return self.save(
            fork_context,
            fork_state,
            parent_checkpoint_id=source.record.checkpoint_id,
            now=now,
        )

    def delete(
        self,
        context: ThreadContext,
        checkpoint_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        snapshot = self.load(context, checkpoint_id, now=now)
        if snapshot.record.retention_class == RetentionClass.AUDIT:
            raise PolicyError("AUDIT_RETENTION_REQUIRED")
        deleted_at = now or datetime.now(timezone.utc)
        self.connection.execute(
            "INSERT OR REPLACE INTO checkpoint_tombstones VALUES (?, ?)",
            (checkpoint_id, deleted_at.isoformat()),
        )
        self.connection.commit()

    def purge_expired_or_deleted(
        self,
        context: ThreadContext,
        *,
        now: datetime | None = None,
    ) -> int:
        """Physically erase eligible payloads in one trusted ownership scope."""

        self._require_scope(context, "checkpoint:purge")
        current_time = now or datetime.now(timezone.utc)
        rows = self.connection.execute(
            "SELECT c.*, t.deleted_at AS tombstone_deleted_at "
            "FROM checkpoints AS c LEFT JOIN checkpoint_tombstones AS t "
            "ON c.checkpoint_id = t.checkpoint_id "
            "WHERE c.thread_id = ? AND c.tenant_id = ? AND c.owner_user_id = ?",
            (context.thread_id, context.tenant_id, context.user_id),
        ).fetchall()
        purge_ids = []
        for row in rows:
            record = CheckpointRecord.model_validate_json(row["record_json"])
            eligible = row["tombstone_deleted_at"] is not None or (
                current_time > record.expires_at
            )
            if eligible and record.retention_class != RetentionClass.AUDIT:
                purge_ids.append(record.checkpoint_id)
        with self.connection:
            self.connection.executemany(
                "DELETE FROM checkpoints WHERE checkpoint_id = ?",
                ((checkpoint_id,) for checkpoint_id in purge_ids),
            )
            self.connection.executemany(
                "DELETE FROM checkpoint_tombstones WHERE checkpoint_id = ?",
                ((checkpoint_id,) for checkpoint_id in purge_ids),
            )
        return len(purge_ids)


class MemoryRecord(FrozenModel):
    memory_id: str = Field(pattern=IDENTIFIER_PATTERN)
    tenant_id: str = Field(pattern=IDENTIFIER_PATTERN)
    subject_id: str = Field(pattern=IDENTIFIER_PATTERN)
    memory_type: MemoryType
    content: str = Field(min_length=1, max_length=1_000)
    source_id: str = Field(pattern=IDENTIFIER_PATTERN)
    source_version: str = Field(min_length=1, max_length=80)
    origin: MemoryOrigin
    verified: bool
    verified_by: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    created_at: datetime
    expires_at: datetime
    sensitivity: Sensitivity
    version: int = Field(gt=0)
    supersedes: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    retention_class: RetentionClass
    deleted_at: datetime | None = None
    relevance_score: float = Field(default=0.0, ge=0, le=1)

    _aware_created_at = field_validator("created_at")(_require_aware)
    _aware_expires_at = field_validator("expires_at")(_require_aware)
    _aware_deleted_at = field_validator("deleted_at")(
        lambda value: _require_aware(value) if value is not None else value
    )

    @model_validator(mode="after")
    def verification_and_expiry_are_valid(self) -> "MemoryRecord":
        if self.verified and not self.verified_by:
            raise ValueError("Verified memory requires verified_by")
        if self.expires_at <= self.created_at:
            raise ValueError("Memory expiry must follow creation")
        return self


class MemoryWriteRequest(FrozenModel):
    """Untrusted write request; verification fields are claims until authorized."""

    tenant_id: str = Field(pattern=IDENTIFIER_PATTERN)
    subject_id: str = Field(pattern=IDENTIFIER_PATTERN)
    memory_type: MemoryType
    content: str = Field(min_length=1, max_length=1_000)
    source_id: str = Field(pattern=IDENTIFIER_PATTERN)
    source_version: str = Field(min_length=1, max_length=80)
    origin: MemoryOrigin
    verified: bool
    verified_by: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    expires_at: datetime
    sensitivity: Sensitivity
    version: int = Field(gt=0)
    supersedes: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    retention_class: RetentionClass = RetentionClass.OPERATIONAL
    relevance_score: float = Field(default=0.0, ge=0, le=1)

    _aware_expires_at = field_validator("expires_at")(_require_aware)


class MemoryPolicy(FrozenModel):
    supersession_policy: SupersessionPolicy = SupersessionPolicy.PERMANENT
    allowed_origins: tuple[MemoryOrigin, ...] = (
        MemoryOrigin.USER_CONFIRMED,
        MemoryOrigin.REVIEWED_POSTMORTEM,
        MemoryOrigin.APPROVED_PROCEDURE,
    )
    forbidden_markers: tuple[str, ...] = (
        "pre-approved",
        "ignore policy",
        "bearer token",
        "api key",
        "password",
        "temporary credential",
    )


class GovernedMemoryStore:
    """Deterministic store fixture with namespace, lifecycle, and write policy."""

    def __init__(self, policy: MemoryPolicy | None = None):
        self.policy = policy or MemoryPolicy()
        self._records: dict[str, MemoryRecord] = {}
        self._superseded_memory_ids: dict[str, str] = {}

    @staticmethod
    def _require_scope(context: ThreadContext, scope: str) -> None:
        if scope not in context.authorization_scope:
            raise PolicyError(f"AUTHORIZATION_SCOPE_DENIED:{scope}")

    def write(
        self,
        context: ThreadContext,
        request: MemoryWriteRequest,
        *,
        verifier: VerifierContext | None = None,
        now: datetime | None = None,
        memory_id: str | None = None,
    ) -> MemoryRecord:
        self._require_scope(context, "memory:write")
        if request.tenant_id != context.tenant_id:
            raise PolicyError("MEMORY_TENANT_DENIED")
        lowered = request.content.casefold()
        if request.origin not in self.policy.allowed_origins:
            raise PolicyError("MEMORY_WRITE_DENIED")
        if not request.verified or not request.verified_by:
            raise PolicyError("MEMORY_WRITE_DENIED")
        if verifier is None:
            raise PolicyError("MEMORY_VERIFIER_REQUIRED")
        if verifier.tenant_id != context.tenant_id:
            raise PolicyError("MEMORY_VERIFIER_TENANT_DENIED")
        if "memory_verifier" not in verifier.roles:
            raise PolicyError("MEMORY_VERIFIER_ROLE_DENIED")
        if "memory:verify" not in verifier.authorization_scope:
            raise PolicyError("MEMORY_VERIFIER_SCOPE_DENIED")
        if request.verified_by != verifier.verifier_id:
            raise PolicyError("MEMORY_VERIFIER_ID_MISMATCH")
        if any(marker in lowered for marker in self.policy.forbidden_markers):
            raise PolicyError("MEMORY_WRITE_DENIED")
        if request.memory_type == MemoryType.PREFERENCE and (
            request.origin != MemoryOrigin.USER_CONFIRMED
        ):
            raise PolicyError("MEMORY_WRITE_DENIED")
        if request.memory_type == MemoryType.PROCEDURAL and (
            request.origin != MemoryOrigin.APPROVED_PROCEDURE
        ):
            raise PolicyError("MEMORY_WRITE_DENIED")
        created_at = now or datetime.now(timezone.utc)
        if request.expires_at <= created_at:
            raise PolicyError("MEMORY_ALREADY_EXPIRED")
        if request.supersedes:
            previous = self._records.get(request.supersedes)
            if previous is None:
                raise PolicyError("SUPERSEDED_MEMORY_NOT_FOUND")
            if (
                previous.tenant_id != request.tenant_id
                or previous.subject_id != request.subject_id
                or previous.memory_type != request.memory_type
                or previous.source_id != request.source_id
                or request.version != previous.version + 1
            ):
                raise PolicyError("INVALID_MEMORY_SUPERSESSION")
            if previous.memory_id in self._superseded_memory_ids:
                raise PolicyError("MEMORY_LINEAGE_ALREADY_SUPERSEDED")
        elif request.version != 1:
            raise PolicyError("MEMORY_LINEAGE_PREDECESSOR_REQUIRED")
        record = MemoryRecord(
            memory_id=memory_id or f"memory-{uuid.uuid4().hex}",
            created_at=created_at,
            **request.model_dump(),
        )
        if record.memory_id in self._records:
            raise PolicyError("MEMORY_ID_CONFLICT")
        self._records[record.memory_id] = record
        if record.supersedes:
            self._superseded_memory_ids[record.supersedes] = record.tenant_id
        return record

    def retrieve(
        self,
        context: ThreadContext,
        *,
        subject_id: str,
        memory_types: tuple[MemoryType, ...] | None = None,
        now: datetime | None = None,
    ) -> tuple[MemoryRecord, ...]:
        self._require_scope(context, "memory:read")
        current_time = now or datetime.now(timezone.utc)
        permanently_superseded_ids = {
            memory_id
            for memory_id, tenant_id in self._superseded_memory_ids.items()
            if tenant_id == context.tenant_id
        }
        allowed_types = set(memory_types) if memory_types else set(MemoryType)
        records = [
            record
            for record in self._records.values()
            if record.tenant_id == context.tenant_id
            and record.subject_id == subject_id
            and record.memory_type in allowed_types
            and record.verified
            and record.deleted_at is None
            and current_time <= record.expires_at
            and record.memory_id not in permanently_superseded_ids
        ]
        return tuple(
            sorted(records, key=lambda item: (item.relevance_score, item.version), reverse=True)
        )

    def delete(
        self,
        context: ThreadContext,
        memory_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        self._require_scope(context, "memory:write")
        record = self._records.get(memory_id)
        if record is None or record.tenant_id != context.tenant_id:
            raise PolicyError("MEMORY_NOT_FOUND")
        if record.retention_class == RetentionClass.AUDIT:
            raise PolicyError("AUDIT_RETENTION_REQUIRED")
        self._records[memory_id] = record.model_copy(
            update={"deleted_at": now or datetime.now(timezone.utc)}
        )

    def purge_expired_or_deleted(
        self,
        context: ThreadContext,
        *,
        now: datetime | None = None,
    ) -> int:
        """Physically erase eligible non-audit memory payloads for one tenant."""

        self._require_scope(context, "memory:purge")
        current_time = now or datetime.now(timezone.utc)
        purge_ids = [
            record.memory_id
            for record in self._records.values()
            if record.tenant_id == context.tenant_id
            and record.retention_class != RetentionClass.AUDIT
            and (record.deleted_at is not None or current_time > record.expires_at)
        ]
        for memory_id in purge_ids:
            del self._records[memory_id]
        return len(purge_ids)

    def seed_fixture(self, record: MemoryRecord) -> None:
        """Load synthetic evaluation data without implying it passed write policy."""

        self._records[record.memory_id] = record
        if record.supersedes:
            self._superseded_memory_ids[record.supersedes] = record.tenant_id


def validate_approval_decision(
    state: IncidentState,
    decision: ApprovalDecision,
    approver: ApproverContext,
    context: ThreadContext,
    *,
    now: datetime | None = None,
) -> ApprovalAuditReference:
    if state.terminal_status == TerminalStatus.CANCELLED:
        raise PolicyError("CANCELLED_RUN")
    proposal = state.pending_approval
    if proposal is None or state.terminal_status != TerminalStatus.INTERRUPTED:
        raise PolicyError("NO_PENDING_APPROVAL")
    current_time = now or datetime.now(timezone.utc)
    if decision.target != proposal.target:
        raise PolicyError("APPROVAL_TARGET_MISMATCH")
    if decision.action != proposal.action:
        raise PolicyError("APPROVAL_ACTION_MISMATCH")
    if decision.tenant_id != proposal.tenant_id or approver.tenant_id != proposal.tenant_id:
        raise PolicyError("APPROVAL_TENANT_MISMATCH")
    if proposal.tenant_id != context.tenant_id:
        raise PolicyError("THREAD_ACCESS_DENIED")
    if decision.policy_version != proposal.policy_version:
        raise PolicyError("APPROVAL_POLICY_MISMATCH")
    if proposal.policy_version != context.policy_version:
        raise PolicyError("POLICY_REVALIDATION_REQUIRED")
    if decision.proposal_expires_at != proposal.expires_at:
        raise PolicyError("APPROVAL_EXPIRY_MISMATCH")
    if decision.decided_at < proposal.created_at:
        raise PolicyError("APPROVAL_DECISION_BEFORE_PROPOSAL")
    if decision.decided_at > proposal.expires_at:
        raise PolicyError("APPROVAL_DECISION_AFTER_EXPIRY")
    if decision.decided_at > current_time:
        raise PolicyError("APPROVAL_DECISION_IN_FUTURE")
    if current_time > proposal.expires_at:
        raise PolicyError("APPROVAL_EXPIRED")
    if decision.proposal_digest != proposal.digest:
        raise PolicyError("APPROVAL_DIGEST_MISMATCH")
    if decision.approver_id != approver.approver_id:
        raise PolicyError("APPROVER_ID_MISMATCH")
    if "incident_commander" not in approver.roles:
        raise PolicyError("APPROVER_ROLE_DENIED")
    if "approval:rollback" not in approver.authorization_scope:
        raise PolicyError("APPROVER_SCOPE_DENIED")
    if decision.decision != DecisionType.APPROVE:
        raise PolicyError("APPROVAL_REJECTED")
    return ApprovalAuditReference(
        approval_record_id=f"approval-record-{decision.decision_id}",
        decision_id=decision.decision_id,
        proposal_digest=proposal.digest,
        validated_approver_id=approver.approver_id,
        logical_operation_id=proposal.logical_operation_id,
        validated_at=current_time,
    )


class MockExecutionReceipt(FrozenModel):
    logical_operation_id: str = Field(pattern=IDENTIFIER_PATTERN)
    attempt_id: str = Field(pattern=IDENTIFIER_PATTERN)
    status: ReceiptStatus
    external_commit_count: int = Field(ge=0)


class IdempotentMockExecutor:
    """Receipt-only fixture; LIVE simulates commit semantics, never a real action."""

    def __init__(self):
        self._receipts: dict[str, MockExecutionReceipt] = {}
        self._attempt_ids: set[str] = set()
        self.external_commit_count = 0

    def execute(
        self,
        logical_operation_id: str,
        attempt_id: str,
        *,
        mode: ExecutionMode,
    ) -> MockExecutionReceipt:
        if attempt_id in self._attempt_ids:
            raise PolicyError("ATTEMPT_ID_REUSED")
        self._attempt_ids.add(attempt_id)
        if mode != ExecutionMode.LIVE:
            return MockExecutionReceipt(
                logical_operation_id=logical_operation_id,
                attempt_id=attempt_id,
                status=ReceiptStatus.DRY_RUN,
                external_commit_count=self.external_commit_count,
            )
        prior = self._receipts.get(logical_operation_id)
        if prior:
            return MockExecutionReceipt(
                logical_operation_id=logical_operation_id,
                attempt_id=attempt_id,
                status=ReceiptStatus.ALREADY_COMMITTED,
                external_commit_count=self.external_commit_count,
            )
        self.external_commit_count += 1
        receipt = MockExecutionReceipt(
            logical_operation_id=logical_operation_id,
            attempt_id=attempt_id,
            status=ReceiptStatus.COMMITTED,
            external_commit_count=self.external_commit_count,
        )
        self._receipts[logical_operation_id] = receipt
        return receipt


class ReplayExperiment(FrozenModel):
    bad_attempt_count: int = Field(ge=0)
    bad_external_commit_count: int = Field(ge=0)
    safe_attempt_count: int = Field(ge=0)
    safe_external_commit_count: int = Field(ge=0)
    logical_operation_id: str = Field(pattern=IDENTIFIER_PATTERN)
    attempt_ids: tuple[str, ...]


def run_replay_experiment() -> ReplayExperiment:
    """Contrast replay behavior using simulated LIVE commits only."""

    logical_id = "rollback-northstar-deploy-1842"
    attempt_ids = ("attempt-before-interrupt", "attempt-after-resume")
    bad_external_commits = 0
    for _attempt_id in attempt_ids:
        bad_external_commits += 1
    executor = IdempotentMockExecutor()
    for attempt_id in attempt_ids:
        executor.execute(logical_id, attempt_id, mode=ExecutionMode.LIVE)
    return ReplayExperiment(
        bad_attempt_count=len(attempt_ids),
        bad_external_commit_count=bad_external_commits,
        safe_attempt_count=len(attempt_ids),
        safe_external_commit_count=executor.external_commit_count,
        logical_operation_id=logical_id,
        attempt_ids=attempt_ids,
    )


class StreamEvent(FrozenModel):
    event_type: StreamEventType
    run_id: str = Field(pattern=IDENTIFIER_PATTERN)
    thread_id: str = Field(pattern=IDENTIFIER_PATTERN)
    checkpoint_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    sequence: int = Field(gt=0)
    timestamp: datetime
    node: str = Field(pattern=IDENTIFIER_PATTERN)
    status: str = Field(min_length=1, max_length=80)
    elapsed_ms: float = Field(ge=0)
    safe_summary: str = Field(min_length=1, max_length=240)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    _aware_timestamp = field_validator("timestamp")(_require_aware)

    @field_validator("safe_summary")
    @classmethod
    def summary_must_be_redacted(cls, value: str) -> str:
        unsafe_patterns = (
            r"(?i)bearer\s+[a-z0-9._-]+",
            r"(?i)(api[_ -]?key|password|secret)\s*[:=]",
            r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}",
        )
        if any(re.search(pattern, value) for pattern in unsafe_patterns):
            raise ValueError("safe_summary contains sensitive material")
        return value


class SafeStreamProjection(FrozenModel):
    node: str
    status: str
    elapsed_ms: float
    safe_summary: str


def project_stream_event(event: StreamEvent) -> SafeStreamProjection:
    return SafeStreamProjection(
        node=event.node,
        status=event.status,
        elapsed_ms=event.elapsed_ms,
        safe_summary=event.safe_summary,
    )


class EvaluationCounters(StrictModel):
    resume_attempts: int = Field(default=0, ge=0)
    resume_successes: int = Field(default=0, ge=0)
    duplicate_work_opportunities: int = Field(default=0, ge=0)
    duplicate_work_executions: int = Field(default=0, ge=0)
    replay_side_effect_attempts: int = Field(default=0, ge=0)
    replay_duplicate_commits: int = Field(default=0, ge=0)
    memory_items_evaluated: int = Field(default=0, ge=0)
    contaminated_memory_results: int = Field(default=0, ge=0)
    cross_tenant_memory_attempts: int = Field(default=0, ge=0)
    cross_tenant_memory_results: int = Field(default=0, ge=0)
    stale_approval_attempts: int = Field(default=0, ge=0)
    stale_approval_resumes: int = Field(default=0, ge=0)
    stream_events_projected: int = Field(default=0, ge=0)
    stream_redaction_failures: int = Field(default=0, ge=0)


class DurabilityMetrics(FrozenModel):
    resume_success_rate: float = Field(ge=0, le=1)
    duplicate_work_rate: float = Field(ge=0, le=1)
    replay_side_effect_rate: float = Field(ge=0, le=1)
    checkpoint_latency_ms: float = Field(ge=0)
    checkpoint_size_bytes: int = Field(ge=0)
    memory_contamination_rate: float = Field(ge=0, le=1)
    cross_tenant_memory_rate: float = Field(ge=0, le=1)
    stale_approval_resume_rate: float = Field(ge=0, le=1)
    stream_redaction_failure_rate: float = Field(ge=0, le=1)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def compute_durability_metrics(
    counters: EvaluationCounters,
    *,
    checkpoint_latencies_ms: tuple[float, ...],
    checkpoint_sizes_bytes: tuple[int, ...],
) -> DurabilityMetrics:
    return DurabilityMetrics(
        resume_success_rate=_rate(counters.resume_successes, counters.resume_attempts),
        duplicate_work_rate=_rate(
            counters.duplicate_work_executions,
            counters.duplicate_work_opportunities,
        ),
        replay_side_effect_rate=_rate(
            counters.replay_duplicate_commits,
            counters.replay_side_effect_attempts,
        ),
        checkpoint_latency_ms=(
            sum(checkpoint_latencies_ms) / len(checkpoint_latencies_ms)
            if checkpoint_latencies_ms
            else 0.0
        ),
        checkpoint_size_bytes=max(checkpoint_sizes_bytes, default=0),
        memory_contamination_rate=_rate(
            counters.contaminated_memory_results,
            counters.memory_items_evaluated,
        ),
        cross_tenant_memory_rate=_rate(
            counters.cross_tenant_memory_results,
            counters.cross_tenant_memory_attempts,
        ),
        stale_approval_resume_rate=_rate(
            counters.stale_approval_resumes,
            counters.stale_approval_attempts,
        ),
        stream_redaction_failure_rate=_rate(
            counters.stream_redaction_failures,
            counters.stream_events_projected,
        ),
    )


class PersistenceComparison(FrozenModel):
    mode: str
    tool_calls: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    latency_ms: int = Field(ge=0)
