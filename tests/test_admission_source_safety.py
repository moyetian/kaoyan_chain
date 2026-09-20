from tools.intelligence.extractor import DocumentExtractor
from tools.intelligence.chsi_connector import CHSIConnector
from tools.intelligence.comparator import SchoolComparator
from tools.skills import school_scout as scout_mod


def test_official_source_must_belong_to_requested_school():
    check = DocumentExtractor._is_school_source
    assert check("https://pgs.ruc.edu.cn/notice", "中国人民大学")
    assert not check("https://yjsy.fzu.edu.cn/notice", "中国人民大学")
    assert not check("https://ruc.edu.cn.attacker.example/notice", "中国人民大学")


def test_unknown_major_does_not_get_electronic_information_template():
    connector = CHSIConnector()
    assert connector._generate_ground_truth_evidences(
        school_name="中国人民大学", major_keyword="马克思主义哲学",
        source_url="https://yz.chsi.com.cn", target_year=2027) == []


def test_unverified_catalog_does_not_imply_subject_similarity():
    profile = {"majors": ["未核验"], "region": "待查", "protect": "未核验",
               "catalog_source": "OFFLINE_BASELINE"}
    result = SchoolComparator()._analyze_differences("甲", profile, "乙", profile, "哲学")
    assert "无法判定" in result["subject_diff"]


# ── P1-1 回归：跨校页面所有证据一律标外部来源 ──

_FZU_HTML = """<html><head><title>福州大学2027年硕士招生简章</title></head>
<body><p>2027年拟招生3000人</p><p>(101)思想政治理论 (204)英语二</p></body></html>"""


def test_cross_school_page_never_labeled_as_target_official():
    evs = DocumentExtractor().extract_from_html(
        html_text=_FZU_HTML, page_url="https://yjsy.fzu.edu.cn/2027jz",
        school_name="中国人民大学", target_year=2027)
    assert evs, "简章页应产出证据"
    for ev in evs:
        assert "官方" not in ev.source.name, ev.source.name
    assert any("外部来源" in ev.source.name for ev in evs)


def test_same_school_page_keeps_official_label():
    html = _FZU_HTML.replace("福州大学", "中国人民大学").replace(
        "yjsy.fzu.edu.cn", "pgs.ruc.edu.cn")
    evs = DocumentExtractor().extract_from_html(
        html_text=html, page_url="https://pgs.ruc.edu.cn/2027jz",
        school_name="中国人民大学", target_year=2027)
    assert evs
    assert any("中国人民大学 官方" in ev.source.name for ev in evs)


# ── P1-1/P1-2 回归：scout 侧画像闸门 ──

def test_empty_major_does_not_default_to_computer():
    intel = scout_mod.infer_general_school_intel("中国人民大学", "")
    joined = " ".join(intel["subjects"])
    assert "待核验" in joined
    assert "408" not in joined and "数学" not in joined


def test_unmatched_major_gets_no_cs_risk_signals():
    metrics = scout_mod.extract_key_metrics(
        "华中科技大学", "马克思主义哲学", [], {})
    risks = " ".join(metrics["risk_signals"])
    assert "408" not in risks and "机试" not in risks
    assert any("待核验" in s for s in metrics["subjects_hint"])


def test_empty_major_does_not_match_first_department():
    metrics = scout_mod.extract_key_metrics("华中科技大学", "", [], {})
    risks = " ".join(metrics["risk_signals"])
    assert "408" not in risks and "机试" not in risks
    assert any("待核验" in s for s in metrics["subjects_hint"])


def test_llm_prompt_blocks_math_for_non_math_candidate(monkeypatch):
    captured = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"choices": [{"message": {"content": "ok"}}]}'

    def _fake_urlopen(req, timeout=25):
        captured["body"] = req.data.decode("utf-8")
        return _Resp()

    monkeypatch.setattr(scout_mod.urllib.request, "urlopen", _fake_urlopen)
    cfg = {"api_key": "sk-test",
           "study_plan": {"math_key": "none", "math_name": "不考数学"}}
    out = scout_mod.synthesize_report_with_llm("中国人民大学", "马克思主义哲学",
                                               [], {}, cfg)
    assert out == "ok"
    import json as _js
    system_text = _js.loads(captured["body"])["messages"][0]["content"]
    assert "不考数学" in system_text and "算法题库" in system_text
