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

    阴性对照：把任意测试文件里的中性校名改回真实校名（如 北京大学 →
    当前 ky_config 的 school），本用例必须变红。
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    rules = pp.build_py_substitutions(root)
    #: **待办名单**：尚未中性化的测试文件。中性化完成后必须从这里删除
    #: （下面的 assert 会钉住这份名单的规模，防止它悄悄长大）。
    #: 2026-09-21：最后一项 ``tests/test_agentic_research.py`` 已中性化
    #: （样本院校由真实目标院校改为「北京大学」这类非身份院校），名单清空 ——
    #: 自此 ``tests/`` 整目录对脱敏免疫，公开副本里这些测试不会再被改写而自毁。
    pending: set = set()
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


# ══════════════════════════════════════════════════════════════════════════
# [2026-09-21 补漏] 两类漏网形态：括号包裹的代码 + 院校 pinyin 域名
# ══════════════════════════════════════════════════════════════════════════
#
# 实测缺口（推送前严格审查时发现）：``/compare`` 生成的《双校对标》研报文件名
# 已被 ``RENAME_NAME_PATTERNS`` 改成中性名，**正文却留着**
# ``gra.<校名拼音>.edu.cn`` 与 ``(自命题科目码)科目名`` 这类形态 ——
# 中文校名规则与 URL 百分号编码规则都覆盖不到它们，导出后自检也只扫中文
# token，于是全程打印「非公开库路径下无真实身份残留」。
#
# 注意：本文件会被 ``test_tests_dir_is_immune_to_py_sanitization`` 用**真实**规则
# 复查，所以下面一律用 PLAN_FULL 的中性代码（610/810），不得写出真实自命题科目码。


def _add_school_db(root, name, *, official="", graduate="", admission="", extra=None):
    """在假仓库根里放一份最小院校库（字段名与 data/universities/*.json 一致）。"""
    db_dir = root / "data" / "universities"
    db_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "chsi_code": "10000",
        "name": name,
        "official_domain": official,
        "graduate_domain": graduate,
        "admission_domain": admission,
    }
    if extra:
        entry.update(extra)
    (db_dir / "national_institutions.json").write_text(
        json.dumps({name: entry}, ensure_ascii=False), encoding="utf-8")


def test_wrapped_subject_codes_are_sanitized(tmp_path):
    """代码被括号 / 方括号包裹时，规则必须照样命中。

    修复前 ``(610)法学基础`` 整条漏网：右括号让 ``\\s*`` 之后的名称匹配落空，
    而生成物里代码几乎总被括号包着，等于这条规则在真实语料上完全失效。

    阴性对照：把 ``_code_with_optional_wrap()`` 换回裸 ``(?<!\\d)code(?!\\d)``，
    本用例必须变红。
    """
    root = _make_root(tmp_path, PLAN_FULL)
    rules = pp.build_substitutions(root)
    assert pp.sanitize_text("初试：(610)法学基础、(810)法学综合", rules) == \
        "初试：自命题科目1、自命题科目2"
    assert pp.sanitize_text("（610）法学基础", rules) == "自命题科目1"
    assert pp.sanitize_text("【610】法学基础", rules) == "自命题科目1"
    assert pp.sanitize_text("科目 [610] 法学基础", rules) == "科目 自命题科目1"
    # 并写对（两个代码同时出现）同样允许括号包裹
    assert pp.sanitize_text("自命题 (610)、(810) 两门", rules) == \
        "自命题 自命题科目1、自命题科目2 两门"


def test_wrapped_code_rule_does_not_widen_to_plain_numbers(tmp_path):
    """放宽的**只有包裹符**：数字边界与「必须与名称/语境词相邻共现」判据一律不变。

    阴性对照：若把 ``_code_with_optional_wrap()`` 改成无条件接受孤立代码
    （去掉与名称 / 语境词的共现要求），本用例必须变红。
    """
    root = _make_root(tmp_path, PLAN_FULL)
    rules = pp.build_substitutions(root)
    for raw in ("这本书 610元", "见第 610 页", "编号 6100", "编号(610)", "编号 810"):
        assert pp.sanitize_text(raw, rules) == raw, raw


def test_school_pinyin_domain_becomes_placeholder(tmp_path):
    """当前院校的注册域必须被替换，且一次覆盖 www / gra / yjsy 全部子域。

    阴性对照：删掉 ``identity_substitutions()`` 里的 ``school_domains()`` 循环，
    本用例必须变红。
    """
    plan = dict(PLAN_FULL)
    plan["school"] = "甲大学"
    root = _make_root(tmp_path, plan)
    _add_school_db(root, "甲大学",
                   official="https://www.jiada.edu.cn",
                   graduate="https://gra.jiada.edu.cn",
                   admission="https://yjsy.jiada.edu.cn")
    assert pp.school_domains(root, "甲大学") == ["jiada.edu.cn"]
    rules = pp.build_substitutions(root)
    assert pp.sanitize_text("https://gra.jiada.edu.cn/zsml.html", rules) == \
        "https://gra.example.edu.cn/zsml.html"
    assert pp.sanitize_text("https://www.jiada.edu.cn", rules) == \
        "https://www.example.edu.cn"
    assert pp.sanitize_text("裸域 jiada.edu.cn", rules) == "裸域 example.edu.cn"
    # .py 侧同样生效 —— radar.py 注释里的真实域名就是靠这条清掉的
    py_rules = pp.build_py_substitutions(root)
    assert pp.sanitize_text('url="https://gra.jiada.edu.cn/x"', py_rules) == \
        'url="https://gra.example.edu.cn/x"'


def test_school_domain_rule_ignores_non_domain_fields(tmp_path):
    """只认 ``*_domain`` 字段：``chsi_url`` 这类**非院校自有域名字段**一律不取。

    实测踩到：若按「任意 ``*_url`` / ``*_web``」收集，院校库里的 ``chsi_url``
    （指向研招网）就会被当身份替换 —— 既改坏公开副本里的官方链接，又让导出后
    自检把每个提到研招网的文件误报成残留。

    夹具刻意用**非公共平台**的聚合站域名：这样只有「字段判据」这一道护栏在起作用，
    变异它才必然翻红（若用 ``chsi.com.cn``，平台黑名单会替它兜住，对照失效）。

    阴性对照：把 ``_collect_domain_values()`` 的字段判据放宽回 ``*_url``，
    聚合站域名就会进入规则集，本用例必须变红。
    """
    plan = dict(PLAN_FULL)
    plan["school"] = "甲大学"
    root = _make_root(tmp_path, plan)
    _add_school_db(root, "甲大学",
                   official="https://www.jiada.edu.cn",
                   extra={"chsi_url": "https://www.some-aggregator.com/sch/1.html"})
    assert pp.school_domains(root, "甲大学") == ["jiada.edu.cn"]
    rules = pp.build_substitutions(root)
    for raw in ("https://www.some-aggregator.com/sch/1.html",
                "https://yz.chsi.com.cn/sch/schoolInfo--schId-1.dhtml",
                "https://www.chsi.com.cn/",
                "https://www.hust.edu.cn",
                "https://gs.whu.edu.cn",
                "https://www.example.com/path",
                "https://gra.example.edu.cn/x"):
        assert pp.sanitize_text(raw, rules) == raw, raw


def test_school_domain_rule_spares_public_platforms(tmp_path):
    """即便某个 ``*_domain`` 字段**指向上游公共平台**，也不得当成身份替换。

    为什么要这道冗余护栏：``national_institutions.json`` 由
    ``university_db_builder.py`` 生成 / 重建，抓取回退时 ``official_domain``
    完全可能被填成研招网页面地址；一旦如此，替换会改坏公开副本里的官方链接，
    并让导出后自检把大量无关文件误报成残留。

    阴性对照：从 ``_load_school_domains()`` 里删掉 ``_PUBLIC_PLATFORM_DOMAINS``
    过滤，本用例必须变红。
    """
    plan = dict(PLAN_FULL)
    plan["school"] = "甲大学"
    root = _make_root(tmp_path, plan)
    _add_school_db(root, "甲大学",
                   official="https://yz.chsi.com.cn/sch/schoolInfo--schId-1.dhtml",
                   graduate="https://www.jiada.edu.cn")
    assert pp.school_domains(root, "甲大学") == ["jiada.edu.cn"]
    rules = pp.build_substitutions(root)
    platform = "https://yz.chsi.com.cn/sch/schoolInfo--schId-1.dhtml"
    assert pp.sanitize_text(platform, rules) == platform


def test_school_domain_rules_degrade_without_db(tmp_path):
    """院校库缺失时**静默降级**：中文校名规则仍须生效，整条导出链不得失败。

    阴性对照：把 ``school_domains()`` 改成「取不到就抛异常」，本用例必须变红。
    """
    root = _make_root(tmp_path, PLAN_FULL)
    assert pp.school_domains(root, "中国人民大学") == []
    rules = pp.build_substitutions(root)
    assert pp.sanitize_text("中国人民大学", rules) == "目标院校"


def test_residual_scan_catches_pinyin_domain(tmp_path):
    """导出后自检必须看得见 pinyin 域名 —— 否则它永远打印「无残留」。

    阴性对照：把 ``scan_residual_identity()`` 里的 ``identity_domain_tokens()``
    并集删掉，``report.md`` 就不再被命中，本用例必须变红。
    """
    plan = dict(PLAN_FULL)
    plan["school"] = "甲大学"
    src = tmp_path / "src"
    src.mkdir()
    _make_root(src, plan)
    _add_school_db(src, "甲大学", official="https://www.jiada.edu.cn",
                   graduate="https://gra.jiada.edu.cn")
    assert pp.identity_domain_tokens(src) == ["jiada.edu.cn"]

    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "report.md").write_text("官网 https://gra.jiada.edu.cn/x", encoding="utf-8")
    (dst / "clean.md").write_text("官网 https://example.edu.cn/x", encoding="utf-8")
    hits = pp.scan_residual_identity(dst, src)
    assert "report.md" in hits
    assert "clean.md" not in hits


# ══════════════════════════════════════════════════════════════════════════
# [2026-09-25 修复] 占位值配置不得生成自指规则（公开副本内自指改写）
# ══════════════════════════════════════════════════════════════════════════

#: 公开副本 ky_config.json 的实际取值（= ``sync_publish.CONFIG_TEMPLATE`` 的
#: study_plan 子集）。副本内再生成规则时，这些值会造成「占位值 → 占位值」
#: 自指改写（副本自己的源码里恰好写着这些字面量）。
TEMPLATE_PLAN = {
    "school": "目标院校",
    "major": "目标专业 (专业代码-方向)",
    "pro_name": "专业课名称 代码",
    "math_weakness": "待诊断",
    "eng_weakness": "待诊断",
    "pol_weakness": "待诊断",
    "pro_weakness": "待诊断",
    "math_baseline": "待摸底",
    "eng_baseline": "待摸底",
    "pol_baseline": "待摸底",
    "pro_baseline": "待摸底",
    "math_books": "[请放入本地参考资料后填写白名单书目]",
    "eng_books": "[请放入本地参考资料后填写白名单书目]",
    "pol_books": "[请放入本地参考资料后填写白名单书目]",
    "pro_books": "[请放入本地参考资料后填写白名单书目]",
}


def test_placeholder_config_generates_no_self_referential_rules(tmp_path):
    """[2026-09-25] 全占位值配置不得生成任何动态规则 —— 否则副本内自指改写。

    实测危害（公开副本 kaoyan_chain_public）：
      * 副本的 ky_config.json 由 ``sync_publish.CONFIG_TEMPLATE`` 写成占位值；
      * 副本内跑 ``build_py_substitutions(副本root)`` 生成 5 类自指规则
        （``待诊断 → 待诊断薄弱点`` / ``目标专业 (专业代码-方向) → 目标专业
        (专业代码)`` 等），把 7 个副本文件改写 —— 含 ``tools/study_planner.py``、
        ``tools/gui/services/settings.py`` 等**功能代码**；
      * 元测试 ``test_tests_dir_is_immune_to_py_sanitization`` 因此变红：
        公开用户初始化（生成 ky_config.json）后跑 pytest 必红。

    阴性对照：从 ``is_placeholder_value()`` 的判据里去掉 SCHOOL/MAJOR 名单
    或 ``待诊断`` 子串，本用例必须变红。
    """
    root = _make_root(tmp_path, TEMPLATE_PLAN)
    assert pp.identity_substitutions(root) == [], (
        f"占位值配置不得生成动态规则: {pp.identity_substitutions(root)}")
    assert pp.identity_name_tokens(root) == [], "占位值不得进文件名判据"

    # 端到端：含占位值文本的「副本文件」必须原样通过（不被自指改写）。
    sample = (
        'X = "待诊断薄弱点"\n'
        'Y = "目标专业 (专业代码-方向)"\n'
        'Z = "专业课名称 代码"\n'
        'W = "[请放入本地参考资料后填写白名单书目]"\n'
    )
    rules = pp.build_py_substitutions(root)
    assert pp.sanitize_text(sample, rules) == sample


def test_placeholder_values_are_gated_across_all_field_types():
    """闸门覆盖面：五类字段的模板值全部命中；真实取值不得被误拦。"""
    for value in ("目标院校", "未指定", "报考专业", "目标专业",
                  "目标专业 (专业代码)", "目标专业 (专业代码-方向)",
                  "专业课名称 代码", "待诊断", "待诊断薄弱点", "待摸底",
                  "[请放入本地参考资料后填写白名单书目]"):
        assert pp.is_placeholder_value(value), value
    for value in ("中国人民大学", "030100 法学", "610 法学基础 810 法学综合",
                  "阅读定位不熟练", "多选题易漏选"):
        assert not pp.is_placeholder_value(value), value


def test_config_template_identity_values_are_all_gated():
    """[防漂移] ``sync_publish.CONFIG_TEMPLATE`` 的身份字段值必须全部命中闸门。

    否则：未来改了配置模板的占位值却忘了同步判据 → 副本内自指改写复燃。
    公开副本里 ``tools/sync_publish.py`` 是 4 行占位（无 CONFIG_TEMPLATE）→ skip。
    """
    sp = pytest.importorskip("tools.sync_publish")
    template = getattr(sp, "CONFIG_TEMPLATE", None)
    if template is None:
        pytest.skip("公开副本里 sync_publish 是占位文件（无 CONFIG_TEMPLATE）")
    plan = template["study_plan"]
    fields = [k for k in plan
              if k in ("school", "major", "pro_name")
              or k.endswith(("_weakness", "_baseline", "_books"))]
    assert fields, "配置模板里应至少有一批身份字段"
    for key in fields:
        assert pp.is_placeholder_value(plan[key]), (key, plan[key])
