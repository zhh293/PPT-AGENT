from __future__ import annotations


def classify_density(title: str, bullets: list[str]) -> str:
    count = len(title) + sum(len(item) for item in bullets)
    if count < 160:
        return "low"
    if count < 420:
        return "medium"
    return "high"
