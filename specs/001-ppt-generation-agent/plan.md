# Implementation Plan: PPT Generation Agent

**Branch**: `` | **Date**: 2026-07-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-ppt-generation-agent/spec.md`

## Summary

Build a file-system-first PPT generation agent that turns project materials into an editable presentation through the five-stage workflow defined in `PPT-Agent-架构设计.md`: document analysis and outline generation, template matching, template parsing and content mapping, visual generation, and PPT assembly plus verification. The engineering architecture follows `AGENT_ARCHITECTURE.md`: a pure Coordinator orchestrates specialized Workers, intermediate state is stored as inspectable JSON/Markdown artifacts, skills are progressively loaded on demand, context is managed with graduated compression, and external visual generation reuses the existing `.catpaw/skills/gptimage2-generator` skill.

## Technical Context

**Language/Version**: Python 3.11+ for document/PPT/image processing workers and CLI orchestration; TypeScript/Node.js optional for future UI or event-stream frontend. Initial implementation can ship as CLI + file workspace without a web UI.

**Primary Dependencies**: `python-pptx` or LibreOffice bridge for PPT assembly, PyMuPDF and python-docx for source extraction, OCR engine for image text extraction, Pillow/OpenCV for image inspection, rank/BM25 library for sparse retrieval, configurable embedding provider, pluggable vector database adapter, existing `gptimage2-generator` skill for image generation, and a lightweight agent runtime layer for Coordinator/Worker execution.

**Storage**: File-system-first for workflow artifacts. Workspace artifacts live under feature/job directories as Markdown, JSON, JSONL, PPTX, and image files. Knowledge-base indexes are managed through a retrieval abstraction: v1 may use local file indexes for development, but the architecture must support vector database backends through configuration without changing Worker code.

**Testing**: Unit tests for parsers, schema validation, template scoring, content mapping, and fallback decisions; integration tests for the end-to-end five-stage workflow using fixture documents/templates; contract tests for JSON artifacts and skill invocation payloads; visual/PPT verification tests that render slides and inspect text/image layout.

**Target Platform**: Local workstation or server worker environment with sandboxed job directories. Network access is only required for optional external visual generation; fallback mode must work without it.

**Project Type**: Agent-powered CLI/workflow service with reusable skills and file-based workspaces. A web UI can be layered on top later but is not required for the core workflow.

**Performance Goals**: Produce a 10-15 slide first draft within 15 minutes excluding user review time; complete document analysis and outline generation within 30 seconds for typical inputs; template retrieval within 5 seconds; content mapping within 40 seconds; final assembly and validation within 90 seconds after visuals are available.

**Constraints**: Preserve user-approved text as editable PPT text; avoid factual hallucinations by tying claims to source materials or marking suggestions; limit image generation concurrency to 2-3 jobs; keep all intermediate artifacts inspectable and resumable; support fallback generation when template matching, OCR, image generation, or layer analysis fails.

**Scale/Scope**: v1 targets individual presentation jobs of 8-20 slides, business/project-report/competition/pitch-style decks, and a curated template library organized by domain and style. Batch multi-user scheduling and advanced template self-training are out of scope for v1.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The current `.specify/memory/constitution.md` is still the default placeholder and contains no enforceable project principles. For this plan, gates are derived from the supplied architecture documents:

- File-system-first persistence: PASS. All job state, conversation/session artifacts, generated content, and reports are files.
- Progressive skill loading: PASS. PPT-specific skills are loaded only by phase; the existing `gptimage2-generator` skill is only required during visual generation.
- Coordinator purity: PASS. Coordinator owns phase routing, dependency ordering, and result aggregation; Workers perform file, OCR, retrieval, image, and PPT operations.
- Context compression discipline: PASS. Long jobs must persist phase summaries and avoid loading full tool outputs or full document bodies into every Worker context.
- Fallback-first delivery: PASS. Every risky external or visual step has a draft-producing fallback.

No complexity violations are introduced at this stage. The missing real constitution should be addressed before hardening the implementation process.

## Project Structure

### Documentation (this feature)

```text
specs/001-ppt-generation-agent/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── outline.schema.json
│   ├── source-summary.schema.json
│   ├── slide-contents.schema.json
│   ├── template-meta.schema.json
│   ├── selected-template.schema.json
│   ├── image-generation-config.schema.json
│   ├── image-generation-report.schema.json
│   ├── validation-report.schema.json
│   ├── slide-design-plan.schema.json
│   ├── workflow-events.schema.json
│   ├── knowledge-base-config.schema.json
│   ├── dispatch-request.schema.json
│   └── dispatch-decision.schema.json
└── tasks.md
```

### Source Code (repository root)

```text
src/
├── ppt_agent/
│   ├── coordinator/
│   │   ├── workflow.py
│   │   ├── phase_state.py
│   │   ├── dispatcher.py
│   │   ├── routing_rules.py
│   │   └── event_bus.py
│   ├── context/
│   │   ├── memory_layers.py
│   │   ├── compression.py
│   │   ├── session_summary.py
│   │   └── dream.py
│   ├── runtime/
│   │   ├── task_types.py
│   │   ├── task_manager.py
│   │   ├── coordinator_tools.py
│   │   ├── mailbox.py
│   │   └── agent_context.py
│   ├── skills/
│   │   ├── registry.py
│   │   ├── loader.py
│   │   └── adapters/
│   │       └── gptimage2.py
│   ├── tools/
│   │   ├── registry.py
│   │   ├── permissions.py
│   │   ├── execution.py
│   │   ├── filesystem.py
│   │   ├── artifacts.py
│   │   ├── documents.py
│   │   ├── ocr.py
│   │   ├── skill_script.py
│   │   ├── image_generation.py
│   │   ├── ppt.py
│   │   └── verification.py
│   ├── workers/
│   │   ├── document_analyst.py
│   │   ├── outline_generator.py
│   │   ├── template_matcher.py
│   │   ├── design_director.py
│   │   ├── content_mapper.py
│   │   ├── image_generator.py
│   │   ├── ppt_assembler.py
│   │   └── ppt_verifier.py
│   ├── retrieval/
│   │   ├── config.py
│   │   ├── ingestion.py
│   │   ├── chunking.py
│   │   ├── enrichment.py
│   │   ├── query_router.py
│   │   ├── template_index.py
│   │   ├── sparse/
│   │   │   └── bm25.py
│   │   ├── vector/
│   │   │   ├── base.py
│   │   │   ├── local.py
│   │   │   ├── qdrant.py
│   │   │   ├── milvus.py
│   │   │   └── pgvector.py
│   │   ├── fusion/
│   │   │   └── rrf.py
│   │   └── rerankers/
│   │       └── base.py
│   ├── models/
│   │   ├── artifacts.py
│   │   ├── source_summary.py
│   │   ├── outline.py
│   │   ├── slide_contents.py
│   │   ├── template_meta.py
│   │   ├── selected_template.py
│   │   ├── image_generation.py
│   │   ├── design_plan.py
│   │   └── validation.py
│   ├── design/
│   │   ├── theme.py
│   │   ├── layout_grammar.py
│   │   ├── visual_density.py
│   │   ├── design_scorer.py
│   │   └── rewrite_suggestions.py
│   ├── assembly/
│   │   ├── ppt_writer.py
│   │   ├── layout_fit.py
│   │   └── render_verify.py
│   └── cli.py
templates/
├── index.json
└── <domain>/<template-id>/
    ├── template.pptx
    ├── meta.json
    └── preview/
workspace/
└── jobs/
    └── <job-id>/
        ├── input/
        ├── source_summary.json
        ├── outline.json
        ├── selected_template.json
        ├── slide_design_plan.json
        ├── template_meta.json
        ├── slide_contents.json
        ├── generated_slides/
        ├── image_generation_config.json
        ├── image_generation_report.json
        ├── layer_analysis/
        ├── final.pptx
        └── validation_report.json
tests/
├── contract/
├── integration/
├── unit/
└── fixtures/
.catpaw/
└── skills/
    ├── gptimage2-generator/        # Existing skill, keep and adapt
    ├── ppt-outline-generator/      # New skill
    ├── ppt-template-matcher/       # New skill
    ├── ppt-design-director/        # New skill
    ├── ppt-content-mapper/         # New skill
    ├── ppt-image-layer/            # New/adapted skill
    └── ppt-assembler/              # New skill
config/
├── dispatcher.yml
└── knowledge-bases/
    ├── templates.yml
    ├── slide-patterns.yml
    └── domain-knowledge.yml
```

**Structure Decision**: Use a single Python package for the core agent workflow, with file-backed job workspaces and JSON contracts between phases. Keep the installed `.catpaw/skills/gptimage2-generator` skill as an external skill package and add an adapter under `src/ppt_agent/skills/adapters/gptimage2.py` rather than copying its API client into core code.

## Architecture Decisions

### Unified Dispatch Entry

The project needs an explicit dispatch entry instead of letting each phase call Workers ad hoc. `src/ppt_agent/coordinator/dispatcher.py` is the single allocation entrance for phase execution. It accepts a `DispatchRequest` containing job id, current phase, required capability, artifact paths, user decisions, and runtime constraints. It returns a `DispatchDecision` describing the selected Worker, required skill, context bundle, permission profile, retry/fallback policy, and expected output contracts.

Dispatch responsibilities:

- Resolve the next phase from `phase_state.py`.
- Select the Worker by capability, not by hardcoded function call.
- Load the minimum required skill through `skills/loader.py`.
- Build the phase context using compressed summaries plus exact active artifacts.
- Apply permission and sandbox rules per Worker.
- Emit workflow events before and after dispatch.
- Persist dispatch decisions to `history.jsonl` for replay/debugging.

Example routing:

```text
capability: outline.generate
  worker: outline_generator
  skill: ppt-outline-generator
  inputs: source_summary.json
  outputs: outline.json

capability: template.retrieve
  worker: template_matcher
  skill: ppt-template-matcher
  knowledge_base: templates
  outputs: selected_template.json, template_meta.json

capability: visual.generate
  worker: image_generator
  skill: gptimage2-generator
  inputs: image_generation_config.json
  outputs: generated_slides/, image_generation_report.json
```

`config/dispatcher.yml` owns routing rules so new Workers or skills can be added without rewriting Coordinator flow logic.

### Agent Runtime Model

The PPT Agent uses the agent runtime patterns from `AGENT_ARCHITECTURE.md` but keeps v1 CLI-first and file-system-first. Runtime features exist to make execution auditable, resumable, and isolated; they are not a requirement to build a web UI or heavyweight workflow engine in v1.

#### Task Types

Runtime work is represented as typed tasks so the Coordinator can distinguish quick in-process phase work from long-running or isolated execution. v1 must model all architecture task types even if only a subset is fully implemented.

| TaskType | ID Prefix | v1 PPT Usage | Execution Policy |
|----------|-----------|--------------|------------------|
| `local_bash` | `b-` | Optional long-running validation/render commands | Spawned process with event logging and timeout |
| `local_agent` | `a-` | Phase-specific Worker agent for analysis, mapping, verification | Logical worker with isolated context bundle |
| `remote_agent` | `r-` | Out of scope for normal CLI flow; reserved for heavy isolated jobs | Must be disabled unless configured |
| `in_process_teammate` | `t-` | Optional future collaboration mode | Uses `agent_context.py` attribution when enabled |
| `local_workflow` | `w-` | Main PPT generation workflow | Deterministic state machine in `workflow.py` |
| `monitor_mcp` | `m-` | Optional future monitoring for external services | Disabled in v1 unless MCP support is configured |
| `dream` | `d-` | Session-end memory consolidation | Background low-priority task with relevance gate |

`task_manager.py` assigns IDs, tracks status, records parent/child relationships, emits lifecycle events, and persists task summaries to `history.jsonl`. Tasks do not share mutable state; they communicate through artifacts, event records, or mailbox messages.

#### Coordinator Tool Allowlist

Coordinator tool access is intentionally narrow. It may use exactly four orchestration tools:

- `AgentTool`: start a Worker task with a bounded context bundle and declared outputs.
- `TaskStopTool`: stop or cancel an active task.
- `SendMessageTool`: send a message to an active Worker through the mailbox.
- `SyntheticOutput`: compose final user-facing output from completed task summaries and artifacts.

The Coordinator must not receive filesystem, execution, network, retrieval, image, PPT, or validation tools. Permission tests must reject any Coordinator attempt to use tools outside this allowlist.

#### File-Based Mailbox

Workers and long-running tasks communicate through a file-backed mailbox instead of shared mutable state. Mailboxes live inside the job workspace:

```text
workspace/jobs/<job-id>/mailbox/
├── coordinator.jsonl
├── document_analyst.jsonl
├── image_generator.jsonl
└── ppt_verifier.jsonl
```

Each mailbox line is an append-only JSON message with sender, recipient, timestamp, task id, summary, full text, read status, and optional artifact references. Writes must use file locking plus atomic append where available. Message frequency is expected to be low; debuggability and crash recovery matter more than high throughput.

#### Agent Context Attribution

`agent_context.py` provides Python `contextvars`-based attribution for all task execution, tool calls, event emission, and artifact writes. Every runtime action records:

- agent id and agent name.
- task id and task type.
- parent task id when present.
- current job id.
- invoking request id or dispatch id when available.

This replaces shared global state and keeps concurrent Worker activity attributable in logs, events, and validation reports.

#### Dream Memory Consolidation

`dream.py` implements the session-end memory consolidation task. It reads `history.jsonl`, `session.md`, task summaries, and final validation results after the workflow completes. It applies a relevance gate before appending to `memory.md`.

The dream task may persist:

- durable user preferences about PPT style, review workflow, or output conventions.
- recurring failure patterns and their mitigations.
- stable project conventions that affect future jobs.
- reusable template or skill tuning notes.

It must not persist transient source content, sensitive project facts, generated slide text, or one-off errors unless explicitly marked reusable. Dream writes are append-only and must record their source task id.

### Tool Execution Model

The PPT Agent needs a real execution layer for file creation, artifact persistence, document parsing, OCR, retrieval, image generation, PPT writing, and verification. These actions must not be treated as prompt-only behavior. A model may request or plan an action, but a Worker or host runtime must execute it through a registered tool.

The architecture follows `AGENT_ARCHITECTURE.md`:

- Coordinator is a pure orchestrator. It may dispatch work, stop work, send messages, and synthesize results, but it must not directly read files, write files, execute shell commands, call network APIs, or search knowledge bases.
- Dispatcher returns the selected Worker plus an explicit `allowed_tools` set, permission profile, sandbox constraints, timeout/retry policy, and expected output contracts.
- Workers call tools through a Tool Registry. Workers must not bypass the registry to perform filesystem, process, network, or external-service actions.
- Function calling is an optional transport. If the runtime supports model tool calls, they are routed through the same Tool Registry. If it does not, the Python Worker/CLI executes deterministic tool calls from structured Worker decisions.
- Every tool invocation emits `tool_call_start`, `tool_result`, and `error` events and appends auditable records to `history.jsonl`.
- Tool results may be summarized for context compression, but active tool results, error records, and user-approved artifacts are preserved according to the compression invariants.

Tool categories and v1 responsibilities:

```text
filesystem
  FileSystemTool: create job directories, copy inputs, read/write files, atomic writes.

artifact
  JsonArtifactTool: validate JSON against contracts, version artifacts, write history entries.

document
  DocumentExtractionTool: extract text, images, metadata, and warnings from project materials.
  OCRTool: extract text from screenshots, certificates, scanned pages, and image-heavy inputs.

retrieval
  RetrievalTool: call query_router.py for sparse/vector/fusion/rerank retrieval.

skill
  SkillScriptTool: execute deterministic scripts under .catpaw/skills/<skill-name>/scripts/
  after skill loading, permission checks, and sandbox path validation.

visual
  ImageGenerationTool: adapt approved slide needs to gptimage2-generator batch execution,
  including fallback when accounts, network, or generation results are unavailable.

ppt
  PPTAssemblyTool: write final.pptx with editable text objects and placed visuals.

verification
  RenderVerifyTool: render/inspect PPT output, compare approved text, detect layout/image issues.
```

Permission model:

- `coordinator`: orchestration only; no filesystem, execution, network, retrieval, or PPT tools.
- `document_analyst`: read job inputs, write `source_summary.json`, use document/OCR tools.
- `outline_generator`: read source summary, write `outline.json`, use skill and artifact tools.
- `template_matcher`: read outline/template index, use retrieval tools, write selected template artifacts.
- `design_director` and `content_mapper`: read approved upstream artifacts, write design/content artifacts, use skill and artifact tools.
- `image_generator`: read approved slide contents, use visual tools, write generated images and reports; network access is optional and gated.
- `ppt_assembler`: read approved content and visual artifacts, use PPT/filesystem tools, write `final.pptx`.
- `ppt_verifier`: read final deck and approved artifacts, use verification tools, write `validation_report.json`.

Sandbox rules:

- Job workspace is mounted read-write; skill directories and templates are read-only unless a task explicitly creates or updates skill/template files.
- Tool paths must be resolved and checked before execution to prevent writes outside the job workspace except declared output roots.
- Destructive filesystem actions are out of scope for v1 except replacing generated artifacts by atomic write.
- External network use is limited to optional visual generation and configured vector/database services; fallback mode must not require network.

This means file creation is handled by `FileSystemTool`/`JsonArtifactTool` under Worker control, not by the Coordinator and not by unverified model text.

### Coordinator-Worker Flow

The Coordinator runs a deterministic state machine:

1. `document_analysis`: extract text/OCR evidence and produce `source_summary.json`.
2. `outline_generation`: produce `outline.json`.
3. `template_matching`: search template metadata and produce `selected_template.json` plus `template_meta.json`.
4. `design_planning`: select theme, layout grammar, visual density, and slide expression strategy, producing `slide_design_plan.json`.
5. `content_mapping`: parse template zones and design plan, then produce user-reviewable `slide_contents.json`.
6. `visual_generation`: invoke `gptimage2-generator` only for approved visuals, producing `generated_slides/` and `image_generation_report.json`.
7. `ppt_assembly`: create `final.pptx` while preserving editable text.
8. `verification`: render/inspect output and produce `validation_report.json`.

Workers must communicate through explicit artifacts and event messages, not shared mutable state.

### Context And Compression Strategy

The implementation will mirror `AGENT_ARCHITECTURE.md`:

- `agent.md`: stable agent identity and workflow rules.
- `memory.md`: durable workspace conventions and learned fixes.
- `session.md`: per-job task state, errors, fallbacks, and user decisions.
- `history.jsonl`: append-only event/conversation log.

Compression levels:

- L0: use full recent context for small jobs.
- L1: replace verbose tool output with result summaries after each phase.
- L2: summarize early phase analysis once slide content generation begins.
- L3: retain only approved artifacts, current phase state, and error records during image generation/assembly.
- L4: emergency mode retains system rules, memory, session error records, final user decision, and last phase outputs.

Invariant: user-approved `slide_contents.json`, error records, active tool results, and latest user decision must never be compressed away.

### Skill Loading Strategy

Skills follow progressive loading:

- `ppt-outline-generator`: load during outline generation only.
- `ppt-template-matcher`: load during template matching only.
- `ppt-design-director`: load after template matching and before content mapping.
- `ppt-content-mapper`: load during template parsing and slide content mapping only.
- `ppt-image-layer`: load during assembly/verification only after generated or template images exist.
- `ppt-assembler`: load during final PPT assembly only.
- `gptimage2-generator`: load during visual generation only; use its existing account file, batch config shape, upload-reference flow, polling, download, and account-switching behavior.

The skill registry records loaded skills per job to prevent redundant reads and to make `session.md` explain which phase introduced which skill.

### Skill Generation Plan

The project should create first-class CatPaw/Codex skill packages for PPT-specific capabilities instead of burying all behavior inside the core workflow. Each skill package lives under `.catpaw/skills/<skill-name>/` and follows this minimum structure:

```text
.catpaw/skills/<skill-name>/
├── SKILL.md
├── scripts/
├── references/
├── examples/
└── assets/
```

`SKILL.md` must document trigger conditions, required inputs, produced outputs, failure modes, and when the Coordinator is allowed to load the skill. Scripts should implement deterministic parsing, scoring, validation, or transformation steps when practical; prompts and few-shot examples belong in `references/` or `examples/`.

Required skill packages:

1. `ppt-outline-generator`
   - Purpose: turn `source_summary.json` into `outline.json`.
   - Inputs: project source summary, target audience, optional user style preferences.
   - Outputs: contract-compliant `outline.json`.
   - References/assets: slide structure patterns for pitch, competition, product report, project report; domain guidance files; few-shot outline examples.
   - Notes: must mark unsupported claims as assumptions or review items instead of inventing facts.

2. `ppt-template-matcher`
   - Purpose: match `outline.json` to the best template profile.
   - Inputs: outline metadata, template index, template metadata, preview captions.
   - Outputs: `selected_template.json`, `template_meta.json`, ranking report.
   - Scripts: BM25 scoring, optional vector scoring, RRF fusion, reranking by slide count/layout/style fit.
   - Notes: must return a general fallback template when no strong match exists.

3. `ppt-design-director`
   - Purpose: turn `outline.json` and `template_meta.json` into `slide_design_plan.json`.
   - Inputs: outline, template profile, domain/tone/audience, optional brand preferences.
   - Outputs: theme profile, per-slide layout grammar choice, visual density, block composition, design constraints, and design risks.
   - References/assets: layout grammar catalog, theme token examples, visual density rules, design scoring rubric.
   - Notes: this skill is responsible for making decks look polished, not just populated.

4. `ppt-content-mapper`
   - Purpose: map outline slides into template zones and produce user-reviewable slide content.
   - Inputs: `outline.json`, `slide_design_plan.json`, `template_meta.json`, template parse/OCR results, user image inventory.
   - Outputs: contract-compliant `slide_contents.json`.
   - Scripts: zone capacity checks, slide-to-layout matching, user image assignment.
   - Notes: this is the main user review boundary; every user-facing text field must remain editable.

5. `ppt-image-layer`
   - Purpose: analyze generated or template slide images into PPT-friendly visual layers.
   - Inputs: generated slide images, template previews, `slide_contents.json`.
   - Outputs: layer analysis JSON used by assembly and verification.
   - Scripts: background detection, image region detection, simple shape extraction, text-region masking hints.
   - Notes: if layer extraction is weak, it must return a fallback instruction rather than blocking assembly.

6. `ppt-assembler`
   - Purpose: assemble `final.pptx` from template, approved slide content, generated visuals, and layer analysis.
   - Inputs: template file, `slide_contents.json`, generated images, layer analysis.
   - Outputs: `final.pptx`, assembly report, preview renders when available.
   - Scripts: PPT writing, text fitting, image placement, fallback full-slide background plus editable text overlay.
   - Notes: must preserve user-approved text as editable objects.

Existing skill package:

7. `gptimage2-generator`
   - Purpose: external visual generation through GPTImage2.online.
   - Inputs: `image_generation_config.json` generated by the PPT-Agent adapter.
   - Outputs: generated images and batch report.
   - Current location: `.catpaw/skills/gptimage2-generator`.
   - Notes: do not duplicate its API logic in new skills. Keep account management, upload-reference, polling, downloads, and account switching inside this skill. The PPT-Agent only adapts slide needs into its batch config.

Skill creation should happen before broad workflow implementation, because each Worker's input/output boundary depends on these skill contracts. The later `/speckit-tasks` phase should include explicit tasks for creating each `SKILL.md`, example fixtures, script stubs, and contract validation tests.

### Design System Strategy

To pursue polished PPT quality, the workflow must include a design planning layer instead of relying only on template filling. The design system is centered on four concepts:

1. `ThemeProfile`: color, typography, spacing, border radius, chart style, icon style, and image treatment tokens.
2. `LayoutGrammar`: reusable slide expression patterns such as `cover.hero`, `section.divider`, `content.left-text-right-image`, `content.three-cards`, `content.metric-grid`, `content.timeline`, `content.comparison`, `content.process-flow`, `product.screenshot-callouts`, and `data.big-number-plus-chart`.
3. `VisualDensity`: `low`, `medium`, or `high` density controls for text length, block count, image proportion, whitespace, and font size.
4. `DesignScorer`: post-assembly scoring for visual hierarchy, alignment, spacing, density, image consistency, and style coherence.

The `ppt-design-director` skill produces `slide_design_plan.json` after template matching and before content mapping. This means content mapping no longer only asks "which template zone receives this text"; it first asks "what is the best expression pattern for this slide." This is the main lesson borrowed from products like Gamma: content should be structured first, expressed through a design system second, and exported last.

Design planning rules:

- Cover and section pages should favor low density, strong visual hierarchy, and large visual surfaces.
- Content pages should prefer 3-5 points or block/card layouts instead of dense bullet lists.
- Product screenshot pages should use callouts, annotations, and screenshot-safe image regions.
- Data pages should emphasize one conclusion plus one chart or metric group.
- AI-generated visuals should enhance backgrounds, covers, section dividers, concepts, and empty visual regions; confirmed text remains controlled by PPT objects.

### Retrieval Strategy

Template matching and future domain guidance use a configurable knowledge-base layer rather than a hardcoded vector library. The retrieval system has four separable parts:

1. ingestion: read templates, slide-pattern examples, domain notes, and preview captions.
2. indexing: chunk, enrich, embed, and store sparse/vector indexes.
3. querying: route a query to one or more configured knowledge bases and methods.
4. ranking: fuse and rerank candidates before returning structured results.

Each knowledge base is configured under `config/knowledge-bases/*.yml`. A config declares source paths, chunking strategy, embedding provider, sparse index, vector backend, fusion method, reranker, metadata filters, and refresh policy.

Template matching uses hybrid retrieval over template metadata and preview descriptions:

- sparse path: BM25 over domain, style, slide layout, and tags.
- dense path: embeddings over template descriptions and preview captions through the configured vector backend.
- RRF fusion: merge sparse and dense ranks.
- rerank: score top candidates by slide-count fit, layout coverage, color/tone fit, and domain fit.

Supported vector backend adapters:

- `local`: file-backed local vector index for development and offline fallback.
- `qdrant`: service-backed vector database for production-like local/server deployments.
- `milvus`: scalable vector database option for larger template/domain corpora.
- `pgvector`: PostgreSQL-backed vector option when an existing relational database is already available.

Worker code must call `retrieval/query_router.py` and must not depend directly on a specific vector database client. Adding a new retrieval method should require a new adapter plus config, not a rewrite of `template_matcher.py`.

Knowledge bases planned for v1:

- `templates`: template `meta.json`, preview captions, layout tags, style tags.
- `slide-patterns`: good PPT outline patterns, page structures, and few-shot examples.
- `domain-knowledge`: optional industry terms and recommended storylines.

Index refresh modes:

- `manual`: rebuild only when explicitly requested.
- `on_startup`: detect changed source files and rebuild stale indexes.
- `watch`: future mode for long-running services.

### Visual Generation Strategy

Use the existing `.catpaw/skills/gptimage2-generator` package for Phase 4. The adapter converts approved slide content into the skill's `batch-generate` config:

- cover, transition, and visual-heavy pages can request whole-slide generation.
- content-heavy pages prefer region visuals while text remains controlled by `slide_contents.json`.
- failures fall back to template visuals, user images, placeholders, or full-slide background images with editable text overlaid.

### Verification Strategy

Verification is a fresh Worker pass, not the same assembler Worker. It checks:

- expected slide count and section coverage.
- text preservation from approved slide content.
- visible overflow or clipping.
- image aspect ratio and placement.
- unresolved fallback/manual-review flags.

## Phase 0 Output

See [research.md](./research.md).

## Phase 1 Output

See [data-model.md](./data-model.md), [contracts/](./contracts/), and [quickstart.md](./quickstart.md).

## Complexity Tracking

No architecture-document gate violations are present. Multi-agent orchestration is required by the supplied PPT-Agent architecture because the workflow contains independently verifiable phases with different tools, risk profiles, and context needs.

## Context-Aware Mapping and Strict Assembly Amendment (2026-07-18)

Content mapping is a semantic layout decision, not a type-to-zone lookup. The
content-mapping Worker receives source facts, neighboring slide context, design
intent, every template zone, original template wording, capacity, formatting,
editability, and stable native shape identity. It may plan content blocks and
rewrite expressive wording, but factual values must remain source-backed.

`zone_id` is only the final execution address. Mapping may use any number of
editable text zones. Every editable zone receives exactly one explicit action:
`replace_text`, `clear_text`, or `preserve`; image zones use `replace_image` or
`preserve`. Missing or incompatible zones trigger review/template reselection,
never an implicit overlay.

Stable addresses use the native PPT shape id and shape path, retaining legacy
enumeration ids for existing artifacts. Assembly resolves the native identity
first and positional compatibility matching second. Under the default
`text_replace_only` policy, assembly cannot create text boxes, change geometry,
change font properties, or rewrite approved text. It mutates existing run text
while preserving run and paragraph XML properties.

Content-block planning initially remains an internal reasoning step of the
content-mapping Worker. It becomes a separate Worker only after mapping quality
and cross-slide feedback demonstrate that an additional phase is warranted.

## XML-Native Template Metadata Amendment (2026-07-18)

Template metadata is produced from the complete PPTX/XML shape tree, recursively
including text boxes inside groups. Each writable text surface has a stable
group-aware `shape_path`, native shape id, parent group chain, displayed
geometry, formatting fingerprints, rotation/text-direction attributes, and
content-capacity constraints. Empty decorative AutoShapes are excluded from
the mapping surface; empty native text boxes remain available.

OCR is audit-only. It may annotate an XML zone with rendered text or report an
unmatched `baked_text_region`, but it cannot create an editable zone or a
`zone_id`. Mapping must respect `content_eligibility`, and assembly/verifier
resolve and fingerprint grouped descendants by their stable path.

## Complete Text-Fill Amendment (2026-07-18)

The default mapping policy fills every editable native text zone. `clear_text`
and empty replacement content are rejected. Non-empty template footers and
noise may be preserved; an originally empty editable text box receives short,
source-grounded copy. Replacement length targets 55%-135% of the original
visible character count and is further bounded by XML capacity hints, geometry,
font size, maximum lines, and content eligibility.

Agent output remains the preferred semantic decision, but deterministic repair
fills omitted zones and replaces unsafe content before assembly. Overlapping
duplicate text layers with the same original text and geometry are synchronized
to one replacement string. Assembly treats empty content, `clear_text`, hard
fallback flags, and overflow as non-bypassable errors, including under force
mode. `mapping_diagnostics.json` records the original and mapped character
counts, target range, action, formatting fingerprint, and per-zone issue state.

## DeepSeek Structured Output Compatibility (2026-07-19)

Structured artifact generation and external tool execution are separate
transports. `generate_json` uses DeepSeek JSON Mode on the normal endpoint with
`response_format: {"type": "json_object"}` and validates the result locally.
It never creates a synthetic function or sends `tool_choice`. The prompt must
mention JSON and includes a compact schema-derived example; empty or invalid
content receives one JSON repair request before deterministic fallback.

Actual Agent tools continue to use Tool Calls. DeepSeek Beta `strict: true`
describes strict function-argument validation, not OpenAI
`response_format=json_schema`. Thinking-mode requests must not force
`tool_choice`. Provider capabilities distinguish JSON Mode, OpenAI JSON Schema,
tool use, strict tools, and forced tool selection.

Fallback is observable: model and workflow logs record `llm_fallback`, mapping
batches are marked `needs_review`, and the server reports
`completed_with_fallbacks`. An atomic per-job run lock rejects duplicate backend
starts. Selected legacy templates are refreshed to XML-native metadata before
mapping, while diagnostics treat a missing legacy `editable` field as editable
unless it is explicitly false.
