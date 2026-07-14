# PPT-Agent 生成策略文档

## 架构原则

```
Agent (LLM)        →  决策层：读数据、推理、决定做什么
Worker (确定性代码)  →  执行层：位置锁定、格式注入、文字替换、图片替换、PPT 组装
```

**Agent 是大脑，Worker 是手。** Agent 不用亲自操作 python-pptx API，Worker 不替 Agent 做内容决策。

---

## 两种执行模式

### 线性模式 (`--model-profile deepseek`)

```python
for phase in phases:
    PHASE_TO_WORKER[phase](workspace, force=force, llm_client=client)
```

每个阶段调 Worker 函数，Worker 内部调 LLM 做决策。

### Agent 模式 (`--model-profile agent:deepseek`)

```python
CoordinatorAgent → 按依赖编排 → 每个阶段启动 WorkerAgent
WorkerAgent → Agent Loop (LLM think→act→observe) → 调 tools
Tools → Worker 函数 (不传 llm_client，确定性执行)
```

**Agent 自己就是 LLM，不需要 Worker 内部再调一个 LLM。** Tool 调 Worker 时走确定性路径。

---

## 8 个阶段：Agent 决策什么，Worker 执行什么

### 1. document_analysis

| | 谁 | 做什么 |
|---|---|---|
| 决策 | Agent | 读上传文件 → 提取项目名、领域、受众、基调、产品能力、证据项 |
| 执行 | Worker | 文件解析（.txt/.md/.docx） |

**Tool**: `read_input_files` — 读原始文件
**输出**: `source_summary.json`

---

### 2. outline_generation

| | 谁 | 做什么 |
|---|---|---|
| 决策 | Agent | 根据 source_summary + user_prompt → 规划大纲结构（封面→背景→问题→方案→产品→证据→路线图→结束）、每页标题、要点、图片需求 |
| 执行 | Worker | 写 `outline.json` |

**Tool**: `read_artifact`（读 source_summary）→ Agent 思考 → `write_artifact`（写 outline）
**输出**: `outline.json`

---

### 3. template_matching

| | 谁 | 做什么 |
|---|---|---|
| 决策 | Agent | 调用 search_templates → 看候选列表 → 评估匹配度 → 决定选哪个模板 |
| 执行 | Worker | 3 层分块 RAG（BM25 + 向量 + 加权聚合），写 `selected_template.json` |

**Tool**: `search_templates`（检索候选）→ Agent 评估 → `select_template`（执行匹配+写文件）
**输出**: `selected_template.json`, `template_meta.json`, `template_zones.json`

**RAG 详细流程**:
- 每个模板拆 3 个 chunk: overview (0.4) + design (0.2) + slides (0.4)
- 查询: 项目名 + 领域 + 受众 + 基调 + 前 3 页标题 + 产品能力
- 检索: BM25 + TF-IDF 向量双路 → RRF 融合 → 加权聚合
- 模板数据: meta.json（位置 + 格式 + 视觉分析 + Qwen VL 真彩色）

---

### 4. design_planning

| | 谁 | 做什么 |
|---|---|---|
| 决策 | Agent | 根据 outline + template_meta → 为每页选布局模式、视觉密度、主题配置 |
| 执行 | Worker | 写 `slide_design_plan.json` |

**Tool**: `read_artifact` → Agent 思考 → `write_artifact`
**输出**: `slide_design_plan.json`

---

### 5. content_mapping

| | 谁 | 做什么 |
|---|---|---|
| 决策 | Agent | 读 template_zones（含每个 zone 的 position / formatting / visual / capacity_hint）→ 决定：哪个 zone 放标题、哪个放要点、哪个放图片 prompt |
| 执行 | Worker | `_lock_zone_positions()` 强制覆写 position（防 Agent 编造）、`_inject_formatting()` 注入模板原格式、写 `slide_contents.json` |

**Tool**: `read_artifact`（读 template_zones / outline / design_plan）→ Agent 决策内容分配 → `map_slide_content`（Worker 执行位置锁定+格式注入+写文件）
**输出**: `slide_contents.json`

**关键约束**: 
- Skill 文件 `.catpaw/skills/ppt-content-mapper/SKILL.md` 强制要求 COPY position 和 zone_id
- Worker 的 `_lock_zone_positions()` 作为代码级兜底
- Worker 不传 llm_client，只用 `_fallback_mapping` 做执行

---

### 6. visual_generation

| | 谁 | 做什么 |
|---|---|---|
| 决策 | Agent | 决定是否需要生成（还是模板底图够了）、用哪种 mode |
| 执行 | Worker | 背景图质量判断（Sobel + 色差 + 亮度 → 0-1 分）、GPTImage2 后台调用、配图生成队列 |

**Tool**: `generate_images(mode="all")` — Worker 做质量检查 + 启动 GPTImage2（Popen，不阻塞）
**输出**: `image_generation_report.json`

**质量判断**: 模板底图 ≥ 0.4 → 保留不生成；< 0.4 → 生成；无底图 → 必须生成
**配图**: 有 `image_prompt` 无 `image_ref` → 加入生成队列；有 `image_ref` → 用用户图片

---

### 7. ppt_assembly

| | 谁 | 做什么 |
|---|---|---|
| 决策 | Agent | 不需要决策——纯机械操作 |
| 执行 | Worker | 模板 PPTX 查找 → 克隆页面 → 文字替换（`_apply_text_to_shape` 只改 p.text，保留格式）→ 图片替换（`_replace_shape_image`）→ 清空未匹配旧文字 → 删除原模板页 |

**Tool**: `assemble_pptx` — 直接调 `ppt_assembler.run()`
**输出**: `final.pptx`

**三不原则**: 不新增文本框、不乱改位置（Agent 没参与这个阶段）、不丢格式

---

### 8. verification

| | 谁 | 做什么 |
|---|---|---|
| 决策 | Agent | 读验证报告 → 评估内容完整性、视觉一致性、是否需要人工审核 |
| 执行 | Worker | 结构检查 + 视觉审计（PPTX XML 直接检查：字体大小、文字溢出、对比度）|

**Tool**: `run_verification` — Worker 做机械检查 → Agent 读报告做质量判断
**输出**: `validation_report.json`

---

## 模板数据管线

```
PPTX 文件 (raw-templates/)
  │
  ├─ python-pptx 读形状 → position, text, placeholder_type, 字体名, 字号
  ├─ LibreOffice → PDF → PyMuPDF → 每页 150DPI PNG
  ├─ 像素分析 (zone_refiner.py) → text_likeness, edge_density, suggested_type
  ├─ 噪音过滤 (_is_template_noise) → 过滤署名/字体样本/色标/教程文字
  ├─ Qwen VL 配色提取 → primary/secondary/accent/background/text (真实 HEX)
  │
  └─ → meta.json (位置 + 格式 + 视觉 + 配色)
         │
         ├─ template_index.py → 3 层分块 → 24 个 chunk
         └─ template_matcher.py → RAG 检索 → selected_template
              │
              └─ template_zones.json (准确 zone 数据)
                   │
                   └─ content_mapper.py → slide_contents.json
                        │
                        ├─ image_generator.py → GPTImage2 生图
                        └─ ppt_assembler.py → final.pptx
```

---

## 为什么 Agent Loop 不直接操作 PPTX

| Agent 不应该做的事 | 原因 | 谁来做 |
|---|---|---|
| 自己算 zone position | Agent 实测会编造坐标（[0.1,0.7] 而非模板的 [0.046,0.114]） | Worker `_lock_zone_positions` |
| 自己调 python-pptx API | blipFill, sldIdLst, drop_rel 等没有类型提示，LLM 推理不了 | Worker `ppt_assembler.run()` |
| 自己写 slide_contents JSON | 可能编造 zone_id、用错字段名 | Worker + Skill 约束 + 后处理校验 |
| 自己调 GPTImage2 脚本 | 子进程编排、账号管理是确定性操作 | Worker `image_generator.run()` |
