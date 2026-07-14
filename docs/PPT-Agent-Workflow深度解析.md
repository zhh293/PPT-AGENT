# PPT-Agent Workflow 深度解析文档

> 基于源码逐行分析，涵盖三种执行模式、8 阶段流水线、每步机制、以及"是否真的被使用"的审计结论。

---

## 目录

1. [入口：run_workflow() 总调度](#1-入口run_workflow-总调度)
2. [三种执行模式的真相](#2-三种执行模式的真相)
3. [模式一：Deterministic Mode（纯确定性）](#3-模式一deterministic-mode纯确定性)
4. [模式二：LLM-Augmented Deterministic Mode（LLM增强确定性）](#4-模式二llm-augmented-deterministic-modellm增强确定性)
5. [模式三：Agent Mode（完全自主编排）——⚠️ 死代码](#5-模式三agent-mode完全自主编排-死代码)
6. [8 个 Pipeline 阶段详解](#6-8-个-pipeline-阶段详解)
7. [支撑系统详解](#7-支撑系统详解)
8. [代码使用率审计](#8-代码使用率审计)
9. [数据流全景图](#9-数据流全景图)
10. [附录：关键文件调用链](#10-附录关键文件调用链)

---

## 1. 入口：run_workflow() 总调度

**文件：** `src/ppt_agent/coordinator/workflow.py`

```python
def run_workflow(
    job: Path | JobWorkspace,
    until: str | None = None,
    start_from: str | None = None,
    force: bool = False,
    model_profile: str | None = None,
) -> list[Path]:
```

### 调用来源（实际生效的入口）

| 调用方 | 文件 | model_profile 值 | 实际走哪个模式 |
|--------|------|-----------------|--------------|
| **Web Server** | `src/ppt_agent/server/app.py:239` | `"deepseek"`（默认值） | LLM-Augmented |
| **CLI `run`** | `src/ppt_agent/cli.py:19` | `None`（默认值） | Deterministic |
| **测试** | `tests/integration/test_e2e_agent_pipeline.py:17` | `"fake"` | LLM-Augmented (fake provider) |

### 初始化流程（所有模式共享）

```
run_workflow(job, model_profile)
  │
  ├─ 1. 解析 workspace（JobWorkspace 包装）
  │     workspace.ensure()  → 创建 input/、generated_slides/、background_images/ 等目录
  │
  ├─ 2. 解析 until / start_from 参数
  │     "content-review" → "content_mapping"
  │     "final"          → "verification"
  │     "visual-generation" → "visual_generation"
  │     确定 active_phases = PHASES[start_idx : end_idx + 1]
  │
  ├─ 3. 初始化三层记忆系统
  │     ensure_memory_files(workspace.root)
  │     → 创建 .ppt_agent/agent.md, session.md
  │
  ├─ 4. 初始化事件总线
  │     bus = EventBus(workspace.root, auto_file_consumer=True)
  │     → 自动注册 FileConsumer（写入 history.jsonl）
  │     bus.subscribe(LoggingConsumer())
  │     → 注册日志消费者
  │
  ├─ 5. 创建 LLM 客户端（仅当 model_profile 提供时）
  │     llm_client = _create_llm_client(model_profile, workspace.root)
  │     → 加载 config/models.yml → 创建 Provider → 包装为 LLMClient
  │     → 失败时返回 None，自动降级为 Deterministic Mode
  │
  └─ 6. 模式分发（详见第2节）
```

---

## 2. 三种执行模式的真相

`workflow.py` 中 **定义了三个函数**，但**只有两个被实际调用**：

```
run_workflow()
    │
    ├── llm_client is not None?
    │   └── YES ──► _run_llm_augmented_workflow()    ← ✅ 实际使用
    │               （LLM 增强确定性模式）
    │
    └── llm_client is None?
        └── ──► _run_deterministic_workflow()          ← ✅ 实际使用
                （纯确定性模式）

_run_agent_workflow()                                   ← ⚠️ 死代码！
    定义了但从未被 run_workflow() 调用。
    需要 "agent:" 前缀判断逻辑才能触发，但该逻辑从未实现。
```

### 关键代码证据

`workflow.py:167-185`：
```python
llm_client = None
if model_profile:
    llm_client = _create_llm_client(model_profile, workspace.root)

if llm_client is not None:
    # 注释说 "Agent mode 通过 model_profile='agent:<name>' 可用"
    # 但实际上直接走了 LLM-Augmented，没有任何 prefix 检查
    return _run_llm_augmented_workflow(
        workspace, llm_client, bus, active_phases, force,
    )
else:
    return _run_deterministic_workflow(
        workspace, bus, active_phases, force,
    )
```

**结论：** `_run_agent_workflow()` 函数及其所有依赖（CoordinatorAgent、WorkerAgent 的 agent-loop 路径、TaskManager、Mailbox、ToolFactory、ToolRegistry）都是**已编写但从未被调用的死代码**。

---

## 3. 模式一：Deterministic Mode（纯确定性）

**函数：** `_run_deterministic_workflow()`  
**触发条件：** `model_profile` 未提供（CLI 默认）或 LLM 客户端创建失败

### 执行流程

```
for phase in active_phases:
    │
    ├─ 1. 发射 PHASE_STARTED 事件
    │     bus.emit(EventType.PHASE_STARTED, phase=phase)
    │
    ├─ 2. 调用 Worker 函数（不带 llm_client）
    │     output = PHASE_TO_WORKER[phase](workspace, force=force)
    │     每个 Worker 内部执行确定性逻辑（硬编码模板、BM25 检索、简单规则）
    │
    ├─ 3. 收集输出路径
    │     outputs.append(output)
    │
    ├─ 4. 写入 session.md
    │     append_phase_summary(workspace.root, phase, ...)
    │
    ├─ 5. 发射 PHASE_COMPLETED 事件
    │     bus.emit(EventType.PHASE_COMPLETED, phase=phase)
    │
    └─ 6. Review Gate（仅 content_mapping 之后）
          if phase == "content_mapping":
              _check_review_gate(workspace, bus, force, outputs)
              → 检查 slide_contents.json 的 review_status 是否为 "approved"
              → 未通过则 return（暂停流水线）
```

### 各 Worker 确定性行为

| 阶段 | Worker | 确定性策略 |
|------|--------|-----------|
| document_analysis | document_analyst.run(workspace, force) | 正则提取文本 → 启发式分类（域名/受众/语调）→ 硬编码 fallback 模板 |
| outline_generation | outline_generator.run(workspace, force) | 8 页固定结构（cover→background→problem→solution→product→evidence→roadmap→closing） |
| template_matching | template_matcher.run(workspace, force) | BM25 + 权重聚合检索 → 选最高分模板 → 无 LLM 重排序 |
| design_planning | design_director.run(workspace, force) | 调用 `default_design_plan(slide_count)` → 使用硬编码默认参数 |
| content_mapping | content_mapper.run(workspace, force) | `_fallback_mapping()` → 简单 type-to-type zone 匹配 → 固定位置 fallback |
| visual_generation | image_generator.run(workspace, force) | 生成 config → 检查已存在的图片 → 可能启动后台 gptimage2 进程 → 生成 report |
| ppt_assembly | ppt_assembler.run(workspace, force) | 读 slide_contents → 检查 review_status → 调用 `write_pptx()` |
| verification | ppt_verifier.run(workspace, force) | `validation_report()` 确定性检查 + 若 final.pptx 存在则运行视觉审计 |

**特点：** 最快、最稳定，但输出质量完全取决于硬编码模板。

---

## 4. 模式二：LLM-Augmented Deterministic Mode（LLM增强确定性）

**函数：** `_run_llm_augmented_workflow()`  
**触发条件：** `model_profile` 有效（Web Server 默认 `"deepseek"`）

### 与 Deterministic Mode 的区别

```
相同点：
  ✅ 线性 for 循环执行（顺序固定）
  ✅ 事件总线发射
  ✅ session.md 写入
  ✅ Review Gate 检查
  ✅ Dream 巩固任务

不同点：
  🆕 每阶段前注入 Skill 内容（progressive disclosure）
  🆕 LLM 阶段传入 llm_client → 调用 LLM 做智能分析
  🆕 末尾运行 Dream 任务（LLM 驱动的记忆巩固）
```

### 渐进式 Skill 注入机制

```python
def _inject_phase_skill(skill_loader, llm_client, phase):
    skill_name = _PHASE_SKILL.get(phase)  # 如 "ppt-outline-generator"
    if skill_name:
        skill_content = skill_loader.load_skill(skill_name)
        # 将 SKILL.md 内容注入到 llm_client._skill_contexts[phase]
```

**Skill 映射表：**

| Phase | Skill |
|-------|-------|
| outline_generation | ppt-outline-generator |
| template_matching | ppt-template-matcher |
| design_planning | ppt-design-director |
| content_mapping | ppt-content-mapper |
| visual_generation | ppt-image-layer |
| ppt_assembly | ppt-assembler |

### 哪些阶段实际调用 LLM

```python
_LLM_PHASES = {
    "document_analysis",   # ✅ LLM 分析输入文档
    "outline_generation",  # ✅ LLM 生成大纲
    "template_matching",   # ✅ LLM 语义重排序模板
    "design_planning",     # ✅ LLM 生成设计方案
    "content_mapping",     # ✅ LLM 做 zone 内容映射
    "verification",        # ✅ LLM fresh-eyes 验证
}
# ❌ visual_generation — 不接收 llm_client（通过 shell 运行外部 skill）
# ❌ ppt_assembly — 不接收 llm_client（纯 python-pptx 操作）
```

### 各阶段 LLM 行为详解

| 阶段 | LLM 用法 | Prompt 来源 |
|------|---------|------------|
| document_analysis | `llm_client.generate_json(prompt, context, fallback=...)` | `llm/prompts/document_analysis.md` |
| outline_generation | `llm_client.generate_json(prompt, context, fallback=...)` | `llm/prompts/outline_generation.md` |
| template_matching | `llm_client.generate_json(prompt, context, fallback=...)` → LLM 语义重排序 | 内联 prompt（模板选择） |
| design_planning | `llm_client.generate_json(prompt, context, fallback=...)` | `llm/prompts/design_planning.md` |
| content_mapping | `llm_client.generate_json(prompt, context, fallback=..., max_tokens=16000)` → **批量处理**（每 4 页一批） | `llm/prompts/content_mapping.md` |
| verification | `llm_client.generate_json(prompt, context={}, fallback=...)` → "fresh-eyes" 隔离上下文 | 内联 system prompt |

### LLM 调用失败的保护

每个 LLM 调用都有 `fallback` 参数，指向确定性逻辑的输出：
```python
result = llm_client.generate_json(
    prompt=prompt,
    context=context,
    fallback=fallback,  # ← LLM 失败时自动返回此值
)
```

`LLMClient.generate_json()` 内部还有 **JSON repair** 机制：如果 LLM 返回无效 JSON，会再调用一次 LLM 请求修复；修复也失败才用 fallback。

### Dream 巩固任务

```python
# 流水线末尾
if "verification" in phases and llm_client is not None:
    insights = run_dream_task(workspace.root, llm_client)
```

Dream 任务会：
1. 读取 `session.md`（所有阶段摘要）+ `history.jsonl`（最近 20 行）
2. 调用 LLM 提取 1-3 条跨会话洞察
3. 相关性 ≥ 0.6 的洞察写入 workspace 级别的 `memory.md`

---

## 5. 模式三：Agent Mode（完全自主编排）——⚠️ 死代码

**函数：** `_run_agent_workflow()` （`workflow.py:188-292`）  
**状态：** ⚠️ **已定义但从未被调用**

### 设计意图

```
CoordinatorAgent (think→act→observe)
    │
    │  LLM 自主决策：下一个 spawn 哪个 Worker
    │  支持异步并行（spawn_agent async=true）
    │
    ├─► WorkerAgent(document_analysis)
    │   自己的 agent loop: think→act→observe
    │   失败 → fallback 到确定性 Worker 函数
    │
    ├─► WorkerAgent(outline_generation)    ← 可与 template_matching 并行
    ├─► WorkerAgent(template_matching)
    ├─► WorkerAgent(design_planning)
    ├─► WorkerAgent(content_mapping)
    ├─► WorkerAgent(visual_generation)
    ├─► WorkerAgent(ppt_assembly)
    └─► WorkerAgent(verification)
```

### 为什么是死代码

在 `run_workflow()` 中：
```python
if llm_client is not None:
    # 注释写 "Agent mode 通过 model_profile='agent:<name>' 可用"
    # 但实际代码直接走了 _run_llm_augmented_workflow
    return _run_llm_augmented_workflow(...)
```

**缺少的逻辑：**
```python
# 应该有但没有的代码：
if model_profile and model_profile.startswith("agent:"):
    return _run_agent_workflow(...)
```

### 该模式的依赖全部是死代码

因为 `_run_agent_workflow` 从未被调用，以下模块也**从未在运行时被使用**：

| 死代码模块 | 说明 |
|-----------|------|
| `coordinator/coordinator_agent.py` | CoordinatorAgent 类（仅被 `_run_agent_workflow` 实例化） |
| `coordinator/worker_agent.py` — Agent Loop 路径 | WorkerAgent 的 `build_system_prompt()`, `get_available_tools()`, `execute_tool()` 等 agent 循环方法 |
| `runtime/task_manager.py` | TaskManager 类（仅 CoordinatorAgent 使用） |
| `runtime/task_types.py` | 7 种 TaskType 枚举（仅 TaskManager 使用） |
| `runtime/mailbox.py` | Mailbox 跨 Agent 通信（CoordinatorAgent + AgentLoop 使用） |
| `runtime/agent_context.py` | AsyncLocalStorage 双层上下文隔离 |
| `runtime/conversation_store.py` | JSONL 对话持久化 |
| `coordinator/tool_factory.py` | ToolFactory → ToolRegistry 按能力组装工具 |
| `coordinator/tool_impls/*.py` | 9 个按能力划分的工具实现文件 |
| `coordinator/capabilities.py` | AgentCapability 注册表 |
| `coordinator/dispatcher.py` | 任务派发器 |
| `coordinator/routing_rules.py` | 路由规则加载 |
| `runtime/coordinator_tools.py` | Coordinator 专用工具 |
| `tools/registry.py` | ToolRegistry RBAC 系统 |

### WorkerAgent 的 fallback 路径仍在使用

WorkerAgent 的 `_run_deterministic_worker()` 和 `_fallback_execution()` 方法虽然定义在类中，但它们的**等效逻辑**存在于 8 个独立 Worker 函数中（`workers/*.py`），那些独立函数才是实际被调用的。

---

## 6. 8 个 Pipeline 阶段详解

### 阶段总览

```
[1] document_analysis ──► source_summary.json
        │
[2] outline_generation ──► outline.json
        │
        ├──── [3] template_matching ──► selected_template.json
        │                              ├── template_meta.json
        │                              └── template_zones.json
        │
        └──── [4] design_planning ──► slide_design_plan.json
        │
        └──── [5] content_mapping ──► slide_contents.json  [⏸ 用户审核]
                   │
                   ├── [6] visual_generation ──► image_generation_report.json
                   │                            └── background_images/*.png
                   │
                   └── [7] ppt_assembly ──► final.pptx
                        │
                        └── [8] verification ──► validation_report.json
```

### 阶段 1: document_analysis

**Worker：** `ppt_agent.workers.document_analyst.run()`  
**输入：** `input/` 目录下的所有文件  
**输出：** `source_summary.json`

**确定性路径：**
1. 遍历 `input/` 下所有文件，并行提取文本（ThreadPoolExecutor, max_workers=8）
2. 图片文件 → `ImageInventoryItem`；文本文件 → `extract_text()`
3. `_fallback_analysis()`：
   - 正则分割句子 → 前 5 句作为 `product_capabilities`
   - `_infer_project_name()` → 取第一个非空行作为项目名
   - 硬编码 `domain="software/product"`、`target_audience="business stakeholders"`、`tone="professional"`
   - 生成 `evidence_items`（每句一个，confidence=0.7）

**LLM 路径：**
1. 加载 `llm/prompts/document_analysis.md`
2. 构造 context（extracted_text[:15000] + file_info + image_info）
3. `llm_client.generate_json(prompt, context, fallback=确定性输出)`
4. 后处理：确保 `image_inventory` 使用实际扫描结果（不信任 LLM 的图片列表）

**缓存：** 若 `source_summary.json` 已存在且 `force=False`，跳过。

---

### 阶段 2: outline_generation

**Worker：** `ppt_agent.workers.outline_generator.run()`  
**输入：** `source_summary.json` + `job.json`（用户 prompt）  
**输出：** `outline.json`

**确定性路径：**
```
硬编码 8 页结构：
  0: cover      — 项目名称
  1: background — 背景与机会
  2: problem    — 核心问题
  3: solution   — 解决方案
  4: product    — 产品能力
  5: evidence   — 支撑材料
  6: roadmap    — 推进计划
  7: closing    — 总结与期待
```
每页的 bullets 从 `product_capabilities` 轮询填充。

**LLM 路径：**
1. 加载 `llm/prompts/outline_generation.md`
2. context = {project_name, domain, audience, tone, value_proposition, capabilities, evidence_items, image_inventory, user_prompt}
3. `llm_client.generate_json(prompt, context, fallback=确定性输出)`
4. `_ensure_valid_outline()` 后处理：修复 slide_index、补充缺失字段

**特殊：** 自动从 `job.json` 读取 `user_prompt`，作为用户追加要求传给 LLM。

---

### 阶段 3: template_matching

**Worker：** `ppt_agent.workers.template_matcher.run()`  
**输入：** `outline.json`  
**输出：** `selected_template.json` + `template_meta.json` + `template_zones.json`

**确定性路径：**
1. 从 outline 构建多字段搜索 query（project_name + domain + audience + tone + 前3页标题 + product_capabilities）
2. BM25 检索模板 chunk 索引 → 30 个 chunk
3. 按 `template_id` 聚合权重分数（overview ×0.33 + design ×0.33 + slides ×0.33）
4. 选最高分模板 → 加载其 meta 和 zones
5. 若最高分 ≤ 0 → 使用 fallback 模板

**LLM 路径（额外步骤）：**
1. 在 BM25 排序后，取 top 5 候选
2. 构造 prompt：展示项目大纲 + 5 个模板摘要
3. `_llm_rerank_templates()` → LLM 语义重排序（考虑领域匹配、风格、页数适配、布局）
4. 用 LLM 新排名覆盖 BM25 排名

**template_zones.json 内容：**
```json
{
  "template_id": "xxx",
  "color_scheme": "...",
  "theme_colors": {...},
  "slides": [
    {
      "index": 0,
      "template_image": "templates/general/xxx/preview/slide_00.png",
      "text_zones": [{"zone_id": "title_0", "type": "title", "position": [...], "formatting": {...}}],
      "image_zones": [{"zone_id": "img_0", "type": "image", "position": [...]}],
      "layout": "cover.hero"
    }
  ]
}
```

---

### 阶段 4: design_planning

**Worker：** `ppt_agent.workers.design_director.run()`  
**输入：** `outline.json` + `template_meta.json`（可选）  
**输出：** `slide_design_plan.json`

**确定性路径：**
```python
slide_count = outline["meta"]["total_slides"]
plan = default_design_plan(slide_count)  # 硬编码默认方案
```

**LLM 路径：**
1. 加载 `llm/prompts/design_planning.md`
2. context = {project_name, domain, audience, tone, total_slides, slides 摘要, template 信息}
3. `llm_client.generate_json(prompt, context, fallback=default_design_plan)`
4. `_ensure_valid_design()` 后处理：补充 theme_profile、typography_tokens、spacing_tokens、shape_tokens 等

---

### 阶段 5: content_mapping

**Worker：** `ppt_agent.workers.content_mapper.run()`  
**输入：** `outline.json` + `selected_template.json` + `slide_design_plan.json` + `source_summary.json` + `template_zones.json`  
**输出：** `slide_contents.json`

**这是整个流水线最关键、最复杂的阶段。**

**确定性路径 (`_fallback_mapping`)：**
1. 遍历 outline 每页
2. 从 template_zones 获取该页的 text_zones + image_zones
3. 简单 type-to-type 匹配：title → title zone, bullets → body zone
4. 用硬编码默认位置作为 fallback

**LLM 路径 (`_llm_mapping`)：**
1. 加载 `llm/prompts/content_mapping.md` + AI Direct Zone Assignment 指令
2. **批量处理**：每 4 页一批（避免输出 token 截断）
3. 每批 context 包含：该批次的 outline slides + design slides + template zones（含 capacity_hint）
4. LLM 需要为**每个 zone** 决定：放什么内容、为什么放这里、为什么留空
5. `_ensure_valid_mapping()` 后处理：
   - `_lock_zone_positions()` → 用模板真实 position 覆盖 LLM 可能编造的值
   - `_inject_formatting()` → 从模板 zone 注入 font_size_pt、font_name、font_color 等
   - `_find_fallback_zone_id()` → LLM 编造的 zone_id 自动修正

**capacity_hint 示例：**
```
"large area (0.35), 28pt, ~35 chars/line, ~15 lines — good for title, key number, or 3+ bullets"
"small area (0.06), 11pt, ~6 chars/line, ~3 lines — good for footer, label, or short text"
```

**⚠️ Review Gate：**
```
content_mapping 完成后 → _check_review_gate()
  ├─ 写入 review_pending.json
  ├─ 发射 USER_INPUT_REQUEST 事件
  ├─ force=True? → 跳过
  └─ slide_contents.json["review_status"] == "approved"? → 继续
      └─ 否则 → return（暂停流水线，等待用户审核）
```

---

### 阶段 6: visual_generation

**Worker：** `ppt_agent.workers.image_generator.run(mode="all")`  
**输入：** `slide_contents.json` + `template_zones.json`  
**输出：** `image_generation_report.json` + `image_generation_config.json` + `background_images/*.png`

**注意：** 此阶段不接收 llm_client，而是通过 **外部 shell 进程** 运行 gptimage2-generator skill。

**流程：**
1. `prepare_generation_config()`：
   - 加载 slide_contents + template_zones
   - `_enrich_with_template_images()` → 解析模板预览图路径
   - `slide_contents_to_batch_config()` → 为每个 slide 生成 prompt + reference_image
   - 注入 `_inline_map`（slide_index + zone_id → output_name）
   - 写入 `image_generation_config.json`

2. `build_skill_context()` → 写入 `skill_instructions.json`

3. `check_generated_images()` → 检查 `background_images/` 中已有图片

4. 若没有图片且 generation 未在运行：
   - 自动注册 gptimage2 账号（若需要）
   - `_invoke_gptimage2_skill_background()` → **Popen 后台启动** gptimage2 batch-generate
   - 写入 `.generation_status.json`（标记 "running"）

5. `check_generated_images()` 再次检查 → 生成 `image_generation_report.json`

**Report 的 `execution.method` 可能值：**
- `"gptimage2_skill"` — 图片已生成
- `"gptimage2_pending"` — 正在后台生成，需重新运行流水线
- `"deterministic_fallback"` — 没有图片生成
- `"template_only"` — 无需 AI 生成

---

### 阶段 7: ppt_assembly

**Worker：** `ppt_agent.workers.ppt_assembler.run()`  
**输入：** `slide_contents.json`  
**输出：** `final.pptx`

**纯确定性操作（不接收 llm_client）：**
1. 加载 `slide_contents.json`
2. 检查 `review_status`：必须为 `"approved"` 或 `force=True`，否则抛出异常
3. 从 `selected_template.json` 查找模板 PPTX 文件路径
4. 调用 `assembly/ppt_writer.py::write_pptx()`：
   - 对每页 slide：若有 `background_images/slide_XX.png` → 设为全页背景
   - 否则：用 `template.pptx` 对应 slide layout 作为 fallback
   - 在背景上叠加可编辑文本框（title、bullets、footer）
5. 生成 `preview.html`（HTML 预览）

---

### 阶段 8: verification

**Worker：** `ppt_agent.workers.ppt_verifier.run()`  
**输入：** `slide_contents.json` + `image_generation_report.json`（可选）+ `final.pptx`（可选）  
**输出：** `validation_report.json`

**确定性检查：**
1. `validation_report()` → 基础结构检查（slide 数量、zone 数量、fallback 标记）
2. 若 `final.pptx` 存在 → 运行视觉审计（`vision/pptx_audit.py`）：
   - 检查字体问题、对比度问题、文本溢出问题
   - 将建议追加到 report

**LLM 检查（fresh-eyes）：**
1. `_fresh_eyes_validation()` → **隔离上下文**（`context={}`），LLM 只看最终 artifacts
2. 评估三个维度：
   - **content_completeness**（score 0-100）：是否覆盖所有要点
   - **visual_consistency**（score 0-100）：fallback 是否解决、图片引用是否合理
   - **text_accuracy**（score 0-100）：文本是否连贯、专业
3. 输出 `overall_score` 和 `summary`

---

## 7. 支撑系统详解

### 7.1 三层记忆系统

```
agent.md    → 系统身份（不可变）
              位置：<job_root>/.ppt_agent/agent.md
              内容：Agent 能力、约束、身份描述
              注入：build_memory_context() → LLM 系统 prompt

memory.md   → 跨会话知识（Dream 任务写入）
              位置：workspace 级别（job_root.parent/.ppt_agent/memory.md）
              内容：历史洞察、警告、模式发现
              注入：build_memory_context() → LLM 系统 prompt

session.md  → 当前会话工作记忆（每阶段完成后追加）
              位置：<job_root>/.ppt_agent/session.md
              内容：每个阶段的摘要（时间戳 + artifact 路径）
              写入：append_phase_summary() 在每阶段后调用
```

### 7.2 事件总线

**EventBus 架构：**
```
EventBus(job_root)
  ├── FileConsumer      → 写入 history.jsonl（自动注册）
  ├── LoggingConsumer   → Python logging 输出（workflow 中手动注册）
  ├── CallbackConsumer  → 用户自定义回调
  └── SSEConsumer       → FastAPI SSE 推送（Web Server 中使用）

事件类型：
  生命周期: phase_started, phase_completed, agent_spawn, agent_complete, status_change
  执行:     tool_call_start, tool_call_result, llm_call_start, llm_call_result
  系统:     artifact_written, skill_loaded, compression_event, memory_update, user_input_request
  诊断:     warning, error, progress
```

**反压控制：** 每个 Consumer 独立上限 1000 事件，超限则丢弃（Agent loop 永不阻塞）。

### 7.3 上下文压缩（L0-L4）

**两级压缩体系：**

1. **LLMClient._build_messages()** — Worker 调用 LLM 前：
   ```
   L0 (<50%)  → 无压缩
   L1 (50-70%) → 去多余空白、截断长列表（>20项）
   L2 (70-85%) → 总结 evidence、截断长字符串（>2000字符）
   L3 (85-95%) → 仅保留阶段相关数据
   L4 (>95%)  → 仅保留 errors + approved text
   ```

2. **AgentLoop._compress_messages()** — Agent 循环中（仅 Agent Mode 使用）：
   ```
   L0 (<60%)  → 无压缩
   L1 (60-75%) → 截断大型工具输出（>2000字符）
   L2 (75-85%) → LLM 总结前半段对话
   L3 (85-95%) → 保留 system + errors + 最近 4 条
   L4 (>95%)  → 保留 system + 最近 2 条
   ```

**⚠️ AgentLoop 的压缩仅用于死代码的 Agent Mode。**

### 7.4 Skill 加载系统

**SkillLoader** 在 LLM-Augmented Mode 中使用：

```python
# workflow.py:_inject_phase_skill()
skill_content = skill_loader.load_skill("ppt-outline-generator")
llm_client._skill_contexts[phase] = {"skill_name": ..., "content": ...}
```

注入的 Skill 内容会出现在 LLM 系统 prompt 中（通过 `LLMClient._build_messages()`）。

**注意：** `load_skill` 作为 AgentLoop 的 built-in tool 仅在 Agent Mode 中可用，在 LLM-Augmented Mode 中 Skill 是由 workflow 主动注入的。

### 7.5 Dream 巩固任务

```python
# dream.py:run_dream_task()
if llm_client:
    _llm_dream()  → LLM 从 session.md + history.jsonl 中提取 1-3 条洞察
else:
    _heuristic_dream()  → 提取含 "warning/error/failed/fallback" 的行
```

**触发时机：** LLM-Augmented Mode 中 verification 完成后（`verification in phases`）。

### 7.6 审计日志

```python
# agent_loop.py:_log_model_call()
log_model_call(job_root, phase=agent_id, result=llm_result, prompt_summary=...)
→ 写入 model_calls.jsonl
```

**⚠️ 仅在 Agent Mode 中有审计。** LLM-Augmented Mode 中的 LLM 调用通过 `LLMClient.generate_json()` 的 `log_model_call()` 审计（在 `client.py:171` 行）。

---

## 8. 代码使用率审计

### 8.1 实际被调用的模块

| 模块 | 用途 | 调用者 |
|------|------|--------|
| `coordinator/workflow.py` — `run_workflow()` + 两个活跃模式函数 | 总调度入口 | CLI, Web Server, 测试 |
| `workers/document_analyst.py` | 阶段 1 Worker | workflow (LLM-Augmented + Deterministic) |
| `workers/outline_generator.py` | 阶段 2 Worker | workflow |
| `workers/template_matcher.py` | 阶段 3 Worker | workflow |
| `workers/design_director.py` | 阶段 4 Worker | workflow |
| `workers/content_mapper.py` | 阶段 5 Worker | workflow |
| `workers/image_generator.py` | 阶段 6 Worker | workflow |
| `workers/ppt_assembler.py` | 阶段 7 Worker | workflow |
| `workers/ppt_verifier.py` | 阶段 8 Worker | workflow |
| `coordinator/event_bus.py` | 事件总线 | workflow |
| `coordinator/phase_state.py` | Artifact 读写 | 所有 Worker |
| `context/memory_layers.py` | 三层记忆 | workflow |
| `context/session_summary.py` | 会话摘要写入 | workflow |
| `context/dream.py` | 记忆巩固 | workflow (LLM-Augmented) |
| `context/compression.py` | 上下文压缩 | LLMClient |
| `skills/loader.py` | Skill 加载 | workflow (LLM-Augmented) |
| `llm/client.py` | LLM 客户端 | 各 Worker (LLM 阶段) |
| `llm/config.py` | 模型配置加载 | LLMClient |
| `llm/json_repair.py` | JSON 修复 | LLMClient |
| `llm/audit.py` | 审计日志 | LLMClient |
| `assembly/ppt_writer.py` | PPTX 写入 | ppt_assembler |
| `assembly/html_preview.py` | HTML 预览 | CLI preview 命令 |
| `retrieval/query_router.py` | BM25 检索 | template_matcher |
| `retrieval/template_index.py` | 模板索引 | template_matcher |
| `models/*` | 数据模型 | 各 Worker |
| `tools/documents.py` | 文本提取 | document_analyst |

### 8.2 死代码模块（已编写但从未调用）

| 模块 | 说明 |
|------|------|
| `coordinator/workflow.py:_run_agent_workflow()` | 完整的 Agent Mode 函数（100 行） |
| `coordinator/coordinator_agent.py` | CoordinatorAgent 类（407 行） |
| `coordinator/worker_agent.py` — Agent Loop 路径 | build_system_prompt, get_available_tools, execute_tool, parse_llm_response（~500 行有效代码） |
| `coordinator/capabilities.py` | AgentCapability 注册表（134 行） |
| `coordinator/tool_factory.py` | 按能力组装工具集（78 行） |
| `coordinator/tool_impls/*.py` | 9 个工具实现文件 |
| `coordinator/dispatcher.py` | 任务派发器（42 行） |
| `coordinator/routing_rules.py` | 路由规则（13 行） |
| `runtime/task_manager.py` | 任务生命周期管理（218 行） |
| `runtime/task_types.py` | 7 种 TaskType + 状态机（89 行） |
| `runtime/mailbox.py` | 跨 Agent 文件邮箱（176 行） |
| `runtime/agent_context.py` | 双层上下文隔离 |
| `runtime/conversation_store.py` | JSONL 对话持久化 |
| `runtime/coordinator_tools.py` | Coordinator 专用工具 |
| `tools/registry.py` | ToolRegistry RBAC |
| `agent_loop.py` — Agent Mode 相关方法 | `_check_mailbox()`, `_compress_messages()`, `_load_skill_tool()` 等 |

**保守估计：约 2,500+ 行代码属于死代码。**

### 8.3 Web Server 中的使用模式

`src/ppt_agent/server/app.py` 的 `/api/jobs/{job_id}/run` 端点：

```python
run_workflow(
    job_path,
    until=req.until,
    start_from=req.start_from,
    force=req.force,
    model_profile=req.model_profile if req.model_profile is not None else "deepseek",
)
```

**关键点：**
- 始终传入 `model_profile`（默认 `"deepseek"`）→ 始终走 LLM-Augmented Mode
- `force` 默认 `False` → 默认会停在 review gate
- 通过 `BackgroundTasks` 异步执行 → 不阻塞 HTTP 响应
- SSE endpoint 通过独立的 `_event_subscribers` 字典推送事件（**不是** EventBus 的 SSEConsumer）

### 8.4 CLI 中的使用模式

```bash
# 创建作业
ppt-agent create-job --input ./materials/ --output workspace/jobs/my-job

# 运行（确定性模式，不调用 LLM）
ppt-agent run --job workspace/jobs/my-job

# 运行（LLM 增强模式）
ppt-agent run --job workspace/jobs/my-job --model-profile deepseek

# 跳过审核
ppt-agent run --job workspace/jobs/my-job --force

# 从中间恢复
ppt-agent run --job workspace/jobs/my-job --from visual_generation --force

# 审核 slide_contents
ppt-agent approve --job workspace/jobs/my-job

# 生成 HTML 预览
ppt-agent preview --job workspace/jobs/my-job

# 启动 Web 界面
ppt-agent serve --port 8000
```

---

## 9. 数据流全景图

### Artifact 依赖链

```
input/ 目录
    │
    ▼
[document_analysis]
    │
    source_summary.json
    │
    ▼
[outline_generation]
    │
    outline.json
    │
    ├──────────────────────────────┐
    ▼                              ▼
[template_matching]          [design_planning]
    │                              │
    ├── selected_template.json     │
    ├── template_meta.json         slide_design_plan.json
    └── template_zones.json        │
    │                              │
    └──────────┬───────────────────┘
               ▼
[content_mapping]
    │
    slide_contents.json ─── [用户审核]
    │
    ├──────────────────┐
    ▼                  ▼
[visual_generation]  [ppt_assembly]
    │                  │
    image_gen_         final.pptx
    report.json        │
    │                  │
    └────────┬─────────┘
             ▼
[verification]
    │
    validation_report.json
```

### Workspace 目录结构

```
workspace/jobs/<job-id>/
├── job.json                    # 作业元数据（status, user_prompt, created_at）
├── history.jsonl               # 事件历史
├── model_calls.jsonl           # LLM 调用审计日志
├── review_pending.json         # 审核挂起标记
├── preview.html                # HTML 预览
├── .ppt_agent/
│   ├── agent.md                # Agent 身份
│   └── session.md              # 会话日志
├── input/                      # 用户上传的原始文件
├── source_summary.json         # [1] 文档分析结果
├── outline.json                # [2] PPT 大纲
├── selected_template.json      # [3] 选中模板
├── template_meta.json          # [3] 模板元数据
├── template_zones.json         # [3] 模板区域映射
├── slide_design_plan.json      # [4] 设计方案
├── slide_contents.json         # [5] 逐页内容映射
├── image_generation_config.json # [6] 图片生成配置
├── image_generation_report.json # [6] 图片生成报告
├── skill_instructions.json     # [6] Skill 上下文
├── .generation_status.json     # [6] 后台生成状态
├── background_images/          # [6] AI 生成的背景图
│   ├── slide_000_bg.png
│   └── ...
├── generated_slides/           # 旧版图片目录（兼容）
├── final.pptx                  # [7] 最终 PPT 文件
└── validation_report.json      # [8] 验证报告
```

---

## 10. 附录：关键文件调用链

### Web Server 请求的完整调用链

```
HTTP POST /api/jobs/{job_id}/run
  │
  ▼
server/app.py:run_job()
  │
  ▼
coordinator/workflow.py:run_workflow(job_path, model_profile="deepseek")
  │
  ├── ensure_memory_files(workspace.root)          # 创建 .ppt_agent/
  ├── EventBus(workspace.root) + LoggingConsumer    # 事件总线
  ├── _create_llm_client("deepseek", workspace.root) # 创建 LLM
  │     └── load_model_config("config/models.yml")
  │     └── _create_provider(deepseek config)
  │           └── OpenAICompatibleProvider(endpoint="https://api.deepseek.com/v1")
  │
  └── _run_llm_augmented_workflow(workspace, llm_client, bus, phases, force)
        │
        └── for phase in phases:
              │
              ├── _inject_phase_skill(skill_loader, llm_client, phase)
              │     └── skill_loader.load_skill("ppt-xxx")
              │
              ├── PHASE_TO_WORKER[phase](workspace, force, llm_client=..., **extra)
              │     │
              │     ├── document_analyst.run(ws, force, llm_client)
              │     │     ├── 遍历 input/ 提取文本
              │     │     ├── llm_client.generate_json(prompt, context, fallback=...)
              │     │     └── write_artifact("source_summary", data)
              │     │
              │     ├── outline_generator.run(ws, force, llm_client)
              │     │     ├── load_artifact("source_summary")
              │     │     ├── llm_client.generate_json(prompt, context, fallback=...)
              │     │     └── write_artifact("outline", data)
              │     │
              │     ├── template_matcher.run(ws, force, llm_client)
              │     │     ├── load_artifact("outline")
              │     │     ├── BM25 检索 + LLM 语义重排序
              │     │     └── write_artifact("selected_template") + "template_meta" + "template_zones"
              │     │
              │     ├── design_director.run(ws, force, llm_client)
              │     │     ├── load_artifact("outline") + "template_meta"
              │     │     ├── llm_client.generate_json(prompt, context, fallback=...)
              │     │     └── write_artifact("slide_design_plan", data)
              │     │
              │     ├── content_mapper.run(ws, force, llm_client)
              │     │     ├── load_artifact("outline", "selected_template", "slide_design_plan", "source_summary", "template_zones")
              │     │     ├── llm_client.generate_json(prompt, context, fallback=..., max_tokens=16000)
              │     │     │     └── 批量处理：每 4 页一批
              │     │     └── write_artifact("slide_contents", data)
              │     │
              │     ├── image_generator.run(ws, force, mode="all")
              │     │     ├── prepare_generation_config() → image_generation_config.json
              │     │     ├── build_skill_context() → skill_instructions.json
              │     │     ├── check_generated_images()
              │     │     ├── _invoke_gptimage2_skill_background() → Popen 后台启动
              │     │     └── write_artifact("image_generation_report", report)
              │     │
              │     ├── ppt_assembler.run(ws, force)
              │     │     ├── load_artifact("slide_contents")
              │     │     ├── 检查 review_status
              │     │     ├── write_pptx(slide_contents, "final.pptx")
              │     │     └── 返回 Path("final.pptx")
              │     │
              │     └── ppt_verifier.run(ws, force, llm_client)
              │           ├── load_artifact("slide_contents", "image_generation_report")
              │           ├── validation_report() 确定性检查
              │           ├── visual_audit_pptx() 视觉审计
              │           ├── llm_client.generate_json(prompt, context={}, fallback=...) → fresh-eyes
              │           └── write_artifact("validation_report", report)
              │
              ├── append_phase_summary(workspace.root, phase, ...)
              │     └── append_memory(ws, "session", content) → session.md
              │
              ├── bus.emit(PHASE_COMPLETED)
              │
              └── if phase == "content_mapping":
                    _check_review_gate(ws, bus, force, outputs)
                      ├── 写入 review_pending.json
                      ├── bus.emit(USER_INPUT_REQUEST)
                      └── 检查 slide_contents["review_status"] == "approved"
                            └── 未通过 → return（流水线暂停）
        │
        └── if "verification" in phases:
              run_dream_task(workspace.root, llm_client)
                └── LLM 从 session.md + history.jsonl 提取洞察 → memory.md
```

---

> **文档生成时间：** 2026-07-09  
> **基于源码版本：** feature-mvp1 (git HEAD: adb7d31)  
> **分析范围：** `src/ppt_agent/` 全部 114 个 Python 文件
