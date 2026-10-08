# -*- coding: utf-8 -*-
"""第三轮审查报告（2026-10-08）修复回归测试。

覆盖三项：
  A1       输出预算注释与常量一致（不再写死「400k」；引用常量名并钉住当前值）；
  小问题1   判卷「未能回写复测状态」按真实原因分类归因（缺题干 / 写入异常 / 回写关闭），
           不再一律误导为「请检查权限后重试」；
  小问题2   ``get_llm_config`` 兼容 ``"api"`` 嵌套配置（顶层平铺优先 +
           同名字段/``key``/``url`` 短别名回退；``api`` 非对象安全降级）。

全部使用合成数据（示例题 / 示例作答 / 占位 URL），不含任何真实身份信息。
"""
import json
from pathlib import Path
from types import SimpleNamespace

from tools.skills import exam_composer as exam
from tools.llm_client import get_llm_config, is_llm_configured

ROOT = Path(__file__).resolve().parent.parent


# ───────────── A1：输出预算注释与常量一致 ─────────────

def test_a1_comments_no_longer_hardcode_wrong_value():
    """A1：两个源码文件的注释不得再写死「400k」（与实际 50 KB 不符）。

    注释是用户/维护者理解默认预算的第一入口；写死的错误数值会直接误导
    「输出为什么被截断」的排查。修复后统一引用常量名。
    """
    for rel in ("tools/agent/output_budget.py", "tools/agent/tools_impl.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "400k" not in src, f"{rel} 仍含与实际值不符的「400k」注释"
    src = (ROOT / "tools/agent/tools_impl.py").read_text(encoding="utf-8")
    assert "DEFAULT_TOOL_OUTPUT_BUDGET" in src, "tools_impl 注释应引用常量名"


def test_a1_constant_value_matches_comment():
    """A1：常量值钉住 50 KB（51200 字符）——改值时本测试先红，提醒同步注释。"""
    from tools.agent.output_budget import DEFAULT_TOOL_OUTPUT_BUDGET
    assert DEFAULT_TOOL_OUTPUT_BUDGET == 50 * 1024


# ───────────── 小问题1：判卷未回写复测状态的如实归因 ─────────────

def _paper(keys):
    """内嵌密钥试卷（与既有判卷测试同款夹具）。"""
    return "<!-- EXAM_ANSWER_KEYS: " + json.dumps(keys) + " -->"


def _fake_logger(writes, fail_write=False):
    """记录回写调用的假错题本；``fail_write=True`` 时模拟写入异常。"""
    def _log(**kw):
        if fail_write:
            raise RuntimeError("磁盘写入被拒绝（示例异常）")
        writes.append(kw)
        return "已写入示例记录"
    return SimpleNamespace(
        scan_error_records=lambda subject=None: [],
        log_error_record=_log,
        mark_error_status=lambda **kw: (False, "无既有记录"))


def test_unarchived_no_question_reports_honestly(monkeypatch):
    """缺题干：未通过题没有 question 字段 → 归档没有锚点。
    报告须如实说「缺少题干」+ 指出题号，且**不得**再误导「请检查权限后重试」。
    """
    writes = []
    monkeypatch.setattr(exam, "error_logger", _fake_logger(writes))
    keys = [{"id": 1, "title": "示例题一", "standard_answer": "A", "score": 5}]
    result = exam.grade_exam_paper(_paper(keys), "1. B")
    report = result["report"]
    assert "缺少题干" in report, report
    assert "第 1 题" in report, "须指出具体题号"
    assert "请检查权限后重试" not in report, "缺题干与权限无关，不得误导"
    assert "请补充题干后重新判卷" in report, "须给可操作指引"
    assert result["updated_records"] == []


def test_unarchived_write_error_reports_permission(monkeypatch):
    """写入异常：须如实说「写入异常」并保留「检查权限后重试」指引（此场景下正确）。"""
    writes = []
    monkeypatch.setattr(exam, "error_logger", _fake_logger(writes, fail_write=True))
    keys = [{"id": 1, "title": "示例题一", "question": "示例题干一",
             "standard_answer": "A", "score": 5}]
    result = exam.grade_exam_paper(_paper(keys), "1. B")
    report = result["report"]
    assert "写入异常" in report, report
    assert "请检查权限后重试" in report
    assert "缺少题干" not in report


def test_unarchived_auto_advance_off_reports_reason(monkeypatch):
    """auto_advance=False：回写被整体跳过 → 须说「自动复测回写已关闭」而非权限。"""
    writes = []
    monkeypatch.setattr(exam, "error_logger", _fake_logger(writes))
    keys = [{"id": 1, "title": "示例题一", "question": "示例题干一",
             "standard_answer": "A", "score": 5}]
    result = exam.grade_exam_paper(_paper(keys), "1. B", auto_advance=False)
    report = result["report"]
    assert "自动复测回写已关闭" in report, report
    assert "请检查权限后重试" not in report


def test_successful_archive_keeps_original_line(monkeypatch):
    """阴性对照：正常回写成功 → 仍走「已重置回第一复测周期」，分类归因不误伤成功路径。"""
    writes = []
    monkeypatch.setattr(exam, "error_logger", _fake_logger(writes))
    keys = [{"id": 1, "title": "示例题一", "question": "示例题干一",
             "standard_answer": "A", "score": 5}]
    result = exam.grade_exam_paper(_paper(keys), "1. B")
    report = result["report"]
    assert "已重置回第一复测周期" in report
    assert "未能回写" not in report
    assert len(writes) == 1


# ───────────── 小问题2：get_llm_config 的 api 嵌套回退 ─────────────

def _write_cfg(tmp_path, cfg):
    (tmp_path / "ky_config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_flat_config_unchanged(tmp_path):
    """阴性对照：顶层平铺配置（向导/GUI 写回格式）行为与旧版完全一致。"""
    ws = _write_cfg(tmp_path, {
        "api_key": "sk-flat", "base_url": "https://flat.example/v1",
        "model": "m-flat", "temperature": 0.4})
    cfg = get_llm_config(ws)
    assert cfg == {"api_key": "sk-flat", "base_url": "https://flat.example/v1",
                   "model": "m-flat", "temperature": 0.4}


def test_nested_api_same_keys(tmp_path):
    """纯嵌套（同名字段）：{"api": {...}} 应被完整识别。"""
    ws = _write_cfg(tmp_path, {"api": {
        "api_key": "sk-nest", "base_url": "https://nest.example/v1",
        "model": "m-nest", "temperature": 0.2}})
    cfg = get_llm_config(ws)
    assert cfg["api_key"] == "sk-nest"
    assert cfg["base_url"] == "https://nest.example/v1"
    assert cfg["model"] == "m-nest"
    assert cfg["temperature"] == 0.2


def test_nested_api_short_aliases(tmp_path):
    """纯嵌套（短别名）：key/url 是手写配置的常见写法，须一并识别。"""
    ws = _write_cfg(tmp_path, {"api": {
        "key": "sk-alias", "url": "https://alias.example/v1", "model": "m-a"}})
    cfg = get_llm_config(ws)
    assert cfg["api_key"] == "sk-alias"
    assert cfg["base_url"] == "https://alias.example/v1"
    assert cfg["model"] == "m-a"


def test_top_level_wins_over_nested(tmp_path):
    """混合形态：顶层平铺键优先，缺失的键才回退嵌套（逐键回退，非整体二选一）。"""
    ws = _write_cfg(tmp_path, {
        "api_key": "sk-top",
        "api": {"api_key": "sk-nested", "base_url": "https://b.example/v1"}})
    cfg = get_llm_config(ws)
    assert cfg["api_key"] == "sk-top", "顶层已配置时不得被嵌套覆盖"
    assert cfg["base_url"] == "https://b.example/v1", "顶层缺失键须回退嵌套"


def test_non_dict_api_degrades_safely(tmp_path):
    """api 为字符串/数组等非对象时按无嵌套处理（不抛异常，行为同旧版）。"""
    ws = _write_cfg(tmp_path, {"api": "not-a-dict", "api_key": "sk-x"})
    cfg = get_llm_config(ws)
    assert cfg["api_key"] == "sk-x"
    assert cfg["base_url"] == "https://api.deepseek.com/v1"
    assert cfg["model"] == "deepseek-chat"


def test_none_values_fall_back_instead_of_string_none(tmp_path):
    """顶层显式 null：回退嵌套（旧版会把 None 变成字面量 'None' / 整份配置报废）。"""
    ws = _write_cfg(tmp_path, {"api_key": None, "api": {"api_key": "sk-n"}})
    cfg = get_llm_config(ws)
    assert cfg["api_key"] == "sk-n"


def test_is_llm_configured_accepts_nested(tmp_path):
    """端到端：嵌套配置经 is_llm_configured 判定为已配置（REPL 可用）。"""
    ws = _write_cfg(tmp_path, {"api": {
        "key": "sk-live", "url": "https://u.example/v1", "model": "m"}})
    assert is_llm_configured(workspace_root=ws) is True


def test_missing_config_file_returns_empty(tmp_path):
    """无配置文件：仍返回 {}（不因新逻辑改变缺失语义）。"""
    assert get_llm_config(tmp_path) == {}
