# -*- coding: utf-8 -*-
"""2026-10-06 批次·内置统考/联考专业课大纲注册表回归测试。

背景：产品此前仅内置 408/199（完整）、312/308（骨架）、432（自命题示例）；
GUI 向导另有 396/333/law 三个悬空选项（选了落占位、333/law 还因名称正则缺
代码拿错「自命题」指引）。本批次新增 29 个统考/联考专业课大纲注册表
（tools/builtin_pro_syllabi.py）并接线三端菜单 + 三分类占位识别。

覆盖：
1. 注册表完整性（29 科目、条目结构、标题/分类一致、无占位标记）
2. 三分类 pro_exam_category（unified/joint/custom + 边界 + 308/347/333 修正）
3. builtin_pro_syllabus 查询（pro_type 命中 / pro_name 代码提取 / 自命题边界）
4. apply_syllabus_selection 注册表分支落盘（含 432 让位旧示例）
5. 占位文案三档（统考/联考/自命题 × placeholder/missing）
6. 菜单辅助（menu_lines / grouped / prompt_builtin_pro_selection）
7. init_workspace 向导 [6] 入口与 ky subject [6] 入口
8. GUI 向导 combo 注册表项（offscreen，PySide6 缺失自动跳过）

测试数据一律中性，全部走 tmp_path，不触碰主仓库任何真实文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import syllabus_manager as sm          # noqa: E402
from tools.builtin_pro_syllabi import PRO_SYLLABI  # noqa: E402

#: 本批次承诺的 29 科目（4 组内容制作清单，缺一即红）
EXPECTED_CODES = {
    # A 组：医学 / 教育 / 历史
    "306", "307", "311", "313", "333",
    # B 组：农学 / 法硕
    "314", "315", "414", "415", "397", "398", "497", "498",
    # C 组：经济
    "396", "431", "432", "433", "434", "435", "436",
    # D 组：联考语言与专业综合
    "211", "346", "347", "348", "349", "354", "357", "445", "448",
}

#: [2026-10-06 B 批次] 院校自命题预设（R3 仿真考生 A 的 811 信号与系统为必需项，
#: 813/814/816 为公开常见自命题代码）。这4科不属于上面 29 科承诺清单 ——
#: 它们没有全国统一大纲，性质与统考/联考不同，单列一组断言。
SELF_DEFINED_CODES = {"811", "813", "814", "816"}

#: 注册表现有全量代码（统考/联考 29 + 自命题 4）
ALL_CODES = EXPECTED_CODES | SELF_DEFINED_CODES

UNIFIED_CODES = {"306", "307", "311", "313", "333", "314", "315", "414", "415",
                 "396", "397", "398", "497", "498"}
JOINT_CODES = {"211", "346", "347", "348", "349", "354", "357",
               "431", "432", "433", "434", "435", "436", "445", "448"}


def _feed(monkeypatch, values):
    """按序喂给 input() 的测试助手（同 w12 口径）。"""
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(it))


# ── 1. 注册表完整性 ──────────────────────────────────────────────────────

def test_registry_covers_all_expected_codes():
    missing = EXPECTED_CODES - set(PRO_SYLLABI)
    extra = set(PRO_SYLLABI) - ALL_CODES
    assert not missing, f"注册表缺科目: {sorted(missing)}"
    assert not extra, f"注册表多出未承诺科目: {sorted(extra)}"
    # [2026-10-06 B 批次] 仿真考生 A 的 811 必须命中（此前 '811' not in PRO_SYLLABI）
    assert SELF_DEFINED_CODES <= set(PRO_SYLLABI)


def test_registry_entry_structure():
    for code, info in PRO_SYLLABI.items():
        assert {"name", "category", "group", "content"} <= set(info), code
        assert str(info["name"]).startswith(code), f"{code}: name 应以代码开头"
        assert info["category"] in ("unified", "joint", "self_defined"), code
        assert str(info["group"]).strip(), code
        body = info["content"]
        assert body.startswith("# 04-专业课 ·"), code
        for section in ("## 一、试卷结构与分值", "## 二、核心考纲模块",
                        "## 三、作答规范与采分红线"):
            assert section in body, f"{code} 缺 {section}"
        if code in SELF_DEFINED_CODES:
            # 自命题参考框架**必须**含占位标记：一旦被判为「已有真实考纲」，
            # AI 私教就会拿通用框架冒充院校官方大纲（防幻觉红线）。
            assert sm.PRO_PLACEHOLDER_MARKER in body, f"{code} 自命题参考框架缺占位标记"
        else:
            assert sm.PRO_PLACEHOLDER_MARKER not in body, f"{code} 含占位标记"


def test_registry_category_matches_expectation():
    for code in UNIFIED_CODES:
        assert PRO_SYLLABI[code]["category"] == "unified", code
    for code in JOINT_CODES:
        assert PRO_SYLLABI[code]["category"] == "joint", code
    for code in SELF_DEFINED_CODES:
        assert PRO_SYLLABI[code]["category"] == "self_defined", code


def test_registry_titles_match_category():
    for code, info in PRO_SYLLABI.items():
        first = info["content"].splitlines()[0]
        if info["category"] == "unified":
            assert "全国统考" in first, f"{code}: {first}"
        elif info["category"] == "joint":
            assert "全国联考" in first, f"{code}: {first}"
        else:
            # 自命题绝不能写成「全国统考/全国联考」（诚实标注红线）
            assert "院校自命题" in first, f"{code}: {first}"
            assert "全国统考" not in first and "全国联考" not in first, f"{code}: {first}"


# ── 1b. 自命题诚实标注（2026-10-06 B 批次）───────────────────────────────

def test_self_defined_names_carry_honest_notice():
    """[红线] 每条自命题预设的名称必须自带「自命题·以院校官方大纲为准」。"""
    from tools.builtin_pro_syllabi import PRO_SELF_DEFINED_NOTICE
    for code in SELF_DEFINED_CODES:
        assert PRO_SELF_DEFINED_NOTICE in PRO_SYLLABI[code]["name"], code
    # 统考/联考不得被误加该标注（会暗示它们也需要去院校官网核验）
    for code in UNIFIED_CODES | JOINT_CODES:
        assert PRO_SELF_DEFINED_NOTICE not in PRO_SYLLABI[code]["name"], code


def test_pro_syllabus_label_appends_notice_only_for_self_defined():
    from tools.builtin_pro_syllabi import PRO_SELF_DEFINED_NOTICE
    sd = sm.pro_syllabus_label(PRO_SYLLABI["811"])
    assert sd.startswith("811") and PRO_SELF_DEFINED_NOTICE in sd
    assert sm.pro_syllabus_label(PRO_SYLLABI["306"]) == PRO_SYLLABI["306"]["name"]
    # 幂等：已带标注的条目再过一次不会叠成「…（…）（…）」
    assert sd.count(PRO_SELF_DEFINED_NOTICE) == 1
    # 非 dict /空值不得抛异常（GUI 建档降级路径）
    assert sm.pro_syllabus_label(None) == ""
    assert sm.pro_syllabus_label({}) == ""


def test_self_defined_pro_codes_helper():
    assert sm.self_defined_pro_codes() == sorted(SELF_DEFINED_CODES)
    assert sm.is_self_defined_pro(PRO_SYLLABI["811"]) is True
    assert sm.is_self_defined_pro(PRO_SYLLABI["306"]) is False


def test_builtin_pro_syllabus_hits_811():
    """仿真考生 A 手填的「811 信号与系统」应能命中内置预设。"""
    hit = sm.builtin_pro_syllabus("custom", "811 信号与系统")
    assert hit is not None and hit["category"] == "self_defined"
    assert sm.builtin_pro_syllabus("811", "") is not None


def test_pro_exam_category_self_defined_stays_custom():
    """自命题预设不得被误判成统考/联考（否则白名单文案指向错误权威源）。"""
    for code in SELF_DEFINED_CODES:
        name = sm.pro_syllabus_label(PRO_SYLLABI[code])
        assert sm.pro_exam_category(code, name) == "custom", code
        assert sm.pro_exam_category("custom", name) == "custom", code
        assert sm.is_unified_pro_subject(code, name) is False, code


def test_menu_lines_show_self_defined_group_with_notice():
    lines = sm.builtin_pro_menu_lines()
    joined = "\n".join(lines)
    from tools.builtin_pro_syllabi import PRO_SELF_DEFINED_NOTICE
    assert PRO_SELF_DEFINED_NOTICE in joined
    for code in SELF_DEFINED_CODES:
        assert code in joined, code
    # 不得出现「自命题·自命题」这类叠词
    assert "自命题·自命题" not in joined


def test_prompt_builtin_pro_selection_self_defined(monkeypatch, capsys):
    """[2026-10-06 B] CLI [6] 选811 必须返回带标注的名字并打印核验提示。"""
    monkeypatch.setattr("builtins.input", lambda *a, **k: "811")
    sel = sm.prompt_builtin_pro_selection()
    assert sel is not None
    code, name = sel
    from tools.builtin_pro_syllabi import PRO_SELF_DEFINED_NOTICE
    assert code == "811"
    assert name.startswith("811") and PRO_SELF_DEFINED_NOTICE in name
    out = capsys.readouterr().out
    assert PRO_SELF_DEFINED_NOTICE in out
    assert "参考框架" in out


def test_apply_syllabus_selection_self_defined_writes_frame(tmp_path):
    """[2026-10-06 B] 自命题预设写入 04-专业课/考试大纲.md 且状态为 placeholder。"""
    ws = tmp_path / "ws"
    pro_dir = ws / "04-专业课"
    pro_dir.mkdir(parents=True)
    (pro_dir / "AGENTS.md").write_text(
        "# 04-专业课 · 私教协议\n\n- **专业课科目代码与名称**：`旧科目`\n",
        encoding="utf-8")
    sm.apply_syllabus_selection(
        math_key="none", eng_key="eng2", pro_type="811",
        pro_name="811 信号与系统", workspace_root=ws, auto_write=True)
    body = (pro_dir / "考试大纲.md").read_text(encoding="utf-8")
    assert body == PRO_SYLLABI["811"]["content"]
    # 状态必须是 placeholder：AI 才不会宣称「已按考纲出题」
    assert sm.pro_syllabus_state(ws) == "placeholder"
    # AGENTS.md 的科目名必须带诚实标注
    agents = (pro_dir / "AGENTS.md").read_text(encoding="utf-8")
    from tools.builtin_pro_syllabi import PRO_SELF_DEFINED_NOTICE
    assert PRO_SELF_DEFINED_NOTICE in agents


def test_self_defined_placeholder_text_points_to_school_site(tmp_path):
    """自命题预设的白名单占位文案必须指向目标院校研究生院官网（而非考试院）。"""
    ws = tmp_path / "04-专业课"
    ws.mkdir(parents=True)
    (ws / "考试大纲.md").write_text("【待自填】占位", encoding="utf-8")
    name = sm.pro_syllabus_label(PRO_SYLLABI["811"])
    txt = sm.pro_books_placeholder_text(ws, name)
    assert "研究生院官网" in txt
    assert "中国教育考试网" not in txt and "教育部教育考试院" not in txt


# ── 2. 三分类 pro_exam_category ──────────────────────────────────────────

def test_pro_exam_category_unified():
    assert sm.pro_exam_category("custom", "311 教育学专业基础") == "unified"
    assert sm.pro_exam_category("408", "计算机学科专业基础") == "unified"
    assert sm.pro_exam_category("199", "管理类综合能力") == "unified"
    assert sm.pro_exam_category("333", "333 教育综合") == "unified"
    assert sm.pro_exam_category("custom", "498 法律硕士综合（非法学）") == "unified"


def test_pro_exam_category_joint():
    # 308 从旧「统考」口径修正为联考（骨架正文即要求按院校官网核验）
    assert sm.pro_exam_category("308", "308 护理综合") == "joint"
    # 347 此前被名称正则误判为统考，现修正为联考
    assert sm.pro_exam_category("custom", "347 心理学专业综合") == "joint"
    assert sm.pro_exam_category("custom", "432 应用统计") == "joint"
    assert sm.pro_exam_category("211", "翻译硕士英语") == "joint"


def test_pro_exam_category_custom_boundaries():
    assert sm.pro_exam_category("custom", "801 信号与系统") == "custom"
    assert sm.pro_exam_category("custom", "610 法学基础") == "custom"
    # 前后紧邻数字的自命题编号不得误判
    assert sm.pro_exam_category("custom", "1811 自命题综合") == "custom"
    assert sm.pro_exam_category("custom", "4088 综合") == "custom"


def test_is_unified_still_compatible():
    # F7 既有断言等价（防回归）
    assert sm.is_unified_pro_subject("custom", "311 教育学专业基础") is True
    assert sm.is_unified_pro_subject("408", "计算机学科专业基础") is True
    assert sm.is_unified_pro_subject("199", "管理类综合能力") is True
    assert sm.is_unified_pro_subject("custom", "801 信号与系统") is False
    assert sm.is_unified_pro_subject("custom", "1811 自命题综合") is False
    # 347 修正后不再是统考
    assert sm.is_unified_pro_subject("custom", "347 心理学专业综合") is False


# ── 3. builtin_pro_syllabus 查询 ─────────────────────────────────────────

def test_builtin_pro_syllabus_by_type():
    hit = sm.builtin_pro_syllabus("333", "")
    assert hit is not None and str(hit["name"]).startswith("333")


def test_builtin_pro_syllabus_by_name_code():
    hit = sm.builtin_pro_syllabus("custom", "306 临床医学综合能力（西医）")
    assert hit is not None and hit["category"] == "unified"
    hit2 = sm.builtin_pro_syllabus("custom", "347 心理学专业综合")
    assert hit2 is not None and hit2["category"] == "joint"


def test_builtin_pro_syllabus_selfdef_miss():
    # 未收录的自命题编号仍须返回 None（注册表不是"什么都收"）
    assert sm.builtin_pro_syllabus("custom", "801 信号与系统") is None
    assert sm.builtin_pro_syllabus("custom", "1811 自命题综合") is None
    assert sm.builtin_pro_syllabus("custom", "610 法学基础") is None
    assert sm.builtin_pro_syllabus("custom", "专业课") is None


def test_builtin_pro_syllabus_codes_and_grouped():
    codes = sm.builtin_pro_syllabus_codes()
    assert codes == sorted(codes)
    assert set(codes) == ALL_CODES
    grouped = sm.builtin_pro_syllabi_grouped()
    assert set(c for c, _ in grouped) == ALL_CODES
    # 分组顺序：医学组（306）应在最前
    assert grouped[0][0] == "306"
    # [2026-10-06 B] 自命题组恒排末位（权威性低于统考/联考，不应抢考生注意力）
    assert grouped[-1][0] in SELF_DEFINED_CODES
    assert {g for g, _ in grouped[-len(SELF_DEFINED_CODES):]} == SELF_DEFINED_CODES


# ── 4. apply_syllabus_selection 注册表分支 ──────────────────────────────

def test_apply_syllabus_selection_registry_by_type(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sm.apply_syllabus_selection(
        math_key="none", eng_key="eng2", pro_type="333",
        pro_name="333 教育综合", workspace_root=ws, auto_write=True)
    body = (ws / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8")
    assert body == PRO_SYLLABI["333"]["content"]


def test_apply_syllabus_selection_registry_by_name(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sm.apply_syllabus_selection(
        math_key="none", eng_key="eng2", pro_type="custom",
        pro_name="497 法律硕士专业基础（非法学）", workspace_root=ws, auto_write=True)
    body = (ws / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8")
    assert body == PRO_SYLLABI["497"]["content"]


def test_apply_432_uses_registry_over_legacy(tmp_path):
    """432 命中注册表（联考完整版），不再走旧 STAT432 自命题示例。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    sm.apply_syllabus_selection(
        math_key="none", eng_key="eng2", pro_type="432",
        pro_name="432 应用统计", workspace_root=ws, auto_write=True)
    body = (ws / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8")
    assert body == PRO_SYLLABI["432"]["content"]
    assert body != sm.STAT432_SYLLABUS


def test_apply_syllabus_selection_agents_md_uses_registry_name(tmp_path):
    ws = tmp_path / "ws"
    pro_dir = ws / "04-专业课"
    pro_dir.mkdir(parents=True)
    (pro_dir / "AGENTS.md").write_text(
        "# 04-专业课 · 私教协议\n\n- **专业课科目代码与名称**：`旧科目`\n",
        encoding="utf-8")
    sm.apply_syllabus_selection(
        math_key="none", eng_key="eng2", pro_type="311",
        pro_name="311 教育学专业基础", workspace_root=ws, auto_write=True)
    agents = (pro_dir / "AGENTS.md").read_text(encoding="utf-8")
    assert "`311 教育学专业基础`" in agents


# ── 5. 占位文案三档 ─────────────────────────────────────────────────────

def _placeholder_ws(tmp_path):
    pro_dir = tmp_path / "04-专业课"
    pro_dir.mkdir(parents=True, exist_ok=True)
    (pro_dir / "考试大纲.md").write_text("【待自填】占位", encoding="utf-8")
    return tmp_path


def test_placeholder_text_three_tiers(tmp_path):
    ws = _placeholder_ws(tmp_path)
    unified_txt = sm.pro_books_placeholder_text(ws, "311 教育学专业基础")
    joint_txt = sm.pro_books_placeholder_text(ws, "347 心理学专业综合")
    custom_txt = sm.pro_books_placeholder_text(ws, "801 信号与系统")
    assert "教育部" in unified_txt and "统考" in unified_txt
    assert "研招网" in joint_txt and "研究生院官网" in joint_txt
    assert "研究生院官网" in custom_txt and "研招网" not in custom_txt


def test_placeholder_text_joint_missing_tier(tmp_path):
    # 无 04-专业课 目录 → missing 档同样按联考口径
    txt = sm.pro_books_placeholder_text(tmp_path, "347 心理学专业综合")
    assert "研招网" in txt and "研究生院官网" in txt


def test_placeholder_body_three_tiers():
    u = sm._pro_placeholder_body("311 教育学专业基础", "示例", "custom")
    j = sm._pro_placeholder_body("347 心理学专业综合", "示例", "custom")
    c = sm._pro_placeholder_body("801 信号与系统", "示例", "custom")
    assert "中国教育考试网" in u
    assert "研招网" in j and "研究生院官网" in j
    assert "研究生院官网" in c and "研招网" not in c
    # 三档互不相同（防分档塌陷）
    assert len({u, j, c}) == 3


# ── 6. 菜单辅助 ─────────────────────────────────────────────────────────

def test_menu_lines_grouped_nonempty():
    lines = sm.builtin_pro_menu_lines()
    assert lines, "注册表非空时菜单行不得为空"
    joined = "\n".join(lines)
    for code in ("306", "333", "431", "448"):
        assert code in joined, code


def test_prompt_builtin_pro_selection_hit(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: "333")
    sel = sm.prompt_builtin_pro_selection()
    assert sel is not None
    code, name = sel
    assert code == "333" and str(name).startswith("333")


def test_prompt_builtin_pro_selection_miss(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *a, **k: "999")
    assert sm.prompt_builtin_pro_selection() is None
    out = capsys.readouterr().out
    assert "999" in out  # 如实提示代码不在内置库


# ── 7. 三端菜单入口 ─────────────────────────────────────────────────────

def test_init_workspace_menu_option6_builtin(monkeypatch):
    from tools import init_workspace as iw
    # 输入顺序：数学[6 不考] → 英语[1 英二] → 专业课[6 内置库] → 代码[333]
    _feed(monkeypatch, ["6", "1", "6", "333"])
    math_key, eng_key, pro_type, pro_name = iw.choose_exam_subjects_and_syllabi(
        interactive=True)
    assert (math_key, eng_key) == ("none", "eng2")
    assert pro_type == "333"
    assert str(pro_name).startswith("333")


def test_init_workspace_menu_option6_miss_falls_back(monkeypatch):
    from tools import init_workspace as iw
    # [6] 输入无效代码 → 回退自命题手输
    _feed(monkeypatch, ["6", "1", "6", "999", "801 信号与系统"])
    math_key, eng_key, pro_type, pro_name = iw.choose_exam_subjects_and_syllabi(
        interactive=True)
    assert pro_type == "custom"
    assert pro_name == "801 信号与系统"


def test_ky_subject_menu_option6_builtin(monkeypatch, tmp_path):
    from tools.cli import config as cfg_mod
    saved = {}
    monkeypatch.setattr(cfg_mod, "ROOT", tmp_path)
    monkeypatch.setattr(cfg_mod, "save_config", lambda cfg: saved.update(cfg))
    # 主菜单[3 专业课] → 专业课菜单[6 内置库] → 代码[432]
    _feed(monkeypatch, ["3", "6", "432"])
    cfg_mod.manage_syllabi_cli({})
    body = (tmp_path / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8")
    assert body == PRO_SYLLABI["432"]["content"]
    assert saved.get("study_plan", {}).get("pro_type") == "432"


def test_init_workspace_menu_option6_self_defined(monkeypatch):
    """[2026-10-06 B]仿真考生 A 走 CLI 向导[6] 选 811：能命中且名字带标注。"""
    from tools import init_workspace as iw
    from tools.builtin_pro_syllabi import PRO_SELF_DEFINED_NOTICE
    _feed(monkeypatch, ["6", "1", "6", "811"])
    math_key, eng_key, pro_type, pro_name = iw.choose_exam_subjects_and_syllabi(
        interactive=True)
    assert (math_key, eng_key) == ("none", "eng2")
    assert pro_type == "811"
    assert str(pro_name).startswith("811")
    assert PRO_SELF_DEFINED_NOTICE in str(pro_name)


def test_ky_subject_menu_option6_self_defined(monkeypatch, tmp_path):
    """[2026-10-06 B] `ky subject` [6] 选 811：写入参考框架 + 配置名带标注。"""
    from tools.cli import config as cfg_mod
    from tools.builtin_pro_syllabi import PRO_SELF_DEFINED_NOTICE
    saved = {}
    monkeypatch.setattr(cfg_mod, "ROOT", tmp_path)
    monkeypatch.setattr(cfg_mod, "save_config", lambda cfg: saved.update(cfg))
    _feed(monkeypatch, ["3", "6", "811"])
    cfg_mod.manage_syllabi_cli({})
    body = (tmp_path / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8")
    assert body == PRO_SYLLABI["811"]["content"]
    plan = saved.get("study_plan", {})
    assert plan.get("pro_type") == "811"
    assert PRO_SELF_DEFINED_NOTICE in str(plan.get("pro_name"))


def test_ky_subject_menu_option2_199_now_writes_syllabus(monkeypatch, tmp_path):
    """[菜单修齐] 此前 [2] 199 落自命题分支（不写 199 大纲），现补齐。"""
    from tools.cli import config as cfg_mod
    saved = {}
    monkeypatch.setattr(cfg_mod, "ROOT", tmp_path)
    monkeypatch.setattr(cfg_mod, "save_config", lambda cfg: saved.update(cfg))
    _feed(monkeypatch, ["3", "2"])
    cfg_mod.manage_syllabi_cli({})
    body = (tmp_path / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8")
    assert body == sm.MGMT199_SYLLABUS
    assert saved.get("study_plan", {}).get("pro_type") == "199"


# ── 8. GUI 向导 combo（offscreen；PySide6 缺失自动跳过）─────────────────

def test_gui_pro_combo_contains_registry_subjects(tmp_path):
    pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 测试")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from tools.gui.widgets.onboarding_wizard import OnboardingWizard
    wizard = OnboardingWizard(workspace_root=tmp_path)
    datas = [wizard.pro_type_combo.itemData(i)
             for i in range(wizard.pro_type_combo.count())]
    for code in ("333", "396", "347", "431", "448"):
        assert code in datas, code
    # 悬空 law 选项已由 397/398/497/498 精确科目取代
    assert "law" not in datas
    # 选中注册表科目时名称自动预填
    idx = wizard.pro_type_combo.findData("333")
    wizard.pro_type_combo.setCurrentIndex(idx)
    assert wizard.pro_name_edit.text().startswith("333")
    assert app is not None


def test_gui_pro_combo_contains_self_defined_with_notice(tmp_path):
    """[2026-10-06 B] GUI 建档页2：811 等自命题预设可直选且展示名带诚实标注。

    这是仿真缺口 1 的直接验收点：考生 A 此前在下拉里找不到任何 811 选项，
    只能手填科目名（仿真记录里被如实记为「交互方式限制」）。
    """
    pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 测试")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from tools.gui.widgets.onboarding_wizard import OnboardingWizard
    from tools.builtin_pro_syllabi import PRO_SELF_DEFINED_NOTICE
    wizard = OnboardingWizard(workspace_root=tmp_path)
    datas = [wizard.pro_type_combo.itemData(i)
             for i in range(wizard.pro_type_combo.count())]
    texts = [wizard.pro_type_combo.itemText(i)
             for i in range(wizard.pro_type_combo.count())]
    for code in SELF_DEFINED_CODES:
        assert code in datas, f"GUI 下拉缺自命题预设 {code}"
        item_text = texts[datas.index(code)]
        assert PRO_SELF_DEFINED_NOTICE in item_text, f"{code} 下拉项未带标注: {item_text}"
        # 标注只出现一次（防「自命题·自命题」叠词）
        assert item_text.count(PRO_SELF_DEFINED_NOTICE) == 1, item_text
        # findData 命中后自动预填名称，且预填名同样带标注
        idx = wizard.pro_type_combo.findData(code)
        assert idx >= 0, code
        wizard.pro_type_combo.setCurrentIndex(idx)
        assert PRO_SELF_DEFINED_NOTICE in wizard.pro_name_edit.text()
    #统考项不得被误加自命题标注
    idx333 = wizard.pro_type_combo.findData("333")
    wizard.pro_type_combo.setCurrentIndex(idx333)
    assert PRO_SELF_DEFINED_NOTICE not in wizard.pro_name_edit.text()
    assert app is not None
