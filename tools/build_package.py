#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan Study Chain) · 独立打包与发行分发构建工具
============================================================
功能特性：
1. 自动化 PyInstaller 单目录 (onedir) / 单文件 (onefile) 打包；
2. 动态提取项目单一权威版本号 (pyproject.toml / tools/version.py)；
3. 自动打包项目所有必要核心资源 (01-05 科目骨架, data 高校库, docs 模板与 Logo 等)；
4. 后置骨架部署 (deploy_workspace_skeleton)，保证发布包独立免装、开箱即可学习；
5. 自动生成适配 Inno Setup 5/6 的纯正简体中文安装向导脚本 (installer.iss)；
6. 提供安全 dry-run 预演模式：校验入口与资源清单、预览打包命令（不检查
   PyInstaller/PySide6 等构建依赖，真正的依赖检查在完整构建时执行）；
7. **内容级身份脱敏**（默认开启）：按 privacy_policy 的规则改写产物里
   *.md/*.html/*.svg/*.py 的真实校名/专业/自命题科目/薄弱点，脱敏后校验 *.py
   仍可编译，并做产物残留自检（检出即中止）。给自己打出「带个人方案」的包时用
   `--keep-identity` 跳过。
"""

import fnmatch
import os
import sys
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


def get_app_version() -> str:
    """动态获取项目版本号，优先从 tools/version.py 或 pyproject.toml 读取，兜底 2.8.0"""
    try:
        from version import get_version
        v = get_version()
        if v and not str(v).startswith("0.0.0"):
            return str(v).strip()
    except Exception:
        pass

    try:
        from tools.version import get_version
        v = get_version()
        if v and not str(v).startswith("0.0.0"):
            return str(v).strip()
    except Exception:
        pass

    pyproject = ROOT / "pyproject.toml"
    if pyproject.exists():
        import re
        try:
            txt = pyproject.read_text(encoding="utf-8")
            m = re.search(r'^\s*version\s*=\s*["\']([^"\']+)["\']', txt, re.MULTILINE)
            if m:
                return m.group(1).strip()
        except Exception:
            pass

    return "2.8.0"


def check_prerequisites() -> bool:
    """检查构建前置环境与依赖包"""
    print("[*] 正在检查构建环境前置依赖...")
    missing = []
    try:
        import PySide6
        print(f"  [√] PySide6: {PySide6.__version__}")
    except ImportError:
        missing.append("PySide6")

    pyinstaller_ok = shutil.which("pyinstaller") is not None
    if not pyinstaller_ok:
        try:
            import PyInstaller
            pyinstaller_ok = True
            print(f"  [√] PyInstaller 模块可用: {PyInstaller.__version__}")
        except ImportError:
            missing.append("pyinstaller")
    else:
        try:
            import PyInstaller
            print(f"  [√] PyInstaller: {PyInstaller.__version__} (CLI: {shutil.which('pyinstaller')})")
        except ImportError:
            print(f"  [√] PyInstaller CLI 可用: {shutil.which('pyinstaller')}")

    for dep in ["fsrs", "filelock", "yaml", "sympy", "pypdf", "certifi"]:
        try:
            __import__(dep)
            print(f"  [√] {dep} 扩展库就绪")
        except ImportError:
            print(f"  [!] 提示: 可选库 {dep} 建议安装以保障全功能")

    if missing:
        print(f"\n[!] 缺少核心构建依赖: {', '.join(missing)}")
        print(f"    请运行安装: py -3 -m pip install {' '.join(missing)}")
        return False
    return True


#: 需要随冻结程序分发的资源目录（源目录名 → 包内目标路径）。
#: 注意：科目目录 01-05 的**运行时数据**由 deploy_workspace_skeleton() 部署在
#: exe 同级目录；这里放进 _MEIPASS 的只是「冻结程序内部要用到的静态资源」。
RESOURCE_DIRS: Tuple[Tuple[str, str], ...] = (
    ("data", "data"),
    ("docs", "docs"),
    ("01-数学", "01-数学"),
    ("02-英语", "02-英语"),
    ("03-思想政治理论", "03-思想政治理论"),
    ("04-专业课", "04-专业课"),
    ("05-考研看板", "05-考研看板"),
    ("tools", "tools"),
)


def collect_data_specs(staging_root: Path) -> List[str]:
    """生成 PyInstaller ``--add-data`` 参数列表（**先按隐私策略过滤到 staging**）。

    [R2-C1 修复] 旧实现把 ``01-数学``~``05-考研看板`` 整棵目录直接交给
    ``--add-data``，而 PyInstaller 会把这些内容原样复制到
    ``dist/<app>/_internal/<目录>/``（冻结后的 ``sys._MEIPASS``）。这条路径
    **完全绕过了 deploy_workspace_skeleton() 里的隐私过滤与白名单**，于是
    「参考资料/教材 PDF、错题本、_状态/真实学情、*_backup_*」全都照样进包，
    而产物断言只扫顶层目录，什么也发现不了。

    现在改为：先按 ``privacy_policy.should_publish(相对仓库根的路径)`` 过滤，
    把允许发布的文件复制到 ``staging_root``，再以 staging 为源交给 PyInstaller。
    这样私有目录里只会剩骨架白名单（README.md / *.template.md / _索引.md …），
    与顶层 deploy_workspace_skeleton() 的口径完全一致。

    [R2-D5 修复] 只套「通用隐私策略」还不够 —— 打包路径特有的排除规则
    （``测试`` / ``双校考情对比_`` / ``双校对标_`` / ``目标院校情报_``）此前只作用在
    顶层 deploy_workspace_skeleton()，staging 这一层没有，于是
    ``目标院校情报_<真实校名>_<专业代码 专业名>.md`` 照样进 ``_internal/``。
    现在 staging 同时套**两层**：通用 ``should_publish()`` + 打包特有 ``_is_junk()``，
    并对科目目录额外按「真实身份文件名」剔除（``data/`` 除外，见
    IDENTITY_NAME_SCOPED_DIRS 的边界说明）。

    冻结程序实际从 _MEIPASS 读取的只有 ``data/``（registry.py）、
    ``tools/theme/templates/``（compile_qt.py）等静态资源；科目目录的运行时数据
    走 exe 同级目录，因此过滤私有内容不会导致「冻结后找不到文件」。
    """
    datas = []
    sep = ";" if sys.platform == "win32" else ":"

    for src, dst in RESOURCE_DIRS:
        src_dir = ROOT / src
        if not src_dir.is_dir():
            continue
        check_identity = _identity_name_applies((src,))
        out_dir = staging_root / dst
        out_dir.mkdir(parents=True, exist_ok=True)
        kept = skipped = 0
        for p in src_dir.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT).as_posix()
            if (not _should_publish(rel)
                    or _is_junk(p.name)
                    or (check_identity and _is_identity_name(p.name))):
                skipped += 1
                continue
            target = out_dir / p.relative_to(src_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            kept += 1
        datas.append(f"{out_dir}{sep}{dst}")
        print(f"  [stage] {src}/ → {kept} 个文件（按隐私策略剔除 {skipped} 个）")

    # 必需根目录文件（单文件，无私有目录风险）
    for f in ["AGENTS.md", "GEMINI.md", "README.md", "00_考研全科总战役规划.example.md"]:
        fp = ROOT / f
        if fp.exists():
            datas.append(f"{fp}{sep}.")

    return datas


SUBJECT_DIRS = ("01-数学", "02-英语", "03-思想政治理论", "04-专业课")

# [P25 修复] 隐私策略收敛到单一事实源 tools/privacy_policy.py。
# 历史教训：本文件与 sync_publish.py 曾各自维护一份「哪些目录不能发」的清单，
# 结果只修了打包路径，公开仓库副本路径长期裸奔（1.8GB dist/、四科 _状态/
# 真实学情、出版社 PDF 被镜像进 kaoyan_chain_public）。策略重复定义必然漂移。
try:  # 双导入路径兼容（脚本直跑 / pytest / 包内导入）
    from privacy_policy import (  # noqa: E402
        JUNK_DIR_NAMES as _JUNK_DIR_NAMES,
        JUNK_FILE_EXTS as _JUNK_FILE_EXTS,
        PRIVATE_CONFIG_FILES as _PRIVATE_CONFIG_FILES,
        PRIVATE_DIRS,
        ROOT_ONLY_EXCLUDE_DIRS as _ROOT_ONLY_EXCLUDE_DIRS,
        SKELETON_WHITELIST,
        identity_name_tokens as _identity_name_tokens,
        is_backup as _is_backup,
        is_junk as _is_policy_junk,
        private_owner as _private_owner,
        should_publish as _should_publish,
    )
    import privacy_policy as _pp  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.privacy_policy import (  # noqa: E402
        JUNK_DIR_NAMES as _JUNK_DIR_NAMES,
        JUNK_FILE_EXTS as _JUNK_FILE_EXTS,
        PRIVATE_CONFIG_FILES as _PRIVATE_CONFIG_FILES,
        PRIVATE_DIRS,
        ROOT_ONLY_EXCLUDE_DIRS as _ROOT_ONLY_EXCLUDE_DIRS,
        SKELETON_WHITELIST,
        identity_name_tokens as _identity_name_tokens,
        is_backup as _is_backup,
        is_junk as _is_policy_junk,
        private_owner as _private_owner,
        should_publish as _should_publish,
    )
    from tools import privacy_policy as _pp  # noqa: E402

#: 用户私有目录（与 .gitignore「隐私防泄露铁律」保持一致）：绝不随发布包分发。
#: 这些目录里存放的是学员真实学情 —— 教材 PDF、做题记录、每日笔记、错题本等。
#: 目录本身允许存在（供学员首次写入），内容按 SKELETON_WHITELIST 过滤。
#: 白名单说明：init_workspace.py 依赖 rglob("*.template.md") 把模板初始化为
#: 学员工作文件；05-考研看板 依赖 错题本/_索引.md 与 _状态/*核心速记*.md
#: 抽取看板卡片；verify_health.py 依赖 参考资料/README.md 等骨架存在。
#: 除白名单外的任何文件（教材 PDF、做题记录、每日笔记等）一律不进包。
#:
#: 注：PRIVATE_DIRS / SKELETON_WHITELIST 为模块级别名，保持既有调用点与测试兼容。


def _is_junk(name: str) -> bool:
    """缓存/开发临时文件、已生成的个人配置、备份与测试产物。

    通用判定（缓存目录、临时后缀、个人配置、``_backup_`` 备份标记）委托给
    privacy_policy，保证与公开仓库副本路径口径一致；仅在此保留**打包路径特有**
    的排除规则（测试产物、个性化侦察报告），这些不应污染通用策略模块。
    """
    if _is_policy_junk(name) or name in _PRIVATE_CONFIG_FILES:
        return True
    if _is_backup(name):
        return True
    # —— 以下为打包路径特有规则 ——
    if "测试" in name:
        return True
    # [R2-D5] 个性化侦察报告：文件名里就写着学员真实校名/专业，
    # 且它们是 comparator/scout 的运行时产出（用户数据），本就不该随包分发。
    if name.startswith(("双校考情对比_", "双校对标_", "目标院校情报_")):
        return True
    return False


#: 「真实身份文件名」红线的**豁免根目录**：这里的文件名是公开数据库条目
#: （``data/universities/<省>/<校名>.yaml``，1800+ 所高校），校名属于**功能数据**
#: 而非个人身份。若不豁免，公开高校库会被整条丢掉，985/211 识别随之失效。
IDENTITY_NAME_EXEMPT_ROOTS: Tuple[str, ...] = ("data",)


def _identity_name_applies(rel_parts: Tuple[str, ...]) -> bool:
    """该路径是否适用「真实身份文件名」红线（公开数据库根豁免）。"""
    return not (rel_parts and rel_parts[0] in IDENTITY_NAME_EXEMPT_ROOTS)


def _current_identity_tokens() -> Tuple[str, ...]:
    """当前真实报考身份字面量（按 ROOT 缓存，避免逐文件读 ky_config.json）。

    ``ROOT`` 会被测试 monkeypatch 成临时目录，故以路径字符串为缓存键；
    键不同即重新读取，不会串味。
    """
    return _identity_tokens_cached(str(ROOT))


@lru_cache(maxsize=8)
def _identity_tokens_cached(root_str: str) -> Tuple[str, ...]:
    return tuple(_identity_name_tokens(Path(root_str)))


def _is_identity_name(name: str) -> bool:
    """文件名本身是否夹带当前真实报考身份（校名 / 专业 / 自命题科目组合）。"""
    return any(tok in name for tok in _current_identity_tokens())


def _reset_dir(path: Path, label: str) -> None:
    """删除已存在的目标目录。

    [P1 修复] 不使用 ignore_errors=True：Windows 下文件被占用时 rmtree 会静默
    失败，既不抛异常也不告警，随后 copytree(dirs_exist_ok=True) 会把旧内容
    （可能含用户私有资料）与新内容叠加，发布包反而越修越脏。这里失败即明确
    告警并中止构建，绝不留下「以为清理了、其实还在」的假象。
    """
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError as e:
        print(f"[!] 清理旧目录失败（{label}）: {path}\n    原因: {e}")
        print("    请关闭可能占用该目录的程序（资源管理器/杀毒软件/正在运行的 exe）后重试。")
        raise


def leak_reason(rel_parts: Tuple[str, ...], name: str,
                identity_tokens: Tuple[str, ...] = ()) -> Optional[str]:
    """判断产物内某个文件是否属于「绝不允许分发」的用户资料；是则返回原因。

    [R2-C1 修复] 断言口径不再靠 `target_dir/<科目>/<私有目录>` 的位置推断，
    而是对**产物根下的任意相对路径**做判定，这样 PyInstaller 的 ``_internal/``
    副本（以及未来任何新增的复制路径）都在覆盖范围内。

    [R2-D5 修复] 增补第 5 类红线「真实身份文件名」：实锤
    ``_internal/04-专业课/目标院校情报_<真实校名>_<专业代码 专业名>.md``
    既不在私有目录内、也不是备份/配置，前四类一条都拦不住，文件名却直接写着
    学员真实校名与专业。身份字面量由调用方传入（``identity_name_tokens(ROOT)``），
    避免在此逐文件读 ky_config.json。

    只认五类硬红线，避免误伤 _internal 下的第三方内容
    （PySide6/、docs/assets/vendor/katex/<ver>/dist/ 等）与缓存文件：
      1. 用户私有目录（参考资料/_状态/错题本/…）内、且不在骨架白名单中的文件；
      2. 历史备份快照（*_backup_*）；
      3. 个人运行时配置（ky_config.json / state_snapshot.json / …）；
      4. 产物根第一层的构建产物 / 开发脚手架目录（dist、build、logs、.agents…）；
      5. 文件名本身夹带当前真实报考身份（校名 / 专业 / 自命题科目组合）——
         ``data/`` 公开高校库豁免（见 IDENTITY_NAME_EXEMPT_ROOTS）。
    """
    if _is_backup(name):
        return "历史备份快照"
    if name in _PRIVATE_CONFIG_FILES:
        return "个人运行时配置"
    if rel_parts and rel_parts[0] in _ROOT_ONLY_EXCLUDE_DIRS:
        return "构建产物/开发脚手架目录"
    owner = _private_owner(rel_parts)
    if owner is not None:
        allowed = SKELETON_WHITELIST.get(owner, ())
        if not any(fnmatch.fnmatch(name, pat) for pat in allowed):
            return f"私有目录 {owner} 内的用户资料"
    if _identity_name_applies(rel_parts):
        for tok in identity_tokens:
            if tok in name:
                return f"真实身份文件名（含「{tok}」）"
    return None


def assert_private_dirs_clean(target_dir: Path) -> None:
    """打包后产物断言：**递归扫描整个产物根**，任何位置都不得夹带用户私有资料。

    目录本身允许存在（供学员首次写入），但绝不允许夹带任何用户私有资料
    （教材 PDF、做题记录、每日笔记、个人配置等）。检出即中止构建。

    [R2-C1 修复] 旧实现只扫 ``target_dir/<科目>/<私有目录>``，而 PyInstaller 把
    ``--add-data`` 的内容复制到 ``target_dir/_internal/<科目>/``，于是
    「教材 PDF / 错题本 / _状态 真实学情 / *_backup_*」从这条完全没被检查的路径
    照样进包，断言却照常打印 [√]。现在改为遍历整个产物根（含 _internal/）。

    [R2-D5 修复] 扫描时一并套用第 5 类红线（真实身份文件名），堵住
    「文件名即身份」这类前四类红线全都看不见的泄漏。
    """
    identity_tokens = _current_identity_tokens()
    leaks: List[Tuple[str, str]] = []
    for p in sorted(target_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(target_dir)
        reason = leak_reason(rel.parts, p.name, identity_tokens)
        if reason:
            leaks.append((rel.as_posix(), reason))

    if leaks:
        shown = "\n".join(f"    - {rel}  （{reason}）" for rel, reason in leaks[:20])
        more = f"\n    ... 其余 {len(leaks) - 20} 项省略" if len(leaks) > 20 else ""
        raise RuntimeError(
            f"[隐私告警] 发布包 {target_dir} 中检出 {len(leaks)} 个用户资料文件，"
            f"已中止构建：\n{shown}{more}"
        )
    print(f"  [√] 隐私断言通过：产物根（含 _internal/）全量扫描无用户资料残留")
    print("  [√] 隐私断言通过：私有目录内仅含骨架模板，无用户资料残留")


def purge_junk_from_product(target_dir: Path) -> int:
    """[R2-C1 补漏] 清除 PyInstaller 绕过 staging 塞进产物根的脚手架/缓存目录。

    ``build()`` 的 PyInstaller 参数里有 ``--collect-all tools``，它会把整个
    ``tools`` 包**再原样收集一遍**到 ``_internal/tools/`` —— 这条路径完全不经过
    ``collect_data_specs()`` 的 staging 过滤。实测：``tools/scratch/uploads/``
    下 199 张学员剪贴板截图（``clip_*.png``，见 tools/cli/repl/session.py 的
    粘贴落盘）就是这样进了发布包，而 ``assert_private_dirs_clean()`` **看不见**它们
    （scratch 不是私有目录、不是备份、不是个人配置，文件名里也没有身份词），
    断言照样打印 [√]。这与 R2-C1 的原始实锤（``_internal/`` 绕过隐私过滤）同类，
    只是入口从 ``--add-data`` 换成了 ``--collect-all``。

    处理方式：在断言之前，把 ``privacy_policy.JUNK_DIR_NAMES`` 里的目录从产物根
    整棵删除。它们都是**运行时自建**的（如 session.py 里
    ``upload_dir.mkdir(parents=True, exist_ok=True)``），删掉不影响冻结程序。
    """
    if not target_dir.is_dir():
        return 0
    # 先枚举再删除，并让深层目录先于其父目录处理
    victims = [
        p for p in target_dir.rglob("*")
        if p.is_dir() and p.name in _JUNK_DIR_NAMES
    ]
    removed = 0
    for p in sorted(victims, key=lambda x: len(x.parts), reverse=True):
        if not p.exists():
            continue  # 已被其祖先目录一并删除
        try:
            shutil.rmtree(p)
            print(f"  [purge] 移除产物内脚手架目录: {p.relative_to(target_dir).as_posix()}")
            removed += 1
        except OSError as e:
            print(f"  [purge] 未移除 {p}: {type(e).__name__}: {e}")
    if removed:
        print(f"  [purge] 共移除 {removed} 个脚手架/缓存目录")
    return removed


# ── 内容级身份脱敏（产物侧与发布副本同源）────────────────────────────────────
# 历史问题：本文件原有四道隐私防线（staging 过滤 / leak_reason 五类红线 /
# assert_private_dirs_clean / purge_junk_from_product）**全部只看路径与文件名，
# 从不读文件内容**。于是路径与文件名都「干净」、内容却写着真实校名 / 专业 /
# 薄弱点的文件 100% 通过全部断言 —— 实测 dist/KaoyanStudyChain/ 有 40 处身份字面量
# 散在 8 个文件里（AGENTS.md、四科 AGENTS.md / 备考总规划.md / 考试大纲.md，
# 以及 05-考研看板/web/snapshot.py 这个**代码文件**），而构建全程打印「隐私断言通过」。
# 现补上内容级脱敏，与 tools/sync_publish.py 共用 privacy_policy 的同一套规则与引擎。

#: 产物里需要做内容级脱敏的「自有子树」（产物根与 `_internal/` 下各一份）。
#: 刻意**不做全树 rglob**：`_internal/` 下还住着 PySide6 / PIL / shapely 等第三方包
#: （几千个 .py），对它们做身份替换既无意义、又可能改坏库代码、还拖慢构建。
_PRODUCT_SANITIZE_TREES: Tuple[str, ...] = (
    "tools", "docs",
    "01-数学", "02-英语", "03-思想政治理论", "04-专业课", "05-考研看板",
)

#: 产物根 / `_internal/` 根下的自有文件。
_PRODUCT_SANITIZE_ROOT_FILES: Tuple[str, ...] = (
    "AGENTS.md", "GEMINI.md", "README.md", "00_考研全科总战役规划.example.md",
)


def _sanitize_rel(f: Path, target_dir: Path) -> str:
    """文件相对产物根的路径；`_internal/` 前缀被剥掉 —— 同一份内容有两种落点。"""
    rel = f.relative_to(target_dir).as_posix()
    prefix = "_internal/"
    return rel[len(prefix):] if rel.startswith(prefix) else rel


def _iter_sanitize_targets(target_dir: Path) -> List[Path]:
    """产物里需要脱敏的文件（产物根 + `_internal/`，只覆盖自有子树）。"""
    out: List[Path] = []
    for base in (target_dir, target_dir / "_internal"):
        if not base.is_dir():
            continue
        for name in _PRODUCT_SANITIZE_ROOT_FILES:
            f = base / name
            if f.is_file():
                out.append(f)
        for name in _PRODUCT_SANITIZE_TREES:
            d = base / name
            if not d.is_dir():
                continue
            for ext in _pp.SANITIZED_SUFFIXES:
                out.extend(d.rglob(ext))
    return out


def sanitize_product(target_dir: Path, *, skip_rels=()) -> List[Path]:
    """对产物做**内容级**身份脱敏；返回被改写的文件列表。

    与 `tools/sync_publish.py` 共用 `tools/privacy_policy.py` 的规则与引擎，
    规则按**本模块的 ROOT 现算** —— 不是 import 时冻结，故测试里
    `monkeypatch.setattr(bp, "ROOT", tmp)` 能真正生效。
    规则为空（本机没有 ky_config.json）时是 no-op，不报错。

    Args:
        skip_rels: 相对产物根的路径，命中的文件整份跳过。调用方传
            `privacy_policy.PY_UNSANITIZED_FILES` —— 公开高校映射表，
            脱敏会把「校名 → 院校代码」改坏（985/211 识别全线失效）。
    """
    skip = {Path(rel).as_posix() for rel in skip_rels}
    md_rules = _pp.build_substitutions(ROOT)
    py_rules = _pp.build_py_substitutions(ROOT)
    changed: List[Path] = []
    for f in _iter_sanitize_targets(target_dir):
        rel = _sanitize_rel(f, target_dir)
        if rel in skip or rel == "LICENSE":
            continue
        is_py = f.suffix == ".py"
        new = _pp.sanitize_file_text(f, py_rules if is_py else md_rules,
                                     line_rules=not is_py)
        if new is None:
            continue
        f.write_text(new, encoding="utf-8")
        changed.append(f)
        print(f"  [sanitize] {f.relative_to(target_dir).as_posix()}")
    print(f"  [sanitize] 内容级脱敏完成：改写 {len(changed)} 个文件"
          f"（规则 md {len(md_rules)} 条 / py {len(py_rules)} 条）")
    return changed


def _pyinstaller_base_args(dist_dir: Path, build_dir: Path) -> List[str]:
    """PyInstaller 基础参数。

    `--specpath` 指向 `build/`（已在 .gitignore）：不给它的话，PyInstaller 会在
    **CWD（仓库根）** 生成 `KaoyanStudyChain.spec`，把仓库里那份手写的「路径无关」
    spec 覆盖掉 —— 生成版里写死了本机盘符路径与 `ky_pkg_staging_xxx` 这种构建后
    即消失的临时目录，正是那份手写 spec 开头专门警告过的情况。
    """
    return [
        sys.executable, "-m", "PyInstaller",
        "--name", "KaoyanStudyChain",
        "--noconfirm",
        "--clean",
        f"--specpath={build_dir}",
        f"--distpath={dist_dir}",
        f"--workpath={build_dir}",
        f"--paths={TOOLS}",
        f"--paths={ROOT}",
        "--collect-all", "tools",
        "--collect-data", "certifi",
        "--collect-submodules", "PySide6",
    ]


def _assert_identity_rules_effective(keep_identity: bool) -> None:
    """构建前的 fail-fast 闸门：脱敏规则算不出来时中止，别静默产出泄漏包。

    **两种「规则算不出来」必须区别对待**（这是本闸门的全部要点）：

    1. `ky_config.json` **存在**、但 `identity_rules_effective()` 为假 ——
       这是「配置被冲成占位值 → 脱敏静默失效」的场景，而 `AGENTS.md` 里可能仍留着
       旧的**真实**身份 → **抛错中止**（提示修 study_plan，或显式 `--keep-identity`）。
    2. `ky_config.json` **不存在**（贡献者 / CI / 全新 clone）—— 本机**无身份可脱敏**，
       脱敏自然是 no-op → 只打日志**放行**，不能因为「这里不是考生本机」就构建不了。

    `keep_identity=True` 时一律放行（用户明确要一份带个人方案的包）。
    """
    if keep_identity:
        print("[*] --keep-identity：跳过身份脱敏前置校验（产物将包含个人报考方案）")
        return
    if not (ROOT / "ky_config.json").exists():
        print("[*] 未发现 ky_config.json（非考生本机）：无身份可脱敏，跳过内容级脱敏")
        return
    ok, reason = _pp.identity_rules_effective(ROOT)
    if not ok:
        raise RuntimeError(
            "[隐私闸门] ky_config.json 存在，但动态身份脱敏规则无效："
            f"{reason}\n"
            "    这会让内容级脱敏静默失效，而产物里可能仍留有旧的**真实**报考信息。\n"
            "    请先修复 ky_config.json 的 study_plan（school / major / pro_name），\n"
            "    或显式加 --keep-identity 表示你确实要一份带个人方案的包。"
        )
    print(f"[*] 身份脱敏规则就绪：{reason}")


def deploy_workspace_skeleton(target_dir: Path, keep_identity: bool = False):
    """复制干净的科目骨架、高校库、前端资源与顶层协议至独立发布包根目录"""
    print(f"\n[*] 正在部署工作区骨架与数据底座至: {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)

    def ignore_patterns(src, names):
        ignored = {name for name in names if _is_junk(name)}
        # [2026-09-24 检查补漏] 与导出层 / staging 同口径：.gitignore 已忽略的
        # 本地产物与受限路径（原始快照、看板构建产物、研报/考纲生成物等）
        # 同样不得随发布包分发 —— 此前只套 _is_junk，dist 实测它们原样进了
        # 产物根（staging 已排除、部署这一步漏掉）。
        try:
            rel_dir = Path(src).relative_to(ROOT)
        except (ValueError, IndexError):
            rel_dir = None
        if rel_dir is not None:
            for name in names:
                if name in ignored:
                    continue
                if _pp.is_local_artifact((rel_dir / name).as_posix()):
                    ignored.add(name)
        # [P1 修复] 位于用户私有目录内时，仅放行骨架白名单，其余一律剔除
        owner = _private_owner(src)
        if owner is not None:
            allowed = SKELETON_WHITELIST[owner]
            for name in names:
                if name in ignored:
                    continue
                if not any(fnmatch.fnmatch(name, pat) for pat in allowed):
                    ignored.add(name)
        # [R2-D5 修复] 按「真实身份文件名」兜底剔除。
        # data/ 刻意豁免（公开高校库，校名是功能数据，见 IDENTITY_NAME_EXEMPT_ROOTS）。
        try:
            top = Path(src).relative_to(ROOT).parts[0]
        except (ValueError, IndexError):
            top = ""
        if _identity_name_applies((top,)):
            ignored.update(name for name in names if _is_identity_name(name))
        return ignored

    # 1. 复制科目骨架 01-05
    for folder_name in ["01-数学", "02-英语", "03-思想政治理论", "04-专业课", "05-考研看板"]:
        src_path = ROOT / folder_name
        dst_path = target_dir / folder_name
        if src_path.exists():
            _reset_dir(dst_path, folder_name)
            shutil.copytree(src_path, dst_path, ignore=ignore_patterns, dirs_exist_ok=True)
            print(f"  [√] 部署骨架目录: {folder_name}")

    # 确保每个科目的必要运行子目录存在
    subject_subdirs = {
        "01-数学": ["参考资料", "每日笔记", "错题本", "_状态"],
        "02-英语": ["参考资料", "每日笔记", "错题与长难句本", "作文语料库", "_状态"],
        "03-思想政治理论": ["参考资料", "每日笔记", "错题本", "_状态"],
        "04-专业课": ["参考资料", "每日作业", "错题本", "_状态"],
    }
    for subj, subdirs in subject_subdirs.items():
        for sub in subdirs:
            (target_dir / subj / sub).mkdir(parents=True, exist_ok=True)

    # 2. 复制 data 目录 (全国高校库与站点图谱)
    data_src = ROOT / "data"
    data_dst = target_dir / "data"
    if data_src.exists():
        _reset_dir(data_dst, "data")
        shutil.copytree(data_src, data_dst, ignore=ignore_patterns, dirs_exist_ok=True)
        print("  [√] 部署高校数据库: data/")

    # 3. 复制 docs 目录 (含 logo, vendor, html 等)
    docs_src = ROOT / "docs"
    docs_dst = target_dir / "docs"
    if docs_src.exists():
        _reset_dir(docs_dst, "docs")
        shutil.copytree(docs_src, docs_dst, ignore=ignore_patterns, dirs_exist_ok=True)
        print("  [√] 部署静态资源: docs/")

    # 4. 复制根协议与说明文件
    root_files = [
        "AGENTS.md",
        "GEMINI.md",
        "README.md",
        "00_考研全科总战役规划.example.md",
    ]
    for rf in root_files:
        src_f = ROOT / rf
        if src_f.exists():
            shutil.copy2(src_f, target_dir / rf)
            print(f"  [√] 部署协议文件: {rf}")

    # 5. 生成配套的 调试启动.bat 方便排查
    debug_bat = target_dir / "调试启动.bat"
    debug_bat.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "title 考研学习链 · 调试运行控制台\r\n"
        "echo [*] 正在以控制台调试模式启动 KaoyanStudyChain.exe...\r\n"
        "\"%~dp0KaoyanStudyChain.exe\"\r\n"
        "set EXIT_CODE=%errorlevel%\r\n"
        "if %EXIT_CODE% neq 0 (\r\n"
        "    echo.\r\n"
        "    echo [!] 程序异常退出，退出码: %EXIT_CODE%\r\n"
        "    pause\r\n"
        ")\r\n",
        encoding="utf-8"
    )
    print("  [√] 生成调试启动脚本: 调试启动.bat")

    # 6. [P1 修复] 打包后产物断言：私有目录内不得残留任何用户资料
    #    [R2-C1 补漏] 先清掉 --collect-all 绕过 staging 带进来的脚手架目录
    #    （tools/scratch/uploads/*.png 等），否则断言看不见它们。
    purge_junk_from_product(target_dir)
    if keep_identity:
        print("  [√] --keep-identity：跳过内容级脱敏（产物保留个人报考方案）")
    else:
        # [R2-E4] 内容级脱敏 + 两道闸门。顺带说明扫描范围：`--collect-all tools`
        # 把 tools/ 原样收进 `_internal/tools/`，**不经过 staging 过滤** —— 所以
        # 必须按产物全树（含 _internal/）复核，只信 staging 一定会漏。
        changed = sanitize_product(target_dir, skip_rels=_pp.PY_UNSANITIZED_FILES)
        _pp.verify_python_compiles(target_dir, changed)
        # strip_prefixes：产物把同一份内容同时放在根与 _internal/ 下，而
        # `data/`（公开高校库）与 PY_UNSANITIZED_FILES（公开校名→代码映射）两条豁免
        # 是按仓库相对路径写的 —— 不剥前缀会把它们当残留误报，构建被自己的自检挡下。
        residual = _pp.scan_residual_identity(target_dir, ROOT,
                                              strip_prefixes=("_internal/",))
        if residual:
            shown = "\n".join(f"    - {r}" for r in residual[:20])
            more = f"\n    ... 其余 {len(residual) - 20} 项省略" if len(residual) > 20 else ""
            raise RuntimeError(
                f"[隐私闸门] 脱敏后产物仍有 {len(residual)} 个文件含当前真实身份，已中止：\n"
                f"{shown}{more}"
            )
        print("  [√] 内容级脱敏与残留自检通过：产物无当前真实身份字面量")
    assert_private_dirs_clean(target_dir)


def generate_inno_setup_script(output_dir: Path) -> Path:
    """生成 Windows Inno Setup 安装包编译器配置文件 (dist/installer.iss 并同步至 root installer.iss)"""
    version = get_app_version()
    iss_content = f"""; 考研学习链 (Kaoyan Study Chain) · Inno Setup 自动化安装向导脚本
#define MyAppName "考研学习链"
#define MyAppVersion "{version}"
#define MyAppPublisher "Kaoyan Study Chain Community"
#define MyAppURL "https://github.com/moyetian/kaoyan_chain"
#define MyAppExeName "KaoyanStudyChain.exe"

#if FileExists("KaoyanStudyChain\\KaoyanStudyChain.exe")
  #define AppSourceDir "KaoyanStudyChain"
  #define AppIconFile "KaoyanStudyChain\\docs\\assets\\logo\\favicon.ico"
  #define MyOutputDir "installer"
#else
  #define AppSourceDir "dist\\KaoyanStudyChain"
  #define AppIconFile "docs\\assets\\logo\\favicon.ico"
  #define MyOutputDir "dist\\installer"
#endif

[Setup]
AppId={{{{C897B762-81D2-4820-9D7D-68B9E780E24D}}}}
AppName={{#MyAppName}}
AppVersion={{#MyAppVersion}}
AppPublisher={{#MyAppPublisher}}
AppPublisherURL={{#MyAppURL}}
AppSupportURL={{#MyAppURL}}
AppUpdatesURL={{#MyAppURL}}
DefaultDirName={{autopf}}\\{{#MyAppName}}
DefaultGroupName={{#MyAppName}}
DisableProgramGroupPage=yes
; 允许普通考生安装至个人目录，无需管理员 UAC 强行提权
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir={{#MyOutputDir}}
OutputBaseFilename=KaoyanStudyChain_Setup_v{{#MyAppVersion}}
SetupIconFile={{#AppIconFile}}
UninstallDisplayIcon={{app}}\\docs\\assets\\logo\\favicon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\\ChineseSimplified.isl"

[CustomMessages]
chinesesimplified.CreateDesktopIcon=创建桌面快捷方式(&D)
chinesesimplified.AdditionalIcons=附加图标:
chinesesimplified.LaunchProgram=立即启动 考研学习链

[Tasks]
Name: "desktopicon"; Description: "{{cm:CreateDesktopIcon}}"; GroupDescription: "{{cm:AdditionalIcons}}"

[Files]
; 递归打包独立运行包全部内容
Source: "{{#AppSourceDir}}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{autoprograms}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; IconFilename: "{{app}}\\docs\\assets\\logo\\favicon.ico"
Name: "{{autodesktop}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; Tasks: desktopicon; IconFilename: "{{app}}\\docs\\assets\\logo\\favicon.ico"

[Run]
Filename: "{{app}}\\{{#MyAppExeName}}"; Description: "{{cm:LaunchProgram}}"; Flags: nowait postinstall skipifsilent
"""
    output_dir.mkdir(parents=True, exist_ok=True)
    dist_iss = output_dir / "installer.iss"
    dist_iss.write_text(iss_content.strip() + "\n", encoding="utf-8-sig")
    print(f"  [√] Inno Setup 脚本已生成: {dist_iss}")

    # 同时复制至项目根目录 installer.iss
    root_iss = ROOT / "installer.iss"
    root_iss.write_text(iss_content.strip() + "\n", encoding="utf-8-sig")
    print(f"  [√] Inno Setup 脚本已同步至根目录: {root_iss}")
    return dist_iss


def build(dry_run: bool = False, onefile: bool = False, keep_identity: bool = False) -> int:
    """执行构建主逻辑。

    [R2-C1 修复] `--add-data` 的源改为临时 staging 目录（按隐私策略过滤），
    因此这里负责 staging 的生命周期：构建结束（含异常/提前 return）即清理，
    绝不在仓库内留残留。
    """
    staging_root = Path(tempfile.mkdtemp(prefix="ky_pkg_staging_"))
    try:
        return _build_with_staging(staging_root, dry_run=dry_run, onefile=onefile,
                                   keep_identity=keep_identity)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _build_with_staging(staging_root: Path, dry_run: bool = False,
                        onefile: bool = False, keep_identity: bool = False) -> int:
    app_version = get_app_version()
    print("\n========================================================")
    print(f"   考研学习链 (Kaoyan Study Chain) · 独立安装包构建工具 v{app_version}")
    print("========================================================\n")

    dist_dir = ROOT / "dist"
    build_dir = ROOT / "build"
    entry_point = ROOT / "tools" / "ky_gui.py"

    if not entry_point.exists():
        print(f"[!] 找不到入口文件: {entry_point}")
        return 1

    # [R2-E4] fail-fast：脱敏规则算不出来就别开工（详见函数文档）。
    # 只在真实构建时校验 —— `--dry-run` 是诊断手段，不该因为配置问题而不可用。
    if not dry_run:
        _assert_identity_rules_effective(keep_identity)

    print("[*] 正在按隐私策略过滤打包资源（staging）...")
    datas = collect_data_specs(staging_root)
    print(f"[*] 已收录 {len(datas)} 项核心项目资源与科目骨架")

    # 图标路径
    icon_ico = ROOT / "docs" / "assets" / "logo" / "favicon.ico"
    if not icon_ico.exists():
        icon_ico = ROOT / "docs" / "assets" / "favicon.ico"

    # 构造 PyInstaller 参数
    cmd = _pyinstaller_base_args(dist_dir, build_dir)

    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")
        cmd.append("--windowed")

    if icon_ico.exists():
        cmd.extend(["--icon", str(icon_ico)])

    for d in datas:
        cmd.extend(["--add-data", d])

    # 显式 Hidden Imports
    hidden_imports = [
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtNetwork",
        "fsrs",
        "filelock",
        "yaml",
        "sympy",
        "pypdf",
        "PIL",
        "certifi",
        "requests",
        "httpx",
        "gzip",
        "ssl",
        "ctypes",
        "urllib.request",
        "urllib.parse",
        "http.client",
        "tools.gui.main_window",
        "tools.gui.services.settings",
        "tools.gui.services.dashboard",
        "tools.gui.widgets.onboarding_wizard",
        "tools.gui.widgets.settings_dialog",
        "tools.gui.widgets.wechat_search_dialog",
        "tools.gui.workers.agent_worker",
        "tools.gui.workers.intel_worker",
        "tools.intelligence.agentic_research",
        "tools.intelligence.registry",
        "tools.intelligence.comparator",
        "tools.intelligence.scout_engine",
        "tools.intelligence.watcher",
        "tools.intelligence.syllabus_diff",
        "tools.skills.wechat_searcher",
        "tools.skills.school_scout",
        "tools.skills.material_ingestion",
        "tools.skills.vision_solver",
        "tools.skills.error_logger",
        "tools.search.service",
        "tools.search.providers.bing",
        "tools.search.providers.ddg",
        "tools.search.providers.sogou",
        "tools.search.providers.tavily",
    ]
    for h in hidden_imports:
        cmd.extend(["--hidden-import", h])

    cmd.append(str(entry_point))

    if dry_run:
        print("\n[DRY RUN 预演模式]（仅校验入口与资源清单，不检查构建依赖）")
        print("  版本号:", app_version)
        print("  目标输出目录:", dist_dir)
        print("  构建入口:", entry_point)
        print("  打包模式:", "Single File (单文件)" if onefile else "Directory (独立免装目录)")
        print(f"  构建命令预览:\n  {' '.join(cmd[:15])} ... (共 {len(cmd)} 项参数)")
        print("  依赖检查: 已跳过（未校验 PyInstaller / PySide6；执行 --build 时才会检查）")
        dist_dir.mkdir(parents=True, exist_ok=True)
        generate_inno_setup_script(dist_dir)
        print("\n[√] DRY RUN 预演完成：资源清单与打包命令已就绪（构建依赖尚未校验）。")
        return 0

    if not check_prerequisites():
        return 1

    if onefile and not keep_identity:
        # onefile 没有「构建后产物树」可供逐文件处理，只能在 --add-data 的源头
        # （staging）先脱敏。注意 `--collect-all tools` 走的是真实 tools/ 目录、
        # 不经过 staging，因此 onefile 下这部分无法覆盖 —— 故在此显式告警，
        # 而不是让用户误以为 onefile 也做了完整的内容级脱敏。
        print("[!] --onefile：内容级脱敏只能作用于 --add-data 的 staging 来源，"
              "无法覆盖 --collect-all tools 打进 exe 的那份；"
              "需要完整的构建后残留自检请用默认的独立目录模式。")
        sanitize_product(staging_root, skip_rels=_pp.PY_UNSANITIZED_FILES)

    print(f"\n[*] 正在启动 PyInstaller 进行独立发布包编译 v{app_version}（可能需要 1~3 分钟）...")
    ret = subprocess.call(cmd)
    if ret == 0:
        target_dist = dist_dir / "KaoyanStudyChain"
        print("\n========================================================")
        print(f"  [√] 核心二进制编译完成！输出目录: {target_dist}")
        print("========================================================")
        if not onefile:
            deploy_workspace_skeleton(target_dist, keep_identity=keep_identity)
        generate_inno_setup_script(dist_dir)
        print("\n[√] 全部打包与安装包配置已就绪！")
        return 0
    else:
        print(f"\n[!] 构建失败，退出码: {ret}")
        return ret


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="考研学习链打包构建工具")
    parser.add_argument("--dry-run", action="store_true", help="预演打包命令与资源清单（不检查 PyInstaller/PySide6 构建依赖）")
    parser.add_argument("--onefile", action="store_true", help="打包为单个 .exe 文件 (默认生成独立目录)")
    parser.add_argument("--build", action="store_true", help="执行完整 PyInstaller 构建")
    parser.add_argument("--keep-identity", action="store_true",
                        help="保留个人报考方案不做内容级脱敏（默认会脱敏；给自己打出"
                             "「开箱即用带我的方案」的包时才用）")
    args = parser.parse_args()

    # 默认若未显式指定 --build，则安全执行 dry-run
    if not args.build:
        sys.exit(build(dry_run=True, onefile=args.onefile,
                       keep_identity=args.keep_identity))
    else:
        sys.exit(build(dry_run=False, onefile=args.onefile,
                       keep_identity=args.keep_identity))
