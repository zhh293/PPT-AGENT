"""HTML preview generator for slide_contents.json.

Generates a visual preview page where users can see each slide's layout
(title, bullets, image zones) as cards, making it much easier to review
the generated content before approving.
"""

from __future__ import annotations

import html
import json
from pathlib import Path


_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
       background: #f0f2f5; color: #1f2933; padding: 24px; }
h1 { text-align: center; margin-bottom: 8px; font-size: 24px; color: #1f4e79; }
.meta { text-align: center; color: #6b7280; font-size: 13px; margin-bottom: 28px; }
.slides { display: flex; flex-direction: column; gap: 24px; max-width: 960px; margin: 0 auto; }
.slide-card { background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08);
              overflow: hidden; }
.slide-header { display: flex; justify-content: space-between; align-items: center;
                padding: 12px 20px; background: #1f4e79; color: #fff; font-size: 13px; }
.slide-header .badge { background: rgba(255,255,255,0.2); padding: 2px 10px;
                       border-radius: 10px; font-size: 11px; }
.slide-header .badge.draft { background: #f59e0b; color: #1f2933; }
.slide-header .badge.approved { background: #10b981; }
.slide-header .badge.warning { background: #ef4444; }
.slide-body { display: flex; gap: 0; min-height: 220px; }
.slide-text { flex: 1; padding: 20px 24px; display: flex; flex-direction: column; gap: 12px; }
.slide-visual { width: 260px; min-height: 200px; background: #f8fafc; display: flex;
                align-items: center; justify-content: center; border-left: 1px solid #e5e7eb;
                flex-shrink: 0; padding: 16px; }
.slide-title { font-size: 20px; font-weight: 700; color: #1f4e79; line-height: 1.3; }
.bullet-list { list-style: none; padding: 0; }
.bullet-list li { padding: 4px 0 4px 18px; position: relative; font-size: 14px;
                  line-height: 1.6; color: #374151; }
.bullet-list li::before { content: "●"; position: absolute; left: 0; color: #70ad47;
                          font-size: 10px; top: 8px; }
.img-placeholder { background: #eef2f6; border: 2px dashed #b8c2cc; border-radius: 8px;
                   width: 100%; height: 100%; display: flex; flex-direction: column;
                   align-items: center; justify-content: center; gap: 6px;
                   color: #6b7280; font-size: 12px; min-height: 140px; }
.img-placeholder .icon { font-size: 28px; opacity: 0.4; }
.img-real { max-width: 100%; max-height: 180px; border-radius: 6px;
            object-fit: contain; }
.slide-footer { padding: 8px 20px; background: #f9fafb; border-top: 1px solid #f3f4f6;
                font-size: 11px; color: #9ca3af; display: flex; gap: 16px; flex-wrap: wrap; }
.slide-footer span { white-space: nowrap; }
.fallback-tag { display: inline-block; background: #fef3c7; color: #92400e; font-size: 10px;
                padding: 1px 6px; border-radius: 4px; margin-left: 6px; }
.legend { max-width: 960px; margin: 32px auto 0; padding: 16px 20px; background: #fff;
          border-radius: 8px; font-size: 12px; color: #6b7280; line-height: 1.8; }
.legend strong { color: #1f2933; }
"""


def _render_zone_visual(zone: dict, job_root: Path | None) -> str:
    """Render an image/visual zone as HTML."""
    image_ref = zone.get("image_ref")
    image_prompt = zone.get("image_prompt", "")

    if image_ref and job_root:
        img_path = job_root / image_ref
        if img_path.exists():
            return f'<img class="img-real" src="file://{img_path}" alt="slide visual">'

    prompt_preview = html.escape(image_prompt[:80]) if image_prompt else "Visual placeholder"
    return f'''<div class="img-placeholder">
        <span class="icon">🖼</span>
        <span>{prompt_preview}</span>
    </div>'''


def _render_slide(slide: dict, job_root: Path | None) -> str:
    """Render a single slide card."""
    idx = slide.get("slide_index", 0)
    layout = slide.get("layout_id", slide.get("layout", "unknown"))
    density = slide.get("visual_density", "medium")
    status = slide.get("review_status", "draft")
    fallback_flags = slide.get("fallback_flags", [])
    zones = slide.get("zones", [])

    # Extract content from zones
    title_text = ""
    bullets: list[str] = []
    visual_html = ""
    for zone in zones:
        ztype = zone.get("type", "")
        content = zone.get("content")
        if ztype == "title" and content:
            title_text = content if isinstance(content, str) else str(content)
        elif ztype in ("subtitle",) and content:
            title_text += f'<div style="font-size:14px;color:#6b7280;margin-top:4px">{html.escape(str(content))}</div>'
        elif ztype == "bullets" and content:
            if isinstance(content, list):
                bullets = content
            elif isinstance(content, str):
                bullets = [content]
        elif ztype == "image":
            visual_html = _render_zone_visual(zone, job_root)

    if not title_text:
        title_text = f"Slide {idx + 1}"

    # Badge class
    badge_cls = "badge"
    if status == "draft":
        badge_cls += " draft"
    elif status == "approved":
        badge_cls += " approved"
    else:
        badge_cls += " warning"

    # Build bullets HTML
    bullets_html = ""
    if bullets:
        items = "\n".join(f"<li>{html.escape(str(b))}</li>" for b in bullets)
        bullets_html = f'<ul class="bullet-list">{items}</ul>'

    # Fallback tags
    fallback_html = ""
    for flag in fallback_flags:
        fallback_html += f'<span class="fallback-tag">{html.escape(flag)}</span>'

    # Visual section (only if there's visual content)
    visual_section = ""
    if visual_html:
        visual_section = f'<div class="slide-visual">{visual_html}</div>'

    return f'''<div class="slide-card">
    <div class="slide-header">
        <span>Slide {idx + 1} / {layout}</span>
        <span class="{badge_cls}">{status}</span>
    </div>
    <div class="slide-body">
        <div class="slide-text">
            <div class="slide-title">{html.escape(title_text)}</div>
            {bullets_html}
        </div>
        {visual_section}
    </div>
    <div class="slide-footer">
        <span>density: {density}</span>
        <span>zones: {len(zones)}</span>
        {fallback_html}
    </div>
</div>'''


def generate_preview_html(slide_contents: dict, job_root: Path | None = None) -> str:
    """Generate a complete HTML preview page from slide_contents."""
    template_id = slide_contents.get("template_id", "unknown")
    review_status = slide_contents.get("review_status", "draft")
    slides = slide_contents.get("slides", [])

    slides_html = "\n".join(_render_slide(s, job_root) for s in slides)

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PPT Preview — {html.escape(template_id)}</title>
    <style>{_CSS}</style>
</head>
<body>
    <h1>PPT Content Preview</h1>
    <div class="meta">
        Template: {html.escape(template_id)} &nbsp;|&nbsp;
        Status: {html.escape(review_status)} &nbsp;|&nbsp;
        Slides: {len(slides)}
    </div>
    <div class="slides">
        {slides_html}
    </div>
    <div class="legend">
        <strong>How to review:</strong>
        Edit <code>slide_contents.json</code> to modify titles, bullets, or image prompts.
        Then run <code>ppt-agent approve --job &lt;path&gt;</code> to mark as approved.
        <br>
        <strong>Status badges:</strong>
        <span class="badge draft" style="font-size:11px;padding:1px 8px;border-radius:8px;background:#f59e0b;color:#1f2933">draft</span> = awaiting review &nbsp;
        <span class="badge approved" style="font-size:11px;padding:1px 8px;border-radius:8px;background:#10b981;color:#fff">approved</span> = ready for assembly &nbsp;
        <span class="fallback-tag">visual_placeholder</span> = image not yet generated
    </div>
</body>
</html>'''


def write_preview(workspace_root: Path, slide_contents: dict | None = None) -> Path:
    """Generate and write preview.html to the workspace root.

    If slide_contents is not provided, reads from slide_contents.json.
    Returns the path to the generated HTML file.
    """
    if slide_contents is None:
        sc_path = workspace_root / "slide_contents.json"
        if not sc_path.exists():
            raise FileNotFoundError(f"slide_contents.json not found at {sc_path}")
        slide_contents = json.loads(sc_path.read_text(encoding="utf-8"))

    html_content = generate_preview_html(slide_contents, job_root=workspace_root)
    output_path = workspace_root / "preview.html"
    output_path.write_text(html_content, encoding="utf-8")
    return output_path
