# -*- coding: utf-8 -*-
"""
自动化验收测试：独立打包与 Inno Setup 安装包工程 (Milestone M7 / C2)
================================================================
测试范围：
1. dist/KaoyanStudyChain/KaoyanStudyChain.exe 存在且具备真实二进制体积 (>20MB)
2. 必需科目骨架 (01-数学 ~ 05-考研看板) 与运行子目录完整性
3. 全国高校考研数据库 (data/universities/registry.json) 与图标资源就绪
4. Inno Setup 配置文件 (dist/installer.iss 与 installer.iss) 纯正简体中文向导与快捷方式规范
5. QSS 模板与注册表在 sys.frozen 冻结环境下的自适应解析
6. GUI.bat 退出反馈一致性
"""

import json
import os
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist" / "KaoyanStudyChain"
EXE_PATH = DIST_DIR / "KaoyanStudyChain.exe"
DIST_ISS_PATH = ROOT / "dist" / "installer.iss"
ROOT_ISS_PATH = ROOT / "installer.iss"

#: [R2-B3 修复] `dist/` 是 `py tools/build_package.py` 的构建产物，且被 `.gitignore`
#: 锚定在仓库根（git 未跟踪，0 文件）。在**干净检出**（含 CI 的 pytest 步骤，见
#: `.github/workflows/test.yml:76-78`，该步骤不先构建 dist）上，凡直接依赖 dist 的
#: 断言都会硬失败 —— 实测 7 例 fail。这里改为按测试逐个判断：真正依赖 dist 产物的
#: 用例声明 skipif；不依赖 dist 的守卫（仓库根 installer.iss、GUI.bat 一致性、
#: 冻结路径解析）继续在干净检出上真跑，避免「一刀切 skip」把守卫整体关掉。
DIST_READY = EXE_PATH.exists()
DIST_SKIP_REASON = "需先构建 dist/（py tools/build_package.py）"


@pytest.mark.skipif(not DIST_READY, reason=DIST_SKIP_REASON)
def test_executable_exists_and_genuine_size():
    """验证可执行文件存在且为真实独立二进制文件 (>20MB)"""
    assert EXE_PATH.exists(), f"未找到构建产物可执行程序: {EXE_PATH}"
    size_bytes = EXE_PATH.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    print(f"\n[Test] KaoyanStudyChain.exe 体积: {size_mb:.2f} MB ({size_bytes} 字节)")
    assert size_mb >= 20.0, f"可执行程序体积异常 ({size_mb:.2f} MB)，疑似未真实编译"


@pytest.mark.skipif(not DIST_READY, reason=DIST_SKIP_REASON)
def test_internal_runtime_directory_exists():
    """验证 PyInstaller onedir 内部运行环境目录存在"""
    internal_dir = DIST_DIR / "_internal"
    assert internal_dir.exists() and internal_dir.is_dir(), f"缺失运行环境目录: {internal_dir}"


@pytest.mark.skipif(not DIST_READY, reason=DIST_SKIP_REASON)
def test_subject_skeletons_and_subdirectories():
    """验证独立发布包中的科目骨架与必要可读写目录完整就绪"""
    expected_structure = {
        "01-数学": ["参考资料", "每日笔记", "错题本", "_状态"],
        "02-英语": ["参考资料", "每日笔记", "错题与长难句本", "作文语料库", "_状态"],
        "03-思想政治理论": ["参考资料", "每日笔记", "错题本", "_状态"],
        "04-专业课": ["参考资料", "每日作业", "错题本", "_状态"],
        "05-考研看板": [],
    }

    for subj, subdirs in expected_structure.items():
        subj_path = DIST_DIR / subj
        assert subj_path.exists() and subj_path.is_dir(), f"缺失科目骨架目录: {subj_path}"
        for sub in subdirs:
            sub_path = subj_path / sub
            assert sub_path.exists() and sub_path.is_dir(), f"缺失科目子目录: {sub_path}"


@pytest.mark.skipif(not DIST_READY, reason=DIST_SKIP_REASON)
def test_data_and_registry_assets():
    """验证全国高校数据库与实体注册表完整落盘"""
    reg_file = DIST_DIR / "data" / "universities" / "registry.json"
    assert reg_file.exists(), f"缺失高校注册表: {reg_file}"
    raw = json.loads(reg_file.read_text(encoding="utf-8"))
    assert len(raw) > 0, "registry.json 内容为空"

    # 验证省份考情数据至少包含 20 个以上省市
    prov_dirs = [p for p in (DIST_DIR / "data" / "universities").iterdir() if p.is_dir()]
    assert len(prov_dirs) >= 20, f"省份数据目录不足: {len(prov_dirs)}"


@pytest.mark.skipif(not DIST_READY, reason=DIST_SKIP_REASON)
def test_docs_and_brand_assets():
    """验证 Logo、Favicon 及前端看板资产完整"""
    favicon = DIST_DIR / "docs" / "assets" / "logo" / "favicon.ico"
    assert favicon.exists(), f"缺失图标: {favicon}"
    assert favicon.stat().st_size > 500, "favicon.ico 文件过小或损坏"

    logo = DIST_DIR / "docs" / "assets" / "logo" / "logo.png"
    assert logo.exists(), f"缺失 Logo: {logo}"
    assert logo.stat().st_size > 1000, "logo.png 文件损坏"

    agents_md = DIST_DIR / "AGENTS.md"
    assert agents_md.exists(), f"缺失中枢协议: {agents_md}"
    assert agents_md.stat().st_size > 1000, "AGENTS.md 内容缺失"

    debug_bat = DIST_DIR / "调试启动.bat"
    assert debug_bat.exists(), f"缺失调试启动脚本: {debug_bat}"


def test_inno_setup_scripts_specification():
    """验证 Inno Setup 安装包脚本 (dist 与 root) 纯正中文向导与快捷方式规范

    [R2-B3] `installer.iss` 在仓库根，任何检出（含干净检出/CI）都应被校验，因此本用例
    **不整条 skip**；仅当 `dist/installer.iss` 存在时才追加校验 dist 分支，否则打印说明。
    这样既不会在无 dist 时误报失败，也不会把仓库根那份守卫一起关掉。
    """
    targets = [("仓库根", ROOT_ISS_PATH)]
    if DIST_ISS_PATH.exists():
        targets.append(("dist", DIST_ISS_PATH))
    else:
        print(f"\n[skip-branch] 未构建 {DIST_ISS_PATH}，本次仅校验仓库根 installer.iss")

    for label, iss_path in targets:
        assert iss_path.exists(), f"缺失 Inno Setup 配置文件 ({label}): {iss_path}"
        content = iss_path.read_text(encoding="utf-8-sig")

        # 1. 验证版本号与应用名称
        assert 'MyAppName "考研学习链"' in content
        assert 'MyAppVersion "3.1.0"' in content
        assert 'MyAppExeName "KaoyanStudyChain.exe"' in content

        # 2. 验证纯正简体中文语言包与自定义消息
        assert 'compiler:Languages\\ChineseSimplified.isl' in content
        assert '[CustomMessages]' in content
        assert 'chinesesimplified.CreateDesktopIcon=创建桌面快捷方式(&D)' in content
        assert 'chinesesimplified.LaunchProgram=立即启动 考研学习链' in content

        # 3. 验证图标绑定与快捷方式
        assert 'SetupIconFile=' in content
        assert 'favicon.ico' in content
        assert 'UninstallDisplayIcon={app}\\docs\\assets\\logo\\favicon.ico' in content
        assert 'Tasks: desktopicon' in content
        assert 'Name: "{autodesktop}\\{#MyAppName}"' in content
        assert 'Name: "{autoprograms}\\{#MyAppName}"' in content

        # 4. 验证递归打包与自动运行
        assert 'DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs' in content
        assert 'Filename: "{app}\\{#MyAppExeName}"; Description: "{cm:LaunchProgram}"' in content


def test_frozen_runtime_path_resolution(monkeypatch):
    """验证在 sys.frozen 模式下 compile_qt 与 registry 的路径自适应能力"""
    from tools.theme import compile_qt
    from tools.intelligence import registry

    # 模拟冻结模式指向发布目录
    fake_exe = str(EXE_PATH)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", fake_exe)

    # 验证 compile_qt 寻找模板
    tmpl_path = compile_qt.default_template_path()
    assert tmpl_path.exists(), f"compile_qt 无法在模拟冻结环境下解析模板: {tmpl_path}"

    # 验证 registry 寻找数据
    reg_resolved = registry._resolve_data_path("universities/registry.json")
    assert reg_resolved.exists(), f"registry 无法在模拟冻结环境下解析数据: {reg_resolved}"


def _read_bat(path: Path) -> str:
    """[R2-C3] 启动脚本已改为 GBK(CP936) 落盘 + ``chcp 936``，不能再按 UTF-8 读。"""
    raw = path.read_bytes()
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def test_gui_bat_exit_feedback_polish():
    """验证 GUI.bat 退出行已美化为 [*]"""
    gui_bat = ROOT / "GUI.bat"
    content = _read_bat(gui_bat)
    assert "[*] GUI 客户端异常退出" in content, "GUI.bat 未应用统一的 [*] 反馈风格"
    assert "[!] GUI 客户端异常退出" not in content, "GUI.bat 仍存在遗留的 [!] 格式"


@pytest.mark.skipif(not DIST_READY, reason=DIST_SKIP_REASON)
def test_standalone_executable_smoke_launch():
    """在无干扰子进程中冒烟拉起独立 EXE，验证其具备真实运行时拉起能力且无即时崩溃"""
    import subprocess
    import time

    assert EXE_PATH.exists(), f"EXE 不存在: {EXE_PATH}"
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"

    proc = subprocess.Popen([str(EXE_PATH)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        time.sleep(3.0)
        poll = proc.poll()
        if poll is not None:
            stdout, stderr = proc.communicate()
            pytest.fail(
                f"独立 EXE 启动后异常退出 (退出码: {poll}):\n"
                f"STDOUT: {stdout.decode('utf-8', errors='replace')}\n"
                f"STDERR: {stderr.decode('utf-8', errors='replace')}"
            )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()

