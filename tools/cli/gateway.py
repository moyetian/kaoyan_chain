# -*- coding: utf-8 -*-
"""
Webhook 接收网关与实时 Web 伴侣 HTTP 服务 (gateway.py)
多模型代理路由、LaTeX 实时排版、群聊双向对话与自动化验算
"""

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

# Web 可视化伴侣会话缓存
LIVE_SESSION_MESSAGES: List[Dict[str, Any]] = []

def append_live_message(role: str, content: str) -> None:
    """向网页可视化伴侣推送同步消息"""
    LIVE_SESSION_MESSAGES.append({
        "role": role,
        "content": content,
        "time": datetime.now().strftime("%H:%M:%S")
    })
    if len(LIVE_SESSION_MESSAGES) > 60:
        LIVE_SESSION_MESSAGES.pop(0)

def create_gateway_handler(token: str = ""):
    """构造网关 HTTP handler"""
    effective_token = (token or os.environ.get("KY_GATEWAY_TOKEN", "")).strip()

    class GatewayHandler(BaseHTTPRequestHandler):
        def _is_authorized(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in ("/live", "/", "/index.html"):
                return True
            if not effective_token:
                return self.client_address[0] in ("127.0.0.1", "::1", "localhost")
            auth_h = self.headers.get("Authorization", "")
            x_tok = self.headers.get("X-KY-Token", "")
            if x_tok and x_tok == effective_token:
                return True
            if auth_h.startswith("Bearer ") and auth_h[7:].strip() == effective_token:
                return True
            return False

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
                data = json.dumps({"messages": LIVE_SESSION_MESSAGES}, ensure_ascii=False).encode("utf-8")
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

def start_background_live_server(start_port: int = 8088, host: str = "127.0.0.1") -> Optional[int]:
    """在后台静默启动 Web 实时伴侣服务器，自动处理端口占用"""
    effective_token = os.environ.get("KY_GATEWAY_TOKEN", "").strip()
    handler_class = create_gateway_handler(token=effective_token)
    bind_host = host
    for p in range(start_port, start_port + 20):
        try:
            httpd = ThreadingHTTPServer((bind_host, p), handler_class)
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            if bind_host not in ("127.0.0.1", "localhost", "::1"):
                if effective_token:
                    print(colorize(f"\n[√] 网关监听于 {bind_host}:{p}，已成功启用 Token 鉴权保护。\n", C.GREEN))
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
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        cand_ip = s.getsockname()[0]
        s.close()
        if not cand_ip.startswith(("198.18.", "198.19.", "127.")):
            local_ip = cand_ip
    except Exception:
        pass
    if local_ip == "127.0.0.1":
        try:
            _, _, ips = socket.gethostbyname_ex(socket.gethostname())
            for ip in ips:
                if (ip.startswith("192.168.") or ip.startswith("10.") or ip.startswith("172.")) and not ip.startswith(("198.18.", "198.19.")):
                    local_ip = ip
                    break
        except Exception:
            pass

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

def run_server(port: int = 8088, host: str = "127.0.0.1", gateway_token: Optional[str] = None) -> None:
    """启动轻量级 HTTP Webhook 接收网关"""
    cfg = load_config()
    print(colorize(f"\n[🚀 考研智能体 Webhook 网关与实时 Web 伴侣正在启动... 监听地址: {host}:{port}]", C.BOLD))
    print(f"  - 网页实时 LaTeX 伴侣: http://{host}:{port}/live")
    print(f"  - 钉钉/企业微信回调地址: http://{host}:{port}/webhook")
    print(f"  - 当前默认学科: {SUBJECT_DIRS[cfg.get('active_subject','math')][1]}")
    print("  - 支持接收群聊提问并自动回复，按 Ctrl+C 停止服务。\n")

    if host not in ("127.0.0.1", "localhost", "::1"):
        print(colorize(
            f"  [!] 已对外暴露 {host}:{port}。强烈建议设置环境变量 KY_GATEWAY_TOKEN 启用鉴权。\n",
            C.RED))

    effective_token = gateway_token if gateway_token else (
        os.environ.get("KY_GATEWAY_TOKEN", "") or cfg.get("gateway_token", "")
    )
    handler_class = create_gateway_handler(token=effective_token)
    httpd = ThreadingHTTPServer((host, port), handler_class)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n网关服务已平稳停止。")
