# -*- coding: utf-8 -*-
"""隐私策略：身份替换规则的**生成口径**（R2-E4）。

锁定三件事，都是实测踩出来的：

1. **学情自由文本字段必须生成规则** —— ``eng_weakness='阅读定位不熟练'`` 这类值
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
    "school": "中国人民大学",
    "major": "030100 法学",
    "pro_name": "610 法学基础 810 法学综合",
    "eng_weakness": "阅读定位不熟练",
    "pol_weakness": "多选题易漏选",
    "pro_weakness": "知识点记忆不牢",
    "eng_baseline": "摸底62",
    "math_baseline": "不考数学",
}


def test_real_weakness_becomes_placeholder(tmp_path):
    root = _make_root(tmp_path, PLAN_FULL)
    rules = pp.build_substitutions(root)
    for raw in ("阅读定位不熟练", "多选题易漏选", "知识点记忆不牢"):
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
    assert pp.sanitize_text("摸底62", rules) == "摸底水平"


def test_backup_school_gets_url_rule(tmp_path):
    plan = dict(PLAN_FULL)
    plan["backup_school"] = "湖南农业大学"
    root = _make_root(tmp_path, plan)
    rules = pp.build_substitutions(root)
    assert pp.sanitize_text("湖南农业大学", rules) == "备选院校"
    from urllib.parse import quote
    assert pp.sanitize_text(quote("湖南农业大学", safe=""), rules) == quote("备选院校", safe="")


def test_exam_date_is_never_rewritten_as_identity(tmp_path):
    """[G2 修复] 初试日期不是身份，任何出口都不得改写它。

    修复前 ``STATIC_IDENTITY_SUBSTITUTIONS`` 里有一条 ``(2026-12-19 → 2027-12-26)``，
    只对 md/html/svg 生效 —— 于是公开副本的 AGENTS.md 写 2027-12-26，而
    ``ky status`` 的「初试首日」是实时算出的 2026-12-19，**同一屏两个初试日期**。

    阴性对照：把该日期规则加回 ``STATIC_IDENTITY_SUBSTITUTIONS``，本用例必须变红。
    """
    root = _make_root(tmp_path, PLAN_FULL)
    md_rules = pp.build_substitutions(root)
    py_rules = pp.build_py_substitutions(root)
    assert pp.sanitize_text("2026-12-19", md_rules) == "2026-12-19"
    assert pp.sanitize_text("2026-12-19", py_rules) == "2026-12-19"
    # 裸专业代码在 *.py 里是公开学科门类代码（chsi_connector 的键名），仍排除
    assert pp.sanitize_text("030100", py_rules) == "030100"


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
    assert "阅读定位不熟练" not in tokens
    assert "摸底62" not in tokens
    assert "中国人民大学" in tokens


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
    ("阅读定位不熟练", True),
    ("无", True),          # school 仍在，规则集非空 → 整体仍有效
])
def test_rules_effective_only_depends_on_school(tmp_path, weakness, expected_effective):
    plan = dict(PLAN_FULL)
    plan["eng_weakness"] = weakness
    root = _make_root(tmp_path, plan)
    ok, _reason = pp.identity_rules_effective(root)
    assert ok is expected_effective


# ══════════════════════════════════════════════════════════════════════════
# [P10] 占位符名单单一事实源
# ══════════════════════════════════════════════════════════════════════════

def test_major_placeholders_cover_all_substitution_outputs(tmp_path):
    """[P10 回归] 规则表产出的专业占位符必须**全部**包含在 ``MAJOR_PLACEHOLDERS``。

    否则 ``gui/services/settings.is_unconfigured()`` 会漏判 —— 把「已被脱敏成
    占位符的专业」当成「已配置」。修复前它本地硬编码的名单只有
    ``("报考专业", "目标专业 (专业代码-方向)")``，漏了规则表实际还会产出的
    ``"目标专业 (专业代码)"`` 与 ``"目标专业"``。

    阴性对照：从 ``MAJOR_PLACEHOLDERS`` 里删掉 ``"目标专业 (专业代码)"``，本用例变红。
    """
    static_produced = {repl for _pat, repl in pp.STATIC_IDENTITY_SUBSTITUTIONS
                       if "专业" in repl}
    assert static_produced, "静态表里应至少有一条专业占位符产物"
    missing_static = static_produced - set(pp.MAJOR_PLACEHOLDERS)
    assert not missing_static, (
        f"静态表产物未纳入 MAJOR_PLACEHOLDERS: {sorted(missing_static)}")

    root = _make_root(tmp_path, PLAN_FULL)
    dynamic_produced = {repl for _pat, repl in pp.identity_substitutions(root)
                        if repl.startswith("目标专业")}
    assert dynamic_produced, "动态规则里应至少有一条专业占位符产物"
    missing_dyn = dynamic_produced - set(pp.MAJOR_PLACEHOLDERS)
    assert not missing_dyn, (
        f"动态规则产物未纳入 MAJOR_PLACEHOLDERS: {sorted(missing_dyn)}")


# ══════════════════════════════════════════════════════════════════════════
# [P2-5] 通用 PII（手机号 / 身份证 / 带标签的准考证号）
# ══════════════════════════════════════════════════════════════════════════

def test_pii_phone_idcard_and_ticket_are_redacted(tmp_path):
    """[P2-5] 通用 PII 必须被脱敏。

    修复前规则表里**一条 PII 规则都没有**（实测原样返回）。
    阴性对照：删掉 ``PII_SUBSTITUTIONS`` 里的对应条目，本用例变红。
    """
    root = _make_root(tmp_path, PLAN_FULL)
    rules = pp.build_substitutions(root)
    # 样本用**字符串拼接**构造而不是写成字面量：否则本文件自己就会被导出脱敏改写
    # （test_tests_dir_is_immune_to_py_sanitization 会因此变红）。
    phone = "138" + "12345678"
    idcard = "110105" + "19900307" + "123X"
    ticket = "2026" + "12345678901"
    text = f"联系电话：{phone}\n身份证号：{idcard}\n准考证号：{ticket}\n"
    out = pp.sanitize_text(text, rules)
    assert phone not in out and "[手机号]" in out
    assert idcard not in out and "[身份证号]" in out
    assert ticket not in out and "[准考证号]" in out


def test_pii_rules_do_not_touch_existing_numbers():
    """[P2-5 阴性对照] 防**过度脱敏**：PII 规则不得改动仓库里合法的既有数字。

    这里只用 ``PII_SUBSTITUTIONS`` 单独验证（不掺身份规则）—— 身份规则本来就该
    替换 fixture 自己的专业代码，那是另一回事。
    仓库里合法数字极多：专业代码 ``030100``、初试日期 ``2026-12-19``、
    自命题科目码 ``610``/``810``、无标签的长数字串。
    若把准考证号规则的「必须有标签」前缀去掉（改成裸 ``\\d{9,16}``），本用例变红。
    """
    for raw in ("030100", "2026-12-19", "610", "810",
                "202612345678901", "1234567890123456"):
        assert pp.sanitize_text(raw, pp.PII_SUBSTITUTIONS) == raw, raw
    # 反向确认规则是「活的」而非空转：带标签的准考证号确实会被替换。
    # 样本同样用拼接构造（避免本文件被导出脱敏改写）。
    assert "[准考证号]" in pp.sanitize_text(
        "准考证号：" + "2026" + "12345678901", pp.PII_SUBSTITUTIONS)


def test_scan_residual_identity_flags_pii_only_when_enabled(tmp_path):
    """[P2-5] ``include_pii=True`` 时含手机号的文件应被判为残留；默认 False 不误报。"""
    root = _make_root(tmp_path, PLAN_FULL)
    dst = tmp_path / "product"
    dst.mkdir()
    # 同样用拼接构造样本（避免本文件被导出脱敏改写）
    (dst / "notes.md").write_text("联系电话：" + "138" + "12345678",
                                  encoding="utf-8")
    assert pp.scan_residual_identity(dst, root, include_pii=True) == ["notes.md"]
    assert pp.scan_residual_identity(dst, root) == []


# ══════════════════════════════════════════════════════════════════════════
# [P6 回归] tests/ 必须对导出脱敏「免疫」
# ══════════════════════════════════════════════════════════════════════════

def test_tests_dir_is_immune_to_py_sanitization():
    """导出时的 .py 脱敏**不得改动 tests/ 下任何文件**。

    为什么这条最关键：``sync_publish.sanitize_markdown_files()`` 会对发布副本里的
    ``*.py``（含 ``tests/``）套用 ``build_py_substitutions()``。若某个测试文件里写着
    **当前真实身份**，它就会被改成占位符 → 公开副本里的断言自毁
    （实测 ``test_packaging_identity.py`` 的 ``assert SCHOOL not in text`` 在副本里
    变成 ``assert "目标院校" not in text``，而同一文件的模板恰好写着「目标院校」）。

    修复方向**不是**给 tests/ 开脱敏豁免（那会让真实校名直接进公开仓库），
    而是让夹具本身不含真实身份 —— 本条守住这条不变量，同时也就守住了
    「夹具不被写回真实身份」。

    阴性对照：把任意测试文件里的中性校名改回真实校名（如 中国人民大学 →
    当前 ky_config 的 school），本用例必须变红。
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    rules = pp.build_py_substitutions(root)
    #: **待办名单**：尚未中性化的测试文件。中性化完成后必须从这里删除
    #: （下面的 assert 会钉住这份名单的规模，防止它悄悄长大）。
    #:   * tests/test_agentic_research.py —— 正被另一个并行任务追加回归用例，
    #:     本轮刻意不动它；它里面仍有真实校名与真实自命题科目串。
    pending = {"tests/test_agentic_research.py"}
    assert len(pending) <= 1, "待办名单只应保留「正在被并行任务改动」的文件"

    offenders = []
    for f in sorted((root / "tests").rglob("*.py")):
        rel = f.relative_to(root).as_posix()
        if rel in pending:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if pp.sanitize_text(text, rules) != text:
            offenders.append(rel)
    assert not offenders, (
        "以下测试文件含有当前真实身份 —— 导出脱敏会改写它们，导致公开副本断言自毁："
        f"{offenders}")
