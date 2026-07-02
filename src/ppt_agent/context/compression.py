from __future__ import annotations


def compress_context(payload: dict, level: str = "L1") -> dict:
    if level in {"L0", "L1"}:
        return payload
    preserved = {k: payload[k] for k in ("slide_contents", "errors", "approved_text") if k in payload}
    if level == "L4":
        return preserved
    return {**preserved, "summary": payload.get("summary", "")}


def assert_preserves_approved_text(original: dict, compressed: dict) -> None:
    if "approved_text" in original and compressed.get("approved_text") != original["approved_text"]:
        raise AssertionError("approved_text was dropped during compression")
