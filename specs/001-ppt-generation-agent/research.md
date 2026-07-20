# Research: PPT Generation Agent

## Decision: Use the PPT-Agent Five-Phase Workflow as the Product Backbone

**Rationale**: `PPT-Agent-架构设计.md` already defines the business workflow in the right order: document analysis, template matching, content mapping, image generation, and PPT assembly. Keeping this as the backbone avoids inventing a parallel architecture and gives each phase a concrete input/output artifact.

**Alternatives considered**:

- Single monolithic generator: rejected because failures would be hard to resume or inspect.
- Template-only filler: rejected because it cannot meet the visual quality goal.
- Image-only deck generator: rejected because user-approved text would not remain editable.

## Decision: Implement Coordinator-Worker Orchestration

**Rationale**: `AGENT_ARCHITECTURE.md` requires a pure Coordinator that delegates to Workers. This maps cleanly to PPT generation because each phase needs different tools and context: OCR/document extraction, retrieval, template parsing, image generation, assembly, and verification.

**Alternatives considered**:

- One agent with all tools loaded: rejected due to context bloat and weaker isolation.
- Fully independent scripts with no Coordinator: rejected because user review, fallbacks, and resumability require phase state management.

## Decision: Add a Unified Dispatch Entry

**Rationale**: The Coordinator needs an explicit allocation entrance so phase routing, Worker selection, skill loading, permission policy, context compression, and fallback policy are not scattered across individual Workers. A `dispatcher` layer also makes it possible to add new skills or Workers through routing configuration.

**Alternatives considered**:

- Hardcode phase-to-worker calls inside the main workflow: rejected because it makes extension and fallback routing brittle.
- Let skills call other skills directly: rejected because it bypasses Coordinator visibility and weakens context/permission control.
- Use a heavyweight external workflow engine for v1: rejected because the architecture favors file-first, low-operational-overhead orchestration.

## Decision: Use File-System-First Persistence

**Rationale**: The architecture document explicitly prioritizes Markdown, JSONL, and JSON. PPT generation benefits from inspectable intermediate artifacts: `outline.json`, `slide_contents.json`, generated images, layer analysis, and validation reports.

**Alternatives considered**:

- Database-backed workflow state: rejected for v1 because it adds operational overhead without improving single-job reliability.
- In-memory-only state: rejected because long-running image generation and user review require resumability.

## Decision: Apply Five-Level Context Compression During Long Jobs

**Rationale**: PPT generation can involve long source documents, verbose OCR output, many image-generation logs, and multiple generated artifacts. The implementation must follow `AGENT_ARCHITECTURE.md` by summarizing old phases while preserving approved content, error records, and active results.

**Alternatives considered**:

- Keep all extracted text in every Worker context: rejected due to context pressure.
- Aggressively summarize all artifacts: rejected because approved slide content and error records must remain exact.

## Decision: Use Progressive Skill Loading

**Rationale**: PPT generation depends on several specialized skills, but not all phases need all skills. Loading `gptimage2-generator` only during visual generation preserves context and keeps credentials/API behavior isolated to the correct phase.

**Alternatives considered**:

- Load all skill documents at startup: rejected because the architecture explicitly avoids this.
- Inline skill behavior into core prompts: rejected because existing skill files and scripts already encode tested API behavior.

## Decision: Create Dedicated PPT Skill Packages

**Rationale**: The PPT workflow has clear phase boundaries and each phase benefits from its own reusable instructions, scripts, examples, and references. Creating `ppt-outline-generator`, `ppt-template-matcher`, `ppt-content-mapper`, `ppt-image-layer`, and `ppt-assembler` as skill packages keeps prompts smaller, makes behavior easier to tune independently, and follows the architecture's progressive disclosure model.

**Alternatives considered**:

- Implement all PPT behavior only inside `src/ppt_agent/workers`: rejected because it would make worker code and prompts harder to reuse and tune.
- Create one large `ppt-agent` skill: rejected because it would be loaded too often and would mix unrelated phase logic.
- Depend only on built-in presentation tooling: rejected because the project needs domain-specific outline, template matching, mapping, and validation behavior.

## Decision: Reuse Existing `gptimage2-generator` Skill Through an Adapter

**Rationale**: The installed skill already contains API reference documentation, account lifecycle behavior, reference-image upload, polling, download, and batch generation. The PPT-Agent should convert slide content into that skill's batch config rather than reimplementing the external integration.

**Alternatives considered**:

- Rewrite the GPTImage2 client inside the PPT-Agent: rejected because it duplicates behavior and risks drift from the skill.
- Skip visual generation in v1: rejected because visual quality is a core scenario, but fallback mode remains required.

## Decision: Use Hybrid Retrieval for Template Matching

**Rationale**: `AGENT_ARCHITECTURE.md` recommends dense retrieval plus BM25 plus RRF plus reranking. `PPT-Agent-架构设计.md` applies that directly to template metadata and previews. This improves matching across both exact tags and semantic descriptions.

**Alternatives considered**:

- Manual template selection only: rejected because the system should reduce user effort.
- Dense-only search: rejected because exact domain/style tags matter.
- Keyword-only search: rejected because descriptions and previews may use different terms.

## Decision: Make Knowledge Bases Configurable and Vector-Database Pluggable

**Rationale**: Template matching is only the first retrieval use case. The project will also need slide-pattern knowledge, domain storylines, and possibly user-provided template libraries. Retrieval should therefore be configured per knowledge base and routed through adapters for local indexes, Qdrant, Milvus, or pgvector instead of binding Worker code to one library's built-in vector store.

**Alternatives considered**:

- Use only an in-process local vector library: rejected because it limits scale and makes later database migration invasive.
- Hardcode one vector database: rejected because deployment environments may differ.
- Skip dense retrieval and rely on metadata filters: rejected because templates and slide patterns often need semantic matching.

## Decision: Keep Text Editable and Treat Generated Images as Visual Layers

**Rationale**: The spec requires user-approved text to remain editable. The plan therefore uses generated visuals for backgrounds, concepts, or regions, while slide titles and bullet content come from `slide_contents.json` during assembly.

**Alternatives considered**:

- Use whole-slide images for all pages: rejected because text would not be reliably editable or accurate.
- Avoid whole-slide images entirely: rejected because cover/transition/data-visual pages benefit from full-page visual generation.

## Decision: Add a Design Director Layer

**Rationale**: If the project aims for polished decks rather than simple template filling, the system needs a design planning step between outline/template matching and content mapping. This layer selects theme tokens, layout grammar, visual density, and expression patterns before mapping content to PPT zones. It borrows the strongest lesson from products such as Gamma: content should be structured first, expressed through a design system second, and exported last.

**Alternatives considered**:

- Rely only on static PPT templates: rejected because visual quality is capped by template pages and cannot adapt well to content intent.
- Let image generation create whole-slide designs: rejected because confirmed text would become inaccurate or non-editable.
- Add design checks only after assembly: rejected because scoring after the fact cannot choose better layouts before content is placed.

## Decision: Verification Runs as an Independent Worker

**Rationale**: `AGENT_ARCHITECTURE.md` emphasizes fresh-eyes verification. The verifier should inspect the final deck and reports independently from assembly to catch missing slides, text drift, overflow, image distortion, and unresolved fallback flags.

**Alternatives considered**:

- Self-check inside the assembler: rejected because it is less likely to catch assumptions made during assembly.
- No automated verification: rejected because the spec requires a validation report.
# Context-Aware Mapping Decisions (2026-07-18)

- **Decision**: Treat native PPT shape identity as the canonical zone address.
  **Rationale**: positional overlap is ambiguous on layered and grouped slides.
  **Alternatives considered**: enumeration ids and position-only matching.
- **Decision**: Let the Mapping Agent see template copy and all zones, with no
  per-slide zone-count limit. **Rationale**: template text reveals card, step,
  comparison, metric, and hierarchy semantics. **Alternatives considered**:
  fixed title/body/footer matching and role-only assignment.
- **Decision**: Separate creative decisions from deterministic enforcement.
  **Rationale**: Agent rewriting improves fit, while schema, source traceability,
  capacity checks, and strict assembly prevent hallucination and layout damage.
- **Decision**: Keep content-block planning inside Mapping for the first
  increment. **Rationale**: it validates the main quality hypothesis without
  adding a new workflow phase and dispatcher dependency.

# DeepSeek Integration Decisions (2026-07-19)

- **Decision**: Use JSON Mode for artifact generation and Tool Calls only for
  real external actions. **Rationale**: DeepSeek documents
  `response_format={"type":"json_object"}` for JSON output; strict Beta applies
  to Function argument schemas rather than OpenAI JSON Schema response format.
- **Decision**: Do not force `tool_choice` for thinking models. **Rationale**:
  thinking mode supports tools, but the production API rejected forced function
  selection and the official examples allow the model to select a tool.
- **Decision**: Validate artifact schemas locally and expose fallback state.
  **Rationale**: deterministic fallback is valuable but must not make an LLM
  failure appear to be a clean completed job.
