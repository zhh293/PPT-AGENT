#!/usr/bin/env python3
"""
GPTImage2.online API Client
封装了登录、注册、退出、积分查询、文生图、图生图、轮询下载等完整流程。
支持账号轮换：当积分耗尽时自动切换到下一个账号。
"""

import argparse
import json
import os
import sys
import time
import uuid
import urllib.parse
from pathlib import Path
from http.cookiejar import CookieJar
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor

BASE_URL = "https://gptimage2.online"

# Next.js Server Action IDs (从前端 JS 逆向获取)
ACTION_LOGIN = "40d394005d47a94e63d9afce96aa3014325ca8b8cb"
ACTION_SIGNUP = "40824ae53380230b8cb0db15ab8a7eae2d4d96872f"
ACTION_SIGNOUT = "0045955e4ea6c2f153c871dffeb230d51d94ac738c"

# 轮询参数
POLL_INITIAL_DELAY_MS = 2000
POLL_MAX_INTERVAL_MS = 6000
POLL_TIMEOUT_MS = 180000  # 3分钟超时


class GPTImage2Client:
    """GPTImage2.online API 客户端，封装完整认证和生图流程。"""

    def __init__(self, accounts_file=None):
        self.cookie_jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))
        self.session_active = False
        self.cached_credits = None

        # 定位 accounts.json
        if accounts_file is None:
            skill_dir = Path(__file__).parent.parent
            accounts_file = skill_dir / "assets" / "accounts.json"
        self.accounts_file = str(accounts_file)
        self.accounts = self._load_accounts()

    # ─── 账号管理 ───

    def _load_accounts(self):
        """从 accounts.json 加载账号列表。"""
        if not os.path.exists(self.accounts_file):
            return {"accounts": [], "current_index": 0}
        with open(self.accounts_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_accounts(self):
        """保存账号列表到 accounts.json。"""
        os.makedirs(os.path.dirname(self.accounts_file), exist_ok=True)
        with open(self.accounts_file, "w", encoding="utf-8") as f:
            json.dump(self.accounts, f, indent=2, ensure_ascii=False)

    def _get_current_account(self):
        """获取当前活跃账号。"""
        idx = self.accounts.get("current_index", 0)
        accounts = self.accounts.get("accounts", [])
        if idx >= len(accounts):
            return None
        return accounts[idx]

    def _find_next_active_account(self):
        """查找下一个未耗尽积分的账号。"""
        accounts = self.accounts.get("accounts", [])
        for i, acc in enumerate(accounts):
            if acc.get("status") == "active":
                return i, acc
        return None, None

    # ─── HTTP 工具 ───

    def _post(self, url, data=None, headers=None, content_type="application/json"):
        """发送 POST 请求。"""
        if headers is None:
            headers = {}
        if data is not None:
            if isinstance(data, bytes):
                pass  # already bytes (multipart etc.)
            elif isinstance(data, dict) and content_type == "application/json":
                data = json.dumps(data).encode("utf-8")
            elif isinstance(data, str):
                data = data.encode("utf-8")
        req = Request(url, data=data, method="POST")
        for k, v in headers.items():
            req.add_header(k, v)
        if "Content-Type" not in headers and content_type:
            req.add_header("Content-Type", content_type)
        try:
            resp = self.opener.open(req)
            return resp.status, resp.read().decode("utf-8", errors="replace"), dict(resp.headers)
        except Exception as e:
            if hasattr(e, "code"):
                return e.code, e.read().decode("utf-8", errors="replace") if e.fp else str(e), {}
            raise

    def _get(self, url, headers=None):
        """发送 GET 请求。"""
        req = Request(url, method="GET")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        try:
            resp = self.opener.open(req)
            return resp.status, resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            if hasattr(e, "code"):
                return e.code, e.read().decode("utf-8", errors="replace") if e.fp else str(e)
            raise

    def _put(self, url, data, headers=None):
        """发送 PUT 请求（用于上传文件到 Supabase Storage）。"""
        if isinstance(data, str):
            data = data.encode("utf-8")
        req = Request(url, data=data, method="PUT")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        try:
            resp = self.opener.open(req)
            return resp.status, resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            if hasattr(e, "code"):
                return e.code, str(e)
            raise

    # ─── 认证操作 ───

    def login(self, email, password):
        """登录 — multipart/form-data + Origin/Referer（适配新版 Next.js）。"""
        # Step 1: GET 页面获取 NEXT_LOCALE cookie
        self._get(f"{BASE_URL}/zh/sign-in")

        # Step 2: 构造 multipart/form-data
        boundary = "----Boundary" + uuid.uuid4().hex[:12]
        parts = []
        for name, value in [
            ("email", email), ("password", password), ("next", "/zh"),
            (f"$ACTION_ID_{ACTION_LOGIN}", ""),
        ]:
            parts.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n'
                f"\r\n"
                f"{value}"
            )
        body = ("\r\n".join(parts) + f"\r\n--{boundary}--\r\n").encode("utf-8")

        # Step 3: POST（不传 Next-Action header！）
        status, _, _ = self._post(
            f"{BASE_URL}/zh/sign-in",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/zh/sign-in",
            },
            content_type=None,
        )

        # 检查 auth cookie
        has_auth = any("sb-" in c.name for c in self.cookie_jar)
        if has_auth:
            self.session_active = True
            print(f"[OK] 登录成功: {email}")
            return True
        else:
            print(f"[FAIL] 登录失败 (HTTP {status}): 未获取到 auth cookie")
            return False

    def signup(self, email, password):
        """注册：Supabase REST API（可靠），然后 Server Action 登录拿 cookie。"""
        SUPABASE_URL = "https://vescivzvzczsjawbgjhs.supabase.co"
        SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZlc2Npdnp2emN6c2phd2JnamhzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY4MjIzNTksImV4cCI6MjA5MjM5ODM1OX0.MmRf3PgjnyO5_-9qZeR-AxChbTMIwQ4JDm_qaR3ZA-4"

        # Step 1: register via Supabase REST API
        status, _, _ = self._post(
            f"{SUPABASE_URL}/auth/v1/signup",
            data={"email": email, "password": password},
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        )
        if status != 200:
            print(f"[FAIL] 注册失败 (HTTP {status})")
            return False

        print(f"[OK] Supabase 注册成功: {email}")
        self.accounts.setdefault("accounts", []).append({
            "email": email, "password": password, "status": "active"
        })
        self._save_accounts()

        # Step 2: login via Server Action to get gptimage2.online auth cookie
        return self.login(email, password)

    def logout(self):
        """退出登录（Server Action，multipart）。"""
        self._get(f"{BASE_URL}/zh")
        boundary = "----Boundary" + uuid.uuid4().hex[:12]
        parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"$ACTION_ID_{ACTION_SIGNOUT}\"\r\n\r\n"]
        body = ("\r\n".join(parts) + f"\r\n--{boundary}--\r\n").encode("utf-8")

        self._post(
            f"{BASE_URL}/zh",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Origin": BASE_URL, "Referer": f"{BASE_URL}/zh",
            },
            content_type=None,
        )
        self.session_active = False
        self.cached_credits = None
        self.cookie_jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))
        print("[OK] 已退出登录")
        return True

    def switch_account(self):
        """切换到下一个可用账号。"""
        # 标记当前账号为已耗尽
        current = self._get_current_account()
        if current:
            current["status"] = "exhausted"
            self._save_accounts()
            print(f"[INFO] 账号 {current['email']} 积分已耗尽，标记为 exhausted")

        # 退出当前登录
        self.logout()

        # 查找下一个可用账号
        idx, acc = self._find_next_active_account()
        if acc:
            self.accounts["current_index"] = idx
            self._save_accounts()
            print(f"[INFO] 切换到账号: {acc['email']}")
            return self.login(acc["email"], acc["password"])
        else:
            print("[WARN] 没有更多可用账号，需要注册新账号")
            return False

    # ─── 积分管理 ───

    def get_credits(self):
        """查询当前积分余额。"""
        if not self.session_active:
            print("[ERROR] 未登录，请先执行 login")
            return None

        status, body = self._get(f"{BASE_URL}/api/credits")
        if status == 200:
            data = json.loads(body)
            credits = data.get("credits", {})
            self.cached_credits = credits
            remaining = credits.get("remaining_credits", 0)
            print(f"[OK] 当前积分余额: {remaining}")
            return credits
        elif status == 401:
            print("[ERROR] 登录已过期，请重新登录")
            self.session_active = False
            return None
        else:
            print(f"[ERROR] 查询积分失败 (HTTP {status}): {body[:200]}")
            return None

    def has_enough_credits(self, needed):
        """检查是否有足够积分。"""
        credits = self.get_credits()
        if credits is None:
            return False
        remaining = credits.get("remaining_credits", 0)
        if remaining < needed:
            print(f"[WARN] 积分不足: 需要 {needed}，剩余 {remaining}")
            return False
        return True

    # ─── 图片生成 ───

    def upload_reference_image(self, image_path):
        """上传参考图（用于图生图模式）。"""
        if not os.path.exists(image_path):
            print(f"[ERROR] 图片文件不存在: {image_path}")
            return None

        file_size = os.path.getsize(image_path)
        file_name = os.path.basename(image_path)
        content_type = "image/png" if file_name.endswith(".png") else "image/jpeg"

        # Step 1: 获取签名 URL
        status, body, _ = self._post(
            f"{BASE_URL}/api/ai/upload-reference",
            data={"fileName": file_name, "contentType": content_type, "fileSize": file_size},
        )
        if status != 200:
            print(f"[ERROR] 获取上传URL失败 (HTTP {status}): {body[:200]}")
            return None

        resp = json.loads(body)
        signed_url = resp.get("signedUrl")
        public_url = resp.get("url")
        upload_headers = resp.get("headers", {})

        if not signed_url or not public_url:
            print("[ERROR] 返回数据缺少 signedUrl 或 url")
            return None

        # Step 2: PUT 上传文件到 Supabase Storage
        with open(image_path, "rb") as f:
            file_data = f.read()

        put_headers = upload_headers or {
            "content-type": content_type,
            "cache-control": "31536000",
            "x-upsert": "false",
        }
        put_status, put_body = self._put(signed_url, file_data, put_headers)
        if put_status not in (200, 201):
            print(f"[ERROR] 上传文件失败 (HTTP {put_status})")
            return None

        print(f"[OK] 参考图上传成功: {public_url[:80]}...")
        return public_url

    def generate_image(self, prompt, aspect_ratio="16:9", resolution="1K", reference_image=None, output_path=None):
        """
        生成图片（文生图或图生图）。
        
        参数:
            prompt: 生图提示词
            aspect_ratio: 宽高比 (auto/1:1/9:16/16:9/4:3/3:4)
            resolution: 分辨率 (1K/2K/4K)
            reference_image: 参考图路径（图生图模式），None 则为文生图
            output_path: 输出文件路径
        
        返回:
            成功返回图片 URL 列表，失败返回 None
        """
        if not self.session_active:
            print("[ERROR] 未登录，请先执行 login")
            return None

        # 构造 input_urls
        input_urls = []
        if reference_image:
            url = self.upload_reference_image(reference_image)
            if url is None:
                print("[ERROR] 参考图上传失败，无法进行图生图")
                return None
            input_urls.append(url)

        # 提交生图任务
        print(f"[INFO] 提交生图任务: prompt='{prompt[:50]}...', ratio={aspect_ratio}, res={resolution}")
        status, body, _ = self._post(
            f"{BASE_URL}/api/ai/text-to-image",
            data={
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "input_urls": input_urls,
            },
        )

        if status == 402:
            print("[ERROR] 积分不足 (HTTP 402)")
            return "INSUFFICIENT_CREDITS"
        elif status == 401:
            print("[ERROR] 登录已过期 (HTTP 401)")
            self.session_active = False
            return None
        elif status != 200:
            print(f"[ERROR] 生图请求失败 (HTTP {status}): {body[:200]}")
            return None

        resp = json.loads(body)
        generation_id = resp.get("generationId")
        poll_after_ms = resp.get("pollAfterMs", POLL_INITIAL_DELAY_MS)

        # 如果直接返回了图片（同步模式）
        if "images" in resp and resp["images"]:
            images = resp["images"]
            print(f"[OK] 图片已生成（同步模式），共 {len(images)} 张")
            if output_path:
                self._download_image(images[0], output_path)
            return images

        if not generation_id:
            print("[ERROR] 返回数据缺少 generationId")
            return None

        # 轮询获取结果
        print(f"[INFO] 任务已提交，ID: {generation_id}，开始轮询...")
        images = self._poll_generation(generation_id, poll_after_ms)
        if images is None:
            print(f"[ERROR] 生图失败或超时，任务 ID: {generation_id}")
            return None

        print(f"[OK] 图片生成成功，共 {len(images)} 张")
        if output_path:
            self._download_image(images[0], output_path)
        return images

    def _poll_generation(self, generation_id, initial_delay_ms):
        """轮询生图结果。"""
        time.sleep(initial_delay_ms / 1000)
        start_time = time.time()
        attempt = 0

        while True:
            elapsed_ms = (time.time() - start_time) * 1000
            if elapsed_ms > POLL_TIMEOUT_MS:
                print(f"[WARN] 轮询超时 ({POLL_TIMEOUT_MS/1000}秒)")
                return None

            status, body = self._get(
                f"{BASE_URL}/api/generations/{urllib.parse.quote(generation_id)}",
                headers={"Cache": "no-store"},
            )

            if status == 401:
                print("[ERROR] 登录已过期")
                self.session_active = False
                return None
            elif status != 200:
                print(f"[WARN] 轮询请求失败 (HTTP {status})，重试中...")
                time.sleep(3)
                continue

            resp = json.loads(body)
            generation = resp.get("generation", {})
            gen_status = generation.get("status")

            if gen_status == "succeeded":
                return generation.get("images", [])
            elif gen_status == "failed":
                print(f"[ERROR] 生成失败: {generation.get('error', 'unknown')}")
                return None
            elif gen_status == "pending":
                attempt += 1
                wait_ms = min(2500 + 300 * attempt, POLL_MAX_INTERVAL_MS)
                print(f"  [等待] 状态: pending, 第 {attempt} 次轮询, 等待 {wait_ms}ms...")
                time.sleep(wait_ms / 1000)
            else:
                print(f"  [未知] 状态: {gen_status}, 继续轮询...")
                time.sleep(3)

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

    # ─── 批量生成 ───

    def batch_generate(self, config_path, output_dir):
        """
        批量生成图片（PPT 场景）。
        当遇到 402 时自动换号继续。
        """
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        slides = config.get("slides", [])
        os.makedirs(output_dir, exist_ok=True)
        results = {"success": [], "failed": []}
        report_path = os.path.join(output_dir, "batch_report.json")

        for slide in slides:
            idx = slide["index"]
            prompt = slide["prompt"]
            aspect_ratio = slide.get("aspect_ratio", "16:9")
            resolution = slide.get("resolution", "1K")
            ref_img = slide.get("reference_image")
            output_path = os.path.join(output_dir, f"slide-{idx:03d}.png")

            # Skip already-generated slides (non-zero file exists)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                print(f"[SKIP] 第 {idx} 页已存在 ({os.path.getsize(output_path):,} bytes)")
                results["success"].append({"index": idx, "images": [output_path], "cached": True})
                continue

            print(f"\n{'='*60}")
            print(f"正在生成第 {idx} 页（共 {len(slides)} 页）...")

            result = self.generate_image(
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                reference_image=ref_img,
                output_path=output_path,
            )

            if result == "INSUFFICIENT_CREDITS":
                # 尝试换号
                print("[INFO] 积分不足，尝试换号...")
                if self.switch_account():
                    # 换号成功，重新生成这一页
                    result = self.generate_image(
                        prompt=prompt,
                        aspect_ratio=aspect_ratio,
                        resolution=resolution,
                        reference_image=ref_img,
                        output_path=output_path,
                    )

            if result and result != "INSUFFICIENT_CREDITS":
                results["success"].append({"index": idx, "images": result})
            else:
                results["failed"].append({"index": idx, "reason": "生成失败或无可用账号"})

            # Write progress after each slide (so backend SSE can read it)
            total = len(slides)
            done = len(results["success"]) + len(results["failed"])
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump({
                    "progress": f"{done}/{total}",
                    "done": done, "total": total,
                    "success": len(results["success"]),
                    "failed": len(results["failed"]),
                    "details": results,
                }, f, indent=2, ensure_ascii=False)
            print(f"进度: {done}/{total} (成功 {len(results['success'])}, 失败 {len(results['failed'])})")

        # 保存最终结果报告
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        print(f"\n{'='*60}")
        print(f"批量生成完成: 成功 {len(results['success'])} 页, 失败 {len(results['failed'])} 页")
        print(f"报告已保存: {report_path}")
        return results


# ─── CLI 接口 ───

def cmd_login(client, args):
    acc = client._get_current_account()
    if acc is None:
        print("[ERROR] accounts.json 中没有账号，请先注册或手动创建账号")
        return False
    return client.login(acc["email"], acc["password"])


def cmd_signup(client, args):
    return client.signup(args.email, args.password)


def cmd_logout(client, args):
    return client.logout()


def cmd_credits(client, args):
    credits = client.get_credits()
    return credits is not None


def cmd_generate(client, args):
    if not client.session_active:
        acc = client._get_current_account()
        if acc:
            client.login(acc["email"], acc["password"])
        else:
            print("[ERROR] 未登录且无可用账号")
            return False

    result = client.generate_image(
        prompt=args.prompt,
        aspect_ratio=args.aspect_ratio,
        resolution=args.resolution,
        reference_image=args.reference,
        output_path=args.output,
    )
    return result is not None and result != "INSUFFICIENT_CREDITS"


def cmd_switch_account(client, args):
    return client.switch_account()


def cmd_batch_generate(client, args):
    if not client.session_active:
        acc = client._get_current_account()
        if acc:
            client.login(acc["email"], acc["password"])
        else:
            print("[ERROR] 未登录且无可用账号")
            return False
    client.batch_generate(args.config, args.output_dir)
    return True


def main():
    parser = argparse.ArgumentParser(description="GPTImage2.online API Client")
    parser.add_argument("--accounts-file", default=None, help="accounts.json 路径")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # login
    p_login = subparsers.add_parser("login", help="登录")
    p_login.add_argument("--email", default=None)
    p_login.add_argument("--password", default=None)

    # signup
    p_signup = subparsers.add_parser("signup", help="注册新账号")
    p_signup.add_argument("--email", required=True)
    p_signup.add_argument("--password", required=True)

    # logout
    subparsers.add_parser("logout", help="退出登录")

    # credits
    subparsers.add_parser("credits", help="查询积分")

    # generate
    p_gen = subparsers.add_parser("generate", help="生成图片")
    p_gen.add_argument("--prompt", required=True, help="生图提示词")
    p_gen.add_argument("--aspect-ratio", default="16:9", help="宽高比")
    p_gen.add_argument("--resolution", default="1K", help="分辨率")
    p_gen.add_argument("--reference", default=None, help="参考图路径（图生图）")
    p_gen.add_argument("--output", default=None, help="输出文件路径")

    # switch-account
    subparsers.add_parser("switch-account", help="切换到下一个可用账号")

    # batch-generate
    p_batch = subparsers.add_parser("batch-generate", help="批量生成图片")
    p_batch.add_argument("--config", required=True, help="配置文件 JSON 路径")
    p_batch.add_argument("--output-dir", required=True, help="输出目录")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    client = GPTImage2Client(accounts_file=args.accounts_file)

    commands = {
        "login": cmd_login,
        "signup": cmd_signup,
        "logout": cmd_logout,
        "credits": cmd_credits,
        "generate": cmd_generate,
        "switch-account": cmd_switch_account,
        "batch-generate": cmd_batch_generate,
    }

    handler = commands.get(args.command)
    if handler:
        success = handler(client, args)
        sys.exit(0 if success else 1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
