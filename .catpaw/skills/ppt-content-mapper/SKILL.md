---
description: Strict LLM-first mapping of source-grounded presentation copy into exact template zones without resizing, hard truncation, missing slides, or preserved sample business text.
---

# PPT Content Mapper

Produce a complete `slide_contents.json` by adapting approved outline content
to the real editable zones of the selected PowerPoint template.

## Non-negotiable output contract

1. Return exactly the requested `expected_slide_indices`. Never renumber a
   batch locally and never omit, duplicate, merge, or reorder a slide.
2. Copy `template_slide_index` and every `zone_id` exactly. A zone ID is an
   execution address, not a content decision rule.
3. Do not request or imply overlays, new text boxes, geometry changes, font
   changes, font-size changes, or position changes.
4. Every editable business text zone must use `replace_text` with non-empty,
   source-grounded copy. `preserve` is allowed only for intentional brand,
   page-number, date, competition, or footer chrome. Never preserve template
   sample business claims, metrics, names, categories, or financial data.
5. Use as many editable zones as the template page contains. There is no
   three-zone limit.

## Think semantically before assigning zones

For each slide:

1. State its narrative job from the outline.
2. Read the original wording, role, geometry, typography, and neighboring
   zones to infer components such as title/subtitle, card label/value/detail,
   process step, metric, caption, comparison column, or footer.
3. Build source-grounded content blocks from the slide title, bullets,
   evidence, and source references.
4. Assign blocks to components by meaning, emphasis, and capacity. Generate
   new concise wording when needed; do not rotate or repeat bullets merely to
   fill boxes.
5. Keep every rewrite traceable with `source_block_ids` and an accurate
   `transformation` value.

## Exact fitting rules

Every template text zone includes machine-computed `min_chars`, `max_chars`,
and `max_lines`. These are hard acceptance constraints.

- Count visible characters after removing whitespace.
- Write natural copy within `min_chars <= visible_chars <= max_chars`.
- A title or short label must express a complete idea, not a prefix.
- Never cut a Chinese phrase, English word, identifier, number, percentage,
  or unit to satisfy a limit.
- Treat technical and metric tokens as atomic, including examples such as
  `WebSocket`, `taskId`, `12000+ QPS`, `320ms`, `96%`, and `XSS/SQL`.
- If a sentence is too long, rewrite it semantically: remove qualifiers,
  choose a shorter synonym, or turn it into a concise label. Do not return a
  substring such as `WebSo`, `320m`, or `服务治`.
- Short, narrow, rotated, vertical, or decorative zones receive concise
  labels only; never put prose in them.
- If truthful copy cannot fit, return `fit_status: overflow` and a review flag.
  Never claim `fits` for truncated or incomplete copy.

## Self-check before returning JSON

Verify all of the following:

- The returned slide-index set exactly equals `expected_slide_indices`.
- Every requested business text zone is present exactly once.
- Every `replace_text` value is non-empty and within its exact range.
- No content ends in a partial English token, partial number/unit, or broken
  Chinese phrase.
- No isolated bullet glyph such as `u`, `•`, or `●` is used as content.
- No unsupported template metric or sample-project wording remains.
- Repeated wording is intentional only for overlapping visual text layers.
- Output is one valid JSON object with no Markdown or explanation around it.

## Targeted repair protocol

When the runtime supplies `repair_targets`, do not regenerate the slide or any
already accepted zone. Each request contains at most four zones and uses a
compact `repairs` response schema.

- Return exactly one repair item for every requested `zone_id` and no others.
- Rewrite by meaning; never shorten by character slicing.
- Use only the supplied `allowed_source_block_ids`.
- Obey each zone's exact `min_chars`, `max_chars`, and `max_lines` independently.
- Keep the wording coherent with `neighboring_zone_copy`, the slide narrative,
  the original template label, and adjacent slides.
- If the runtime rejects one item, the next request contains only that zone;
  return one new semantic rewrite rather than repeating the rejected wording.
