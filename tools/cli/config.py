# -*- coding: utf-8 -*-
"""
交互式配置中心模块 (config.py)
包含大模型 API、视觉多模态模型、机器人 Webhook、科目切换与配置向导
"""

import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from tools.cli.shared import (
        ROOT, CONFIG_FILE, SUBJECT_DIRS, DEFAULT_CONFIG,
        load_config, save_config, read_text_safe, atomic_write_text
    )
except ImportError:
    from cli.shared import (
        ROOT, CONFIG_FILE, SUBJECT_DIRS, DEFAULT_CONFIG,
        load_config, save_config, read_text_safe, atomic_write_text
    )

try:
    from tools.cli.repl.renderer import C, colorize
except ImportError:
    from cli.repl.renderer import C, colorize

try:
    from tools.cli.repl.session import get_clipboard_text
except ImportError:
    from cli.repl.session import get_clipboard_text

try:
    from tools.cli.notify import (
        send_to_dingtalk, send_to_feishu, send_to_wechat, send_to_qq, broadcast_briefing
    )
except ImportError:
    from cli.notify import (
        send_to_dingtalk, send_to_feishu, send_to_wechat, send_to_qq, broadcast_briefing
    )

#: 微信 ClawBot 连接器的 npm 包（版本锁定）。
#: [P2-7 修复] 此前用 `@latest` 无版本锁定、无完整性校验，上游一旦投毒即 RCE。
#: 版本取自 2026-09-21 `npm view @tencent-weixin/openclaw-weixin-cli dist-tags` → latest=2.1.4。
WECHAT_CLAWBOT_CLI_PKG = "@tencent-weixin/openclaw-weixin-cli@2.1.4"

#: :func:`_mask_secret` 的下限：短于等于该长度的凭证一律只报「已设置」，不做部分回显。
#: 与 :func:`show_config` 中 ``api_key`` 的 ``len <= 12`` 判定同口径。
_MASK_MIN_LEN = 12


def _mask_secret(value: Any, keep_head: int = 6, keep_tail: int = 4) -> str:
    """把凭证/Webhook 统一打码，避免在终端提示或配置清单里明文回显。

    [P1-5 修复] 此前 input() 提示语与 show_config 直接插值现存 webhook URL
    （内含 access_token）与加签 Secret，短 API Key 还会整体原样打印。
    统一口径：保留前 ``keep_head`` 后 ``keep_tail``，中间用 ``***``；
    长度不足以打码时整体显示「已设置」，绝不回显原文。

    [修复·短密钥泄漏] 原阈值是 ``len <= keep_head + keep_tail``（即 10），
    于是 **11~12 字符**的密钥会走部分回显分支，暴露出 10/11 个字符 —— 等同于
    明文。现把下限提到 ``_MASK_MIN_LEN``（12，与 :func:`show_config` 里
    ``api_key`` 的「长度 ≤12 只报已设置」口径一致），短密钥一律只报「已设置」。
    """
    s = str(value or "")
    if not s:
        return "未设置"
    if len(s) <= max(keep_head + keep_tail, _MASK_MIN_LEN):
        return "已设置"
    return f"{s[:keep_head]}***{s[-keep_tail:]}"


def open_provider_console_and_get_key(provider_name: str, console_url: str, current_key: str = "") -> str:
    """自动打开服务商官方认证/API Key 管理页面，并支持一键套用剪贴板密钥"""
    print(colorize(f"\n🌐 正在为您自动打开 {provider_name} 官方控制台: {console_url}", C.CYAN))
    print(colorize("💡 提示：在网页中登录后，点击「创建 API Key」并复制即可！\n", C.YELLOW))
    try:
        webbrowser.open(console_url)
    except Exception as e:
        print(colorize(f"   [提示] 自动唤起浏览器受阻: {e}，请手动访问上方链接。", C.DIM))

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

    curr_display = _mask_secret(current_key)
    user_key = input(f"请输入 API Key (直接回车保持现有: {curr_display}): ").strip()
    return user_key if user_key else current_key

def configure_llm(cfg: Dict[str, Any]) -> None:
    """配置大模型 API 服务商与密钥"""
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
        curr_key_display = _mask_secret(cfg.get('api_key', ''))
        new_key = input(f"API Key (输入新密钥或直接回车保持现有: {curr_key_display}): ").strip()
        if new_key: cfg["api_key"] = new_key

    save_config(cfg)
    print(colorize(f"[√] 模型 API 配置已更新为 [{cfg['api_provider']} / {cfg['model']}]！", C.GREEN))

def run_wechat_clawbot_install() -> None:
    """启动腾讯官方微信 ClawBot 扫码连接工具"""
    print(f"""
{C.CYAN}╭────────────────────────────────────────────────────────────────────────╮
│  📱 微信个人号 · WeChat ClawBot 手机扫码直连专属中枢                     │
│  (腾讯官方 @tencent-weixin/openclaw-weixin-cli 驱动)                  │
╰────────────────────────────────────────────────────────────────────────╯{C.RESET}
""")
    print(colorize("🔍 正在核验 Node.js 与 NPX 环境...", C.DIM))
    npx_path = shutil.which("npx")
    if not npx_path:
        print(colorize("❌ 未检测到 npx 命令。请先安装 Node.js (https://nodejs.org) 或在终端运行: winget install OpenJS.NodeJS\n", C.RED))
        return

    print(colorize("✔ Node.js / NPX 环境正常！", C.GREEN))
    print(f"""
{C.BOLD}【微信 ClawBot 连接原理与步骤说明】{C.RESET}
• 微信个人号是由腾讯官方开源的 OpenClaw 微信连接器驱动；
• 它{C.YELLOW}并非普通 Webhook{C.RESET}，而是直接在终端打印【登录二维码】，手机微信扫码授权即可；
• 扫码成功后，微信接收到的考研提问会自动转发给本地私教大模型并推回微信！
• 本地 OpenAI 兼容接口地址: {C.GREEN}http://127.0.0.1:8088/v1{C.RESET} (已自动挂载考研私教 Prompt 与技能)

{C.CYAN}[执行命令]: npx -y {WECHAT_CLAWBOT_CLI_PKG} install{C.RESET}
""")
    try:
        act = input("是否立即启动腾讯官方扫码安装程序? (y/n) [y]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n操作已取消。")
        return
    if act != "n":
        print(colorize("\n🚀 正在拉取腾讯官方微信连接器并启动二维码，请准备好手机微信扫一扫...\n", C.CYAN))
        try:
            # [P2-7 修复] 参数列表形式（去 shell=True）+ 版本锁定，杜绝命令拼接与 @latest 漂移。
            # [修复·Windows npx] 直接传字面量 "npx" 时 CreateProcess 只自动补 `.EXE`，
            # 而 nvm-for-windows 装出来的是 `npx.CMD`，于是必抛
            # FileNotFoundError [WinError 2]，`ky clawbot` 完全不可用。
            # 用 shutil.which 解析出真实可执行文件路径再传入（仍保持 shell=False，
            # 不引入命令注入面）；解析不到时给出可操作的错误提示。
            subprocess.run([npx_path, "-y", WECHAT_CLAWBOT_CLI_PKG, "install"])
        except FileNotFoundError:
            print(colorize(
                f"❌ 无法启动 npx（已解析路径: {npx_path}）。请确认 Node.js 安装完整，"
                f"或改用完整路径重试。\n", C.RED))
        except Exception as e:
            print(colorize(f"执行异常: {e}", C.RED))

def configure_webhooks(cfg: Dict[str, Any]) -> None:
    """多选菜单式配置各个聊天机器人 Webhook"""
    hooks = cfg.setdefault("webhooks", {})

    while True:
        wc_tag = colorize("已配置", C.GREEN) if hooks.get("wechat") else colorize("未配置", C.DIM)
        dt_tag = colorize("已配置", C.GREEN) if hooks.get("dingtalk") else colorize("未配置", C.DIM)
        fs_tag = colorize("已配置", C.GREEN) if hooks.get("feishu") else colorize("未配置", C.DIM)
        qq_tag = colorize("已配置", C.GREEN) if hooks.get("qq_onebot") else colorize("未配置", C.DIM)
        wt_tag = colorize("已设置", C.GREEN) if cfg.get("webhook_token") else colorize("未设置", C.DIM)

        print(colorize("\n--- 📱 2. 聊天机器人 / 消息推送与双向讲题配置 ---", C.CYAN))
        print("请选择您想配置或连接的机器人平台：")
        print(f"  [1] 📱 微信个人号 (WeChat ClawBot 手机扫码直连)")
        print(f"  [2] 🏢 企业微信群机器人 (Webhook 推送模式) [{wc_tag}]")
        print(f"  [3] 📌 钉钉群自定义机器人 (DingTalk)       [{dt_tag}]")
        print(f"  [4] 🐦 飞书群自定义机器人 (Feishu)         [{fs_tag}]")
        print(f"  [5] 🐧 QQ 机器人 (OneBot 11 / NapCat)      [{qq_tag}]")
        print(f"  [6] 📢 发送一条测试消息验证所有已配机器人")
        print(f"  [7] 🗑️ 清空某个平台的配置")
        print(f"  [8] 🔑 群机器人回调密钥 (/webhook 专用)    [{wt_tag}]")
        print(f"  [0] 💾 保存并返回上级菜单")

        choice = input("\n请选择平台编号 (0~8) [默认 0]: ").strip() or "0"

        if choice == "0":
            save_config(cfg)
            print(colorize("[√] 机器人 Webhook 配置已安全保存！", C.GREEN))
            break
        elif choice == "1":
            run_wechat_clawbot_install()
        elif choice == "2":
            print(colorize("\n[配置 企业微信群机器人 Webhook]", C.BOLD))
            curr = hooks.get("wechat", "")
            val = input(f"请输入 Webhook URL (直接回车保持现有: {_mask_secret(curr)}): ").strip()
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
            curr = hooks.get("dingtalk", "")
            val = input(f"请输入 Webhook URL (直接回车保持现有: {_mask_secret(curr)}): ").strip()
            if val:
                hooks["dingtalk"] = val
            sec = input(f"请输入加签 Secret (若机器人未勾选加签直接回车，当前: {_mask_secret(hooks.get('dingtalk_secret',''))}): ").strip()
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
            curr = hooks.get("feishu", "")
            val = input(f"请输入 Webhook URL (直接回车保持现有: {_mask_secret(curr)}): ").strip()
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
            curr = hooks.get("qq_onebot", "")
            val = input(f"请输入 OneBot HTTP 接口 (直接回车保持现有: {_mask_secret(curr)}): ").strip()
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
        elif choice == "8":
            # [修复·webhook 密钥接入配置] 此前 /webhook 专用密钥只能靠环境变量
            # KY_WEBHOOK_TOKEN，重启终端即失效。现落盘到 ky_config.json 顶层
            # ``webhook_token``（与 ``gateway_token`` 同一约定），环境变量仍可临时覆盖。
            print(colorize("\n[配置 群机器人回调密钥 /webhook 专用]", C.BOLD))
            print("  • 该密钥只作用于 /webhook 回调端点（钉钉/飞书/QQ OneBot），与网关 token 相互独立；")
            print("  • 设置后请把回调地址写成 http://<地址>/webhook?token=<该密钥>；")
            print("  • 留空跳过（保持现有）；未设置时 /webhook 仅接受本机回环回调。")
            new_wt = input(
                f"请输入回调密钥 (直接回车保持现有: {_mask_secret(cfg.get('webhook_token'))}；输入 - 清空): "
            ).strip()
            if new_wt == "-":
                cfg["webhook_token"] = ""
                print(colorize("[√] 已清空回调密钥。", C.YELLOW))
            elif new_wt:
                cfg["webhook_token"] = new_wt
                print(colorize("[√] 回调密钥已更新（仅显示掩码）。", C.GREEN))
            else:
                print(colorize("[i] 未修改回调密钥。", C.DIM))
            save_config(cfg)

def show_config(cfg: Dict[str, Any]) -> None:
    """显示当前完整配置清单"""
    print(colorize("\n=== 📄 当前考研私教 CLI 配置清单 ===", C.BOLD))
    print(f"  - 服务商类型: {cfg.get('api_provider')}")
    print(f"  - 接口地址:   {cfg.get('base_url')}")
    print(f"  - 模型代号:   {cfg.get('model')}")
    curr_key = cfg.get('api_key', '')
    # [P1-5 修复] 长度 ≤12 的 key 不再原样输出（此前 `len<=12` 分支会整体回显），
    # 只报告长度；更长的才做 前6后4 打码。
    if not curr_key:
        masked_key = "未设置"
    elif len(curr_key) <= 12:
        masked_key = f"已设置(长度{len(curr_key)})"
    else:
        masked_key = _mask_secret(curr_key)
    print(f"  - API 密钥:   {masked_key}")
    print(f"  - 当前学科:   {SUBJECT_DIRS.get(cfg.get('active_subject','math'), ('',''))[1]}")

    hooks = cfg.get("webhooks", {})
    print(colorize("\n--- 机器人 Webhook 配置状态 ---", C.CYAN))
    print(f"  - 微信 Webhook: {_mask_secret(hooks.get('wechat'))}")
    print(f"  - 钉钉 Webhook: {_mask_secret(hooks.get('dingtalk'))} (加签: {'已启用' if hooks.get('dingtalk_secret') else '未启用'})")
    print(f"  - 飞书 Webhook: {_mask_secret(hooks.get('feishu'))}")
    print(f"  - QQ OneBot:    {_mask_secret(hooks.get('qq_onebot'))} (目标: {hooks.get('qq_target_id') or '无'})")
    # [修复·webhook 密钥接入配置] 回调密钥属凭证，沿用 _mask_secret 口径只回显掩码，
    # 绝不打印明文（与上方各 Webhook / API Key 一致）。
    print(f"  - 回调密钥:     {_mask_secret(cfg.get('webhook_token'))} (/webhook 专用，环境变量 KY_WEBHOOK_TOKEN 可临时覆盖)")

    vis_m = cfg.get("vision_model")
    print(colorize("\n--- 视觉大模型 (Vision Model) 配置状态 ---", C.CYAN))
    print(f"  - 视觉模型:     {vis_m or '未独立配置 (默认调用本地 RapidOCR 提取题干后交由主模型)'}")
    if vis_m:
        print(f"  - 视觉 Base URL: {cfg.get('vision_base_url', '跟随主模型')}")

    print(f"\n配置文件绝对路径: {CONFIG_FILE}")
    print("本文件已被 .gitignore 严密保护，绝不会被 Git 追踪提交。\n")

def configure_vision_model(cfg: Dict[str, Any]) -> None:
    """配置用于视觉识图的多模态大模型"""
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

def manage_syllabi_cli(cfg: Dict[str, Any]) -> None:
    """交互式切换考研科目与重载官方标准考纲"""
    try:
        from tools import syllabus_manager
    except ImportError:
        import syllabus_manager

    def _persist_subject(**kv):
        sp = cfg.setdefault("study_plan", {})
        sp.update({k: v for k, v in kv.items() if v})
        save_config(cfg)

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
    print("  [1] 📐 切换数学考试科目 (数一 / 数二 / 数三 / 396 / 不考数学)")
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
        print("    [5] 不考数学 [哲学/法学/教育/文学/历史/艺术等，数学任务与报到自动隐藏]")
        m_sel = input("  请选择 (1~5) [默认 1]: ").strip() or "1"
        if m_sel == "5":
            # [P2-2残 修复] 事后也可确认"不考数学"：此前菜单只有 1~4，
            # 不考数学考生一旦误选便无法修正（首次填报 init 才有 none 选项）。
            _persist_subject(math_key="none", math_name="不考数学")
            atomic_write_text(
                (ROOT / "01-数学" / "考试大纲.md"),
                "# 01-数学 · 本人不考数学\n\n> 根据备考方案（math_key=none），本日不安排数学学习任务。\n"
                "> 报到/任务/组卷/看板中的数学入口已自动隐藏。如专业实际要求数学，请重跑 `ky subject` 切回。\n")
            txt = read_text_safe(math_agents)
            txt = re.sub(r"- \*\*考试科目\*\*：.*", "- **考试科目**：`不考数学`", txt)
            atomic_write_text(math_agents, txt)
            print(colorize("\n[√] 已标记为不考数学：数学任务与报到已隐藏，01-数学/考试大纲.md 已写入占位说明。", C.GREEN))
            return
        m_key = {"1": "math2", "2": "math1", "3": "math3", "4": "math396"}.get(m_sel, "math2")
        math_info = syllabus_manager.MATH_SYLLABI[m_key]
        atomic_write_text((ROOT / "01-数学" / "考试大纲.md"), math_info["content"])
        txt = read_text_safe(math_agents)
        txt = re.sub(r"- \*\*考试科目\*\*：.*", f"- **考试科目**：`{math_info['name']}`", txt)
        atomic_write_text(math_agents, txt)
        _persist_subject(math_key=m_key, math_name=math_info["name"])
        print(colorize(f"\n[√] 已切换为 {math_info['name']}！已将官方大纲与超纲红线写入 01-数学/考试大纲.md", C.GREEN))
    elif c == "2":
        print("\n  --- 📖 请选择您的英语考试科目 ---")
        print("    [1] 英语二 (204) [专硕为主，整段段落英译汉 15分 + 图表数据大作文 15分] (专硕主流)")
        print("    [2] 英语一 (201) [学硕为主，5大高难长难句精译 10分 + 图画哲理漫画大作文 20分]")
        e_sel = input("  请选择 (1~2) [默认 1]: ").strip() or "1"
        e_key = {"1": "eng2", "2": "eng1"}.get(e_sel, "eng2")
        eng_info = syllabus_manager.ENGLISH_SYLLABI[e_key]
        atomic_write_text((ROOT / "02-英语" / "考试大纲.md"), eng_info["content"])
        txt = read_text_safe(eng_agents)
        txt = re.sub(r"- \*\*考试科目\*\*：.*", f"- **考试科目**：`{eng_info['name']}`", txt)
        atomic_write_text(eng_agents, txt)
        _persist_subject(eng_key=e_key, eng_name=eng_info["name"])
        print(colorize(f"\n[√] 已切换为 {eng_info['name']}！已将官方大纲写入 02-英语/考试大纲.md", C.GREEN))
    elif c == "3":
        print("\n  --- 💻 请选择您的专业课方案 ---")
        print("    [1] 全国统考 408 计算机学科专业基础")
        print("    [2] 全国统考 199 管理类综合能力")
        print("    [3] 院校自命题专业课")
        p_sel = input("  请选择 (1~3) [默认 3]: ").strip() or "3"
        pro_outline = ROOT / "04-专业课" / "考试大纲.md"
        existed_outline = pro_outline.exists()
        if existed_outline:
            bak = syllabus_manager.backup_syllabus_file(pro_outline)
            if bak:
                print(colorize(f"  [i] 已将原大纲备份至: {bak.name}", C.CYAN))

        if p_sel == "1":
            atomic_write_text(pro_outline, syllabus_manager.CS408_SYLLABUS)
            pro_title = "408 计算机学科专业基础"
        else:
            pro_title = input("  请输入专业课代码与名称 [如 801 信号与系统]: ").strip() or "专业课"
            existing_txt = read_text_safe(pro_outline) if existed_outline else ""
            has_real_content = (
                len(existing_txt.strip()) > 200
                and "请根据报考院校官网大纲填入" not in existing_txt
                and "请在此填入各章节掌握" not in existing_txt
            )
            if has_real_content:
                print(colorize(
                    f"  [i] 检测到 04-专业课/考试大纲.md 已有 {len(existing_txt.splitlines())} 行真实考纲内容，"
                    f"本次仅更新科目名称，不覆盖正文。\n"
                    f"      如需按新科目重建大纲骨架，请运行 `ky plan` 完整向导，或在 `ky subject` 中先把科目改为 408 再改回。",
                    C.YELLOW))
            else:
                atomic_write_text(pro_outline,
                    f"# 04-专业课 · 【{pro_title}】官方考试大纲\n\n> 本大纲为报考院校官方考纲。\n\n## 考查要点\n- 请在此填入各章节掌握/理解要求",
                )
        txt = read_text_safe(pro_agents)
        txt = re.sub(r"- \*\*专业课科目代码与名称\*\*：.*", f"- **专业课科目代码与名称**：`{pro_title}`", txt)
        atomic_write_text(pro_agents, txt)
        _persist_subject(pro_type=("408" if p_sel == "1" else "custom"), pro_name=pro_title)
        print(colorize(f"\n[√] 专业课已更新为: {pro_title}！", C.GREEN))
    elif c == "4":
        init_py = ROOT / "tools" / "init_workspace.py"
        subprocess.run([sys.executable, str(init_py)])

def interactive_config() -> None:
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

        try:
            choice = input("\n请选择功能 (0~7) [默认 0]: ").strip() or "0"
        except (EOFError, KeyboardInterrupt):
            print(colorize("\n[!] 输入流提前结束 (EOF)，配置向导已安全中止，本次未做的改动不会保存。\n", C.YELLOW))
            save_config(cfg)
            break
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
