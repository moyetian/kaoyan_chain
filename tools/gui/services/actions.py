# -*- coding: utf-8 -*-
"""
GUI 后端动作服务（不依赖 Qt 控件）

把「调用后端模块并整理回显文本」从 MainWindow 抽出来：
  * 可在无图形环境下单测（CI 里不必起窗口）
  * 每个动作返回**可读文本**而不是直接往控件里写，
    MainWindow 只负责把文本贴到哪个面板

所有函数都只做「执行 + 返回文本」，异常一律转成可读文本，
不让 GUI 因后端异常弹异常栈。
"""

from __future__ import annotations

import contextlib
import io
import logging
import re
from pathlib import Path
from typing import Optional, Tuple

_LOG = logging.getLogger(__name__)

#: 识别「文本里是否已含中文」——含中文的错误多为后端已本地化的提示，不再改写
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

#: [P1 修复·2026-10-08] 英文报错特征 → 中文提示（含 GUI 内恢复路径）。
#: 键一律小写、做子串匹配；顺序即优先级（具体特征在前，泛化特征在后）。
_ERROR_SIGNATURE_MAP: Tuple[Tuple[str, str], ...] = (
    ("timed out", "请求超时：上游响应过慢或网络抖动，请稍后重试；仍失败可到「设置中心」更换模型或 Base URL。"),
    ("timeout", "请求超时：上游响应过慢或网络抖动，请稍后重试；仍失败可到「设置中心」更换模型或 Base URL。"),
    ("connection refused", "网络连接被拒绝：请检查本机网络与代理设置后重试。"),
    ("name resolution", "域名解析失败：请检查网络连接（DNS/代理）后重试。"),
    ("getaddrinfo", "域名解析失败：请检查网络连接（DNS/代理）后重试。"),
    ("max retries exceeded", "网络连接失败：请检查本机网络后重试；若使用中转 API，可到「设置中心」核对 Base URL。"),
    ("connection aborted", "网络连接被中断：多为网络波动，请稍后重试。"),
    ("remotedisconnected", "上游连接被中断：多为服务端波动，请稍后重试。"),
    ("remote end closed", "上游连接被中断：多为服务端波动，请稍后重试。"),
    ("connectionerror", "网络连接失败：请检查本机网络后重试；若使用中转 API，可到「设置中心」核对 Base URL。"),
    ("sslerror", "HTTPS 证书校验失败：请检查网络环境（代理/抓包工具）后重试。"),
    ("certificate", "HTTPS 证书校验失败：请检查网络环境（代理/抓包工具）后重试。"),
    ("429", "上游接口限流（429）：请求过于频繁，请稍等片刻再重试。"),
    ("rate limit", "上游接口限流：请求过于频繁，请稍等片刻再重试。"),
    ("too many requests", "上游接口限流：请求过于频繁，请稍等片刻再重试。"),
    ("401", "API Key 无效或未授权（401）：请在「设置中心 → AI 大模型设置」核对 Key。"),
    ("unauthorized", "API Key 无效或未授权：请在「设置中心 → AI 大模型设置」核对 Key。"),
    ("authenticationerror", "API Key 无效或未授权：请在「设置中心 → AI 大模型设置」核对 Key。"),
    ("402", "账户额度不足（402）：请到服务商控制台充值后重试。"),
    ("insufficient", "账户额度不足：请到服务商控制台充值后重试。"),
    ("quota", "账户额度不足：请到服务商控制台充值后重试。"),
    ("errno 2", "文件不存在或已被移动：请重新选择文件。"),
    ("no such file", "文件不存在或已被移动：请重新选择文件。"),
    ("filenotfounderror", "文件不存在或已被移动：请重新选择文件。"),
    ("errno 13", "文件被占用或无访问权限：请关闭占用该文件的程序（如 Office/PDF 阅读器）后重试。"),
    ("permissionerror", "文件被占用或无访问权限：请关闭占用该文件的程序（如 Office/PDF 阅读器）后重试。"),
    ("access is denied", "文件被占用或无访问权限：请关闭占用该文件的程序后重试。"),
    ("jsondecodeerror", "数据解析失败：目标文件或配置内容可能损坏；如为 ky_config.json，请到「设置中心」重新保存。"),
    ("expecting value", "数据解析失败：目标文件或配置内容可能损坏；请到「设置中心」重新保存配置。"),
    ("modulenotfounderror", "运行依赖缺失：请确认程序安装完整后重启。"),
    ("importerror", "运行依赖缺失：请确认程序安装完整后重启。"),
    ("keyerror", "配置或数据缺少必要字段：请到「设置中心」重新保存配置后重试。"),
    ("database is locked", "本地数据库繁忙或被占用：请稍后重试（勿同时开多个窗口）。"),
    ("operationalerror", "本地数据库繁忙或被占用：请稍后重试（勿同时开多个窗口）。"),
    ("memoryerror", "内存不足：请关闭其他大内存程序后重试。"),
)


def _map_error_text(text: str) -> str:
    """把一段错误文本映射为中文提示；无法识别时给中文框架 + 截断原文。"""
    t = (text or "").strip()
    # 完整 traceback：取最后一行（通常即 "XxxError: msg"），不再整段外泄
    if "Traceback (most recent call last)" in t:
        lines = [ln for ln in t.splitlines() if ln.strip()]
        if lines:
            t = lines[-1].strip()
    if not t:
        return "未知错误（无错误详情）。"
    low = t.lower()
    for pattern, msg in _ERROR_SIGNATURE_MAP:
        if pattern in low:
            return msg
    # 纯中文错误（后端已本地化）：原样保留，避免二次改写
    if _CJK_RE.search(t):
        return t
    # 未识别的英文报错：中文框架 + 截断原文，兼顾可读性与可排查性
    if len(t) > 180:
        t = t[:180] + "…"
    return f"发生未预期错误：{t}（可稍后重试；若反复出现，请到「设置中心」检查配置）"


def humanize_error(err) -> str:
    """[P1 修复·2026-10-08] 异常/报错文本 → 面向考生的中文提示。

    为什么：GUI 的错误通道此前把 Python 异常原文（英文类名 + 堆栈式消息）
    直出到聊天区/情报面板，考生看不懂也不知道怎么办。本函数按异常类型与
    常见报错特征做中文映射，并给出 GUI 内恢复路径（设置中心 / 重新选文件 /
    稍后重试），不再指向 CLI 命令。

    输入可以是 Exception 实例或字符串；若字符串已是「[×] 中文标签: 英文异常」
    形态，保留前缀与标签、只翻译英文尾巴（避免调用方再拼出双层前缀）。
    """
    if isinstance(err, BaseException):
        return _map_error_text(f"{type(err).__name__}: {err}")
    text = _ANSI_RE.sub("", str(err or "")).strip()
    if not text:
        return "未知错误（无错误详情）。"
    # 保留既有失败前缀（[×]/[x]/❌），只处理其余部分
    prefix = ""
    m = re.match(r"^(\[[×xX]\]|❌)\s*", text)
    if m:
        prefix = m.group(1) + " "
        text = text[m.end():]
    # 「中文标签[:：] 尾巴」→ 保留标签、翻译尾巴；否则整段映射
    m2 = re.match(r"^(.*?[:：]\s*)(.+)$", text, re.S)
    if m2 and _CJK_RE.search(m2.group(1)):
        return prefix + m2.group(1) + _map_error_text(m2.group(2))
    return prefix + _map_error_text(text)


def run_action_capture(alias: str, interactive: bool = False, extra: Optional[dict] = None) -> str:
    """执行 TUI 中枢的某个动作并捕获其标准输出。

    GUI 复用 TUI 的动作分发（单实现），而不是另写一套后端调用 ——
    这也是「三端不一致」类问题的结构性解法。
    """
    try:
        try:
            from tui_navigator import execute_action
        except ImportError:  # pragma: no cover
            from tools.tui_navigator import execute_action  # type: ignore

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            execute_action(alias, interactive=interactive, extra=extra)
        return buf.getvalue().strip()
    except Exception as exc:
        _LOG.warning("动作执行失败: %s -> %s", alias, exc)
        return f"[×] 模块 [{alias}] 执行异常: {humanize_error(exc)}"


def rag_search(query: str, top_k: int = 5, source_filter: str = "") -> str:
    """本地知识库检索，返回可读结果文本。

    [为什么复用而不是重写] ``ky rag`` 的检索与**显式降级提示**都在
    ``cli.commands.search.run_rag_search`` 里（词法+向量 RRF 融合、向量不可用
    时打印降级原因、知识库不存在时给「先 ingest 再 index」两步引导）。此处
    只做「调用 + 捕获 stdout」，不碰检索逻辑，也不美化任何降级话术 ——
    R2/R3 仿真里这两条命令的缺口正是**入口不可达**，不是实现缺失。
    """
    try:
        try:
            from cli.commands.search import run_rag_search as _run_rag
        except ImportError:  # pragma: no cover
            from tools.cli.commands.search import run_rag_search as _run_rag  # type: ignore

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = _run_rag(query, top_k=top_k,
                            source_filter=source_filter or None)
        out = buf.getvalue().strip()
        if not out:
            # run_rag_search 空查询时打印用法，这里兜底补上，避免面板一片空白
            return ("[!] 请输入检索关键词后再试（本地知识库检索，"
                    "内容来自院校库与各科 参考资料/ 的切片索引）。")
        return out
    except Exception as exc:
        _LOG.warning("本地检索失败: %s", exc)
        return f"[×] 本地检索异常: {humanize_error(exc)}"


def build_index(show_progress: bool = True) -> str:
    """本地知识库建索引，返回可读结果文本。

    复用 ``ky index`` 的唯一实现 ``run_index_build``（只读本地文件、不联网、
    不代建资料；资料为空时如实提示「没有找到可索引的文档」）。GUI 只负责把
    它的输出搬到面板上。
    """
    try:
        try:
            from cli.commands.search import run_index_build as _run_index
        except ImportError:  # pragma: no cover
            from tools.cli.commands.search import run_index_build as _run_index  # type: ignore

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _run_index(enable_vector=True, show_progress=show_progress)
        out = buf.getvalue().strip()
        return out or "[!] 建索引未产生任何输出，请检查本地资料目录是否为空。"
    except Exception as exc:
        _LOG.warning("建索引失败: %s", exc)
        return f"[×] 建索引异常: {humanize_error(exc)}"


def ingest_file(workspace_root: Path, path: str, subject: str = "pro") -> str:
    """把一份真题/讲义切片入库，返回可读结果文本。"""
    try:
        try:
            from skills import material_ingestion
        except ImportError:  # pragma: no cover
            from tools.skills import material_ingestion  # type: ignore

        pipe = material_ingestion.MaterialIngestionPipeline(workspace_root=workspace_root)
        res = pipe.ingest_file(Path(path), subject=subject)
        if res.get("success"):
            return (f"[√] 切片入库成功：识别 {res['count']} 道题目 "
                    f"(选择 {res['choices']} / 填空 {res['blanks']} / 大题 {res['essays']})\n"
                    f"   生成路径: {res['target_path']}")
        return f"[×] 切片入库失败: {res.get('msg')}"
    except Exception as exc:
        return f"[×] 切片入库异常: {humanize_error(exc)}"


def diff_syllabus(workspace_root: Path, old_path: str, new_path: str) -> str:
    """比对两份考纲并落盘研报，返回可读结果文本。

    严禁在未提供新大纲时伪造变动 —— 本函数要求两个真实文件路径。
    """
    try:
        try:
            from intelligence.syllabus_diff import get_syllabus_diff_generator
            from intelligence.models import current_exam_year
        except ImportError:  # pragma: no cover
            from tools.intelligence.syllabus_diff import get_syllabus_diff_generator  # type: ignore
            from tools.intelligence.models import current_exam_year  # type: ignore

        y_new = current_exam_year()
        gen = get_syllabus_diff_generator()
        target_school, target_major = _target_labels(workspace_root, Path(new_path))
        # [P1 修复·2026-10-08] 命名随实（infer_diff_naming）：旧版直接取 config
        # 志愿命名 —— 对公共课考纲（01-数学/02-英语/03-思想政治理论）比对却落盘
        # 「考纲变动分析_<志愿校>_<志愿专业>_<年>.md」，张冠李戴。路径能推导出
        # 「全国统考 + 科目名」时优先于 config；专业课路径推导不出则保持原样。
        # 与 CLI `ky fetch diff` / REPL `/diff` 的「推导 > config 回退」口径一致。
        try:
            try:
                from intelligence.syllabus_diff import infer_diff_naming
            except ImportError:
                from tools.intelligence.syllabus_diff import infer_diff_naming  # type: ignore
            _s_inf, _m_inf = infer_diff_naming(old_path, new_path)
            if _s_inf:
                target_school = _s_inf
            if _m_inf:
                target_major = _m_inf
        except Exception:
            pass  # 推导失败不阻断比对，保持 config 命名
        rep = gen.compare_files(
            old_file=Path(old_path), new_file=Path(new_path),
            school=target_school, major=target_major,
            year_old=y_new - 1, year_new=y_new,
        )
        saved = gen.save_diff_report(rep)
        m = rep["metrics"]
        # [P0-10 修复·0 点谎报] 解析到 0 考点时结果不可信，头部标记降级 + 附警示
        _parse_warning = str(rep.get("parse_warning") or "").strip()
        _head = "[!] 考纲 Diff 完成（解析警示）" if _parse_warning else "[√] 考纲 Diff 完成"
        _msg = (f"{_head} (动荡率 {m['volatility_percentage']}% / "
                f"{m['stability_grade']})：新增 {m['added_count']} | "
                f"剔除 {m['removed_count']} | 调整 {m['modified_count']} | "
                f"不变 {m['unchanged_count']}\n   研报路径: {saved}")
        if _parse_warning:
            _msg += f"\n   ⚠️ {_parse_warning}"
        return _msg
    except Exception as exc:
        return f"[×] 考纲比对异常: {humanize_error(exc)}"


def compare_schools(workspace_root: Path, school1: str, school2: str,
                    major: str = "") -> Tuple[str, str]:
    """双校对标，返回 ``(研报文本, 落盘路径或空串)``。"""
    try:
        # [W12 P0-1] intelligence 包解析三端统一（此前 CLI/GUI/TUI 各写一套）
        try:
            from intel_imports import resolve_intel_import
        except ImportError:  # pragma: no cover
            from tools.intel_imports import resolve_intel_import  # type: ignore

        # 读取工作区配置以传递大模型与搜索 API 设置
        api_config = None
        cfg_path = workspace_root / "ky_config.json"
        if cfg_path.exists():
            try:
                import json as _js
                api_config = _js.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                api_config = None

        comp = resolve_intel_import().get_school_comparator().compare(
            school1_query=school1, school2_query=school2,
            major_keyword=major, save_report=True,
            api_config=api_config
        )
        return str(comp.get("terminal_report", "")), str(comp.get("saved_path") or "")
    except Exception as exc:
        return f"[×] 双校对标执行异常: {humanize_error(exc)}", ""


def make_error_quiz(workspace_root: Path, subject: str = "pro",
                    count: int = 3) -> Tuple[str, str]:
    """生成错题盲盒自测卷，返回 ``(展示文本, 落盘路径或空串)``。"""
    try:
        try:
            from skills import exam_composer
        except ImportError:  # pragma: no cover
            from tools.skills import exam_composer  # type: ignore

        res = exam_composer.compose_exam_paper(
            subject=subject, count=count, include_weak=True, save_file=True)
        saved = str(res.get("saved_path", "") or "")
        paper_text = res.get("formatted_paper") or res.get("content") or ""
        display = f"\n\n【错题盲盒自测卷】已生成！\n{'=' * 50}\n{paper_text}\n"
        if saved:
            display += f"\n> 自测卷已落盘: `{saved}`"
        return display, saved
    except Exception as exc:
        return f"[×] 组卷异常: {humanize_error(exc)}", ""


def _target_labels(workspace_root: Path, new_path: Path) -> Tuple[str, str]:
    """从配置取目标院校/专业（失败则用占位符与文件名）。"""
    try:
        try:
            from state import load_config
        except ImportError:  # pragma: no cover
            from tools.state import load_config  # type: ignore

        cfg = load_config(workspace_root)
        sp = cfg.get("study_plan", {}) if isinstance(cfg, dict) else {}
        school = sp.get("school") or cfg.get("target_school") or "目标院校"
        major = sp.get("major") or cfg.get("target_major") or new_path.stem
        return str(school), str(major)
    except Exception:                       # pragma: no cover
        return "目标院校", new_path.stem


__all__ = [
    "build_index",
    "compare_schools",
    "diff_syllabus",
    "humanize_error",
    "ingest_file",
    "make_error_quiz",
    "rag_search",
    "run_action_capture",
]
