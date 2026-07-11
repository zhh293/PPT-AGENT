# PPT-Agent 分块检索策略详解

> 完整覆盖离线入库管道、智能分块算法、上下文增强、模板索引自动发现、在线 4-Stage RAG 检索管线的实现细节。

---

## 目录

1. [系统总览](#1-系统总览)
2. [离线入库管道 (IngestionPipeline)](#2-离线入库管道-ingestionpipeline)
3. [智能分块算法 (chunking.py)](#3-智能分块算法-chunkingpy)
4. [上下文增强 (enrichment.py)](#4-上下文增强-enrichmentpy)
5. [索引构建与持久化](#5-索引构建与持久化)
6. [模板索引自动发现 (template_index.py)](#6-模板索引自动发现-template_indexpy)
7. [在线检索：4-Stage RAG Pipeline](#7-在线检索4-stage-rag-pipeline)
8. [Stage 1——查询增强 (query_enhancement.py)](#8-stage-1查询增强-query_enhancementpy)
9. [Stage 2——双路检索 + RRF 融合](#9-stage-2双路检索--rrf-融合)
10. [Stage 3——启发式重排序 (rerankers/base.py)](#10-stage-3启发式重排序-rerankersbasepy)
11. [Stage 4——上下文组装](#11-stage-4上下文组装)
12. [实际应用：模板匹配全链路](#12-实际应用模板匹配全链路)
13. [关键参数速查表](#13-关键参数速查表)

---

## 1. 系统总览

检索系统在 PPT-Agent 中的核心作用是**模板匹配**（Phase 2）：给定 outline 中的领域(domain)、受众(audience)、语调(tone)，从模板库中找到最匹配的 PPT 模板。

```
                    ┌─────────────────────────┐
                    │   outline.json          │
                    │   { domain, audience,   │
                    │     tone, slide_count } │
                    └───────────┬─────────────┘
                                │
                                ▼
                    ┌─────────────────────────┐
                    │   4-Stage RAG Pipeline  │
                    │   query_router.query()  │
                    └───────────┬─────────────┘
                                │
                    ┌───────────┼───────────┐
                    │           │           │
                    ▼           ▼           ▼
              ┌─────────┐ ┌─────────┐ ┌─────────┐
              │ BM25    │ │ TF-IDF  │ │ RRF     │
              │ 稀疏检索 │ │ 向量检索 │ │ 融合排序 │
              └─────────┘ └─────────┘ └─────────┘
```

整个检索管线分为两个生命周期：

| 环节 | 时机 | 说明 |
|------|------|------|
| **离线入库** | 系统初始化 / 模板库变更时 | 模板 meta.json → chunk → enrich → 索引 |
| **在线检索** | 每次 PPT 生成任务 | outline → query enhance → dual-path → RRF → rerank → top-K |

---

## 2. 离线入库管道 (IngestionPipeline)

```
文件目录
  │
  ▼
_discover_files()          ← 递归扫描，收集所有文件
  │
  ▼
_extract_text()            ← 提取纯文本（.txt/.md/.docx 等）
  │
  ▼
_chunk_text()              ← 按文档类型智能分块 (chunk_size=512, overlap=64)
  │                         chunk_document(text, filename, max_chars, overlap)
  │
  ▼
_enrich_chunk()            ← 为每块拼接上下文导言（Anthropic Contextual Retrieval）
  │                         "This chunk is from X, located in the middle..."
  │
  ▼
KnowledgeBaseIndex         ← 组装 document 列表，持久化为 chunks.json
  │
  ▼
BM25Retriever(documents)   ← 在线检索时构建 BM25 倒排索引
LocalVectorBackend(documents) ← 在线检索时构建 TF-IDF 向量空间
```

### 2.1 源码位置

| 模块 | 文件 | 职责 |
|------|------|------|
| 入库管道 | `retrieval/ingestion_pipeline.py` | 编排整个入库流程 |
| 智能分块 | `retrieval/chunking.py` | 4 种文档类型感知的分块策略 |
| 上下文增强 | `retrieval/enrichment.py` | Anthropic 方法的上下文导言生成 |
| 模板索引 | `retrieval/template_index.py` | 从 `templates/*/meta.json` 自动发现模板 |
| BM25 | `retrieval/sparse/bm25.py` | 经典 BM25 稀疏检索实现 |
| TF-IDF | `retrieval/vector/local.py` | 本地 TF-IDF 向量，Cosine 相似度 |
| RRF | `retrieval/fusion/rrf.py` | Reciprocal Rank Fusion (k=60) |
| 查询路由 | `retrieval/query_router.py` | 4-Stage RAG 管线总入口 |
| 查询增强 | `retrieval/query_enhancement.py` | 查询改写 + 多查询扩展 |
| 重排序 | `retrieval/rerankers/base.py` | 启发式 4 信号 Cross-Encoder |

### 2.2 文本提取 (`tools/documents.py`)

支持的文件类型和提取方式：

```python
def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()

    # 纯文本类：直接读
    if suffix in {".txt", ".md", ".csv", ".json", ".yaml", ".yml"}:
        return path.read_text(encoding="utf-8", errors="ignore")

    # Word：ZipFile → XML → 提取 <w:t> 文本节点
    if suffix == ".docx":
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
        root = ET.fromstring(xml)
        # 遍历 w:p → w:t 拼接段落文本
        ...

    # Excel：仅返回文件名占位
    if suffix == ".xlsx":
        return f"Spreadsheet file: {path.name}"

    # 其他：不处理
    return ""
```

关键限制：**PDF 文件当前不支持文本提取**，`extract_text()` 对 PDF 直接返回空字符串。文档解析依赖 LLM 模式下的 document_analysis prompt 做补充。

---

## 3. 智能分块算法 (chunking.py)

### 3.1 统一入口：`chunk_document()`

```python
def chunk_document(
    text: str,
    doc_type: str | None = None,   # 显式指定，None 则自动检测
    filename: str | None = None,   # 用于扩展名检测
    max_chars: int = 1200,         # 每块最大字符数（入库管道实际传入 512）
    overlap: int = 100,            # 纯文本重叠量（入库管道实际传入 64）
) -> list[str]:
```

### 3.2 类型检测：`detect_type()`

按优先级依次判断：

```
1. 文件扩展名 → 直接映射
   .md / .markdown      → "markdown"
   .py / .js / .ts /    → "code"
   .java / .go / .rs /
   .cpp / .c / .h / .rb
   .json / .jsonl       → "json"

2. 内容首字符
   以 "# " / "## " / "### " 开头 → "markdown"
   以 "{" / "[" 开头且可 json.loads → "json"

3. 正则匹配
   匹配到代码定义模式 → "code"

4. 默认 → "text"
```

### 3.3 Markdown 分块：`chunk_markdown()`

**策略：按标题层级分割**

```
输入文本:
  # 第一章
  内容A...

  ## 1.1 第一节
  内容B...

  ## 1.2 第二节
  内容C...

  # 第二章
  内容D...
```

**算法步骤：**

1. 用正则 `^(#{1,6})\s+(.+)$` 找到所有标题及其层级
2. 对于每个标题，**结束边界**是下一个**同级或更高级**标题的起始位置
3. 标题前的文本（前言）作为独立块，用 `chunk_text` 分割
4. 超过 `max_chars` 的 section 做二次分割：
   - 标题行 + body 前 N 字符作为第一个子块（**保留原标题**）
   - 剩余 body 用 `chunk_text` 递归分割
   - 子块不再带标题

```
结果:
  chunk 0: "# 第一章\n内容A..."          ← 遇到 ## 1.1（下一同级）截断
  chunk 1: "## 1.1 第一节\n内容B..."     ← 遇到 ## 1.2（下一同级）截断
  chunk 2: "## 1.2 第二节\n内容C..."     ← 遇到 # 第二章（更高层级）截断
  chunk 3: "# 第二章\n内容D..."          ← 到文本末尾
```

关键细节：
- 用 `len(headers[j].group(1)) <= level` 判断是否结束——同级或更高级都算边界
- 超大 section 的第一块保留原标题行，后面不保留，避免上下文污染

### 3.4 源代码分块：`chunk_source_code()`

**策略：按函数/类定义边界分割**

正则模式覆盖 Python、JS/TS、Java 等：

```
^(?:
  (?:def|class|async\s+def)\s+\w+          # Python
  |(?:function\s+\w+|class\s+\w+)          # JS/TS
  |(?:public|private|protected|static)?
    \s*(?:class|void|int|String|boolean|def)\s+\w+\s*\(  # Java-ish
)\s*[\(:{]
```

算法：
1. 找到所有定义边界位置
2. 第一个定义之前的内容（imports、module docstring）作为前言块
3. 每个定义块 = `text[def[i].start : def[i+1].start]`
4. 超大块用 `chunk_text` 二次分割
5. 只有一个或零个定义 → 整体降级为 `chunk_text`

### 3.5 JSON/JSONL 分块：`chunk_json()`

**策略：按顶层对象拆分，保留结构完整性**

```
优先级 1 — JSONL 检测
  条件: ≥2 行非空行，且每行以 "{" 开头
  方式: 每行一个 chunk
  超大行用 chunk_text 二次分割

优先级 2 — JSON Array
  方式: 每个元素 stringify → 一个 chunk
  超大元素用 chunk_text 二次分割

优先级 3 — JSON Object with list value
  方式: 找到第一个 list 类型的 value，拆其元素
  没有 list → 整个对象作为一个 chunk

优先级 4 — 降级
  无法 parse → chunk_text 纯文本模式
```

### 3.6 纯文本分块：`chunk_text()`

**策略：递归字符分割 + overlap**

```python
def chunk_text(text: str, max_chars: int = 1200, overlap: int = 100):
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end]

        # 在 chunk 尾部附近找最佳断点
        if end < len(text):
            boundary = _find_boundary(chunk)
            if boundary > max_chars * 0.5:   # 断点不能太靠前
                chunk = chunk[:boundary]
                end = start + boundary

        chunks.append(chunk)
        next_start = end - overlap    # 下一块起始 = 当前结束 - overlap
        start = max(next_start, start + 1)  # 保证前进
```

**断点寻找 `_find_boundary()`**：从 chunk 末尾向前搜索，按优先级：

```
1. \n\n     ← 段落分隔（最优）
2. \n       ← 行分隔
3. 。！？!?. ← 句子结束标点
4. ，,;；   ← 子句分隔
```

取**最后一个**匹配位置作为断点。这样每块都在自然的语义边界处断开。

---

## 4. 上下文增强 (enrichment.py)

遵循 **Anthropic Contextual Retrieval** 方法，每个 chunk 入库时在前面拼接一个简短的**上下文导言 (contextual preamble)**，描述该 chunk 在原文中的位置。这显著提升 BM25 关键词匹配和向量语义相似度的质量。

### 4.1 核心函数：`enrich_chunk()`

```python
def enrich_chunk(
    text: str,
    context: str = "",
    *,
    document_name: str = "",    # 如 "项目计划书.pdf"
    chunk_index: int = 0,       # 0-based
    total_chunks: int = 0,
    doc_type: str = "",         # "markdown" | "code" | "json" | "text"
    llm_client: LLMClient | None = None,
) -> str:
```

### 4.2 LLM 模式（`_llm_enrich`）

**System Prompt:**
```
You are a document context annotator. Given a document chunk and
optional metadata, write a 1-2 sentence contextual preamble that
explains where this chunk sits within the document and what topic
it covers. This preamble will be prepended to the chunk to improve
retrieval. Respond with ONLY the preamble text — no labels, no
markdown, no explanation.
```

**User Prompt:**
```
Metadata: Document: 项目计划书.pdf; Type: text; Chunk 5 of 12

Chunk content:
{原文前 2000 字符}
```

**超参：** `temperature=0.0`, `max_tokens=150`

**输出示例：**
```
This chunk is from the project plan document, located in the middle
section covering the core product architecture and technical
implementation details. It describes the AI-assisted image analysis
pipeline and integration with existing hospital PACS systems.
```

**最终入库文本：**
```
[Contextual Preamble]
[Original Chunk Text]
```

### 4.3 启发式降级（`_heuristic_preamble`）

无 LLM 客户端的默认模式，从元数据拼接：

```python
def _heuristic_preamble(document_name, chunk_index, total_chunks, doc_type, text):
    parts = []

    if document_name:
        parts.append(f"This chunk is from {document_name}")

    if total_chunks > 1:
        position = "beginning" if idx==0 else ("end" if idx==last else "middle")
        parts.append(f"located in the {position} of the document (chunk {i+1}/{total})")

    if doc_type:
        labels = {"markdown": "a Markdown section", "code": "a source code block",
                  "json": "a JSON data structure", "text": "a text passage"}
        parts.append(f"containing {labels[doc_type]}")

    return "This chunk is from 项目计划书.pdf, located in the middle
            of the document (chunk 5/12), containing a text passage."
```

### 4.4 入库管道中的实际调用

```python
# ingestion_pipeline.py::_enrich_chunk()
prompt = (
    f"Given this document:\n\n{full_doc_text[:2000]}\n\n"
    f"Write a short (80-token) context that situates this chunk "
    f"within the document:\n\n{chunk.text[:200]}\n\nContext:"
)
result = self.llm_client.provider.generate(
    [LLMMessage.user(prompt)],
    temperature=0.1,
    max_tokens=80  # 比 enrichment.py 的 150 更短
)
```

注意：入库管道的 `_enrich_chunk` 与 `enrichment.py::enrich_chunk` 是**两套独立的实现**。前者是 `IngestionPipeline` 的私有方法，后者是公共 API。两者都做上下文导言生成但 prompt 格式略有不同。

---

## 5. 索引构建与持久化

### 5.1 数据结构

```python
@dataclass
class IngestedChunk:
    chunk_id: str           # "chunk_00001"
    source_doc: str         # 源文件路径
    text: str               # 原始 chunk 文本（不含 preamble）
    context_prefix: str     # 上下文导言
    metadata: dict          # {"source": ..., "char_count": ...}

    @property
    def retrieval_text(self) -> str:
        """检索用文本 = context_prefix + chunk 原文"""
        if self.context_prefix:
            return f"{self.context_prefix}\n{self.text}"
        return self.text
```

关键设计：**`retrieval_text` 同时包含了上下文导言和原始内容**，这意味着 BM25 和 TF-IDF 索引都能利用上下文丰富性来提升匹配精度。

### 5.2 持久化格式

```json
// chunks.json
{
  "kb_name": "templates",
  "chunks": [
    {
      "chunk_id": "chunk_00000",
      "source_doc": "templates/tech/modern-blue-12/meta.json",
      "text": "{\"template_id\": \"tech/modern-blue-12\", ...}",
      "context_prefix": "This chunk is from modern-blue-12 meta.json, ...",
      "metadata": { "source": "templates/tech/modern-blue-12/meta.json", "char_count": 450 }
    }
  ]
}
```

### 5.3 在线检索时的索引构建

检索是在线进行的——每次调用 `query()` 时从 `documents` 列表构建索引：

```python
# query_router.py
bm25 = BM25Retriever(documents)         # 构建 BM25 倒排索引
vector = LocalVectorBackend(documents)   # 构建 TF-IDF 向量空间
```

`documents` 来自模板索引加载器，格式为：
```python
[{
    "id": "tech/modern-blue-12",
    "text": "简洁专业 blue 科技 tech/modern-blue-12",  # retrieval_text
    "template_id": "tech/modern-blue-12",
    "domain_tags": ["科技"],
    "tone_tags": ["简洁专业"],
    "color_scheme": "blue",
    "slide_count": 12,
    ...
}]
```

---

## 6. 模板索引自动发现 (template_index.py)

### 6.1 自动发现机制

无需手动维护 `index.json`。放入模板文件夹即可被自动发现：

```
templates/
├── tech/                          ← 分类目录名 → domain 标签
│   ├── modern-blue-12/
│   │   ├── meta.json              ← 自动扫描
│   │   ├── template.pptx
│   │   └── preview/
│   │       ├── slide_0.png
│   │       └── ...
│   └── dark-minimal-10/
│       └── meta.json              ← 自动扫描
├── medical/
│   └── clean-green-15/
│       └── meta.json              ← 自动扫描
└── index.json                     ← 可选，手动覆盖
```

### 6.2 扫描逻辑

```python
def _discover_templates(root: Path) -> list[dict]:
    # 扫描 templates/*/*/meta.json
    for meta_path in sorted(root.glob("*/*/meta.json")):
        category = meta_path.parent.parent.name    # 如 "tech"
        meta = json.loads(meta_path.read_text())
        entry = _build_entry(meta, category, meta_path.parent)
        entries.append(entry)
```

跳过 `sample`、`__pycache__`、`.git` 目录。

### 6.3 索引条目生成：`_build_entry()`

从 `meta.json` 提取信息，自动生成检索文本：

```python
def _build_entry(meta, category, tpl_dir):
    template_id = meta["template_id"]              # "tech/modern-blue-12"
    domain = meta.get("domain", category)          # "科技"
    style = meta.get("style", "general")           # "简洁专业"
    scene = meta.get("scene", [])                  # ["路演", "产品发布"]
    color_scheme = meta.get("color_scheme", {})
    color_name = _guess_color_name(color_scheme)   # "blue"

    domain_tags = list(set([domain] + scene))      # ["科技", "路演", "产品发布"]
    tone_tags = [style]                            # ["简洁专业"]

    # 自动生成检索文本
    retrieval_parts = [style, color_name, domain] + scene + [template_id]
    retrieval_text = " ".join(retrieval_parts).lower()
    # → "简洁专业 blue 科技 路演 产品发布 tech/modern-blue-12"
```

### 6.4 颜色名推断：`_guess_color_name()`

从 `color_scheme.primary` 的 hex 值推断颜色名：

```
1. 饱和度 delta = max(r,g,b) - min(r,g,b)
   若 delta < 35 → 消色系:
      lightness > 0.85 → "white"
      lightness < 0.25 → "dark"
      否则 → "gray" 或 "neutral"

2. 彩色系 — 按主导通道:
      max == R: G > 120 → "orange"; G < 90 → "red"; 否则 → "pink"
      max == G → "green"
      max == B → "blue"
```

### 6.5 手动/自动合并

```python
def _merge(manual, auto):
    # 同一 template_id: 手动覆盖自动
    # 手动独有: 追加
    # 自动独有: 追加
```

`index.json` 可以只覆盖需要调整的字段，其余由自动发现补充。

---

## 7. 在线检索：4-Stage RAG Pipeline

完整入口：

```python
# query_router.py
def query(
    documents: list[dict],         # 模板索引条目，每个必须有 "text" 字段
    query_text: str,               # 用户查询（从 outline 构建）
    methods: list[str] | None,     # 保留参数，实际强制双路
    top_k: int = 5,                # 最终返回数量
    llm_client: LLMClient | None,  # 可选 LLM 客户端
) -> list[dict]:
```

### 7.1 Pipeline 流程图

```
用户查询 "医疗 投资人 专业严谨"
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 1: Query Enhancement                                  │
│                                                             │
│ rewrite_query()  → "医疗健康行业 投资路演 专业正式 路演PPT" │
│ expand_query()   → ["医疗AI融资路演模板",                    │
│                      "专业医疗项目投资推介PPT",               │
│                      "医疗机构商业计划书演示"]               │
│                                                             │
│ all_queries = [rewritten, expanded...]  (2-5 个查询)        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 2: Multi-Route Retrieval + RRF Fusion                 │
│                                                             │
│ For each query in all_queries:                              │
│   ┌─────────────────┐     ┌─────────────────┐              │
│   │ BM25Retriever   │     │LocalVectorBackend│              │
│   │                 │     │                  │              │
│   │ • 倒排索引查询  │     │ • TF-IDF 向量化  │              │
│   │ • BM25 打分     │     │ • Cosine 相似度  │              │
│   │ • 取 top-100    │     │ • 取 top-100     │              │
│   └────────┬────────┘     └────────┬────────┘              │
│            │                       │                        │
│            └───────────┬───────────┘                        │
│                        ▼                                    │
│           RRF 融合 (k=60)                                   │
│           RRF_score = Σ 1/(60 + rank_i)                     │
│                        │                                    │
│   跨所有查询的结果再 RRF 融合                                │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 3: Reranking (heuristic cross-encoder)                │
│                                                             │
│ 4 信号加权:                                                 │
│   0.35 × phrase_overlap  (精确短语匹配)                     │
│   0.35 × term_coverage   (查询词覆盖率)                     │
│   0.15 × position_weight (位置加权，靠前更高)               │
│   0.15 × length_norm     (长度归一化，200-800 最优)         │
│                                                             │
│ → 重新排序 → 丢弃 score < 0.01 的结果                       │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ Stage 4: Context Assembly                                   │
│                                                             │
│ • 阈值过滤: score >= 0.0                                    │
│ • 去重: 按 id → template_id → text[:200]                    │
│ • 来源标注: source + retrieval_method                       │
│ • 截断: top_k                                               │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
                    返回 top-K 结果
```

---

## 8. Stage 1——查询增强 (query_enhancement.py)

### 8.1 查询改写：`rewrite_query()`

**目的：** 将口语化查询转化为关键词密集的检索优化形式。

**LLM 模式——System Prompt:**
```
You are a search query optimiser. Rewrite the user's query into a
concise, keyword-rich form optimised for document retrieval.
Remove filler words, expand abbreviations, and add synonyms.
Respond with ONLY the rewritten query — no explanation.
```

**调用参数：** `temperature=0.0`, `max_tokens=256`

**示例：**
```
输入: "这东西怎么用"
输出: "产品使用说明 操作指南 功能文档"

输入: "医疗投资人路演"
输出: "医疗健康行业 投资路演 专业正式 商业计划书PPT模板"
```

**降级：** 无 LLM 时返回原始查询（identity fallback）。

### 8.2 多查询扩展：`expand_query()`

**目的：** 生成 2-4 个互补角度查询，每个覆盖信息需求的不同侧面。

**LLM 模式——System Prompt + JSON Schema 约束：**
```
You are a search query expansion assistant. Given a user query,
generate 2 to 4 complementary search queries, each targeting a
different facet of the information need.

Schema: {"queries": ["query1", "query2", ...]}
```

**调用参数：** `temperature=0.3`, `max_tokens=512`

**示例：**
```
输入: "医疗 投资人 专业严谨"

生成:
  Q1: "医疗AI融资路演PPT模板 蓝色专业风格"
  Q2: "专业医疗项目投资推介演示模板 简洁大方"
  Q3: "医疗机构商业计划书PPT 投资人路演 数据可视化"

→ 过滤掉与原始查询相同的 → 返回列表
```

### 8.3 启发式扩展：`heuristic_expand()`

无 LLM 时的降级方案：

```python
def heuristic_expand(query: str) -> list[str]:
    tokens = _tokenize(query)  # 正则 [\w一-鿿]+
    stop = {"the", "a", "an", "is", "are", "of", "to", "in", "on",
            "for", "how", "what", "why", "where", "and", "or"}

    keywords = [t for t in tokens if t not in stop]
    variants = []

    # 变体 1: 纯关键词版（去停用词）
    if keywords != tokens:
        variants.append(" ".join(keywords))

    # 变体 2-3: 二元组分对（≥3 个关键词时）
    if len(keywords) >= 3:
        variants.append(" ".join(keywords[:2]))   # 前两个
        variants.append(" ".join(keywords[1:]))    # 后 N-1 个

    return variants[:4]
```

**示例：**
```
输入: "医疗投资人专业严谨路演"
tokens: ["医疗", "投资人", "专业", "严谨", "路演"]
keywords: ["医疗", "投资人", "专业", "严谨", "路演"]  (中文无 stopword)

→ ["医疗 投资人", "投资人 专业 严谨 路演"]
```

---

## 9. Stage 2——双路检索 + RRF 融合

### 9.1 BM25 稀疏检索 (`sparse/bm25.py`)

**标准 BM25 实现，全部从零构建。**

**分词：**
```python
_TOKEN_RE = re.compile(r"[\w一-鿿]+")

def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())
```
- `\w` 匹配英文单词字符
- `一-鿿` 匹配中文汉字（CJK 统一表意文字基本区）
- 全部 lower()

**索引构建（`__init__`）：**
```python
class BM25Retriever:
    def __init__(self, documents):
        self.documents = documents
        # 每个文档的 token 列表
        self.tokens = [tokenize(doc["text"]) for doc in documents]
        # 文档频率: df[term] = 包含 term 的文档数
        self.df = Counter(token for toks in self.tokens for token in set(toks))
        # 平均文档长度
        self.avgdl = sum(len(toks) for toks in self.tokens) / max(1, len(self.tokens))
```

**BM25 打分公式（`search` 方法）：**
```python
# 参数: k1=1.5, b=0.75

for each document d:
    score = 0
    for each query term t:
        # IDF（平滑版）
        idf = log((N - df(t) + 0.5) / (df(t) + 0.5) + 1)

        # TF 归一化
        tf_norm = tf(t,d) * (k1 + 1) / (tf(t,d) + k1 * (1 - b + b * doc_len / avgdl))

        score += idf * tf_norm
```

**关键参数：**
- `k1=1.5`：控制 TF 饱和速度
- `b=0.75`：控制文档长度归一化强度（b=1 完全归一化，b=0 不归一化）
- 候选池大小：`min(100, len(documents))`

### 9.2 TF-IDF 向量检索 (`vector/local.py`)

**纯本地实现，不依赖任何外部 embedding 模型或向量数据库。**

**TF-IDF 公式：**

```python
# 规范化 TF（除以文档内最大词频，抑制长文档偏差）
tf(t, d) = count(t, d) / max(count(any_term, d))

# 平滑 IDF
idf(t) = log((N + 1) / (df(t) + 1)) + 1

# TF-IDF 权重
tfidf(t, d) = tf(t, d) * idf(t)
```

**索引构建（`_build_index`）：**

```python
def _build_index(self):
    # Pass 1: 分词 + 计算 TF + 统计 DF
    for doc in self.documents:
        tokens = _tokenize(doc["text"])
        counts = Counter(tokens)
        max_count = max(counts.values())
        tf = {term: count/max_count for term, count in counts.items()}
        self._doc_tf.append(tf)
        for term in counts:
            df[term] += 1

    # 构建词表 + IDF
    for term, doc_freq in df.items():
        self._idf[term] = math.log((N + 1) / (doc_freq + 1)) + 1

    # Pass 2: 计算 TF-IDF 向量 + L2 范数
    for tf in self._doc_tf:
        tfidf = {term: tf_val * self._idf[term] for term, tf_val in tf.items()
                 if tf_val * self._idf[term] > 0}
        self._doc_tfidf.append(tfidf)
        self._doc_norms.append(sqrt(sum(w*w for w in tfidf.values())))
```

**Cosine 相似度检索（`search`）：**

```python
def search(self, query, top_k=5):
    # 1. 查询向量化
    query_tfidf = self._compute_query_tfidf(query)
    query_norm = sqrt(sum(w*w for w in query_tfidf.values()))

    # 2. 对每个文档计算 Cosine 相似度
    for i, doc in enumerate(self.documents):
        # 遍历较短的向量（性能优化）
        if len(query_tfidf) <= len(doc_tfidf):
            dot = sum(w * doc_tfidf.get(term, 0) for term, w in query_tfidf.items())
        else:
            dot = sum(w * query_tfidf.get(term, 0) for term, w in doc_tfidf.items())

        sim = dot / (query_norm * doc_norm)
        scored.append((sim, doc))

    # 3. 按 sim 降序排列，取 top_k
    scored.sort(key=lambda p: p[0], reverse=True)
    return [item for _, item in scored[:top_k]]
```

**查询 IDF 使用的是语料库预计算的 IDF**（不是查询内 IDF），这保证了查询和文档在同一向量空间。

### 9.3 RRF 融合 (`fusion/rrf.py`)

**Reciprocal Rank Fusion，k=60：**

```python
def reciprocal_rank_fusion(result_sets: list[list[dict]], k: int = 60):
    scores: dict[str, float] = {}
    payloads: dict[str, dict] = {}

    for results in result_sets:           # [BM25结果, TF-IDF结果]
        for rank, item in enumerate(results, start=1):
            key = item.get("id") or item.get("template_id") or item.get("text", "")
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            payloads[key] = item

    # 按 RRF score 降序返回
    return sorted(..., key=lambda p: p[1], reverse=True)
```

**为什么 k=60：**
- 经典值，经验证明对各种 score 分布都鲁棒
- 较小的 k 更强调排名差异，较大的 k 更均匀
- 60 在大多数场景中是 sweet spot

**RRF 的关键优势：**
- 不依赖原始 score 量纲（BM25 是任意正数，Cosine 是 [0,1]）
- 不受异常分数影响
- 仅依赖相对排序

### 9.4 多查询融合

```python
# 对每个扩展查询独立跑 BM25 + TF-IDF → RRF 融合
for q in all_queries:  # 原始 + 改写 + 扩展 = 2~5 个查询
    bm25_results = bm25.search(q, top_k=pool)
    vector_results = vector.search(q, top_k=pool)
    fused = reciprocal_rank_fusion([bm25_results, vector_results])
    all_fused_sets.append(fused)

# 跨查询再次 RRF 融合
fused_results = reciprocal_rank_fusion(all_fused_sets)
```

这样实现了**双层 RRF**：先在检索路径间融合，再在查询间融合。

---

## 10. Stage 3——启发式重排序 (rerankers/base.py)

不需要外部 cross-encoder 模型，纯启发式计算 4 个信号，加权求和重新排序。

### 10.1 权重配置

```python
Reranker(
    phrase_weight=0.35,    # 精确短语匹配
    coverage_weight=0.35,  # 查询词覆盖率
    position_weight=0.15,  # 位置加权
    length_weight=0.15,    # 长度归一化
    min_score=0.01,        # 低于此分数的结果丢弃
)
```

### 10.2 信号 1——精确短语匹配 (`_phrase_overlap`)

```python
def _phrase_overlap(query_lower, doc_lower):
    # 完整查询串完全匹配
    if query_lower in doc_lower:
        ratio = len(query_lower) / max(len(doc_lower), 1)
        return min(1.0, 0.7 + ratio * 0.3)

    # 部分匹配: 找最长公共 n-gram
    for i in range(len(query_terms)):
        for j in range(i+1, len(query_terms)+1):
            phrase = " ".join(query_terms[i:j])
            if phrase in doc_lower:
                max_phrase_len = max(max_phrase_len, len(query_terms[i:j]))

    if max_phrase_len == 0: return 0.0
    return max_phrase_len / len(query_terms) * 0.5
```

**逻辑：**
- 完整匹配 → 0.7 起步 + 短语占文档比例加成 → 最高 1.0
- 部分匹配 → 最长公共 n-gram 占查询词比例 × 0.5 → 最高 0.5
- 完全没有公共子串 → 0

### 10.3 信号 2——查询词覆盖率 (`_term_coverage`)

```python
def _term_coverage(query_terms, doc_lower):
    doc_tokens = set(_tokenize(doc_lower))
    found = sum(1 for t in query_terms if t in doc_tokens)
    return found / len(query_terms)
```

简单直接：文档中出现了多少个查询词。范围 [0, 1]。

### 10.4 信号 3——位置加权 (`_position_weighting`)

```python
def _position_weighting(query_terms, doc_lower):
    # 构建每个 token 首次出现位置的查找表
    first_pos = {}
    for idx, tok in enumerate(doc_tokens):
        if tok not in first_pos:
            first_pos[tok] = idx

    # 指数衰减: e^(-3 * position / total_length)
    for term in query_terms:
        if term in first_pos:
            pos = first_pos[term]
            decay = math.exp(-3.0 * pos / max(total_len, 1))
            weights.append(decay)
        else:
            weights.append(0.0)

    return sum(weights) / len(weights)
```

**逻辑：**
- 查询词在文档开头出现 → 权重接近 1.0
- 在中间 → ~0.22
- 在末尾 → ~0.05
- 没出现 → 0

### 10.5 信号 4——长度归一化 (`_length_norm`)

```python
def _length_norm(doc_text):
    length = len(doc_text)
    if length == 0:     return 0.0
    if length <= 100:   return length / 100 * 0.5          # 0 → 0.5
    if length <= 200:   return 0.5 + (length-100)/100*0.5  # 0.5 → 1.0
    if length <= 800:   return 1.0                         # 甜点区
    if length <= 2000:  return 1.0 - (length-800)/1200*0.3 # 1.0 → 0.7
    return max(0.3, 0.7 - (length-2000)/5000*0.2)          # 0.7 → ≥0.3
```

**设计思路：**
- 太短（<100 字符）→ 缺少上下文，降权
- 适中（200-800）→ 信息密度最优，满分 1.0
- 太长（>800）→ 信息稀释，逐渐降权
- 极长（>2000）→ 最低 0.3，不完全淘汰

### 10.6 最终打分

```python
final = (0.35 * phrase_score
       + 0.35 * coverage_score
       + 0.15 * position_score
       + 0.15 * length_score)
```

---

## 11. Stage 4——上下文组装

```python
def _assemble_context(results, top_k, query_text):
    seen = set()
    deduped = []

    for item in results:
        # 阈值过滤
        if item.get("score", 0) < 0.0:  # 当前实现门槛为 0
            continue

        # 去重 key: id → template_id → text[:200]
        key = item.get("id") or item.get("template_id") or item.get("text", "")[:200]
        if key in seen:
            continue
        seen.add(key)

        # 来源标注
        enriched = {
            **item,
            "source": item.get("source", _infer_source(item)),
            "retrieval_method": "hybrid_bm25_tfidf_rrf_rerank",
        }
        deduped.append(enriched)

    return deduped[:top_k]
```

---

## 12. 实际应用：模板匹配全链路

### 12.1 调用入口 (`workers/template_matcher.py`)

```python
def run(workspace, force=False):
    outline = load_artifact(workspace, "outline")
    templates = load_template_index()         # 自动发现模板

    # 从 outline 构建查询
    query_parts = [
        outline["meta"].get("domain", ""),     # "医疗"
        outline["meta"].get("audience", ""),   # "投资人"
        outline["meta"].get("tone", ""),       # "专业严谨"
    ]
    query_text = " ".join(filter(None, query_parts))
    # → "医疗 投资人 专业严谨"

    # 运行 4-Stage RAG Pipeline
    ranked = query(
        [
            {"id": t["template_id"], "template_id": t["template_id"],
             "text": t.get("retrieval_text", ""), **t}
            for t in templates
        ],
        query_text,    # "医疗 投资人 专业严谨"
        ["bm25", "vector"],
        top_k=3,
    )

    # 取最佳匹配
    if ranked and ranked[0].get("score", 0) > 0:
        best_match = ...  # 加载真实模板 meta
    else:
        # 降级为 fallback 模板
        selected = fallback_selection()
```

### 12.2 完整数据流

```
templates/**/meta.json
    │
    ▼
load_template_index()
    │ _build_entry() 为每个模板生成:
    │   retrieval_text = "简洁专业 blue 科技 路演 tech/modern-blue-12"
    │
    ▼
documents = [{id, template_id, text: retrieval_text, ...}, ...]
    │
    ▼
query("医疗 投资人 专业严谨", documents, top_k=3)
    │
    ├── Stage 1: 查询增强
    │   rewrite: "医疗健康行业 投资路演 专业正式 商业计划书PPT"
    │   expand:  ["医疗AI融资路演模板", "专业医疗项目投资推介PPT", ...]
    │   all_queries = [rewritten, expanded...]
    │
    ├── Stage 2: 双路检索 + RRF
    │   For each query:
    │     BM25(documents).search(query, top_k=100)     → 100 results
    │     LocalVectorBackend(documents).search(query, top_k=100) → 100 results
    │     RRF([bm25_results, vector_results])          → fused
    │   RRF(all_fused_sets)                            → final fused
    │
    ├── Stage 3: 重排序
    │   Reranker().rerank(query_text, fused_results)
    │   → 4 信号加权 → 重排 → 过滤
    │
    └── Stage 4: 上下文组装
        → 去重 → 标注 → top_k=3
    │
    ▼
返回 Top-3 模板:
  [
    {"template_id": "medical/blue-clean-15", "score": 0.92, ...},
    {"template_id": "medical/green-warm-12", "score": 0.78, ...},
    {"template_id": "general/professional-gray", "score": 0.65, ...},
  ]
```

---

## 13. 关键参数速查表

### 离线入库

| 参数 | 默认值 | 位置 | 说明 |
|------|--------|------|------|
| `chunk_size` | 512 | `ingestion_pipeline.py` | 每块最大字符数 |
| `chunk_overlap` | 64 | `ingestion_pipeline.py` | 纯文本块重叠字符数 |
| `max_chars` (API) | 1200 | `chunking.py` | `chunk_document` 的公共 API 默认值 |
| `overlap` (API) | 100 | `chunking.py` | `chunk_document` 的公共 API 默认值 |
| `enrich_max_tokens` | 80 | `ingestion_pipeline.py` | LLM 上下文导言最大长度 |
| `enrich_temperature` | 0.1 | `ingestion_pipeline.py` | LLM 导言生成温度 |
| `enrich_max_tokens` (API) | 150 | `enrichment.py` | `enrich_chunk` 的公共 API 默认值 |
| `enrich_temperature` (API) | 0.0 | `enrichment.py` | `enrich_chunk` 的公共 API 默认值 |

### 在线检索

| 参数 | 默认值 | 位置 | 说明 |
|------|--------|------|------|
| `_CANDIDATE_POOL` | 100 | `query_router.py` | 每条检索路径的候选数 |
| RRF `k` | 60 | `fusion/rrf.py` | RRF 融合常数 |
| BM25 `k1` | 1.5 | `sparse/bm25.py` | TF 饱和参数 |
| BM25 `b` | 0.75 | `sparse/bm25.py` | 长度归一化参数 |
| `top_k` | 3 | `template_matcher.py` | 模板匹配返回数 |
| `top_k` (API) | 5 | `query_router.py` | `query()` 公共 API 默认值 |
| `rewrite_temperature` | 0.0 | `query_enhancement.py` | 查询改写温度 |
| `expand_temperature` | 0.3 | `query_enhancement.py` | 查询扩展温度 |
| `expand_count` | 2-4 | `query_enhancement.py` | 扩展查询数量 |

### 重排序

| 参数 | 默认值 | 位置 | 说明 |
|------|--------|------|------|
| `phrase_weight` | 0.35 | `rerankers/base.py` | 精确短语权重 |
| `coverage_weight` | 0.35 | `rerankers/base.py` | 词覆盖率权重 |
| `position_weight` | 0.15 | `rerankers/base.py` | 位置衰减权重 |
| `length_weight` | 0.15 | `rerankers/base.py` | 长度归一化权重 |
| `min_score` | 0.01 | `rerankers/base.py` | 最低分数阈值 |
| `position_decay_rate` | 3.0 | `rerankers/base.py` | 指数衰减率 e^(-3×pos/len) |
| 长度甜点区 | 200-800 | `rerankers/base.py` | 满分长度范围 |

---

> *文档生成时间: 2026-07-08*
> *基于源码版本: feature-mvp1*
> *覆盖文件: retrieval/chunking.py, retrieval/enrichment.py, retrieval/ingestion_pipeline.py, retrieval/template_index.py, retrieval/query_router.py, retrieval/query_enhancement.py, retrieval/sparse/bm25.py, retrieval/vector/local.py, retrieval/fusion/rrf.py, retrieval/rerankers/base.py, workers/template_matcher.py*
