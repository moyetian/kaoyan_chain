# -*- coding: utf-8 -*-
"""# [Regress] 发布豁免守卫：``PY_UNSANITIZED_FILES`` 内的 .py 不得被脱敏改写。

迁移自 tests/test_fix_publish_privacy.py（原 R2-C2 边界补漏用例；
2026-10-12 拆模块适配：未收录高校合成器随 KNOWN_REGIONAL 拆入
registry_synth.py，守卫参数化覆盖两个豁免文件，并迁入分层目录 ——
原文件受 review_gate R3 ratchet 约束，迁出以回落到基线内）。
"""

import re
import shutil
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sync_publish as sp


@pytest.mark.parametrize("rel, probe", [
    # 探测串用相邻字面量拼接：本文件受 tests/ 脱敏免疫元测试扫描，
    # 完整校名连续出现会被真实规则集改写（动态规则随 ky_config 变化，
    # 两个串都拆开才稳健）；运行时值不受影响。
    ("tools/intelligence/registry.py", "华中科技" "大学"),
    ("tools/intelligence/registry_synth.py", "河南农业" "大学"),
], ids=["registry", "registry_synth"])
def test_registry_py_is_exempt_from_py_sanitization(
        rel: str, probe: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """R2-C2 边界补漏：公开高校映射表不得被 ``*.py`` 脱敏改动。

    privacy_policy 的文档边界早已写明「registry.py 的『校名 → 院校代码』
    映射也不得脱敏」，但此前**没有任何代码执行它**。实测发布副本里
    ``KNOWN_REGIONAL`` 的

        ``"中国人民大学": ("10466", "河南郑州", ...)``

    被替换成 ``"目标院校": ("10466", ...)`` —— 公开高校兜底索引整条改坏，
    校名解析失配，还凭空多出一个叫「目标院校」的学校。

    [2026-10-12 拆模块适配] 未收录高校合成器连同 KNOWN_REGIONAL 拆入
    registry_synth.py（同为 ``PY_UNSANITIZED_FILES`` 豁免）。守卫参数化覆盖
    两个文件，探测串取各文件内实际存在的公开校名 —— 两文件都被显式跳过才
    算通过（任一被脱敏改动即变红）。
    """
    src = ROOT / rel
    if not src.exists():
        pytest.skip(f"本工作区没有 {rel}")
    if probe not in src.read_text(encoding="utf-8"):
        pytest.skip(f"{rel} 未含探测串，守卫会空转")

    dst = tmp_path / "dst"
    target = dst / rel
    target.parent.mkdir(parents=True)
    shutil.copy2(src, target)
    # 对照组：同目录下普通 .py 必须照常脱敏（证明跳过是精准的，不是整体空转）
    other = dst / "tools" / "ordinary.py"
    other.write_text(f'SCHOOL = "{probe}"\n', encoding="utf-8")

    # 造一条**必然命中**的规则：证明豁免文件是被显式跳过，而非碰巧没命中
    synthetic = [(re.escape(probe), "目标院校")]
    monkeypatch.setattr(sp, "DST", dst)
    monkeypatch.setattr(sp, "SUBSTITUTIONS", synthetic)
    monkeypatch.setattr(sp, "PY_SUBSTITUTIONS", synthetic)
    sp.sanitize_markdown_files()

    assert probe not in other.read_text(encoding="utf-8"), \
        "对照组的普通 .py 未被脱敏，本测试失去意义"
    assert target.read_bytes() == src.read_bytes(), \
        f"公开高校映射表 {rel} 被脱敏改动（校名→目标院校），校名解析会失配"
