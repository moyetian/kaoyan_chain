# -*- coding: utf-8 -*-
"""
Webhook 接收网关与实时 Web 伴侣 HTTP 服务 (gateway.py)
多模型代理路由、LaTeX 实时排版、群聊双向对话与自动化验算
"""

import hmac
import html
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from tools.cli.shared import ROOT, SUBJECT_DIRS, load_config
except ImportError:
    from cli.shared import ROOT, SUBJECT_DIRS, load_config

try:
    from tools.cli.repl.renderer import C, colorize
except ImportError:
    from cli.repl.renderer import C, colorize

try:
    from tools.cli.agent.engine import query_llm_reply
except ImportError:
    from cli.agent.engine import query_llm_reply

try:
    from tools.cli.notify import send_to_feishu
except ImportError:
    from cli.notify import send_to_feishu

try:
    from skills import vision_solver
except ImportError:
    try:
        from tools.skills import vision_solver
    except ImportError:
        vision_solver = None

# Web 可视化伴侣会话缓存与并发锁
_LIVE_SESSION_LOCK = threading.RLock()
LIVE_SESSION_MESSAGES: List[Dict[str, Any]] = []

def append_live_message(role: str, content: str) -> None:
    """向网页可视化伴侣推送同步消息 (线程安全)"""
    with _LIVE_SESSION_LOCK:
        LIVE_SESSION_MESSAGES.append({
            "role": role,
            "content": content,
            "time": datetime.now().strftime("%H:%M:%S")
        })
        while len(LIVE_SESSION_MESSAGES) > 60:
            LIVE_SESSION_MESSAGES.pop(0)

def clear_live_messages() -> None:
    """清空可视化伴侣会话缓存 (线程安全)"""
    with _LIVE_SESSION_LOCK:
        LIVE_SESSION_MESSAGES.clear()

def get_live_messages_snapshot(limit: int = 60) -> List[Dict[str, Any]]:
    """获取可视化伴侣会话消息快照副本 (线程安全)"""
    with _LIVE_SESSION_LOCK:
        if limit is None:
            items = LIVE_SESSION_MESSAGES[:]
        elif limit <= 0:
            return []
        else:
            items = LIVE_SESSION_MESSAGES[-limit:]
        return [dict(m) for m in items]

def _detect_lan_ip() -> str:
    """探测本机可用于局域网访问的 IP（探测失败时回退 127.0.0.1）。"""
    local_ip = "127.0.0.1"
    try:
        # [S1 修复·socket 泄漏] connect 抛异常即跳过 close；改用上下文管理。
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("223.5.5.5", 80))
            cand_ip = s.getsockname()[0]
        if not cand_ip.startswith(("198.18.", "198.19.", "127.")):
            local_ip = cand_ip
    except Exception:
        pass
    if local_ip == "127.0.0.1":
        try:
            _, _, ips = socket.gethostbyname_ex(socket.gethostname())
            for ip in ips:
                if (ip.startswith("192.168.") or ip.startswith("10.") or ip.startswith("172.")) \
                        and not ip.startswith(("198.18.", "198.19.")):
                    local_ip = ip
                    break
        except Exception:
            pass
    return local_ip


def _token_matches(candidate: str, expected: str) -> bool:
    """恒定时间比较 token，避免逐字符比较造成的时序侧信道。

    [修复·非 ASCII token] ``hmac.compare_digest`` 在两侧都是 ``str`` 时要求
    全为 ASCII，否则抛 ``TypeError: comparing strings with non-ASCII characters
    is not supported``。中文用户极易把 ``KY_GATEWAY_TOKEN`` 设成中文（如
    ``我的密钥``），此时任何带非空 token 的请求都会让 ``_token_ok`` 抛出未捕获
    异常 —— ``do_GET`` / ``do_POST`` 都没有 try 包裹，连接被直接掐断并刷
    traceback。改为统一 UTF-8 编码成 bytes 后比较，恒定时间语义不变。
    """
    if not candidate or not expected:
        return False
    return hmac.compare_digest(
        str(candidate).encode("utf-8"), str(expected).encode("utf-8")
    )


def resolve_webhook_secret(explicit: str = "", cfg: Optional[Dict[str, Any]] = None) -> str:
    """解析 ``/webhook`` 专用回调密钥。

    [修复·webhook 密钥接入配置] 此前该密钥只能靠环境变量 ``KY_WEBHOOK_TOKEN``，
    既不在 ``ky_config.json`` 里、也没有向导入口 —— 用户重启终端或换个 shell 就
    静默失效，群里表现为「机器人没反应」。现按 ``run_server`` 里
    ``gateway_token`` 的既有口径补齐配置来源，优先级：

      1) 显式参数（命令行/调用方传入）；
      2) 环境变量 ``KY_WEBHOOK_TOKEN``（沿用旧名，向后兼容，临时覆盖配置用）；
      3) ``ky_config.json`` 顶层 ``webhook_token``（向导写入，持久生效）。

    注意与 ``gateway_token`` 的分工：本密钥**只**对 ``/webhook`` 生效，
    不参与其它路径的鉴权（见 ``_webhook_authorized``）。
    """
    if (explicit or "").strip():
        return explicit.strip()
    env_secret = (os.environ.get("KY_WEBHOOK_TOKEN") or "").strip()
    if env_secret:
        return env_secret
    if cfg is None:
        cfg = load_config()
    return str(cfg.get("webhook_token", "") or "").strip()


def create_gateway_handler(token: str = "", webhook_token: str = ""):
    """构造网关 HTTP handler"""
    effective_token = (token or os.environ.get("KY_GATEWAY_TOKEN", "")).strip()
    # [修复·/webhook 豁免] 独立的 webhook 密钥：钉钉/飞书/QQ OneBot 等第三方平台
    # 无法携带我们的网关 token，但可以在回调 URL 上追加查询参数，故单列一条密钥。
    # [修复·webhook 密钥接入配置] 取值优先级见 ``resolve_webhook_secret``。
    webhook_secret = resolve_webhook_secret(webhook_token)

    class GatewayHandler(BaseHTTPRequestHandler):
        def _token_ok(self, parsed, expected: str = "") -> bool:
            """校验请求是否携带正确 token：X-KY-Token 头 / Bearer 头 / ?token= 查询参数。"""
            want = (expected or effective_token).strip()
            if not want:
                return False
            x_tok = self.headers.get("X-KY-Token", "")
            if _token_matches(x_tok, want):
                return True
            auth_h = self.headers.get("Authorization", "")
            if auth_h.startswith("Bearer ") and _token_matches(auth_h[7:].strip(), want):
                return True
            q_tok = (urllib.parse.parse_qs(parsed.query).get("token") or [""])[0]
            if _token_matches(q_tok, want):
                return True
            return False

        def _is_loopback(self) -> bool:
            return self.client_address[0] in ("127.0.0.1", "::1", "localhost")

        def _webhook_authorized(self, parsed) -> bool:
            """`/webhook` 的专属鉴权（群聊机器人回调端点）。

            [修复] `/webhook` 是钉钉/飞书/QQ OneBot 的**回调**端点，调用方是第三方
            平台，天然拿不到我们的网关 token；此前它和其它路径一样被网关 token 一视
            同仁拦截，于是用户按代码自身建议设了 ``KY_GATEWAY_TOKEN`` 后，群聊双向
            讲题会**静默**失效。这里给出一条显式且不退化为「局域网裸奔」的通道：
              1) 配了专用密钥 ``KY_WEBHOOK_TOKEN`` —— 一律要求携带（第三方把它写进
                 回调 URL 的 ``?token=`` 即可），这是对外暴露时的推荐用法；
              2) 未配专用密钥但配了网关 token —— 回环来源（本地 NapCat 直连）放行，
                 其余来源必须携带网关 token；
              3) 两者都没配 —— 维持既有行为，仅回环放行。
            """
            if webhook_secret:
                return self._token_ok(parsed, expected=webhook_secret)
            if effective_token:
                return self._is_loopback() or self._token_ok(parsed)
            return self._is_loopback()

        def _is_authorized(self):
            """统一鉴权闸门。

            [P0-3 修复] 此前 `/live`、`/`、`/index.html` 无条件 `return True`，
            于是「配了 token 也对局域网裸奔」。现改为：
              * 未配置 token —— 所有路径仅回环（本地开发）放行；
              * 已配置 token —— 所有路径（含静态页）都必须通过 token 校验。
            [修复·/webhook 豁免] 群聊机器人回调端点走独立通道，见
            ``_webhook_authorized``；其余路径策略不变。
            """
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/webhook":
                return self._webhook_authorized(parsed)
            if not effective_token:
                return self._is_loopback()
            return self._token_ok(parsed)

        def _deny(self):
            self.send_response(401)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("WWW-Authenticate", 'Bearer realm="ky-gateway"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"error": "Unauthorized",
                 "hint": "Provide 'Authorization: Bearer <token>' or 'X-KY-Token: <token>' header"},
                ensure_ascii=False
            ).encode("utf-8"))

        def do_GET(self):
            if not self._is_authorized():
                return self._deny()
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in ("/live", "/", "/index.html"):
                live_html_p = ROOT / "docs" / "live.html"
                if not live_html_p.exists():
                    live_html_p = ROOT / "05-考研看板" / "docs" / "live.html"
                if live_html_p.exists():
                    content = live_html_p.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(content)
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"docs/live.html not found")
            elif parsed.path == "/v1/models":
                models_data = {
                    "object": "list",
                    "data": [
                        {"id": "kaoyan-tutor", "object": "model", "owned_by": "kaoyan-chain"},
                        {"id": "kaoyan-math", "object": "model", "owned_by": "kaoyan-chain"},
                        {"id": "kaoyan-eng", "object": "model", "owned_by": "kaoyan-chain"},
                        {"id": "kaoyan-pol", "object": "model", "owned_by": "kaoyan-chain"},
                        {"id": "kaoyan-pro", "object": "model", "owned_by": "kaoyan-chain"}
                    ]
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(models_data).encode("utf-8"))
                return
            elif parsed.path == "/api/live":
                with _LIVE_SESSION_LOCK:
                    snapshot = [dict(m) for m in LIVE_SESSION_MESSAGES]
                data = json.dumps({"messages": snapshot}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if not self._is_authorized():
                return self._deny()
            cfg = load_config()
            parsed = urllib.parse.urlparse(self.path)
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length).decode("utf-8", errors="ignore")

            if parsed.path == "/api/clear":
                with _LIVE_SESSION_LOCK:
                    LIVE_SESSION_MESSAGES.clear()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"status":"cleared"}')
                return

            if parsed.path == "/api/ask":
                import base64
                import time
                try:
                    data = json.loads(post_data)
                    user_msg = data.get("message", "").strip()
                    img_base64 = data.get("image", "").strip()
                except Exception:
                    user_msg = post_data.strip()
                    img_base64 = ""

                if not user_msg and not img_base64:
                    self.send_response(400)
                    self.end_headers()
                    return

                reply = ""
                if img_base64:
                    upload_dir = ROOT / "tools" / "scratch" / "uploads"
                    upload_dir.mkdir(parents=True, exist_ok=True)
                    img_filename = f"web_upload_{int(time.time()*1000)}.png"
                    img_path = upload_dir / img_filename
                    try:
                        header_sep = img_base64.find(",")
                        raw_b64 = img_base64[header_sep+1:] if header_sep != -1 else img_base64
                        decoded_bytes = base64.b64decode(raw_b64, validate=True)
                        img_path.write_bytes(decoded_bytes)
                        print(colorize(f"\n[📸 收到 Web 伴侣上传图片: {img_filename}，启动视觉技能阅卷批改...]", C.CYAN))
                        vs = vision_solver
                        if vs is None:
                            try:
                                from skills import vision_solver as vs
                            except ImportError:
                                from tools.skills import vision_solver as vs
                        prompt_text = user_msg or "请详细批改本题并按步骤给分，指出关键推导与可能的丢分点。"
                        reply = vs.solve_image_with_model(str(img_path), prompt_text, cfg, stream=False)
                    except Exception as err:
                        self.send_response(400)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(json.dumps({"reply": f"【图片解析异常】: {err}"}, ensure_ascii=False).encode("utf-8"))
                        return

                    safe_msg = html.escape(user_msg) if user_msg else ""
                    safe_img_src = html.escape(img_base64) if img_base64.startswith("data:image/") else f"data:image/png;base64,{html.escape(raw_b64)}"
                    user_display = f'<img src="{safe_img_src}" class="bubble-uploaded-img" alt="手写草稿" />' + (f'<div>{safe_msg}</div>' if safe_msg else '')
                    append_live_message("user", user_display)
                    append_live_message("assistant", reply)
                else:
                    append_live_message("user", user_msg)
                    reply = query_llm_reply(user_msg, cfg)
                    append_live_message("assistant", reply)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"reply": reply}, ensure_ascii=False).encode("utf-8"))
                return

            if parsed.path in ("/v1/chat/completions", "/chat/completions"):
                import time
                req_data = {}
                try:
                    req_data = json.loads(post_data) if post_data else {}
                    msgs = req_data.get("messages", [])
                    user_msg = msgs[-1]["content"] if msgs else ""
                except Exception:
                    user_msg = post_data.strip()

                reply = query_llm_reply(user_msg, cfg)
                append_live_message("user", f"[微信ClawBot提问]: {user_msg}")
                append_live_message("assistant", reply)

                completion_data = {
                    "id": f"chatcmpl-ky-{int(time.time())}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": req_data.get("model", "kaoyan-tutor") if isinstance(req_data, dict) else "kaoyan-tutor",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": reply},
                        "finish_reason": "stop"
                    }],
                    "usage": {"prompt_tokens": len(user_msg), "completion_tokens": len(reply), "total_tokens": len(user_msg) + len(reply)}
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(completion_data, ensure_ascii=False).encode("utf-8"))
                return

            try:
                data = json.loads(post_data) if post_data else {}
            except Exception:
                data = {}

            if data.get("type") == "url_verification":
                challenge = data.get("challenge", "")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"challenge": challenge}).encode("utf-8"))
                print(colorize("\n[✔ 飞书开放平台 Webhook URL 校验握手成功！]", C.GREEN))
                return

            user_msg = ""
            session_webhook = None
            is_feishu_event = False

            if "text" in data and isinstance(data["text"], dict) and "content" in data["text"]:
                user_msg = data["text"]["content"].strip()
                session_webhook = data.get("sessionWebhook")
            elif "event" in data and isinstance(data["event"], dict):
                is_feishu_event = True
                ev = data["event"]
                if "message" in ev and isinstance(ev["message"], dict):
                    raw_c = ev["message"].get("content", "")
                    try:
                        inner = json.loads(raw_c)
                        user_msg = inner.get("text", "").strip()
                    except Exception:
                        user_msg = str(raw_c).strip()
                    user_msg = re.sub(r"@_user_\d+", "", user_msg).strip()
                elif "text" in ev:
                    user_msg = str(ev["text"]).strip()
            elif "Content" in data:
                user_msg = str(data["Content"]).strip()
            elif "raw_message" in data:
                user_msg = str(data["raw_message"]).strip()
            elif "message" in data and isinstance(data["message"], str):
                user_msg = str(data["message"]).strip()
            else:
                user_msg = post_data.strip()

            if not user_msg:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
                return

            print(colorize(f"\n[🤖 收到群聊机器人呼入提问]: {user_msg}", C.CYAN))

            if session_webhook:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"msgtype":"empty"}')

                def dingtalk_bg(msg, s_url):
                    ans = query_llm_reply(msg, cfg)
                    append_live_message("user", f"[钉钉群提问]: {msg}")
                    append_live_message("assistant", ans)
                    try:
                        p_data = {
                            "msgtype": "markdown",
                            "markdown": {
                                "title": "考研私教解答",
                                "text": f"### 🎓 考研私教解答\n\n> **提问**: {msg}\n\n{ans}"
                            }
                        }
                        req = urllib.request.Request(s_url, data=json.dumps(p_data).encode("utf-8"), headers={"Content-Type": "application/json"})
                        urllib.request.urlopen(req, timeout=10)
                        print(colorize(f"[✔ 考研私教解答已成功送达钉钉群聊]", C.GREEN))
                    except Exception as err:
                        print(colorize(f"[!] 钉钉异步发送失败: {err}", C.RED))

                threading.Thread(target=dingtalk_bg, args=(user_msg, session_webhook), daemon=True).start()
                return

            if is_feishu_event:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"code":0}')

                def feishu_bg(msg):
                    ans = query_llm_reply(msg, cfg)
                    append_live_message("user", f"[飞书群提问]: {msg}")
                    append_live_message("assistant", ans)
                    f_hook = cfg.get("webhooks", {}).get("feishu")
                    if f_hook:
                        send_to_feishu(f_hook, f"🎓 考研私教解答\n\n> 提问: {msg}\n\n{ans}")
                        print(colorize(f"[✔ 考研私教解答已推回飞书群聊]", C.GREEN))

                threading.Thread(target=feishu_bg, args=(user_msg,), daemon=True).start()
                return

            reply = query_llm_reply(user_msg, cfg)
            append_live_message("user", user_msg)
            append_live_message("assistant", reply)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()

            if "post_type" in data:
                resp_body = json.dumps({"reply": reply, "at_sender": True}, ensure_ascii=False)
            else:
                resp_body = json.dumps({"msgtype": "text", "text": {"content": reply}}, ensure_ascii=False)
            self.wfile.write(resp_body.encode("utf-8"))

        def log_message(self, format, *args):
            return

    return GatewayHandler

def start_background_live_server(start_port: int = 8088, host: str = "127.0.0.1",
                                 token: str = "") -> Optional[int]:
    """在后台静默启动 Web 实时伴侣服务器，自动处理端口占用。

    [P1-8 修复] 此前本函数**没有 token 形参**，只读环境变量 ``KY_GATEWAY_TOKEN``，
    于是 ``ky --gateway-token=xxx`` 传进来的 token 被静默丢弃（调用方 loop.py /
    misc.py 明明持有 gateway_token 却无处可传）。现增加 ``token`` 形参，
    **显式传入优先于环境变量**。
    """
    effective_token = (token or "").strip() or os.environ.get("KY_GATEWAY_TOKEN", "").strip()
    handler_class = create_gateway_handler(token=effective_token)
    bind_host = host
    for p in range(start_port, start_port + 20):
        try:
            httpd = ThreadingHTTPServer((bind_host, p), handler_class)
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            if bind_host not in ("127.0.0.1", "localhost", "::1"):
                if effective_token:
                    print(colorize(f"\n[√] 网关监听于 {bind_host}:{p}，已成功启用 Token 鉴权保护。", C.GREEN))
                    print(colorize(f"    手机访问：http://{_detect_lan_ip()}:{p}/?token={effective_token}\n", C.CYAN))
                else:
                    print(colorize(
                        f"\n[!] 网关监听于 {bind_host}:{p}（非本机回环）。"
                        f"强烈建议设置环境变量 KY_GATEWAY_TOKEN 启用鉴权，"
                        f"否则 LAN 内任何人都可调用 /v1/chat/completions 或读取会话！\n",
                        C.RED))
            return p
        except OSError:
            continue
    return None

def show_bridge_guide() -> None:
    """打印钉钉、飞书、QQ、微信双向对话讲题接入指南"""
    local_ip = _detect_lan_ip()

    print(f"""
{C.CYAN}╭────────────────────────────────────────────────────────────────────────╮
│  🤖 考研智能体 · 聊天机器人群聊「双向对话讲题」完整打通指南              │
╰────────────────────────────────────────────────────────────────────────╯{C.RESET}

{C.BOLD}【核心原理科普：为什么之前“可以连接但无法对话”？】{C.RESET}
• {C.YELLOW}单向推送 Webhook{C.RESET}: 相当于大喇叭，电脑只能往群里“推送”晨报，群里的消息大模型听不到。
• {C.GREEN}双向对话 Webhook{C.RESET}: 钉钉/飞书/QQ 收到群员提问后，把题目 POST 给考研网关，私教批改完立即在群里回复。

{C.BOLD}【当前网关服务地址】{C.RESET}
  • 本地/同局域网回调地址: {C.GREEN}http://{local_ip}:8088/webhook{C.RESET}
  • 外网穿透参考命令: {C.CYAN}cpolar http 8088{C.RESET} 或 {C.CYAN}cloudflared tunnel --url http://localhost:8088{C.RESET}
  • {C.YELLOW}已设 KY_GATEWAY_TOKEN 时{C.RESET}：第三方平台拿不到网关 token，
    请另设回调密钥（推荐在 {C.GREEN}ky config{C.RESET} ➔ {C.GREEN}[3] 机器人配置{C.RESET} ➔
    {C.GREEN}[8] 回调密钥{C.RESET} 写入 ky_config.json，也可临时用环境变量
    {C.GREEN}KY_WEBHOOK_TOKEN=你的回调密钥{C.RESET}，或启动网关时加
    {C.GREEN}--webhook-token=你的回调密钥{C.RESET}），并把回调地址写成
    {C.GREEN}http://<地址>/webhook?token=你的回调密钥{C.RESET}；
    未设该密钥时仅本机回环（本地 QQ NapCat）可直接回调。

────────────────────────────────────────────────────────────────────────
{C.BOLD}📌 0. 微信个人号 (WeChat ClawBot 手机扫码直连，无需公网与穿透):{C.RESET}
  ① 在终端直接运行命令: {C.CYAN}ky clawbot{C.RESET}
  ② 终端将自动输出微信登录二维码，打开手机微信【扫一扫】授权连接
  ③ 本地考研私教 OpenAI API 地址: {C.GREEN}http://127.0.0.1:8088/v1{C.RESET}
  ④ 在个人微信中给机器人发题目，即可随时随地在手机上享受考研私教 1对1 讲题！

────────────────────────────────────────────────────────────────────────
{C.BOLD}📌 1. 钉钉群 (DingTalk) 实现双向讲题:{C.RESET}
  ① 打开钉钉电脑端 ➔ 进入你的考研备考群 ➔ 点击右上角【群设置】➔【智能群助手】
  ② 找到你创建的自定义机器人 ➔ 点击展开设置
  ③ 开启【机器人回调】开关 ➔ 在【POST 地址】中填入: {C.GREEN}http://<公网IP或穿透域名>/webhook{C.RESET}
  ④ 在群里直接输入: {C.YELLOW}@机器人 学数学：请问罗尔定理的核心条件是什么？{C.RESET}

────────────────────────────────────────────────────────────────────────
{C.BOLD}📌 2. 飞书群 (Feishu) 实现双向讲题:{C.RESET}
  ① 打开【飞书开放平台 (open.feishu.cn)】➔ 创建自建企业应用 ➔ 添加【机器人】能力
  ② 在【事件与回调】页面，在【请求网址】填入: {C.GREEN}http://<公网IP或穿透域名>/webhook{C.RESET}
  ③ 添加事件: 【接收消息 (im.message.receive_v1)】
  ④ 发布应用并在群聊中添加该机器人，在群里 @机器人 即可对话讲题！

────────────────────────────────────────────────────────────────────────
{C.BOLD}📌 3. QQ 群 (NapCat / OneBot 11 本地模式，无需公网 IP):{C.RESET}
  ① 在本地启动 NapCat QQ 机器人 (自带 Web 控制台)
  ② 在网络配置中添加【HTTP 事件上报】，上报地址填: {C.GREEN}http://127.0.0.1:8088/webhook{C.RESET}
  ③ 在 QQ 群里艾特机器人提问，私教直接本地极速秒回！
────────────────────────────────────────────────────────────────────────
""")

def run_server(port: int = 8088, host: str = "127.0.0.1", gateway_token: Optional[str] = None,
               webhook_token: Optional[str] = None) -> None:
    """启动轻量级 HTTP Webhook 接收网关"""
    cfg = load_config()
    print(colorize(f"\n[🚀 考研智能体 Webhook 网关与实时 Web 伴侣正在启动... 监听地址: {host}:{port}]", C.BOLD))
    print(f"  - 网页实时 LaTeX 伴侣: http://{host}:{port}/live")
    print(f"  - 钉钉/企业微信回调地址: http://{host}:{port}/webhook")
    print(f"  - 当前默认学科: {SUBJECT_DIRS[cfg.get('active_subject','math')][1]}")
    print("  - 支持接收群聊提问并自动回复，按 Ctrl+C 停止服务。\n")

    effective_token = (gateway_token or "").strip() or os.environ.get("KY_GATEWAY_TOKEN", "").strip() \
        or str(cfg.get("gateway_token", "") or "").strip()

    # [补齐·命令行形参] 与 ``gateway_token`` 同口径解析 ``/webhook`` 专用回调密钥：
    # 显式参数（``ky serve --webhook-token=xxx``）> 环境变量 ``KY_WEBHOOK_TOKEN``
    # > ``ky_config.json`` 顶层 ``webhook_token``。解析结果**必须一路传到**
    # ``create_gateway_handler``，否则形参只是摆设（本文件 P1-8 是同型缺陷：
    # 解析出来的 token 在传递环节被静默丢弃）。密钥一律不回显。
    effective_webhook_token = resolve_webhook_secret(webhook_token or "", cfg)

    if host not in ("127.0.0.1", "localhost", "::1"):
        if effective_token:
            print(colorize(f"  [√] 已启用 Token 鉴权保护。", C.GREEN))
            print(colorize(f"      手机访问：http://{_detect_lan_ip()}:{port}/?token={effective_token}\n", C.CYAN))
        else:
            print(colorize(
                f"  [!] 已对外暴露 {host}:{port}。强烈建议设置环境变量 KY_GATEWAY_TOKEN 启用鉴权。\n",
                C.RED))
        # [修复·webhook 密钥接入配置] 对外暴露时若没配回调密钥，群机器人回调会被挡在
        # 回环之外（用户配了网关 token 后群里「没反应」的典型成因），此处显式提示。
        # 只报告状态，绝不回显密钥本身。判定用**已解析的**生效值，否则
        # ``--webhook-token=xxx`` 会被误报成「未配置回调密钥」。
        if not effective_webhook_token:
            print(colorize(
                f"  [!] 未配置群机器人回调密钥：/webhook 将只接受本机回环回调。"
                f"可在 `ky config` ➔ [3] 机器人配置 ➔ [8] 设置回调密钥，"
                f"或临时用环境变量 KY_WEBHOOK_TOKEN。\n",
                C.YELLOW))

    handler_class = create_gateway_handler(token=effective_token,
                                           webhook_token=effective_webhook_token)
    httpd = ThreadingHTTPServer((host, port), handler_class)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n网关服务已平稳停止。")
    finally:
        # [S1 修复] 退出时释放监听 socket，否则句柄残留。
        try:
            httpd.server_close()
        except Exception:
            pass
