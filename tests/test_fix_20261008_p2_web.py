# -*- coding: utf-8 -*-
"""P2 批次 · Web 看板组修复回归（8 条，2026-10-08）。

对应六领域深度审查报告 §3 Web（8）条目：
  1. 未定义模板占位符静默残留 —— ``build.substitute_placeholders`` 收集未命中键
  2. vendor 抓取失败文案与实际不符 + 目录重复处理 —— 文案如实 + 目录去重
  3. ``.gitignore:191`` 无效规则 —— 删除死 negation（父目录已忽略，无法 re-include）
  4. ``color-mix()`` 无纯色 fallback —— 6 处同属性前置纯色声明
  5. ``theme_preset_gallery()`` 死代码 —— 删除（web 包内零调用者）
  6. ``check_dashboard.py`` 临时 JS 写仓库根 —— 改系统临时目录 + finally 清理
  7. ``renderTrend`` resize 无防抖 + 探针只测 390px —— 150ms 防抖 + 四档视口
  8. ``build_svg_assets.py`` 配图漂移（含 1 处 ``📑``）—— 五大页签结构 + 📑 归零

夹具全部为源码/产物级只读断言，不依赖真实考生数据、不落盘。
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "05-考研看板"
for _p in (str(ROOT), str(DASHBOARD), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build  # noqa: E402


# ════════════════════════════════════════════════════════════════
# 1. 未定义模板占位符：不再静默残留
# ════════════════════════════════════════════════════════════════

def test_substitute_placeholders_reports_missing_keys():
    """未定义占位符仍原样保留，但必须被如实收集（供构建告警）。"""
    html, missing = build.substitute_placeholders(
        "a {{FOO}} b {{BAR}} c {{FOO}}", {"FOO": "1"})
    assert html == "a 1 b {{BAR}} c 1"
    assert missing == ["BAR"]


def test_substitute_placeholders_all_defined_no_missing():
    html, missing = build.substitute_placeholders("x {{A}} y", {"A": "v"})
    assert html == "x v y"
    assert missing == []


def test_build_wires_missing_placeholder_warning():
    src = (DASHBOARD / "build.py").read_text(encoding="utf-8")
    assert "substitute_placeholders(load_template(), values)" in src
    assert "模板占位符未定义" in src, "build() 缺未命中占位符告警"


# ════════════════════════════════════════════════════════════════
# 2. vendor 失败文案如实 + 目标目录去重
# ════════════════════════════════════════════════════════════════

def test_vendor_failure_message_matches_offline_default():
    src = (DASHBOARD / "web" / "vendor.py").read_text(encoding="utf-8")
    assert "保留 CDN 引用即可" not in src, \
        "失败文案仍承诺回退 CDN（与默认离线引用本地路径的事实不符）"
    assert "产物仍引用本地路径" in src


def test_build_dedups_vendor_target_dirs():
    src = (DASHBOARD / "build.py").read_text(encoding="utf-8")
    assert "dict.fromkeys((OUT.parent, ROOT_DOCS.parent))" in src, \
        "OUT 与 ROOT_DOCS 同目录时仍会被重复抓取两遍"


# ════════════════════════════════════════════════════════════════
# 3. .gitignore 死 negation 删除（行为不变：仍被父目录规则忽略）
# ════════════════════════════════════════════════════════════════

def test_gitignore_dead_negation_removed():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "!05-考研看板/docs/state_snapshot.json" not in text


def test_dashboard_docs_snapshot_still_ignored():
    if not (ROOT / ".git").exists():
        pytest.skip("非 git 工作区")
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--no-index",
             "05-考研看板/docs/state_snapshot.json"],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
        pytest.skip("git 不可用")
    assert proc.returncode == 0, \
        "删除死 negation 后该路径必须仍被目录规则忽略（行为不变）"


# ════════════════════════════════════════════════════════════════
# 4. color-mix() 六处均有纯色 fallback
# ════════════════════════════════════════════════════════════════

_MIX_DECL = re.compile(r"(background|stroke)\s*:\s*[^;{}]*color-mix\([^;{}]*")


def test_color_mix_declarations_have_solid_fallback():
    """每处 color-mix 声明的同属性前一条声明必须是纯色（fallback 模式）。"""
    css = (DASHBOARD / "web" / "template.html").read_text(encoding="utf-8")
    mixes = list(_MIX_DECL.finditer(css))
    assert len(mixes) == 6, f"color-mix 声明数量变化（{len(mixes)}），请同步复核 fallback"
    for m in mixes:
        prop = m.group(1)
        head = css[:m.start()]
        prev = head.rfind(prop + ":")
        assert prev != -1, f"{prop} 的 color-mix 前无同属性 fallback 声明"
        prev_value = head[prev:].split(":", 1)[1]
        assert "color-mix" not in prev_value, \
            f"{prop} 的 color-mix 前一条同属性声明仍是 color-mix（fallback 失效）"
        assert prev_value.strip().strip(";").strip(), f"{prop} 的 fallback 值为空"


# ════════════════════════════════════════════════════════════════
# 5. theme_preset_gallery 死代码已删除
# ════════════════════════════════════════════════════════════════

def test_theme_preset_gallery_dead_code_removed():
    for rel in ("web/theme_vars.py", "web/__init__.py"):
        src = (DASHBOARD / rel).read_text(encoding="utf-8")
        assert "theme_preset_gallery" not in src, f"{rel} 仍残留死代码"


def test_web_package_has_no_gallery_export():
    import web as web_pkg
    assert not hasattr(web_pkg, "theme_preset_gallery")


# ════════════════════════════════════════════════════════════════
# 6. check_dashboard 临时 JS 出仓（系统临时目录 + 清理）
# ════════════════════════════════════════════════════════════════

def test_check_dashboard_temp_js_written_outside_repo(monkeypatch):
    import check_dashboard as cd

    seen = {}

    class _Proc:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        return _Proc()

    monkeypatch.setattr(cd.shutil, "which", lambda name: "node")
    monkeypatch.setattr(cd.subprocess, "run", fake_run)

    ok, msg = cd.check_js_syntax("<script>var a = 1;</script>", "docs/.local/index.html")
    assert ok, msg
    tmp_path = Path(seen["cmd"][-1])
    assert tmp_path.name.startswith("ky_dashboard_check_"), tmp_path.name
    assert not tmp_path.is_relative_to(cd.ROOT), "临时 JS 不得写在仓库内"
    assert not tmp_path.exists(), "临时 JS 未在 finally 中清理"


def test_check_dashboard_source_has_no_repo_root_temp_write():
    src = (ROOT / "tools" / "check_dashboard.py").read_text(encoding="utf-8")
    # 旧实现的精确表达式：tmp = ROOT / f".dashboard_check_{...}.js"
    assert 'ROOT / f".dashboard_check' not in src, "写仓库根的旧临时文件表达式仍残留"


# ════════════════════════════════════════════════════════════════
# 7. renderTrend 防抖 + 运行时探针四档视口
# ════════════════════════════════════════════════════════════════

def test_rendertrend_resize_is_debounced():
    tpl = (DASHBOARD / "web" / "template.html").read_text(encoding="utf-8")
    assert "window.addEventListener('resize', renderTrend)" not in tpl, \
        "resize 仍直接绑定 renderTrend（无防抖）"
    assert "_trendRzTimer" in tpl
    assert "clearTimeout(_trendRzTimer)" in tpl
    assert "setTimeout(renderTrend, 150)" in tpl


def test_probe_covers_four_viewports():
    src = (ROOT / "tools" / "check_dashboard.py").read_text(encoding="utf-8")
    assert 'result.get("overflows")' in src
    for w in ("360", "390", "768", "1280"):
        assert f"w: {w}" in src, f"探针缺 {w}px 视口档"
    assert 'result.get("overflow")' not in src, "旧的单档 overflow 判定仍残留"


def test_runtime_probe_js_is_syntactically_valid(tmp_path):
    import check_dashboard as cd
    if not shutil.which("node"):
        pytest.skip("本机无 Node.js")
    probe = tmp_path / "probe.js"
    probe.write_text("var __probe = " + cd.RUNTIME_PROBE_JS + ";\n", encoding="utf-8")
    proc = subprocess.run(["node", "--check", str(probe)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.stderr or "")[:400]


# ════════════════════════════════════════════════════════════════
# 8. build_svg_assets 配图：同源、📑 归零、页签结构如实
# ════════════════════════════════════════════════════════════════

SVG_PRODUCTS = (
    ("SVG1", "intelligence_architecture.svg"),
    ("SVG2", "school_comparator_matrix.svg"),
    ("SVG3", "dashboard_5tabs_architecture.svg"),
)


def _load_svg_script():
    spec = importlib.util.spec_from_file_location(
        "ky_p2_build_svg_assets", ROOT / "tools" / "build_svg_assets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_svg_script_and_products_have_no_bookmark_emoji():
    src = (ROOT / "tools" / "build_svg_assets.py").read_text(encoding="utf-8")
    assert "📑" not in src
    for _, name in SVG_PRODUCTS:
        text = (ROOT / "docs" / "assets" / name).read_text(encoding="utf-8")
        assert "📑" not in text, f"{name} 仍含 📑"


def _is_publish_copy() -> bool:
    """[2026-10-08 推送前核查] 发布副本判据：``tools/sync_publish.py`` 被
    neutralize 成 4 行英文占位。

    发布副本里 ``docs/assets/*.svg`` 产物经示例校名占位化改写（→「对比院校B」），
    而 ``build_svg_assets.py`` 是 .py 文件、静态表校名按设计豁免改写（见
    ``privacy_policy`` 豁免清单）——源脚本与产物在副本里必然不同形态，
    本用例的「漂移防护」只在真源仓库成立，副本里跳过（私有侧 CI 覆盖）。
    """
    sp = ROOT / "tools" / "sync_publish.py"
    try:
        text = sp.read_text(encoding="utf-8")
    except OSError:
        return False
    # 注意：不可用「特征串 in text」判据 —— 主仓库 sync_publish.py 的
    # neutralize_sync_script() 生成器里本身就含该占位文本，会自身命中。
    non_empty = [ln for ln in text.splitlines() if ln.strip()]
    return len(non_empty) <= 5


def test_svg_products_match_source_script():
    if _is_publish_copy():
        pytest.skip("发布副本：SVG 产物经占位化改写（.py 源豁免），字节比对不适用")
    mod = _load_svg_script()
    for attr, name in SVG_PRODUCTS:
        expected = getattr(mod, attr)
        actual = (ROOT / "docs" / "assets" / name).read_text(encoding="utf-8")
        assert actual == expected, f"{name} 与源脚本漂移（改了脚本必须重跑）"


def test_svg3_reflects_five_tab_product_structure():
    svg = (ROOT / "docs" / "assets" / "dashboard_5tabs_architecture.svg").read_text(
        encoding="utf-8")
    for marker in ("五大核心交互页签", "🎯 错题 (Weak)", "📊 进度 (Stats)",
                   "📡 考情 (Radar)", "🗺️ 知识图谱二级入口"):
        assert marker in svg, f"SVG3 缺 {marker}"
    for stale in ("六大核心交互页签", "🎯 薄弱 (Radar)", "📊 数据 (Data)",
                  "🗺️ 图谱 (Map)"):
        assert stale not in svg, f"SVG3 仍含旧结构 {stale}"


def test_dashboard_readme_matches_five_tab_product():
    md = (DASHBOARD / "README.md").read_text(encoding="utf-8")
    for marker in ("五大核心交互页签与知识图谱图解",
                   "🎯 **错题**<br>*(Weak & Queue)*",
                   "📊 **进度**<br>*(Stats)*",
                   "🗺️ **知识图谱**<br>*(Map · 进度页二级入口)*"):
        assert marker in md, f"README 缺 {marker}"
    assert "六大核心交互页签" not in md
