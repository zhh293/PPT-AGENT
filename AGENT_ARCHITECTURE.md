# Agentic System Architecture: A Comprehensive Technical Design

> **Abstract**  
> This document presents a complete architectural design for a next-generation AI Agent runtime system. The design prioritizes lightweight persistence (file-system-first), progressive resource loading, industrial-grade retrieval-augmented generation (RAG), and a sophisticated multi-agent orchestration framework with formal isolation guarantees. The system draws upon state-of-the-art practices from frontier AI systems while maintaining minimal operational overhead through deliberate avoidance of heavyweight middleware dependencies.

---

## Table of Contents

1. [Design Philosophy](#1-design-philosophy)
2. [Memory System](#2-memory-system)
3. [Context Window Management](#3-context-window-management)
4. [Skill System: Progressive Loading](#4-skill-system-progressive-loading)
5. [Tool Registry & Permission Management](#5-tool-registry--permission-management)
6. [Event Bus Architecture](#6-event-bus-architecture)
7. [Sandbox Execution Environment](#7-sandbox-execution-environment)
8. [Knowledge Base: Hybrid Retrieval-Augmented Generation](#8-knowledge-base-hybrid-retrieval-augmented-generation)
9. [Multi-Agent Orchestration](#9-multi-agent-orchestration)
10. [Communication & Context Isolation](#10-communication--context-isolation)
11. [System Integration Overview](#11-system-integration-overview)

---

## 1. Design Philosophy

The architecture is governed by four cardinal principles:

**Principle 1 — Complexity Where It Matters.** Computational sophistication is concentrated in components that directly impact agent intelligence (retrieval quality, context engineering, orchestration strategies), while infrastructure concerns (storage, communication, deployment) remain deliberately minimal.

**Principle 2 — File-System as First-Class Citizen.** Persistent state is stored in human-readable, version-controllable file formats (Markdown, JSONL, JSON). This eliminates the operational burden of database administration, enables trivial debugging via text editors, and provides natural Git integration for state evolution tracking.

**Principle 3 — Progressive Disclosure of Information.** The system never loads more information into the context window than is immediately necessary. Skills, tools, and memory are revealed incrementally based on runtime demand, preserving precious context capacity for task-relevant reasoning.

**Principle 4 — Isolation by Default.** Every execution unit operates within well-defined boundaries—sandboxed file systems for code execution, AsyncLocalStorage for concurrent agent attribution, and file-based mailboxes for inter-agent communication. Shared mutable state is treated as a design defect.

---

## 2. Memory System

### 2.1 Three-Layer Memory Architecture

The memory system implements a cognitively-inspired three-layer hierarchy, each layer distinguished by its temporal scope, mutability characteristics, and sharing semantics:

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: agent.md                                          │
│  ─────────────────                                          │
│  Scope:       Immutable system identity                     │
│  Lifetime:    Permanent (manual revision only)              │
│  Sharing:     All sessions, all conversations               │
│  Content:     Agent personality, capabilities declaration,  │
│               behavioral constraints, tool usage policies   │
│  Analogy:     Constitutional DNA                            │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: memory.md                                         │
│  ──────────────────                                         │
│  Scope:       Cross-session accumulated knowledge           │
│  Lifetime:    Long-term (grows over weeks/months)           │
│  Sharing:     All sessions within the workspace             │
│  Content:     User preferences, project conventions,        │
│               architectural decisions, learned patterns,    │
│               recurring error mitigations                   │
│  Analogy:     Long-term episodic + semantic memory          │
│  Write Policy: Background agent (dream task) appends after  │
│               session conclusion; requires relevance gate   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: session.md                                        │
│  ──────────────────                                         │
│  Scope:       Current session working memory                │
│  Lifetime:    Single session (ephemeral)                    │
│  Sharing:     Current conversation only                     │
│  Content:     Task progress, errors encountered,            │
│               intermediate decisions, scratch notes,        │
│               correction records                            │
│  Analogy:     Working memory / scratchpad                   │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Conversation History: JSONL Persistence

Complete conversation history is stored in append-only JSONL (JSON Lines) files, one entry per message:

```
storage/
└── conversations/
    ├── {conversation_id}/
    │   ├── history.jsonl          # Full message sequence
    │   ├── session.md             # Session working memory
    │   └── metadata.json          # Conversation metadata
    └── ...
```

Each line in `history.jsonl` represents a single message event:

```jsonl
{"id":"msg_001","role":"user","content":"...","timestamp":"2025-01-15T10:30:00Z","node_id":"n1","parent_id":null}
{"id":"msg_002","role":"assistant","content":"...","timestamp":"2025-01-15T10:30:05Z","node_id":"n2","parent_id":"n1","tool_calls":[...]}
{"id":"msg_003","role":"tool","content":"...","timestamp":"2025-01-15T10:30:06Z","node_id":"n3","parent_id":"n2","tool_call_id":"tc_001"}
```

**Design Rationale:**

- **Append-only semantics** ensure crash consistency — partial writes never corrupt existing history.
- **JSONL over databases** eliminates connection pooling, schema migrations, and operational overhead while maintaining sub-millisecond read performance for typical conversation lengths (< 10,000 messages).
- **`node_id` / `parent_id` fields** enable optional conversation branching (editing a previous message forks a new branch) without requiring relational joins.
- **Git-friendly** — JSONL files diff cleanly, enabling version control of conversation evolution.

### 2.3 Memory Write Policy: The Dream Mechanism

The `memory.md` file is not written synchronously during conversation. Instead, a dedicated background task (`dream` type, see §9.1) executes after session conclusion:

```
Session ends → Dream task spawns → LLM evaluates session content
    → Relevance gate: "Does this session contain information
       that would benefit future sessions?"
    → If YES: Extract and append structured entries to memory.md
    → If NO: No modification
```

This design prevents memory pollution from trivial interactions and ensures that only genuinely useful knowledge persists across sessions.

### 2.4 Memory Injection at Conversation Start

When a new conversation initiates, the context is assembled in strict order:

```
[System Prompt] = agent.md (full content)
                + memory.md (full content, or summarized if exceeding threshold)
                + session.md (current session state)
                + [conversation history from JSONL]
```

---

## 3. Context Window Management

### 3.1 Five-Level Progressive Compression

As conversation history grows and approaches the model's context window limit, the system applies a graduated compression strategy. Each level represents an increasingly aggressive trade-off between information preservation and token economy:

| Level | Trigger Condition | Strategy | Information Loss |
|-------|------------------|----------|-----------------|
| **L0** | < 50% capacity | No compression | None |
| **L1** | 50–70% capacity | Remove verbose tool outputs; retain tool call signatures and result summaries | Low — execution details lost, outcomes preserved |
| **L2** | 70–85% capacity | LLM-generated summary of the first half of conversation; recent messages retained verbatim | Medium — early context becomes abstract |
| **L3** | 85–95% capacity | Retain only the most recent 1/3 of messages; everything prior compressed to a single summary block | High — most history becomes a narrative summary |
| **L4+** | > 95% capacity | Aggressive emergency compression; retain only system prompt + memory layers + last N messages | Very High — emergency mode |

### 3.2 Compression Invariants

Regardless of compression level, the following elements are **never compressed**:

1. **agent.md** content (system identity)
2. **memory.md** content (long-term knowledge)
3. **session.md** error records (prevents repeating mistakes)
4. **Active tool call results** from the current reasoning chain
5. **User's most recent message** and the assistant's most recent response

### 3.3 Compression Execution

Compression is performed by the LLM itself (self-summarization), with a dedicated summarization prompt:

```
You are summarizing a conversation to fit within context limits.
Preserve: key decisions, file paths modified, errors encountered,
          user preferences expressed, architectural choices made.
Discard:  verbose tool outputs, exploratory dead ends,
          redundant confirmations.
```

The summarized output replaces the original messages in the active context while the full history remains intact in the JSONL file (lossless archival, lossy working memory).

---

## 4. Skill System: Progressive Loading

### 4.1 Design Motivation

A naive approach loads all skill definitions into the system prompt, consuming thousands of tokens per skill. With 20+ skills, this approach wastes 30,000–50,000 tokens on capability descriptions that may never be invoked. Progressive loading resolves this through a two-phase retrieval protocol.

### 4.2 Two-Phase Loading Protocol

```
┌──────────────────────────────────────────────────────────────┐
│  Phase 1: Manifest Injection (at conversation start)         │
│  ────────────────────────────────────────────────────────────│
│  Load only the skill manifest into system prompt:            │
│                                                              │
│  Available Skills:                                           │
│  - "code-review": Systematic code review across 18 categories│
│  - "pdf-generator": Create and manipulate PDF documents      │
│  - "data-analysis": Statistical analysis and visualization   │
│  - ...                                                       │
│                                                              │
│  Token cost: ~50 tokens per skill × N skills                 │
└──────────────────────────────────────────────────────────────┘
                              │
                    Agent determines need
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  Phase 2: On-Demand Full Loading                             │
│  ────────────────────────────────────────────────────────────│
│  Agent returns: "I need skill: code-review"                  │
│                                                              │
│  System reads: ~/.skills/code-review/SKILL.md                │
│  → Full skill content injected into current context          │
│  → Skill remains loaded for remainder of conversation        │
│  → NOT re-loaded on subsequent turns (single-load guarantee) │
│                                                              │
│  Token cost: Full skill content only when actually needed    │
└──────────────────────────────────────────────────────────────┘
```

### 4.3 Skill File System Convention

```
~/.skills/
├── {skill-name}/
│   ├── SKILL.md              # Skill definition (instructions + metadata)
│   ├── templates/            # Optional: file templates
│   ├── examples/             # Optional: few-shot examples
│   └── tools/                # Optional: auxiliary scripts
└── ...
```

### 4.4 Single-Load Guarantee

Once a skill is loaded in a conversation, it persists in the context for all subsequent turns within that conversation. The system tracks loaded skills to prevent redundant reads:

```python
loaded_skills: Set[str] = set()

def load_skill(name: str) -> str:
    if name in loaded_skills:
        return "Skill already loaded."
    content = read_file(f"~/.skills/{name}/SKILL.md")
    loaded_skills.add(name)
    return content  # Injected into context
```

---

## 5. Tool Registry & Permission Management

### 5.1 Unified Registration Model

All tools — whether built-in, user-defined, or provided via MCP (Model Context Protocol) — are registered through a single, authoritative registry. This eliminates the distinction between "native" and "external" capabilities at the invocation layer.

```
┌─────────────────────────────────────────────────────────────┐
│                    Tool Registry (Singleton)                  │
│  ─────────────────────────────────────────────────────────── │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐    │
│  │  Built-in   │  │  MCP Tools  │  │  User-Defined    │    │
│  │  Tools      │  │  (dynamic)  │  │  Tools           │    │
│  ├─────────────┤  ├─────────────┤  ├──────────────────┤    │
│  │ grep_search │  │ web_fetch   │  │ custom_deploy    │    │
│  │ file_read   │  │ db_query    │  │ run_pipeline     │    │
│  │ file_write  │  │ calendar    │  │ ...              │    │
│  │ shell_exec  │  │ ...         │  │                  │    │
│  │ python_exec │  │             │  │                  │    │
│  └─────────────┘  └─────────────┘  └──────────────────┘    │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │            Permission Management Layer                 │  │
│  │  ─────────────────────────────────────────────────── │  │
│  │  Role-Based Access Control (RBAC):                    │  │
│  │    • main_agent:  full access                         │  │
│  │    • sub_agent:   restricted (no destructive ops)     │  │
│  │    • coordinator: orchestration tools only            │  │
│  │    • worker:      task-specific subset                │  │
│  │                                                       │  │
│  │  Category-Based Gating:                               │  │
│  │    • filesystem: read / write / delete                │  │
│  │    • execution:  shell / python / sandbox             │  │
│  │    • network:    search / fetch / api_call            │  │
│  │    • communication: user_input / notifications        │  │
│  │    • orchestration: spawn_agent / stop_agent          │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 MCP Integration Protocol

MCP servers are registered declaratively and their tools are discovered at runtime:

```json
{
  "mcp_servers": [
    {
      "name": "web-tools",
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@anthropic/web-tools"],
      "lifecycle": "session"
    }
  ]
}
```

Upon connection, the system performs tool discovery (`tools/list`), converts MCP tool schemas to the internal registry format, and applies permission policies uniformly.

### 5.3 Permission Resolution

```
Tool invocation request
    │
    ├── Check: Is tool registered? → NO → Reject
    ├── Check: Does caller role have category access? → NO → Reject
    ├── Check: Is tool in caller's explicit allowlist? → NO → Reject
    └── Execute tool with sandboxed context
```

---

## 6. Event Bus Architecture

### 6.1 Design Motivation

Direct push architectures (e.g., WebSocket direct-write) tightly couple the agent runtime to specific delivery mechanisms. An event bus decouples event production from consumption, enabling transparent addition of consumers (logging, monitoring, multi-client delivery, replay) without modifying the core agent loop.

### 6.2 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Agent Runtime                              │
│                                                                   │
│  Agent Loop → emit(event) ─────────────────┐                    │
│                                             │                    │
└─────────────────────────────────────────────┼────────────────────┘
                                              │
                                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Event Bus (In-Process)                        │
│  ─────────────────────────────────────────────────────────────── │
│                                                                   │
│  Event Types:                                                     │
│  • text_delta          — Incremental text generation             │
│  • tool_call_start     — Tool invocation initiated               │
│  • tool_call_chunk     — Streaming tool output                   │
│  • tool_result         — Tool execution completed                │
│  • agent_spawn         — Sub-agent created                       │
│  • agent_complete      — Sub-agent finished                      │
│  • error              — Error occurrence                          │
│  • status_change      — Agent state transition                   │
│  • memory_update      — Memory layer modification                │
│  • user_input_request — Agent requests user interaction          │
│  • compression_event  — Context compression triggered            │
│                                                                   │
│  Delivery Guarantee: At-least-once (in-process)                  │
│  Ordering: Per-agent FIFO                                        │
│                                                                   │
└──────────────┬──────────────┬──────────────┬─────────────────────┘
               │              │              │
               ▼              ▼              ▼
┌──────────────────┐ ┌───────────────┐ ┌──────────────────┐
│  WebSocket       │ │  File Logger  │ │  Metrics         │
│  Consumer        │ │  Consumer     │ │  Consumer        │
│  (→ Frontend)    │ │  (→ .jsonl)   │ │  (→ Prometheus)  │
└──────────────────┘ └───────────────┘ └──────────────────┘
```

### 6.3 Consumer Registration

Consumers subscribe to event types via a declarative registration API:

```python
bus.subscribe("text_delta", websocket_consumer)
bus.subscribe("*", file_logger_consumer)          # Wildcard: all events
bus.subscribe("error", alerting_consumer)
```

### 6.4 Backpressure Handling

If a consumer falls behind (e.g., slow WebSocket client), the bus applies per-consumer bounded buffering. When the buffer is full, the oldest undelivered events are dropped with a `dropped_events` counter incremented — the agent loop is never blocked by slow consumers.

---

## 7. Sandbox Execution Environment

### 7.1 Per-Conversation Isolation

Every conversation spawns a dedicated sandbox instance. This provides:

- **Security**: Untrusted code cannot affect the host system
- **Reproducibility**: Each conversation starts from a clean state
- **Conflict prevention**: Concurrent conversations cannot interfere with each other's file systems

### 7.2 Sandbox Lifecycle

```
Conversation start → Provision sandbox
    │
    ├── Mount workspace directory (read-write)
    ├── Mount skill directories (read-only)
    ├── Configure network policies
    └── Initialize language runtimes
    
Conversation active → Execute tools within sandbox
    │
    ├── shell_exec → sandbox shell
    ├── python_exec → sandbox Python interpreter
    ├── file_write → sandbox filesystem (workspace mount)
    └── file_read → sandbox filesystem

Conversation end → Teardown sandbox
    │
    ├── Persist workspace changes back to host
    ├── Collect execution logs
    └── Destroy sandbox instance
```

### 7.3 Technology Selection

The system leverages lightweight container-based sandboxing (e.g., Tencent's open-source sandbox solution) that provides:

- Sub-second provisioning latency (pre-warmed pool)
- Full Linux syscall filtering (seccomp)
- Network namespace isolation with configurable egress policies
- Resource limits (CPU, memory, disk I/O, execution time)

### 7.4 File System Mapping

```
Host                              Sandbox
────                              ───────
~/workspace/{project}/    →→→     /workspace/        (rw)
~/.skills/                →→→     /skills/           (ro)
/tmp/sandbox-{id}/        →→→     /tmp/              (rw, ephemeral)
```

---

## 8. Knowledge Base: Hybrid Retrieval-Augmented Generation

### 8.1 Architecture Overview

The RAG subsystem implements a multi-stage retrieval pipeline that significantly outperforms naive single-vector-search approaches. The architecture draws upon Anthropic's Contextual Retrieval research and established information retrieval literature.

```
┌─────────────────────────────────────────────────────────────────┐
│                     Query Processing Pipeline                     │
│                                                                   │
│  User Query                                                       │
│      │                                                            │
│      ▼                                                            │
│  ┌──────────────────────────────────────┐                        │
│  │  Stage 1: Query Enhancement          │                        │
│  │  • Query Rewriting (LLM-based)       │                        │
│  │  • Multi-Query Expansion             │                        │
│  │  • Contextual Disambiguation         │                        │
│  └──────────────────┬───────────────────┘                        │
│                     │                                             │
│                     ▼                                             │
│  ┌──────────────────────────────────────┐                        │
│  │  Stage 2: Multi-Route Retrieval      │                        │
│  │  ┌────────────┐  ┌────────────────┐  │                        │
│  │  │ Dense Path │  │  Sparse Path   │  │                        │
│  │  │ (Embedding)│  │  (BM25)        │  │                        │
│  │  │  Top-K     │  │   Top-K        │  │                        │
│  │  └──────┬─────┘  └───────┬────────┘  │                        │
│  │         │                 │           │                        │
│  │         └────────┬────────┘           │                        │
│  │                  ▼                    │                        │
│  │         ┌────────────────┐            │                        │
│  │         │  RRF Fusion    │            │                        │
│  │         └────────┬───────┘            │                        │
│  └──────────────────┼───────────────────┘                        │
│                     │                                             │
│                     ▼                                             │
│  ┌──────────────────────────────────────┐                        │
│  │  Stage 3: Reranking                  │                        │
│  │  Cross-Encoder scoring               │                        │
│  │  Top-100 → Top-5~10                  │                        │
│  └──────────────────┬───────────────────┘                        │
│                     │                                             │
│                     ▼                                             │
│  ┌──────────────────────────────────────┐                        │
│  │  Stage 4: Context Assembly           │                        │
│  │  • Relevance threshold filtering     │                        │
│  │  • Deduplication                     │                        │
│  │  • Source attribution                │                        │
│  └──────────────────────────────────────┘                        │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### 8.2 Document Ingestion Pipeline

#### 8.2.1 Intelligent Chunking

Documents are split using type-aware strategies:

| Document Type | Chunking Strategy | Rationale |
|---------------|-------------------|-----------|
| Markdown | Header-based hierarchical split | Preserves document structure |
| Source Code | AST-aware / language-specific split | Respects function/class boundaries |
| PDF / HTML | Section-based split | Maintains semantic coherence |
| Plain Text | Recursive character split with overlap | Fallback for unstructured content |
| JSON / JSONL | Recursive JSON split | Preserves object integrity |

#### 8.2.2 Contextual Enrichment (Anthropic Method)

Following Anthropic's Contextual Retrieval methodology, each chunk is enriched with a contextual preamble generated by an LLM:

```
Original Chunk:
"The function accepts two parameters: a connection pool
 and a timeout value in milliseconds..."

Contextual Preamble (LLM-generated):
"This chunk is from Chapter 4 of the Database Driver documentation,
 specifically the section on connection configuration. The preceding
 section discussed pool initialization, and this section details
 the execute() function's parameter interface."

Final Indexed Content:
[Contextual Preamble] + [Original Chunk]
```

This enrichment is performed once during ingestion and dramatically improves both embedding quality and BM25 keyword matching by providing surrounding context that would otherwise be lost during chunking.

**Empirical Impact** (per Anthropic's published results):
- Contextual Embeddings + Contextual BM25: **49% reduction** in retrieval failure rate vs. vanilla vector search
- Adding Reranking: **67% reduction** in retrieval failure rate

### 8.3 Hybrid Search: Dense + Sparse Fusion

#### 8.3.1 Dense Retrieval Path

```
Query → Embedding Model → Query Vector
     → Vector Database (cosine / L2 similarity)
     → Top-K candidates with scores
```

#### 8.3.2 Sparse Retrieval Path (BM25)

```
Query → Tokenization → Term Frequency Analysis
     → Inverted Index Lookup
     → BM25 Scoring: score(D,Q) = Σ IDF(qi) · [f(qi,D) · (k1+1)]
                                              / [f(qi,D) + k1·(1-b+b·|D|/avgdl)]
     → Top-K candidates with scores
```

#### 8.3.3 Reciprocal Rank Fusion (RRF)

The two retrieval paths are merged using RRF, which is agnostic to score scales:

```
RRF_score(d) = Σ(i=1 to N) 1 / (k + rank_i(d))

where:
  k = 60 (constant, empirically robust)
  rank_i(d) = rank of document d in the i-th retrieval path
  N = number of retrieval paths (2 for dense + sparse)
```

**Advantages of RRF over weighted score fusion:**
- No need to normalize scores across different retrieval systems
- Robust to outlier scores
- Only depends on relative ordering, not absolute score magnitudes

### 8.4 Query Enhancement

#### 8.4.1 Query Rewriting

User queries are often colloquial, ambiguous, or incomplete. The LLM rewrites them into retrieval-optimized forms:

```
Input:  "这东西怎么用"
Output: "产品使用说明 操作指南 功能文档"

Input:  "上次那个 bug 怎么修的"  
Output: "错误修复方案 bug 修复记录 异常处理"
```

#### 8.4.2 Multi-Query Expansion

A single user question is expanded into multiple complementary queries, each targeting a different facet of the information need:

```
Original: "How does the RAG system improve answer accuracy?"

Expanded queries:
  Q1: "Retrieval precision improvement methods in RAG systems"
  Q2: "Reducing hallucination in retrieval-augmented generation"
  Q3: "Impact of reranking on RAG response quality"
  Q4: "How chunking strategies affect RAG retrieval effectiveness"
```

Each expanded query is executed independently against the retrieval system, and results are merged with deduplication.

### 8.5 Reranking

#### 8.5.1 Cross-Encoder Reranking

After initial retrieval returns a broad candidate set (Top-100), a Cross-Encoder model scores each (query, document) pair jointly:

```
Initial retrieval: Top-100 candidates (fast, approximate)
    │
    ▼
Cross-Encoder: score(query, candidate) for each candidate
    │
    ▼
Final ranking: Top-5 to Top-10 (precise, expensive)
```

#### 8.5.2 Engineering Considerations

- **Cascade architecture**: Lightweight model narrows from 100→30, heavy model from 30→10
- **Score thresholding**: Candidates below a minimum reranker score are discarded entirely (prevents low-relevance noise)
- **Latency budget**: Reranking adds 100–300ms; acceptable given the quality improvement

### 8.6 Agent-Accessible Retrieval Tool

Beyond automatic retrieval at conversation start, the agent is provided with an explicit `search_knowledge_base` tool, enabling self-directed retrieval during complex reasoning:

```python
@tool
def search_knowledge_base(query: str, top_k: int = 5) -> List[Document]:
    """Search the user's knowledge base using hybrid retrieval.
    Use this when you need specific information that may be in
    the user's documents but is not in the current context."""
    ...
```

---

## 9. Multi-Agent Orchestration

### 9.1 Seven Task Types

The system defines a typed taxonomy of work units, each with distinct execution semantics and resource requirements:

| TaskType | ID Prefix | Execution Environment | Use Case | Isolation Level |
|----------|-----------|----------------------|----------|-----------------|
| `local_bash` | `b-` | Background shell process | Long-running builds, test suites | Process |
| `local_agent` | `a-` | Sync/async sub-agent | Research, code generation | Logical |
| `remote_agent` | `r-` | Remote session (CCR) | Heavy tasks requiring full isolation | Machine |
| `in_process_teammate` | `t-` | In-process (shared runtime) | Team collaboration mode | AsyncLocalStorage |
| `local_workflow` | `w-` | Workflow engine | Multi-step orchestrated procedures | Logical |
| `monitor_mcp` | `m-` | Passive monitoring | External service health checks | Process |
| `dream` | `d-` | Background, low-priority | Memory consolidation (§2.3) | Logical |

### 9.2 Coordinator Mode: Pure Orchestration

#### 9.2.1 Design Principle

The Coordinator agent operates as a **pure orchestrator** — it never directly manipulates files, executes code, or performs search. Its sole responsibility is decomposing complex tasks into structured work phases and delegating execution to specialized workers.

#### 9.2.2 Coordinator Tool Set (Exactly 4 Tools)

```
┌──────────────────────────────────────────────────────────────────┐
│  Coordinator Available Tools                                      │
│                                                                    │
│  1. AgentTool       → Spawn a new worker agent                    │
│  2. TaskStopTool    → Terminate an existing worker                │
│  3. SendMessageTool → Send instruction to an existing worker      │
│  4. SyntheticOutput → Compose final output from worker results    │
│                                                                    │
│  NOT available: file_read, file_write, shell_exec, python_exec,  │
│                 search, fetch — these belong to workers only       │
└──────────────────────────────────────────────────────────────────┘
```

**Rationale**: By restricting the Coordinator's tool set, its system prompt remains concise and focused on planning. This prevents the failure mode where an orchestrator "gets distracted" by directly executing subtasks instead of delegating.

#### 9.2.3 Four-Phase Structured Workflow

The Coordinator follows a disciplined four-phase execution framework:

```
┌────────────────────┐         ┌────────────────────┐
│  Phase 1:          │         │  Phase 2:          │
│  RESEARCH          │────────→│  SYNTHESIS         │
│                    │         │                    │
│  • Spawn parallel  │         │  • Collect results │
│    research agents │         │  • Identify gaps   │
│  • Gather context  │         │  • Form plan       │
│  • Explore options │         │  • Define specs    │
└────────────────────┘         └─────────┬──────────┘
                                         │
┌────────────────────┐         ┌─────────▼──────────┐
│  Phase 4:          │         │  Phase 3:          │
│  VERIFICATION      │←────────│  IMPLEMENTATION    │
│                    │         │                    │
│  • Independent     │         │  • Execute plan    │
│    verification    │         │  • Write code      │
│    agent           │         │  • Build artifacts │
│  • Fresh eyes      │         │  • Run tests       │
│  • Catch errors    │         │                    │
└────────────────────┘         └────────────────────┘
```

**Critical Design Choice — Phase 4 (Verification)**: A dedicated verification agent reviews the implementation with "fresh eyes" (no shared context with the implementation agent). This catches errors that self-review typically misses due to confirmation bias.

### 9.3 Fork Sub-Agent: Lightweight Context Inheritance

#### 9.3.1 Mechanism

Fork is the most lightweight multi-agent primitive. A fork child inherits the parent's complete context via reference, not copy:

```
┌─────────────────────────────────────────────────────────────┐
│  Parent Agent                                                │
│  ├── System Prompt (rendered bytes)                          │
│  ├── Messages (full conversation history)                    │
│  └── Tools (complete tool set)                               │
│                                                              │
│               fork()                                         │
│                ↓                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Fork Child                                            │  │
│  │  ├── System Prompt: reference to parent's (byte-identical) │
│  │  ├── forkContextMessages: reference to parent's messages   │
│  │  ├── useExactTools: true (identical tool set)              │
│  │  └── Task-specific instruction (unique per child)          │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

#### 9.3.2 KV Cache Optimization

Because the fork child's prompt prefix is **byte-identical** to the parent's, LLM inference services can reuse the parent's computed KV cache. This eliminates the prefill cost for the shared prefix, reducing fork child startup latency from seconds to near-zero.

```
Parent KV Cache:  [system_prompt | messages | ...]
                   ↑───────────── reused ──────────↑
Fork Child:       [system_prompt | messages | task_instruction]
                   ↑── cached ──↑              ↑── new ──↑
```

#### 9.3.3 Use Cases

- **Parallel exploration**: Fork multiple children to explore different solution approaches simultaneously
- **Speculative execution**: Fork a child to attempt a risky operation while the parent continues safe work
- **Fan-out / fan-in**: Fork N children for N independent subtasks, collect results

### 9.4 In-Process Teammate: Persistent Collaborative Agents

#### 9.4.1 Lifecycle

Unlike sub-agents (which terminate after completing a single task), teammates are **persistent** — they remain alive across multiple interactions, maintaining their own conversation state:

```
spawnInProcessTeammate()
    │
    ├── Generate task ID (prefix 't-')
    ├── Create independent AbortController
    ├── Create TeammateContext (agentId, teamName, color, parentSessionId)
    ├── Register in task manager
    └── Start event loop (fire-and-forget)
            │
            └── while (!aborted && !shouldExit) {
                    1. Create per-turn AbortController
                    2. Check context → compress if needed
                    3. Execute agent turn (runAgent)
                    4. Collect messages, update state
                    5. Mark IDLE, send idle notification
                    6. waitForNextPromptOrShutdown()
                    7. Route: new_message → continue
                             shutdown → graceful exit
                             aborted → immediate exit
                }
```

#### 9.4.2 Message Priority

When an in-process teammate polls for incoming messages, strict priority ordering ensures critical signals are never starved:

| Priority | Source | Rationale |
|----------|--------|-----------|
| 1 (highest) | Shutdown request | Prevents zombie teammates |
| 2 | Team Lead message | Leader represents user intent |
| 3 | Peer message | FIFO among equals |
| 4 (lowest) | Unclaimed task list | Background work |

### 9.5 Execution Backends

The system supports three execution backends, selected dynamically based on environment detection:

| Backend | Environment | Isolation | Visibility | Communication |
|---------|-------------|-----------|------------|---------------|
| `tmux` | Terminal with tmux | Process-level | Separate panes | tmux socket |
| `iterm2` | iTerm2 terminal | Process-level | Split panes | it2 CLI |
| `in-process` | Any | AsyncLocalStorage | Shared output | In-memory channels |

**Selection Logic**: The system probes for available backends in order (tmux → iTerm2 → in-process) and uses the first available. In-process is the universal fallback.

---

## 10. Communication & Context Isolation

### 10.1 File-Based Mailbox System

Inter-agent communication uses a file-based mailbox protocol, providing persistence without external message brokers:

```
~/.agent/teams/{team_name}/inboxes/{agent_name}.json
```

#### 10.1.1 Message Schema

```typescript
type TeammateMessage = {
  from: string;        // Sender agent name
  text: string;        // Message content
  timestamp: string;   // ISO 8601 timestamp
  read: boolean;       // Consumption status
  color?: string;      // Sender's display color (UI hint)
  summary?: string;    // Brief preview for UI rendering
};
```

#### 10.1.2 Concurrency Safety

File-based communication requires explicit concurrency control:

```
Write operation:
  1. Acquire file lock (proper-lockfile, retries: 10, backoff: 5-100ms)
  2. Read current file content
  3. Append new message
  4. Write updated content atomically
  5. Release lock
```

This provides **serializable** access semantics without external coordination services.

#### 10.1.3 Design Trade-offs

| Property | File Mailbox | In-Memory Queue | Redis/RabbitMQ |
|----------|-------------|-----------------|----------------|
| Persistence | ✓ (survives crashes) | ✗ | ✓ |
| Operational overhead | None | None | High |
| Throughput | ~1000 msg/s | ~1M msg/s | ~100K msg/s |
| Cross-process | ✓ | ✗ | ✓ |
| Debuggability | Open file and read | Requires tooling | Requires client |

For agent collaboration scenarios (message frequency: 1–10 msg/second), file-based mailboxes provide optimal trade-offs between simplicity, persistence, and debuggability.

### 10.2 Dual-Layer Context Isolation (AsyncLocalStorage)

When multiple agents execute concurrently within the same process, a formal isolation mechanism prevents cross-contamination of agent state:

#### 10.2.1 Layer 1: TeammateContext

```typescript
// Runtime identity for each concurrent agent
interface TeammateContext {
  agentId: string;          // Unique agent identifier
  agentName: string;        // Human-readable name
  teamName: string;         // Team membership
  color: string;            // Display color
  abortController: AbortController;  // Lifecycle control
}
```

**Purpose**: Runtime identity and lifecycle management. Enables `isInProcessTeammate()` fast-path checks and per-agent abort signaling.

#### 10.2.2 Layer 2: AgentContext

```typescript
// Attribution context for observability
interface AgentContext {
  type: 'subagent' | 'teammate';
  agentId: string;
  invokingRequestId?: string;  // Trace linkage to parent
}
```

**Purpose**: Observability attribution. Every API call, tool invocation, and event emission is tagged with the originating agent's context, enabling precise distributed tracing.

#### 10.2.3 Resolution Priority

When determining the current agent's identity:

```
1. AsyncLocalStorage (TeammateContext)     ← In-process teammates
2. dynamicTeamContext                       ← Runtime-joined process agents
3. Environment variables (AGENT_ID)         ← Separate-process agents (tmux/iTerm2)
```

---

## 11. System Integration Overview

### 11.1 Complete Data Flow

```
┌───────────────────────────────────────────────────────────────────────┐
│                          User Input                                     │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────────────┐
│  Context Assembly                                                      │
│  ┌─────────┐ ┌───────────┐ ┌────────────┐ ┌──────────────────────┐  │
│  │agent.md │+│ memory.md │+│ session.md │+│ history.jsonl (comp.) │  │
│  └─────────┘ └───────────┘ └────────────┘ └──────────────────────┘  │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────────────┐
│  Skill Manifest Check → Load if needed                                │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────────────┐
│  LLM Inference (streaming)                                            │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Model processes context → generates response / tool calls      │  │
│  └────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
                    ┌───────────┼───────────┐
                    │           │           │
                    ▼           ▼           ▼
            ┌────────────┐ ┌────────┐ ┌──────────────┐
            │ Tool Calls │ │  Text  │ │ Agent Spawn  │
            │ (sandbox)  │ │ Output │ │ (Coordinator)│
            └─────┬──────┘ └───┬────┘ └──────┬───────┘
                  │            │              │
                  ▼            ▼              ▼
            ┌─────────────────────────────────────────┐
            │           Event Bus                      │
            └──────┬──────────┬──────────┬────────────┘
                   │          │          │
                   ▼          ▼          ▼
            ┌──────────┐ ┌────────┐ ┌──────────┐
            │ Frontend │ │  Logs  │ │ Metrics  │
            │ (stream) │ │(.jsonl)│ │(counters)│
            └──────────┘ └────────┘ └──────────┘
```

### 11.2 Technology Stack Summary

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Persistence | Markdown + JSONL files | Human-readable, Git-friendly, zero-ops |
| Agent Framework | Custom (no LangChain) | Full control, deep understanding of internals |
| LLM Integration | OpenAI-compatible API | Universal compatibility |
| Vector Database | Milvus / Qdrant | Production-grade vector search |
| Sparse Index | BM25 (rank-bm25 / Elasticsearch) | Keyword matching complement |
| Reranker | Cross-Encoder (e.g., bge-reranker) | Precision refinement |
| Sandbox | Container-based (Tencent OSS) | Sub-second provisioning, full isolation |
| Frontend | React + TypeScript | Type-safe, component-driven UI |
| Backend | Python + FastAPI | Async-native, minimal overhead |
| Communication | File mailbox + Event bus | Zero external dependencies |

### 11.3 Comparison with Conventional Approaches

| Dimension | Conventional (e.g., APIX) | This Architecture |
|-----------|--------------------------|-------------------|
| Memory storage | MySQL + Redis + stored procedures | Markdown + JSONL (file-system) |
| Operational burden | 3+ middleware services | Zero (files only) |
| Skill loading | Full injection into prompt | Progressive (name → full, on-demand) |
| RAG quality | Single-vector naive search | Hybrid retrieval + RRF + Reranking |
| Multi-agent | Role-based (4 types) | Typed tasks (7) + Coordinator pattern |
| Sub-agent cost | Full graph rebuild per spawn | Fork with KV cache reuse |
| Communication | In-memory queue (volatile) | File mailbox (persistent, debuggable) |
| Verification | None | Independent Phase 4 agent |
| Context isolation | generation_id tagging | Dual-layer AsyncLocalStorage |
| Event delivery | Direct WebSocket push | Event bus with pluggable consumers |

---

## References

1. Anthropic. "Introducing Contextual Retrieval." Anthropic Research Blog, 2024.
2. Robertson, S. & Zaragoza, H. "The Probabilistic Relevance Framework: BM25 and Beyond." Foundations and Trends in Information Retrieval, 2009.
3. Cormack, G. V., Clarke, C. L. A., & Büttcher, S. "Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods." SIGIR, 2009.
4. Nogueira, R. & Cho, K. "Passage Re-ranking with BERT." arXiv:1901.04085, 2019.
5. Lewis, P. et al. "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks." NeurIPS, 2020.
6. Anthropic. "Claude Code: Best Practices for Agentic Coding." Anthropic Documentation, 2025.

---

> *Document Version: 1.0*  
> *Last Updated: 2025-06*  
> *Classification: Technical Architecture Specification*
