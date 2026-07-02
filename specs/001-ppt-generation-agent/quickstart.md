# Quickstart: PPT Generation Agent

This guide validates the planned workflow end to end using local fixture materials and file-based artifacts.

## Prerequisites

- Python 3.11+ environment.
- A fixture project plan document and at least one supporting image.
- At least one template under `templates/<domain>/<template-id>/` with `template.pptx`, `meta.json`, and preview images.
- Optional: configured accounts in `.catpaw/skills/gptimage2-generator/assets/accounts.json` for visual generation. If no account is configured, fallback visual mode should still produce a draft.

## Expected Commands

```bash
python -m ppt_agent.cli create-job \
  --input ./fixtures/sample_project/ \
  --output ./workspace/jobs/sample-project
```

Expected result:

- `workspace/jobs/sample-project/input/` contains copied inputs.
- Job metadata is created.

```bash
python -m ppt_agent.cli run \
  --job ./workspace/jobs/sample-project \
  --until content-review
```

Expected result:

- `source_summary.json` exists.
- `outline.json` exists.
- `template_meta.json` exists.
- `slide_contents.json` exists and is readable/editable.

```bash
python -m ppt_agent.cli approve \
  --job ./workspace/jobs/sample-project \
  --slide-contents ./workspace/jobs/sample-project/slide_contents.json
```

Expected result:

- Slide content review status is marked approved.

```bash
python -m ppt_agent.cli run \
  --job ./workspace/jobs/sample-project \
  --from visual-generation
```

Expected result:

- `generated_slides/` contains generated or fallback visuals.
- `final.pptx` exists.
- `validation_report.json` exists.

## Validation Scenarios

### Scenario 1: Full Draft With Editable Text

Use a normal fixture with a project plan and screenshots.

Pass conditions:

- Final deck contains 8-20 slides.
- Title and bullet text from approved `slide_contents.json` remain editable.
- `validation_report.json` marks approved-text slides with `text_editable: true`.
- Validation report status is `passed` or `passed_with_warnings`.

### Scenario 2: No Visual Generation Account

Leave the GPTImage2 account file empty and run the same workflow.

Pass conditions:

- Workflow does not fail at visual generation.
- Final deck uses template visuals, placeholders, or user images.
- Validation report includes fallback warnings.

### Scenario 3: Long Text Overflow

Edit one slide in `slide_contents.json` to include overly long bullet text.

Pass conditions:

- Layout fitting either adjusts the text or validation flags the affected slide.
- The final report includes a manual review item if text still does not fit.

### Scenario 4: Template Mismatch

Use a template with fewer layouts than the outline requires.

Pass conditions:

- Workflow maps remaining slides to reusable/general layouts.
- Validation report does not mark the whole job failed solely due to template mismatch.

## Artifact Checks

Validate contracts with schema checks:

```bash
python -m ppt_agent.cli validate-artifacts \
  --job ./workspace/jobs/sample-project \
  --contracts ./specs/001-ppt-generation-agent/contracts
```

Expected result:

- `outline.json`, `slide_contents.json`, `template_meta.json`, image generation config/report, and validation report conform to their contracts.
