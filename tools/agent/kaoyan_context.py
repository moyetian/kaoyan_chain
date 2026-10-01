# -*- coding: utf-8 -*-
r"""
考研学习链 (ky-cli) · 考研统一上下文 (KaoyanContext)
职责：把「当前科目 / 数学编码 / 目标院校 / 报考专业 / 初试日期 / 辅导风格 /
工作区 / 会话 id」收敛为**一份不可变上下文**，供 Agent 循环、hooks 与系统
提示组装共同消费 —— 杜绝各处各自读盘 ky_config.json 导致的漂移
（如 hook 按目标校 A 拦截、系统提示却挂载了目标校 B 的考情档案）。

设计约定：
- ``frozen=True``：实例不可变；切换科目用 :meth:`with_subject` 返回替换副本。
- ``from_config`` 的 ``active_subject`` / ``math_key`` 解析与 ``loop.run``
  旧内联 ctx 构建**逐字同语义**（K6 行为不变约束），键名与 ``hooks.py``
  现有读取键（``active_subject`` / ``math_key`` / ``user_input``）兼容。
- 初试日期走 ``exam_calendar.resolve_exam_date`` 单一真源，本模块不新增
  任何倒计时/日期推算逻辑。
"""

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from exam_calendar import resolve_exam_date as _resolve_exam_date
except ImportError:  # pragma: no cover - 包式导入上下文
    from tools.exam_calendar import resolve_exam_date as _resolve_exam_date


SUPPORTED_SUBJECTS = frozenset({"math", "eng", "pol", "pro"})


def normalize_subject_key(value: Any, default: str = "math") -> str:
    """把配置/模型输入归一到受支持的科目键。"""
    raw = str(value or "").strip().lower()
    aliases = {
        "maths": "math", "数学": "math", "math1": "math", "math2": "math",
        "math3": "math", "396": "math", "eng": "eng", "english": "eng",
        "英语": "eng", "eng1": "eng", "eng2": "eng", "pol": "pol",
        "politics": "pol", "政治": "pol", "思想政治理论": "pol",
        "pro": "pro", "major": "pro", "专业课": "pro",
    }
    key = aliases.get(raw, raw)
    return key if key in SUPPORTED_SUBJECTS else default


def normalize_school_name(value: Any) -> str:
    """生成用于实体绑定的稳定校名规范形式。"""
    text = re.sub(r"\s+", "", str(value or "")).strip().lower()
    return text


def school_entity_id(value: Any) -> str:
    """为目标院校生成稳定的本地实体 ID，不把自然语言校名直接当主键。"""
    normalized = normalize_school_name(value)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"school:{digest}" if normalized else "school:unknown"


@dataclass(frozen=True)
class KaoyanContext:
    """考研统一上下文（不可变快照）。

    :param active_subject: 当前科目（math/eng/pol/pro）；与旧 ctx 同语义：
        配置缺失时取默认 "math"，显式 null/空串按原样保留（get 语义）。
    :param math_key: 数学科目编码（math1/math2/math3/math396）；仅当配置的
        ``active_subject`` 原始值为 "math" 时解析（缺失回落 "math2"），
        其余科目一律 None —— hooks.py 的考纲红线只在数学科目下生效。
    :param target_school: 目标院校（study_plan.school，去空白）。
    :param target_major: 报考专业（study_plan.major，去空白）。
    :param exam_date: 初试日期（exam_calendar 解析；失败为 None）。
    :param style_name: 辅导风格（study_plan.style_name 或顶层 coaching_style）。
    :param workspace_root: 工作区根（已 resolve 的 Path；未知为 None）。
    :param session_id: 会话 id（resume / GUI 复用时非空）。
    :param source: 上下文构建来源标记（config / default 等，供审计）。
    """

    active_subject: Any = "math"
    math_key: Optional[str] = None
    target_school: str = ""
    target_school_id: str = "school:unknown"
    target_major: str = ""
    exam_date: Optional[date] = None
    style_name: str = ""
    workspace_root: Optional[Path] = None
    session_id: Optional[str] = None
    source: str = "config"

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None, *,
                    workspace_root=None, session_id=None,
                    source: str = "config") -> "KaoyanContext":
        """从 ``ky_config.json`` 配置字典构建统一上下文（fail-safe：任何
        异常字段都收敛为安全默认，绝不因配置写错让 AgentRunner 构造崩溃）。
        """
        cfg = config if isinstance(config, dict) else {}
        plan = cfg.get("study_plan")
        if not isinstance(plan, dict):
            plan = {}

        # active_subject / math_key：与 loop.run 旧内联 ctx 构建逐字同语义 ——
        #   ctx["active_subject"] = cfg.get("active_subject", "math")
        #   math_key 判定用**不带默认**的 cfg.get("active_subject") == "math"
        #   （配置缺键时旧逻辑 math_key 为 None，此处原样保留，不做"顺手修正"）。
        active_subject = cfg.get("active_subject", "math")
        math_key = plan.get("math_key", "math2") if cfg.get("active_subject") == "math" else None

        exam_date: Optional[date] = None
        try:
            exam_date, _date_source = _resolve_exam_date(cfg)
        except Exception:
            exam_date = None

        root: Optional[Path] = None
        if workspace_root:
            try:
                root = Path(workspace_root).resolve()
            except Exception:
                root = None

        return cls(
            active_subject=active_subject,
            math_key=math_key,
            target_school=str(plan.get("school") or "").strip(),
            target_school_id=school_entity_id(plan.get("school") or ""),
            target_major=str(plan.get("major") or "").strip(),
            exam_date=exam_date,
            style_name=str(plan.get("style_name") or cfg.get("coaching_style") or "").strip(),
            workspace_root=root,
            session_id=str(session_id) if session_id else None,
            source=source,
        )

    def with_subject(self, subject: str) -> "KaoyanContext":
        """返回切换科目后的新上下文（frozen 实例不可变，返回替换副本）。

        ``math_key`` 归一规则与 :meth:`from_config` 对齐：切到数学科目时
        保留现值（缺失回落 "math2"），其余科目一律置 None。
        """
        math_key = (self.math_key or "math2") if subject == "math" else None
        return replace(self, active_subject=subject, math_key=math_key)

    def hook_ctx(self, user_input: str = "") -> Dict[str, Any]:
        """导出 hooks.py / PreToolUse / PostToolUse 消费的上下文字典。

        键名与现有读取键严格兼容：``active_subject`` / ``math_key``
        （hooks.py 考纲红线）、``user_input``（school_scope_guard 判定
        「学员是否提及」）；新增 ``target_school`` / ``target_major``
        供院校范围守卫消费。
        """
        return {
            "active_subject": self.active_subject,
            "math_key": self.math_key,
            "user_input": user_input,
            "target_school": self.target_school,
            "target_school_id": self.target_school_id,
            "target_major": self.target_major,
        }
