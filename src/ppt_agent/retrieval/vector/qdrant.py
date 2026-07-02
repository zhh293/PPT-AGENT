from __future__ import annotations

from ppt_agent.retrieval.vector.local import LocalVectorBackend


class QdrantBackend(LocalVectorBackend):
    def __init__(self, config: dict) -> None:
        if not config.get("collection"):
            raise ValueError("Qdrant config requires collection")
        super().__init__([])
