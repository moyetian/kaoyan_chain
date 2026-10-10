# -*- coding: utf-8 -*-
"""[P2 修复·2026-10-08] 「检索/微信文章」组 P2 打磨回归测试（六领域审查第五批）

本批 6 条均先核实现状再动手，核实结论与测试落点：

* **① 多路查询对反爬源放大 4 倍请求 —— 已修复（P1-S1/S2），本文件钉住不回归**。
  核实：基线 eff2f07 的软反爬（200+垃圾）丢弃分支不落账、``mark_healthy`` 在
  守门之前 → 每轮 4 路计划全部真发请求且永不冷却；P1 修复后为「显式反爬 1 次
  即冷却 / 软反爬 2 次（阈值语义）后冷却 / 冷却源 0 次」。
* **② 搜狗跳转 URL authority 低估（0.40）**：``weixin.sogou.com`` 未收录域名表
  → 与「未知来源」同档 0.40，而 provider 已如实声明 ``source_type=wechat``，
  自相矛盾。修法：收录进域名表（wechat, 0.55，与 mp.weixin.qq.com 同档）。
* **③ 缓存 key 精确匹配致 serve-stale 覆盖窄**：多路规划的同题变体（原句 /
  校名+专业 / 追加「招生简章」等后缀）拿不到旧数据。修法：**仅 serve-stale
  路径**放宽到「同 provider/limit/time_range 的近义查询」兜底；严格模式不变。
* **④ ``"verify"`` marker 过宽**：裸子串会命中正常页（英文引导语 / JS 标识符），
  把正常结果页误判成反爬页。修法：收紧为「人机验证」句式。
* **⑤ relevance 守门对纯停用词失效**：整句停用词（如「考研 招生」）→ token
  列表为空 → 一律放行（反爬垃圾被当结果、源不被记失败）。修法：token 为空时
  改用「未滤停用词」的兜底 token 判定。
  （「对微信源可能误杀」子项**核实不成立**：真实搜狗结果页 10/10 通过守门。）
* **⑥ 知识入库 N+1 + 不清理已删源**：``add_chunks`` 逐条 commit（N 次事务）；
  源文件删除 / 变短后旧片段永留（``ky rag`` 会命中已删资料）。修法：单事务
  批量写入 + 按受管来源前缀清理失效片段（带存在性护栏，防误删）。

全程离线：无网络请求；健康度落盘、缓存与知识库全部指向 ``tmp_path``。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.search import health, indexer, relevance, source_registry  # noqa: E402
from tools.search import knowledge_store as ks_mod  # noqa: E402
from tools.search.cache import STALE_MAX_AGE, SearchCache  # noqa: E402
from tools.search.knowledge_store import Chunk, KnowledgeStore  # noqa: E402
from tools.search.models import SearchQuery, SearchResult  # noqa: E402
from tools.search.providers._http import looks_like_anti_bot  # noqa: E402
from tools.search.providers.base import ProviderError, SearchProvider  # noqa: E402
from tools.search.service import SearchService  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_health(tmp_path):
    """冷却状态落盘指向 tmp：不碰真实工作区 ``.memory/``，用例间互不污染。"""
    health.set_persistence(True, path=tmp_path / "cooldown.json")
    health.reset()
    yield
    health.reset()
    health.set_persistence(False, path=None)


# ══════════════════════════════════════════════════════════════════════
# ① 多路查询 × 失败源：请求数有界且失败后冷却（P1-S1/S2 行为钉住）
# ══════════════════════════════════════════════════════════════════════

_JUNK = [SearchResult(title="YouTube TV Help",
                      url="https://support.google.com/youtubetv/?hl=en",
                      snippet="Watch YouTube TV on supported devices.")]


class _JunkProvider(SearchProvider):
    """返回 200+全垃圾（模拟软性反爬）。"""

    name = "junk-src"
    priority = 5

    def __init__(self):
        self.calls = 0

    def search(self, query, *, limit=10, time_range=None):
        self.calls += 1
        return list(_JUNK)


class _BlockedProvider(SearchProvider):
    """显式反爬（ProviderError）。"""

    name = "block-src"
    priority = 5

    def __init__(self):
        self.calls = 0

    def search(self, query, *, limit=10, time_range=None):
        self.calls += 1
        raise ProviderError("示例源要求验证码/限制访问（反爬），本次跳过该源")


class _IdleProvider(SearchProvider):
    """正常源（用于冷却期测试中占位）。"""

    name = "idle-src"
    priority = 5

    def __init__(self):
        self.calls = 0

    def search(self, query, *, limit=10, time_range=None):
        self.calls += 1
        return []


def _planned(svc):
    return svc.search_planned("示例大学 计算机 复试线 2027", school="示例大学",
                              major="计算机", max_queries=6)


def test_multi_query_junk_source_bounded_and_cooled():
    """软反爬源：多路计划最多 2 次请求（阈值语义）后进冷却，不得 4 路全打。

    阴性对照：把 ``service.py`` 丢弃分支的 ``health.note_failure`` 删掉
    （回到基线行为），第 2 次请求不会冷却 → 4 路全部真发请求，``calls`` 断言变红。
    """
    provider = _JunkProvider()
    resp = _planned(SearchService(providers=[provider]))

    assert resp.queries_run >= 4, "多路规划确实展开了多路查询"
    assert provider.calls == 2, (
        f"软反爬最多 2 次请求（连续失败阈值）后必须冷却，实际 {provider.calls} 次")
    assert health.is_cooling(provider.name)


def test_multi_query_blocked_source_single_request():
    """显式反爬：首次即冷却 → 后续各路不再请求（1 次，而非 4 次）。"""
    provider = _BlockedProvider()
    resp = _planned(SearchService(providers=[provider]))

    assert resp.queries_run >= 4
    assert provider.calls == 1, "显式反爬首次即冷却，不得在多路里重复挨打"
    assert health.is_cooling(provider.name)


def test_multi_query_cooling_source_zero_requests():
    """已冷却源：多路计划 0 次请求（冷却过滤在 provider 选择处生效）。"""
    provider = _IdleProvider()
    health.mark_blocked(provider.name, "反爬")
    _planned(SearchService(providers=[provider]))

    assert provider.calls == 0, "冷却中的源不得被任何一路查询到"


# ══════════════════════════════════════════════════════════════════════
# ② 搜狗跳转 URL authority
# ══════════════════════════════════════════════════════════════════════


def test_sogou_jump_host_classified_as_wechat():
    """跳转 host 收录为公众号来源，权威分与 mp.weixin.qq.com 同档。"""
    assert source_registry.classify(
        "https://weixin.sogou.com/link?url=abc&type=2") == ("wechat", 0.55)
    assert source_registry.classify("mp.weixin.qq.com") == ("wechat", 0.55)


def test_sogou_result_authority_not_unknown_default():
    """provider 声明 wechat 的跳转结果：标注后 authority=0.55（修复前 0.40）。"""
    result = SearchResult(title="示例文章标题",
                          url="https://weixin.sogou.com/link?url=abc&type=2",
                          source_type="wechat")
    annotated = SearchService(providers=[])._annotate([result])[0]

    assert annotated.source_type == "wechat"
    assert annotated.authority == pytest.approx(0.55), (
        "公众号文章不得被按「未知来源」0.40 计分（声明与权威分自相矛盾）")


def test_sogou_plain_web_host_not_upgraded():
    """阴性对照：sogou 通用网页入口不得被当成公众号来源（修复不得过宽）。"""
    stype, authority = source_registry.classify("https://www.sogou.com/web?query=x")
    assert stype == "unknown"
    assert authority == source_registry.DEFAULT_AUTHORITY


# ══════════════════════════════════════════════════════════════════════
# ③ serve-stale 近义查询兜底（仅 allow_stale 路径）
# ══════════════════════════════════════════════════════════════════════

_CACHED_URL = "https://yz.example.edu.cn/notice"


def _one_result():
    return [SearchResult(title="示例大学2027招生简章", url=_CACHED_URL,
                         snippet="招生专业目录与复试分数线",
                         source_type="graduate_school", authority=0.98)]


def _expire(cache: SearchCache, provider: str, query: str, limit: int = 10,
            by: float = 3600.0) -> None:
    key = cache.make_key(provider, query, limit, None)
    cache._entries[key].stored_at -= by
    assert cache._entries[key].expired


def test_stale_fallback_covers_variant_queries(tmp_path):
    """核心修复：同题变体（后缀不同）在源不可用时也能拿到陈旧兜底。

    阴性对照：删掉 ``cache.get`` 里的 ``_find_stale_fallback`` 分支，
    本用例第一条断言变红（修复前只覆盖精确同问）。
    """
    cache = SearchCache(path=tmp_path / "c.json")
    cache.put("ddg", "示例大学 计算机 招生简章", 10, _one_result(), ttl=600)

    got = cache.get("ddg", "示例大学 计算机 复试分数线", 10, allow_stale=True)
    assert got, "同题变体必须能命中陈旧兜底（修复前精确 key 匹配覆盖不到）"
    assert got[0].extra.get("stale") is True
    assert got[0].url == _CACHED_URL, "内容必须是原缓存内容"
    assert cache.stale_serves == 1


def test_stale_fallback_scope_is_strict(tmp_path):
    """provider / limit / time_range 任一不同都不得兜底（与精确匹配同范围）。"""
    cache = SearchCache(path=tmp_path / "c.json")
    cache.put("ddg", "示例大学 计算机 招生简章", 10, _one_result(), ttl=600)
    variant = "示例大学 计算机 复试分数线"

    assert cache.get("bing", variant, 10, allow_stale=True) is None
    assert cache.get("ddg", variant, 5, allow_stale=True) is None
    assert cache.get("ddg", variant, 10, "week", allow_stale=True) is None


def test_stale_fallback_rejects_unrelated_query(tmp_path):
    """异校/异题查询（仅共享泛词）不得拿旧数据顶包。"""
    cache = SearchCache(path=tmp_path / "c.json")
    cache.put("ddg", "甲大学 法学 招生简章", 10, _one_result(), ttl=600)

    assert cache.get("ddg", "乙大学 计算机 复试分数线", 10, allow_stale=True) is None
    assert cache.get("ddg", "考研 招生", 10, allow_stale=True) is None, (
        "纯停用词查询抽不出有效词 → 不判定、不兜底")


def test_strict_mode_never_uses_fallback(tmp_path):
    """默认严格模式：变体查询仍然未命中（新鲜读必须精确）。"""
    cache = SearchCache(path=tmp_path / "c.json")
    cache.put("ddg", "示例大学 计算机 招生简章", 10, _one_result(), ttl=600)

    assert cache.get("ddg", "示例大学 计算机 复试分数线", 10) is None


def test_exact_hit_still_fresh(tmp_path):
    """精确同问仍走新鲜路径（不带 stale 标注）。"""
    cache = SearchCache(path=tmp_path / "c.json")
    cache.put("ddg", "示例大学 计算机 招生简章", 10, _one_result(), ttl=600)

    got = cache.get("ddg", "示例大学 计算机 招生简章", 10)
    assert got and not got[0].extra.get("stale")


def test_stale_fallback_serves_expired_entry_with_age(tmp_path):
    """过期条目同样可被近义兜底命中，且带真实陈旧度。"""
    cache = SearchCache(path=tmp_path / "c.json")
    cache.put("ddg", "示例大学 计算机 招生简章", 10, _one_result(), ttl=600)
    _expire(cache, "ddg", "示例大学 计算机 招生简章")

    got = cache.get("ddg", "示例大学 计算机 复试分数线", 10, allow_stale=True)
    assert got and got[0].extra.get("stale") is True
    assert got[0].extra.get("stale_age_seconds", 0) >= 3600


def test_stale_fallback_refuses_too_old_entry(tmp_path):
    """超出兜底窗口的旧条目不得被兜底（10 天前的简章对考生是负资产）。"""
    cache = SearchCache(path=tmp_path / "c.json")
    cache.put("ddg", "示例大学 计算机 招生简章", 10, _one_result(), ttl=600)
    _expire(cache, "ddg", "示例大学 计算机 招生简章", by=STALE_MAX_AGE + 3600)

    assert cache.get("ddg", "示例大学 计算机 复试分数线", 10,
                     allow_stale=True) is None


class _CoolingProvider(SearchProvider):
    name = "cooling-src"
    priority = 5

    def search(self, query, *, limit=10, time_range=None):   # pragma: no cover
        return []


def test_service_serves_stale_for_variant_query_when_source_cooling(tmp_path):
    """集成：源冷却 + 用户换了个说法追问 → 仍给出标注陈旧度的旧结果。"""
    cache = SearchCache(path=tmp_path / "c.json")
    cache.put("cooling-src", "示例大学 计算机 招生简章", 10, _one_result(), ttl=600)

    svc = SearchService(providers=[_CoolingProvider()])
    svc._cache = cache
    health.mark_blocked("cooling-src", "反爬")

    resp = svc.search(SearchQuery(text="示例大学 计算机 复试分数线", limit=10))
    assert resp.has_results, "源不可用时应给陈旧兜底而不是「无结果」"
    assert resp.results[0].extra.get("stale") is True
    assert any("陈旧" in u for u in resp.providers_used)


# ══════════════════════════════════════════════════════════════════════
# ④ "verify" marker 收紧
# ══════════════════════════════════════════════════════════════════════


def test_bare_verify_word_is_not_anti_bot():
    """正常页含 verify（英文引导语 / JS 标识符）不得被判成反爬页。"""
    assert looks_like_anti_bot(
        "<html>Please verify your admission status on the official portal.</html>") == ""
    assert looks_like_anti_bot(
        "<script>function verifyForm(){return true}</script><p>招生简章</p>") == ""


def test_human_verification_phrases_still_detected():
    """人机验证句式仍必须命中（收紧不得收成失明）。"""
    assert looks_like_anti_bot("<html>Verify you are human</html>") == "verify you are"
    assert looks_like_anti_bot(
        "<html>verify that you are not a robot</html>") == "verify that you"


def test_existing_anti_bot_markers_unaffected():
    """既有特征词全部保持命中（含 SourceVerifyCode —— 修复前被 verify 抢先）。"""
    assert looks_like_anti_bot("<html>请协助验证，SourceVerifyCode: 480928</html>") == "请协助验证"
    assert looks_like_anti_bot("<html>SourceVerifyCode: 480928</html>") == "SourceVerifyCode"
    assert looks_like_anti_bot("<html>unusual traffic detected</html>") == "unusual traffic"
    # [BOT-M1 修复] 裸 "anomaly" 已收紧为反爬专属短语，真反爬页仍必须命中
    assert looks_like_anti_bot("<html>anomaly detected</html>") == "anomaly detected"
    assert looks_like_anti_bot("<html>请输入验证码</html>") == "请输入验证码"


def test_academic_anomaly_term_is_not_anti_bot():
    """[BOT-M1 回归] 学术检索正常结果页不得被判成反爬页。

    ``looks_like_anti_bot`` 是对**整页 HTML**（含每条结果的标题与摘要）做子串匹配。
    检索「anomaly detection」这类学术主题时，裸 ``"anomaly"`` 必然出现在正常结果页里
    → 双端点皆被判反爬 → 源被raise（文案含「反爬」标记）→ health 侧首次即600s 硬封。
    修复前本用例会红。
    """
    normal_page = (
        "<html><body>"
        "<h2>Anomaly Detection in Time Series: A Survey</h2>"
        "<p class=snippet>Deep learning approaches for anomaly detection, "
        "including isolation forest and autoencoder-based methods.</p>"
        "<h2>Unsupervised Anomaly Detection Using Gaussian Mixture Models</h2>"
        "<p class=snippet>A tutorial on anomaly detection benchmarks.</p>"
        "</body></html>"
    )
    assert looks_like_anti_bot(normal_page) == "", (
        "学术术语 'anomaly' 出现在正常结果页标题/摘要里，不得触发反爬判定")


# ══════════════════════════════════════════════════════════════════════
# ⑤ relevance 守门：纯停用词查询
# ══════════════════════════════════════════════════════════════════════

_STOPWORD_QUERY = "考研 招生"


def test_pure_stopword_query_gate_no_longer_bypassed():
    """核心修复：整句停用词的查询不再一律放行垃圾。

    阴性对照：删掉 ``filter_relevant`` 里的 ``_fallback_gate_tokens`` 兜底，
    本用例变红（修复前 kept=[junk]、dropped=0，守门被整条绕过）。
    """
    junk = _JUNK[0]
    kept, dropped = relevance.filter_relevant([junk], _STOPWORD_QUERY)

    assert kept == [], "纯停用词查询下反爬垃圾必须仍被拦下"
    assert dropped == 1


def test_pure_stopword_query_keeps_real_results():
    """正常结果（含查询词）必须保留 —— 守门不是「一律拒绝」。"""
    junk = _JUNK[0]
    good = SearchResult(title="考研招生信息汇总", url="https://yz.example.edu.cn/a",
                        snippet="2027 年硕士研究生招生简章")
    kept, dropped = relevance.filter_relevant([junk, good], _STOPWORD_QUERY)

    assert len(kept) == 1 and kept[0].url == good.url
    assert dropped == 1


def test_significant_tokens_contract_unchanged():
    """打分/重排用的 significant_tokens 口径不得被本修复改动。"""
    assert relevance.significant_tokens(_STOPWORD_QUERY) == []
    assert relevance.significant_tokens("示例大学 计算机 复试线") == [
        "示例", "计算机", "复试分数线"]


def test_normal_query_gate_unchanged():
    """常规查询口径不变：垃圾被拦、真实结果保留。"""
    junk = _JUNK[0]
    kept, dropped = relevance.filter_relevant([junk], "示例大学 计算机 复试线")
    assert kept == [] and dropped == 1


def test_service_marks_junk_provider_failed_for_stopword_query(tmp_path):
    """集成：纯停用词查询 + 全垃圾 → 源如实记为失败（修复前垃圾进结果）。"""
    provider = _JunkProvider()
    svc = SearchService(providers=[provider])
    svc._cache = SearchCache(path=tmp_path / "c.json")

    resp = svc.search(SearchQuery(text=_STOPWORD_QUERY, limit=5))
    assert not resp.has_results
    failed = dict(resp.providers_failed)
    assert "junk-src" in failed and "无关" in failed["junk-src"]


# ══════════════════════════════════════════════════════════════════════
# ⑥ 知识入库：批量写入 + 失效片段清理
# ══════════════════════════════════════════════════════════════════════


class _CommitSpy:
    """统计 commit 次数的连接包装（其余方法透传）。"""

    def __init__(self, conn):
        self._conn = conn
        self.commits = 0

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def commit(self):
        self.commits += 1
        return self._conn.commit()


def test_add_chunks_is_single_transaction(tmp_path):
    """批量写入 = 1 次 commit（修复前是逐条 commit 的 N+1）。"""
    store = KnowledgeStore(tmp_path / "kb.db")
    spy = _CommitSpy(store.conn)
    store._local.conn = spy
    try:
        n = store.add_chunks([Chunk(id=f"c-{i}", text=f"片段{i}",
                                    source="universities/00000", metadata={})
                              for i in range(5)])
        assert n == 5
        assert spy.commits == 1, f"应为单事务，实际 commit {spy.commits} 次"
        assert store.count() == 5
    finally:
        store._local.conn = spy._conn
        store.close()


def test_add_chunks_rolls_back_on_failure(tmp_path):
    """任一条失败 → 整批回滚，不留部分写入（不做「部分成功」的模糊语义）。"""
    store = KnowledgeStore(tmp_path / "kb.db")
    try:
        chunks = [Chunk(id="ok-1", text="片段", source="universities/00000", metadata={}),
                  Chunk(id="bad-1", text=None, source="universities/00000", metadata={})]
        assert store.add_chunks(chunks) == 0
        assert store.count() == 0, "失败必须整批回滚（不得留下 ok-1）"
    finally:
        store.close()


def test_prune_chunks_removes_deleted_and_shrunk_sources(tmp_path):
    """清理规则：受管前缀下「不在 keep_ids」的片段全删；其它来源不碰。"""
    store = KnowledgeStore(tmp_path / "kb.db")
    try:
        store.add_chunks([
            Chunk(id=f"material-doc-{i}", text="x",
                  source="04-专业课/参考资料/doc.md", metadata={})
            for i in range(5)
        ] + [
            # Windows 风格反斜杠 source 同样受管（分隔符归一）
            Chunk(id="material-win-0", text="x",
                  source="04-专业课\\参考资料\\win.md", metadata={}),
            Chunk(id="univ-10001", text="x", source="universities/10001", metadata={}),
            Chunk(id="other-1", text="x", source="external/keep-me", metadata={}),
        ])

        removed = store.prune_chunks(
            {f"material-doc-{i}" for i in range(2)} | {"univ-10001"},
            indexer.MANAGED_SOURCE_PREFIXES)

        assert removed == 4, "应清理 doc 尾 3 段 + 反斜杠缩水源 1 段"
        rows = {r["id"] for r in store.conn.execute("SELECT id FROM chunks")}
        assert {"material-doc-0", "material-doc-1"} <= rows
        assert "material-doc-4" not in rows, "文件变短后的旧片段必须清理"
        assert "material-win-0" not in rows, "已删源（含反斜杠形态）必须清理"
        assert "other-1" in rows, "非受管来源不得被清理"
    finally:
        store.close()


def _make_workspace(root: Path, materials: dict) -> None:
    """造一个最小工作区：院校注册表 + 参考资料。"""
    (root / "data" / "universities").mkdir(parents=True)
    (root / "data" / "universities" / "registry.json").write_text(
        json.dumps({"00000": {"name": "示例大学"}}, ensure_ascii=False),
        encoding="utf-8")
    mat = root / "04-专业课" / "参考资料"
    mat.mkdir(parents=True)
    for name, text in materials.items():
        (mat / name).write_text(text, encoding="utf-8")


def test_build_index_prunes_deleted_material(tmp_path, monkeypatch, capsys):
    """集成：源文件被删除后重跑建索引，其旧片段必须从库里清掉。"""
    ws = tmp_path / "ws"
    _make_workspace(ws, {"示例资料.md": "# 示例标题\n" + "内容" * 400})
    monkeypatch.setattr(indexer, "ROOT", ws)

    store = KnowledgeStore(tmp_path / "kb.db")
    monkeypatch.setattr(ks_mod, "get_knowledge_store", lambda: store)
    try:
        indexer.build_index(enable_vector=False, show_progress=False)
        assert store.count() > 0
        sources = {r["source"] for r in
                   store.conn.execute("SELECT DISTINCT source FROM chunks")}
        assert any("示例资料.md" in s for s in sources)

        (ws / "04-专业课" / "参考资料" / "示例资料.md").unlink()
        (ws / "04-专业课" / "参考资料" / "示例资料二.md").write_text(
            "# 示例标题二\n" + "内容" * 400, encoding="utf-8")
        indexer.build_index(enable_vector=False, show_progress=False)

        sources = {r["source"] for r in
                   store.conn.execute("SELECT DISTINCT source FROM chunks")}
        assert not any("示例资料.md" in s for s in sources), (
            "已删资料的旧片段必须清理（否则 ky rag 会命中已删除资料）")
        assert any("示例资料二.md" in s for s in sources)
    finally:
        store.close()


def test_build_index_skips_prune_when_source_tree_absent(tmp_path, monkeypatch, capsys):
    """存在性护栏：工作区被剥离（参考资料目录不存在）时不得误删旧片段。"""
    ws = tmp_path / "ws"
    (ws / "data" / "universities").mkdir(parents=True)
    (ws / "data" / "universities" / "registry.json").write_text(
        json.dumps({"00000": {"name": "示例大学"}}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(indexer, "ROOT", ws)

    store = KnowledgeStore(tmp_path / "kb.db")
    monkeypatch.setattr(ks_mod, "get_knowledge_store", lambda: store)
    try:
        # 预置一份「上一个工作区」留下的资料片段
        store.add_chunks([Chunk(id="material-old-0", text="x",
                                source="04-专业课/参考资料/old.md", metadata={})])
        indexer.build_index(enable_vector=False, show_progress=False)

        rows = {r["id"] for r in store.conn.execute("SELECT id FROM chunks")}
        assert "material-old-0" in rows, (
            "参考资料目录不存在 ≠ 资料被删空 —— 不得据此清理旧片段")
    finally:
        store.close()
