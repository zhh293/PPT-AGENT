from __future__ import annotations

from ppt_agent.context.compression import assert_preserves_approved_text, compress_context


def test_compression_preserves_approved_text() -> None:
    payload = {"summary": "x", "approved_text": ["A"], "errors": ["E"]}
    compressed = compress_context(payload, "L4")
    assert_preserves_approved_text(payload, compressed)
    assert compressed["errors"] == ["E"]
