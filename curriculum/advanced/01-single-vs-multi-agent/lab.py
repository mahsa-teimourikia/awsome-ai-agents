"""Credential-free Course 01 lab built on the shared policy implementation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Mapping

from policy import (
    AgentDefinition,
    ArchitectureRun,
    ArchitectureType,
    ArtifactFinding,
    ConcurrencyPolicy,
    ContextExperiment,
    DelegationBudget,
    EvidenceRecord,
    ExecutionFixture,
    FailureCode,
    FailureEvent,
    HandoffEnvelope,
    RouteDecision,
    RouteState,
    RoutingCase,
    RunMetrics,
    RunStatus,
    SpecialistArtifact,
    TaskCase,
    TopologyPolicy,
    TrustedContext,
    WorkItem,
    aggregate_fixture,
    evaluate_routes,
    measure_handoff_information,
    validate_artifact,
    validate_concurrency,
    validate_topology,
)


FIXED_TIME = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
NORTHSTAR_QUESTION = (
    "Why did EU checkout conversion fall after deploy-1842, and what should we do?"
)
REQUIRED_FACTS = (
    "tenant_id",
    "region",
    "deploy_id",
    "customer_tier",
    "incident_window",
)


def build_trusted_context() -> TrustedContext:
    return TrustedContext(
        tenant_id="northstar-commerce",
        user_id="incident-commander-17",
        authorization_capabilities=(
            "health.read",
            "logs.read",
            "deployment.read",
            "customer-impact.read",
            "runbook.read",
        ),
        incident_id="inc-eu-checkout-1842",
        region="EU",
        deploy_id="deploy-1842",
        customer_tier="enterprise",
        incident_window="2026-01-15T09:00:00Z/2026-01-15T10:00:00Z",
    )


def build_northstar_case() -> TaskCase:
    return TaskCase(
        case_id="northstar-eu-checkout",
        question=NORTHSTAR_QUESTION,
        tenant_id="northstar-commerce",
        required_evidence_ids=(
            "health",
            "logs",
            "deployment",
            "customer-impact",
            "current-runbook",
        ),
        required_capabilities=(
            "health.read",
            "logs.read",
            "deployment.read",
            "customer-impact.read",
            "runbook.read",
        ),
        expected_outcome=(
            "Identify deploy-1842 as the likely 3DS callback regression, quantify "
            "EU customer impact, and propose an approval-gated rollback investigation."
        ),
        risk_tier="HIGH",
    )


def build_evidence_registry() -> dict[str, EvidenceRecord]:
    common = {
        "tenant_id": "northstar-commerce",
        "observed_at": FIXED_TIME,
        "provenance_verified": True,
    }
    return {
        "health": EvidenceRecord(
            evidence_id="health",
            evidence_type="service-health",
            source_id="metrics/eu-checkout/2026-01-15T09",
            capability="health.read",
            facts={"conversion_drop": "38%", "service": "checkout-api"},
            **common,
        ),
        "logs": EvidenceRecord(
            evidence_id="logs",
            evidence_type="application-log",
            source_id="logs/checkout-api/3ds-callback/1842",
            capability="logs.read",
            facts={"failure_mode": "3DS callback signature mismatch"},
            **common,
        ),
        "deployment": EvidenceRecord(
            evidence_id="deployment",
            evidence_type="deployment-record",
            source_id="deployments/deploy-1842",
            capability="deployment.read",
            facts={"suspected_change": "deploy-1842", "component": "checkout-api"},
            **common,
        ),
        "customer-impact": EvidenceRecord(
            evidence_id="customer-impact",
            evidence_type="support-aggregate",
            source_id="support/eu-enterprise/window-0900",
            capability="customer-impact.read",
            facts={"affected_segment": "EU enterprise", "customer_tier": "enterprise"},
            **common,
        ),
        "current-runbook": EvidenceRecord(
            evidence_id="current-runbook",
            evidence_type="runbook",
            source_id="runbooks/checkout-3ds/v7",
            capability="runbook.read",
            facts={"recommended_next_step": "validate rollback candidate; seek approval"},
            **common,
        ),
    }


def build_agents() -> tuple[AgentDefinition, ...]:
    reads = (
        "health.read",
        "logs.read",
        "deployment.read",
        "customer-impact.read",
        "runbook.read",
    )
    return (
        AgentDefinition(
            agent_id="generalist",
            role="Own the bounded investigation without production writes.",
            allowed_capabilities=reads,
            allowed_handoffs=(),
            context_policy="trusted incident context plus scoped evidence",
            max_concurrency=1,
        ),
        AgentDefinition(
            agent_id="manager",
            role="Retain control and synthesize validated specialist artifacts.",
            allowed_capabilities=reads,
            allowed_handoffs=(
                "observability-specialist",
                "deployment-specialist",
                "customer-specialist",
                "incident-specialist",
            ),
            context_policy="trusted context plus validated artifact summaries",
            max_concurrency=3,
        ),
        AgentDefinition(
            agent_id="observability-specialist",
            role="Analyze health, logs, and the diagnostic runbook.",
            allowed_capabilities=("health.read", "logs.read", "runbook.read"),
            allowed_handoffs=("manager",),
            context_policy="projected observability facts only",
            max_concurrency=1,
        ),
        AgentDefinition(
            agent_id="deployment-specialist",
            role="Analyze the deployment record.",
            allowed_capabilities=("deployment.read",),
            allowed_handoffs=("manager",),
            context_policy="projected deployment facts only",
            max_concurrency=1,
        ),
        AgentDefinition(
            agent_id="customer-specialist",
            role="Analyze aggregated customer impact.",
            allowed_capabilities=("customer-impact.read",),
            allowed_handoffs=("manager",),
            context_policy="projected aggregate impact only",
            max_concurrency=1,
        ),
        AgentDefinition(
            agent_id="incident-specialist",
            role="Take ownership of the complete read-only incident investigation.",
            allowed_capabilities=reads,
            allowed_handoffs=(),
            context_policy="trusted context plus all required evidence",
            max_concurrency=1,
        ),
        AgentDefinition(
            agent_id="production-executor",
            role="Out-of-scope production execution boundary.",
            allowed_capabilities=("production.execute",),
            allowed_handoffs=(),
            context_policy="validated approval only; unavailable in the core lab",
            max_concurrency=1,
        ),
    )


def agent_by_id(agent_id: str) -> AgentDefinition:
    return next(agent for agent in build_agents() if agent.agent_id == agent_id)


def build_budget() -> DelegationBudget:
    return DelegationBudget(
        max_agent_invocations=5,
        max_handoffs=4,
        max_depth=3,
        max_parallel_agents=3,
        max_total_cost=0.08,
        deadline_ms=3_000,
    )


def build_concurrency_policy() -> ConcurrencyPolicy:
    return ConcurrencyPolicy(
        max_parallel_specialists=3,
        rate_limit_groups={
            "observability-specialist": "telemetry-read",
            "deployment-specialist": "change-read",
            "customer-specialist": "support-read",
        },
        shared_dependencies={
            "observability-specialist": ("model-quota", "telemetry-api"),
            "deployment-specialist": ("model-quota", "deployment-api"),
            "customer-specialist": ("model-quota", "support-api"),
        },
        max_per_rate_limit_group=1,
        max_per_shared_dependency=3,
    )


def _finding(evidence: EvidenceRecord, index: int) -> ArtifactFinding:
    fact_key, fact_value = next(iter(evidence.facts.items()))
    return ArtifactFinding(
        finding_id=f"finding-{evidence.evidence_id}-{index}",
        statement=f"{fact_key} is {fact_value}",
        fact_key=fact_key,
        fact_value=fact_value,
        evidence_ids=(evidence.evidence_id,),
    )


def build_artifact(
    agent_id: str,
    evidence_ids: tuple[str, ...],
    *,
    tenant_id: str = "northstar-commerce",
    findings: tuple[ArtifactFinding, ...] | None = None,
) -> SpecialistArtifact:
    registry = build_evidence_registry()
    return SpecialistArtifact(
        artifact_id=f"artifact-{agent_id}-{'-'.join(evidence_ids)}",
        task_id="northstar-eu-checkout",
        agent_id=agent_id,
        tenant_id=tenant_id,
        evidence_ids=evidence_ids,
        findings=(
            findings
            if findings is not None
            else tuple(_finding(registry[evidence_id], index) for index, evidence_id in enumerate(evidence_ids))
        ),
        assumptions=("Correlation requires bounded reconciliation before causation.",),
        unresolved_questions=("Did a pre-deploy control cohort show the same callback error?",),
        confidence_label="HIGH" if len(evidence_ids) >= 2 else "MEDIUM",
        created_at=FIXED_TIME,
    )


def build_specialist_artifacts() -> tuple[SpecialistArtifact, ...]:
    return (
        build_artifact(
            "observability-specialist", ("health", "logs", "current-runbook")
        ),
        build_artifact("deployment-specialist", ("deployment",)),
        build_artifact("customer-specialist", ("customer-impact",)),
    )


def validate_northstar_artifacts(
    artifacts: tuple[SpecialistArtifact, ...],
    expected_agent_ids: tuple[str, ...],
) -> tuple[str, ...]:
    if len(artifacts) != len(expected_agent_ids):
        raise ValueError("each artifact requires a trusted invocation identity")
    task = build_northstar_case()
    context = build_trusted_context()
    registry = build_evidence_registry()
    return tuple(
        validate_artifact(
            artifact,
            task=task,
            trusted_context=context,
            agent=agent_by_id(expected_agent_id),
            evidence_registry=registry,
        ).artifact_id
        for artifact, expected_agent_id in zip(artifacts, expected_agent_ids, strict=True)
    )


def build_handoff(
    *,
    from_agent: str = "manager",
    to_agent: str = "incident-specialist",
    depth: int = 1,
    tenant_id: str = "northstar-commerce",
    handoff_id: str = "handoff-manager-incident",
) -> HandoffEnvelope:
    return HandoffEnvelope(
        handoff_id=handoff_id,
        task_id="northstar-eu-checkout",
        from_agent=from_agent,
        to_agent=to_agent,
        tenant_id=tenant_id,
        required_facts=REQUIRED_FACTS,
        artifact_ids=(),
        reason="Transfer active incident ownership to the bounded specialist.",
        depth=depth,
        deadline=FIXED_TIME + timedelta(minutes=5),
    )


def build_architecture_fixtures() -> dict[ArchitectureType, ExecutionFixture]:
    def item(
        operation_id: str,
        model_ms: int,
        tool_ms: int,
        input_tokens: int,
        output_tokens: int,
        coordination_tokens: int,
        serialization_ms: int,
        cost: float,
    ) -> WorkItem:
        return WorkItem(
            operation_id=operation_id,
            model_work_ms=model_ms,
            tool_work_ms=tool_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            coordination_tokens=coordination_tokens,
            handoff_serialization_ms=serialization_ms,
            cost_usd=cost,
        )

    return {
        ArchitectureType.SINGLE_GENERALIST: ExecutionFixture(
            batches=((item("generalist", 520, 310, 1700, 360, 0, 0, 0.012),),)
        ),
        ArchitectureType.SINGLE_DYNAMIC_TOOLS: ExecutionFixture(
            batches=((item("dynamic-generalist", 500, 310, 1250, 350, 40, 0, 0.010),),)
        ),
        ArchitectureType.PIPELINE: ExecutionFixture(
            batches=(
                (item("investigate", 240, 240, 900, 180, 0, 0, 0.006),),
                (item("review", 220, 80, 700, 150, 80, 20, 0.005),),
                (item("synthesize", 260, 0, 850, 240, 90, 20, 0.006),),
            )
        ),
        ArchitectureType.MANAGER_SPECIALISTS: ExecutionFixture(
            batches=(
                (item("manager-plan", 220, 0, 850, 120, 100, 0, 0.005),),
                (item("observability", 310, 180, 650, 160, 90, 30, 0.006),),
                (item("deployment", 250, 100, 500, 130, 80, 30, 0.004),),
                (item("customer", 250, 100, 520, 130, 80, 30, 0.004),),
                (item("manager-synthesis", 300, 0, 1050, 280, 150, 20, 0.007),),
            )
        ),
        ArchitectureType.HANDOFF: ExecutionFixture(
            batches=(
                (item("triage", 230, 0, 700, 100, 90, 30, 0.004),),
                (item("incident-specialist", 430, 310, 1450, 330, 100, 40, 0.011),),
            )
        ),
        ArchitectureType.PARALLEL_SPECIALISTS: ExecutionFixture(
            batches=(
                (item("manager-plan", 180, 0, 700, 100, 90, 0, 0.004),),
                (
                    item("observability", 300, 180, 620, 150, 80, 30, 0.006),
                    item("deployment", 250, 100, 480, 120, 70, 30, 0.004),
                    item("customer", 250, 100, 500, 120, 70, 30, 0.004),
                ),
                (item("synthesis", 280, 0, 980, 260, 140, 20, 0.007),),
            )
        ),
    }


def run_architecture(
    architecture: ArchitectureType,
    *,
    task: TaskCase | None = None,
) -> ArchitectureRun:
    """Run the same deterministic Northstar case through one architecture."""

    task = task or build_northstar_case()
    if task.case_id != "northstar-eu-checkout":
        raise ValueError("core architecture comparison uses the identical Northstar case")

    artifacts: tuple[SpecialistArtifact, ...]
    owner = "generalist"
    handoffs = 0
    exposure = 5
    quality = 0.92
    grounding = 1.0
    if architecture is ArchitectureType.SINGLE_GENERALIST:
        artifacts = (build_artifact("generalist", task.required_evidence_ids),)
        expected_agent_ids = ("generalist",)
    elif architecture is ArchitectureType.SINGLE_DYNAMIC_TOOLS:
        artifacts = (build_artifact("generalist", task.required_evidence_ids),)
        expected_agent_ids = ("generalist",)
        exposure = 3
        quality = 0.94
    elif architecture is ArchitectureType.PIPELINE:
        artifacts = (build_artifact("generalist", task.required_evidence_ids),)
        expected_agent_ids = ("generalist",)
        exposure = 3
        quality = 0.96
    elif architecture is ArchitectureType.HANDOFF:
        artifacts = (build_artifact("incident-specialist", task.required_evidence_ids),)
        expected_agent_ids = ("incident-specialist",)
        envelope = build_handoff()
        validate_topology(
            agents=build_agents(),
            handoffs=(envelope,),
            delegated_capabilities={envelope.handoff_id: task.required_capabilities},
            task=task,
            trusted_context=build_trusted_context(),
            budget=build_budget(),
            estimated_cost=0.015,
            estimated_wall_clock_ms=1_100,
            now=FIXED_TIME,
        )
        owner = "incident-specialist"
        handoffs = 1
        exposure = 5
        quality = 0.97
    else:
        artifacts = build_specialist_artifacts()
        expected_agent_ids = (
            "observability-specialist",
            "deployment-specialist",
            "customer-specialist",
        )
        if architecture is ArchitectureType.PARALLEL_SPECIALISTS:
            validate_concurrency(
                (
                    "observability-specialist",
                    "deployment-specialist",
                    "customer-specialist",
                ),
                agents=build_agents(),
                policy=build_concurrency_policy(),
            )
        owner = "manager"
        handoffs = 3
        exposure = 3
        quality = 0.98

    validated_ids = validate_northstar_artifacts(artifacts, expected_agent_ids)
    timing = aggregate_fixture(build_architecture_fixtures()[architecture])
    model_calls = sum(len(batch) for batch in build_architecture_fixtures()[architecture].batches)
    tool_calls = len({evidence_id for artifact in artifacts for evidence_id in artifact.evidence_ids})
    cost_per_success = round(timing.cost_usd / quality, 6)
    metrics = RunMetrics(
        task_success=quality,
        grounding=grounding,
        required_evidence_recall=len(
            {evidence_id for artifact in artifacts for evidence_id in artifact.evidence_ids}
            & set(task.required_evidence_ids)
        )
        / len(task.required_evidence_ids),
        handoff_information_recall=(1.0 if handoffs else 1.0),
        route_accuracy=1.0,
        conflict_rate=0.0,
        model_calls=model_calls,
        tool_calls=tool_calls,
        handoff_count=handoffs,
        coordination_tokens=timing.coordination_tokens,
        total_tokens=timing.total_tokens,
        total_model_work_ms=timing.total_model_work_ms,
        total_tool_work_ms=timing.total_tool_work_ms,
        total_coordination_work_ms=timing.total_coordination_work_ms,
        total_work_ms=timing.total_work_ms,
        wall_clock_latency_ms=timing.wall_clock_latency_ms,
        critical_path_ms=timing.critical_path_ms,
        cost_usd=timing.cost_usd,
        cost_per_compliant_success=cost_per_success,
        privileged_tool_exposure=exposure,
        duplicate_coordination=0,
        failure_recovery_rate=1.0,
        safety_violations=0,
    )
    return ArchitectureRun(
        architecture=architecture,
        case_id=task.case_id,
        initial_owner="manager" if architecture is ArchitectureType.HANDOFF else owner,
        active_owner=owner,
        validated_artifact_ids=validated_ids,
        status=RunStatus.COMPLETED,
        application_completed=True,
        metrics=metrics,
    )


def compare_all_architectures() -> tuple[ArchitectureRun, ...]:
    return tuple(run_architecture(architecture) for architecture in ArchitectureType)


def build_routing_cases() -> tuple[RoutingCase, ...]:
    return (
        RoutingCase(
            route_case_id="faq",
            query="What does the EU checkout runbook say?",
            expected_state=RouteState.SINGLE_ROUTE,
            expected_agents=("observability-specialist",),
        ),
        RoutingCase(
            route_case_id="deploy",
            query="Which release changed checkout?",
            expected_state=RouteState.SINGLE_ROUTE,
            expected_agents=("deployment-specialist",),
        ),
        RoutingCase(
            route_case_id="cross-domain",
            query="Correlate checkout errors, deploy-1842, and customer impact.",
            expected_state=RouteState.MULTI_ROUTE,
            expected_agents=(
                "customer-specialist",
                "deployment-specialist",
                "observability-specialist",
            ),
        ),
        RoutingCase(
            route_case_id="ambiguous",
            query="Investigate the thing customers mentioned.",
            expected_state=RouteState.AMBIGUOUS,
            expected_agents=(),
        ),
        RoutingCase(
            route_case_id="unknown",
            query="Forecast next season's fashion colors.",
            expected_state=RouteState.UNKNOWN,
            expected_agents=(),
        ),
        RoutingCase(
            route_case_id="approval",
            query="Roll back deploy-1842 in production.",
            expected_state=RouteState.SINGLE_ROUTE,
            expected_agents=("production-executor",),
            approval_gated=True,
        ),
    )


def deterministic_router(case: RoutingCase) -> RouteDecision:
    """A transparent rule router; rules are fixtures, not universal heuristics."""

    text = case.query.lower()
    if "roll back" in text or "rollback" in text:
        state, agents, approval = RouteState.SINGLE_ROUTE, ("production-executor",), True
    else:
        selected = []
        if any(word in text for word in ("error", "runbook", "health", "log")):
            selected.append("observability-specialist")
        if any(word in text for word in ("deploy", "release")):
            selected.append("deployment-specialist")
        if "customer impact" in text:
            selected.append("customer-specialist")
        agents = tuple(sorted(set(selected)))
        approval = False
        if len(agents) > 1:
            state = RouteState.MULTI_ROUTE
        elif len(agents) == 1:
            state = RouteState.SINGLE_ROUTE
        elif "thing customers mentioned" in text:
            state = RouteState.AMBIGUOUS
        else:
            state = RouteState.UNKNOWN
    return RouteDecision(
        state=state,
        agent_ids=agents,
        approval_required=approval,
        router_name="deterministic-rule",
        latency_ms=2,
        cost_usd=0.0,
    )


def fixture_router(case: RoutingCase, router_name: str) -> RouteDecision:
    """Frozen semantic/LLM/manager outputs for reproducible comparison."""

    costs = {
        "semantic-classifier": (18, 0.0002),
        "llm-router": (240, 0.0020),
        "manager-delegation": (330, 0.0030),
    }
    latency, cost = costs[router_name]
    return RouteDecision(
        state=case.expected_state,
        agent_ids=case.expected_agents,
        approval_required=case.approval_gated,
        router_name=router_name,
        latency_ms=latency,
        cost_usd=cost,
    )


def compare_routers() -> dict[str, object]:
    cases = build_routing_cases()
    names = (
        "deterministic-rule",
        "semantic-classifier",
        "llm-router",
        "manager-delegation",
    )
    results: dict[str, object] = {}
    for name in names:
        decisions = {
            case.route_case_id: (
                deterministic_router(case)
                if name == "deterministic-rule"
                else fixture_router(case, name)
            )
            for case in cases
        }
        results[name] = evaluate_routes(cases, decisions)
    return results


def context_projection_experiment() -> tuple[ContextExperiment, ContextExperiment]:
    trusted = build_trusted_context()
    full_facts = trusted.model_dump()
    projected = {fact: full_facts[fact] for fact in REQUIRED_FACTS}
    full_recall = measure_handoff_information(REQUIRED_FACTS, full_facts)
    projected_recall = measure_handoff_information(REQUIRED_FACTS, projected)
    return (
        ContextExperiment(
            mode="full-transcript",
            tokens=2_400,
            sensitive_fields_exposed=3,
            handoff_information_recall=full_recall.recall,
        ),
        ContextExperiment(
            mode="task-scoped-projection",
            tokens=820,
            sensitive_fields_exposed=0,
            handoff_information_recall=projected_recall.recall,
        ),
    )


def apply_untrusted_context_claim(
    trusted_context: TrustedContext, claim: Mapping[str, str]
) -> TrustedContext:
    """Model output cannot overwrite immutable application context."""

    del claim
    return trusted_context


def malicious_handoff_attempt() -> None:
    envelope = build_handoff(
        from_agent="observability-specialist",
        to_agent="production-executor",
        handoff_id="malicious-production-transfer",
    )
    validate_topology(
        agents=build_agents(),
        handoffs=(envelope,),
        delegated_capabilities={envelope.handoff_id: ("production.execute",)},
        task=build_northstar_case(),
        trusted_context=build_trusted_context(),
        budget=build_budget(),
        policy=TopologyPolicy(),
        now=FIXED_TIME,
    )


def completion_from_specialist_text(text: str) -> bool:
    """Specialist text is data; only the workflow may mark global completion."""

    del text
    return False


def build_conflicting_artifacts() -> tuple[SpecialistArtifact, ...]:
    observation = ArtifactFinding(
        finding_id="finding-observed-component",
        statement="The callback error is emitted by Redis.",
        fact_key="suspected_component",
        fact_value="redis",
        evidence_ids=("logs",),
    )
    deployment = ArtifactFinding(
        finding_id="finding-deployed-component",
        statement="deploy-1842 changed checkout-api.",
        fact_key="suspected_component",
        fact_value="checkout-api",
        evidence_ids=("deployment",),
    )
    return (
        build_artifact(
            "observability-specialist", ("logs",), findings=(observation,)
        ),
        build_artifact(
            "deployment-specialist", ("deployment",), findings=(deployment,)
        ),
    )


def build_evaluation_cases() -> tuple[TaskCase, ...]:
    northstar = build_northstar_case()
    return (
        northstar.model_copy(
            update={
                "case_id": "simple-faq",
                "question": "What is the current checkout runbook version?",
                "required_evidence_ids": ("current-runbook",),
                "required_capabilities": ("runbook.read",),
                "risk_tier": "LOW",
            }
        ),
        northstar.model_copy(
            update={
                "case_id": "single-domain-incident",
                "question": "What do the checkout logs show?",
                "required_evidence_ids": ("logs",),
                "required_capabilities": ("logs.read",),
                "risk_tier": "MEDIUM",
            }
        ),
        northstar,
        northstar.model_copy(
            update={
                "case_id": "security-sensitive",
                "question": "Roll back deploy-1842 in production.",
                "expected_outcome": "Require validated approval; do not execute.",
            }
        ),
        northstar.model_copy(
            update={
                "case_id": "multi-route",
                "question": "Correlate health, deployment, and customer impact.",
            }
        ),
        northstar.model_copy(
            update={
                "case_id": "out-of-domain",
                "question": "Forecast next season's fashion colors.",
                "required_evidence_ids": (),
                "required_capabilities": (),
                "expected_outcome": "Return UNKNOWN and abstain.",
                "risk_tier": "LOW",
            }
        ),
    )


def do_not_split_simple_faq() -> str:
    return "KEEP_SINGLE"


def pipeline_not_team() -> str:
    return "PIPELINE"


def failure_examples() -> tuple[FailureEvent, ...]:
    return tuple(
        FailureEvent(code=code, agent_id="observability-specialist")
        for code in FailureCode
    )


if __name__ == "__main__":
    print("Northstar EU checkout architecture comparison (deterministic fixtures)")
    print("architecture | success | tokens | work_ms | wall_ms | cost_usd | exposure")
    for architecture_run in compare_all_architectures():
        metric = architecture_run.metrics
        print(
            f"{architecture_run.architecture.value:24} | "
            f"{metric.task_success:.2f} | {metric.total_tokens:6} | "
            f"{metric.total_work_ms:7} | {metric.wall_clock_latency_ms:7} | "
            f"{metric.cost_usd:8.3f} | {metric.privileged_tool_exposure}"
        )
