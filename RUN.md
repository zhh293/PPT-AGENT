# PPT-Agent 运行指南

## 环境准备

```bash
# 依赖安装
pip install python-pptx openai pyyaml pillow

# 进入项目目录
cd PPT-AGENT
```

## 模型配置

`config/models.yml` 已配置 5 个 provider：

| Provider | 需要的 key | 使用方式 |
|----------|-----------|---------|
| `deepseek` (默认) | 已内置 | `--model-profile deepseek` |
| `anthropic` | `export ANTHROPIC_API_KEY=sk-xxx` | `--model-profile anthropic` |
| `openai` | `export OPENAI_API_KEY=sk-xxx` | `--model-profile openai` |
| `local` (Ollama) | `ollama pull qwen2.5:14b` | `--model-profile local` |
| `fake` | 无（确定性降级模式） | `--model-profile fake` |

## 三步走

### 1. 准备输入材料

```bash
mkdir -p workspace/examples/my-project
# 放入你的项目文件：.txt / .md / .docx / .pdf / 图片
cp 项目计划书.txt workspace/examples/my-project/
```

### 2. 创建 job 并生成 PPT

```bash
# 创建 job 工作区
PYTHONPATH="src" python -m ppt_agent.cli create-job \
  --input workspace/examples/my-project \
  --output workspace/jobs/demo

# 阶段一：文档分析 → 大纲 → 模板匹配 → 设计 → 内容映射
# 使用真实 LLM (DeepSeek)
PYTHONPATH="src" python -m ppt_agent.cli run \
  --job workspace/jobs/demo \
  --model-profile deepseek

# 或者先用 fake 模式快速验证（不需要 API key，用确定性算法）
PYTHONPATH="src" python -m ppt_agent.cli run \
  --job workspace/jobs/demo \
  --model-profile fake
```

此时停下来，检查 `workspace/jobs/demo/slide_contents.json` 中的内容是否正确。

### 3. 审批 + 继续生成

```bash
# 审批 slide_contents（确认内容无误）
PYTHONPATH="src" python -m ppt_agent.cli approve \
  --job workspace/jobs/demo

# 阶段二：图片生成 → PPT 组装 → 验证
PYTHONPATH="src" python -m ppt_agent.cli run \
  --job workspace/jobs/demo \
  --from visual_generation \
  --model-profile fake \
  --force
```

## 最终产出

```
workspace/jobs/demo/
├── final.pptx              ← 最终可编辑 PPT
├── validation_report.json  ← 验证报告
├── source_summary.json     ← 文档分析结果
├── outline.json            ← 大纲（10-15 页）
├── selected_template.json  ← 选中的模板
├── template_meta.json      ← 模板布局描述
├── template_zones.json     ← 模板区域映射
├── slide_design_plan.json  ← 设计方案
├── slide_contents.json     ← 每页内容（可手动编辑）
├── image_generation_config.json
├── image_generation_report.json
├── model_calls.jsonl       ← LLM 调用审计日志
├── history.jsonl           ← 事件历史
└── review_pending.json     ← 审批状态
```

## 高级用法

```bash
# 只跑到某个阶段（查看中间产物）
PYTHONPATH="src" python -m ppt_agent.cli run \
  --job workspace/jobs/demo \
  --until outline_generation

# 从某个阶段继续
PYTHONPATH="src" python -m ppt_agent.cli run \
  --job workspace/jobs/demo \
  --from content_mapping

# 强制重新生成（忽略已有文件）
PYTHONPATH="src" python -m ppt_agent.cli run \
  --job workspace/jobs/demo \
  --model-profile deepseek \
  --force

# 验证 JSON artifact 格式
PYTHONPATH="src" python -m ppt_agent.cli validate-artifacts \
  --job workspace/jobs/demo

# 生成 HTML 预览
PYTHONPATH="src" python -m ppt_agent.cli preview \
  --job workspace/jobs/demo

# 启动 Web Dashboard
PYTHONPATH="src" python -m ppt_agent.cli serve --port 8000
# 浏览器打开 http://localhost:8000
```

## 一键完整流程

```bash
# 创建 + 生成 + 审批 + 组装 一条龙
PYTHONPATH="src" python -m ppt_agent.cli create-job \
  --input workspace/examples/ai-platform \
  --output workspace/jobs/demo && \
PYTHONPATH="src" python -m ppt_agent.cli run \
  --job workspace/jobs/demo --model-profile deepseek && \
PYTHONPATH="src" python -m ppt_agent.cli approve \
  --job workspace/jobs/demo && \
PYTHONPATH="src" python -m ppt_agent.cli run \
  --job workspace/jobs/demo --from visual_generation \
  --model-profile fake --force
```
