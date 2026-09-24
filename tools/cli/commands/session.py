# -*- coding: utf-8 -*-
"""
会话管理命令模块 (session.py) —— B3b 批次

``ky session ls | resume <id> | fork <id> [--at N] | rm <id> | prune [--keep N] [--yes]``

* ``ls``     —— 列出历史会话（短 id / 创建时间 / 事件数 / 首条消息 / fork 来源）；
* ``resume`` —— 恢复指定会话（支持 id 前缀匹配）并进入 REPL；
* ``fork``   —— 从既有会话分叉（安全边界对齐，源文件只读不动）；
* ``rm``     —— 删除单个会话；
* ``prune``  —— 清理较旧会话，**默认 dry-run**，加 ``--yes`` 才真删。

底层实现全部在 ``tools/agent/session_log.py``（list_sessions / fork_session /
remove_session / prune_sessions），本模块只做参数解析与展示。
"""

from typing import List, Optional, Tuple

try:
    from tools.cli.dispatch import Command, register
    from tools.cli.shared import ROOT
    from tools.agent.session_log import (
        fork_session,
        list_sessions,
        prune_sessions,
        remove_session,
    )
except ImportError:
    from cli.dispatch import Command, register
    from cli.shared import ROOT
    from agent.session_log import (
        fork_session,
        list_sessions,
        prune_sessions,
        remove_session,
    )

_USAGE = "用法: ky session <ls|resume|fork|rm|prune> [id] [--at N] [--keep N] [--yes]"


def _resolve_session_id(token: str) -> Tuple[Optional[str], str]:
    """把用户输入的 id（支持唯一前缀）解析成完整 session_id。

    返回 ``(完整 id, "")`` 或 ``(None, 错误信息)``。
    """
    prefix = (token or "").strip()
    if not prefix:
        return None, "请提供会话 id（可用 `ky session ls` 查看）"
    ids = [s["session_id"] for s in list_sessions(ROOT)]
    if prefix in ids:
        return prefix, ""
    matches = [sid for sid in ids if sid.startswith(prefix)]
    if not matches:
        return None, f"未找到会话: {prefix}"
    if len(matches) > 1:
        return None, f"会话 id 前缀不唯一（匹配 {len(matches)} 个）: {prefix}"
    return matches[0], ""


def _session_ls() -> None:
    sessions = list_sessions(ROOT)
    if not sessions:
        print("\n[i] 暂无历史会话（.memory/sessions/ 为空）。直接开始对话即可自动创建。\n")
        return
    print("\n=== 💬 历史会话 ===")
    print(f"{'会话 id (时间戳)':<18} {'创建时间':<21} {'事件':<6} {'消息':<6} 首条消息 / 来源")
    print("-" * 88)
    for s in sessions:
        # 短 id 取「YYYYMMDD-HHMMSS」段：session_id 前 8 位只有日期，同一天的
        # 会话会全部显示成同一个串、无法区分；随机后缀（末 6 位）另行保留在
        # 完整 id 里，用户用任意唯一前缀即可 resume。
        short = s["session_id"][:15]
        created = (s.get("created_at") or "")[:19]
        preview = (s.get("first_user_text") or "").replace("\n", " ")[:30]
        mark = ""
        ff = s.get("forked_from")
        if isinstance(ff, dict) and ff.get("session_id"):
            mark = f"  [fork←{str(ff['session_id'])[:8]}]"
        print(f"{short:<18} {created:<21} {s['event_count']:<6} {s['message_count']:<6} {preview}{mark}")
    print(f"\n共 {len(sessions)} 个会话；恢复: ky session resume <id>（支持 id 前缀）")
    print("分叉: ky session fork <id> [--at N]；清理: ky session prune [--keep N] [--yes]\n")


def _session_resume(token: str) -> None:
    sid, err = _resolve_session_id(token)
    if not sid:
        print(f"[!] {err}")
        return
    try:
        from tools.cli.repl.loop import run_repl
    except ImportError:
        from cli.repl.loop import run_repl
    print(f"\n[i] 正在恢复会话 {sid} ...")
    run_repl(resume_session_id=sid)


def _session_fork(rest: List[str]) -> None:
    token = ""
    at_event: Optional[str] = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--at":
            if i + 1 >= len(rest):
                print("[!] --at 需要一个整数参数，例如: ky session fork <id> --at 12")
                return
            at_event = rest[i + 1]
            i += 2
        elif tok.startswith("--at="):
            at_event = tok.split("=", 1)[1]
            i += 1
        elif not token:
            token = tok
            i += 1
        else:
            print(f"[!] 未识别的参数: {tok}")
            print(_USAGE)
            return
    sid, err = _resolve_session_id(token)
    if not sid:
        print(f"[!] {err}")
        return
    new_path, _copied, message = fork_session(ROOT, sid, at_event=at_event)
    if new_path is None:
        print(f"[!] 分叉失败: {message}")
        return
    print(f"[√] {message}")
    print(f"    新会话文件: {new_path}")
    print(f"    恢复新会话: ky session resume {new_path.stem}")


def _session_rm(token: str) -> None:
    sid, err = _resolve_session_id(token)
    if not sid:
        print(f"[!] {err}")
        return
    if remove_session(ROOT, sid):
        print(f"[√] 已删除会话: {sid}")
    else:
        print(f"[!] 删除失败（文件可能已不存在）: {sid}")


def _session_prune(rest: List[str]) -> None:
    keep_raw: Optional[str] = None
    do_delete = False
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in ("--yes", "-y"):
            do_delete = True
            i += 1
        elif tok == "--keep":
            if i + 1 >= len(rest):
                print("[!] --keep 需要一个整数参数，例如: ky session prune --keep 10")
                return
            keep_raw = rest[i + 1]
            i += 2
        elif tok.startswith("--keep="):
            keep_raw = tok.split("=", 1)[1]
            i += 1
        else:
            print(f"[!] 未识别的参数: {tok}")
            print(_USAGE)
            return

    keep = 20
    if keep_raw is not None:
        try:
            keep = int(keep_raw)
        except (TypeError, ValueError):
            print(f"[!] --keep 需要整数: {keep_raw!r}")
            return
    if keep < 0:
        print("[!] --keep 不能为负数")
        return

    sessions = list_sessions(ROOT)
    victims = sessions[keep:]
    if not victims:
        print(f"\n[i] 当前共 {len(sessions)} 个会话，无需清理（保留最近 {keep} 个）。\n")
        return
    if not do_delete:
        print(f"\n[i] 预演模式 (dry-run)：将删除以下 {len(victims)} 个较旧会话（保留最近 {keep} 个）:")
        for s in victims:
            print(f"    - {s['session_id']}  ({(s.get('created_at') or '?')[:19]})")
        print(f"\n    确认删除请追加 --yes: ky session prune --keep {keep} --yes\n")
        return
    removed = prune_sessions(ROOT, keep=keep)
    print(f"\n[√] 已清理 {len(removed)} 个旧会话（保留最近 {keep} 个）:")
    for sid in removed:
        print(f"    - {sid}")
    print()


def _cmd_session(args: List[str]) -> None:
    sub = args[1].lower() if len(args) > 1 else "ls"
    rest = [str(x) for x in args[2:]]
    if sub in ("ls", "list"):
        _session_ls()
    elif sub == "resume":
        _session_resume(rest[0] if rest else "")
    elif sub == "fork":
        _session_fork(rest)
    elif sub in ("rm", "remove", "delete"):
        _session_rm(rest[0] if rest else "")
    elif sub in ("prune", "clean"):
        _session_prune(rest)
    else:
        print(f"未知 session 子命令: {sub}")
        print(_USAGE)


register(Command('session', ("session",), '<ls|resume|fork|rm|prune> [id]',
                 '会话管理：列出 / 恢复 / 分叉 / 删除 / 清理历史会话',
                 handler=_cmd_session, write=True))
