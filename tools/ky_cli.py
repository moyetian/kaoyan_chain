# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 专有智能终端 CLI (ky-cli)
全功能考研私教终端：类似 Claude Code 的全功能命令行智能体
支持：
  1. 多模型 API 接入 (DeepSeek / OpenAI / Claude / Kimi / Qwen / Ollama 等兼容协议)
  2. 聊天机器人桥接 (微信 ClawBot/企微、QQ OneBot/NapCat、钉钉、飞书 Webhook)
  3. 四科私教智能路由、流式打字输出、任务读取与状态写回
  4. 纯 Python 3.8+ 标准库实现，零 pip 第三方依赖！
"""

import os
import sys
import json
import time
import re
import urllib.request
import urllib.error
import urllib.parse
import hmac
import hashlib
import base64
import unicodedata
from pathlib import Path
from datetime import datetime

# Windows 控制台安全编码
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "ky_config.json"
HISTORY_FILE = ROOT / "ky_history.json"

# 引入考研专用 Skills 体系与路径配置
tools_dir = Path(__file__).resolve().parent
for p_item in (str(ROOT), str(tools_dir)):
    if p_item not in sys.path:
        sys.path.insert(0, p_item)

try:
    from skills import vision_solver, math_verifier, english_dissector, socratic_tutor, pdf_extractor, error_logger, latex_beautifier, list_skills, exam_composer, variant_retriever, knowledge_map, exam_diagnoser, school_scout
except Exception as _e:
    try:
        from tools.skills import vision_solver, math_verifier, english_dissector, socratic_tutor, pdf_extractor, error_logger, latex_beautifier, list_skills, exam_composer, variant_retriever, knowledge_map, exam_diagnoser, school_scout
    except Exception:
        list_skills = lambda: {}
        vision_solver = None
        math_verifier = None
        english_dissector = None
        socratic_tutor = None
        pdf_extractor = None
        error_logger = None
        latex_beautifier = None
        exam_composer = None
        variant_retriever = None
        knowledge_map = None
        exam_diagnoser = None
        school_scout = None

try:
    from agent import AgentRunner, Sandbox, PermissionManager, ToolRegistry, ContextEngine
except Exception:
    try:
        from tools.agent import AgentRunner, Sandbox, PermissionManager, ToolRegistry, ContextEngine
    except Exception:
        AgentRunner = None

# Web 可视化伴侣会话缓存
LIVE_SESSION_MESSAGES = []

def grab_clipboard_image():
    """从 Windows/macOS/Linux 系统剪贴板中提取图像并暂存为本地图片文件"""
    upload_dir = ROOT / "tools" / "scratch" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target_path = upload_dir / f"clip_{int(time.time() * 1000)}.png"

    # 1. 优先使用 Pillow ImageGrab (跨平台，支持微信截图、SnippingTool、浏览器复制图像等)
    try:
        from PIL import ImageGrab
        im = ImageGrab.grabclipboard()
        if im is not None:
            if hasattr(im, "save"):
                im.save(str(target_path), "PNG")
                return target_path
            elif isinstance(im, list):
                for item in im:
                    p = Path(item)
                    if p.is_file() and p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.bmp'):
                        return p
    except Exception:
        pass

    # 2. Windows PowerShell 底层读取剪贴板位图
    if sys.platform == "win32":
        try:
            import subprocess
            ps_cmd = f"""
            Add-Type -AssemblyName System.Windows.Forms;
            $img = [System.Windows.Forms.Clipboard]::GetImage();
            if ($img -ne $null) {{
                $img.Save('{str(target_path).replace("\\", "/")}', [System.Drawing.Imaging.ImageFormat]::Png);
                Write-Output 'OK';
            }}
            """
            res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, timeout=4)
            if "OK" in (res.stdout or "") and target_path.exists():
                return target_path
        except Exception:
            pass

    return None

def get_clipboard_text():
    """读取系统剪贴板中的纯文本 (跨平台，支持自动捕获复制的 API Key)"""
    if sys.platform == "win32":
        try:
            import subprocess
            res = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout:
                return res.stdout.strip()
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            import subprocess
            res = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout:
                return res.stdout.strip()
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        try:
            import subprocess
            res = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout:
                return res.stdout.strip()
        except Exception:
            pass
    return ""

def open_provider_console_and_get_key(provider_name, console_url, current_key=""):
    """
    自动打开服务商官方认证/API Key 管理页面，并支持一键套用剪贴板密钥
    """
    import webbrowser
    print(colorize(f"\n🌐 正在为您自动打开 {provider_name} 官方控制台: {console_url}", C.CYAN))
    print(colorize("💡 提示：在网页中登录后，点击「创建 API Key」并复制即可！\n", C.YELLOW))
    try:
        webbrowser.open(console_url)
    except Exception as e:
        print(colorize(f"   [提示] 自动唤起浏览器受阻: {e}，请手动访问上方链接。", C.DIM))

    # 检测当前系统剪贴板中是否已有密钥
    time.sleep(0.4)
    clip_text = get_clipboard_text().strip()
    is_key_like = bool(clip_text and (clip_text.startswith("sk-") or len(clip_text) >= 20) and "\n" not in clip_text and " " not in clip_text)

    if is_key_like and clip_text != current_key:
        masked = clip_text[:6] + "..." + clip_text[-4:]
        print(colorize(f"📋 检测到剪贴板中已有密钥: {masked}", C.GREEN))
        choice = input(f"👉 直接回车(Enter)立即套用剪贴板密钥，或手动粘贴新密钥: ").strip()
        if not choice:
            print(colorize(f"[√] 已成功套用剪贴板密钥！\n", C.GREEN))
            return clip_text
        return choice

    curr_display = (current_key[:6] + "..." + current_key[-4:]) if len(current_key) > 10 else (current_key or "未设置")
    user_key = input(f"请输入 API Key (直接回车保持现有: {curr_display}): ").strip()
    return user_key if user_key else current_key

def append_live_message(role, content):
    """向网页可视化伴侣推送同步消息"""
    LIVE_SESSION_MESSAGES.append({
        "role": role,
        "content": content,
        "time": datetime.now().strftime("%H:%M:%S")
    })
    if len(LIVE_SESSION_MESSAGES) > 60:
        LIVE_SESSION_MESSAGES.pop(0)

# ANSI 终端色彩
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    MAGENTA = "\033[95m"

def colorize(text, color_code):
    # Windows 终端若不支持 ANSI 则优雅降级
    if os.name == "nt" and "WT_SESSION" not in os.environ and "TERM" not in os.environ:
        return text
    return f"{color_code}{text}{C.RESET}"

# 默认配置
DEFAULT_CONFIG = {
    "api_provider": "deepseek",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "temperature": 0.3,
    "active_subject": "math",  # math, eng, pol, pro
    "webhooks": {
        "wechat": "",       # 企业微信 / 微信 ClawBot Webhook URL
        "qq_onebot": "",    # QQ OneBot11 HTTP 接口 (如 http://127.0.0.1:3000)
        "qq_target_id": "", # QQ 目标群号或好友 QQ 号
        "dingtalk": "",     # 钉钉自定义机器人 Webhook URL
        "dingtalk_secret": "", # 钉钉加签密钥 (可选)
        "feishu": "",       # 飞书群自定义机器人 Webhook URL
    }
}

def load_config():
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            # 合并默认缺失字段
            merged = DEFAULT_CONFIG.copy()
            merged.update(cfg)
            if "webhooks" in cfg:
                merged["webhooks"] = {**DEFAULT_CONFIG["webhooks"], **cfg["webhooks"]}
            return merged
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

# ════════════════════════════════════════════════════════════════
# 1. 考研私教上下文与状态加载器
# ════════════════════════════════════════════════════════════════

SUBJECT_DIRS = {
    "math": ("01-数学", "数学专属私教"),
    "eng": ("02-英语", "英语专属私教"),
    "pol": ("03-思想政治理论", "政治专属私教"),
    "pro": ("04-专业课", "专业课专属私教"),
}

def read_text_safe(path):
    if not path.exists():
        return ""
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    return ""

def build_system_prompt(active_subj="math"):
    """组装当前激活学科的私教系统提示词与外置记忆上下文"""
    sys_parts = []
    
    # 1. 顶层总控协议
    agents_root = ROOT / "AGENTS.md"
    if agents_root.exists():
        sys_parts.append("=== 【顶层协议 AGENTS.md】 ===\n" + read_text_safe(agents_root))

    # 2. 当前学科协议与状态
    subj_folder, subj_name = SUBJECT_DIRS.get(active_subj, ("01-数学", "数学专属私教"))
    s_dir = ROOT / subj_folder
    
    agents_subj = s_dir / "AGENTS.md"
    if agents_subj.exists():
        sys_parts.append(f"\n=== 【当前学科协议：{subj_name}】 ===\n" + read_text_safe(agents_subj))
        
    # 学情状态文件挂载 (优先读 .md，无则读 .template.md)
    state_files = [
        ("今日任务", s_dir / "_状态" / "今日任务.md", s_dir / "_状态" / "今日任务.template.md"),
        ("学员档案", s_dir / "_状态" / "学员档案.md", s_dir / "_状态" / "学员档案.template.md"),
        ("薄弱点雷达", s_dir / "_状态" / "薄弱点雷达.md", s_dir / "_状态" / "薄弱点雷达.template.md"),
        ("专业课学情", s_dir / "学情档案.md", s_dir / "学情档案.template.md"),
        ("考试大纲", s_dir / "考试大纲.md", None),
    ]
    
    state_context = []
    for label, real_p, tmpl_p in state_files:
        p = real_p if real_p.exists() else (tmpl_p if (tmpl_p and tmpl_p.exists()) else None)
        if p and p.exists():
            txt = read_text_safe(p)
            if txt.strip():
                state_context.append(f"--- [{label}] ({p.name}) ---\n{txt}")

    if state_context:
        sys_parts.append(f"\n=== 【当前学员学情档案与记忆状态 ({subj_name})】 ===\n" + "\n\n".join(state_context))

    # 3. 动态核验参考资料真实性 (杜绝假冒李林880等虚构题源)
    mat_dir = s_dir / "参考资料"
    mat_files = []
    if mat_dir.exists():
        for f in mat_dir.iterdir():
            if f.is_file() and f.name.lower() not in ("readme.md", ".gitkeep", ".gitignore"):
                mat_files.append(f.name)

    if mat_files:
        mat_text = (
            f"\n=== 📚【本地真题与资料白名单清单 ({subj_name})】===\n"
            f"本地「参考资料/」目录下实际存放的文件为：{', '.join(mat_files)}。\n"
            "若需抽题或引用，必须严格以以上文件为准，严禁引用上述列表之外的任何书籍！"
        )
    else:
        mat_text = (
            f"\n=== 🚨【最高红线：本地未放入参考资料 · 绝对禁止虚构题源出处】===\n"
            f"系统物理核验结果：当前学科【{subj_name}】的「参考资料/」目录下【尚未放置任何教材或题库文件】！\n"
            "【四大不可违背的真实性铁律】：\n"
            "1. 严禁凭空捏造题目出处！绝对严禁声称“以下题目均来自《李林880》”、“来自《张宇1000》”、“来自《汤家凤1800》”等虚假书名！\n"
            "2. 当学员自主输入题目时：私教只针对学员给出的题目本身进行采分点批改与思路拆解；\n"
            "3. 若在解答后提供类似题供学员巩固，必须如实标明为【私教自拟类似变式训练】，绝对禁止伪称来自某本未核验的出版物！\n"
            "4. 若学员要求从某题册（如李林880）抽题，但本地无该文件且学员未提供题号，必须如实告知：“您本地参考资料库尚未放置该文件，请提供具体题目文字或截图，私教立刻为您解答。”"
        )
    sys_parts.append(mat_text)

    sys_parts.append(
        "\n=== 【CLI 指令与行为规则】 ===\n"
        "1. 严格遵守当前配置的私教辅导风格（严格/秒杀/鼓励/溯源）；\n"
        "2. 所有派题必须来自题源白名单或学员指定题号，坚决杜绝随性自编偏题超纲题；\n"
        "3. 学员交作业时，必须输出明晰的【采分点步骤分】与【错因五分类归因】；\n"
        "4. 输出排版尽量精简、结构清晰、便于终端与手机屏幕阅读。"
    )

    return "\n\n".join(sys_parts)

# ════════════════════════════════════════════════════════════════
# 2. LLM 多模型 API 交互引擎 (零依赖流式输出)
# ════════════════════════════════════════════════════════════════

def stream_chat(messages, config):
    """向 OpenAI 兼容 API 发起流式请求并打字机式打印"""
    base_url = config.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
    url = f"{base_url}/chat/completions"
    api_key = config.get("api_key", "").strip()
    model = config.get("model", "deepseek-chat")

    if not api_key:
        print(colorize("\n[!] 错误: 未配置 API Key！请先运行 /config 设置您的模型密钥。\n", C.RED))
        return ""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Kaoyan-Study-Chain/1.0",
        "Accept": "application/json, text/event-stream"
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": config.get("temperature", 0.3),
        "stream": True
    }

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

    import threading
    stop_spinner = threading.Event()

    def spinner_task():
        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        idx = 0
        while not stop_spinner.is_set():
            frame = frames[idx % len(frames)]
            sys.stdout.write(f"\r  {C.CYAN}{frame}{C.RESET} {C.DIM}[考研私教正在审阅题干关键采分点与推导步骤...]{C.RESET}")
            sys.stdout.flush()
            idx += 1
            time.sleep(0.08)
        # 清除 spinner 行
        sys.stdout.write("\r" + " " * 48 + "\r")
        sys.stdout.flush()

    spinner_thread = threading.Thread(target=spinner_task, daemon=True)
    spinner_thread.start()

    full_reply = []
    first_token = True
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            if first_token:
                                stop_spinner.set()
                                spinner_thread.join(timeout=0.2)
                                first_token = False
                            sys.stdout.write(content)
                            sys.stdout.flush()
                            full_reply.append(content)
                except Exception:
                    continue
        stop_spinner.set()
        print()  # 换行
        return "".join(full_reply)
    except urllib.error.HTTPError as e:
        stop_spinner.set()
        err_msg = e.read().decode("utf-8", errors="ignore")
        print(colorize(f"\n[API 错误 {e.code}]: {err_msg}\n", C.RED))
        return ""
    except Exception as e:
        stop_spinner.set()
        print(colorize(f"\n[网络连接异常]: {e}\n", C.RED))
        return ""
    finally:
        stop_spinner.set()

# ════════════════════════════════════════════════════════════════
# 3. 聊天平台 Webhook / 消息桥接 (微信 / QQ / 钉钉 / 飞书)
# ════════════════════════════════════════════════════════════════

def send_to_dingtalk(webhook_url, text, secret=None):
    """向钉钉机器人推送消息 (支持加签)"""
    if not webhook_url:
        return False, "未配置钉钉 Webhook"
    target_url = webhook_url
    if secret:
        ts = str(round(time.time() * 1000))
        string_to_sign = f"{ts}\n{secret}"
        hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        sep = "&" if "?" in webhook_url else "?"
        target_url = f"{webhook_url}{sep}timestamp={ts}&sign={sign}"

    data = {
        "msgtype": "markdown",
        "markdown": {"title": "考研学习链 · 每日简报", "text": text}
    }
    req = urllib.request.Request(target_url, data=json.dumps(data).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ret = json.loads(resp.read().decode("utf-8"))
            return (ret.get("errcode") == 0), ret.get("errmsg", "ok")
    except Exception as e:
        return False, str(e)

def send_to_feishu(webhook_url, text):
    """向飞书群机器人推送消息"""
    if not webhook_url:
        return False, "未配置飞书 Webhook"
    data = {
        "msg_type": "text",
        "content": {"text": text}
    }
    req = urllib.request.Request(webhook_url, data=json.dumps(data).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ret = json.loads(resp.read().decode("utf-8"))
            return (ret.get("code") == 0 or ret.get("StatusCode") == 0), ret.get("msg", "ok")
    except Exception as e:
        return False, str(e)

def send_to_wechat(webhook_url, text):
    """向企业微信 / 微信 ClawBot Webhook 推送 Markdown 消息"""
    if not webhook_url:
        return False, "未配置微信 Webhook"
    data = {
        "msgtype": "markdown",
        "markdown": {"content": text}
    }
    req = urllib.request.Request(webhook_url, data=json.dumps(data).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ret = json.loads(resp.read().decode("utf-8"))
            return (ret.get("errcode") == 0), ret.get("errmsg", "ok")
    except Exception as e:
        return False, str(e)

def send_to_qq(endpoint, target_id, text):
    """向 QQ OneBot 11 (NapCat / Go-CQHTTP) 推送群/私聊消息"""
    if not endpoint or not target_id:
        return False, "未配置 QQ OneBot 接口或目标 ID"
    url = endpoint.rstrip("/") + "/send_msg"
    data = {
        "message_type": "group" if len(str(target_id)) > 6 else "private",
        "group_id": int(target_id) if len(str(target_id)) > 6 else 0,
        "user_id": int(target_id) if len(str(target_id)) <= 6 else 0,
        "message": text
    }
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ret = json.loads(resp.read().decode("utf-8"))
            return (ret.get("status") == "ok"), str(ret)
    except Exception as e:
        return False, str(e)

def broadcast_briefing(config, custom_msg=None):
    """一键向所有已配置的群机器人广播备考晨报与任务"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    msg = custom_msg
    if not msg:
        # 自动提取今日任务
        tasks = []
        for key, (folder, name) in SUBJECT_DIRS.items():
            t_file = ROOT / folder / "_状态" / "今日任务.md"
            if not t_file.exists():
                t_file = ROOT / folder / "_状态" / "今日任务.template.md"
            if t_file.exists():
                txt = read_text_safe(t_file)
                lines = [l.strip() for l in txt.split("\n") if l.strip().startswith("|") and not l.startswith("| 模块") and not l.startswith("|---")][:3]
                if lines:
                    tasks.append(f"📚 **{name}**:\n" + "\n".join(lines))

        msg = f"🌅 **考研学习链 · 今日任务与晨报 ({today_str})**\n\n" + \
              ( "\n\n".join(tasks) if tasks else "今日任务待生成，请在终端输入 /math 或 /eng 进行晨间报到！" ) + \
              f"\n\n👉 手机自测看板: 查看 docs/index.html 开启遮罩默写！"

    print(colorize("\n[正在向配置的 IM 机器人推送简报...]", C.CYAN))
    hooks = config.get("webhooks", {})
    
    # 钉钉
    if hooks.get("dingtalk"):
        ok, res = send_to_dingtalk(hooks["dingtalk"], msg, hooks.get("dingtalk_secret"))
        print(f"  - 钉钉机器人: {colorize('成功', C.GREEN) if ok else colorize(f'失败 ({res})', C.RED)}")
    # 飞书
    if hooks.get("feishu"):
        ok, res = send_to_feishu(hooks["feishu"], msg)
        print(f"  - 飞书机器人: {colorize('成功', C.GREEN) if ok else colorize(f'失败 ({res})', C.RED)}")
    # 微信
    if hooks.get("wechat"):
        ok, res = send_to_wechat(hooks["wechat"], msg)
        print(f"  - 微信机器人 (ClawBot/企微): {colorize('成功', C.GREEN) if ok else colorize(f'失败 ({res})', C.RED)}")
    # QQ
    if hooks.get("qq_onebot"):
        ok, res = send_to_qq(hooks["qq_onebot"], hooks.get("qq_target_id"), msg)
        print(f"  - QQ OneBot: {colorize('成功', C.GREEN) if ok else colorize(f'失败 ({res})', C.RED)}")

    print()

# ════════════════════════════════════════════════════════════════
# 4. 交互式配置管理中心 (/config)
# ════════════════════════════════════════════════════════════════

def configure_llm(cfg):
    """配置大模型 API 服务商与密钥 (支持一键直达官方控制台认证与剪贴板密钥捕获)"""
    print(colorize("\n--- 🧠 1. 大模型 API 服务商与密钥配置 ---", C.CYAN))
    print("支持接入各大主流大模型 API (选择后将自动在默认浏览器中打开官方认证与密钥页面)：")
    print("  [1] DeepSeek (api.deepseek.com) (V3/R1 理工科解题)")
    print("  [2] 智谱清言 GLM (open.bigmodel.cn)")
    print("  [3] 阿里云百炼通义千问 Qwen (dashscope.aliyuncs.com)")
    print("  [4] 硅基流动 SiliconFlow (api.siliconflow.cn - 聚合主流开源模型)")
    print("  [5] 月之暗面 Kimi (api.moonshot.cn)")
    print("  [6] 本地 Ollama (http://localhost:11434/v1)")
    print("  [7] 自定义 OpenAI 兼容接口 / 豆包 / Claude / GPT 等\n")

    p_choice = input(f"选择服务商 (1~7，直接回车保持现有: {cfg.get('api_provider','deepseek')}): ").strip()
    if p_choice == "1":
        cfg["api_provider"] = "deepseek"
        cfg["base_url"] = "https://api.deepseek.com/v1"
        cfg["model"] = "deepseek-chat"
        cfg["api_key"] = open_provider_console_and_get_key("DeepSeek", "https://platform.deepseek.com/api_keys", cfg.get("api_key", ""))
    elif p_choice == "2":
        cfg["api_provider"] = "glm"
        cfg["base_url"] = "https://open.bigmodel.cn/api/paas/v4"
        cfg["model"] = "glm-4-plus"
        cfg["api_key"] = open_provider_console_and_get_key("智谱清言 GLM", "https://open.bigmodel.cn/usercenter/apikeys", cfg.get("api_key", ""))
    elif p_choice == "3":
        cfg["api_provider"] = "qwen"
        cfg["base_url"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        cfg["model"] = "qwen-plus"
        cfg["api_key"] = open_provider_console_and_get_key("阿里云百炼 (通义千问)", "https://dashscope.console.aliyun.com/apiKey", cfg.get("api_key", ""))
    elif p_choice == "4":
        cfg["api_provider"] = "siliconflow"
        cfg["base_url"] = "https://api.siliconflow.cn/v1"
        cfg["model"] = "deepseek-ai/DeepSeek-V3"
        cfg["api_key"] = open_provider_console_and_get_key("硅基流动 SiliconFlow", "https://cloud.siliconflow.cn/account/ak", cfg.get("api_key", ""))
    elif p_choice == "5":
        cfg["api_provider"] = "kimi"
        cfg["base_url"] = "https://api.moonshot.cn/v1"
        cfg["model"] = "moonshot-v1-32k"
        cfg["api_key"] = open_provider_console_and_get_key("月之暗面 Kimi", "https://platform.moonshot.cn/console/api-keys", cfg.get("api_key", ""))
    elif p_choice == "6":
        cfg["api_provider"] = "ollama"
        cfg["base_url"] = "http://localhost:11434/v1"
        cfg["model"] = "deepseek-r1:14b"
        cfg["api_key"] = "ollama"
        print(colorize("\n[√] 本地 Ollama 接口已配置就绪 (无需 API Key)！", C.GREEN))
    elif p_choice == "7":
        cfg["api_provider"] = "custom"
        new_url = input(f"Base URL (直接回车保持现有: {cfg.get('base_url', '')}): ").strip()
        if new_url: cfg["base_url"] = new_url
        new_model = input(f"Model 模型代号 (直接回车保持现有: {cfg.get('model', '')}): ").strip()
        if new_model: cfg["model"] = new_model
        curr_key_display = cfg['api_key'][:6] + "..." if len(cfg.get('api_key','')) > 8 else (cfg.get('api_key','') or "未设置")
        new_key = input(f"API Key (输入新密钥或直接回车保持现有: {curr_key_display}): ").strip()
        if new_key: cfg["api_key"] = new_key

    save_config(cfg)
    print(colorize(f"[√] 模型 API 配置已更新为 [{cfg['api_provider']} / {cfg['model']}]！", C.GREEN))

def run_wechat_clawbot_install():
    """启动腾讯官方微信 ClawBot 扫码连接工具 (@tencent-weixin/openclaw-weixin-cli)"""
    import shutil
    import subprocess

    print(f"""
{C.CYAN}╭────────────────────────────────────────────────────────────────────────╮
│  📱 微信个人号 · WeChat ClawBot 手机扫码直连专属中枢                     │
│  (腾讯官方 @tencent-weixin/openclaw-weixin-cli 驱动)                  │
╰────────────────────────────────────────────────────────────────────────╯{C.RESET}
""")
    print(colorize("🔍 正在核验 Node.js 与 NPX 环境...", C.DIM))
    if not shutil.which("npx"):
        print(colorize("❌ 未检测到 npx 命令。请先安装 Node.js (https://nodejs.org) 或在终端运行: winget install OpenJS.NodeJS\n", C.RED))
        return
    
    print(colorize("✔ Node.js / NPX 环境正常！", C.GREEN))
    print(f"""
{C.BOLD}【微信 ClawBot 连接原理与步骤说明】{C.RESET}
• 微信个人号是由腾讯官方开源的 OpenClaw 微信连接器驱动；
• 它{C.YELLOW}并非普通 Webhook{C.RESET}，而是直接在终端打印【登录二维码】，手机微信扫码授权即可；
• 扫码成功后，微信接收到的考研提问会自动转发给本地私教大模型并推回微信！
• 本地 OpenAI 兼容接口地址: {C.GREEN}http://127.0.0.1:8088/v1{C.RESET} (已自动挂载考研私教 Prompt 与技能)

{C.CYAN}[执行命令]: npx -y @tencent-weixin/openclaw-weixin-cli@latest install{C.RESET}
""")
    act = input("是否立即启动腾讯官方扫码安装程序? (y/n) [y]: ").strip().lower()
    if act != "n":
        print(colorize("\n🚀 正在拉取腾讯官方微信连接器并启动二维码，请准备好手机微信扫一扫...\n", C.CYAN))
        try:
            subprocess.run("npx -y @tencent-weixin/openclaw-weixin-cli@latest install", shell=True)
        except Exception as e:
            print(colorize(f"执行异常: {e}", C.RED))

def configure_webhooks(cfg):
    """多选菜单式配置各个聊天机器人 Webhook"""
    hooks = cfg.setdefault("webhooks", {})

    while True:
        wc_tag = colorize("已配置", C.GREEN) if hooks.get("wechat") else colorize("未配置", C.DIM)
        dt_tag = colorize("已配置", C.GREEN) if hooks.get("dingtalk") else colorize("未配置", C.DIM)
        fs_tag = colorize("已配置", C.GREEN) if hooks.get("feishu") else colorize("未配置", C.DIM)
        qq_tag = colorize("已配置", C.GREEN) if hooks.get("qq_onebot") else colorize("未配置", C.DIM)

        print(colorize("\n--- 📱 2. 聊天机器人 / 消息推送与双向讲题配置 ---", C.CYAN))
        print("请选择您想配置或连接的机器人平台：")
        print(f"  [1] 📱 微信个人号 (WeChat ClawBot 手机扫码直连)")
        print(f"  [2] 🏢 企业微信群机器人 (Webhook 推送模式) [{wc_tag}]")
        print(f"  [3] 📌 钉钉群自定义机器人 (DingTalk)       [{dt_tag}]")
        print(f"  [4] 🐦 飞书群自定义机器人 (Feishu)         [{fs_tag}]")
        print(f"  [5] 🐧 QQ 机器人 (OneBot 11 / NapCat)      [{qq_tag}]")
        print(f"  [6] 📢 发送一条测试消息验证所有已配机器人")
        print(f"  [7] 🗑️ 清空某个平台的配置")
        print(f"  [0] 💾 保存并返回上级菜单")

        choice = input("\n请选择平台编号 (0~7) [默认 0]: ").strip() or "0"
        
        if choice == "0":
            save_config(cfg)
            print(colorize("[√] 机器人 Webhook 配置已安全保存！", C.GREEN))
            break
        elif choice == "1":
            # 微信个人号 ClawBot (扫码直连，非 Webhook)
            run_wechat_clawbot_install()
        elif choice == "2":
            print(colorize("\n[配置 企业微信群机器人 Webhook]", C.BOLD))
            print("说明：适用于企业微信群添加的机器人。在群聊 -> 添加群机器人 获取 Webhook。")
            print("地址格式如：https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxx")
            curr = hooks.get("wechat", "")
            val = input(f"请输入 Webhook URL (直接回车保持现有: {curr or '空'}): ").strip()
            if val:
                hooks["wechat"] = val
            save_config(cfg)
            if hooks.get("wechat"):
                t = input("是否立即向该微信机器人发送测试消息? (y/n) [y]: ").strip().lower()
                if t != "n":
                    ok, res = send_to_wechat(hooks["wechat"], "🎓【考研学习链】企业微信机器人连接成功！每日任务与晨报将在此推送。")
                    print(colorize(f"  -> 发送成功！", C.GREEN) if ok else colorize(f"  -> 发送失败: {res}", C.RED))
        elif choice == "3":
            print(colorize("\n[配置 钉钉群自定义机器人]", C.BOLD))
            print("说明：在钉钉电脑端群聊 -> 群设置 -> 智能群助手 -> 添加机器人 -> 自定义。")
            print("地址格式如：https://oapi.dingtalk.com/robot/send?access_token=xxxxxx")
            curr = hooks.get("dingtalk", "")
            val = input(f"请输入 Webhook URL (直接回车保持现有: {curr or '空'}): ").strip()
            if val:
                hooks["dingtalk"] = val
            sec = input(f"请输入加签 Secret (若机器人未勾选加签直接回车，当前: {hooks.get('dingtalk_secret','') or '无'}): ").strip()
            if sec != "":
                hooks["dingtalk_secret"] = sec
            save_config(cfg)
            if hooks.get("dingtalk"):
                t = input("是否立即向钉钉发送测试消息? (y/n) [y]: ").strip().lower()
                if t != "n":
                    ok, res = send_to_dingtalk(hooks["dingtalk"], "🎓 **【考研学习链】** 钉钉群机器人连接成功！每日任务与晨报将在此推送。", hooks.get("dingtalk_secret"))
                    print(colorize(f"  -> 发送成功！", C.GREEN) if ok else colorize(f"  -> 发送失败: {res}", C.RED))
        elif choice == "4":
            print(colorize("\n[配置 飞书群自定义机器人]", C.BOLD))
            print("说明：在飞书群设置 -> 机器人 -> 添加机器人 -> 自定义机器人。")
            print("地址格式如：https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxx")
            curr = hooks.get("feishu", "")
            val = input(f"请输入 Webhook URL (直接回车保持现有: {curr or '空'}): ").strip()
            if val:
                hooks["feishu"] = val
            save_config(cfg)
            if hooks.get("feishu"):
                t = input("是否立即向飞书发送测试消息? (y/n) [y]: ").strip().lower()
                if t != "n":
                    ok, res = send_to_feishu(hooks["feishu"], "🎓【考研学习链】飞书群机器人连接成功！每日任务与晨报将在此推送。")
                    print(colorize(f"  -> 发送成功！", C.GREEN) if ok else colorize(f"  -> 发送失败: {res}", C.RED))
        elif choice == "5":
            print(colorize("\n[配置 QQ 机器人 (OneBot 11 / NapCat / Go-CQHTTP)]", C.BOLD))
            print("说明：使用 NapCat QQ、LLOneBot 或 Go-CQHTTP 提供的 OneBot 11 HTTP 接口。")
            print("地址格式如：http://127.0.0.1:3000")
            curr = hooks.get("qq_onebot", "")
            val = input(f"请输入 OneBot HTTP 接口 (直接回车保持现有: {curr or '空'}): ").strip()
            if val:
                hooks["qq_onebot"] = val
            qid = input(f"请输入目标群号或好友 QQ 号 (当前: {hooks.get('qq_target_id','') or '无'}): ").strip()
            if qid:
                hooks["qq_target_id"] = qid
            save_config(cfg)
            if hooks.get("qq_onebot") and hooks.get("qq_target_id"):
                t = input("是否立即向 QQ 发送测试消息? (y/n) [y]: ").strip().lower()
                if t != "n":
                    ok, res = send_to_qq(hooks["qq_onebot"], hooks.get("qq_target_id"), "🎓【考研学习链】QQ 机器人连接成功！每日任务与晨报将在此推送。")
                    print(colorize(f"  -> 发送成功！", C.GREEN) if ok else colorize(f"  -> 发送失败: {res}", C.RED))
        elif choice == "6":
            broadcast_briefing(cfg, custom_msg="🎓【考研学习链】这是一条自检广播测试消息，您的机器人连接状态正常！")
        elif choice == "7":
            print("\n请选择要清空的平台：")
            print("  [1] 微信  [2] 钉钉  [3] 飞书  [4] QQ  [5] 清空全部")
            c = input("请输入数字: ").strip()
            if c == "1": hooks["wechat"] = ""
            elif c == "2": hooks["dingtalk"] = ""; hooks["dingtalk_secret"] = ""
            elif c == "3": hooks["feishu"] = ""
            elif c == "4": hooks["qq_onebot"] = ""; hooks["qq_target_id"] = ""
            elif c == "5":
                for k in list(hooks.keys()): hooks[k] = ""
            save_config(cfg)
            print(colorize("[√] 已清空所选平台的配置。", C.YELLOW))

def show_config(cfg):
    """显示当前完整配置清单"""
    print(colorize("\n=== 📄 当前考研私教 CLI 配置清单 ===", C.BOLD))
    print(f"  - 服务商类型: {cfg.get('api_provider')}")
    print(f"  - 接口地址:   {cfg.get('base_url')}")
    print(f"  - 模型代号:   {cfg.get('model')}")
    curr_key = cfg.get('api_key', '')
    masked_key = curr_key[:6] + "..." + curr_key[-4:] if len(curr_key) > 12 else (curr_key or "未设置")
    print(f"  - API 密钥:   {masked_key}")
    print(f"  - 当前学科:   {SUBJECT_DIRS.get(cfg.get('active_subject','math'), ('',''))[1]}")
    
    hooks = cfg.get("webhooks", {})
    print(colorize("\n--- 机器人 Webhook 配置状态 ---", C.CYAN))
    print(f"  - 微信 Webhook: {hooks.get('wechat') or '未设置'}")
    print(f"  - 钉钉 Webhook: {hooks.get('dingtalk') or '未设置'} (加签: {'已启用' if hooks.get('dingtalk_secret') else '未启用'})")
    print(f"  - 飞书 Webhook: {hooks.get('feishu') or '未设置'}")
    print(f"  - QQ OneBot:    {hooks.get('qq_onebot') or '未设置'} (目标: {hooks.get('qq_target_id') or '无'})")
    
    vis_m = cfg.get("vision_model")
    print(colorize("\n--- 视觉大模型 (Vision Model) 配置状态 ---", C.CYAN))
    print(f"  - 视觉模型:     {vis_m or '未独立配置 (默认调用本地 RapidOCR 提取题干后交由主模型)'}")
    if vis_m:
        print(f"  - 视觉 Base URL: {cfg.get('vision_base_url', '跟随主模型')}")

    print(f"\n配置文件绝对路径: {CONFIG_FILE}")
    print("本文件已被 .gitignore 严密保护，绝不会被 Git 追踪提交。\n")

def configure_vision_model(cfg):
    """配置用于视觉识图的多模态大模型 (支持一键直达官方控制台认证与剪贴板密钥捕获)"""
    print(colorize("\n--- 📸 配置多模态视觉大模型 (Vision Model) ---", C.BOLD))
    print("可选模型预设 (选择后将自动在默认浏览器中打开官方认证与密钥页面)：")
    print("  [1] 智谱清言 GLM-4V-Flash (open.bigmodel.cn)")
    print("  [2] 阿里通义千问 Qwen2-VL (DashScope / dashscope.console.aliyun.com)")
    print("  [3] 硅基流动 SiliconFlow Qwen-VL (cloud.siliconflow.cn)")
    print("  [4] 谷歌 Gemini 1.5 Flash (aistudio.google.com)")
    print("  [5] OpenAI GPT-4o-mini (platform.openai.com)")
    print("  [6] 自定义 Vision API (兼容 OpenAI 规范)")
    print("  [7] 清空配置 (使用主模型 + 本地 RapidOCR 引擎)")
    print("  [0] 取消返回")

    c = input("\n请选择视觉模型预设 (0~7) [默认 1]: ").strip() or "1"
    if c == "0":
        return
    elif c == "1":
        cfg["vision_model"] = "glm-4v-flash"
        cfg["vision_base_url"] = "https://open.bigmodel.cn/api/paas/v4"
        cfg["vision_api_key"] = open_provider_console_and_get_key("智谱清言 GLM-4V", "https://open.bigmodel.cn/usercenter/apikeys", cfg.get("vision_api_key") or cfg.get("api_key", ""))
    elif c == "2":
        cfg["vision_model"] = "qwen-vl-max"
        cfg["vision_base_url"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        cfg["vision_api_key"] = open_provider_console_and_get_key("阿里云百炼 (通义千问)", "https://dashscope.console.aliyun.com/apiKey", cfg.get("vision_api_key") or cfg.get("api_key", ""))
    elif c == "3":
        cfg["vision_model"] = "Qwen/Qwen2-VL-72B-Instruct"
        cfg["vision_base_url"] = "https://api.siliconflow.cn/v1"
        cfg["vision_api_key"] = open_provider_console_and_get_key("硅基流动 SiliconFlow", "https://cloud.siliconflow.cn/account/ak", cfg.get("vision_api_key") or cfg.get("api_key", ""))
    elif c == "4":
        cfg["vision_model"] = "gemini-1.5-flash"
        cfg["vision_base_url"] = "https://generativelanguage.googleapis.com/v1beta/openai"
        cfg["vision_api_key"] = open_provider_console_and_get_key("Google AI Studio", "https://aistudio.google.com/app/apikey", cfg.get("vision_api_key") or cfg.get("api_key", ""))
    elif c == "5":
        cfg["vision_model"] = "gpt-4o-mini"
        cfg["vision_base_url"] = "https://api.openai.com/v1"
        cfg["vision_api_key"] = open_provider_console_and_get_key("OpenAI", "https://platform.openai.com/api-keys", cfg.get("vision_api_key") or cfg.get("api_key", ""))
    elif c == "6":
        cfg["vision_model"] = input("请输入模型代号 (如 claude-3-5-sonnet): ").strip()
        cfg["vision_base_url"] = input("请输入 Base URL: ").strip()
        cfg["vision_api_key"] = input("请输入 API Key: ").strip()
    elif c == "7":
        cfg.pop("vision_model", None)
        cfg.pop("vision_base_url", None)
        cfg.pop("vision_api_key", None)
        print(colorize("\n[√] 已清空独立视觉模型，将优先使用本地 RapidOCR 引擎进行图文提取！\n", C.GREEN))
        save_config(cfg)
        return

    save_config(cfg)
    print(colorize(f"\n[√] 视觉模型已更新为: {cfg.get('vision_model')}！\n", C.GREEN))

def manage_syllabi_cli(cfg):
    """交互式切换考研科目与重载官方标准考纲"""
    try:
        from tools import syllabus_manager
    except ImportError:
        import syllabus_manager

    math_agents = ROOT / "01-数学" / "AGENTS.md"
    eng_agents = ROOT / "02-英语" / "AGENTS.md"
    pro_agents = ROOT / "04-专业课" / "AGENTS.md"

    curr_m = "数学二 (302)"
    curr_e = "英语二 (204)"
    curr_p = "专业课"
    if math_agents.exists():
        m = re.search(r"- \*\*考试科目\*\*：`([^`]+)`", read_text_safe(math_agents))
        if m: curr_m = m.group(1)
    if eng_agents.exists():
        m = re.search(r"- \*\*考试科目\*\*：`([^`]+)`", read_text_safe(eng_agents))
        if m: curr_e = m.group(1)
    if pro_agents.exists():
        m = re.search(r"- \*\*专业课科目代码与名称\*\*：`([^`]+)`", read_text_safe(pro_agents))
        if m: curr_p = m.group(1)

    print(colorize("\n--- 🎓 考研科目精细配置与官方考纲管理 ---", C.BOLD))
    print(f"当前绑定状态：数学: [{colorize(curr_m, C.CYAN)}]  英语: [{colorize(curr_e, C.CYAN)}]  专业课: [{colorize(curr_p, C.CYAN)}]")
    print("\n请选择您想调整的科目：")
    print("  [1] 📐 切换数学考试科目 (数一 / 数二 / 数三 / 396 / 自命题)")
    print("  [2] 📖 切换英语考试科目 (英语一 / 英语二 / 单独命题)")
    print("  [3] 💻 修改专业课科目 (408统考 / 199管综 / 院校自命题)")
    print("  [4] 🔄 运行完整工作区向导 (重选院校、专业与全科考纲)")
    print("  [0] 取消返回")

    c = input("\n请选择 (0~4) [默认 0]: ").strip() or "0"
    if c == "0":
        return
    elif c == "1":
        print("\n  --- 📐 请选择您的数学考试科目 ---")
        print("    [1] 数学二 (302) [高数78% + 线代22%，严控不考概率/级数/曲面积分/三重积分] (专硕主流)")
        print("    [2] 数学一 (301) [高数56% + 线代22% + 概率22%，考查范围最广/工学学硕]")
        print("    [3] 数学三 (303) [微积分56% + 线代22% + 概率22%，经管门类/差分方程]")
        print("    [4] 396 经济类综合能力数学 [微积分+线代+概率，单选与计算]")
        m_sel = input("  请选择 (1~4) [默认 1]: ").strip() or "1"
        m_key = {"1": "math2", "2": "math1", "3": "math3", "4": "math396"}.get(m_sel, "math2")
        math_info = syllabus_manager.MATH_SYLLABI[m_key]
        (ROOT / "01-数学" / "考试大纲.md").write_text(math_info["content"], encoding="utf-8")
        txt = read_text_safe(math_agents)
        txt = re.sub(r"- \*\*考试科目\*\*：.*", f"- **考试科目**：`{math_info['name']}`", txt)
        math_agents.write_text(txt, encoding="utf-8")
        print(colorize(f"\n[√] 已切换为 {math_info['name']}！已将官方大纲与超纲红线写入 01-数学/考试大纲.md", C.GREEN))
    elif c == "2":
        print("\n  --- 📖 请选择您的英语考试科目 ---")
        print("    [1] 英语二 (204) [专硕为主，整段段落英译汉 15分 + 图表数据大作文 15分] (专硕主流)")
        print("    [2] 英语一 (201) [学硕为主，5大高难长难句精译 10分 + 图画哲理漫画大作文 20分]")
        e_sel = input("  请选择 (1~2) [默认 1]: ").strip() or "1"
        e_key = {"1": "eng2", "2": "eng1"}.get(e_sel, "eng2")
        eng_info = syllabus_manager.ENGLISH_SYLLABI[e_key]
        (ROOT / "02-英语" / "考试大纲.md").write_text(eng_info["content"], encoding="utf-8")
        txt = read_text_safe(eng_agents)
        txt = re.sub(r"- \*\*考试科目\*\*：.*", f"- **考试科目**：`{eng_info['name']}`", txt)
        eng_agents.write_text(txt, encoding="utf-8")
        print(colorize(f"\n[√] 已切换为 {eng_info['name']}！已将官方大纲写入 02-英语/考试大纲.md", C.GREEN))
    elif c == "3":
        print("\n  --- 💻 请选择您的专业课方案 ---")
        print("    [1] 全国统考 408 计算机学科专业基础")
        print("    [2] 全国统考 199 管理类综合能力")
        print("    [3] 院校自命题专业课")
        p_sel = input("  请选择 (1~3) [默认 3]: ").strip() or "3"
        if p_sel == "1":
            (ROOT / "04-专业课" / "考试大纲.md").write_text(syllabus_manager.CS408_SYLLABUS, encoding="utf-8")
            pro_title = "408 计算机学科专业基础"
        else:
            pro_title = input("  请输入专业课代码与名称 [如 801 信号与系统]: ").strip() or "专业课"
            (ROOT / "04-专业课" / "考试大纲.md").write_text(
                f"# 04-专业课 · 【{pro_title}】官方考试大纲\n\n> 本大纲为报考院校官方考纲。\n\n## 考查要点\n- 请在此填入各章节掌握/理解要求",
                encoding="utf-8"
            )
        txt = read_text_safe(pro_agents)
        txt = re.sub(r"- \*\*专业课科目代码与名称\*\*：.*", f"- **专业课科目代码与名称**：`{pro_title}`", txt)
        pro_agents.write_text(txt, encoding="utf-8")
        print(colorize(f"\n[√] 专业课已更新为: {pro_title}！", C.GREEN))
    elif c == "4":
        import subprocess
        init_py = ROOT / "tools" / "init_workspace.py"
        subprocess.run([sys.executable, str(init_py)])

def interactive_config():
    """配置管理中心主路由"""
    cfg = load_config()
    while True:
        curr_p = cfg.get("api_provider", "deepseek")
        curr_m = cfg.get("model", "deepseek-chat")
        has_key = bool(cfg.get("api_key"))
        key_tag = colorize("已设置", C.GREEN) if has_key else colorize("未设置", C.RED)

        vis_m = cfg.get("vision_model", "本地 RapidOCR 引擎")
        
        hooks = cfg.get("webhooks", {})
        active_hooks = [k for k, v in hooks.items() if v and not k.endswith("_secret") and not k.endswith("_id")]
        hooks_tag = colorize(f"已配 {len(active_hooks)} 个 ({', '.join(active_hooks)})", C.GREEN) if active_hooks else colorize("未配任何平台", C.DIM)

        print(colorize("\n=== ⚙️ 考研私教 CLI 配置管理中心 ===", C.BOLD))
        print(f"  [1] 🧠 配置主大模型 API 与密钥     [当前: {curr_p} / {curr_m} / {key_tag}]")
        print(f"  [2] 📸 配置多模态视觉大模型 API   [当前: {colorize(vis_m, C.CYAN)}]")
        print(f"  [3] 📱 配置聊天机器人 Webhook 推送 [当前: {hooks_tag}]")
        print(f"  [4] 📋 个人定制化必考方案设计     [时间/考纲/已有资料白名单/学情摸底/时间预算/作息]")
        print(f"  [5] 🎓 考研科目与官方考纲快速切换 [数一/数二/数三/396、英一/英二、408/自命题]")
        print(f"  [6] 📄 查看当前完整配置清单")
        print(f"  [7] 📢 一键测试所有机器人推送")
        print(f"  [0] 💾 完成配置并返回")

        choice = input("\n请选择功能 (0~7) [默认 0]: ").strip() or "0"
        if choice == "0":
            save_config(cfg)
            print(colorize("\n[√] 配置已安全保存至 ky_config.json！\n", C.GREEN))
            break
        elif choice == "1":
            configure_llm(cfg)
        elif choice == "2":
            configure_vision_model(cfg)
        elif choice == "3":
            configure_webhooks(cfg)
        elif choice == "4":
            try:
                import study_planner
                study_planner.run_study_plan_wizard(interactive=True)
                cfg = load_config()
            except Exception as e:
                print(f"方案设计提示: {e}")
        elif choice == "5":
            manage_syllabi_cli(cfg)
        elif choice == "6":
            show_config(cfg)
        elif choice == "7":
            broadcast_briefing(cfg, custom_msg="🎓【考研学习链】这是一条自检测试广播消息，您的机器人连接状态正常！")

# ════════════════════════════════════════════════════════════════
# 5. 交互式 TUI 主界面 (Claude Code / Codex / Gemini 融合风格)
# ════════════════════════════════════════════════════════════════

def print_welcome(live_port=8088, animate=True):
    # ── 1. 彩色渐变 ASCII 大字艺术标题 ──
    gradient_ascii = f"""
{C.CYAN}{C.BOLD}  ██╗  ██╗ █████╗  ██████╗ ██╗   ██╗ █████╗ ███╗   ██╗     ██████╗██╗     ██╗{C.RESET}
{C.CYAN}{C.BOLD}  ██║ ██╔╝██╔══██╗██╔═══██╗╚██╗ ██╔╝██╔══██╗████╗  ██║    ██╔════╝██║     ██║{C.RESET}
{C.GREEN}{C.BOLD}  █████═╝ ███████║██║   ██║ ╚████╔╝ ███████║██╔██╗ ██║    ██║     ██║     ██║{C.RESET}
{C.GREEN}{C.BOLD}  ██╔═██╗ ██╔══██║██║   ██║  ╚██╔╝  ██╔══██║██║╚██╗██║    ██║     ██║     ██║{C.RESET}
{C.YELLOW}{C.BOLD}  ██║ ╚██╗██║  ██║╚██████╔╝   ██║   ██║  ██║██║ ╚████║    ╚██████╗███████╗██║{C.RESET}
{C.YELLOW}{C.BOLD}  ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝    ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═══╝     ╚═════╝╚══════╝╚═╝{C.RESET}
"""
    print(gradient_ascii)

    # ── 2. 动感载入动画 (Boot Sequence) ──
    if animate:
        steps = [
            ("装载考研全科中枢总控协议 (AGENTS.md)...", 0.04),
            ("唤醒 12 项考研专有技能 (Vision/Math/Composer/Variant/Map/Scout)...", 0.04),
            (f"启动 Web 实时可视化伴侣 (:{live_port}/live)...", 0.04)
        ]
        for step, delay in steps:
            sys.stdout.write(f"  {C.CYAN}⠋{C.RESET} {step}")
            sys.stdout.flush()
            time.sleep(delay)
            sys.stdout.write(f"\r  {C.GREEN}✔{C.RESET} {step} {C.GREEN}[就绪]{C.RESET}\n")
            sys.stdout.flush()
        print()

    # ── 3. 现代化状态与快捷指令大盘卡片 ──
    today = datetime.now().date()
    exam_date = datetime(today.year, 12, 19).date()
    if today > exam_date:
        exam_date = datetime(today.year + 1, 12, 19).date()
    days_left = (exam_date - today).days

    cfg = load_config()
    curr_subj = cfg.get("active_subject", "math")
    subj_name = SUBJECT_DIRS.get(curr_subj, ("01-数学", "数学"))[1]
    provider = cfg.get("api_provider", "deepseek")
    model_name = cfg.get("model", "deepseek-chat")

    style_tag = "严格把关·保姆流"
    agents_root = ROOT / "AGENTS.md"
    if agents_root.exists():
        txt = read_text_safe(agents_root)
        m = re.search(r"- \*\*当前激活辅导风格\*\*：`([^`]+)`", txt)
        if m:
            raw_s = m.group(1).strip().strip("[]")
            m_s = re.search(r"(\d+\.\s*)?([^\s/\]]+(?:·[^\s/\]]+)?)", raw_s)
            if m_s:
                style_tag = re.sub(r"^\d+\.\s*", "", m_s.group(2)).strip()
            else:
                style_tag = "严格把关保姆流"

    subj_short = subj_name.replace("专属私教", "").replace("私教", "").strip()
    style_short = style_tag.split("·")[0] if "·" in style_tag else style_tag

    print(f"""{C.CYAN}╭────────────────────────────────────────────────────────────────────────╮{C.RESET}
{C.CYAN}│{C.RESET}  {C.BOLD}🎓 考研全科 AI 专属私教终端 · Kaoyan CLI (Claude Code / Gemini 体验版){C.RESET}  {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  [ 专属私教: {C.GREEN}{subj_short}{C.RESET} · {C.YELLOW}{style_short}{C.RESET} ]   [ 🎯 研考初试倒计时: {C.MAGENTA}{days_left} 天{C.RESET} ]          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  [ 🧠 模型: {C.BLUE}{provider}/{model_name}{C.RESET} ]   [ 🌐 伴侣: {C.CYAN}:{live_port}/live{C.RESET} ]   [ 🧩 技能: {C.GREEN}12项全就绪{C.RESET} ]{C.CYAN}│{C.RESET}
{C.CYAN}├────────────────────────────────────────────────────────────────────────┤{C.RESET}
{C.CYAN}│{C.RESET}  {C.BOLD}快捷指令速查 (随时输入 / 展开完整指令大盘)：                            {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}   {C.GREEN}/math{C.RESET} 数学  {C.GREEN}/eng{C.RESET} 英语  {C.GREEN}/pol{C.RESET} 政治  {C.GREEN}/pro{C.RESET} 专业课  {C.CYAN}/view{C.RESET} 网页伴侣            {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}   {C.YELLOW}/scout{C.RESET} 院校侦察  {C.YELLOW}/exam{C.RESET} 靶向组卷  {C.YELLOW}/variant{C.RESET} 真题变式  {C.YELLOW}/map{C.RESET} 知识图谱  {C.CYAN}│{C.RESET}
{C.CYAN}╰────────────────────────────────────────────────────────────────────────╯{C.RESET}
""")

    # ── 4. 防疲劳减负保障警报 ──
    try:
        import study_planner
        fatigue_info = study_planner.check_fatigue_alert(cfg)
        if fatigue_info.get("alert"):
            print(f"{C.YELLOW}{C.BOLD}╭── ⚠️ 防疲劳减负保障警报 (Fatigue Protection Alert) ─────────────────╮{C.RESET}")
            for l in fatigue_info.get("message", "").splitlines():
                print(f"{C.YELLOW}│{C.RESET}  {l}")
            print(f"{C.YELLOW}╰────────────────────────────────────────────────────────────────────────╯{C.RESET}\n")
    except Exception:
        pass

def cjk_width(s: str) -> int:
    """计算考虑中文字符宽度的显示列数（去除 ANSI 逃逸码）"""
    clean = re.sub(r'\033\[[0-9;]*m', '', s)
    w = 0
    for ch in clean:
        ea = unicodedata.east_asian_width(ch)
        w += 2 if ea in ('W', 'F') else 1
    return w

def get_today_tasks_data() -> dict:
    """提取四科今日任务的结构化数据字典"""
    subjs = [
        ("01-数学", "math", "数学二 (302)"),
        ("02-英语", "eng", "英语二 (204)"),
        ("03-思想政治理论", "pol", "思想政治理论"),
        ("04-专业课", "pro", "408 计算机学科专业基础"),
    ]
    result = {"date": datetime.now().strftime("%Y-%m-%d"), "subjects": {}, "summary": {"total": 0, "completed": 0, "rate": 0.0}}
    total_count = 0
    done_count = 0

    for dir_name, key, label in subjs:
        task_file = ROOT / dir_name / "_状态" / "今日任务.md"
        tasks = []
        if task_file.exists():
            content = read_text_safe(task_file)
            lines = [l.strip() for l in content.splitlines() if "|" in l and not l.startswith("|---|") and "完成状态" not in l and "模块" not in l]
            for l in lines:
                parts = [p.strip() for p in l.split("|") if p.strip()]
                if len(parts) >= 3:
                    is_done = "[x]" in parts[-1].lower()
                    total_count += 1
                    if is_done:
                        done_count += 1
                    tasks.append({
                        "module": parts[0],
                        "content": parts[1],
                        "duration": parts[2] if len(parts) > 2 else "",
                        "done": is_done,
                    })
        result["subjects"][key] = {
            "label": label,
            "tasks": tasks,
            "total": len(tasks),
            "completed": sum(1 for t in tasks if t["done"])
        }
    result["summary"]["total"] = total_count
    result["summary"]["completed"] = done_count
    result["summary"]["rate"] = round(done_count / total_count * 100, 1) if total_count > 0 else 0.0
    return result

def mark_today_task_done(keyword: str, subject: str = None) -> tuple:
    """在今日任务中根据关键词匹配并标记为 [x] 完成"""
    subjs = [
        ("01-数学", "math"),
        ("02-英语", "eng"),
        ("03-思想政治理论", "pol"),
        ("04-专业课", "pro"),
    ]
    matched = False
    match_info = ""
    for dir_name, s_key in subjs:
        if subject and subject != s_key:
            continue
        task_file = ROOT / dir_name / "_状态" / "今日任务.md"
        if not task_file.exists():
            continue
        content = read_text_safe(task_file)
        lines = content.splitlines()
        new_lines = []
        for line in lines:
            if "|" in line and keyword in line and not line.startswith("|---|") and "完成状态" not in line and "模块" not in line:
                if "[x]" in line.lower():
                    match_info = f"任务此前已是完成状态: {line.strip()}"
                    matched = True
                    new_lines.append(line)
                else:
                    new_line = re.sub(r'\[\s*\]', '[x]', line, count=1)
                    if new_line != line:
                        matched = True
                        match_info = f"已完成打卡: {new_line.strip()}"
                        new_lines.append(new_line)
                    else:
                        new_lines.append(line)
            else:
                new_lines.append(line)
        if matched:
            task_file.write_text("\n".join(new_lines), encoding="utf-8")
            return True, match_info

    if not matched:
        return False, f"未找到包含关键词「{keyword}」的今日任务"
    return True, match_info

COACHING_STYLES = {
    "1": ("严格把关·保姆提分型 (Strict & Disciplined)", "以真题阅卷人严苛视角审视解答，步步赋分，零容忍计算与书写失误，强制归因"),
    "2": ("高效应试·高频秒杀型 (High-Yield Hacker)", "80/20法则，只抓必考得分盘，传授代入/特值/排除/帽子词口诀与解题模板"),
    "3": ("温和启发·减负鼓励型 (Encouraging Mentor)", "耐心倾听、正向激励，大题微步化拆解，降低复习挫败感与焦虑内耗"),
    "4": ("深度原理·学霸溯源型 (Deep Conceptual Master)", "溯源定理物理与几何背景，从命题设计反推陷阱，打通底层知识图谱"),
}

def manage_coaching_style(choice: str = None) -> tuple:
    """查看或切换私教辅导风格，并同步至 AGENTS.md 与 ky_config.json"""
    agents_root = ROOT / "AGENTS.md"
    content = read_text_safe(agents_root) if agents_root.exists() else ""
    cfg = load_config()

    current_style = ""
    m = re.search(r"- \*\*当前激活辅导风格\*\*：`([^`]+)`", content)
    if m:
        current_style = m.group(1).strip()
    if not current_style:
        current_style = cfg.get("coaching_style", COACHING_STYLES["1"][0])

    if not choice:
        return current_style, False

    choice = choice.strip()
    new_style_name = None
    if choice in COACHING_STYLES:
        new_style_name = COACHING_STYLES[choice][0]
    else:
        for k, (name, _) in COACHING_STYLES.items():
            if choice in name or choice in k:
                new_style_name = name
                break

    if not new_style_name:
        return current_style, False

    # 更新 AGENTS.md
    if agents_root.exists() and content:
        if "- **当前激活辅导风格**：" in content:
            content = re.sub(r"- \*\*当前激活辅导风格\*\*：.*", f"- **当前激活辅导风格**：`{new_style_name}`", content)
        else:
            content = content.replace("## 0. 你的身份与总目标", f"## 0. 你的身份与总目标\n\n- **当前激活辅导风格**：`{new_style_name}`")
        agents_root.write_text(content, encoding="utf-8")

    # 更新 ky_config.json
    cfg["coaching_style"] = new_style_name
    save_config(cfg)
    return new_style_name, True

def print_today_tasks_summary(as_json: bool = False):
    """读取并打印四科今日真实任务清单，支持终端全彩或结构化 JSON"""
    if as_json:
        print(json.dumps(get_today_tasks_data(), ensure_ascii=False, indent=2))
        return

    subjs = [
        ("01-数学", "数学", C.GREEN),
        ("02-英语", "英语", C.CYAN),
        ("03-思想政治理论", "思想政治理论", C.RED),
        ("04-专业课", "专业课", C.YELLOW)
    ]
    today_str = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{C.CYAN}╭── 📋 今日全科复习任务清单 ({today_str}) ─────────────────────────╮{C.RESET}")
    has_any = False
    for dir_name, label, color in subjs:
        task_file = ROOT / dir_name / "_状态" / "今日任务.md"
        if task_file.exists():
            has_any = True
            content = read_text_safe(task_file)
            print(f"  {colorize(f'【{label}】', color)}")
            lines = [l.strip() for l in content.splitlines() if "|" in l and not l.startswith("|---|") and "完成状态" not in l and "模块" not in l]
            for line in lines:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 3:
                    status = f"{C.GREEN}[√]{C.RESET}" if "[x]" in parts[-1].lower() else f"{C.DIM}[ ]{C.RESET}"
                    print(f"    {status} {parts[0]} ({parts[2]}): {parts[1]}")
            print()
        else:
            print(f"  {colorize(f'【{label}】', color)}: 暂未生成今日任务，输入 /plan 一键生成。\n")
    print(f"{C.CYAN}╰────────────────────────────────────────────────────────────────────────╯{C.RESET}")
    print(f"💡 开始学习口令: 输入 {C.GREEN}[科目]报到{C.RESET} (如「数学报到」) 立即由私教派题；完成输入 {C.YELLOW}交作业{C.RESET} 自动批改打分！\n")

def print_command_palette():
    """打印 Claude Code 风格分类指令面板"""
    print(f"""
{C.CYAN}╭── 🛠️ 考研私教智能终端 · 指令大盘 (Command Palette) ───────────────────────╮{C.RESET}
{C.CYAN}│{C.RESET}                                                                          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  {C.BOLD}🎓 学科专属私教路由与每日任务:{C.RESET}                                           {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.GREEN}/today{C.RESET}     查看四科今日必做任务清单与完成进度打钩 (或直接输入 /done <词>)   {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.GREEN}/math{C.RESET}      切换数学私教 (或直接输入「数学报到」/「学数学」)                 {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.GREEN}/eng{C.RESET}       切换英语私教 (或直接输入「英语报到」/「学英语」)                 {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.GREEN}/pol{C.RESET}       切换政治私教 (或直接输入「政治报到」/「学政治」)                 {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.GREEN}/pro{C.RESET}       切换专业课私教 (或直接输入「专业课报到」/「学专业课」)           {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}                                                                          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  {C.BOLD}🧩 考研专有扩展技能 (Skills):{C.RESET}                                            {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/scout <高校> [专业]{C.RESET}目标院校招生简章、大纲、招生人数与知乎/B站口碑侦察   {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/exam [科目]{C.RESET}  错题反向靶向组卷 (阶段自测盲盒试卷，支持导出与评分)       {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/variant <考点>{C.RESET}考研同类真题变式检索与防伪溯源 (优先白名单真题，严禁伪造)   {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/map [科目]{C.RESET}   官方考纲知识点图谱与四维掌握度映射 (大纲/错题薄弱点对齐)  {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/diagnose <文本>{C.RESET}整卷级多题诊断与失分聚类引擎 (章节失分排行与个性化处方)      {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/review{C.RESET}        艾宾浩斯错题盲盒重测 (隐去原答案，独立重做，通过后出库)   {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/hint{C.RESET}          苏格拉底微步骤启发 (拒绝全解剧透，分级引导突破口)         {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/done <词>{C.RESET}     快速将今日任务标记为完成并同步回写文件                   {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/batch{C.RESET}         客观题答题卡批量对题 (快速比对选项，统计正确率与错题归因) {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/img <路径>{C.RESET}      上传草稿纸或截图，逐行批改、采分点打分与 LaTeX 题干提取   {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/calc <式子>{C.RESET}     数学高精度验算 (微分方程/二次型/级数/极限/微积分/矩阵)     {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/dissect <句>{C.RESET}    英语长难句搭积木解剖 (主干骨架/从句解构/考点词/润色翻译)   {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/pdf [关键词]{C.RESET}    全文检索四科资料库中的官方教材与历年真题                 {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.YELLOW}/skills{C.RESET}         查看当前已装载的所有技能详细清单与状态                    {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}                                                                          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  {C.BOLD}🌐 前端联动与外设协同:{C.RESET}                                                  {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.CYAN}/view{C.RESET}          打开实时可视化网页伴侣 (印刷级 KaTeX 排版与双端同步)        {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.CYAN}/notify{C.RESET}        一键向微信、钉钉、飞书、QQ 群广播今日考研晨报与自测卡片    {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.CYAN}/build{C.RESET}         重新编译并刷新本地与手机自测看板 (或直接输入「更新看板」)  {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}                                                                          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  {C.BOLD}⚙️ 终端管理与辅助:{C.RESET}                                                      {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/style [1-4]{C.RESET}     查看或动态切换 4 种私教辅导风格 (严格/秒杀/鼓励/溯源)      {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/doctor{C.RESET}          一键系统健康全链路体检 (环境/依赖/状态/连通性)              {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/fatigue{C.RESET}         查看疲劳度与完成率监控警报                                 {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/relieve{C.RESET}         一键启动智能减负模式 (任务下调 25%，切换为鼓励型)          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/memory [status|prune]{C.RESET}三级分层记忆健康度查看与滚动修剪                {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/rollback{C.RESET}         快速回滚 Plan Mode 上一次快照备份                          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/plan{C.RESET}            个人专属定制化必考方案向导 (时间/考纲/白名单/学情摸底/作息) {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/status{C.RESET}          查看考研总战役大盘态势、倒计时与四科目标矩阵             {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/config{C.RESET}          分类多选管理菜单：配置大模型 API 与机器人 Webhook          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/clear{C.RESET}           清空当前会话上下文                                         {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}    {C.MAGENTA}/exit{C.RESET}            退出私教终端 (落盘记忆与会话钩子)                          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}                                                                          {C.CYAN}│{C.RESET}
{C.CYAN}│{C.RESET}  💡 {C.BOLD}中文原生口令{C.RESET}: 「数学报到」「查漏」「交作业」「更新看板」「打卡」「组卷」「变式」「知识图谱」「整卷诊断」「减负」 {C.CYAN}│{C.RESET}
{C.CYAN}╰──────────────────────────────────────────────────────────────────────────╯{C.RESET}
""")

def print_status_summary():
    """打印考研总战役大盘态势、打卡 Streak 与周日休整关怀提示 (S3-6)"""
    from datetime import date, timedelta
    today_d = date.today()
    today_s = today_d.strftime("%Y-%m-%d")
    exam_d = date(2026, 12, 19)
    days_left = (exam_d - today_d).days

    cfg = load_config()
    hist = cfg.get("completion_history", {})

    # 计算连续打卡天数 (Streak)
    streak = 0
    chk_d = today_d
    if today_s not in hist:
        chk_d = today_d - timedelta(days=1)
    while True:
        ds = chk_d.strftime("%Y-%m-%d")
        if ds in hist and (hist[ds].get("rate", 0.0) >= 60.0 or hist[ds].get("completed", 0) > 0):
            streak += 1
            chk_d -= timedelta(days=1)
        else:
            break

    # 周日休整节律提示 (AGENTS.md 个性化学情与作息调节机制)
    weekday = today_d.weekday()  # 0=Monday ... 6=Sunday
    if weekday == 6:
        rest_msg = f"{C.YELLOW}今日为周日！系统预定晚间 18:00~22:30 为休整放松窗口，适度给大脑减压，严防考前倦怠！{C.RESET}"
    else:
        days_to_sun = (6 - weekday) % 7
        rest_msg = f"距下次周日休整窗口（周日晚 18:00~22:30）还有 {C.BOLD}{days_to_sun}{C.RESET} 天，按部就班高效攻坚！"

    print(colorize(f"\n============================================================", C.CYAN))
    print(colorize(f"  🏆 考研总战役态势大盘 · 倒计时 {days_left} 天", C.BOLD))
    print(colorize(f"============================================================", C.CYAN))
    print(f"• 今日日期: {today_s} (初试首日: 2026-12-19)")
    print(f"• 连续打卡: {C.GREEN}{streak} 天 (Streak 保持中){C.RESET}")
    print(f"• 作息节律: {rest_msg}")
    print("-" * 60)

    agents_root = ROOT / "AGENTS.md"
    if agents_root.exists():
        txt = read_text_safe(agents_root)
        for line in txt.split("\n"):
            if line.startswith("- **") or line.startswith("| **科目") or line.startswith("| 合计"):
                print("  " + line)
    print(colorize("============================================================\n", C.CYAN))

def run_repl(permission_mode: str = "ask", gateway_host: str = "127.0.0.1", gateway_token: str = ""):
    cfg = load_config()

    # token 三层优先级：CLI 参数 > 环境变量 > 配置
    effective_token = (
        gateway_token
        or os.environ.get("KY_GATEWAY_TOKEN", "")
        or cfg.get("gateway_token", "")
    )

    # 静默启动后台实时 Web 可视化伴侣
    live_port = start_background_live_server(8088, host=gateway_host) or 8088
    print_welcome(live_port=live_port)

    # 首次使用引导：个人专属定制化必考方案向导
    if not cfg.get("onboarding_completed"):
        print(f"""
{C.CYAN}╭────────────────────────────────────────────────────────────────────────╮
│  🎓 欢迎使用考研全科 AI 私人教师中枢！                                 │
│  检测到您尚未进行个人专属定制化必考方案设计。                          │
│  💡 仅需 2~3 分钟即可完成时间倒计时、官方考纲、已有资料白名单、        │
│     当前学情痛点摸底与每日复习黄金作息个性化建档！                     │
╰────────────────────────────────────────────────────────────────────────╯{C.RESET}
""")
        init_plan = input("是否立即启动【个人定制化必考方案设计向导】? (y/n) [y]: ").strip().lower()
        if init_plan != "n":
            try:
                import study_planner
                study_planner.run_study_plan_wizard(interactive=True)
                cfg = load_config()
            except Exception as e:
                print(f"方案向导提示: {e}")
        else:
            print(colorize("💡 提示：您可以随时在终端输入 /plan 或运行 ky plan 重新启动向导。\n", C.DIM))

    if not cfg.get("api_key"):
        print(colorize("[!] 检测到尚未配置大模型 API Key！", C.YELLOW))
        init_ask = input("是否立即配置 API Key? (y/n) [y]: ").strip().lower()
        if init_ask != "n":
            interactive_config()
            cfg = load_config()

    curr_subj = cfg.get("active_subject", "math")
    history = []
    active_quiz_item = None

    # 初始化自主智能体内核 AgentRunner
    agent_runner = None
    if AgentRunner:
        effective_perm = permission_mode if permission_mode != "ask" else (cfg.get("permission_mode") or "ask")
        agent_runner = AgentRunner(
            config=cfg,
            workspace_root=ROOT,
            permission_mode=effective_perm,
            live_callback=append_live_message
        )
        agent_runner.set_subject(curr_subj)

    def get_prompt_tag():
        _, s_name = SUBJECT_DIRS.get(curr_subj, ("01-数学", "数学"))
        target = "110+冲刺"
        if curr_subj == "eng": target = "65+突破"
        elif curr_subj == "pol": target = "70+稳拿"
        elif curr_subj == "pro": target = "120-130拔高"
        return f"\n{C.CYAN}╭─{C.RESET} [ {C.BOLD}{s_name}{C.RESET} · {C.YELLOW}{target}{C.RESET} ] {C.DIM}──────────────────────────────────────────{C.RESET}\n{C.CYAN}╰─❯{C.RESET} "

    def print_followup_toolbar():
        print(f"\n{C.CYAN}╭──────────────────────────────────────────────────────────────────────────────────╮{C.RESET}")
        print(f"{C.CYAN}│{C.RESET}  {C.BOLD}💡 下一步:{C.RESET} [1] 📐 符号验算  [2] 📌 记错题  [3] 🌐 网页伴侣  [4] 🔄 变式演练  [5] 💡 启发提示 {C.CYAN}│{C.RESET}")
        print(f"{C.CYAN}╰──────────────────────────────────────────────────────────────────────────────────╯{C.RESET}")

    print(colorize(f"当前已激活：{SUBJECT_DIRS[curr_subj][1]}。直接输入问题/题目，或使用 /img 批改草稿，/calc 验算数学。", C.DIM))
    if live_port:
        print(colorize(f"🌐 [实时 LaTeX 网页伴侣已就绪]: http://localhost:{live_port}/live (随时输入 /view 自动打开浏览器对照排版)\n", C.CYAN))

    while True:
        try:
            user_input = input(get_prompt_tag()).strip()
        except (KeyboardInterrupt, EOFError):
            if agent_runner and hasattr(agent_runner, "hooks"):
                agent_runner.hooks.trigger_session_end({"active_subject": curr_subj})
            print("\n再见！保持节奏，一战成硕！🎓")
            break

        if not user_input:
            continue

        # ── 数字快捷操作响应 (Codex 风格) ──
        if user_input == "1":
            calc_expr = input(colorize("请输入待精确验算的数学式 (如 ode y''+4*y=0, quad [[2,1],[1,2]], limit (sin(x)-x)/x^3 as x->0): ", C.YELLOW)).strip()
            if calc_expr:
                user_input = f"/calc {calc_expr}"
            else:
                continue
        elif user_input == "2":
            last_resp = history[-1]["content"] if history and history[-1]["role"] == "assistant" else "做题记录"
            last_q = ""
            for h in reversed(history):
                if h.get("role") == "user" and not h.get("content", "").startswith("/"):
                    last_q = h.get("content", "")
                    break
            err_type = "需强化复练"
            for et in ("概念漏洞", "审题偏差", "公式记错", "计算失误", "书写丢分"):
                if et in last_resp:
                    err_type = et
                    break
            res = error_logger.log_error_record(
                subject=curr_subj,
                title=f"{SUBJECT_DIRS[curr_subj][1]}重点错题复盘",
                error_type=err_type,
                detail=last_resp[:400] + ("..." if len(last_resp) > 400 else ""),
                prescription="已载入艾宾浩斯盲盒复测队列 (1天/3天/7天后重做)。",
                question=last_q
            )
            print(colorize(f"\n[√] {res}\n", C.GREEN))
            continue
        elif user_input == "3":
            user_input = "/view"
        elif user_input == "4":
            print(colorize(f"\n[🔄 正在根据上一题考点与易错陷阱为您抽取同类变式真题...]\n", C.CYAN))
            user_input = "请根据上一题的核心考点与命题陷阱，为我抽取一道难度相当的考研真题同类变式题。要求：只给题干背景与设问，不要直接贴答案，让我先独立作答。"
        elif user_input == "5":
            last_q = ""
            for h in reversed(history):
                if h.get("role") == "user" and not h.get("content", "").startswith("/"):
                    last_q = h.get("content", "")
                    break
            if not last_q:
                print(colorize("\n[!] 暂无上一题上下文，请直接输入：/hint <题目内容>\n", C.YELLOW))
                continue
            user_input = f"/hint {last_q}"

        # ── 呼出指令大盘 ──
        if user_input in ("/", "/help", "/h", "help", "？", "?"):
            print_command_palette()
            continue

        # ── 核心中文原生口令路由 (兑现 AGENTS.md 顶层中枢协议) ──
        raw_cmd = user_input.strip()
        if raw_cmd in ("数学报到", "学数学", "切换数学"):
            curr_subj = "math"
            cfg["active_subject"] = "math"
            save_config(cfg)
            history = []
            active_quiz_item = None
            if agent_runner:
                agent_runner.set_subject("math")
            print(colorize(f"\n[已切换至：{SUBJECT_DIRS['math'][1]}] 状态与考纲已就绪。输入题目或输入 /review 立即开练！\n", C.GREEN))
            continue
        elif raw_cmd in ("英语报到", "学英语", "切换英语"):
            curr_subj = "eng"
            cfg["active_subject"] = "eng"
            save_config(cfg)
            history = []
            active_quiz_item = None
            if agent_runner:
                agent_runner.set_subject("eng")
            print(colorize(f"\n[已切换至：{SUBJECT_DIRS['eng'][1]}] 状态与考纲已就绪。输入长难句或输入 /review 立即开练！\n", C.GREEN))
            continue
        elif raw_cmd in ("政治报到", "学政治", "切换政治"):
            curr_subj = "pol"
            cfg["active_subject"] = "pol"
            save_config(cfg)
            history = []
            active_quiz_item = None
            if agent_runner:
                agent_runner.set_subject("pol")
            print(colorize(f"\n[已切换至：{SUBJECT_DIRS['pol'][1]}] 状态与考纲已就绪。输入考点或输入 /batch 对选择题！\n", C.GREEN))
            continue
        elif raw_cmd in ("专业课报到", "学专业课", "切换专业课"):
            curr_subj = "pro"
            cfg["active_subject"] = "pro"
            save_config(cfg)
            history = []
            active_quiz_item = None
            if agent_runner:
                agent_runner.set_subject("pro")
            print(colorize(f"\n[已切换至：{SUBJECT_DIRS['pro'][1]}] 状态与考纲已就绪。输入题目或高频考点演练！\n", C.GREEN))
            continue
        elif raw_cmd in ("查漏", "查漏补缺"):
            print(colorize("\n=== 🔍 考研全科薄弱点雷达与到期复测清单 ===", C.BOLD))
            for s_k, (d_name, label) in SUBJECT_DIRS.items():
                radar_file = ROOT / d_name / "_状态" / "薄弱点雷达.md"
                due_items = error_logger.get_due_reviews(s_k, max_count=3) if error_logger else []
                due_count = len(due_items)
                due_str = colorize(f"{due_count} 道到期待复测", C.YELLOW if due_count > 0 else C.GREEN)
                print(f"  • {label}: {due_str}")
                if radar_file.exists():
                    r_txt = read_text_safe(radar_file)
                    for r_line in r_txt.splitlines()[:15]:
                        if any(kw in r_line for kw in ("核心薄弱", "痛点", "高频错因", "计算失误")):
                            print(f"    - {r_line.strip()}")
            print(f"\n💡 立即复测错题请输入 {colorize('/review', C.YELLOW)}，查看今日任务请输入 {colorize('/today', C.GREEN)}。\n")
            continue
        elif raw_cmd in ("更新看板", "刷新看板"):
            build_py = ROOT / "05-考研看板" / "build.py"
            if build_py.exists():
                print(colorize("\n[正在更新并重新编译自测看板...]", C.CYAN))
                import subprocess
                subprocess.run([sys.executable, str(build_py)], cwd=str(ROOT / "05-考研看板"))
            print()
            continue
        elif raw_cmd in ("交作业", "对答案"):
            print(colorize("\n[📝 考研私教交作业与批改模式]", C.CYAN))
            print("请选择交作业方式：")
            print("  1. 截图/草稿：先截图 (Alt+A / Win+Shift+S)，然后在此输入 /paste 立即视觉逐步批改与采分点打分")
            print("  2. 客观答题卡：输入 /batch 你的答案 [标准答案] 立即批量核对正确率与归因")
            print("  3. 推导大题文字：直接输入推导步骤，私教将严格按照采分点扣分与步骤赋分！\n")
            continue
        elif raw_cmd.startswith("打卡") or raw_cmd.startswith("完成"):
            kw = raw_cmd.replace("打卡", "").replace("完成", "").strip()
            if kw:
                ok, msg = mark_today_task_done(kw, curr_subj)
                tag = C.GREEN if ok else C.YELLOW
                print(colorize(f"\n[{msg}]\n", tag))
                continue
        elif raw_cmd in ("组卷", "反向组卷", "生成试卷") or raw_cmd.startswith("组卷 "):
            sub_target = curr_subj
            c_parts = raw_cmd.split()
            if len(c_parts) > 1:
                for sk, sv in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
                    if sk in c_parts[1] or sv in c_parts[1]:
                        sub_target = sk
                        break
            if exam_composer:
                print(colorize(f"\n[📝 正在基于错题库与高频易错考点为您靶向组卷...]\n", C.CYAN))
                res = exam_composer.compose_exam_paper(sub_target, count=3, save_file=True)
                print(res.get("formatted_paper", ""))
                if res.get("saved_path"):
                    print(colorize(f"[√ 试卷已归档至]: {res['saved_path']}\n", C.GREEN))
            else:
                print(colorize("[!] exam_composer 技能模块未载入", C.RED))
            continue
        elif raw_cmd.startswith("变式") or raw_cmd in ("变式题", "找变式"):
            topic = raw_cmd.replace("变式", "").replace("题", "").replace("找", "").strip()
            if not topic:
                topic = "导数中值定理" if curr_subj == "math" else "核心高频考点"
            if variant_retriever:
                print(colorize(f"\n[🔍 正在四科白名单题源中检索【{topic}】同类真题变式...]\n", C.CYAN))
                res = variant_retriever.search_real_variant(subject=curr_subj, keyword=topic)
                print(variant_retriever.format_variant_output(res))
            else:
                print(colorize("[!] variant_retriever 技能模块未载入", C.RED))
            continue
        elif raw_cmd in ("知识图谱", "考纲图谱", "知识点图谱") or raw_cmd.startswith("知识图谱 "):
            sub_target = curr_subj
            c_parts = raw_cmd.split()
            if len(c_parts) > 1:
                for sk, sv in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
                    if sk in c_parts[1] or sv in c_parts[1]:
                        sub_target = sk
                        break
            if knowledge_map:
                print(knowledge_map.format_knowledge_map_table(sub_target))
            else:
                print(colorize("[!] knowledge_map 技能模块未载入", C.RED))
            continue
        elif raw_cmd in ("整卷诊断", "试卷诊断") or raw_cmd.startswith("整卷诊断 ") or raw_cmd.startswith("试卷诊断 "):
            text_arg = raw_cmd.replace("整卷诊断", "").replace("试卷诊断", "").strip()
            if not text_arg:
                print(colorize("用法: 整卷诊断 <答题卡文本或文件路径>\n示例: 整卷诊断 1-5: A B C D A", C.YELLOW))
                continue
            if exam_diagnoser:
                content = text_arg
                if Path(text_arg).exists():
                    content = Path(text_arg).read_text(encoding="utf-8", errors="ignore")
                res = exam_diagnoser.diagnose_mock_exam(subject=curr_subj, exam_input=content)
                print(exam_diagnoser.format_diagnosis_report(res))
            else:
                print(colorize("[!] exam_diagnoser 技能模块未载入", C.RED))
            continue
        elif raw_cmd in ("减负", "启动减负", "减负模式"):
            try:
                import study_planner
                res = study_planner.apply_relief_mode()
                if res.get("success"):
                    print(colorize(f"\n[√ {res.get('message')}]\n", C.GREEN))
                else:
                    print(colorize(f"\n[!] 启动减负失败: {res.get('message')}\n", C.RED))
            except Exception as e:
                print(f"启动减负异常: {e}")
            continue
        elif raw_cmd in ("疲劳检查", "防疲劳"):
            try:
                import study_planner
                info = study_planner.check_fatigue_alert()
                if info.get("alert"):
                    print(colorize(f"\n[⚠️ 疲劳警报触发] 连续 {info.get('consecutive_low_days')} 天低完成率 (均值 {info.get('avg_rate')}%):", C.YELLOW))
                    print(info.get("message"))
                    print(colorize("\n💡 提示：输入 减负 或 /relieve 可立即一键启动减负模式。\n", C.CYAN))
                else:
                    print(colorize(f"\n[√ 复习节奏正常] {info.get('message')}\n", C.GREEN))
            except Exception as e:
                print(f"检查疲劳异常: {e}")
            continue

        # ── 智能图片输入检测 (直接输入图片、拖拽路径、文件名匹配、或系统剪贴板自动抓取) ──
        clean_input = user_input.strip().strip('"').strip("'")
        img_pattern = r'([a-zA-Z]:[\\/][^\r\n"\'<>|?*]+?\.(?:png|jpg|jpeg|webp|bmp)|\b[^\s"\'<>|?*]+?\.(?:png|jpg|jpeg|webp|bmp))\b'
        img_match = re.search(img_pattern, user_input, re.IGNORECASE)
        found_img_path = None
        extra_question = ""

        if Path(clean_input).exists() and clean_input.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            found_img_path = clean_input
            extra_question = ""
        elif img_match:
            candidate = img_match.group(1).strip().strip('"').strip("'")
            if Path(candidate).exists():
                found_img_path = candidate
                extra_question = user_input.replace(img_match.group(0), "").strip()
            else:
                # 尝试在常见临时、桌面、下载或上传目录查找同名文件
                cand_name = Path(candidate).name
                search_dirs = [
                    ROOT / "tools" / "scratch" / "uploads",
                    Path.home() / "Desktop",
                    Path.home() / "Downloads",
                    Path(os.environ.get("TEMP", "")) if os.environ.get("TEMP") else None
                ]
                for sd in search_dirs:
                    if sd and (sd / cand_name).exists():
                        found_img_path = str(sd / cand_name)
                        extra_question = user_input.replace(img_match.group(0), "").strip()
                        break

        # 若用户输入中明确带有 [图片: ...]、"图片"、或从微信复制的图片标识，本地文件未命中时自动抓取系统剪贴板
        if not found_img_path and ("[图片" in user_input or "截图" in user_input):
            clip_img = grab_clipboard_image()
            if clip_img:
                found_img_path = str(clip_img)
                extra_question = re.sub(r'\[图片[^\]]*\]', '', user_input).strip()
                print(colorize(f"\n[📸 检测到您粘贴了图片引用，已自动从系统剪贴板抓取最新截图: {Path(found_img_path).name}！]", C.GREEN))
            else:
                print(colorize("\n[!] 提示：检测到您输入了图片引用，但在本地未找到对应文件，且当前剪贴板中无截图。", C.YELLOW))
                print("💡 解决方案：\n  1. 使用微信 (Alt+A) 或系统 (Win+Shift+S) 截图后，在终端直接输入 /paste 即可立即批改！\n  2. 或在网页伴侣 (http://127.0.0.1:8088/live) 中按 Ctrl+V 粘贴图片。\n")
                continue

        if found_img_path:
            print(colorize(f"\n[📸 检测到题目/草稿图片: {Path(found_img_path).name}，正在调起考研视觉解题技能...]\n", C.CYAN))
            reply = vision_solver.solve_image_with_model(found_img_path, extra_question, cfg, stream=True)
            if reply:
                append_live_message("user", f"[图片: {Path(found_img_path).name}] {extra_question}")
                append_live_message("assistant", reply)
                history.append({"role": "user", "content": f"[图片批改: {Path(found_img_path).name}] {extra_question}"})
                history.append({"role": "assistant", "content": reply})

                # Codex CLI 风格快捷操作栏
                print_followup_toolbar()
            continue

        # ── 斜杠指令与 Skills 分发 ──
        if user_input.startswith("/"):
            cmd_parts = user_input.split(maxsplit=1)
            cmd = cmd_parts[0].lower()
            arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""

            # 注：/exit 与 /quit 统一由下方更靠后的分支处理（保留 hooks.trigger_session_end）

            # ── 技能 1: /skills 查看所有技能 ──
            if cmd == "/skills":
                print(colorize("\n=== 🧩 考研专有智能体技能中心 (Skills Registry) ===", C.BOLD))
                for sk_id, sk in list_skills().items():
                    print(f"\n  {sk['name']} [{colorize(sk['status'], C.GREEN)}]")
                    print(f"    - 功能: {sk['desc']}")
                    print(f"    - 指令: {colorize(sk['command'], C.YELLOW)}")
                print()
                continue

            # ── 技能 2: /paste 或 /clip 直接读取系统剪贴板截图 ──
            elif cmd in ("/paste", "/clip", "/v"):
                clip_img = grab_clipboard_image()
                if not clip_img:
                    print(colorize("\n[!] 当前系统剪贴板中未检测到图片截图！", C.YELLOW))
                    print("💡 提示：您可以先使用微信截图 (Alt+A)、QQ截图 (Ctrl+Alt+A) 或 Windows截图 (Win+Shift+S) 截取题目后，在此输入 /paste 即可立即批改！\n")
                    continue
                extra = arg
                print(colorize(f"\n[📸 已从系统剪贴板读取到最新题目/草稿截图: {clip_img.name}，正在调起考研视觉解题技能...]\n", C.CYAN))
                reply = vision_solver.solve_image_with_model(str(clip_img), extra, cfg, stream=True)
                if reply:
                    append_live_message("user", f"[剪贴板截图: {clip_img.name}] {extra}")
                    append_live_message("assistant", reply)
                    history.append({"role": "user", "content": f"[剪贴板批改: {clip_img.name}] {extra}"})
                    history.append({"role": "assistant", "content": reply})

                    # Codex CLI 风格快捷操作栏
                    print_followup_toolbar()
                continue

            # ── 技能 3: /img 或 /ocr 视觉看图与手写批改 ──
            elif cmd in ("/img", "/ocr"):
                img_p = ""
                extra = ""
                if not arg:
                    # 自动尝试剪贴板
                    clip_img = grab_clipboard_image()
                    if clip_img:
                        img_p = str(clip_img)
                        print(colorize(f"\n[📸 未提供路径，已自动提取剪贴板最新截图: {clip_img.name}]", C.GREEN))
                    else:
                        print(colorize("用法: /img <图片路径> [补充要求] 或输入 /paste 自动读取剪贴板截图\n示例: /img C:\\Users\\draft.jpg 请重点检查第3行计算", C.YELLOW))
                        continue
                else:
                    parts = arg.split(maxsplit=1)
                    img_p = parts[0].strip('"').strip("'")
                    extra = parts[1] if len(parts) > 1 else ""

                if not Path(img_p).exists():
                    print(colorize(f"\n[!] 未找到图片: {img_p}\n", C.RED))
                    continue
                print(colorize(f"\n[📸 正在调起多模态视觉阅卷技能分析: {Path(img_p).name}...]\n", C.CYAN))
                reply = vision_solver.solve_image_with_model(img_p, extra, cfg, stream=True)
                if reply:
                    append_live_message("user", f"[图片: {Path(img_p).name}] {extra}")
                    append_live_message("assistant", reply)
                    history.append({"role": "user", "content": f"[图片批改: {Path(img_p).name}] {extra}"})
                    history.append({"role": "assistant", "content": reply})

                    # Codex CLI 风格快捷操作栏
                    print_followup_toolbar()
                continue

            # ── 技能 3: /calc 或 /verify 数学符号验算 ──
            elif cmd in ("/calc", "/verify"):
                if not arg:
                    print(colorize("用法: /calc <数学式子>\n示例:\n  /calc diff x^3*sin(x)\n  /calc limit (sin(x)-x)/x^3 as x->0\n  /calc int x*exp(x) dx", C.YELLOW))
                    continue
                print(colorize(f"\n[📐 正在运行数学符号验算引擎...]\n", C.CYAN))
                res = math_verifier.run_math_query(arg)
                print(res + "\n")
                print_followup_toolbar()
                continue

            # ── 技能: /review 或 /quiz 艾宾浩斯盲盒复测 ──
            elif cmd in ("/review", "/quiz"):
                target_subj = curr_subj
                if arg:
                    for s_k, s_v in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
                        if s_k in arg.lower() or s_v in arg:
                            target_subj = s_k
                            break
                due_items = error_logger.get_due_reviews(target_subj, max_count=5) if error_logger else []
                if not due_items:
                    print(colorize(f"\n[🎉 恭喜] {SUBJECT_DIRS[target_subj][1]} 当前没有到期需要艾宾浩斯复测的错题！掌握度优良！\n", C.GREEN))
                    continue

                active_quiz_item = due_items[0]
                quiz_card = error_logger.generate_blind_quiz(active_quiz_item)
                print(quiz_card + "\n")
                print(colorize("👉 请直接在下方输入您的推导步骤或最终答案进行核对 (输入 cancel 随时退出复测)：\n", C.CYAN))
                continue

            # ── 技能: /hint 或 /tishi 苏格拉底微步骤启发 ──
            elif cmd in ("/hint", "/tishi"):
                target_q = arg
                if not target_q:
                    for h in reversed(history):
                        if h.get("role") == "user" and not h.get("content", "").startswith("/"):
                            target_q = h.get("content", "")
                            break
                if not target_q:
                    print(colorize("用法: /hint <题目内容> 或做题卡壳时直接输入 /hint 获取微步骤启发\n示例: /hint 设 f(x) 在 [0, 1] 上连续，证明存在 xi 使得...", C.YELLOW))
                    continue

                hint_lvl = 1
                for h in history[-4:]:
                    c = h.get("content", "")
                    if "【第 1 级启发性提示】" in c: hint_lvl = 2
                    if "【第 2 级启发性提示】" in c: hint_lvl = 3

                print(colorize(f"\n[💡 苏格拉底导师正在为您构建 Level {hint_lvl} 微步骤启发 (严守不剧透铁律)...]\n", C.CYAN))
                hint_prompt = socratic_tutor.build_hint_prompt(target_q, hint_level=hint_lvl) if socratic_tutor else target_q
                messages = [
                    {"role": "system", "content": "你是一位深谙苏格拉底式启发教学理念的考研专属私教总教练。"},
                    {"role": "user", "content": hint_prompt}
                ]
                reply = stream_chat(messages, cfg)
                if reply:
                    history.append({"role": "user", "content": f"/hint {target_q}"})
                    history.append({"role": "assistant", "content": f"【第 {hint_lvl} 级启发性提示】\n{reply}"})
                    print_followup_toolbar()
                continue

            # ── 技能: /batch 或 /answers 客观题答题卡批量对题 ──
            elif cmd in ("/batch", "/answers"):
                if not arg:
                    print(colorize("用法: /batch <你的选项答案序列> [标准答案序列]\n示例:\n  /batch 1-5: A B C D A; 6-10: C B A D C\n  /batch 我的答案: CADBD 标准答案: CADBC", C.YELLOW))
                    continue
                print(colorize(f"\n[📊 正在核对客观题答题卡并统计正答率与错题考点...]\n", C.CYAN))
                batch_prompt = (
                    "你是一位考研命题与阅卷总教练。学员输入了一组客观选择题的答题卡选项：\n\n"
                    f"```text\n{arg}\n```\n\n"
                    "请按照以下格式进行批量核对与诊断：\n"
                    "1. 【正误统计】：逐题核对并列出对错清单，计算总正答率与得分；\n"
                    "2. 【错题聚类与考点定位】：明确指出错题分别考察考纲哪一章节考点；\n"
                    "3. 【深度精讲指引】：挑出错题中最关键的一道，指出解题突破口并建议学员在草稿纸上复练。"
                )
                messages = [
                    {"role": "system", "content": "你是一位考研阅卷与答题卡批改专家。"},
                    {"role": "user", "content": batch_prompt}
                ]
                reply = stream_chat(messages, cfg)
                if reply:
                    history.append({"role": "user", "content": f"/batch {arg}"})
                    history.append({"role": "assistant", "content": reply})
                    print_followup_toolbar()
                continue

            # ── 技能 4: /dissect 英语长难句解剖 ──
            elif cmd in ("/dissect", "/chai"):
                if not arg:
                    print(colorize("用法: /dissect <考研英语长难句>\n示例: /dissect But the human mind can also imagine what it would be like...", C.YELLOW))
                    continue
                dissect_prompt = english_dissector.build_dissection_prompt(arg)
                messages = [
                    {"role": "system", "content": "你是一位考研英语长难句命题分析与拆解专家。"},
                    {"role": "user", "content": dissect_prompt}
                ]
                print(colorize(f"\n[🧱 正在执行长难句搭积木分层切分...]\n", C.CYAN))
                reply = stream_chat(messages, cfg)
                if reply:
                    history.append({"role": "user", "content": f"/dissect {arg}"})
                    history.append({"role": "assistant", "content": reply})
                continue

            # ── 技能 5: /pdf 资料检索 ──
            elif cmd == "/pdf":
                if arg:
                    print(colorize(f"\n[📚 正在四科资料库中检索关键词: {arg}...]\n", C.CYAN))
                    matches = pdf_extractor.search_text_in_materials(arg)
                    if matches:
                        for m in matches[:10]:
                            print("  " + m)
                    else:
                        print("  未检索到相关内容。")
                else:
                    print(colorize("\n[📚 四科「参考资料/」文献清单]:", C.CYAN))
                    mats = pdf_extractor.list_materials()
                    for s, flist in mats.items():
                        print(f"  - {s}: {', '.join(flist) if flist else '暂无文件 (可放入教材PDF/真题)'}")
                print()
                continue

            # ── 四科路由 ──
            elif cmd in ("/math", "/shuxue"):
                curr_subj = "math"
                cfg["active_subject"] = "math"
                save_config(cfg)
                history = []
                active_quiz_item = None
                if agent_runner:
                    agent_runner.set_subject("math")
                print(colorize(f"\n[已切换至：{SUBJECT_DIRS['math'][1]}] 上下文与状态已重载。\n", C.GREEN))
                continue
            elif cmd in ("/eng", "/yingyu"):
                curr_subj = "eng"
                cfg["active_subject"] = "eng"
                save_config(cfg)
                history = []
                active_quiz_item = None
                if agent_runner:
                    agent_runner.set_subject("eng")
                print(colorize(f"\n[已切换至：{SUBJECT_DIRS['eng'][1]}] 上下文与状态已重载。\n", C.GREEN))
                continue
            elif cmd in ("/pol", "/zhengzhi"):
                curr_subj = "pol"
                cfg["active_subject"] = "pol"
                save_config(cfg)
                history = []
                active_quiz_item = None
                if agent_runner:
                    agent_runner.set_subject("pol")
                print(colorize(f"\n[已切换至：{SUBJECT_DIRS['pol'][1]}] 上下文与状态已重载。\n", C.GREEN))
                continue
            elif cmd in ("/pro", "/zhuanye"):
                curr_subj = "pro"
                cfg["active_subject"] = "pro"
                save_config(cfg)
                history = []
                active_quiz_item = None
                if agent_runner:
                    agent_runner.set_subject("pro")
                print(colorize(f"\n[已切换至：{SUBJECT_DIRS['pro'][1]}] 上下文与状态已重载。\n", C.GREEN))
                continue
            # ── 辅导风格动态切换 ──
            elif cmd == "/style":
                new_style, changed = manage_coaching_style(arg)
                if changed:
                    print(colorize(f"\n[√ 辅导风格切换成功] 当前已激活：{new_style}\n", C.GREEN))
                else:
                    cur_s, _ = manage_coaching_style()
                    print(colorize(f"\n=== 🎯 当前私教辅导风格: {cur_s} ===", C.BOLD))
                    for k, (name, desc) in COACHING_STYLES.items():
                        mark = colorize(" [当前激活]", C.GREEN) if name == cur_s else ""
                        print(f"  [{k}] {name}{mark}\n      {desc}")
                    print("切换命令示例: /style 1 或 /style 2\n")
                continue
            # ── 今日任务打卡 ──
            elif cmd == "/done":
                if not arg:
                    print(colorize("用法: /done <任务关键词>\n示例: /done 导数中值定理", C.YELLOW))
                    continue
                ok, msg = mark_today_task_done(arg, curr_subj)
                tag = C.GREEN if ok else C.YELLOW
                print(colorize(f"\n[{msg}]\n", tag))
                continue
            # ── 系统全链路健康诊断 ──
            elif cmd == "/doctor":
                try:
                    import doctor
                    doctor.run_doctor()
                except Exception as e:
                    print(colorize(f"\n[!] 执行 doctor 异常: {e}\n", C.RED))
                continue
            elif cmd == "/clear":
                history = []
                print(colorize("\n[已清空当前会话上下文]\n", C.YELLOW))
                continue
            elif cmd == "/config":
                interactive_config()
                cfg = load_config()
                continue
            elif cmd == "/notify":
                broadcast_briefing(cfg)
                continue
            elif cmd == "/build":
                print(colorize("\n[正在重新编译移动端看板...]", C.CYAN))
                build_py = ROOT / "05-考研看板" / "build.py"
                if build_py.exists():
                    import subprocess
                    subprocess.run([sys.executable, str(build_py)], cwd=str(ROOT / "05-考研看板"))
                print()
                continue
            elif cmd in ("/view", "/live"):
                import webbrowser
                target_url = f"http://localhost:{live_port or 8088}/live"
                webbrowser.open(target_url)
                print(colorize(f"\n[已在默认浏览器中打开实时可视化伴侣: {target_url}]\n", C.GREEN))
                continue
            elif cmd in ("/today", "/tasks", "/task"):
                print_today_tasks_summary()
                continue
            elif cmd in ("/exit", "/quit", "exit", "quit"):
                if agent_runner and hasattr(agent_runner, "hooks"):
                    agent_runner.hooks.trigger_session_end({"active_subject": curr_subj})
                print("\n再见！保持节奏，一战成硕！🎓")
                break
            elif cmd in ("/plan", "/profile", "/blueprint"):
                try:
                    import study_planner
                    study_planner.run_study_plan_wizard(interactive=True)
                    cfg = load_config()
                except Exception as e:
                    print(f"方案设计提示: {e}")
                continue
            elif cmd in ("/subject", "/syllabus"):
                manage_syllabi_cli(cfg)
                continue
            elif cmd in ("/exam", "/compose"):
                sub_target = curr_subj
                exam_count = 3
                if arg:
                    for part in arg.split():
                        if part.startswith("--count="):
                            try: exam_count = int(part.split("=")[1])
                            except: pass
                        elif part.isdigit():
                            exam_count = int(part)
                        else:
                            for sk, sv in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
                                if sk in part.lower() or sv in part:
                                    sub_target = sk
                                    break
                if exam_composer:
                    print(colorize(f"\n[📝 正在基于错题库与高频考点为【{SUBJECT_DIRS.get(sub_target, ('', sub_target))[1]}】靶向组卷 ({exam_count}题)...]\n", C.CYAN))
                    res = exam_composer.compose_exam_paper(sub_target, count=exam_count, save_file=True)
                    print(res.get("formatted_paper", ""))
                    if res.get("saved_path"):
                        print(colorize(f"[√ 试卷已归档至]: {res['saved_path']}\n", C.GREEN))
                else:
                    print(colorize("[!] exam_composer 技能未载入", C.RED))
                continue
            elif cmd in ("/scout", "/yuanxiao", "/school"):
                parts = arg.strip().split()
                target_school = parts[0] if parts else ""
                target_major = parts[1] if len(parts) > 1 else ""
                if not target_school:
                    cfg_tmp = load_config()
                    target_school = cfg_tmp.get("study_plan", {}).get("school", "")
                    target_major = cfg_tmp.get("study_plan", {}).get("major", "")
                if not target_school or target_school == "目标院校":
                    print(colorize("用法: /scout <高校名> [专业名]\n示例: /scout 华中科技大学 计算机\n提示: 也可在 ky_config.json 中配置目标院校后直接输入 /scout", C.YELLOW))
                    continue
                if school_scout:
                    print(colorize(f"\n[🎯 正在对【{target_school}】{target_major} 启动研招官方与知乎/B站/小红书舆情侦察...]\n", C.CYAN))
                    res = school_scout.scout_school(school=target_school, major=target_major, include_social=True, save_report=True, apply_to_config=False, use_llm=True)
                    print(res.get("formatted_report", ""))
                    if res.get("saved_path"):
                        print(colorize(f"\n[√ 完整研报已归档至]: {res['saved_path']}\n", C.GREEN))
                else:
                    print(colorize("[!] school_scout 技能未载入", C.RED))
                continue
            elif cmd in ("/variant", "/bianshi"):
                topic = arg.strip()
                if not topic:
                    topic = "导数中值定理" if curr_subj == "math" else "核心高频考点"
                if variant_retriever:
                    print(colorize(f"\n[🔍 正在四科白名单题源中检索【{topic}】同类真题变式...]\n", C.CYAN))
                    res = variant_retriever.search_real_variant(subject=curr_subj, keyword=topic)
                    print(variant_retriever.format_variant_output(res))
                else:
                    print(colorize("[!] variant_retriever 技能未载入", C.RED))
                continue
            elif cmd in ("/map", "/tupu"):
                sub_target = curr_subj
                if arg:
                    for sk, sv in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
                        if sk in arg.lower() or sv in arg:
                            sub_target = sk
                            break
                if knowledge_map:
                    print(knowledge_map.format_knowledge_map_table(sub_target))
                else:
                    print(colorize("[!] knowledge_map 技能未载入", C.RED))
                continue
            elif cmd in ("/diagnose", "/zhenduan"):
                if not arg:
                    print(colorize("用法: /diagnose <答题卡文本或文件路径>\n示例: /diagnose 1-5: A B C D A; 6-10: B C A D B", C.YELLOW))
                    continue
                if exam_diagnoser:
                    content = arg
                    if Path(arg).exists():
                        content = Path(arg).read_text(encoding="utf-8", errors="ignore")
                    res = exam_diagnoser.diagnose_mock_exam(subject=curr_subj, exam_input=content)
                    print(exam_diagnoser.format_diagnosis_report(res))
                else:
                    print(colorize("[!] exam_diagnoser 技能未载入", C.RED))
                continue
            elif cmd == "/fatigue":
                try:
                    import study_planner
                    info = study_planner.check_fatigue_alert()
                    if info.get("alert"):
                        print(colorize(f"\n[⚠️ 疲劳警报触发] 连续 {info.get('consecutive_low_days')} 天低完成率 (均值 {info.get('avg_rate')}%):", C.YELLOW))
                        print(info.get("message"))
                        print(colorize("\n💡 提示：输入 /relieve 可立即一键启动减负模式。\n", C.CYAN))
                    else:
                        print(colorize(f"\n[√ 复习节奏正常] {info.get('message')}\n", C.GREEN))
                except Exception as e:
                    print(f"检查疲劳异常: {e}")
                continue
            elif cmd in ("/relieve", "/jianfu"):
                try:
                    import study_planner
                    res = study_planner.apply_relief_mode()
                    if res.get("success"):
                        print(colorize(f"\n[√ {res.get('message')}]\n", C.GREEN))
                    else:
                        print(colorize(f"\n[!] 启动减负失败: {res.get('message')}\n", C.RED))
                except Exception as e:
                    print(f"启动减负异常: {e}")
                continue
            elif cmd in ("/clawbot", "/wechat", "/wx"):
                run_wechat_clawbot_install()
                continue
            elif cmd in ("/bridge", "/bot", "/webhook"):
                show_bridge_guide()
                continue
            elif cmd in ("/rollback", "/restore"):
                try:
                    from tools.agent import PermissionManager
                    pm = PermissionManager(workspace_root=ROOT)
                    res = pm.restore_last_checkpoint()
                    if res.get("success"):
                        print(colorize(f"\n[√ 快照回滚成功] {res.get('message')}\n", C.GREEN))
                    else:
                        print(colorize(f"\n[!] 快照回滚失败: {res.get('message')}\n", C.YELLOW))
                except Exception as e:
                    print(f"回滚失败: {e}")
                continue
            elif cmd.startswith("/memory"):
                parts = user_input.strip().split()
                sub = parts[1].lower() if len(parts) > 1 else "status"
                try:
                    from tools.agent import MemoryManager
                    mem_mgr = MemoryManager(workspace_root=ROOT)
                    if sub in ("status", "health"):
                        health = mem_mgr.get_memory_health()
                        print(colorize("\n=== 🧠 三级分层记忆健康度 ===", C.BOLD))
                        print(f"总 Tokens: {health['total_tokens']} | 字符数: {health['total_chars']}")
                        for scope, info in health.get("details", {}).items():
                            st_color = C.GREEN if info.get('status') == 'ok' else C.YELLOW
                            print(f"  • [{colorize(info.get('status', 'ok'), st_color)}] {scope}: {info.get('tokens', 0)} tokens ({info.get('chars', 0)} chars)")
                        print()
                    elif sub in ("prune", "trim"):
                        target_scope = parts[2] if len(parts) > 2 else "session"
                        res = mem_mgr.prune_memory(scope=target_scope, max_items=50, archive_to_decisions=True)
                        print(colorize(f"\n[√ 记忆修剪] 作用域: {target_scope} | 修剪: {res.get('pruned_count')} 条 | 归档: {res.get('archived_count')} 条\n", C.GREEN))
                except Exception as e:
                    print(f"记忆管理失败: {e}")
                continue
            elif cmd == "/status":
                print_status_summary()
                continue
            else:
                print(colorize(f"未知指令 {cmd}，输入 /skills 查看可用技能，或输入 /math /eng /pol /pro", C.RED))
                continue

        # ── 艾宾浩斯错题盲盒作答判定 ──
        if active_quiz_item and not user_input.startswith("/"):
            if user_input.lower() in ("cancel", "/cancel", "退出", "放弃"):
                active_quiz_item = None
                print(colorize("\n[已退出当前错题盲盒复测]\n", C.YELLOW))
                continue

            print(colorize(f"\n[🎯 考研阅卷人正在对您的盲盒复测作答进行智能核验与采分...]\n", C.CYAN))
            quiz_eval_prompt = (
                f"你是一位考研全真阅卷专家。学员正在对以下历史错题进行【艾宾浩斯盲盒复测】：\n\n"
                f"【题目标题】：{active_quiz_item['title']}\n"
                f"【原题设问与题干】：\n{active_quiz_item['question']}\n\n"
                f"【学员复测提交的作答】：\n{user_input}\n\n"
                f"请按真题采分点严格判定：\n"
                f"1. 核验学员的核心步骤与最终结论是否正确无误？\n"
                f"2. 若完全正确，请在回答首行明确标出：`【复测通过·已掌握】`，并简明指出亮点与关键得分点；\n"
                f"3. 若仍有错误或计算失误，请在回答首行明确标出：`【复测未通过·需强化】`，指出具体第几步失误与错因五分类，并给出正解示范。"
            )
            messages = [
                {"role": "system", "content": "你是一位考研真题阅卷与艾宾浩斯复测主考官。"},
                {"role": "user", "content": quiz_eval_prompt}
            ]
            reply = stream_chat(messages, cfg)
            if reply:
                if "【复测通过·已掌握】" in reply or "复测通过" in reply:
                    ok, msg = error_logger.mark_error_status(
                        active_quiz_item["subject"],
                        active_quiz_item["file_name"],
                        active_quiz_item["title"],
                        new_status="已掌握"
                    )
                    print(colorize(f"\n🎉 [艾宾浩斯系统判定]: {msg} 掌握度已更新，该题已从复测队列出库！\n", C.GREEN))
                else:
                    print(colorize("\n⚠️ [艾宾浩斯系统判定]: 复测仍有失误，已重置艾宾浩斯记忆周期，保持在待测队列！\n", C.YELLOW))
                history.append({"role": "user", "content": f"[错题复测作答: {active_quiz_item['title']}] {user_input}"})
                history.append({"role": "assistant", "content": reply})
                active_quiz_item = None
                print_followup_toolbar()
            continue

        # ── LLM 交互 (Agent Loop 驱动) ──
        append_live_message("user", user_input)
        print(colorize(f"\n[{SUBJECT_DIRS[curr_subj][1]} 正在思考并规划解答...]\n", C.DIM))

        reply = ""
        if agent_runner and cfg.get("api_key"):
            reply = agent_runner.run(user_input, interactive=True)
        else:
            # 降级传统 stream_chat
            sys_prompt = build_system_prompt(curr_subj)
            messages = [{"role": "system", "content": sys_prompt}]
            for h in history[-6:]:  # 保持最近 6 轮
                messages.append(h)
            messages.append({"role": "user", "content": user_input})
            reply = stream_chat(messages, cfg)
            if reply:
                append_live_message("assistant", reply)
                history.append({"role": "user", "content": user_input})
                history.append({"role": "assistant", "content": reply})

            # 如果回复中包含 LaTeX，在终端下方自动打印美化版本并提示 /view
            if latex_beautifier and any(sym in reply for sym in ("\\(", "\\[", "\\int", "\\frac", "\\lim", "\\sum", "$$")):
                beautified = latex_beautifier.prettify_latex_for_terminal(reply)
                print(colorize("\n" + "─" * 58, C.DIM))
                print(colorize(" 📐 【终端数学公式与推导步骤美化视图】", C.BOLD))
                print(colorize("─" * 58, C.DIM))
                print(beautified)
                print(colorize("─" * 58, C.DIM))
                print(colorize(" 💡 提示: 输入 /view 可在浏览器中对照查看印刷级 KaTeX 排版！\n", C.YELLOW))
            else:
                print()

            # Codex CLI 风格快捷操作栏 (Follow-up Toolbar)
            print_followup_toolbar()

# ════════════════════════════════════════════════════════════════
# 6. Webhook 网关模式 (`ky serve --port 8088`)
# ════════════════════════════════════════════════════════════════

def query_llm_reply(user_msg, cfg=None):
    """网关统一调用私教大模型生成详细讲题回复 (支持 Web 伴侣上下文记忆与防 403 拦截)"""
    # 动态加载最新配置，避免使用启动时的陈旧配置
    latest_cfg = load_config()
    if cfg:
        latest_cfg.update({k: v for k, v in cfg.items() if v})
    cfg = latest_cfg

    active_subj = cfg.get("active_subject", "math")
    if "英语" in user_msg or "/eng" in user_msg: active_subj = "eng"
    elif "政治" in user_msg or "/pol" in user_msg: active_subj = "pol"
    elif "专业课" in user_msg or "/pro" in user_msg: active_subj = "pro"
    elif "数学" in user_msg or "/math" in user_msg: active_subj = "math"

    if user_msg.startswith("/calc") or "验算" in user_msg:
        try:
            mv = math_verifier
            if mv is None:
                try:
                    from skills import math_verifier as mv
                except ImportError:
                    from tools.skills import math_verifier as mv
            expr = user_msg.replace("/calc", "").replace("验算", "").strip()
            if expr and mv:
                return mv.run_math_query(expr)
        except Exception:
            pass

    sys_prompt = build_system_prompt(active_subj)
    messages = [{"role": "system", "content": sys_prompt}]

    # 挂载 Web 伴侣最近多轮会话上下文 (提取最近 6 轮对话)，确保“选C”等后续选项或追问能精准关联题目
    import re
    for m in LIVE_SESSION_MESSAGES[-6:]:
        c = m.get("content", "")
        if '<img' in c:
            c = re.sub(r'<img[^>]*>', '[学员手写草稿图片]', c)
        if c.strip():
            messages.append({"role": m.get("role", "user"), "content": c})

    # 避免当前用户消息被重复追加
    if not messages or messages[-1].get("content") != user_msg:
        messages.append({"role": "user", "content": user_msg})

    api_key = cfg.get("api_key", "").strip()
    if not api_key:
        return f"🎓【考研私教】收到提问: \"{user_msg}\"\n⚠️ 尚未配置大模型 API Key，请在电脑端终端运行 `ky config` 设置密钥后即可畅享网页端与群聊对话讲题！"

    base_url = cfg.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
    url = f"{base_url}/chat/completions"
    model = cfg.get("model", "deepseek-chat")

    # 标配浏览器真实 User-Agent 与 Accept 标头，严防云厂商 WAF 将 Python-urllib 拦截为 403 Forbidden
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Kaoyan-Study-Chain/1.0",
        "Accept": "application/json"
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": cfg.get("temperature", 0.3),
        "stream": False
    }

    try:
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            return res_json["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        detail = ""
        try:
            err_json = json.loads(err_body)
            detail = err_json.get("error", {}).get("message") or err_json.get("message") or ""
        except Exception:
            detail = err_body[:200]
        return f"🎓【考研私教解答异常】：模型服务请求失败 (HTTP {e.code}: {e.reason})。\n错误详情: {detail or '服务商拒绝访问，请检查 API Key 余额或权限'}\n建议：请在终端输入 /config 检查模型与密钥配置。"
    except Exception as e:
        return f"🎓【考研私教网络连接异常】: {e}"

def create_gateway_handler(token: str = ""):
    """
    构造网关 HTTP handler。
    - token 非空：对所有非静态端点（live.html 本身、/api/live 等伴随资源外）强制要求
      Authorization: Bearer <token> 或 X-KY-Token 头；token 不匹配返回 401。
    - token 为空：仅放行来自 127.0.0.1 / ::1 的请求，外部 IP 一律 401。
    - 静态端点（live 页面、/api/live、/api/clear、/v1/models）始终允许，方便网页伴侣与本地预览。
    """
    from http.server import BaseHTTPRequestHandler
    cfg = load_config()

    class GatewayHandler(BaseHTTPRequestHandler):
        def _is_authorized(self):
            parsed = urllib.parse.urlparse(self.path)
            # 静态/前端资源始终放行
            if parsed.path in ("/live", "/", "/index.html", "/api/live", "/api/clear", "/v1/models"):
                return True
            if not token:
                return self.client_address[0] in ("127.0.0.1", "::1", "localhost")
            auth_h = self.headers.get("Authorization", "")
            x_tok = self.headers.get("X-KY-Token", "")
            if x_tok and x_tok == token:
                return True
            if auth_h.startswith("Bearer ") and auth_h[7:].strip() == token:
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
            
            # 清空可视化伴侣视图
            if parsed.path == "/api/clear":
                LIVE_SESSION_MESSAGES.clear()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"status":"cleared"}')
                return

            # 来自 Web 伴侣前端的提问 (支持多模态图片批改与草稿手写上传)
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
                # 1. 若前端上传了图片 (拍照/截图/剪贴板Ctrl+V)
                if img_base64:
                    upload_dir = ROOT / "tools" / "scratch" / "uploads"
                    upload_dir.mkdir(parents=True, exist_ok=True)
                    img_filename = f"web_upload_{int(time.time()*1000)}.png"
                    img_path = upload_dir / img_filename
                    try:
                        header_sep = img_base64.find(",")
                        raw_b64 = img_base64[header_sep+1:] if header_sep != -1 else img_base64
                        img_path.write_bytes(base64.b64decode(raw_b64))
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
                        reply = f"【图片解析异常】: {err}"

                    user_display = f'<img src="{img_base64}" class="bubble-uploaded-img" alt="手写草稿" />' + (f'<div>{user_msg}</div>' if user_msg else '')
                    append_live_message("user", user_display)
                    append_live_message("assistant", reply)
                else:
                    # 2. 纯文字提问，使用 query_llm_reply 进行学科路由、技能验算与反幻觉保障
                    append_live_message("user", user_msg)
                    reply = query_llm_reply(user_msg, cfg)
                    append_live_message("assistant", reply)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"reply": reply}, ensure_ascii=False).encode("utf-8"))
                return

            # ── OpenAI 兼容接口 (/v1/chat/completions 供 OpenClaw / WeChat ClawBot 使用) ──
            if parsed.path in ("/v1/chat/completions", "/chat/completions"):
                import time
                # 先初始化 req_data，确保 JSON 解析失败时也不会 NameError
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

# ── 1. 飞书开放平台 URL 校验握手 (url_verification) ──
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

            # A. 钉钉 incoming (含 sessionWebhook 回调)
            if "text" in data and isinstance(data["text"], dict) and "content" in data["text"]:
                user_msg = data["text"]["content"].strip()
                session_webhook = data.get("sessionWebhook")
            # B. 飞书 v2 事件 (im.message.receive_v1)
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
            # C. 企业微信
            elif "Content" in data:
                user_msg = str(data["Content"]).strip()
            # D. QQ OneBot 11
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

            # ── 场景 1: 钉钉 sessionWebhook 异步回传 (彻底解决 5 秒超时) ──
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

                import threading
                threading.Thread(target=dingtalk_bg, args=(user_msg, session_webhook), daemon=True).start()
                return

            # ── 场景 2: 飞书事件订阅 (需 3 秒内返回 200) ──
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

                import threading
                threading.Thread(target=feishu_bg, args=(user_msg,), daemon=True).start()
                return

            # ── 场景 3: QQ OneBot 11 与其他 HTTP 同步应答 ──
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
            return  # 静默请求日志

    return GatewayHandler

def start_background_live_server(start_port=8088, host="127.0.0.1"):
    """
    在后台静默启动 Web 实时伴侣服务器，自动处理端口占用。

    安全默认值：仅绑定 127.0.0.1，避免局域网白嫖 API 额度；
    若调用方需要对外（IM 群机器人从其他机器回调），请显式传入 host="0.0.0.0"
    并配套 KY_GATEWAY_TOKEN。
    """
    from http.server import ThreadingHTTPServer
    import threading
    handler_class = create_gateway_handler()
    bind_host = host
    for p in range(start_port, start_port + 20):
        try:
            httpd = ThreadingHTTPServer((bind_host, p), handler_class)
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            if bind_host not in ("127.0.0.1", "localhost", "::1"):
                print(colorize(
                    f"\n[!] 网关监听于 {bind_host}:{p}（非本机回环）。"
                    f"建议设置环境变量 KY_GATEWAY_TOKEN 启用鉴权，"
                    f"否则 LAN 内任何人都可调用 /v1/chat/completions！\n",
                    C.RED))
            return p
        except OSError:
            continue
    return None

def show_bridge_guide():
    """打印钉钉、飞书、QQ、微信双向对话讲题接入指南"""
    import socket
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    cfg = load_config()
    print(f"""
{C.CYAN}╭────────────────────────────────────────────────────────────────────────╮
│  🤖 考研智能体 · 聊天机器人群聊「双向对话讲题」完整打通指南              │
╰────────────────────────────────────────────────────────────────────────╯{C.RESET}

{C.BOLD}【核心原理科普：为什么之前“可以连接但无法对话”？】{C.RESET}
• {C.YELLOW}单向推送 Webhook{C.RESET} (您之前配置的): 相当于大喇叭，电脑只能往群里“推送”晨报，群里的消息大模型听不到。
• {C.GREEN}双向对话 Webhook{C.RESET} (本网关): 钉钉/飞书/QQ 收到群员提问后，把题目 POST 给考研网关，私教批改完立即在群里回复。

{C.BOLD}【当前网关服务地址】{C.RESET}
  • 本地/同局域网回调地址: {C.GREEN}http://{local_ip}:8088/webhook{C.RESET}
  • 外网穿透参考命令: {C.CYAN}cpolar http 8088{C.RESET} 或 {C.CYAN}cloudflared tunnel --url http://localhost:8088{C.RESET}

────────────────────────────────────────────────────────────────────────
{C.BOLD}📌 0. 微信个人号 (WeChat ClawBot 手机扫码直连，无需公网与穿透):{C.RESET}
  ① 在终端直接运行命令: {C.CYAN}ky clawbot{C.RESET} (或 {C.CYAN}npx -y @tencent-weixin/openclaw-weixin-cli@latest install{C.RESET})
  ② 终端将自动输出微信登录二维码，打开手机微信【扫一扫】授权连接
  ③ 本地考研私教 OpenAI API 地址: {C.GREEN}http://127.0.0.1:8088/v1{C.RESET} (自动挂载全科考纲与解题技能)
  ④ 在个人微信中给机器人发题目，即可随时随地在手机上享受考研私教 1对1 讲题！

────────────────────────────────────────────────────────────────────────
{C.BOLD}📌 1. 钉钉群 (DingTalk) 实现双向讲题:{C.RESET}
  ① 打开钉钉电脑端 ➔ 进入你的考研备考群 ➔ 点击右上角【群设置】➔【智能群助手】
  ② 找到你创建的自定义机器人 ➔ 点击展开设置
  ③ 开启【机器人回调】开关 ➔ 在【POST 地址】中填入:
     {C.GREEN}http://<你的公网IP或穿透域名>/webhook{C.RESET}
  ④ 保存即可！在群里直接输入: {C.YELLOW}@机器人 学数学：请问罗尔定理的核心条件是什么？{C.RESET}
     考研私教会自动识别、步骤采分并推回群聊！(已内置异步通道，绝不超时)

────────────────────────────────────────────────────────────────────────
{C.BOLD}📌 2. 飞书群 (Feishu) 实现双向讲题:{C.RESET}
  ① 打开【飞书开放平台 (open.feishu.cn)】➔ 创建自建企业应用 ➔ 添加【机器人】能力
  ② 在【事件与回调】页面，在【请求网址】填入:
     {C.GREEN}http://<你的公网IP或穿透域名>/webhook{C.RESET}
     (系统已内置 url_verification 握手，飞书会提示“校验成功”)
  ③ 添加事件: 【接收消息 (im.message.receive_v1)】
  ④ 发布应用并在群聊中添加该机器人，在群里 @机器人 即可对话讲题！

────────────────────────────────────────────────────────────────────────
{C.BOLD}📌 3. QQ 群 (NapCat / OneBot 11 本地模式，无需公网 IP):{C.RESET}
  ① 在本地启动 NapCat QQ 机器人 (自带 Web 控制台)
  ② 在网络配置中添加【HTTP 事件上报】，上报地址填: {C.GREEN}http://127.0.0.1:8088/webhook{C.RESET}
  ③ 在 QQ 群里艾特机器人提问，私教直接本地极速秒回！
────────────────────────────────────────────────────────────────────────
""")

def run_server(port=8088, host="127.0.0.1", gateway_token=None):
    """
    启动轻量级 HTTP Webhook 接收网关（ThreadingHTTPServer 支持并发请求）。
    默认仅监听 127.0.0.1；如需对外（IM 群机器人从其他机器回调）请显式传入 host='0.0.0.0'
    并配套 KY_GATEWAY_TOKEN。
    token 优先级: 显式传入 gateway_token (CLI --gateway-token) > 环境变量 KY_GATEWAY_TOKEN > ky_config.json
    """
    from http.server import ThreadingHTTPServer
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

# ════════════════════════════════════════════════════════════════
# 7. 主入口
# ════════════════════════════════════════════════════════════════

def main():
    permission_mode = "ask"
    gateway_host = "127.0.0.1"
    gateway_token = ""  # CLI 显式 --gateway-token 覆盖
    filtered_args = []
    for a in sys.argv[1:]:
        if a.startswith("--permission="):
            permission_mode = a.split("=", 1)[1].strip().lower()
        elif a.startswith("-p="):
            permission_mode = a.split("=", 1)[1].strip().lower()
        elif a.startswith("--host="):
            gateway_host = a.split("=", 1)[1].strip() or "127.0.0.1"
        elif a.startswith("--gateway-token="):
            gateway_token = a.split("=", 1)[1].strip()
        else:
            filtered_args.append(a)

    args = filtered_args
    if not args:
        run_repl(permission_mode=permission_mode, gateway_host=gateway_host, gateway_token=gateway_token)
    elif args[0] in ("view", "--view", "--web", "live"):
        port = start_background_live_server(8088, host=gateway_host) or 8088
        import webbrowser
        webbrowser.open(f"http://localhost:{port}/live")
        print(f"已在默认浏览器打开实时 LaTeX 伴侣: http://localhost:{port}/live")
        run_repl(permission_mode=permission_mode, gateway_host=gateway_host, gateway_token=gateway_token)
    elif args[0] in ("config", "--config"):
        interactive_config()
    elif args[0] in ("plan", "--plan", "profile", "--profile", "onboarding"):
        try:
            import study_planner
            study_planner.run_study_plan_wizard(interactive=True)
        except Exception as e:
            print(f"方案设计提示: {e}")
    elif args[0] in ("today", "--today", "tasks", "--tasks"):
        as_json = "--json" in args or "-j" in args
        print_today_tasks_summary(as_json=as_json)
    elif args[0] in ("done", "--done"):
        if len(args) < 2:
            print(colorize("用法: ky done <任务关键词>\n示例: ky done 导数中值定理", C.YELLOW))
            sys.exit(1)
        kw = " ".join(args[1:])
        ok, msg = mark_today_task_done(kw)
        print(colorize(f"[{msg}]", C.GREEN if ok else C.YELLOW))
        sys.exit(0 if ok else 1)
    elif args[0] in ("review", "--review", "quiz", "--quiz"):
        target_subj = "math"
        if len(args) > 1:
            raw_s = args[1].lower()
            for s_k, s_v in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
                if s_k in raw_s or s_v in raw_s:
                    target_subj = s_k
                    break
        due_items = error_logger.get_due_reviews(target_subj, max_count=5) if error_logger else []
        if not due_items:
            print(colorize(f"\n[🎉 恭喜] {SUBJECT_DIRS.get(target_subj, ('', target_subj))[1]} 当前没有到期需要艾宾浩斯复测的错题！\n", C.GREEN))
        else:
            print(colorize(f"\n=== 📚 {SUBJECT_DIRS.get(target_subj, ('', target_subj))[1]} 艾宾浩斯待复测错题 ({len(due_items)} 道) ===", C.BOLD))
            for i, it in enumerate(due_items, 1):
                print(f"  {i}. [{it.get('date', '')}] {it.get('title', '')} (错因: {it.get('error_type', '未分类')})")
            print("\n💡 提示：在终端运行 python tools/ky_cli.py 启动交互式私教后，输入 /review 即可进入盲盒重测！\n")
    elif args[0] in ("style", "--style"):
        choice = args[1] if len(args) > 1 else None
        if not choice:
            cur_s, _ = manage_coaching_style()
            print(colorize(f"\n=== 🎯 当前激活私教辅导风格 ===", C.BOLD))
            print(f"  • 当前风格: {C.GREEN}{cur_s}{C.RESET}")
            print("\n可选风格清单:")
            for k, (name, desc) in COACHING_STYLES.items():
                active_mark = f" {C.GREEN}[当前激活]{C.RESET}" if name == cur_s else ""
                print(f"  [{k}] {name}{active_mark}\n      {desc}")
            print("\n切换方式: python tools/ky_cli.py style <1/2/3/4>\n")
        else:
            new_style, changed = manage_coaching_style(choice)
            if changed:
                print(colorize(f"\n[√ 辅导风格切换成功] 当前已激活：{new_style}\n", C.GREEN))
            else:
                print(colorize(f"\n[!] 未识别的辅导风格选项: {choice}，请输入 1、2、3、4\n", C.YELLOW))
                sys.exit(1)
    elif args[0] in ("doctor", "--doctor", "check", "--check"):
        try:
            import doctor
            ok = doctor.run_doctor()
            sys.exit(0 if ok else 1)
        except Exception as e:
            print(f"体检执行异常: {e}")
            sys.exit(1)
    elif args[0] in ("notify", "--notify"):
        cfg = load_config()
        custom = " ".join(args[1:]) if len(args) > 1 else None
        broadcast_briefing(cfg, custom_msg=custom)
    elif args[0] in ("subject", "--subject", "syllabus", "--syllabus"):
        cfg = load_config()
        manage_syllabi_cli(cfg)
    elif args[0] in ("exam", "--exam", "compose", "--compose"):
        target_subj = "math"
        count = 3
        save_flag = False
        for a in args[1:]:
            if a.startswith("--count="):
                try: count = int(a.split("=")[1])
                except: pass
            elif a in ("--save", "-s"):
                save_flag = True
            else:
                for sk, sv in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
                    if sk in a.lower() or sv in a:
                        target_subj = sk
                        break
        if exam_composer:
            res = exam_composer.compose_exam_paper(target_subj, count=count, save_file=save_flag)
            print(res.get("formatted_paper", ""))
            if res.get("saved_path"):
                print(colorize(f"\n[√ 试卷已成功保存至]: {res['saved_path']}\n", C.GREEN))
        else:
            print("exam_composer 技能模块未载入")
    elif args[0] in ("exam-submit", "--exam-submit", "grade-paper", "--grade-paper"):
        if len(args) < 3:
            print(colorize("用法: ky exam-submit <试卷文件路径> <作答文本或答案文件>\n示例: ky exam-submit paper_123.json '1. A 2. C 3. B'", C.YELLOW))
            sys.exit(1)
        paper_p = args[1]
        answers = " ".join(args[2:])
        if Path(answers).exists():
            answers = Path(answers).read_text(encoding="utf-8", errors="ignore")
        if exam_composer:
            res = exam_composer.grade_exam_paper(paper_p, answers)
            if res.get("report"):
                print(res["report"])
            elif res.get("success"):
                print(colorize(f"\n=== 🎯 自测整卷批改得分: {res.get('score')} / {res.get('total_score')} (正答率 {res.get('accuracy')}%) ===\n", C.BOLD))
            else:
                print(colorize(f"[!] 批改失败: {res.get('message', '未识别到有效作答')}", C.RED))
        else:
            print("exam_composer 技能模块未载入")
    elif args[0] in ("scout", "--scout"):
        school_name = ""
        major_name = ""
        include_social = True
        save_flag = False
        apply_flag = False

        pos_args = []
        for a in args[1:]:
            if a in ("--no-social", "-ns"):
                include_social = False
            elif a in ("--save", "-s"):
                save_flag = True
            elif a in ("--apply", "-a"):
                apply_flag = True
            elif not a.startswith("-"):
                pos_args.append(a)

        if pos_args:
            school_name = pos_args[0]
            if len(pos_args) > 1:
                major_name = pos_args[1]
        else:
            cfg = load_config()
            school_name = cfg.get("study_plan", {}).get("school", "")
            major_name = cfg.get("study_plan", {}).get("major", "")

        if "--help" in args or "-h" in args or (not school_name or school_name == "目标院校"):
            print(colorize("用法: ky scout <高校名> [专业名] [--no-social] [--save] [--apply]\n示例: ky scout 华中科技大学 计算机 --save\n说明: 定向侦察目标院校研究生院官网招生简章、自命题大纲、拟招人数，并聚合知乎/B站/小红书口碑与避坑指南。", C.YELLOW))
            sys.exit(0 if ("--help" in args or "-h" in args) else 1)

        if school_scout:
            print(colorize(f"\n[🎯 正在启动考研目标院校与社媒情报侦察: 【{school_name}】{major_name}]", C.CYAN))
            print("  • 官方研招检索: 研招网 (yz.chsi.com.cn) + 高校研究生院官网 (.edu.cn)")
            if include_social:
                print("  • 社交舆情聚合: 知乎就读体验 + 哔哩哔哩备考贴 + 小红书避坑与压分")
            print("  • 正在提取核心指标与生成情报研报...\n")

            res = school_scout.scout_school(
                school=school_name,
                major=major_name,
                include_social=include_social,
                save_report=save_flag,
                apply_to_config=apply_flag,
                use_llm=True
            )
            print(res.get("formatted_report", ""))

            if res.get("saved_path"):
                print(colorize(f"\n[√ 情报研报已成功落盘至]: {res['saved_path']}", C.GREEN))
            if res.get("applied"):
                print(colorize(f"[√ 目标高校与专业已一键同步至 ky_config.json]", C.GREEN))
            print()
        else:
            print("school_scout 技能模块未载入")
    elif args[0] in ("variant", "--variant"):
        if len(args) < 2:
            print(colorize("用法: ky variant <考点关键词或原题干>\n示例: ky variant 导数中值定理", C.YELLOW))
            sys.exit(1)
        topic = " ".join(args[1:])
        if variant_retriever:
            cfg = load_config()
            res = variant_retriever.search_real_variant(subject=cfg.get("active_subject", "math"), keyword=topic)
            print(variant_retriever.format_variant_output(res))
        else:
            print("variant_retriever 技能模块未载入")
    elif args[0] in ("map", "--map", "knowledge", "--knowledge"):
        target_subj = "math"
        as_json = "--json" in args or "-j" in args
        for a in args[1:]:
            if a in ("--json", "-j"):
                continue
            for sk, sv in (("math", "数"), ("eng", "英"), ("pol", "政"), ("pro", "专")):
                if sk in a.lower() or sv in a:
                    target_subj = sk
                    break
        if knowledge_map:
            if as_json:
                data = knowledge_map.build_knowledge_map(target_subj)
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(knowledge_map.format_knowledge_map_table(target_subj))
        else:
            print("knowledge_map 技能模块未载入")
    elif args[0] in ("diagnose", "--diagnose"):
        if len(args) < 2:
            print(colorize("用法: ky diagnose <模考答题卡文本或文件路径>\n示例: ky diagnose 模考记录.txt 或 ky diagnose '1-5: A B C D A'", C.YELLOW))
            sys.exit(1)
        raw_target = " ".join(args[1:])
        content = raw_target
        if Path(raw_target).exists():
            content = Path(raw_target).read_text(encoding="utf-8", errors="ignore")
        if exam_diagnoser:
            cfg = load_config()
            res = exam_diagnoser.diagnose_mock_exam(subject=cfg.get("active_subject", "math"), exam_input=content)
            print(exam_diagnoser.format_diagnosis_report(res))
        else:
            print("exam_diagnoser 技能模块未载入")
    elif args[0] in ("fatigue", "--fatigue"):
        try:
            import study_planner
            info = study_planner.check_fatigue_alert()
            if info.get("alert"):
                print(colorize(f"\n[⚠️ 疲劳警报触发] 连续 {info.get('consecutive_low_days')} 天低完成率 (均值 {info.get('avg_rate')}%):", C.YELLOW))
                print(info.get("message"))
                print(colorize("\n💡 提示：输入 ky relieve 可立即一键启动减负模式。\n", C.CYAN))
            else:
                print(colorize(f"\n[√ 复习节奏正常] {info.get('message')}\n", C.GREEN))
        except Exception as e:
            print(f"检查疲劳异常: {e}")
    elif args[0] in ("relieve", "--relieve"):
        try:
            import study_planner
            res = study_planner.apply_relief_mode()
            if res.get("success"):
                print(colorize(f"\n[√ 减负模式已成功启动]", C.GREEN))
                print(f"  • 每日复习总时间: {res.get('old_hours')}h ➔ {C.BOLD}{res.get('new_hours')}h{C.RESET}")
                print(f"  • 辅导风格切换为: {C.GREEN}{res.get('style')}{C.RESET}")
                print(f"  • 说明: {res.get('message')}\n")
            else:
                print(colorize(f"[!] 启动减负失败: {res.get('message')}", C.RED))
        except Exception as e:
            print(f"启动减负异常: {e}")
    elif args[0] in ("clawbot", "--clawbot", "wechat", "--wechat", "wx"):
        run_wechat_clawbot_install()
    elif args[0] in ("bridge", "--bridge", "tunnel", "--tunnel"):
        show_bridge_guide()
    elif args[0] in ("serve", "--serve"):
        port = 8088
        if len(args) > 1 and args[1].isdigit():
            port = int(args[1])
        # serve 接受附加参数 --host=0.0.0.0
        if len(args) > 1 and args[-1].startswith("--host="):
            gateway_host = args[-1].split("=", 1)[1].strip() or gateway_host
        # 显式传递 CLI 解析出的 gateway_token（此前被丢弃，导致 --gateway-token 不生效）
        run_server(port=port, host=gateway_host, gateway_token=gateway_token)
    elif args[0] in ("status", "--status"):
        print_status_summary()
    elif args[0] in ("memory", "--memory"):
        sub = args[1].lower() if len(args) > 1 else "status"
        try:
            from tools.agent import MemoryManager
            mem_mgr = MemoryManager(workspace_root=ROOT)
            if sub in ("status", "health", "check"):
                health = mem_mgr.get_memory_health()
                print(colorize("\n=== 🧠 三级分层记忆健康度诊断 ===", C.BOLD))
                print(f"总容量消耗: {health['total_tokens']} tokens ({health['total_chars']} 字符)")
                print("-" * 55)
                print(f"{'记忆层级':<12} {'文件路径':<20} {'字符数':<8} {'Tokens':<8} {'健康状态'}")
                print("-" * 55)
                for scope, info in health.get("details", {}).items():
                    st = info.get("status", "ok")
                    st_color = C.GREEN if st == "ok" else (C.YELLOW if st == "warning" else C.RED)
                    p_rel = Path(info.get("path", "")).relative_to(ROOT) if info.get("path") else "-"
                    print(f"{scope:<12} {str(p_rel):<20} {info.get('chars', 0):<8} {info.get('tokens', 0):<8} {colorize(st, st_color)}")
                print("-" * 55)
                print("💡 提示：若某一记忆层膨胀过大，可运行 ky memory prune 进行滚动修剪与决策归档。\n")
            elif sub in ("prune", "trim", "clean"):
                target_scope = args[2] if len(args) > 2 else "session"
                res = mem_mgr.prune_memory(scope=target_scope, max_items=50, archive_to_decisions=True)
                if res.get("pruned"):
                    print(colorize(f"\n[√ 记忆修剪完成] 作用域: {target_scope}", C.GREEN))
                    print(f"  • 修剪条目: {res.get('pruned_count')} 条")
                    print(f"  • 归档至决策库: {res.get('archived_count')} 条")
                    print(f"  • 剩余条目: {res.get('remaining_count')} 条\n")
                else:
                    print(colorize(f"\n[i] 记忆条目未超限 ({res.get('total_items', 0)} 条)，无需修剪。\n", C.CYAN))
            else:
                print(colorize(f"未知 memory 子命令: {sub}，支持 status / prune", C.YELLOW))
        except Exception as e:
            print(f"记忆管理执行失败: {e}")
    elif args[0] in ("rollback", "--rollback", "restore", "--restore"):
        try:
            from tools.agent import PermissionManager
            pm = PermissionManager(workspace_root=ROOT)
            res = pm.restore_last_checkpoint()
            if res.get("success"):
                print(colorize(f"\n[√ 快照回滚成功] {res.get('message')}\n", C.GREEN))
            else:
                print(colorize(f"\n[!] 快照回滚失败: {res.get('message')}\n", C.YELLOW))
        except Exception as e:
            print(f"回滚执行失败: {e}")
    elif args[0] in ("build", "--build"):
        build_py = ROOT / "05-考研看板" / "build.py"
        if build_py.exists():
            import subprocess
            subprocess.run([sys.executable, str(build_py)], cwd=str(ROOT / "05-考研看板"))
    elif args[0] in ("help", "--help", "-h"):
        print(f"""
考研学习链专用终端工具 (ky-cli)
用法：
  python tools/ky_cli.py                       启动交互式 Agent 私教终端 (默认 --permission=ask)
  python tools/ky_cli.py --permission=plan    计划模式 (写操作前出具变更计划卡片并创建快照备份)
  python tools/ky_cli.py --permission=auto    全自动沙箱模式 (免交互确认)
  python tools/ky_cli.py --permission=safe    严格只读安全模式 (禁止文件写入与执行)
  python tools/ky_cli.py --host=0.0.0.0        网关对外暴露（需配合 KY_GATEWAY_TOKEN）
  python tools/ky_cli.py --gateway-token=xxx   显式传入网关 token

子命令：
  status                                      查看考研总战役大盘态势、倒计时、打卡天数与作息节律
  memory [status|prune]                       三级分层记忆健康度诊断与滚动修剪归档
  rollback                                    快速回滚 Plan Mode 写入前备份的最近一次文件快照
  today [--json]                              查看今日四科任务清单；加 --json 输出结构化数据
  done <关键词>                               快速将包含关键词的今日任务标记为完成并回写状态
  review [math|eng|pol|pro]                   查看艾宾浩斯待复测错题列表
  style [1/2/3/4]                             查看或动态切换 4 种私教辅导风格
  doctor                                      一键系统健康诊断 (Python环境/依赖/状态/连通性)
  plan                                        启动个人专属定制化必考方案向导
  scout <高校名> [专业名] [--save] [--apply]  定向侦察目标高校招生简章、考试大纲、报录比与知乎/B站口碑
  exam [科目] [--count=N] [--save]            基于错题库与核心考点反向靶向组卷
  exam-submit <试卷路径> <作答文本>           自动判卷并输出正答率、采分点与错题归因
  variant <考点关键词>                        四科白名单同类真题变式检索与防幻觉溯源
  map [科目] [--json]                         官方考试大纲知识点图谱与掌握度映射
  diagnose <答题卡文本或文件>                 整卷级多题诊断引擎 (章节失分排行与薄弱处方)
  fatigue                                     检查疲劳度与完成率监控警报
  relieve                                     一键启动智能减负模式 (任务下调 25%，切换为鼓励型)
  notify [内容]                               一键推送今日任务/晨报到微信、QQ、钉钉、飞书群
  build                                       一键重新编译并刷新本地与移动端看板
  subject                                     选择考研科目(数一/二/三/396、英一/二)并加载考纲
  config                                      配置大模型 API Key、视觉模型与机器人 Webhook
  serve [port]                                启动 Webhook 网关与实时 Web 伴侣
  clawbot                                     启动微信个人号 ClawBot 扫码连接器
  bridge                                      查看各平台双向讲题网关接入指南
""")
    else:
        print(f"未知参数: {args[0]}，运行 python tools/ky_cli.py --help 查看帮助。")
        sys.exit(1)

if __name__ == "__main__":
    main()
