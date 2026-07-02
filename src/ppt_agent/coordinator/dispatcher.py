from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.event_bus import emit_event
from ppt_agent.coordinator.routing_rules import load_routes
from ppt_agent.models.dispatch import DispatchDecision, DispatchRequest


DEFAULT_WORKERS = {
    "document_analysis": "ppt_agent.workers.document_analyst:run",
    "outline_generation": "ppt_agent.workers.outline_generator:run",
    "template_matching": "ppt_agent.workers.template_matcher:run",
    "design_planning": "ppt_agent.workers.design_director:run",
    "content_mapping": "ppt_agent.workers.content_mapper:run",
    "visual_generation": "ppt_agent.workers.image_generator:run",
    "ppt_assembly": "ppt_agent.workers.ppt_assembler:run",
    "verification": "ppt_agent.workers.ppt_verifier:run",
}


def dispatch(job_root: Path, request: DispatchRequest) -> DispatchDecision:
    routes = load_routes()
    route = routes.get(request.capability, {})
    decision = DispatchDecision(
        job_id=request.job_id,
        phase=request.phase,
        worker=route.get("worker", DEFAULT_WORKERS.get(request.capability, DEFAULT_WORKERS.get(request.phase, ""))),
        skill=route.get("skill"),
        knowledge_base=route.get("knowledge_base"),
        expected_outputs=route.get("expected_outputs", []),
        permission_profile=route.get("permission_profile", "workspace-write"),
        context_bundle={
            "exact_artifacts": request.context_policy.get("exact_artifacts", []),
            "summary_artifacts": request.context_policy.get("summary_artifacts", []),
            "loaded_skills": [route["skill"]] if route.get("skill") else [],
        },
        retry_policy=route.get("retry_policy", {"max_attempts": 1}),
    )
    emit_event(job_root, request.phase, "progress", f"Dispatched {request.capability}", metadata=decision.to_dict())
    return decision
