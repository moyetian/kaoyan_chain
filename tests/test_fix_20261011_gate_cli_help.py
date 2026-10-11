# -*- coding: utf-8 -*-
"""R12 修复回归：ingest 最小可信度门禁（G4-2）+ CLI 失败退出码（G4-1）+
``ky help diff`` 别名（G4-3）。

来源：第四轮三方审查报告（2026-10-11）第三节小瑕疵 ——
  * G4-1 ``ky ingest`` 空文件 / 解码失败返回 rc=0，脚本化无法判成败；
  * G4-2 垃圾文本（随笔 / 乱码）也能切成"大题"入库，无最小可信度门禁；
  * G4-3 ``ky help diff`` 不认自述别名（``ky diff`` 本体可用）。

覆盖：
  1. 门禁：无题号/标记的垃圾单题拒收（含提示文案），正常单题放行
  2. 门禁边界：有题号 / 有分值 / 有作答动词 / 有分段标题 / 空 stem（TOC）
     一律放行（防过度拦截误杀正常材料）
  3. 门禁端到端：ingest_text 对垃圾文本返回 success=False + 可读 msg
  4. CLI 退出码：空文件 ingest → rc=1；正常入库路径不受影响（单元级验证）
  5. ``ky help diff`` / ``ky commands diff`` → rc=0 且解析到 fetch

CLI 用例经 subprocess 跑真实入口（隔离于 tmp_path 的空文件；失败路径不写
工作区）；其余用例隔离在 tmp_path、不联网。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools.skills.material_ingestion import MaterialIngestionPipeline  # noqa: E402

_GARBAGE_ESSAY = "今天天气不错，我去了图书馆，看了会儿书，感觉心情很好，晚上准备去吃火锅。"
_GARBAGE_MOJIBAKE = "asdjkhaskjdhkajshdkjahsd kasjdhkajshd kjahsd kjhasd"
_TOC_TEXT = "目  录\n第一章 函数、极限、连续 ................. 1\n第二章 导数与微分 ....................... 12"


def _pipe(tmp_path) -> MaterialIngestionPipeline:
    p = MaterialIngestionPipeline(workspace_root=tmp_path)
    p._force_python = True  # 固定纯 Python 路径，排除 Rust 扩展有无的环境差异
    return p


# ═════════════════ 1. 门禁：垃圾拒收 ═════════════════

def test_garbage_essay_rejected_with_hint(tmp_path, capsys):
    """[红线] 随笔文本：无题号/信号 → 拒收 + 提示补救路径。"""
    p = _pipe(tmp_path)
    chunks = p.chunk_text(_GARBAGE_ESSAY, "probe")
    assert chunks == []
    out = capsys.readouterr().out
    assert "未检测到题目结构" in out, out
    assert "请保留题号" in out, out


def test_garbage_mojibake_rejected(tmp_path):
    """乱码文本同样拒收。"""
    p = _pipe(tmp_path)
    assert p.chunk_text(_GARBAGE_MOJIBAKE, "probe") == []


def test_garbage_ingest_end_to_end_fails(tmp_path):
    """端到端：ingest_text 对垃圾文本返回 success=False（不再"成功入库 1 道"）。"""
    p = _pipe(tmp_path)
    res = p.ingest_text(_GARBAGE_ESSAY, subject="pro", source_name="垃圾材料")
    assert res["success"] is False
    assert res["count"] == 0
    assert res["msg"]


# ═════════════════ 2. 门禁边界：正常材料一律放行 ═════════════════

def test_single_question_with_score_passes(tmp_path):
    """带分值的无题号单题：正常放行。"""
    p = _pipe(tmp_path)
    chunks = p.chunk_text(
        "试述生产力与生产关系的辩证关系，并说明其对全面深化改革的指导意义。（15 分）",
        "probe")
    assert len(chunks) == 1
    assert "生产力" in chunks[0].stem


def test_single_question_with_verb_passes(tmp_path):
    """带作答动词（简述）的无题号单题：正常放行。"""
    p = _pipe(tmp_path)
    chunks = p.chunk_text(
        "简述实践是检验真理的唯一标准及其在新时代全面深化改革中的现实指导意义。",
        "probe")
    assert len(chunks) == 1


def test_numbered_single_question_passes(tmp_path):
    """有题号的单题：放行（材料自带结构）。"""
    p = _pipe(tmp_path)
    chunks = p.chunk_text("1. 题目A\n【答案】A", "probe")
    assert len(chunks) == 1
    assert chunks[0].answer and "A" in chunks[0].answer


def test_chinese_numbered_question_form_passes(tmp_path):
    """[红线·代码审查补漏] `第N题` 形态单题必须放行（两路径一致）。

    背景：切片器的 q_pattern 认 `第\\s*N\\s*题` 为题号边界（单题正常切出），
    但门禁的结构判据 _NUMBERING_RE 曾漏掉该分支 —— 无分值、无信号词的
    `第1题：求极限…` 被误判"无题目结构"拒收（审查实测两路径均 0 题）。
    """
    p = _pipe(tmp_path)  # 纯 Python 路径
    p_default = MaterialIngestionPipeline(workspace_root=tmp_path)  # 默认（Rust）路径
    for text in ("第1题：求极限 lim(x->0) sin(x)/x。",
                 "第 2 题 求函数 y=x^2 的单调区间",
                 "第3题 简述实践与认识的辩证关系"):
        assert len(p.chunk_text(text, "probe")) == 1, text
        assert len(p_default.chunk_text(text, "probe")) == 1, text
    # 阴性：`第一章` 等章节形态不因该分支被误当题号（目录页仍走 TOC 归一/门禁）
    assert MaterialIngestionPipeline._has_question_structure("第一章 函数、极限、连续 ................. 1") is False


def test_section_headed_noun_explanation_passes(tmp_path):
    """分段标题 + 无作答动词的名词解释（马理论常见形态）：放行。"""
    p = _pipe(tmp_path)
    chunks = p.chunk_text(
        "一、简答题\n生产力的含义是指人们在生产中形成的人与自然的关系，"
        "包括劳动者、劳动资料和劳动对象三个要素。",
        "probe")
    assert len(chunks) == 1


def test_toc_placeholder_still_returned(tmp_path):
    """既有契约：TOC 过滤后的空 stem 占位不参与门禁（ky_suite 21-10 钉住）。"""
    p = _pipe(tmp_path)
    chunks = p.chunk_text(_TOC_TEXT, "TOC Test")
    assert len(chunks) == 1  # 空 stem 占位（不抛异常即既有断言口径）
    assert chunks[0].stem == ""


def test_toc_page_consistent_across_rust_and_python_paths(tmp_path):
    """[红线] 纯目录页在默认路径（Rust 加速器）与纯 Python 参考路径产物一致。

    背景：Rust 切片器无 Python 参考实现的目录行过滤，曾把整页目录当题干；
    下游 G4-2 门禁在 Rust 环境因此把 TOC 文本拒收为 0 题、Python 环境却豁免
    空 stem 占位返回 1 题 —— 同一文本在有无加速器环境下行为不同（ky_suite
    21-10 在 Rust 环境变红的实锤根因）。统一出口按 _is_toc_text 归一为占位后，
    两路径必须逐项一致。
    """
    p_default = MaterialIngestionPipeline(workspace_root=tmp_path)  # 不强制 → 本机走 Rust
    p_py = _pipe(tmp_path)
    r_default = p_default.chunk_text(_TOC_TEXT, "TOC Test")
    r_py = p_py.chunk_text(_TOC_TEXT, "TOC Test")
    assert len(r_default) == len(r_py) == 1, (len(r_default), len(r_py))
    assert r_default[0].stem == "" and r_py[0].stem == ""
    assert r_default[0].q_type == r_py[0].q_type == "essay"


def test_toc_page_with_real_questions_parses_questions(tmp_path):
    """[红线] 目录页 + 真题（PDF 转文本常见形态）：正常切出题目，目录不成题卡。

    两路径都只应切出 2 道题，且题干不含目录行残片（「第一章」「.....」）。
    """
    mixed = ("目  录\n第一章 函数、极限、连续 ................. 1\n"
             "第二章 导数与微分 ....................... 12\n\n"
             "1. 求极限 lim(x->0) sin(x)/x。（10 分）\n【答案】1\n\n"
             "2. 求导数 f(x)=x^2 的导函数。（10 分）\n【答案】2x\n")
    for force_python in (False, True):
        p = MaterialIngestionPipeline(workspace_root=tmp_path)
        p._force_python = force_python
        chunks = p.chunk_text(mixed, "混合材料")
        assert len(chunks) == 2, (force_python, [(c.number, c.stem) for c in chunks])
        for c in chunks:
            assert "第一章" not in c.stem and "....." not in c.stem, (force_python, c.stem)
        assert "求极限" in chunks[0].stem and "求导数" in chunks[1].stem


def test_question_signal_helpers_unit(tmp_path):
    """两个判据助手的最小契约（防未来把判据改宽/改窄而无人察觉）。"""
    from tools.skills.material_ingestion import QuestionChunk
    assert MaterialIngestionPipeline._has_question_structure("1. 题干") is True
    assert MaterialIngestionPipeline._has_question_structure("一、简答题\n题干") is True
    assert MaterialIngestionPipeline._has_question_structure(_GARBAGE_ESSAY) is False
    assert MaterialIngestionPipeline._looks_like_question(
        QuestionChunk(number=1, q_type="essay", score=10, stem="（15 分）题干")) is True
    assert MaterialIngestionPipeline._looks_like_question(
        QuestionChunk(number=1, q_type="essay", score=10, stem="今天天气不错")) is False


# ═════════════════ 3. CLI 退出码与别名（subprocess 真入口） ═════════════════

def _run_ky(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "ky_cli.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(cwd or ROOT), timeout=120,
    )


def test_cli_ingest_empty_file_exit_code_1(tmp_path):
    """[红线] G4-1：空文件 ingest 必须以非零码退出（脚本化可判成败）。"""
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    cp = _run_ky("ingest", str(empty))
    assert cp.returncode == 1, f"rc={cp.returncode}\n{cp.stdout}\n{cp.stderr}"
    assert "未完成" in cp.stdout or "未完成" in cp.stderr


def test_cli_help_diff_resolves_to_fetch(tmp_path):
    """[红线] G4-3：ky help diff 解析到 fetch 帮助（rc=0），不再"未知参数"。"""
    cp = _run_ky("help", "diff")
    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stdout}"
    assert "未知参数" not in cp.stdout
    assert "fetch" in cp.stdout


def test_cli_commands_diff_resolves_to_fetch():
    """ky commands diff 同样解析到 fetch。"""
    cp = _run_ky("commands", "diff")
    assert cp.returncode == 0, f"rc={cp.returncode}\n{cp.stdout}"
    assert "未知参数" not in cp.stdout


def test_registry_diff_alias_points_to_fetch():
    """单元级：注册表查询 diff 命中 fetch（别名登记的最小契约）。"""
    from tools.cli.dispatch import get_command
    cmd = get_command("diff")
    assert cmd is not None and cmd.name == "fetch"
