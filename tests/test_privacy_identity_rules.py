# -*- coding: utf-8 -*-
"""隐私策略：身份替换规则的**生成口径**（R2-E4）。

锁定三件事，都是实测踩出来的：

1. **学情自由文本字段必须生成规则** —— ``eng_weakness='待诊断薄弱点'`` 这类值
   此前一条规则都没有，而 AGENTS.md 把它们明文写着，于是随发布副本 / 打包产物
   一起公开（打包产物实测 40 处身份字面量里薄弱点占两类）。
2. **通用值必须被闸门挡住** —— ``计算失误`` 是「错因五分类」的正文词、
   ``不考数学`` 是本仓库的标准表述、``暂未放置实体资料…`` 是未挂载资料时的模板值。
   无差别生成规则会在公开文档里全局替换它们，**反而改坏内容**。
3. **规则按 root 现算** —— 引擎下沉 privacy_policy 就是为了不让规则在 import 时
   被冻结成某个固定仓库根（否则打包路径测试里的 ``monkeypatch(ROOT)`` 会失效）。
"""
import json

import pytest

import tools.privacy_policy as pp


def _make_root(tmp_path, plan: dict):
    """造一个只含 ky_config.json 的假仓库根。"""
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"study_plan": plan}, ensure_ascii=False), encoding="utf-8")
    return tmp_path


PLAN_FULL = {
    "school": "目标院校",
    "major": "目标专业 (专业代码)",
    "pro_name": "自命题专业课科目",
    "eng_weakness": "待诊断薄弱点",
    "pol_weakness": "待诊断薄弱点",
    "pro_weakness": "待诊断薄弱点",
    "eng_baseline": "摸底水平",
    "math_baseline": "不考数学",
}


def test_real_weakness_becomes_placeholder(tmp_path):
    root = _make_root(tmp_path, PLAN_FULL)
    rules = pp.build_substitutions(root)
    for raw in ("待诊断薄弱点", "待诊断薄弱点", "待诊断薄弱点"):
        assert pp.sanitize_text(raw, rules) == "待诊断薄弱点", raw


def test_generic_weakness_is_gated(tmp_path):
    """通用词不得生成规则：它们是公开文档正文，替换即改坏内容。"""
    plan = dict(PLAN_FULL)
    plan["eng_weakness"] = "计算失误"
    root = _make_root(tmp_path, plan)
    rules = pp.build_substitutions(root)
    for raw in ("计算失误", "概念漏洞", "审题偏差", "公式记错", "书写丢分"):
        assert pp.sanitize_text(raw, rules) == raw, raw


def test_generic_baseline_and_books_are_gated(tmp_path):
    plan = dict(PLAN_FULL)
    plan["math_books"] = "暂未放置实体资料（私教严格按【不考数学】官方考纲出题）"
    root = _make_root(tmp_path, plan)
    rules = pp.build_substitutions(root)
    assert pp.sanitize_text("不考数学", rules) == "不考数学"
    assert "暂未放置实体资料" in pp.sanitize_text(plan["math_books"], rules)
    # 而真实摸底分仍要被脱敏
    assert pp.sanitize_text("摸底水平", rules) == "摸底水平"


def test_backup_school_gets_url_rule(tmp_path):
    plan = dict(PLAN_FULL)
    plan["backup_school"] = "对比院校B"
    root = _make_root(tmp_path, plan)
    rules = pp.build_substitutions(root)
    assert pp.sanitize_text("对比院校B", rules) == "备选院校"
    from urllib.parse import quote
    assert pp.sanitize_text(quote("对比院校B", safe=""), rules) == quote("备选院校", safe="")


def test_exam_date_excluded_from_py_rules(tmp_path):
    """日期在 *.py 里是功能默认值（日历/默认初试日期），绝不能套用到源码。"""
    root = _make_root(tmp_path, PLAN_FULL)
    md_rules = pp.build_substitutions(root)
    py_rules = pp.build_py_substitutions(root)
    assert pp.sanitize_text("2026-12-19", md_rules) == "2027-12-26"
    assert pp.sanitize_text("2026-12-19", py_rules) == "2026-12-19"
    # 裸专业代码在 *.py 里是公开学科门类代码（chsi_connector 的键名），同样排除
    assert pp.sanitize_text("030500", py_rules) == "030500"


def test_build_substitutions_is_root_scoped(tmp_path):
    """规则必须随 root 现算 —— 这是把引擎下沉 privacy_policy 的全部理由。"""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _make_root(a, {"school": "甲大学", "major": "030500 甲专业", "pro_name": ""})
    _make_root(b, {"school": "乙大学", "major": "030500 乙专业", "pro_name": ""})

    rules_a = pp.build_substitutions(a)
    rules_b = pp.build_substitutions(b)
    assert pp.sanitize_text("甲大学", rules_a) == "目标院校"
    assert pp.sanitize_text("甲大学", rules_b) == "甲大学"  # b 的规则里没有甲大学
    assert pp.sanitize_text("乙大学", rules_b) == "目标院校"
    assert rules_a != rules_b


def test_identity_name_tokens_not_extended_by_weakness(tmp_path):
    """薄弱点/摸底分**不进**文件名判据 —— 避免把通用词当身份大面积误判。"""
    root = _make_root(tmp_path, PLAN_FULL)
    tokens = pp.identity_name_tokens(root)
    assert "待诊断薄弱点" not in tokens
    assert "摸底水平" not in tokens
    assert "目标院校" in tokens


def test_policy_file_is_exempt_from_sanitizing_itself():
    """规则表文件绝不能被自己的规则改写 —— 那会**自毁**规则。

    实测（真实构建）：产物里 ``(r"<历史校名>", "对比院校B")`` 被自己的规则改写成
    ``(r"对比院校B", "对比院校B")`` —— 规则失效，且是不可逆的静默降级。
    故 ``tools/privacy_policy.py`` 必须与 ``registry.py`` 一样列入豁免。
    """
    assert "tools/privacy_policy.py" in pp.PY_UNSANITIZED_FILES
    assert "tools/intelligence/registry.py" in pp.PY_UNSANITIZED_FILES


def test_policy_file_uses_neutral_placeholders_only():
    """策略文件会被**原样发布**，故注释里的举例必须是中性占位。

    本文件自身也属于「会被原样发布」的一份，因此它不能拿真实身份当示例 ——
    这条边界写在模块开头，本用例只做结构性复核；针对具体考生的取值由端到端
    脚本的守卫 1 兜住（那里能读到真实 ky_config，判据更精确）。
    """
    import pathlib
    text = pathlib.Path(pp.__file__).read_text(encoding="utf-8")
    assert "某农业类院校" in text                      # 中性占位确实在用
    # 不得以 registry 条目形态（"<某校>": (代码, 地区, ...)）出现真实校名示例
    assert '农业大学": (' not in text


def test_residual_scan_exemptions_survive_internal_prefix(tmp_path):
    """产物形态下豁免必须仍生效：`_internal/` 前缀不得让公开库变成「残留」。

    PyInstaller 产物把同一份内容同时放在产物根与 ``_internal/`` 下。``data/``
    （1800+ 所高校公开库）与 ``PY_UNSANITIZED_FILES``（公开校名→代码映射）这两条
    豁免按**仓库相对路径**写，若不先剥 ``_internal/`` 前缀，构建会被自己的自检
    挡下 —— 实测首次真实重建就撞上（8 个误报，全是 `_internal/data/universities/**`
    与 `_internal/tools/intelligence/registry.py`）。
    """
    root = _make_root(tmp_path, PLAN_FULL)
    dst = tmp_path / "product"
    (dst / "_internal" / "data" / "universities").mkdir(parents=True)
    (dst / "_internal" / "data" / "universities" / "national.json").write_text(
        f'[{{"school": "{PLAN_FULL["school"]}"}}]', encoding="utf-8")
    (dst / "_internal" / "tools" / "intelligence").mkdir(parents=True)
    (dst / "_internal" / "tools" / "intelligence" / "registry.py").write_text(
        f'KNOWN = {{"{PLAN_FULL["school"]}": "10466"}}', encoding="utf-8")
    # 真正的残留：非豁免路径下的自有文件
    (dst / "_internal" / "AGENTS.md").write_text(
        f"目标院校：{PLAN_FULL['school']}", encoding="utf-8")

    hits = pp.scan_residual_identity(dst, root, strip_prefixes=("_internal/",))
    assert hits == ["_internal/AGENTS.md"], hits
    # 不传 strip_prefixes 时才会出现那批误报（证明这个参数确实在起作用）
    noisy = pp.scan_residual_identity(dst, root)
    assert "_internal/tools/intelligence/registry.py" in noisy
    assert any(h.startswith("_internal/data/") for h in noisy)


@pytest.mark.parametrize("weakness,expected_effective", [
    ("待诊断薄弱点", True),
    ("无", True),          # school 仍在，规则集非空 → 整体仍有效
])
def test_rules_effective_only_depends_on_school(tmp_path, weakness, expected_effective):
    plan = dict(PLAN_FULL)
    plan["eng_weakness"] = weakness
    root = _make_root(tmp_path, plan)
    ok, _reason = pp.identity_rules_effective(root)
    assert ok is expected_effective
