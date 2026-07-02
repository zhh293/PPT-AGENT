from __future__ import annotations

from abc import ABC, abstractmethod


class VectorBackend(ABC):
    @abstractmethod
    def search(self, query: str, top_k: int = 5) -> list[dict]:
        raise NotImplementedError
