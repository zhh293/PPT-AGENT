#!/usr/bin/env python3
"""Extract accurate color schemes from template slide images using Qwen VL.

Usage: PYTHONPATH=src python scripts/color_extract.py [--dry-run]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from openai import OpenAI

API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
if not API_KEY:
    # Fallback: read from .env file
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().strip().split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == "DASHSCOPE_API_KEY":
                    API_KEY = v.strip()
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen-vl-plus"

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)


def extract_colors(image_paths: list[Path]) -> dict:
    """Send slide images to Qwen VL, get color scheme back."""
    # Build message with multiple images
    content = [
        {
            "type": "text",
            "text": (
                "Analyze these PPT template slides and extract the EXACT color scheme.\n"
                "Return a JSON object with these fields:\n"
                "- primary: main title/heading color (hex, e.g. \"#1F4E79\")\n"
                "- secondary: accent/secondary color (hex)\n"
                "- accent: highlight/emphasis color (hex)\n"
                "- background: main background color (hex)\n"
                "- text_primary: primary text color (hex)\n"
                "- text_secondary: secondary/subtitle text color (hex)\n"
                "- style: one of [\"modern\", \"professional\", \"clean\", \"warm\", \"bold\"]\n"
                "- brightness: one of [\"dark\", \"light\", \"balanced\"]\n"
                "Output ONLY the JSON object, no markdown, no extra text."
            ),
        }
    ]
    for img_path in image_paths[:3]:  # First 3 slides enough for color analysis
        import base64
        with open(img_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": content}],
        max_tokens=500,
    )
    raw = resp.choices[0].message.content.strip()
    # Clean markdown wrapping
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[:-3]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"  [WARN] Qwen returned non-JSON: {raw[:200]}")
        return {}


def main():
    dry_run = "--dry-run" in sys.argv
    templates_root = Path("templates")
    meta_files = sorted(templates_root.glob("*/*/meta.json"))

    updated = 0
    skipped = 0
    for meta_path in meta_files:
        tpl_dir = meta_path.parent
        tpl_name = f"{tpl_dir.parent.name}/{tpl_dir.name}"
        preview_dir = tpl_dir / "preview"

        images = sorted(preview_dir.glob("slide_0*.png"))[:3]
        if not images:
            print(f"[SKIP] {tpl_name} — no preview images")
            skipped += 1
            continue

        print(f"[PROCESS] {tpl_name} ({len(images)} images)...", end=" ", flush=True)

        if dry_run:
            print("DRY RUN")
            continue

        try:
            colors = extract_colors(images)
        except Exception as e:
            print(f"FAILED: {e}")
            skipped += 1
            continue

        if not colors:
            print("EMPTY")
            skipped += 1
            continue

        # Update meta.json
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["color_scheme"] = {
            "primary": colors.get("primary", ""),
            "secondary": colors.get("secondary", ""),
            "accent": colors.get("accent", ""),
            "background": colors.get("background", ""),
            "text_primary": colors.get("text_primary", ""),
            "text_secondary": colors.get("text_secondary", ""),
        }
        if "style" in colors:
            meta["style"] = colors["style"]
        if "brightness" in colors:
            meta["brightness"] = colors.get("brightness", "balanced")
        meta["color_source"] = "qwen-vl-plus"

        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"OK — {colors.get('primary','?')}/{colors.get('secondary','?')}/{colors.get('accent','?')} style={colors.get('style','?')}")
        updated += 1

    print(f"\nDone: {updated} updated, {skipped} skipped")


if __name__ == "__main__":
    main()
