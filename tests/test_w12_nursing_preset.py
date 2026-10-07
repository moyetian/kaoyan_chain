# -*- coding: utf-8 -*-
"""P2-12 回归：护理考生（308 nursing profile）无预设。

缺陷背景（护理三端实测问题修复报告 第 12 条）：
    nursing 308 只能走 custom 手输，落到通用占位大纲；`today` 专业课
    话术也是通用「经典大题推导」口径，与护理综合以名词解释 / 简答 /
    病例分析为主的题型不符。

修复口径（三处入口 + 两处话术）：
    1. 内置 ``NURSING308_SYLLABUS`` 模块骨架：只列常见模块名（护理学基础 /
       内科 / 外科 / 妇产 / 儿科），带【待自填】告警，**绝不虚构逐章考点分值**；
    2. ``init_workspace`` / ``ky plan`` / ``ky subject`` / GUI onboarding 均可选 308；
    3. ``today`` 专业课话术随 ``pro_name`` 切换（护理口径）。

测试数据一律使用中性占位，不触碰主仓库任何真实文件（全部走 tmp_path）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import init_workspace as iw          # noqa: E402
from tools import study_planner as sp           # noqa: E402
from tools import syllabus_manager as sm        # noqa: E402
from tools.cli import config as cfg_mod         # noqa: E402


@pytest.fixture
def ws(tmp_path):
    """最小工作区：仅建 04-专业课/AGENTS.md（供科目名替换行）。"""
    pro_dir = tmp_path / "04-专业课"
    pro_dir.mkdir(parents=True, exist_ok=True)
    (pro_dir / "AGENTS.md").write_text(
        "# 04-专业课 · 私教协议\n\n"
        "- **专业课科目代码与名称**：`旧科目`\n",
        encoding="utf-8",
    )
    return tmp_path


# ── 1. 大纲骨架：pro_type="308" 与按名识别 ──────────────────────────

def test_syllabus_308_by_type_writes_module_skeleton(ws):
    sm.apply_syllabus_selection(
        math_key="math2", eng_key="eng2", pro_type="308",
        pro_name="308 " + "护理综合", workspace_root=ws, auto_write=True)

    body = (ws / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8")
    # 模块骨架：常见模块名齐全（含「部分院校加考」的妇产 / 儿科）
    for mod in ("护理学基础", "内科护理学", "外科护理学",
                "妇产科护理学", "儿科护理学"):
        assert mod in body, mod
    # 待核验告警：不得把骨架伪装成官方成品考纲
    assert sm.PRO_PLACEHOLDER_MARKER in body
    assert "考纲骨架" in body and "待核验" in body
    assert "研究生院官网" in body
    assert "官方核心考试大纲" not in body

    # 科目名同步写入 AGENTS.md（真源联动）
    agents = (ws / "04-专业课" / "AGENTS.md").read_text(encoding="utf-8")
    assert ("308 " + "护理综合") in agents


def test_syllabus_308_by_name_writes_module_skeleton(ws):
    """custom 手输 nursing code/name 同样命中骨架（按名兜底）。"""
    for name in ("308 " + "护理综合", "护理综合"):
        sm.apply_syllabus_selection(
            math_key="math2", eng_key="eng2", pro_type="custom",
            pro_name=name, workspace_root=ws, auto_write=True)
        body = (ws / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8")
        assert "护理学基础" in body and "内科护理学" in body, name


def test_syllabus_non_nursing_stays_generic_placeholder(ws):
    """阴性对照：非护理科目不得误命中护理骨架。"""
    sm.apply_syllabus_selection(
        math_key="math2", eng_key="eng2", pro_type="custom",
        pro_name="610 法学基础", workspace_root=ws, auto_write=True)

    body = (ws / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8")
    assert "护理学基础" not in body
    assert "内科护理学" not in body
    assert sm.PRO_PLACEHOLDER_MARKER in body  # 仍走通用占位正文


def test_pro_placeholder_example_nursing_branch():
    """占位示例贴合学科：护理考生不得看到通用话术。"""
    assert "护理" in sm._pro_placeholder_example("308 " + "护理综合")
    assert sm._pro_placeholder_example("308 " + "护理综合") != sm._pro_placeholder_example("610 法学基础")


# ── 2. 入口接线：init_workspace 菜单 [4] 与 ky subject 菜单 [4] ───────

def _feed(monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(it))


def test_init_workspace_menu_option4_means_308(monkeypatch):
    # 输入顺序：数学[6 不考] → 英语[1 英二] → 专业课[4 护理]
    _feed(monkeypatch, ["6", "1", "4"])
    math_key, eng_key, pro_type, pro_name = iw.choose_exam_subjects_and_syllabi(
        interactive=True)
    assert (math_key, eng_key, pro_type, pro_name) == (
        "none", "eng2", "308", "308 " + "护理综合")


def test_ky_subject_menu_option4_writes_nursing_skeleton(monkeypatch, ws):
    """`ky subject` → [3 专业课] → [4 护理] 必须落盘护理骨架并持久化 pro_type。"""
    saved = {}
    monkeypatch.setattr(cfg_mod, "ROOT", ws)
    monkeypatch.setattr(cfg_mod, "save_config", lambda cfg: saved.update(cfg))
    _feed(monkeypatch, ["3", "4"])

    cfg_mod.manage_syllabi_cli({})

    body = (ws / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8")
    assert "护理学基础" in body and "内科护理学" in body
    assert sm.PRO_PLACEHOLDER_MARKER in body
    assert saved.get("study_plan", {}).get("pro_type") == "308"
    assert saved.get("study_plan", {}).get("pro_name") == ("308 " + "护理综合")


# ── 3. today 专业课话术随 pro_name 切换 ──────────────────────────────

def _today_pro_task(monkeypatch, tmp_path, pro_name):
    monkeypatch.setattr(
        sp, "_generate_subject_task_content",
        lambda key, name, plan, today, tpl, workspace_root=None: tpl)
    plan = {
        "math_key": "none", "math_name": "不考数学", "pro_name": pro_name,
        "eng_hours": 2.0, "pol_hours": 1.0, "pro_hours": 2.5,
        "pro_books": "暂未放置实体资料（私教严格按【专业课】官方考纲出题，严禁虚构书目）",
    }
    sp.generate_plan_and_today_files(plan, ai_strategy="测试策略", workspace_root=tmp_path)
    return (tmp_path / "04-专业课" / "_状态" / "今日任务.md").read_text(encoding="utf-8")


def test_today_pro_task_uses_nursing_wording(monkeypatch, tmp_path):
    task = _today_pro_task(monkeypatch, tmp_path, "308 " + "护理综合")
    assert "基础护理学与内、外科护理学" in task
    assert "名词解释" in task and "病例分析" in task
    # 通用「经典大题推导」口径不得出现在护理任务里
    assert "经典大题" not in task


def test_today_pro_task_generic_wording_for_non_nursing(monkeypatch, tmp_path):
    """阴性对照：非护理专业课保持通用话术，不被护理分支误伤。"""
    task = _today_pro_task(monkeypatch, tmp_path, "610 法学基础")
    assert "经典大题" in task
    assert "名词解释" not in task and "病例分析" not in task
