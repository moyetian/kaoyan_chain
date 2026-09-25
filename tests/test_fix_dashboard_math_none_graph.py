# -*- coding: utf-8 -*-
"""P5 残留：不考数学考生的看板，知识图谱面板仍出现「数学」tab / ``maps.math``。

背景
----
``web/config.py`` 已在不考数学时把 ``math`` 从 ``SUBJECTS``/``SECTIONS`` 剔除，
但知识图谱面板是另一条链路，残留两处：

1. ``web/template.html`` 的图谱 IIFE **硬编码**了 4 个 tab（数学/英语/政治/专业课）
   且默认 ``currSubj='math'`` —— 文科考生打开看板仍会看到数学页签、甚至默认展示
   数学知识图谱。
2. ``build.py`` 无条件为 ``("math", "eng", "pol", "pro")`` 构建图谱，52 个数学
   考点数据仍被塞进产物。

修复后：tab 由内联数据 ``D.subjects``（已按报考科目过滤）∩ ``D.maps`` 驱动，
默认选中第一门可用科目；``build.py`` 只为 ``SUBJECTS`` 里实际存在的科目构建图谱。

测试策略
--------
图谱 tab 由浏览器端 JS 渲染，产物 HTML 里不会出现静态 tab 标记，故分两层断言：

* **数据层（始终执行）**：解析产物内联的 ``var D = {...}``，断言 ``subjects`` /
  ``maps`` 的键集 —— 这是 tab 的唯一数据来源，也是 ``maps.math`` 是否泄漏的铁证。
* **运行时层（本机有 Node.js 时执行）**：用 Node 真正跑一遍产物里的图谱 IIFE
  （配最小 DOM 桩），断言渲染出的 chip 列表与默认选中项。

隔离
----
所有构建都在 ``tmp_path`` 里以**子进程**跑 ``build.py``：子进程拥有干净的
``sys.modules``，可避免 pytest 同进程内 ``web`` / ``tools`` 包已被真实仓库占位
导致的串味。``knowledge_map`` / ``error_logger`` 按真实文件复制进 tmp 工作区
（二者仅依赖标准库），其 ``ROOT`` 由 ``__file__`` 反推即落在 tmp 工作区，
因此读的是 tmp 里的考纲、写的是 tmp 里的 docs —— 主仓库的 ``ky_config.json``、
各科 ``_状态/`` 与 ``docs/`` 一概不碰。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "05-考研看板"

#: 考纲需 ≥5 行「有效正文」才不被 knowledge_map 判为未填写的占位模板
_SYLLABUS = """# 考试大纲

## 第一章 基础模块

- **掌握**：核心概念甲、核心概念乙、核心概念丙
- **理解**：方法丁、方法戊
- **了解**：拓展己、拓展庚、拓展辛
- **掌握**：综合应用壬、综合应用癸
- **理解**：附加考点子、附加考点丑
- **了解**：背景知识寅、背景知识卯
"""

_SUBJECT_DIRS = ("01-数学", "02-英语", "03-思想政治理论", "04-专业课")


def _rmtree(path: Path) -> None:
    """Windows 上被复制过来的只读文件会让 rmtree 失败，逐个放开权限重试。"""
    import stat

    def _onexc(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    if path.exists():
        shutil.rmtree(path, onexc=_onexc)


def _make_workspace(tmp_path: Path, math_key: str, extra_plan: dict | None = None) -> Path:
    """搭一个隔离的最小工作区（tmp_path 即工作区根）。"""
    board = tmp_path / "05-考研看板"
    board.mkdir(parents=True)
    shutil.copy2(DASHBOARD / "build.py", board / "build.py")
    shutil.copytree(DASHBOARD / "web", board / "web",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # 只搬运 knowledge_map 链路真正需要的模块（均为纯标准库 / 已声明的三方依赖），
    # 避免拉起 tools.skills 的全部 15 个子模块（sympy / pypdf 等）。
    #   knowledge_map → error_logger → ky_io / note_lock / fsrs_scheduler
    #                                → question_source（题源身份校验，C3；依赖 ky_io）
    # tools.skills/__init__.py 故意留空：真实那份会 eager 导入全部技能，
    # 而 knowledge_map 只需同级 error_logger，get_subject_name 缺失时它自带回退。
    skills = tmp_path / "tools" / "skills"
    skills.mkdir(parents=True)
    shutil.copy2(REPO / "tools" / "__init__.py", tmp_path / "tools" / "__init__.py")
    for name in ("ky_io.py", "note_lock.py", "fsrs_scheduler.py"):
        shutil.copy2(REPO / "tools" / name, tmp_path / "tools" / name)
    (skills / "__init__.py").write_text("", encoding="utf-8")
    for name in ("knowledge_map.py", "error_logger.py", "question_source.py"):
        shutil.copy2(REPO / "tools" / "skills" / name, skills / name)

    plan = {
        "math_key": math_key,
        "math_name": "不考数学" if math_key == "none" else "数学二 (302)",
        "eng_name": "英语一 (201)",
        "pro_name": "马克思主义理论",
        "exam_date": "2026-12-19",
    }
    plan.update(extra_plan or {})
    (tmp_path / "ky_config.json").write_text(
        json.dumps({"study_plan": plan}, ensure_ascii=False), encoding="utf-8")

    for folder in _SUBJECT_DIRS:
        (tmp_path / folder / "_状态").mkdir(parents=True, exist_ok=True)
        (tmp_path / folder / "考试大纲.md").write_text(_SYLLABUS, encoding="utf-8")
    return tmp_path


def _build(ws: Path) -> str:
    """在隔离工作区里以子进程跑一次 build.py，返回产物 HTML。"""
    env = dict(os.environ)
    # 只把 tmp 工作区放上 PYTHONPATH：web / tools 都必须解析到 tmp 副本，
    # 否则会串到真实仓库（pytest 进程里这些包可能已被其它测试导入）。
    env["PYTHONPATH"] = str(ws)
    env["PYTHONIOENCODING"] = "utf-8"
    env["KY_VENDOR_MODE"] = "cdn"     # 免去离线模式的 vendor 下载
    env["KY_SNAPSHOT_OPT_IN"] = "1"   # 发布态（脱敏），与线上产物一致

    proc = subprocess.run(
        [sys.executable, str(ws / "05-考研看板" / "build.py"), "--cdn"],
        cwd=str(ws), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    assert proc.returncode == 0, f"build.py 失败：\n{proc.stdout}\n{proc.stderr}"
    return (ws / "05-考研看板" / "docs" / "index.html").read_text(encoding="utf-8")


def _inline_data(html: str) -> dict:
    """从产物里取回内联的 ``var D = {...};``。"""
    m = re.search(r"^var D = (.*);$", html, re.M)
    assert m, "产物里找不到内联数据 `var D = {...};`"
    return json.loads(m.group(1))


def _graph_tab_source(html: str) -> str:
    """产物里图谱 tab 的推导表达式（供断言其确实数据驱动）。"""
    m = re.search(r"var subjs = (.*);", html)
    assert m, "产物里找不到图谱 tab 推导语句"
    return m.group(1)


# ── 运行时层：用 Node 真跑一遍产物里的图谱 IIFE ─────────────────────
_NODE_HARNESS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const dm = html.match(/^var D = (.*);$/m);
if (!dm) { console.error('NO_INLINE_DATA'); process.exit(2); }
const D = JSON.parse(dm[1]);

// 抽取产物里图谱 IIFE 的主体（从 `var maps = D.maps` 到该 IIFE 收尾）
const s = html.indexOf('var maps = D.maps || {};');
const e = html.indexOf('})();', s);
if (s < 0 || e < 0) { console.error('NO_GRAPH_IIFE'); process.exit(3); }
const code = '(function(){' + html.slice(s, e) + '})();';

const els = {};
function mkEl() { return { innerHTML: '', querySelectorAll() { return []; } }; }
const document = { getElementById(id) { return els[id] || (els[id] = mkEl()); } };
function esc(x) { return String(x == null ? '' : x); }
function tex() {}

eval(code);

console.log(JSON.stringify({
  dk: els['dk-map'] ? els['dk-map'].innerHTML : '',
  tree: els['map-tree'] ? els['map-tree'].innerHTML : '',
}));
"""


def _run_graph_js(ws: Path, html: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("本机无 Node.js，跳过图谱 tab 的前端运行时校验")
    harness = ws / "graph_harness.js"
    harness.write_text(_NODE_HARNESS, encoding="utf-8")
    idx = ws / "05-考研看板" / "docs" / "index.html"
    proc = subprocess.run([node, str(harness), str(idx)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)
    assert proc.returncode == 0, f"Node 运行图谱 IIFE 失败：{proc.stderr}"
    return json.loads(proc.stdout)


def _chips(dk_html: str):
    """从 chip 容器 HTML 里解析出 (科目键, 是否默认选中) 列表。"""
    out = []
    for chunk in re.findall(r"<div class='chip( on)?'[^>]*data-s='([a-z0-9]+)'", dk_html):
        out.append((chunk[1], chunk[0].strip() == "on"))
    return out


# ── 阴性对照：none vs math2 ─────────────────────────────────────
@pytest.mark.parametrize("math_key, expect_math", [("none", False), ("math2", True)])
def test_graph_data_matches_enrolled_subjects(tmp_path, math_key, expect_math):
    """不考数学：产物不得含 maps.math，也不得把 math 列为图谱 tab 数据源。"""
    ws = _make_workspace(tmp_path, math_key)
    html = _build(ws)
    data = _inline_data(html)

    subject_keys = [s["key"] for s in data["subjects"]]
    map_keys = list(data["maps"])

    assert ("math" in subject_keys) is expect_math, f"subjects={subject_keys}"
    assert ("math" in map_keys) is expect_math, f"maps={map_keys}"
    if expect_math:
        assert data["maps"]["math"]["total_points"] > 0, "数学考生的图谱不应为空"
    else:
        assert subject_keys == ["eng", "pol", "pro"], subject_keys
        assert map_keys == ["eng", "pol", "pro"], map_keys


def test_graph_tabs_are_data_driven_not_hardcoded(tmp_path):
    """图谱 tab 必须由内联数据推导，且不再硬编码 4 科/默认数学。"""
    html = _build(_make_workspace(tmp_path, "none"))

    source = _graph_tab_source(html)
    assert "D.subjects" in source, f"图谱 tab 未由 D.subjects 驱动: {source}"
    assert "D.maps" in html

    assert "{key:'math', name:'数学'" not in html, "产物里仍硬编码数学 tab"
    assert "var subjs = [" not in html, "产物里仍是硬编码的 tab 数组"
    # 默认选中第一门可用科目，而非写死 'math'
    assert re.search(r"var currSubj = subjs\.length \? subjs\[0\]\.key", html), \
        "默认选中科目未按「第一门可用科目」推导"


@pytest.mark.parametrize("extra_plan, expect_subjects, expect_maps", [
    ({"pro2_name": "专业课二"}, ["eng", "pol", "pro", "pro2"], {"eng", "pol", "pro"}),
    ({"exam_mode": "mode_c", "pro_name": "199 管理类综合能力"}, ["pro", "eng"], {"pro", "eng"}),
])
def test_mode_b_c_graph_maps_stay_in_sync(tmp_path, extra_plan, expect_subjects, expect_maps):
    """mode_b/mode_c 同样不考数学；且未知科目键（pro2）不得传给 knowledge_map
    —— 它只认 math/eng/pol/pro，传 pro2 会回退到 01-数学 目录产出错误图谱。"""
    ws = _make_workspace(tmp_path, "none", extra_plan)
    data = _inline_data(_build(ws))
    assert [s["key"] for s in data["subjects"]] == expect_subjects
    assert set(data["maps"]) == expect_maps
    assert "math" not in data["maps"]


@pytest.mark.parametrize("math_key, expect_chips, expect_default", [
    ("none", [("eng", True), ("pol", False), ("pro", False)], "eng"),
    ("math2", [("math", True), ("eng", False), ("pol", False), ("pro", False)], "math"),
])
def test_graph_tab_runtime_rendering(tmp_path, math_key, expect_chips, expect_default):
    """用 Node 跑产物里的图谱 IIFE：chip 列表与默认选中项都要正确。"""
    ws = _make_workspace(tmp_path, math_key)
    html = _build(ws)
    rendered = _run_graph_js(ws, html)

    chips = _chips(rendered["dk"])
    assert chips == expect_chips, f"math_key={math_key} 渲染出的 chip: {chips}"
    if math_key == "none":
        assert "data-s='math'" not in rendered["dk"], "不考数学却渲染出数学 tab"
    on = [k for k, is_on in chips if is_on]
    assert on == [expect_default], f"默认选中应为 {expect_default}，实际 {on}"
