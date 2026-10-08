# -*- coding: utf-8 -*-
"""[UT2] 招生情报噪声过滤与 compare 引导提示回归测试。

覆盖三处修复：
  1. watcher 标题提取/清洗环节过滤内联 JS 代码片段（`'+item.bt+'` 类噪声），
     并对 `.memory/admission_watch.json` 历史存量数据做惰性清洗；
  2. admission 消费侧伪科目后置过滤（门牌号/楼栋名被误判为初试科目）；
  3. compare「证据不足」分支补可操作引导（ky admission / ky scout / 在线核验）。

夹具全部中性化（tests/ 约定）：合成校名与合成 HTML，不写真实仓库、不联网。
"""

import json

from tools.intelligence.comparator import SchoolComparator
from tools.intelligence.extractor import (
    DocumentExtractor,
    _extract_subjects,
    _is_plausible_subject,
    filter_subjects,
)
from tools.intelligence.watcher import AdmissionWatcher, _is_code_noise_title


# ═══════════════════ 修复1：watcher 标题 JS 噪声过滤 ═══════════════════

#: 仿研招网院校页：公告列表由内联 JS 模板动态渲染（历史实测把
#: `'+item.bt+'` 当标题写入监控快照的形态）。
JS_TEMPLATE_HTML = """<html><body>
<div id="wbggtable"></div>
<script type="text/javascript">
    api.syncAjax('get', '/sswbgg/pages/list/' + dwdm + '.json').then(function (res){
        if (res.flag) {
            res.msg.forEach(function (item) {
                $('<li><div class="value"><a href="/sswbgg/pages/msgdetail.do?dwdm='+item.dwdm+'&msg_id='+item.id+'" target="_blank">'+item.bt+'</a></div><div class="time">'+item.fbsj+'</div></li>').appendTo("#wbggtable");
            });
        }
    });
</script>
<ul class="notice-list">
  <li><a href="/n/1" title="测试大学2027年硕士研究生招生专业目录公告">测试大学2027年硕士研究生招生专业目录公告</a></li>
  <li><a href="/n/2">2027年硕士研究生招生简章发布</a></li>
</ul>
</body></html>
"""


class TestWatcherJsNoiseFilter:
    """修复1：内联 JS 拼接片段不得作为页面标题进入监控快照。"""

    def test_marker_judgement(self):
        """JS 拼接特征判为噪声；正常中文公告标题不误伤。"""
        assert _is_code_noise_title("'+item.bt+'")
        assert _is_code_noise_title('$(\'<li><div class="value">\'+item.id+\'</div></li>\')')
        assert _is_code_noise_title("appendTo('#wbggtable')")
        for normal in (
            "关于2027年硕士研究生招生专业目录的公告",
            "测试大学2026年硕士研究生招生考试调剂复试录取工作办法",
            "【官方通知】2027年招收攻读硕士学位研究生自命题科目考试大纲",
            "关于“全国硕士研究生招生考试”测试考点网上确认公告",
        ):
            assert not _is_code_noise_title(normal), f"误伤正常标题: {normal}"

    def test_extract_titles_drops_inline_js_template(self):
        """script 块内的 JS 模板不进入标题列表；静态正常标题保留。"""
        w = AdmissionWatcher.__new__(AdmissionWatcher)
        titles = w._extract_recent_titles(JS_TEMPLATE_HTML)
        assert titles, "正常公告标题被连带剥离，过度过滤"
        assert not any(_is_code_noise_title(t) for t in titles), titles
        assert any("专业目录公告" in t for t in titles)
        assert any("招生简章发布" in t for t in titles)

    def test_load_sanitizes_legacy_noise_and_writes_back(self, tmp_path):
        """历史脏数据（存量 recent_titles / alert_titles）加载时惰性清洗并写回。"""
        watch_file = tmp_path / ".memory" / "admission_watch.json"
        watch_file.parent.mkdir(parents=True)
        watch_file.write_text(json.dumps({
            "10000": {
                "name": "测试大学",
                "recent_titles": ["测试大学2027年硕士招生简章", "'+item.bt+'"],
                "updates": [{
                    "time": "2026-09-30 10:00",
                    "alert_titles": ["$('<li>'+item.fbsj+'</li>')", "2027年招生专业目录公告"],
                }],
            }
        }, ensure_ascii=False), encoding="utf-8")

        w = AdmissionWatcher(workspace_root=tmp_path)

        # 内存清洗
        rec = w.watch_data["10000"]
        assert rec["recent_titles"] == ["测试大学2027年硕士招生简章"]
        assert rec["updates"][0]["alert_titles"] == ["2027年招生专业目录公告"]
        # 文件写回（惰性清洗落盘）
        on_disk = json.loads(watch_file.read_text(encoding="utf-8"))
        assert on_disk["10000"]["recent_titles"] == ["测试大学2027年硕士招生简章"]
        assert "'+item.bt+'" not in watch_file.read_text(encoding="utf-8")

    def test_load_without_noise_does_not_rewrite(self, tmp_path, monkeypatch):
        """阴性对照：干净文件加载不触发写回（不产生无谓的磁盘写入）。"""
        watch_file = tmp_path / ".memory" / "admission_watch.json"
        watch_file.parent.mkdir(parents=True)
        watch_file.write_text(json.dumps({
            "10000": {"name": "测试大学", "recent_titles": ["测试大学2027年硕士招生简章"]}
        }, ensure_ascii=False), encoding="utf-8")

        calls = []
        orig_save = AdmissionWatcher._save

        def _spy_save(self):
            calls.append(1)
            orig_save(self)

        monkeypatch.setattr(AdmissionWatcher, "_save", _spy_save)
        AdmissionWatcher(workspace_root=tmp_path)
        assert calls == [], "干净数据不应触发清洗写回"


# ═══════════════════ 修复3：compare「证据不足」引导 ═══════════════════

def _profile(catalog_source: str, majors=None) -> dict:
    """构造与 `_get_school_profile` 同形的画像（中性化）。"""
    return {
        "name": "测试大学",
        "code": "10000",
        "level": "双一流建设高校",
        "region": "北京",
        "official": "",
        "graduate": "",
        "majors": majors or ["(101)思想政治理论", "(201)外国语"],
        "catalog_source": catalog_source,
        "score_trend": "未核验",
        "ratio": "未核验",
        "protect": "未核验",
        "reputation": "",
        "pitfalls": "",
    }


class TestCompareInsufficientEvidenceHint:
    """修复3：未核验来源时 subject_diff 必须携带可操作引导。"""

    def test_unverified_branch_carries_actionable_hint(self):
        comp = SchoolComparator()
        d = comp._analyze_differences(
            "测试大学甲", _profile("[UNVERIFIED 通用兜底·初试科目代码未核验]"),
            "测试大学乙", _profile("[UNVERIFIED 未核验]"), "测试专业")
        sd = d["subject_diff"]
        assert "当前证据不足" in sd
        assert "ky admission" in sd, "缺少取证命令引导"
        assert "ky scout" in sd, "缺少取证命令引导"

    def test_verified_branch_lists_subjects_without_hint(self):
        """阴性对照：来源已核验时逐条列科目，不出现证据不足引导。"""
        comp = SchoolComparator()
        d = comp._analyze_differences(
            "测试大学甲", _profile("[LOCAL_DB_VERIFIED 本地高校库实录]"),
            "测试大学乙", _profile("[LOCAL_DB_VERIFIED 本地高校库实录]"), "测试专业")
        sd = d["subject_diff"]
        assert "当前证据不足" not in sd
        assert "测试大学甲" in sd and "初试科目" in sd


# ═══════════════════ 修复2：消费侧伪科目过滤 ═══════════════════

class TestSubjectAddressFilter:
    """修复2：门牌号/楼栋名不得作为初试科目进入证据（消费侧后置过滤）。"""

    def test_address_text_yields_no_subject_evidence(self):
        """阴性测试：地址文本经完整消费链路不产出科目证据。"""
        html = ("<html><body>"
                "<p>办公地点：理科大楼 500号办公楼 A座</p>"
                "</body></html>")
        evs = DocumentExtractor().extract_from_html(
            html, "https://example.edu.cn/notice", "测试大学", target_year=2027)
        fields = [(e.to_dict() if hasattr(e, "to_dict") else e).get("field")
                  for e in evs]
        # [P1 修复·2026-10-08 R10] field 名由「初试科目配置」归一为「初试科目」
        # （四种同语义产出统一 field 名以进入冲突仲裁分组），断言同步。
        assert "初试科目" not in fields, "地址文本不应产出科目证据"

    def test_filter_drops_address_like_items(self):
        """实测污染例（含数字恰为真实代码的 333）全部剔除。"""
        for bad in ("(500)号办公楼", "(333)号沧浦社区办公楼", "(268)号二楼",
                    "(106)号汇通写字楼", "(499)杭州市科协大楼"):
            assert not _is_plausible_subject(bad), f"伪科目漏网: {bad}"

    def test_filter_keeps_real_subjects(self):
        """真实统考/联考/自命题科目不误伤（含含「号/路/区/室/楼」字样的科目名）。"""
        for good in ("(101)思想政治理论", "(201)英语一", "(204)英语(二)",
                     "(312)心理学专业基础综合", "(333)教育综合", "(347)心理学专业综合",
                     "(431)金融学综合", "(497)法硕联考综合(法学)", "(408)计算机学科专业基础",
                     "(816)材料力学", "(814)通信原理", "思想政治理论",
                     "(614)信号与系统", "(824)道路工程", "(802)区域经济学",
                     "(830)室内设计", "(850)楼宇自动化"):
            assert _is_plausible_subject(good), f"误伤真实科目: {good}"

    def test_mixed_text_keeps_normal_subjects(self):
        """伪科目与正常科目同现时：只剔除伪科目，正常科目不误伤。"""
        html = ("<html><body><p>办公地点：理科大楼 500号办公楼 A座</p>"
                "<p>初试科目：①101思想政治理论 ②201英语一 ③312心理学专业基础综合</p>"
                "</body></html>")
        kept = filter_subjects(_extract_subjects(html))
        assert "(500)号办公楼" not in kept
        assert "(101)思想政治理论" in kept
        assert "(201)英语一" in kept
        assert "(312)心理学专业基础综合" in kept
        assert len([x for x in kept if x.startswith("(")]) == 3, \
            f"代码科目应恰为 3 门（不误伤）: {kept}"
