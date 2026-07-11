#!/usr/bin/env python3
"""Batch import PPTX templates from a watch directory.

Usage:
    python scripts/batch_ingest.py                     # scan raw-templates/
    python scripts/batch_ingest.py --dir my-pptx       # scan custom dir
    python scripts/batch_ingest.py --force             # overwrite existing

All PPTX files found in the directory are imported in one go.
Already-imported files are skipped (unless --force).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to path so we can import ppt_agent
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from ppt_agent.templates.ingest import auto_ingest


def main():
    parser = argparse.ArgumentParser(
        description="Batch-import PPTX templates from a watch directory"
    )
    parser.add_argument(
        "--dir", default="raw-templates",
        help="Directory to scan for PPTX files (default: raw-templates/)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite existing templates",
    )
    args = parser.parse_args()

    watch_dir = Path(args.dir)
    if not watch_dir.exists():
        print(f"[FAIL] Directory not found: {watch_dir}")
        print(f"       Create it and drop your PPTX files there:")
        print(f"         mkdir {watch_dir}")
        return 1

    pptx_files = sorted(watch_dir.glob("*.pptx"))
    if not pptx_files:
        print(f"[INFO] No .pptx files found in {watch_dir}/")
        print(f"       Drop your PPTX templates there and run again.")
        return 0

    # Check which ones are already imported
    templates_root = _PROJECT_ROOT / "templates"
    existing_ids = _list_existing_template_ids(templates_root)

    new_files = []
    skip_files = []
    for f in pptx_files:
        tid = f"__.{(f.stem)}"  # rough check
        if any(tid.endswith(f".{f.stem}") for tid in existing_ids) and not args.force:
            skip_files.append(f)
        else:
            new_files.append(f)

    if skip_files:
        print(f"[SKIP] {len(skip_files)} already imported:")
        for f in skip_files:
            print(f"         {f.name}")
    if not new_files:
        print(f"[INFO] All {len(pptx_files)} files already imported. Use --force to re-import.")
        return 0

    print(f"[INGEST] Importing {len(new_files)} template(s)...\n")

    ok, fail = 0, 0
    for f in new_files:
        try:
            entry = auto_ingest(f, templates_root=templates_root, force=args.force)
            print(f"  [OK] {f.name} -> {entry['template_id']}")
            print(f"       domain={entry['domain']} style={entry['style']} "
                  f"scene={entry['scene']} slides={entry['slide_count']}")
            ok += 1
        except Exception as e:
            print(f"  [FAIL] {f.name}: {e}")
            fail += 1

    print(f"\n[DONE] {ok} imported, {fail} failed, {len(skip_files)} skipped")
    return 0 if fail == 0 else 1


def _list_existing_template_ids(templates_root: Path) -> set[str]:
    """Collect all known template IDs from meta.json files."""
    ids: set[str] = set()
    for meta_path in templates_root.glob("*/*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            tid = meta.get("template_id")
            if tid:
                ids.add(tid)
        except Exception:
            pass
    # Also check index.json for manually-defined ones
    idx_path = templates_root / "index.json"
    if idx_path.exists():
        try:
            data = json.loads(idx_path.read_text(encoding="utf-8"))
            for entry in data.get("templates", []):
                tid = entry.get("template_id")
                if tid:
                    ids.add(tid)
        except Exception:
            pass
    return ids


if __name__ == "__main__":
    raise SystemExit(main())
