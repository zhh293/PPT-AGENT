# Agent Mode 当前问题清单

> 状态：路由已激活，启动不会崩，但运行时存在多个阻塞性 bug。未经过端到端实测。

---

## 一、已修复（上一轮）

| # | 问题 | 严重度 | 状态 |
|---|------|--------|------|
| 1 | 所有 tool executor 签名 `(args: dict)` 但被调时传 `(call_id, arguments)` → TypeError | 🔴 崩溃 | ✅ |
| 2 | `"verification"` vs `"quality_verification"` 命名不一致 → KeyError | 🔴 崩溃 | ✅ |
| 3 | FakeProvider 不理解 agent-loop JSON 协议 → NO_ACTION 空转 | 🔴 空转 | ✅ |
| 4 | `_PHASE_EXTRA_KWARGS` 只对 LLM 阶段生效 | 🟠 | ✅ |
| 5 | `_futures` 泄漏、Mailbox 无锁、logger 位置等 | 🟡 | ✅ |

---

## 二、未修复 — 阻塞 Agent Mode 实际运行

### B1. Coordinator 的工具名和参数名对不上

**文件：** `coordinator/coordinator_agent.py` + `coordinator/workflow.py`

**现象：**

task_prompt 告诉 LLM 用的工具名是：
```python
# workflow.py:258
"spawn a worker using the spawn_worker tool."
```

但 `get_available_tools()` 里注册的名字是：
```python
# coordinator_agent.py:134
{"name": "spawn_agent", ...}
```

`spawn_worker` 不在工具列表里。如果 LLM 严格按指令找 `spawn_worker` → 找不到。

**叠加第二层：** 就算 LLM 用了 `spawn_agent`，`execute_tool` 里 `spawn_worker` 的 handler 读的参数名也不对：

```python
# coordinator_agent.py:167-170 — spawn_worker handler
return self._spawn_agent(
    args.get("phase", ""),        # ← 读的是 "phase"
    args.get("instructions", ""),
    False,
)
```

但工具的 `parameters` 描述里定义的是：
```python
{"spawn_agent": {"parameters": {
    "capability": "Capability ID",   # ← 定义的是 "capability"
    ...
}}}
```

LLM 按工具描述传 `{"capability": "document_analysis"}` → handler 读 `args.get("phase", "")` → 空字符串 → `"capability is required"` 错误。

**影响：** Coordinator 第一步 tool call 就失败。

**修复方向：**
- task_prompt 里 `spawn_worker` → `spawn_agent`
- 统一参数名：要么全用 `capability`，要么 handler 里同时读 `capability` 和 `phase`

---

### B2. Worker 的工具列表缺 `compose_artifact`

**文件：** `coordinator/capabilities.py` + `coordinator/tool_impls/common.py`

**现象：**

Worker 的 system prompt（`_build_content_reasoning_prompt`）告诉 LLM 标准 7 步工作流，其中包括：

```
3. COMPOSE — Use compose_artifact to generate the output JSON.
```

但 Agent Mode 的 Worker 通过 ToolRegistry 获取工具，ToolRegistry 的工具来自 `capabilities.py` 的 `tools` 列表：

```python
# capabilities.py — 5 个 content_reasoning 能力的 tools 列表
DOCUMENT_ANALYSIS.tools    = ["read_input_files", "read_artifact", "write_artifact", "validate_output", "search_knowledge_base"]
OUTLINE_GENERATION.tools   = ["read_artifact", "write_artifact", "validate_output", "search_knowledge_base", "load_skill"]
DESIGN_PLANNING.tools      = ["read_artifact", "write_artifact", "validate_output", "load_skill"]
CONTENT_MAPPING.tools      = ["read_artifact", "write_artifact", "validate_output", "search_knowledge_base", "load_skill"]
QUALITY_VERIFICATION.tools = ["read_artifact", "write_artifact", "validate_output", "check_file", "check_file_exists"]
```

**没有一个包含 `compose_artifact`。** common.py 也没有 `make_compose_artifact` maker。

注意：WorkerAgent 的旧版（非 registry 路径）`_get_content_reasoning_tools()` **是包含 compose_artifact 的**。但 Agent Mode 下 `self._registry is not None` → 直接用 registry → 旧版工具列表被绕过。

**影响：** content_reasoning 类的 5 个 Worker 拿不到 compose_artifact。LLM 按 system prompt 的指示找不到这个工具 → 要么跳过 compose 步骤，要么幻觉调用（error）。

**修复方向：**
- 在 `capabilities.py` 的 5 个 content_reasoning 能力 tools 列表中加入 `"compose_artifact"`
- 在 `tool_impls/common.py` 中实现 `make_compose_artifact`

---

### B3. parse_llm_response 不允许纯思考回合

**文件：** `coordinator/coordinator_agent.py:177` + `coordinator/worker_agent.py:727`

**现象：**

```python
def parse_llm_response(self, result):
    text = result.text
    try:
        data, _warnings = repair_json(text)
    except Exception:
        return text, [], False       # 不是 JSON → 无工具、无 done

    if not isinstance(data, dict):
        return text, [], False       # 是 JSON 非 dict → 同上

    if data.get("done") is True:
        return text, [], True        # 明确 done

    tool_name = data.get("tool")
    if tool_name:
        return text, [ToolCall(...)], False  # 工具调用

    return text, [], False           # ← 问题在这里
```

当 LLM 返回一个合法 JSON dict，但不包含 `"done": true` 也不包含 `"tool": "xxx"` 时——比如 LLM 想做纯推理：

```json
{"reasoning": "outline 有 8 页，但选中的模板只有 5 页 layout。需要重新评估或让 design_planning 调整。", "decision": "proceed with caution"}
```

→ 最后一个 `return text, [], False` → AgentLoop 看到 `not tool_calls and not stop_signal` → **`StopReason.NO_ACTION`** → Agent Loop 整条终止。

**这意味着 LLM 每轮必须恰好输出一个 tool call 或 done。不能思考、不能输出多个工具调用、不能先解释再调工具。** Anthropic/OpenAI 的 tool-use API 允许 assistant 返回纯文本后继续，但这个自建循环不支持。

**影响：** LLM 只要不小心输出一个非工具非 done 的 JSON（极容易发生），Worker 或 Coordinator 直接终止。

**修复方向：**
- `parse_llm_response` 的最后一个 return 改为 `return text, [], False` 保持不变，但在 AgentLoop 中区分 "纯文本思考" 和 "真正的 NO_ACTION"：如果是纯文本，把文本作为 assistant message 追加然后继续下一轮，而不是终止

---

### B4. Coordinator 收到矛盾指令

**文件：** `coordinator/coordinator_agent.py:88-130` + `coordinator/workflow.py:257-264`

**现象：**

System prompt 说：
```
"Decide AUTONOMOUSLY which capabilities to invoke and in what order."
"Use spawn_agent(capability=..., async=true) for parallel work."
```

Task prompt 说：
```
"Execute the PPT generation pipeline phases in order: document_analysis, ..."
"Wait for each worker to complete before spawning the next."
```

一个说"自主决策、可以并行"，另一个说"严格顺序、一个一个来"。

**影响：** LLM 不知道听谁的。可能有些模型按 task_prompt（顺序执行），有些尝试并行但和 task_prompt 矛盾。

**修复方向：** 统一为一个指令源。Agent Mode 的设计意图是自主编排 → task_prompt 应该去掉"按顺序、等上一个"的约束。

---

### B5. 工具参数描述不是 JSON Schema

**文件：** `coordinator/coordinator_agent.py:132-146`

**现象：**

Coordinator 的工具参数是自由文本：

```python
{"name": "spawn_agent", "parameters": {
    "capability": "Capability ID",                        # 自由文本
    "instructions": "Extra instructions",                 # 自由文本
    "async": "Run in background (default: false)"         # 自由文本
}}
```

渲染到 prompt 里变成：
```
Parameters: {"capability": "Capability ID", "instructions": "Extra instructions", "async": "Run in background (default: false)"}
```

LLM 看不出 `capability` 是必填 string、`async` 是 boolean。只能靠自然语言猜。

**对比：** Worker 端走 ToolRegistry → `ToolDescriptor.to_llm_dict()` → 有 `{"type": "string"}` 等基础类型标注，比 Coordinator 端好。但也缺少 `required`、`enum` 等约束。

**影响：** LLM 可能传 `"async": "yes"`（字符串而非 bool），`capability` 名可能拼错。handler 里 `str(args.get("async", "false")).lower() == "true"` 做了容错，但 capability 名错就 KeyError。

**修复方向：** Coordinator 的 `get_available_tools()` 改用标准 JSON Schema 格式描述参数。

---

### B6. LLM 调用失败无重试

**文件：** `runtime/agent_loop.py:452-460`

**现象：**

```python
def _call_llm(self) -> LLMResult:
    ...
    result = self.llm_client.provider.generate(messages, ...)
    ...
    if not result.success:
        raise RuntimeError(f"LLM returned error: {result.error}")
```

一次网络超时 / rate limit → 抛异常 → `_execute_turn` 捕获 → `_consecutive_errors += 1` → 连续 3 次 → **ERROR_BUDGET → Coordinator 或 Worker 整条终止。**

**对比：** `LLMClient.generate_json()` 至少有 JSON repair 重试。AgentLoop 直接调 provider，什么都没有。

**影响：** API 不稳定时整条流水线崩。

**修复方向：** 在 `_call_llm` 中加入指数退避重试（2-3 次）。

---

### B7. Skill 路径是相对路径

**文件：** `coordinator/workflow.py:220`

**现象：**

```python
skill_loader = SkillLoader(root=Path(".catpaw/skills"))
```

如果 server 不是从项目根目录启动（比如 systemd、docker、或从其他目录执行 `python -m ppt_agent`），`Path(".catpaw/skills")` 解析到错误位置 → 所有 Skill 加载失败 → Worker 系统 prompt 缺领域知识。

**影响：** 取决于启动方式。开发环境从项目根目录跑没问题，部署环境可能挂。

**修复方向：** 用 `Path(__file__).resolve().parent.parent.parent.parent / ".catpaw" / "skills"` 或环境变量。

---

## 三、严重度汇总

| # | 问题 | 严重度 | 现象 |
|---|------|--------|------|
| B1 | spawn 工具名/参数名不匹配 | 🔴 阻塞 | Coordinator 第一步就失败 |
| B2 | 缺 compose_artifact | 🔴 阻塞 | 5 个 Worker 核心工作流断裂 |
| B3 | 不允许纯思考回合 | 🔴 阻塞 | LLM 非工具输出 → NO_ACTION 终止 |
| B4 | Coordinator 收到矛盾指令 | 🟠 混乱 | LLM 行为不可预测 |
| B5 | 工具参数非标准 Schema | 🟡 隐患 | 参数类型/名可能传错 |
| B6 | LLM 调用无重试 | 🟡 脆弱 | 网络波动 → 整体崩溃 |
| B7 | Skill 路径相对 | 🟢 环境 | 部署时可能找不到 Skill |

---

## 四、修复优先级

**第一批（不改就跑不起来）：** B1 + B2 + B3

这三个是硬 bug，和 LLM 能力强弱无关。修完至少能走通一个完整的 Coordinator→Worker→产出 的循环。

**第二批（跑了但容易挂）：** B4 + B6

修完能提高稳定性，减少"随机失败"的概率。

**第三批（打磨）：** B5 + B7

不影响核心流程，但影响边缘情况和生产部署。
