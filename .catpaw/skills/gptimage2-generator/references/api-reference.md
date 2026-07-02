# GPTImage2.online API 完整参考

> 本文档是 SKILL.md 的详细参考，包含完整的 API 请求/响应格式。
> 日常使用时只需阅读 SKILL.md 即可，遇到 API 细节问题再查此文档。

---

## 目录

1. [技术架构](#1-技术架构)
2. [登录 API](#2-登录-api)
3. [注册 API](#3-注册-api)
4. [退出登录 API](#4-退出登录-api)
5. [查询积分 API](#5-查询积分-api)
6. [生图 API](#6-生图-api)
7. [轮询生成状态 API](#7-轮询生成状态-api)
8. [上传参考图 API](#8-上传参考图-api)
9. [消耗积分 API](#9-消耗积分-api)
10. [Server Action ID 汇总](#10-server-action-id-汇总)
11. [Supabase 底层端点汇总](#11-supabase-底层端点汇总)

---

## 1. 技术架构

该网站基于 Next.js 16.1.2 App Router 构建：

- **认证层**：Supabase（项目 URL：`https://vescivzvzczsjawbgjhs.supabase.co`，anon key 公开嵌入前端 JS）
- **认证操作**（登录/注册/退出）：Next.js Server Actions 机制，POST 到同源页面 URL，在 `Next-Action` header 中携带 42 字符的 action ID 哈希
- **业务操作**（生图等）：传统 Next.js API Routes（`/api/...`），标准 JSON
- **认证状态**：Supabase session token 通过 HTTP-Only Cookie 传递

Supabase 公共配置：

```
Project URL: https://vescivzvzczsjawbgjhs.supabase.co
Anon Key: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZlc2Npdnp2emN6c2phd2JnamhzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY4MjIzNTksImV4cCI6MjA5MjM5ODM1OX0.MmRf3PgjnyO5_-9qZeR-AxChbTMIwQ4JDm_qaR3ZA-4
```

---

## 2. 登录 API

### 请求

```
POST https://gptimage2.online/zh/sign-in
Content-Type: text/plain;charset=UTF-8
Accept: text/x-component
Next-Action: 40d394005d47a94e63d9afce96aa3014325ca8b8cb

email: user@example.com
password: yourpassword
next: /zh
```

### 底层调用

Server Action 内部调用 Supabase：

```
POST https://vescivzvzczsjawbgjhs.supabase.co/auth/v1/token?grant_type=password
Headers:
  apikey: <Anon Key>
  Content-Type: application/json
Body:
{"email": "user@example.com", "password": "yourpassword"}
```

### 响应

前端层：HTTP 303 重定向（成功 → `/zh`，失败 → `/zh/sign-in?error=...`）

Supabase 底层响应：

```json
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "expires_in": 3600,
  "refresh_token": "v1.xxx...",
  "user": {"id": "uuid", "email": "user@example.com"}
}
```

登录成功后，`Set-Cookie` 中会包含 `sb-vescivzvzczsjawbgjhs-auth-token=<JWT>`，后续 API 调用需携带此 cookie。

---

## 3. 注册 API

### 请求

```
POST https://gptimage2.online/zh/sign-up
Content-Type: text/plain;charset=UTF-8
Accept: text/x-component
Next-Action: 600106e3c3b2e4e139cf6f45b97fd16194794326c3

email: user@example.com
password: yourpassword
next: /zh
signup_device_id: <UUID>
$ACTION_2:0: {"id":"600106e3c3b2e4e139cf6f45b97fd16194794326c3","bound":"$@1"}
```

### 底层调用

```
POST https://vescivzvzczsjawbgjhs.supabase.co/auth/v1/signup
Headers:
  apikey: <Anon Key>
  Content-Type: application/json
Body:
{
  "email": "user@example.com",
  "password": "yourpassword",
  "data": {},
  "code_challenge": "<PKCE challenge>",
  "code_challenge_method": "S256"
}
```

### 响应

前端层：HTTP 303 重定向

Supabase 底层响应：

```json
{
  "user": {"id": "uuid", "email": "user@example.com"},
  "session": null
}
```

如果开启了邮箱验证，`session` 为 null，需点击邮箱验证链接后才完成注册。

---

## 4. 退出登录 API

### 请求

```
POST https://gptimage2.online/zh
Content-Type: text/plain;charset=UTF-8
Accept: text/x-component
Next-Action: 0045955e4ea6c2f153c871dffeb230d51d94ac738c

(空 body)
```

### 底层调用

```
POST https://vescivzvzczsjawbgjhs.supabase.co/auth/v1/logout?scope=global
Headers:
  apikey: <Anon Key>
  Authorization: Bearer <access_token>
```

`scope` 参数：`global`（清除所有设备 session）、`others`（清除其他设备）、`local`（仅当前设备）。

### 响应

前端层：HTTP 303 重定向到首页
Supabase 底层：HTTP 204 No Content

---

## 5. 查询积分 API

### 请求

```
GET https://gptimage2.online/api/credits
Cookie: sb-vescivzvzczsjawbgjhs-auth-token=<JWT>
```

### 响应

```json
{
  "credits": {
    "remaining_credits": 500,
    "has_paid_access": false
  }
}
```

---

## 6. 生图 API

### 请求

```
POST https://gptimage2.online/api/ai/text-to-image
Content-Type: application/json
Cookie: sb-vescivzvzczsjawbgjhs-auth-token=<JWT>

{
  "prompt": "用户输入的提示词",
  "aspect_ratio": "16:9",
  "resolution": "1K",
  "input_urls": []
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| prompt | string | 是 | 生图提示词 |
| aspect_ratio | string | 是 | 宽高比：auto/1:1/9:16/16:9/4:3/3:4 |
| resolution | string | 是 | 分辨率：1K/2K/4K |
| input_urls | string[] | 否 | 图生图模式的参考图 URL 数组，文生图传空数组 |

**aspect_ratio 限制：** auto 仅支持 1K；2K/4K 需固定宽高比；4K 不支持 1:1。

### 响应（异步模式）

```json
{
  "generationId": "gen_xxxxx",
  "pollAfterMs": 2000
}
```

### 响应（同步模式）

```json
{
  "images": ["https://...image-url.png"],
  "generationId": "gen_xxxxx"
}
```

### 错误响应

| HTTP 状态码 | 含义 |
|------------|------|
| 401 | 未登录 |
| 402 | 积分不足 |
| 500 | 服务端错误 |

---

## 7. 轮询生成状态 API

### 请求

```
GET https://gptimage2.online/api/generations/{generationId}
Cache: no-store
Cookie: sb-vescivzvzczsjawbgjhs-auth-token=<JWT>
```

### 轮询策略

- 首次等待：`pollAfterMs` 毫秒（默认 2000ms）
- 后续间隔：`min(2500 + 300 * i, 6000)` 毫秒（递增，上限 6 秒）
- 超时时间：72 秒

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

status 可选值：`pending` / `succeeded` / `failed`

---

## 8. 上传参考图 API

### 请求

```
POST https://gptimage2.online/api/ai/upload-reference
Content-Type: application/json
Cookie: sb-vescivzvzczsjawbgjhs-auth-token=<JWT>

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

获取签名 URL 后，客户端直接 `PUT` 文件到 `signedUrl`，上传成功后将返回的 `url` 作为 `input_urls` 传入生图接口。

---

## 9. 消耗积分 API

### 请求

```
POST https://gptimage2.online/api/credits
Content-Type: application/json
Cookie: sb-vescivzvzczsjawbgjhs-auth-token=<JWT>

{
  "amount": 10,
  "operation": "name_generation"
}
```

### 响应

```json
{
  "credits": {
    "remaining_credits": 490
  }
}
```

---

## 10. Server Action ID 汇总

| 操作 | Action 名称 | Action ID | 页面路径 |
|------|------------|-----------|---------|
| 登录 | - | `40d394005d47a94e63d9afce96aa3014325ca8b8cb` | `/zh/sign-in` |
| 注册 | - | `600106e3c3b2e4e139cf6f45b97fd16194794326c3` | `/zh/sign-up` |
| 退出登录 | signOutAction | `0045955e4ea6c2f153c871dffeb230d51d94ac738c` | 任意已登录页面 |

---

## 11. Supabase 底层端点汇总

| 操作 | HTTP 方法 | 端点 |
|------|----------|------|
| 密码登录 | POST | `/auth/v1/token?grant_type=password` |
| 注册 | POST | `/auth/v1/signup` |
| 退出登录 | POST | `/auth/v1/logout?scope=global` |
| 刷新 Token | POST | `/auth/v1/token?grant_type=refresh_token` |
| 获取用户信息 | GET | `/auth/v1/user` |

所有 Supabase API 调用需携带：

```
apikey: <Supabase Anon Key>
Content-Type: application/json
Authorization: Bearer <access_token>  （除登录和注册外）
```
