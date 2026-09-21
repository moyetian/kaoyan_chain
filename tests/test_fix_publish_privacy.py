# -*- coding: utf-8 -*-
"""P25 回归测试：公开仓库副本（sync_publish）必须与打包路径共用同一套隐私策略。

背景（多角色端到端测试审查报告 P1 的补漏，编号 P25）：
    仓库有两条「内容离开作者本机」的路径 ——
      a) tools/build_package.py  打 PyInstaller 分发包；
      b) tools/sync_publish.py   把工作区镜像成公开仓库副本（kaoyan_chain_public/）。
    报告 P1 只覆盖了 (a)，于是 (b) 长期裸奔。实测 (b) 的 dry-run 计划复制
    5435 个文件，其中 114 个命中私有目录，包含：

      * dist/ 整个 1.8GB 构建产物被当成源码镜像 —— 内含出版社 PDF
        （27腿姐刷题计划-解析_1.pdf / -试题_1.pdf）与李林880 错题切片；
      * 四个科目 _状态/ 下的 今日任务.md、12+ 个 *_backup_* 备份、
        01-数学-核心概念.md、核心速记_帽子词与历史节点.md；
      * 上述文件在现网公开副本 C:/Users/29652/Desktop/kaoyan_chain_public
        中**已经存在**，属正在发生的泄漏。

本测试锁定：sync_publish 的目录/文件两级闸门必须与 tools/privacy_policy.py
（.gitignore:75-108 的单一事实源）口径一致。
"""

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sync_publish as sp  # noqa: E402
from privacy_policy import should_publish  # noqa: E402


# ───────────────────────── 第 1 层：策略模块本身 ─────────────────────────
@pytest.mark.parametrize("rel, publishable", [
    # 私有目录内的个人真实文件 —— 必须剔除
    ("01-数学/_状态/今日任务.md", False),
    ("01-数学/_状态/今日任务_backup_2026-09-18_161554.md", False),
    ("01-数学/_状态/01-数学-核心概念.md", False),
    ("03-思想政治理论/_状态/核心速记_帽子词与历史节点_backup_2026-09-16_102107.md", False),
    ("01-数学/参考资料/李林880题.pdf", False),
    ("03-思想政治理论/参考资料/27腿姐刷题计划-解析_1.pdf", False),
    ("02-英语/错题本/我的错题.md", False),
    ("02-英语/错题与长难句本/我的长难句.md", False),
    ("02-英语/作文语料库/我的语料.md", False),
    ("02-英语/每日笔记/2026-09-18.md", False),
    # 私有目录内的骨架白名单 —— 必须保留（发布包开箱即用的前提）
    ("01-数学/_状态/今日任务.template.md", True),
    ("01-数学/_状态/薄弱点雷达.template.md", True),
    ("03-思想政治理论/_状态/核心速记_帽子词与历史节点.md", True),  # .gitignore:78 显式放行
    ("01-数学/参考资料/README.md", True),
    ("02-英语/错题本/_索引.md", True),
    ("02-英语/错题本/_模板.md", True),
    ("02-英语/作文语料库/通用作文语料.md", True),
    ("02-英语/每日笔记/_模板.md", True),
    # 私有目录之外的个人档案
    ("04-专业课/学情档案.md", False),
    ("04-专业课/学情档案.template.md", True),
    # 个人运行时配置
    ("ky_config.json", False),
    ("state_snapshot.json", False),
    # 正常源码
    ("tools/ky_cli.py", True),
    ("README.md", True),
    ("AGENTS.md", True),
    # 开发脚手架残留（本机调试副产物，可能夹带 ky_config 副本 / 爬取缓存）
    (".agents/orchestrator/task.md", False),
    (".tmp_unidata/admission_units.json", False),
    (".playwright-cli/console-2026-09-19T09-51-05-337Z.log", False),
    (".codex_full_check_04/test_cache_roundtrip0/ky_config.json", False),
    # scratch 目录：任意深度都排除（CLI 粘贴图片的运行时落盘 = 学员真实屏幕内容）
    ("scratch/debug.py", False),
    ("tools/scratch/_contract_diff_report.md", False),
    ("tools/scratch/uploads/clip_1789375408728.png", False),
    # 第三方包内部的 dist/ 目录不得被误伤（.gitignore:17-21 的 C7 教训）
    ("docs/assets/vendor/katex/0.16.9/dist/katex.min.js", True),
    # 与 .gitignore 对齐的受限路径：原始快照 / 消费者视图 / 运行时向量库
    # 都声明「仅本机留存」，发布层必须同口径（否则导出即泄漏）
    ("data/universities/_sources/chsi_schools.json", False),
    ("data/universities/_sources/fjw_universities.json", False),
    ("data/universities/exam_subjects.json", False),
    ("data/knowledge/embeddings.db", False),
    # 阴性对照：公开派生库**必须仍然发布**，不得被上面几条误伤
    ("data/universities/registry.json", True),
    ("data/universities/national_institutions.json", True),
])
def test_privacy_policy_verdicts(rel, publishable):
    assert should_publish(rel) is publishable, f"策略判定错误: {rel}"


# ─────────────────── 第 2 层：sync_publish 目录级闸门 ───────────────────
@pytest.mark.parametrize("parts, name", [
    (["dist"], "dist"),
    (["build"], "build"),
    (["dist_wheels"], "dist_wheels"),
    (["node_modules"], "node_modules"),
    (["kaoyan_study_chain.egg-info"], "kaoyan_study_chain.egg-info"),
    (["rust_ext"], "rust_ext"),
    # 开发脚手架目录：本机调试副产物，可能夹带 ky_config 副本与爬取缓存
    ([".agents"], ".agents"),
    ([".tmp_unidata"], ".tmp_unidata"),
    ([".playwright-cli"], ".playwright-cli"),
    ([".codex_full_check_04"], ".codex_full_check_04"),
    ([".git"], ".git"),
    ([".memory"], ".memory"),
    (["__pycache__"], "__pycache__"),
    # PyInstaller 冻结产物的私有资源根（防御性排除，见 EXCLUDE_DIRS 注释）
    (["_internal"], "_internal"),
    (["dist", "KaoyanStudyChain", "_internal"], "_internal"),
    # 受限路径的目录同样不下钻（不在副本里留下空目录）
    (["data", "universities"], "_sources"),
    (["data"], "knowledge"),
])
def test_build_artifacts_and_secrets_excluded(parts, name):
    """构建产物与密钥目录必须整棵子树排除。"""
    assert sp.dir_should_exclude(parts, name) is True, f"未排除目录: {name}"


def test_private_dirs_are_descended_into():
    """私有目录本身不能整棵排除 —— 必须下钻以便放行骨架白名单。"""
    for d in ("_状态", "参考资料", "作文语料库", "错题与长难句本"):
        assert sp.dir_should_exclude(["01-数学", d], d) is False, f"误排除私有目录: {d}"


# ─────────────────── 第 3 层：sync_publish 文件级闸门 ───────────────────
@pytest.mark.parametrize("rel", [
    "01-数学/_状态/今日任务.md",
    "01-数学/_状态/今日任务_backup_2026-09-18_161554.md",
    "01-数学/_状态/01-数学-核心概念.md",
    "03-思想政治理论/_状态/核心速记_帽子词与历史节点_backup_2026-09-16_102107.md",
    "01-数学/参考资料/李林880题.pdf",
    "03-思想政治理论/参考资料/27腿姐刷题计划-解析_1.pdf",
    "02-英语/作文语料库/我的语料.md",
    "04-专业课/学情档案.md",
    "ky_config.json",
    "dist/KaoyanStudyChain/03-思想政治理论/参考资料/27腿姐刷题计划-解析_1.pdf",
])
def test_sync_publish_file_gate_excludes_private(rel):
    """任何私有/构建产物文件都不得被计划复制到公开副本。"""
    p = Path(rel)
    assert sp.file_should_exclude(p.parts, p.name) is True, f"公开副本会夹带: {rel}"


@pytest.mark.parametrize("rel", [
    "01-数学/_状态/今日任务.template.md",
    "01-数学/参考资料/README.md",
    "03-思想政治理论/_状态/核心速记_帽子词与历史节点.md",
    "tools/ky_cli.py",
    "AGENTS.md",
])
def test_sync_publish_file_gate_keeps_skeletons(rel):
    """骨架文件与正常源码必须继续发布，避免修复过头。"""
    p = Path(rel)
    assert sp.file_should_exclude(p.parts, p.name) is False, f"误剔除: {rel}"


# ──────────────── 第 4 层：已泄漏副本必须能被清理 ────────────────
def test_leaked_paths_helper_finds_existing_leaks():
    """审计助手应能一眼列出给定清单里的泄漏项。

    [P25 补漏 · 缺陷 1] 顺带覆盖「内部文档」这一类：审查报告 / 多轮实测报告 /
    原始需求书必须被判为不可发布。修复前 ``sync_publish.EXCLUDE_REPORTS`` 只
    硬编码 4 个 ``D盘实测_*.md`` 文件名，与 ``.gitignore:161`` 的模式
    ``*审查报告*.md`` 口径漂移，于是新出现的两份审查报告漏进公开副本。
    """
    from privacy_policy import leaked_paths
    internal_docs = [
        "代码审查报告_全量.md",
        "多角色端到端测试审查报告.md",
        "D盘实测_用户视角全功能运行报告.md",
        "D盘实测_第二轮全功能运行报告.md",
        "D盘实测_第三轮_2027考生全流程CLI-TUI-GUI贯通测试报告.md",
        "D盘实测_第四轮_2027天工大考生_三端全流程核对报告.md",
        "ORIGINAL_REQUEST.md",
    ]
    got = leaked_paths(ROOT, [
        "01-数学/_状态/今日任务.md",
        "01-数学/_状态/今日任务.template.md",
        "03-思想政治理论/参考资料/27腿姐刷题计划-解析_1.pdf",
        *internal_docs,
        "README.md",
    ])
    assert got == [
        "01-数学/_状态/今日任务.md",
        "03-思想政治理论/参考资料/27腿姐刷题计划-解析_1.pdf",
        *internal_docs,
    ]
    # 发布闸门与策略同源：内部文档必须同样被 file_should_exclude 拒绝
    for rel in internal_docs:
        p = Path(rel)
        assert sp.file_should_exclude(p.parts, p.name) is True, f"发布闸门放行内部文档: {rel}"


# ──────────────── 第 5 层：内容级脱敏必须覆盖「当前」真实报考信息（R2） ────────────────
# 复测发现的漏网之鱼：目录策略修好了，内容仍在裸奔。
# SUBSTITUTIONS 只硬编码了历年校名（天津工业大学 / 华南理工大学…），
# 学员一换院校，新的真实校名、专业与自命题科目代码就不在任何名单里，
# 而 sanitize_markdown_files() 会扫全部 *.md（含 AGENTS.md），等于毫无保护。


def test_identity_substitutions_cover_current_target(tmp_path):
    """动态规则必须覆盖 study_plan 里的校名 / 专业 / 自命题科目代码。

    [R2-C3 顺带] 改为**自造配置**：原先读本机 ``ky_config.json``，而该文件是
    gitignore 保护的个人配置、会被其它角色的演练/清空操作改写（实测被改成
    ``school=目标院校`` 后本测试误报「真实校名未脱敏」）。测逻辑就不该依赖环境。
    """
    from privacy_policy import identity_substitutions

    plan = {
        "school": "示例农业大学",
        "major": "030500 示例理论",
        "pro_name": "618 示例科目甲 823 示例科目乙",
    }
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"study_plan": plan}, ensure_ascii=False), encoding="utf-8")

    rules = identity_substitutions(tmp_path)
    assert rules, "study_plan 已配置却没有任何动态脱敏规则"

    sample = f"{plan['school']} {plan['major']} {plan['pro_name']}"
    out = sp.sanitize_text(sample, rules)
    for value in (plan["school"], plan["major"], "618 示例科目甲", "823 示例科目乙"):
        assert value not in out, f"未脱敏: {value} -> {out}"

    # [P25 补漏 · 缺陷 2] 老规则是**字面顺序敏感**的完整串匹配，于是报告原文里
    #   「法学 030100 · 自命题 610/810」这种换序 / 缩写 / 并写形式全部漏网 ——
    #   专业代码 + 自命题科目代码的组合本身就是考生可识别指纹。
    #
    # 注意：这里的 plan2 用**中性示例身份**（不是学员真实报考信息）。理由见
    # tests/README 与 test_privacy_identity_rules.test_tests_dir_is_immune_to_py_sanitization：
    # 测试夹具一旦写入真实身份，导出脱敏就会改写本文件、令断言自毁。
    plan2 = {
        "school": "示例农业大学",
        "major": "030100 法学",
        "pro_name": "610 法学基础 810 法学综合",
    }
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"study_plan": plan2}, ensure_ascii=False), encoding="utf-8")
    rules2 = identity_substitutions(tmp_path)

    line = ("| **A** 小白文科跨考生 | 目标院校 · **法学 030100** · "
            "**不考数学** · **自命题 610/810** · 英语一 |")
    out2 = sp.sanitize_text(line, rules2)
    for tok in ("030100", "610", "810"):
        assert tok not in out2, f"报告原文的换序/缩写形式漏网: {out2}"

    for text in ("法学 030100", "030100 法学", "法学030100", "030100",
                 "610/810", "610 810", "610、810", "810/610", "自命题 610 科目"):
        o = sp.sanitize_text(text, rules2)
        assert not any(t in o for t in ("030100", "610", "810")), \
            f"换序/缩写/并写形式漏网: {text!r} -> {o!r}"

    # 反向守卫：判据是「两码共现或紧邻语境词」—— 普通数字绝不能被误伤。
    for text in ("1688", "610元", "页码 610", "2026-06-10", "810", "共 810 人"):
        assert sp.sanitize_text(text, rules2) == text, f"普通数字被误伤: {text!r}"

    # URL 百分号编码形态（分享/检索链接）：修复前导出物的链接里仍带身份字面量。
    from urllib.parse import quote
    url = (f"https://x.com/s?q={quote('示例农业大学', safe='')}%20"
           f"030100%20{quote('法学', safe='')}")
    out3 = sp.sanitize_text(url, rules2)
    assert "030100" not in out3, out3
    assert quote("法学", safe="") not in out3, out3


def test_dynamic_identity_rules_survive_py_exclusions():
    """动态身份规则进入 ``*.py`` 规则集的边界必须精准。

    ``PY_EXCLUDED_PATTERNS`` 为保护「公开数据当功能数据」而存在；当前真实报考
    身份（校名 / 专业组合 / 科目组合）必须**始终**进入 ``*.py`` 规则集，否则发布
    副本的 .py 又会残留校名/专业。但 [缺陷 3b] **裸专业代码**是**全国统一学科
    门类代码**（公开事实），必须留在 .md/.html/.svg 一侧 —— 它是
    ``chsi_connector.STANDARD_SUBJECTS_CATALOG`` 的键名与十余个测试的公开常量。
    """
    from privacy_policy import identity_py_excluded_patterns, identity_substitutions

    dynamic = identity_substitutions(ROOT)
    if not dynamic:
        pytest.skip("本工作区 ky_config.json 未配置 study_plan")
    md_only = set(identity_py_excluded_patterns(ROOT))
    py_pats = {p for p, _ in sp.PY_SUBSTITUTIONS}
    for pat, _repl in dynamic:
        if pat in md_only:
            assert pat not in py_pats, f"裸专业代码规则泄漏进 *.py 规则集: {pat}"
            assert pat in sp.PY_EXCLUDED_PATTERNS, f"裸专业代码规则未登记为 .py 排除: {pat}"
        else:
            assert pat in py_pats, f"动态身份规则被 PY_EXCLUDED_PATTERNS 误排除: {pat}"


def test_identity_substitutions_do_not_mangle_generic_terms():
    """修复必须精准：不得把「马克思主义理论」这类学科通用词一起替换掉。

    它们在 03-思想政治理论 的正文里大量合法出现，全局替换会把公开副本的
    学习内容改坏 —— 这比泄漏更难被发现。
    """
    from privacy_policy import identity_substitutions

    rules = identity_substitutions(ROOT)
    generic = ("马克思主义理论", "马克思主义基本原理", "中国化马克思主义理论与实践")
    for name in generic:
        for pat, _repl in rules:
            assert pat != re.escape(name), f"通用学科词被整体替换: {name}"


def test_sanitize_text_handles_missing_config(tmp_path):
    """ky_config.json 缺失（如已生成的公开副本）时不得抛异常，退化为纯静态表。"""
    from privacy_policy import identity_substitutions, load_study_plan

    assert load_study_plan(tmp_path) == {}
    assert identity_substitutions(tmp_path) == []


# ─────────── 第 6 层：*.py 脱敏（R2-C2）——必须真脱敏，且不能改坏功能数据 ───────────


def test_py_sanitize_scrubs_current_identity(tmp_path):
    """发布副本里的 .py 不得残留学员当前真实报考身份（校名 / 专业 / 科目组合）。

    修复前实测：bee1474 产物的 ``*.py`` 中「中国人民大学」命中 15 处。
    与上面同理，用自造配置，避免依赖会被其它角色改写的本机 ky_config.json。
    """
    from privacy_policy import identity_substitutions

    plan = {
        "school": "示例农业大学",
        "major": "030500 示例理论",
        "pro_name": "618 示例科目甲 823 示例科目乙",
    }
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"study_plan": plan}, ensure_ascii=False), encoding="utf-8")

    dynamic = identity_substitutions(tmp_path)
    py_rules = [(p, r) for p, r in dynamic if p not in sp.PY_EXCLUDED_PATTERNS]
    assert py_rules, "动态身份规则全部被排除，*.py 将毫无保护"

    for value in (plan["school"], plan["major"], "618 示例科目甲", "823 示例科目乙"):
        # 同时覆盖「字符串字面量」与「注释」两种语境
        src = f'x = "{value}"  # 说明：{value}\n'
        out = sp.sanitize_text(src, py_rules)
        assert value not in out, f"*.py 规则集未覆盖当前身份: {value} -> {out}"
        compile(out, "<sanitized>", "exec")


def test_py_excluded_patterns_keep_functional_data():
    """⑤ 公开校名在 .py 里是**功能数据**（985/211 名单），必须原样保留。

    实测反例：把 school_scout.py 的 ``top_985`` 里「天津大学」「中山大学」替换成
    「对比院校B」后，发布副本的 985/211 识别直接失效。
    """
    functional = ("天津大学", "中山大学", "武汉大学", "华中科技大学", "华南理工大学")
    for name in functional:
        assert name in sp.sanitize_text(name, sp.PY_SUBSTITUTIONS), f"功能数据被脱敏改坏: {name}"

    # 日期与上游模型标识同属功能默认值，替换会改变冻结程序语义
    assert sp.sanitize_text("2026-12-19", sp.PY_SUBSTITUTIONS) == "2026-12-19"
    assert sp.sanitize_text("gpt-5.4-mini", sp.PY_SUBSTITUTIONS) == "gpt-5.4-mini"

    # [缺陷 3b] 裸专业代码是**全国统一学科门类代码**（公开事实），在 .py 里是
    # 公开目录键名与测试常量，不得被 .py 规则集替换；但「专业名 + 代码」的
    # **组合**本身即身份，在 .py 里出现仍应替换。
    #
    # 探针从**运行时配置**拼出，不写死字面量 —— 写死真实身份会让导出脱敏改写
    # 本文件本身（见 test_privacy_identity_rules.test_tests_dir_is_immune_to_py_sanitization）。
    import privacy_policy as pp

    plan_now = pp.load_study_plan(ROOT)
    m = re.match(r"^(\d{3,6})\s+(.+)$", str(plan_now.get("major") or "").strip())
    if not m:
        pytest.skip("本工作区无 ky_config.json / major（无身份可验证）")
    code_now, name_now = m.group(1), m.group(2)

    chsi = ROOT / "tools" / "intelligence" / "chsi_connector.py"
    if chsi.exists():
        src = chsi.read_text(encoding="utf-8")
        if f'"{code_now}"' in src:
            out = sp.sanitize_text(src, sp.PY_SUBSTITUTIONS)
            assert f'"{code_now}"' in out, (
                "公开学科目录键名被 .py 脱敏改坏（STANDARD_SUBJECTS_CATALOG）")
            compile(out, "<chsi>", "exec")
    combo = sp.sanitize_text(f"{name_now} {code_now}", sp.PY_SUBSTITUTIONS)
    assert code_now not in combo, f"组合身份在 .py 里未脱敏: {combo}"


def test_py_sanitize_output_still_compiles():
    """安全闸门：脱敏后的 .py 必须仍能 compile()。

    修复过程中真实踩到的坑：行级规则 ``LOCAL_WHITELIST_RE`` 会把
    ``summary_str = f"[本地资料库已就绪]: " + ...`` 的收尾引号一起吃掉，产出
    ``unterminated f-string literal``；这条测试把该场景钉死。
    """
    samples = [
        'summary_str = f"[本地资料库已就绪]: " + str(len(items)) + " 项"\n',
        'top_985 = ["天津大学", "中山大学", "武汉大学"]\n',
        'DEFAULT_EXAM_DATE = "2026-12-19"\n',
        'MODEL = "gpt-5.4-mini"\n',
    ]
    for src in samples:
        out = sp.sanitize_text(src, sp.PY_SUBSTITUTIONS)
        compile(out, "<sanitized>", "exec")  # 语法错误即失败


def test_sanitize_never_targets_json():
    """边界守卫：``*.json`` 绝不在脱敏扫描后缀里（公开高校数据库，非个人身份）。"""
    assert not any(s.endswith("json") for s in sp.SANITIZED_SUFFIXES), sp.SANITIZED_SUFFIXES


def test_universities_json_byte_identical_after_publish_pipeline(tmp_path, monkeypatch):
    """守卫：跑**真实脱敏管线**后，``data/universities/**/*.json`` 必须逐字节不变。

    比「看起来没坏」可靠 —— 直接比对字节。一旦有人把 ``*.json`` 加进
    ``SANITIZED_SUFFIXES``，高校库会被整片改坏（校名→「目标院校」），这是最难被
    发现的一类回归。
    """
    src = ROOT / "data" / "universities"
    dst = tmp_path / "dst"
    shutil.copytree(src, dst / "data" / "universities")

    monkeypatch.setattr(sp, "DST", dst)
    sp.sanitize_markdown_files()

    files = sorted(src.rglob("*.json"))
    assert files, "data/universities 下没有 json，守卫测试失去意义"
    for f in files:
        rel = f.relative_to(src)
        assert (dst / "data" / "universities" / rel).read_bytes() == f.read_bytes(), \
            f"{rel} 在发布管线中被改动"


def test_universities_json_guard_is_not_vacuous():
    """反证：公开高校库**确实**含替换表里的校名 —— 所以上面的守卫不是空转。

    实测 ``_sources/chsi_408_offerings.json`` 里 9 个校名合计命中 49 次；
    若把 ``*.json`` 纳入脱敏，这份 408 招生快照会被整片改坏。
    """
    f = ROOT / "data" / "universities" / "_sources" / "chsi_408_offerings.json"
    if not f.exists():
        pytest.skip("本工作区没有 408 快照")

    text = f.read_text(encoding="utf-8")
    changed = sp.sanitize_text(text, sp.SUBSTITUTIONS)
    assert changed != text, (
        "公开高校库已不含替换表中的校名；请重新评估「*.json 不脱敏」这条边界的必要性"
    )


# ─────────────── 第 7 层：.bat 启动器编码（R2-C3） ───────────────

BAT_LAUNCHERS = ("GUI.bat", "启动GUI.bat", "调试模式启动GUI.bat", "更新看板.bat", "ky.bat")


def _bat_bytes(name: str) -> bytes:
    p = ROOT / name
    assert p.exists(), f"{name} 不存在"
    return p.read_bytes()


def test_bat_launchers_declare_matching_codepage():
    """每个 .bat 的**落盘编码**必须与它自己 ``chcp`` 声明的码页一致。

    背景（R2-C3 实测，全新控制台 CREATE_NEW_CONSOLE）：
      * UTF-8 无 BOM + ``chcp 65001``：cmd 先按控制台 CP936 读文件，中文被误解码后
        整行当命令执行（``'官方下载地址:' 不是内部或外部命令``）并吞掉后续行，
        关键提示全不打印 —— 修复前实测 nopy / crash 两分支各 10/10 轮必现；
      * UTF-8 带 BOM：cmd 把 BOM 当命令字符，``'锘緻chcp' 不是内部或外部命令``，
        连 ``@echo off`` 都失效；
      * 只在文件内 ``chcp 65001``（含 ``call %~f0`` 自重入）**不能修复**，实测 6/6 轮仍报错。
    唯一稳定方案：GBK(CP936) 落盘 + ``chcp 936``，读文件与打印输出同码页。
    """
    for name in BAT_LAUNCHERS:
        raw = _bat_bytes(name)
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{name} 带 BOM，cmd 会把它当命令字符"
        assert raw.count(b"\n") == raw.count(b"\r\n"), f"{name} 存在裸 LF 行"

        m = re.search(rb"chcp\s+(\d+)", raw)
        assert m, f"{name} 未声明 chcp"
        cp = int(m.group(1))
        codec = {936: "gbk", 65001: "utf-8"}.get(cp)
        assert codec, f"{name} 使用了未纳入约定的码页 {cp}"
        try:
            text = raw.decode(codec)
        except UnicodeDecodeError as e:
            raise AssertionError(f"{name} 落盘编码与 chcp {cp} 不匹配: {e}") from e

        # chcp 生效之前的内容必须是纯 ASCII，否则首行就可能被误读
        head = text[: text.index(f"chcp {cp}")]
        offenders = [ln for ln in head.splitlines() if not ln.isascii()]
        assert not offenders, f"{name} 在 chcp 之前出现非 ASCII 行: {offenders}"


def test_gui_bat_no_longer_declares_utf8_codepage():
    """锁定回归：GUI.bat 生效的 chcp 必须是 936（修复前是 65001）。"""
    raw = _bat_bytes("GUI.bat")
    first = re.search(rb"chcp\s+(\d+)", raw)
    assert first and first.group(1) == b"936", "GUI.bat 首个 chcp 不是 936"


# ─────────── 第 8 层：身份文件名脱敏的单一事实源（R2-D5） ───────────
# 复测发现：目录级与内容级都修好之后，**文件名本身**仍在裸奔 ——
#   dist/KaoyanStudyChain/_internal/04-专业课/
#     目标院校情报_中国人民大学_030100 法学_backup_2026-09-18_162147.md
# 两条出口（打包 / 发布副本）曾各维护一份「哪些文件名算身份」的名单，必然漂移：
# 打包路径漏了 双校对标_*，发布路径漏了后来新出现的身份文件名。


def test_rename_name_patterns_single_source():
    """RENAME_NAME_PATTERNS 必须定义在 privacy_policy，且被 sync_publish 复用。"""
    import privacy_policy as pp

    assert hasattr(pp, "RENAME_NAME_PATTERNS"), \
        "RENAME_NAME_PATTERNS 未收敛到隐私策略单一事实源 privacy_policy"
    assert sp.RENAME_NAME_PATTERNS is pp.RENAME_NAME_PATTERNS, \
        "sync_publish 仍在本地维护一份文件名脱敏表（与打包路径必然漂移）"

    # [P25 补漏 · 缺陷 1] 「内部文档排除」同样必须单一事实源 + 模式匹配，
    # 与 .gitignore:161 的 ``*审查报告*.md`` 口径一致；sync_publish 不得再维护
    # 第二份硬编码文件名清单（正是它导致新报告漏网）。
    assert hasattr(pp, "INTERNAL_DOC_PATTERNS"), "内部文档排除未收敛到单一事实源"
    assert "*审查报告*.md" in pp.INTERNAL_DOC_PATTERNS, "与 .gitignore 口径不一致"
    assert "ORIGINAL_REQUEST.md" in pp.INTERNAL_DOC_PATTERNS
    assert not hasattr(sp, "EXCLUDE_REPORTS"), \
        "sync_publish 仍在本地维护硬编码报告名单（与 .gitignore 必然漂移）"


@pytest.mark.parametrize("name", [
    "目标院校情报_示例农业大学_030500 示例理论.md",
    "双校考情对比_示例农业大学_VS_对比院校B_030500 示例理论.md",
    "双校对标_示例农业大学_VS_对比院校B_030500 示例理论.md",
])
def test_rename_name_patterns_cover_identity_reports(name):
    """三类个性化侦察报告的文件名都必须被脱敏表覆盖（含此前漏掉的 双校对标_）。"""
    assert any(pat.match(name) for pat, _ in sp.RENAME_NAME_PATTERNS), f"未覆盖: {name}"


def test_identity_name_tokens_are_precise():
    """身份字面量只取「校名 / 专业组合 / 自命题科目组合」，不含裸学科通用词。"""
    import privacy_policy as pp

    tokens = pp.identity_name_tokens(ROOT)
    if not tokens:
        pytest.skip("本工作区 ky_config.json 未配置 study_plan")
    for generic in ("马克思主义理论", "马克思主义基本原理", "中国化马克思主义理论与实践"):
        assert generic not in tokens, f"裸学科通用词被当成身份标记: {generic}"
    # 每个身份字面量都必须有同源的替换规则（同一套口径，不另起炉灶）
    rules = " ".join(p for p, _ in pp.identity_substitutions(ROOT))
    for tok in tokens:
        assert re.escape(tok) in rules, f"身份字面量无同源替换规则: {tok}"


def test_identity_filename_rules_scope_public_university_db(tmp_path):
    """公开高校库（data/universities）不适用身份文件名剔除 —— 校名是功能数据。"""
    import json

    import privacy_policy as pp

    plan = {"school": "示例农业大学", "major": "030500 示例理论", "pro_name": ""}
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"study_plan": plan}, ensure_ascii=False), encoding="utf-8")

    # 身份规则本身能识别（路径级豁免由打包路径负责）
    assert pp.identity_filename_reason("示例农业大学.yaml", tmp_path) is not None

    import tools.build_package as bp
    assert "data" in bp.IDENTITY_NAME_EXEMPT_ROOTS, \
        "data/ 未豁免身份文件名红线，公开高校库会被整条报红/丢掉"


def test_publish_pipeline_scrubs_python_sources(tmp_path, monkeypatch):
    """R2-C2 管线级回归：``sanitize_markdown_files()`` 必须真的改写 ``*.py``。

    比「规则集里有 .py」更靠得住 —— 直接跑真实脱敏函数。若有人把 ``*.py``
    从 ``SANITIZED_SUFFIXES`` 里拿掉，发布副本的 Python 源码就会原样公开身份
    （修复前实测：bee1474 产物的 ``*.py`` 中「中国人民大学」命中 15 处），
    而这类回归在只看 md 的测试里完全看不见。
    """
    import privacy_policy as pp

    plan = {"school": "示例农业大学", "major": "030500 示例理论",
            "pro_name": "618 示例科目甲 823 示例科目乙"}
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"study_plan": plan}, ensure_ascii=False), encoding="utf-8")
    dynamic = pp.identity_substitutions(tmp_path)
    assert dynamic, "自造配置未产生动态脱敏规则，测试失去意义"

    dst = tmp_path / "dst"
    py = dst / "tools" / "sample.py"
    py.parent.mkdir(parents=True)
    py.write_text(
        '"""示例农业大学 · 030500 示例理论 说明。"""\n'
        'SCHOOL = "示例农业大学"  # 示例农业大学\n',
        encoding="utf-8")

    monkeypatch.setattr(sp, "DST", dst)
    monkeypatch.setattr(sp, "SUBSTITUTIONS", dynamic + sp.SUBSTITUTIONS)
    monkeypatch.setattr(sp, "PY_SUBSTITUTIONS",
                        [(p, r) for p, r in dynamic + sp.SUBSTITUTIONS
                         if p not in sp.PY_EXCLUDED_PATTERNS])
    sp.sanitize_markdown_files()

    out = py.read_text(encoding="utf-8")
    assert "示例农业大学" not in out, f"*.py 未被管线脱敏: {out}"
    assert "030500 示例理论" not in out, f"*.py 未被管线脱敏: {out}"
    compile(out, "<sanitized>", "exec")  # 脱敏后必须仍是合法 Python


def test_registry_py_is_exempt_from_py_sanitization(tmp_path, monkeypatch):
    """R2-C2 边界补漏：公开高校映射表 ``registry.py`` 不得被 ``*.py`` 脱敏改动。

    privacy_policy 的文档边界早已写明「tools/intelligence/registry.py 的
    『校名 → 院校代码』映射也不得脱敏」，但此前**没有任何代码执行它**。实测
    发布副本里 ``KNOWN_REGIONAL`` 的

        ``"中国人民大学": ("10466", "河南郑州", ...)``

    被替换成 ``"目标院校": ("10466", ...)`` —— 公开高校兜底索引整条改坏，
    校名解析失配，还凭空多出一个叫「目标院校」的学校。
    """
    src = ROOT / "tools" / "intelligence" / "registry.py"
    if not src.exists():
        pytest.skip("本工作区没有 registry.py")

    probe = "中国人民大学"
    if probe not in src.read_text(encoding="utf-8"):
        pytest.skip("registry.py 未含探测串，守卫会空转")

    dst = tmp_path / "dst"
    target = dst / "tools" / "intelligence" / "registry.py"
    target.parent.mkdir(parents=True)
    shutil.copy2(src, target)
    # 对照组：同目录下普通 .py 必须照常脱敏（证明跳过是精准的，不是整体空转）
    other = dst / "tools" / "ordinary.py"
    other.write_text(f'SCHOOL = "{probe}"\n', encoding="utf-8")

    # 造一条**必然命中**的规则：证明 registry.py 是被显式跳过，而非碰巧没命中
    synthetic = [(re.escape(probe), "目标院校")]
    monkeypatch.setattr(sp, "DST", dst)
    monkeypatch.setattr(sp, "SUBSTITUTIONS", synthetic)
    monkeypatch.setattr(sp, "PY_SUBSTITUTIONS", synthetic)
    sp.sanitize_markdown_files()

    assert probe not in other.read_text(encoding="utf-8"), \
        "对照组的普通 .py 未被脱敏，本测试失去意义"
    assert target.read_bytes() == src.read_bytes(), \
        "公开高校映射表 registry.py 被脱敏改动（校名→目标院校），校名解析会失配"


# ─────────── 第 9 层：配置被中性化时的「静默失效」守卫 ───────────
# 实锤（本机 ../kaoyan_chain_public，生成于 2026-09-19 20:35）：
#   含**静态**规则词（天津工业大学/华南理工大学…）的 .md = 0 个；
#   含**动态**身份词（真实校名/专业）的 .md = 52 个；共 72 个文件带真实身份。
#   → .md 脱敏确实跑了（静态规则生效），只是 identity_substitutions() 生成的
#     动态规则整体缺失/自指 —— 而 sync_publish 既不报错也不警告，exit 0。
# 根因：规则是运行时从 ky_config.json 生成的，配置被冲成占位值即整体失效。


def _write_plan(root: Path, plan: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ky_config.json").write_text(
        json.dumps({"study_plan": plan}, ensure_ascii=False), encoding="utf-8")


def test_identity_rules_effective_rejects_placeholder_config(tmp_path):
    """占位/缺失配置必须被判定为「规则无效」。"""
    from privacy_policy import identity_rules_effective

    assert identity_rules_effective(tmp_path)[0] is False          # 配置缺失
    _write_plan(tmp_path / "empty", {})
    assert identity_rules_effective(tmp_path / "empty")[0] is False  # study_plan 空

    _write_plan(tmp_path / "placeholder", {
        "school": "目标院校", "major": "目标专业 (专业代码-方向)",
        "pro_name": "专业课名称 代码"})
    ok, reason = identity_rules_effective(tmp_path / "placeholder")
    assert ok is False, reason
    assert "占位值" in reason

    _write_plan(tmp_path / "real", {
        "school": "示例农业大学", "major": "030500 示例理论",
        "pro_name": "618 示例科目甲 823 示例科目乙"})
    ok, reason = identity_rules_effective(tmp_path / "real")
    assert ok is True, reason


def test_main_refuses_export_when_identity_rules_ineffective(tmp_path, monkeypatch):
    """配置中性化时必须**拒绝导出**，而不是静默产出泄漏物。"""
    src = tmp_path / "repo"
    _write_plan(src, {"school": "目标院校", "major": "目标专业 (专业代码-方向)",
                      "pro_name": "专业课名称 代码"})
    dst = tmp_path / "dst"
    monkeypatch.setattr(sp, "SRC", src)
    monkeypatch.setattr(sp, "DST", dst)
    monkeypatch.setattr(sys, "argv", ["sync_publish.py", "--force"])

    with pytest.raises(SystemExit) as exc:
        sp.main()
    assert exc.value.code == 3, f"未拒绝导出（退出码 {exc.value.code}）"
    assert not dst.exists(), "拒绝导出后不得留下任何产物"


def test_main_allows_export_only_with_explicit_override(tmp_path, monkeypatch):
    """只有显式 --allow-placeholder-identity 才放行。"""
    src = tmp_path / "repo"
    (src / "04-专业课").mkdir(parents=True)
    _write_plan(src, {"school": "目标院校"})
    (src / "04-专业课" / "note.md").write_text("目标院校\n", encoding="utf-8")
    dst = tmp_path / "dst"
    monkeypatch.setattr(sp, "SRC", src)
    monkeypatch.setattr(sp, "DST", dst)
    monkeypatch.setattr(sys, "argv", ["sync_publish.py", "--force",
                                      "--allow-placeholder-identity"])
    sp.main()  # 不应抛错
    assert dst.exists()


def test_scan_residual_identity_excludes_public_db(tmp_path, monkeypatch):
    """导出后自检必须能列出残留，且豁免公开库与公开高校索引。"""
    src = tmp_path / "repo"
    _write_plan(src, {"school": "示例农业大学"})
    monkeypatch.setattr(sp, "SRC", src)

    dst = tmp_path / "dst"
    (dst / "04-专业课").mkdir(parents=True)
    (dst / "04-专业课" / "leak.md").write_text("示例农业大学\n", encoding="utf-8")
    (dst / "data" / "universities").mkdir(parents=True)
    (dst / "data" / "universities" / "u.json").write_text("示例农业大学\n", encoding="utf-8")
    reg = dst / "tools" / "intelligence" / "registry.py"
    reg.parent.mkdir(parents=True)
    reg.write_text('"示例农业大学": ("10466",)\n', encoding="utf-8")
    (dst / "clean.py").write_text("print('ok')\n", encoding="utf-8")

    assert sp.scan_residual_identity(dst) == ["04-专业课/leak.md"]


def test_main_runs_post_export_residual_selfcheck(tmp_path, monkeypatch):
    """导出管线必须**真的调用**残留自检（否则「还剩多少文件带身份」无人可见）。

    上面那条只测了 ``scan_residual_identity()`` 本身；若有人把它从 ``main()``
    里摘掉，函数照样是绿的，静默失效却重新回来了 —— 这里钉住调用点。
    """
    src = tmp_path / "repo"
    (src / "04-专业课").mkdir(parents=True)
    (src / "04-专业课" / "note.md").write_text("示例农业大学\n", encoding="utf-8")
    _write_plan(src, {"school": "示例农业大学"})

    dst = tmp_path / "dst"
    monkeypatch.setattr(sp, "SRC", src)
    monkeypatch.setattr(sp, "DST", dst)
    monkeypatch.setattr(sys, "argv", ["sync_publish.py", "--force"])

    called = []
    real = sp.scan_residual_identity
    monkeypatch.setattr(sp, "scan_residual_identity",
                        lambda d: called.append(Path(d)) or real(d))

    sp.main()
    assert called, "导出管线未调用 scan_residual_identity（残留自检形同虚设）"
    # 自检必须作用在**产物目录**上，而不是源目录
    assert called[-1] == dst


def test_main_refuses_dst_inside_src(tmp_path, monkeypatch):
    """DST 落在工作区内部时必须拒绝导出（否则会自我递归到 WinError 206）。

    实测：把 DST 指成 ``<SRC>/publish`` 后，python_mirror 把 DST 当成普通子目录
    反复拷进自己，产出 ``publish/publish/publish/…``（21 层后 Windows 路径超长）。
    默认 DST 在工作区之外，但用户很容易顺手写 ``./publish``。
    """
    src = tmp_path / "repo"
    (src / "04-专业课").mkdir(parents=True)
    _write_plan(src, {"school": "示例农业大学"})
    monkeypatch.setattr(sp, "SRC", src)
    monkeypatch.setattr(sp, "DST", src / "publish")
    monkeypatch.setattr(sys, "argv", ["sync_publish.py", "--force"])

    with pytest.raises(SystemExit) as exc:
        sp.main()
    assert exc.value.code == 4, f"未拒绝工作区内部的 DST（退出码 {exc.value.code}）"
    assert not (src / "publish").exists(), "拒绝导出后不得在工作区里留下产物"

