# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（路径无关）。

本文件必须保持「换台机器就能跑」：早期版本把作者本机的盘符绝对路径
（形如 ``<盘符>:/Users/<用户名>/Desktop/...``）写死进了 datas / Analysis /
EXE 三处，导致其它用户 clone 后打包直接失败或打出一份指向不存在目录的包。
这里统一改为基于 ``SPECPATH``（PyInstaller 执行 spec 时注入的 spec 所在目录）
推导仓库根目录，不再出现任何本机路径。
"""

import os
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

# PyInstaller 在 exec spec 时会注入 SPECPATH（本 spec 文件所在目录 = 仓库根目录）
ROOT = os.path.abspath(SPECPATH)


def _p(*parts):
    return os.path.join(ROOT, *parts)


datas = [
    (_p('data'), 'data'),
    (_p('docs'), 'docs'),
    (_p('01-数学'), '01-数学'),
    (_p('02-英语'), '02-英语'),
    (_p('03-思想政治理论'), '03-思想政治理论'),
    (_p('04-专业课'), '04-专业课'),
    (_p('05-考研看板'), '05-考研看板'),
    (_p('tools'), 'tools'),
    (_p('AGENTS.md'), '.'),
    (_p('GEMINI.md'), '.'),
    (_p('README.md'), '.'),
    (_p('00_考研全科总战役规划.example.md'), '.'),
]
binaries = []
hiddenimports = ['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'PySide6.QtNetwork', 'fsrs', 'filelock', 'yaml', 'sympy', 'pypdf', 'PIL', 'certifi', 'requests', 'httpx', 'gzip', 'ssl', 'ctypes', 'urllib.request', 'urllib.parse', 'http.client', 'tools.gui.main_window', 'tools.gui.services.settings', 'tools.gui.services.dashboard', 'tools.gui.widgets.onboarding_wizard', 'tools.gui.widgets.settings_dialog', 'tools.gui.widgets.wechat_search_dialog', 'tools.gui.workers.agent_worker', 'tools.gui.workers.intel_worker', 'tools.intelligence.agentic_research', 'tools.intelligence.registry', 'tools.intelligence.comparator', 'tools.intelligence.scout_engine', 'tools.intelligence.watcher', 'tools.intelligence.syllabus_diff', 'tools.skills.wechat_searcher', 'tools.skills.school_scout', 'tools.skills.material_ingestion', 'tools.skills.vision_solver', 'tools.skills.error_logger', 'tools.search.service', 'tools.search.providers.bing', 'tools.search.providers.ddg', 'tools.search.providers.sogou', 'tools.search.providers.tavily']
datas += collect_data_files('certifi')
hiddenimports += collect_submodules('PySide6')
tmp_ret = collect_all('tools')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    [_p('tools', 'ky_gui.py')],
    pathex=[_p('tools'), ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KaoyanStudyChain',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[_p('docs', 'assets', 'logo', 'favicon.ico')],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='KaoyanStudyChain',
)
