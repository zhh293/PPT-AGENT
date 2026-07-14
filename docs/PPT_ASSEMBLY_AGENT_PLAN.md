# PPT Assembly Agent 实施方案

## 目标

`ppt_assembly` 阶段让 Agent 参与决策：读模板数据 + 内容数据 → 决定映射关系 → 调工具执行。

**关键原则**: 数据全在 workspace 的 JSON 文件里，不需要重新读 PPTX。

---

## 改动清单

| 文件 | 做什么 |
|---|---|
| `assembly/ppt_writer.py` | 新增 `write_pptx_from_mapping()` + `_inject_zone_ids()` |
| `workers/ppt_assembler.py` | 新增 `assemble_with_mapping()` |
| `coordinator/tool_impls/ppt_assembly.py` | `assemble_pptx` 改为接收 Agent 映射表 |
| `coordinator/worker_agent.py` | 更新 system prompt 描述 |

---

## 数据流

```
Agent 收到的上下文:
  - 通过 read_artifact 工具可读取 workspace 中的任意 JSON 文件

Agent 读取:
  - template_zones.json  → 每个 slide 的 text_zones / image_zones / all_zones
  - slide_contents.json  → 每个 slide 的 zones (带 content / image_prompt)

Agent 推理后调用:
  assemble_pptx({
    mappings: {
      0: { title:    {zone_id: "s0_shape0", content: "智慧旅游"},
           subtitle: {zone_id: "s0_shape1", content: "全域智慧旅游规划..."} },
      1: { title:    {zone_id: "s1_shape0", content: "行业痛点"},
           bullets:  {zone_id: "s1_shape3", content: ["痛点1","痛点2"]} },
      ...
    },
    image_mappings: {
      0: { "image_s0_shape4": "workspace/jobs/xxx/background_images/slide-000.png" },
      ...
    },
    force: false
  })
```

---

## 详细实现

### 1. `ppt_writer.py` — 新增 `write_pptx_from_mapping()`

```python
def write_pptx_from_mapping(
    template_path: Path,
    template_zones: dict,       # ← 新增参数，用于注入 zone_id
    mappings: dict[int, dict],  # slide_index → {content_type → {zone_id, content}}
    image_mappings: dict[int, dict],  # slide_index → {zone_id → image_path}
    slide_contents: dict,
    output_path: Path,
    workspace_root: Path | None = None,
) -> Path:
    """按 Agent 给的映射表组装 PPTX。

    对每页: 克隆模板页 → 注入 zone_id 到 shape.name
      → 按 zone_id 精确定位 shape → 替换文字/图片
      → 清空未映射 shape 的文字 → 删除原模板页
    """
    prs = Presentation(str(template_path))
    n_template = len(prs.slides)
    slides = slide_contents.get("slides", [])
    original_slide_ids = [s.slide_id for s in prs.slides]
    tpl_slides = {s["index"]: s for s in template_zones.get("slides", [])}

    for idx, slide_data in enumerate(slides):
        tpl_idx = idx % n_template
        _clone_slide(prs, prs.slides[tpl_idx])
        new_slide = prs.slides[-1]

        # 注入 zone_id: shape.name = "s0_shape0"
        tpl_slide = tpl_slides.get(tpl_idx, {})
        zone_id_map = _inject_zone_ids(new_slide, tpl_slide)
        # zone_id_map: {"s0_shape0": shape, "s0_shape1": shape, ...}

        slide_mappings = mappings.get(idx, {})
        img_mappings = image_mappings.get(idx, {})

        matched_shape_ids = set()

        # 文字替换: 按 zone_id 精确定位
        for content_type, entry in slide_mappings.items():
            zid = entry["zone_id"]
            content = entry["content"]
            shape = zone_id_map.get(zid)
            if shape:
                _apply_text_to_shape(shape, {"type": content_type, "content": content})
                matched_shape_ids.add(zid)

        # 图片替换
        for zid, img_path in img_mappings.items():
            shape = zone_id_map.get(zid)
            if shape and Path(img_path).exists():
                _replace_shape_image(shape, Path(img_path))
                matched_shape_ids.add(zid)

        # 清空未匹配的 shape
        for zid, shape in zone_id_map.items():
            if zid not in matched_shape_ids and shape.has_text_frame:
                _clear_shape_text(shape)

    _delete_slides_by_id(prs, original_slide_ids)
    prs.save(str(output_path))
    return output_path


def _inject_zone_ids(slide, tpl_slide: dict) -> dict[str, Any]:
    """把 zone_id 写入 shape.name，返回 zone_id → shape 映射表。

    匹配策略: 按 position 重叠。template_zones 的 zone 位置
    与 PPTX slide 的 shape 位置一一对应（同一来源）。
    """
    all_zones = tpl_slide.get("all_zones", [])
    zone_id_map: dict[str, Any] = {}

    for i, shape in enumerate(slide.shapes):
        best_zone = None
        best_overlap = 0.0
        sx = (shape.left or 0) / SLIDE_W
        sy = (shape.top or 0) / SLIDE_H
        sw = (shape.width or 0) / SLIDE_W
        sh = (shape.height or 0) / SLIDE_H

        for zone in all_zones:
            pos = zone.get("position", [0,0,0,0])
            zx, zy, zw, zh = pos
            # 计算重叠面积
            ox = max(0, min(sx+sw, zx+zw) - max(sx, zx))
            oy = max(0, min(sy+sh, zy+zh) - max(sy, zy))
            overlap = ox * oy
            if overlap > best_overlap:
                best_overlap = overlap
                best_zone = zone

        if best_zone and best_overlap > 0:
            zid = best_zone["zone_id"]
            shape.name = zid
            zone_id_map[zid] = shape

    return zone_id_map
```

### 2. `ppt_assembler.py` — 新增 `assemble_with_mapping()`

```python
def assemble_with_mapping(
    workspace: JobWorkspace,
    mappings: dict,
    image_mappings: dict | None = None,
    force: bool = False,
) -> Path:
    """Agent 驱动的组装。

    mappings: {slide_index: {content_type: {zone_id, content}}}
    image_mappings: {slide_index: {zone_id: image_path}}
    """
    if image_mappings is None:
        image_mappings = {}

    template_path = _find_template_pptx(workspace)
    if not template_path:
        raise FileNotFoundError("No template PPTX found")

    slide_contents = load_artifact(workspace, "slide_contents")
    template_zones = load_artifact(workspace, "template_zones")

    output = workspace.root / "final.pptx"
    from ppt_agent.assembly.ppt_writer import write_pptx_from_mapping
    write_pptx_from_mapping(
        template_path, template_zones, mappings, image_mappings,
        slide_contents, output, workspace.root,
    )
    return output
```

### 3. `tool_impls/ppt_assembly.py` — 工具改为接收参数

```python
def register_tools(registry, workspace, capability, llm_client=None):
    def assemble(call_id: str, arguments: dict) -> ToolResult:
        """Agent 驱动的组装。Agent 必须先读 template_zones + slide_contents
        来决定映射关系，然后调用此工具执行。"""
        try:
            from ppt_agent.workers.ppt_assembler import assemble_with_mapping
            mappings_str = arguments.get("mappings", "{}")
            mappings = json.loads(mappings_str) if isinstance(mappings_str, str) else mappings_str
            img_str = arguments.get("image_mappings", "{}")
            image_mappings = json.loads(img_str) if isinstance(img_str, str) else img_str
            force = arguments.get("force", False)
            output = assemble_with_mapping(workspace, mappings, image_mappings, force)
            return ToolResult(call_id=call_id, output={"path": str(output)}, success=True)
        except Exception as e:
            return ToolResult(call_id=call_id, output=None, success=False, error=str(e))

    registry.register_tool(
        ToolDescriptor("assemble_pptx", "assembly", WORKER_AND_ABOVE,
            "Assemble final PPTX using agent-provided zone-to-shape mappings. "
            "Agent MUST: (1) read template_zones for shape info, "
            "(2) read slide_contents for content, "
            "(3) decide which zone_id gets which content, "
            "(4) call this tool with the mappings.",
            {"mappings": {"type": "string"},
             "image_mappings": {"type": "string", "default": "{}"},
             "force": {"type": "boolean", "default": False}}),
        assemble)
```

### 4. `worker_agent.py` — 更新 System Prompt

```python
"ppt_assembly": (
    "You are a PPT Assembler. Your job is to place content into template shapes.\n\n"
    "WORKFLOW:\n"
    "1. Call read_artifact to read template_zones — "
    "this tells you what shapes exist on each slide (zone_id, type, position, formatting).\n"
    "2. Call read_artifact to read slide_contents — "
    "this tells you what content needs to go on each slide.\n"
    "3. For each slide, MATCH content zones to template zones:\n"
    "   - Match by TYPE: title content → template zones with type='title'\n"
    "   - If no exact type match, use the CLOSEST type (subtitle→title, bullets→body)\n"
    "   - PREFER zones with matching formatting (e.g. 28pt font for title)\n"
    "   - PREFER zones with high text_likeness and is_content_area=true\n"
    "   - Each template zone can be used at MOST once\n"
    "4. Call assemble_pptx with the mappings.\n\n"
    "CRITICAL: Do NOT invent zone_ids. Use ONLY zone_ids from template_zones."
),
```

---

## Agent 推理示例

```
Agent 调用 read_artifact("template_zones") 得到:

  Slide 0 text_zones:
    s0_shape0: type=title, pos=[.046,.114], 28pt, text_likeness=0.95
    s0_shape1: type=title, pos=[.337,.244], 24pt, text_likeness=0.88
    s0_shape3: type=body,  pos=[.127,.361], 12pt, text_likeness=0.42
    ...

Agent 调用 read_artifact("slide_contents") 得到:

  Slide 0 zones:
    zone_0: type=title,    content="智慧旅游"
    zone_1: type=subtitle, content="全域智慧旅游规划..."
    zone_2: type=image,    image_prompt="产品架构图"

Agent 推理:
  "title '智慧旅游' → 找 type=title 的模板 zone"
  "候选: s0_shape0(28pt), s0_shape1(24pt)"
  "s0_shape0 字号更大、text_likeness 更高 → 选它"
  "subtitle → 没有 subtitle 类型，fallback 到 body"
  "s0_shape1(24pt, body) 比 s0_shape3(12pt) 更适合做副标题 → 选 s0_shape1"

Agent 调用 assemble_pptx:
  {
    "mappings": {
      "0": {
        "title":    {"zone_id": "s0_shape0", "content": "智慧旅游"},
        "subtitle": {"zone_id": "s0_shape1", "content": "全域智慧旅游规划..."}
      }
    },
    "image_mappings": {
      "0": {"s0_img1": "workspace/jobs/xxx/background_images/slide-000.png"}
    }
  }
```

---

## 降级路径

如果 Agent 决策失败（agent loop 报错、超时、返回无效映射），`_run_deterministic_worker()` 仍然可用——`ppt_assembler.run()` 走原来的自动匹配路径。两套逻辑共存，agent 失败不影响管道可用性。
