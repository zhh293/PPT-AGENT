# PPT 智能生成 Agent 架构设计

> 基于项目计划书 + 关键图片，全自动生成精美可编辑 PPT 的 Agent 系统

---

## 一、系统全景架构

```
用户输入                            Coordinator Agent                            输出
─────────                          ┌───────────────┐                          ──────
项目计划书    ──────────────►       │   五阶段编排    │      ──────────────►     .pptx
软著证书                            │               │                          可编辑
功能截图                            └───┬───┬───┬───┘
                                        │   │   │
                    ┌───────────────────┘   │   └───────────────────┐
                    ▼                       ▼                       ▼
            ┌──────────────┐       ┌──────────────┐       ┌──────────────┐
            │ Phase 1      │       │ Phase 2-3    │       │ Phase 4-5    │
            │ 文档分析      │──────►│ 模板匹配+内容 │──────►│ 图片生成+分层 │
            │ +大纲生成     │       │ 映射          │       │ +PPT组装     │
            └──────────────┘       └──────────────┘       └──────────────┘
```

系统采用 AGENT_ARCHITECTURE.md 中定义的 **Coordinator-Worker 多 Agent 编排模式**，由一个 Coordinator Agent 负责五阶段流程调度，每个阶段派发专门的 Worker Agent 执行。Coordinator 自身不执行任何具体操作，仅做任务分解、结果收集和阶段交接。

---

## 二、五阶段核心链路

### Phase 1：文档解析与大纲生成

**输入：** 项目计划书（PDF/Word/TXT）、关键图片（软著证书、功能界面截图）

**执行 Agent：** Document Analyst Worker

```
用户上传文件
    │
    ├──► 文本提取 Agent（并行）
    │    ├── PDF/Word → 纯文本 + 结构标记
    │    └── 图片 → OCR 识别（软著证书编号、界面功能文字）
    │
    ├──► 赛道分析 Agent（并行）
    │    ├── 分析文本关键词，判断行业赛道
    │    │   （医疗/教育/金融/制造/互联网/农业...）
    │    ├── 分析目标受众（投资人/评委/客户/内部汇报）
    │    └── 输出：{ track: "医疗", audience: "投资人", tone: "专业严谨" }
    │
    └──► 大纲生成 Agent（依赖前两者）
         ├── 基于赛道知识库选择 PPT 结构模板
         │   （如：路演型 = 问题→方案→市场→产品→团队→财务→融资）
         ├── 生成每页 slide 的标题 + 要点 bullet
         ├── 标注每页需要的图片类型
         │   （产品截图/数据图表/概念图/团队照片）
         └── 输出：outline.json
```

**outline.json 结构示例：**

```json
{
  "meta": {
    "track": "医疗",
    "audience": "投资人",
    "tone": "专业严谨",
    "color_preference": "蓝色系",
    "total_slides": 15
  },
  "slides": [
    {
      "slide_index": 0,
      "type": "cover",
      "title": "智能影像诊断平台",
      "subtitle": "AI赋能基层医疗影像筛查",
      "image_needs": "logo或概念图"
    },
    {
      "slide_index": 1,
      "type": "problem",
      "title": "行业痛点",
      "bullets": [
        "基层医疗机构影像诊断能力不足",
        "三甲医院影像科超负荷运转",
        "误诊漏诊率居高不下"
      ],
      "image_needs": "数据图表"
    },
    {
      "slide_index": 5,
      "type": "product",
      "title": "核心产品",
      "bullets": [
        "AI辅助CT/MRI影像分析",
        "秒级出具初步诊断报告",
        "支持50+常见疾病识别"
      ],
      "image_needs": "产品界面截图（用户提供）"
    }
  ]
}
```

**关键 Skill：** `ppt-outline-generator`（需调试）

这个 Skill 内嵌了不同赛道、不同场景的 PPT 结构模板库。其核心是一个经过 fine-tune 的 LLM prompt，输入是项目文档摘要 + 赛道信息，输出是结构化的 slide outline。调试方向是让 LLM 学习大量优秀路演 PPT 的结构模式，确保大纲逻辑连贯、重点突出。

---

### Phase 2：模板匹配

**输入：** outline.json（含赛道、色调偏好）

**执行 Agent：** Template Matcher Worker

```
outline.json
    │
    ├──► 模板检索（RAG）
    │    ├── 向量检索：用赛道+色调描述做 embedding 搜索
    │    ├── 关键词检索：BM25 匹配模板标签
    │    ├── RRF 融合排序
    │    └── 返回 Top-5 模板候选
    │
    ├──► 模板评分
    │    ├── 匹配度：slide 数量接近度、布局类型覆盖度
    │    ├── 色调匹配：赛道色系偏好 vs 模板主色调
    │    └── 风格匹配：受众正式度 vs 模板设计风格
    │
    └──► 输出：selected_template.pptx + template_meta.json
```

**模板库组织结构：**

```
templates/
├── index.json                    # 模板索引（元数据）
├── medical/                      # 医疗赛道
│   ├── blue-clean-15/            # 蓝色系·简洁·15页
│   │   ├── template.pptx
│   │   ├── preview/              # 每页预览图
│   │   │   ├── slide-0.png
│   │   │   ├── slide-1.png
│   │   │   └── ...
│   │   └── meta.json             # 布局描述
│   ├── green-warm-12/
│   └── ...
├── education/                    # 教育赛道
├── finance/                      # 金融赛道
├── tech/                         # 科技赛道
└── ...
```

**meta.json 结构（每个模板的布局描述）：**

```json
{
  "template_id": "medical/blue-clean-15",
  "track": "医疗",
  "color_scheme": {
    "primary": "#1a73e8",
    "secondary": "#e8f0fe",
    "accent": "#ff6b2c",
    "background": "#ffffff"
  },
  "style": "简洁专业",
  "slide_count": 15,
  "slides": [
    {
      "index": 0,
      "layout": "cover",
      "zones": [
        { "type": "title", "position": [0.1, 0.3, 0.8, 0.15], "font_size": 36 },
        { "type": "subtitle", "position": [0.1, 0.5, 0.8, 0.1], "font_size": 20 },
        { "type": "image", "position": [0.6, 0.2, 0.3, 0.4] }
      ]
    },
    {
      "index": 1,
      "layout": "content-with-image",
      "zones": [
        { "type": "title", "position": [0.08, 0.08, 0.84, 0.1], "font_size": 28 },
        { "type": "bullets", "position": [0.08, 0.25, 0.5, 0.6], "font_size": 16 },
        { "type": "image", "position": [0.6, 0.25, 0.35, 0.55] }
      ]
    }
  ]
}
```

模板匹配使用 AGENT_ARCHITECTURE.md 中定义的混合检索 RAG 管线（Dense Embedding + BM25 + RRF + Reranking），知识库就是模板库的 meta.json 和预览图描述。

---

### Phase 3：模板 OCR 与内容映射

**输入：** selected_template.pptx + template_meta.json + outline.json

**执行 Agent：** Content Mapper Worker

这是整个系统最关键的阶段之一。需要把模板每一页的布局结构"读懂"，然后把大纲内容精准填入。

```
模板 .pptx 文件
    │
    ├──► 模板页面解析（并行处理每页）
    │    ├── 将 .pptx 每页导出为高分辨率图片
    │    ├── OCR 识别图片中的所有文本区域
    │    │   → 得到文本块的位置、大小、内容
    │    ├── 视觉分析识别非文本区域
    │    │   → 图标位置、图表区域、装饰元素
    │    └── 合并 OCR 结果 + meta.json 布局描述
    │       → 每页的精确 zone map
    │
    ├──► 内容生成（依赖页面解析 + 大纲）
    │    ├── 对每页 slide，根据其 layout type 和 zone map
    │    │   从 outline.json 中提取对应内容
    │    ├── LLM 生成适配 zone 的文本
    │    │   （标题长度适配、bullet 数量适配、字号适配）
    │    ├── LLM 生成图片描述 prompt
    │    │   （针对每个 image zone，生成图生图的 prompt）
    │    └── 用户上传的图片直接映射到对应 zone
    │       （如功能截图 → product 页的 image zone）
    │
    └──► 输出：slide_contents.json（用户可编辑）
```

**slide_contents.json 结构：**

```json
{
  "template_id": "medical/blue-clean-15",
  "slides": [
    {
      "slide_index": 0,
      "layout": "cover",
      "zones": [
        {
          "zone_id": "title_0",
          "type": "title",
          "position": [0.1, 0.3, 0.8, 0.15],
          "content": "智能影像诊断平台",
          "editable": true
        },
        {
          "zone_id": "subtitle_0",
          "type": "subtitle",
          "position": [0.1, 0.5, 0.8, 0.1],
          "content": "AI赋能基层医疗影像筛查",
          "editable": true
        },
        {
          "zone_id": "image_0",
          "type": "image",
          "position": [0.6, 0.2, 0.3, 0.4],
          "source": "generate",
          "image_prompt": "Medical AI brain scan concept, blue and white color scheme, clean minimal style, brain MRI visualization with AI neural network overlay"
        }
      ]
    },
    {
      "slide_index": 5,
      "layout": "content-with-image",
      "zones": [
        {
          "zone_id": "title_5",
          "type": "title",
          "position": [0.08, 0.08, 0.84, 0.1],
          "content": "核心产品",
          "editable": true
        },
        {
          "zone_id": "bullets_5",
          "type": "bullets",
          "position": [0.08, 0.25, 0.5, 0.6],
          "content": [
            "AI辅助CT/MRI影像分析",
            "秒级出具初步诊断报告",
            "支持50+常见疾病识别"
          ],
          "editable": true
        },
        {
          "zone_id": "image_5",
          "type": "image",
          "position": [0.6, 0.25, 0.35, 0.55],
          "source": "user_upload",
          "image_ref": "用户上传的功能截图.png"
        }
      ]
    }
  ]
}
```

**用户交互节点：** 此阶段输出后，系统暂停，用户可以修改 slide_contents.json 中的任何文本内容、图片选择和 prompt 描述。确认后继续。

---

### Phase 4：图片生成（GPTImage2 集成）

**输入：** slide_contents.json（用户已确认）+ 模板每页的预览图

**执行 Agent：** Image Generation Worker

这一步利用之前逆向分析的 GPTImage2.online API，通过图生图模式生成每页的视觉设计。核心思路是把模板页面作为参考图，让 AI 在保持布局结构的前提下替换内容。

```
slide_contents.json + 模板预览图
    │
    ├──► 认证流程（Session 初始化）
    │    ├── POST /zh/sign-in（Server Action）
    │    │   → 获取 Supabase session cookie
    │    └── 保存 cookie 供后续 API 调用使用
    │
    ├──► 逐页图片生成（可并行，受积分限制）
    │    │
    │    │  对每个 source="generate" 的 image zone：
    │    │
    │    ├── 上传参考图（图生图模式）
    │    │   ├── POST /api/ai/upload-reference
    │    │   │   → 获取 signedUrl
    │    │   ├── PUT signedUrl（上传模板预览图）
    │    │   │   → 获取公开 url
    │    │   └── 将 url 存入 input_urls
    │    │
    │    ├── 构造生图请求
    │    │   ├── prompt = zone.image_prompt
    │    │   │   + 模板风格描述（色调、风格关键词）
    │    │   │   + 布局约束（"保持左文右图布局"）
    │    │   ├── aspect_ratio = "16:9"（标准 PPT 比例）
    │    │   ├── resolution = "1K"（控制成本）
    │    │   └── input_urls = [模板预览图 url]
    │    │
    │    ├── POST /api/ai/text-to-image
    │    │   → 获取 generationId + pollAfterMs
    │    │
    │    ├── 轮询生成结果
    │    │   ├── 等待 pollAfterMs
    │    │   ├── GET /api/generations/{generationId}
    │    │   ├── status == "succeeded" → 获取 images[]
    │    │   ├── status == "pending" → 继续轮询
    │    │   │   间隔: min(2500 + 300*i, 6000) ms
    │    │   └── 超时 72s → 标记失败，降级处理
    │    │
    │    └── 下载生成的图片到本地
    │
    └──► 输出：generated_slides/
         ├── slide-0.png（完整生成的封面页）
         ├── slide-1.png（完整生成的内容页）
         └── ...
```

**关键设计决策——整页生成 vs 区域生成：**

有两种图片生成策略，系统支持两种模式：

**模式 A：整页生成（推荐，效果好）**

把整个模板页面作为参考图，prompt 描述整页内容，让 AI 生成一整页完整的 PPT 图片。

```
prompt: "Professional PowerPoint slide, blue medical theme,
         title '核心产品' at top, three bullet points on left,
         product screenshot on right, clean modern layout,
         white background with blue accents"

input_urls: [模板对应页面的预览图 url]
aspect_ratio: "16:9"
```

优点是视觉一致性最好，AI 能理解整体布局。缺点是文字可能不够精确。

**模式 B：区域生成（精确度高）**

只对 image zone 生成图片，文本 zone 在 Phase 5 中用 python-pptx 直接写入。

```
prompt: zone.image_prompt  # 只描述图片内容
input_urls: [模板中对应 image zone 区域的截图 url]
aspect_ratio: 根据 zone 比例计算
```

优点是文字精确可控，缺点是整体视觉协调性需要后期调整。

**推荐策略：混合模式**

封面页、过渡页、数据可视化页使用整页生成（追求视觉效果）；内容页使用区域生成（保证文字准确性），背景和装饰元素使用模板原始设计。

---

### Phase 5：图片分层与 PPT 组装

**输入：** generated_slides/*.png + slide_contents.json + template.pptx

**执行 Agent：** PPT Assembler Worker（使用已有的图片分层 Skill）

这一步是"从图片到可编辑文件"的关键跨越。将生成的图片通过分层 Skill 分解为可编辑元素，然后组装成 .pptx 文件。

```
生成的图片 + 模板 + 内容数据
    │
    ├──► 图片分层分析（对每张生成的 slide 图片）
    │    │
    │    │  使用已有的图片分层 Skill：
    │    │
    │    ├── 背景层提取
    │    │   → 纯色背景 / 渐变背景 / 图片背景
    │    │
    │    ├── 文本层识别
    │    │   → 文字内容、字体、字号、颜色、位置
    │    │   → 标题、副标题、正文、列表项分类
    │    │
    │    ├── 图像层识别
    │    │   → 图片区域、图片内容描述
    │    │   → 图标、插图、照片分类
    │    │
    │    ├── 形状层识别
    │    │   → 矩形、圆形、线条等装饰元素
    │    │   → 位置、大小、颜色、透明度
    │    │
    │    └── 图表层识别
    │         → 柱状图、饼图、折线图等
    │         → 转换为可编辑的 PPT 图表对象
    │
    ├──► PPT 组装（使用 python-pptx 或 LibreOffice）
    │    │
    │    │  以 template.pptx 为基础：
    │    │
    │    ├── 背景层 → 设置 slide background
    │    ├── 文本层 → 添加 textbox，写入精确文字
    │    │   （优先使用 slide_contents.json 中的文字，
    │    │    而非 OCR 结果，因为前者经过用户确认）
    │    ├── 图像层 → 插入图片到对应位置
    │    │   （用户上传的图片优先，AI 生成的图片次之）
    │    ├── 形状层 → 用 python-pptx shapes API 重建
    │    └── 图表层 → 用 python-pptx chart API 重建
    │
    ├──► 后处理
    │    ├── 字体嵌入（确保跨设备一致性）
    │    ├── 动画设置（可选，从模板继承）
    │    ├── 页面切换效果（从模板继承）
    │    └── 文件校验（确保可正常打开）
    │
    └──► 输出：final.pptx
```

---

## 三、Skill 体系设计

系统依赖三个核心 Skill，遵循 AGENT_ARCHITECTURE.md 中的 Skill 渐进加载规范：

### Skill 1：ppt-outline-generator（需调试）

```
~/.skills/ppt-outline-generator/
├── SKILL.md                    # Skill 主文件
├── structures/                 # PPT 结构模板库
│   ├── pitch-deck.json         # 路演型（10-15页）
│   ├── product-launch.json     # 产品发布型（15-20页）
│   ├── project-report.json     # 项目汇报型（15-25页）
│   └── competition.json        # 比赛路演型（8-12页）
├── track-knowledge/            # 赛道知识库
│   ├── medical.md              # 医疗赛道术语+常用结构
│   ├── education.md            # 教育赛道
│   ├── finance.md              # 金融赛道
│   └── ...
├── examples/                   # Few-shot 示例
│   ├── medical-pitch-1.json    # 医疗路演大纲示例
│   ├── tech-product-1.json     # 科技产品大纲示例
│   └── ...
└── prompts/
    ├── analyze-track.md        # 赛道分析 prompt
    └── generate-outline.md     # 大纲生成 prompt
```

**调试方向：**

调试的核心是 `generate-outline.md` prompt。需要收集 50+ 份不同赛道的优秀路演 PPT，提取其结构模式，转化为 few-shot examples。LLM 在生成大纲时会参考这些示例的结构逻辑。关键调试点：

- 每个赛道应该有默认推荐结构（如医疗路演默认"痛点-解决方案-技术壁垒-临床数据-商业模式-团队-融资计划"）
- 大纲的每页要点数量控制在 3-5 条（太多页面放不下，太少信息密度不够）
- 图片需求标注要具体（不能只说"配图"，要说"CT影像对比图"或"市场规模趋势图"）

### Skill 2：ppt-template-matcher（需开发）

```
~/.skills/ppt-template-matcher/
├── SKILL.md
├── templates/                  # 模板库（同前文结构）
├── embeddings/                 # 模板描述的预计算向量
└── matcher.py                  # 匹配脚本
```

### Skill 3：ppt-image-layer（已有，需适配）

已有的图片分层 Skill，适配为 PPT 专用版本，增加以下能力：

- 识别 PPT 特有元素（slide number、page footer、slide title placeholder）
- 输出 python-pptx 兼容的元素描述格式
- 处理 16:9 宽高比的图片（标准 PPT 尺寸）

---

## 四、多 Agent 编排流程

基于 AGENT_ARCHITECTURE.md 的 Coordinator 模式：

```
Coordinator Agent（纯编排，4个工具：spawn/stop/send/output）
    │
    │  Phase 1: 文档分析
    ├──► spawn: doc-analyst-worker
    │         ├── 读取项目计划书
    │         ├── OCR 关键图片
    │         ├── 分析赛道
    │         └── 生成 outline.json
    │    ◄── 返回 outline.json
    │
    │  Phase 2: 模板匹配
    ├──► spawn: template-matcher-worker
    │         ├── 检索模板库
    │         ├── 评分排序
    │         └── 返回 selected_template
    │    ◄── 返回 template.pptx + meta.json
    │
    │  Phase 3: 内容映射
    ├──► spawn: content-mapper-worker
    │         ├── 解析模板页面（OCR + 布局分析）
    │         ├── 生成每页内容
    │         └── 输出 slide_contents.json
    │    ◄── 返回 slide_contents.json
    │    │
    │    ├── ⏸ 暂停，等待用户确认/修改
    │    └── 用户确认后继续
    │
    │  Phase 4: 图片生成
    ├──► spawn: image-gen-worker（可 fork 多个并行）
    │         ├── 登录 GPTImage2
    │         ├── 对每页执行图生图
    │         ├── 轮询结果
    │         └── 下载图片
    │    ◄── 返回 generated_slides/
    │
    │  Phase 5: PPT 组装
    ├──► spawn: ppt-assembler-worker
    │         ├── 图片分层分析
    │         ├── python-pptx 组装
    │         ├── 后处理
    │         └── 输出 final.pptx
    │    ◄── 返回 final.pptx
    │
    │  Phase 6: 验证（独立 Agent，fresh eyes）
    ├──► spawn: ppt-verifier-worker
    │         ├── 打开 final.pptx 检查
    │         ├── 验证每页内容完整性
    │         ├── 检查文字溢出/图片变形
    │         └── 返回验证报告
    │    ◄── 返回 verification_report
    │
    └──► SyntheticOutput: 输出 final.pptx + 验证报告
```

**并行优化点：**

Phase 1 中文本提取和赛道分析可以并行。Phase 4 中每页的图片生成可以并行（但需注意 GPTImage2 积分限制，建议限制并发为 2-3）。Phase 5 中分层分析可以按页并行。

---

## 五、GPTImage2 API 集成方案

### 认证流程

```
1. POST https://gptimage2.online/zh/sign-in
   Headers:
     Next-Action: 40d394005d47a94e63d9afce96aa3014325ca8b8cb
   Body: email=xxx&password=xxx&next=/zh
   
2. 从响应中提取 Set-Cookie 中的 session token
   → sb-vescivzvzczsjawbgjhs-auth-token=eyJ...

3. 后续所有 /api/ 请求携带该 cookie
```

### 图生图完整调用链

```python
# 1. 上传模板预览图作为参考
upload_resp = requests.post(
    "https://gptimage2.online/api/ai/upload-reference",
    json={"fileName": "template-slide-0.png", "contentType": "image/png", "fileSize": len(image_bytes)},
    cookies=session_cookies
)
signed_url = upload_resp.json()["signedUrl"]
public_url = upload_resp.json()["url"]

# 2. PUT 上传图片到 Supabase Storage
requests.put(signed_url, data=image_bytes, headers={"content-type": "image/png"})

# 3. 调用生图 API（图生图模式）
gen_resp = requests.post(
    "https://gptimage2.online/api/ai/text-to-image",
    json={
        "prompt": "Professional PowerPoint slide, blue medical theme, "
                  "title '智能影像诊断平台' at center, "
                  "subtitle 'AI赋能基层医疗影像筛查' below, "
                  "clean modern layout with neural network background, "
                  "white and blue color scheme",
        "aspect_ratio": "16:9",
        "resolution": "1K",
        "input_urls": [public_url]  # 模板预览图作为参考
    },
    cookies=session_cookies
)

# 4. 轮询结果
generation_id = gen_resp.json()["generationId"]
poll_after_ms = gen_resp.json()["pollAfterMs"]

time.sleep(poll_after_ms / 1000)
while True:
    status_resp = requests.get(
        f"https://gptimage2.online/api/generations/{generation_id}",
        cookies=session_cookies
    )
    generation = status_resp.json()["generation"]
    if generation["status"] == "succeeded":
        image_urls = generation["images"]
        break
    elif generation["status"] == "failed":
        raise Exception("Generation failed")
    time.sleep(min(2500 + 300 * attempt, 6000) / 1000)

# 5. 下载生成的图片
for url in image_urls:
    img_data = requests.get(url).content
    with open(f"generated_slides/slide-{i}.png", "wb") as f:
        f.write(img_data)
```

### 积分管理

每次生图消耗 10 积分（1K 分辨率）。一个 15 页 PPT 需要 15 次生成 = 150 积分。系统应在开始前检查积分余额：

```
GET /api/credits → remaining_credits >= 需要的生成次数 × 10
```

---

## 六、数据流全景

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          用户工作区 (Workspace)                           │
│                                                                         │
│  input/                  workspace/                  output/            │
│  ├── 项目计划书.pdf      ├── outline.json            ├── final.pptx     │
│  ├── 软著证书.jpg        ├── slide_contents.json     └── preview/       │
│  └── 功能截图.png        ├── template_meta.json          ├── slide-0.png│
│                          ├── generated_slides/            └── ...       │
│                          │   ├── slide-0.png                            │
│                          │   └── ...                                    │
│                          └── layer_analysis/                             │
│                              ├── slide-0.json                            │
│                              └── ...                                     │
└─────────────────────────────────────────────────────────────────────────┘
         ▲                                                      │
         │                 Coordinator Agent                    │
         │              ┌──────────────────┐                    │
         │              │  阶段调度 + 结果收集 │                    │
         │              └────────┬─────────┘                    │
         │                       │                              │
    ┌────┴──────┬────────────┬───┴──────┬────────────┬──────────┘
    ▼           ▼            ▼          ▼            ▼
┌────────┐ ┌────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐
│Phase 1 │ │Phase 2 │ │Phase 3  │ │Phase 4   │ │Phase 5   │
│文档分析│ │模板匹配│ │内容映射  │ │图片生成   │ │PPT组装   │
│        │ │        │ │         │ │          │ │          │
│PDF读取 │ │RAG检索 │ │OCR识别  │ │GPTImage2 │ │图片分层  │
│图片OCR │ │模板评分│ │内容生成  │ │API调用   │ │python-pptx│
│赛道分析│ │        │ │用户确认  │ │轮询下载  │ │文件组装  │
└────────┘ └────────┘ └─────────┘ └──────────┘ └──────────┘
```

---

## 七、风险与降级策略

| 风险场景 | 降级方案 |
|---------|---------|
| GPTImage2 积分不足 | 降级为纯模板填充模式（python-pptx 直接写入文字+用户图片，不做 AI 生图） |
| GPTImage2 API 不可用 | 降级为模板填充模式，图片区域使用占位符或用户上传的图片 |
| 生图文字模糊不可读 | Phase 5 中用 slide_contents.json 的精确文字覆盖图片中的文字区域 |
| 模板库无匹配项 | 降级为通用模板（白底+系统默认配色），通过 AI 生图提升视觉效果 |
| OCR 识别不准 | 以 template_meta.json 的布局描述为准，OCR 仅作辅助验证 |
| 分层 Skill 解析失败 | 直接将整页图片作为全屏背景插入 PPT，文字以透明 textbox 叠加 |

---

## 八、技术栈选型

| 模块 | 技术 | 说明 |
|------|------|------|
| Agent 框架 | 基于 AGENT_ARCHITECTURE.md | Coordinator-Worker 编排模式 |
| 文档解析 | PyMuPDF + python-docx + pytesseract | PDF/Word 读取 + 图片 OCR |
| 模板检索 | Milvus + BM25 + RRF | 混合检索匹配模板 |
| 图片生成 | GPTImage2.online API | 图生图模式，16:9，1K 分辨率 |
| 图片分层 | 已有 Skill | 图像分层分析 |
| PPT 组装 | python-pptx | 程序化生成 .pptx 文件 |
| 数据格式 | JSON + Markdown | 中间数据全部 JSON，便于调试 |
| 用户交互 | Web UI（React） | 大纲编辑、内容确认、预览 |

---

## 九、关键路径与耗时估算

| 阶段 | 操作 | 预估耗时 | 可并行 |
|------|------|---------|--------|
| Phase 1 | 文档解析 + 大纲生成 | 15-30s | 文本提取 ∥ 赛道分析 |
| Phase 2 | 模板检索 + 评分 | 2-5s | 否（依赖 Phase 1） |
| Phase 3 | OCR + 内容映射 | 20-40s | 每页 OCR 可并行 |
| - | 用户确认 | 用户控制 | - |
| Phase 4 | 图片生成（15页） | 3-8min | 每页生图可并行（限 2-3 并发） |
| Phase 5 | 分层 + PPT 组装 | 30-60s | 每页分层可并行 |
| **总计** | | **5-12min**（不含用户确认） | |

---

## 十、后续迭代方向

**模板自训练体系（Step 2 的深化）：** 建立模板评分反馈机制。用户生成 PPT 后可以打分，高分模板在检索时权重提升，低分模板降权。同时收集用户上传的优秀 PPT，自动提取结构模式扩充模板库。

**多模型生图对比：** 除了 GPTImage2，接入其他生图模型（如 Midjourney API、DALL-E API）作为备选，用户可以选择生图引擎。不同模型的风格适配不同赛道。

**品牌定制能力：** 支持用户上传品牌 VI（logo、配色、字体），生图 prompt 自动注入品牌约束，确保生成的 PPT 符合企业视觉规范。

**动画与交互：** Phase 5 中增加动画设置能力，根据 slide type 自动推荐页面切换效果和元素动画（如产品页用"淡入"，数据页用"擦除"）。
