"""Intelligent Chunking — §8.2.1.

Type-aware document chunking that respects structural boundaries:

| Document Type | Chunking Strategy                    |
|---------------|--------------------------------------|
| Markdown      | Header-based hierarchical split      |
| Source Code   | Function/class definition split      |
| JSON / JSONL  | Top-level object split               |
| Plain Text    | Recursive character split w/ overlap |

The public entry point is :func:`chunk_document`.  The legacy
:func:`chunk_text` is retained as an alias for the plain-text fallback.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# ------------------------------------------------------------------ #
#  Plain-text fallback (legacy behaviour)                            #
# ------------------------------------------------------------------ #

def chunk_text(text: str, max_chars: int = 1200, overlap: int = 100) -> list[str]:
    """Recursive character split with overlap — the plain-text fallback."""
    if not text:
        return [""]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end]
        # Try to break on a sentence / paragraph boundary near the end
        if end < len(text):
            boundary = _find_boundary(chunk)
            if boundary > max_chars * 0.5:
                chunk = chunk[:boundary].rstrip()
                end = start + boundary
        chunks.append(chunk)
        next_start = end - overlap if end < len(text) else end
        if next_start <= start:
            next_start = start + 1
        start = next_start
    return chunks or [""]


def _find_boundary(text: str) -> int:
    """Find the best break point near the end of *text*."""
    for pattern in (r"\n\n", r"\n", r"[。！？!?\.]", r"[，,;；]"):
        matches = list(re.finditer(pattern, text))
        if matches:
            return matches[-1].end()
    return len(text)


# ------------------------------------------------------------------ #
#  Markdown chunking                                                 #
# ------------------------------------------------------------------ #

_MD_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def chunk_markdown(text: str, max_chars: int = 1200) -> list[str]:
    """Split markdown by headers (##, ###, etc.).

    Each chunk starts with the header line and includes all content up
    to the next header of the same or higher level.  Oversized sections
    are further split with :func:`chunk_text`.
    """
    if not text.strip():
        return [""]

    # Find all header positions
    headers = list(_MD_HEADER_RE.finditer(text))
    if not headers:
        return chunk_text(text, max_chars=max_chars)

    chunks: list[str] = []
    # Content before the first header
    if headers[0].start() > 0:
        preface = text[: headers[0].start()].strip()
        if preface:
            chunks.extend(chunk_text(preface, max_chars=max_chars))

    for i, hdr in enumerate(headers):
        level = len(hdr.group(1))
        start = hdr.start()
        # Find the end: next header of same or higher level, or end of text
        end = len(text)
        for j in range(i + 1, len(headers)):
            if len(headers[j].group(1)) <= level:
                end = headers[j].start()
                break
        section = text[start:end].strip()
        if not section:
            continue
        if len(section) > max_chars:
            # Preserve the header on the first sub-chunk
            header_line = hdr.group(0)
            body = section[len(header_line):].strip()
            sub_chunks = chunk_text(body, max_chars=max_chars - len(header_line) - 2)
            for k, sc in enumerate(sub_chunks):
                if k == 0:
                    chunks.append(f"{header_line}\n{sc}")
                else:
                    chunks.append(sc)
        else:
            chunks.append(section)

    return chunks or [""]


# ------------------------------------------------------------------ #
#  Source code chunking                                              #
# ------------------------------------------------------------------ #

# Match common function/class/method definitions across Python, JS/TS, Java, etc.
_CODE_DEF_RE = re.compile(
    r"^(?:"
    r"(?:def|class|async\s+def)\s+\w+"               # Python
    r"|(?:function\s+\w+|class\s+\w+)"                # JS/TS
    r"|(?:public|private|protected|static)?\s*(?:class|void|int|String|boolean|def)\s+\w+\s*\("  # Java-ish
    r")\s*[\(:{]",
    re.MULTILINE,
)


def chunk_source_code(text: str, max_chars: int = 1200) -> list[str]:
    """Split source code by function/class definitions.

    Uses regex to find definition boundaries.  If the code is too short
    or has no detectable definitions, falls back to :func:`chunk_text`.
    """
    if not text.strip():
        return [""]

    defs = list(_CODE_DEF_RE.finditer(text))
    if len(defs) <= 1:
        return chunk_text(text, max_chars=max_chars)

    chunks: list[str] = []
    # Content before the first definition (imports, module docstring, etc.)
    if defs[0].start() > 0:
        preface = text[: defs[0].start()].strip()
        if preface:
            chunks.extend(chunk_text(preface, max_chars=max_chars))

    for i, d in enumerate(defs):
        start = d.start()
        end = defs[i + 1].start() if i + 1 < len(defs) else len(text)
        block = text[start:end].rstrip()
        if not block:
            continue
        if len(block) > max_chars:
            chunks.extend(chunk_text(block, max_chars=max_chars))
        else:
            chunks.append(block)

    return chunks or [""]


# ------------------------------------------------------------------ #
#  JSON / JSONL chunking                                             #
# ------------------------------------------------------------------ #

def chunk_json(text: str, max_chars: int = 1200) -> list[str]:
    """Split JSON / JSONL by top-level objects.

    - JSON array: each element becomes a chunk.
    - JSON object with a list value: each element of the first list
      value becomes a chunk.
    - JSONL: each line is a chunk.
    - Fallback: :func:`chunk_text` on raw text.
    """
    text = text.strip()
    if not text:
        return [""]

    # Try JSONL first (one JSON object per line).
    # JSONL requires at least 2 non-blank lines, each starting with '{'.
    lines = text.splitlines()
    non_blank = [l for l in lines if l.strip()]
    if len(non_blank) >= 2 and all(l.strip().startswith("{") for l in non_blank):
        chunks: list[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if len(line) > max_chars:
                chunks.extend(chunk_text(line, max_chars=max_chars))
            else:
                chunks.append(line)
        return chunks or [""]

    # Try parsing as a single JSON document
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return chunk_text(text, max_chars=max_chars)

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Find the first list value
        items = None
        for v in data.values():
            if isinstance(v, list):
                items = v
                break
        if items is None:
            # Single object — return as one chunk
            return [text] if len(text) <= max_chars else chunk_text(text, max_chars=max_chars)
    else:
        return [text]

    chunks = []
    for item in items:
        serialized = json.dumps(item, ensure_ascii=False) if not isinstance(item, str) else item
        if len(serialized) > max_chars:
            chunks.extend(chunk_text(serialized, max_chars=max_chars))
        else:
            chunks.append(serialized)
    return chunks or [""]


def _is_json_line(line: str) -> bool:
    """Check if a single line looks like a JSON object/array."""
    line = line.strip()
    if not line:
        return True  # blank lines are OK in JSONL
    return line.startswith("{") or line.startswith("[")


# ------------------------------------------------------------------ #
#  Type detection                                                    #
# ------------------------------------------------------------------ #

def detect_type(text: str, filename: str | None = None) -> str:
    """Infer document type from filename extension or content heuristics."""
    if filename:
        ext = Path(filename).suffix.lower()
        ext_map = {
            ".md": "markdown",
            ".markdown": "markdown",
            ".py": "code",
            ".js": "code",
            ".ts": "code",
            ".jsx": "code",
            ".tsx": "code",
            ".java": "code",
            ".go": "code",
            ".rs": "code",
            ".cpp": "code",
            ".c": "code",
            ".h": "code",
            ".rb": "code",
            ".json": "json",
            ".jsonl": "json",
        }
        if ext in ext_map:
            return ext_map[ext]

    # Content-based heuristics
    stripped = text.lstrip()
    if stripped.startswith(("# ", "## ", "### ")):
        return "markdown"
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
            return "json"
        except (json.JSONDecodeError, ValueError):
            pass
    if _CODE_DEF_RE.search(text):
        return "code"
    return "text"


# ------------------------------------------------------------------ #
#  Public API                                                        #
# ------------------------------------------------------------------ #

def chunk_document(
    text: str,
    doc_type: str | None = None,
    filename: str | None = None,
    max_chars: int = 1200,
    overlap: int = 100,
) -> list[str]:
    """Type-aware chunking dispatcher.

    Parameters
    ----------
    text
        Raw document text.
    doc_type
        Explicit type override (``"markdown"``, ``"code"``, ``"json"``,
        ``"text"``).  When ``None``, type is auto-detected.
    filename
        Used for type detection when *doc_type* is ``None``.
    max_chars
        Maximum characters per chunk.
    overlap
        Overlap size for the plain-text fallback.

    Returns a list of chunk strings (never empty — returns ``[""]`` for
    empty input to match the legacy contract).
    """
    if not text:
        return [""]

    if doc_type is None:
        doc_type = detect_type(text, filename)

    if doc_type == "markdown":
        return chunk_markdown(text, max_chars=max_chars)
    elif doc_type == "code":
        return chunk_source_code(text, max_chars=max_chars)
    elif doc_type == "json":
        return chunk_json(text, max_chars=max_chars)
    else:
        return chunk_text(text, max_chars=max_chars, overlap=overlap)
