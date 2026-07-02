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
│   ├── slide-contents.schema.json
│   ├── template-meta.schema.json
│   ├── image-generation-config.schema.json
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
│   │   └── session_summary.py
│   ├── skills/
│   │   ├── registry.py
│   │   ├── loader.py
│   │   └── adapters/
│   │       └── gptimage2.py
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
│   │   ├── outline.py
│   │   ├── slide_contents.py
│   │   ├── template_meta.py
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
        ├── outline.json
        ├── slide_design_plan.json
        ├── template_meta.json
        ├── slide_contents.json
        ├── generated_slides/
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
  outputs: generated_slides/, batch_report.json
```

`config/dispatcher.yml` owns routing rules so new Workers or skills can be added without rewriting Coordinator flow logic.

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
