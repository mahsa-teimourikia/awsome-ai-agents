"""Deterministic architecture-policy primitives for Advanced Course 01.

The application owns identity, capabilities, topology, budgets, validation, and
completion. Model-produced text is data; it is never execution authority.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PolicyError(ValueError):
    """A fail-closed policy decision with a stable reason code."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArchitectureType(StrEnum):
    SINGLE_GENERALIST = "SINGLE_GENERALIST"
    SINGLE_DYNAMIC_TOOLS = "SINGLE_DYNAMIC_TOOLS"
    PIPELINE = "PIPELINE"
    MANAGER_SPECIALISTS = "MANAGER_SPECIALISTS"
    HANDOFF = "HANDOFF"
    PARALLEL_SPECIALISTS = "PARALLEL_SPECIALISTS"


class RiskTier(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RouteState(StrEnum):
    SINGLE_ROUTE = "SINGLE_ROUTE"
    MULTI_ROUTE = "MULTI_ROUTE"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"


class FailureCode(StrEnum):
    TIMEOUT = "TIMEOUT"
    AUTH_DENIED = "AUTH_DENIED"
    POLICY_DENIED = "POLICY_DENIED"
    INVALID_ARTIFACT = "INVALID_ARTIFACT"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


class RecoveryAction(StrEnum):
    RETRY = "RETRY"
    FALLBACK = "FALLBACK"
    CONTINUE_DEGRADED = "CONTINUE_DEGRADED"
    ABSTAIN = "ABSTAIN"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class RunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    COMPLETED_DEGRADED = "COMPLETED_DEGRADED"
    ABSTAINED = "ABSTAINED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    BLOCKED = "BLOCKED"


class AgentDefinition(FrozenModel):
    agent_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    allowed_capabilities: tuple[str, ...]
    allowed_handoffs: tuple[str, ...]
    context_policy: str = Field(min_length=1)
    max_concurrency: int = Field(ge=1)

    @field_validator("allowed_capabilities", "allowed_handoffs")
    @classmethod
    def unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("values must be unique")
        return value


class TaskCase(FrozenModel):
    case_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    required_evidence_ids: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    expected_outcome: str = Field(min_length=1)
    risk_tier: RiskTier


class ArtifactFinding(FrozenModel):
    finding_id: str
    statement: str
    fact_key: str
    fact_value: str
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class SpecialistArtifact(FrozenModel):
    artifact_id: str
    task_id: str
    agent_id: str
    tenant_id: str
    evidence_ids: tuple[str, ...]
    findings: tuple[ArtifactFinding, ...]
    assumptions: tuple[str, ...]
    unresolved_questions: tuple[str, ...]
    confidence_label: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class HandoffEnvelope(FrozenModel):
    handoff_id: str
    task_id: str
    from_agent: str
    to_agent: str
    tenant_id: str
    required_facts: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    reason: str
    depth: int = Field(ge=1)
    deadline: datetime

    @field_validator("deadline")
    @classmethod
    def aware_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("deadline must be timezone-aware")
        return value


class TrustedContext(FrozenModel):
    tenant_id: str
    user_id: str
    authorization_capabilities: tuple[str, ...]
    incident_id: str
    region: str
    deploy_id: str
    customer_tier: str
    incident_window: str


class EvidenceRecord(FrozenModel):
    evidence_id: str
    tenant_id: str
    evidence_type: str
    source_id: str
    capability: str
    facts: Mapping[str, str]
    observed_at: datetime
    provenance_verified: bool


class DelegationBudget(FrozenModel):
    max_agent_invocations: int = Field(ge=1)
    max_handoffs: int = Field(ge=0)
    max_depth: int = Field(ge=0)
    max_parallel_agents: int = Field(ge=1)
    max_total_cost: float = Field(ge=0)
    deadline_ms: int = Field(ge=1)


class ConcurrencyPolicy(FrozenModel):
    max_parallel_specialists: int = Field(ge=1)
    rate_limit_groups: Mapping[str, str]
    shared_dependencies: Mapping[str, tuple[str, ...]]
    max_per_rate_limit_group: int = Field(ge=1)
    max_per_shared_dependency: int = Field(ge=1)


class ConcurrencyValidation(FrozenModel):
    selected_agents: tuple[str, ...]
    rate_limit_group_counts: Mapping[str, int]
    shared_dependency_counts: Mapping[str, int]


class TopologyPolicy(FrozenModel):
    allow_self_handoff: bool = False
    allow_cycles: bool = False
    explicit_capability_grants: Mapping[str, tuple[str, ...]] = Field(
        default_factory=dict
    )


class TopologyValidation(FrozenModel):
    handoff_path: tuple[str, ...]
    invocation_count: int
    handoff_count: int
    maximum_depth: int


class ArtifactValidation(FrozenModel):
    artifact_id: str
    validated_evidence_ids: tuple[str, ...]
    provenance_source_ids: tuple[str, ...]


class RoutingCase(FrozenModel):
    route_case_id: str
    query: str
    expected_state: RouteState
    expected_agents: tuple[str, ...]
    approval_gated: bool = False


class RouteDecision(FrozenModel):
    state: RouteState
    agent_ids: tuple[str, ...]
    approval_required: bool
    router_name: str
    latency_ms: int = Field(ge=0)
    cost_usd: float = Field(ge=0)


class RoutingMetrics(FrozenModel):
    route_accuracy: float
    agent_precision: float
    agent_recall: float
    unknown_detection: float
    total_latency_ms: int
    total_cost_usd: float


class WorkItem(FrozenModel):
    operation_id: str
    model_work_ms: int = Field(ge=0)
    tool_work_ms: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    coordination_tokens: int = Field(ge=0)
    handoff_serialization_ms: int = Field(ge=0)
    cost_usd: float = Field(ge=0)

    @property
    def elapsed_ms(self) -> int:
        return self.model_work_ms + self.tool_work_ms + self.handoff_serialization_ms


class ExecutionFixture(FrozenModel):
    """Sequential batches whose items are conceptually concurrent."""

    batches: tuple[tuple[WorkItem, ...], ...]

    @model_validator(mode="after")
    def non_empty_batches(self) -> "ExecutionFixture":
        if not self.batches or any(not batch for batch in self.batches):
            raise ValueError("fixtures require non-empty batches")
        return self


class TimingSummary(FrozenModel):
    total_model_work_ms: int
    total_tool_work_ms: int
    total_coordination_work_ms: int
    total_work_ms: int
    wall_clock_latency_ms: int
    critical_path_ms: int
    input_tokens: int
    output_tokens: int
    coordination_tokens: int
    total_tokens: int
    cost_usd: float


class RunMetrics(FrozenModel):
    task_success: float = Field(ge=0, le=1)
    grounding: float = Field(ge=0, le=1)
    required_evidence_recall: float = Field(ge=0, le=1)
    handoff_information_recall: float = Field(ge=0, le=1)
    route_accuracy: float = Field(ge=0, le=1)
    conflict_rate: float = Field(ge=0, le=1)
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    handoff_count: int = Field(ge=0)
    coordination_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    total_model_work_ms: int = Field(ge=0)
    total_tool_work_ms: int = Field(ge=0)
    total_coordination_work_ms: int = Field(ge=0)
    total_work_ms: int = Field(ge=0)
    wall_clock_latency_ms: int = Field(ge=0)
    critical_path_ms: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    cost_per_compliant_success: float | None
    privileged_tool_exposure: int = Field(ge=0)
    duplicate_coordination: int = Field(ge=0)
    failure_recovery_rate: float = Field(ge=0, le=1)
    safety_violations: int = Field(ge=0)


class ArchitectureRun(FrozenModel):
    architecture: ArchitectureType
    case_id: str
    initial_owner: str
    active_owner: str
    validated_artifact_ids: tuple[str, ...]
    status: RunStatus
    application_completed: bool
    metrics: RunMetrics


class HandoffRecall(FrozenModel):
    recall: float
    preserved_facts: tuple[str, ...]
    missing_facts: tuple[str, ...]


class Conflict(FrozenModel):
    fact_key: str
    asserted_values: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    resolution: str = "HUMAN_REVIEW"


class FailureEvent(FrozenModel):
    code: FailureCode
    agent_id: str
    evidence_ids: tuple[str, ...] = ()
    retryable: bool = False


class FailureResolution(FrozenModel):
    action: RecoveryAction
    status: RunStatus
    missing_required_evidence_ids: tuple[str, ...]


class ContextExperiment(FrozenModel):
    mode: str
    tokens: int
    sensitive_fields_exposed: int
    handoff_information_recall: float


class ArchitectureGate(FrozenModel):
    accepted: bool
    verdict: str
    reasons: tuple[str, ...]


def validate_concurrency(
    selected_agent_ids: tuple[str, ...],
    *,
    agents: tuple[AgentDefinition, ...],
    policy: ConcurrencyPolicy,
) -> ConcurrencyValidation:
    """Bound fan-out by agent, rate-limit group, and shared dependency."""

    agent_map = {agent.agent_id: agent for agent in agents}
    if len(selected_agent_ids) > policy.max_parallel_specialists:
        raise PolicyError("MAX_PARALLEL_SPECIALISTS_EXCEEDED")
    group_counts: dict[str, int] = defaultdict(int)
    dependency_counts: dict[str, int] = defaultdict(int)
    for agent_id in selected_agent_ids:
        if agent_id not in agent_map:
            raise PolicyError("UNKNOWN_AGENT")
        if selected_agent_ids.count(agent_id) > agent_map[agent_id].max_concurrency:
            raise PolicyError("AGENT_MAX_CONCURRENCY_EXCEEDED")
        group = policy.rate_limit_groups.get(agent_id)
        if group:
            group_counts[group] += 1
        for dependency in policy.shared_dependencies.get(agent_id, ()):
            dependency_counts[dependency] += 1
    if any(count > policy.max_per_rate_limit_group for count in group_counts.values()):
        raise PolicyError("RATE_LIMIT_GROUP_EXCEEDED")
    if any(count > policy.max_per_shared_dependency for count in dependency_counts.values()):
        raise PolicyError("SHARED_DEPENDENCY_CONCURRENCY_EXCEEDED")
    return ConcurrencyValidation(
        selected_agents=selected_agent_ids,
        rate_limit_group_counts=dict(sorted(group_counts.items())),
        shared_dependency_counts=dict(sorted(dependency_counts.items())),
    )


def validate_topology(
    *,
    agents: tuple[AgentDefinition, ...],
    handoffs: tuple[HandoffEnvelope, ...],
    delegated_capabilities: Mapping[str, tuple[str, ...]],
    task: TaskCase,
    trusted_context: TrustedContext,
    budget: DelegationBudget,
    policy: TopologyPolicy | None = None,
    estimated_cost: float = 0.0,
    estimated_wall_clock_ms: int = 0,
    parallel_agents: int = 1,
    now: datetime | None = None,
) -> TopologyValidation:
    """Validate application-owned topology and capability attenuation."""

    policy = policy or TopologyPolicy()
    now = now or datetime.now(timezone.utc)
    agent_map = {agent.agent_id: agent for agent in agents}
    if len(agent_map) != len(agents):
        raise PolicyError("DUPLICATE_AGENT_ID")
    if trusted_context.tenant_id != task.tenant_id:
        raise PolicyError("TASK_TENANT_MISMATCH")
    if not set(task.required_capabilities).issubset(
        trusted_context.authorization_capabilities
    ):
        raise PolicyError("REQUEST_CAPABILITY_DENIED")
    if len(handoffs) > budget.max_handoffs:
        raise PolicyError("MAX_HANDOFFS_EXCEEDED")
    if len(handoffs) + 1 > budget.max_agent_invocations:
        raise PolicyError("MAX_AGENT_INVOCATIONS_EXCEEDED")
    if parallel_agents > budget.max_parallel_agents:
        raise PolicyError("MAX_PARALLEL_AGENTS_EXCEEDED")
    if estimated_cost > budget.max_total_cost:
        raise PolicyError("MAX_TOTAL_COST_EXCEEDED")
    if estimated_wall_clock_ms > budget.deadline_ms:
        raise PolicyError("DEADLINE_BUDGET_EXCEEDED")

    path: list[str] = []
    seen_edges: set[tuple[str, str]] = set()
    for envelope in handoffs:
        if envelope.from_agent not in agent_map or envelope.to_agent not in agent_map:
            raise PolicyError("UNKNOWN_AGENT")
        if envelope.task_id != task.case_id:
            raise PolicyError("HANDOFF_TASK_MISMATCH")
        if envelope.tenant_id != trusted_context.tenant_id:
            raise PolicyError("HANDOFF_TENANT_MISMATCH")
        if envelope.deadline < now:
            raise PolicyError("HANDOFF_DEADLINE_EXPIRED")
        if envelope.depth > budget.max_depth:
            raise PolicyError("MAX_HANDOFF_DEPTH_EXCEEDED")
        if envelope.from_agent == envelope.to_agent and not policy.allow_self_handoff:
            raise PolicyError("SELF_HANDOFF_DENIED")
        source = agent_map[envelope.from_agent]
        target = agent_map[envelope.to_agent]
        if envelope.to_agent not in source.allowed_handoffs:
            raise PolicyError("UNAUTHORIZED_HANDOFF")

        requested = set(delegated_capabilities.get(envelope.handoff_id, ()))
        parent_capabilities = set(source.allowed_capabilities)
        request_capabilities = set(trusted_context.authorization_capabilities)
        explicit_grants = set(policy.explicit_capability_grants.get(envelope.handoff_id, ()))
        attenuated = (parent_capabilities & request_capabilities) | explicit_grants
        if not requested.issubset(attenuated):
            raise PolicyError("CAPABILITY_ESCALATION")
        if not requested.issubset(target.allowed_capabilities):
            raise PolicyError("TARGET_CAPABILITY_DENIED")

        edge = (envelope.from_agent, envelope.to_agent)
        if edge in seen_edges:
            raise PolicyError("DUPLICATE_COORDINATION")
        seen_edges.add(edge)
        if not path:
            path.append(envelope.from_agent)
        elif path[-1] != envelope.from_agent:
            raise PolicyError("DISCONNECTED_HANDOFF_PATH")
        path.append(envelope.to_agent)
        if not policy.allow_cycles and len(path) != len(set(path)):
            raise PolicyError("HANDOFF_CYCLE_DETECTED")

    return TopologyValidation(
        handoff_path=tuple(path),
        invocation_count=len(handoffs) + 1,
        handoff_count=len(handoffs),
        maximum_depth=max((handoff.depth for handoff in handoffs), default=0),
    )


def validate_artifact(
    artifact: SpecialistArtifact,
    *,
    task: TaskCase,
    trusted_context: TrustedContext,
    agent: AgentDefinition,
    evidence_registry: Mapping[str, EvidenceRecord],
) -> ArtifactValidation:
    """Validate an artifact before it enters shared state or synthesis."""

    if artifact.task_id != task.case_id:
        raise PolicyError("ARTIFACT_TASK_MISMATCH")
    if artifact.tenant_id != trusted_context.tenant_id:
        raise PolicyError("ARTIFACT_TENANT_MISMATCH")
    if artifact.agent_id != agent.agent_id:
        raise PolicyError("ARTIFACT_AGENT_MISMATCH")
    if len(artifact.evidence_ids) != len(set(artifact.evidence_ids)):
        raise PolicyError("DUPLICATE_EVIDENCE_REFERENCE")
    unknown = set(artifact.evidence_ids) - set(evidence_registry)
    if unknown:
        raise PolicyError("INVALID_EVIDENCE_REFERENCE")
    if not set(artifact.evidence_ids).issubset(task.required_evidence_ids):
        raise PolicyError("EVIDENCE_OUTSIDE_TASK_SCOPE")

    sources: list[str] = []
    for evidence_id in artifact.evidence_ids:
        record = evidence_registry[evidence_id]
        if record.tenant_id != trusted_context.tenant_id:
            raise PolicyError("EVIDENCE_TENANT_MISMATCH")
        if not record.provenance_verified:
            raise PolicyError("UNVERIFIED_SOURCE_PROVENANCE")
        if record.capability not in agent.allowed_capabilities:
            raise PolicyError("ARTIFACT_CAPABILITY_DENIED")
        sources.append(record.source_id)
    for finding in artifact.findings:
        if not set(finding.evidence_ids).issubset(artifact.evidence_ids):
            raise PolicyError("UNGROUNDED_FINDING")

    return ArtifactValidation(
        artifact_id=artifact.artifact_id,
        validated_evidence_ids=artifact.evidence_ids,
        provenance_source_ids=tuple(sorted(set(sources))),
    )


def measure_handoff_information(
    required_facts: tuple[str, ...], transmitted_facts: Mapping[str, str]
) -> HandoffRecall:
    preserved = tuple(fact for fact in required_facts if transmitted_facts.get(fact))
    missing = tuple(fact for fact in required_facts if fact not in preserved)
    recall = len(preserved) / len(required_facts) if required_facts else 1.0
    return HandoffRecall(
        recall=recall, preserved_facts=preserved, missing_facts=missing
    )


def aggregate_fixture(fixture: ExecutionFixture) -> TimingSummary:
    """Track total work separately from conceptual parallel wall-clock time."""

    items = [item for batch in fixture.batches for item in batch]
    model_work = sum(item.model_work_ms for item in items)
    tool_work = sum(item.tool_work_ms for item in items)
    coordination_work = sum(item.handoff_serialization_ms for item in items)
    wall_clock = sum(max(item.elapsed_ms for item in batch) for batch in fixture.batches)
    input_tokens = sum(item.input_tokens for item in items)
    output_tokens = sum(item.output_tokens for item in items)
    coordination_tokens = sum(item.coordination_tokens for item in items)
    return TimingSummary(
        total_model_work_ms=model_work,
        total_tool_work_ms=tool_work,
        total_coordination_work_ms=coordination_work,
        total_work_ms=model_work + tool_work + coordination_work,
        wall_clock_latency_ms=wall_clock,
        critical_path_ms=wall_clock,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        coordination_tokens=coordination_tokens,
        total_tokens=input_tokens + output_tokens + coordination_tokens,
        cost_usd=round(sum(item.cost_usd for item in items), 6),
    )


def evaluate_routes(
    cases: tuple[RoutingCase, ...], decisions: Mapping[str, RouteDecision]
) -> RoutingMetrics:
    if set(decisions) != {case.route_case_id for case in cases}:
        raise PolicyError("INCOMPLETE_ROUTING_EVALUATION")
    exact = 0
    true_positive = predicted_total = expected_total = 0
    unknown_total = unknown_correct = 0
    for case in cases:
        decision = decisions[case.route_case_id]
        predicted = set(decision.agent_ids)
        expected = set(case.expected_agents)
        exact += int(
            decision.state == case.expected_state
            and predicted == expected
            and decision.approval_required == case.approval_gated
        )
        true_positive += len(predicted & expected)
        predicted_total += len(predicted)
        expected_total += len(expected)
        if case.expected_state is RouteState.UNKNOWN:
            unknown_total += 1
            unknown_correct += int(decision.state is RouteState.UNKNOWN)
    return RoutingMetrics(
        route_accuracy=exact / len(cases),
        agent_precision=true_positive / predicted_total if predicted_total else 1.0,
        agent_recall=true_positive / expected_total if expected_total else 1.0,
        unknown_detection=unknown_correct / unknown_total if unknown_total else 1.0,
        total_latency_ms=sum(decision.latency_ms for decision in decisions.values()),
        total_cost_usd=round(sum(decision.cost_usd for decision in decisions.values()), 6),
    )


def detect_conflicts(artifacts: tuple[SpecialistArtifact, ...]) -> tuple[Conflict, ...]:
    by_fact: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for artifact in artifacts:
        for finding in artifact.findings:
            by_fact[finding.fact_key][finding.fact_value].add(artifact.artifact_id)
    conflicts: list[Conflict] = []
    for fact_key, values in by_fact.items():
        if len(values) > 1:
            conflicts.append(
                Conflict(
                    fact_key=fact_key,
                    asserted_values=tuple(sorted(values)),
                    artifact_ids=tuple(
                        sorted({item for ids in values.values() for item in ids})
                    ),
                )
            )
    return tuple(conflicts)


def resolve_partial_failure(
    *,
    required_evidence_ids: tuple[str, ...],
    collected_evidence_ids: tuple[str, ...],
    failures: tuple[FailureEvent, ...],
) -> FailureResolution:
    missing = tuple(
        evidence_id
        for evidence_id in required_evidence_ids
        if evidence_id not in collected_evidence_ids
    )
    if not missing:
        return FailureResolution(
            action=(
                RecoveryAction.CONTINUE_DEGRADED
                if failures
                else RecoveryAction.FALLBACK
            ),
            status=(RunStatus.COMPLETED_DEGRADED if failures else RunStatus.COMPLETED),
            missing_required_evidence_ids=(),
        )
    if any(failure.code is FailureCode.AUTH_DENIED for failure in failures):
        action = RecoveryAction.HUMAN_REVIEW
        status = RunStatus.HUMAN_REVIEW
    else:
        action = RecoveryAction.ABSTAIN
        status = RunStatus.ABSTAINED
    return FailureResolution(
        action=action, status=status, missing_required_evidence_ids=missing
    )


class CoordinationLedger:
    """Application-owned duplicate-call detector."""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    def record(
        self,
        *,
        agent_id: str,
        task_id: str,
        inputs: Mapping[str, str],
        artifact_ids: tuple[str, ...],
    ) -> str:
        canonical = json.dumps(
            {
                "agent_id": agent_id,
                "task_id": task_id,
                "inputs": dict(sorted(inputs.items())),
                "artifact_ids": sorted(artifact_ids),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        key = sha256(canonical.encode()).hexdigest()
        if key in self._keys:
            raise PolicyError("DUPLICATE_COORDINATION")
        self._keys.add(key)
        return key


def architecture_gate(
    baseline: RunMetrics,
    candidate: RunMetrics,
    *,
    minimum_exposure_reduction: int = 2,
    max_cost_usd: float,
    latency_sla_ms: int,
) -> ArchitectureGate:
    reasons: list[str] = []
    benefit = (
        candidate.task_success > baseline.task_success
        or baseline.privileged_tool_exposure - candidate.privileged_tool_exposure
        >= minimum_exposure_reduction
    )
    if not benefit:
        reasons.append("NO_MEASURED_STRUCTURAL_BENEFIT")
    if candidate.grounding < baseline.grounding:
        reasons.append("GROUNDING_REGRESSION")
    if candidate.safety_violations > baseline.safety_violations:
        reasons.append("SAFETY_REGRESSION")
    if candidate.cost_usd > max_cost_usd:
        reasons.append("COST_BUDGET_EXCEEDED")
    if candidate.wall_clock_latency_ms > latency_sla_ms:
        reasons.append("LATENCY_SLA_EXCEEDED")
    accepted = not reasons
    return ArchitectureGate(
        accepted=accepted,
        verdict="SPLIT_JUSTIFIED" if accepted else "KEEP_SMALLER_ARCHITECTURE",
        reasons=tuple(reasons),
    )


def pareto_front(runs: tuple[ArchitectureRun, ...]) -> tuple[ArchitectureType, ...]:
    """Return non-dominated architectures for quality, latency, cost, and exposure."""

    def dominates(left: RunMetrics, right: RunMetrics) -> bool:
        no_worse = (
            left.task_success >= right.task_success
            and left.grounding >= right.grounding
            and left.wall_clock_latency_ms <= right.wall_clock_latency_ms
            and left.cost_usd <= right.cost_usd
            and left.privileged_tool_exposure <= right.privileged_tool_exposure
        )
        strictly_better = (
            left.task_success > right.task_success
            or left.grounding > right.grounding
            or left.wall_clock_latency_ms < right.wall_clock_latency_ms
            or left.cost_usd < right.cost_usd
            or left.privileged_tool_exposure < right.privileged_tool_exposure
        )
        return no_worse and strictly_better

    return tuple(
        run.architecture
        for run in runs
        if not any(
            other is not run and dominates(other.metrics, run.metrics) for other in runs
        )
    )
