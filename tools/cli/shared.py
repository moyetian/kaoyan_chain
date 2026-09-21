# -*- coding: utf-8 -*-
"""
考研学习链 CLI 共享配置与工具模块
包含全局路径、配置读写、学科定义与安全检查
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from tools.ky_io import atomic_write_text, read_text_fallback
except ImportError:
    from ky_io import atomic_write_text, read_text_fallback

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = ROOT / "ky_config.json"
HISTORY_FILE = ROOT / "ky_history.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "api_provider": "deepseek",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "webhook_token": "",  # /webhook 专用回调密钥（群机器人双向讲题；环境变量 KY_WEBHOOK_TOKEN 可临时覆盖）
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

SUBJECT_DIRS: Dict[str, Tuple[str, str]] = {
    "math": ("01-数学", "数学专属私教"),
    "eng": ("02-英语", "英语专属私教"),
    "pol": ("03-思想政治理论", "政治专属私教"),
    "pro": ("04-专业课", "专业课专属私教"),
}

COACHING_STYLES: Dict[str, Tuple[str, str]] = {
    "1": ("严格把关·保姆提分型 (Strict & Disciplined)", "以真题阅卷人严苛视角审视解答，步步赋分，零容忍计算与书写失误，强制归因"),
    "2": ("高效应试·高频秒杀型 (High-Yield Hacker)", "80/20法则，只抓必考得分盘，传授代入/特值/排除/帽子词口诀与解题模板"),
    "3": ("温和启发·减负鼓励型 (Encouraging Mentor)", "耐心倾听、正向激励，大题微步化拆解，降低复习挫败感与焦虑内耗"),
    "4": ("深度原理·学霸溯源型 (Deep Conceptual Master)", "溯源定理物理与几何背景，从命题设计反推陷阱，打通底层知识图谱"),
}

#: 当前平台是否为 Windows（模块级固化，便于测试直接替换）
_IS_WINDOWS: bool = sys.platform.startswith("win")

#: interpreter_hint() 的模块级缓存（避免每次打印用户提示都探测一次 PATH）
_INTERPRETER_HINT: Optional[str] = None


def interpreter_hint() -> str:
    """返回用户在当前平台应当键入的 Python 解释器名（仅用于用户可见文案）。

    - Windows：优先官方启动器 `py`（`python` 常被微软商店 stub 劫持，直接执行会
      exit 49 且零输出）；若 `py` 不可用再退回 `python`。
    - 非 Windows：优先 `python3`；不可用再退回 `python`。

    结果做模块级缓存，避免每次打印提示都探测 PATH。
    """
    global _INTERPRETER_HINT
    if _INTERPRETER_HINT is None:
        if _IS_WINDOWS:
            _INTERPRETER_HINT = "py" if shutil.which("py") else "python"
        else:
            _INTERPRETER_HINT = "python3" if shutil.which("python3") else "python"
    return _INTERPRETER_HINT


def _fresh_default_config() -> Dict[str, Any]:
    """返回与模块级 DEFAULT_CONFIG 解耦的默认配置副本。

    [G10 修复·浅拷贝污染] 旧实现三处 ``DEFAULT_CONFIG.copy()`` 都是浅拷贝：
    返回值的 ``cfg["webhooks"]`` 与模块级默认是同一对象，
    ``configure_webhooks`` 原地写入会污染进程内默认值，后续 load_config()
    读到脏默认。此处对 webhooks 再拷一层（值均为标量，一层足够）。
    """
    fresh = DEFAULT_CONFIG.copy()
    _wh = DEFAULT_CONFIG.get("webhooks")
    fresh["webhooks"] = dict(_wh) if isinstance(_wh, dict) else {}
    return fresh


def load_config() -> Dict[str, Any]:
    """加载 ky_config.json，损坏时自动备份并回退默认配置"""
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            merged = _fresh_default_config()
            merged.update(cfg)
            if "webhooks" in cfg and isinstance(cfg["webhooks"], dict):
                merged["webhooks"] = {**merged["webhooks"], **cfg["webhooks"]}
            return merged
        except Exception as e:
            try:
                import shutil
                bak = CONFIG_FILE.with_suffix(".corrupted.bak")
                shutil.copyfile(CONFIG_FILE, bak)
                # [P1-4 修复] 备份同样含明文 api_key，POSIX 下收紧为 0600。
                if os.name != "nt":
                    try:
                        os.chmod(bak, 0o600)
                    except OSError:
                        pass
                print(f"[!] 警告: 读取 {CONFIG_FILE.name} 失败: {e}，已备份至 {bak.name} 并回退默认配置")
            except Exception:
                pass
            return _fresh_default_config()
    return _fresh_default_config()

def _guard_before_config_write() -> None:
    """写**真实** ``ky_config.json`` 前的留档与审计（best-effort，绝不抛异常）。

    2026-09-21 事故：真实配置被并发运行的临时验证脚本覆写成只剩 2 个键的测试
    夹具（39 字段 ``study_plan`` + 51 字符真实 ``api_key`` 丢失）。写入者不在
    pytest 进程内，``tests/conftest.py`` 的绊线一次都没响，损坏持续存在并被反复
    覆盖，直到人工从历史副本恢复。

    因此在这里对**真实仓库**的配置做写前留档：任何一次写入之前都先存一份快照，
    保证最坏情况下也能回退。同时把调用来源写进审计日志，便于事后定位。
    被重定向到 tmp 的测试路径不受影响（不触发守卫）。

    留档与审计日志**同源同落位**：都在 ``config_guard.BACKUP_DIR``（仓库之外，
    由 ``config_guard.default_backup_dir()`` 决定，见该模块的 CRITICAL 说明）。
    本函数只负责「写前留档 + 记审计」的既有语义，落位一律不在本模块决定。
    """
    try:
        target = Path(CONFIG_FILE).resolve()
        root = Path(__file__).resolve().parent.parent.parent
        if target != (root / "ky_config.json").resolve():
            return  # 测试重定向或非真实路径，无需守卫

        # [CRITICAL 加固·审计日志落位] 审计日志必须和快照同处**仓库之外**。
        # 落位取自 ``config_guard``：``BACKUP_DIR`` 就是 ``default_backup_dir()``
        # 的模块级结果，也正是 ``auto_backup()`` 实际写入的目录 —— 这里不另拼
        # 一套路径。此前写死的「仓库根下配置备份子目录」会在**每次真实保存配置**
        # 时把仓库内目录重新创建出来（导出遍历的排除名单只是第二层防御）。
        from tools.config_guard import BACKUP_DIR, auto_backup  # noqa: WPS433
        auto_backup()

        import time
        import traceback
        backup_dir = Path(BACKUP_DIR)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stack = "".join(traceback.format_stack()[-6:-1])
        with (backup_dir / "write_audit.log").open("a", encoding="utf-8") as fh:
            fh.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] save_config -> {target}\n{stack}")
    except Exception:
        # 守卫是尽力而为：任何异常都不得影响正常写盘
        pass


def save_config(cfg: Dict[str, Any]) -> None:
    """原子化持久保存配置至 ky_config.json

    [P1-4 修复] ky_config.json 含 API Key / Webhook access_token，属凭证文件，
    以 ``sensitive=True`` 落盘，POSIX 下权限收紧为 0600（此前为 umask 默认 0644）。

    [配置守卫] 写盘前自动留档 + 审计，见 :func:`_guard_before_config_write`。
    """
    _guard_before_config_write()
    atomic_write_text(CONFIG_FILE, json.dumps(cfg, ensure_ascii=False, indent=2), sensitive=True)

def read_text_safe(path: Path) -> str:
    """安全读取文件文本内容，不存在或报错返回空字符串"""
    try:
        p = Path(path)
        if not p.exists():
            return ""
        return read_text_fallback(p)
    except Exception:
        return ""

def resolve_major_keyword(cfg=None, fallback: str = "计算机") -> str:
    """解析专业关键词，缺省时读取档案里的报考专业"""
    cfg = cfg if isinstance(cfg, dict) else load_config()
    plan = cfg.get("study_plan") if isinstance(cfg.get("study_plan"), dict) else {}
    major = (plan.get("major") or cfg.get("major") or "").strip()
    if major in ("", "报考专业", "未指定", "目标院校"):
        return fallback
    return major

def resolve_profile_schools(cfg=None) -> Tuple[str, str]:
    """解析考生档案中的报考院校与备选院校"""
    cfg = cfg if isinstance(cfg, dict) else load_config()
    plan = cfg.get("study_plan") if isinstance(cfg.get("study_plan"), dict) else {}
    bad = ("", "未指定", "目标院校", "未填写")
    s1 = (plan.get("school") or cfg.get("school") or "").strip()
    s2 = ""
    for cand in (plan.get("backup_school"), cfg.get("backup_school"),
                 cfg.get("backup_target"), plan.get("backup_target")):
        v = (cand or "").strip()
        if v and v not in bad:
            s2 = v
            break
    return ("" if s1 in bad else s1), s2

def _discover_new_syllabus() -> Optional[Path]:
    """探测 04-专业课/参考资料 下的新考纲文档"""
    pro_ref = ROOT / "04-专业课" / "参考资料"
    if not pro_ref.exists():
        return None
    for pat in ("*2027*大纲*", "*2027*考纲*", "*新*大纲*", "*新*考纲*", "*大纲*.md", "*大纲*.txt"):
        for m in pro_ref.glob(pat):
            if m.is_file() and "demo" not in m.name.lower() and "样例" not in m.name:
                return m
    return None

def _looks_like_path(token: str) -> bool:
    """判断字符串是否呈现为文件路径形态（用于区分答题卡内联文本与文件）"""
    t = (token or "").strip()
    if not t or "\n" in t or len(t) >= 260:
        return False
    if re.match(r"^[A-Za-z]:[\\/]", t):
        return True
    if any(ch.isspace() for ch in t):
        return False
    if re.search(r"\.(md|txt|markdown|json|csv|pdf|docx?|xlsx?)$", t, flags=re.IGNORECASE):
        return True
    if ("/" in t or "\\" in t) and not re.search(r"[：；，。！？、【】（）]", t):
        return True
    return False

# ── 严格只读模式 (--permission=safe) 命令级闸门 ───────────────────────────
# [P0 修复·写命令未拦截] 此前这里是一份**手写的写命令黑名单**，末尾 `return None`
# ——「未列举即放行」(fail-open)。任何新增写命令只要忘了补名单，safe 模式就直接
# 放行：实测 `ky mount`（注册表里 write=True）在 --permission=safe 下改写了
# ky_config.json 与 AGENTS.md，还以 exit=0 报告"成功"。
#
# 现改为：**以命令注册表 (tools/cli/dispatch.py 的 Command.write) 为唯一事实源**，
# 未注册命令一律 fail-closed 拒绝。下面三张表只是把 write 元数据在「子命令/开关」
# 粒度上补齐说明，不是第二套命令名单。

#: 「默认只读、显式开关才落盘」：出现任一触发词即按写操作拦截。
_SAFE_MODE_WRITE_FLAGS: Dict[str, frozenset] = {
    "map":       frozenset({"--save", "-s"}),
    "compare":   frozenset({"--save", "-s"}),
    "diagnose":  frozenset({"--save", "-s"}),
    "fetch":     frozenset({"--save", "-s"}),
    "scout":     frozenset({"--save", "-s", "--apply", "-a"}),
    "admission": frozenset({"--save", "-s"}),
    "wechat":    frozenset({"--save", "-s"}),
}

#: 「写命令里仍可安全执行的只读子操作」：子命令命中白名单才放行。
_SAFE_MODE_READONLY_SUBS: Dict[str, frozenset] = {
    "key":    frozenset({"list", "ls", "--list", "l", "show", "query", ""}),
    "memory": frozenset({"status", "health", "check", "show", "list", ""}),
}

#: 「默认只读、但特定子命令会落盘」：子命令不在白名单内即按写操作拦截。
_SAFE_MODE_ALLOWED_SUBS: Dict[str, frozenset] = {
    # ky watch（列监控清单）/ ky watch --list 只读；
    # ky watch <高校名> / ky watch --remove <高校名> / ky watch --check 都会写监控库
    # —— ``check_updates()`` 末尾无条件 ``self._save()``（tools/intelligence/watcher.py:317），
    # 所以 ``--check`` **不是**只读操作（此前按只读放行是错的）。
    "watch": frozenset({"", "--list", "-l", "list"}),
}

#: 永远只读的开关：任何命令加 --help/-h 都不会落盘。
_SAFE_MODE_HELP_TOKENS = frozenset({"--help", "-h"})


def _lookup_command_meta(token: str):
    """从命令注册表查命令元数据。

    返回 ``(found, command)``：
      * ``(True, cmd)``   —— 命中注册表；
      * ``(False, None)`` —— 注册表可用，但该命令未注册（调用方须 fail-closed）；
      * ``(None, None)``  —— 注册表不可用（命令模块整体导入失败），无法判定。

    这里用函数内延迟导入：``shared`` 与 ``dispatch`` 互相导入，模块级导入会成环。
    """
    try:
        from tools.cli import dispatch as dispatch_mod
    except ImportError:
        try:
            from cli import dispatch as dispatch_mod
        except ImportError:
            return None, None
    try:
        dispatch_mod._init_all_commands()
    except Exception:
        pass
    if not dispatch_mod.list_commands():
        # 注册表为空 = 命令模块导入失败。此时 dispatch.main 自身也拿不到任何
        # handler（一律「未知参数」exit 1），不会有写操作漏网；此处不做「未注册」
        # 误判，以免只读模式把所有查询命令都打成未注册而拒绝一切。
        return None, None
    raw = str(token).strip()
    cmd = dispatch_mod.get_command(raw) or dispatch_mod.get_command(raw.lstrip("-").lower())
    if cmd is None:
        return False, None
    return True, cmd


def detect_safe_mode_violation(args: list) -> Optional[str]:
    """严格只读模式 (--permission=safe) 命令级安全检查。

    事实源是命令注册表的 ``Command.write`` 元数据；未注册命令 fail-closed。
    """
    if not args:
        return None
    token = str(args[0])
    rest = [str(x).lower() for x in args[1:]]
    sub = rest[0] if rest else ""

    # --help/-h 是纯读路径，任何命令都不落盘
    if any(tok in _SAFE_MODE_HELP_TOKENS for tok in rest):
        return None

    found, cmd = _lookup_command_meta(token)
    if found is None:
        return None
    if not found:
        return f"{token}（未注册命令，只读模式下拒绝执行）"

    name = cmd.name

    # 归一化：`ky fetch watch ...` 在 dispatch 里被转发给 `ky watch ...`（intel.py
    # 的 _cmd_fetch），语义与写入行为完全一致，按同一套规则判定，避免「同一个动作
    # 换条路径进来就漏判」。
    if name == "fetch" and sub in ("watch", "--watch"):
        name = "watch"
        rest = rest[1:]
        sub = rest[0] if rest else ""

    if getattr(cmd, "write", False):
        allowed = _SAFE_MODE_READONLY_SUBS.get(name)
        if allowed is not None and sub in allowed:
            return None
        return f"{name} (含写操作/子进程执行)"

    for tok in rest:
        if tok in _SAFE_MODE_WRITE_FLAGS.get(name, frozenset()):
            return f"{name} {tok} (写操作落盘)"
    allowed = _SAFE_MODE_ALLOWED_SUBS.get(name)
    if allowed is not None and sub not in allowed:
        return f"{name} {sub or '<默认>'} (写操作落盘)"
    return None

def detect_repl_safe_mode_violation(cmd: str, arg: str = "") -> Optional[str]:
    """REPL 斜杠指令的只读模式安全检查"""
    c = (cmd or "").lower()
    a = (arg or "").lower()
    readonly_ok = {"/skills", "/status", "/today", "/tasks", "/task", "/pdf",
                   "/fatigue", "/doctor", "/map", "/exit", "/quit"}
    if c in readonly_ok:
        if c == "/map" and "--save" in a:
            return "/map --save"
        return None
    if c == "/memory":
        return f"/memory {a}" if a.startswith("prune") else None
    if c == "/help":
        return None
    blocked = {
        "/diff": "考纲 Diff（报告落盘）",
        "/ingest": "切片入库（写入索引与知识库）",
        "/build": "看板编译（写盘 + 子进程）",
        "/done": "任务打钩（回写今日任务与打卡记录）",
        "/notify": "消息广播（写盘 + 网络）",
        "/rollback": "快照回滚（覆盖当前文件）",
        "/restore": "快照回滚（覆盖当前文件）",
        "/exam": "反向组卷（试卷文件落盘）",
        "/compose": "反向组卷（试卷文件落盘）",
        "/variant": "变式检索（结果落盘）",
        "/review": "错题盲盒重测（回写错题状态）",
        "/batch": "答题卡批改（回写错题状态）",
        "/img": "草稿批改（上传文件落盘）",
        "/paste": "剪贴板取图（临时文件落盘）",
        "/clip": "剪贴板取图（临时文件落盘）",
        "/plan": "方案向导（写档案与配置）",
        "/profile": "方案向导（写档案与配置）",
        "/blueprint": "方案向导（写档案与配置）",
        "/subject": "科目/大纲维护（写配置）",
        "/syllabus": "科目/大纲维护（写配置）",
        "/style": "风格切换（写配置）",
        "/relieve": "减负模式（写配置）",
        "/config": "配置管理（写配置）",
        "/compare": "双校对标（研报落盘）",
        "/scout": "院校侦察（证据落盘）",
        "/watch": "情报雷达（指纹落盘）",
        "/admission": "招考核验（证据落盘）",
        "/gui": "启动图形界面（独立进程写盘）",
    }
    return blocked.get(c, f"{c}（含写操作）" if c.startswith("/") and c not in ("/calc", "/hint", "/dissect", "/math", "/eng", "/pol", "/pro", "/view", "/live", "/clear") else None)

def get_today_tasks_data() -> dict:
    """提取四科今日任务的结构化数据字典"""
    try:
        from tools.state import load_dashboard_state, state_to_cli_dict
    except ImportError:
        try:
            from state import load_dashboard_state, state_to_cli_dict
        except ImportError:
            return {"subjects": {}}
    return state_to_cli_dict(load_dashboard_state(ROOT))

def mark_today_task_done(keyword: str, subject: str = None) -> Tuple[bool, str]:
    """在今日任务中根据关键词匹配并标记为 [x] 完成"""

    def _humanize(line: str) -> str:
        # [收尾修复·回显竖线] 此前把整行表格原文（含 | 竖线）直接打印。
        # 现按"内容（模块 · 时长）"口径 humanize，与 today 渲染口径一致。
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        parts = [re.sub(r"\[(?:x| )\]", "", p).strip() for p in parts]
        parts = [p for p in parts if p and set(p) != {"-", ":"}]
        if len(parts) >= 3:
            return f"{parts[1]}（{parts[0]} · {parts[2]}）"
        return "｜".join(parts) if parts else line.strip()

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
            if "|" in line and keyword in line and not line.replace(" ", "").startswith("|---|") and "完成状态" not in line and "模块" not in line:
                if "[x]" in line.lower():
                    match_info = f"任务此前已是完成状态: {_humanize(line)}"
                    matched = True
                    new_lines.append(line)
                else:
                    new_line = re.sub(r'\[\s*\]', '[x]', line, count=1)
                    if new_line != line:
                        matched = True
                        match_info = f"已完成打卡: {_humanize(new_line)}"
                        new_lines.append(new_line)
                    else:
                        new_lines.append(line)
            else:
                new_lines.append(line)
        if matched:
            atomic_write_text(task_file, "\n".join(new_lines))
            return True, match_info

    if not matched:
        return False, f"未找到包含关键词「{keyword}」的今日任务"
    return True, match_info

def manage_coaching_style(choice: str = None) -> Tuple[str, bool]:
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

    if agents_root.exists() and content:
        if "- **当前激活辅导风格**：" in content:
            content = re.sub(r"- \*\*当前激活辅导风格\*\*：.*", f"- **当前激活辅导风格**：`{new_style_name}`", content)
        else:
            content = content.replace("## 0. 你的身份与总目标", f"## 0. 你的身份与总目标\n\n- **当前激活辅导风格**：`{new_style_name}`")
        atomic_write_text(agents_root, content)

    cfg["coaching_style"] = new_style_name
    if isinstance(cfg.get("study_plan"), dict):
        cfg["study_plan"]["style_name"] = new_style_name
    save_config(cfg)
    return new_style_name, True

def subject_display_name(cfg: dict, curr_subj: str) -> str:
    """[命名统一] 报到播报头与提示符同源：SUBJECT_DIRS 标签 + 方案别名后缀。
    如「英语专属私教（英语一 (201)）」——提示符显示"英语专属私教"，两者可互认。
    此前播报头只用方案别名（英语一 (201)），与提示符（英语专属私教）两套命名。"""
    label = SUBJECT_DIRS.get(curr_subj, ("", curr_subj))[1]
    plan = cfg.get("study_plan", {}) or {}
    plan_name = str(plan.get(f"{curr_subj}_name", "") or "").strip()
    if plan_name and plan_name != label:
        return f"{label}（{plan_name}）"
    return label

#: 「不考数学」的统一判定键（与 syllabus_manager.MATH_NONE_KEYS 同源口径）。
MATH_NONE_KEYS = frozenset({"none", "no", "不考数学"})


def is_math_disabled(cfg: dict) -> bool:
    """统一判定当前备考方案是否「不考数学」（含双专业课 / 199 管综等模式）。

    [R2-A4 修复·判定单源] 此前 renderer / engine / 各入口各自内联
    ``math_key == "none" or math_name == "不考数学"``，任一处漏改，文科考生就会在
    ``ky today`` 看到空的【数学】段落、提示语仍写「数学报到」。此处收敛为唯一实现。
    """
    if not isinstance(cfg, dict):
        return False
    plan = cfg.get("study_plan")
    if not isinstance(plan, dict):
        plan = cfg
    if str(plan.get("exam_mode") or cfg.get("exam_mode") or "") in (
            "mode_b", "no_math_dual_pro", "mode_c", "mgmt_199"):
        return True
    if plan.get("pol_disabled") or cfg.get("pol_disabled"):
        return True
    if str(plan.get("pro2_name") or cfg.get("pro2_name") or "").strip():
        return True
    if str(plan.get("math_key") or "").strip().lower() in MATH_NONE_KEYS:
        return True
    return str(plan.get("math_name") or "").strip() == "不考数学"


def recommended_checkin_command(cfg: dict) -> str:
    """给学员的「下一步开始学习」报到口令（不考数学时不得再指向数学）。

    与 ``init_workspace._start_command_for`` 同源口径：数学关闭时改推英语报到。
    """
    return "英语报到" if is_math_disabled(cfg) else "数学报到"


def build_subject_checkin_brief(cfg: dict, curr_subj: str) -> str:
    """生成某科目的「私教报到就绪」本地播报文本"""
    plan = cfg.get("study_plan", {})
    if curr_subj == "math" and is_math_disabled(cfg):
        return "当前方案为不考数学，不派发数学任务。请使用英语报到、政治报到或专业课报到。"
    subj_name = subject_display_name(cfg, curr_subj)
    hours = plan.get(f"{curr_subj}_hours", 2.0)
    target = plan.get(f"{curr_subj}_target", "高分冲刺")
    weak = plan.get(f"{curr_subj}_weakness", "核心考点攻坚")

    lines = [
        f"🎓 【{subj_name} · 私教报到就绪】",
        f"• 今日规划投入: {hours} 小时 ｜ 战役目标: {target}",
        f"• 核心薄弱防线: 【{weak}】",
    ]

    due_count = 0
    try:
        from tools.skills import error_logger
    except ImportError:
        try:
            from skills import error_logger
        except ImportError:
            error_logger = None

    if error_logger:
        try:
            due_count = len(error_logger.get_due_reviews(curr_subj, max_count=3))
        except Exception:
            due_count = 0
    if due_count:
        lines.append(f"🔔 检测到您有 {due_count} 道 FSRS 到期错题！完成作答并输入「交作业」即可启动盲盒复测。")
    else:
        t_file = ROOT / SUBJECT_DIRS[curr_subj][0] / "_状态" / "今日任务.md"
        task_lines = []
        if t_file.exists():
            txt = read_text_safe(t_file)
            for l in txt.splitlines():
                l_s = l.strip()
                if re.match(r"^-\s*\[ \]", l_s) or ("|" in l_s and "[ ]" in l_s):
                    task_lines.append(l_s)
        if task_lines:
            lines.append("📋 今日攻坚任务清单（前 2 项）：")
            lines.extend(f"  {t}" for t in task_lines[:2])
    lines.append("💡 私教提示：可直接输入题目或题干提问，完成后输入「交作业」按采分点批改！")
    return "\n".join(lines)
