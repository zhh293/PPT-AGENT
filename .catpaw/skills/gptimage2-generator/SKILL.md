---
name: gptimage2-generator
description: "通过 GPTImage2.online API 生成图片，支持文生图和图生图。涵盖完整流程：登录认证、积分查询、图片生成、结果轮询、退出登录、额度耗尽时自动注册新账号换号。当用户提到 GPTImage2、gptimage2.online、AI生图、文生图、图生图、生成图片、AI绘图、gpt image、换号生图、积分用完、生图额度时使用此 Skill。即使用户只说'帮我生成几张图'或'用那个AI生图'也应触发。"
---

# GPTImage2 Generator

通过 GPTImage2.online 的 API 进行 AI 图片生成，支持文生图和图生图两种模式。Skill 封装了完整的账号生命周期管理：登录 → 查积分 → 生图 → 轮询下载 → （额度不足时）退出 → 注册新号 → 重新登录。

## 核心概念

GPTImage2.online 是一个基于 GPT Image 2 模型的在线 AI 生图服务。它的前端是 Next.js 应用，认证使用 Supabase，生图是标准 REST API。关键在于：

- **认证操作**（登录/注册/退出）走 Next.js Server Actions，不是标准 REST API，需要特殊的请求格式
- **生图操作**走标准 REST API（`/api/ai/text-to-image`），只需携带登录后的 cookie
- **积分制**：每次生图消耗积分（1K=10分/张，2K=20分/张，4K=40分/张），积分不足时 API 返回 402

## 前置准备

在开始之前，确保你有可用的账号凭据。账号信息存储在 `assets/accounts.json` 中。如果该文件不存在，Skill 运行时会引导你创建。

`accounts.json` 格式：

```json
{
  "accounts": [
    {"email": "user1@example.com", "password": "pass1", "status": "active"},
    {"email": "user2@example.com", "password": "pass2", "status": "exhausted"}
  ],
  "current_index": 0
}
```

## 工作流程

### 流程总览

```
开始
  │
  ├── 1. 读取 accounts.json，获取当前账号
  ├── 2. 登录（Server Action POST /zh/sign-in）
  ├── 3. 查询积分（GET /api/credits）
  │    ├── 积分充足 → 继续
  │    └── 积分不足 → 换号流程（见下文）
  ├── 4. 生图（POST /api/ai/text-to-image）
  │    ├── 文生图：input_urls=[]
  │    └── 图生图：先上传参考图，再传 input_urls
  ├── 5. 轮询结果（GET /api/generations/{id}）
  ├── 6. 下载图片
  ├── 7. 更新积分缓存
  └── 循环 4-7 直到所有图片生成完毕
       │
       └── 中途 402 → 换号流程
```

### 换号流程（额度耗尽时）

当生图 API 返回 402（积分不足）或积分查询显示余额不够时，执行以下流程：

```
1. 退出当前账号
   POST /zh (Next-Action: signOutAction ID)
   → 清除 session cookie

2. 标记当前账号为 exhausted
   更新 accounts.json 中该账号的 status

3. 选择下一个可用账号
   ├── 有下一个账号 → 切换 current_index
   └── 没有可用账号 → 注册新账号（见下文）

4. 用新账号登录
   POST /zh/sign-in (Server Action)

5. 继续生图任务
```

### 注册新账号

当所有现有账号的积分都耗尽时，需要注册新账号：

```
1. 生成新邮箱（可用临时邮箱服务或自有邮箱）
2. POST /zh/sign-up (Server Action)
   Headers:
     Next-Action: 600106e3c3b2e4e139cf6f45b97fd16194794326c3
   Body:
     email: newuser@example.com
     password: newpassword
     next: /zh
     signup_device_id: <随机UUID>
     $ACTION_2:0: {"id":"600106e3c3b2e4e139cf6f45b97fd16194794326c3","bound":"$@1"}
3. 等待注册完成（可能需要邮箱验证）
4. 登录新账号
5. 记录到 accounts.json
```

注意：注册后 Supabase 可能要求邮箱验证。如果返回的 session 为 null，说明需要先完成邮箱验证才能登录。注册新号前确认你有一个可接收验证邮件的邮箱。

## 使用辅助脚本

Skill 附带了一个 Python 脚本 `scripts/gptimage2_client.py`，封装了所有 API 调用。优先使用这个脚本而不是手动构造请求，因为它处理了 Server Action 的特殊请求格式、cookie 管理、轮询逻辑和错误重试。

### 基本用法

```bash
# 登录并查看积分
python scripts/gptimage2_client.py login --email user@example.com --password pass123

# 查询当前积分
python scripts/gptimage2_client.py credits

# 文生图
python scripts/gptimage2_client.py generate \
  --prompt "A blue medical AI concept image, brain scan with neural network overlay" \
  --aspect-ratio 16:9 \
  --resolution 1K \
  --output ./output.png

# 图生图（上传参考图 + 生成）
python scripts/gptimage2_client.py generate \
  --prompt "Professional PowerPoint slide, blue theme, title at top, content on left, image on right" \
  --aspect-ratio 16:9 \
  --resolution 1K \
  --reference ./template-slide.png \
  --output ./generated.png

# 退出登录
python scripts/gptimage2_client.py logout

# 换号（退出当前 + 登录下一个）
python scripts/gptimage2_client.py switch-account
```

### 批量生图（PPT 场景）

```bash
# 批量生成：读取 JSON 配置文件，逐页生成
python scripts/gptimage2_client.py batch-generate \
  --config slides_config.json \
  --output-dir ./generated_slides/
```

`slides_config.json` 格式：

```json
{
  "slides": [
    {
      "index": 0,
      "prompt": "Professional PowerPoint cover slide, blue medical theme...",
      "aspect_ratio": "16:9",
      "resolution": "1K",
      "reference_image": "./templates/slide-0.png"
    },
    {
      "index": 1,
      "prompt": "Content slide with bullet points...",
      "aspect_ratio": "16:9",
      "resolution": "1K",
      "reference_image": "./templates/slide-1.png"
    }
  ]
}
```

批量生成时脚本会自动处理换号逻辑：当遇到 402 时自动退出当前账号、切换到下一个账号登录、继续生成。

## API 细节参考

完整的 API 请求/响应格式、Server Action ID、Supabase 底层端点等信息，参见 `references/api-reference.md`。以下是最常用的摘要：

| 操作 | 方法 | URL | 关键 Header |
|------|------|-----|------------|
| 登录 | POST | /zh/sign-in | Next-Action: 40d394005d47a94e63d9afce96aa3014325ca8b8cb |
| 注册 | POST | /zh/sign-up | Next-Action: 600106e3c3b2e4e139cf6f45b97fd16194794326c3 |
| 退出 | POST | /zh (任意页面) | Next-Action: 0045955e4ea6c2f153c871dffeb230d51d94ac738c |
| 查积分 | GET | /api/credits | Cookie: session token |
| 生图 | POST | /api/ai/text-to-image | Content-Type: application/json |
| 轮询 | GET | /api/generations/{id} | Cache: no-store |
| 上传参考图 | POST | /api/ai/upload-reference | Content-Type: application/json |

## 参数说明

### aspect_ratio（宽高比）

| 值 | 说明 | PPT 场景 |
|----|------|---------|
| auto | 自动（仅支持 1K） | 不推荐 |
| 1:1 | 正方形 | 社交媒体图 |
| 9:16 | 竖屏 | 手机端展示 |
| 16:9 | 横屏 | **标准 PPT 页面** |
| 4:3 | 横向 | 传统 PPT |
| 3:4 | 纵向 | 海报 |

### resolution（分辨率）

| 值 | 积分消耗 | 适用场景 |
|----|---------|---------|
| 1K | 10 分/张 | 批量生成、草稿验证（推荐默认） |
| 2K | 20 分/张 | 最终版本（需固定宽高比） |
| 4K | 40 分/张 | 高精度需求（需固定宽高比，不支持 1:1） |

## 注意事项

**关于积分成本**：一个 15 页的 PPT 如果每页都生成一张图，1K 分辨率需要 150 积分。在开始批量生成前务必检查积分余额，避免中途断号。

**关于图生图效果**：图生图模式（传入 reference_image）会把参考图作为构图参考，AI 会在保持大致布局的前提下替换内容。这对 PPT 场景特别有用——可以把模板每页的截图作为参考图，让 AI 在保持布局的同时替换文字和图片内容。但注意 AI 对文字的还原精度有限，关键文字建议在后续 PPT 组装阶段用 python-pptx 精确写入。

**关于轮询超时**：生图是异步的，提交后需要轮询结果。默认超时 72 秒，超时后任务可能仍在后台处理。脚本会在超时后将该任务标记为 failed 并继续下一张，你可以稍后手动检查 `/api/generations/{id}` 获取最终结果。

**关于并发**：GPTImage2 没有明确的并发限制，但建议同时不超过 2-3 个并发生图请求，避免触发频率限制。批量脚本默认串行执行。

**关于 cookie 过期**：Supabase session token 有效期约 1 小时（expires_in: 3600）。如果长时间操作后遇到 401 错误，重新执行登录流程即可。
