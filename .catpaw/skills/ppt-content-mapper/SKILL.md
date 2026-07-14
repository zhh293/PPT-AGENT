# PPT Content Mapper

Map approved outline content into template zones to produce `slide_contents.json`.

## CRITICAL RULES — VIOLATING THESE WILL BREAK THE PIPELINE

### 1. Position — COPY, NEVER INVENT

Every zone in the output MUST use the exact `position` array from the matching template zone in `all_zones`. You are FORBIDDEN from calculating, guessing, estimating, or inventing positions. The coordinates come from the PPTX file's actual shape positions and are the ONLY values that will align text with the template design.

```
✅ CORRECT: "position": [0.0459, 0.1143, 0.200, 0.0628]   ← copied from template
❌ WRONG:   "position": [0.1, 0.7, 0.8, 0.15]              ← invented, will misalign
```

### 2. zone_id — COPY, NEVER INVENT

Every zone MUST use the exact `zone_id` from the template `all_zones`. If you invent an ID, the assembler cannot find the matching shape.

### 3. Formatting — PRESERVE

If the template zone has a `formatting` object, carry it through unchanged. The font name, font size, color, bold, italic, and alignment come from the original PPTX shape.

### 4. Visual data — USE IT

Each template zone has a `visual` object with:
- `text_likeness`: how much this zone looks like a text area (0.0-1.0)
- `suggested_type`: what the pixel analysis thinks this zone is (title/body/decoration)
- `is_content_area`: whether this zone is in the main content region

Use these to decide WHICH zones get content:
- High text_likeness (>0.4) → good candidate for text content
- is_content_area=true → primary content target
- suggested_type="title" → prefer for title content
- text_likeness < 0.05 → likely decoration, leave empty (content: null)

### 5. Content assignment

```
For each slide:
  1. Read all template zones from all_zones
  2. Pick the best zone for the title (high text_likeness, wide, top area)
  3. Pick the best zone(s) for body/bullets (body-like zones)
  4. Leave decorative zones empty (content: null)
  5. Leave image zones with their prompts
```

### 6. Output ONLY valid JSON

No markdown, no comments outside the JSON. Output the complete slide_contents structure.
