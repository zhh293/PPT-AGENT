# PPT-Agent 完整改造方案

> **文档用途**：本文件是 PPT-Agent 系统从"30% 实现"到"完整可用"的全面改造方案。覆盖 Harness 层（11 个子系统）、Domain 层（PPT 编排与领域方法）、开发任务清单和风险分析。AI 可直接根据此文档生成可用的 PPT Agent 代码。
>
> **参考架构**：`docs/AGENT_ARCHITECTURE.md`（994 行，11 个子系统）
>
> **技术栈**：Python 3.11+, python-pptx, ThreadPoolExecutor, JSONL, fcntl

---

## 目录

- Part A: Harness 层 — 11 个子系统差距分析与实施方案
- Part B: Domain 层 — PPT 编排与领域方法
- Part C: 开发任务清单
- Part D: 风险与缓解

---

## Part A: Harness 层 — 11 个子系统差距分析与实施方案

### A.1 Memory System (§2) — 实现度: 40%

**已实现**：

| 组件 | 文件 | 状态 |
|------|------|------|
| 三层记忆结构 | `context/memory_layers.py` | ✅ agent.md / memory.md / session.md |
| build_memory_context() | `context/memory_layers.py` | ✅ 组装到 system prompt |
| append_memory() | `context/memory_layers.py` | ✅ 追加写入 |
| Dream 机制 | `context/dream.py` | ✅ LLM 提取 insight + 启发式回退 |
| session_summary | `context/session_summary.py` | ✅ append_phase_summary() |

**未实现**：

| 组件 | 规范位置 | 差距 |
|------|----------|------|
| memory.md 跨会话共享 | §2.3 | 当前 per-job，需改为 workspace 级 |
| ConversationStore (JSONL 持久化) | §2.4 | 完全缺失 |
| Dream 后台任务 | §2.5 | 当前只在 verification 完成后触发，不是独立 background task |
| 记忆遗忘机制 | §2.6 | 完全缺失 |

**实施方案**：

1. **ConversationStore**（新建 `src/ppt_agent/runtime/conversation_store.py`）：

```python
class ConversationStore:
    """JSONL-based conversation persistence (§2.4).
    每条消息独立一行追加到 history.jsonl。
    崩溃恢复：agent 重启后从文件恢复对话历史。
    无损归档：压缩只修改内存 messages，完整历史始终保留在文件中。
    """
    def __init__(self, conversation_dir: Path) -> None:
        self.conversation_dir = Path(conversation_dir)
        self.conversation_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = self.conversation_dir / "history.jsonl"

    def append(self, message: LLMMessage, *, node_id: str,
               parent_id: str | None = None, tool_calls: list[dict] | None = None,
               agent_id: str = "", metadata: dict | None = None) -> ConversationEntry: ...

    def load_messages(self, limit: int | None = None) -> list[LLMMessage]: ...

    def load_entries(self, limit: int | None = None) -> list[ConversationEntry]: ...

    def get_stats(self) -> dict: ...
```

关键细节：`append()` 使用 `open("a")` 追加模式，线程安全（OS 对小写保证原子性）。`load_messages()` 将 ConversationEntry 转回 LLMMessage（只保留 role + content）。

2. **memory.md 跨会话**（修改 `context/memory_layers.py`）：

新增 `get_workspace_memory_root(job_root: Path) -> Path`，优先使用环境变量 `PPT_AGENT_MEMORY_ROOT`，否则使用 `job_root.parent / MEMORY_DIR`。memory.md 读写改为 workspace 级路径，agent.md 和 session.md 仍 per-job。

3. **AgentLoop 集成 ConversationStore**（见 C.1.2）：

在 `run()` 开始时从 ConversationStore 加载历史消息，每个 turn 结束后追加新消息。

---

### A.2 Context Compression (§3) — 实现度: 30%

**已实现**：

| 组件 | 文件 | 状态 |
|------|------|------|
| compress_context() | `context/compression.py` | ✅ 5 级 L0-L4 |
| INVARIANT_KEYS | `context/compression.py` | ✅ 不可压缩字段列表 |
| _llm_summarize() | `context/compression.py` | ✅ LLM 自摘要 |
| _determine_compression_level() | `llm/client.py` | ✅ token 估算 + 阈值 |

**未实现**：

| 组件 | 差距 |
|------|------|
| 消息级压缩 | 当前只压缩 context dict，不压缩 self.messages |
| L0-L4 在 AgentLoop 中触发 | AgentLoop 无 _compress_messages() |
| 压缩事件发射 | 无 compression_event 事件 |

**实施方案**：

在 AgentLoop 中新增 `_compress_messages()` 方法（详见 C.1.2 和 C.2.2）：

```python
def _compress_messages(self) -> None:
    """Compress conversation history when approaching context window limit (§3)."""
    total_tokens = self._estimate_tokens(self.messages)
    utilization = total_tokens / self.context_window_limit

    if utilization < 0.6:
        return  # L0: 不压缩

    # L1: 截断大型工具输出 (> 2000 chars → 500 chars + [truncated])
    # L2: LLM 摘要前半段对话，保留后半段原文
    # L3: 只保留 system + 错误 + 最近 4 条
    # L4: 紧急模式，只保留 system + 最近 2 条

    # 压缩不变量（NEVER 移除）：
    # - self.messages[0]（system prompt）
    # - 最近 2 条消息
    # - 包含 "[Error" 或 "[Tool Error" 的消息
```

调用位置：`_call_llm()` 的最开始，在构建 augmented_messages 之前。

---

### A.3 Skill System (§4) — 实现度: 50%

**已实现**：

| 组件 | 文件 | 状态 |
|------|------|------|
| SkillLoader | `skills/loader.py` | ✅ 两阶段渐进加载 |
| get_catalog_summary() | `skills/loader.py` | ✅ Phase 1 manifest |
| load_skill() | `skills/loader.py` | ✅ Phase 2 完整加载 + 单次保证 |
| SKILL.md 解析 | `skills/loader.py` | ✅ YAML frontmatter |

**未实现**：

| 组件 | 差距 |
|------|------|
| load_skill 作为 agent 工具 | SkillLoader 从未被 agent 调用 |
| Manifest 注入所有 agent | 只有 visual_generation worker 拿到 skill 内容 |
| is_loaded() 查询 | 缺少，agent 无法判断是否已加载 |

**实施方案**：

1. 在 SkillLoader 中新增 `is_loaded(skill_name: str) -> bool` 方法。

2. 在 AgentLoop 中新增 `_load_skill_tool(skill_name: str) -> ToolResult` 内置工具（详见 C.2.3）：

```python
def _load_skill_tool(self, skill_name: str) -> ToolResult:
    if self.skill_loader.is_loaded(skill_name):
        return ToolResult(..., output={"status": "already_loaded"})
    content = self.skill_loader.load_skill(skill_name)
    if content is None:
        return ToolResult(..., success=False, error=f"Skill '{skill_name}' not found")
    # 追加 skill 内容到 system prompt
    self.messages[0].content[0].text += f"\n\n## Loaded Skill: {skill_name}\n\n{content}"
    self._loaded_skills.add(skill_name)
    self._emit(EventType.SKILL_LOADED, message=f"Loaded skill: {skill_name}")
    return ToolResult(..., output={"status": "loaded", "content_length": len(content)})
```

3. 所有 agent 的 system prompt 中注入 skill manifest（`skill_loader.get_catalog_summary()` 结果）。

---

### A.4 Tool Registry & RBAC (§5) — 实现度: 20%

**已实现**：

| 组件 | 文件 | 状态 |
|------|------|------|
| ToolDescriptor | `tools/registry.py` | ✅ name, category, roles |
| ToolRegistry | `tools/registry.py` | ✅ register, get, list |
| 4 种角色 | `tools/registry.py` | ✅ main_agent, coordinator, worker, sub_agent |
| 12 个默认描述符 | `tools/registry.py` | ✅ 但只有描述符，无执行器 |

**未实现**：

| 组件 | 差距 |
|------|------|
| ToolEntry (描述符 + 执行器绑定) | 完全缺失 |
| 三层权限检查 | 当前只有单层 allows_role() |
| create_default_registry() 调用 | 从未在 workflow 中调用，RBAC 是死代码 |
| 工具执行 | 无 execute() 方法，工具不可执行 |
| Category 级权限映射 | 缺少 CATEGORY_PERMISSIONS |

**实施方案**：

重写 `tools/registry.py`（详见 C.1.3），核心改造：

```python
@dataclass
class ToolEntry:
    descriptor: ToolDescriptor
    executor: ToolExecutor  # (arguments: dict) -> ToolResult
    category: str

class ToolRegistry:
    def register_tool(self, descriptor: ToolDescriptor, executor: ToolExecutor) -> None: ...
    def check_permission(self, tool_name: str, role: str) -> None:
        # Layer 1: 工具是否已注册
        # Layer 2: 角色是否有该 category 的权限 (CATEGORY_PERMISSIONS)
        # Layer 3: 工具 descriptor.roles 是否包含该角色
    def execute(self, tool_call: ToolCall, role: str) -> ToolResult:
        self.check_permission(tool_call.tool_name, role)
        return self.get(tool_call.tool_name).executor(tool_call.arguments)
    def get_tools_for_role(self, role: str) -> list[ToolDescriptor]: ...
    def get_tools_for_role_as_dicts(self, role: str) -> list[dict]: ...
```

CATEGORY_PERMISSIONS 映射：
- filesystem, execution, retrieval, assembly, verification, extraction, generation, skill → WORKER_AND_ABOVE
- artifact → FULL_ACCESS
- orchestration → COORDINATOR_ONLY
- communication → FULL_ACCESS

---

### A.5 Event Bus (§6) — 实现度: 85%

**已实现**：

| 组件 | 文件 | 状态 |
|------|------|------|
| EventBus | `coordinator/event_bus.py` | ✅ pub/sub |
| 16 种事件类型 | `coordinator/event_bus.py` | ✅ EventType 枚举 |
| 3 种 Consumer | `coordinator/event_bus.py` | ✅ File, Logging, Callback |
| per-consumer 背压 | `coordinator/event_bus.py` | ✅ buffer 满时丢弃 |
| FileConsumer (JSONL) | `coordinator/event_bus.py` | ✅ history.jsonl |

**未实现**：

| 组件 | 差距 |
|------|------|
| 事件发射点补全 | 16 种中只有 ~6 种实际发射 |
| SSEConsumer | 缺失（前端实时推送需要） |
| text_delta 流式事件 | 缺失 |

**实施方案**：

1. 在 AgentLoop 中补全事件发射点（详见 C.2.4）：
   - `compression_event`：在 `_compress_messages()` 触发后
   - `skill_loaded`：在 `_load_skill_tool()` 成功后
   - `agent_spawn`/`agent_complete`：在 CoordinatorAgent 的 `_spawn_agent()` 中
   - `memory_update`：在 `append_memory()` 后

2. 新增 SSEConsumer：

```python
class SSEConsumer(EventConsumer):
    """Consumer that pushes events to an SSE queue for frontend streaming."""
    def __init__(self, max_queue_size: int = 500) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=max_queue_size)
    def handle(self, event: dict) -> None:
        self._queue.put_nowait(event)  # 满时丢弃 (backpressure)
    async def event_generator(self):
        while True:
            event = await self._queue.get()
            yield event
```

---

### A.6 Sandbox (§7) — 实现度: 15%

**已实现**：

| 组件 | 文件 | 状态 |
|------|------|------|
| SafeFilesystem | `tools/filesystem.py` | ✅ resolve() 防路径逃逸 |
| atomic_write_text() | `tools/filesystem.py` | ✅ 安全写入 |

**未实现**：

| 组件 | 差距 |
|------|------|
| SafeFilesystem 使用 | 从未在 AgentLoop 中使用 |
| Shell 命令限制 | worker_agent 的 _run_shell_command 无安全检查 |
| 环境变量白名单 | 完全缺失 |
| 容器隔离 | 规范描述的容器级隔离完全未实现 |

**实施方案**（最小实现，详见 C.2.5）：

1. 所有文件操作工具强制使用 `SafeFilesystem`：`Path(path)` → `self._safe_fs.resolve(path)`

2. `_run_shell_command()` 增加安全限制：
   - 命令黑名单：`rm -rf /`, `mkfs`, `> /dev/`, fork bomb 等
   - 强制 cwd 在 workspace 内
   - 环境变量白名单：只保留 PATH, HOME, LANG, PYTHONUNBUFFERED, WORKSPACE_ROOT

---

### A.7 Hybrid RAG (§8) — 实现度: 40%

**已实现**：

| 组件 | 文件 | 状态 |
|------|------|------|
| query_router.query() | `retrieval/query_router.py` | ✅ 4-stage pipeline |
| BM25 检索 | `retrieval/sparse/bm25.py` | ✅ |
| Vector 检索 | `retrieval/dense/` | ✅ TF-IDF 后端 |
| RRF 融合 | `retrieval/fusion/rrf.py` | ✅ |
| Reranker | `retrieval/rerankers/` | ✅ 启发式 |
| Query Enhancement | `retrieval/query_enhancement.py` | ✅ LLM 重写 + multi-query |

**未实现**：

| 组件 | 差距 |
|------|------|
| 知识库摄入 pipeline | 完全缺失，无 ingestion_pipeline.py |
| search_knowledge_base 工具 | RAG 从未作为 agent 工具暴露 |
| 完整 4-stage 实际运行 | template_matcher 只用 ["bm25"] 单路 |
| 向量索引持久化 | 当前每次查询重新构建 |

**实施方案**：

1. 新建 `retrieval/ingestion_pipeline.py`（详见 C.3.1）：

```python
class IngestionPipeline:
    """Knowledge base ingestion: files → chunks → enriched chunks → indexes."""
    def __init__(self, kb_name: str, output_dir: Path, *,
                 llm_client=None, chunk_size: int = 512, chunk_overlap: int = 64) -> None: ...
    def ingest(self, source_paths: list[Path]) -> KnowledgeBaseIndex: ...
    # Pipeline: _discover_files → _extract_text → _chunk_text → _enrich_chunk → _build_indexes
```

2. 注册 `search_knowledge_base` 工具到 ToolRegistry（详见 C.3.2），所有 worker 可调用。

3. 改造 template_matcher 从 `["bm25"]` 改为 `["bm25", "vector"]` + RRF + reranking（详见 C.3.3）。

---

### A.8 Multi-Agent Orchestration — Harness 部分 (§9) — 实现度: 35%

**已实现**：

| 组件 | 文件 | 状态 |
|------|------|------|
| TaskManager | `runtime/task_manager.py` | ✅ 7 种 task 类型 + 状态机 |
| TaskType 枚举 | `runtime/task_types.py` | ✅ 7 种类型 |
| CoordinatorAgent | `coordinator/coordinator_agent.py` | ⚠️ 但是固定 phase 列表 |
| WorkerAgent | `coordinator/worker_agent.py` | ⚠️ 但是 compose_artifact 模式 |
| AgentLoop 基类 | `runtime/agent_loop.py` | ⚠️ 缺 ConversationStore/mailbox/ToolRegistry |

**未实现**：

| 组件 | 差距 |
|------|------|
| Fork sub-agent (KV cache) | 完全缺失 |
| In-process teammate | 完全缺失 |
| 异步并行执行 | 当前 _spawn_worker 同步阻塞 |
| 自主编排 | PHASES 硬编码列表 |
| AgentCapability 注册表 | 完全缺失 |

**实施方案**：

AgentLoop 改造见 C.1.2。Coordinator 和 Worker 重写见 Part B。异步并行通过 ThreadPoolExecutor 实现。

---

### A.9 Communication & Context Isolation (§10) — 实现度: 25%

**已实现**：

| 组件 | 文件 | 状态 |
|------|------|------|
| Mailbox | `runtime/mailbox.py` | ✅ fcntl.flock + JSONL |
| MessagePriority | `runtime/mailbox.py` | ✅ SHUTDOWN/TEAM_LEAD/PEER/BACKGROUND |
| TeammateContext | `runtime/agent_context.py` | ✅ ContextVar |
| AgentContext | `runtime/agent_context.py` | ✅ ContextVar |
| resolve() | `runtime/agent_context.py` | ✅ 3 级优先 |

**未实现**：

| 组件 | 差距 |
|------|------|
| Mailbox 消费 | Worker 不检查 mailbox，邮箱已建但从未读取 |
| Context 设置 | set_teammate_context() / set_agent_context() 从未在 workflow 中调用 |
| teammate_scope / agent_scope | context manager 未使用 |

**实施方案**：

1. 在 AgentLoop 中新增 `_check_mailbox()` 方法（C.1.2），每个 turn 开始时调用。

2. 在 CoordinatorAgent 的 `_spawn_agent()` 中用 `teammate_scope()` + `agent_scope()` 包裹 worker 执行（C.1.4）：

```python
with teammate_scope(TeammateContext(workspace_root=..., event_bus=..., ...)):
    with agent_scope(AgentContext(agent_id=f"worker-{capability_id}", ...)):
        worker_result = worker.run(task_prompt, context)
```

---

### A.10 System Integration (§11) — 实现度: 50%

**已实现**：

| 组件 | 文件 | 状态 |
|------|------|------|
| Workflow 驱动 | `coordinator/workflow.py` | ✅ 3 种执行模式 |
| JobWorkspace | `models/artifacts.py` | ✅ root/input_dir/generated_slides_dir/background_images_dir |
| ARTIFACT_NAMES | `models/artifacts.py` | ✅ 11 个 artifact 映射 |
| atomic_write_json / read_json | `models/artifacts.py` | ✅ |
| phase_state | `coordinator/phase_state.py` | ✅ load_artifact / write_artifact |

**未实现**：

| 组件 | 差距 |
|------|------|
| 完整数据流连通 | 多个子系统未连接（ToolRegistry、Mailbox、RAG） |
| LLM client 集成 memory + skill + compression | llm/client.py 已有但 AgentLoop 不使用 |

**实施方案**：通过 Part C 的开发任务逐项连通。

---

## Part B: Domain 层 — PPT 编排与领域方法

### B.1 Coordinator 重新设计

#### 核心改造

删除 `PHASES` 硬编码列表，Coordinator 接收目标（"生成 PPT"）和材料，由 LLM 自主决策执行顺序、并行策略和重试逻辑。

#### 6 个编排工具

```python
def get_available_tools(self) -> list[dict]:
    return [
        {
            "name": "spawn_agent",
            "description": "Spawn a worker agent for a specific capability. "
                           "Set async=true for background execution.",
            "parameters": {
                "capability": "Capability ID (e.g., 'document_analysis', 'outline_generation')",
                "instructions": "Additional instructions for the worker",
                "async": "If true, run in background (default: false)",
            },
        },
        {
            "name": "check_artifacts",
            "description": "Check which artifacts exist in the workspace and their status.",
            "parameters": {
                "artifact_names": "List of artifact names to check (optional, checks all if empty)",
            },
        },
        {
            "name": "wait_agents",
            "description": "Wait for one or more async agents to complete.",
            "parameters": {
                "task_ids": "List of task IDs to wait for",
                "timeout": "Timeout in seconds (default: 300)",
            },
        },
        {
            "name": "review_result",
            "description": "Review the result of a completed worker agent.",
            "parameters": {"task_id": "Task ID of the completed worker"},
        },
        {
            "name": "request_user_review",
            "description": "Pause for user review of slide_contents.json.",
            "parameters": {"message": "Message to show the user"},
        },
        {
            "name": "synthesize_output",
            "description": "Produce the final output after all work is done.",
            "parameters": {"summary": "Summary of results", "warnings": "Any warnings"},
        },
    ]
```

#### Coordinator system prompt

```python
def build_system_prompt(self) -> str:
    return f"""You are the Coordinator for a PPT generation system.

You have 8 capabilities available:
{self._format_capabilities()}

You decide AUTONOMOUSLY:
- Which capabilities to invoke and in what order
- Whether to run capabilities in parallel (e.g., outline_generation + template_matching)
- Whether to retry a failed capability
- When to request user review
- When to synthesize the final output

Dependency rules:
- document_analysis must complete before outline_generation
- outline_generation must complete before template_matching and design_planning
- content_mapping depends on outline, template, and design_plan
- visual_generation depends on slide_contents
- ppt_assembly depends on slide_contents (and optionally visual_generation)
- quality_verification depends on ppt_assembly

Parallel opportunities:
- outline_generation and template_matching can run in parallel after document_analysis
- design_planning can run in parallel with template_matching if outline is ready

After each spawn_agent completes, use check_artifacts to verify outputs.
If a worker fails, you may retry once or use the fallback.
After content_mapping, use request_user_review to let the user approve slide contents.
After all capabilities complete, use synthesize_output to finish.

{self._format_current_state()}
"""
```

#### 异步并行执行设计

```python
from concurrent.futures import ThreadPoolExecutor, Future

class CoordinatorAgent(AgentLoop):
    def __init__(self, ...):
        self._executor = ThreadPoolExecutor(max_workers=3)
        self._futures: dict[str, Future] = {}  # task_id -> Future

    def _spawn_agent(self, capability_id: str, instructions: str, is_async: bool) -> ToolResult:
        capability = get_capability(capability_id)
        # 创建 worker（见 B.4）
        worker = WorkerAgent(workspace=self.workspace, capability=capability, ...)
        # 设置 context isolation
        with teammate_scope(...), agent_scope(...):
            if is_async:
                future = self._executor.submit(worker.run, task_prompt, phase_context)
                self._futures[task.task_id] = future
                return ToolResult(output={"task_id": task.task_id, "status": "running_async"})
            else:
                worker_result = worker.run(task_prompt, phase_context)
                return ToolResult(output={"task_id": task.task_id, "status": "completed", ...})

    def _wait_agents(self, task_ids: list[str], timeout: int = 300) -> ToolResult:
        results = {}
        for tid in task_ids:
            future = self._futures.get(tid)
            if future:
                try:
                    result = future.result(timeout=timeout)
                    results[tid] = {"status": "completed", "result": result}
                except Exception as e:
                    results[tid] = {"status": "failed", "error": str(e)}
        return ToolResult(output=results)
```

#### Coordinator 决策流程示例

```
Turn 1: Coordinator reads task prompt → decides to start with document_analysis
  → calls spawn_agent("document_analysis", async=false)
  → gets source_summary.json

Turn 2: Checks artifacts → source_summary exists
  → decides to parallelize outline_generation + template_matching
  → calls spawn_agent("outline_generation", async=true) → task_id "a-0001"
  → calls spawn_agent("template_matching", async=true) → task_id "a-0002"

Turn 3: Calls wait_agents(["a-0001", "a-0002"])
  → both complete → outline.json + selected_template.json + template_meta.json ready

Turn 4: Checks artifacts → all ready for design_planning
  → calls spawn_agent("design_planning", async=false)
  → gets slide_design_plan.json

Turn 5: All inputs ready for content_mapping
  → calls spawn_agent("content_mapping", async=false)
  → gets slide_contents.json
  → calls request_user_review("Slide contents ready for review")

Turn 6: User approves
  → calls spawn_agent("visual_generation", async=false)
  → gets background_images/ + image_generation_report.json

Turn 7: Calls spawn_agent("ppt_assembly", async=false) → final.pptx

Turn 8: Calls spawn_agent("quality_verification", async=false) → validation_report.json

Turn 9: Calls synthesize_output("PPT generated successfully")
```

---

### B.2 AgentCapability 注册表

定义 8 种能力，在 `src/ppt_agent/coordinator/capabilities.py` 中声明：

```python
@dataclass(frozen=True)
class AgentCapability:
    capability_id: str
    description: str
    input_artifacts: list[str]
    output_artifacts: list[str]
    tools: list[str]
    max_turns: int = 10
    fallback_module: str = ""
    reads_input_dir: bool = False
    skill_name: str = ""
```

8 种能力完整定义：

| # | capability_id | input_artifacts | output_artifacts | tools | max_turns | fallback_module |
|---|---|---|---|---|---|---|
| 1 | document_analysis | [] | source_summary | read_input_files, read_artifact, write_artifact, validate_output, search_knowledge_base | 8 | workers.document_analyst |
| 2 | outline_generation | source_summary | outline | read_artifact, write_artifact, validate_output, search_knowledge_base, load_skill | 10 | workers.outline_generator |
| 3 | template_matching | outline | selected_template, template_meta, template_zones | read_artifact, write_artifact, validate_output, search_templates | 8 | workers.template_matcher |
| 4 | design_planning | outline, template_meta | slide_design_plan | read_artifact, write_artifact, validate_output, load_skill | 10 | workers.design_director |
| 5 | content_mapping | outline, selected_template, slide_design_plan, source_summary, template_zones | slide_contents | read_artifact, write_artifact, validate_output, search_knowledge_base, load_skill | 12 | workers.content_mapper |
| 6 | visual_generation | slide_contents, template_zones | image_generation_report | run_shell_command, check_file_exists, list_directory, read_artifact, write_artifact, validate_output | 15 | workers.image_generator |
| 7 | ppt_assembly | slide_contents | (final.pptx 文件) | read_artifact, assemble_pptx, check_file, write_artifact, validate_output | 8 | workers.ppt_assembler |
| 8 | quality_verification | slide_contents, image_generation_report | validation_report | read_artifact, write_artifact, validate_output, check_file, check_file_exists | 8 | workers.ppt_verifier |

---

### B.3 ToolFactory

`src/ppt_agent/coordinator/tool_factory.py`：

```python
class ToolFactory:
    """Create tool registries populated with capability-specific tools."""

    def __init__(self, workspace: JobWorkspace) -> None:
        self.workspace = workspace

    def create_registry_for_capability(
        self, capability: AgentCapability, *,
        skill_loader=None, llm_client=None,
    ) -> ToolRegistry:
        registry = ToolRegistry()
        self._register_common_tools(registry, capability, skill_loader)
        self._register_domain_tools(registry, capability, llm_client)
        return registry
```

通用工具（5 个，所有 capability 共享，只注册 capability.tools 中声明的）：

| 工具名 | category | 描述 |
|--------|----------|------|
| read_artifact | artifact | 从 workspace 读取 JSON artifact |
| write_artifact | artifact | 写入 JSON artifact 到 workspace |
| validate_output | artifact | 验证 artifact 存在且格式正确 |
| search_knowledge_base | retrieval | 使用 RAG pipeline 检索知识库 |
| load_skill | skill | 加载 SKILL.md 内容到 agent 上下文 |

每种能力的专属领域工具（从现有 worker_agent.py 的 _xxx 方法迁移）：

| capability | 专属工具 | 迁移来源 |
|---|---|---|
| document_analysis | read_input_files | _read_input_files() |
| template_matching | search_templates | _search_templates() |
| visual_generation | run_shell_command, check_file_exists, list_directory | _run_shell_command(), _check_file_exists(), _list_directory() |
| ppt_assembly | assemble_pptx, check_file | _assemble_pptx(), _check_file_exists() |

tool_impls 目录结构（`coordinator/tool_impls/`）：

```
__init__.py
common.py              # 5 个通用工具的 make_xxx() 工厂函数
document_analysis.py   # register_tools() → read_input_files
outline_generation.py  # register_tools() (可能为空，只用通用工具)
template_matching.py   # register_tools() → search_templates
design_planning.py     # register_tools() (可能为空)
content_mapping.py     # register_tools() (可能为空)
visual_generation.py   # register_tools() → run_shell_command, check_file_exists, list_directory
ppt_assembly.py        # register_tools() → assemble_pptx, check_file
quality_verification.py # register_tools() (可能为空)
```

每个 tool_impls 模块导出 `register_tools(registry, workspace, capability, llm_client)` 函数。`common.py` 导出 `make_read_artifact()`, `make_write_artifact()` 等工厂函数，返回 `(ToolDescriptor, executor)` 元组。

---

### B.4 WorkerAgent 重新设计

#### 核心改造

1. 用 `AgentCapability` 替代字符串 phase
2. 通过 ToolFactory 获取工具集
3. 移除 `compose_artifact` — Worker 直接使用 `write_artifact` 写入 JSON
4. 多轮工具交互：think → use tool → observe → think → ... → write artifact
5. 确定性回退：LLM 失败时调用 `capability.fallback_module.run()`

```python
class WorkerAgent(AgentLoop):
    def __init__(
        self, workspace: JobWorkspace, capability: AgentCapability, llm_client, *,
        force: bool = False, skill_content: str | None = None,
        instructions: str = "", registry: ToolRegistry | None = None,
    ) -> None:
        super().__init__(
            agent_id=f"worker-{capability.capability_id}",
            llm_client=llm_client,
            max_turns=capability.max_turns,
        )
        self.workspace = workspace
        self.capability = capability
        self._registry = registry

    def get_available_tools(self) -> list[dict]:
        if self._registry:
            return self._registry.get_tools_for_role_as_dicts("worker")
        return []

    def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        if self._registry:
            return self._registry.execute(tool_call, role="worker")
        return ToolResult(..., success=False, error="No tool registry configured")

    def _fallback_execution(self, agent_result: AgentLoopResult) -> AgentLoopResult:
        """通过 capability.fallback_module 回退。"""
        import importlib
        module = importlib.import_module(self.capability.fallback_module)
        output = module.run(self.workspace, force=self.force)
        agent_result.stop_reason = StopReason.COMPLETED
        agent_result.final_output = output
        return agent_result
```

#### Worker system prompt 模板

```python
def build_system_prompt(self) -> str:
    return f"""You are a PPT generation worker agent.

## Your Capability
{self.capability.description}

## Your Task
You are working on: {self.capability.capability_id}
Input artifacts: {self.capability.input_artifacts}
Output artifacts: {self.capability.output_artifacts}

## Instructions
1. Use read_artifact to read your input artifacts
2. Reason about what needs to be done
3. Use your available tools to accomplish the task
4. Use write_artifact to write each output artifact
5. Use validate_output to verify your work is correct

## Available Tools
{self._format_tools()}

## Available Skills
{self._format_skill_catalog()}

{self.skill_content or ""}
"""
```

---

### B.5 PPT 领域方法详解

#### B.5.1 模板选择方法 (Template Selection)

**输入**：`outline.json`（含 domain, audience, tone 元数据）

**检索流程**：

```python
# 1. 构建 query_text
query_parts = [outline["meta"].get("domain", ""),
                outline["meta"].get("audience", ""),
                outline["meta"].get("tone", "")]
query_text = " ".join(p for p in query_parts if p)

# 2. 加载模板索引
templates = load_template_index()

# 3. 检索（当前只用 BM25，应改为双路 + RRF）
ranked = query(
    [{"id": t["template_id"], "template_id": t["template_id"],
      "text": t.get("retrieval_text", ""), **t} for t in templates],
    query_text,
    ["bm25"],  # ← 应改为 ["bm25", "vector"] + RRF
    3,
)

# 4. 选择最佳匹配
best_match = ranked[0] if ranked and ranked[0].get("score", 0) > 0 else None
```

**产出三个 artifact**：

1. `selected_template.json`：template_id, selection_status, score, reason, ranking, warnings
2. `template_meta.json`：template_id, color_scheme, theme_colors, slides[] (每个 slide 含 index, layout, zones[])
3. `template_zones.json`：per-slide 的 template_image 路径 + text_zones + image_zones

**template_zones 结构**（`_build_template_zones()` 产出）：

```python
{
    "template_id": "modern.tech",
    "color_scheme": "blue",
    "theme_colors": {"primary": "#1F4E79", "secondary": "#70AD47", ...},
    "slide_count": 8,
    "slides": [
        {
            "index": 0,
            "template_image": "templates/modern.tech/slide_0.png",  # 用于 img2img 参考
            "text_zones": [{"zone_id": "title_0", "type": "title", "position": [0.08, 0.08, 0.84, 0.16]}, ...],
            "image_zones": [{"zone_id": "img_0", "type": "image", "position": [0.70, 0.32, 0.22, 0.34]}, ...],
            "all_zones": [...],
            "layout": "cover.hero"
        },
        ...
    ]
}
```

**回退逻辑**（`_build_fallback_zones()`）：无匹配模板时使用 DEFAULT_ZONES，template_image 为 None，layout 为 "cover.hero"（第一页）或 "fallback.basic"。

---

#### B.5.2 内容映射方法 (Content Mapping)

**输入**：outline + selected_template + slide_design_plan + source_summary + template_zones

**数据模型** — SlideZoneContent：

```python
@dataclass
class SlideZoneContent:
    zone_id: str           # 区域标识
    type: str              # title | subtitle | bullets | body | footer | image | chart | shape
    position: list[float]  # [x, y, w, h] 分数坐标 (0.0-1.0)
    editable: bool         # 是否为可编辑文本区域
    content: Any           # 文本内容 (str 或 list[str]) 或 None
    source: str            # generated | user_upload | placeholder
    image_ref: str | None  # 图片路径（image 类型）
    image_prompt: str | None  # 图片生成 prompt
    fit_status: str        # fits | overflow | unknown
```

**映射流程**（`_build_zones_for_slide()`）：

1. 从 template_zones 获取 text_zones 和 image_zones
2. 遍历 text_zones，按 type 分配内容：
   - type="title" → 写入 outline_slide["title"]
   - type="body"/"subtitle" → 写入 outline_slide["bullets"]
   - type="footer" → 空字符串（后续填充）
3. 如果没有匹配的 template zones，使用默认位置：title [0.08, 0.08, 0.84, 0.16]，body [0.10, 0.28, 0.56, 0.54]
4. 遍历 image_zones，分配 image_ref 和 image_prompt
5. 如果没有 image_zones，添加默认 image zone [0.70, 0.32, 0.22, 0.34]

**LLM 映射 vs 确定性映射**：
- LLM 映射（`_llm_mapping()`）：传入完整 context（outline, template, design_plan, image_inventory, template_zones），LLM 决定内容分配
- 确定性映射（`_fallback_mapping()`）：按上述固定逻辑映射

**验证逻辑**（`_ensure_valid_mapping()`）：确保每个 slide 有 zones，每个 zone 有 zone_id/type/position/editable/source/fit_status 字段。carry template_image 到 slide 级别供下游 img2img 使用。

**产出**：`slide_contents.json`

```python
{
    "template_id": "modern.tech",
    "review_status": "draft",
    "slides": [
        {
            "slide_index": 0,
            "layout": "cover.hero",
            "layout_id": "cover.hero",
            "visual_density": "low",
            "review_status": "draft",
            "source_refs": [],
            "fallback_flags": [],
            "template_image": "templates/modern.tech/slide_0.png",
            "zones": [
                {"zone_id": "title_0", "type": "title", "position": [0.08, 0.08, 0.84, 0.16],
                 "editable": true, "content": "AI驱动的PPT生成", "source": "generated", "fit_status": "fits"},
                {"zone_id": "body_0", "type": "bullets", "position": [0.10, 0.28, 0.56, 0.54],
                 "editable": true, "content": ["要点1", "要点2"], "source": "generated", "fit_status": "fits"},
                {"zone_id": "img_0", "type": "image", "position": [0.70, 0.32, 0.22, 0.34],
                 "editable": false, "source": "placeholder", "image_ref": null,
                 "image_prompt": "科技感背景图", "fit_status": "unknown"}
            ]
        }
    ]
}
```

---

#### B.5.3 设计规划方法 (Design Planning)

**输入**：outline + template_meta

**theme_profile 结构**：

```python
{
    "theme_id": "generated.professional",
    "color_tokens": {
        "primary": "#1F4E79",
        "background": "#FFFFFF",
        "text": "#1F2933"
    },
    "typography_tokens": {
        "title_font": "Aptos Display",
        "body_font": "Aptos",
        "title_scale": 1.0,
        "body_scale": 1.0
    },
    "spacing_tokens": {"page_margin": 0.08, "block_gap": 0.03, "card_padding": 0.02},
    "shape_tokens": {"border_radius": 0.02, "stroke": "light"},
    "image_treatment": {"crop": "contain", "tone": "natural"},
    "chart_style": {"palette": ["#1F4E79", "#70AD47", "#F4B183"]}
}
```

**per-slide 决策**：

```python
{
    "slide_index": 0,
    "layout_id": "cover.hero",         # 布局 ID
    "visual_density": "low",            # low | medium | high
    "block_plan": [                     # 内容块计划
        {"block_type": "editable_text", "purpose": "communicate slide message", "priority": "primary"}
    ],
    "visual_strategy": "placeholder",   # placeholder | background_image | split_layout
    "text_budget": {"max_title_chars": 70, "max_bullets": 5, "max_lines_per_block": 8},
    "design_constraints": ["preserve approved text as editable PPT text"],
    "design_warnings": []
}
```

**验证逻辑**（`_ensure_valid_design()`）：确保 theme_profile 有所有必需字段（color_tokens, typography_tokens, spacing_tokens, shape_tokens, image_treatment, chart_style），缺失字段用默认值填充。确保 slides 数量与 outline 一致。

**产出**：`slide_design_plan.json`

---

#### B.5.4 图片生成方法 (Image Generation)

**转换流程**（`slide_contents_to_batch_config()`）：

将 slide_contents 转换为 GPTImage2 批量配置：

```python
{
    "slides": [
        {
            "index": 0,
            "prompt": "A modern technology background with blue gradient, ...",
            "reference_image": "templates/modern.tech/slide_0.png",  # img2img 参考
            "output_name": "slide_00.png"
        }
    ]
}
```

**prompt 构建策略**（`_build_slide_prompt()`）：
- 基于 slide title + bullets + design_plan 的 visual_strategy
- 加入风格描述词（来自 theme_profile 的 color_tokens, image_treatment）
- 16:9 全页背景图

**两种生成模式**：
- img2img 模式：当有 template_image（参考图）时，使用 `--reference` 参数保持模板视觉风格
- text2img 模式：无参考图时，纯文本生成

**skill_context 结构**（`build_skill_context()`）：

```python
{
    "task": "background_image_generation",
    "skill_name": "gptimage2-generator",
    "workspace_root": "/path/to/workspace",
    "config_path": "/path/to/image_generation_config.json",
    "output_dir": "/path/to/background_images/",
    "slide_count": 8,
    "generation_mode": "full_page_background",
    "slides_needing_images": [
        {"index": 0, "prompt": "...", "has_reference": true,
         "reference_image": "...", "output_name": "slide_00.png"}
    ],
    "notes": "Generate FULL-PAGE 16:9 background images..."
}
```

**检查逻辑**（`check_generated_images()`）：检查 `background_images/` 和 `generated_slides/` 两个目录，对每个 slide 判断是否生成了图片。状态：generated / fallback_used。

**GPTImage2 skill 执行流程**：
1. WorkerAgent 读取 skill_instructions.json（或通过 load_skill 加载 SKILL.md）
2. Agent 自主决定如何调用 GPTImage2 API
3. 登录认证 → 逐张生成图片 → 轮询结果 → 退出登录
4. 生成失败时自动换号（积分耗尽场景）

**产出**：`background_images/` 目录 + `image_generation_report.json`

---

#### B.5.5 PPT 组装方法 (PPT Assembly)

**这是最重要的领域方法。**

**主入口**：`write_pptx(slide_contents, output_path, workspace_root)`

```python
def write_pptx(slide_contents: dict, output_path: str | Path,
               workspace_root: Path | None = None) -> Path:
    prs = Presentation()
    prs.slide_width = Emu(SLIDE_W)   # 12192000 EMU = 13.333 inch (16:9)
    prs.slide_height = Emu(SLIDE_H)  # 6858000 EMU = 7.5 inch

    for slide_data in slide_contents.get("slides", []):
        bg_image = _resolve_background_image(slide_data, workspace_root)
        if bg_image:
            _build_slide_with_background(prs, slide_data, bg_image)
        else:
            _build_slide_legacy(prs, slide_data, workspace_root)

    output_path = Path(output_path)
    prs.save(str(output_path))
    return output_path
```

**坐标系统**：

```python
SLIDE_W = 12192000  # EMU – 16:9 width
SLIDE_H = 6858000   # EMU – 16:9 height

def _emu_rect(position: list[float]) -> tuple[int, int, int, int]:
    """Convert fractional [x, y, w, h] to EMU (left, top, width, height)."""
    x_frac, y_frac, w_frac, h_frac = position
    return (int(x_frac * SLIDE_W), int(y_frac * SLIDE_H),
            int(w_frac * SLIDE_W), int(h_frac * SLIDE_H))
```

**模式 A — 背景图模式** (`_build_slide_with_background`)：

1. `_set_background_image(slide, image_path)`：全页背景图

```python
def _set_background_image(slide, image_path: Path) -> None:
    slide.shapes.add_picture(
        str(image_path), Emu(0), Emu(0), Emu(SLIDE_W), Emu(SLIDE_H))
```

2. `_add_text_overlay(slide, zone)`：透明文本框覆盖在背景图上

```python
def _add_text_overlay(slide, zone: dict) -> None:
    left, top, width, height = _emu_rect(zone["position"])
    txbox = slide.shapes.add_textbox(Emu(left), Emu(top), Emu(width), Emu(height))
    tf = txbox.text_frame
    tf.word_wrap = True
    txbox.fill.background()  # 透明背景

    if zone["type"] == "title":
        p = tf.paragraphs[0]
        p.text = str(zone["content"])
        p.alignment = PP_ALIGN.LEFT
        font = p.font
        font.size = Pt(title_font_size(text))  # 24/30/36pt based on length
        font.bold = True
        font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)  # 白色
        _add_text_shadow(p)  # 阴影增强可读性

    elif zone["type"] in ("bullets", "body"):
        items = zone["content"] if isinstance(zone["content"], list) else [str(zone["content"])]
        for idx, item in enumerate(items):
            p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
            p.text = item
            p.alignment = PP_ALIGN.LEFT
            # 添加项目符号 (a:buChar)
            font = p.font
            font.size = Pt(bullet_font_size(items))  # 15/17/20pt based on total chars
            font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)  # 白色
            _add_text_shadow(p)

    elif zone["type"] == "footer":
        # 灰色小字，右对齐
        font.size = Pt(12)
        font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)

    elif zone["type"] == "subtitle":
        # 浅灰色，左对齐
        font.size = Pt(20)
        font.color.rgb = RGBColor(0xEE, 0xEE, 0xEE)
```

3. `_add_text_shadow(paragraph)`：XML 阴影效果

```python
def _add_text_shadow(paragraph) -> None:
    rPr = paragraph.runs[0]._r.get_or_add_rPr()
    effectLst = rPr.makeelement(qn("a:effectLst"), {})
    outerShdw = effectLst.makeelement(qn("a:outerShdw"), {
        "blurRad": "38100",   # 3pt blur
        "dist": "19050",      # 1.5pt distance
        "dir": "5400000",     # 方向：下方
        "algn": "t",
    })
    srgbClr = outerShdw.makeelement(qn("a:srgbClr"), {"val": "000000"})
    alpha = srgbClr.makeelement(qn("a:alpha"), {"val": "60000"})  # 60% opacity
    srgbClr.append(alpha)
    outerShdw.append(srgbClr)
    effectLst.append(outerShdw)
    rPr.append(effectLst)
```

**模式 B — 传统模式** (`_build_slide_legacy`)：

1. `_set_white_background(slide)`：白色实色背景

```python
def _set_white_background(slide) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
```

2. Zone 类型处理函数：

| 函数 | zone type | 颜色 | 对齐 | 字号 |
|------|-----------|------|------|------|
| `_add_title_zone()` | title | 深灰 #333333 | LEFT | title_font_size(): 24/30/36pt |
| `_add_bullets_zone()` | bullets/body | 深灰 #444444 | LEFT + buChar | bullet_font_size(): 15/17/20pt |
| `_add_image_zone()` | image | 图片或占位矩形 | CENTER | 14pt (placeholder) |
| `_add_chart_zone()` | chart | 图表或占位矩形 | CENTER | 16pt (placeholder) |
| `_add_shape_zone()` | shape | 自定义 fill/line | CENTER | 14pt |

3. 图表支持 (`_add_chart_zone()`)：

```python
chart_type_map = {
    "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "line": XL_CHART_TYPE.LINE,
    "pie": XL_CHART_TYPE.PIE,
    "area": XL_CHART_TYPE.AREA,
}
# 使用 CategoryChartData：
chart_data = CategoryChartData()
chart_data.categories = chart_data_raw.get("categories", [])
for series in chart_data_raw.get("series", []):
    chart_data.add_series(series["name"], series["values"])
slide.shapes.add_chart(xl_type, left, top, width, height, chart_data)
```

4. 形状支持 (`_add_shape_zone()`)：

```python
shape_type_map = {
    "roundRect": MSO_SHAPE.ROUNDED_RECTANGLE,
    "rect": MSO_SHAPE.RECTANGLE,
    "ellipse": MSO_SHAPE.OVAL,
    "oval": MSO_SHAPE.OVAL,
    "diamond": MSO_SHAPE.DIAMOND,
    "triangle": MSO_SHAPE.ISOSCELES_TRIANGLE,
}
```

**背景图查找逻辑**（`_resolve_background_image()`）：
1. 检查 `background_images/slide_NN.png`（新流程目录）
2. 检查 `generated_slides/slide_NN.png`（旧流程目录）
3. 都没有 → 返回 None → 使用传统模式

**Zone 类型完整列表**：

| type | editable | 背景图模式 | 传统模式 |
|------|----------|-----------|---------|
| title | True | 白色加粗 + 阴影 | 深灰加粗 |
| subtitle | True | 浅灰 + 阴影 | (同 title) |
| bullets | True | 白色 + 项目符号 + 阴影 | 深灰 + 项目符号 |
| body | True | (同 bullets) | (同 bullets) |
| footer | True | 灰色小字右对齐 | (不渲染) |
| image | False | (不渲染，背景图覆盖) | 图片或占位矩形 |
| chart | False | (不渲染) | 图表或占位矩形 |
| shape | False | (不渲染) | 形状 |

**字体大小自适应** (`layout_fit.py`)：

```python
def title_font_size(text: str) -> int:
    if len(text) > 50: return 24
    if len(text) > 25: return 30
    return 36

def bullet_font_size(bullets: list[str]) -> int:
    total = sum(len(b) for b in bullets)
    if total > 200: return 15
    if total > 100: return 17
    return 20
```

---

#### B.5.6 PPT 创建方法 (PPTX Generation)

**python-pptx 使用方式**：

```python
from pptx import Presentation
from pptx.util import Emu, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.chart import XL_CHART_TYPE
from pptx.oxml.ns import qn
```

**Presentation 对象**：

```python
prs = Presentation()
prs.slide_width = Emu(SLIDE_W)   # 12192000
prs.slide_height = Emu(SLIDE_H)  # 6858000
# 使用 blank layout (index 6)
slide = prs.slides.add_slide(prs.slide_layouts[6])
```

**EMU 坐标系统**：EMU (English Metric Unit) 是 PPTX 的内部坐标单位。1 inch = 914400 EMU。16:9 幻灯片尺寸为 12192000 × 6858000 EMU (13.333" × 7.5")。

**文本框创建**：

```python
txbox = slide.shapes.add_textbox(Emu(left), Emu(top), Emu(width), Emu(height))
tf = txbox.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]  # 或 tf.add_paragraph()
p.text = "Hello"
p.alignment = PP_ALIGN.LEFT
font = p.font
font.size = Pt(24)
font.bold = True
font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
```

**图片插入**：

```python
slide.shapes.add_picture(str(image_path), Emu(left), Emu(top), Emu(width), Emu(height))
```

**图表创建**：

```python
from pptx.chart.data import CategoryChartData
chart_data = CategoryChartData()
chart_data.categories = ["Q1", "Q2", "Q3"]
chart_data.add_series("Revenue", [100, 200, 150])
slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED,
                       Emu(left), Emu(top), Emu(width), Emu(height), chart_data)
```

**形状创建**：

```python
shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                Emu(left), Emu(top), Emu(width), Emu(height))
shape.fill.solid()
shape.fill.fore_color.rgb = RGBColor(0xEE, 0xF2, 0xF6)
shape.line.color.rgb = RGBColor(0xB8, 0xC2, 0xCC)
```

**XML 操作**（命名空间和阴影）：

```python
from pptx.oxml.ns import qn
# 添加项目符号
pPr = p._p.get_or_add_pPr()
buChar = pPr.makeelement(qn("a:buChar"), {"char": "\u2022"})
pPr.append(buChar)
# 添加阴影
rPr = paragraph.runs[0]._r.get_or_add_rPr()
effectLst = rPr.makeelement(qn("a:effectLst"), {})
# ... (见 B.5.5 中的完整代码)
```

**背景设置**：

```python
# 实色背景
fill = slide.background.fill
fill.solid()
fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
# 透明背景（文本框）
txbox.fill.background()
```

---

#### B.5.7 验证与预览方法

**PPTX 验证** (`render_verify.py`)：

```python
def inspect_pptx(path: str | Path, expected_slide_count: int | None = None) -> dict:
    """检查 PPTX 文件的完整性。
    返回:
    {
        "slide_count": int,
        "total_shapes": int,
        "per_slide_info": [{"slide_index": 0, "shape_count": 5, "has_text": true, "has_image": true}, ...],
        "warnings": ["Slide 3 has no text shapes", ...],
        "file_size_bytes": int,
    }
    """
```

**HTML 预览** (`html_preview.py`)：

```python
def generate_preview_html(slide_contents: dict, job_root: Path) -> Path:
    """生成 HTML 预览文件，展示每张 slide 的卡片视图。
    每个 slide 显示为一个卡片，包含标题、bullets、图片占位符。
    输出文件：preview.html
    """
```

**文本保存检查** (`layout_fit.py`)：

```python
def assert_text_preserved(expected: list[str], actual: list[str]) -> bool:
    """验证所有预期文本都在实际输出中存在。"""
```

**Artifact 验证工具** (`validate_output` 通用工具)：

Worker 在写入 artifact 后调用 `validate_output` 验证：
- 文件存在且为合法 JSON
- 必需字段存在（如 slide_contents 必须有 slides 数组）
- slide 数量与 outline 一致
- 每个 zone 有 position 且值在 0.0-1.0 范围内
- title zone 的 content 不为空

---

## Part C: 开发任务清单

> **阅读指南**：每个任务包含目标文件、涉及子系统、具体工作内容（含函数签名）、验收标准和依赖关系。任务编号格式为 `C.{Phase}.{序号}`。所有代码使用 Python 3.11+，类型注解使用 `X | None` 语法。

---

### C.1 Phase 1: Runtime 基础设施 (P0)

> 这是整个 harness 的地基。所有后续 Phase 都依赖此层的接口。

---

#### C.1.1 创建 ConversationStore（JSONL 对话持久化）

- **目标文件**：`src/ppt_agent/runtime/conversation_store.py`（新建）
- **涉及子系统**：§2 Memory System
- **依赖任务**：无

**具体工作内容**：

创建 `ConversationStore` 类，实现 JSONL 格式的对话历史持久化。每条消息按行追加到 `history.jsonl`，支持 node_id / parent_id 分支结构（为未来 fork sub-agent 预留）。

```python
# src/ppt_agent/runtime/conversation_store.py

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ppt_agent.llm.messages import LLMMessage


@dataclass
class ConversationEntry:
    """A single persisted conversation entry."""
    id: str                         # 唯一消息 ID，格式 "msg_{uuid_hex[:12]}"
    role: str                       # "system" | "user" | "assistant"
    content: str                    # 消息文本
    timestamp: float                # time.time()
    node_id: str                    # 对话树节点 ID（默认等于 id）
    parent_id: str | None = None    # 父节点 ID，用于分支
    tool_calls: list[dict] | None = None  # assistant 消息的工具调用
    agent_id: str = ""              # 产生此消息的 agent
    metadata: dict = field(default_factory=dict)


class ConversationStore:
    """JSONL-based conversation persistence (AGENT_ARCHITECTURE.md §2.2).

    每条消息独立一行追加到 history.jsonl，支持：
    - 崩溃恢复：agent 重启后从文件恢复对话历史
    - 无损归档：压缩只修改内存中的 messages，完整历史始终保留在文件中
    - 分支结构：node_id / parent_id 为 fork sub-agent 预留

    线程安全：append 使用 append-only 模式打开文件，不需要锁。
    """

    def __init__(self, conversation_dir: Path) -> None:
        self.conversation_dir = Path(conversation_dir)
        self.conversation_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = self.conversation_dir / "history.jsonl"
        self.metadata_path = self.conversation_dir / "metadata.json"
        self._entry_count = 0

    def append(
        self,
        message: LLMMessage,
        *,
        node_id: str,
        parent_id: str | None = None,
        tool_calls: list[dict] | None = None,
        agent_id: str = "",
        metadata: dict | None = None,
    ) -> ConversationEntry:
        """Append a message to the JSONL history.

        Args:
            message: LLMMessage to persist.
            node_id: 对话树节点 ID.
            parent_id: 父节点 ID（可选，用于分支场景）.
            tool_calls: assistant 消息中的工具调用列表.
            agent_id: 产生此消息的 agent ID.
            metadata: 附加元数据 dict.

        Returns:
            创建的 ConversationEntry.
        """
        ...

    def load_messages(self, limit: int | None = None) -> list[LLMMessage]:
        """Load messages from history, optionally limited to the last N.

        Args:
            limit: 只返回最后 N 条消息。None 表示全部。

        Returns:
            LLMMessage 列表，按时间顺序排列。
        """
        ...

    def load_entries(self, limit: int | None = None) -> list[ConversationEntry]:
        """Load raw ConversationEntry objects (包含完整元数据).

        用于压缩决策、统计等需要元数据的场景。
        """
        ...

    def get_stats(self) -> dict:
        """Return conversation statistics.

        Returns:
            {
                "message_count": int,
                "total_chars": int,
                "oldest_timestamp": float | None,
                "newest_timestamp": float | None,
                "roles": {"system": int, "user": int, "assistant": int},
            }
        """
        ...

    def _serialize_entry(self, entry: ConversationEntry) -> str:
        """Serialize a ConversationEntry to a JSON line."""
        ...

    def _deserialize_entry(self, line: str) -> ConversationEntry:
        """Deserialize a JSON line to a ConversationEntry."""
        ...
```

**关键实现细节**：

1. `append()` 使用 `open("a")` 追加模式写入，每次写入一个完整 JSON 行 + `\n`，线程安全（OS 对小写保证原子性）
2. `load_messages()` 将 ConversationEntry 转换回 LLMMessage（只保留 role + content），丢弃元数据
3. `_serialize_entry()` 提取 `message.content[0].text`（当前 LLMMessage 的 content 是 `list[ContentBlock]` 结构），处理 None 值
4. `get_stats()` 遍历所有行计算统计，缓存 `_entry_count` 避免重复计数
5. 文件不存在时 `load_messages()` 返回空列表，不抛异常

**验收标准**：

1. `append()` 后文件增加一行合法 JSON，包含 `id`, `role`, `content`, `timestamp`, `node_id` 字段
2. `load_messages(limit=5)` 返回最后 5 条 LLMMessage，role 和 content 正确还原
3. `get_stats()` 返回的 `message_count` 等于 `append()` 调用次数
4. 并发两个线程各 append 100 条消息后，文件行数为 200 且每行都是合法 JSON
5. 空文件时 `load_messages()` 返回 `[]`，`get_stats()` 返回 `message_count: 0`

---

#### C.1.2 重写 AgentLoop（集成 ConversationStore + 消息压缩 + mailbox + ToolRegistry）

- **目标文件**：`src/ppt_agent/runtime/agent_loop.py`（重写）
- **涉及子系统**：§2, §3, §4, §5, §9, §10
- **依赖任务**：C.1.1, C.1.3

**具体工作内容**：

在现有 AgentLoop 基类上新增 4 个方法，改造 3 个现有方法。保留接口签名兼容性（`run()`, `_execute_turn()` 的参数和返回值不变），让 CoordinatorAgent 和 WorkerAgent 子类无需修改基础调用方式。

**新增 `__init__` 参数**：

```python
def __init__(
    self,
    agent_id: str,
    llm_client,
    *,
    max_turns: int = 20,
    max_consecutive_errors: int = 3,
    compression_level: int = 0,
    event_bus: EventBus | None = None,
    # ── 新增参数 ──
    conversation_store: ConversationStore | None = None,  # C.1.1
    mailbox: Mailbox | None = None,                       # §10
    tool_registry: ToolRegistry | None = None,            # C.1.3
    skill_loader: SkillLoader | None = None,              # §4
    context_window_limit: int = 128_000,                  # 模型的 context window 大小
) -> None:
```

**新增方法 1 — `_check_mailbox()`**：

```python
def _check_mailbox(self) -> None:
    """Check for incoming messages and inject into context (§10).

    在每个 turn 的 THINK 阶段开始前调用。
    读取未读消息，按优先级排序，注入到 self.messages 中。
    如果收到 SHUTDOWN 消息，设置 self._aborted = True。
    """
```

调用位置：`_execute_turn()` 的最开始，在 THINK 之前。

**新增方法 2 — `_compress_messages()`**：

```python
def _compress_messages(self) -> None:
    """Compress conversation history when approaching context window limit (§3).

    策略（对应 AGENT_ARCHITECTURE.md 的 L0-L4）：
    - L0: 不压缩（token 使用 < 60%）
    - L1: 截断大型工具输出（> 2000 chars）到 500 chars + [truncated]
    - L2: LLM 摘要前半段对话，保留后半段原文
    - L3: 只保留 system prompt + 最近 4 条消息 + 错误记录
    - L4: 紧急模式，只保留 system prompt + 最近 2 条消息

    压缩不变量（NEVER 被移除）：
    - self.messages[0]（system prompt，包含 agent.md + memory.md + session.md）
    - 最近 2 条消息
    - 包含 "[Error" 或 "[Tool Error" 的消息
    """
```

调用位置：`_call_llm()` 的最开始，在构建 augmented_messages 之前。

**新增方法 3 — `_load_skill_tool(skill_name: str) -> ToolResult`**：

```python
def _load_skill_tool(self, skill_name: str) -> ToolResult:
    """Load a skill's full SKILL.md content into context (§4).

    这是一个内置工具，任何 agent 都可以调用。
    首次加载时读取 SKILL.md 并追加为新的 system message。
    重复加载返回 "already loaded"。

    Args:
        skill_name: skill 目录名（如 "gptimage2-generator"）

    Returns:
        ToolResult with success=True and loaded content, or "already loaded" message.
    """
```

在 `__init__` 中初始化 `self._loaded_skills: set[str] = set()`。

**新增方法 4 — `_estimate_tokens(messages: list[LLMMessage]) -> int`**：

```python
def _estimate_tokens(self, messages: list[LLMMessage]) -> int:
    """Estimate total token count for a message list.

    使用字符数 / 4 的启发式估算（与现有 _CHARS_PER_TOKEN = 4 一致）。
    未来可替换为 tiktoken 精确计数。
    """
```

**改造 `run()` 方法**：

在现有 `run()` 的开始处增加：
1. 如果 `self.conversation_store` 不为 None，从 ConversationStore 加载历史消息到 `self.messages`（在 system prompt 之后）
2. 每次 `_execute_turn()` 后，将新增的消息追加到 ConversationStore

```python
def run(self, task_prompt: str, context: dict | None = None) -> AgentLoopResult:
    # ... 现有初始化 ...

    # ── 新增：恢复历史消息 ──
    if self.conversation_store is not None:
        prior = self.conversation_store.load_messages()
        if prior:
            # 插入到 system prompt 之后、新 user message 之前
            self.messages = [self.messages[0]] + prior + [self.messages[-1]]

    # ... 现有循环 ...
    # 在每个 turn 结束后：
    #   if self.conversation_store:
    #       for new_msg in (本轮新增的消息):
    #           self.conversation_store.append(new_msg, node_id=..., agent_id=self.agent_id)
```

**改造 `_execute_turn()` 方法**：

在 THINK 阶段开始前插入：
```python
# ── 新增：检查 mailbox ──
self._check_mailbox()
```

**改造 `_call_llm()` 方法**：

在构建 augmented_messages 之前插入：
```python
# ── 新增：消息压缩 ──
self._compress_messages()
```

**验收标准**：

1. 新参数 `conversation_store`, `mailbox`, `tool_registry`, `skill_loader` 都有默认值 None，不传时行为与改造前完全一致
2. 传入 ConversationStore 时，`run()` 完成后 history.jsonl 中包含所有对话消息
3. 传入 Mailbox 并写入一条消息后，下一个 turn 的 `self.messages` 中出现 `[Message from ...]` 内容
4. `_compress_messages()` 在 token 估算 < 60% 时不修改 messages；> 80% 时触发 L2 压缩，messages 数量减少
5. `_load_skill_tool("gptimage2-generator")` 首次返回 SKILL.md 内容，第二次返回 "already loaded"
6. 现有测试（如果有）仍然通过，AgentLoop 的 `run()` 和 `_execute_turn()` 的返回值类型不变

---

#### C.1.3 重写 ToolRegistry（可执行注册表 + 三层 RBAC）

- **目标文件**：`src/ppt_agent/tools/registry.py`（重写）
- **涉及子系统**：§5 Tool Registry & RBAC
- **依赖任务**：无

**具体工作内容**：

将当前的"描述符列表"升级为"可执行的工具注册表"。新增 `ToolEntry` 将描述符和执行器绑定；`check_permission()` 从单层 allowlist 升级为三层检查。

```python
# src/ppt_agent/tools/registry.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ppt_agent.runtime.agent_loop import ToolCall, ToolResult


class ToolExecutor(Protocol):
    """Protocol for tool execution functions."""
    def __call__(self, arguments: dict[str, Any]) -> ToolResult: ...


@dataclass(frozen=True)
class ToolDescriptor:
    """Descriptor for a registered tool (保持现有接口)."""
    name: str
    category: str                # filesystem | execution | retrieval | assembly | orchestration | ...
    roles: frozenset[str]        # 允许的角色集合
    description: str = ""
    parameters: dict = field(default_factory=dict)  # 新增：参数 JSON Schema

    def allows_role(self, role: str) -> bool:
        return role in self.roles

    def to_llm_dict(self) -> dict:
        """Convert to LLM-readable tool description dict."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass
class ToolEntry:
    """A registered tool with its executor."""
    descriptor: ToolDescriptor
    executor: ToolExecutor          # 实际执行函数：(arguments: dict) -> ToolResult
    category: str                   # 冗余字段，等于 descriptor.category，便于按类别查询

    @property
    def name(self) -> str:
        return self.descriptor.name


# ─── Role Definitions (保持现有) ──────────────────────────────────
ROLE_MAIN_AGENT = "main_agent"
ROLE_COORDINATOR = "coordinator"
ROLE_WORKER = "worker"
ROLE_SUB_AGENT = "sub_agent"

ALL_ROLES = frozenset({ROLE_MAIN_AGENT, ROLE_COORDINATOR, ROLE_WORKER, ROLE_SUB_AGENT})
WORKER_AND_ABOVE = frozenset({ROLE_MAIN_AGENT, ROLE_WORKER, ROLE_SUB_AGENT})
COORDINATOR_ONLY = frozenset({ROLE_COORDINATOR})
FULL_ACCESS = ALL_ROLES

# ─── Category-based permissions (§5.2) ──────────────────────────
CATEGORY_PERMISSIONS: dict[str, frozenset[str]] = {
    "filesystem":     WORKER_AND_ABOVE,
    "execution":      WORKER_AND_ABOVE,
    "retrieval":      WORKER_AND_ABOVE,
    "assembly":       WORKER_AND_ABOVE,
    "verification":   WORKER_AND_ABOVE,
    "extraction":     WORKER_AND_ABOVE,
    "generation":     WORKER_AND_ABOVE,
    "skill":          WORKER_AND_ABOVE,
    "artifact":       FULL_ACCESS,
    "orchestration":  COORDINATOR_ONLY,
    "communication":  FULL_ACCESS,
}


class ToolRegistry:
    """Central tool registry with executable entries and three-layer RBAC.

    三层权限检查 (§5.3)：
    1. 注册检查：tool_name 是否在 registry 中
    2. Category 检查：caller role 是否有该 category 的访问权
    3. Tool-level 检查：tool descriptor 的 roles 是否包含 caller role

    可选第四层：caller 的显式 allowlist（由 agent 自己维护，不在 registry 层面）。
    """

    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}

    def register_tool(
        self,
        descriptor: ToolDescriptor,
        executor: ToolExecutor,
    ) -> None:
        """Register a tool with its descriptor and executor.

        Args:
            descriptor: 工具描述（name, category, roles, description, parameters）
            executor: 执行函数，签名 (arguments: dict) -> ToolResult

        Raises:
            ValueError: 如果同名工具已注册
        """
        if descriptor.name in self._entries:
            raise ValueError(f"Tool '{descriptor.name}' already registered")
        self._entries[descriptor.name] = ToolEntry(
            descriptor=descriptor,
            executor=executor,
            category=descriptor.category,
        )

    def register_tool_simple(
        self,
        name: str,
        category: str,
        roles: frozenset[str],
        executor: ToolExecutor,
        description: str = "",
        parameters: dict | None = None,
    ) -> None:
        """Convenience method to register without manually creating ToolDescriptor."""
        desc = ToolDescriptor(
            name=name,
            category=category,
            roles=roles,
            description=description,
            parameters=parameters or {},
        )
        self.register_tool(desc, executor)

    def get(self, name: str) -> ToolEntry:
        """Get a tool entry by name. Raises KeyError if not found."""
        if name not in self._entries:
            raise KeyError(f"Tool '{name}' not registered. Available: {list(self._entries.keys())}")
        return self._entries[name]

    def check_permission(self, tool_name: str, role: str) -> None:
        """Three-layer permission check (§5.3).

        Layer 1: 工具是否已注册
        Layer 2: 角色是否有该 category 的权限
        Layer 3: 工具的 descriptor.roles 是否包含该角色

        Raises:
            KeyError: tool 未注册 (Layer 1)
            PermissionError: 角色无权 (Layer 2 或 Layer 3)
        """
        # Layer 1: Registration check
        entry = self.get(tool_name)  # raises KeyError if not found

        # Layer 2: Category-based gating
        category = entry.category
        allowed_roles = CATEGORY_PERMISSIONS.get(category, frozenset())
        if role not in allowed_roles:
            raise PermissionError(
                f"Role '{role}' has no access to category '{category}'. "
                f"Allowed roles for this category: {allowed_roles}"
            )

        # Layer 3: Tool-level role check
        if not entry.descriptor.allows_role(role):
            raise PermissionError(
                f"Role '{role}' cannot use tool '{tool_name}'. "
                f"Allowed roles: {entry.descriptor.roles}"
            )

    def execute(self, tool_call: ToolCall, role: str) -> ToolResult:
        """Check permissions and execute a tool call.

        Args:
            tool_call: 包含 tool_name 和 arguments 的调用请求
            role: 调用者角色

        Returns:
            ToolResult
        """
        self.check_permission(tool_call.tool_name, role)
        entry = self.get(tool_call.tool_name)
        return entry.executor(tool_call.arguments)

    def get_tools_for_role(self, role: str) -> list[ToolDescriptor]:
        """Return all tool descriptors accessible to a given role."""
        result = []
        for entry in self._entries.values():
            category = entry.category
            cat_roles = CATEGORY_PERMISSIONS.get(category, frozenset())
            if role in cat_roles and entry.descriptor.allows_role(role):
                result.append(entry.descriptor)
        return result

    def get_tools_for_role_as_dicts(self, role: str) -> list[dict]:
        """Return LLM-ready tool description dicts for a role."""
        return [d.to_llm_dict() for d in self.get_tools_for_role(role)]

    def list_by_category(self, category: str) -> list[ToolEntry]:
        """List all tools in a category."""
        return [e for e in self._entries.values() if e.category == category]

    @property
    def tool_count(self) -> int:
        return len(self._entries)
```

**验收标准**：

1. `register_tool()` 后 `tool_count` 增加 1，`get()` 能取回 ToolEntry
2. `check_permission("spawn_agent", "worker")` 抛 PermissionError（orchestration category）
3. `check_permission("read_artifact", "worker")` 通过（artifact category，FULL_ACCESS）
4. `check_permission("nonexistent", "worker")` 抛 KeyError
5. `execute(tool_call, "worker")` 先校验权限再调用 executor，executor 的返回值被透传
6. `get_tools_for_role("coordinator")` 只返回 orchestration + communication + artifact 类工具
7. `get_tools_for_role("worker")` 不包含 orchestration 类工具
8. 重复注册同名工具抛 ValueError

---

#### C.1.4 集成 AgentContext（spawn 时设置 scope）

- **目标文件**：`src/ppt_agent/runtime/agent_context.py`（小改）+ `src/ppt_agent/coordinator/coordinator_agent.py`（修改 `_spawn_worker`）
- **涉及子系统**：§10 Communication & Context Isolation
- **依赖任务**：C.1.2

**具体工作内容**：

在 CoordinatorAgent 的 `_spawn_worker()` 方法中，用 `teammate_scope()` 和 `agent_scope()` 包裹 worker 的执行：

```python
# coordinator_agent.py 中的 _spawn_worker 改造

from ppt_agent.runtime.agent_context import (
    TeammateContext, AgentContext,
    teammate_scope, agent_scope,
)

def _spawn_worker(self, phase: str, instructions: str) -> ToolResult:
    # ... 现有的 task 创建和 skill 加载 ...

    with teammate_scope(TeammateContext(
        workspace_root=self.workspace.root,
        event_bus=self.event_bus,
        skill_loader=self.skill_loader,
        job_id=self.workspace.root.name,
        team_role="worker",
    )):
        with agent_scope(AgentContext(
            agent_id=f"worker-{phase}",
            phase=phase,
        )):
            worker_result = worker.run(task_prompt=task_prompt, context=phase_context)

    # ... 现有的结果处理 ...
```

同时在 AgentLoop 基类的 `run()` 方法中（C.1.2），将现有的 `agent_attribution(self.agent_id)` 替换为同时设置 `agent_scope()`。

**验收标准**：

1. Worker 执行期间，`get_teammate_context()` 返回非 None 的 TeammateContext，包含正确的 `workspace_root` 和 `job_id`
2. Worker 执行期间，`get_agent_context()` 返回非 None 的 AgentContext，包含正确的 `agent_id` 和 `phase`
3. Worker 执行完成后，`get_teammate_context()` 和 `get_agent_context()` 恢复为 None（context manager 正确清理）
4. `resolve("workspace_root")` 在 Worker 执行期间返回正确的 workspace root 路径

---

### C.2 Phase 2: Infrastructure 补全 (P1)

> 在 Runtime 地基之上，完善记忆、压缩、Skill、事件总线和沙箱。

---

#### C.2.1 Memory 跨会话改造

- **目标文件**：`src/ppt_agent/context/memory_layers.py`（修改）
- **涉及子系统**：§2 Memory System
- **依赖任务**：无

**具体工作内容**：

将 memory.md 的存储路径从 per-job (`workspace_root/.ppt_agent/memory.md`) 改为 per-workspace 级别，使得不同 job 共享同一份 memory.md。

1. 新增配置项 `WORKSPACE_MEMORY_ROOT`，默认为 job workspace 的父目录:
```python
def get_workspace_memory_root(job_root: Path) -> Path:
    """Resolve the workspace-level memory root.

    优先使用环境变量 PPT_AGENT_MEMORY_ROOT，
    否则使用 job_root 的父目录（即 workspace/jobs/ 的上一级 workspace/）。

    Returns:
        workspace 级别的 .ppt_agent/ 目录路径
    """
    env_root = os.environ.get("PPT_AGENT_MEMORY_ROOT")
    if env_root:
        return Path(env_root) / MEMORY_DIR
    return job_root.parent / MEMORY_DIR
```

2. 修改 `ensure_memory_files()` 和 `build_memory_context()`，让 memory.md 读写使用 workspace 级路径，而 agent.md 和 session.md 仍然使用 job 级路径。

3. 修改 `append_memory(workspace_root, "memory", content)` 使其写入 workspace 级路径。

4. `read_memory(workspace_root, "memory")` 优先读 workspace 级路径，不存在时回退读 job 级路径（兼容旧数据）。

**验收标准**：

1. Job A 通过 `append_memory(job_a_root, "memory", "insight_1")` 写入后，Job B 通过 `read_memory(job_b_root, "memory")` 能读到 "insight_1"（两个 job 在同一 workspace 下）
2. `session.md` 仍然是 per-job 的，Job A 和 Job B 的 session.md 互不影响
3. `agent.md` 仍然是 per-job 的
4. 设置 `PPT_AGENT_MEMORY_ROOT=/tmp/test_memory` 后，memory.md 写入到 `/tmp/test_memory/.ppt_agent/memory.md`

---

#### C.2.2 消息压缩完善

- **目标文件**：`src/ppt_agent/runtime/agent_loop.py`（C.1.2 中新增的 `_compress_messages` 完善实现）
- **涉及子系统**：§3 Context Window Management
- **依赖任务**：C.1.2

**具体工作内容**：

完善 C.1.2 中声明的 `_compress_messages()` 方法，实现完整的 L0-L4 五级消息压缩。

```python
def _compress_messages(self) -> None:
    total_tokens = self._estimate_tokens(self.messages)
    utilization = total_tokens / self.context_window_limit

    if utilization < 0.6:
        return  # L0: 不压缩

    # 确定压缩级别
    if utilization >= 0.95:
        level = 4
    elif utilization >= 0.85:
        level = 3
    elif utilization >= 0.75:
        level = 2
    else:
        level = 1

    # 压缩不变量：始终保留的消息
    system_msg = self.messages[0]  # system prompt
    recent_msgs = self.messages[-2:]  # 最近 2 条
    error_msgs = [m for m in self.messages[1:-2]
                  if "[Error" in (m.content[0].text or "") or "[Tool Error" in (m.content[0].text or "")]

    compressible = [m for m in self.messages[1:-2] if m not in error_msgs]

    if level == 1:
        # L1: 截断大型工具输出
        for msg in compressible:
            text = msg.content[0].text or ""
            if "[Tool Result:" in text and len(text) > 2000:
                # 保留前 500 字符 + truncated 标记
                truncated = text[:500] + "\n... [truncated, original: " + str(len(text)) + " chars]"
                msg.content[0].text = truncated

    elif level == 2:
        # L2: LLM 摘要前半段，保留后半段原文
        midpoint = len(compressible) // 2
        old_msgs = compressible[:midpoint]
        keep_msgs = compressible[midpoint:]

        if old_msgs:
            summary = self._summarize_conversation(old_msgs)
            summary_msg = LLMMessage.user(f"[Conversation Summary]\n{summary}")
            self.messages = [system_msg, summary_msg] + error_msgs + keep_msgs + recent_msgs
        return

    elif level == 3:
        # L3: 只保留 system + 错误 + 最近 4 条
        recent_4 = self.messages[-4:] if len(self.messages) >= 4 else self.messages[1:]
        self.messages = [system_msg] + error_msgs + recent_4
        return

    elif level == 4:
        # L4: 紧急模式，只保留 system + 最近 2 条
        self.messages = [system_msg] + recent_msgs
        return

    # L1 不改变 messages 结构，只截断内容
    self._emit(EventType.COMPRESSION_EVENT,
               message=f"Compressed messages at L{level}, utilization={utilization:.1%}")


def _summarize_conversation(self, messages: list[LLMMessage]) -> str:
    """Use LLM to summarize a list of messages into a concise summary.

    Falls back to concatenated truncation if LLM fails.
    """
    combined = "\n".join(
        f"[{m.role}] {(m.content[0].text or '')[:500]}" for m in messages
    )
    try:
        from ppt_agent.llm.messages import LLMMessage as Msg
        result = self.llm_client.provider.generate(
            [
                Msg.system("Summarize the following conversation concisely. "
                          "Preserve all key decisions, tool results, and artifacts mentioned."),
                Msg.user(combined),
            ],
            max_tokens=500,
            temperature=0.0,
        )
        if result.success and result.text:
            return result.text.strip()
    except Exception:
        pass
    # Fallback: 截断拼接
    return combined[:2000] + "... [truncated summary]"
```

**验收标准**：

1. 消息总 token < 60% context window 时，`_compress_messages()` 不修改 messages
2. 模拟 80% 利用率，触发 L2 压缩后 messages 数量减少约 50%
3. L2 压缩后 `messages[0]`（system prompt）不变
4. L2 压缩后最近 2 条消息内容不变
5. L4 压缩后 messages 只剩 3 条（system + 最近 2 条）
6. 包含 `[Tool Error:` 的消息在 L1-L3 压缩中被保留
7. 压缩触发后 EventBus 收到 `compression_event` 事件

---

#### C.2.3 Skill agent 驱动加载

- **目标文件**：`src/ppt_agent/skills/loader.py`（修改）+ `src/ppt_agent/runtime/agent_loop.py`（C.1.2 中的 `_load_skill_tool`）
- **涉及子系统**：§4 Skill System
- **依赖任务**：C.1.2

**具体工作内容**：

1. 在 `SkillLoader.load_skill()` 中增加返回值变体，首次加载返回完整内容，重复加载返回特殊标记（而非 None），让 agent 能区分"已加载"和"未找到"：

```python
# skills/loader.py 修改

def load_skill(self, skill_name: str) -> str | None:
    """Phase 2: Load full SKILL.md content.

    Returns:
        - 首次加载：完整 SKILL.md 内容
        - 重复加载：None（调用者应检查 is_loaded()）
        - 未找到：None
    """
    # ... 现有逻辑保持不变 ...

def is_loaded(self, skill_name: str) -> bool:
    """Check if a skill has already been loaded."""
    return skill_name in self._loaded
```

2. 在 AgentLoop 中实现 `_load_skill_tool()`（C.1.2 中声明），作为所有 agent 的内置工具：

```python
# agent_loop.py

def _load_skill_tool(self, skill_name: str) -> ToolResult:
    if not self.skill_loader:
        return ToolResult(call_id="", output=None, success=False,
                          error="No skill loader configured")

    if self.skill_loader.is_loaded(skill_name):
        return ToolResult(call_id="", output={"status": "already_loaded", "skill": skill_name},
                          success=True)

    content = self.skill_loader.load_skill(skill_name)
    if content is None:
        return ToolResult(call_id="", output=None, success=False,
                          error=f"Skill '{skill_name}' not found")

    # 追加 skill 内容到 system prompt 中
    self.messages[0].content[0].text += f"\n\n## Loaded Skill: {skill_name}\n\n{content}"
    self._loaded_skills.add(skill_name)

    # 发射事件
    self._emit(EventType.SKILL_LOADED, message=f"Loaded skill: {skill_name}")

    return ToolResult(call_id="", output={"status": "loaded", "skill": skill_name,
                                          "content_length": len(content)},
                      success=True)
```

3. 所有 agent 的 system prompt 中注入 skill manifest（Phase 1 catalog）。修改 AgentLoop 的 `build_system_prompt()` 文档，要求子类在 system prompt 中包含 `self.skill_loader.get_catalog_summary()` 的结果。在 WorkerAgent 的 `_build_content_reasoning_prompt()` 等方法中加入 skill catalog 注入。

**验收标准**：

1. `_load_skill_tool("gptimage2-generator")` 首次返回 `success=True, status="loaded"`
2. 再次调用返回 `success=True, status="already_loaded"`
3. `_load_skill_tool("nonexistent")` 返回 `success=False, error="Skill 'nonexistent' not found"`
4. 首次加载后 `self.messages[0]`（system prompt）的 text 末尾包含 SKILL.md 内容
5. Worker agent 的 system prompt 中包含 "Available Skills" 列表
6. EventBus 收到 `skill_loaded` 事件

---

#### C.2.4 Event Bus 补全缺失事件类型 + SSE Consumer

- **目标文件**：`src/ppt_agent/coordinator/event_bus.py`（修改）+ `src/ppt_agent/runtime/agent_loop.py`（补全事件发射点）
- **涉及子系统**：§6 Event Bus
- **依赖任务**：C.1.2

**具体工作内容**：

1. 在 AgentLoop 中补全事件发射点：
   - `text_delta`：在 `_call_llm()` 中，如果 provider 支持流式生成（检查 `hasattr(provider, 'generate_stream')`），每个 chunk 发射一次
   - `agent_spawn`：在 CoordinatorAgent 的 `_spawn_worker()` 开始时发射（当前由 TaskManager 发射，确认链路通畅）
   - `agent_complete`：在 `_spawn_worker()` 中 `worker.run()` 返回后发射
   - `memory_update`：在 `append_memory()` 调用后发射
   - `compression_event`：在 `_compress_messages()` 触发压缩后发射（C.2.2 已包含）
   - `skill_loaded`：在 `_load_skill_tool()` 成功后发射（C.2.3 已包含）

2. 新增 `SSEConsumer`，将事件转发到 FastAPI 的 SSE endpoint：

```python
# event_bus.py 新增

class SSEConsumer(EventConsumer):
    """Consumer that pushes events to an SSE queue for frontend streaming.

    与 FastAPI 的 StreamingResponse 配合使用。
    """

    def __init__(self, max_queue_size: int = 500) -> None:
        import asyncio
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=max_queue_size)

    def handle(self, event: dict) -> None:
        try:
            self._queue.put_nowait(event)
        except Exception:
            pass  # Queue full, drop event (backpressure)

    async def event_generator(self):
        """Async generator for SSE streaming."""
        while True:
            event = await self._queue.get()
            yield event
```

**验收标准**：

1. 完整 pipeline 运行后，history.jsonl 中包含 `agent_spawn`, `agent_complete`, `phase_started`, `phase_completed` 事件
2. 长对话触发压缩时，history.jsonl 中出现 `compression_event`
3. 加载 skill 时，history.jsonl 中出现 `skill_loaded`
4. `SSEConsumer.event_generator()` 能异步 yield 出 `handle()` 收到的事件
5. 已定义的 16 种事件类型中，至少 12 种在完整 pipeline 中被发射

---

#### C.2.5 Sandbox 最小实现

- **目标文件**：`src/ppt_agent/tools/filesystem.py`（修改）+ `src/ppt_agent/coordinator/worker_agent.py`（修改 `_run_shell_command`）
- **涉及子系统**：§7 Sandbox
- **依赖任务**：无

**具体工作内容**：

1. 在所有文件操作工具中强制使用 `SafeFilesystem`：

```python
# worker_agent.py 中修改

def __init__(self, ...):
    # ... 现有 ...
    self._safe_fs = SafeFilesystem(self.workspace.root)

# 所有 _read_artifact, _write_artifact, _check_file_exists, _list_directory 中：
# 将 Path(path) 改为 self._safe_fs.resolve(path)
```

2. 在 `_run_shell_command()` 中增加安全限制：

```python
def _run_shell_command(self, command: str, timeout: int = 120, cwd: str | None = None) -> ToolResult:
    # ── 新增：命令安全检查 ──
    _BLOCKED_PATTERNS = [
        "rm -rf /", "rm -rf /*", "mkfs", "> /dev/",
        "dd if=", ":(){ :|:& };:",  # fork bomb
    ]
    for pattern in _BLOCKED_PATTERNS:
        if pattern in command:
            return ToolResult(call_id="", output=None, success=False,
                              error=f"Blocked dangerous command pattern: {pattern}")

    # ── 新增：强制 cwd 在 workspace 内 ──
    work_dir = cwd or str(self.workspace.root)
    resolved_cwd = Path(work_dir).resolve()
    if self.workspace.root not in resolved_cwd.parents and resolved_cwd != self.workspace.root:
        return ToolResult(call_id="", output=None, success=False,
                          error=f"Working directory must be within workspace: {resolved_cwd}")

    # ── 新增：环境变量白名单 ──
    safe_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "PYTHONUNBUFFERED": "1",
        "WORKSPACE_ROOT": str(self.workspace.root),
    }

    result = subprocess.run(
        command, shell=True, capture_output=True, text=True,
        timeout=timeout, cwd=work_dir,
        env=safe_env,  # 替换 {**os.environ, ...}
    )
    # ... 现有结果处理 ...
```

**验收标准**：

1. `_read_artifact()` 尝试读 workspace 外的文件时抛 PermissionError
2. `_run_shell_command("rm -rf /")` 返回 success=False，包含 "Blocked" 错误
3. `_run_shell_command("ls", cwd="/etc")` 返回 success=False（cwd 在 workspace 外）
4. `_run_shell_command("echo $HOME")` 输出合法（HOME 在白名单中）
5. `_run_shell_command("echo $SECRET_TOKEN")` 输出为空（SECRET_TOKEN 不在白名单中）

---

### C.3 Phase 3: RAG 激活 (P2)

> 让现有的 RAG pipeline 从"代码存在"变为"实际运行"。

---

#### C.3.1 知识库摄入 pipeline

- **目标文件**：`src/ppt_agent/retrieval/ingestion_pipeline.py`（新建）
- **涉及子系统**：§8 Hybrid RAG
- **依赖任务**：无

**具体工作内容**：

创建完整的知识库摄入 pipeline，将输入文件分块、增强上下文、构建索引。

```python
# src/ppt_agent/retrieval/ingestion_pipeline.py

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ppt_agent.retrieval.chunking import chunk_document
from ppt_agent.retrieval.sparse.bm25 import BM25Index

logger = logging.getLogger(__name__)


@dataclass
class IngestedChunk:
    """A single chunk with enriched context."""
    chunk_id: str
    source_doc: str           # 来源文件路径
    text: str                 # chunk 原文
    context_prefix: str       # LLM 生成的上下文前置（Anthropic Contextual Retrieval 方法）
    metadata: dict = field(default_factory=dict)


@dataclass
class KnowledgeBaseIndex:
    """Persisted knowledge base index."""
    name: str                          # 知识库名称（如 "templates", "user_materials"）
    chunks: list[IngestedChunk]        # 所有 chunks
    bm25_index: BM25Index | None = None
    index_path: Path | None = None     # 索引持久化目录

    def save(self, path: Path) -> None:
        """Persist index to disk."""
        ...

    @classmethod
    def load(cls, path: Path) -> KnowledgeBaseIndex:
        """Load index from disk."""
        ...


class IngestionPipeline:
    """Knowledge base ingestion: files → chunks → enriched chunks → indexes.

    Pipeline stages:
    1. 文件发现：扫描输入目录，识别文件类型
    2. 文本提取：使用 documents.extract_text() 提取纯文本
    3. 分块：使用 chunking.chunk_document() 按类型分块
    4. 上下文增强：（可选）使用 LLM 为每个 chunk 生成上下文前置
    5. 索引构建：构建 BM25 索引（+ 可选 vector 索引）
    6. 持久化：保存到 workspace 的 .kb/ 目录
    """

    def __init__(
        self,
        kb_name: str,
        output_dir: Path,
        *,
        llm_client=None,       # 可选，用于上下文增强
        chunk_size: int = 512,
        chunk_overlap: int = 64,
    ) -> None:
        self.kb_name = kb_name
        self.output_dir = output_dir
        self.llm_client = llm_client
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def ingest(self, source_paths: list[Path]) -> KnowledgeBaseIndex:
        """Run the full ingestion pipeline.

        Args:
            source_paths: 输入文件或目录列表

        Returns:
            构建好的 KnowledgeBaseIndex
        """
        ...

    def _discover_files(self, source_paths: list[Path]) -> list[Path]:
        """Discover all files to ingest (recursive for directories)."""
        ...

    def _extract_text(self, file_path: Path) -> str:
        """Extract text from a single file."""
        ...

    def _chunk_text(self, text: str, source_doc: str) -> list[IngestedChunk]:
        """Split text into chunks."""
        ...

    def _enrich_chunk(self, chunk: IngestedChunk, full_doc_text: str) -> IngestedChunk:
        """Add LLM-generated context prefix to a chunk (Anthropic Contextual Retrieval).

        Prompt: "Given this document: {full_doc_text[:2000]}
                 Provide a short context for this chunk to improve retrieval:
                 {chunk.text}"

        If no LLM client, returns chunk unchanged.
        """
        ...

    def _build_indexes(self, chunks: list[IngestedChunk]) -> KnowledgeBaseIndex:
        """Build BM25 (and optionally vector) indexes from enriched chunks."""
        ...
```

**验收标准**：

1. `ingest([Path("input/")])` 扫描目录下所有 .txt/.md/.docx 文件
2. 每个文件被分块为 ≤ 512 字符的 chunks
3. 如果传入 llm_client，每个 chunk 的 `context_prefix` 不为空
4. 如果不传 llm_client，`context_prefix` 为空字符串
5. 构建的 BM25 索引能正确检索匹配的 chunks
6. `KnowledgeBaseIndex.save()` 后 `KnowledgeBaseIndex.load()` 能还原完整索引
7. 索引持久化到 `output_dir/.kb/{kb_name}/` 目录

---

#### C.3.2 search_knowledge_base 工具注册

- **目标文件**：`src/ppt_agent/coordinator/tool_impls/common.py`（C.4.3 中新建，此处定义工具实现）
- **涉及子系统**：§8 Hybrid RAG
- **依赖任务**：C.3.1, C.1.3

**具体工作内容**：

实现 `search_knowledge_base` 工具，注册到 ToolRegistry，任何 agent 都可以调用：

```python
def search_knowledge_base(arguments: dict) -> ToolResult:
    """Search the knowledge base using hybrid retrieval.

    Args (from arguments dict):
        query: str — 搜索查询
        top_k: int — 返回结果数（默认 5）
        kb_name: str — 知识库名称（默认 "default"）

    Returns:
        ToolResult with ranked results.
    """
    query = arguments.get("query", "")
    top_k = int(arguments.get("top_k", 5))
    kb_name = arguments.get("kb_name", "default")

    # 从 workspace 加载已构建的索引
    # 使用 query_router.query() 执行检索
    # 返回 top_k 结果
```

注册到 ToolRegistry 时的描述符：
```python
ToolDescriptor(
    name="search_knowledge_base",
    category="retrieval",
    roles=WORKER_AND_ABOVE,
    description="Search the knowledge base using hybrid retrieval (BM25 + vector + RRF). "
                "Returns ranked results with relevance scores.",
    parameters={
        "query": {"type": "string", "description": "Search query"},
        "top_k": {"type": "integer", "description": "Number of results (default: 5)", "default": 5},
    },
)
```

**验收标准**：

1. 工具注册后 `registry.get("search_knowledge_base")` 返回有效的 ToolEntry
2. Worker 角色可以调用，Coordinator 角色不能调用（retrieval category）
3. 传入有效 query 后返回 top_k 个结果，每个结果包含 `text`, `score`, `source` 字段
4. 知识库不存在时返回 `success=False` 和明确错误信息

---

#### C.3.3 改造 template_matcher 使用完整 4-stage pipeline

- **目标文件**：`src/ppt_agent/workers/template_matcher.py`（修改）+ `src/ppt_agent/coordinator/worker_agent.py`（修改 `_search_templates`）
- **涉及子系统**：§8 Hybrid RAG
- **依赖任务**：C.3.1

**具体工作内容**：

当前 `_search_templates()` 只使用 BM25 单路检索。改造为使用完整的 4-stage pipeline（Query Enhancement → Multi-Route → RRF Fusion → Reranking）。

```python
# worker_agent.py 中的 _search_templates 改造

def _search_templates(self, query_text: str, top_k: int = 3) -> ToolResult:
    from ppt_agent.retrieval.query_router import query
    from ppt_agent.retrieval.template_index import load_template_index

    templates = load_template_index()
    items = [
        {"id": t["template_id"], "template_id": t["template_id"],
         "text": t.get("retrieval_text", ""), **t}
        for t in templates
    ]

    # 改造：从 ["bm25"] 改为使用完整 pipeline
    ranked = query(
        items,
        query_text,
        methods=["bm25", "vector"],  # 使用双路检索
        top_k=top_k,
        use_enhancement=True,         # 启用 query enhancement
        use_reranking=True,           # 启用 reranking
    )

    return ToolResult(
        call_id="", success=True,
        output={
            "query": query_text,
            "result_count": len(ranked),
            "results": ranked[:top_k],
        },
    )
```

同时修改 `retrieval/query_router.py` 的 `query()` 函数签名，增加 `use_enhancement` 和 `use_reranking` 参数（默认 False 保持兼容）。

**验收标准**：

1. `_search_templates()` 使用双路检索后，返回结果的排序质量优于纯 BM25（通过 RRF 分数验证）
2. `use_enhancement=True` 时，query_router 调用 `query_enhancement.py` 的 LLM 重写
3. `use_reranking=True` 时，query_router 调用 `rerankers/base.py` 的启发式 reranker
4. 当 LLM 不可用时，`use_enhancement=True` 回退到原始 query（不崩溃）
5. 现有的 template_matcher worker 仍然能正常工作

---

### C.4 Phase 4: 编排层 + Domain 层 (P0)

> Coordinator 和 Worker 的核心重写，从"固定 phase 列表"变为"自主能力驱动"。

---

#### C.4.1 创建 AgentCapability 注册表

- **目标文件**：`src/ppt_agent/coordinator/capabilities.py`（新建）
- **涉及子系统**：§9 Multi-Agent Orchestration
- **依赖任务**：无

**具体工作内容**：

声明 8 种 agent 能力，每种能力定义输入输出 artifact、可用工具、最大 turn 数和回退模块。

```python
# src/ppt_agent/coordinator/capabilities.py

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentCapability:
    """Declarative definition of an agent capability.

    每种能力声明了：
    - 它需要什么 artifact 作为输入
    - 它产出什么 artifact
    - 它可以使用哪些工具（tool names）
    - 它最多运行多少 turn
    - 如果 LLM 失败，回退到哪个确定性模块
    """
    capability_id: str
    description: str
    input_artifacts: list[str]       # 需要的输入 artifact 名称
    output_artifacts: list[str]      # 产出的 artifact 名称
    tools: list[str]                 # 允许使用的工具名称列表
    max_turns: int = 10              # 最大 turn 数
    fallback_module: str = ""        # 确定性回退模块路径（如 "ppt_agent.workers.document_analyst"）
    reads_input_dir: bool = False    # 是否需要读取原始输入目录
    skill_name: str = ""             # 关联的 skill 名称（如 "ppt-outline-generator"）


# ── 8 种能力定义 ──

DOCUMENT_ANALYSIS = AgentCapability(
    capability_id="document_analysis",
    description="Analyze uploaded project materials and produce a structured source summary.",
    input_artifacts=[],
    output_artifacts=["source_summary"],
    tools=[
        "read_input_files", "read_artifact", "write_artifact",
        "validate_output", "search_knowledge_base",
    ],
    max_turns=8,
    fallback_module="ppt_agent.workers.document_analyst",
    reads_input_dir=True,
)

OUTLINE_GENERATION = AgentCapability(
    capability_id="outline_generation",
    description="Create a structured presentation outline from the source summary.",
    input_artifacts=["source_summary"],
    output_artifacts=["outline"],
    tools=[
        "read_artifact", "write_artifact", "validate_output",
        "search_knowledge_base", "load_skill",
    ],
    max_turns=10,
    fallback_module="ppt_agent.workers.outline_generator",
    skill_name="ppt-outline-generator",
)

TEMPLATE_MATCHING = AgentCapability(
    capability_id="template_matching",
    description="Search and select the best template for the presentation.",
    input_artifacts=["outline"],
    output_artifacts=["selected_template", "template_meta", "template_zones"],
    tools=[
        "read_artifact", "write_artifact", "validate_output",
        "search_templates",
    ],
    max_turns=8,
    fallback_module="ppt_agent.workers.template_matcher",
    skill_name="ppt-template-matcher",
)

DESIGN_PLANNING = AgentCapability(
    capability_id="design_planning",
    description="Create a detailed slide design plan with theme, layouts, and visual density.",
    input_artifacts=["outline", "template_meta"],
    output_artifacts=["slide_design_plan"],
    tools=[
        "read_artifact", "write_artifact", "validate_output",
        "load_skill",
    ],
    max_turns=10,
    fallback_module="ppt_agent.workers.design_director",
    skill_name="ppt-design-director",
)

CONTENT_MAPPING = AgentCapability(
    capability_id="content_mapping",
    description="Map outline content into template zones, producing user-reviewable slide contents.",
    input_artifacts=["outline", "selected_template", "slide_design_plan", "source_summary", "template_zones"],
    output_artifacts=["slide_contents"],
    tools=[
        "read_artifact", "write_artifact", "validate_output",
        "search_knowledge_base", "load_skill",
    ],
    max_turns=12,
    fallback_module="ppt_agent.workers.content_mapper",
    skill_name="ppt-content-mapper",
)

VISUAL_GENERATION = AgentCapability(
    capability_id="visual_generation",
    description="Generate AI images for slides using the gptimage2-generator skill.",
    input_artifacts=["slide_contents", "template_zones"],
    output_artifacts=["image_generation_report"],
    tools=[
        "run_shell_command", "check_file_exists", "list_directory",
        "read_artifact", "write_artifact", "validate_output",
    ],
    max_turns=15,
    fallback_module="ppt_agent.workers.image_generator",
    skill_name="gptimage2-generator",
)

PPT_ASSEMBLY = AgentCapability(
    capability_id="ppt_assembly",
    description="Assemble the final PowerPoint file from slide contents and images.",
    input_artifacts=["slide_contents"],
    output_artifacts=[],
    tools=[
        "read_artifact", "assemble_pptx", "check_file",
        "write_artifact", "validate_output",
    ],
    max_turns=8,
    fallback_module="ppt_agent.workers.ppt_assembler",
    skill_name="ppt-assembler",
)

QUALITY_VERIFICATION = AgentCapability(
    capability_id="quality_verification",
    description="Verify the final PPT with fresh eyes — check content, visuals, and accuracy.",
    input_artifacts=["slide_contents", "image_generation_report"],
    output_artifacts=["validation_report"],
    tools=[
        "read_artifact", "write_artifact", "validate_output",
        "check_file", "check_file_exists",
    ],
    max_turns=8,
    fallback_module="ppt_agent.workers.ppt_verifier",
)


# ── 注册表 ──

CAPABILITY_REGISTRY: dict[str, AgentCapability] = {
    cap.capability_id: cap
    for cap in [
        DOCUMENT_ANALYSIS,
        OUTLINE_GENERATION,
        TEMPLATE_MATCHING,
        DESIGN_PLANNING,
        CONTENT_MAPPING,
        VISUAL_GENERATION,
        PPT_ASSEMBLY,
        QUALITY_VERIFICATION,
    ]
}


def get_capability(capability_id: str) -> AgentCapability:
    """Get a capability by ID. Raises KeyError if not found."""
    if capability_id not in CAPABILITY_REGISTRY:
        raise KeyError(
            f"Unknown capability: {capability_id}. "
            f"Available: {list(CAPABILITY_REGISTRY.keys())}"
        )
    return CAPABILITY_REGISTRY[capability_id]


def list_capabilities() -> list[AgentCapability]:
    """Return all registered capabilities in pipeline order."""
    return list(CAPABILITY_REGISTRY.values())
```

**验收标准**：

1. `CAPABILITY_REGISTRY` 包含 8 个 capability，每个 capability_id 唯一
2. `get_capability("document_analysis")` 返回 DOCUMENT_ANALYSIS，其 `input_artifacts` 为空，`output_artifacts` 为 `["source_summary"]`
3. `get_capability("content_mapping")` 的 `input_artifacts` 包含 5 个 artifact 名称
4. 每个 capability 的 `fallback_module` 指向一个可 import 的模块路径
5. `list_capabilities()` 返回 8 个 capability，顺序与 pipeline 一致
6. 每个 capability 的 `tools` 列表中至少包含 `write_artifact` 和 `validate_output`

---

#### C.4.2 创建 ToolFactory

- **目标文件**：`src/ppt_agent/coordinator/tool_factory.py`（新建）
- **涉及子系统**：§5 Tool Registry, §9 Multi-Agent Orchestration
- **依赖任务**：C.1.3, C.4.1, C.4.3

**具体工作内容**：

按 capability 创建工具集，将工具描述符和执行函数注册到 ToolRegistry。

```python
# src/ppt_agent/coordinator/tool_factory.py

from __future__ import annotations

from pathlib import Path

from ppt_agent.coordinator.capabilities import AgentCapability
from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.tools.registry import (
    ToolDescriptor, ToolRegistry, WORKER_AND_ABOVE, FULL_ACCESS,
)


class ToolFactory:
    """Create tool registries populated with capability-specific tools.

    通用工具（5个）所有 capability 都有：
    - read_artifact
    - write_artifact
    - validate_output
    - search_knowledge_base
    - load_skill

    领域工具按 capability 分配，从 tool_impls/ 模块加载。
    """

    def __init__(self, workspace: JobWorkspace) -> None:
        self.workspace = workspace

    def create_registry_for_capability(
        self,
        capability: AgentCapability,
        *,
        skill_loader=None,
        llm_client=None,
    ) -> ToolRegistry:
        """Create a ToolRegistry populated with tools for the given capability.

        Args:
            capability: 能力定义，决定加载哪些工具
            skill_loader: SkillLoader 实例，用于 load_skill 工具
            llm_client: LLM 客户端，用于某些工具

        Returns:
            填充好的 ToolRegistry
        """
        registry = ToolRegistry()

        # 1. 注册通用工具
        self._register_common_tools(registry, capability, skill_loader)

        # 2. 注册能力特定的领域工具
        self._register_domain_tools(registry, capability, llm_client)

        return registry

    def _register_common_tools(
        self,
        registry: ToolRegistry,
        capability: AgentCapability,
        skill_loader=None,
    ) -> None:
        """Register the 5 common tools available to all capabilities."""
        from ppt_agent.coordinator.tool_impls.common import (
            make_read_artifact,
            make_write_artifact,
            make_validate_output,
            make_search_knowledge_base,
            make_load_skill,
        )
        # 只注册 capability.tools 中声明的通用工具
        common_makers = {
            "read_artifact": make_read_artifact,
            "write_artifact": make_write_artifact,
            "validate_output": make_validate_output,
            "search_knowledge_base": make_search_knowledge_base,
            "load_skill": make_load_skill,
        }
        for tool_name in capability.tools:
            if tool_name in common_makers:
                desc, executor = common_makers[tool_name](self.workspace, skill_loader)
                registry.register_tool(desc, executor)

    def _register_domain_tools(
        self,
        registry: ToolRegistry,
        capability: AgentCapability,
        llm_client=None,
    ) -> None:
        """Register capability-specific domain tools."""
        # 按 capability_id 加载对应的 tool_impls 模块
        import importlib
        module_name = f"ppt_agent.coordinator.tool_impls.{capability.capability_id}"
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            return  # 没有对应的 tool_impls 模块，跳过

        # 每个 tool_impls 模块导出 register_tools(registry, workspace, capability, llm_client)
        if hasattr(module, "register_tools"):
            module.register_tools(registry, self.workspace, capability, llm_client)
```

**验收标准**：

1. `create_registry_for_capability(DOCUMENT_ANALYSIS)` 返回包含 `read_input_files`, `read_artifact`, `write_artifact`, `validate_output`, `search_knowledge_base` 的 registry
2. `create_registry_for_capability(TEMPLATE_MATCHING)` 返回包含 `search_templates` 的 registry
3. `create_registry_for_capability(VISUAL_GENERATION)` 返回包含 `run_shell_command`, `check_file_exists`, `list_directory` 的 registry
4. 所有注册的工具都有有效的 executor（callable）
5. 工具数量与 capability.tools 列表长度一致

---

#### C.4.3 创建 8 个 tool_impls 模块

- **目标文件**：`src/ppt_agent/coordinator/tool_impls/`（新建目录 + 9 个文件）
- **涉及子系统**：§5 Tool Registry, §9 Multi-Agent Orchestration
- **依赖任务**：C.1.3, C.4.1

**具体工作内容**：

新建 `coordinator/tool_impls/` 目录，包含以下文件：

**`__init__.py`**：空文件

**`common.py`**：5 个通用工具的工厂函数

```python
# src/ppt_agent/coordinator/tool_impls/common.py

"""Common tools shared by all capabilities.

每个工厂函数返回 (ToolDescriptor, executor) 元组。
工厂函数接收 workspace 和其他依赖，返回绑定了 workspace 的 executor 闭包。
"""

from ppt_agent.models.artifacts import JobWorkspace
from ppt_agent.runtime.agent_loop import ToolResult
from ppt_agent.tools.registry import ToolDescriptor, WORKER_AND_ABOVE, FULL_ACCESS


def make_read_artifact(workspace: JobWorkspace, skill_loader=None):
    """Create read_artifact tool."""
    def executor(arguments: dict) -> ToolResult:
        name = arguments.get("name", "")
        # ... 从 workspace 读取 artifact ...
    descriptor = ToolDescriptor(
        name="read_artifact",
        category="artifact",
        roles=FULL_ACCESS,
        description="Read a JSON artifact from the workspace.",
        parameters={"name": {"type": "string", "description": "Artifact name"}},
    )
    return descriptor, executor


def make_write_artifact(workspace: JobWorkspace, skill_loader=None):
    """Create write_artifact tool."""
    def executor(arguments: dict) -> ToolResult:
        name = arguments.get("name", "")
        payload = arguments.get("payload", {})
        # ... 写入 workspace ...
    descriptor = ToolDescriptor(
        name="write_artifact",
        category="artifact",
        roles=FULL_ACCESS,
        description="Write a JSON artifact to the workspace.",
        parameters={
            "name": {"type": "string", "description": "Artifact name"},
            "payload": {"type": "object", "description": "JSON data to write"},
        },
    )
    return descriptor, executor


def make_validate_output(workspace: JobWorkspace, skill_loader=None):
    """Create validate_output tool."""
    # ... 验证 artifact 存在且格式正确 ...


def make_search_knowledge_base(workspace: JobWorkspace, skill_loader=None):
    """Create search_knowledge_base tool."""
    # ... 使用 RAG pipeline 检索 ...


def make_load_skill(workspace: JobWorkspace, skill_loader=None):
    """Create load_skill tool."""
    # ... 加载 SKILL.md 内容 ...
```

**`document_analysis.py`**：文档分析专属工具

```python
def register_tools(registry, workspace, capability, llm_client=None):
    """Register document_analysis-specific tools."""
    # read_input_files: 读取原始输入文件
    registry.register_tool_simple(
        name="read_input_files",
        category="extraction",
        roles=WORKER_AND_ABOVE,
        executor=_make_read_input_files(workspace),
        description="Read raw input files from the workspace input directory.",
        parameters={"max_chars": {"type": "integer", "default": 10000}},
    )
```

**其余 7 个模块**（`outline_generation.py`, `template_matching.py`, `design_planning.py`, `content_mapping.py`, `visual_generation.py`, `ppt_assembly.py`, `quality_verification.py`）：

每个模块导出 `register_tools(registry, workspace, capability, llm_client)` 函数，注册该能力的专属领域工具。工具实现大部分从现有 `worker_agent.py` 中的 `_xxx` 方法迁移过来，例如：

- `template_matching.py`：注册 `search_templates` 工具（迁移自 `_search_templates`）
- `ppt_assembly.py`：注册 `assemble_pptx` 和 `check_file` 工具（迁移自 `_assemble_pptx` 和 `_check_file_exists`）
- `visual_generation.py`：注册 `run_shell_command`, `check_file_exists`, `list_directory` 工具（迁移自对应方法）

**验收标准**：

1. `coordinator/tool_impls/` 目录包含 `__init__.py` + `common.py` + 8 个能力模块 = 10 个文件
2. 每个能力模块都导出 `register_tools()` 函数
3. `common.py` 的 5 个 `make_xxx()` 函数都返回 `(ToolDescriptor, callable)` 元组
4. 从 `worker_agent.py` 迁移的工具实现功能等价（相同输入产生相同输出）
5. 所有 tool executor 返回 `ToolResult` 对象

---

#### C.4.4 重写 CoordinatorAgent（自主决策 + 异步并行 + 6 个编排工具）

- **目标文件**：`src/ppt_agent/coordinator/coordinator_agent.py`（重写）
- **涉及子系统**：§9 Multi-Agent Orchestration
- **依赖任务**：C.1.2, C.1.3, C.1.4, C.4.1, C.4.2

**具体工作内容**：

核心改造点：

1. **删除 PHASES 列表**：Coordinator 不再接收固定 phase 列表，只接收目标（"生成 PPT"）和材料。

2. **自主决策 system prompt**：让 LLM 自己决定执行顺序、是否并行、是否重试。

3. **6 个编排工具**：

```python
def get_available_tools(self) -> list[dict]:
    return [
        {
            "name": "spawn_agent",
            "description": "Spawn a worker agent for a specific capability. "
                           "Set async=true for background execution.",
            "parameters": {
                "capability": "Capability ID (e.g., 'document_analysis')",
                "instructions": "Additional instructions for the worker",
                "async": "If true, run in background and continue (default: false)",
            },
        },
        {
            "name": "check_artifacts",
            "description": "Check which artifacts exist in the workspace and their status.",
            "parameters": {
                "artifact_names": "List of artifact names to check (optional, checks all if empty)",
            },
        },
        {
            "name": "wait_agents",
            "description": "Wait for one or more async agents to complete.",
            "parameters": {
                "task_ids": "List of task IDs to wait for",
                "timeout": "Timeout in seconds (default: 300)",
            },
        },
        {
            "name": "review_result",
            "description": "Review the result of a completed worker agent.",
            "parameters": {
                "task_id": "Task ID of the completed worker",
            },
        },
        {
            "name": "request_user_review",
            "description": "Pause for user review of slide_contents.json.",
            "parameters": {
                "message": "Message to show the user",
            },
        },
        {
            "name": "synthesize_output",
            "description": "Produce the final output after all work is done.",
            "parameters": {
                "summary": "Summary of results",
                "warnings": "Any warnings or issues",
            },
        },
    ]
```

4. **ThreadPoolExecutor 异步执行**：

```python
from concurrent.futures import ThreadPoolExecutor, Future

class CoordinatorAgent(AgentLoop):
    def __init__(self, ...):
        # ...
        self._executor = ThreadPoolExecutor(max_workers=3)
        self._futures: dict[str, Future] = {}  # task_id -> Future

    def _spawn_agent(self, capability_id: str, instructions: str, is_async: bool) -> ToolResult:
        capability = get_capability(capability_id)
        # ... 创建 worker ...

        if is_async:
            future = self._executor.submit(worker.run, task_prompt, phase_context)
            self._futures[task.task_id] = future
            return ToolResult(call_id="", output={"task_id": task.task_id, "status": "running_async"},
                              success=True)
        else:
            # 同步执行
            worker_result = worker.run(task_prompt, phase_context)
            # ... 处理结果 ...
```

5. **自主决策 system prompt**：

```python
def build_system_prompt(self) -> str:
    return f"""You are the Coordinator for a PPT generation system.

You have 8 capabilities available:
{self._format_capabilities()}

You decide AUTONOMOUSLY:
- Which capabilities to invoke and in what order
- Whether to run capabilities in parallel (e.g., outline_generation + template_matching)
- Whether to retry a failed capability
- When to request user review
- When to synthesize the final output

Dependency rules:
- document_analysis must complete before outline_generation
- outline_generation must complete before template_matching and design_planning
- content_mapping depends on outline, template, and design_plan
- visual_generation depends on slide_contents
- ppt_assembly depends on slide_contents (and optionally visual_generation)
- quality_verification depends on ppt_assembly

Parallel opportunities:
- outline_generation and template_matching can run in parallel after document_analysis
- design_planning can run in parallel with template_matching if outline is ready

{self._format_current_state()}
"""
```

**验收标准**：

1. CoordinatorAgent 不再接收 `phases` 参数
2. `get_available_tools()` 返回 6 个工具
3. `spawn_agent(capability="document_analysis", async=false)` 同步执行并返回结果
4. `spawn_agent(capability="outline_generation", async=true)` 异步提交并立即返回 task_id
5. `wait_agents(task_ids=["a-0001"])` 等待异步 agent 完成并返回结果
6. `check_artifacts(artifact_names=["source_summary", "outline"])` 返回每个 artifact 的存在状态和大小
7. Coordinator 不再按固定顺序执行 phases，而是由 LLM 自主决策

---

#### C.4.5 重写 WorkerAgent（capability 驱动 + 真实工具 + 移除 compose_artifact）

- **目标文件**：`src/ppt_agent/coordinator/worker_agent.py`（重写）
- **涉及子系统**：§9 Multi-Agent Orchestration
- **依赖任务**：C.1.2, C.1.3, C.4.1, C.4.2, C.4.3

**具体工作内容**：

核心改造点：

1. **用 capability 替代 phase**：WorkerAgent 接收 `AgentCapability` 而非字符串 phase。

2. **通过 ToolFactory 获取工具集**：不再硬编码三套工具方法。

3. **移除 compose_artifact**：Worker 直接使用 `write_artifact` 写入 JSON，不再通过 `compose_artifact` 中间步骤。

4. **多轮工具交互**：Worker 使用真实工具读取输入、推理、产出输出。

5. **确定性回退**：LLM 失败时调用 `capability.fallback_module` 的 `run()` 函数。

```python
# worker_agent.py 重写核心

class WorkerAgent(AgentLoop):
    def __init__(
        self,
        workspace: JobWorkspace,
        capability: AgentCapability,    # 替代 phase: str
        llm_client,
        *,
        force: bool = False,
        skill_content: str | None = None,
        instructions: str = "",
        registry: ToolRegistry | None = None,  # 从 ToolFactory 获取
    ) -> None:
        super().__init__(
            agent_id=f"worker-{capability.capability_id}",
            llm_client=llm_client,
            max_turns=capability.max_turns,
            max_consecutive_errors=3,
        )
        self.workspace = workspace
        self.capability = capability
        self.force = force
        self.skill_content = skill_content
        self.instructions = instructions
        self._registry = registry

    def get_available_tools(self) -> list[dict]:
        """从 ToolRegistry 获取工具描述，而非硬编码。"""
        if self._registry:
            return self._registry.get_tools_for_role_as_dicts("worker")
        return []

    def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """通过 ToolRegistry 执行工具，自动进行 RBAC 检查。"""
        if self._registry:
            return self._registry.execute(tool_call, role="worker")
        return ToolResult(call_id=tool_call.call_id, output=None, success=False,
                          error="No tool registry configured")

    def _fallback_execution(self, agent_result: AgentLoopResult) -> AgentLoopResult:
        """通过 capability.fallback_module 回退。"""
        module_path = self.capability.fallback_module
        if not module_path:
            agent_result.error = "No fallback module configured"
            return agent_result
        import importlib
        module = importlib.import_module(module_path)
        try:
            output = module.run(self.workspace, force=self.force)
            agent_result.stop_reason = StopReason.COMPLETED
            agent_result.final_output = output
            agent_result.error = None
        except Exception as e:
            agent_result.error = f"Fallback failed: {e}"
        return agent_result
```

**验收标准**：

1. WorkerAgent 接收 `AgentCapability` 对象而非字符串 phase
2. `get_available_tools()` 返回从 ToolRegistry 获取的工具列表
3. `execute_tool()` 通过 ToolRegistry 执行，PermissionError 时返回 `success=False`
4. 没有 `compose_artifact` 方法/工具
5. LLM 失败时，自动调用 `capability.fallback_module` 指向的确定性 worker
6. Worker 的 `max_turns` 来自 `capability.max_turns`
7. Worker 的 system prompt 包含 capability 描述和可用工具列表

---

#### C.4.6 简化 Workflow（不再传 phases）

- **目标文件**：`src/ppt_agent/coordinator/workflow.py`（修改）
- **涉及子系统**：§9 Multi-Agent Orchestration, §11 System Integration
- **依赖任务**：C.4.4

**具体工作内容**：

简化 `_run_agent_workflow()` 函数，不再传 phases 列表给 Coordinator。Coordinator 自主决策执行顺序。

```python
def _run_agent_workflow(
    workspace: JobWorkspace,
    llm_client,
    bus: EventBus,
    phases: list[str],    # 保留参数用于 until/start_from 约束
    force: bool,
) -> list[Path]:
    """Run using the full agent architecture.

    Coordinator 自主决策执行顺序。phases 参数只用于约束范围
    （通过 until/start_from 传入），不决定顺序。
    """
    coordinator = CoordinatorAgent(
        workspace=workspace,
        llm_client=llm_client,
        task_manager=TaskManager(event_bus=bus),
        event_bus=bus,
        skill_loader=SkillLoader(root=Path(".catpaw/skills")),
        force=force,
        # 不再传 phases=phases
    )

    # 构建约束提示
    constraint = ""
    if phases != PHASES:
        constraint = f"\nScope constraint: only execute these capabilities: {phases}"

    result = coordinator.run(
        task_prompt=(
            f"Generate a PPT from the materials in {workspace.input_dir}. "
            f"Analyze inputs, plan the outline and design, map content, "
            f"generate visuals, assemble the final PPT, and verify quality."
            f"{constraint}"
        ),
    )

    # ... 收集输出路径 ...
```

**验收标准**：

1. `_run_agent_workflow()` 不再将 phases 列表传给 CoordinatorAgent 构造函数
2. CoordinatorAgent.__init__ 不再有 `phases` 参数
3. task_prompt 中包含目标描述，不包含固定 phase 列表
4. until/start_from 约束通过 constraint 字符串注入 task_prompt
5. 确定性模式 (`_run_deterministic_workflow`) 不受影响，保持原样
6. LLM-augmented 模式 (`_run_llm_augmented_workflow`) 不受影响，保持原样

---

### C.5 Phase 5: 集成验证 (P1)

> 端到端验证所有改造的整体效果。

---

#### C.5.1 端到端测试

- **目标文件**：`tests/integration/test_full_pipeline.py`（新建）
- **涉及子系统**：§11 System Integration
- **依赖任务**：C.4.4, C.4.5, C.4.6

**具体工作内容**：

创建端到端集成测试，使用真实 LLM（或足够智能的 mock provider）运行完整 pipeline。

```python
def test_full_pipeline_agent_mode():
    """End-to-end test: agent mode produces final.pptx."""
    # 1. 准备 workspace，放入测试输入文件
    # 2. 调用 run_workflow(job, model_profile="test")
    # 3. 验证 8 个 artifact 都已生成
    # 4. 验证 final.pptx 存在且大小 > 0
    # 5. 验证 history.jsonl 包含完整事件序列
    # 6. 验证 ConversationStore 的 history.jsonl 包含对话记录
```

**验收标准**：

1. 测试使用真实或高质量 mock LLM 运行完整 pipeline
2. `source_summary.json`, `outline.json`, `selected_template.json`, `slide_design_plan.json`, `slide_contents.json`, `image_generation_report.json`, `validation_report.json` 全部存在
3. `final.pptx` 存在且 `stat().st_size > 10000`
4. `history.jsonl` 包含 `phase_started` + `phase_completed` 事件对

---

#### C.5.2 并行执行验证

- **目标文件**：`tests/integration/test_parallel_execution.py`（新建）
- **涉及子系统**：§9 Multi-Agent Orchestration
- **依赖任务**：C.4.4

**具体工作内容**：

验证 Coordinator 能正确并行执行独立的 capabilities。

```python
def test_parallel_outline_and_template():
    """Verify that outline_generation and template_matching can run in parallel."""
    # 1. 预先准备好 source_summary.json
    # 2. 运行 Coordinator（设定 LLM 会选择并行执行 outline + template）
    # 3. 检查两个 worker 的 task 开始时间接近（< 1s 差距）
    # 4. 检查两个 artifact 都已生成
```

**验收标准**：

1. 两个 async worker 的创建时间差 < 2 秒
2. 两个 worker 的 task 状态最终都是 COMPLETED
3. 结果 artifact 都正确生成
4. 无线程安全问题（无 race condition 报错）

---

#### C.5.3 记忆跨会话验证

- **目标文件**：`tests/integration/test_cross_session_memory.py`（新建）
- **涉及子系统**：§2 Memory System
- **依赖任务**：C.2.1

**具体工作内容**：

```python
def test_memory_persists_across_jobs():
    """Verify memory.md is shared across jobs in the same workspace."""
    # 1. 创建 workspace/jobs/job_a/ 和 workspace/jobs/job_b/
    # 2. Job A 运行 dream task，写入 memory insight
    # 3. Job B 调用 build_memory_context()，验证能读到 Job A 的 insight
```

**验收标准**：

1. Job A 写入 memory.md 的内容在 Job B 中可读
2. Session.md 内容不跨 job 泄漏

---

#### C.5.4 压缩验证

- **目标文件**：`tests/integration/test_compression.py`（新建）
- **涉及子系统**：§3 Context Window Management
- **依赖任务**：C.2.2

**具体工作内容**：

```python
def test_compression_triggers_at_threshold():
    """Verify _compress_messages triggers when utilization > 60%."""
    # 1. 创建 AgentLoop 实例，设置小的 context_window_limit（如 4000 tokens）
    # 2. 填入大量消息使 utilization 超过 75%
    # 3. 调用 _compress_messages()
    # 4. 验证 messages 数量减少
    # 5. 验证 system prompt 和最近 2 条消息未被修改
    # 6. 验证错误消息被保留
```

**验收标准**：

1. utilization < 60% 时不触发压缩
2. utilization > 75% 时触发 L2 压缩，messages 数量减少约 50%
3. 压缩后 system prompt 内容不变
4. 压缩后最近 2 条消息内容不变
5. 包含错误标记的消息在 L1-L3 中被保留

---

#### C.5.5 Mailbox 验证

- **目标文件**：`tests/integration/test_mailbox.py`（新建）
- **涉及子系统**：§10 Communication & Context Isolation
- **依赖任务**：C.1.2, C.1.4

**具体工作内容**：

```python
def test_coordinator_sends_message_to_worker():
    """Verify Coordinator can send messages to a running Worker via mailbox."""
    # 1. 启动一个长时间运行的 Worker（多 turn）
    # 2. 在另一个线程中，Coordinator 通过 send_message 发送指令
    # 3. 验证 Worker 在下一个 turn 的 _check_mailbox() 中读到消息
    # 4. 验证消息内容被注入到 Worker 的 messages 中
```

**验收标准**：

1. Coordinator 调用 `send_message` 后，Worker 的 mailbox 文件包含新消息
2. Worker 的 `_check_mailbox()` 在下一个 turn 读取到消息
3. 消息内容作为 `[Message from coordinator]` 格式注入到 Worker 的 messages 列表
4. 读取后消息标记为已读，不会重复注入

---

## Part D: 风险与缓解

---

### D.1 AgentLoop 重写影响面大

| 维度 | 内容 |
|------|------|
| **风险描述** | AgentLoop 是 CoordinatorAgent 和 WorkerAgent 的基类，C.1.2 的重写涉及 `run()`, `_execute_turn()`, `_call_llm()` 三个核心方法以及新增 4 个方法。任何基类行为变化都会影响所有 agent 的执行流程。回归缺陷可能导致 agent 死循环、消息丢失、或压缩误删关键上下文。 |
| **影响范围** | CoordinatorAgent, WorkerAgent, 所有测试用例, 确定性模式（间接影响） |
| **缓解措施** | (1) 保留 `run()` 和 `_execute_turn()` 的现有接口签名不变，只新增方法和扩展内部逻辑。(2) 新增的 `_check_mailbox()`, `_compress_messages()`, `_load_skill_tool()`, `_estimate_tokens()` 都有独立的 enable/disable 开关（如 `self.mailbox` 为 None 时跳过 mailbox 检查）。(3) 确定性模式不走 AgentLoop，完全不受影响。(4) 在重写前先为现有 AgentLoop 补充单元测试，确保回归可检测。 |
| **应急方案** | 如果重写后出现严重问题，回退 `agent_loop.py` 到改动前的版本。新增的 ConversationStore、ToolRegistry 等模块是独立文件，不影响回退。极端情况下可将新功能放到 `AgentLoopV2` 子类中，让 V1 和 V2 共存直到验证完成。 |

---

### D.2 Token 消耗大幅增加

| 维度 | 内容 |
|------|------|
| **风险描述** | Worker 从"单次 compose_artifact"变为"多轮工具交互"，每个 turn 都有 LLM 调用。8 个 Worker 平均 8 turns = 64 次 LLM 调用（当前约 16 次）。加上 skill 内容注入（SKILL.md 通常 3000-8000 字符）和消息历史累积，单个 job 的 token 消耗可能从 ~50K 增加到 ~200K-400K。 |
| **影响范围** | 运行成本, API 速率限制, 响应延迟 |
| **缓解措施** | (1) 每种 capability 设置合理的 `max_turns`（8-15），不给过多 budget。(2) 工具返回精简结果：`_read_artifact` 对大 artifact 返回 preview + keys 而非全文。(3) `_compress_messages()` 在 L2 时触发，及时回收旧对话的 token。(4) ConversationStore 保留完整历史，但 `self.messages` 只保留压缩后的版本。(5) 对 skill 内容做摘要注入（只注入 SKILL.md 的关键部分而非全文）。 |
| **应急方案** | 如果 token 消耗过高，降低 `max_turns` 到 5，或切换到更便宜的 LLM（如 DeepSeek）。极端情况下在特定 capability 上强制使用确定性回退（跳过 LLM loop，直接调用 fallback_module）。 |

---

### D.3 异步执行的线程安全

| 维度 | 内容 |
|------|------|
| **风险描述** | C.4.4 引入 `ThreadPoolExecutor` 支持并行 Worker 执行。多个 Worker 同时运行时可能出现：(1) 多个 Worker 同时写同一个 artifact 文件导致数据损坏；(2) EventBus 的 `_dispatch` 方法在多线程下可能有竞态条件；(3) TaskManager 的 `tasks` 字典在并发修改时可能不一致。 |
| **影响范围** | 并行 Worker 执行, artifact 完整性, 事件丢失 |
| **缓解措施** | (1) Worker 之间通过文件系统（artifact）通信，不共享内存状态。每个 Worker 写不同的 artifact（由 capability 定义），不存在写冲突。(2) artifact 写入使用 `atomic_write_json()`（先写 .tmp 再 `os.replace`），保证原子性。(3) EventBus 已有 per-consumer 背压，`_dispatch` 中的 `consumer.handle()` 互不依赖。(4) LLM client 的每次调用是独立 HTTP 请求，无共享状态。(5) ConversationStore 用 append-only JSONL（单 writer，线程安全）。(6) TaskManager 加 `threading.Lock` 保护 `tasks` 字典的读写。 |
| **应急方案** | 如果并行执行出现线程安全问题，将 `ThreadPoolExecutor(max_workers=3)` 改为 `max_workers=1`（实质退化为串行，但保留异步接口）。在定位和修复竞态条件后恢复并行度。 |

---

### D.4 工具实现工作量大（40+ 工具）

| 维度 | 内容 |
|------|------|
| **风险描述** | 8 种 capability 各自有专属工具，加上 5 个通用工具，总计约 40 个工具需要实现。每个工具需要：ToolDescriptor 定义、executor 实现、RBAC 配置、错误处理。这是一个大量的体力活，容易出现遗漏或不一致。 |
| **影响范围** | C.4.3 的 9 个文件, 开发周期 |
| **缓解措施** | (1) `common.py` 覆盖 5 个通用工具（所有 capability 共享），减少约 40 个工具中的 5x8=40 个重复实现到 5 个。(2) 大部分领域工具是现有 `worker_agent.py` 中 `_xxx` 方法的直接迁移，不需要从零编写。(3) 很多工具实现非常简单（`check_file_exists`、`list_directory` 不到 20 行）。(4) 按 capability 优先级实现：先做 document_analysis 和 ppt_assembly（pipeline 入口和出口），中间的 capability 用 fallback module 兜底。 |
| **应急方案** | 对于时间紧迫的 capability，暂不实现其 tool_impls 模块。ToolFactory 在 `import_module` 失败时会跳过领域工具注册，Worker 只有通用工具可用 —— 此时 LLM 无法完成复杂任务，自动回退到确定性 fallback。这意味着即使 tool_impls 只实现了一半，系统仍然能通过 fallback 运行完整 pipeline。 |

---

### D.5 RAG pipeline 从未真正运行

| 维度 | 内容 |
|------|------|
| **风险描述** | 现有 RAG 代码（query_router, BM25, vector, RRF, reranker）虽然存在，但从未在知识库检索场景下端到端运行过。摄入 pipeline (C.3.1) 是新写的代码，可能存在：(1) 分块逻辑对不同文件类型的兼容性问题；(2) BM25 索引在大量 chunks 下的性能问题；(3) RRF 融合在只有 BM25 路径时的退化行为；(4) 向量后端（local TF-IDF）的检索质量不足。 |
| **影响范围** | C.3.1, C.3.2, C.3.3, template_matching 质量 |
| **缓解措施** | (1) 先用 BM25-only 模式跑通完整 pipeline，再逐步加入 vector + RRF + reranking。(2) 知识库先只索引用户的输入材料和模板 meta，不索引外部文档（控制数据量）。(3) 为 query_router 的 `query()` 函数增加 `methods` 参数的默认值 `["bm25"]`，确保单路检索始终可用。(4) 在 C.3.3 中将 `use_enhancement` 和 `use_reranking` 默认设为 False，确保不强制依赖 LLM。 |
| **应急方案** | 如果 RAG 质量不达标，template_matching 可以继续使用现有的纯 BM25 检索（当前行为），`search_knowledge_base` 工具返回降级结果并提示"knowledge base not available"。Phase 3 整体可以推迟到 Phase 4 之后实施，不阻塞核心 pipeline。 |

---

### D.6 PPT 组装质量不可控

| 维度 | 内容 |
|------|------|
| **风险描述** | PPT 组装 (`ppt_writer.py`) 的质量高度依赖上游 artifact 的格式正确性。当 LLM 生成的 `slide_contents.json` 包含不规范的 zone position、缺失字段、或超长文本时，组装出的 PPT 可能出现：(1) 文本溢出文本框；(2) 背景图与文字重叠不可读；(3) 空白幻灯片；(4) 图表数据格式错误。由于 Worker 重写后 slide_contents 由 LLM 多轮交互产出（而非确定性模块），内容不可预测性增加。 |
| **影响范围** | 最终 PPT 质量, 用户体验 |
| **缓解措施** | (1) `quality_verification` capability 作为 pipeline 最后一步，专门检查 PPT 质量问题。(2) `validate_output` 工具在 content_mapping 阶段检查 slide_contents 的格式完整性（必需字段、zone position 范围、文本长度限制）。(3) `ppt_writer.py` 中已有回退逻辑（无背景图时用纯色背景 + 深色文字），保证最差情况下也能产出可用 PPT。(4) `layout_fit.py` 中的 `title_font_size()` 和 `bullet_font_size()` 根据文本长度自适应字号。(5) content_mapping capability 的 system prompt 中明确约束 zone position 的取值范围（0.0-1.0）和文本长度上限。 |
| **应急方案** | 如果 LLM 产出的 slide_contents 质量不稳定，在 ppt_assembly 之前增加一个确定性的 schema 验证步骤（`jsonschema.validate()`），不合格的 slide 使用 content_mapper 确定性模块重新生成。极端情况下，ppt_assembly capability 直接使用 fallback module（确定性 ppt_assembler），跳过 LLM 环节。 |
