# -*- coding: utf-8 -*-
"""[R11 修复·2026-10-10] 判卷/检索域 10 项已复核缺陷回归测试。

覆盖清单（与修复报告编号一致）：
  * B#1 ``material_scanner.execute`` 对 bool/str 误用 ``join(map(str,...))``
  * B#2 ``experience_dossier.append_experience_to_dossier`` 条件与替换串不一致
  * B#3 ``exam_grading`` 伴生密钥文件名反解 paper_id 的正则贪婪吞日期
  * B#4 ``exam_calendar`` 「第 3 个周六」≠「倒数第二个周六」（5 周六年份错 7 天）
  * C#1 ``evidence_engine.resolve_conflicts`` 同源多行互判 CONFLICT
  * C#2 ``search.relevance`` 年份-only 查询全判不相关 + 误报反爬
  * C#3 ``discovery.build_targeted_queries`` 站内查询词嵌入字面「0」
  * C#4 ``school_scout`` 研报落盘未走单一真源（report_paths）
  * C#6 ``fetcher`` SSL 降级分支缺 attempt 守卫（末次证书错误落 ERROR）

隔离约定：
  * 全程离线：LLM / 网络交互一律打桩，不触碰真实工作区；
  * 文件写入全部落在 ``tmp_path``（``school_scout.ROOT`` 等显式重定向）；
  * 测试夹具只用「示例农业大学」等中性占位，无真实身份字面量。
"""

from __future__ import annotations

import re
import ssl
import urllib.error
from datetime import date
from pathlib import Path

import pytest

# ════════════════════════════════════════════════════════════════════
# B#1 material_scanner.execute：would_watch(bool) / would_scout(str)
# ════════════════════════════════════════════════════════════════════


class TestMaterialScannerExecutePreviewLines:
    """``execute()`` 对 preview 字段必须按真实类型渲染，不得 join 展开。"""

    @staticmethod
    def _patch(monkeypatch, res):
        from tools.skills import material_scanner

        monkeypatch.setattr(material_scanner, "scan_and_mount_materials",
                            lambda workspace_root=None, auto_scout_school=True,
                            apply=False: res)
        return material_scanner

    def test_bool_and_str_preview_do_not_crash(self, monkeypatch):
        """would_watch=True（bool）/ would_scout=文件名（str）→ 不抛异常且文案正确。

        修复前：``", ".join(map(str, True))`` → TypeError（bool 不可迭代）；
        若 would_scout 为 str 则被逐字符展开成「文, 件, 名」。
        """
        res = {
            "success": True, "applied": False, "total_files": 0,
            "details": {}, "changes": [],
            "would_watch": True,
            "would_scout": "目标院校情报_示例农业大学.md",
        }
        mod = self._patch(monkeypatch, res)

        out = mod.execute({})  # 修复前此处直接 TypeError

        assert "研招简章监控" in out and "自动纳入监控雷达" in out
        assert "院校侦察" in out and "目标院校情报_示例农业大学.md" in out
        # 不得出现逐字符展开的形态
        assert "目, 标" not in out

    def test_false_and_empty_produce_no_preview_lines(self, monkeypatch):
        """阴性：False / "" 时不输出对应行（不误报）。"""
        res = {
            "success": True, "applied": False, "total_files": 0,
            "details": {}, "changes": [],
            "would_watch": False, "would_scout": "",
        }
        mod = self._patch(monkeypatch, res)

        out = mod.execute({})

        assert "研招简章监控" not in out
        assert "院校侦察" not in out


# ════════════════════════════════════════════════════════════════════
# B#2 experience_dossier：无后缀标题档案的追加必须真实生效
# ════════════════════════════════════════════════════════════════════

_PLAIN_HEADING = "## 💡 2. 精选高置信度学长学姐实名经验"
_SUFFIX_HEADING = "## 💡 2. 精选高置信度学长学姐实名经验 (Top Experiences)"


class TestExperienceDossierAppend:
    """条件查无后缀子串、replace 匹配带后缀串 → 无后缀档案静默丢失。"""

    @staticmethod
    def _write(path: Path, heading: str) -> None:
        path.write_text(f"# 🎓 示例农业大学经验档案\n\n{heading}\n\n旧内容占位\n",
                        encoding="utf-8")

    def test_plain_heading_append_really_changes_content(self, tmp_path):
        """核心修复：无后缀标题档案追加 → True 且内容确实变化。

        修复前：replace 模式带 `` (Top Experiences)`` 后缀永不命中 → 原样写回、
        return True 谎报成功、经验静默丢失。
        """
        from tools.skills.experience_dossier import append_experience_to_dossier

        f = tmp_path / "示例农业大学.md"
        self._write(f, _PLAIN_HEADING)
        before = f.read_text(encoding="utf-8")

        ok = append_experience_to_dossier(
            "示例农业大学", "微信公众号", "示例作者", "经验正文A",
            dossier_path=f)

        assert ok is True
        after = f.read_text(encoding="utf-8")
        assert after != before, "返回 True 但内容未变化 —— 经验被静默丢弃"
        assert "经验正文A" in after
        assert _PLAIN_HEADING in after, "原有无后缀标题行必须保留"

    def test_suffixed_heading_append_still_works(self, tmp_path):
        """带后缀标题档案同样生效（防回归）。"""
        from tools.skills.experience_dossier import append_experience_to_dossier

        f = tmp_path / "示例农业大学.md"
        self._write(f, _SUFFIX_HEADING)

        ok = append_experience_to_dossier(
            "示例农业大学", "微信公众号", "示例作者", "经验正文B",
            dossier_path=f)

        assert ok is True
        after = f.read_text(encoding="utf-8")
        assert "经验正文B" in after
        assert _SUFFIX_HEADING in after

    def test_success_implies_content_changed(self, tmp_path, monkeypatch):
        """不谎报契约：返回 True ⟺ 写入内容确实与原内容不同。

        （防线 ``if updated == orig: return False`` 的守卫面 —— 插入非空文本时
        必然变化；本用例以「写入记录 != 原内容」钉住 True 的语义。）
        """
        from tools.skills import experience_dossier

        f = tmp_path / "示例农业大学.md"
        self._write(f, _PLAIN_HEADING)
        before = f.read_text(encoding="utf-8")

        written = []
        monkeypatch.setattr(experience_dossier, "atomic_write_text",
                            lambda path, text, **kw: written.append(text))

        ok = experience_dossier.append_experience_to_dossier(
            "示例农业大学", "微信公众号", "示例作者", "经验正文C",
            dossier_path=f)

        assert ok is True
        assert len(written) == 1
        assert written[0] != before, "返回 True 但写入内容与原内容相同 —— 谎报成功"


# ════════════════════════════════════════════════════════════════════
# B#3 exam_grading：伴生密钥文件名反解 paper_id 不得吞日期前缀
# ════════════════════════════════════════════════════════════════════

_COMPANION_NAME = ".自测卷_2026-10-10_EXAM-MATH-20261010-120000-ab12.md.keys.json"
_TRUE_PAPER_ID = "EXAM-MATH-20261010-120000-ab12"


class TestCompanionPaperIdExtraction:
    """正则必须锚定 EXAM- 前缀，不得把日期前缀一起吞进 paper_id。"""

    def test_extracts_exam_prefixed_paper_id(self):
        from tools.skills.exam_grading import _extract_companion_paper_id

        assert _extract_companion_paper_id(_COMPANION_NAME) == _TRUE_PAPER_ID

    def test_greedy_legacy_pattern_would_swallow_date(self):
        """缺陷形态对照：旧贪婪正则反解出「2026-10-10_EXAM-...」。"""
        legacy = re.search(r"_([A-Za-z0-9_\-]+)\.md\.keys\.json$", _COMPANION_NAME)
        assert legacy is not None
        assert legacy.group(1) != _TRUE_PAPER_ID, "对照前提不成立：旧正则竟然没吞日期"
        assert legacy.group(1).startswith("2026-10-10")

    def test_no_exam_prefix_returns_empty(self):
        from tools.skills.exam_grading import _extract_companion_paper_id

        assert _extract_companion_paper_id(".自测卷_2026-10-10_notes.md.keys.json") == ""
        assert _extract_companion_paper_id("") == ""

    def test_roundtrip_seal_open_with_extracted_id(self, monkeypatch):
        """往返验证：用反解出的 paper_id 解封伴生密钥必须还原明文 JSON。"""
        from tools.skills import exam_composer
        from tools.skills.exam_grading import _extract_companion_paper_id

        # 固定盐：不读写真实 .memory/exam_keys/.salt（纯内存加解密）
        monkeypatch.setattr(exam_composer, "_get_exam_key_salt",
                            lambda: b"r11-unit-test-salt-0123456789abcdef")
        plaintext = '[{"id": 1, "answer": "示例答案"}]'
        sealed = exam_composer._seal_keys_payload(_TRUE_PAPER_ID, plaintext)

        pid = _extract_companion_paper_id(_COMPANION_NAME)
        opened = exam_composer._open_keys_payload(pid, sealed)

        assert opened == plaintext, "用反解 paper_id 解封失败（派生 keystream 不匹配）"

        # 对照：旧贪婪反解值解封得到乱码（非明文）→ 佐证缺陷真实存在
        legacy_pid = "2026-10-10_" + _TRUE_PAPER_ID
        assert exam_composer._open_keys_payload(legacy_pid, sealed) != plaintext


# ════════════════════════════════════════════════════════════════════
# B#4 exam_calendar：12 月倒数第二个周六（不是「第 3 个周六」）
# ════════════════════════════════════════════════════════════════════


class TestExamCalendarSecondLastSaturday:
    """5 周六年份（2023/2028/2029）「第 3 个周六」比倒数第二个周六早 7 天。"""

    @pytest.mark.parametrize("year,expected", [
        (2023, date(2023, 12, 23)),   # 实际初试日 12-23；旧实现算 12-16
        (2024, date(2024, 12, 21)),
        (2025, date(2025, 12, 20)),
        (2026, date(2026, 12, 19)),
        (2027, date(2027, 12, 18)),
        (2028, date(2028, 12, 23)),   # 5 周六年份：旧实现算 12-16
        (2029, date(2029, 12, 22)),   # 5 周六年份：旧实现算 12-15
    ])
    def test_second_last_saturday_of_december(self, year, expected):
        from tools.exam_calendar import third_saturday_of_december

        got = third_saturday_of_december(year)
        assert got == expected, f"{year} 初试日应为 {expected}，实得 {got}"
        assert got.weekday() == 5, "初试日必须是周六"

    def test_infer_exam_year_inside_exam_week_stays_same_year(self):
        """考试周内（2028-12-20，初试 12-23 之前）不得跳到次年。

        旧实现把 2028 初试日算成 12-16 → 12-20 已过 → 误判 2029。
        """
        from tools.exam_calendar import infer_exam_year

        assert infer_exam_year(date(2028, 12, 20)) == 2028
        assert infer_exam_year(date(2028, 12, 24)) == 2029


# ════════════════════════════════════════════════════════════════════
# C#1 evidence_engine：同源多行并列条目不是「多源官方冲突」
# ════════════════════════════════════════════════════════════════════

_CATALOG_URL = "https://yz.chsi.com.cn/zsml/queryAction.do"


def _catalog_row(i: int) -> dict:
    return {
        "school": "示例农业大学",
        "college": "马克思主义学院",
        "major_code": f"03050{i}",
        "major_name": f"示例专业{i}",
        "direction": "不区分研究方向",
    }


def _catalog_evidence(i: int, url: str = _CATALOG_URL, source_type: str = "chsi"):
    from tools.intelligence.evidence_engine import build_evidence

    return build_evidence("招生院系与专业", _catalog_row(i), "条", 2027,
                          source_type, "研招网官方专业目录", url, target_year=2027)


class TestResolveConflictsSameSourceRows:
    """研招网目录每行一条证据：同源（type+url 相同）异值不是冲突。"""

    def test_same_source_rows_not_conflict(self):
        """核心修复：3 条同源不同 dict → 全部保留且无 CONFLICT。"""
        from tools.intelligence.evidence_engine import resolve_conflicts

        out = resolve_conflicts([_catalog_evidence(i) for i in (1, 2, 3)])

        assert len(out) == 3, "同源并列条目必须全部保留"
        assert not any(e.status == "CONFLICT" for e in out), \
            "同源多行被误判为「多源官方冲突」"
        assert all(e.conflict_detail is None for e in out)

    def test_multi_source_diff_values_still_conflict(self):
        """阴性对照：2 条不同来源不同值 → 仍判 CONFLICT（仲裁器不得改瘫）。"""
        from tools.intelligence.evidence_engine import resolve_conflicts

        out = resolve_conflicts([
            _catalog_evidence(1, url="https://yz.chsi.com.cn/zsml/a"),
            _catalog_evidence(2, url="https://gs.example.edu.cn/zsml/b",
                              source_type="graduate_school"),
        ])

        assert len(out) == 2
        assert all(e.status == "CONFLICT" for e in out)

    def test_same_source_same_value_still_merges(self):
        """防回归：同源同值仍去重合并（既有行为，不得被同源豁免顺带取消）。"""
        from tools.intelligence.evidence_engine import resolve_conflicts

        out = resolve_conflicts([_catalog_evidence(1), _catalog_evidence(1)])

        assert len(out) == 1
        assert out[0].status != "CONFLICT"


# ════════════════════════════════════════════════════════════════════
# C#2 search.relevance：年份-only 查询不得全判不相关
# ════════════════════════════════════════════════════════════════════


class TestYearOnlyQueryGate:
    """查询仅剩年份 token 时无辨别力 → 与空 tokens 同口径放行。"""

    def test_significant_tokens_year_only_precondition(self):
        """缺陷前提：'2027 考研' 的有效 token 只有年份。"""
        from tools.search import relevance

        assert relevance.significant_tokens("2027 考研") == ["2027"]

    def test_year_only_query_keeps_results(self):
        """核心修复：含查询年份的真实结果不再被整条丢弃。"""
        from tools.search import relevance
        from tools.search.models import SearchResult

        r = SearchResult(title="2027考研报名时间与流程",
                         url="https://yz.example.edu.cn/baoming",
                         snippet="2027 年硕士研究生招生考试网上报名时间安排")
        kept, dropped = relevance.filter_relevant([r], "2027 考研")

        assert len(kept) == 1 and dropped == 0

    def test_explicit_keyword_query_junk_still_dropped(self):
        """阴性对照：查询含明确关键词时，仅含年份的垃圾仍被拦（年份 continue 不变）。"""
        from tools.search import relevance
        from tools.search.models import SearchResult

        junk = SearchResult(title="空调省电2027年问题",
                            url="https://example.test/jp/2027",
                            snippet="エアコン2027年問題について")
        kept, dropped = relevance.filter_relevant([junk], "示例大学 计算机 2027")

        assert kept == [] and dropped == 1


# ════════════════════════════════════════════════════════════════════
# C#3 discovery / rewrite：站内查询不得嵌入字面「0」
# ════════════════════════════════════════════════════════════════════


class TestTargetedQueriesYear:
    """``year=None`` 必须在运行时兜底为当前考试年，不得出现字面 0/None。"""

    def test_none_year_falls_back_to_runtime_current_year(self):
        from tools.intelligence.discovery import OfficialDiscovery

        d = OfficialDiscovery(fetcher=object())  # 纯函数调用，不发请求
        qs = d.build_targeted_queries("示例农业大学", "https://www.example.edu.cn",
                                      None, year=None)

        assert qs, "应生成站内查询"
        for q in qs:
            assert " 0 " not in f" {q} ", f"查询词嵌入字面 0: {q}"
            assert "None" not in q, f"查询词嵌入 None: {q}"
            assert re.search(r"(?:19|20)\d{2}", q), f"查询词缺 4 位年份: {q}"

    def test_explicit_year_is_respected(self):
        from tools.intelligence.discovery import OfficialDiscovery

        d = OfficialDiscovery(fetcher=object())
        qs = d.build_targeted_queries("示例农业大学", "https://www.example.edu.cn",
                                      "马克思主义理论", year=2027)

        assert all("2027" in q for q in qs)

    def test_rewrite_side_passes_none_not_zero(self, monkeypatch):
        """rewrite._site_queries 调用侧：year=0 必须转为 None（而非字面 0）。

        rewrite 内部按 ``intelligence.discovery`` → ``tools.intelligence.discovery``
        双路径导入（tools/ 在 sys.path 时前者可导入、且是**另一模块对象**），
        两个候选都打桩。
        """
        import importlib

        from tools.search.rewrite import _site_queries

        calls = []

        def spy(self, school_name, domain, major_keyword=None, year=None):
            calls.append(year)
            return []

        patched = 0
        for name in ("intelligence.discovery", "tools.intelligence.discovery"):
            try:
                mod = importlib.import_module(name)
            except ImportError:  # pragma: no cover - 依运行环境
                continue
            monkeypatch.setattr(mod.OfficialDiscovery,
                                "build_targeted_queries", spy)
            patched += 1
        assert patched >= 1

        _site_queries("示例农业大学", {"admission": "yz.example.edu.cn"}, "", 0)

        assert calls, "build_targeted_queries 未被调用"
        assert all(y is None for y in calls), \
            f"调用侧仍把 0 传给 discovery: {calls}"


# ════════════════════════════════════════════════════════════════════
# C#4 school_scout：研报落盘必须走 report_paths 单一真源
# ════════════════════════════════════════════════════════════════════


class TestScoutReportSingleSource:
    """落盘文件名与 admission 侧同函数（含归一：非法字符替换、空白压平）。"""

    def test_scout_report_filename_normalizes(self):
        from tools.intelligence.report_paths import scout_report_filename

        name = scout_report_filename("示例  农业大学", "马理论/原理  研究")

        assert "/" not in name and "\\" not in name, "文件名不得含路径分隔符"
        assert "马理论_原理 研究" in name, "非法字符应替换为下划线、连续空白应压平"
        assert name == "目标院校情报_示例 农业大学_马理论_原理 研究.md"

    def test_scout_school_save_uses_single_source_name(self, tmp_path, monkeypatch):
        """scout_school(save_report=True) 落盘名必须等于 scout_report_filename。"""
        from tools.intelligence.report_paths import scout_report_filename
        from tools.skills import school_scout

        monkeypatch.setattr(school_scout, "ROOT", tmp_path)
        monkeypatch.setattr(school_scout, "find_school_in_db", lambda s: None)
        monkeypatch.setattr(school_scout, "search_official_admissions",
                            lambda *a, **k: [])
        monkeypatch.setattr(school_scout, "search_social_sentiment",
                            lambda *a, **k: {"zhihu": [], "bilibili": [],
                                             "xiaohongshu": [], "direct_links": {}})
        monkeypatch.setattr(school_scout, "extract_key_metrics", lambda *a, **k: {})
        monkeypatch.setattr(school_scout, "format_scout_report",
                            lambda *a, **k: "REPORT-BODY")
        monkeypatch.setattr(school_scout, "filter_community_experiences",
                            lambda *a, **k: [])
        monkeypatch.setattr(school_scout, "save_experience_dossier",
                            lambda *a, **k: tmp_path / "dossier.md")

        school, major = "示例  农业大学", "马理论/原理"
        res = school_scout.scout_school(school, major, include_social=False,
                                        save_report=True, use_llm=False)

        assert res["success"] is True
        saved = Path(res["saved_path"])
        assert saved.parent == tmp_path / "04-专业课", "必须落在 04-专业课 下"
        assert saved.name == scout_report_filename(school, major)
        assert saved.exists()


# ════════════════════════════════════════════════════════════════════
# C#6 fetcher：SSL 降级分支缺 attempt 守卫（末次证书错误归因丢失）
# ════════════════════════════════════════════════════════════════════

_CERT_URL = "https://self-signed.example.test/notice/1.html"


class TestFetcherSslDowngradeAttemptGuard:
    """末次 attempt 的证书错误必须返回 TLS_CERT_ERROR，而非落到 ERROR。"""

    @staticmethod
    def _no_ssrf(monkeypatch):
        from tools.intelligence import fetcher as fetcher_mod

        monkeypatch.setattr(fetcher_mod, "assert_url_safe", lambda url: url)
        monkeypatch.setattr(fetcher_mod.time, "sleep", lambda _s: None)
        return fetcher_mod

    def test_cert_error_on_last_attempt_reports_tls_cert_error(self, monkeypatch):
        """前 N-1 次瞬时失败 + 末次证书错误 + opt-in → TLS_CERT_ERROR。

        修复前：末次证书错误仍走「设置 fallback ctx → continue」→ 循环耗尽
        → 落 ``ERROR``（opt-in 的未验证重试未兑现、归因丢失）。
        """
        fetcher_mod = self._no_ssrf(monkeypatch)

        calls = []

        def _probe(req, timeout=6, context=None):
            calls.append(context)
            if len(calls) == 1:
                raise urllib.error.URLError(TimeoutError("timed out"))
            raise urllib.error.URLError(ssl.SSLError("certificate verify failed"))

        monkeypatch.setattr(fetcher_mod, "safe_urlopen", _probe)

        res = fetcher_mod.HTTPFetcher(retry_attempts=1).fetch(
            _CERT_URL, allow_insecure_ssl=True)

        assert res.access_status == "TLS_CERT_ERROR", \
            f"末次证书错误归因丢失: {res.access_status}"
        assert res.is_valid is False

    def test_first_attempt_cert_error_still_downgrades_when_opted_in(self, monkeypatch):
        """阴性：首次即证书错误 + opt-in → 仍按既有路径降级重试（行为不变）。"""
        fetcher_mod = self._no_ssrf(monkeypatch)

        class _Probe:
            def __init__(self):
                self.insecure_hits = 0

            def __call__(self, req, timeout=6, context=None):
                if context is not None and getattr(context, "check_hostname", True):
                    raise urllib.error.URLError(
                        ssl.SSLError("certificate verify failed"))
                self.insecure_hits += 1

                class _Resp:
                    status = 200
                    headers = {"Content-Type": "text/html; charset=utf-8"}

                    def read(self, n=None):
                        return b"<html><body>ok</body></html>"

                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False

                return _Resp()

        probe = _Probe()
        monkeypatch.setattr(fetcher_mod, "safe_urlopen", probe)

        res = fetcher_mod.HTTPFetcher(retry_attempts=1).fetch(
            _CERT_URL, allow_insecure_ssl=True)

        assert res.is_valid is True and probe.insecure_hits == 1
        assert res.access_status == "UNVERIFIED_SSL"
        assert res.ssl_verified is False

    def test_cert_error_on_last_attempt_without_optin_reports_tls(self, monkeypatch):
        """无 opt-in 时末次证书错误同样必须如实报 TLS_CERT_ERROR。"""
        fetcher_mod = self._no_ssrf(monkeypatch)

        calls = []

        def _probe(req, timeout=6, context=None):
            calls.append(context)
            if len(calls) == 1:
                raise urllib.error.URLError(TimeoutError("timed out"))
            raise urllib.error.URLError(ssl.SSLError("certificate verify failed"))

        monkeypatch.setattr(fetcher_mod, "safe_urlopen", _probe)

        res = fetcher_mod.HTTPFetcher(retry_attempts=1).fetch(_CERT_URL)

        assert res.access_status == "TLS_CERT_ERROR"


# ════════════════════════════════════════════════════════════════════
# N4 material_scanner.execute：apply=True 已执行确认 vs 将来时预览
# ════════════════════════════════════════════════════════════════════


class TestMaterialScannerExecuteAppliedConfirm:
    """[N4 修复] apply=True 且 res 带执行确认时，渲染「已将/已生成」形态。

    修复前：两态恒用将来时（「将自动纳入」/「将生成 X」）—— apply=True 时
    实际已执行（watcher 已写入、侦察报告已落盘），却向模型谎报为预览。
    """

    @staticmethod
    def _patch(monkeypatch, res):
        from tools.skills import material_scanner

        monkeypatch.setattr(material_scanner, "scan_and_mount_materials",
                            lambda workspace_root=None, auto_scout_school=True,
                            apply=False: res)
        return material_scanner

    def test_applied_renders_executed_confirmations(self, monkeypatch):
        """核心修复：school_watch / scout_report 存在 → 「已将/已生成」。"""
        res = {
            "success": True, "applied": True, "total_files": 0,
            "details": {}, "changes": [],
            "school_watch": "已将目标高校【示例农业大学】自动纳入简章动态指纹监控雷达",
            "would_watch": True,
            "would_scout": "目标院校情报_示例农业大学_618 示例专业.md",
            "scout_report": "04-专业课/目标院校情报_示例农业大学_618 示例专业.md",
        }
        mod = self._patch(monkeypatch, res)

        out = mod.execute({"apply": True})

        assert "已将目标高校【示例农业大学】自动纳入简章动态指纹监控雷达" in out
        assert "已生成 04-专业课/目标院校情报_示例农业大学_618 示例专业.md" in out
        assert "将自动纳入监控雷达" not in out, "apply=True 仍渲染将来时预览"
        assert "将生成" not in out, "apply=True 仍渲染将来时预览"

    def test_scan_only_keeps_future_tense_preview(self, monkeypatch):
        """阴性：apply=False（确认字段为空）→ 保持将来时预览（行为不变）。"""
        res = {
            "success": True, "applied": False, "total_files": 0,
            "details": {}, "changes": [],
            "school_watch": "", "would_watch": True,
            "would_scout": "目标院校情报_示例农业大学.md", "scout_report": "",
        }
        mod = self._patch(monkeypatch, res)

        out = mod.execute({})

        assert "将自动纳入监控雷达" in out
        assert "将生成 目标院校情报_示例农业大学.md" in out
        assert "已生成" not in out


# ════════════════════════════════════════════════════════════════════
# N6 evidence_engine：url None 与 "" 是同一「无 URL」来源
# ════════════════════════════════════════════════════════════════════


class TestResolveConflictsNoneVsEmptyUrl:
    """[N6 修复] sources 集合未归一 url：None 与 "" 被凑成两个来源 → 假冲突。"""

    def test_none_and_empty_url_are_same_source(self):
        """核心修复：type 同、url 一 None 一 ""、值不同 → 不判 CONFLICT。"""
        from tools.intelligence.evidence_engine import resolve_conflicts

        out = resolve_conflicts([
            _catalog_evidence(1, url=None),
            _catalog_evidence(2, url=""),
        ])

        assert len(out) == 2, "同源（url 均空）并列条目必须全部保留"
        assert not any(e.status == "CONFLICT" for e in out), \
            "None 与 '' 被当作两个来源 → 同源假冲突残留"

    def test_different_type_empty_url_still_conflicts(self):
        """阴性：type 不同（即使 url 均空）→ 仍判 CONFLICT（仲裁器不得改瘫）。"""
        from tools.intelligence.evidence_engine import resolve_conflicts

        out = resolve_conflicts([
            _catalog_evidence(1, url=None),
            _catalog_evidence(2, url="", source_type="graduate_school"),
        ])

        assert len(out) == 2
        assert all(e.status == "CONFLICT" for e in out)
