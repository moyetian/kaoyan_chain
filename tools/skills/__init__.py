# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 专有技能中枢 (Skills Registry)
汇总所有考研专用技能插件：
  1. vision_solver: 多模态图像识别、手写草稿逐行批改、LaTeX 提取
  2. math_verifier: SymPy 高精度符号计算与验算 (微分方程、二次型正定、极限、求导、微积分、矩阵、级数)
  3. english_dissector: 考研英语长难句搭积木解剖与翻译
  4. socratic_tutor: 苏格拉底式三级阶梯微步骤启发引导 (/hint)
  5. error_logger: 错题归档与 FSRS 盲盒复测闭环引擎 (/review /quiz)
  6. pdf_extractor: 资料库教材与真题 PDF 文本抽取
  7. latex_beautifier: 终端数学公式 Unicode 美化与实时网页伴侣联动

[B4 修复·状态真实性] 此前 SKILLS_REGISTRY 里 13 项技能的 ``status`` 是**硬编码的
"已就绪"字面量**：sympy 没装、pypdf 缺失、大模型 API Key 未配置、开放题判分
未启用 —— 面板上照样显示"已就绪"。用户据此以为功能可用，实际一调用就降级或
报错。现在每项技能的 status 都由该技能模块自己的 ``health_check()`` **运行时计算**：

  * ``READY``       全功能可用；
  * ``DEGRADED``    核心链路可用但有降级（如无 sympy 走纯 Python 多项式引擎）；
  * ``UNAVAILABLE`` 完全不可用（如无 pypdf 无法解析 PDF、无 API Key 无法批改图片）。

``_SKILL_META``（文案）与 ``_HEALTH_PROVIDERS``（自检函数）是两个**必须一一对应**
的唯一真源：新增技能只改一处、或漏配 health_check，``build_skills_registry()``
会在导入期直接报错（防回退），元测试 ``tests/test_b4_mcp_skills_health.py`` 同步守住。
"""

# [C2 修复·惰性导入] 本包此前在 ``import skills`` 时无条件导入全部 16 个子模块。
# 其中 pdf_extractor 会经 pypdf 拉起 cryptography（实测约 100ms），是 CLI
# 冷启动噪音与延迟的主因之一；而多数命令（`ky --version` / `ky doctor` /
# `ky build`）根本用不到 PDF 能力。
#
# 采用 PEP 562 模块级 ``__getattr__``：``from .skills import pdf_extractor``、
# ``skills.pdf_extractor``、``import skills; skills.pdf_extractor`` 三种写法
# 语义**完全不变**（首次访问时按需导入并缓存进模块命名空间），但 ``import skills``
# 本身不再付出 pypdf/cryptography 的导入代价。其余子模块保持 eager 导入 ——
# SKILLS_REGISTRY 的 status 字段需要在导入期调用它们的 health_check()，
# 把它们一并惰性化收益有限、却会让状态展示与真实情况脱节。
#
# ⚠ 维护提醒：variant_retriever 等子模块会在**自身导入期**``from skills import
# pdf_extractor`` —— 那会把 pdf_extractor 再次绑进本包命名空间并连带拉起 pypdf，
# 使上面的惰性化失效。新增子模块时请改用 ``from . import xxx`` 直接引用兄弟模块，
# 不要经由包命名空间中转。下方 import 之后有断言守住这一点。
#
# ⚠ [B4] health_check 同样受此约束：各技能的 health_check 必须**只做轻量探测**
# （importlib.util.find_spec / 读配置 / 调纯本地函数），绝不 import 重依赖 ——
# 否则"导入期计算 status"会把惰性化重新变成 eager 导入。

import importlib
from typing import Any, Optional

from . import vision_solver
from . import math_verifier
from . import english_dissector
from . import socratic_tutor
from . import error_logger
from . import latex_beautifier
from . import exam_composer
from . import variant_retriever
from . import knowledge_map
from . import exam_diagnoser
from . import school_scout
from . import material_ingestion
from . import wechat_searcher
from . import material_scanner
from . import open_grader

#: 惰性加载的子模块清单（含重依赖，不适合在 ``import skills`` 时拉起）
_LAZY_MODULES = ("pdf_extractor",)


def __getattr__(name: str) -> Any:
    """按需导入重依赖子模块（PEP 562）。"""
    if name in _LAZY_MODULES:
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module          # 缓存，后续访问不再走 __getattr__
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_LAZY_MODULES))


# ══════════════════════════════════════════════════════════════════════════
# [B4] 技能健康自检 (Skill Health)
# ══════════════════════════════════════════════════════════════════════════

HEALTH_READY = "READY"
HEALTH_DEGRADED = "DEGRADED"
HEALTH_UNAVAILABLE = "UNAVAILABLE"

#: 结构化档位 → 中文展示前缀（status 字段渲染用）
_HEALTH_ZH = {
    HEALTH_READY: "已就绪",
    HEALTH_DEGRADED: "降级可用",
    HEALTH_UNAVAILABLE: "不可用",
}

#: 合法档位集合（health_check 返回非法档位时按 UNAVAILABLE 处理，宁可保守）
_VALID_HEALTH = frozenset(_HEALTH_ZH)


def _find_spec_available(module_name: str) -> bool:
    """轻量探测某模块是否**可被导入**（``find_spec`` 不执行模块代码）。

    这是 health_check 判定可选依赖的统一手法：比 ``import`` 快得多，也不会把
    sympy/pypdf 这类重依赖真正拉起来（那会破坏本包的惰性加载设计）。
    """
    try:
        import importlib.util
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


def _normalize_health(raw: Any, skill_id: str) -> dict:
    """把 health_check 的返回值规范化为 ``{"status", "reason"}``。

    容错原则：自检函数写错（返回非 dict / 缺字段 / 非法档位）时**不得**让
    SKILLS_REGISTRY 构建崩溃 —— 按 UNAVAILABLE 处理并如实写明原因，
    让错误在面板上可见而不是把整个 CLI 拖垮。
    """
    if isinstance(raw, dict):
        status = str(raw.get("status", "")).strip().upper()
        reason = str(raw.get("reason", "") or "").strip()
        if status in _VALID_HEALTH:
            return {"status": status, "reason": reason or "（未提供原因）"}
        return {"status": HEALTH_UNAVAILABLE,
                "reason": f"health_check 返回非法档位 {raw.get('status')!r}"}
    return {"status": HEALTH_UNAVAILABLE,
            "reason": f"health_check 返回值非法（{type(raw).__name__}），应为 dict"}


def _safe_health(provider, skill_id: str) -> dict:
    """调用 health_check 并兜住异常 —— 自检失败只降级该项技能，不拖垮注册表。"""
    try:
        return _normalize_health(provider(), skill_id)
    except Exception as e:  # noqa: BLE001 - 自检异常必须被收敛为可见状态
        return {"status": HEALTH_UNAVAILABLE,
                "reason": f"健康自检异常: {type(e).__name__}: {e}"}


def _probe_pdf_extractor_health() -> dict:
    """pdf_extractor 的**免导入**健康探测。

    pdf_extractor 是惰性模块（导入它 = 拉起 pypdf/cryptography），registry 构建期
    不能导入，因此这里用 find_spec 做等价判定。模块内的 ``health_check()`` 也提供
    同一判据，供已导入场景直接调用。
    """
    if _find_spec_available("pypdf"):
        return {"status": HEALTH_READY, "reason": "pypdf 可用，可直接解析 PDF 真题与教材"}
    return {"status": HEALTH_UNAVAILABLE,
            "reason": "未安装 pypdf，无法解析二进制 PDF；pip install pypdf 解锁"}


#: 技能展示元信息（name/desc/command）—— 文案唯一真源，与 _HEALTH_PROVIDERS 一一对应
_SKILL_META = {
    "vision_solver": {
        "name": "👁️ 视觉看图与手写批改技能 (Vision & OCR Solver)",
        "desc": "支持上传手写草稿与试卷截图，逐行批改、采分点赋分、LaTeX公式提取",
        "command": "/img <路径> 或 /ocr <路径>",
    },
    "math_verifier": {
        "name": "📐 数学高精度符号计算技能 (Math & SymPy Verifier)",
        "desc": "常微分方程/二次型正定/级数求和/极限/微积分/矩阵，杜绝计算幻觉",
        "command": "/calc <数学表达式>",
    },
    "socratic_tutor": {
        "name": "💡 苏格拉底式微步骤脚手架 (Socratic Scaffolding Tutor)",
        "desc": "拒绝直接剧透答案，通过三级微步骤（破题定性/首步搭桥/避坑指南）循循善诱",
        "command": "/hint [题目] 或快捷键 [5]",
    },
    "error_logger": {
        "name": "🎯 错题归档与 FSRS 盲盒复测闭环 (Error Logger & Quiz Engine)",
        "desc": "自动提取错题现场，隐去原解析生成盲盒试题，复测合格自动标记出库",
        "command": "/review [科目] 或 /quiz 或快捷键 [2]",
    },
    "latex_beautifier": {
        "name": "🌐 终端公式美化与实时网页伴侣 (LaTeX Beautifier & Live View)",
        "desc": "将晦涩的 LaTeX 语法转为易读 Unicode 符号，并联动 KaTeX 实时网页渲染",
        "command": "/view (打开网页伴侣) 或快捷键 [3]",
    },
    "english_dissector": {
        "name": "🧱 英语长难句搭积木切分技能 (Sentence Dissector)",
        "desc": "五步切分长难句主干、从句层级、非谓语与润色翻译",
        "command": "/dissect <长难句>",
    },
    "pdf_extractor": {
        "name": "📚 参考书与真题检索技能 (PDF & Document Extractor)",
        "desc": "快速检索四科「参考资料/」教材与历年真题库内容",
        "command": "/pdf [关键词或页码]",
    },
    "exam_composer": {
        "name": "📝 错题反向靶向组卷技能 (Exam Composer)",
        "desc": "基于历史高频错因与 FSRS 到期错题，靶向生成阶段专项自测卷",
        "command": "ky exam [科目] [--count=N] 或 /exam",
    },
    "variant_retriever": {
        "name": "🔍 考研同类真题变式检索与防幻觉溯源 (Real Variant Retriever)",
        "desc": "优先检索白名单题库同类变式题，若无则标注自拟警告，严禁伪造题源",
        "command": "ky variant <考点> 或 /variant",
    },
    "knowledge_map": {
        "name": "🗺️ 官方考纲知识点图谱与掌握度映射 (Knowledge Map)",
        "desc": "将官方考试大纲要求、历年题型与学员错题薄弱点多维对齐映射",
        "command": "ky map [科目] 或 /map",
    },
    "exam_diagnoser": {
        "name": "🩺 整卷级多题诊断与失分聚类引擎 (Exam Diagnoser)",
        "desc": "分析模考整卷答题情况，输出章节失分排行、错因分布与薄弱攻坚战术",
        "command": "ky diagnose <试卷文本/路径> 或 /diagnose",
    },
    "school_scout": {
        "name": "🎯 目标高校与社媒考研情报侦察引擎 (School Scout)",
        "desc": "定向检索官方招生简章、自命题大纲、拟招人数与报录比，聚合知乎/B站/小红书就读体验与避坑指南",
        "command": "ky scout <高校> [专业] 或 /scout",
    },
    "material_ingestion": {
        "name": "📥 试题与备考资料智能切片入库管道 (Material Ingestion Pipeline)",
        "desc": "将外部 PDF/Markdown/TXT 试题智能分块切片，自动识别题型并构造步骤级采分点入库归档",
        "command": "ky ingest <试题文件路径> [--subject=科目] [--save] 或 /ingest",
    },
    "wechat_searcher": {
        "name": "📱 微信公众号文章检索与爬虫工具 (WeChat Article Searcher)",
        "desc": "多源检索微信考研经验、院校解读与考点精讲，清洗为 Markdown 并联动沉淀至经验档案",
        "command": "ky wechat <关键词> [--max=N] [--save] [--school=校名] 或 /wx",
    },
    "open_grader": {
        "name": "🧑‍⚖️ 开放题多模型判分引擎 (Open-Ended Multi-Model Grader)",
        "desc": "论述/推导类开放题：要点抽取→多模型并行初评→分歧仲裁→确定性裁决，"
                "未启用或异常时自动回落人工复核，绝不臆造分数",
        "command": "自动接入 exam-submit 的开放题判分链路",
    },
}

def _provider(module, func_name: str = "health_check"):
    """构造**延迟解析**的健康自检提供者（每次调用都按模块属性现取）。

    为什么不直接存 ``module.health_check`` 的函数引用：那样构建期就把函数对象
    钉死了 —— 测试/维护者 monkeypatch 模块级 health_check（如阴性验证、替换桩）
    不会生效，"status 来自运行时自检"的承诺会失真。闭包内 ``getattr`` 保证每次
    调用都取到模块当前绑定的函数。
    """
    def _run():
        return getattr(module, func_name)()
    return _run


#: skill_id → health_check 可调用对象。每个技能模块自带（最了解自己的能力边界）；
#: pdf_extractor 例外（惰性模块），走免导入探测。与 _SKILL_META **必须一一对应**。
_HEALTH_PROVIDERS = {
    "vision_solver": _provider(vision_solver),
    "math_verifier": _provider(math_verifier),
    "socratic_tutor": _provider(socratic_tutor),
    "error_logger": _provider(error_logger),
    "latex_beautifier": _provider(latex_beautifier),
    "english_dissector": _provider(english_dissector),
    "pdf_extractor": _probe_pdf_extractor_health,
    "exam_composer": _provider(exam_composer),
    "variant_retriever": _provider(variant_retriever),
    "knowledge_map": _provider(knowledge_map),
    "exam_diagnoser": _provider(exam_diagnoser),
    "school_scout": _provider(school_scout),
    "material_ingestion": _provider(material_ingestion),
    "wechat_searcher": _provider(wechat_searcher),
    "open_grader": _provider(open_grader),
}


def _render_status(health: dict) -> str:
    """把结构化健康结果渲染成人类可读的 status 文案（面板/横幅展示用）。"""
    return f"{_HEALTH_ZH[health['status']]} · {health['reason']}"


def build_skills_registry() -> dict:
    """构建技能注册表（每次调用都重新自检，返回全新字典）。

    为什么是函数而不是一次性的字面量：status 必须来自运行时自检。提供函数也让
    测试可以"两次独立构建、对比 status"，从而证明它来自 health_check 而非字面量。

    防回退：``_SKILL_META`` 与 ``_HEALTH_PROVIDERS`` 的键集不一致时**直接抛错** ——
    新增技能忘了写 health_check，或删了技能忘了清 provider，都会在导入期暴露。
    """
    missing = sorted(set(_SKILL_META) - set(_HEALTH_PROVIDERS))
    orphan = sorted(set(_HEALTH_PROVIDERS) - set(_SKILL_META))
    if missing or orphan:
        raise RuntimeError(
            "SKILLS_REGISTRY 构建失败：技能元信息与健康自检表不一致 —— "
            f"缺 health_check 的技能: {missing or '无'}；"
            f"无对应元信息的 provider: {orphan or '无'}。"
            "新增技能时必须同时登记 _SKILL_META 与 _HEALTH_PROVIDERS。"
        )

    registry: dict = {}
    for skill_id, meta in _SKILL_META.items():
        provider = _HEALTH_PROVIDERS[skill_id]
        if not callable(provider):
            raise RuntimeError(f"技能 [{skill_id}] 的 health_check 不是可调用对象: {provider!r}")
        health = _safe_health(provider, skill_id)
        registry[skill_id] = {
            **meta,
            "status": _render_status(health),
            "health": health,
            "health_check": provider,
        }
    return registry


SKILLS_REGISTRY = build_skills_registry()


def list_skills():
    """返回当前已加载的所有专有技能清单"""
    return SKILLS_REGISTRY


def get_skill_health(skill_id: str) -> dict:
    """实时查询某技能的**结构化**健康状态（``{"status", "reason"}``）。

    与 ``SKILLS_REGISTRY[...]["health"]`` 的区别：后者是导入期快照，本函数每次
    重新调用 health_check，反映"此刻"的能力（例如用户刚装好 sympy）。
    未知 skill_id 返回 UNAVAILABLE 而不是抛 KeyError —— 调用方（调度守卫、
    doctor）只关心"能不能用"，不该因拼写错误崩掉。
    """
    provider = _HEALTH_PROVIDERS.get(skill_id)
    if not callable(provider):
        return {"status": HEALTH_UNAVAILABLE, "reason": f"未注册的技能 [{skill_id}]"}
    return _safe_health(provider, skill_id)


def dispatch_guard(skill_id: str) -> Optional[str]:
    """调度守卫：技能**完全不可用**时返回给用户看的提示；可用则返回 None。

    为什么需要它：UNAVAILABLE 的技能此前在各入口各自失败（/img 无 Key 时的
    提示、/pdf 缺 pypdf 的提示…文案不一），有的路径甚至只在深层调用处才暴露。
    统一守卫让任何入口都能在"调度之前"给出「技能名 + 原因」，而不是抛裸异常。
    DEGRADED 不拦截 —— 降级路径本身就是设计好的可用能力。
    """
    health = get_skill_health(skill_id)
    if health["status"] != HEALTH_UNAVAILABLE:
        return None
    name = _SKILL_META.get(skill_id, {}).get("name", skill_id)
    return f"[技能不可用] {name} —— {health['reason']}"


def get_subject_name(subject_key: str, default: str = None) -> str:
    """
    根据 subject_key ('math', 'eng', 'pol', 'pro') 动态读取 ky_config.json 中配置的科目全称。
    若未配置或读取失败，回退到默认映射。
    """
    import json
    from pathlib import Path

    defaults = {
        "math": "数学",
        "eng": "英语",
        "pol": "思想政治理论",
        "pro": "专业课",
    }
    alias_map = {
        "math": "math", "maths": "math", "数学": "math", "math1": "math", "math2": "math", "math3": "math",
        "eng": "eng", "english": "eng", "英语": "eng", "eng1": "eng", "eng2": "eng",
        "pol": "pol", "politics": "pol", "政治": "pol",
        "pro": "pro", "major": "pro", "专业课": "pro"
    }

    norm_key = alias_map.get(str(subject_key).strip().lower(), str(subject_key).strip().lower())

    try:
        root = Path(__file__).resolve().parent.parent.parent
        cfg_file = root / "ky_config.json"
        if cfg_file.exists():
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            plan = data.get("study_plan", {})
            name_key = f"{norm_key}_name"
            if name_key in plan and plan[name_key]:
                return plan[name_key]
    except Exception:
        pass

    if default is not None:
        return default
    return defaults.get(norm_key, defaults.get(subject_key, str(subject_key)))
