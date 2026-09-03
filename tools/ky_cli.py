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

# 引入考研专用 Skills 体系
tools_dir = Path(__file__).resolve().parent
if str(tools_dir) not in sys.path:
    sys.path.insert(0, str(tools_dir))

try:
    from skills import vision_solver, math_verifier, english_dissector, pdf_extractor, error_logger, latex_beautifier, list_skills
except Exception as _e:
    try:
        import skills
        from skills import vision_solver, math_verifier, english_dissector, pdf_extractor, error_logger, latex_beautifier, list_skills
    except Exception:
        list_skills = lambda: {}
        vision_solver = None
        math_verifier = None
        english_dissector = None
        pdf_extractor = None
        error_logger = None
        latex_beautifier = None

# Web 可视化伴侣会话缓存
LIVE_SESSION_MESSAGES = []

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
# 4. 交互式配置管理中心 (/config)
# ════════════════════════════════════════════════════════════════

def configure_llm(cfg):
    """配置大模型 API 服务商与密钥"""
    print(colorize("\n--- 🧠 1. 大模型 API 服务商与密钥配置 ---", C.CYAN))
    print("支持接入各大主流大模型 API：")
    print("  [1] DeepSeek (api.deepseek.com) · 极力推荐 (V3/R1 理工科解题首选)")
    print("  [2] 智谱清言 GLM (open.bigmodel.cn)")
    print("  [3] 阿里云百炼 Qwen (dashscope.aliyuncs.com)")
    print("  [4] 月之暗面 Kimi (api.moonshot.cn)")
    print("  [5] 本地 Ollama (http://localhost:11434/v1)")
    print("  [6] 自定义 OpenAI 兼容接口 / SiliconFlow / 豆包 等\n")

    p_choice = input(f"选择服务商 (1~6，直接回车保持现有: {cfg.get('api_provider','deepseek')}): ").strip()
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
    elif p_choice == "6":
        cfg["api_provider"] = "custom"

    new_url = input(f"Base URL (直接回车保持现有: {cfg['base_url']}): ").strip()
    if new_url:
        cfg["base_url"] = new_url

    new_model = input(f"Model 模型代号 (直接回车保持现有: {cfg['model']}): ").strip()
    if new_model:
        cfg["model"] = new_model

    curr_key_display = cfg['api_key'][:6] + "..." if len(cfg.get('api_key','')) > 8 else (cfg.get('api_key','') or "未设置")
    new_key = input(f"API Key (输入新密钥或直接回车保持现有: {curr_key_display}): ").strip()
    if new_key:
        cfg["api_key"] = new_key

    save_config(cfg)
    print(colorize("[√] 模型 API 配置已更新！", C.GREEN))

def configure_webhooks(cfg):
    """多选菜单式配置各个聊天机器人 Webhook"""
    hooks = cfg.setdefault("webhooks", {})

    while True:
        wc_tag = colorize("已配置", C.GREEN) if hooks.get("wechat") else colorize("未配置", C.DIM)
        dt_tag = colorize("已配置", C.GREEN) if hooks.get("dingtalk") else colorize("未配置", C.DIM)
        fs_tag = colorize("已配置", C.GREEN) if hooks.get("feishu") else colorize("未配置", C.DIM)
        qq_tag = colorize("已配置", C.GREEN) if hooks.get("qq_onebot") else colorize("未配置", C.DIM)

        print(colorize("\n--- 📱 2. 聊天机器人 Webhook / 消息推送配置 ---", C.CYAN))
        print("请直接选择您想配置或修改的机器人平台：")
        print(f"  [1] 微信 / 企业微信 / 微信助手 ClawBot  [{wc_tag}]")
        print(f"  [2] 钉钉群自定义机器人 (DingTalk)         [{dt_tag}]")
        print(f"  [3] 飞书群自定义机器人 (Feishu)           [{fs_tag}]")
        print(f"  [4] QQ 机器人 (OneBot 11 / NapCat)        [{qq_tag}]")
        print(f"  [5] 📢 发送一条测试消息验证所有已配机器人")
        print(f"  [6] 🗑️ 清空某个平台的配置")
        print(f"  [0] 💾 保存并返回上级菜单")

        choice = input("\n请选择平台编号 (0~6) [默认 0]: ").strip() or "0"
        
        if choice == "0":
            save_config(cfg)
            print(colorize("[√] 机器人 Webhook 配置已安全保存！", C.GREEN))
            break
        elif choice == "1":
            print(colorize("\n[配置 微信 / 企业微信 / ClawBot Webhook]", C.BOLD))
            print("说明：适用于企业微信群机器人或微信 ClawBot 助手。")
            print("地址格式如：https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxx")
            curr = hooks.get("wechat", "")
            val = input(f"请输入 Webhook URL (直接回车保持现有: {curr or '空'}): ").strip()
            if val:
                hooks["wechat"] = val
            save_config(cfg)
            if hooks.get("wechat"):
                t = input("是否立即向该微信机器人发送测试消息? (y/n) [y]: ").strip().lower()
                if t != "n":
                    ok, res = send_to_wechat(hooks["wechat"], "🎓【考研学习链】微信机器人连接成功！每日任务与晨报将在此推送。")
                    print(colorize(f"  -> 发送成功！", C.GREEN) if ok else colorize(f"  -> 发送失败: {res}", C.RED))
        elif choice == "2":
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
        elif choice == "3":
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
        elif choice == "4":
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
        elif choice == "5":
            broadcast_briefing(cfg, custom_msg="🎓【考研学习链】这是一条自检广播测试消息，您的机器人连接状态正常！")
        elif choice == "6":
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
    print(f"\n配置文件绝对路径: {CONFIG_FILE}")
    print("本文件已被 .gitignore 严密保护，绝不会被 Git 追踪提交。\n")

def interactive_config():
    """配置管理中心主路由"""
    cfg = load_config()
    while True:
        curr_p = cfg.get("api_provider", "deepseek")
        curr_m = cfg.get("model", "deepseek-chat")
        has_key = bool(cfg.get("api_key"))
        key_tag = colorize("已设置", C.GREEN) if has_key else colorize("未设置", C.RED)
        
        hooks = cfg.get("webhooks", {})
        active_hooks = [k for k, v in hooks.items() if v and not k.endswith("_secret") and not k.endswith("_id")]
        hooks_tag = colorize(f"已配 {len(active_hooks)} 个 ({', '.join(active_hooks)})", C.GREEN) if active_hooks else colorize("未配任何平台", C.DIM)

        print(colorize("\n=== ⚙️ 考研私教 CLI 配置管理中心 ===", C.BOLD))
        print(f"  [1] 🧠 配置大模型 API 与密钥     [当前: {curr_p} / {curr_m} / {key_tag}]")
        print(f"  [2] 📱 配置聊天机器人 Webhook 推送 [当前: {hooks_tag}]")
        print(f"  [3] 📄 查看当前完整配置清单")
        print(f"  [4] 📢 一键测试所有机器人推送")
        print(f"  [0] 💾 完成配置并返回")

        choice = input("\n请选择功能 (0~4) [默认 0]: ").strip() or "0"
        if choice == "0":
            save_config(cfg)
            print(colorize("\n[√] 配置已安全保存至 ky_config.json！\n", C.GREEN))
            break
        elif choice == "1":
            configure_llm(cfg)
        elif choice == "2":
            configure_webhooks(cfg)
        elif choice == "3":
            show_config(cfg)
        elif choice == "4":
            broadcast_briefing(cfg, custom_msg="🎓【考研学习链】这是一条自检测试广播消息，您的机器人连接状态正常！")

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
{C.BOLD}  🎓 考研全科 AI 专属私教终端 · Kaoyan Study CLI v1.2 (Skills 全功能版){C.RESET}
  ----------------------------------------------------------------------
  四科私教调度：
   {C.GREEN}/math{C.RESET} 数学专属私教    {C.GREEN}/eng{C.RESET} 英语专属私教    {C.GREEN}/pol{C.RESET} 政治专属私教    {C.GREEN}/pro{C.RESET} 专业课私教
  考研专有技能 (Skills)：
   {C.YELLOW}/img <图片路径>{C.RESET}   📸 多模态视觉批改：识别题干、手写推导、采分点打分与公式还原
   {C.YELLOW}/calc <数学表达式>{C.RESET} 📐 符号验算引擎：求导、极限、积分、矩阵与泰勒级数严谨验算
   {C.YELLOW}/dissect <英语句子>{C.RESET} 🧱 英语长难句搭积木解剖：主干抽取/从句解构/高频词/润色翻译
   {C.YELLOW}/pdf [关键词]{C.RESET}      📚 资料库与历年真题教材全文检索
   {C.YELLOW}/skills{C.RESET}            🧩 查看当前已装载的所有技能及其就绪状态
  大盘与前端联动：
   {C.CYAN}/view{C.RESET}   🌐 打开实时可视化网页伴侣 (印刷级 KaTeX 排版对照)
   {C.CYAN}/status{C.RESET} 今日大盘态势    {C.CYAN}/notify{C.RESET} 推送晨报到群   {C.CYAN}/build{C.RESET} 编译移动端看板
   {C.CYAN}/config{C.RESET} 配置中心        {C.CYAN}/clear{C.RESET}  清空当前对话   {C.CYAN}/exit{C.RESET}   退出终端私教
  ----------------------------------------------------------------------
"""
    print(banner)

def run_repl():
    cfg = load_config()
    print_welcome()

    # 静默启动后台实时 Web 可视化伴侣
    live_port = start_background_live_server(8088)

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

    print(colorize(f"当前已激活：{SUBJECT_DIRS[curr_subj][1]}。直接输入问题/题目，或使用 /img 批改草稿，/calc 验算数学。", C.DIM))
    if live_port:
        print(colorize(f"🌐 [实时 LaTeX 网页伴侣已就绪]: http://localhost:{live_port}/live (随时输入 /view 自动打开浏览器对照排版)\n", C.CYAN))

    while True:
        try:
            user_input = input(get_prompt_tag()).strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见！保持节奏，一战成硕！🎓")
            break

        if not user_input:
            continue

        # ── 智能图片输入检测 (直接输入或拖拽图片路径) ──
        clean_input = user_input.strip().strip('"').strip("'")
        if clean_input.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")) and Path(clean_input).exists():
            print(colorize(f"\n[📸 检测到直接输入图片: {Path(clean_input).name}，正在调起多模态视觉阅卷技能...]\n", C.CYAN))
            reply = vision_solver.solve_image_with_model(clean_input, "", cfg, stream=True)
            if reply:
                history.append({"role": "user", "content": f"[图片批改: {Path(clean_input).name}]"})
                history.append({"role": "assistant", "content": reply})
            continue

        # ── 斜杠指令与 Skills 分发 ──
        if user_input.startswith("/"):
            cmd_parts = user_input.split(maxsplit=1)
            cmd = cmd_parts[0].lower()
            arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""

            if cmd in ("/exit", "/quit"):
                print("再见！一战成硕！")
                break

            # ── 技能 1: /skills 查看所有技能 ──
            elif cmd == "/skills":
                print(colorize("\n=== 🧩 考研专有智能体技能中心 (Skills Registry) ===", C.BOLD))
                for sk_id, sk in list_skills().items():
                    print(f"\n  {sk['name']} [{colorize(sk['status'], C.GREEN)}]")
                    print(f"    - 功能: {sk['desc']}")
                    print(f"    - 指令: {colorize(sk['command'], C.YELLOW)}")
                print()
                continue

            # ── 技能 2: /img 或 /ocr 视觉看图与手写批改 ──
            elif cmd in ("/img", "/ocr"):
                if not arg:
                    print(colorize("用法: /img <图片路径> [补充要求]\n示例: /img C:\\Users\\draft.jpg 请重点检查第3行计算", C.YELLOW))
                    continue
                parts = arg.split(maxsplit=1)
                img_p = parts[0].strip('"').strip("'")
                extra = parts[1] if len(parts) > 1 else ""
                if not Path(img_p).exists():
                    print(colorize(f"\n[!] 未找到图片: {img_p}\n", C.RED))
                    continue
                print(colorize(f"\n[📸 正在调起多模态视觉阅卷技能分析: {Path(img_p).name}...]\n", C.CYAN))
                reply = vision_solver.solve_image_with_model(img_p, extra, cfg, stream=True)
                if reply:
                    history.append({"role": "user", "content": f"[图片批改: {Path(img_p).name}] {extra}"})
                    history.append({"role": "assistant", "content": reply})
                continue

            # ── 技能 3: /calc 或 /verify 数学符号验算 ──
            elif cmd in ("/calc", "/verify"):
                if not arg:
                    print(colorize("用法: /calc <数学式子>\n示例:\n  /calc diff x^3*sin(x)\n  /calc limit (sin(x)-x)/x^3 as x->0\n  /calc int x*exp(x) dx", C.YELLOW))
                    continue
                print(colorize(f"\n[📐 正在运行数学符号验算引擎...]\n", C.CYAN))
                res = math_verifier.run_math_query(arg)
                print(res + "\n")
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
            elif cmd in ("/view", "/live"):
                import webbrowser
                target_url = f"http://localhost:{live_port or 8088}/live"
                webbrowser.open(target_url)
                print(colorize(f"\n[已在默认浏览器中打开实时可视化伴侣: {target_url}]\n", C.GREEN))
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
                print(colorize(f"未知指令 {cmd}，输入 /skills 查看可用技能，或输入 /math /eng /pol /pro", C.RED))
                continue

        # ── LLM 交互 ──
        sys_prompt = build_system_prompt(curr_subj)
        messages = [{"role": "system", "content": sys_prompt}]
        for h in history[-6:]:  # 保持最近 6 轮
            messages.append(h)
        messages.append({"role": "user", "content": user_input})

        append_live_message("user", user_input)

        print(colorize(f"\n[{SUBJECT_DIRS[curr_subj][1]} 正在思考并按评分标准批改...]\n", C.DIM))
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

# ════════════════════════════════════════════════════════════════
# 6. Webhook 网关模式 (`ky serve --port 8088`)
# ════════════════════════════════════════════════════════════════

def create_gateway_handler():
    from http.server import BaseHTTPRequestHandler
    cfg = load_config()

    class GatewayHandler(BaseHTTPRequestHandler):
        def do_GET(self):
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

            # 来自 Web 伴侣前端的提问
            if parsed.path == "/api/ask":
                try:
                    data = json.loads(post_data)
                    user_msg = data.get("message", "").strip()
                except Exception:
                    user_msg = post_data.strip()

                if not user_msg:
                    self.send_response(400)
                    self.end_headers()
                    return

                append_live_message("user", user_msg)
                active_subj = cfg.get("active_subject", "math")
                sys_prompt = build_system_prompt(active_subj)
                messages = [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_msg}
                ]
                
                reply = "已收到！正在为您解析..."
                if not cfg.get("api_key"):
                    reply = f"🎓【考研私教】收到您的提问: \"{user_msg}\"\n当前尚未配置大模型 API Key，请在电脑端运行 `ky config` 设置密钥。"
                else:
                    try:
                        base_url = cfg.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
                        url = f"{base_url}/chat/completions"
                        req = urllib.request.Request(
                            url,
                            data=json.dumps({"model": cfg.get("model", "deepseek-chat"), "messages": messages, "stream": False}).encode("utf-8"),
                            headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg.get('api_key','')}"}
                        )
                        with urllib.request.urlopen(req, timeout=30) as resp:
                            res_json = json.loads(resp.read().decode("utf-8"))
                            reply = res_json["choices"][0]["message"]["content"]
                    except Exception as e:
                        reply = f"[私教处理异常]: {e}"

                append_live_message("assistant", reply)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"reply": reply}, ensure_ascii=False).encode("utf-8"))
                return

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

            # 发起请求或返回未配置提示
            reply = "已收到！正在为您解析..."
            if not cfg.get("api_key"):
                reply = f"🎓【考研私教】收到您的提问: \"{user_msg}\"\n当前尚未配置大模型 API Key，请在电脑端运行 `ky config` 设置密钥。"
            else:
                try:
                    base_url = cfg.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
                    url = f"{base_url}/chat/completions"
                    req = urllib.request.Request(
                        url,
                        data=json.dumps({"model": cfg.get("model", "deepseek-chat"), "messages": messages, "stream": False}).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg.get('api_key','')}"}
                    )
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        res_json = json.loads(resp.read().decode("utf-8"))
                        reply = res_json["choices"][0]["message"]["content"]
                except Exception as e:
                    reply = f"[私教处理异常]: {e}"

            append_live_message("user", user_msg)
            append_live_message("assistant", reply)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            
            # 返回给调用方
            resp_body = json.dumps({"msgtype": "text", "text": {"content": reply}}, ensure_ascii=False)
            self.wfile.write(resp_body.encode("utf-8"))

        def log_message(self, format, *args):
            return  # 静默请求日志

    return GatewayHandler

def start_background_live_server(start_port=8088):
    """在后台静默启动 Web 实时伴侣服务器，自动处理端口占用"""
    from http.server import HTTPServer
    import threading
    handler_class = create_gateway_handler()
    for p in range(start_port, start_port + 20):
        try:
            httpd = HTTPServer(("127.0.0.1", p), handler_class)
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            return p
        except OSError:
            continue
    return None

def run_server(port=8088):
    """启动轻量级 HTTP Webhook 接收网关，实现微信/QQ/钉钉双向收发与 Web 伴侣"""
    from http.server import HTTPServer
    cfg = load_config()

    print(colorize(f"\n[🚀 考研智能体 Webhook 网关与实时 Web 伴侣正在启动... 监听端口: {port}]", C.BOLD))
    print(f"  - 网页实时 LaTeX 伴侣: http://127.0.0.1:{port}/live")
    print(f"  - 钉钉/企业微信回调地址: http://<你的公网IP或内网穿透域名>:{port}/webhook")
    print(f"  - 当前默认学科: {SUBJECT_DIRS[cfg.get('active_subject','math')][1]}")
    print("  - 支持接收群聊提问并自动回复，按 Ctrl+C 停止服务。\n")

    handler_class = create_gateway_handler()
    httpd = HTTPServer(("0.0.0.0", port), handler_class)
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
    elif args[0] in ("view", "--view", "--web", "live"):
        port = start_background_live_server(8088) or 8088
        import webbrowser
        webbrowser.open(f"http://localhost:{port}/live")
        print(f"已在默认浏览器打开实时 LaTeX 伴侣: http://localhost:{port}/live")
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
