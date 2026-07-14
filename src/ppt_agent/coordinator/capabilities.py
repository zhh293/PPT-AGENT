"""AgentCapability registry — declarative definitions of all 8 PPT pipeline capabilities.

Each capability declares its inputs, outputs, allowed tools, max turns,
and fallback module.  The Coordinator uses this registry to decide which
workers to spawn and the WorkerAgent uses it to configure its tool set.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentCapability:
    """Declarative definition of an agent capability."""

    capability_id: str
    description: str
    input_artifacts: list[str]       # required input artifact names
    output_artifacts: list[str]      # produced artifact names
    tools: list[str]                 # allowed tool names
    max_turns: int = 10
    fallback_module: str = ""        # "ppt_agent.workers.<name>" for deterministic fallback
    reads_input_dir: bool = False
    skill_name: str = ""


# ── 8 capabilities in pipeline order ─────────────────────────────────

DOCUMENT_ANALYSIS = AgentCapability(
    capability_id="document_analysis",
    description="Analyze uploaded project materials and produce a structured source summary.",
    input_artifacts=[],
    output_artifacts=["source_summary"],
    tools=["read_input_files", "read_artifact", "compose_artifact", "write_artifact", "validate_output", "search_knowledge_base"],
    max_turns=8,
    fallback_module="ppt_agent.workers.document_analyst",
    reads_input_dir=True,
)

OUTLINE_GENERATION = AgentCapability(
    capability_id="outline_generation",
    description="Create a structured presentation outline from the source summary.",
    input_artifacts=["source_summary"],
    output_artifacts=["outline"],
    tools=["read_artifact", "compose_artifact", "write_artifact", "validate_output", "search_knowledge_base", "load_skill"],
    max_turns=10,
    fallback_module="ppt_agent.workers.outline_generator",
    skill_name="ppt-outline-generator",
)

TEMPLATE_MATCHING = AgentCapability(
    capability_id="template_matching",
    description="Search and select the best template for the presentation.",
    input_artifacts=["outline"],
    output_artifacts=["selected_template", "template_meta", "template_zones"],
    tools=["read_artifact", "write_artifact", "validate_output", "search_templates"],
    max_turns=8,
    fallback_module="ppt_agent.workers.template_matcher",
    skill_name="ppt-template-matcher",
)

DESIGN_PLANNING = AgentCapability(
    capability_id="design_planning",
    description="Create a detailed slide design plan with theme, layouts, and visual density.",
    input_artifacts=["outline", "template_meta"],
    output_artifacts=["slide_design_plan"],
    tools=["read_artifact", "compose_artifact", "write_artifact", "validate_output", "load_skill"],
    max_turns=10,
    fallback_module="ppt_agent.workers.design_director",
    skill_name="ppt-design-director",
)

CONTENT_MAPPING = AgentCapability(
    capability_id="content_mapping",
    description="Map outline content into template zones, producing user-reviewable slide contents.",
    input_artifacts=["outline", "selected_template", "slide_design_plan", "source_summary", "template_zones"],
    output_artifacts=["slide_contents"],
    tools=["read_artifact", "compose_artifact", "write_artifact", "validate_output", "search_knowledge_base", "load_skill"],
    max_turns=12,
    fallback_module="ppt_agent.workers.content_mapper",
    skill_name="ppt-content-mapper",
)

VISUAL_GENERATION = AgentCapability(
    capability_id="visual_generation",
    description="Generate AI images for slides using the gptimage2-generator skill.",
    input_artifacts=["slide_contents", "template_zones"],
    output_artifacts=["image_generation_report"],
    tools=["run_shell_command", "check_file_exists", "list_directory", "read_artifact", "write_artifact", "validate_output"],
    max_turns=15,
    fallback_module="ppt_agent.workers.image_generator",
    skill_name="gptimage2-generator",
)

PPT_ASSEMBLY = AgentCapability(
    capability_id="ppt_assembly",
    description="Assemble the final PowerPoint file from slide contents and images.",
    input_artifacts=["slide_contents"],
    output_artifacts=[],  # produces final.pptx (not a JSON artifact)
    tools=["read_artifact", "assemble_pptx", "check_file", "write_artifact", "validate_output"],
    max_turns=8,
    fallback_module="ppt_agent.workers.ppt_assembler",
    skill_name="ppt-assembler",
)

QUALITY_VERIFICATION = AgentCapability(
    capability_id="verification",
    description="Verify the final PPT with fresh eyes — check content, visuals, and accuracy.",
    input_artifacts=["slide_contents", "image_generation_report"],
    output_artifacts=["validation_report"],
    tools=["read_artifact", "write_artifact", "validate_output", "check_file", "check_file_exists"],
    max_turns=8,
    fallback_module="ppt_agent.workers.ppt_verifier",
)

# ── Registry ─────────────────────────────────────────────────────────

CAPABILITY_REGISTRY: dict[str, AgentCapability] = {
    c.capability_id: c for c in [
        DOCUMENT_ANALYSIS, OUTLINE_GENERATION, TEMPLATE_MATCHING,
        DESIGN_PLANNING, CONTENT_MAPPING, VISUAL_GENERATION,
        PPT_ASSEMBLY, QUALITY_VERIFICATION,
    ]
}

CAPABILITIES_IN_ORDER = list(CAPABILITY_REGISTRY.values())


def get_capability(capability_id: str) -> AgentCapability:
    if capability_id not in CAPABILITY_REGISTRY:
        raise KeyError(f"Unknown capability: {capability_id}. Available: {list(CAPABILITY_REGISTRY)}")
    return CAPABILITY_REGISTRY[capability_id]
