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
from pathlib import Path
from datetime import datetime

# Windows 控制台安全编码
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "ky_config.json"
HISTORY_FILE = ROOT / "ky_history.json"

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
        "User-Agent": "Kaoyan-Study-Chain-CLI/1.0"
    }

    payload = {
        "model": model,
        "messages": messages,
        "temperature": config.get("temperature", 0.3),
        "stream": True
    }

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

    full_reply = []
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
                            sys.stdout.write(content)
                            sys.stdout.flush()
                            full_reply.append(content)
                except Exception:
                    continue
        print()  # 换行
        return "".join(full_reply)
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        print(colorize(f"\n[API 错误 {e.code}]: {err_msg}\n", C.RED))
        return ""
    except Exception as e:
        print(colorize(f"\n[网络连接异常]: {e}\n", C.RED))
        return ""

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
# 4. 交互式配置向导 (/config)
# ════════════════════════════════════════════════════════════════

def interactive_config():
    cfg = load_config()
    print(colorize("\n=== ⚙️ 考研私教 CLI 配置管理 ===", C.BOLD))
    print("支持接入各大主流大模型 API 与常用即时通讯聊天机器人。\n")

    print(colorize("--- 1. 模型 API 配置 ---", C.CYAN))
    print("推荐 API 服务商：")
    print("  [1] DeepSeek (api.deepseek.com) · 极力推荐 (V3/R1 理工科解题首选)")
    print("  [2] 智谱清言 GLM (open.bigmodel.cn)")
    print("  [3] 阿里云百炼 Qwen (dashscope.aliyuncs.com)")
    print("  [4] 月之暗面 Kimi (api.moonshot.cn)")
    print("  [5] 本地 Ollama (http://localhost:11434/v1)")
    print("  [6] 自定义 OpenAI 兼容接口 / SiliconFlow / OpenAI\n")

    p_choice = input(f"选择服务商类型 (直接回车保持现有: {cfg['api_provider']}): ").strip()
    if p_choice == "1":
        cfg["api_provider"] = "deepseek"
        cfg["base_url"] = "https://api.deepseek.com/v1"
        cfg["model"] = "deepseek-chat"
    elif p_choice == "2":
        cfg["api_provider"] = "glm"
        cfg["base_url"] = "https://open.bigmodel.cn/api/paas/v4"
        cfg["model"] = "glm-4-plus"
    elif p_choice == "3":
        cfg["api_provider"] = "qwen"
        cfg["base_url"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        cfg["model"] = "qwen-plus"
    elif p_choice == "4":
        cfg["api_provider"] = "kimi"
        cfg["base_url"] = "https://api.moonshot.cn/v1"
        cfg["model"] = "moonshot-v1-32k"
    elif p_choice == "5":
        cfg["api_provider"] = "ollama"
        cfg["base_url"] = "http://localhost:11434/v1"
        cfg["model"] = "deepseek-r1:14b"

    new_url = input(f"Base URL [{cfg['base_url']}]: ").strip()
    if new_url:
        cfg["base_url"] = new_url

    new_model = input(f"Model 名称 [{cfg['model']}]: ").strip()
    if new_model:
        cfg["model"] = new_model

    curr_key_display = cfg['api_key'][:6] + "..." if len(cfg['api_key']) > 8 else (cfg['api_key'] or "未设置")
    new_key = input(f"API Key [{curr_key_display}]: ").strip()
    if new_key:
        cfg["api_key"] = new_key

    print(colorize("\n--- 2. 聊天机器人 Webhook 配置 (可选 · 留空跳过) ---", C.CYAN))
    hooks = cfg.setdefault("webhooks", {})
    
    wc = input(f"微信 / 企微 Webhook URL [{hooks.get('wechat','')}]: ").strip()
    if wc != "": hooks["wechat"] = wc
    
    dt = input(f"钉钉 Webhook URL [{hooks.get('dingtalk','')}]: ").strip()
    if dt != "": hooks["dingtalk"] = dt
    if hooks.get("dingtalk"):
        sec = input(f"钉钉加签 Secret [{hooks.get('dingtalk_secret','')}]: ").strip()
        if sec != "": hooks["dingtalk_secret"] = sec
        
    fs = input(f"飞书 Webhook URL [{hooks.get('feishu','')}]: ").strip()
    if fs != "": hooks["feishu"] = fs

    qq = input(f"QQ OneBot HTTP 接口 (如 http://127.0.0.1:3000) [{hooks.get('qq_onebot','')}]: ").strip()
    if qq != "": hooks["qq_onebot"] = qq
    if hooks.get("qq_onebot"):
        qid = input(f"QQ 目标群号/好友号 [{hooks.get('qq_target_id','')}]: ").strip()
        if qid != "": hooks["qq_target_id"] = qid

    save_config(cfg)
    print(colorize("\n[√] 配置已安全保存至 ky_config.json（已加入 .gitignore，绝不会上传 GitHub）！\n", C.GREEN))

# ════════════════════════════════════════════════════════════════
# 5. 交互式 TUI 主界面 (类似 Claude Code)
# ════════════════════════════════════════════════════════════════

def print_welcome():
    banner = f"""
{C.CYAN}{C.BOLD}
  ██╗  ██╗ █████╗  ██████╗ ██╗   ██╗ █████╗ ███╗   ██╗     ██████╗██╗     ██╗
  ██║ ██╔╝██╔══██╗██╔═══██╗╚██╗ ██╔╝██╔══██╗████╗  ██║    ██╔════╝██║     ██║
  █████═╝ ███████║██║   ██║ ╚████╔╝ ███████║██╔██╗ ██║    ██║     ██║     ██║
  ██╔═██╗ ██╔══██║██║   ██║  ╚██╔╝  ██╔══██║██║╚██╗██║    ██║     ██║     ██║
  ██║ ╚██╗██║  ██║╚██████╔╝   ██║   ██║  ██║██║ ╚████║    ╚██████╗███████╗██║
  ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝    ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═══╝     ╚═════╝╚══════╝╚═╝
{C.RESET}
{C.BOLD}  🎓 考研全科 AI 专属私教终端 · Kaoyan Study CLI v1.0{C.RESET}
  ----------------------------------------------------------------------
  快捷指令：
   {C.GREEN}/math{C.RESET} 切换数学私教    {C.GREEN}/eng{C.RESET} 切换英语私教    {C.GREEN}/pol{C.RESET} 切换政治私教    {C.GREEN}/pro{C.RESET} 切换专业课
   {C.GREEN}/status{C.RESET} 今日进度大盘  {C.GREEN}/notify{C.RESET} 推送晨报到群   {C.GREEN}/build{C.RESET} 重新编译看板    {C.GREEN}/config{C.RESET} 接口设置
   {C.GREEN}/clear{C.RESET} 清空当前对话   {C.GREEN}/exit{C.RESET} 退出终端
  ----------------------------------------------------------------------
"""
    print(banner)

def run_repl():
    cfg = load_config()
    print_welcome()

    if not cfg.get("api_key"):
        print(colorize("[!] 检测到尚未配置大模型 API Key！", C.YELLOW))
        init_ask = input("是否立即配置 API Key? (y/n) [y]: ").strip().lower()
        if init_ask != "n":
            interactive_config()
            cfg = load_config()

    curr_subj = cfg.get("active_subject", "math")
    history = []

    def get_prompt_tag():
        _, s_name = SUBJECT_DIRS.get(curr_subj, ("01-数学", "数学"))
        return f"{C.CYAN}[ky-cli:{s_name}]{C.RESET} > "

    print(colorize(f"当前已激活：{SUBJECT_DIRS[curr_subj][1]}。直接输入你的问题、解题草稿，或输入 /math, /eng 切换科目。\n", C.DIM))

    while True:
        try:
            user_input = input(get_prompt_tag()).strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见！保持节奏，一战成硕！🎓")
            break

        if not user_input:
            continue

        # ── 斜杠命令处理 ──
        if user_input.startswith("/"):
            cmd = user_input.lower().split()[0]
            if cmd in ("/exit", "/quit"):
                print("再见！一战成硕！")
                break
            elif cmd in ("/math", "/shuxue"):
                curr_subj = "math"
                cfg["active_subject"] = "math"
                save_config(cfg)
                history = []
                print(colorize(f"\n[已切换至：{SUBJECT_DIRS['math'][1]}] 上下文与状态已重载。\n", C.GREEN))
                continue
            elif cmd in ("/eng", "/yingyu"):
                curr_subj = "eng"
                cfg["active_subject"] = "eng"
                save_config(cfg)
                history = []
                print(colorize(f"\n[已切换至：{SUBJECT_DIRS['eng'][1]}] 上下文与状态已重载。\n", C.GREEN))
                continue
            elif cmd in ("/pol", "/zhengzhi"):
                curr_subj = "pol"
                cfg["active_subject"] = "pol"
                save_config(cfg)
                history = []
                print(colorize(f"\n[已切换至：{SUBJECT_DIRS['pol'][1]}] 上下文与状态已重载。\n", C.GREEN))
                continue
            elif cmd in ("/pro", "/zhuanye"):
                curr_subj = "pro"
                cfg["active_subject"] = "pro"
                save_config(cfg)
                history = []
                print(colorize(f"\n[已切换至：{SUBJECT_DIRS['pro'][1]}] 上下文与状态已重载。\n", C.GREEN))
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
            elif cmd == "/status":
                print(colorize(f"\n--- 考研大盘概况 ({datetime.now().strftime('%Y-%m-%d')}) ---", C.BOLD))
                agents_root = ROOT / "AGENTS.md"
                if agents_root.exists():
                    txt = read_text_safe(agents_root)
                    for line in txt.split("\n"):
                        if line.startswith("- **") or line.startswith("| **科目"):
                            print("  " + line)
                print()
                continue
            else:
                print(colorize(f"未知指令 {cmd}，支持: /math /eng /pol /pro /status /notify /build /config /clear /exit", C.RED))
                continue

        # ── LLM 交互 ──
        sys_prompt = build_system_prompt(curr_subj)
        messages = [{"role": "system", "content": sys_prompt}]
        for h in history[-6:]:  # 保持最近 6 轮
            messages.append(h)
        messages.append({"role": "user", "content": user_input})

        print(colorize(f"\n[{SUBJECT_DIRS[curr_subj][1]} 正在思考并按评分标准批改...]\n", C.DIM))
        reply = stream_chat(messages, cfg)
        if reply:
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": reply})
            print()

# ════════════════════════════════════════════════════════════════
# 6. Webhook 网关模式 (`ky serve --port 8088`)
# ════════════════════════════════════════════════════════════════

def run_server(port=8088):
    """启动轻量级 HTTP Webhook 接收网关，实现微信/QQ/钉钉双向收发"""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    cfg = load_config()

    print(colorize(f"\n[🚀 考研智能体 Webhook 网关正在启动... 监听端口: {port}]", C.BOLD))
    print(f"  - 钉钉/企业微信回调地址: http://<你的公网IP或内网穿透域名>:{port}/webhook")
    print(f"  - 当前默认学科: {SUBJECT_DIRS[cfg.get('active_subject','math')][1]}")
    print("  - 支持接收群聊提问并自动回复，按 Ctrl+C 停止服务。\n")

    class GatewayHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(content_length).decode("utf-8", errors="ignore")
            
            user_msg = ""
            try:
                data = json.loads(post_data)
                # 兼容钉钉 incoming
                if "text" in data and "content" in data["text"]:
                    user_msg = data["text"]["content"].strip()
                # 兼容飞书
                elif "event" in data and "text" in data["event"]:
                    user_msg = data["event"]["text"].strip()
                # 兼容企业微信
                elif "Content" in data:
                    user_msg = data["Content"].strip()
                # 兼容 QQ OneBot
                elif "raw_message" in data:
                    user_msg = data["raw_message"].strip()
            except Exception:
                user_msg = post_data.strip()

            if not user_msg:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
                return

            print(colorize(f"[Webhook 收到消息]: {user_msg}", C.CYAN))
            
            # 判断学科路由
            active_subj = "math"
            if "英语" in user_msg: active_subj = "eng"
            elif "政治" in user_msg: active_subj = "pol"
            elif "专业课" in user_msg: active_subj = "pro"

            sys_prompt = build_system_prompt(active_subj)
            messages = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_msg}
            ]

            # 发起非流式请求以返回 JSON
            reply = "已收到！正在为您解析..."
            try:
                base_url = cfg.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
                url = f"{base_url}/chat/completions"
                req = urllib.request.Request(
                    url,
                    data=json.dumps({"model": cfg.get("model", "deepseek-chat"), "messages": messages, "stream": False}).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg.get('api_key','')}"}
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    reply = res_json["choices"][0]["message"]["content"]
            except Exception as e:
                reply = f"[私教处理异常]: {e}"

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            
            # 返回给调用方
            resp_body = json.dumps({"msgtype": "text", "text": {"content": reply}}, ensure_ascii=False)
            self.wfile.write(resp_body.encode("utf-8"))

        def log_message(self, format, *args):
            return  # 静默请求日志

    httpd = HTTPServer(("0.0.0.0", port), GatewayHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n网关服务已平稳停止。")

# ════════════════════════════════════════════════════════════════
# 7. 主入口
# ════════════════════════════════════════════════════════════════

def main():
    args = sys.argv[1:]
    if not args:
        run_repl()
    elif args[0] in ("config", "--config"):
        interactive_config()
    elif args[0] in ("notify", "--notify"):
        cfg = load_config()
        custom = " ".join(args[1:]) if len(args) > 1 else None
        broadcast_briefing(cfg, custom_msg=custom)
    elif args[0] in ("serve", "--serve"):
        port = 8088
        if len(args) > 1 and args[1].isdigit():
            port = int(args[1])
        run_server(port=port)
    elif args[0] in ("build", "--build"):
        build_py = ROOT / "05-考研看板" / "build.py"
        if build_py.exists():
            import subprocess
            subprocess.run([sys.executable, str(build_py)], cwd=str(ROOT / "05-考研看板"))
    elif args[0] in ("help", "--help", "-h"):
        print(f"""
考研学习链专用终端工具 (ky-cli)
用法：
  python tools/ky_cli.py              启动类似 Claude Code 的交互式终端私教 (默认)
  python tools/ky_cli.py notify [内容] 一键推送今日任务/晨报到微信、QQ、钉钉、飞书群
  python tools/ky_cli.py serve [端口] 启动双向 Webhook 网关 (默认 8088 端口)
  python tools/ky_cli.py config       配置大模型 API Key 与聊天机器人 Webhook
  python tools/ky_cli.py build        一键重新编译并刷新本地与移动端看板
""")
    else:
        print(f"未知参数: {args[0]}，运行 python tools/ky_cli.py --help 查看帮助。")

if __name__ == "__main__":
    main()
