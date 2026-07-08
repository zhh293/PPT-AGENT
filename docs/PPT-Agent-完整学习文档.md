# PPT-Agent 完整学习文档

> 面向学习者，逐模块、逐文件、逐函数详解整个 PPT-Agent 系统。从入口到底层，从数据模型到 Agent 循环，全部覆盖。

---

## 目录

- [第一部分：项目概览](#第一部分项目概览)
- [第二部分：入口与配置](#第二部分入口与配置)
- [第三部分：数据模型层](#第三部分数据模型层)
- [第四部分：LLM 抽象层](#第四部分llm-抽象层)
- [第五部分：上下文管理](#第五部分上下文管理)
- [第六部分：运行时基础设施](#第六部分运行时基础设施)
- [第七部分：Agent 循环引擎](#第七部分agent-循环引擎)
- [第八部分：Coordinator 编排器](#第八部分coordinator-编排器)
- [第九部分：Worker 执行器](#第九部分worker-执行器)
- [第十部分：能力注册表与工具工厂](#第十部分能力注册表与工具工厂)
- [第十一部分：Skill 系统](#第十一部分skill-系统)
- [第十二部分：8 大 Worker 详解](#第十二部分8-大-worker-详解)
- [第十三部分：检索管线](#第十三部分检索管线)
- [第十四部分：设计系统](#第十四部分设计系统)
- [第十五部分：PPT 组装](#第十五部分ppt-组装)
- [第十六部分：事件总线](#第十六部分事件总线)
- [第十七部分：记忆系统](#第十七部分记忆系统)
- [第十八部分：前端与服务器](#第十八部分前端与服务器)
- [附录：关键设计模式](#附录关键设计模式)

---

## 第一部分：项目概览

### 1.1 系统定位

PPT-Agent 是一个 AI Agent 驱动的 PPT 生成系统。输入项目材料（计划书、截图、证书等），自动生成可编辑的 `.pptx` 文件。

### 1.2 核心设计理念

1. **Coordinator-Worker 多 Agent 架构**：一个纯编排的 Coordinator 负责调度，8 个专门化的 Worker 各自执行流水线的一个阶段
2. **文件系统优先**：所有中间数据用 JSON 文件存储，人类可读，Git 友好，不需要数据库
3. **渐进式 Skill 加载**：Skill 先展示目录（名称+描述），用时再加载完整内容，不浪费 context window
4. **Agent Loop + Fallback**：LLM Agent 循环失败时自动降级到确定性函数，保证鲁棒性
5. **隔离优先**：双层 ContextVar 上下文隔离、文件邮箱通信、Shell 沙箱

### 1.3 技术栈

| 层 | 技术 |
|---|------|
| Agent 框架 | 自研（非 LangChain），Coordinator-Worker 模式 |
| LLM 集成 | 统一 Provider 抽象，支持 Anthropic / OpenAI Compatible / Fake |
| PPT 生成 | python-pptx |
| 检索 | 自研 BM25 + TF-IDF + RRF + 启发式 Reranker |
| OCR | Tesseract (pytesseract) |
| 前端 | React + TypeScript + Vite + Tailwind |
| 后端 API | Python FastAPI + SSE 流式推送 |
| 持久化 | Markdown/JSONL 文件系统 |

### 1.4 PPT 生成流水线 (8 阶段)

```
document_analysis  →  outline_generation  →  template_matching
                                              design_planning
                                                    ↓
                                              content_mapping
                                              (⏸ 用户审核)
                                               ↙         ↘
                               visual_generation    ppt_assembly
                                               ↘      ↙
                                          quality_verification
```

---

## 第二部分：入口与配置

### 2.1 CLI 入口 (`cli.py`)

文件：`src/ppt_agent/cli.py`

提供 6 个子命令：

| 命令 | 函数 | 说明 |
|------|------|------|
| `create-job` | `cmd_create_job` | 从 input 目录创建 job workspace，复制文件 |
| `run` | `cmd_run` | 运行 PPT 生成流水线 |
| `approve` | `cmd_approve` | 审核 slide_contents.json，设置 review_status="approved" |
| `preview` | `cmd_preview` | 生成 HTML 预览页 |
| `validate-artifacts` | `cmd_validate_artifacts` | 用 JSON Schema 验证所有 artifact |
| `serve` | `cmd_serve` | 启动 Web Dashboard 服务器 |

**`run` 命令参数：**

```
--job          作业目录路径
--until        指定停止阶段（如 content_mapping）
--from         指定起始阶段（如 visual_generation）
--force        跳过用户审核门控
--model-profile LLM 配置名（如 deepseek / anthropic / agent:deepseek）
```

**`model_profile` 决定执行模式：**
- 不传 → Deterministic 模式（纯确定性）
- 传 `deepseek` 等 → LLM-Augmented 模式（LLM 辅助 Worker，线性执行）
- 传 `agent:deepseek` → Agent 模式（Coordinator + Worker 完全自主循环）

### 2.2 FastAPI 服务器 (`server/app.py`)

文件：`src/ppt_agent/server/app.py`

提供 REST API + SSE 实时事件流：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/jobs` | GET | 列出所有作业 |
| `/api/jobs/{id}` | GET | 获取作业状态 |
| `/api/jobs` | POST | 上传文件创建新作业 |
| `/api/jobs/{id}/run` | POST | 后台启动流水线 |
| `/api/jobs/{id}/artifacts` | GET | 列出所有 artifact |
| `/api/jobs/{id}/artifacts/{name}` | GET | 获取具体 artifact 内容 |
| `/api/jobs/{id}/events` | GET | 获取历史事件（history.jsonl） |
| `/api/jobs/{id}/stream` | GET | SSE 实时事件流（30s keepalive） |
| `/api/jobs/{id}/preview` | GET | HTML 预览页 |
| `/api/jobs/{id}/approve` | POST | 审核 slide_contents |

**SSE 实现细节：**
- 每个 job 维护一个 `asyncio.Queue` 订阅者列表
- `SSEConsumer` 实现 `EventConsumer` 接口
- 线程安全：用 `threading.Lock` 保护订阅者列表

### 2.3 配置系统

#### 模型配置 (`llm/config.py`)

文件：`src/ppt_agent/llm/config.py`

```python
@dataclass
class ProviderConfig:
    name: str
    protocol: str          # "anthropic_messages" | "openai_chat_completions" | "fake"
    api_key: str           # 直接 API key（优先级最高）
    api_key_env: str       # 环境变量名
    base_url_env: str      # Base URL 环境变量名
    endpoint: str          # 直接 endpoint URL
    text_model: str
    vision_model: str
    reasoning_model: str
    temperature: float = 0.4
    max_tokens: int = 8000
    timeout_seconds: int = 90
```

**配置加载优先级：**
1. `api_key` 直接值 > `api_key_env` 环境变量 > 空字符串
2. `endpoint` 直接值 > `base_url_env` 环境变量 > None

**`config/models.yml` 支持 5 个 provider：**
- `deepseek`（默认，内置 key）
- `anthropic`（从环境变量取 `ANTHROPIC_API_KEY`）
- `openai`（从环境变量取 `OPENAI_API_KEY`）
- `local`（Ollama，`http://localhost:11434/v1`）
- `fake`（确定性 fake，测试用）

#### 派发配置 (`coordinator/routing_rules.py`)

文件：`src/ppt_agent/coordinator/routing_rules.py`

从 `config/dispatcher.yml` 加载路由表：capability → worker 模块 + skill + knowledge_base。

---

## 第三部分：数据模型层

所有数据模型位于 `src/ppt_agent/models/`，使用 dataclass + 手动 to_dict() 格式化。

### 3.1 作业工作区 (`models/artifacts.py`)

```python
class JobWorkspace:
    root: Path              # workspace/jobs/<job-id>/
    input_dir: Path         # workspace/jobs/<job-id>/input/
    background_images_dir   # workspace/jobs/<job-id>/background_images/

    def artifact_path(name: str) -> Path
        # 自动加 .json 后缀（除非已经是 .pptx）
```

**原子写入 `atomic_write_json()`：**
```python
def atomic_write_json(path, payload):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    tmp.replace(path)  # OS 级别原子重命名
```

**为什么重要：** 防止写入一半的 JSON 被其他进程读到。

### 3.2 Outline 模型 (`models/outline.py`)

```python
@dataclass
class OutlineSlide:
    slide_index: int
    type: str          # cover | problem | solution | product | ...
    title: str
    purpose: str
    bullets: list[str]
    source_refs: list[str]   # 关联的 evidence_id
    image_needs: str         # 图片需求描述
    priority: str            # required | recommended | optional

@dataclass
class PresentationOutline:
    meta: dict              # project_name, domain, audience, tone, total_slides
    slides: list[OutlineSlide]
    assumptions: list[str]
    needs_user_review: bool
```

### 3.3 Source Summary 模型 (`models/source_summary.py`)

```python
@dataclass
class ImageInventoryItem:
    image_id: str           # "img_1"
    path: str               # 相对路径
    detected_usage: str     # "screenshot" | "reference_image"
    summary: str            # 用户图片描述
    relevance: float = 0.5

@dataclass
class SourceSummary:
    project_name: str
    domain: str             # "医疗" | "教育" | "科技" ...
    target_audience: str    # "投资人" | "评委" ...
    tone: str               # "专业严谨"
    value_proposition: str  # 核心价值主张
    product_capabilities: list[str]
    evidence_items: list[dict]   # 有 evidence_id, summary, source_refs, confidence
    unsupported_claims: list[str]
    image_inventory: list[ImageInventoryItem]
    warnings: list[str]
    confidence: float
```

### 3.4 Slide Contents 模型 (`models/slide_contents.py`)

```python
def approve_slide_contents(data: dict) -> dict:
    """设置 review_status 为 'approved'，所有 slide 的 review_status 也为 'approved'"""
    data["review_status"] = "approved"
    for slide in data.get("slides", []):
        slide["review_status"] = "approved"
    return data
```

这是**用户审核门控**的核心：`review_status` 必须为 `"approved"` 才能继续到 ppt_assembly。

### 3.5 其他模型一览

| 模型 | 文件 | 关键字段 |
|------|------|---------|
| `DesignPlan` | `design_plan.py` | theme_profile (color/typography/spacing/shape/image/chart tokens), slides |
| `TemplateMeta` | `template_meta.py` | template_id, slides(含 zones), default 值 |
| `SelectedTemplate` | `selected_template.py` | template_id, selection_status, score, ranking |
| `ImageGenConfig` | `image_generation.py` | slides 配置, prompt, aspect_ratio, resolution |
| `DispatchDecision` | `dispatch.py` | job_id, phase, worker, skill, context_bundle, retry_policy |
| `KBConfig` | `knowledge_base.py` | knowledge base 配置 |
| `ValidationReport` | `validation.py` | 验证报告结构 |
| `schema_loader` | `schema_loader.py` | JSON Schema 加载和验证 |

---

## 第四部分：LLM 抽象层

### 4.1 消息模型 (`llm/messages.py`)

```python
@dataclass
class ContentBlock:
    type: Literal["text", "image", "json", "tool_result"]
    text: str | None
    path: str | None        # 图片路径
    mime_type: str | None
    data: dict | None       # JSON 数据
    image_data: str | None  # base64 编码（用于 vision API）

@dataclass
class LLMMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: list[ContentBlock]

    @classmethod
    def system(cls, text: str) -> LLMMessage    # 快捷构建 system 消息
    @classmethod
    def user(cls, text: str) -> LLMMessage      # 快捷构建 user 消息
    @classmethod
    def assistant(cls, text: str) -> LLMMessage # 快捷构建 assistant 消息
    @classmethod
    def user_with_images(cls, text, image_paths) # 构建含图片的 user 消息

    def text(self) -> str:   # 提取所有文本拼接
```

**设计要点：**
- 所有 Provider 使用统一的 `LLMMessage`，在自己的 `generate()` 中转换为厂商格式
- `ContentBlock` 支持 text/image/json/tool_result 四种类型
- image 通过 base64 编码内联到消息中（vision API 需要）

```python
@dataclass
class LLMResult:
    text: str = ""
    json_data: dict | None = None     # FakeProvider 预填充
    provider: str = ""                # 厂商名
    model: str = ""                   # 模型名
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    repaired: bool = False            # JSON 是否经过修复
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None
```

### 4.2 Provider 体系

#### 抽象基类 (`llm/providers/base.py`)

```python
@dataclass(frozen=True)
class ProviderCapabilities:
    supports_vision: bool = False
    supports_json_mode: bool = False     # 原生 JSON mode
    supports_tool_use: bool = False
    supports_streaming: bool = False
    max_context_tokens: int = 128_000

class BaseLLMProvider(ABC):
    def __init__(self, config: ProviderConfig): ...
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...
    @abstractmethod
    def generate(self, messages, *, temperature, max_tokens, json_mode) -> LLMResult: ...
```

#### Anthropic Provider (`llm/providers/anthropic.py`)

协议：`anthropic_messages`

将 `LLMMessage` 转换为 Anthropic Messages API 格式，支持：
- System prompt 作为顶层参数
- ContentBlock 转换为 text/image 内容块
- 可选 JSON mode（在 system prompt 中注入指令）

#### OpenAI Compatible Provider (`llm/providers/openai_compatible.py`)

协议：`openai_chat_completions` 或 `openai_responses`

支持所有 OpenAI-compatible API（DeepSeek, Ollama 等），支持：
- `response_format: {"type": "json_object"}` 原生 JSON mode
- 自定义 endpoint URL
- Vision API（base64 图片内联）

#### Fake Provider (`llm/providers/fake.py`)

```python
class FakeProvider(BaseLLMProvider):
    # 配置选项:
    custom_responses: dict       # 关键词 → 响应字典
    simulate_error: str          # 模拟错误消息
    simulate_invalid_json: bool  # 模拟非法 JSON
    simulate_latency_ms: int     # 模拟延迟

    def _find_response(messages):
        """优先级匹配:
        1. custom_responses 关键词（全文匹配）
        2. _DEFAULT_RESPONSES 关键词（优先 user 消息）
        3. _DEFAULT_RESPONSES 关键词（全文 fallback）
        4. 通用响应 {"status": "ok"}
        """
```

**预置默认响应：** `_DEFAULT_RESPONSES` 包含 document_analysis、outline_generation、design_planning、content_mapping 四个阶段的完整模拟输出。

### 4.3 LLM Client (`llm/client.py`)

这是 Worker 调用的**唯一入口**，封装了 Provider 选择、消息构建、JSON 解析/修复、审计日志。

```python
class LLMClient:
    def __init__(self, provider, job_root=None, context_window=None):
        self.provider = provider
        self.job_root = job_root
        self._capabilities = provider.capabilities()

    @classmethod
    def from_config(cls, profile, config_path="config/models.yml", job_root=None):
        """从配置文件创建客户端"""
        model_config = load_model_config(config_path)
        provider_config = model_config.get_provider(profile)
        provider = _create_provider(provider_config)  # 按 protocol 创建
        return cls(provider, job_root=job_root)
```

**两套生成方法：**

#### `generate_text()` — 生成自由文本

```python
def generate_text(self, *, prompt, context=None, system=None,
                  phase="", temperature=None, max_tokens=None) -> str:
    messages = self._build_messages(prompt, context, system)
    result = self.provider.generate(messages, temperature, max_tokens)
    if self.job_root:
        log_model_call(self.job_root, phase, result, prompt_summary=prompt[:200])
    if not result.success:
        raise RuntimeError(f"LLM call failed: {result.error}")
    return result.text
```

#### `generate_json()` — 生成结构化 JSON（核心方法）

```python
def generate_json(self, *, prompt, schema=None, context=None, system=None,
                  phase="", fallback=None, temperature=None, max_tokens=None) -> dict:
    # 1. 注入 JSON 指令到 system prompt
    json_system = (system or "") + "\n\nYou MUST respond with ONLY a valid JSON object..."

    # 2. 构建消息，发送请求
    messages = self._build_messages(prompt, context, json_system)
    result = self.provider.generate(messages, temperature, max_tokens,
                                     json_mode=self._capabilities.supports_json_mode)

    # 3. 解析 + validate
    data, warnings = parse_json_response(result, schema)
    if data is not None:
        return data

    # 4. 修复尝试：让模型修复自己的非法 JSON
    repair_result = self._attempt_repair(result.text, messages, schema)
    if repair_result and repair_result.success:
        repair_data, _ = parse_json_response(repair_result, schema)
        if repair_data is not None:
            return repair_data

    # 5. 所有尝试失败 → fallback or raise
    if fallback is not None:
        return fallback
    raise RuntimeError(f"Failed to get valid JSON after repair")
```

**消息构建 `_build_messages()`：**
1. system prompt 注入 Memory 上下文（`_memory_context`）
2. system prompt 注入 Skill 上下文（`_skill_contexts`）
3. context dict 进行 L0-L4 压缩（基于 token 利用率估算）
4. 组装 `[system_prompt, user_prompt]`

**5 级压缩阈值（`_COMPRESSION_THRESHOLDS`）：**

| 利用率 | 级别 | 策略 |
|--------|------|------|
| < 50% | L0 | 无压缩 |
| 50-70% | L1 | 合并空白，截断长列表 |
| 70-85% | L2 | LLM 摘要长文本 + 截断 |
| 85-95% | L3 | 仅保留 phase 相关数据 |
| > 95% | L4 | 紧急：仅保留 errors + approved text |

**JSON 修复 `_attempt_repair()`：**
```python
repair_prompt = (
    "Your previous response was not valid JSON. Here is what you returned:\n\n"
    f"```\n{broken_text[:2000]}\n```\n\n"
    "Please return ONLY a valid JSON object with no other text."
)
# 用 temperature=0.1 重试一次
```

### 4.4 JSON 修复引擎 (`llm/json_repair.py`)

```python
def repair_json(text: str, max_attempts: int = 2) -> tuple[dict | None, list[str]]:
```

**修复步骤：**

1. **直接 parse** → 成功就返回
2. **提取 JSON 对象** `extract_json_object()`：
   - 优先匹配 markdown code fence ` ```json ... ``` `
   - 否则从第一个 `{` 开始，逐字符跟踪嵌套深度找到匹配的 `}`
3. **自动修复**（最多 2 轮）：
   - 删除尾随逗号 `,\s*([}\]])` → `\1`
   - 单引号 → 双引号
   - 给无引号的 key 加引号 `(?<=[{,])\s*(\w+)\s*:` → `"key":`
   - 补全缺失的 `}` 和 `]`

### 4.5 审计日志 (`llm/audit.py`)

```python
def log_model_call(job_root, phase, result, prompt_summary="", schema_name=""):
    record = {
        "timestamp": ...,
        "phase": phase,
        "provider": result.provider,
        "model": result.model,
        "prompt_hash": sha256(prompt_summary).hexdigest()[:16],  # 仅存 hash
        "schema_name": schema_name,
        "latency_ms": result.latency_ms,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "status": "success" if result.success else "error",
        "error": result.error,
        "repaired": result.repaired,
    }
    # append to model_calls.jsonl
```

**不记录完整 prompt/response**（隐私考虑），只记录 hash。

---

## 第五部分：上下文管理

### 5.1 L0-L4 压缩 (`context/compression.py`)

```python
def compress_context(context: dict, level: int, llm_client=None) -> dict:
```

**压缩不变量（`INVARIANT_KEYS`，任何级别都不移除）：**
- `approved_text`, `errors`, `agent_identity`, `memory`, `session`, `active_tool_results`, `recent_messages`

**各级别操作：**

| 级别 | 操作 |
|------|------|
| L0 | `context.copy()` 直接返回 |
| L1 | `_normalize_whitespace()` 合并多余空白 + `_truncate_list(max_items=20)` |
| L2 | 长列表截断到 8 项 + 长字符串（>2000 字符）LLM 摘要 / 截断 |
| L3 | 仅保留 `INVARIANT_KEYS + slide_contents + outline + source_summary + phase + job_id` |
| L4 | **紧急**：仅保留 `INVARIANT_KEYS` |

**LLM 摘要降级：** 当 LLM 调用失败时，回退到 `text[:2000] + "... [truncated]"`

### 5.2 三层记忆系统 (`context/memory_layers.py`)

```
Layer 1: agent.md    → 不变的系统身份和能力描述
Layer 2: memory.md   → 跨会话累积知识（workspace 级别共享）
Layer 3: session.md  → 当前会话的阶段日志（per-job）
```

**存储位置：**
- `agent.md` / `session.md` → `<job_root>/.ppt_agent/`
- `memory.md` → workspace 级别（`job_root.parent/.ppt_agent/`），跨 job 共享

**关键函数：**

```python
def ensure_memory_files(workspace_root):
    """确保 agent.md + session.md 存在且有默认内容
    memory.md 不在此创建（workspace 级别）"""

def read_memory(workspace_root, layer: str) -> str:
    """读指定层。"memory" 层优先 workspace 路径，fallback per-job 路径"""

def append_memory(workspace_root, layer: str, content: str):
    """追加到 session 或 memory 层。agent 层不可追加"""

def build_memory_context(workspace_root) -> str:
    """组装三层内容为 LLM 注入文本，顺序: agent → memory → session"""
```

**agent.md 默认内容：**
- Identity: 身份声明
- Capabilities: 8 大能力枚举
- Constraints: 3 条行为约束（不编造事实、保留用户编辑、需用户审核）

### 5.3 会话摘要 (`context/session_summary.py`)

```python
def append_phase_summary(workspace_root, phase, summary, artifact_name=None, warnings=None):
    """每阶段完成后追加到 session.md:
    ### phase_name (HH:MM:SS)
    summary
    - Artifact: `xxx`
    - Warnings: ...
    """
```

### 5.4 Dream 记忆巩固 (`context/dream.py`)

Pipeline 完成后执行，从会话日志中提取可持久化的经验：

```python
def run_dream_task(workspace_root, llm_client=None, source_task_id="session") -> str:
    # 返回 dream task ID: "d-dream-<timestamp>"
```

**LLM 模式：**
```
prompt: "Review the following session log from a PPT generation job.
         Extract 1-3 durable insights that would be useful for future jobs..."
schema: {"insights": [{"text": "...", "relevance": 0.0-1.0}]}
```

**启发式降级：** 提取 session 中包含 "warning"/"error"/"failed"/"fallback" 的行

**相关性门控：** `relevance >= 0.6` 才会写入 memory.md

---

## 第六部分：运行时基础设施

### 6.1 任务类型 (`runtime/task_types.py`)

```python
class TaskType(str, Enum):
    LOCAL_BASH = "local_bash"            # b-: 后台 shell
    LOCAL_AGENT = "local_agent"          # a-: 同步/异步子 Agent
    REMOTE_AGENT = "remote_agent"        # r-: 远程会话
    IN_PROCESS_TEAMMATE = "in_process_teammate"  # t-: 持久队友
    LOCAL_WORKFLOW = "local_workflow"    # w-: 多步骤工作流
    MONITOR_MCP = "monitor_mcp"          # m-: 被动监控
    DREAM = "dream"                      # d-: 记忆巩固
```

**状态机：**

```
PENDING → RUNNING → WAITING → RUNNING → COMPLETED
                                    ↘ FAILED
PENDING → CANCELLED
RUNNING → CANCELLED
WAITING → CANCELLED / FAILED
COMPLETED / FAILED / CANCELLED → (terminal, 不可转换)
```

### 6.2 任务管理器 (`runtime/task_manager.py`)

```python
class TaskManager:
    tasks: dict[str, ManagedTask]           # 所有任务
    _serial_counters: dict[TaskType, int]    # 自动 ID 计数器

    def create(task_type, parent_id=None, description="", task_id=None) -> ManagedTask
    def transition(task_id, status, **kwargs):  # 状态转换 + 验证 + 事件发射
    def get(task_id) -> ManagedTask
    def list_by_type(task_type) -> list[ManagedTask]
    def list_children(parent_id) -> list[ManagedTask]
    def all_completed(parent_id) -> bool
```

**状态转换时自动：**
1. 验证转换合法性
2. 记录 `started_at` / `completed_at`
3. 持久化到 history.jsonl
4. 发射 `STATUS_CHANGE` 事件

### 6.3 双层上下文隔离 (`runtime/agent_context.py`)

```
TeammateContext (团队级，ContextVar #1)
├── workspace_root
├── event_bus
├── skill_loader
├── job_id
└── team_role

AgentContext (Agent 级，ContextVar #2)
├── agent_id
├── phase
├── tool_history
└── turn_count
```

**解析优先级：**
1. `AgentContext` (ContextVar)
2. `TeammateContext` (ContextVar)
3. 环境变量 `PPT_AGENT_*`

**用法示例（Coordinator spawn Worker 时）：**
```python
with teammate_scope(TeammateContext(workspace_root=..., event_bus=..., ...)):
    with agent_scope(AgentContext(agent_id=f"worker-{phase}", phase=phase)):
        worker.run(task_prompt, context)
```

### 6.4 邮箱通信 (`runtime/mailbox.py`)

文件：`src/ppt_agent/runtime/mailbox.py`

```python
class MessagePriority(IntEnum):
    SHUTDOWN = 1       # 最高：防止僵尸 Agent
    TEAM_LEAD = 2      # Coordinator 指令
    PEER = 3           # Agent 间平等通信
    BACKGROUND = 4     # 后台工作

class Mailbox:
    def __init__(self, path: Path):  # path 指向 JSONL 文件
    def append(sender, recipient, message, artifact_refs, priority) -> dict
    def read_all() -> list[dict]       # 按 priority 排序
    def read_unread() -> list[dict]    # 仅未读
    def mark_read(sender=None) -> int  # 标记已读
    def has_shutdown_request() -> bool # 检查 SHUTDOWN 优先级消息
```

**并发安全：**
- Unix：`fcntl.flock(LOCK_EX)` 文件锁
- Windows：跳过锁（append-only 写入对并发小量写入相对安全）

### 6.5 对话持久化 (`runtime/conversation_store.py`)

```python
class ConversationStore:
    """JSONL 对话历史持久化 (AGENT_ARCHITECTURE.md §2.2)

    每条消息一行 JSON:
    {"id":"msg_xxx", "role":"user", "content":"...", "timestamp":..., "node_id":"n1", "parent_id":null, ...}
    """

    def append(message, node_id, parent_id, tool_calls, agent_id, metadata) -> ConversationEntry
    def load_messages(limit=None) -> list[LLMMessage]
    def load_entries(limit=None) -> list[ConversationEntry]
    def get_stats() -> dict  # message_count, total_chars, roles 分布
```

**ContentBlock 保真度：** 通过 `content_blocks_json` 字段完整序列化所有 ContentBlock（包括 image_data），支持无损往返。

---

## 第七部分：Agent 循环引擎

文件：`src/ppt_agent/runtime/agent_loop.py`

这是整个系统最重要的抽象——Coordinator 和 Worker 都继承自此。

### 7.1 核心数据结构

```python
class StopReason(str, Enum):
    COMPLETED = "completed"       # Agent 发出 DONE 信号
    MAX_TURNS = "max_turns"       # 达到 turn 上限
    ERROR_BUDGET = "error_budget" # 连续错误超限
    ABORTED = "aborted"           # 外部 abort 信号
    NO_ACTION = "no_action"       # LLM 返回了文本但没有 tool call 也没有 DONE

@dataclass
class ToolCall:
    tool_name: str
    arguments: dict
    call_id: str = ""

@dataclass
class ToolResult:
    call_id: str
    output: Any
    success: bool = True
    error: str | None = None

@dataclass
class AgentTurn:
    turn_number: int
    thought: str                      # LLM 的推理文本
    tool_calls: list[ToolCall]
    tool_results: list[ToolResult]
    duration_ms: int
    stop_signal: bool = False

@dataclass
class AgentLoopResult:
    agent_id: str
    stop_reason: StopReason
    turns: list[AgentTurn]
    final_output: Any
    total_duration_ms: int
    error: str | None = None
```

### 7.2 AgentLoop 抽象基类

```python
class AgentLoop(ABC):
    def __init__(self, agent_id, llm_client, *, max_turns=20,
                 max_consecutive_errors=3, compression_level=0,
                 event_bus=None, conversation_store=None, mailbox=None,
                 tool_registry=None, skill_loader=None,
                 context_window_limit=128000, job_root=None):
```

### 7.3 `run()` 方法——完整的 Think→Act→Observe 循环

```python
def run(self, task_prompt: str, context: dict | None = None) -> AgentLoopResult:
```

**执行步骤：**

```
1. 重置 turns + errors
2. 设置 agent_attribution ContextVar

3. 构建初始消息:
   system_prompt = self.build_system_prompt()     ← 子类实现
   messages = [system_prompt]

   如果 conversation_store 有历史 → 恢复（跳过 system 角色避免重复）
   构建 user message: task_prompt + compressed context

4. FOR turn_num = 1 to max_turns:
   a. 检查 abort 信号
   b. _execute_turn(turn_num) → AgentTurn
      ├── _check_mailbox()         检查跨 Agent 消息
      │   ├── Priority 1 (SHUTDOWN) → 设置 _aborted
      │   └── 其他消息 → 注入 messages
      ├── _compress_messages()      L0-L4 压缩
      ├── THINK: _call_llm()
      │   ├── 构建 tool descriptions → 注入 system prompt
      │   ├── provider.generate(messages, ...)
      │   └── _log_model_call()     审计日志
      ├── parse_llm_response() → (thought, tool_calls, done)
      ├── 如果 done=True → 记录 stop_signal, break
      ├── 如果 tool_calls=[] → NO_ACTION, break
      ├── ACT: FOR each tool_call:
      │   ├── 如果是 "load_skill" → _load_skill_tool() 内置处理
      │   ├── execute_tool(tc) → ToolResult
      │   └── 错误时: 注入 "[Tool Error: ...] Please fix and retry"
      └── on_turn_complete(turn)

   c. 如果 stop_signal → COMPLETED, break
   d. 如果 no tool calls → NO_ACTION, break
   e. 如果 连续错误 >= max_consecutive_errors → ERROR_BUDGET, break

5. 返回 AgentLoopResult
```

**关键机制——自纠正（Self-Correction）：**
```python
# 工具执行失败时，错误消息被注入 message 历史：
self.messages.append(LLMMessage.user(
    f"[Tool Error: {tool_name}]\n{error}\n\nPlease fix the issue and try again."
))
# Agent 在下一轮 THINK 时会看到这个错误，可以尝试修复
```

### 7.4 L0-L4 消息压缩 (`_compress_messages`)

```python
def _compress_messages(self):
    total_tokens = len(all_text) / 4  # 估算
    utilization = total / context_window_limit

    if utilization < 0.6: return  # L0

    level = 1 if util >= 0.6 else 2 if util >= 0.75 else 3 if util >= 0.85 else 4

    # 保护: system prompt[0], 最后 2 条, 含 [Error 的消息
    error_msgs = [m for m in messages[1:-2] if "[Error" in m]
    compressible = [m for m in messages[1:-2] if not in error_msgs]
```

| Level | 操作 |
|-------|------|
| L1 | 截断 >2000 字符的大工具输出 |
| L2 | LLM 摘要前半段 + 保留后半段原文 |
| L3 | system + errors + 最后 4 条 |
| L4 | system + 最后 2 条 |

### 7.5 内置 `load_skill` 工具

```python
def _load_skill_tool(self, skill_name: str) -> ToolResult:
    # 检查是否已加载（_loaded_skills 或 skill_loader.is_loaded）
    # 加载 SKILL.md → append 到 system prompt
    # 发射 SKILL_LOADED 事件
    # 单次加载保证：已加载返回 {"status": "already_loaded"}
```

### 7.6 子类必须实现的抽象方法

| 方法 | 职责 |
|------|------|
| `build_system_prompt()` | 返回 Agent 的 system prompt 文本 |
| `get_available_tools()` | 返回 LLM 格式的工具定义列表 |
| `execute_tool(tool_call)` | 执行工具调用，返回 ToolResult |
| `parse_llm_response(result)` | 从 LLM 输出解析 (thought, tool_calls, done) |

---

## 第八部分：Coordinator 编排器

文件：`src/ppt_agent/coordinator/coordinator_agent.py`

### 8.1 类结构

```python
class CoordinatorAgent(AgentLoop):
    """纯编排 Agent，6 个编排工具，绝不直接操作文件"""

    def __init__(self, workspace, llm_client, *, task_manager, event_bus,
                 skill_loader, phases, force, max_turns=30):
        self.workspace = workspace
        self._executor = ThreadPoolExecutor(max_workers=3)  # 异步 Worker 池
        self._futures: dict[str, Future] = {}              # 异步追踪
        self._worker_results: dict = {}                     # Worker 结果汇总
        self._completed: list[str] = []                     # 完成的 capability_id
        self.mailbox = Mailbox(.../coordinator.jsonl)
```

### 8.2 System Prompt 构建

```python
def build_system_prompt(self) -> str:
    return f"""You are the Coordinator for a PPT generation system.

## Available Capabilities
- document_analysis: Analyze uploaded materials...
- outline_generation: Create a structured outline...
...

## Dependency Rules
- document_analysis → outline_generation
- outline_generation → template_matching + design_planning
- content_mapping depends on outline + template + design_plan + ...

## Parallel Opportunities
- outline_generation and template_matching can run in parallel
- design_planning can run with template_matching if outline is ready

## Completed: {json.dumps(self._completed)}

## Skills: {skill_catalog}
## Memory: {memory_context}
"""
```

### 8.3 6 个编排工具

```python
def get_available_tools(self) -> list[dict]:
    return [
        {"name": "spawn_agent", ...},        # 为 capability 生成 Worker
        {"name": "check_artifacts", ...},     # 检查 workspace 中的 artifact
        {"name": "wait_agents", ...},         # 等待异步 Worker 完成
        {"name": "review_result", ...},       # 查看 Worker 输出
        {"name": "request_user_review", ...}, # 暂停等待用户审核
        {"name": "synthesize_output", ...},   # 汇总最终输出
    ]
```

**`spawn_agent` 实现：**
```python
def _spawn_agent(self, capability_id, instructions, is_async):
    capability = get_capability(capability_id)  # 从注册表获取
    task = task_manager.create(TaskType.LOCAL_AGENT, parent_id="coordinator")
    task_manager.transition(task.task_id, TaskStatus.RUNNING)

    if is_async:
        future = self._executor.submit(self._run_worker, capability, task, instructions)
        self._futures[task.task_id] = future
        return {"task_id": ..., "status": "running_async"}

    return self._run_worker(capability, task, instructions)  # 同步阻塞
```

**`_run_worker` 实现（核心）：**
```python
def _run_worker(self, capability, task, instructions):
    # 1. 加载 Skill（如果有）
    skill_content = skill_loader.load_skill(capability.skill_name) if capability.skill_name else None

    # 2. 创建专属 ToolRegistry（RBAC）
    factory = ToolFactory(self.workspace)
    registry = factory.create_registry_for_capability(capability, self.skill_loader, self.llm_client)

    # 3. 创建 WorkerAgent
    worker = WorkerAgent(workspace, capability, llm_client, ..., registry=registry)

    # 4. 双层上下文隔离
    with teammate_scope(TeammateContext(...)):
        with agent_scope(AgentContext(agent_id=f"worker-{capability_id}", phase=capability_id)):
            result = worker.run(task_prompt, context)

    # 5. 收集结果
    self._worker_results[capability_id] = {status, turns, output, error}
    self._completed.append(capability_id)
```

**`_build_phase_context`：** 根据阶段自动加载依赖的前序 artifact

```python
artifact_map = {
    "outline_generation": ["source_summary"],
    "template_matching": ["outline"],
    "content_mapping": ["outline", "selected_template", "slide_design_plan",
                         "source_summary", "template_zones"],
    "visual_generation": ["slide_contents"],
    "ppt_assembly": ["slide_contents"],
    "verification": ["slide_contents", "image_generation_report"],
}
```

### 8.4 响应解析

```python
def parse_llm_response(self, result: LLMResult) -> tuple[str, list[ToolCall], bool]:
    data, _ = repair_json(result.text)
    if data.get("done"):
        return text, [], True
    if data.get("tool"):
        return text, [ToolCall(tool_name=data["tool"], arguments=data["arguments"])], False
    return text, [], False  # 纯文本推理，无动作
```

---

## 第九部分：Worker 执行器

文件：`src/ppt_agent/coordinator/worker_agent.py`

### 9.1 三种 Worker 类别

```python
_CONTENT_REASONING = {"document_analysis", "outline_generation",
                       "design_planning", "content_mapping", "verification"}
_OPERATION = {"template_matching", "ppt_assembly"}
_SKILL_AUTONOMOUS = {"visual_generation"}
```

### 9.2 System Prompt 三类

#### Content Reasoning Prompt（5 个阶段共用）

7 步工作流：READ → REASON → COMPOSE → REVIEW → WRITE → VALIDATE → DONE

```python
def _build_content_reasoning_prompt(self) -> str:
    return f"""You are a WorkerAgent executing '{self.phase}'.

## Identity: {phase_description}

## Your Workflow:
1. READ — read_artifact + read_input_files 加载输入
2. REASON — 理解数据，考虑领域、受众、语调
3. COMPOSE — compose_artifact 生成结构化 JSON
4. REVIEW — 检查字段完整性、内容一致性
5. WRITE — write_artifact 保存结果
6. VALIDATE — validate_output 验证
7. DONE — 发出完成信号

## Input artifacts: {inputs}
## Output: {output}
## Skill (if loaded): {skill_content}
## Instructions: {instructions}
"""
```

#### Operation Prompt

template_matching：search_templates → evaluate → compose → write → validate → done
ppt_assembly：read → check review_status → assemble → check file → validate → done

#### Skill Autonomous Prompt（visual_generation 专用）

```python
def _build_skill_autonomous_prompt(self) -> str:
    return f"""You are an AUTONOMOUS agent operating a Skill.

## Skill Documentation:
{self.skill_content}  ← 完整的 SKILL.md

## How You Work:
- READ Skill documentation → REASON → DECIDE commands → EXECUTE → OBSERVE → ADAPT
- Use run_shell_command, check_file_exists, list_directory
- Handle errors (retry, switch accounts, etc.)
"""
```

### 9.3 Tool 定义（三类）

#### Content Reasoning Tools

```python
["read_input_files"]        # 仅 document_analysis 有
["read_artifact"]           # 读前序 artifact
["compose_artifact"]        # LLM 提供 JSON → 工具验证
["write_artifact"]          # 写入 workspace
["validate_output"]         # 验证 artifact 存在性
```

#### Operation Tools

```python
# template_matching:
["read_artifact", "search_templates", "write_artifact", "validate_output"]

# ppt_assembly:
["read_artifact", "assemble_pptx", "check_file", "write_artifact", "validate_output"]
```

#### Skill Autonomous Tools

```python
["run_shell_command"]       # 执行 Skill 脚本（沙箱保护）
["check_file_exists"]       # 文件存在性检查
["list_directory"]          # 目录列表
["read_artifact", "write_artifact", "validate_output"]
```

### 9.4 Fallback 机制

```python
def run(self, task_prompt, context=None):
    result = super().run(task_prompt, context)  # Agent loop

    if result.stop_reason == StopReason.COMPLETED:
        if self.phase in _SKILL_AUTONOMOUS:
            self._post_skill_execution()  # 后处理（检查生成图片）
        return result

    # Agent loop 未正常完成 → 降级到确定性 Worker
    return self._fallback_execution(result)

def _fallback_execution(self, agent_result):
    # 使用 capability.fallback_module 或 _PHASE_WORKERS 映射
    # import 对应模块 → 调用 module.run(workspace, force, llm_client)
```

### 9.5 Shell 沙箱 (`_run_shell_command`)

三层安全：

```python
# 1. 模式黑名单
_BLOCKED = ["rm -rf / ", "rm -rf /* ", "mkfs", "mke2fs", "dd if=", ":(){ :|:& };:"]

# 2. 工作目录必须在 workspace 内
if workspace_root not in work_dir.parents and work_dir != workspace_root:
    return ERROR

# 3. 环境变量白名单
safe_env = {
    "PATH", "HOME", "LANG", "PYTHONUNBUFFERED", "WORKSPACE_ROOT",
    "TMPDIR", "PYTHONPATH",  *_API_KEY_VARS
}
```

---

## 第十部分：能力注册表与工具工厂

### 10.1 AgentCapability (`coordinator/capabilities.py`)

```python
@dataclass(frozen=True)
class AgentCapability:
    capability_id: str       # "document_analysis"
    description: str         # "Analyze uploaded project materials..."
    input_artifacts: list[str]   # ["source_summary"]
    output_artifacts: list[str]  # ["outline"]
    tools: list[str]         # ["read_artifact", "write_artifact", ...]
    max_turns: int = 10      # Agent loop 最大轮次
    fallback_module: str     # "ppt_agent.workers.document_analyst"
    reads_input_dir: bool    # 是否读原始输入文件
    skill_name: str          # 关联 Skill
```

**8 个能力一览：**

| Capability | Input | Output | Turns | Skill | Fallback |
|-----------|-------|--------|-------|-------|----------|
| document_analysis | (raw files) | source_summary | 8 | - | workers.document_analyst |
| outline_generation | source_summary | outline | 10 | ppt-outline-generator | workers.outline_generator |
| template_matching | outline | selected_template, template_meta, template_zones | 8 | ppt-template-matcher | workers.template_matcher |
| design_planning | outline, template_meta | slide_design_plan | 10 | ppt-design-director | workers.design_director |
| content_mapping | outline, selected_template, slide_design_plan, source_summary, template_zones | slide_contents | 12 | ppt-content-mapper | workers.content_mapper |
| visual_generation | slide_contents, template_zones | image_generation_report | 15 | gptimage2-generator | workers.image_generator |
| ppt_assembly | slide_contents | (final.pptx) | 8 | ppt-assembler | workers.ppt_assembler |
| quality_verification | slide_contents, image_generation_report | validation_report | 8 | - | workers.ppt_verifier |

### 10.2 ToolFactory (`coordinator/tool_factory.py`)

```python
class ToolFactory:
    def create_registry_for_capability(self, capability, skill_loader, llm_client) -> ToolRegistry:
        registry = ToolRegistry()
        self._register_common(registry, capability, skill_loader)
        # 注册 5 个通用工具: read_artifact, write_artifact, validate_output,
        #                    search_knowledge_base, load_skill
        self._register_domain(registry, capability, llm_client)
        # import tool_impls.<capability_id> → register_tools()
        return registry
```

### 10.3 ToolRegistry (`tools/registry.py`)

完整的 RBAC 工具注册中心：

```python
# 9 个工具类别
CATEGORY_PERMISSIONS = {
    "filesystem": WORKER_AND_ABOVE,
    "execution": WORKER_AND_ABOVE,
    "retrieval": WORKER_AND_ABOVE,
    "assembly": WORKER_AND_ABOVE,
    "verification": WORKER_AND_ABOVE,
    "extraction": WORKER_AND_ABOVE,
    "generation": WORKER_AND_ABOVE,
    "skill": WORKER_AND_ABOVE,
    "artifact": FULL_ACCESS,          # 所有角色可用
    "orchestration": COORDINATOR_ONLY, # 仅 Coordinator
    "communication": FULL_ACCESS,
}

# 4 个角色
ROLE_MAIN_AGENT, ROLE_COORDINATOR, ROLE_WORKER, ROLE_SUB_AGENT
```

**三层权限检查：**

```python
def check_permission(self, tool_name, role):
    # Layer 1: 工具是否注册？
    # Layer 2: 角色能否访问该类别？
    # Layer 3: 工具描述符是否允许该角色？
```

### 10.4 Tool Implementations (`coordinator/tool_impls/`)

| 模块 | 注册的工具 | 类别 |
|------|----------|------|
| `common.py` | read_artifact, write_artifact, validate_output, search_knowledge_base, load_skill | artifact/retrieval/skill |
| `document_analysis.py` | read_input_files | extraction |
| `outline_generation.py` | (无，仅用通用工具) | - |
| `template_matching.py` | search_templates | retrieval |
| `design_planning.py` | (无，仅用通用工具) | - |
| `content_mapping.py` | (无，仅用通用工具) | - |
| `visual_generation.py` | run_shell_command, check_file_exists, list_directory | execution/filesystem |
| `ppt_assembly.py` | assemble_pptx, check_file | assembly/verification |
| `quality_verification.py` | (无，仅用通用工具) | - |

这些模块都遵循相同的注册接口：
```python
def register_tools(registry, workspace, capability, llm_client=None):
    ...
```

---

## 第十一部分：Skill 系统

### 11.1 SkillLoader (`skills/loader.py`)

```python
class SkillLoader:
    def __init__(self, root=Path(".catpaw/skills")):
        self._loaded: set[str] = set()      # 防止重复加载
        self._catalog: list[SkillMetadata]   # Phase 1 缓存

    def get_catalog(self) -> list[SkillMetadata]:
        """Phase 1: 轻量目录——扫描目录 + 解析 SKILL.md frontmatter"""

    def get_catalog_summary(self) -> str:
        """紧凑的 "Available Skills" 列表，注入 system prompt"""

    def load_skill(self, skill_name) -> str | None:
        """Phase 2: 完整加载 SKILL.md，单次加载保证"""

    def is_loaded(self, skill_name) -> bool:
        """检查是否已加载"""
```

**目录发现逻辑：**
```python
def _discover_with_descriptions(root):
    for path in sorted(root.iterdir()):
        skill_md = path / "SKILL.md"
        if skill_md.exists():
            # 解析 YAML frontmatter (---...---) 提取 description
            ...
```

### 11.2 Skill 目录结构

```
.catpaw/skills/
├── ppt-outline-generator/
│   ├── SKILL.md           ← YAML frontmatter + Markdown 指令
│   ├── references/structures.md   ← PPT 结构模板
│   └── examples/outline.example.json
├── ppt-template-matcher/
│   └── ...
├── ppt-content-mapper/
├── ppt-design-director/
├── ppt-image-layer/
├── ppt-assembler/
└── gptimage2-generator/
    ├── SKILL.md
    └── references/api-reference.md
```

### 11.3 渐进式加载流程

```
System Prompt 注入 Phase 1:
  ## Available Skills
  - ppt-outline-generator: PPT 大纲生成
  - ppt-template-matcher: 模板匹配
  ...

Phase 2 运行时:
  Agent 调用 load_skill("ppt-outline-generator")
  → SkillLoader.load_skill() → 读 SKILL.md 全文
  → AgentLoop._load_skill_tool() → append 到 system prompt
  → _loaded_skills.add("ppt-outline-generator")
  → 后续调用返回 "already_loaded"
```

---

## 第十二部分：8 大 Worker 详解

### 12.1 document_analyst（文档分析）

文件：`src/ppt_agent/workers/document_analyst.py`

**流程：**

```
1. 扫描 input_dir 所有文件
2. 并行处理每个文件（ThreadPoolExecutor, max_workers=8）:
   - 图片文件 (.png/.jpg/...) → ImageInventoryItem
     - 文件名含 "screen"/"截图"/"界面" → usage="screenshot"
     - 否则 → usage="reference_image"
   - 文本文件 → extract_text() 提取文本
3. 组装 full_text = "[文件名]\n内容" 拼接
4. LLM 分析或启发式分析
```

**LLM 模式：**
- System: `"You are a document analyst for PPT generation. Output only valid JSON."`
- Prompt: 从 `llm/prompts/document_analysis.md` 加载
- Context: `{extracted_text (前15000字), files, images, existing_warnings}`
- Fallback: `_fallback_analysis()` 启发式分析

**启发式分析 `_fallback_analysis()`：**
```python
def _fallback_analysis(full_text, files, images, warnings):
    project_name = 取第一个 3-60 字符的行
    domain = 文本含"系统/平台/软件"关键词 → "software/product"
    capabilities = 前 5 个 >8 字符的句子
    evidence = 每个句子 → evidence item (confidence=0.7)
    return SourceSummary(...)
```

### 12.2 outline_generator（大纲生成）

文件：`src/ppt_agent/workers/outline_generator.py`

**LLM 模式：** 读取 `llm/prompts/outline_generation.md` 作为 prompt，输入 source_summary，输出结构化大纲。

**确定性 Fallback：** 8 页标准结构：
```
cover → background → problem → solution → product
→ evidence → roadmap → closing
```

每页 3 个 bullet，从 source_summary 的 capabilities/evidence 提取内容。

### 12.3 template_matcher（模板匹配）

文件：`src/ppt_agent/workers/template_matcher.py`

**流程：**
```
1. 从 outline 构建查询: query_text = "医疗 投资人 专业严谨"
2. 加载模板索引: load_template_index()
3. 4-Stage RAG: query(documents, query_text, top_k=3)
4. 最佳匹配 score > 0 → 选为模板
   否则 → fallback_selection()
5. 产出: selected_template.json + template_meta.json + template_zones.json
```

详见[第十三部分：检索管线](#第十三部分检索管线)。

### 12.4 design_director（设计指导）

文件：`src/ppt_agent/workers/design_director.py`

根据 outline + template_meta 生成每页的设计方案：

**输出 slide_design_plan.json：**
- `theme_profile`: 颜色、字体、间距、形状、图片、图表 token
- `slides[]`: 每页的布局选择、视觉密度、回退标记
- `global_style_notes`: 全局设计建议

**LLM 模式：** 使用 `llm/prompts/design_planning.md` prompt
**Fallback：** `default_design_plan()` 生成基础设计

### 12.5 content_mapper（内容映射）

文件：`src/ppt_agent/workers/content_mapper.py`

将大纲内容映射到模板的具体 zone 中。最复杂的 Worker，需要协调 5 个输入 artifact。

**关键逻辑：**
- 模板 zone 类型匹配：title → title zone, bullets → body zone
- 文本适配：标题长度 > 指定阈值则截断
- 图片映射：用户上传图片 → image zone，AI 生成图片 → image_prompt
- 输出 `slide_contents.json` 含 `review_status: "draft"`

### 12.6 image_generator（图片生成）

文件：`src/ppt_agent/workers/image_generator.py`

**Skill-Autonomous 模式：** 通过 `gptimage2-generator` Skill 自动化图片生成。

```python
def prepare_generation_config(workspace):
    """从 slide_contents 提取需要生成图片的 zone，生成 image_generation_config.json"""

def build_skill_context(workspace, config):
    """构建 Python 脚本的上下文参数（slide 数量、prompts、aspect_ratio 等）"""
```

**执行模式：** Worker 读取 SKILL.md，自主决定执行哪些 shell 命令（如 `python gptimage2_client.py generate ...`）。

### 12.7 ppt_assembler（PPT 组装）

文件：`src/ppt_agent/workers/ppt_assembler.py`

**入口保护：** 检查 `review_status == "approved"` 或 `force=True`，否则拒绝执行。

**组装：** 调用 `ppt_writer.write_pptx(slide_contents, output_path, workspace_root, template_path)`。

详见[第十五部分：PPT 组装](#第十五部分ppt-组装)。

### 12.8 ppt_verifier（验证）

文件：`src/ppt_agent/workers/ppt_verifier.py`

**确定性检查：**
- final.pptx 是否存在且大小 > 0
- slide_contents 的每页是否有 title
- bullet 数量是否合理（≤6 条）

**LLM 模式（Fresh Eyes）：** 独立的 LLM 调用，不传入之前任何 conversation 上下文——模拟 "第一次看到这个 PPT" 的视角，发现自审查盲区。

---

## 第十三部分：检索管线

完整检索系统，分**离线入库**和**在线检索**两大环节。详细文档见 `docs/PPT-Agent-分块检索策略详解.md`。

### 13.1 离线入库 (IngestionPipeline)

```
文件 → 文本提取 → 智能分块 → 上下文增强 → 持久化
```

**分块策略（4 种）：**

| 文档类型 | 策略 | 核心逻辑 |
|---------|------|---------|
| Markdown | Header-based 分割 | 按 `#` 标题层级，同级标题为边界 |
| 源代码 | 函数定义分割 | 正则匹配 def/class/function/async def |
| JSON/JSONL | 顶层对象分割 | JSONL 按行、Array 按元素、Object 取 list value |
| 纯文本 | 递归字符分割 + overlap | max_chars=512, overlap=64，在段落/标点处断开 |

**上下文增强（Anthropic Contextual Retrieval）：**

每个 chunk 前面拼接 LLM 生成的上下文导言：
```
[Preamble] This chunk is from 项目计划书.pdf, located in the middle
           of the document (chunk 5/12), containing a text passage.
[Original] 系统核心功能包括...
```

### 13.2 模板索引自动发现 (`retrieval/template_index.py`)

```python
def load_template_index():
    # 扫描 templates/*/*/meta.json
    # 为每个模板生成:
    #   retrieval_text = "简洁专业 blue 科技 路演 tech/modern-blue-12"
    #   domain_tags, tone_tags, color_scheme, slide_count
    # 手动 index.json 覆盖自动发现
```

**颜色推断 `_guess_color_name()`：**
从 `color_scheme.primary` 的 hex 值启发式推断颜色名（饱和度+主导通道）。

### 13.3 在线检索 (4-Stage RAG Pipeline)

```python
def query(documents, query_text, methods=None, top_k=5, llm_client=None) -> list[dict]:
```

**Stage 1: 查询增强**
- LLM 改写（非 LLM → identity fallback）
- 多查询扩展（2-4 个，LLM 模式）或启发式扩展（去停用词+二元组，非 LLM）

**Stage 2: 双路检索 + RRF 融合**
- BM25 (k1=1.5, b=0.75)：从零构建的倒排索引
- TF-IDF (规范化 TF + 平滑 IDF + Cosine 相似度)：本地向量空间
- 各取 top-100 → RRF (k=60) 融合 → 跨查询再次 RRF

**Stage 3: 启发式重排序**
- 4 信号加权：phrase_overlap(0.35) + term_coverage(0.35) + position_weight(0.15) + length_norm(0.15)
- min_score=0.01 过滤

**Stage 4: 上下文组装**
- 去重（按 id → template_id → text[:200]）
- 来源标注 + top_k 截断

---

## 第十四部分：设计系统

### 14.1 主题 (`design/theme.py`)

```python
DEFAULT_THEME = {
    "primary": "#1F4E79", "secondary": "#70AD47",
    "accent": "#F4B183", "background": "#FFFFFF", "text": "#1F2933"
}
```

### 14.2 布局语法 (`design/layout_grammar.py`)

```python
LAYOUTS = {
    "cover.hero": {"max_text_items": 2, "recommended_visual_ratio": 0.4},
    "fallback.basic": {"max_text_items": 6, "recommended_visual_ratio": 0.25},
}
```

### 14.3 视觉密度 (`design/visual_density.py`)

```python
def classify_density(title, bullets) -> str:
    count = len(title) + sum(len(item) for item in bullets)
    if count < 160:  return "low"
    if count < 420:  return "medium"
    return "high"
```

### 14.4 设计评分 (`design/design_scorer.py`)

```python
def score_slide(slide) -> tuple[int, list[str]]:
    # bullets > 6 → 65 分 + "Reduce bullet count"
    # 否则 → 85 分
```

### 14.5 重写建议 (`design/rewrite_suggestions.py`)

```python
def suggest_shorter_bullets(bullets, max_chars=90):
    # 超长 bullet → 截断 + "..."
```

---

## 第十五部分：PPT 组装

文件：`src/ppt_agent/assembly/ppt_writer.py`

### 15.1 两种构建模式

**模式 A：背景图片模式（新）**
```
每个 slide:
  1. 全页背景图片（AI 生成）
  2. 透明 textbox 叠加
  3. 白色文字 + 阴影（保证可读性）
```
`_build_slide_with_background()` → `_set_background_image()` + `_add_text_overlay()`

**模式 B：纯色背景模式（Legacy）**
```
每个 slide:
  1. 白色背景
  2. 不同 zone type 对应不同渲染:
     - title → 深色大号文字
     - bullets → 深色 bullet list
     - image → 图片或占位矩形
     - chart → python-pptx chart 或占位
     - shape → 自定义形状
```

### 15.2 模板模式 `_write_with_template()`

已有模板时的高级组装策略：

```
1. 保留原模板 slides 的视觉设计
2. 对每个 outline slide:
   a. Clone 对应模板 slide (深拷贝 XML 元素 + 背景)
   b. Overlay 用户文本内容
   c. 形状角色推断 (_infer_shape_role):
      - Placeholder type (PPTX XML 解析) → 最可靠
      - 位置启发式 (top, width, 字符数)
      - 字体启发式
3. 按类型优先级匹配 zone → shape
4. 清除未匹配形状的文本
5. 删除原始模板 slides
```

### 15.3 文本适配 (`assembly/layout_fit.py`)

```python
def title_font_size(text: str) -> int:
    len > 60 → 24pt, > 36 → 30pt, else → 36pt

def bullet_font_size(bullets: list[str]) -> int:
    total > 420 or len > 6 → 15pt, total > 240 → 17pt, else → 20pt
```

### 15.4 HTML 预览 (`assembly/html_preview.py`)

从 `slide_contents.json` 生成可视化卡片预览，每张卡片显示：
- Slide 序号 + 布局类型
- 标题 + bullet 列表
- 图片区域（真实图片或 prompt 文本）
- Review 状态徽章（draft / approved）
- 回退标记（visual_placeholder 等）

---

## 第十六部分：事件总线

文件：`src/ppt_agent/coordinator/event_bus.py`

### 16.1 事件类型（13+ 种）

```python
class EventType:
    # 生命周期
    PHASE_STARTED, PHASE_COMPLETED, AGENT_SPAWN, AGENT_COMPLETE, STATUS_CHANGE
    # 执行
    TOOL_CALL_START, TOOL_CALL_RESULT, LLM_CALL_START, LLM_CALL_RESULT
    # 系统
    ARTIFACT_WRITTEN, SKILL_LOADED, COMPRESSION_EVENT, MEMORY_UPDATE
    # 用户交互
    USER_INPUT_REQUEST
    # 诊断
    WARNING, ERROR, PROGRESS, TEXT_DELTA
```

### 16.2 事件对象格式

```python
event = {
    "event_id": "evt_<uuid12>",
    "job_id": "...",
    "phase": "...",
    "type": "phase_started",
    "timestamp": "2026-07-08T...",
    "message": "Starting document_analysis",
    "artifact_path": "..." | None,
    "metadata": {...} | None
}
```

### 16.3 Consumer 体系

```python
class EventConsumer(ABC):
    def handle(self, event: dict) -> None: ...

# 4 个实现:
FileConsumer      → append to history.jsonl      (自动注册)
LoggingConsumer   → Python logging
CallbackConsumer  → 用户自定义函数
SSEConsumer       → asyncio.Queue → FastAPI StreamingResponse
```

### 16.4 反压控制

每个 Consumer 独立 `max_buffer=1000`：
- 未超 → 正常 dispatch
- 超限 → drop + `dropped_events` 计数器递增
- Agent loop 永不阻塞

---

## 第十七部分：记忆系统

### 17.1 架构

```
Layer 1: agent.md    (per-job, 不可变)
Layer 2: memory.md   (workspace 级别, 跨 job 共享)
Layer 3: session.md  (per-job, 当前会话)
```

### 17.2 注入顺序

```
[System Prompt] = agent.md 全文
                + memory.md 全文 (如果存在且非空)
                + session.md 全文
                + Skill 目录摘要
                + 已加载 Skill 全文
```

### 17.3 Dream 任务

流水线完成后执行：
```python
run_dream_task(workspace_root, llm_client):
    1. 读 session.md + history.jsonl tail (最后 20 行)
    2. LLM 提取 1-3 条 durable insights
    3. relevance >= 0.6 → append to memory.md
    4. 无 LLM → 提取含 "warning"/"error"/"failed"/"fallback" 的行
```

---

## 第十八部分：前端与服务器

### 18.1 React 前端 (`frontend/`)

基于 React + TypeScript + Vite + Tailwind 的 Web Dashboard：
- 作业列表、创建、运行
- 实时 SSE 事件监控
- Artifact 查看
- HTML 预览
- 图片生成进度

### 18.2 FastAPI 后端 (`server/app.py`)

核心 API 设计：
- RESTful 资源映射：`/api/jobs/{id}/artifacts/{name}`
- 后台任务：`BackgroundTasks.add_task()` 启动流水线
- SSE 实时流：`StreamingResponse(event_generator(), media_type="text/event-stream")`
- 30 秒 keepalive 心跳

---

## 附录：关键设计模式

### A.1 策略模式
- 分块：`chunk_document()` 按类型分发到 `chunk_markdown` / `chunk_source_code` / `chunk_json` / `chunk_text`
- Provider：`BaseLLMProvider` → `AnthropicProvider` / `OpenAICompatibleProvider` / `FakeProvider`
- Worker 类别：content_reasoning / operation / skill_autonomous

### A.2 模板方法模式
- `AgentLoop.run()` 定义主循环骨架，子类实现 `build_system_prompt()` / `get_available_tools()` / `execute_tool()` / `parse_llm_response()`

### A.3 工厂模式
- `ToolFactory.create_registry_for_capability()` 按能力创建工具集
- `LLMClient.from_config()` 按 profile 创建客户端
- `_create_provider()` 按 protocol 创建 Provider

### A.4 观察者模式
- EventBus pub/sub：多个 Consumer 订阅 EventType，解耦事件生产与消费

### A.5 命令模式
- CLI 的 6 个子命令：`cmd_create_job`, `cmd_run`, `cmd_approve`, `cmd_preview`, `cmd_validate_artifacts`, `cmd_serve`

### A.6 责任链模式
- JSON 修复：直接 parse → 提取 JSON → 自动修复（逗号/引号/括号）→ LLM 重试
- LLM JSON 生成：首次尝试 → 修复 → fallback

### A.7 降级模式
- LLM → 确定性函数 fallback
- LLM 上下文增强 → 启发式 preamble
- LLM 查询扩展 → 启发式扩展
- GPTImage2 不可用 → 纯模板填充模式
- OCR 不可用 → 返回空列表，依靠 meta.json

### A.8 门控模式
- Content review gate：`review_status == "approved"` 或 `force=True` 才能继续到 ppt_assembly
- Dream relevance gate：`relevance >= 0.6` 才写入 memory.md
- Reranker min_score gate：`score >= 0.01` 才保留

---

> *文档版本: 2.0*
> *生成时间: 2026-07-08*
> *覆盖: 全部 110+ Python 源文件*
> *目标读者: 正在学习 PPT-Agent 项目架构与实现的开发者*
