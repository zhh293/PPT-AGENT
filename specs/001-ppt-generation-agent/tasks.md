# Tasks: PPT Generation Agent

**Input**: Design documents from `specs/001-ppt-generation-agent/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Include contract, unit, integration, and quickstart validation tasks because the spec defines independent tests and the plan requires schema/workflow verification.

**Organization**: Tasks are grouped by user story to keep each increment independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches different files or has no dependency on incomplete tasks.
- **[Story]**: Maps the task to a user story from `spec.md`.
- Every task includes a concrete file path.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the Python package, workspace directories, config roots, fixture roots, and test harness.

- [ ] T001 Create Python project metadata and console entry point in `pyproject.toml`
- [ ] T002 Create package root and module exports in `src/ppt_agent/__init__.py`
- [ ] T003 [P] Create CLI module skeleton with subcommand placeholders in `src/ppt_agent/cli.py`
- [ ] T004 [P] Create runtime config directory and base dispatcher config in `config/dispatcher.yml`
- [ ] T005 [P] Create knowledge-base config directory with empty config files in `config/knowledge-bases/templates.yml`, `config/knowledge-bases/slide-patterns.yml`, and `config/knowledge-bases/domain-knowledge.yml`
- [ ] T006 [P] Create template library seed structure in `templates/index.json`
- [ ] T007 [P] Create test directory layout in `tests/contract/`, `tests/integration/`, `tests/unit/`, and `tests/fixtures/`
- [ ] T008 [P] Create sample fixture input manifest in `tests/fixtures/sample_project/manifest.json`
- [ ] T009 [P] Create workspace ignore/placeholder files in `workspace/jobs/.gitkeep`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the shared contracts, models, dispatch entry, context handling, skill loading, retrieval abstraction, and file workspace that all user stories need.

**Critical**: No user story implementation should begin until this phase is complete.

### Contract And Model Foundation

- [ ] T010 [P] Implement JSON schema loading helpers for `specs/001-ppt-generation-agent/contracts/*.schema.json` in `src/ppt_agent/models/schema_loader.py`
- [ ] T011 [P] Implement artifact path and job workspace models in `src/ppt_agent/models/artifacts.py`
- [ ] T012 [P] Implement outline models matching `outline.schema.json` in `src/ppt_agent/models/outline.py`
- [ ] T013 [P] Implement slide content models matching `slide-contents.schema.json` in `src/ppt_agent/models/slide_contents.py`
- [ ] T014 [P] Implement template metadata models matching `template-meta.schema.json` in `src/ppt_agent/models/template_meta.py`
- [ ] T015 [P] Implement dispatch request/decision models matching dispatch schemas in `src/ppt_agent/models/dispatch.py`
- [ ] T016 [P] Implement knowledge-base config models matching `knowledge-base-config.schema.json` in `src/ppt_agent/models/knowledge_base.py`
- [ ] T017 [P] Implement validation report models matching `validation-report.schema.json` in `src/ppt_agent/models/validation.py`
- [ ] T018 [P] Add contract tests for artifact schemas in `tests/contract/test_artifact_schemas.py`
- [ ] T019 [P] Add contract tests for dispatch and knowledge-base schemas in `tests/contract/test_dispatch_and_kb_schemas.py`
- [ ] T020 [P] Implement slide design plan models matching `slide-design-plan.schema.json` in `src/ppt_agent/models/design_plan.py`
- [ ] T021 [P] Add contract test for `slide_design_plan.json` in `tests/contract/test_slide_design_plan_contract.py`

### Workspace, Events, Context

- [ ] T022 Implement job workspace creation and artifact read/write helpers in `src/ppt_agent/coordinator/phase_state.py`
- [ ] T023 [P] Implement append-only workflow event logging in `src/ppt_agent/coordinator/event_bus.py`
- [ ] T024 [P] Implement memory layer file handling for `agent.md`, `memory.md`, `session.md`, and `history.jsonl` in `src/ppt_agent/context/memory_layers.py`
- [ ] T025 [P] Implement phase summary creation and session update helpers in `src/ppt_agent/context/session_summary.py`
- [ ] T026 Implement L0-L4 context compression policy in `src/ppt_agent/context/compression.py`
- [ ] T027 Add unit tests for workspace state and event logging in `tests/unit/test_phase_state_and_events.py`
- [ ] T028 Add unit tests for compression invariants preserving approved slide content and error records in `tests/unit/test_context_compression.py`

### Dispatch And Skill Loading

- [ ] T029 Implement routing rule parser for `config/dispatcher.yml` in `src/ppt_agent/coordinator/routing_rules.py`
- [ ] T030 Implement unified dispatch entry in `src/ppt_agent/coordinator/dispatcher.py`
- [ ] T031 Implement deterministic workflow state machine in `src/ppt_agent/coordinator/workflow.py`
- [ ] T032 [P] Implement skill registry metadata model in `src/ppt_agent/skills/registry.py`
- [ ] T033 [P] Implement progressive skill loader for `.catpaw/skills/<skill-name>/SKILL.md` in `src/ppt_agent/skills/loader.py`
- [ ] T034 Add unit tests for dispatch routing and skill loading in `tests/unit/test_dispatcher_and_skill_loader.py`

### Knowledge Base And Retrieval Foundation

- [ ] T035 [P] Implement knowledge-base config loader in `src/ppt_agent/retrieval/config.py`
- [ ] T036 [P] Implement source ingestion interface in `src/ppt_agent/retrieval/ingestion.py`
- [ ] T037 [P] Implement chunking strategies in `src/ppt_agent/retrieval/chunking.py`
- [ ] T038 [P] Implement contextual enrichment hook interface in `src/ppt_agent/retrieval/enrichment.py`
- [ ] T039 [P] Implement BM25 sparse retriever in `src/ppt_agent/retrieval/sparse/bm25.py`
- [ ] T040 [P] Implement vector backend base interface in `src/ppt_agent/retrieval/vector/base.py`
- [ ] T041 [P] Implement local file-backed vector backend stub in `src/ppt_agent/retrieval/vector/local.py`
- [ ] T042 [P] Implement Qdrant adapter stub with config validation in `src/ppt_agent/retrieval/vector/qdrant.py`
- [ ] T043 [P] Implement Milvus adapter stub with config validation in `src/ppt_agent/retrieval/vector/milvus.py`
- [ ] T044 [P] Implement pgvector adapter stub with config validation in `src/ppt_agent/retrieval/vector/pgvector.py`
- [ ] T045 [P] Implement RRF fusion in `src/ppt_agent/retrieval/fusion/rrf.py`
- [ ] T046 [P] Implement reranker base interface in `src/ppt_agent/retrieval/rerankers/base.py`
- [ ] T047 Implement retrieval query router that selects configured sparse/vector/fusion/rerank methods in `src/ppt_agent/retrieval/query_router.py`
- [ ] T048 Add unit tests for retrieval config, vector adapter selection, and query routing in `tests/unit/test_retrieval_query_router.py`

### Design System Foundation

- [ ] T049 [P] Implement theme token model and defaults in `src/ppt_agent/design/theme.py`
- [ ] T050 [P] Implement layout grammar catalog in `src/ppt_agent/design/layout_grammar.py`
- [ ] T051 [P] Implement visual density rules in `src/ppt_agent/design/visual_density.py`
- [ ] T052 [P] Implement design scoring primitives in `src/ppt_agent/design/design_scorer.py`
- [ ] T053 [P] Implement design rewrite suggestion helpers in `src/ppt_agent/design/rewrite_suggestions.py`
- [ ] T054 Add unit tests for theme, layout grammar, and visual density rules in `tests/unit/test_design_system.py`

### Skill Package Scaffolds

- [ ] T055 [P] Create `ppt-outline-generator` skill scaffold in `.catpaw/skills/ppt-outline-generator/SKILL.md`
- [ ] T056 [P] Create `ppt-template-matcher` skill scaffold in `.catpaw/skills/ppt-template-matcher/SKILL.md`
- [ ] T057 [P] Create `ppt-design-director` skill scaffold in `.catpaw/skills/ppt-design-director/SKILL.md`
- [ ] T058 [P] Create `ppt-content-mapper` skill scaffold in `.catpaw/skills/ppt-content-mapper/SKILL.md`
- [ ] T059 [P] Create `ppt-image-layer` skill scaffold in `.catpaw/skills/ppt-image-layer/SKILL.md`
- [ ] T060 [P] Create `ppt-assembler` skill scaffold in `.catpaw/skills/ppt-assembler/SKILL.md`
- [ ] T061 Add skill fixture directories and placeholder examples in `.catpaw/skills/ppt-outline-generator/examples/`, `.catpaw/skills/ppt-template-matcher/examples/`, `.catpaw/skills/ppt-design-director/examples/`, `.catpaw/skills/ppt-content-mapper/examples/`, `.catpaw/skills/ppt-image-layer/examples/`, and `.catpaw/skills/ppt-assembler/examples/`

**Checkpoint**: Foundation is ready. Dispatch, context, schema validation, skill loading, retrieval routing, and skill package skeletons exist.

---

## Phase 3: User Story 1 - Generate an Editable Presentation from Project Materials (Priority: P1) - MVP

**Goal**: Given project materials and supporting images, produce a coherent editable PPT draft with source-backed content.

**Independent Test**: Run the workflow with a fixture project plan and screenshot and confirm `final.pptx`, `outline.json`, `slide_contents.json`, and `validation_report.json` are produced; approved titles and bullets are editable.

### Tests for User Story 1

- [ ] T062 [P] [US1] Add integration fixture project plan and screenshot in `tests/fixtures/sample_project/input/`
- [ ] T063 [P] [US1] Add contract test for `outline.json` generation in `tests/contract/test_outline_contract.py`
- [ ] T064 [P] [US1] Add contract test for `slide_contents.json` MVP output in `tests/contract/test_slide_contents_contract.py`
- [ ] T065 [P] [US1] Add integration test for create-job through fallback PPT generation in `tests/integration/test_us1_generate_editable_presentation.py`

### Implementation for User Story 1

- [ ] T066 [US1] Implement `create-job` CLI command copying inputs and initializing job files in `src/ppt_agent/cli.py`
- [ ] T067 [P] [US1] Implement source material extraction worker in `src/ppt_agent/workers/document_analyst.py`
- [ ] T068 [P] [US1] Implement `source_summary.json` artifact writer in `src/ppt_agent/models/source_summary.py`
- [ ] T069 [US1] Complete `ppt-outline-generator` SKILL instructions for source-summary to outline conversion in `.catpaw/skills/ppt-outline-generator/SKILL.md`
- [ ] T070 [US1] Add outline structure references in `.catpaw/skills/ppt-outline-generator/references/structures.md`
- [ ] T071 [US1] Implement outline worker that loads `ppt-outline-generator` and writes `outline.json` in `src/ppt_agent/workers/outline_generator.py`
- [ ] T072 [US1] Implement basic default template metadata fallback in `templates/index.json`
- [ ] T073 [US1] Implement MVP content mapper producing editable `slide_contents.json` from outline and default layout in `src/ppt_agent/workers/content_mapper.py`
- [ ] T074 [US1] Complete `ppt-assembler` SKILL instructions for editable text preservation in `.catpaw/skills/ppt-assembler/SKILL.md`
- [ ] T075 [US1] Implement PPT writer abstraction with editable text boxes in `src/ppt_agent/assembly/ppt_writer.py`
- [ ] T076 [US1] Implement layout fitting helpers for titles and bullets in `src/ppt_agent/assembly/layout_fit.py`
- [ ] T077 [US1] Implement assembler worker for default-template fallback deck in `src/ppt_agent/workers/ppt_assembler.py`
- [ ] T078 [US1] Wire `run --until content-review` and full `run` CLI flow through Coordinator in `src/ppt_agent/cli.py`
- [ ] T079 [US1] Add MVP dispatch routes for document analysis, outline generation, content mapping, and PPT assembly in `config/dispatcher.yml`
- [ ] T080 [US1] Add workflow event emission for US1 phases in `src/ppt_agent/coordinator/workflow.py`

**Checkpoint**: User Story 1 can generate a local editable fallback PPT without requiring image generation or vector database connectivity.

---

## Phase 4: User Story 2 - Review and Adjust Slide Content Before Final Assembly (Priority: P2)

**Goal**: Let the user inspect and modify generated outline/slide content before final assembly, and guarantee approved edits are preserved.

**Independent Test**: Generate `slide_contents.json`, edit a title and bullet list, approve the file, rerun assembly, and confirm the final PPT uses the revised editable text.

### Tests for User Story 2

- [ ] T081 [P] [US2] Add state-transition contract test for slide content review statuses in `tests/contract/test_slide_content_review_contract.py`
- [ ] T082 [P] [US2] Add integration test for editing and approving `slide_contents.json` in `tests/integration/test_us2_review_and_adjust_content.py`

### Implementation for User Story 2

- [ ] T083 [US2] Complete `ppt-content-mapper` SKILL instructions for user-reviewable slide content in `.catpaw/skills/ppt-content-mapper/SKILL.md`
- [ ] T084 [US2] Add content mapping examples in `.catpaw/skills/ppt-content-mapper/examples/slide_contents.example.json`
- [ ] T085 [US2] Implement slide content review state transitions in `src/ppt_agent/models/slide_contents.py`
- [ ] T086 [US2] Implement `approve` CLI command validating and marking `slide_contents.json` approved in `src/ppt_agent/cli.py`
- [ ] T087 [US2] Update content mapper worker to preserve user image inventory and fallback flags in `src/ppt_agent/workers/content_mapper.py`
- [ ] T088 [US2] Update assembler worker to refuse non-approved user-reviewed content unless forced in `src/ppt_agent/workers/ppt_assembler.py`
- [ ] T089 [US2] Add exact-text preservation check before assembly in `src/ppt_agent/assembly/layout_fit.py`
- [ ] T090 [US2] Add dispatch route for content approval capability in `config/dispatcher.yml`

**Checkpoint**: User Story 2 supports review, edit, approve, and assemble without losing user changes.

---

## Phase 5: User Story 3 - Apply a Suitable Visual Style and Template (Priority: P3)

**Goal**: Select a domain/audience-appropriate template and generate or assign visuals while preserving editable text.

**Independent Test**: Run the workflow for two fixture domains and verify selected templates, style metadata, image strategy, and fallback behavior differ appropriately.

### Tests for User Story 3

- [ ] T091 [P] [US3] Add contract test for `template_meta.json` and selected template ranking output in `tests/contract/test_template_meta_contract.py`
- [ ] T092 [P] [US3] Add contract test for `image_generation_config.json` in `tests/contract/test_image_generation_config_contract.py`
- [ ] T093 [P] [US3] Add integration test for template matching with configurable knowledge base in `tests/integration/test_us3_template_matching.py`
- [ ] T094 [P] [US3] Add integration test for no-account visual fallback mode in `tests/integration/test_us3_visual_generation_fallback.py`

### Implementation for User Story 3

- [ ] T095 [US3] Complete `ppt-template-matcher` SKILL instructions for template retrieval and ranking in `.catpaw/skills/ppt-template-matcher/SKILL.md`
- [ ] T096 [US3] Add template matcher examples in `.catpaw/skills/ppt-template-matcher/examples/template-ranking.example.json`
- [ ] T097 [US3] Implement template index builder over `templates/index.json` and template `meta.json` files in `src/ppt_agent/retrieval/template_index.py`
- [ ] T098 [US3] Populate `config/knowledge-bases/templates.yml` with template source, chunking, sparse, vector, fusion, and refresh settings
- [ ] T099 [US3] Populate `config/knowledge-bases/slide-patterns.yml` with slide-pattern knowledge-base settings
- [ ] T100 [US3] Populate `config/knowledge-bases/domain-knowledge.yml` with domain-knowledge settings
- [ ] T101 [US3] Implement template matcher worker using `retrieval/query_router.py` in `src/ppt_agent/workers/template_matcher.py`
- [ ] T102 [US3] Complete `ppt-design-director` SKILL instructions for theme, layout grammar, visual density, and design scoring in `.catpaw/skills/ppt-design-director/SKILL.md`
- [ ] T103 [US3] Add design director examples in `.catpaw/skills/ppt-design-director/examples/slide_design_plan.example.json`
- [ ] T104 [US3] Implement design director worker that writes `slide_design_plan.json` in `src/ppt_agent/workers/design_director.py`
- [ ] T105 [US3] Implement style-aware slide-to-layout selection using `slide_design_plan.json` in `src/ppt_agent/workers/content_mapper.py`
- [ ] T106 [US3] Implement GPTImage2 adapter converting `slide_contents.json` to batch config in `src/ppt_agent/skills/adapters/gptimage2.py`
- [ ] T107 [US3] Implement image generation worker invoking existing `.catpaw/skills/gptimage2-generator/scripts/gptimage2_client.py` in `src/ppt_agent/workers/image_generator.py`
- [ ] T108 [US3] Complete `ppt-image-layer` SKILL instructions for generated/template image layer analysis in `.catpaw/skills/ppt-image-layer/SKILL.md`
- [ ] T109 [US3] Implement image layer fallback analyzer in `src/ppt_agent/assembly/render_verify.py`
- [ ] T110 [US3] Add dispatch routes for template retrieval, design planning, and visual generation in `config/dispatcher.yml`

**Checkpoint**: User Story 3 can match templates through a configurable knowledge base, prepare visuals, call the existing GPTImage2 skill when configured, and fallback when unavailable.

---

## Phase 6: User Story 4 - Validate Final Output Quality (Priority: P4)

**Goal**: Produce a validation report that identifies missing content, text drift, non-editable approved text, overflow, image distortion, unresolved fallbacks, and manual review items.

**Independent Test**: Generate a deck with intentionally missing content, long text, and image fallback, then confirm `validation_report.json` flags the affected slides.

### Tests for User Story 4

- [ ] T111 [P] [US4] Add contract test for `validation_report.json` including `text_editable` checks in `tests/contract/test_validation_report_contract.py`
- [ ] T112 [P] [US4] Add integration test for final PPT validation warnings in `tests/integration/test_us4_validation_report.py`
- [ ] T113 [P] [US4] Add unit test for text preservation and editability comparison in `tests/unit/test_text_preservation_validation.py`

### Implementation for User Story 4

- [ ] T114 [US4] Implement PPT render/inspection helper in `src/ppt_agent/assembly/render_verify.py`
- [ ] T115 [US4] Implement verifier worker checking slide count, content presence, text preservation, text editability, text fit, image fit, fallback resolution, and design score in `src/ppt_agent/workers/ppt_verifier.py`
- [ ] T116 [US4] Add design score and design suggestions to validation report generation in `src/ppt_agent/design/design_scorer.py`
- [ ] T117 [US4] Add validation report artifact writing in `src/ppt_agent/models/validation.py`
- [ ] T118 [US4] Update workflow to run verification as an independent final Worker in `src/ppt_agent/coordinator/workflow.py`
- [ ] T119 [US4] Add dispatch route for final verification in `config/dispatcher.yml`
- [ ] T120 [US4] Update `run` CLI to print validation summary and manual review items in `src/ppt_agent/cli.py`

**Checkpoint**: User Story 4 produces a useful quality report and does not rely on assembler self-checks.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Harden the workflow, documentation, examples, and quickstart validation after the user stories work.

- [ ] T121 [P] Add `validate-artifacts` CLI command for schema validation in `src/ppt_agent/cli.py`
- [ ] T122 [P] Add quickstart fixture validation script in `tests/integration/test_quickstart_scenarios.py`
- [ ] T123 [P] Document local setup, fixture workflow, and fallback behavior in `README.md`
- [ ] T124 [P] Add sample template metadata and previews placeholder manifest in `templates/sample/default/meta.json`
- [ ] T125 Add end-to-end quickstart validation covering all four scenarios from `quickstart.md` in `tests/integration/test_quickstart_scenarios.py`
- [ ] T126 Add task for checking all JSON schemas are valid with `python3 -m json.tool` in `tests/contract/test_schema_files_parse.py`
- [ ] T127 Review all new `.catpaw/skills/*/SKILL.md` files for trigger clarity and progressive loading boundaries in `.catpaw/skills/`
- [ ] T128 Run full local validation command documented in `quickstart.md` and record expected outputs in `README.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: No dependencies.
- **Phase 2 Foundational**: Depends on Phase 1 and blocks all user stories.
- **Phase 3 US1**: Depends on Phase 2. This is the MVP.
- **Phase 4 US2**: Depends on Phase 2 and can reuse US1 assembly, but its review state logic is independently testable.
- **Phase 5 US3**: Depends on Phase 2 and integrates with US1/US2 artifacts.
- **Phase 6 US4**: Depends on Phase 2 and can validate any assembled deck; best run after US1 is available.
- **Phase 7 Polish**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1 Generate Editable Presentation**: First delivery target; no dependency on US2-US4.
- **US2 Review And Adjust Content**: Can start after foundational models and content mapper exist; final demonstration benefits from US1 assembler.
- **US3 Visual Style And Template**: Can start after retrieval foundation; final demonstration benefits from US1/US2 artifacts.
- **US4 Validate Output Quality**: Can start after validation models and render helper exist; final demonstration benefits from US1 final PPT.

### Skill Creation Priority

1. `ppt-outline-generator`
2. `ppt-template-matcher`
3. `ppt-design-director`
4. `ppt-content-mapper`
5. `ppt-assembler`
6. `ppt-image-layer`
7. Existing `gptimage2-generator` adapter integration

### Parallel Opportunities

- T003-T009 can run in parallel after T001-T002.
- T010-T019 can run in parallel by model/contract area.
- T023-T026 can run in parallel after T022.
- T035-T046 can run in parallel before T047.
- T055-T061 can run in parallel.
- US1 tests T062-T065 can run in parallel before implementation.
- US3 tests T091-T094 and knowledge-base config tasks T098-T100 can run in parallel.
- US4 tests T111-T113 can run in parallel.

---

## Parallel Example: User Story 1

```bash
Task: "T062 Add integration fixture project plan and screenshot in tests/fixtures/sample_project/input/"
Task: "T063 Add contract test for outline.json generation in tests/contract/test_outline_contract.py"
Task: "T064 Add contract test for slide_contents.json MVP output in tests/contract/test_slide_contents_contract.py"
Task: "T067 Implement source material extraction worker in src/ppt_agent/workers/document_analyst.py"
Task: "T068 Implement source_summary.json artifact writer in src/ppt_agent/models/source_summary.py"
```

## Parallel Example: User Story 3

```bash
Task: "T095 Complete ppt-template-matcher SKILL instructions in .catpaw/skills/ppt-template-matcher/SKILL.md"
Task: "T098 Populate config/knowledge-bases/templates.yml"
Task: "T099 Populate config/knowledge-bases/slide-patterns.yml"
Task: "T100 Populate config/knowledge-bases/domain-knowledge.yml"
Task: "T106 Implement GPTImage2 adapter in src/ppt_agent/skills/adapters/gptimage2.py"
```

---

## Implementation Strategy

### MVP First

1. Complete Phase 1 Setup.
2. Complete Phase 2 Foundational.
3. Complete Phase 3 US1 with fallback template assembly and editable text.
4. Stop and validate that `final.pptx` is produced without external image generation.

### Incremental Delivery

1. Add US2 so users can review and approve `slide_contents.json`.
2. Add US3 so template matching and visual generation improve deck quality.
3. Add US4 so every final deck ships with a validation report.

### Quality Gates

- Every JSON artifact must validate against its schema.
- `slide_contents.json` approved text must remain editable in `final.pptx` and must produce `text_editable: true` in `validation_report.json`.
- Workflow must produce a fallback PPT when external visual generation is unavailable.
- Dispatch decisions must be logged to `history.jsonl`.
- Context compression must never drop approved slide content or error records.
- Quickstart scenarios must pass before considering the feature complete.

## Notes

- `[P]` tasks are safe parallel candidates.
- Story labels map directly to `spec.md` user stories.
- Keep generated skills small and phase-specific; do not create one giant PPT skill.
- Do not duplicate the GPTImage2 API logic; call the existing `gptimage2-generator` skill through an adapter.
- Add new vector database support through `src/ppt_agent/retrieval/vector/` plus config, not through Worker rewrites.
