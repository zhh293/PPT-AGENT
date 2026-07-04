# PPT-Agent 真实大模型接入与精美 PPT 产出开发文档

## 1. 背景

当前仓库已经完成 PPT-Agent 的基础骨架：CLI、文件型 job workspace、阶段性 JSON 产物、Worker 模块、Skill 骨架、检索抽象、设计系统骨架、PPTX fallback 写入和基础测试。

但当前实现仍属于 fallback MVP：

- 大纲生成主要依赖固定结构和简单文本抽取。
- 模板匹配只基于极少量 metadata，没有真实模板库和真实 PPT 模板应用。
- 图片生成默认走 fallback report，没有真实调用生图模型。
- PPT 组装通过手写 PPTX XML 输出白底文本框，视觉效果较弱。
- 验证逻辑主要基于 JSON 内容启发式判断，没有真实渲染级检查。
- `workflow.py` 直接线性调用 Worker，没有真正走统一 dispatcher 和 Agent 编排。

本文档目标是从当前进度继续推进，直到系统能够接入真实大模型，经过 Agent 多阶段处理后，稳定产出一份结构完整、视觉精美、文字可编辑、可验证的 PowerPoint 文件。

## 2. 目标

### 2.1 产品目标

用户输入项目计划书、产品截图、证书、Logo、业务图片等材料后，系统自动完成：

1. 材料解析与事实提取。
2. 行业、受众、表达风格识别。
3. PPT 大纲生成。
4. 真实模板匹配。
5. 模板版式理解与内容映射。
6. 用户审核与修改。
7. 真实图片生成或用户图片分配。
8. 可编辑 PPT 组装。
9. 渲染级质量验证。
10. 输出 `final.pptx` 和 `validation_report.json`。

### 2.2 工程目标

- 接入真实 LLM，用于文档分析、大纲生成、内容改写、设计规划和验证建议。
- 接入真实视觉生成能力，优先复用 `.catpaw/skills/gptimage2-generator`。
- 使用真实 PPT 模板，而不是只生成白底 fallback PPT。
- 保证用户批准过的标题、正文和 bullet 保持为可编辑文本。
- 保留所有中间产物，支持从任意阶段恢复。
- 每个核心阶段都有可测试的 JSON 合约和集成测试。

### 2.3 非目标

- 不做 Web UI，第一阶段仍以 CLI 和文件工作区为主。
- 不做多人任务调度和 SaaS 化部署。
- 不追求一次性支持所有 PPT 类型，先覆盖路演、项目汇报、比赛答辩、产品介绍四类。
- 不允许把用户确认文本整体栅格化成图片来规避排版问题。

## 3. 当前代码基线

| 模块 | 当前状态 | 主要差距 |
|------|----------|----------|
| CLI | 已有 `create-job`、`run`、`approve`、`validate-artifacts` | 缺少模型配置、阶段重跑、调试报告输出 |
| Workflow | 可线性跑通 8 个阶段 | 未真正使用 dispatcher |
| Dispatcher | 有决策模型和路由读取 | 未参与主流程执行 |
| Document Analyst | 可抽取文本和图片清单 | 缺 OCR、结构化事实提取、证书/截图理解 |
| Outline Generator | 可生成固定 8 页大纲 | 缺真实 LLM 生成和行业结构模板 |
| Template Matcher | 有本地 BM25 排序 | 缺真实模板库、向量检索、模板评分 |
| Design Director | 有设计系统骨架 | 缺 LLM 设计决策和模板适配 |
| Content Mapper | 可生成 `slide_contents.json` | 缺真实模板 zone map 和内容长度适配 |
| Image Generator | 默认 fallback | 缺真实生图调用和图片落盘 |
| PPT Assembler | 可生成可编辑文本 PPTX | 缺真实模板应用、图片插入、主题样式 |
| Verifier | 可生成基础报告 | 缺渲染级检查和 PPT 内容解析 |

## 4. 总体架构

目标架构保留当前文件系统优先设计，但将每个阶段升级为真实 Agent Worker：

```text
input/
  -> Document Analyst Agent
  -> Outline Generator Agent
  -> Template Matcher Agent
  -> Design Director Agent
  -> Content Mapper Agent
  -> Image Generator Agent
  -> PPT Assembler Agent
  -> PPT Verifier Agent
  -> final.pptx
```

Coordinator 只负责：

- 读取 job 状态。
- 根据 phase 生成 `DispatchRequest`。
- 调用 dispatcher 获取 `DispatchDecision`。
- 加载对应 Skill。
- 构建最小上下文。
- 调用 Worker。
- 记录事件、错误、重试和 fallback。

Worker 负责具体产物生成，不直接决定全局流程。

## 5. 真实大模型接入设计

### 5.0 协议支持结论

必须支持 Anthropic 格式的 AI。这里要区分两种接入形态：

| 形态 | 示例 | 是否支持 | 说明 |
|------|------|----------|------|
| Anthropic 原生 Messages API | Claude 官方 `/v1/messages` | 必须支持 | 独立 provider，处理 `system`、`messages`、content blocks、tool use |
| OpenAI-compatible API | OpenAI、模型网关、本地 vLLM/Ollama/LM Studio | 必须支持 | 通用协议，适合大部分兼容网关 |
| Fake Provider | 测试专用 | 必须支持 | CI 不依赖真实模型和网络 |

LLM 抽象层不能假设所有模型都遵循 OpenAI 消息格式。内部应使用统一消息模型，再由 provider 转换为 Anthropic 或 OpenAI-compatible 协议。

### 5.1 新增模型抽象层

新增目录：

```text
src/ppt_agent/llm/
├── __init__.py
├── client.py
├── config.py
├── messages.py
├── response_parser.py
├── json_repair.py
├── audit.py
├── providers/
│   ├── base.py
│   ├── openai.py
│   ├── anthropic.py
│   ├── openai_compatible.py
│   ├── fake.py
│   └── local.py
└── prompts/
    ├── document_analysis.md
    ├── outline_generation.md
    ├── design_planning.md
    ├── content_mapping.md
    └── validation_review.md
```

核心接口：

```python
class LLMClient:
    def generate_json(self, *, prompt: str, schema: dict, context: dict) -> dict:
        ...

    def generate_text(self, *, prompt: str, context: dict) -> str:
        ...
```

要求：

- 所有结构化输出必须经过 JSON schema 校验。
- LLM 返回无效 JSON 时，最多自动修复 2 次。
- 修复失败后写入 warning，并使用 fallback。
- Worker 不直接依赖具体厂商 SDK，只依赖 `LLMClient`。
- 每次调用写入 `model_calls.jsonl`，记录 provider、model、phase、prompt_hash、schema_name、latency_ms、status、error。默认不记录完整敏感原文。

### 5.1.1 内部消息模型

新增 `src/ppt_agent/llm/messages.py`，定义厂商无关消息：

```python
@dataclass
class LLMMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: list[ContentBlock]

@dataclass
class ContentBlock:
    type: Literal["text", "image", "json", "tool_result"]
    text: str | None = None
    path: str | None = None
    mime_type: str | None = None
    data: dict | None = None
```

转换规则：

- Anthropic：`system` 转为顶层 `system` 字段，图片转 Anthropic content block。
- OpenAI-compatible：`system` 保持为 system message，图片转 provider 支持的 `image_url` 或等价格式。
- Worker 不允许拼厂商原始 payload。

### 5.1.2 Anthropic Provider

`src/ppt_agent/llm/providers/anthropic.py` 负责：

- 读取 `ANTHROPIC_API_KEY`。
- 调用 Anthropic Messages API。
- 支持文本、图片输入和结构化 JSON 输出。
- 将内部消息模型转换为 Anthropic content blocks。
- 将 Anthropic 响应转换回统一 `LLMResult`。

配置示例：

```yaml
providers:
  anthropic:
    protocol: anthropic_messages
    api_key_env: ANTHROPIC_API_KEY
    base_url_env: ANTHROPIC_BASE_URL
    text_model: claude-sonnet-4-20250514
    vision_model: claude-sonnet-4-20250514
    temperature: 0.3
    max_tokens: 8000
    timeout_seconds: 120
```

Anthropic JSON 输出策略：

1. system prompt 明确要求只输出 JSON。
2. user message 附 schema 摘要和少量示例。
3. response parser 截取第一个合法 JSON object。
4. schema 校验失败时，将错误和原始输出交给同 provider 做一次 repair。

如果后续启用工具调用，Anthropic 的 `tool_use` 只能调用 Coordinator 授权工具，不允许 Worker 自行扩权。

### 5.1.3 OpenAI-compatible Provider

`src/ppt_agent/llm/providers/openai_compatible.py` 负责接入 OpenAI、模型网关和本地推理服务。

配置示例：

```yaml
providers:
  openai_compatible:
    protocol: openai_chat_completions
    api_key_env: OPENAI_API_KEY
    base_url_env: OPENAI_BASE_URL
    text_model: gpt-4.1
    vision_model: gpt-4.1
    temperature: 0.4
    max_tokens: 8000
    timeout_seconds: 90
```

Provider 层必须暴露能力标记：

```python
class ProviderCapabilities:
    supports_vision: bool
    supports_json_mode: bool
    supports_tool_use: bool
    supports_streaming: bool
    max_context_tokens: int
```

Worker 根据能力决定是否传图片、是否需要 OCR fallback、是否启用 JSON repair。

### 5.1.4 Fake Provider

必须实现 `fake.py`：

- 根据 prompt 名称返回固定 JSON。
- 可配置返回非法 JSON，用于测试 repair。
- 可配置超时、异常、空输出。
- CI 默认使用 fake provider，真实模型测试只在显式配置时运行。

### 5.2 配置方式

新增配置文件：

```text
config/models.yml
```

示例：

```yaml
default_provider: openai

providers:
  openai:
    protocol: openai_responses
    api_key_env: OPENAI_API_KEY
    base_url_env: OPENAI_BASE_URL
    text_model: gpt-4.1
    reasoning_model: o4-mini
    vision_model: gpt-4.1
    temperature: 0.4
    timeout_seconds: 90

  anthropic:
    protocol: anthropic_messages
    api_key_env: ANTHROPIC_API_KEY
    base_url_env: ANTHROPIC_BASE_URL
    text_model: claude-sonnet-4-20250514
    vision_model: claude-sonnet-4-20250514
    temperature: 0.3
    timeout_seconds: 120

  local:
    protocol: openai_chat_completions
    endpoint: http://localhost:11434/v1
    text_model: qwen2.5:14b
```

CLI 增加：

```bash
ppt-agent run --job workspace/jobs/demo --model-profile openai
ppt-agent run --job workspace/jobs/demo --from outline-generation --force
```

### 5.3 各阶段模型使用策略

| 阶段 | 模型能力 | 输出 |
|------|----------|------|
| 文档分析 | 长文本理解、OCR 结果归纳、事实抽取 | `source_summary.json` |
| 大纲生成 | 结构规划、行业表达、故事线组织 | `outline.json` |
| 模板匹配 | 查询改写、模板候选解释 | `selected_template.json` |
| 设计规划 | 视觉风格、布局选择、密度控制 | `slide_design_plan.json` |
| 内容映射 | 文案压缩、分区填充、图片 prompt | `slide_contents.json` |
| 验证建议 | 问题归因、人工修改建议 | `validation_report.json` |

## 6. Skill 包设计与书写规范

当前 `.catpaw/skills/ppt-*` 已经存在，但多数仍是 scaffold。后续要把 Skill 当成 Agent 行为契约，而不是简单提示词片段。

### 6.1 Skill 包目录标准

每个 PPT Skill 使用统一结构：

```text
.catpaw/skills/<skill-name>/
├── SKILL.md
├── examples/
│   ├── input.example.json
│   ├── output.example.json
│   └── bad-output.example.json
├── references/
│   ├── schema.md
│   ├── prompt-patterns.md
│   └── quality-checklist.md
└── scripts/
    └── optional_helper.py
```

不是每个 Skill 都必须有脚本，但每个 Skill 至少要有：

- `SKILL.md`
- 一个输入示例
- 一个输出示例
- 一个质量 checklist

### 6.2 SKILL.md 标准结构

每个 `SKILL.md` 必须包含：

```markdown
# Skill Name

## 触发时机
说明哪个 phase 使用，哪些请求不应该使用。

## 输入产物
列出必须读取的 JSON、图片或 PPT 文件。

## 输出产物
列出必须写入的 artifact 和 schema。

## 工作步骤
1. ...
2. ...

## 质量规则
- ...

## 禁止事项
- ...

## Fallback 策略
- ...

## 示例
指向 examples/。
```

### 6.3 各 Skill 需要补齐的设计

| Skill | 当前缺口 | 必须补齐 |
|-------|----------|----------|
| `ppt-outline-generator` | 只有宽泛规则 | 场景结构模板、source_refs 规则、不同 audience 的大纲策略 |
| `ppt-template-matcher` | 缺评分细节 | RAG 查询构造、评分权重、fallback reason 规范 |
| `ppt-design-director` | 缺设计系统细节 | layout grammar、text_budget、visual_strategy、design_risks |
| `ppt-content-mapper` | 缺 zone 适配规则 | TemplateZone 映射、标题压缩、bullet 预算、用户图片匹配 |
| `ppt-image-layer` | 缺生图 prompt 规范 | GPTImage2 batch config、参考图使用、禁止文字栅格化 |
| `ppt-assembler` | 缺 PPTX 组装规则 | python-pptx 写入规则、模板保真、可编辑文本验证 |
| `gptimage2-generator` | 已相对完整 | 只通过 adapter 调用，不复制 API 逻辑 |

### 6.4 Skill 与 Worker 的责任边界

Skill 负责：

- 描述阶段行为。
- 给 LLM prompt 和示例。
- 规定质量规则。
- 提供脚本或参考资料。

Worker 负责：

- 读取和写入 artifact。
- 调用 `LLMClient`。
- 调用工具和脚本。
- 做 schema 校验。
- 处理 fallback。

禁止 Skill 直接决定全局流程；全局流程只能由 Coordinator 和 Dispatcher 决定。

### 6.5 Skill 加载策略

| Phase | Skill |
|-------|-------|
| `outline_generation` | `ppt-outline-generator` |
| `template_matching` | `ppt-template-matcher` |
| `design_planning` | `ppt-design-director` |
| `content_mapping` | `ppt-content-mapper` |
| `visual_generation` | `ppt-image-layer` + `gptimage2-generator` |
| `ppt_assembly` | `ppt-assembler` |
| `verification` | 后续新增 `ppt-verifier` 或补齐 verifier references |

加载规则：

- Skill 内容进入当前 phase context。
- examples 只在需要 few-shot 时加载。
- references 按需加载，不默认全量加载。
- 已加载 Skill 写入 history，便于复盘。

## 7. 核心产物合约

当前已有 JSON schema，应继续沿用。后续开发必须保证以下产物稳定存在：

```text
source_summary.json
outline.json
selected_template.json
template_meta.json
slide_design_plan.json
slide_contents.json
image_generation_config.json
image_generation_report.json
final.pptx
validation_report.json
```

新增建议产物：

```text
materials.json               # SDD ProjectMaterial 的落地产物
template_zone_map.json       # 真实模板解析后的区域地图
presentation_artifact.json   # SDD PresentationArtifact 的落地产物
render_report.json           # PPT 渲染检查原始结果
model_calls.jsonl            # 模型调用审计日志，不记录敏感原文时只记录摘要
```

### 7.1 SDD 数据模型承接关系

从 `specs/001-ppt-generation-agent/data-model.md` 看，当前计划还漏了承接几个核心实体：

| SDD 实体 | 当前状态 | 后续落点 |
|----------|----------|----------|
| `ProjectMaterial` | 未独立落盘 | 新增 `materials.json`，记录每个输入文件、类型、OCR、实体和 relevance |
| `TemplateZone` | 仅在 meta/schema 中间接存在 | 在 `template_zone_map.json` 中显式表达 |
| `ThemeProfile` | 有代码骨架 | 在 `slide_design_plan.json` 中完整落地 |
| `LayoutGrammar` | 有代码骨架 | 在 design director Skill references 中补齐 |
| `ImageGenerationJob` | 未独立表达 | 在 `image_generation_config.json` 中按 slide/job 展开 |
| `PresentationArtifact` | 未独立表达 | 新增 `presentation_artifact.json`，记录 final.pptx、预览图、assembly_mode |
| `SlideValidationResult` | 基础存在 | 接入真实 PPTX/render 检查结果 |

### 7.2 Contract 对齐要求

`specs/001-ppt-generation-agent/contracts/` 已有以下 schema：

- `dispatch-request.schema.json`
- `dispatch-decision.schema.json`
- `knowledge-base-config.schema.json`
- `source-summary.schema.json`
- `outline.schema.json`
- `selected-template.schema.json`
- `template-meta.schema.json`
- `slide-design-plan.schema.json`
- `slide-contents.schema.json`
- `image-generation-config.schema.json`
- `image-generation-report.schema.json`
- `validation-report.schema.json`
- `workflow-events.schema.json`

如果新增 `materials.json`、`template_zone_map.json`、`presentation_artifact.json`、`render_report.json`，必须同步新增 schema 和 contract tests，避免隐式数据结构扩散。

### 7.3 Quickstart 对齐要求

`quickstart.md` 要求至少一个真实模板包含：

```text
templates/<domain>/<template-id>/
├── template.pptx
├── meta.json
└── preview/
```

当前仓库只有 `templates/index.json` 和 `templates/sample/default/meta.json`，与 quickstart 的预期不一致。真实 PPT 输出前必须先建设模板库。

## 8. 阶段一：真实文档分析

### 8.1 目标

把用户材料转成高质量 `source_summary.json`，为后续大纲和设计提供事实基础。

### 8.2 实现内容

1. 支持 `pdf`、`docx`、`txt`、`md`、`pptx`、图片文件。
2. 对图片执行 OCR，提取证书编号、系统名称、截图中文字。
3. 对材料进行分块，避免一次性塞入上下文。
4. 先生成 `materials.json`，再使用 LLM 归纳：
   - 项目名称
   - 业务领域
   - 目标受众
   - 核心痛点
   - 产品能力
   - 价值主张
   - 关键证据
   - 风险和不确定信息
5. 所有事实必须带 `source_refs`。
6. 不确定内容必须标记为 `suggested` 或写入 `warnings`。

### 8.2.1 `materials.json` 建议结构

```json
{
  "materials": [
    {
      "material_id": "mat_001",
      "file_name": "项目计划书.docx",
      "material_type": "project_plan",
      "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "source_path": "input/项目计划书.docx",
      "summary": "项目计划书，描述系统背景、功能和价值。",
      "detected_entities": ["智能图书管理系统"],
      "relevance": "primary",
      "warnings": []
    }
  ]
}
```

### 8.3 验收标准

- 输入图书管理系统样例时，能识别项目名、系统能力、截图用途。
- 输入包含证书图片时，能识别为证书类材料，并标记适合放在资质/成果页。
- `source_summary.json` 通过 schema 校验。

## 9. 阶段二：真实大纲生成

### 9.1 目标

用真实 LLM 根据项目材料生成适合场景的 PPT 结构，而不是固定 8 页。

### 9.2 大纲策略

按场景选择结构模板：

| 场景 | 推荐结构 |
------|----------|
| 路演 | 封面、痛点、方案、产品、市场、商业模式、竞争优势、团队、计划、融资 |
| 比赛答辩 | 封面、背景、问题、创新点、系统设计、功能展示、成果、价值、总结 |
| 项目汇报 | 封面、背景、目标、进展、成果、问题、计划、资源诉求 |
| 产品介绍 | 封面、客户问题、产品定位、核心功能、场景演示、价值、案例、部署方案 |

### 9.3 LLM 输出要求

- 每页必须有 `slide_index`、`type`、`title`、`purpose`、`bullets`、`source_refs`、`image_needs`。
- 页数根据材料和目标场景自动控制，默认 10-15 页。
- 不得生成无来源的具体数字和成果。
- 可提出建议性表达，但必须标记需要用户审核。

### 9.4 验收标准

- 同一份材料指定不同 audience 时，大纲结构和语气有明显差异。
- 每页至少关联一个来源或说明为什么是建议页。
- 生成结果能被用户直接审核和修改。

## 10. 阶段三：真实模板库和模板匹配

### 9.1 模板库结构

扩展模板目录：

```text
templates/
├── index.json
├── business/report-clean-12/
│   ├── template.pptx
│   ├── meta.json
│   └── preview/
│       ├── slide-001.png
│       └── slide-002.png
├── pitch/tech-blue-15/
└── competition/modern-green-12/
```

`meta.json` 必须描述：

- 模板 ID
- 适用领域
- 适用场景
- 主色、辅助色、字体
- 支持的 layout 类型
- 每页 zones
- 预览图路径

### 9.2 检索策略

真实模板匹配采用混合检索：

```text
query = domain + audience + tone + slide_types + color_preference
  -> BM25
  -> embedding vector search
  -> RRF fusion
  -> LLM rerank
  -> selected_template.json
```

### 9.3 模板评分

评分维度：

| 维度 | 权重 | 说明 |
|------|------|------|
| 场景匹配 | 30% | 路演、汇报、比赛、产品介绍 |
| 行业匹配 | 20% | 科技、教育、医疗、金融等 |
| 页数匹配 | 15% | 模板页数和大纲页数接近 |
| 布局覆盖 | 20% | 是否覆盖封面、图文、流程、数据、总结等 |
| 视觉风格 | 15% | 色调、正式度、受众适配 |

### 9.4 验收标准

- 至少准备 5 套真实模板。
- 两个不同领域样例能选出不同模板。
- 模板不可用时自动降级到通用专业模板。

## 11. 阶段四：模板解析与内容映射

### 10.1 目标

让系统真正理解模板页面结构，把内容映射到合适区域。

### 10.2 模板解析

实现 `template_zone_map.json`：

```json
{
  "template_id": "competition/modern-green-12",
  "slides": [
    {
      "index": 0,
      "layout": "cover",
      "zones": [
        {
          "zone_id": "title_0",
          "type": "title",
          "position": [0.1, 0.28, 0.78, 0.16],
          "font_size": 36,
          "editable": true
        }
      ]
    }
  ]
}
```

优先实现顺序：

1. 读取 `meta.json` 中人工标注 zones。
2. 使用 `python-pptx` 解析 PPT shape。
3. 使用 LibreOffice 导出预览图。
4. OCR 和视觉检测补充未知区域。

### 10.3 内容映射

Content Mapper 使用 LLM 完成：

- 标题压缩。
- bullet 数量控制。
- 不同 layout 的表达形式转换。
- 图片 prompt 生成。
- 用户图片匹配。
- 长文本自动拆页建议。

### 10.4 验收标准

- 真实模板中标题、正文、图片区域能正确填充。
- 用户删除或重排 slide 后，最终 PPT 顺序一致。
- 用户批准文本在最终 PPT 中逐字保留。

## 12. 阶段五：真实图片生成与分配

### 11.1 目标

对需要视觉素材的页面，优先使用用户图片；没有合适图片时调用生图能力。

### 11.2 图片来源优先级

1. 用户上传的产品截图、Logo、证书、现场照片。
2. 模板自带装饰图和图标。
3. GPTImage2 生成的概念图。
4. fallback placeholder。

### 11.3 GPTImage2 接入

复用已有 skill：

```text
.catpaw/skills/gptimage2-generator/
```

Image Generator Worker 负责：

1. 读取 `slide_contents.json`。
2. 生成 `image_generation_config.json`。
3. 调用 skill script。
4. 下载或复制生成图片到：

```text
workspace/jobs/<job-id>/generated_slides/
```

5. 写入 `image_generation_report.json`。
6. 将生成图片路径回写到 `slide_contents.json` 或新增版本文件。

### 11.4 图片 prompt 要求

Prompt 必须包含：

- 页面主题
- 表达目的
- 行业风格
- 模板色系
- 构图要求
- 禁止出现的文字
- 是否需要透明背景
- 是否使用用户参考图

示例：

```text
为一页“智能图书管理系统核心能力”生成科技感产品概念图。
风格：现代、清爽、蓝绿配色、适合比赛答辩 PPT。
画面：图书馆数字化管理界面、书籍流转、数据看板、AI 检索元素。
要求：无可读文字、不出现水印、不替代 PPT 中的真实标题和 bullet。
```

### 11.5 验收标准

- 无账号或调用失败时，仍生成 PPT，但 validation 标记 unresolved fallback。
- 有账号时，至少封面和 2 个概念页能插入真实生成图片。
- 用户上传截图不会被概念图替代。

## 13. 阶段六：真实 PPT 组装

### 12.1 技术选择

推荐使用 `python-pptx` 作为第一阶段实现：

- 可读取真实模板。
- 可复制/操作 slide。
- 可插入文本框、图片、形状。
- 用户文字保持可编辑。

对于复杂模板复制能力不足的情况，引入 LibreOffice bridge 作为后续增强。

### 12.2 组装原则

- 不破坏模板母版和主题。
- 用户批准文本必须写入 PowerPoint 文本 shape。
- 图片插入必须保持比例，不拉伸变形。
- 无图时使用模板风格一致的 placeholder。
- 每页输出前执行 layout fit。

### 12.3 输出文件

```text
workspace/jobs/<job-id>/final.pptx
```

可选调试产物：

```text
workspace/jobs/<job-id>/assembly_debug/
├── slide-001.xml
├── slide-001.png
└── fit-report.json
```

### 12.4 验收标准

- 使用真实模板生成，不是白底 fallback。
- PowerPoint/WPS/Keynote 能正常打开。
- 标题和 bullet 可编辑。
- 图片正常显示且不变形。
- 视觉上达到可交付初稿水平。

## 14. 阶段七：渲染级验证

### 13.1 目标

验证报告不只检查 JSON，还要检查最终 PPT 的真实渲染结果。

### 13.2 检查项

| 检查项 | 实现方式 |
|--------|----------|
| 页数完整 | 读取 PPTX slide 数 |
| 文本存在 | 解析 PPTX shape 文本 |
| 文本可编辑 | 确认文本在 shape 中而非图片中 |
| 文本保真 | 对比 `slide_contents.json` 已批准文本 |
| 文本溢出 | 渲染后检测 bbox 或使用 fit report |
| 图片存在 | 检查 image relationship 和文件 |
| 图片比例 | 对比原图宽高和目标区域比例 |
| fallback | 检查 unresolved fallback flags |
| 视觉密度 | 基于文本量、shape 数、留白比例估算 |

### 13.3 渲染方案

优先方案：

```bash
soffice --headless --convert-to pdf final.pptx
pdftoppm -png final.pdf render/slide
```

备选方案：

- macOS 使用 Keynote/LibreOffice 手动验证。
- CI 环境只做 PPTX 结构检查和文本保真检查。

### 13.4 验收标准

- 故意制造超长标题时，validation 能标记溢出风险。
- 删除某页文本时，validation 能标记缺失内容。
- 将批准文本放入图片而不是文本框时，validation 能标记不可编辑。

## 15. Agent 编排改造

### 15.1 当前问题

当前 `workflow.py` 直接调用：

```python
PHASE_TO_WORKER[phase](workspace, force=force)
```

这绕过了 dispatcher，导致技能加载、权限、重试、fallback、上下文压缩没有真正生效。

### 15.2 目标实现

改为：

```text
workflow
  -> build DispatchRequest
  -> dispatcher.dispatch
  -> skill_loader.load
  -> build context bundle
  -> execute selected worker
  -> validate expected outputs
  -> emit events
```

`DispatchRequest` 必须携带：

- 当前 phase。
- capability。
- 输入 artifact 列表。
- 用户决策。
- 模型 profile。
- context policy。
- 运行约束，如是否允许联网、生图并发、最大成本。

`DispatchDecision` 必须返回：

- Worker。
- Skill。
- 允许工具。
- permission profile。
- retry/fallback policy。
- expected outputs。
- model profile override。
- context bundle。

现有 `src/ppt_agent/models/dispatch.py` 需要扩展：

- `allowed_tools`
- `model_profile`
- `fallback_policy`
- `cost_budget`
- `skill_references`

### 15.3 验收标准

- 每个 phase 都有一条 dispatch decision 记录。
- `history.jsonl` 能回放关键阶段。
- Worker 失败时按 route 配置执行重试或 fallback。
- 技能只在需要时加载。

## 16. 开发里程碑

### Milestone 0：SDD 对齐和 Skill 设计补齐

- [ ] 修订本文档与 `spec.md`、`plan.md`、`research.md`、`data-model.md`、`quickstart.md` 的差异。
- [ ] 明确 `materials.json`、`template_zone_map.json`、`presentation_artifact.json`、`render_report.json` 是否新增 schema。
- [ ] 补齐所有 `.catpaw/skills/ppt-*` 的输入、输出、步骤、质量规则、fallback、examples、references。
- [ ] 更新 README，修正“代码尚未开始实现”的过期描述。

验收：开发者只看 SDD、本开发文档和 Skill 文件，就能理解每个阶段如何落地。

### Milestone 1：真实 LLM 基础接入

- [ ] 新增 `src/ppt_agent/llm/`。
- [ ] 新增 `config/models.yml`。
- [ ] 支持 Anthropic Messages API。
- [ ] 支持 OpenAI-compatible API。
- [ ] 支持 fake provider。
- [ ] Document Analyst 接入 LLM 事实抽取。
- [ ] Outline Generator 接入 LLM 大纲生成。
- [ ] 所有 LLM 输出经过 schema 校验。
- [ ] 增加模型调用失败 fallback 测试。

验收：同一份输入能生成比固定模板更贴合材料的大纲。

### Milestone 2：真实模板库与模板匹配

- [ ] 准备至少 5 套真实 PPT 模板。
- [ ] 为模板补齐 `meta.json` 和 preview。
- [ ] Template Matcher 使用真实模板索引。
- [ ] 支持模板评分和 fallback。

验收：不同领域输入能选择不同模板。

### Milestone 3：模板解析和内容映射

- [ ] 生成 `template_zone_map.json`。
- [ ] Content Mapper 使用 zone map。
- [ ] LLM 根据 zone 长度压缩文本。
- [ ] 用户图片按相关性匹配 slide。

验收：真实模板页面能被正确填充。

### Milestone 4：真实视觉生成

- [ ] Image Generator 调用 `gptimage2-generator`。
- [ ] 生成图片落盘。
- [ ] 图片路径回写 slide 内容。
- [ ] PPT 组装插入生成图。

验收：至少 3 页包含真实视觉图，不影响可编辑文本。

### Milestone 5：真实 PPT 组装

- [ ] 引入 `python-pptx`。
- [ ] 使用模板生成最终 PPT。
- [ ] 支持文本、图片、placeholder、基础形状。
- [ ] 保留模板主题和母版。

验收：`final.pptx` 视觉效果达到可人工微调交付的初稿水平。

### Milestone 6：渲染级验证

- [ ] LibreOffice/PDF 渲染能力接入。
- [ ] 检查文本保真和可编辑性。
- [ ] 检查图片存在和比例。
- [ ] 输出详细 `validation_report.json`。

验收：人工构造的问题能被 validation 稳定识别。

### Milestone 7：端到端打磨

- [ ] 完成 quickstart 真实模型流程。
- [ ] 更新 README。
- [ ] 增加 2-3 个样例 job。
- [ ] 增加性能预算测试。
- [ ] 记录模型成本和耗时。

验收：一条 CLI 命令能从样例输入生成精美 PPT。

## 17. 端到端目标命令

最终期望用户这样使用：

```bash
export OPENAI_API_KEY=...

ppt-agent create-job \
  --input ./图书管理系统 \
  --output workspace/jobs/book-system-demo

ppt-agent run \
  --job workspace/jobs/book-system-demo \
  --model-profile openai \
  --until content-review

# 用户检查并修改 slide_contents.json 后
ppt-agent approve \
  --job workspace/jobs/book-system-demo

ppt-agent run \
  --job workspace/jobs/book-system-demo \
  --from visual-generation \
  --model-profile openai
```

最终输出：

```text
workspace/jobs/book-system-demo/final.pptx
workspace/jobs/book-system-demo/validation_report.json
```

## 18. 测试策略

### 18.1 单元测试

- LLM provider 配置解析。
- Anthropic 消息转换。
- OpenAI-compatible 消息转换。
- fake provider 异常注入。
- JSON 修复和 schema 校验。
- 模板评分。
- zone map 解析。
- 文本 fit 计算。
- 图片比例计算。

### 18.2 集成测试

- 无模型 key 时 fallback 仍可产出 PPT。
- 有 fake LLM provider 时可稳定生成结构化产物。
- Anthropic provider 在 mock server 下能完成 JSON 输出。
- OpenAI-compatible provider 在 mock server 下能完成 JSON 输出。
- 真实模板能生成可打开 PPTX。
- 用户修改文本后最终 PPT 保真。
- 图片生成失败时 validation 标记 fallback。

### 18.3 端到端测试

准备三个 fixture：

```text
tests/fixtures/projects/
├── book-management/
├── startup-pitch/
└── product-report/
```

每个 fixture 验证：

- 产物完整。
- PPT 可打开。
- 文本可编辑。
- 至少一页使用用户图片。
- 至少一页使用生成图片或明确 fallback。

## 19. 风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| LLM 输出 JSON 不稳定 | 阶段失败 | schema 校验、自动修复、fallback |
| 模型幻觉 | 生成不实内容 | 强制 source_refs，不确定内容标记 review |
| 生图服务不可用 | PPT 视觉不足 | 用户图片优先、模板图优先、placeholder fallback |
| python-pptx 模板复制能力有限 | 真实模板效果受限 | 先支持标注模板，后续引入 LibreOffice bridge |
| 渲染环境依赖重 | CI 不稳定 | 本地完整验证，CI 做结构级验证 |
| 成本不可控 | 大量模型调用费用高 | 分块摘要缓存、阶段缓存、模型调用审计 |

## 20. 质量门禁

每个 milestone 合入前必须满足：

- 所有现有测试通过。
- 新增核心能力必须有测试。
- 所有 JSON 产物通过 schema 校验。
- `final.pptx` 能被 PowerPoint/WPS 打开。
- 用户批准文本保持可编辑。
- fallback 情况必须写入 `validation_report.json`。
- README 和开发文档同步更新。

## 21. 最小可交付定义

达到以下标准，才算完成“接入真实大模型，经过 Agent 后产出精美 PPT”的目标：

1. 同时支持 Anthropic 原生协议和 OpenAI-compatible 协议，fake provider 可用于 CI。
2. 使用真实 LLM 完成文档分析、大纲生成、设计规划和内容映射。
3. 所有 PPT Skill 都有完整 `SKILL.md`、examples、references 或明确说明不需要。
4. SDD 中的核心实体都有对应 artifact、内部模型或明确的非落地理由。
5. 使用至少 5 套真实模板中的一套生成 PPT。
6. 使用用户图片或生成图片填充至少 3 个视觉区域。
7. 输出的 `final.pptx` 不是白底 fallback，而是保留模板风格。
8. 用户批准的标题和 bullet 在 PPT 中可编辑且内容一致。
9. `validation_report.json` 能说明是否存在文本溢出、图片缺失、fallback 未解决等问题。
10. 从样例输入到最终 PPT 的端到端流程可复现。

## 22. 当前补漏结论

对照 SDD 后，当前开发还需要补齐：

- `README.md` 状态过期，需要从“未开始实现”改成“fallback MVP 已实现，真实模型和真实模板待接入”。
- `quickstart.md` 要求真实模板目录，但仓库当前没有 `template.pptx` 和 preview。
- `data-model.md` 中的 `ProjectMaterial` 没有对应 artifact，建议新增 `materials.json`。
- `ImageGenerationJob` 没有独立状态，建议在 `image_generation_config.json` 中展开或新增 jobs 字段。
- Skill scaffold 太薄，不能支撑真实 Agent 行为，需要补 examples、references、checklist。
- Workflow 没有使用 dispatcher，和 `research.md` 的 unified dispatch decision 不一致。
- Anthropic 协议支持需要独立 provider，不能只写 OpenAI-compatible provider。
