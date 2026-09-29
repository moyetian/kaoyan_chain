# -*- coding: utf-8 -*-
"""D 节「白名单三拒绝」回归锁定（护理三端实测问题修复报告 · 附：勿回归）。

报告要求把三处**正确的**白名单行为用单测钉死：
  1. ``exam`` 无题源拒绝：完全空工作区 → 不产出试卷、不落盘，
     输出「3 步启动」可执行上手引导（而非占位假卷）；
  2. ``map`` 占位拒绝：占位大纲（含 P2-12 的 308 护理骨架）→ 拒绝产出假图谱；
  3. ``admission`` 离线兜底声明：无 API 时报录比 / 一志愿保护 / 口碑 / 院线
     一律标「未核验」—— 该口径已在
     ``tests/test_agentic_research.py::TestUnlistedUniversitiesProfiling``
     中锁定，本文件不重复，仅覆盖 1 / 2。

测试数据一律中性占位；全部走 tmp_path 沙箱，不触碰主仓库任何文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from skills import error_logger                    # noqa: E402
from tools import syllabus_manager as sm           # noqa: E402
from tools.skills import exam_composer as exam     # noqa: E402


# ── 1. exam：完全无题源 → 拒绝组卷 + 上手引导 ────────────────────────

def _patch_roots(tmp_path, monkeypatch):
    """把组卷链路与错题扫描的 ROOT 指向沙箱（写入侧全部落 tmp_path）。"""
    monkeypatch.setattr(exam, "ROOT", tmp_path)
    monkeypatch.setattr(error_logger, "ROOT", tmp_path)


def test_exam_refuses_completely_empty_workspace(tmp_path, monkeypatch):
    _patch_roots(tmp_path, monkeypatch)
    res = exam.compose_exam_paper(subject="pro", count=2,
                                  include_weak=False, save_file=False)

    assert res["success"] is False
    assert res["refused"] is True
    assert res["reason"] == "no_material"
    assert res["items"] == []
    assert res["saved_path"] is None
    assert res["formatted_paper"] == res["content"]

    content = res["content"]
    assert "未组卷" in content
    assert "白名单题源门禁" in content      # 拒绝用考纲模板句冒充试卷
    assert "3 步启动" in content            # 可执行上手引导
    assert "ky ingest" in content
    # 不落盘：沙箱内不得出现任何试卷文件
    assert not list(tmp_path.rglob("*试卷*"))


def test_exam_negative_control_one_real_card_composes(tmp_path, monkeypatch):
    """阴性对照：只要有 1 张真实白名单卡，门禁必须放行（拒绝不得过度触发）。"""
    _patch_roots(tmp_path, monkeypatch)
    ref = tmp_path / "04-专业课" / "参考资料"
    ref.mkdir(parents=True)
    (ref / "题库切片_示例.md").write_text(
        "# 题库切片 · 示例\n\n"
        "### 【题号 1】论述题（满分: 15 分）\n"
        "- **【题源出处】**：`示例真题2024`\n"
        "#### 1. 试题原题\n"
        "示例题干：论述实践与认识的辩证关系及其方法论意义。\n",
        encoding="utf-8")

    res = exam.compose_exam_paper(subject="pro", count=1,
                                  include_weak=False, save_file=False)
    assert res["success"] is True
    assert res.get("refused") is not True
    assert res["source_breakdown"]["whitelist"] == 1


# ── 2. map：占位大纲（含 308 护理骨架）→ 拒绝假图谱 ─────────────────

def test_map_refuses_placeholder_nursing_skeleton(tmp_path, monkeypatch):
    from tools.skills import knowledge_map as km

    sm.apply_syllabus_selection(
        math_key="math2", eng_key="eng2", pro_type="308",
        pro_name="308 护理综合", workspace_root=tmp_path, auto_write=True)
    body = (tmp_path / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8")
    assert km._is_placeholder_syllabus(body) is True

    monkeypatch.setattr(km, "ROOT", tmp_path)
    data = km.build_knowledge_map("pro")
    assert data.get("syllabus_placeholder") is True
    assert data.get("total_points") == 0
    assert data.get("chapters") == []
    assert "占位" in (data.get("syllabus_warning") or "")
    # 终端报表必须显式拒绝生成图谱（而不是输出 0% 掌握率的假雷达）
    assert "图谱不可用" in km.format_knowledge_map_table("pro")
