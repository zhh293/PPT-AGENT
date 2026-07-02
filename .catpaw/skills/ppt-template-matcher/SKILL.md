# PPT Template Matcher

Use this skill to select a template profile from the configured template knowledge base.

Rules:

- Prefer domain, audience, tone, and layout coverage over visual novelty.
- If no reliable match exists, return `fallback.default` with `selection_status: fallback`.
- Always include deterministic ranking evidence and fallback reasons.
