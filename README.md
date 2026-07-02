# PPT-AGENT

PPT-AGENT 是一个面向“项目材料自动生成可编辑 PPT”的 Agent 工作流项目。它的目标是把项目计划书、软著证书、产品截图、Logo 和其他辅助图片，转换成一份结构完整、视觉合适、文字可编辑的 PowerPoint 文件。

项目当前处于 SDD / Spec Kit 设计阶段。仓库里已经包含架构文档、功能规格、实施计划、数据模型、接口合约、Quickstart 验证说明和任务拆解，后续可以按 `tasks.md` 逐步进入工程实现。

## 项目目标

系统需要接收用户提供的项目材料，并通过阶段化流程产出 `.pptx` 文件：

1. 分析项目文档和图片。
2. 生成 PPT 大纲。
3. 匹配合适的 PPT 模板。
4. 将确认后的内容映射到模板版式中。
5. 生成或分配视觉素材。
6. 组装成可编辑的 PowerPoint。
7. 校验最终输出，并报告需要人工复查的问题。

最核心的要求不是“生成一张 PPT 图片”，而是：用户确认过的标题、副标题和正文 bullet 必须在最终 PPT 中保持可编辑。

## 当前状态

当前仓库包含：

- 通用 Agent Runtime 架构：[AGENT_ARCHITECTURE.md](./AGENT_ARCHITECTURE.md)
- PPT Agent 架构设计：[PPT-Agent-架构设计.md](./PPT-Agent-%E6%9E%B6%E6%9E%84%E8%AE%BE%E8%AE%A1.md)
- GPTImage2 API 分析文档：[gptimage2-API文档.md](./gptimage2-API%E6%96%87%E6%A1%A3.md)
- Spec Kit / SDD 文档：[specs/001-ppt-generation-agent/](./specs/001-ppt-generation-agent/)
- 已有 GPTImage2 生图 skill：[.catpaw/skills/gptimage2-generator/](./.catpaw/skills/gptimage2-generator/)

目前还没有创建 `src/ppt_agent/` 下的正式实现代码。下一步应按照 [tasks.md](./specs/001-ppt-generation-agent/tasks.md) 开始实现。

## 架构概览

计划中的运行时采用 Coordinator-Worker 架构。

Coordinator 是纯编排器，不直接解析文件、不做 OCR、不调用图片 API，也不直接组装 PPT。它只负责通过统一分配入口，把任务派发给专门的 Worker：

```text
src/ppt_agent/coordinator/dispatcher.py
```

计划中的 Worker 包括：

```text
document_analyst.py
outline_generator.py
template_matcher.py
content_mapper.py
image_generator.py
ppt_assembler.py
ppt_verifier.py
```

Worker 之间通过明确的文件产物交接，而不是共享内存状态。核心产物包括：

```text
source_summary.json
outline.json
selected_template.json
template_meta.json
slide_contents.json
image_generation_config.json
generated_slides/
layer_analysis/
final.pptx
validation_report.json
history.jsonl
session.md
```

## 工作流程

### 1. 创建任务空间

每次生成任务都会创建独立工作区：

```text
workspace/jobs/<job-id>/
├── input/
├── history.jsonl
├── session.md
├── outline.json
├── slide_contents.json
├── generated_slides/
├── final.pptx
└── validation_report.json
```

这样可以保证中间结果可查看、可恢复、可调试。

### 2. 文档分析

`document_analyst` 从用户材料中提取文本和 OCR 信息，识别文件类型、产品名称、关键事实和辅助证据，并输出：

```text
source_summary.json
```

### 3. 大纲生成

`outline_generator` 根据项目摘要生成结构化大纲：

```text
outline.json
```

大纲包含每页标题、页面目的、关键 bullet、来源引用和视觉需求。

### 4. 模板匹配

`template_matcher` 根据项目行业、受众、风格、页数和版式需求，从模板库中匹配合适模板。

检索层不会写死在某个本地向量库里，而是设计为可配置知识库层。计划支持：

- BM25 稀疏检索
- 向量检索
- 元数据过滤
- RRF 融合排序
- reranker 重排
- 本地向量索引
- Qdrant
- Milvus
- pgvector

知识库配置计划放在：

```text
config/knowledge-bases/
├── templates.yml
├── slide-patterns.yml
└── domain-knowledge.yml
```

### 5. 内容映射

`content_mapper` 将 `outline.json` 和 `template_meta.json` 合并，生成用户可审阅的：

```text
slide_contents.json
```

这是整个流程中最重要的人工确认点。用户可以在这里修改标题、bullet、页面顺序、图片选择和生图 prompt。

### 6. 图片生成

图片生成阶段复用现有 skill：

```text
.catpaw/skills/gptimage2-generator/
```

PPT-AGENT 不应该重复实现 GPTImage2 API 逻辑。正确做法是写一个 adapter，把确认后的 `slide_contents.json` 转成该 skill 支持的批量生图配置，再由该 skill 处理登录、参考图上传、轮询、下载和账号切换。

如果图片生成不可用，流程必须降级：

- 使用模板原图
- 使用用户上传图片
- 使用占位图
- 使用整页背景图 + 可编辑文字覆盖

### 7. PPT 组装

`ppt_assembler` 根据模板、确认后的 slide 内容、用户图片、生图结果和图层分析结果，生成：

```text
final.pptx
```

关键规则：用户确认过的文字必须写成 PowerPoint 可编辑文本对象，不能被压平成图片。

### 8. 输出验证

`ppt_verifier` 是独立的最终检查 Worker，输出：

```text
validation_report.json
```

它负责检查：

- 页数和必要章节是否完整
- 内容是否存在
- 文本是否被保留
- 文本是否可编辑
- 文本是否溢出
- 图片是否变形或越界
- fallback 是否已处理
- 是否存在需要人工复查的页面

验证合约中包含 `text_editable` 字段，因为“可编辑”是本项目的核心验收标准。

## 计划中的 Skills

项目采用渐进式 skill 加载。每个阶段只加载当前需要的 skill，避免把所有能力一次性塞进上下文。

计划新增的 PPT 专用 skill：

```text
.catpaw/skills/ppt-outline-generator/
.catpaw/skills/ppt-template-matcher/
.catpaw/skills/ppt-content-mapper/
.catpaw/skills/ppt-image-layer/
.catpaw/skills/ppt-assembler/
```

已有 skill：

```text
.catpaw/skills/gptimage2-generator/
```

每个新增 skill 建议包含：

```text
SKILL.md
scripts/
references/
examples/
assets/
```

## SDD 文档入口

当前活跃的 Spec Kit feature 是：

```text
specs/001-ppt-generation-agent/
```

关键文档：

- [spec.md](./specs/001-ppt-generation-agent/spec.md)：功能规格
- [plan.md](./specs/001-ppt-generation-agent/plan.md)：实施计划
- [research.md](./specs/001-ppt-generation-agent/research.md)：架构决策
- [data-model.md](./specs/001-ppt-generation-agent/data-model.md)：数据模型
- [quickstart.md](./specs/001-ppt-generation-agent/quickstart.md)：验证指南
- [tasks.md](./specs/001-ppt-generation-agent/tasks.md)：实现任务清单
- [contracts/](./specs/001-ppt-generation-agent/contracts/)：阶段产物 JSON Schema

重要合约：

- `outline.schema.json`
- `slide-contents.schema.json`
- `template-meta.schema.json`
- `image-generation-config.schema.json`
- `validation-report.schema.json`
- `workflow-events.schema.json`
- `knowledge-base-config.schema.json`
- `dispatch-request.schema.json`
- `dispatch-decision.schema.json`

## 建议实现顺序

建议先做一个最小本地 MVP：

1. 创建项目骨架和 CLI。
2. 实现任务工作区、产物 schema、事件日志和统一分配入口。
3. 实现上下文/session 管理和 skill 加载。
4. 先跑通 fallback PPT 路径：
   - 文档分析
   - 大纲生成
   - 基础内容映射
   - 可编辑 PPT 组装
5. 增加 `slide_contents.json` 的用户审核和确认流程。
6. 增加可配置知识库检索和模板匹配。
7. 接入 GPTImage2 adapter 和图片生成。
8. 增加最终验证和 quickstart 测试。

MVP 阶段不应该依赖外部生图服务，也不应该强依赖向量数据库。

## Quickstart 目标形态

计划中的 CLI 形态如下：

```bash
python -m ppt_agent.cli create-job \
  --input ./fixtures/sample_project/ \
  --output ./workspace/jobs/sample-project

python -m ppt_agent.cli run \
  --job ./workspace/jobs/sample-project \
  --until content-review

python -m ppt_agent.cli approve \
  --job ./workspace/jobs/sample-project \
  --slide-contents ./workspace/jobs/sample-project/slide_contents.json

python -m ppt_agent.cli run \
  --job ./workspace/jobs/sample-project \
  --from visual-generation
```

详细验证场景见 [quickstart.md](./specs/001-ppt-generation-agent/quickstart.md)。

## 质量门禁

功能完成前至少需要满足：

- 所有 JSON 中间产物都能通过 schema 校验。
- `slide_contents.json` 中用户确认过的文字必须在 `final.pptx` 中保持可编辑。
- `validation_report.json` 必须包含 `text_editable` 检查结果。
- GPTImage2 不可用时，流程仍然能生成 fallback PPT。
- dispatch 决策必须写入 `history.jsonl`。
- 上下文压缩不能丢失用户确认内容和错误记录。
- Quickstart 场景必须跑通。

## 仓库说明

仓库中还包含规划阶段使用的分析文件和示例文件。SDD 计划中提到的一些实现目录目前还没有创建，后续应按照 [tasks.md](./specs/001-ppt-generation-agent/tasks.md) 逐步实现。
