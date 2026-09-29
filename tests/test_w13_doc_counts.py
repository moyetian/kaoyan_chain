# -*- coding: utf-8 -*-
"""W13-0 派生计数测试：C1 / C2 评测集条数的「数据 ↔ 文档」一致性。

背景（W13 方案 §2.1 修正 #1）：条数口径曾三源不一 —— ``README.md`` 写
「考纲守卫 128 条」、``tools/evaluate_pipeline.py`` 两处写 C2「108 条」、
``tools/ci_evaluate_gate.py`` 已是 137/109。修复后本测试以**数据文件实际
行数**为唯一真源（与 ``runner.load_cases`` 的「空行跳过」同口径），断言：

  * ``tests/benchmarks/syllabus_guard.jsonl`` 实际行数（当前 **137**）
    ↔ ``README.md`` 中的「考纲守卫 <N> 条」；
  * ``tests/benchmarks/citation_faithfulness.jsonl`` 实际行数（当前 **109**）
    ↔ ``README.md`` 中的「引文忠实度 <N> 条」；
  * 同一行数 ↔ ``tools/evaluate_pipeline.py`` 两处 C2 文档串
    （模块 docstring 的「（<N> 条，覆盖」与 ``evaluate_ragas_faithfulness``
    docstring 的「（C2：<N> 条，覆盖」）。

未来任何一侧增删样本或改文案而另一侧未同步，本测试立即变红。

刻意**不**纳入本契约：``CHANGELOG.md`` 的 v3.1.0 发布段（128 条 / 31.2% 是
发布时点的历史快照，不随数据增长回改；新口径说明写在「未发布」段）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SYLLABUS_FILE = ROOT / "tests" / "benchmarks" / "syllabus_guard.jsonl"
CITATION_FILE = ROOT / "tests" / "benchmarks" / "citation_faithfulness.jsonl"
README_FILE = ROOT / "README.md"
PIPELINE_FILE = ROOT / "tools" / "evaluate_pipeline.py"

#: 修复时点的真源行数（W13 开工实测）。数据增删时这里与两处文档必须同批更新。
C1_EXPECTED = 137
C2_EXPECTED = 109


def _count_data_lines(path: Path) -> int:
    """数据行数 = 非空行数（与 ``benchmarks.runner.load_cases`` 的容错口径一致）。"""
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def test_jsonl_line_counts_are_stable():
    """真源行数锁定：C1=137 / C2=109（数据增删必须与文档同批改，见下方断言）。"""
    assert _count_data_lines(SYLLABUS_FILE) == C1_EXPECTED, \
        f"C1 评测集行数变化：{_count_data_lines(SYLLABUS_FILE)} != {C1_EXPECTED}"
    assert _count_data_lines(CITATION_FILE) == C2_EXPECTED, \
        f"C2 评测集行数变化：{_count_data_lines(CITATION_FILE)} != {C2_EXPECTED}"


def test_readme_counts_match_data():
    """``README.md`` 的条数文案必须等于数据文件实际行数。

    定位：README「v3.1.0 亮点」段内写「考纲守卫 <N> 条 / 引文忠实度 <N> 条」。
    """
    text = README_FILE.read_text(encoding="utf-8")
    c1 = _count_data_lines(SYLLABUS_FILE)
    c2 = _count_data_lines(CITATION_FILE)
    assert f"考纲守卫 {c1} 条" in text, \
        f"README.md 未写「考纲守卫 {c1} 条」（当前数据行数）"
    assert f"引文忠实度 {c2} 条" in text, \
        f"README.md 未写「引文忠实度 {c2} 条」（当前数据行数）"
    # 旧值不得复现（防未来改数据时只改一半 / 回退到历史口径）
    assert "考纲守卫 128 条" not in text, "README.md 回退到旧口径「考纲守卫 128 条」"
    assert "引文忠实度 108 条" not in text, "README.md 出现旧口径「引文忠实度 108 条」"


def test_pipeline_doc_counts_match_data():
    """``tools/evaluate_pipeline.py`` 两处 C2 条数必须等于数据文件实际行数。

    定位：模块 docstring（``--ragas`` 条目）与 ``evaluate_ragas_faithfulness``
    docstring 各有一处「（[C2：]<N> 条，覆盖」。两处必须同源，不得只改一处。
    """
    text = PIPELINE_FILE.read_text(encoding="utf-8")
    c2 = _count_data_lines(CITATION_FILE)
    assert f"（{c2} 条，覆盖" in text, \
        f"evaluate_pipeline.py 模块 docstring 未写「（{c2} 条，覆盖」"
    assert f"（C2：{c2} 条，覆盖" in text, \
        f"evaluate_pipeline.py 函数 docstring 未写「（C2：{c2} 条，覆盖」"
    # 旧值不得复现
    assert "108 条" not in text, "evaluate_pipeline.py 回退到旧口径「108 条」"
