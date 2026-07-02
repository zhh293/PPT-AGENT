from __future__ import annotations


def suggest_shorter_bullets(bullets: list[str], max_chars: int = 90) -> list[str]:
    return [item if len(item) <= max_chars else item[: max_chars - 1].rstrip() + "..." for item in bullets]
