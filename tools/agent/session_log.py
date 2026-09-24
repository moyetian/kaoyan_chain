# -*- coding: utf-8 -*-
r"""
考研学习链 (ky-cli) · 会话事件日志 (Append-only Session Log) —— B3a 批次

为什么需要它
------------
B3a 之前 ``AgentRunner.history`` 只活在内存里：进程一退出，上下文全丢；
压缩摘要（B1 的结构化摘要）同样不落盘，每次 run 都重新生成。

本模块把「会话里发生了什么」以 **append-only JSONL** 落盘到
``<workspace_root>/.memory/sessions/<session_id>.jsonl``，并定义**唯一一份**
``AgentEvent`` schema 与 history 重建规则，供后续批次（B3b / C 系列 / E3）复用。
``.memory/`` 是本地隐私目录（.gitignore 与导出排除均已覆盖），日志不随公开副本出门。

AgentEvent schema（``SCHEMA_VERSION = 1``）
------------------------------------------
一条事件是一个 JSON 对象::

    {
      "id": "9f2c1a4b7e01",              # 事件 id（append 的返回值；parent 指向它）
      "ts": "2026-09-24T18:30:00.123",   # 本地时间 ISO 字符串（毫秒精度）
      "type": "user",                    # 见 KNOWN_EVENT_TYPES
      "parent": null,                    # 串链：tool_result 指向对应 tool_call 的事件 id
      "payload": {"content": "..."},     # 各类型自定义（见下）
      "schema_version": 1
    }

各类型 payload 约定：

* ``session_start``: ``{"active_subject": str, "user_input": str}``
* ``user``:          ``{"content": str}``
* ``assistant``:     ``{"content": str}``
* ``tool_call``:     ``{"tool_call_id": str, "name": str, "arguments": dict}``
* ``tool_result``:   ``{"tool_call_id": str, "name": str, "content": str,
                        "truncated": bool, "original_chars": int}``
  —— ``content`` 超过 :data:`TOOL_RESULT_MAX_CHARS` 时截断存储
  （``truncated=True`` 标注）。**截断不破坏 resume 语义**：重建 history 不依赖
  tool 事件，它们只进日志供审计与后续批次分析。
* ``compact``:       ``{"summary": str, "before_messages": int, "after_messages": int}``
* ``session_end``:   ``{"active_subject": str}``

向前 / 向后兼容契约
-------------------
* 读到**未知 type**（旧版本读新事件、新版本读未来事件）或**多余字段**：
  :func:`load_events` 原样保留、绝不抛异常；:func:`rebuild_history` 忽略不认识的事件。
* 文件**最后一行可能是半截 JSON**（进程崩溃现场）：:func:`load_events` 丢弃残行，
  正常返回前面的事件。
* :func:`validate_event` 只做「单条事件是否像话」的静态校验，返回问题列表
  （空列表 = 合法）；未知 type 会作为**提示性问题**报出，但加载侧不据此过滤
  —— 校验与兼容是两件事。

history 模型（先定死）
----------------------
* 日志**全量**：所有事件都进 JSONL，一条不省；
* **resume 只重建「最近一条 compact 摘要 + 最后 N 条 user/assistant 消息」**
  （N = :data:`RESUME_TAIL_MESSAGES`，与 ``loop.py`` 既有的 12 条截断语义一致）；
* :func:`rebuild_history` 产出与实时 ``AgentRunner.history`` **逐条同构**的消息
  列表 —— 实时侧用同一个 :func:`compose_history` 维护，因此可断言
  「resume 上下文 == 实时上下文」；
* tool_call / tool_result 只进日志，**不进 history** —— 实时
  ``AgentRunner.history`` 本来就只有 user/assistant 两条消息。
"""

import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── 常量 ────────────────────────────────────────────────────────────────

#: AgentEvent schema 版本。字段结构变化时递增，旧事件保持可读。
SCHEMA_VERSION = 1

#: 已知事件类型集合（新增类型须同时更新本文档的 payload 约定）。
EVENT_SESSION_START = "session_start"
EVENT_USER = "user"
EVENT_ASSISTANT = "assistant"
EVENT_TOOL_CALL = "tool_call"
EVENT_TOOL_RESULT = "tool_result"
EVENT_COMPACT = "compact"
EVENT_SESSION_END = "session_end"

KNOWN_EVENT_TYPES = frozenset({
    EVENT_SESSION_START,
    EVENT_USER,
    EVENT_ASSISTANT,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_COMPACT,
    EVENT_SESSION_END,
})

#: resume 时保留的消息条数（与 ``loop.py`` 旧实现 ``history[-12:]`` 同语义：
#: 12 条消息 = 6 轮问答）。
RESUME_TAIL_MESSAGES = 12

#: tool_result 落盘的单条 content 上限（字符）。超长工具输出（整卷真题等）
#: 不能无界写盘；截断只在日志层发生，实时上下文不受影响。
TOOL_RESULT_MAX_CHARS = 4_000

#: 会话日志相对 workspace 根的目录（本地隐私目录，导出已排除）。
SESSIONS_SUBDIR = (".memory", "sessions")

#: 事件 id 的随机段长度。
_ID_RANDOM_HEX = 12

#: 降级警告的仓库统一配色（黄色）。
_YELLOW = "\033[93m"
_RESET = "\033[0m"


# ── 事件构造与校验 ──────────────────────────────────────────────────────


def new_event(event_type: str, payload: Optional[Dict[str, Any]] = None,
              parent: Optional[str] = None) -> Dict[str, Any]:
    """构造一条 AgentEvent（不落盘）。``payload`` 非 dict 一律收敛为空 dict。"""
    return {
        "id": uuid.uuid4().hex[:_ID_RANDOM_HEX],
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "type": event_type,
        "parent": parent,
        "payload": dict(payload) if isinstance(payload, dict) else {},
        "schema_version": SCHEMA_VERSION,
    }


def validate_event(evt: Any) -> List[str]:
    """校验单条事件的 schema，返回问题列表（空列表 = 合法）。

    覆盖：缺字段 / 类型错误 / 未知 type。**未知 type 只作提示性报告**
    （向前兼容：加载侧会原样保留它），因此调用方不应拿本函数的结果去过滤事件流。
    """
    if not isinstance(evt, dict):
        return [f"事件不是 dict（{type(evt).__name__}）"]

    problems: List[str] = []
    etype = evt.get("type")
    if not isinstance(etype, str) or not etype.strip():
        problems.append("type 缺失或不是非空字符串")
    elif etype not in KNOWN_EVENT_TYPES:
        problems.append(f"type 未知：{etype!r}（向前兼容：加载时会原样保留，不影响重建）")

    if "payload" not in evt:
        problems.append("payload 缺失")
    elif not isinstance(evt["payload"], dict):
        problems.append(f"payload 不是 dict（{type(evt['payload']).__name__}）")

    if "ts" not in evt:
        problems.append("ts 缺失")
    elif not isinstance(evt["ts"], str):
        problems.append(f"ts 不是字符串（{type(evt['ts']).__name__}）")

    if "parent" not in evt:
        problems.append("parent 缺失（无父事件应显式写 null）")
    elif evt["parent"] is not None and not isinstance(evt["parent"], str):
        problems.append(f"parent 既不是字符串也不是 None（{type(evt['parent']).__name__}）")

    if "id" not in evt:
        problems.append("id 缺失")
    elif not isinstance(evt["id"], str) or not evt["id"].strip():
        problems.append("id 不是非空字符串")

    sv = evt.get("schema_version")
    if isinstance(sv, bool) or not isinstance(sv, int):
        problems.append(f"schema_version 缺失或不是整数（{sv!r}）")
    return problems


# ── 读取（截断容错 + 未知事件保留） ─────────────────────────────────────


def load_events(path: Any) -> List[Dict[str, Any]]:
    """读取 JSONL 事件流，返回事件列表。

    容错契约（B3a 验收项）：
    * **残行丢弃**：最后一行可能是半截 JSON（进程崩溃现场），逐行解析失败即跳过，
      正常返回前面的全部事件，绝不抛异常；
    * **未知事件保留**：``type`` 不在 :data:`KNOWN_EVENT_TYPES` 的事件原样返回
      （旧版本读新事件、新版本读旧事件都要能跑）；
    * 结构性坏行（非 JSON 对象 / 缺 type）跳过；payload 非 dict 时补空 dict，
      保证下游 ``payload.get`` 安全；
    * 文件不存在 / 不可读 → 返回空列表。
    """
    events: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except Exception:
                    # 半截 JSON / 坏行：丢弃该行，保留前面的内容
                    continue
                if not isinstance(evt, dict):
                    continue
                if not isinstance(evt.get("type"), str) or not evt["type"].strip():
                    continue
                if not isinstance(evt.get("payload"), dict):
                    evt["payload"] = {}
                events.append(evt)
    except FileNotFoundError:
        return []
    except OSError:
        return []
    return events


# ── history 重建（与实时维护共用同一语义） ──────────────────────────────


def compose_history(summary_text: Optional[str], messages: List[Dict[str, Any]],
                    limit: int = RESUME_TAIL_MESSAGES) -> List[Dict[str, Any]]:
    """组装有效历史：**摘要（若有）在前 + 最后 ``limit`` 条消息**。

    这是「实时维护」与「resume 重建」共用的唯一一份组装逻辑 ——
    ``loop.py`` 的 ``AgentRunner`` 每轮结束时也调用本函数，两边结果因此可逐条断言。
    ``limit <= 0`` 表示不保留消息（只留摘要）。
    """
    out: List[Dict[str, Any]] = []
    if isinstance(summary_text, str) and summary_text.strip():
        out.append({"role": "system", "content": summary_text})
    tail = [m for m in (messages or []) if isinstance(m, dict)]
    if isinstance(limit, int) and limit > 0:
        tail = tail[-limit:]
    elif limit is not None and limit <= 0:
        tail = []
    out.extend(tail)
    return out


def rebuild_history(events: List[Dict[str, Any]],
                    limit: int = RESUME_TAIL_MESSAGES) -> List[Dict[str, Any]]:
    """从事件流重建「有效历史」，产出与实时 ``AgentRunner.history`` 同构的列表。

    规则（见模块 docstring「history 模型」）：
    1. 收集全部 ``user`` / ``assistant`` 消息事件（按时间序）；
    2. 取**最后一条** ``compact`` 事件的 ``payload["summary"]`` 作为摘要
       （更早的摘要已被它覆盖）；
    3. 返回 ``compose_history(摘要, 消息, limit)``。

    未知事件类型与 tool 事件一律忽略 —— 重建不依赖它们。
    """
    messages: List[Dict[str, Any]] = []
    summary_text: Optional[str] = None
    for evt in events or []:
        if not isinstance(evt, dict):
            continue
        etype = evt.get("type")
        payload = evt.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        if etype == EVENT_USER:
            messages.append({"role": "user", "content": payload.get("content", "")})
        elif etype == EVENT_ASSISTANT:
            messages.append({"role": "assistant", "content": payload.get("content", "")})
        elif etype == EVENT_COMPACT:
            text = payload.get("summary")
            if isinstance(text, str) and text.strip():
                summary_text = text
    return compose_history(summary_text, messages, limit=limit)


# ── 写入（append-only + 降级） ──────────────────────────────────────────


class SessionLog:
    """一次会话的 append-only JSONL 日志。

    * **懒创建**：构造时不碰磁盘，首次 :meth:`append` 才建目录 / 开文件 ——
      AgentRunner 构造与无 key 的 run 都不会产生空日志文件；
    * **写失败降级**：磁盘满 / 权限不足 / 路径被占等任何写入异常都被捕获，
      只打印一次警告并降级为纯内存（``append`` 仍返回事件 id），**绝不中断对话**；
    * ``path`` 指向 ``<workspace_root>/.memory/sessions/<session_id>.jsonl``。
    """

    def __init__(self, workspace_root: Optional[Any] = None,
                 session_id: Optional[str] = None):
        root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        self.workspace_root = root
        self.sessions_dir = root.joinpath(*SESSIONS_SUBDIR)
        self.session_id = session_id or self._new_session_id()
        self._fh = None
        self._degraded = False
        self._warned = False

    @staticmethod
    def _new_session_id() -> str:
        """时间戳 + 短随机段（无新依赖；同一秒内多个实例也能区分）。"""
        return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

    @property
    def path(self) -> Path:
        return self.sessions_dir / f"{self.session_id}.jsonl"

    def append(self, event_type: str, payload: Optional[Dict[str, Any]] = None,
               parent: Optional[str] = None) -> str:
        """追加一条事件，返回事件 id（即使写盘失败也返回 —— 调用方不必分支）。"""
        evt = new_event(event_type, payload, parent=parent)
        self._write_line(evt)
        return evt["id"]

    def _write_line(self, evt: Dict[str, Any]) -> None:
        if self._degraded:
            return
        try:
            # 用内置 open（而非 Path.open）—— 测试可 monkeypatch builtins.open
            # 模拟磁盘满/权限失败；newline="\n" 保证跨平台写出稳定的 LF 字节。
            if self._fh is None:
                self.sessions_dir.mkdir(parents=True, exist_ok=True)
                self._fh = open(str(self.path), "a", encoding="utf-8", newline="\n")
            try:
                line = json.dumps(evt, ensure_ascii=False)
            except (TypeError, ValueError):
                # payload 里混入不可序列化对象：降级为字符串存根，事件本身不丢
                evt = dict(evt)
                evt["payload"] = {"_unserializable": str(evt.get("payload"))[:500]}
                line = json.dumps(evt, ensure_ascii=False)
            self._fh.write(line + "\n")
            self._fh.flush()
        except Exception as e:
            self._degraded = True
            if not self._warned:
                self._warned = True
                print(f"{_YELLOW}[warn] 会话日志写入失败，本会话降级为纯内存"
                      f"（对话不受影响）: {type(e).__name__}: {e}{_RESET}", file=sys.stderr)

    def close(self) -> None:
        """关闭文件句柄（幂等）。已降级 / 从未写入时为空操作。"""
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None


# ── [B3b] 会话管理：列举 / 安全分叉 / 删除 / 清理 ────────────────────────


def _sessions_dir(workspace_root: Optional[Any] = None) -> Path:
    """会话日志目录：``<workspace_root>/.memory/sessions``。"""
    root = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
    return root.joinpath(*SESSIONS_SUBDIR)


def _event_ts(evt: Any) -> str:
    """取事件的 ``ts`` 字符串（旧格式缺 ts 时返回空串，绝不抛）。"""
    if isinstance(evt, dict):
        ts = evt.get("ts")
        if isinstance(ts, str) and ts.strip():
            return ts
    return ""


def _file_mtime_iso(path: Path) -> str:
    """文件 mtime 的 ISO 字符串（事件缺 ts 时的兜底时间戳）。"""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return ""


def list_sessions(workspace_root: Optional[Any] = None) -> List[Dict[str, Any]]:
    """扫描 ``.memory/sessions/*.jsonl``，返回会话元信息列表（按最后活动倒序）。

    每项::

        {session_id, path, created_at, last_ts, event_count, message_count,
         first_user_text, forked_from}

    * ``created_at`` / ``last_ts`` 取首/末事件的 ``ts``（旧格式缺 ts 时回落
      文件 mtime）；``last_ts`` 同时是排序键（ISO 字符串可直接字典序比较）；
    * ``first_user_text`` = 首条 ``user`` 事件 content 的前 60 字（无则空串）；
    * ``forked_from`` = 首条 ``session_start`` payload 里的 ``forked_from``
      （由 :func:`fork_session` 写入），非 dict 时为 None；
    * 空文件 / 读失败 / 坏文件一律跳过，绝不抛异常。
    """
    out: List[Dict[str, Any]] = []
    try:
        files = sorted(_sessions_dir(workspace_root).glob("*.jsonl"))
    except OSError:
        return out
    for path in files:
        try:
            events = load_events(path)
            if not events:
                continue
            first_ts = _event_ts(events[0]) or _file_mtime_iso(path)
            last_ts = _event_ts(events[-1]) or first_ts
            message_count = 0
            first_user_text = ""
            for evt in events:
                etype = evt.get("type")
                if etype in (EVENT_USER, EVENT_ASSISTANT):
                    message_count += 1
                if not first_user_text and etype == EVENT_USER:
                    payload = evt.get("payload")
                    content = payload.get("content") if isinstance(payload, dict) else None
                    if isinstance(content, str) and content.strip():
                        first_user_text = content[:60]
            forked_from = None
            first = events[0]
            if first.get("type") == EVENT_SESSION_START:
                payload = first.get("payload")
                if isinstance(payload, dict) and isinstance(payload.get("forked_from"), dict):
                    forked_from = dict(payload["forked_from"])
            out.append({
                "session_id": path.stem,
                "path": path,
                "created_at": first_ts,
                "last_ts": last_ts,
                "event_count": len(events),
                "message_count": message_count,
                "first_user_text": first_user_text,
                "forked_from": forked_from,
            })
        except Exception:
            continue
    out.sort(key=lambda item: item.get("last_ts") or "", reverse=True)
    return out


def find_safe_fork_index(events: List[Dict[str, Any]], idx: int) -> int:
    """把 fork 点向前对齐到最近的安全位置：前缀 ``events[:idx]`` 中
    ``tool_call`` / ``tool_result`` 必须配对完整（不切断配对）。

    配对判据：``tool_result.parent`` 指向 ``tool_call`` 的事件 id（旧格式
    事件可能缺 id，无法判定时按「不阻断」处理）。返回 ``<= idx`` 的最大安全
    索引（可能为 0）；``idx`` 越界或非整数时按「全量」收敛。
    """
    events = list(events or [])
    total = len(events)
    if not isinstance(idx, int) or isinstance(idx, bool):
        idx = total
    idx = max(0, min(idx, total))
    while idx > 0:
        paired = set()
        for evt in events[:idx]:
            if not isinstance(evt, dict) or evt.get("type") != EVENT_TOOL_RESULT:
                continue
            parent = evt.get("parent")
            if isinstance(parent, str) and parent:
                paired.add(parent)
            payload = evt.get("payload")
            if isinstance(payload, dict):
                tcid = payload.get("tool_call_id")
                if isinstance(tcid, str) and tcid:
                    paired.add(tcid)
        orphan = -1
        for pos in range(idx):
            evt = events[pos]
            if not isinstance(evt, dict) or evt.get("type") != EVENT_TOOL_CALL:
                continue
            eid = evt.get("id")
            payload = evt.get("payload")
            tcid = payload.get("tool_call_id") if isinstance(payload, dict) else None
            if isinstance(eid, str) and eid and eid not in paired:
                orphan = pos
                break
            if not isinstance(eid, str) and isinstance(tcid, str) and tcid and tcid not in paired:
                orphan = pos
                break
        if orphan < 0:
            return idx
        idx = orphan
    return 0


def fork_session(workspace_root: Optional[Any], session_id: str,
                 at_event: Optional[Any] = None) -> Tuple[Optional[Path], int, str]:
    """从既有会话 fork 出新会话文件，返回 ``(新文件路径 or None, 事件数, 提示文案)``。

    * 源不存在 / 无事件 → ``(None, 0, 原因)``；
    * ``at_event`` 缺省 = 全部事件；给值时收敛到 ``[1, len]`` 再经
      :func:`find_safe_fork_index` 对齐到安全边界（不切断 tool 配对），
      对齐后为 0 → ``(None, 0, "没有可安全分叉的位置")``；
    * 新文件用新的 session_id；首条 ``session_start`` 的 payload 追加
      ``{"forked_from": {"session_id": 源id, "at_event": 对齐后索引}}``；
      若源首条不是 ``session_start``（旧格式 / 损坏）→ 新造一条带
      ``forked_from`` 的 ``session_start`` 置于文件头；
    * **只读源文件**（绝不写源）；逐行 ``json.dumps(ensure_ascii=False)``
      写入，LF 行尾。
    """
    src_path = _sessions_dir(workspace_root) / f"{session_id}.jsonl"
    if not src_path.is_file():
        return None, 0, f"源会话不存在: {session_id}"
    events = load_events(src_path)
    if not events:
        return None, 0, f"源会话没有可用事件: {session_id}"

    total = len(events)
    if at_event is None:
        idx = total
    else:
        try:
            wanted = int(at_event)
        except (TypeError, ValueError):
            return None, 0, f"fork 点必须是整数: {at_event!r}"
        idx = find_safe_fork_index(events, min(max(1, wanted), total))
        if idx <= 0:
            return None, 0, "没有可安全分叉的位置"

    prefix = events[:idx]
    fork_marker = {"session_id": session_id, "at_event": idx}
    out_events: List[Dict[str, Any]] = []
    first = prefix[0]
    if isinstance(first, dict) and first.get("type") == EVENT_SESSION_START:
        head = dict(first)
        payload = head.get("payload")
        payload = dict(payload) if isinstance(payload, dict) else {}
        payload["forked_from"] = fork_marker
        head["payload"] = payload
        out_events.append(head)
        out_events.extend(prefix[1:])
    else:
        out_events.append(new_event(EVENT_SESSION_START, {"forked_from": fork_marker}))
        out_events.extend(prefix)

    new_id = SessionLog._new_session_id()
    dst_dir = _sessions_dir(workspace_root)
    dst_path = dst_dir / f"{new_id}.jsonl"
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        with open(str(dst_path), "w", encoding="utf-8", newline="\n") as fh:
            for evt in out_events:
                fh.write(json.dumps(evt, ensure_ascii=False) + "\n")
            fh.flush()
    except Exception as e:
        return None, 0, f"写入新会话失败: {type(e).__name__}: {e}"
    copied = len(out_events)
    return dst_path, copied, f"已分叉出新会话 {new_id}（复制 {copied} 条事件，fork 点 {idx}）"


def remove_session(workspace_root: Optional[Any], session_id: str) -> bool:
    """删除单个会话文件（存在则 unlink）；失败 / 不存在返回 False。"""
    try:
        path = _sessions_dir(workspace_root) / f"{session_id}.jsonl"
        if not path.is_file():
            return False
        path.unlink()
        return True
    except OSError:
        return False


def prune_sessions(workspace_root: Optional[Any] = None, keep: int = 20) -> List[str]:
    """保留最近 ``keep`` 个会话（按 ``last_ts`` 倒序），删除其余。

    返回被删除的 session_id 列表。**本函数直接执行删除**；dry-run（预演）
    由 CLI 层负责（``ky session prune`` 默认只列不删）。
    """
    try:
        keep_n = max(0, int(keep))
    except (TypeError, ValueError):
        keep_n = 20
    removed: List[str] = []
    for item in list_sessions(workspace_root)[keep_n:]:
        if remove_session(workspace_root, item["session_id"]):
            removed.append(item["session_id"])
    return removed
