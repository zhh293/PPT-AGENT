# PPT-Agent 编排结构文档

> 基于源码分析，本文档描述 PPT-Agent 的多 Agent 编排架构——包括执行模式、核心组件、数据流与生命周期。

---

## 目录

1. [架构总览](#1-架构总览)
2. [三种执行模式](#2-三种执行模式)
3. [核心组件详解](#3-核心组件详解)
4. [8 大能力 (Capability) 定义](#4-8-大能力-capability-定义)
5. [Agent Loop: Think→Act→Observe 循环](#5-agent-loop-thinkactobserve-循环)
6. [Coordinator-Worker 编排](#6-coordinator-worker-编排)
7. [Tool 与权限体系](#7-tool-与权限体系)
8. [Skill 渐进加载系统](#8-skill-渐进加载系统)
9. [上下文隔离与通信机制](#9-上下文隔离与通信机制)
10. [事件总线](#10-事件总线)
11. [数据流全景](#11-数据流全景)
12. [上下文窗口管理](#12-上下文窗口管理)

---

## 1. 架构总览

```
                        ┌─────────────────────────┐
    用户输入             │    CLI / Web Server      │
    ─────────           │    (cli.py / app.py)     │
                        └───────────┬─────────────┘
                                    │
                                    ▼
                        ┌─────────────────────────┐
                        │  workflow.run_workflow() │
                        │  (coordinator/workflow) │
                        │                         │
                        │  三模式调度：             │
                        │  • Agent Mode            │
                        │  • LLM-Augmented         │
                        │  • Deterministic         │
                        └───────────┬─────────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            │                       │                       │
            ▼                       ▼                       ▼
    ┌──────────────┐       ┌──────────────┐       ┌──────────────┐
    │ Coordinator  │──────►│WorkerAgent ×8│──────►│  最终输出    │
    │   Agent      │       │ think→act→   │       │  final.pptx  │
    │ (纯编排)     │       │  observe     │       │              │
    └──────────────┘       └──────────────┘       └──────────────┘
```

系统以 **Coordinator-Worker** 模式组织多 Agent 协作：

- **CoordinatorAgent**：纯编排角色，拥有 6 个编排工具（spawn/wait/review/...），自身不操作文件或执行业务逻辑。
- **WorkerAgent**：每个 PPT 流水线阶段一个 Worker，拥有该阶段专属工具集，在 think→act→observe 循环中自主完成任务。

核心代码路径：

| 文件 | 职责 |
|------|------|
| `src/ppt_agent/coordinator/workflow.py` | 总调度入口，三模式分发 |
| `src/ppt_agent/coordinator/coordinator_agent.py` | Coordinator 实现 |
| `src/ppt_agent/coordinator/worker_agent.py` | Worker 实现 |
| `src/ppt_agent/coordinator/capabilities.py` | 8 大能力注册表 |
| `src/ppt_agent/runtime/agent_loop.py` | 抽象基类：think→act→observe |
| `src/ppt_agent/coordinator/event_bus.py` | 发布/订阅事件总线 |
| `src/ppt_agent/coordinator/tool_factory.py` | 按能力组装工具集 |
| `src/ppt_agent/runtime/agent_context.py` | 双层上下文隔离 |

---

## 2. 三种执行模式

`run_workflow()` 根据是否有 LLM 客户端以及 profile 前缀，选择三种执行模式：

```
run_workflow(job, model_profile=...)
    │
    ├── model_profile 以 "agent:" 开头？
    │   └── YES ──►  AGENT MODE
    │               CoordinatorAgent 自主循环 → 动态 spawn WorkerAgent
    │               （完全自主决策，LLM 驱动的 think→act→observe）
    │
    ├── model_profile 其他值？
    │   └── YES ──►  LLM-AUGMENTED DETERMINISTIC MODE
    │               流水线线性执行，但各 Worker 函数接收 llm_client
    │               （LLM 辅助分析，但编排是确定性的）
    │
    └── model_profile 未提供？
        └── ──►  DETERMINISTIC MODE
                纯线性 for 循环，Worker 函数不调用 LLM
                （完全确定性，向后兼容）
```

### 2.1 Agent Mode（完全自主编排）

```python
# workflow.py::_run_agent_workflow()
coordinator = CoordinatorAgent(workspace, llm_client, ...)
coordinator.run(task_prompt=(
    "Execute the PPT generation pipeline phases in order: "
    "document_analysis, outline_generation, ..."
))
```

- Coordinator 自行决定何时 spawn 哪个 Worker
- 支持异步并行（`spawn_agent(async=true)`）
- Worker 运行自己的 agent loop，失败则 fallback 到确定性 Worker 函数

### 2.2 LLM-Augmented Mode（LLM 增强确定性）

```python
# workflow.py::_run_llm_augmented_workflow()
for phase in phases:
    _inject_phase_skill(skill_loader, llm_client, phase)
    output = PHASE_TO_WORKER[phase](workspace, force=force, llm_client=llm_client)
```

- 按固定顺序执行 8 个阶段
- 需要 LLM 的阶段（document_analysis、outline_generation、design_planning、content_mapping、verification）传入 `llm_client`
- 每个阶段前按需加载对应 Skill（progressive disclosure）

### 2.3 Deterministic Mode（纯确定性）

```python
# workflow.py::_run_deterministic_workflow()
for phase in phases:
    output = PHASE_TO_WORKER[phase](workspace, force=force)
```

- 完全不依赖 LLM
- 使用硬编码逻辑和模板匹配
- 最稳定但智能程度最低

---

## 3. 核心组件详解

### 3.1 AgentLoop（抽象基类）

```
src/ppt_agent/runtime/agent_loop.py
```

所有 Agent 的基类，实现 **Think → Act → Observe** 循环：

```
┌──────────────────────────────────────────────────────────┐
│                     AgentLoop.run()                       │
│                                                          │
│  初始化 messages = [system_prompt] + [user_task+context]  │
│                                                          │
│  FOR turn_num = 1 ... max_turns:                          │
│    ┌──────────┐                                          │
│    │  THINK   │  _call_llm() → LLMResult                  │
│    │          │  parse_llm_response() → (text, tools, done)│
│    └────┬─────┘                                          │
│         │                                                │
│         ├── done=true? ──► 结束 (COMPLETED)               │
│         ├── tools=[]?   ──► 结束 (NO_ACTION)              │
│         │                                                │
│         ▼                                                │
│    ┌──────────┐                                          │
│    │  ACT     │  FOR each tool_call:                      │
│    │          │    execute_tool(tc) → ToolResult          │
│    └────┬─────┘                                          │
│         │                                                │
│         ▼                                                │
│    ┌──────────┐                                          │
│    │ OBSERVE  │  将 ToolResult 注入 messages              │
│    │          │  错误时提供 self-correction 反馈           │
│    └──────────┘                                          │
│                                                          │
│  每次迭代前：_check_mailbox() + _compress_messages()      │
└──────────────────────────────────────────────────────────┘
```

**关键机制：**

- **自纠正 (Self-Correction)**：工具错误被注入消息历史，Agent 可重试修复
- **Fallback**：Worker 的 agent loop 失败后自动降至确定性 Worker 函数
- **终止条件**：DONE 信号 / 达到 max_turns / 连续错误超限 / 外部 abort

**抽象方法（子类必须实现）：**

| 方法 | 说明 |
|------|------|
| `build_system_prompt()` | Agent 的身份、能力描述、工作流指引 |
| `get_available_tools()` | 返回该 Agent 可用的工具描述列表 |
| `execute_tool(tool_call)` | 实际执行工具调用 |
| `parse_llm_response(result)` | 从 LLM 输出中提取 (thought, tool_calls, done) |

### 3.2 CoordinatorAgent

```
src/ppt_agent/coordinator/coordinator_agent.py
```

**角色定义：** 纯编排者——**绝不**直接操作文件、执行代码或搜索。只负责分解任务、派发 Worker、收集结果。

**6 个编排工具：**

| 工具 | 功能 |
|------|------|
| `spawn_agent` | 为指定 capability 生成 WorkerAgent（支持 async 异步并行） |
| `check_artifacts` | 检查 workspace 中哪些 artifact 已生成 |
| `wait_agents` | 等待异步生成的 Worker 完成 |
| `review_result` | 查看已完成 Worker 的输出 |
| `request_user_review` | 暂停流程，等待用户审核 slide_contents |
| `synthesize_output` | 汇总所有阶段结果，生成最终输出 |

**内部状态：**

```
CoordinatorAgent
├── workspace: JobWorkspace          # 作业工作区
├── task_manager: TaskManager        # 任务生命周期管理
├── event_bus: EventBus             # 事件发射
├── skill_loader: SkillLoader       # 渐进式 Skill 加载
├── mailbox: Mailbox                # 跨 Agent 通信
├── _executor: ThreadPoolExecutor   # 异步 Worker 执行（max_workers=3）
├── _futures: dict[str, Future]     # 异步任务追踪
├── _worker_results: dict           # Worker 执行结果汇总
└── _completed: list[str]           # 已完成的 capability IDs
```

**上下文传递：** `_build_phase_context()` 方法根据当前阶段自动加载前序 artifact：

```python
# 例如 content_mapping 阶段需要：
# outline + selected_template + slide_design_plan + source_summary + template_zones
```

### 3.3 WorkerAgent

```
src/ppt_agent/coordinator/worker_agent.py
```

每个 Worker 绑定一个 `AgentCapability`，执行 PPT 流水线的一个阶段。

**三种 Worker 类别：**

| 类别 | 阶段 | 工具集特征 |
|------|------|-----------|
| **content_reasoning** | document_analysis, outline_generation, design_planning, content_mapping, verification | read/write/compose/validate 认知工具 |
| **operation** | template_matching, ppt_assembly | search/assemble/check 操作工具 |
| **skill_autonomous** | visual_generation | run_shell/check_file/list_dir 自主 Shell 工具 |

**标准 7 步工作流** (content_reasoning 类)：

```
1. READ    — read_artifact / read_input_files 加载输入
2. REASON  — 理解数据，考虑领域、受众、语调
3. COMPOSE — compose_artifact 生成结构化 JSON
4. REVIEW  — 检查字段完整性、内容一致性
5. WRITE   — write_artifact 写入 workspace
6. VALIDATE — validate_output 验证格式正确性
7. DONE    — 发出完成信号
```

**Fallback 机制：**

```python
def run(self, task_prompt, context):
    result = super().run(task_prompt, context)  # Agent loop
    if result.stop_reason == COMPLETED:
        return result
    # Agent loop 失败 → 降到确定性 Worker 函数
    return self._fallback_execution(result)
```

---

## 4. 8 大能力 (Capability) 定义

```
src/ppt_agent/coordinator/capabilities.py
```

每个 `AgentCapability` 是一个不可变数据类，声明式定义流水线阶段：

```python
@dataclass(frozen=True)
class AgentCapability:
    capability_id: str        # 唯一标识
    description: str          # 能力描述
    input_artifacts: list[str]   # 依赖的前序 artifact
    output_artifacts: list[str]  # 产出的 artifact
    tools: list[str]            # 允许使用的工具名
    max_turns: int = 10         # Agent loop 最大轮次
    fallback_module: str        # 确定性 fallback 模块路径
    reads_input_dir: bool       # 是否读取原始输入文件
    skill_name: str             # 关联的 Skill 名称
```

### 8 大能力流水线：

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Phase 1                Phase 2                Phase 3                   │
│  ┌────────────────┐    ┌────────────────┐    ┌────────────────────┐     │
│  │DOCUMENT_ANALYSIS│───►│OUTLINE_GENERATION│──►│TEMPLATE_MATCHING   │     │
│  │                │    │                │    │                    │     │
│  │ 输入: 原始文件  │    │ 输入: source_  │    │ 输入: outline      │     │
│  │ 输出: source_   │    │       summary  │    │ 输出: selected_    │     │
│  │       summary   │    │ 输出: outline  │    │  template,         │     │
│  │                │    │                │    │  template_meta,     │     │
│  │ max_turns: 8   │    │ max_turns: 10  │    │  template_zones    │     │
│  │ skill: -       │    │ skill: ppt-    │    │                    │     │
│  │                │    │  outline-      │    │ max_turns: 8       │     │
│  │                │    │  generator     │    │ skill: ppt-        │     │
│  └────────────────┘    └────────────────┘    │  template-matcher  │     │
│                                              └─────────┬──────────┘     │
│                                                        │                │
│  ┌────────────────────┐                               │                │
│  │DESIGN_PLANNING     │◄──────────────────────────────┘                │
│  │                    │                                                │
│  │ 输入: outline,     │                                                │
│  │       template_meta│                                                │
│  │ 输出: slide_design_│                                                │
│  │       plan         │                                                │
│  │ max_turns: 10      │                                                │
│  │ skill: ppt-design- │                                                │
│  │  director          │                                                │
│  └─────────┬──────────┘                                                │
│            │                                                           │
│            ▼                                                           │
│  ┌────────────────────┐    ┌──────────────────┐    ┌──────────────┐   │
│  │CONTENT_MAPPING     │───►│VISUAL_GENERATION │───►│PPT_ASSEMBLY  │   │
│  │                    │    │                  │    │              │   │
│  │ 输入: outline,     │    │ 输入: slide_     │    │ 输入: slide_ │   │
│  │  selected_template,│    │  contents,       │    │  contents    │   │
│  │  slide_design_plan,│    │  template_zones  │    │ 输出: final. │   │
│  │  source_summary,   │    │ 输出: image_     │    │  pptx        │   │
│  │  template_zones    │    │  generation_     │    │              │   │
│  │ 输出: slide_       │    │  report          │    │ max_turns: 8 │   │
│  │  contents          │    │                  │    │ skill: ppt-  │   │
│  │                    │    │ max_turns: 15    │    │  assembler   │   │
│  │ max_turns: 12      │    │ skill: gptimage2-│    └──────┬───────┘   │
│  │ skill: ppt-content-│    │  generator       │           │           │
│  │  mapper            │    │                  │           │           │
│  └────────────────────┘    └──────────────────┘           │           │
│                                                           ▼           │
│                                              ┌──────────────────────┐ │
│                                              │QUALITY_VERIFICATION  │ │
│                                              │                      │ │
│                                              │ 输入: slide_contents,│ │
│                                              │  image_generation_   │ │
│                                              │  report              │ │
│                                              │ 输出: validation_    │ │
│                                              │  report              │ │
│                                              │ max_turns: 8         │ │
│                                              └──────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

### 依赖关系：

```
document_analysis ─────┬──► outline_generation ──┬──► template_matching
                       │                         │
                       │                         ├──► design_planning
                       │                         │
                       │                         └──► content_mapping
                       │                                   │
                       │                         ┌─────────┘
                       │                         ▼
                       │              visual_generation
                       │                         │
                       └─────────────► ppt_assembly
                                              │
                                              ▼
                                    quality_verification
```

**并行机会：**
- `outline_generation` 和 `template_matching` 可在 `document_analysis` 后并行
- `design_planning` 可与 `template_matching` 并行（都需要 outline）
- `visual_generation` 中每页图片生成可并行（并发限制 2-3）

---

## 5. Agent Loop: Think→Act→Observe 循环

### 5.1 循环状态机

```
                    ┌──────┐
                    │ IDLE │
                    └──┬───┘
                       │ run()
                       ▼
                 ┌──────────┐
            ┌───►│ THINKING │◄──────────────┐
            │    └────┬─────┘               │
            │         │ LLM 返回             │
            │         ▼                     │
            │    ┌──────────┐               │
            │    │  done?   │── YES ──► DONE│
            │    └────┬─────┘               │
            │         │ NO                  │
            │         ▼                     │
            │    ┌──────────┐               │
            │    │  ACTING  │               │
            │    └────┬─────┘               │
            │         │ 工具执行完成          │
            │         ▼                     │
            │    ┌──────────┐               │
            └────│OBSERVING │               │
                 └──────────┘               │
                       │                    │
                       └────────────────────┘
                          (下一轮 turn)
```

### 5.2 消息压缩

每轮 LLM 调用前检查消息量，超过阈值触发 L0-L4 分级压缩：

| 级别 | 触发条件 | 策略 | 信息损失 |
|------|---------|------|---------|
| L0 | < 60% 容量 | 无压缩 | 无 |
| L1 | 60-75% | 截断大型工具输出（>2000字符） | 低 |
| L2 | 75-85% | LLM 总结前半段对话 | 中 |
| L3 | 85-95% | 保留最近 4 条消息 + 错误消息 | 高 |
| L4 | > 95% | 仅保留系统 prompt + 最后 2 条 | 极高 |

**压缩不变量：** 系统 prompt、内存层、错误记录、当前工具链结果、最新用户/助手消息永不压缩。

### 5.3 审计日志

每次 LLM 调用自动写入 `model_calls.jsonl`（Phase 3 审计跟踪）：

```python
# agent_loop.py::_log_model_call()
log_model_call(job_root, phase=agent_id, result=llm_result, prompt_summary=...)
```

---

## 6. Coordinator-Worker 编排

### 6.1 Coordinator 的决策循环

```
CoordinatorAgent.run(task_prompt, context)
    │
    ├── 构建 system_prompt (包含 8 大能力描述、依赖规则、并行机会)
    │
    ├── THINK: LLM 分析当前状态 → 决定下一步
    │
    ├── ACT:
    │   ├── spawn_agent(capability="document_analysis")
    │   ├── spawn_agent(capability="outline_generation", async=true)
    │   ├── wait_agents(task_ids=["..."])
    │   ├── check_artifacts()
    │   ├── request_user_review(message="请审核 slide_contents")
    │   └── synthesize_output(summary="...")
    │
    └── OBSERVE: 收集结果 → _worker_results / _completed
```

### 6.2 Worker 的 spawn 与执行

```python
# coordinator_agent.py::_spawn_agent()
def _spawn_agent(capability_id, instructions, is_async):
    capability = get_capability(capability_id)       # 从注册表获取
    task = task_manager.create(TaskType.LOCAL_AGENT)  # 创建任务记录
    task_manager.transition(task.task_id, RUNNING)

    if is_async:
        future = _executor.submit(_run_worker, capability, task, instructions)
        _futures[task.task_id] = future
        return {"task_id": ..., "status": "running_async"}

    return _run_worker(capability, task, instructions)  # 同步执行
```

### 6.3 Worker 执行细节

```python
# coordinator_agent.py::_run_worker()
def _run_worker(capability, task, instructions):
    # 1. 加载关联 Skill
    if capability.skill_name:
        skill_content = skill_loader.load_skill(capability.skill_name)

    # 2. 创建专属 ToolRegistry (RBAC)
    factory = ToolFactory(workspace)
    registry = factory.create_registry_for_capability(capability)

    # 3. 创建 WorkerAgent
    worker = WorkerAgent(workspace, capability, llm_client, registry=registry)

    # 4. 双层上下文隔离中执行
    with teammate_scope(TeammateContext(...)):
        with agent_scope(AgentContext(agent_id=f"worker-{capability_id}")):
            result = worker.run(task_prompt, context)

    # 5. 收集结果
    _worker_results[capability_id] = {...}
    _completed.append(capability_id)
```

---

## 7. Tool 与权限体系

### 7.1 工具注册与创建

```
ToolFactory(workspace)
    │
    ├── _register_common()    ← tool_impls/common.py
    │   ├── read_artifact
    │   ├── write_artifact
    │   ├── validate_output
    │   ├── search_knowledge_base
    │   └── load_skill
    │
    └── _register_domain()    ← tool_impls/<capability_id>.py
        ├── document_analysis → tool_impls/document_analysis.py
        ├── content_mapping   → tool_impls/content_mapping.py
        ├── ppt_assembly      → tool_impls/ppt_assembly.py
        └── ...
```

### 7.2 按 Worker 类别的工具分配

**Content Reasoning 工具** (document_analysis, outline_generation, design_planning, content_mapping, verification)：

| 工具 | 说明 |
|------|------|
| `read_input_files` | 读取原始输入文件（仅 document_analysis） |
| `read_artifact` | 读取前序阶段的 JSON artifact |
| `compose_artifact` | LLM 提供内容 → 工具生成结构化 JSON |
| `write_artifact` | 将 artifact 写入 workspace |
| `validate_output` | 验证 artifact 存在且格式正确 |

**Operation 工具** (template_matching, ppt_assembly)：

| 工具 | 说明 |
|------|------|
| `read_artifact` | 读取 artifact |
| `search_templates` | 搜索模板索引（template_matching） |
| `assemble_pptx` | 调用 python-pptx 生成 .pptx（ppt_assembly） |
| `check_file` | 检查文件存在/大小 |
| `write_artifact` / `validate_output` | 写入与验证 |

**Skill Autonomous 工具** (visual_generation)：

| 工具 | 说明 |
|------|------|
| `run_shell_command` | 执行 Skill 脚本（带安全沙箱检查） |
| `check_file_exists` | 检查文件存在性 |
| `list_directory` | 列出目录内容 |
| `read_artifact` / `write_artifact` | artifact 读写 |

### 7.3 Shell 沙箱安全

`WorkerAgent._run_shell_command()` 实施三层防御：

1. **模式黑名单** — 阻止 `rm -rf /`、`mkfs`、`dd if=`、fork bomb 等
2. **路径限制** — 工作目录必须在 workspace 内
3. **环境变量白名单** — 仅透传 `PATH`、`HOME`、`LANG` 及 `*_API_KEY`

---

## 8. Skill 渐进加载系统

### 8.1 两阶段加载协议

```
Phase 1: Manifest Injection（启动时）
    ─────────────────────────────────
    系统 prompt 仅包含 Skill 名称和简短描述
    每个 Skill ~50 tokens

    Available Skills:
    - "ppt-outline-generator": 生成 PPT 大纲
    - "ppt-template-matcher": 模板检索与匹配
    - ...

Phase 2: On-Demand Full Loading（运行时）
    ─────────────────────────────────
    Agent 调用 load_skill("ppt-outline-generator")
    → 系统读取 .catpaw/skills/ppt-outline-generator/SKILL.md
    → 完整内容注入系统 prompt
    → 该 Skill 在当前会话中不再重复加载
```

### 8.2 Skill 目录结构

```
.catpaw/skills/
├── ppt-outline-generator/
│   ├── SKILL.md                    # Skill 主定义
│   ├── references/structures.md    # PPT 结构模板库
│   └── examples/outline.example.json
├── ppt-template-matcher/
│   ├── SKILL.md
│   └── examples/template-ranking.example.json
├── ppt-content-mapper/
│   ├── SKILL.md
│   └── examples/slide_contents.example.json
├── ppt-design-director/
│   ├── SKILL.md
│   └── examples/slide_design_plan.example.json
├── ppt-image-layer/
│   └── SKILL.md
├── ppt-assembler/
│   ├── SKILL.md
│   └── examples/final_manifest.example.json
└── gptimage2-generator/
    ├── SKILL.md
    └── references/api-reference.md
```

### 8.3 Skill 与 Capability 的映射

| Capability | Skill |
|-----------|-------|
| outline_generation | ppt-outline-generator |
| template_matching | ppt-template-matcher |
| design_planning | ppt-design-director |
| content_mapping | ppt-content-mapper |
| visual_generation | gptimage2-generator |
| ppt_assembly | ppt-assembler |

---

## 9. 上下文隔离与通信机制

### 9.1 双层上下文隔离

```
┌─────────────────────────────────────────────┐
│              AsyncLocalStorage                │
│                                             │
│  Layer 1: TeammateContext (团队级)            │
│  ┌─────────────────────────────────────────┐│
│  │ workspace_root, event_bus, skill_loader ││
│  │ job_id, team_role                       ││
│  └─────────────────────────────────────────┘│
│                                             │
│  Layer 2: AgentContext (Agent级)             │
│  ┌─────────────────────────────────────────┐│
│  │ agent_id, phase, tool_history,          ││
│  │ turn_count                              ││
│  └─────────────────────────────────────────┘│
│                                             │
│  解析优先级：                                 │
│  1. AgentContext (ContextVar)                │
│  2. TeammateContext (ContextVar)             │
│  3. 环境变量 PPT_AGENT_*                      │
└─────────────────────────────────────────────┘
```

Worker 执行时的隔离设置：

```python
with teammate_scope(TeammateContext(workspace_root=..., event_bus=...,
                                     skill_loader=..., job_id=..., team_role="worker")):
    with agent_scope(AgentContext(agent_id=f"worker-{capability_id}", phase=capability_id)):
        worker.run(task_prompt, context)
```

### 9.2 Mailbox 跨 Agent 通信

```
workspace/.mailbox/
├── coordinator.jsonl      # Coordinator 的收件箱
├── worker-document_analysis.jsonl
├── worker-outline_generation.jsonl
└── ...
```

**消息优先级：**

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 (最高) | Shutdown 请求 | 阻止僵尸 Agent |
| 2 | Team Lead (Coordinator) | 编排指令 |
| 3 | Peer 消息 | Agent 间平等通信 |
| 4 (最低) | 未领取任务列表 | 后台工作 |

**工作流：**
1. 每轮 THINK 前调用 `_check_mailbox()` 检查新消息
2. 按优先级排序，注入到 messages
3. Priority=1 (SHUTDOWN) 直接设置 `_aborted = True`

### 9.3 三层记忆系统

```
agent.md    → 系统身份（不变）
memory.md   → 跨会话知识（Dream 任务异步写入）
session.md  → 当前会话工作记忆（阶段完成后写入）
```

---

## 10. 事件总线

### 10.1 事件类型

```
EventBus (workspace_root)
    │
    ├── 生命周期事件
    │   ├── phase_started / phase_completed
    │   ├── agent_spawn / agent_complete
    │   └── status_change
    │
    ├── 执行事件
    │   ├── tool_call_start / tool_call_result
    │   ├── llm_call_start / llm_call_result
    │   └── text_delta (流式输出)
    │
    ├── 系统事件
    │   ├── artifact_written
    │   ├── skill_loaded
    │   ├── compression_event
    │   ├── memory_update
    │   └── user_input_request
    │
    └── 诊断事件
        ├── warning / error
        └── progress
```

### 10.2 Consumer 体系

| Consumer | 用途 |
|----------|------|
| `FileConsumer` | 写入 `history.jsonl`（自动注册） |
| `LoggingConsumer` | Python logging 输出 |
| `CallbackConsumer` | 用户自定义回调 |
| `SSEConsumer` | FastAPI SSE 推送到前端 |

### 10.3 反压控制

```
每个 Consumer 独立缓冲上限 (max_buffer=1000)
    ├── 未超 → 正常处理
    └── 超限 → 丢弃事件，递增 dropped_events 计数器
                Agent loop 永不阻塞
```

---

## 11. 数据流全景

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           JobWorkspace                                   │
│                                                                          │
│  input/                    artifacts/                output/             │
│  ├── 项目计划书.pdf        ├── source_summary.json   ├── final.pptx      │
│  ├── 软著证书.jpg          ├── outline.json          └── preview/        │
│  └── 功能截图.png          ├── selected_template.json                    │
│                            ├── template_meta.json                        │
│  background_images/        ├── template_zones.json                       │
│  ├── slide_0_bg.png        ├── slide_design_plan.json                    │
│  ├── slide_1_bg.png        ├── slide_contents.json                       │
│  └── ...                   ├── image_generation_config.json              │
│                            ├── image_generation_report.json              │
│  .mailbox/                 └── validation_report.json                    │
│  ├── coordinator.jsonl                                                   │
│  └── worker-*.jsonl        review_pending.json                           │
│                                                                          │
│  history.jsonl             model_calls.jsonl                             │
│  session.md                memory.md                                     │
└──────────────────────────────────────────────────────────────────────────┘
```

### Artifact 流转：

```
document_analysis     → source_summary.json
        │
        ▼
outline_generation    → outline.json
        │
        ├──────────────┬──────────────────┐
        ▼              ▼                  ▼
template_matching  design_planning    [供 content_mapping]
  → selected_template  → slide_design_plan
  → template_meta
  → template_zones
        │              │
        └──────┬───────┘
               ▼
content_mapping      → slide_contents.json  [⏸ 用户审核]
               │
               ├──────────────┐
               ▼              ▼
visual_generation    ppt_assembly
  → image_gen_report   → final.pptx
               │              │
               └──────┬───────┘
                      ▼
quality_verification  → validation_report.json
```

---

## 12. 上下文窗口管理

### 12.1 消息生命周期

```
新对话:
  [System Prompt] = agent.md + memory.md + session.md
                  + Skill manifest (仅名称，~50 tokens/skill)

运行时:
  + User message (task + context)
  + Assistant message (thought)
  + Tool call → Tool result 对

压缩时:
  保留: System prompt, memory layers, error messages, active tool results
  压缩: 早期详细输出 → 摘要
```

### 12.2 压缩决策树

```
估算 token 使用量
    │
    ├── < 60%  → L0: 不压缩
    ├── 60-75% → L1: 截断 >2000 字符的工具输出
    ├── 75-85% → L2: LLM 摘要前半段
    ├── 85-95% → L3: 仅保留 system + error + 最近 4 条
    └── > 95%  → L4: 仅保留 system + 最近 2 条
```

---

## 附录 A: 关键文件索引

```
src/ppt_agent/
├── coordinator/
│   ├── workflow.py            # 总调度入口
│   ├── coordinator_agent.py   # Coordinator Agent 实现
│   ├── worker_agent.py        # Worker Agent 实现
│   ├── capabilities.py        # 8 大能力注册表
│   ├── dispatcher.py          # 任务派发
│   ├── event_bus.py           # 事件总线
│   ├── tool_factory.py        # 工具工厂
│   ├── phase_state.py         # Artifact 读写
│   ├── routing_rules.py       # 路由规则
│   └── tool_impls/            # 按能力划分的工具实现
│       ├── common.py
│       ├── document_analysis.py
│       ├── outline_generation.py
│       ├── template_matching.py
│       ├── design_planning.py
│       ├── content_mapping.py
│       ├── visual_generation.py
│       ├── ppt_assembly.py
│       └── quality_verification.py
├── runtime/
│   ├── agent_loop.py          # AgentLoop 抽象基类
│   ├── agent_context.py       # 双层上下文隔离
│   ├── task_manager.py        # 任务生命周期管理
│   ├── task_types.py          # 7 种任务类型定义
│   ├── coordinator_tools.py   # Coordinator 专用工具
│   ├── mailbox.py             # 跨 Agent 文件邮箱
│   └── conversation_store.py  # JSONL 对话持久化
├── context/
│   ├── compression.py         # L0-L4 上下文压缩
│   ├── memory_layers.py       # 三层记忆系统
│   ├── session_summary.py     # 会话摘要
│   └── dream.py              # Dream 记忆巩固
├── skills/
│   ├── loader.py              # Skill 加载器
│   └── registry.py            # Skill 发现
├── tools/
│   ├── registry.py            # 工具注册表 (RBAC)
│   └── ...
├── workers/                   # 确定性 Worker 函数
│   ├── document_analyst.py
│   ├── outline_generator.py
│   ├── template_matcher.py
│   ├── design_director.py
│   ├── content_mapper.py
│   ├── image_generator.py
│   ├── ppt_assembler.py
│   └── ppt_verifier.py
├── models/                    # 数据模型 (Pydantic)
├── llm/                       # LLM 客户端与 Provider
├── retrieval/                 # RAG 检索管线
└── assembly/                  # PPT 组装模块
```

## 附录 B: 任务类型

| TaskType | 前缀 | 执行环境 | 用途 |
|----------|------|---------|------|
| `local_bash` | `b-` | 后台 Shell 进程 | 长时构建、测试套件 |
| `local_agent` | `a-` | 同步/异步子 Agent | Worker 执行 |
| `remote_agent` | `r-` | 远程会话 | 需完全隔离的重任务 |
| `in_process_teammate` | `t-` | 进程内 (AsyncLocalStorage) | 团队协作模式 |
| `local_workflow` | `w-` | 工作流引擎 | Coordinator 编排 |
| `monitor_mcp` | `m-` | 被动监控 | 外部服务健康检查 |
| `dream` | `d-` | 后台低优先级 | 记忆巩固 |

## 附录 C: 设计原则

1. **复杂度集中在关键路径** — 检索质量、上下文工程、编排策略使用复杂方案；基础设施极简化
2. **文件系统优先** — 所有持久化状态使用 Markdown + JSONL，人类可读，Git 友好
3. **渐进式信息披露** — Skill 先加载名称，用时再加载完整内容；Memory 分层注入；上下文按需压缩
4. **默认隔离** — 每个执行单元在明确定义的边界内运行（沙箱文件系统、AsyncLocalStorage、文件邮箱）
5. **自纠正与降级** — Agent loop 失败自动 fallback 到确定性逻辑；API 不可用时降级为模板填充模式

---

> *文档生成时间: 2026-07-08*
> *基于源码版本: feature-mvp1*
