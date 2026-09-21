# -*- coding: utf-8 -*-
"""发布脱敏策略 · 单一事实源（Single Source of Truth）。

**为什么需要这个模块**

本仓库有两条「内容离开作者本机」的路径：

1. ``tools/build_package.py`` —— 打 PyInstaller 分发包（dist/）；
2. ``tools/sync_publish.py`` —— 把工作区镜像成公开仓库副本（kaoyan_chain_public/）。

历史教训（多角色端到端测试审查报告 P1 / P25）：这两条路径各自维护一份
「哪些目录不能发」的清单，结果只修了其中一条，另一条继续把作者真实学习数据、
错题本、甚至出版社 PDF 发出去。策略一旦重复定义就必然漂移，故收敛到本模块。

**权威依据**

白名单与 ``.gitignore:75-108`` 的 ``!`` 反选条目严格一一对应 ——
凡是 .gitignore 主动保护的私有目录，只有它显式放行的骨架文件可以发布。
本模块的任何改动都必须同步核对 ``.gitignore``，反之亦然。

**用法**

    from privacy_policy import should_publish, private_owner

    if not should_publish(rel_path):   # rel_path 相对仓库根
        continue                        # 绝不复制 / 绝不打包
"""

import json
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Union
from urllib.parse import quote

__all__ = [
    "PRIVATE_DIRS",
    "SKELETON_WHITELIST",
    "PRIVATE_TOP_LEVEL_FILES",
    "JUNK_DIR_NAMES",
    "JUNK_FILE_EXTS",
    "PRIVATE_CONFIG_FILES",
    "BUILD_ARTIFACT_DIRS",
    "DEV_SCRATCH_DIRS",
    "ROOT_ONLY_EXCLUDE_DIRS",
    "NON_PUBLISH_PATH_PREFIXES",
    "PRIVATE_WORKSPACE_ONLY_PATHS",
    "BACKUP_MARK",
    "INTERNAL_DOC_PATTERNS",
    "RENAME_NAME_PATTERNS",
    "private_owner",
    "is_backup",
    "is_junk",
    "should_publish",
    "load_study_plan",
    "identity_substitutions",
    "identity_py_excluded_patterns",
    "identity_name_tokens",
    "identity_domain_tokens",
    "school_domains",
    "identity_filename_reason",
    "identity_rules_effective",
    # ── 内容级脱敏引擎（两条出口共用）────────────────────────────────────
    "STATIC_IDENTITY_SUBSTITUTIONS",
    "PII_SUBSTITUTIONS",
    "PII_RESIDUAL_PATTERNS",
    "SCHOOL_PLACEHOLDERS",
    "MAJOR_PLACEHOLDERS",
    "PY_STATIC_EXCLUDED_PATTERNS",
    "SANITIZED_SUFFIXES",
    "PY_UNSANITIZED_FILES",
    "LOCAL_WHITELIST_RE",
    "GENERIC_WEAKNESS_VALUES",
    "GENERIC_BASELINE_VALUES",
    "GENERIC_VALUE_SUBSTRINGS",
    "build_substitutions",
    "build_py_excluded_patterns",
    "build_py_substitutions",
    "sanitize_text",
    "sanitize_file_text",
    "verify_python_compiles",
    "scan_residual_identity",
]

# ── 用户私有目录（对应 .gitignore:75-108 的保护段）─────────────────────────
#: 这些目录存放学员真实学情：教材 PDF、做题记录、每日笔记、错题本、状态快照。
#: 目录本身可以发布（保留骨架让发布包开箱即用），但内容必须按白名单过滤。
PRIVATE_DIRS: Tuple[str, ...] = (
    "参考资料",
    "每日笔记",
    "每日作业",
    "错题本",
    "错题与长难句本",
    "作文语料库",
    "_状态",
)

#: 私有目录内**允许**发布的骨架文件（对应 .gitignore 的 `!` 反选条目）。
#: 这些模板是发布包「开箱即用」的前提，绝不能误删：
#:   - init_workspace.py 依赖 rglob("*.template.md") 把模板初始化为学员工作文件；
#:   - 05-考研看板 依赖 错题本/_索引.md 与 _状态/*核心速记*.md 抽取看板卡片；
#:   - verify_health.py 依赖 参考资料/README.md、作文语料库/ 等骨架存在。
SKELETON_WHITELIST = {
    "_状态": ("*.template.md", "*.example.md", "*核心速记*.md"),
    "每日笔记": ("_模板.md", "README.md"),
    "每日作业": ("_模板.md", "README.md"),
    "错题本": ("_模板.md", "_索引.md", "README.md"),
    "错题与长难句本": ("_模板.md", "_索引.md", "README.md"),
    "参考资料": ("README.md",),
    "作文语料库": ("通用作文语料.md",),
}

#: 不在私有目录内、但同样属于个人档案的文件（对应 .gitignore:80-82）。
#: 例：04-专业课/学情档案.md 是学员真实学情，仅 .template/.example 可发布。
PRIVATE_TOP_LEVEL_FILES: Tuple[str, ...] = ("学情档案.md",)

# ── 通用垃圾/临时产物 ────────────────────────────────────────────────────
JUNK_DIR_NAMES = frozenset({
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".idea", ".vscode", "logs", "htmlcov", ".tox", ".coverage",
    # [R2-C1 补漏] 开发期 scratch 目录：**任意深度**排除。
    # 仓库里实际存在 ``tools/scratch/uploads/clip_*.png``（CLI 粘贴图片的运行时落盘，
    # 见 tools/cli/repl/session.py），是学员真实屏幕内容；``.gitignore:118`` 的
    # ``scratch/`` 也明确忽略它。此前它只被列在「仅根层级」的 DEV_SCRATCH_DIRS 里，
    # 于是 ``tools/scratch/**`` 绕过过滤进了发布包 —— 实测旧 dist 里躺着 152 个
    # clip_*.png。这里改为与 __pycache__ 同级的任意深度排除。
    "scratch",
})
JUNK_FILE_EXTS: Tuple[str, ...] = (".pyc", ".pyo", ".log", ".tmp")

#: 个人运行时配置：可能含 API Key、真实院校、历史对话，一律不进发布物。
PRIVATE_CONFIG_FILES = frozenset({
    "ky_config.json", "ky_history.json", "ui_theme.json", "state_snapshot.json",
})

#: 根级构建产物/依赖目录：是「本机编译出来的东西」，不是源码。
#: 其中 dist/ 尤其危险 —— 它是上一轮打包的产物，内部可能残留被复制的
#: 参考资料 PDF，一旦被镜像进公开仓库就等于二次泄漏（P25 实测命中）。
BUILD_ARTIFACT_DIRS = frozenset({
    "dist", "build", "dist_wheels", "node_modules",
    ".venv", "venv", "rust_ext", "kaoyan_study_chain.egg-info",
    # [R2-E 补漏] Rust 扩展的 CARGO_TARGET_DIR（见 .gitignore:9）。
    # 口径漂移实锤：git 明确忽略它，发布副本却照拷 —— 全新空目录导出实测
    # ../kaoyan_chain_public 里躺着 .cargo_target/（432K 本机编译产物）。
    ".cargo_target",
})

#: 开发期脚手架/工具残留目录：AI 代理的临时工作区、爬取缓存、浏览器自动化日志等。
#: 这些目录是「本机干活时的副产物」，既不是产品源码，也可能夹带真实数据
#: （例如 .codex_full_check_04 下散落着多份 ky_config.json 测试副本）。
#: 与 BUILD_ARTIFACT_DIRS 同样**只在仓库根层级**整棵排除。
DEV_SCRATCH_DIRS = frozenset({
    ".agents", ".tmp_unidata", ".playwright-cli",
    ".codex_full_check_04", ".codebuddy", ".claude", ".cursor",
    "scratch", "logs", "htmlcov", ".ruff_cache", ".mypy_cache",
    ".pytest_cache", ".pytest_tmp", ".tox",
    ".tmp.driveupload", ".tmp.drivedownload",
    # [R2-E 补漏] 残缺 git 对象库的备份骨架（本机修 .git 时的残留）。
    # 既未被 .gitignore 覆盖，也不在旧的排除名单里，于是每次导出都被镜像进
    # 公开副本 —— 公开仓库里出现一个 .git 备份目录既无意义又容易误导。
    ".git_broken_backup",
    # [CRITICAL 补漏] 配置写前自动备份目录（见 tools/config_guard.py 的
    # ``auto_backup()``，由 tools/cli/shared.py 的 ``_guard_before_config_write``
    # 触发）。里面是 ky_config.json 的**明文完整快照**：51 字符 ``sk-`` 开头的
    # 真实 api_key 与全部顶层字段，实测本机 7 份 .json 中 6 份是 2622 字节的
    # 完整配置。此前它**只被 .gitignore:49 忽略**（git 层），而导出副本走的是
    # **文件系统遍历**，只认 dir_should_exclude / file_should_exclude ——
    # 于是密钥快照被原样镜像进公开副本。故必须进本清单（= 进
    # ROOT_ONLY_EXCLUDE_DIRS），由两条出口共用同一判据。
    ".config_backup",
})

#: 根级整棵排除的目录（构建产物 + 开发脚手架）。
ROOT_ONLY_EXCLUDE_DIRS = BUILD_ARTIFACT_DIRS | DEV_SCRATCH_DIRS

#: 与 `.gitignore` 对齐的「不入发布物」路径（相对仓库根的 parts 前缀元组）。
#: 这些路径在 **git 层已被忽略**，但发布副本走的是**文件系统遍历**，只认
#: ``should_publish()`` —— 两处口径不一致时，它们会随导出进公开仓库。
#: 逐条对应 .gitignore 的声明：
#:   - ``data/universities/_sources/``：公开数据源原始快照。其中两个上游仓库
#:     **未声明任何许可**，原样再分发属未授权汇编（.gitignore 明示「仅本机留存」）。
#:   - ``data/universities/exam_subjects.json``：可由上述快照复现的消费者视图。
#:   - ``data/knowledge/``：检索知识库（KnowledgeStore 首次使用/切片入库时
#:     自动生成的 sqlite 向量库）。它是**运行时产物**、可由「切片入库」重建，
#:     且一旦学员用 ``ky ingest`` 入库自己的真题资料，库内就会含资料正文。
#: 注意：``data/universities/registry.json`` 与 ``national_institutions.json``
#: 是**要发布**的公开派生库，**不得**加入本清单。
NON_PUBLISH_PATH_PREFIXES: Tuple[Tuple[str, ...], ...] = (
    ("data", "universities", "_sources"),
    ("data", "universities", "exam_subjects.json"),
    ("data", "knowledge"),
)

#: 只在**私有工作区**里才有意义的路径（相对仓库根的 parts 前缀元组）。
#:
#: 与 ``NON_PUBLISH_PATH_PREFIXES`` 的分工必须分清，否则会把两类东西混成一类：
#:   * ``NON_PUBLISH_PATH_PREFIXES`` —— **隐私**上不能发：文件里有真实数据，
#:     ``.gitignore`` 已经忽略它们，发布层只是补齐同口径（导出即泄漏）；
#:   * 本清单 —— **工程**上不该发：文件里**没有任何身份信息**，git 也正常跟踪它，
#:     但它依赖的东西在公开副本里被替换成了占位实现，留在副本里只会让
#:     ``pytest tests/`` 整批报错（公开仓库 CI 恒红）。
#:
#: 逐条理由：
#:   * ``tests/test_fix_publish_privacy.py`` —— 全部用例都在测
#:     ``tools/sync_publish.py`` 与 ``tools/build_package.py`` 的隐私闸门
#:     （``sp.SRC`` / ``sp.EXCLUDE_DIRS`` / 脱敏引擎 / ``sp.main()`` 的拒绝分支 …）。
#:     而这两个脚本在公开副本里是**刻意保留的 4 行占位文件**
#:     （见 ``sync_publish.neutralize_sync_script()``），于是副本里 51 个用例
#:     全部 ``AttributeError: module 'sync_publish' has no attribute 'SRC'``
#:     （2026-09-21 实测：公开副本 ``pytest tests/`` = 52 failed / 1097 passed，
#:     其中 51 个出自本文件）。私有工程内部事务的测试，公开副本既不需要也无法运行。
#:
#: 注意：这里**只列具体文件**，不得写成 ``("tests",)`` —— 其余测试文件在公开副本
#: 里是能跑通的，整目录排除会让公开仓库失去全部回归测试（有专门用例钉住这一点）。
PRIVATE_WORKSPACE_ONLY_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("tests", "test_fix_publish_privacy.py"),
)

#: 备份文件标记：任何带此标记的文件都是历史快照，绝不发布（含私有目录白名单内）。
BACKUP_MARK = "_backup_"

#: 内部文档（basename 通配符）：审查报告、路线图、多轮实测报告、原始需求书。
#:
#: **单一事实源** —— 两条出口（打包 build_package / 发布副本 sync_publish）都必须
#: 从这里取，不得再各自硬编码文件名清单。历史教训（本轮 P25 补漏）：sync_publish
#: 曾硬编码 4 个 ``D盘实测_*.md`` 文件名，而 ``.gitignore:161`` 声明的是**模式**
#: ``*审查报告*.md`` —— 两处口径必然漂移，于是新出现的
#: ``代码审查报告_全量.md`` / ``多角色端到端测试审查报告.md`` 双双漏进公开副本。
#:
#: 与 ``.gitignore`` 的对应关系（改这里必须同步核对 .gitignore，反之亦然）：
#:   * ``*审查报告*.md`` / ``*路线图*.md`` —— ``.gitignore:161-162`` 的
#:     「临时分析报告、竞品方案与搜索规划文件」段；
#:   * ``D盘实测_*.md`` —— 本机多轮端到端实测报告，同属内部文档且含真实身份；
#:   * ``ORIGINAL_REQUEST.md`` —— 用户给 AI 的原始需求书，是内部需求文档而非产品文档。
INTERNAL_DOC_PATTERNS: Tuple[str, ...] = (
    "*审查报告*.md",
    "*路线图*.md",
    "D盘实测_*.md",
    "ORIGINAL_REQUEST.md",
)


def is_internal_doc(name: str) -> bool:
    """basename 是否属于「内部文档」（审查报告/路线图/实测报告/原始需求书）。"""
    return any(fnmatch(name, pat) for pat in INTERNAL_DOC_PATTERNS)


def _parts(path: Union[str, Path]) -> Tuple[str, ...]:
    """把路径归一化为 parts 元组。

    注意：调用方可能直接传入已经切分好的 parts 序列（如 sync_publish 的
    ``rel_parts``），此时必须原样返回 —— 若盲目 ``Path(str(x))`` 会把元组
    的 repr 当成一个路径段，导致所有私有目录判定静默失效。
    """
    if isinstance(path, (tuple, list)):
        return tuple(str(p) for p in path)
    return Path(str(path)).parts


def private_owner(path: Union[str, Path]) -> Optional[str]:
    """若路径位于某个私有目录内，返回该私有目录名；否则返回 None。"""
    for part in _parts(path):
        if part in PRIVATE_DIRS:
            return part
    return None


def is_backup(name: str) -> bool:
    """是否为历史备份快照（如 今日任务_backup_2026-09-18_161554.md）。"""
    return BACKUP_MARK in name


def is_junk(name: str) -> bool:
    """是否为缓存、临时文件或开发期产物。"""
    if name in JUNK_DIR_NAMES:
        return True
    if name.endswith(JUNK_FILE_EXTS):
        return True
    return False


def should_publish(path: Union[str, Path]) -> bool:
    """判断某个相对路径是否可以进入发布物（分发包 / 公开仓库副本）。

    Args:
        path: 相对仓库根的路径（绝对路径亦可，按 parts 匹配）。

    Returns:
        True  可以发布；
        False 必须剔除（私有资料、备份、个人配置、缓存、构建产物）。
    """
    parts = _parts(path)
    if not parts:
        return True
    name = parts[-1]

    # 1. 通用垃圾与个人配置：任何位置都剔除
    if is_junk(name):
        return False
    # 任意深度的缓存/开发脚手架目录（__pycache__ / scratch / logs …）：
    # 与 sync_publish.dir_should_exclude() 同口径。此前只查 basename，于是
    # ``tools/scratch/uploads/*.png`` 这类**嵌套**脚手架目录整棵漏过，
    # 学员粘贴的屏幕截图随发布包一起发出去（旧 dist 实测 152 个）。
    if any(part in JUNK_DIR_NAMES for part in parts[:-1]):
        return False
    if name in PRIVATE_CONFIG_FILES:
        return False
    # 内部文档（审查报告/路线图/实测报告/原始需求书）：与 .gitignore 同口径，
    # 任何位置都不得进入发布物（见 INTERNAL_DOC_PATTERNS）。
    if is_internal_doc(name):
        return False

    # 2. 备份快照：优先级高于私有目录白名单
    #    （否则 `!**/*核心速记*.md` 会把 核心速记_..._backup_xxx.md 一起放行）
    if is_backup(name):
        return False

    # 3. 根级构建产物 / 依赖目录 / 开发脚手架：整棵子树剔除
    #    只在仓库根判断，避免误伤 docs/assets/vendor/katex/<ver>/dist/ 这类第三方包内部结构
    if parts[0] in ROOT_ONLY_EXCLUDE_DIRS:
        return False

    # 3.5 与 .gitignore 对齐的受限路径（原始快照 / 运行时向量库）：
    #     git 层已忽略，发布层必须同口径，否则导出即泄漏（见上方常量注释）。
    if any(parts[:len(pfx)] == pfx for pfx in NON_PUBLISH_PATH_PREFIXES):
        return False

    # 3.6 只在私有工作区里有意义的文件（测试「公开副本里的占位工具」的用例）：
    #     文件本身不含隐私，但公开副本里被测的实现已换成占位，留下只会让
    #     公开仓库 CI 恒红（见上方常量注释）。与 3.5 同构，但理由不同故分列。
    if any(parts[:len(pfx)] == pfx for pfx in PRIVATE_WORKSPACE_ONLY_PATHS):
        return False

    # 4. 私有目录：仅放行白名单骨架
    owner = private_owner(parts)
    if owner is not None:
        return any(fnmatch(name, pat) for pat in SKELETON_WHITELIST.get(owner, ()))

    # 5. 散落在私有目录之外的个人档案
    if name in PRIVATE_TOP_LEVEL_FILES:
        return False

    return True


def leaked_paths(root: Union[str, Path], paths: Iterable[Union[str, Path]]) -> list:
    """在给定路径集合中筛出「按策略不该发布」的部分，便于测试与审计。

    返回相对路径字符串列表（统一使用 `/` 分隔，跨平台可比对；保持输入顺序）。
    """
    root_path = Path(root)
    out = []
    for p in paths:
        pp = Path(str(p))
        try:
            rel = pp.relative_to(root_path)
        except ValueError:
            rel = pp
        if not should_publish(rel):
            out.append(rel.as_posix())
    return out


# ── 内容级脱敏：当前真实报考信息 ────────────────────────────────────────────
# 历史教训（多角色端到端复测 R2）：目录策略修好了，**内容**仍在裸奔。
# sync_publish 的替换表只硬编码了若干历史公开校名，学员一换院校，新的真实校名
# （如「某农业类院校」这类学员当前身份）、专业（专业代码 + 专业名）与自命题科目
# 代码（三位数字 + 科目名）就不在任何名单里 —— 而 sanitize_markdown_files()
# 会扫全部 *.md（含 AGENTS.md），等于毫无保护。
# 这里改为从 ky_config.json 的 study_plan 动态取值，换校不再失效。
#
# 注意：本模块自身会被发布到公开副本，注释里不得写入学员真实身份信息；
# 举例一律用中性占位（「某农业类院校」/「专业代码 + 专业名」）。
#
# 边界（R2 复测追加，务必遵守）：``data/universities/**`` 与一切 ``*.json``
# 属于 1800+ 所高校的**公开数据库**，不是个人身份，**绝不套用本模块的替换规则**；
# 无差别替换会直接把高校库改坏（校名→「目标院校」后 985/211 识别全失效）。
# 同理 tools/intelligence/registry.py 的「校名 → 院校代码」映射也不得脱敏。


def load_study_plan(root: Union[str, Path]) -> dict:
    """读取 ``<root>/ky_config.json`` 的 ``study_plan``；缺失或损坏时返回空字典。"""
    try:
        raw = (Path(root) / "ky_config.json").read_text(encoding="utf-8")
        cfg = json.loads(raw)
    except (OSError, ValueError):
        return {}
    plan = cfg.get("study_plan")
    return plan if isinstance(plan, dict) else {}


def _major_abbreviations(name: str) -> List[str]:
    """从专业名生成可能的**简称**（供「换序 / 缩写容忍」脱敏规则使用）。

    中文专业简称的常见构造是「首字 + 末两字」（如「马克思主义理论」→「马理论」）。
    这里只做这一条保守启发式：生成的简称规则**必须与专业代码共现**才会触发
    （见 ``identity_substitutions``），匹配不到就自然不生效，不会误伤正文。
    """
    out: List[str] = []
    if len(name) >= 3:
        abbr = name[0] + name[-2:]
        if abbr != name:
            out.append(abbr)
    return out


def _add_url_rule(rules: List[Tuple[str, str]], text: str, repl: str) -> None:
    """追加一条「URL 百分号编码形态」的替换规则。

    分享/检索链接里身份字面量会被百分号编码（如「某农业类院校」→ ``%E6%9F%90...``），
    纯中文规则匹配不到，等于在 URL 里原样公开。静态表早已对历史校名这么做过，
    这里把**当前身份**也补上，口径一致。

    注意：本模块自身会被发布到公开副本，故注释一律用中性占位，不写真实身份
    （见模块开头的边界说明）。
    """
    enc = quote(text, safe="")
    if enc and enc != text:
        rules.append((re.escape(enc), quote(repl, safe="")))


# ── 院校注册域名（2026-09-21 补漏）──────────────────────────────────────────
# 实测缺口：``build_substitutions()`` 原先只产出「中文校名 + 其 URL 百分号编码」，
# 于是生成物（对比研报 / 考纲摘要 / 看板）里的 **pinyin 域名**原样通过 ——
# ``https://gra.<校名拼音>.edu.cn`` 这类字符串既不含中文、也不是百分号编码，
# 中文规则匹配不到；而导出后自检 ``scan_residual_identity()`` 只扫中文 token，
# 对它同样无感，照样打印「非公开库路径下无真实身份残留」。
#
# 域名不必猜拼音：本地院校库里就有现成字段（``official_domain`` /
# ``graduate_domain`` / ``admission_domain`` / ``departments.*.college_domain``）。
# 取注册域（保留「公共后缀 + 1 段」）而非完整主机名，才能一次覆盖
# ``www.`` / ``gra.`` / ``yjsy.`` 等全部子域。

#: 多段公共后缀。仅用于把 ``gra.<校名拼音>.edu.cn`` 归约到 ``<校名拼音>.edu.cn``；
#: 不需要完整 PSL —— 本模块只处理「学员自己院校的域名」，候选集来自院校库，
#: 不面向任意公网域名。
_PUBLIC_SUFFIXES = frozenset({
    "edu.cn", "com.cn", "net.cn", "org.cn", "gov.cn", "ac.cn", "mil.cn",
    "co.uk", "ac.uk", "org.uk", "gov.uk",
    "edu.hk", "com.hk", "edu.tw", "com.tw", "edu.mo",
})

#: 域名占位符的二级标签：``<label>.<公共后缀>``（如 ``example.edu.cn``）。
#: 保留原公共后缀，替换后的链接在语法上仍是合法 URL，不会把 Markdown 链接写坏。
_DOMAIN_PLACEHOLDER_LABEL = "example"

#: 院校库文件（相对仓库根）。两者同构：``{key: {"name":…, "official_domain":…,
#: "graduate_domain":…, "admission_domain":…, "departments": {…: {"college_domain":…}}}}``。
#: ``registry.json`` 是精选库（键=院校代码），``national_institutions.json``
#: 是全国库（键=校名）。只读不写。
_SCHOOL_DB_FILES: Tuple[str, ...] = (
    "data/universities/registry.json",
    "data/universities/national_institutions.json",
)

#: 公共平台域名兜底黑名单：即便某个 ``*_domain`` 字段指向上游聚合站，也不得替换。
#: 这些站点是**所有人共用的公共设施**，把它们当身份替换既改坏链接、又让自检误报。
_PUBLIC_PLATFORM_DOMAINS = frozenset({
    "chsi.com.cn", "chsi.cn", "eol.cn", "gaokao.cn", "shanghairanking.cn",
})

#: 院校库解析结果缓存：``{(path, mtime_ns, size): {校名/别名: {注册域名}}}``。
#: 全国库 1.7 MB、解析约 17 ms，而 ``build_substitutions()`` 会被测试反复调用，
#: 不缓存会明显拖慢套件。上限 8 条，超了整体清空（键含 mtime，天然失效）。
_SCHOOL_DOMAIN_CACHE: dict = {}
_SCHOOL_DOMAIN_CACHE_MAX = 8


def _normalize_domain(raw: object) -> Optional[str]:
    """``https://Gra.Example.EDU.CN:443/x`` → ``gra.example.edu.cn``；非法输入返回 None。"""
    text = str(raw or "").strip().lower()
    if not text:
        return None
    text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text)          # 去 scheme
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    text = text.split("@")[-1].split(":", 1)[0]                  # 去 userinfo / 端口
    text = text.strip(".")
    if not text or "." not in text or ".." in text:
        return None
    if not re.fullmatch(r"[a-z0-9.-]+", text):
        return None
    return text


def _split_public_suffix(domain: str) -> Optional[Tuple[str, str]]:
    """``gra.example.edu.cn`` → ``("gra.example", "edu.cn")``；``example.com`` → ``("example", "com")``。"""
    labels = domain.split(".")
    if len(labels) < 2:
        return None
    if len(labels) >= 3 and ".".join(labels[-2:]) in _PUBLIC_SUFFIXES:
        return ".".join(labels[:-2]), ".".join(labels[-2:])
    return ".".join(labels[:-1]), labels[-1]


def _registrable_domain(domain: str) -> Optional[str]:
    """``gra.<校名拼音>.edu.cn`` → ``<校名拼音>.edu.cn``（公共后缀 + 注册主体一段）。"""
    split = _split_public_suffix(domain)
    if not split:
        return None
    label, suffix = split
    label = label.rsplit(".", 1)[-1]
    return f"{label}.{suffix}" if label else None


def _placeholder_domain(domain: str) -> Optional[str]:
    """注册域 → 中性占位域；已是占位域或无法归约时返回 None。

    返回 None 是**必要**的：否则会产出 ``example.edu.cn → example.edu.cn``
    这种自指空转规则 —— 与 ``identity_rules_effective()`` 防范的
    「规则看着生效、实际什么都没改」属同一族缺陷。
    """
    split = _split_public_suffix(domain)
    if not split:
        return None
    label, suffix = split
    if label == _DOMAIN_PLACEHOLDER_LABEL:
        return None
    return f"{_DOMAIN_PLACEHOLDER_LABEL}.{suffix}"


def _collect_domain_values(node: object, out: set) -> None:
    """递归收集 ``*_domain`` 字段的字符串值（含 ``departments`` 下的 ``college_domain``）。

    **刻意不匹配任意 ``*_url`` / ``*_web``**：院校库里的 ``chsi_url`` 指向研招网
    （``yz.chsi.com.cn``），那是全国公共平台、不是考生身份 —— 当成身份替换会改坏
    公开副本里的研招网链接，并让导出后自检把每个提到研招网的文件都误报成残留
    （2026-09-21 实测踩到）。院校自有域名在本库里一律用 ``*_domain`` 命名。
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and key.endswith("_domain"):
                out.add(value)
            else:
                _collect_domain_values(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_domain_values(item, out)


def _load_school_domains(path: Path) -> dict:
    """``{校名/别名: {注册域名}}``（按 path+mtime+size 缓存）。读不到时返回 ``{}``。"""
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = _SCHOOL_DOMAIN_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    mapping: dict = {}
    if isinstance(data, dict):
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            raw: set = set()
            _collect_domain_values(entry, raw)
            domains = set()
            for value in raw:
                normalized = _normalize_domain(value)
                if not normalized:
                    continue
                registrable = _registrable_domain(normalized)
                if registrable and registrable not in _PUBLIC_PLATFORM_DOMAINS:
                    domains.add(registrable)
            if not domains:
                continue
            names = {str(entry.get("name") or "").strip()}
            aliases = entry.get("aliases")
            if isinstance(aliases, list):
                names.update(str(alias).strip() for alias in aliases)
            for name in names:
                if name:
                    mapping.setdefault(name, set()).update(domains)
    if len(_SCHOOL_DOMAIN_CACHE) >= _SCHOOL_DOMAIN_CACHE_MAX:
        _SCHOOL_DOMAIN_CACHE.clear()
    _SCHOOL_DOMAIN_CACHE[key] = mapping
    return mapping


def school_domains(root: Union[str, Path], name: str) -> List[str]:
    """``name`` 对应院校在**本地院校库**里的全部注册域名（去重排序）。

    为什么不用「中文名转拼音」：需要额外依赖，且学校简称、多音字、历史更名都会
    猜错；院校库里本来就有权威字段，直接取用更可靠。

    取不到（库缺失 / 校名未收录 / 院校库未随包分发）时返回空列表 —— 这是
    **刻意的降级**：中文校名规则仍然生效，不因一个增强规则让整条导出链失败。
    """
    target = str(name or "").strip()
    if not target:
        return []
    domains: set = set()
    for rel in _SCHOOL_DB_FILES:
        domains.update(_load_school_domains(Path(root) / rel).get(target, ()))
    return sorted(domains)


def identity_domain_tokens(root: Union[str, Path]) -> List[str]:
    """当前真实报考身份对应院校的**注册域名**（供导出后自检使用）。

    为什么要单列一份：``identity_name_tokens()`` 只产出中文校名与「代码 + 名称」
    组合，而生成物里泄漏的往往是 pinyin 域名 —— 自检对这类完全无感。
    实测（2026-09-21）：``04-专业课/双校对标_*.md`` 正文留着
    ``gra.<校名拼音>.edu.cn``，自检仍打印「非公开库路径下无真实身份残留」。
    """
    plan = load_study_plan(root)
    tokens: List[str] = []
    for key in ("school", "backup_school"):
        tokens.extend(school_domains(root, str(plan.get(key) or "")))
    seen = set()
    out: List[str] = []
    for tok in tokens:
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


# ── 括号包裹的代码形态（2026-09-21 补漏）───────────────────────────────────
# 实测缺口：生成物（对比研报、考纲摘要、招生简章转录）里代码几乎总被括号包裹，
# 形如 ``(618)某自命题科目`` / ``（030500）某专业``，而原先的规则只认
# 「裸码 + 可选空白 + 名称」—— ``(618)`` 的右括号让 ``\s*`` 之后的名称匹配落空，
# 整条规则静默失效。科目组合码本身就是考生指纹，漏掉等于没脱敏。
_CODE_OPEN = r"[（(\[【]"
_CODE_CLOSE = r"[）)\]】]"


def _code_with_optional_wrap(code: str) -> str:
    """把裸代码正则包装成「可被括号 / 方括号包裹」的形态，并保留数字边界。

    只放宽**包裹符**，不放宽代码本身的判据 —— ``618元`` / 页码 ``618`` /
    ``2026-06-18`` 这些普通数字依然不会命中（数字边界 + 必须与名称相邻共现）。
    """
    return rf"(?:{_CODE_OPEN}\s*)?(?<!\d){code}(?!\d)(?:\s*{_CODE_CLOSE})?"


#: 学情自由文本字段的「通用值」闸门 —— 取值命中其一即**不生成**替换规则。
#:
#: **为什么必须有这道闸门**：同一批字段的默认取值往往是**公开的推荐值/学科通用词**，
#: 它们大量合法出现在公开文档正文里：
#:   * ``无`` / ``计算失误`` / ``概念漏洞`` / ``审题偏差`` / ``公式记错`` / ``书写丢分``
#:     是「错因五分类」的正文用词（AGENTS.md 严格把关风格一节整段在讲它们）；
#:   * ``不考数学`` 是本仓库对「本科目不考数学」的标准表述；
#:   * ``待摸底…`` 是尚未摸底时的模板值。
#: 无差别生成规则会把这些词在公开文档与模板里全局替换掉，**反而改坏内容** ——
#: 这是比漏脱敏更隐蔽的缺陷，故宁可少生成。
GENERIC_WEAKNESS_VALUES = frozenset({
    "无", "计算失误", "概念漏洞", "审题偏差", "公式记错", "书写丢分", "基础薄弱",
})
GENERIC_BASELINE_VALUES = frozenset({"不考数学"})

#: 只要**包含**其中任一子串即视为通用值（用于「前缀式」的模板取值）。
GENERIC_VALUE_SUBSTRINGS = frozenset({"待摸底", "暂未放置实体资料"})

#: 学情自由文本字段的科目前缀（与 ky_config.json 的 study_plan 键名一致）。
_SUBJECT_FIELD_PREFIXES = ("math", "eng", "pol", "pro")


def _personal_text_rules(plan: dict, root: Union[str, Path]) -> List[Tuple[str, str]]:
    """学情自由文本字段（薄弱点 / 摸底水平 / 白名单资料 / 备选院校）→ 占位符。

    **为什么需要**：这些字段不在 ``school`` / ``major`` / ``pro_name`` 里，
    此前**一条规则都没有** —— 实测对真实的英语薄弱点取值 ``sanitize_text()`` 原样返回，
    而 ``AGENTS.md`` 恰恰把这些值明文写着，于是随打包产物一起公开。
    实测打包产物 40 处身份字面量里，薄弱点占两类（英语 / 政治各一）。
    """
    rules: List[Tuple[str, str]] = []

    def _emit(value: object, repl: str, gate: "frozenset[str]") -> None:
        v = str(value or "").strip()
        if len(v) < 3 or v in gate or any(g in v for g in GENERIC_VALUE_SUBSTRINGS):
            return
        rules.append((re.escape(v), repl))

    for prefix in _SUBJECT_FIELD_PREFIXES:
        _emit(plan.get(f"{prefix}_weakness"), "待诊断薄弱点", GENERIC_WEAKNESS_VALUES)
        _emit(plan.get(f"{prefix}_baseline"), "摸底水平", GENERIC_BASELINE_VALUES)
        _emit(plan.get(f"{prefix}_books"), "白名单资料（已隐去）", frozenset())

    # 备选院校是**院校名**，与 school 同类：除字面替换外还要覆盖 URL 编码形态与
    # pinyin 域名（备选院校同样是选校轨迹，其官网域名不该随生成物公开）。
    backup = str(plan.get("backup_school") or "").strip()
    if len(backup) >= 3 and backup not in GENERIC_VALUE_SUBSTRINGS:
        rules.append((re.escape(backup), "备选院校"))
        _add_url_rule(rules, backup, "备选院校")
        for domain in school_domains(root, backup):
            placeholder = _placeholder_domain(domain)
            if placeholder:
                rules.append((re.escape(domain), placeholder))
    return rules


def identity_substitutions(root: Union[str, Path]) -> List[Tuple[str, str]]:
    """当前真实报考信息 → 占位符 的正则替换规则（供发布路径复用）。

    **只替换「代码 + 名称」这种整体组合**，绝不单独替换「马克思主义理论」
    「马克思主义基本原理」这类学科通用词 —— 它们在 03-思想政治理论 的正文里
    大量合法出现，全局替换会把公开副本的学习内容改坏。

    [P25 补漏] 原先的规则是**字面顺序敏感**的完整串匹配（专业代码 + 空格 + 专业名，
    / 科目代码 + 空格 + 科目名），一旦写成「简称 + 代码」「618/823」这类
    **换序 / 缩写 / 并写**形式就漏网 —— 而专业代码 + 自命题科目代码的组合本身就是
    考生的可识别指纹。现补上**顺序无关**规则，并给出区分「科目代码」与
    「普通数字」的明确判据：

      * 专业代码（6 位）：裸码即身份指纹，用数字边界 ``(?<!\\d)…(?!\\d)``
        整体替换；不会误伤普通数字，也不会截断更长的数字串；
      * 专业简称 + 专业代码：**二者必须相邻共现**（只允许空格/·/、/,/，分隔），
        任意顺序均替换 —— 单独的裸简称不动（是通用学科简称，非身份）；
      * 自命题科目代码：**必须两个代码同时出现**（618/823、618 823、618、823 …）
        才替换；单码仅在紧邻显式语境词（自命题/专业课/科目/大纲/考试）时才替换。
        → 因此 618元 / 页码 618 / 2026-06-18 / 单码 823 这类普通数字一律保持原样；
      * URL 百分号编码形态：分享/检索链接里的身份字面量与代码一并替换。
      * [2026-09-21 补漏] **括号包裹形态**：生成物里代码几乎总被括号包着
        （``(618)某自命题科目`` / ``（030500）某专业``），原规则因右括号让名称匹配
        落空而整条静默失效。现由 ``_code_with_optional_wrap()`` 统一放宽包裹符 ——
        只放宽包裹符，数字边界与「必须与名称相邻共现」的判据不变。
      * [2026-09-21 补漏] **院校 pinyin 域名**：中文规则与百分号编码规则都覆盖不到
        ``gra.<校名拼音>.edu.cn``，而它恰恰是生成物里最常泄漏的形态。现按
        ``school`` / ``backup_school`` 查本地院校库取注册域，替换为
        ``example.<原公共后缀>``（保留后缀，链接语法仍合法）。
    """
    plan = load_study_plan(root)
    rules: List[Tuple[str, str]] = []

    school = str(plan.get("school") or "").strip()
    if school:
        rules.append((re.escape(school), "目标院校"))
        _add_url_rule(rules, school, "目标院校")
        # pinyin 域名（``www.<校名拼音>.edu.cn`` / ``gra.<校名拼音>.edu.cn`` …）：
        # 中文规则与百分号编码规则都覆盖不到，必须单独按院校库取注册域替换。
        for domain in school_domains(root, school):
            placeholder = _placeholder_domain(domain)
            if placeholder:
                rules.append((re.escape(domain), placeholder))

    major = str(plan.get("major") or "").strip()
    if major:
        rules.append((re.escape(major), "目标专业 (专业代码)"))
        m = re.match(r"^\s*(\d{3,6})\s*(.+?)\s*$", major)
        if m:
            major_code, major_name = m.group(1), m.group(2)
            sep = r"[\s·、,，/]*"
            # 换序 / 缩写 / 括号包裹：专业名（或简称）与专业代码相邻共现 → 整体替换。
            wrapped_major = _code_with_optional_wrap(major_code)
            for alias in [major_name] + _major_abbreviations(major_name):
                rules.append((rf"{re.escape(alias)}{sep}{wrapped_major}",
                              "目标专业 (专业代码)"))
                rules.append((rf"{wrapped_major}{sep}{re.escape(alias)}",
                              "目标专业 (专业代码)"))
            # 裸专业代码：6 位码几乎不可能是普通数字，本身就是身份指纹。
            # 数字边界 lookaround 保证不截断更长的数字串、不误伤普通数字；
            # 额外兼容 URL 里以 ``%20`` / ``+`` 编码空格相邻的形态（链接中常见）——
            # 这几种规则的占位符必须**同样百分号编码**，否则会在 URL 里塞进裸中文与
            # 空格，产出一条不合法的链接（``quote`` 默认不编码 ``/``，此处用 safe=""）。
            rules.append((rf"(?<!\d){major_code}(?!\d)", "目标专业 (专业代码)"))
            enc_major = quote("目标专业 (专业代码)", safe="")
            for lb, la in ((r"(?<=%20)", r"(?!\d)"), (r"(?<!\d)", r"(?=%20)"),
                           (r"(?<=\+)", r"(?!\d)"), (r"(?<!\d)", r"(?=\+)")):
                rules.append((rf"{lb}{major_code}{la}", enc_major))
            _add_url_rule(rules, major_name, "目标专业")

    pro_name = str(plan.get("pro_name") or "").strip()
    if pro_name:
        rules.append((re.escape(pro_name), "自命题专业课科目"))
        # 形如 "618 某自命题科目名 823 另一自命题科目名"
        #   → ("618", "某自命题科目名") / ("823", "另一自命题科目名")
        pairs = re.findall(r"(\d{3})\s+([^\d]+?)(?=\s+\d{3}\s|$)", pro_name)
        for idx, (code, name) in enumerate(pairs, 1):
            name = name.strip()
            if name:
                # 括号包裹形态（``(618)某自命题科目``）与裸码形态都要覆盖。
                rules.append((rf"{_code_with_optional_wrap(code)}\s*{re.escape(name)}",
                              f"自命题科目{idx}"))
                _add_url_rule(rules, name, f"自命题科目{idx}")
        codes = [code for code, _ in pairs]
        # 并写形式（判据：两个代码必须同时出现）：
        #   618/823 · 618 823 · 618、823 · 618,823（含反序 823/618）
        # 代码本身允许括号包裹，故 ``(618)、(823)`` / ``（618）／（823）`` 同样命中。
        pair_sep = r"(\s*[/、,，]\s*|\s+)"
        # URL 编码形态（分隔符是 %20 / +）：占位符也必须编码，否则链接不合法
        pair_sep_url = r"(%20|\+)"
        for i in range(len(codes) - 1):
            c1, c2 = codes[i], codes[i + 1]
            w1 = _code_with_optional_wrap(c1)
            w2 = _code_with_optional_wrap(c2)
            rules.append((rf"{w1}{pair_sep}{w2}",
                          f"自命题科目{i + 1}\\1自命题科目{i + 2}"))
            rules.append((rf"{w2}{pair_sep}{w1}",
                          f"自命题科目{i + 2}\\1自命题科目{i + 1}"))
            e1 = quote(f"自命题科目{i + 1}", safe="")
            e2 = quote(f"自命题科目{i + 2}", safe="")
            rules.append((rf"{w1}{pair_sep_url}{w2}", f"{e1}\\1{e2}"))
            rules.append((rf"{w2}{pair_sep_url}{w1}", f"{e2}\\1{e1}"))
        # 单码兜底（判据：必须紧邻显式语境词），否则 618元 / 页码 618 会被误伤。
        for idx, code in enumerate(codes, 1):
            wrapped = _code_with_optional_wrap(code)
            rules.append((rf"((?:自命题|专业课)\s*){wrapped}",
                          rf"\1自命题科目{idx}"))
            rules.append((rf"{wrapped}(\s*(?:科目|大纲|考试))",
                          rf"自命题科目{idx}\1"))

    # 学情自由文本字段（薄弱点 / 摸底水平 / 白名单资料 / 备选院校）——
    # 它们不在 school/major/pro_name 里，此前一条规则都没有（见 _personal_text_rules）。
    rules.extend(_personal_text_rules(plan, root))
    return rules


def identity_py_excluded_patterns(root: Union[str, Path]) -> List[str]:
    """身份规则中**只应作用于 .md/.html/.svg、不得套用到 .py** 的那几条。

    目前仅「裸专业代码」及其 URL 编码相邻形态。为什么必须从 ``*.py`` 里排除：
    6 位专业代码是**全国统一学科门类代码**（公开事实）—— 例如
    ``tools/intelligence/chsi_connector.py`` 的 ``STANDARD_SUBJECTS_CATALOG``
    以 ``"030500"`` 为键名，十余个测试文件也把它当公开常量断言。无上下文地替换
    裸码会把公开学科目录改坏、并让发布副本里的测试断言集体失配（与
    ``PY_UNSANITIZED_FILES`` 豁免 ``registry.py`` 属同一类「公开索引不是身份」）。

    而**组合规则**（简称/专业名与代码相邻共现）**不在此列** —— 组合本身即身份，
    ``*.py`` 里出现同样应当替换。故这里只回传裸码与其 URL 相邻形态，
    由 ``sync_publish`` 并入 ``PY_EXCLUDED_PATTERNS``（沿用既有 .py 受限规则集机制）。
    """
    plan = load_study_plan(root)
    major = str(plan.get("major") or "").strip()
    m = re.match(r"^\s*(\d{3,6})\s*(.+?)\s*$", major)
    if not m:
        return []
    code = m.group(1)
    return [
        rf"(?<!\d){code}(?!\d)",
        rf"(?<=%20){code}(?!\d)",
        rf"(?<!\d){code}(?=%20)",
        rf"(?<=\+){code}(?!\d)",
        rf"(?<!\d){code}(?=\+)",
    ]


# ── 文件名级脱敏：真实身份文件名（R2-D5）────────────────────────────────────
# 历史教训（R2-D5 实锤）：目录级与内容级都修好之后，**文件名本身**仍在裸奔 ——
#   dist/<app>/_internal/04-专业课/
#     目标院校情报_<真实校名>_<专业代码 专业名>_backup_<时间戳>.md
# 名字里直接写着学员真实校名与专业。它既不在私有目录内、也不是个人配置，
# 于是「扫内容」「扫目录」两道闸门都看不见它。
#
# 这里把「文件名脱敏表」也收敛到本模块（原先散落在 sync_publish.py），
# 供两条出口共用；打包路径另按 identity_name_tokens() 做兜底剔除。

#: 个性化侦察报告的文件名改写规则（basename 正则 → 中性名）。
#: 发布副本走 rename（保留文件内容），打包路径走剔除（见 build_package._is_junk）。
RENAME_NAME_PATTERNS: List[Tuple["re.Pattern[str]", str]] = [
    (re.compile(r"^目标院校情报_.*\.md$"), "目标院校情报_目标院校_目标专业.md"),
    (re.compile(r"^双校考情对比_.*\.md$"), "双校考情对比_目标院校_VS_对比院校B_目标专业.md"),
    # 仓库里实际存在 双校对标_<校名A>_VS_<校名B>_<专业>.md 等含真实校名的文件，
    # 原先未纳入脱敏命名，校名会随文件名直接公开。
    (re.compile(r"^双校对标_.*\.md$"), "双校对标_目标院校_VS_对比院校B_目标专业.md"),
]


def identity_name_tokens(root: Union[str, Path]) -> List[str]:
    """当前真实报考身份中「一旦出现在文件名里即为泄漏」的字面量（去重保序）。

    只取「校名」「专业（代码 + 名称）」「自命题科目（代码 + 名称）」这类**整体组合**，
    与 ``identity_substitutions()`` 同口径 —— 绝不把「马克思主义理论」这种学科通用词
    当作身份标记，否则 03-思想政治理论 里合法的文件名会被大面积误判。
    """
    plan = load_study_plan(root)
    tokens: List[str] = []
    for key in ("school", "major", "pro_name"):
        value = str(plan.get(key) or "").strip()
        if value:
            tokens.append(value)

    # 专业代码本身即身份指纹（6 位码几乎不可能是普通数字）。单独取出，
    # 使「马理论 030500」「030500」这类换序/裸码写法也能被残留自检捕获；
    # 该 token 与 identity_substitutions() 的裸码规则同源（测试会校验）。
    major = str(plan.get("major") or "").strip()
    code_match = re.match(r"^\s*(\d{3,6})\s", major)
    if code_match:
        tokens.append(code_match.group(1))

    pro_name = str(plan.get("pro_name") or "").strip()
    for code, name in re.findall(r"(\d{3})\s+([^\d]+?)(?=\s+\d{3}\s|$)", pro_name):
        name = name.strip()
        if name:
            tokens.append(f"{code} {name}")

    seen = set()
    out: List[str] = []
    for tok in tokens:
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def identity_filename_reason(name: str, root: Union[str, Path]) -> Optional[str]:
    """文件名本身是否夹带当前真实报考身份；是则返回原因字符串，否则 None。"""
    for tok in identity_name_tokens(root):
        if tok in name:
            return f"真实身份文件名（含「{tok}」）"
    return None


def identity_rules_effective(root: Union[str, Path]) -> Tuple[bool, str]:
    """动态身份脱敏规则是否**真的能脱敏**；返回 ``(是否有效, 说明)``。

    **为什么需要这个判定（静默失效实锤）**

    ``identity_substitutions()`` 的规则是**运行时从 ky_config.json 生成**的，
    而 ky_config.json 不在版本控制内、会被各种脚本重写。一旦它缺失、``study_plan``
    被清空、或 school 被冲成占位值「目标院校」，动态规则要么为空、要么退化成
    「目标院校 → 目标院校」这种自指空转 —— 于是 ``sanitize_markdown_files()``
    照常跑、照常打印 ``[sanitize] …``、照常 exit 0，**却一个字都没脱敏**。

    实测证据（本机 ``../kaoyan_chain_public``，生成于 2026-09-19 20:35）：
      含**静态**规则词（天津工业大学/华南理工大学…）的文件 = 0 个 .md；
      含**动态**身份词（真实校名/专业）的 .md = 52 个。
      → 说明 .md 脱敏确实跑了（静态规则生效），只是动态规则整体缺失。
      导出物共 72 个文件带真实身份，全程无任何警告。

    判定口径（只认「能真正改变文本」的规则）：
      * ``study_plan`` 缺失/为空 → 无效；
      * ``school`` 缺失或为空 → 无效；
      * ``school`` 对应的规则替换后与原文相同（占位值）→ 无效。
    """
    plan = load_study_plan(root)
    if not plan:
        return False, "ky_config.json 缺失或 study_plan 未配置"
    school = str(plan.get("school") or "").strip()
    if not school:
        return False, "study_plan.school 为空"

    rules = identity_substitutions(root)
    if not rules:
        return False, "study_plan 未生成任何动态脱敏规则"

    school_rule = next((r for r in rules if r[0] == re.escape(school)), None)
    if school_rule is None:
        return False, "study_plan.school 未生成对应的脱敏规则"
    if re.sub(school_rule[0], school_rule[1], school) == school:
        return False, f"study_plan.school 仍是占位值「{school}」（替换后与原文相同，等于没脱敏）"
    return True, f"{len(rules)} 条动态规则（school={school}）"


# ═══════════════════════════════════════════════════════════════════════════
# 内容级脱敏引擎（**两条出口共用**）
# ═══════════════════════════════════════════════════════════════════════════
# 历史问题：引擎与静态替换表住在 tools/sync_publish.py 里，且该模块在 **import 时**
# 就执行 ``SUBSTITUTIONS = identity_substitutions(SRC) + SUBSTITUTIONS``
# （SRC = sync_publish 自己的仓库根）。于是打包路径 tools/build_package.py
# 若直接复用，规则会**被冻结成另一个仓库根**的配置 —— 测试里
# ``monkeypatch.setattr(bp, "ROOT", tmp)`` 注入的 ROOT 完全不生效，
# 「改完必须能被阴性验证」这条仓库铁律也就无从谈起。
#
# 故把引擎与规则表下沉到本模块，一律 **root 参数化**；sync_publish 只保留
# 「绑定 SRC 的薄别名」，对外符号与语义逐字不变。

#: 历史公开校名 / 日期 / 密钥形态的静态替换表。
#: 动态部分（学员当前真实身份）由 ``identity_substitutions(root)`` 另行生成，
#: 且在 ``build_substitutions()`` 里置于本表**之前** —— 万一某校既是历史对比院校、
#: 又是当前目标院校，以「当前目标」为准。
STATIC_IDENTITY_SUBSTITUTIONS: List[Tuple[str, str]] = [
    (r"天津工业大学", "目标院校"),
    (r"医学电子信息工程 \(085400-01\)", "目标专业 (专业代码-方向)"),
    (r"医学电子信息工程", "目标专业"),
    (r"华南理工大学", "目标院校"),
    (r"中山大学", "对比院校B"),
    (r"华中科技大学", "对比院校B"),
    (r"武汉大学", "对比院校B"),
    (r"天津大学", "对比院校B"),
    (r"长沙理工大学", "对比院校B"),
    # 仓库里实际存在《双校对标_<真实校名>_VS_<对比校名>_<专业>.md》，
    # 对比院校校名同样属于「学员真实选校轨迹」，随文件名/正文一起公开。
    (r"湖南农业大学", "对比院校B"),
    (r"人工智能 \(085400\)", "目标专业 (专业代码-方向)"),
    (r"华工", "目标院校简称"),
    (r"天工大", "目标院校简称"),
    # [G2 修复] 这里原先有一条 ``(r"2026-12-19", "2027-12-26")`` —— **已删除**。
    # 理由：初试日期不是隐私（全国统一考试日期是公开信息），把它当身份改写会
    # 制造自相矛盾：公开副本的 AGENTS.md 被改成 2027-12-26，而 ``ky status``
    # 的「初试首日」是**实时算**出来的 2026-12-19 —— 同一屏出现两个初试日期。
    # 而且口径本就不一致：.py 侧一直在 PY_STATIC_EXCLUDED_PATTERNS 里豁免它，
    # .md 侧却没有。正确做法是让日期在各处保持同一真源（exam_calendar / 配置），
    # 而不是在脱敏层改写它。
    (r"https://www\.fhl\.mom", "https://your-api-endpoint.example.com"),
    # 密钥脱敏用通用正则：任何形态的 API Key 都不允许进入公开副本 / 发布包。
    (r"sk-[A-Za-z0-9]{20,}", "YOUR_API_KEY_HERE"),
    (r"gpt-5\.4-mini", "gpt-4o-mini"),
    # 联系方式（QQ / 邮箱）按学员要求随仓库公开，不做脱敏映射。
    # 下列是 URL 百分号编码形态，让内嵌的分享/检索链接同样被匿名化。
    (r"%E5%A4%A9%E6%B4%A5%E5%B7%A5%E4%B8%9A%E5%A4%A7%E5%AD%A6", "%E7%9B%AE%E6%A0%87%E9%99%A2%E6%A0%A1"),
    (r"%E5%8D%8E%E5%8D%97%E7%90%86%E5%B7%A5%E5%A4%A7%E5%AD%A6", "%E7%9B%AE%E6%A0%87%E9%99%A2%E6%A0%A1"),
    (r"%E4%B8%AD%E5%B1%B1%E5%A4%A7%E5%AD%A6", "%E5%AF%B9%E6%AF%94%E9%99%A2%E6%A0%A1B"),
    (r"%E5%8D%8E%E4%B8%AD%E7%A7%91%E6%8A%80%E5%A4%A7%E5%AD%A6", "%E5%AF%B9%E6%AF%94%E9%99%A2%E6%A0%A1B"),
    (r"%E6%AD%A6%E6%B1%89%E5%A4%A7%E5%AD%A6", "%E5%AF%B9%E6%AF%94%E9%99%A2%E6%A0%A1B"),
    (r"%E5%A4%A9%E6%B4%A5%E5%A4%A7%E5%AD%A6", "%E5%AF%B9%E6%AF%94%E9%99%A2%E6%A0%A1B"),
    (r"%E9%95%BF%E6%B2%99%E7%90%86%E5%B7%A5%E5%A4%A7%E5%AD%A6", "%E5%AF%B9%E6%AF%94%E9%99%A2%E6%A0%A1B"),
    (r"%E6%B9%96%E5%8D%97%E5%86%9C%E4%B8%9A%E5%A4%A7%E5%AD%A6", "%E5%AF%B9%E6%AF%94%E9%99%A2%E6%A0%A1B"),
    (r"%E5%8C%BB%E5%AD%A6%E7%94%B5%E5%AD%90%E4%BF%A1%E6%81%AF%E5%B7%A5%E7%A8%8B", "%E7%9B%AE%E6%A0%87%E4%B8%93%E4%B8%9A"),
]

#: **通用 PII 形态**（与「报考身份」无关的个人隐私）：手机号 / 身份证号 /
#: 带显式标签的准考证号。此前规则表里**一条都没有** —— 实测 ``sanitize_text``
#: 对含手机号与身份证号的样本文本原样返回，等于公开副本会原样带着它们出门。
#:
#: 设计原则：**宁少勿滥**。数字类规则极易误伤仓库里大量合法的既有数字
#: （专业代码 ``030500``、初试日期 ``2026-12-19``、自命题科目码 ``618``/``823``、
#: 倒计时天数、页码…），所以：
#:   * 手机号 / 身份证：靠**长度 + 结构 + 数字边界**锁定，不需要语境；
#:   * 准考证号：**必须**紧邻显式标签（准考证号/考生编号/报名号），裸数字一律不动。
PII_SUBSTITUTIONS: List[Tuple[str, str]] = [
    # 手机号：11 位、1 开头、第二位 3-9；数字边界保证不截断更长的数字串。
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号]"),
    # 身份证：18 位，且生日段必须落在 19xx/20xx + 合法月日，末位可为 X/x。
    # 结构约束使「18 位随机数字」不会被误判。
    (r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
     r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)", "[身份证号]"),
    # 准考证号 / 考生编号 / 报名号：**必须有标签**（可带 : ：= 与空格），
    # 9~16 位数字。无标签的裸数字绝不替换 —— 否则 030500 / 618 / 2026-12-19
    # 这些既有数字会被成片改坏（比漏脱敏更隐蔽的缺陷）。
    (r"((?:准考证号|考生编号|报名号)\s*[:：=]?\s*)\d{9,16}(?!\d)", r"\1[准考证号]"),
]

#: PII 的**残留自检**用正则（与 PII_SUBSTITUTIONS 同源）。
PII_RESIDUAL_PATTERNS: Tuple["re.Pattern[str]", ...] = tuple(
    re.compile(p) for p, _ in PII_SUBSTITUTIONS
)

#: 身份脱敏后写回配置的**中性占位符**（= 规则表的产物 + 向导/模板的默认值）。
#:
#: **单一事实源**：任何「配置里出现这些值 ⇒ 视作尚未配置」的判据都必须从这里取，
#: 不得在别处再硬编码一份。历史教训（本轮 P10）：``gui/services/settings.py`` 的
#: ``is_unconfigured()`` 自己硬编码了一份 ``("报考专业", "目标专业 (专业代码-方向)")``，
#: 而规则表实际还会产出 ``"目标专业 (专业代码)"``（``identity_substitutions`` 的
#: major 规则）与 ``"目标专业"``（``_add_url_rule`` 与静态表）—— 两处必然漂移，
#: 于是「被脱敏成占位符的专业」会被误判成「已配置」。
SCHOOL_PLACEHOLDERS: Tuple[str, ...] = (
    "目标院校",
    "未指定",
)

#: 专业占位符：``报考专业`` 是向导/交互默认值，其余是规则表的产物。
MAJOR_PLACEHOLDERS: Tuple[str, ...] = (
    "报考专业",
    "目标专业",
    "目标专业 (专业代码)",
    "目标专业 (专业代码-方向)",
    "未指定",
)

#: 静态替换表里**只对 Markdown / HTML / SVG 生效、绝不套用到 ``*.py``** 的规则。
#: 逐条理由（依据是「全仓 *.py 实测命中 + 发布副本逐文件 diff」）：
#:
#: ① 日期 ``2026-12-19 → 2027-12-26``：**该规则已删除（G2 修复）**，此处不再列
#:    豁免项 —— 日期不是身份，脱敏层不应改写它；.py 与 .md 口径因此天然一致。
#:    保留这条注释是为了说明历史理由：.py 里日期是**功能默认值/日历数据**
#:    （exam_calendar / study_planner / gui settings 以及 9 个测试）。
#: ② 模型名 ``gpt-5.4-mini → gpt-4o-mini``：doctor.py 里是上游模型标识，
#:    替换会让「上游下线检测」的注释与逻辑对不上。
#: ③ 私有接口地址：全仓 *.py 实测 0 命中，只出现在 md/配置语境。
#: ④ 全部 ``%E5...`` URL 编码规则：只服务于 md/html 里的分享链接。
#: ⑤ 历史目标/对比院校的**公开校名**及简称：这些在 .py 里是**功能数据** ——
#:    实测把 school_scout.py 的 ``top_985 = [..., "天津大学", ...]`` 替换成
#:    「对比院校B」后，985/211 识别直接失效。.py 脱敏只处理「当前真实报考身份」
#:    （由 ``identity_substitutions()`` 动态提供），那才是无法从公开数据推断的信息。
PY_STATIC_EXCLUDED_PATTERNS = frozenset({
    r"gpt-5\.4-mini",
    r"https://www\.fhl\.mom",
    r"%E5%A4%A9%E6%B4%A5%E5%B7%A5%E4%B8%9A%E5%A4%A7%E5%AD%A6",
    r"%E5%8D%8E%E5%8D%97%E7%90%86%E5%B7%A5%E5%A4%A7%E5%AD%A6",
    r"%E4%B8%AD%E5%B1%B1%E5%A4%A7%E5%AD%A6",
    r"%E5%8D%8E%E4%B8%AD%E7%A7%91%E6%8A%80%E5%A4%A7%E5%AD%A6",
    r"%E6%AD%A6%E6%B1%89%E5%A4%A7%E5%AD%A6",
    r"%E5%A4%A9%E6%B4%A5%E5%A4%A7%E5%AD%A6",
    r"%E9%95%BF%E6%B2%99%E7%90%86%E5%B7%A5%E5%A4%A7%E5%AD%A6",
    r"%E6%B9%96%E5%8D%97%E5%86%9C%E4%B8%9A%E5%A4%A7%E5%AD%A6",
    r"%E5%8C%BB%E5%AD%A6%E7%94%B5%E5%AD%90%E4%BF%A1%E6%81%AF%E5%B7%A5%E7%A8%8B",
    r"天津工业大学",
    r"医学电子信息工程 \(085400-01\)",
    r"医学电子信息工程",
    r"华南理工大学",
    r"中山大学",
    r"华中科技大学",
    r"武汉大学",
    r"天津大学",
    r"长沙理工大学",
    # [P6 补漏] 静态表里有 ``湖南农业大学 → 对比院校B``，但本豁免清单漏了它 ——
    # 于是同一类「历史对比院校的公开校名」在 .py 里被替换、在 .md 里不被替换，
    # 口径自相矛盾；实测后果是 ``tests/`` 里把 湖南农业大学 当普通字符串用的
    # 用例在发布副本中被改写（断言里的字面量一起变成「对比院校B」）。
    r"湖南农业大学",
    r"人工智能 \(085400\)",
    r"华工",
    r"天工大",
})

#: 会被内容级脱敏改写的文件后缀（**单一事实源**）。
#: 这里**绝不允许**出现 ``*.json``：``data/universities/**`` 与
#: ``tools/intelligence/registry.py`` 是 1800+ 所高校的公开数据库/映射，不是个人
#: 身份，脱敏会把「校名 → 院校代码」改坏（985/211 识别、地区归类全线失效）。
SANITIZED_SUFFIXES: Tuple[str, ...] = ("*.md", "*.html", "*.svg", "*.py")

#: **不得被内容级脱敏改写**的 ``*.py``。两类，理由不同但都不可动：
#:
#: 1. ``tools/intelligence/registry.py`` —— 公开数据表型：内容本身就是
#:    「校名 → 院校代码/官网」的公开索引。实测发布副本里它的
#:    ``"<真实校名>": ("10466", "河南郑州", ...)`` 被替换成
#:    ``"目标院校": ("10466", ...)``，等于把 KNOWN_REGIONAL 兜底索引整条改坏
#:    （校名解析失配，且凭空多出一个叫「目标院校」的学校）。
#: 2. ``tools/privacy_policy.py`` —— **本文件就是规则表本身**。拿自己的规则去
#:    改自己会**自毁**：实测真实构建后产物里的
#:    ``(r"<历史校名>", "对比院校B")`` 被自己的规则改写成
#:    ``(r"对比院校B", "对比院校B")`` —— 规则失效，且这是不可逆的静默降级。
#:    该文件随后又被 ``scan_residual_identity`` 当作豁免跳过（它必须保留
#:    那些历史校名才能工作），故两个清单共用这一份定义。
PY_UNSANITIZED_FILES: frozenset = frozenset({
    "tools/intelligence/registry.py",
    "tools/privacy_policy.py",
})

#: 行级敏感规则（只对 md/html 生效：.py 里命中会破坏代码行结构）。
LOCAL_WHITELIST_RE = re.compile(r"\[本地资料库已就绪\]:.*")
LOCAL_WHITELIST_REPL = "[本地资料库已就绪]: 请放入本地参考资料后填写白名单书目"


def build_substitutions(root: Union[str, Path]) -> List[Tuple[str, str]]:
    """``root`` 对应的全量替换表：**动态当前身份在前，静态历史公开校名在后**，
    末尾追加与身份无关的**通用 PII** 规则（手机号/身份证/准考证号）。"""
    return (identity_substitutions(root)
            + STATIC_IDENTITY_SUBSTITUTIONS
            + PII_SUBSTITUTIONS)


def build_py_excluded_patterns(root: Union[str, Path]) -> frozenset:
    """``*.py`` 不适用的规则集（静态 .py 禁忌 + 裸专业代码及其 URL 相邻形态）。"""
    return PY_STATIC_EXCLUDED_PATTERNS | frozenset(identity_py_excluded_patterns(root))


def build_py_substitutions(root: Union[str, Path]) -> List[Tuple[str, str]]:
    """应用于 ``*.py`` 的替换表（全量表去掉 .py 禁忌项）。"""
    excluded = build_py_excluded_patterns(root)
    return [(p, r) for p, r in build_substitutions(root) if p not in excluded]


def sanitize_text(text: str, patterns: Iterable[Tuple[str, str]], *,
                  line_rules: bool = False) -> str:
    """按 ``patterns`` 脱敏文本。

    Args:
        patterns: 替换规则；md/html/svg 传 ``build_substitutions(root)``，
                  ``*.py`` 传 ``build_py_substitutions(root)``。
        line_rules: 是否额外套用行级规则（**只对 md/html 传 True**；
                  对 ``*.py`` 启用会把代码行结构改坏）。
    """
    for pat, repl in patterns:
        text = re.sub(pat, repl, text)
    if line_rules:
        text = LOCAL_WHITELIST_RE.sub(LOCAL_WHITELIST_REPL, text)
    return text


def sanitize_file_text(path: Union[str, Path], patterns: Iterable[Tuple[str, str]], *,
                       line_rules: bool = False) -> Optional[str]:
    """读取 ``path`` 并脱敏；**无法解码或内容无变化时返回 None**（调用方据此刻盘）。

    本函数刻意**不写盘** —— 让本模块保持零副作用依赖（写盘用各出口自己的原子写）。
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None
    new = sanitize_text(text, patterns, line_rules=line_rules)
    return None if new == text else new


def verify_python_compiles(root_dir: Union[str, Path], paths: Optional[Iterable[Path]] = None) -> None:
    """安全闸门：脱敏后的 ``*.py`` 必须仍能编译通过，否则抛错中止。

    对源码做正则替换天然有「改坏语法」的风险（例如规则吃掉引号或换行）。
    采用内存内 ``compile()`` 而非 ``compileall``：语义等价（同样只报语法错误），
    但不会往产物里写 ``__pycache__`` 垃圾。

    Args:
        paths: 只校验这批文件（打包路径只在脱敏改写过文件时传，避免编译几千个
               第三方 ``.py``）；None 表示遍历 ``root_dir`` 下全部 ``*.py``。
    """
    root = Path(root_dir)
    targets = [Path(p) for p in paths] if paths is not None else list(root.rglob("*.py"))
    broken: List[str] = []
    for f in targets:
        if f.suffix != ".py":
            continue
        try:
            source = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        try:
            compile(source, str(f), "exec")
        except SyntaxError as e:
            try:
                rel = f.relative_to(root).as_posix()
            except ValueError:
                rel = str(f)
            broken.append(f"{rel}:{e.lineno}: {e.msg}")

    if broken:
        shown = "\n".join(f"    - {x}" for x in broken[:20])
        more = f"\n    ... 其余 {len(broken) - 20} 项省略" if len(broken) > 20 else ""
        raise RuntimeError(
            f"[安全闸门] 脱敏后产物中有 {len(broken)} 个 .py 语法错误，已中止：\n{shown}{more}"
        )
    print(f"[verify] 脱敏后 .py 语法校验通过（{len(targets)} 个文件）")


def scan_residual_identity(dst: Union[str, Path], src_root: Union[str, Path], *,
                           strip_prefixes: Sequence[str] = (),
                           include_pii: bool = False) -> List[str]:
    """列出 ``dst`` 里**仍含当前真实身份字面量**的文件（相对路径，``/`` 分隔）。

    Args:
        strip_prefixes: 判定豁免时**先剥掉**的路径前缀。PyInstaller 产物把同一份内容
            同时放在产物根与 ``_internal/`` 下，而 ``data/`` 与 ``PY_UNSANITIZED_FILES``
            这两条豁免是按**仓库相对路径**写的 —— 不剥前缀就会把
            ``_internal/data/universities/*.json``（1800+ 所高校公开库）与
            ``_internal/tools/intelligence/registry.py``（公开校名→代码映射）
            当成残留误报，构建会被自己的自检挡下。发布副本路径传空即可（根即仓库相对）。
        include_pii: 是否同时把**通用 PII 形态**（手机号 / 身份证 / 带标签的准考证号，
            见 ``PII_RESIDUAL_PATTERNS``）计入残留。默认 False ——
            PyInstaller 产物树里有 ``_internal/PySide6/**`` 等第三方源码，
            拿数字正则去扫它们容易误报；发布副本没有第三方树，由
            ``sync_publish`` 显式传 True。

    光有脱敏规则不够 —— 规则可能为空/自指（见 ``identity_rules_effective``），
    脱敏函数照样会打印一堆 ``[sanitize] …`` 而实际什么都没改。这一步直接拿**产物**
    说话，把「还有多少文件带着真实身份」变成肉眼可见的数字。

    豁免范围（与既有隐私边界一致，避免误报）：
      * ``data/**``：1800+ 所高校的公开数据库，校名是功能数据；
      * ``PY_UNSANITIZED_FILES``：公开高校映射表（如 registry.py）。

    扫描口径 = ``identity_name_tokens()``（中文校名 / 「代码 + 名称」组合）
    **并集** ``identity_domain_tokens()``（院校 pinyin 注册域名）。后者是
    2026-09-21 补的：只有中文 token 时，正文留着 ``gra.<校名拼音>.edu.cn``
    的文件会被判为「干净」—— 自检与规则同时失明。
    """
    dst = Path(dst)
    tokens = [t for t in identity_name_tokens(src_root) if t]
    tokens += [t for t in identity_domain_tokens(src_root) if t]
    if not tokens and not include_pii:
        return []
    # 纯数字 token（如专业代码）用**数字边界**匹配，而不是朴素子串：否则
    # ``030500`` 会被 ``1030500`` 这类无关长数字误报。
    # 另外：裸专业代码在 ``*.py`` 里是**公开学科门类代码**（如
    # chsi_connector.STANDARD_SUBJECTS_CATALOG 的键名），并非身份，故扫描 ``*.py``
    # 时剔除纯数字 token —— 与 ``build_py_excluded_patterns`` 的 .py 边界一致。
    def _matchers(drop_digit_only: bool):
        out = []
        for tok in tokens:
            if tok.isdigit():
                if drop_digit_only:
                    continue
                out.append(re.compile(rf"(?<!\d){re.escape(tok)}(?!\d)"))
            else:
                out.append(re.compile(re.escape(tok)))
        return out

    def _logical(posix: str) -> str:
        """剥掉产物形态的前缀，得到「仓库相对路径」用于豁免判定。"""
        for pre in strip_prefixes:
            if posix.startswith(pre):
                return posix[len(pre):]
        return posix

    md_matchers = _matchers(drop_digit_only=False)
    py_matchers = _matchers(drop_digit_only=True)
    hits: List[str] = []
    for f in sorted(dst.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(dst)
        posix = rel.as_posix()
        logical = _logical(posix)
        if logical.split("/", 1)[0] == "data":
            continue
        if logical in PY_UNSANITIZED_FILES:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        matchers = py_matchers if f.suffix == ".py" else md_matchers
        if any(m.search(text) for m in matchers):
            hits.append(posix)
            continue
        if include_pii and any(p.search(text) for p in PII_RESIDUAL_PATTERNS):
            hits.append(posix)
    return hits
