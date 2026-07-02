# PPT-AGENT

PPT-AGENT is a planned agentic workflow for generating editable PowerPoint decks from project materials such as project plans, software copyright certificates, product screenshots, logos, and other supporting images.

The project is currently in the SDD / Spec Kit design stage. The repository contains the architecture notes, feature specification, implementation plan, contracts, data model, quickstart validation guide, and task breakdown needed to start implementation.

## Goal

The system should take user-provided project materials and produce a polished, editable `.pptx` file through a staged workflow:

1. Analyze project documents and images.
2. Generate a presentation outline.
3. Match a suitable template.
4. Map approved content into slide layouts.
5. Generate or assign visual assets.
6. Assemble an editable PowerPoint deck.
7. Validate the final output and report issues that need manual review.

The key product requirement is not just "make a PPT image". User-approved titles, subtitles, and bullet text must remain editable in the final PowerPoint file.

## Current Status

This repository currently contains:

- General agent runtime architecture: [`AGENT_ARCHITECTURE.md`](./AGENT_ARCHITECTURE.md)
- PPT agent architecture design: [`PPT-Agent-架构设计.md`](./PPT-Agent-%E6%9E%B6%E6%9E%84%E8%AE%BE%E8%AE%A1.md)
- GPTImage2 API notes: [`gptimage2-API文档.md`](./gptimage2-API%E6%96%87%E6%A1%A3.md)
- Spec Kit feature documents under [`specs/001-ppt-generation-agent/`](./specs/001-ppt-generation-agent/)
- Existing GPTImage2 generation skill under [`.catpaw/skills/gptimage2-generator/`](./.catpaw/skills/gptimage2-generator/)

Implementation code under `src/ppt_agent/` has not been created yet. The next step is to implement tasks from [`tasks.md`](./specs/001-ppt-generation-agent/tasks.md).

## Architecture Overview

The planned runtime follows a Coordinator-Worker architecture.

The Coordinator is a pure orchestrator. It does not parse files, run OCR, call image APIs, or assemble PowerPoint files directly. Instead, it dispatches work to specialized Workers through a unified dispatch entry:

```text
src/ppt_agent/coordinator/dispatcher.py
```

The planned Workers are:

```text
document_analyst.py
outline_generator.py
template_matcher.py
content_mapper.py
image_generator.py
ppt_assembler.py
ppt_verifier.py
```

Workers communicate through explicit file artifacts rather than shared mutable state. Important artifacts include:

```text
source_summary.json
outline.json
selected_template.json
template_meta.json
slide_contents.json
image_generation_config.json
generated_slides/
layer_analysis/
final.pptx
validation_report.json
history.jsonl
session.md
```

## Workflow

### 1. Job Workspace

Each generation job gets its own workspace:

```text
workspace/jobs/<job-id>/
├── input/
├── history.jsonl
├── session.md
├── outline.json
├── slide_contents.json
├── generated_slides/
├── final.pptx
└── validation_report.json
```

This makes the workflow inspectable, resumable, and easier to debug.

### 2. Document Analysis

The document analyst extracts text and OCR evidence from user materials, classifies files, identifies product names and supporting facts, and writes `source_summary.json`.

### 3. Outline Generation

The outline generator creates a structured `outline.json` with slide titles, slide purpose, key points, source references, and visual needs.

### 4. Template Matching

The template matcher searches template metadata and preview descriptions to select a suitable deck template.

Retrieval is planned as a configurable knowledge-base layer, not a hardcoded local vector library. The design supports:

- BM25 sparse retrieval
- vector search
- metadata filtering
- RRF fusion
- reranking
- local vector index
- Qdrant
- Milvus
- pgvector

Knowledge-base configs live under:

```text
config/knowledge-bases/
├── templates.yml
├── slide-patterns.yml
└── domain-knowledge.yml
```

### 5. Content Mapping

The content mapper combines the outline and template metadata to produce `slide_contents.json`. This is the main user review boundary.

Users should be able to edit titles, bullet points, slide order, image selections, and visual prompts before final assembly.

### 6. Visual Generation

The visual generation phase reuses the existing skill:

```text
.catpaw/skills/gptimage2-generator/
```

The PPT agent should not duplicate GPTImage2 API logic. Instead, it should adapt approved slide content into the skill's batch generation config and let the skill handle login, upload-reference, polling, image download, and account switching.

If visual generation is unavailable, the workflow must fall back to template visuals, user images, placeholders, or full-slide background images with editable text overlaid.

### 7. PPT Assembly

The assembler creates `final.pptx` from the selected template, approved slide content, user images, generated visuals, and layer analysis.

Critical rule: approved text must be written as editable PowerPoint text objects, not flattened into images.

### 8. Verification

The verifier runs as an independent final Worker and writes `validation_report.json`.

It checks:

- slide count and required sections
- content presence
- text preservation
- text editability
- text fit / overflow
- image fit / distortion
- fallback resolution
- manual review items

The validation schema includes `text_editable` because editability is a core acceptance requirement.

## Planned Skills

The design uses progressive skill loading. Skills should be loaded only when their phase needs them.

Planned PPT-specific skill packages:

```text
.catpaw/skills/ppt-outline-generator/
.catpaw/skills/ppt-template-matcher/
.catpaw/skills/ppt-content-mapper/
.catpaw/skills/ppt-image-layer/
.catpaw/skills/ppt-assembler/
```

Existing skill package:

```text
.catpaw/skills/gptimage2-generator/
```

Each new skill should include:

```text
SKILL.md
scripts/
references/
examples/
assets/
```

## SDD Documents

The active Spec Kit feature is:

```text
specs/001-ppt-generation-agent/
```

Key files:

- [`spec.md`](./specs/001-ppt-generation-agent/spec.md): feature specification
- [`plan.md`](./specs/001-ppt-generation-agent/plan.md): implementation plan
- [`research.md`](./specs/001-ppt-generation-agent/research.md): architecture decisions
- [`data-model.md`](./specs/001-ppt-generation-agent/data-model.md): data model
- [`quickstart.md`](./specs/001-ppt-generation-agent/quickstart.md): validation guide
- [`tasks.md`](./specs/001-ppt-generation-agent/tasks.md): implementation task list
- [`contracts/`](./specs/001-ppt-generation-agent/contracts/): JSON schemas for phase artifacts

Important contracts:

- `outline.schema.json`
- `slide-contents.schema.json`
- `template-meta.schema.json`
- `image-generation-config.schema.json`
- `validation-report.schema.json`
- `workflow-events.schema.json`
- `knowledge-base-config.schema.json`
- `dispatch-request.schema.json`
- `dispatch-decision.schema.json`

## Suggested Implementation Order

Start with a minimal local MVP:

1. Build project skeleton and CLI.
2. Implement job workspace, artifact schemas, event log, and dispatch entry.
3. Implement context/session handling and skill loading.
4. Implement the fallback deck path:
   - document analysis
   - outline generation
   - basic content mapping
   - editable PPT assembly
5. Add user review and approval of `slide_contents.json`.
6. Add configurable knowledge-base retrieval and template matching.
7. Add GPTImage2 adapter and visual generation.
8. Add final verification and quickstart tests.

The MVP should work without external image generation or a vector database.

## Quickstart Target

The planned CLI shape is:

```bash
python -m ppt_agent.cli create-job \
  --input ./fixtures/sample_project/ \
  --output ./workspace/jobs/sample-project

python -m ppt_agent.cli run \
  --job ./workspace/jobs/sample-project \
  --until content-review

python -m ppt_agent.cli approve \
  --job ./workspace/jobs/sample-project \
  --slide-contents ./workspace/jobs/sample-project/slide_contents.json

python -m ppt_agent.cli run \
  --job ./workspace/jobs/sample-project \
  --from visual-generation
```

See [`quickstart.md`](./specs/001-ppt-generation-agent/quickstart.md) for planned validation scenarios.

## Quality Gates

Before the feature is considered complete:

- All JSON artifacts must validate against their schemas.
- Approved slide text must remain editable in `final.pptx`.
- `validation_report.json` must include `text_editable`.
- The workflow must produce a fallback PPT when GPTImage2 is unavailable.
- Dispatch decisions must be recorded in `history.jsonl`.
- Context compression must never drop approved slide content or error records.
- Quickstart scenarios must pass.

## Repository Notes

This repository also contains early analysis artifacts and sample files used during planning. Some implementation folders described in the SDD plan are not present yet and should be created by following [`tasks.md`](./specs/001-ppt-generation-agent/tasks.md).
