from __future__ import annotations


def title_font_size(text: str) -> int:
    if len(text) > 60:
        return 24
    if len(text) > 36:
        return 30
    return 36


def bullet_font_size(bullets: list[str]) -> int:
    total = sum(len(item) for item in bullets)
    if total > 420 or len(bullets) > 6:
        return 15
    if total > 240:
        return 17
    return 20


def assert_text_preserved(expected: list[str], actual: list[str]) -> None:
    missing = [item for item in expected if item not in actual]
    if missing:
        raise AssertionError(f"Text not preserved: {missing}")
