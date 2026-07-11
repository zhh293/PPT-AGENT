"""Knowledge Base Ingestion Pipeline (§8.2).

Files → text extraction → intelligent chunking → (optional) contextual
enrichment → BM25 index → persistence.

Implements Anthropic's Contextual Retrieval methodology: each chunk is
optionally prefixed with a short LLM-generated context paragraph that
describes where the chunk sits in the source document.

Usage::

    pipeline = IngestionPipeline("templates", Path("workspace/kb"))
    index = pipeline.ingest([Path("templates/")])
    index.save(Path("workspace/kb/templates"))
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ppt_agent.retrieval.chunking import chunk_document
from ppt_agent.tools.documents import extract_text

logger = logging.getLogger(__name__)

_DEFAULT_CHUNK_SIZE = 512
_DEFAULT_CHUNK_OVERLAP = 64
_CONTEXT_ENRICHMENT_MAX_TOKENS = 80


@dataclass
class IngestedChunk:
    """A single chunk with enriched context."""

    chunk_id: str
    source_doc: str           # source file path
    text: str                 # the chunk's text content
    context_prefix: str = ""  # LLM-generated context (Anthropic contextual method)
    metadata: dict = field(default_factory=dict)

    @property
    def retrieval_text(self) -> str:
        """Full text for indexing: context_prefix + chunk text."""
        if self.context_prefix:
            return f"{self.context_prefix}\n{self.text}"
        return self.text


@dataclass
class KnowledgeBaseIndex:
    """Persisted knowledge base index.

    ``chunks`` stores the full text of all indexed chunks.
    ``documents`` is a list of dicts suitable for BM25Retriever.
    """

    name: str
    chunks: list[IngestedChunk]
    documents: list[dict] = field(default_factory=list)
    index_dir: Path | None = None

    def save(self, directory: Path) -> None:
        """Persist index to disk."""
        directory.mkdir(parents=True, exist_ok=True)
        chunks_data = [
            {
                "chunk_id": c.chunk_id,
                "source_doc": c.source_doc,
                "text": c.text,
                "context_prefix": c.context_prefix,
                "metadata": c.metadata,
            }
            for c in self.chunks
        ]
        (directory / "chunks.json").write_text(
            json.dumps({"kb_name": self.name, "chunks": chunks_data}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.index_dir = directory
        logger.info("Saved KB '%s' (%d chunks) to %s", self.name, len(self.chunks), directory)

    @classmethod
    def load(cls, directory: Path) -> KnowledgeBaseIndex:
        """Load index from disk."""
        chunks_path = directory / "chunks.json"
        if not chunks_path.exists():
            raise FileNotFoundError(f"Chunks file not found: {chunks_path}")

        raw = json.loads(chunks_path.read_text(encoding="utf-8"))
        kb_name = raw.get("kb_name", directory.name)
        chunks_data = raw.get("chunks", raw)  # support old format without wrapper
        chunks = [
            IngestedChunk(
                chunk_id=d["chunk_id"],
                source_doc=d["source_doc"],
                text=d["text"],
                context_prefix=d.get("context_prefix", ""),
                metadata=d.get("metadata", {}),
            )
            for d in chunks_data
        ]
        documents = [{"id": c.chunk_id, "text": c.retrieval_text} for c in chunks]
        return cls(name=kb_name, chunks=chunks, documents=documents, index_dir=directory)


class IngestionPipeline:
    """Knowledge base ingestion: files → chunks → enriched chunks → indexes.

    Pipeline stages:
        1. File discovery
        2. Text extraction via ``extract_text``
        3. Chunking via ``chunk_document``
        4. Context enhancement (optional, requires LLM client)
        5. BM25 index building
        6. Persistence
    """

    def __init__(
        self,
        kb_name: str,
        output_dir: Path,
        *,
        llm_client=None,  # optional LLMClient for context enrichment
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        self.kb_name = kb_name
        self.output_dir = output_dir
        self.llm_client = llm_client
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def ingest(self, source_paths: list[Path]) -> KnowledgeBaseIndex:
        """Run the full ingestion pipeline.

        Args:
            source_paths: Input files or directories to scan.

        Returns:
            A populated KnowledgeBaseIndex ready for search.
        """
        files = self._discover_files(source_paths)
        logger.info("Ingesting %d files into KB '%s'", len(files), self.kb_name)

        all_chunks: list[IngestedChunk] = []
        chunk_idx = 0

        for f in files:
            text = self._extract_text(f)
            if not text.strip():
                continue

            chunks = self._chunk_text(text, str(f))
            for chunk in chunks:
                chunk.chunk_id = f"chunk_{chunk_idx:05d}"
                chunk_idx += 1

                if self.llm_client is not None:
                    enriched = self._enrich_chunk(chunk, text)
                    all_chunks.append(enriched)
                else:
                    all_chunks.append(chunk)

        # Build document list for retrieval
        documents = [{"id": c.chunk_id, "text": c.retrieval_text} for c in all_chunks]

        return KnowledgeBaseIndex(
            name=self.kb_name,
            chunks=all_chunks,
            documents=documents,
        )

    def _discover_files(self, source_paths: list[Path]) -> list[Path]:
        """Recursively discover all ingestable files."""
        result: list[Path] = []
        for p in source_paths:
            p = Path(p)
            if p.is_file():
                result.append(p)
            elif p.is_dir():
                for child in p.rglob("*"):
                    if child.is_file():
                        result.append(child)
        return result

    def _extract_text(self, file_path: Path) -> str:
        """Extract text from a single file."""
        try:
            return extract_text(file_path)
        except Exception as exc:
            logger.warning("Failed to extract text from %s: %s", file_path, exc)
            return ""

    def _chunk_text(self, text: str, source_doc: str) -> list[IngestedChunk]:
        """Split text into chunks with metadata."""
        raw_chunks = chunk_document(text, filename=source_doc, max_chars=self.chunk_size, overlap=self.chunk_overlap)
        return [
            IngestedChunk(
                chunk_id="",  # assigned later
                source_doc=source_doc,
                text=c,
                metadata={"source": source_doc, "char_count": len(c)},
            )
            for c in raw_chunks
        ]

    def _enrich_chunk(self, chunk: IngestedChunk, full_doc_text: str) -> IngestedChunk:
        """Add LLM-generated context prefix (Anthropic Contextual Retrieval).

        Uses a short context window of the full document to situate the
        chunk within the larger document structure.
        """
        prompt = (
            f"Given this document:\n\n{full_doc_text[:2000]}\n\n"
            f"Write a short ({_CONTEXT_ENRICHMENT_MAX_TOKENS}-token) context that "
            f"situates this chunk within the document:\n\n{chunk.text[:200]}\n\n"
            f"Context:"
        )
        try:
            from ppt_agent.llm.messages import LLMMessage
            result = self.llm_client.provider.generate(
                [LLMMessage.user(prompt)],
                temperature=0.1,
                max_tokens=_CONTEXT_ENRICHMENT_MAX_TOKENS,
            )
            if result.success and result.text:
                chunk.context_prefix = result.text.strip()
        except Exception:
            logger.debug("Context enrichment failed for chunk %s, skipping", chunk.chunk_id)

        return chunk
