# GPTImage2.online API 接口文档

> 目标站点：`https://gptimage2.online/zh`
> 分析时间：2025-07-02
> 技术栈：Next.js 16.1.2 (App Router) + Supabase Auth + Server Actions

---

## 技术架构概述

该网站基于 Next.js 16.1.2 App Router 构建，采用以下技术方案：

- **认证层**：Supabase（项目 URL：`https://vescivzvzczsjawbgjhs.supabase.co`，anon key 公开嵌入前端 JS）
- **认证操作**（登录/注册/退出）：使用 Next.js Server Actions 机制，客户端通过 POST 请求到同源页面 URL，在 `Next-Action` header 中携带 42 字符的 action ID 哈希进行路由
- **业务操作**（生图等）：使用传统 Next.js API Routes（`/api/...`），标准 JSON 请求/响应
- **认证状态传递**：Supabase session token 通过 HTTP-Only Cookie 传递

**Supabase 公共配置（前端可见）：**

```
Project URL: https://vescivzvzczsjawbgjhs.supabase.co
Anon Key: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZlc2Npdnp2emN6c2phd2JnamhzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY4MjIzNTksImV4cCI6MjA5MjM5ODM1OX0.MmRf3PgjnyO5_-9qZeR-AxChbTMIwQ4JDm_qaR3ZA-4
```

---

## 1. 登录 API

### 前端入口

```
POST https://gptimage2.online/zh/sign-in
```

### 请求方式

Next.js Server Action（HTML 表单提交，enctype: text/plain）

### 请求 Headers

| Header | 值 |
|--------|-----|
| Content-Type | text/plain;charset=UTF-8 |
| Accept | text/x-component |
| Next-Action | `40d394005d47a94e63d9afce96aa3014325ca8b8cb` |

### 请求 Body

```
email: user@example.com
password: yourpassword
next: /zh
```

| 字段 | 类型 | 说明 |
|------|------|------|
| email | string | 用户邮箱 |
| password | string | 用户密码 |
| next | string | 登录成功后重定向路径，默认 `/zh` |

### 底层调用（Server Action 内部）

Server Action 接收到表单数据后，在服务端调用 Supabase GoTrue 认证 API：

```
POST https://vescivzvzczsjawbgjhs.supabase.co/auth/v1/token?grant_type=password
Headers:
  apikey: <Supabase Anon Key>
  Content-Type: application/json
Body:
{
  "email": "user@example.com",
  "password": "yourpassword"
}
```

### 响应

**前端层响应：** HTTP 303 重定向

- 成功：重定向到 `next` 参数指定的路径（默认 `/zh`）
- 失败：重定向到 `/zh/sign-in?error=...` 并附带错误信息

**Supabase 底层响应（JSON）：**

```json
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "expires_in": 3600,
  "refresh_token": "v1.xxx...",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "created_at": "2025-01-01T00:00:00Z"
  }
}
```

---

## 2. 注册 API

### 前端入口

```
POST https://gptimage2.online/zh/sign-up
```

### 请求方式

Next.js Server Action（HTML 表单提交）

### 请求 Headers

| Header | 值 |
|--------|-----|
| Content-Type | text/plain;charset=UTF-8 |
| Accept | text/x-component |
| Next-Action | `600106e3c3b2e4e139cf6f45b97fd16194794326c3` |

### 请求 Body

```
email: user@example.com
password: yourpassword
next: /zh
signup_device_id: <UUID>
$ACTION_2:0: {"id":"600106e3c3b2e4e139cf6f45b97fd16194794326c3","bound":"$@1"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| email | string | 注册邮箱 |
| password | string | 注册密码 |
| next | string | 注册成功后重定向路径 |
| signup_device_id | string (UUID) | 前端生成的设备标识符 |
| $ACTION_2:0 | string (JSON) | Next.js Server Action 元数据 |

### 底层调用（Server Action 内部）

Server Action 使用 PKCE 流程调用 Supabase 注册 API：

```
POST https://vescivzvzczsjawbgjhs.supabase.co/auth/v1/signup
Headers:
  apikey: <Supabase Anon Key>
  Content-Type: application/json
Body:
{
  "email": "user@example.com",
  "password": "yourpassword",
  "data": {},
  "code_challenge": "<PKCE code challenge>",
  "code_challenge_method": "S256"
}
```

### 响应

**前端层响应：** HTTP 303 重定向

- 成功：重定向到 `next` 路径或显示"验证邮箱"提示
- 失败：重定向到 `/zh/sign-up?error=...`

**Supabase 底层响应（JSON）：**

```json
{
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "created_at": "2025-01-01T00:00:00Z"
  },
  "session": null
}
```

> 注意：如果开启了邮箱验证，`session` 为 null，用户需点击邮箱验证链接后才完成注册。

---

## 3. 生图 API

### 3.1 提交生图任务

### 接口地址

```
POST https://gptimage2.online/api/ai/text-to-image
```

### 请求方式

标准 REST API（fetch 调用），需要用户已登录

### 请求 Headers

| Header | 值 |
|--------|-----|
| Content-Type | application/json |
| Cookie | sb-<project-ref>-auth-token=<JWT session token> |

### 请求 Body

```json
{
  "prompt": "A premium citrus soda advertisement, dewy aluminum can on a bright tabletop",
  "aspect_ratio": "1:1",
  "resolution": "1K",
  "input_urls": []
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| prompt | string | 是 | 生图提示词 |
| aspect_ratio | string | 是 | 宽高比，可选值见下表 |
| resolution | string | 是 | 分辨率，可选值见下表 |
| input_urls | string[] | 否 | 图生图模式时的参考图片 URL 数组，文生图传空数组 |

**aspect_ratio 可选值：**

| 值 | 说明 |
|----|------|
| auto | 自动宽高比（仅支持 1K） |
| 1:1 | 正方形 |
| 9:16 | 竖屏 |
| 16:9 | 横屏 |
| 4:3 | 横向 |
| 3:4 | 纵向 |

**resolution 可选值：**

| 值 | 积分消耗 | 限制 |
|----|---------|------|
| 1K | 10 积分/张 | 无限制 |
| 2K | 20 积分/张 | 需固定宽高比（不支持 auto） |
| 4K | 40 积分/张 | 需固定宽高比，不支持 1:1 |

### 响应

**成功响应（异步模式）：**

```json
{
  "generationId": "gen_xxxxx",
  "pollAfterMs": 2000
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| generationId | string | 生成任务 ID，用于后续轮询 |
| pollAfterMs | number | 建议首次轮询等待时间（毫秒） |

**成功响应（同步模式，直接返回图片）：**

```json
{
  "images": ["https://...image-url-1.png", "https://...image-url-2.png"],
  "generationId": "gen_xxxxx"
}
```

**错误响应：**

| HTTP 状态码 | 含义 | 前端处理 |
|------------|------|---------|
| 401 | 未登录 | 跳转登录页 |
| 402 | 积分不足 | 弹出充值弹窗 |
| 500 | 服务端错误 | 显示错误信息 |

### 3.2 轮询生成状态

当生图接口返回 `generationId` 时，前端通过轮询获取最终结果。

### 接口地址

```
GET https://gptimage2.online/api/generations/{generationId}
```

### 请求 Headers

| Header | 值 |
|--------|-----|
| Cache | no-store |
| Cookie | sb-<project-ref>-auth-token=<JWT session token> |

### 轮询策略

- 首次等待：`pollAfterMs` 毫秒（默认 2000ms）
- 后续间隔：递增，公式 `min(2500 + 300 * i, 6000)` 毫秒
- 超时时间：72 秒（超过后提示用户任务可能仍在后台处理）

### 响应

```json
{
  "generation": {
    "status": "succeeded",
    "images": ["https://...image-url.png"],
    "created_at": "2025-01-01T00:00:00Z"
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| generation.status | string | 生成状态：`pending` / `succeeded` / `failed` |
| generation.images | string[] | 生成的图片 URL 列表（status 为 succeeded 时存在） |

### 3.3 上传参考图（图生图辅助接口）

图生图模式下，需要先上传参考图片。

### 接口地址

```
POST https://gptimage2.online/api/ai/upload-reference
```

### 请求 Body

```json
{
  "fileName": "photo.png",
  "contentType": "image/png",
  "fileSize": 1234567
}
```

### 响应

```json
{
  "path": "uploads/xxx/photo.png",
  "signedUrl": "https://vescivzvzczsjawbgjhs.supabase.co/storage/v1/object/...",
  "url": "https://vescivzvzczsjawbgjhs.supabase.co/storage/v1/object/public/...",
  "headers": {
    "content-type": "image/png",
    "cache-control": "31536000",
    "x-upsert": "false"
  }
}
```

获取签名 URL 后，客户端直接 PUT 文件到 `signedUrl`，上传成功后将返回的 `url` 作为 `input_urls` 传入生图接口。

---

## 4. 退出登录 API

### 前端入口

```
POST https://gptimage2.online/zh
```

> 退出登录通过页面上 `<form action={signOutAction}>` 的提交按钮触发，可从任意已登录页面发起。

### 请求方式

Next.js Server Action（HTML 表单提交）

### 请求 Headers

| Header | 值 |
|--------|-----|
| Content-Type | text/plain;charset=UTF-8 |
| Accept | text/x-component |
| Next-Action | `0045955e4ea6c2f153c871dffeb230d51d94ac738c` |

### 请求 Body

空（无表单字段，仅一个 type="submit" 的按钮）

### 底层调用（Server Action 内部）

Server Action 调用 Supabase 登出 API：

```
POST https://vescivzvzczsjawbgjhs.supabase.co/auth/v1/logout?scope=global
Headers:
  apikey: <Supabase Anon Key>
  Authorization: Bearer <access_token>
```

> `scope` 参数默认为 `global`，表示清除该用户在所有设备上的 session。也可设为 `others`（清除其他设备）或 `local`（仅清除当前设备）。

### 响应

**前端层响应：** HTTP 303 重定向到首页或登录页

**Supabase 底层响应：** HTTP 204 No Content

退出后浏览器中的 Supabase auth cookie 被清除。

---

## 5. 补充 API

分析过程中还发现了以下辅助 API 路由：

### 5.1 查询积分

```
GET https://gptimage2.online/api/credits
```

响应：

```json
{
  "credits": {
    "remaining_credits": 500,
    "has_paid_access": false
  }
}
```

### 5.2 消耗积分

```
POST https://gptimage2.online/api/credits
Content-Type: application/json

{
  "amount": 10,
  "operation": "name_generation"
}
```

响应：

```json
{
  "credits": {
    "remaining_credits": 490
  }
}
```

---

## 附录：Server Action ID 汇总

| 操作 | Action 名称 | Action ID | 页面路径 |
|------|------------|-----------|---------|
| 登录 | - | `40d394005d47a94e63d9afce96aa3014325ca8b8cb` | `/zh/sign-in` |
| 注册 | - | `600106e3c3b2e4e139cf6f45b97fd16194794326c3` | `/zh/sign-up` |
| 退出登录 | signOutAction | `0045955e4ea6c2f153c871dffeb230d51d94ac738c` | 任意已登录页面 |

## 附录：Supabase GoTrue 认证端点汇总

| 操作 | HTTP 方法 | 端点 |
|------|----------|------|
| 密码登录 | POST | `/auth/v1/token?grant_type=password` |
| 注册 | POST | `/auth/v1/signup` |
| 退出登录 | POST | `/auth/v1/logout?scope=global` |
| 刷新 Token | POST | `/auth/v1/token?grant_type=refresh_token` |
| 获取用户信息 | GET | `/auth/v1/user` |

所有 Supabase API 调用需携带以下 Header：

```
apikey: <Supabase Anon Key>
Content-Type: application/json
Authorization: Bearer <access_token>  （除登录和注册外）
```
