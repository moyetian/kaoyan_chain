# -*- coding: utf-8 -*-
"""UT4 四沙箱批次 · 建档/规划链路缺陷修复回归（P1×1 + P2×6）。

来源：2026-09-30/10-01 UT4 三角色沙箱实测（理论物理 custom 自命题数学 /
西医临床 nomath 三科 / 通信工程数二四科），修复落点：

  P1      BUG-1  preset 建档后 01/02/03 薄弱点雷达未实例化，建档痛点静默丢失
                 （tools/study_planner.py seed_radars_from_plan）
  P2-1    BUG-3  04-专业课/AGENTS.md 缺失时建档静默跳过（update_subject_agents）
  P2-2    P1-4① 今日任务「研考倒计时」沿用建档日快照不重算（ensure_subject_today_task）
  P2-3    P2-1   mount --apply 后磁盘今日任务残留「待导入」（material_scanner 刷新钩子）
  P2-4    BUG-2  ky plan 输入 custom 后的死胡同文案
  P2-5    BUG-5  custom 数学科目显示名与「官方考纲」白名单文案失真
  P2-6    BUG-6  目标分区间原文在 plan/config 数据链的保留（截断发生在看板层，
                 由另一批次修复；本文件钉住数据链不截断）

全部用例只读 / 本地夹具：不落盘真实工作区、不联网、不触碰真实 ky_config.json。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tools.study_planner as sp  # noqa: E402
from tools.study_planner import (  # noqa: E402
    _days_left_now, ensure_subject_today_task, is_custom_math_key,
    run_study_plan_wizard, seed_radars_from_plan, update_subject_agents,
)
from tools.skills.material_scanner import scan_and_mount_materials  # noqa: E402


# ═════════════════════ P1：seed_radars_from_plan 模板实例化 ═════════════════════

_MATH_RADAR_TPL = """# 数学模块掌握度雷达

| 模块名称 | 预估分值 | 当前评级 | 核心卡点与错因 |
|---|---|---|---|
| 函数、极限与连续 | 10-15 | 未测 | 待首次自测评估 |
"""


def _plan_custom_math_four_subjects(**over):
    """四科 preset 方案基线（中性虚构身份；math_key=custom 对齐 UT4 物理沙箱）。"""
    plan = {
        "school": "示例理工大学", "major": "070201 理论物理",
        "math_key": "custom", "math_name": "数学（院校自命题）",
        "eng_key": "eng1", "eng_name": "英语一 (201)",
        "pro_name": "813 量子力学", "pro_type": "custom",
        "math_weakness": "级数收敛判定", "eng_weakness": "主谓一致与插入语拆解",
        "pol_weakness": "史纲时间线混淆", "pro_weakness": "算符对易关系推导",
        "math_hours": 2.5, "eng_hours": 2.0, "pol_hours": 1.0, "pro_hours": 2.5,
        "math_books": "暂未放置实体资料（私教按目标院校自命题大纲出题，严禁虚构书目）",
        "eng_books": "真题", "pol_books": "讲义", "pro_books": "教材",
        "exam_date": (date.today() + timedelta(days=79)).isoformat(),
        "days_left": 80,
    }
    plan.update(over)
    return plan


def test_ut4_p1_seed_radars_instantiates_from_template(tmp_path, capsys):
    """P1：工作文件缺失但同名 .template.md 存在 → 先实例化再注入建档卡点。"""
    st = tmp_path / "01-数学" / "_状态"
    st.mkdir(parents=True)
    (st / "薄弱点雷达.template.md").write_text(_MATH_RADAR_TPL, encoding="utf-8")
    eng_st = tmp_path / "02-英语" / "_状态"
    eng_st.mkdir(parents=True)
    (eng_st / "薄弱点雷达.template.md").write_text(
        "# 英语能力雷达与长难句卡片\n\n| 题型模块 | 熟练评级 | 主要失分原因 |\n"
        "|---|---|---|\n| 事实细节题 | 未测 | 待首次真题自测评估 |\n", encoding="utf-8")

    plan = _plan_custom_math_four_subjects()
    seed_radars_from_plan(plan, workspace_root=tmp_path)

    math_radar = (st / "薄弱点雷达.md").read_text(encoding="utf-8")
    assert "建档摸底核心卡点" in math_radar
    assert "级数收敛判定" in math_radar, "建档痛点未注入实例化后的雷达"
    assert "函数、极限与连续" in math_radar, "模板正文应随实例化保留"
    eng_radar = (eng_st / "薄弱点雷达.md").read_text(encoding="utf-8")
    assert "主谓一致与插入语拆解" in eng_radar
    # 04 学情档案无模板无文件 → 走最小骨架兜底，不应抛异常
    assert (tmp_path / "04-专业课" / "学情档案.md").exists()
    assert "建档摸底核心卡点" in (tmp_path / "04-专业课" / "学情档案.md").read_text(encoding="utf-8")
    assert "从模板实例化" in capsys.readouterr().out


def test_ut4_p1_seed_radars_minimal_fallback_without_template(tmp_path, capsys):
    """P1：工作文件与模板双缺 → 生成最小可用骨架并显式告警（不再静默丢失）。"""
    plan = _plan_custom_math_four_subjects()
    seed_radars_from_plan(plan, workspace_root=tmp_path)

    for rel in ("01-数学/_状态/薄弱点雷达.md", "02-英语/_状态/薄弱点雷达.md",
                "03-思想政治理论/_状态/薄弱点雷达.md", "04-专业课/学情档案.md"):
        fp = tmp_path / rel
        assert fp.exists(), f"{rel} 未生成最小骨架"
        text = fp.read_text(encoding="utf-8")
        assert "建档摸底核心卡点" in text, f"{rel} 最小骨架未注入建档卡点"
    out = capsys.readouterr().out
    assert "最小可用骨架" in out, "双缺场景必须显式告警而非静默生成"
    assert "init_workspace" in out


def test_ut4_p1_seed_radars_existing_file_stays_idempotent(tmp_path):
    """阴性对照：既有雷达文件不被重建，注入幂等（重复调用不产生重复行）。"""
    st = tmp_path / "01-数学" / "_状态"
    st.mkdir(parents=True)
    (st / "薄弱点雷达.md").write_text(_MATH_RADAR_TPL, encoding="utf-8")
    plan = _plan_custom_math_four_subjects()
    seed_radars_from_plan(plan, workspace_root=tmp_path)
    seed_radars_from_plan(plan, workspace_root=tmp_path)

    text = (st / "薄弱点雷达.md").read_text(encoding="utf-8")
    assert text.count("建档摸底核心卡点") == 1
    # 未放置模板时也不得残留 .template 拷贝副作用
    assert not (st / "薄弱点雷达.template.md").exists()


def test_ut4_p1_pro_placeholder_weakness_not_injected(tmp_path):
    """痛点为默认占位话术时不注入卡点行（_weakness_is_real 既有口径保持）。"""
    plan = _plan_custom_math_four_subjects(
        math_weakness="待首次自测诊断 (从零建立题型雷达)",
        eng_weakness="待首次自测诊断 (从零建立长难句与题型雷达)",
        pol_weakness="待首次自测诊断", pro_weakness="待首次自测诊断")
    seed_radars_from_plan(plan, workspace_root=tmp_path)
    for rel in ("01-数学/_状态/薄弱点雷达.md", "04-专业课/学情档案.md"):
        text = (tmp_path / rel).read_text(encoding="utf-8")
        assert "建档摸底核心卡点" not in text, f"{rel} 占位痛点被当成真实卡点注入"


# ═════════════════════ P2-1：04-专业课/AGENTS.md 缺失重建 ═════════════════════

def test_ut4_p2_rebuild_pro_agents_when_missing(tmp_path, capsys):
    """P2：04-专业课/AGENTS.md 缺失 → 按中性骨架重建、告警，且回填 plan 真实值。"""
    plan = _plan_custom_math_four_subjects(
        school="示例理工大学", major="070201 理论物理")
    update_subject_agents(plan, workspace_root=tmp_path)

    fp = tmp_path / "04-专业课" / "AGENTS.md"
    assert fp.exists(), "缺失的 04 AGENTS.md 未被重建"
    text = fp.read_text(encoding="utf-8")
    assert "专业课通用私教系统协议" in text, "重建文件缺少协议骨架"
    assert "示例理工大学" in text, "目标院校未按 plan 回填"
    assert "813 量子力学" in text, "专业课科目名未按 plan 回填"
    assert "教材" in text, "白名单未按 plan 回填"
    out = capsys.readouterr().out
    assert "04-专业课/AGENTS.md 缺失" in out, "重建时必须打印告警"


def test_ut4_p2_existing_pro_agents_not_overwritten_by_skeleton(tmp_path, capsys):
    """阴性对照：文件已存在时维持现行为（自改行保留、无重建告警）。"""
    pro_dir = tmp_path / "04-专业课"
    pro_dir.mkdir(parents=True)
    custom = (
        "# 自改版专业课协议\n\n### 学员自定义配置区（使用者自填）\n"
        "- **目标院校**：`旧值`\n- **专业代码与名称**：`旧专业`\n"
        "- **专业课科目代码与名称**：`旧科目`\n- **满分与目标成绩**：`目标 130 分`\n"
        "- **指定白名单资料**：`我的书单`\n- **核心薄弱点**：`考生自填卡点`\n"
    )
    (pro_dir / "AGENTS.md").write_text(custom, encoding="utf-8")

    update_subject_agents(_plan_custom_math_four_subjects(), workspace_root=tmp_path)
    text = (pro_dir / "AGENTS.md").read_text(encoding="utf-8")
    assert "自改版专业课协议" in text, "文件存在时不得整体重建覆盖"
    assert "考生自填卡点" in text, "非模板行（考生自改内容）必须保留"
    assert "813 量子力学" in text, "既有文件的科目名仍应按 plan 正常更新"
    assert "AGENTS.md 缺失" not in capsys.readouterr().out


# ═════════════════════ P2-2：今日任务倒计时按当日重算 ═════════════════════

def test_ut4_p2_days_left_now_recomputes_from_exam_date():
    """_days_left_now：exam_date 可解析时按当日重算并钳非负；异常时回退快照。"""
    plan = {"exam_date": (date.today() + timedelta(days=79)).isoformat(),
            "days_left": 80}
    assert _days_left_now(plan) == 79, "应按当日重算而非沿用快照 80"
    assert _days_left_now({"exam_date": date.today().isoformat()}) == 0
    # 考试日已过 → 钳非负
    assert _days_left_now({"exam_date": "2020-01-01"}) == 0
    # 非法日期回退快照（同样钳非负）
    assert _days_left_now({"exam_date": "bad", "days_left": 80}) == 80
    assert _days_left_now({"exam_date": "bad", "days_left": -3}) == 0
    assert _days_left_now({}) >= 0


def test_ut4_p2_roll_call_rewrites_countdown_for_new_day(tmp_path):
    """P2：跨日报到重写今日任务时，文件头倒计时必须按当日重算（旧快照 80 → 79）。"""
    plan = _plan_custom_math_four_subjects()
    task_dir = tmp_path / "02-英语" / "_状态"
    task_dir.mkdir(parents=True)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    (task_dir / "今日任务.md").write_text(
        f"# 今日英语任务 ({yesterday})\n\n"
        f"> 研考倒计时：80 天 ｜ 当前阶段：强化题型攻坚阶段 ｜ 今日目标用时：120 分钟\n",
        encoding="utf-8")

    res = ensure_subject_today_task(plan, "eng", workspace_root=tmp_path)
    assert res["status"] in ("created", "overwritten"), res
    text = (task_dir / "今日任务.md").read_text(encoding="utf-8")
    today_str = datetime.now().strftime("%Y-%m-%d")
    assert f"({today_str})" in text.splitlines()[0], "跨日报到后日期应为当日"
    assert "研考倒计时：79 天" in text, "倒计时未按当日重算（仍为建档快照 80）"
    assert "研考倒计时：80 天" not in text


def test_ut4_p2_same_day_task_file_preserved(tmp_path):
    """阴性对照：当日文件存在时原样保留（勾选/编辑不被覆盖，既有行为回归）。"""
    plan = _plan_custom_math_four_subjects()
    task_dir = tmp_path / "02-英语" / "_状态"
    task_dir.mkdir(parents=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    original = (f"# 今日英语任务 ({today_str})\n\n"
                f"> 研考倒计时：999 天 ｜ 当前阶段：X ｜ 今日目标用时：1 分钟\n\n"
                f"| 模块 | 任务内容 | 预计用时 | 完成状态 |\n|---|---|---|---|\n"
                f"| 词汇破冰 | 考生自改任务 | 1 分钟 | [x] |\n")
    (task_dir / "今日任务.md").write_text(original, encoding="utf-8")

    res = ensure_subject_today_task(plan, "eng", workspace_root=tmp_path)
    assert res["status"] == "exists"
    assert (task_dir / "今日任务.md").read_text(encoding="utf-8") == original


# ═════════════════════ P2-3：mount --apply 后刷新今日任务 ═════════════════════

def _prepare_mount_ws(tmp_path):
    """构造 mount 场景：既有当日今日任务（含「待导入」）、就绪大纲、一份真实资料。"""
    cfg = {"study_plan": {
        "school": "", "major": "", "math_key": "none", "math_name": "不考数学",
        "eng_key": "eng1", "eng_name": "英语一 (201)",
        "pro_name": "812 信号与系统", "pro_type": "custom",
        "pro_hours": 2.0, "pro_weakness": "卷积推导",
        "pro_books": "暂未放置实体资料，且尚未导入考试大纲（请从目标院校研究生院官网下载后放入 04-专业课/考试大纲.md；导入前不得宣称按纲出题）",
    }}
    (tmp_path / "ky_config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    pro = tmp_path / "04-专业课"
    (pro / "参考资料").mkdir(parents=True)
    # [CI 修复·平台相关假绿] 内容必须 ≥ scan_subject_materials 的 min_size=50 字节：
    # 此前 48 字节的夹具只在 Windows 上通过（write_text 的 \n→\r\n 转换使其变 51
    # 字节），Linux/macOS 上 48 字节被当空占位过滤 → 白名单为空 → CI 必红。
    (pro / "参考资料" / "真题回忆_2024.md").write_text(
        "# 812 真题回忆\n\n1. 求系统输出响应。\n2. 求冲激响应 h(t)。\n",
        encoding="utf-8")
    # 大纲就绪（无占位标记）→ mount 后「待导入」替换条件成立
    (pro / "考试大纲.md").write_text(
        "# 812 信号与系统 · 考试大纲\n\n## 一、信号与系统基本概念\n"
        "- 掌握：连续与离散信号分类\n- 掌握：线性时不变系统性质\n",
        encoding="utf-8")
    st = pro / "_状态"
    st.mkdir(parents=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    (st / "今日任务.md").write_text(
        f"# 今日专业课任务 ({today_str})\n\n"
        f"> 研考倒计时：79 天 ｜ 当前阶段：强化题型攻坚阶段 ｜ 今日目标用时：120 分钟\n\n"
        f"| 模块 | 任务内容 | 预计用时 | 完成状态 |\n|---|---|---|---|\n"
        f"| 核心知识点 | 聚焦专业课核心知识体系，完成首日题型规范度摸底 | 36 分钟 | [ ] |\n"
        f"| 习题精练 | （⚠️ 专业课大纲与真题待导入）选取经典大题 2~3 道动笔完整书写 | 60 分钟 | [ ] |\n"
        f"| 订正归档 | 在终端输入「交作业」 | 24 分钟 | [ ] |\n",
        encoding="utf-8")
    return cfg


def test_ut4_p2_mount_apply_refreshes_pending_import_text(tmp_path):
    """P2：mount --apply 后磁盘今日任务即时去「待导入」并引用白名单真题。"""
    _prepare_mount_ws(tmp_path)
    res = scan_and_mount_materials(workspace_root=tmp_path, apply=True,
                                   auto_scout_school=False)
    assert res["success"] and res["applied"]

    text = (tmp_path / "04-专业课" / "_状态" / "今日任务.md").read_text(encoding="utf-8")
    assert "专业课大纲与真题待导入" not in text, "mount --apply 后磁盘今日任务仍残留待导入"
    assert "白名单【真题回忆_2024.md】" in text, "题源短语未替换为白名单真题"
    # 外科式替换：其余行与表格结构保留
    assert "订正归档" in text and "[ ]" in text
    # config 白名单已写回
    cfg = json.loads((tmp_path / "ky_config.json").read_text(encoding="utf-8"))
    assert "真题回忆_2024.md" in cfg["study_plan"]["pro_books"]


def test_ut4_p2_mount_scan_only_never_touches_today_task(tmp_path):
    """阴性对照：默认只读预览（apply=False）不得改写今日任务文件。"""
    _prepare_mount_ws(tmp_path)
    before = (tmp_path / "04-专业课" / "_状态" / "今日任务.md").read_text(encoding="utf-8")
    res = scan_and_mount_materials(workspace_root=tmp_path, apply=False,
                                   auto_scout_school=False)
    assert res["success"] and not res["applied"]
    after = (tmp_path / "04-专业课" / "_状态" / "今日任务.md").read_text(encoding="utf-8")
    assert before == after, "只读预览改写了今日任务文件"


# ═════════════════════ P2-4：ky plan custom 死胡同文案 ═════════════════════

def _wizard_input_seq(m_choice: str) -> list:
    """交互向导的完整输入序列（维度 1~7 共 36 项，m_choice 注入数学科目位）。"""
    return [
        "2027", "", "",                          # 维度1：年份/初试日/阶段
        "示例理工大学", "070201 理论物理", "",       # 维度2：院校/专业/备选
        m_choice, "eng1", "3", "813 量子力学",     # 维度2：数学/英语/专业课类型/科目名
        "60 分", "级数收敛判定",                    # 维度3：数学摸底/痛点
        "摸底 58 分", "主谓一致与插入语拆解",          # 维度3：英语摸底/痛点
        "摸底 50 分", "史纲时间线混淆",              # 维度3：政治摸底/痛点
        "摸底 75 分", "算符对易关系推导",            # 维度3：专业课摸底/痛点
        "", "", "", "",                          # 维度4：数/英/政/专白名单
        "1", "", "", "", "", "",                 # 维度5：作息档/总时长/分科小时×4
        "", "",                                  # 维度6：周/月休整
        "1", "", "", "", "", "",                 # 维度7：风格/分科目标×4/总分
    ]


@pytest.mark.parametrize("m_choice", ["custom"])
def test_ut4_p2_custom_math_hint_points_to_real_path(m_choice, monkeypatch, capsys):
    """P2：无效数学科目输入的告警必须指向真实可行路径，不再是死胡同。"""
    monkeypatch.setattr(sp, "apply_study_plan", lambda *a, **k: None)
    monkeypatch.setattr(sp, "print_study_plan_summary", lambda *a, **k: None)
    answers = iter(_wizard_input_seq(m_choice))
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))

    with contextlib.redirect_stdout(io.StringIO()) as buf:
        plan = run_study_plan_wizard(interactive=True)
    out = buf.getvalue()
    assert "init_workspace.py" in out and "[5]" in out, "指引未指向 init_workspace 完整向导 [5]"
    assert "preset" in out, "指引未提及非交互 preset 配置"
    assert "请重跑 ky plan 并选择对应项" not in out, "死胡同文案残留"
    # 回退兜底保持：无效输入不得原样写入方案
    assert plan["math_key"] == "math2"


# ═════════════════════ P2-5：custom 显示名与白名单文案 ═════════════════════

def test_ut4_p2_custom_math_display_name_defaults():
    """P2：is_custom_math_key 判定口径（custom 真 / 统考键与空值假）。"""
    assert is_custom_math_key("custom") is True
    assert is_custom_math_key("CUSTOM") is True
    for k in ("math1", "math2", "math3", "math396"):
        assert is_custom_math_key(k) is False
    assert is_custom_math_key("none") is False
    assert is_custom_math_key("") is False
    assert is_custom_math_key(None) is False


def test_ut4_p2_custom_math_preset_display_name(monkeypatch):
    """P2：preset 建档 custom 时科目名默认「数学（院校自命题）」，用户自定义名不覆盖。"""
    monkeypatch.setattr(sp, "apply_study_plan", lambda *a, **k: None)
    monkeypatch.setattr(sp, "print_study_plan_summary", lambda *a, **k: None)
    plan = run_study_plan_wizard(interactive=False, preset_data=_plan_custom_math_four_subjects())
    assert plan["math_name"] == "数学（院校自命题）"

    # 通用旧名「数学」也应升级为自命题名（UT4 物理沙箱实测 math_name=数学）
    plan2 = run_study_plan_wizard(
        interactive=False, preset_data=_plan_custom_math_four_subjects(math_name="数学"))
    assert plan2["math_name"] == "数学（院校自命题）"

    # 考生已自定义的科目名不覆盖
    plan3 = run_study_plan_wizard(
        interactive=False, preset_data=_plan_custom_math_four_subjects(math_name="613 数学分析"))
    assert plan3["math_name"] == "613 数学分析"


def test_ut4_p2_custom_math_whitelist_copy_in_agents(tmp_path):
    """P2：custom 数学的白名单缺省文案不得宣称「官方考纲」（update_subject_agents 链）。"""
    m_dir = tmp_path / "01-数学"
    m_dir.mkdir(parents=True)
    (m_dir / "AGENTS.md").write_text(
        "# 数学私教协议\n\n### 学员配置区\n"
        "- **考试科目**：`数学`\n- **核心教材与白名单**：`占位`\n"
        "- **核心薄弱点**：`待首次自测诊断 (从零建立学情雷达)`\n",
        encoding="utf-8")
    plan = _plan_custom_math_four_subjects()
    plan["math_books"] = ""  # 触发 _book_defaults 缺省文案分支

    update_subject_agents(plan, workspace_root=tmp_path)
    text = (m_dir / "AGENTS.md").read_text(encoding="utf-8")
    assert "按目标院校自命题大纲出题" in text, "自命题数学白名单文案未按自命题口径"
    assert "官方考纲" not in text, "自命题数学不得宣称按官方考纲出题"


def test_ut4_p2_custom_math_whitelist_copy_in_mount_preview(tmp_path):
    """P2：material_scanner 挂载预览对 custom 数学同样使用自命题口径文案。"""
    cfg = {"study_plan": {
        "school": "", "major": "", "math_key": "custom",
        "math_name": "数学（院校自命题）",
        "eng_key": "eng1", "eng_name": "英语一 (201)",
        "pro_name": "813 量子力学", "pro_type": "custom",
        "eng_books": "真题", "pol_books": "讲义", "pro_books": "教材",
    }}
    (tmp_path / "ky_config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp_path / "01-数学" / "参考资料").mkdir(parents=True)

    res = scan_and_mount_materials(workspace_root=tmp_path, apply=False,
                                   auto_scout_school=False)
    math_changes = [c for c in res["changes"] if c.get("field") == "study_plan.math_books"]
    assert math_changes, "custom 数学白名单变更未进预览"
    assert "按目标院校自命题大纲出题" in math_changes[0]["new"]
    assert "官方考纲" not in math_changes[0]["new"]


# ═════════════════════ P2-6：目标分区间原文保留（数据链不截断） ═════════════════════

def test_ut4_p2_target_range_kept_verbatim_in_plan(monkeypatch):
    """P2：plan 采集与存储层保留区间原文「120-130 分」（截断仅发生在看板渲染层）。"""
    monkeypatch.setattr(sp, "apply_study_plan", lambda *a, **k: None)
    monkeypatch.setattr(sp, "print_study_plan_summary", lambda *a, **k: None)
    base = _plan_custom_math_four_subjects(pro_target="120-130 分")
    plan = run_study_plan_wizard(interactive=False, preset_data=dict(base))
    assert plan["pro_target"] == "120-130 分", "preset 建档截断了目标分区间原文"

    # 非交互且未显式提供时，默认值本身也是区间原文
    plan2 = run_study_plan_wizard(
        interactive=False, preset_data=_plan_custom_math_four_subjects())
    del plan2["pro_target"]
    assert plan2.get("pro_target", "120-130 分") == "120-130 分"
