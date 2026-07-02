# Data Model: PPT Generation Agent

## ProjectMaterial

Represents a user-provided source file.

**Fields**:

- `material_id`: Stable identifier within a job.
- `file_name`: Original file name.
- `material_type`: `project_plan`, `certificate`, `screenshot`, `logo`, `reference_image`, `unknown`.
- `mime_type`: Detected file type.
- `source_path`: Path inside the job workspace.
- `extracted_text`: Text extracted directly or through OCR.
- `summary`: Short source summary used by later phases.
- `detected_entities`: Product names, organization names, dates, metrics, certificate numbers, or UI labels.
- `relevance`: `primary`, `supporting`, `low`, `ignored`.
- `warnings`: Extraction or quality issues.

**Validation rules**:

- Every job must include at least one `project_plan` or one material with `primary` relevance.
- Low-quality OCR or unreadable files must generate warnings rather than fail the whole job.

## SourceSummary

Condensed facts extracted from all materials.

**Fields**:

- `project_name`
- `domain`
- `target_audience`
- `tone`
- `value_proposition`
- `product_capabilities`
- `evidence_items`
- `image_inventory`
- `unsupported_claims`
- `confidence`

**Relationships**:

- Derived from many `ProjectMaterial` records.
- Used by `PresentationOutline`.

## PresentationOutline

Represents the planned deck structure.

**Fields**:

- `outline_id`
- `project_name`
- `domain`
- `target_audience`
- `tone`
- `slide_count`
- `slides`: Ordered list of `OutlineSlide`.
- `assumptions`
- `needs_user_review`

## OutlineSlide

Represents one planned slide before detailed layout mapping.

**Fields**:

- `slide_index`
- `slide_type`: `cover`, `background`, `problem`, `solution`, `product`, `market`, `evidence`, `team`, `roadmap`, `closing`, `other`.
- `title`
- `purpose`
- `bullets`
- `source_refs`
- `image_needs`
- `priority`

**Validation rules**:

- Slide indexes must be unique and sequential.
- Titles must not be empty.
- Source-backed claims should include `source_refs`.

## TemplateProfile

Represents a reusable template and its matching metadata.

**Fields**:

- `template_id`
- `domain_tags`
- `audience_tags`
- `tone_tags`
- `color_scheme`
- `slide_count`
- `style_description`
- `preview_paths`
- `layouts`: Ordered list of `TemplateSlideLayout`.
- `retrieval_text`

## TemplateSlideLayout

Represents layout zones for one template page.

**Fields**:

- `index`
- `layout_type`
- `zones`: Ordered list of `TemplateZone`.
- `visual_density`
- `supports_generated_background`

## TemplateZone

Represents a positionable region on a slide.

**Fields**:

- `zone_id`
- `zone_type`: `title`, `subtitle`, `bullets`, `image`, `chart`, `footer`, `decorative`.
- `position`: Normalized `[x, y, width, height]`.
- `font_size`
- `capacity_hint`
- `style_hint`

## ThemeProfile

Represents reusable design tokens for a deck.

**Fields**:

- `theme_id`
- `color_tokens`: primary, secondary, accent, background, text, muted.
- `typography_tokens`: title font, body font, title scale, body scale, line height.
- `spacing_tokens`: page margin, block gap, card padding, section spacing.
- `shape_tokens`: border radius, stroke style, shadow style.
- `image_treatment`: crop style, overlay style, preferred image tone.
- `chart_style`: axis, label, palette, gridline, and emphasis rules.

## LayoutGrammar

Represents a reusable slide expression pattern.

**Fields**:

- `layout_id`: Examples include `cover.hero`, `content.three-cards`, `product.screenshot-callouts`, `data.big-number-plus-chart`.
- `supported_slide_types`
- `required_blocks`
- `optional_blocks`
- `max_text_items`
- `recommended_visual_ratio`
- `density_range`
- `ppt_zone_mapping_rules`

## SlideDesignPlan

Represents design decisions made before content is mapped into PPT zones.

**Fields**:

- `theme_profile`
- `slides`: Ordered list of `SlideDesignDecision`.
- `global_style_notes`
- `design_risks`

## SlideDesignDecision

Represents per-slide visual expression intent.

**Fields**:

- `slide_index`
- `layout_id`
- `visual_density`: `low`, `medium`, `high`.
- `block_plan`: planned content blocks such as hero title, metric card, image-text, timeline, process flow, comparison, or screenshot callout.
- `visual_strategy`: `template_only`, `user_image`, `generated_background`, `generated_region`, `chart`, `placeholder`.
- `text_budget`: maximum title length, bullet count, and line count.
- `design_constraints`: spacing, alignment, image ratio, color, or hierarchy constraints.
- `design_warnings`: known risks before assembly.

## SlideContent

Represents user-reviewable content mapped to one slide.

**Fields**:

- `slide_index`
- `layout_type`
- `layout_id`
- `visual_density`
- `review_status`: `draft`, `approved`, `edited`, `needs_review`.
- `zones`: Ordered list of `SlideZoneContent`.
- `source_refs`
- `fallback_flags`

**State transitions**:

- `draft` -> `approved`
- `draft` -> `edited` -> `approved`
- `draft` -> `needs_review`
- `needs_review` -> `edited` -> `approved`

## SlideZoneContent

Represents content for a template zone.

**Fields**:

- `zone_id`
- `zone_type`
- `position`
- `content`
- `editable`
- `source`: `user_upload`, `generated`, `template`, `placeholder`, `none`.
- `image_ref`
- `image_prompt`
- `fit_status`

**Validation rules**:

- User-approved text zones must have `editable: true`.
- Generated text suggestions must not replace approved text.

## ImageGenerationJob

Represents one requested visual generation task.

**Fields**:

- `job_id`
- `slide_index`
- `mode`: `whole_slide`, `region`
- `prompt`
- `aspect_ratio`
- `resolution`
- `reference_image`
- `output_path`
- `status`: `pending`, `running`, `succeeded`, `failed`, `skipped`, `fallback_used`.
- `provider_metadata`
- `error`

**Validation rules**:

- `whole_slide` jobs should not be the only source of editable text.
- Failed jobs must set a fallback or manual-review flag.

## PresentationArtifact

Represents the assembled output.

**Fields**:

- `artifact_id`
- `job_id`
- `pptx_path`
- `preview_paths`
- `created_at`
- `slide_count`
- `assembly_mode`: `normal`, `fallback_template`, `image_background_with_text_overlay`.
- `warnings`

## ValidationReport

Represents output verification results.

**Fields**:

- `job_id`
- `status`: `passed`, `passed_with_warnings`, `failed`.
- `checked_at`
- `summary`
- `slide_results`: Ordered list of `SlideValidationResult`.
- `manual_review_items`

## SlideValidationResult

Represents quality findings for one slide.

**Fields**:

- `slide_index`
- `status`: `passed`, `warning`, `failed`.
- `checks`: `content_present`, `text_preserved`, `text_editable`, `text_fit`, `image_fit`, `fallback_resolved`.
- `issues`
- `recommended_action`
- `design_score`
- `design_suggestions`

**Validation rules**:

- `text_editable` must be `true` for slides containing user-approved title, subtitle, or bullet text.
- A slide may pass with `text_editable: false` only when it has no user-approved text zones, such as a purely visual divider slide.
- `design_score` should flag slides with excessive text density, weak hierarchy, inconsistent spacing, poor image fit, or style inconsistency.
