# PPT-AGENT

PPT-AGENT 是一个“项目材料 → 可编辑 PPT”的智能生成系统。它面向路演、比赛、项目汇报、产品介绍等场景，目标是把项目计划书、软著证书、产品截图、Logo、业务图片等材料，自动整理成一份结构清晰、视觉统一、内容可编辑的 PowerPoint 文件。

> 当前状态：项目处于 SDD / Spec Kit 设计阶段，已完成架构设计、功能规格、实施计划、数据合约和任务拆解；核心工程代码尚未开始实现。

## 解决什么问题

传统做 PPT 的流程通常是：

1. 人工阅读项目计划书。
2. 手动提炼大纲。
3. 找模板。
4. 一页页复制文字和截图。
5. 补配图、调排版。
6. 最后检查有没有错字、溢出、图片变形。

PPT-AGENT 希望把这套流程拆成可控、可审查、可恢复的 Agent 工作流：

```text
项目材料
  -> 文档分析
  -> 大纲生成
  -> 模板匹配
  -> 内容映射
  -> 图片生成/分配
  -> PPT 组装
  -> 输出验证
  -> final.pptx
```

系统不是简单生成一张 PPT 图片，而是生成真正可编辑的 `.pptx`。用户确认过的标题、正文和 bullet 必须保留为 PowerPoint 文本对象。

## 核心功能规划

- **材料解析**：读取项目计划书、截图、证书、Logo 等输入材料，提取项目名称、产品能力、关键证据、业务卖点和图片清单。
- **大纲生成**：根据行业、受众和项目材料生成 PPT 页面结构。
- **模板匹配**：从模板库中检索适合的风格、版式和页数。
- **内容映射**：把大纲内容映射到模板区域，生成可人工审核的 `slide_contents.json`。
- **设计导演**：在内容映射前选择主题、版式语法、视觉密度和页面表达方式，避免只是“机械填模板”。
- **图片生成**：复用现有 `gptimage2-generator` skill，为封面、概念图、产品页等生成视觉素材。
- **PPT 组装**：生成 `.pptx`，并保证用户确认文字可编辑。
- **输出验证**：检查页数、内容完整性、文字可编辑性、溢出、图片变形和 fallback 情况。
- **可扩展知识库**：支持模板知识库、页面结构知识库、行业知识库，并预留 Qdrant / Milvus / pgvector 等向量数据库适配。

## 当前仓库包含什么

```text
.
├── AGENT_ARCHITECTURE.md              # 通用 Agent Runtime 架构
├── PPT-Agent-架构设计.md              # PPT Agent 业务架构
├── gptimage2-API文档.md               # GPTImage2.online API 分析
├── .catpaw/skills/gptimage2-generator # 已有生图 skill
├── .specify/                          # Spec Kit / SDD 工程配置
├── .agents/skills/                    # Spec Kit 相关 skills
├── specs/001-ppt-generation-agent/    # 当前 feature 的 SDD 文档
├── js_analysis/                       # GPTImage2 前端分析材料
└── 图书管理系统/                       # 示例/实验材料
```

当前还没有 `src/ppt_agent/` 实现目录。后续应按照 [tasks.md](./specs/001-ppt-generation-agent/tasks.md) 从项目骨架开始实现。

## 架构设计

PPT-AGENT 采用 **Coordinator + Worker** 架构。

Coordinator 是纯编排器，只负责：

- 判断当前任务阶段
- 选择 Worker
- 加载对应 skill
- 分配上下文
- 记录事件
- 处理重试和 fallback

具体工作由 Worker 完成：

```text
document_analyst     # 文档/图片分析
outline_generator    # PPT 大纲生成
template_matcher     # 模板匹配
content_mapper       # 内容映射
image_generator      # 图片生成
ppt_assembler        # PPT 组装
ppt_verifier         # 输出验证
```

统一分配入口计划为：

```text
src/ppt_agent/coordinator/dispatcher.py
```

路由配置计划为：

```text
config/dispatcher.yml
```

这样后续新增 Worker、替换 skill、增加 fallback 策略时，不需要把逻辑写死在主流程里。

## 工作流产物

每次生成任务都会创建独立工作区：

```text
workspace/jobs/<job-id>/
├── input/                    # 用户输入材料
├── history.jsonl             # 事件流和执行记录
├── session.md                # 当前任务状态、错误和用户决策
├── source_summary.json       # 项目材料摘要
├── outline.json              # PPT 大纲
├── selected_template.json    # 模板匹配结果
├── template_meta.json        # 模板版式描述
├── slide_contents.json       # 用户可审核的页面内容
├── image_generation_config.json
├── generated_slides/
├── layer_analysis/
├── final.pptx
└── validation_report.json
```

这种文件系统优先的方式有三个好处：

- 中间结果可读、可改、可调试。
- 失败后可以从某个阶段恢复。
- 后续接入 Web UI 或远程任务系统时，状态边界清晰。

## 知识库与检索

模板匹配和大纲生成不能只靠写死规则。项目规划了可配置知识库层：

```text
config/knowledge-bases/
├── templates.yml        # 模板库
├── slide-patterns.yml   # PPT 页面结构和优秀案例
└── domain-knowledge.yml # 行业术语和表达方式
```

检索层计划支持：

- BM25
- 向量检索
- 元数据过滤
- RRF 融合
- reranker 重排
- 本地向量索引
- Qdrant
- Milvus
- pgvector

Worker 不直接依赖某个向量数据库客户端，而是统一调用：

```text
src/ppt_agent/retrieval/query_router.py
```

后续新增向量数据库或检索方法时，只需要增加 adapter 和配置，不重写业务 Worker。

## 设计系统

如果要追求精美 PPT，PPT-AGENT 不能只做“模板填充”。计划中会加入一层设计系统：

```text
src/ppt_agent/design/
├── theme.py
├── layout_grammar.py
├── visual_density.py
├── design_scorer.py
└── rewrite_suggestions.py
```

核心概念：

- **ThemeProfile**：颜色、字体、间距、形状、图表和图片风格 token。
- **LayoutGrammar**：封面、章节页、三卡片、左右图文、时间线、对比页、流程图、截图标注页、数据洞察页等版式语法。
- **VisualDensity**：控制每页信息密度，避免文字太满、留白不足。
- **DesignScorer**：检查视觉层级、对齐、间距、图片一致性和风格统一性。

对应的 skill 是：

```text
.catpaw/skills/ppt-design-director/
```

它会在 `outline.json` 和 `template_meta.json` 之后生成：

```text
slide_design_plan.json
```

再由 `content_mapper` 根据设计计划生成 `slide_contents.json`。

## Skills 设计

项目采用渐进式 skill 加载。每个阶段只加载当前需要的 skill，避免上下文膨胀。

计划新增的 PPT 专用 skills：

```text
.catpaw/skills/ppt-outline-generator/
.catpaw/skills/ppt-template-matcher/
.catpaw/skills/ppt-design-director/
.catpaw/skills/ppt-content-mapper/
.catpaw/skills/ppt-image-layer/
.catpaw/skills/ppt-assembler/
```

已有生图 skill：

```text
.catpaw/skills/gptimage2-generator/
```

`gptimage2-generator` 已经包含：

- 登录认证
- 积分查询
- 参考图上传
- 文生图 / 图生图
- 结果轮询
- 图片下载
- 账号切换

PPT-AGENT 后续只应该写 adapter，把 `slide_contents.json` 转换成该 skill 的批量生图配置，不应该重复实现 GPTImage2 API。

## 输出质量验证

最终生成的 `validation_report.json` 不负责评价“好不好看”，而是检查能不能交付：

- PPT 页数是否正确
- 必要章节是否存在
- 用户确认文本是否保留
- 用户确认文本是否可编辑
- 文本是否溢出
- 图片是否变形
- 生图失败是否有 fallback
- 哪些页面需要人工复查

其中 `text_editable` 是强制检查项，因为这是本项目和“整页图片生成器”的关键区别。

## SDD 文档

当前 feature 目录：

```text
specs/001-ppt-generation-agent/
```

主要文档：

- [spec.md](./specs/001-ppt-generation-agent/spec.md)：功能规格
- [plan.md](./specs/001-ppt-generation-agent/plan.md)：实施计划
- [research.md](./specs/001-ppt-generation-agent/research.md)：架构决策
- [data-model.md](./specs/001-ppt-generation-agent/data-model.md)：数据模型
- [quickstart.md](./specs/001-ppt-generation-agent/quickstart.md)：验证指南
- [tasks.md](./specs/001-ppt-generation-agent/tasks.md)：实现任务清单
- [contracts/](./specs/001-ppt-generation-agent/contracts/)：JSON Schema 合约

重点合约：

```text
outline.schema.json
slide-contents.schema.json
slide-design-plan.schema.json
template-meta.schema.json
image-generation-config.schema.json
validation-report.schema.json
workflow-events.schema.json
knowledge-base-config.schema.json
dispatch-request.schema.json
dispatch-decision.schema.json
```

## 计划中的目录结构

后续核心代码计划放在：

```text
src/ppt_agent/
├── coordinator/
├── context/
├── skills/
├── workers/
├── retrieval/
├── models/
├── assembly/
└── cli.py
```

测试目录：

```text
tests/
├── contract/
├── integration/
├── unit/
└── fixtures/
```

模板目录：

```text
templates/
├── index.json
└── <domain>/<template-id>/
    ├── template.pptx
    ├── meta.json
    └── preview/
```

## 使用方式（规划中）

当前还没有可运行 CLI。计划中的使用方式如下：

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

目标输出：

```text
workspace/jobs/sample-project/final.pptx
workspace/jobs/sample-project/validation_report.json
```

## 开发路线

建议按以下顺序实现：

1. 创建 Python 项目骨架和 CLI。
2. 实现 job workspace、JSON schema 校验、事件日志。
3. 实现统一 dispatch 入口和 routing 配置。
4. 实现上下文压缩、session、history 管理。
5. 创建 PPT 专用 skills 的 SKILL.md 和示例。
6. 跑通无生图、无向量数据库的 fallback PPT MVP。
7. 增加 `slide_contents.json` 审核和确认流程。
8. 实现可配置知识库与模板匹配。
9. 接入 GPTImage2 adapter。
10. 实现最终 PPT 验证。

任务拆解见：[tasks.md](./specs/001-ppt-generation-agent/tasks.md)

## MVP 范围

第一版建议只做：

- 单机 CLI
- 单用户任务
- 本地文件工作区
- 项目计划书 + 图片输入
- 16:9 PPT
- 本地模板库
- fallback PPT 生成
- 可编辑文本
- 基础验证报告

暂不做：

- Web UI
- 多用户任务调度
- 模板自训练
- 多模型生图
- 动画
- 复杂图表自动重建

## 质量门禁

功能完成前至少需要满足：

- 所有 JSON 中间产物都能通过 schema 校验。
- `slide_contents.json` 中用户确认过的文字必须在 `final.pptx` 中保持可编辑。
- `validation_report.json` 必须包含 `text_editable` 检查结果。
- GPTImage2 不可用时，流程仍然能生成 fallback PPT。
- dispatch 决策必须写入 `history.jsonl`。
- 上下文压缩不能丢失用户确认内容和错误记录。
- Quickstart 场景必须跑通。

## 许可证

当前尚未声明许可证。
# PPT Generation Agent

This repository now contains a file-system-first MVP for the Spec Kit feature
`001-ppt-generation-agent`.

## Local Workflow

```bash
PYTHONPATH=src python3 -m ppt_agent.cli create-job \
  --input tests/fixtures/sample_project/input \
  --output workspace/jobs/sample-project

PYTHONPATH=src python3 -m ppt_agent.cli run \
  --job workspace/jobs/sample-project \
  --until content-review

PYTHONPATH=src python3 -m ppt_agent.cli approve \
  --job workspace/jobs/sample-project

PYTHONPATH=src python3 -m ppt_agent.cli run \
  --job workspace/jobs/sample-project \
  --from visual-generation
```

The MVP writes inspectable JSON artifacts, uses fallback template metadata, and
creates `final.pptx` with editable DrawingML text. Visual generation currently
falls back to placeholders unless a future GPTImage2 integration is configured.
