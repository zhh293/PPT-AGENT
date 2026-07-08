# GPTImage2 Skill 调试报告与修复指南

> 日期：2026-07-07
> 结论：Skill 核心流程能跑，登录方式和图片下载需要适配网站升级

---

## 一、调试过程总览

### 1.1 环境

| 项 | 值 |
|---|---|
| 目标网站 | `https://gptimage2.online` |
| 测试账号 | `pptagent_test3@outlook.com` |
| 积分 | 初始 30 分，每张 1K 图消耗 10 分 |
| Python | 3.14，零依赖（仅 urllib + http.cookiejar） |

### 1.2 调试步骤

```
步骤 1: 原样运行 Skill  → 登录 HTTP 500 ❌
步骤 2: 直连 Supabase   → 登录成功，拿到 token，但 API 不认 Bearer token
步骤 3: 手动构造 cookie → API 返回 401，cookie 格式不匹配
步骤 4: 抓取线上页面    → 发现 form 结构变了（multipart/form-data + $ACTION_ID_）
步骤 5: 新格式登录      → HTTP 200 + auth cookie ✅
步骤 6: 查询积分        → 30 分余额 ✅
步骤 7: 提交生图任务    → generationId 返回 ✅
步骤 8: 轮询结果        → 40 秒后 succeeded，返回 R2 图片 URL ✅
步骤 9: 下载图片        → R2 返回 403（缺 User-Agent）→ 加上后 1.8MB PNG ✅
```

---

## 二、完整工作流程（新方式）

### 2.1 登录（最关键的变化）

**旧方式（已失效）：**
```python
POST https://gptimage2.online/zh/sign-in
Content-Type: text/plain;charset=UTF-8
Next-Action: 40d394005d47a94e63d9afce96aa3014325ca8b8cb

email: user@example.com
password: yourpassword
next: /zh
```
→ 返回 HTTP 500，无 auth cookie

**新方式（必须使用）：**

```python
import json, uuid
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor
from http.cookiejar import CookieJar

BASE = "https://gptimage2.online"
AID_LOGIN = "40d394005d47a94e63d9afce96aa3014325ca8b8cb"  # 这个 ID 没变！
email = "your@email.com"
password = "yourpassword"

cj = CookieJar()
opener = build_opener(HTTPCookieProcessor(cj))

# 第1步：先 GET 页面拿 NEXT_LOCALE cookie（必须！）
opener.open(Request(f"{BASE}/zh/sign-in"))

# 第2步：构造 multipart/form-data 请求体
boundary = "----Boundary" + uuid.uuid4().hex[:12]

parts = []
for name, value in [
    ("email", email),
    ("password", password),
    ("next", "/zh"),
    ("$ACTION_ID_" + AID_LOGIN, ""),  # <-- 关键！空值字段标识 Server Action
]:
    parts.append(
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="{name}"\r\n'
        f'\r\n'
        f'{value}'
    )
body = ("\r\n".join(parts) + f"\r\n--{boundary}--\r\n").encode("utf-8")

# 第3步：发送（不传 Next-Action header！）
req = Request(f"{BASE}/zh/sign-in", data=body, method="POST")
req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
req.add_header("Origin", BASE)
req.add_header("Referer", f"{BASE}/zh/sign-in")
resp = opener.open(req)

# 第4步：检查 cookie
for cookie in cj:
    if "sb-" in cookie.name:
        print(f"AUTH COOKIE OK: {cookie.name}")
```
→ 返回 HTTP 200，auth cookie 自动存入 CookieJar

### 2.2 查询积分

```python
resp = opener.open(Request(f"{BASE}/api/credits"))
data = json.loads(resp.read().decode())
credits = data["credits"]["remaining_credits"]
# 返回示例: {"credits": {"remaining_credits": 30, "has_paid_access": false}}
```

积分消耗规则：
| 分辨率 | 消耗 |
|--------|------|
| 1K | 10 分/张 |
| 2K | 20 分/张 |
| 4K | 40 分/张 |

### 2.3 生成图片

```python
gen_data = json.dumps({
    "prompt": "A cute orange cat, digital art style",
    "aspect_ratio": "1:1",   # 16:9 / 1:1 / 9:16 / 4:3 / 3:4 / auto
    "resolution": "1K",       # 1K / 2K / 4K
    "input_urls": [],         # 空数组=文生图；填参考图URL=图生图
}).encode()

req = Request(f"{BASE}/api/ai/text-to-image", data=gen_data, method="POST")
req.add_header("Content-Type", "application/json")
req.add_header("Origin", BASE)
req.add_header("Referer", f"{BASE}/zh")
resp = opener.open(req)

result = json.loads(resp.read().decode())
# {"generationId": "xxx-xxx", "pollAfterMs": 2500, "status": "processing"}
```

### 2.4 轮询结果

```python
import time

gen_id = result["generationId"]
time.sleep(result.get("pollAfterMs", 2000) / 1000)

for attempt in range(1, 30):
    req = Request(f"{BASE}/api/generations/{gen_id}")
    req.add_header("Cache", "no-store")
    gen = json.loads(opener.open(req).read().decode())["generation"]

    if gen["status"] == "succeeded":
        img_url = gen["images"][0]
        break
    elif gen["status"] == "failed":
        raise Exception(f"Generation failed: {gen.get('error')}")

    # 递增等待：min(2500 + 300*attempt, 6000) ms
    wait_ms = min(2500 + 300 * attempt, 6000)
    time.sleep(wait_ms / 1000)
```

### 2.5 下载图片（需要 User-Agent！）

```python
req = Request(img_url)
req.add_header("User-Agent",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
resp = urlopen(req)
with open("output.png", "wb") as f:
    f.write(resp.read())
```

**注意**：图片托管在 Cloudflare R2（`pub-xxx.r2.dev`），不加 `User-Agent` 会返回 403。

---

## 三、PPT Agent 生图流程

### 3.1 调用链路

```
coordinator_agent.py
  └─ 调度 visual_generation 阶段
       └─ image_generator.py :: run(workspace)
            ├─ prepare_generation_config()   → 生成 batch config JSON
            ├─ build_skill_context()         → 给 WorkerAgent 的指令
            ├─ check_generated_images()      → 检查是否已有图（断点续传）
            └─ _invoke_gptimage2_skill()     → 调用 CLI 脚本
                 └─ subprocess: python gptimage2_client.py batch-generate
```

### 3.2 关键文件

| 文件 | 作用 |
|------|------|
| `src/ppt_agent/workers/image_generator.py` | 生图 worker，入口是 `run(workspace)` |
| `src/ppt_agent/skills/adapters/gptimage2.py` | 将 slide_contents 转为 batch config |
| `.catpaw/skills/gptimage2-generator/scripts/gptimage2_client.py` | **实际执行生图的 CLI 脚本** |
| `.catpaw/skills/gptimage2-generator/assets/accounts.json` | 账号池 |

### 3.3 数据流

```
slide_contents.json                     image_generation_config.json
  ├─ slides[]                             ├─ slides[]
  │   ├─ slide_index: 0          →       │   ├─ index: 0
  │   ├─ zones[] (title/content)  →       │   ├─ prompt: "Title: xxx | Content: yyy | ..."
  │   └─ template_image           →       │   ├─ reference_image: "./templates/slide-0.png"
  │                                       │   ├─ aspect_ratio: "16:9"
  │                                       │   ├─ resolution: "1K"
  │                                       │   └─ output_name: "slide_00.png"
  └─ ...                                  └─ ...

                    ↓ gptimage2_client.py batch-generate
                    
  background_images/                      image_generation_report.json
    ├─ slide_00.png                       ├─ status: "succeeded"
    ├─ slide_01.png                       ├─ generated: 15
    └─ ...                                └─ slides[] (每页状态)
```

---

## 四、Skill 修改建议

### 4.1 `gptimage2_client.py` — 必须修改

#### 修改点 1：`login()` 方法（第 138-157 行）

**现状**：使用 `text/plain` + `Next-Action` header
**改为**：`multipart/form-data` + `$ACTION_ID_<hash>` 表单字段

```python
def login(self, email, password):
    """登录 — 使用 multipart/form-data 格式（适配新版 Next.js）"""
    
    # Step 1: GET 页面获取 NEXT_LOCALE cookie
    self._get(f"{BASE_URL}/zh/sign-in")
    
    # Step 2: 构造 multipart/form-data 请求
    boundary = "----Boundary" + uuid.uuid4().hex[:12]
    parts = []
    for name, value in [
        ("email", email),
        ("password", password),
        ("next", "/zh"),
        (f"$ACTION_ID_{ACTION_LOGIN}", ""),
    ]:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            f"\r\n"
            f"{value}"
        )
    body = ("\r\n".join(parts) + f"\r\n--{boundary}--\r\n").encode("utf-8")
    
    # Step 3: POST
    status, body_text, headers = self._post(
        f"{BASE_URL}/zh/sign-in",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/zh/sign-in",
        },
        content_type=None,  # 不覆盖 Content-Type
    )
    
    # 检查是否拿到了 auth cookie
    has_auth = any("sb-" in c.name for c in self.cookie_jar)
    if has_auth:
        self.session_active = True
        print(f"[OK] 登录成功: {email}")
        return True
    else:
        print(f"[FAIL] 登录失败: 未获取到 auth cookie")
        return False
```

#### 修改点 2：`_download_image()` 方法（第 429-441 行）

**现状**：直接 GET，无 User-Agent
**改为**：加 User-Agent header，且去掉冗余的第一次 GET

```python
def _download_image(self, url, output_path):
    """下载图片到本地。R2 bucket 需要 User-Agent header。"""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    
    req = Request(url, method="GET")
    req.add_header("User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    
    try:
        resp = self.opener.open(req)
        with open(output_path, "wb") as f:
            f.write(resp.read())
        print(f"[OK] 图片已保存: {output_path}")
    except Exception as e:
        print(f"[ERROR] 下载图片失败: {e}")
```

#### 修改点 3：`_post()` 方法（第 82-102 行）

`_post()` 目前不支持接收 `bytes` 类型的 data。当 `isinstance(data, bytes)` 时需要直接使用，不要尝试 `json.dumps` 或 `.encode()`：

```python
def _post(self, url, data=None, headers=None, content_type="application/json"):
    if headers is None:
        headers = {}
    if data is not None:
        if isinstance(data, bytes):
            pass  # 已经是 bytes，直接使用
        elif isinstance(data, dict) and content_type == "application/json":
            data = json.dumps(data).encode("utf-8")
        elif isinstance(data, str):
            data = data.encode("utf-8")
    # ... 其余不变
```

#### 修改点 4：`cmd_generate()` 和 `cmd_batch_generate()` 的登录逻辑

这两个命令在 `session_active=False` 时会自动登录，但没有先 GET 页面拿 cookie。改为：

```python
def cmd_generate(client, args):
    if not client.session_active:
        acc = client._get_current_account()
        if acc:
            # login() 内部已处理 GET cookie 的逻辑
            client.login(acc["email"], acc["password"])
        else:
            ...
```

如果 `login()` 方法按上面的方式改了（内部先 GET），这里就不需要额外修改。

### 4.2 `SKILL.md` — 建议更新

登录 API 那一段（第 42-55 行）需要更新为新格式：

```markdown
### 登录 API

POST https://gptimage2.online/zh/sign-in
Content-Type: multipart/form-data

表单字段：
  email: user@example.com
  password: yourpassword
  next: /zh
  $ACTION_ID_40d394005d47a94e63d9afce96aa3014325ca8b8cb: (空值)

注意：需先 GET /zh/sign-in 获取 NEXT_LOCALE cookie
```

### 4.3 `api-reference.md` — 建议更新

第 46-55 行的登录示例需要同步更新。

### 4.4 `accounts.json` — 安全建议 🔴

```diff
+ .catpaw/skills/gptimage2-generator/assets/accounts.json
```
加入 `.gitignore`！当前文件包含真实邮箱密码且已被 git 追踪：
- `pptagent_test3@outlook.com / AgentTest333!`
- `pptagent_v2@outlook.com / AgentV2Test!`

建议：
1. `git rm --cached` 取消追踪
2. 创建 `accounts.example.json` 作为模板
3. 把 `accounts.json` 加入 `.gitignore`

---

## 五、备用信息

### 5.1 Supabase 直接登录（Server Action 失效时的回退方案）

```python
SUPABASE_URL = "https://vescivzvzczsjawbgjhs.supabase.co"
ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."  # 完整 key 见 api-reference.md

data = json.dumps({"email": email, "password": password}).encode()
req = Request(f"{SUPABASE_URL}/auth/v1/token?grant_type=password", data=data, method="POST")
req.add_header("apikey", ANON_KEY)
req.add_header("Content-Type", "application/json")
resp = urlopen(req)
result = json.loads(resp.read().decode())
# result["access_token"] 是有效的 JWT
```

但 Supabase token 不能直接用于 gptimage2.online API——API 只认 Server Action 登录后 Set-Cookie 返回的 session cookie。

### 5.2 Server Action ID（当前有效值）

| 操作 | Action ID | 来源 |
|------|-----------|------|
| 登录 | `40d394005d47a94e63d9afce96aa3014325ca8b8cb` | 页面 form button name |
| 注册 | `600106e3c3b2e4e139cf6f45b97fd16194794326c3` | api-reference.md（未实测） |
| 退出 | `0045955e4ea6c2f153c871dffeb230d51d94ac738c` | api-reference.md（未实测） |

注册和退出的 action ID 可能也需要类似的多格式适配，建议一并修改。

### 5.3 换号流程（402 处理）

当 API 返回 402 时表示积分不足，需：
1. 标记当前账号 `status: "exhausted"`
2. 退出登录（POST `/zh` + signout action）
3. 切换到下一个 `active` 账号
4. 如果没有可用账号，注册新号（Supabase REST API 直接注册已验证可行）

### 5.4 注意：网站是 Vercel 托管的 Next.js 16

- 使用 Turbopack 构建
- Server Actions 格式与 Next.js 15 不同
- 每次网站重新部署，页面上嵌入的 JS chunk 文件名会变（但 action ID 不一定变）
