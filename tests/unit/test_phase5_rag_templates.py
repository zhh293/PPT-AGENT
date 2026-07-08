"""Tests for Phase 5 — ingestion pipeline, template matching, and template library."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ppt_agent.retrieval.ingestion_pipeline import (
    IngestionPipeline,
    IngestedChunk,
    KnowledgeBaseIndex,
)


# ── IngestionPipeline ────────────────────────────────────────────────

def test_ingestion_discovers_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
    (tmp_path / "b.md").write_text("# title\n\ncontent here", encoding="utf-8")
    pipeline = IngestionPipeline("test", tmp_path / "kb")
    index = pipeline.ingest([tmp_path])
    assert len(index.chunks) >= 1


def test_ingestion_chunks_text(tmp_path: Path) -> None:
    text = "word " * 500  # 2500 chars → multiple chunks
    (tmp_path / "long.txt").write_text(text, encoding="utf-8")
    pipeline = IngestionPipeline("test", tmp_path / "kb", chunk_size=200, chunk_overlap=20)
    index = pipeline.ingest([tmp_path])
    assert len(index.chunks) > 1


def test_ingestion_skips_empty_files(tmp_path: Path) -> None:
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    pipeline = IngestionPipeline("test", tmp_path / "kb")
    index = pipeline.ingest([tmp_path])
    assert len(index.chunks) == 0


def test_ingestion_builds_documents(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("first document about AI", encoding="utf-8")
    (tmp_path / "b.txt").write_text("second document about PPT", encoding="utf-8")
    pipeline = IngestionPipeline("test", tmp_path / "kb")
    index = pipeline.ingest([tmp_path])
    assert len(index.documents) == len(index.chunks)
    assert all("id" in d and "text" in d for d in index.documents)


def test_knowledge_base_save_load(tmp_path: Path) -> None:
    (tmp_path / "doc.txt").write_text("sample text for indexing", encoding="utf-8")
    pipeline = IngestionPipeline("test", tmp_path / "kb")
    index = pipeline.ingest([tmp_path])

    save_dir = tmp_path / "saved"
    index.save(save_dir)
    assert (save_dir / "chunks.json").exists()

    loaded = KnowledgeBaseIndex.load(save_dir)
    assert loaded.name == "test"
    assert len(loaded.chunks) == len(index.chunks)
    assert loaded.chunks[0].text == index.chunks[0].text


def test_ingested_chunk_retrieval_text(tmp_path: Path) -> None:
    chunk = IngestedChunk(chunk_id="c1", source_doc="a.txt", text="body text")
    assert chunk.retrieval_text == "body text"

    chunk.context_prefix = "This is from section 3."
    assert "section 3" in chunk.retrieval_text
    assert "body text" in chunk.retrieval_text


# ── Template library ─────────────────────────────────────────────────

def test_template_index_has_all_five_templates() -> None:
    """The index.json should list 6 entries (5 real + 1 fallback)."""
    index_path = Path("templates/index.json")
    data = json.loads(index_path.read_text(encoding="utf-8"))
    templates = data["templates"]
    assert len(templates) == 6

    ids = {t["template_id"] for t in templates}
    assert ids == {
        "tech.modern-blue-12",
        "business.professional-gray-15",
        "medical.clean-green-14",
        "education.warm-orange-12",
        "general.minimal-white-10",
        "fallback.default",
    }


def test_all_templates_have_valid_meta_json() -> None:
    """Every non-fallback template must have a parseable meta.json."""
    index_path = Path("templates/index.json")
    data = json.loads(index_path.read_text(encoding="utf-8"))
    for t in data["templates"]:
        if t["template_id"] == "fallback.default":
            continue
        meta_path = Path(f"templates/{t['path']}/meta.json")
        assert meta_path.exists(), f"Missing meta.json for {t['template_id']}"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert "slides" in meta
        assert len(meta["slides"]) == t["slide_count"]
        assert "color_scheme" in meta
        # Every slide should have zones
        for slide in meta["slides"]:
            assert "zones" in slide
            assert len(slide["zones"]) >= 1


def test_templates_have_unique_domain_tags() -> None:
    """Different templates should have distinguishable domain tags."""
    index_path = Path("templates/index.json")
    data = json.loads(index_path.read_text(encoding="utf-8"))
    domain_sets = {}
    for t in data["templates"]:
        if t["template_id"] == "fallback.default":
            continue
        tags = frozenset(t["domain_tags"])
        for other_id, other_tags in domain_sets.items():
            # Templates should not be identical in domain tags
            assert tags != other_tags, f"{t['template_id']} and {other_id} have identical domain tags"
        domain_sets[t["template_id"]] = tags


def test_template_color_schemes_are_distinct() -> None:
    """Color schemes should differ across templates."""
    index_path = Path("templates/index.json")
    data = json.loads(index_path.read_text(encoding="utf-8"))
    schemes = set()
    for t in data["templates"]:
        if t["template_id"] == "fallback.default":
            continue
        schemes.add(t["color_scheme"])
    assert len(schemes) == 5  # all 5 real templates have different schemes


# ── Load index entry for ingestion ───────────────────────────────────

def test_template_index_entries_have_retrieval_text() -> None:
    """Every template entry must have retrieval_text for search."""
    index_path = Path("templates/index.json")
    data = json.loads(index_path.read_text(encoding="utf-8"))
    for t in data["templates"]:
        assert t.get("retrieval_text", ""), f"{t['template_id']} missing retrieval_text"
