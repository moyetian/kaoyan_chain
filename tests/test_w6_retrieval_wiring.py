# -*- coding: utf-8 -*-
"""[W6] 检索接线与工具改进 —— 回归测试。

背景（KaoYanBench core50 实测）
------------------------------
- 多路查询规划（plan_queries/search_planned）是死代码：Agent 只走单路原始
  query，召回取决于运气；show_scores 默认关，权威分没喂给模型；
- PDF 提取只读前 8 页且 offset 无效：20 页文档的关键字段在后段页面，
  模型被迫写脚本绕行并撞上脚本执行权限墙（PDF-005 类任务因此失分）。

修复
----
1. ``web_search`` 默认走 ``search_planned``（多路规划 → 融合去重），单路仅作
   降级回退；输出开 ``show_scores``（相关性/权威分显式可见）；
2. ``extract_pdf_pages`` 支持 ``start_page``（1-based 续读）；``read_file``
   对 PDF 的 offset 解释为起始页，默认页数上限 8→20、默认字符上限 2000→12000；
3. 安全拦截文案追加「替代路径」引导（read_file 直读），避免模型陷入
   写脚本 → 被拦 → 再写脚本的循环。

全程离线：mock 检索服务，PDF 用 core50 数据集真实 fixture。
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import search as search_pkg  # noqa: E402
from search.models import SearchResponse, SearchResult  # noqa: E402
from tools.agent.loop import AgentRunner  # noqa: E402
from tools.skills.pdf_extractor import extract_pdf_pages  # noqa: E402

PDF_20P = (ROOT / "tests" / "benchmarks" / "core50" / "benchmark" / "tasks"
           / "public" / "pdf" / "PDF-005" / "references" / "brief_long_20p.pdf")


def _make_runner(tmp_path):
    # agent.headless_write_policy=allow_list + 点名 web_search：与 core50 评测
    # shim 的配置一致（非交互下 NETWORK 级工具只有被点名才放行，否则拦截）。
    cfg = {"api_key": "sk-test-fake", "model": "样本模型", "active_subject": "pol",
           "agent": {"headless_write_policy": "allow_list",
                     "headless_allow_tools": ["web_search"]}}
    return AgentRunner(config=cfg, workspace_root=tmp_path,
                       permission_mode="auto", quiet=True, max_steps=1)


# ── 1. PDF 提取：start_page 续读 ───────────────────────────────────────


def test_extract_pdf_pages_default_first_pages():
    """默认行为不变：从第 1 页起提取 max_pages 页（向后兼容）。"""
    info = extract_pdf_pages(str(PDF_20P), max_pages=5)
    assert info["success"], info.get("error")
    assert info["total_pages"] == 20
    assert [p["page"] for p in info["pages"]] == [1, 2, 3, 4, 5]
    print("  [√] 默认从第 1 页提取")


def test_extract_pdf_pages_start_page():
    """start_page=8 → 从第 8 页续读（长文档后段页面可达）。"""
    info = extract_pdf_pages(str(PDF_20P), max_pages=5, start_page=8)
    assert info["success"]
    assert [p["page"] for p in info["pages"]] == [8, 9, 10, 11, 12]
    print("  [√] start_page 续读生效")


def test_extract_pdf_pages_start_page_overflow_clamped():
    """越界起始页收敛到最后一页，绝不抛错。"""
    info = extract_pdf_pages(str(PDF_20P), max_pages=5, start_page=999)
    assert info["success"]
    assert [p["page"] for p in info["pages"]] == [20]
    print("  [√] 越界起始页收敛")


# ── 2. read_file 的 PDF 分支：offset 为起始页 + 字符上限提升 ──────────


def test_read_file_pdf_offset_is_start_page(tmp_path):
    """read_file 对 PDF 的 offset 解释为起始页（1-based）。"""
    target = tmp_path / "doc.pdf"
    shutil.copy(PDF_20P, target)
    runner = _make_runner(tmp_path)
    out = runner.tool_registry.execute_tool(
        "read_file", {"path": "doc.pdf", "offset": 8}, interactive=False)
    assert "本次提取第 8 页起" in out, out[:200]
    assert "--- 第 8 页 ---" in out
    assert "--- 第 7 页 ---" not in out
    print("  [√] read_file offset=起始页")


def test_read_file_pdf_default_char_limit_raised(tmp_path):
    """默认上限提升后 20 页文档完整返回（旧实现 2000 字符截断会丢第 18–20 页）。

    该 fixture 20 页共约 2295 字符：旧行为 ``pages_txt[:2000]`` 会截掉末段，
    ``--- 第 20 页 ---`` 标记必丢失；新默认 12000 上限完整保留全部页。
    """
    target = tmp_path / "doc.pdf"
    shutil.copy(PDF_20P, target)
    runner = _make_runner(tmp_path)
    out = runner.tool_registry.execute_tool(
        "read_file", {"path": "doc.pdf"}, interactive=False)
    assert "共 20 页" in out
    assert "--- 第 20 页 ---" in out, "旧 2000 字符截断会丢末段页；新默认上限应完整保留"
    assert len(out) > 2000
    print(f"  [√] 20 页完整返回（输出 {len(out)} 字符，旧上限 2000 必截断）")


class _FakeExtractor:
    """桩：返回 15000 字符单页文本，用于直接验证字符上限数值。"""

    @staticmethod
    def extract_pdf_pages(path, max_pages=8, start_page=1):
        return {"success": True, "total_pages": 40,
                "pages": [{"page": start_page, "text": "长文本内容。" * 2500}]}


def test_read_file_pdf_char_limit_is_12000(tmp_path, monkeypatch):
    """字符上限实测为 12000：15000 字符输入被截到约 12000（远大于旧 2000）。"""
    import tools.agent.tools_impl as impl
    monkeypatch.setattr(impl, "_get_pdf_extractor", lambda: _FakeExtractor)
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"%PDF-1.4 fake")
    runner = _make_runner(tmp_path)
    out = runner.tool_registry.execute_tool(
        "read_file", {"path": "doc.pdf"}, interactive=False)
    # 头部（约 40 字符）+ 12000 字符文本；旧上限 2000 时输出必 < 2100。
    assert 10000 < len(out) <= 12200, f"字符上限应为 12000（实际输出 {len(out)}）"
    print(f"  [√] 字符上限 12000 生效（输出 {len(out)} 字符）")


# ── 3. web_search：默认多路规划 + show_scores + 单路回退 ───────────────


class _FakeService:
    def __init__(self):
        self.calls = []

    def search_planned(self, text, *, limit=10, year=None, school="",
                       major="", domains=(), providers=(), max_queries=4):
        self.calls.append(("planned", text, int(max_queries)))
        return SearchResponse(
            query=text,
            results=(SearchResult(
                title="研招网公告", url="https://yz.chsi.com.cn/kyzx/notice.html",
                snippet="初试时间公告", source_type="官网", score=0.9),),
            providers_used=("bing",), queries_run=4)

    def search(self, q):
        self.calls.append(("single", q.text, 1))
        return SearchResponse(query=q.text, results=())


class _BrokenPlannedService(_FakeService):
    def search_planned(self, text, **kwargs):
        self.calls.append(("planned", text, 0))
        raise RuntimeError("多路规划失败")


def _patch_default(monkeypatch, fake):
    monkeypatch.setattr(
        search_pkg.SearchService, "default",
        classmethod(lambda cls, providers=None: fake))


def test_web_search_uses_search_planned(tmp_path, monkeypatch):
    """web_search 默认走多路查询规划（search_planned），输出带 score。"""
    fake = _FakeService()
    _patch_default(monkeypatch, fake)
    runner = _make_runner(tmp_path)

    out = runner.tool_registry.execute_tool(
        "web_search", {"query": "2026 研招网 初试时间"}, interactive=False)

    assert fake.calls and fake.calls[0][0] == "planned", \
        f"应优先走 search_planned，实际 {fake.calls}"
    assert fake.calls[0][2] == 4, "默认 4 路查询"
    assert "yz.chsi.com.cn" in out
    assert "score=" in out, "show_scores=True 应把权威/相关性分喂给模型"
    print("  [√] web_search 走多路规划 + show_scores")


def test_web_search_falls_back_to_single_on_planned_failure(tmp_path, monkeypatch):
    """search_planned 异常 → 回退单路 search（检索不中断）。"""
    fake = _BrokenPlannedService()
    _patch_default(monkeypatch, fake)
    runner = _make_runner(tmp_path)

    out = runner.tool_registry.execute_tool(
        "web_search", {"query": "测试"}, interactive=False)

    kinds = [c[0] for c in fake.calls]
    assert kinds == ["planned", "single"], f"应为 planned 失败后回退 single，实际 {kinds}"
    assert isinstance(out, str)
    print("  [√] 多路失败回退单路")
