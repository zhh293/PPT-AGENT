from __future__ import annotations


def reciprocal_rank_fusion(result_sets: list[list[dict]], k: int = 60) -> list[dict]:
    scores: dict[str, float] = {}
    payloads: dict[str, dict] = {}
    for results in result_sets:
        for rank, item in enumerate(results, start=1):
            key = item.get("id") or item.get("template_id") or item.get("text", "")
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            payloads[key] = item
    return [{**payloads[key], "score": score} for key, score in sorted(scores.items(), key=lambda pair: pair[1], reverse=True)]
