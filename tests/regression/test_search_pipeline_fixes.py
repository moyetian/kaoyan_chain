# -*- coding: utf-8 -*-
"""# [Regress] 检索链路回归：site: 语法解析 / stale 夹 limit / 四科索引 / 向量链路。
修复来源：2026-10-11《第五轮深审报告》（针对远端 af42f05，见
`桌面/考研学习chain分析报告/kaoyan_chain_第五轮深审报告_2026-10-11.md`）。覆盖：

  P0-2 向量检索 5 缺陷：sqlite_vec.load 优先加载 / tokenizer 门禁去
       encode_plus / token_type_ids 动态 feed / 维度 384→512 参数化 /
       维度不符只跳向量不回滚文本；download_model 换 Xenova 源。
  P1：site: 文本解析进 domains / serve-stale 全源冷却夹 limit /
      RAG 索引扫四科。

隔离：不联网、不写真实工作区（全部 tmp_path）；文本断言只针对本批新增行为。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ════════════════════════════════════════════════════════════════
# 1. site: 文本语法解析（P1）
# ════════════════════════════════════════════════════════════════

class TestSiteSyntaxParsing:
    """SearchQuery 构造时把 text 里的 ``site:域名`` 解析进 domains 字段。"""

    @staticmethod
    def _q(text: str, **kw: object):
        from tools.search.models import SearchQuery
        return SearchQuery(text=text, **kw)

    def test_site_parsed_into_domains(self):
        """[红线] `site:xiaohongshu.com 考研经验` → domains 含域名、text 剥离。

        修复前：site: 只在查询文本里（provider 层不解析该语法），三平台
        定向检索实际搜的是全网。阴性对照：去掉 __post_init__ 解析 → 红。
        """
        q = self._q("site:xiaohongshu.com 考研经验")
        assert q.domains == ("xiaohongshu.com",)
        assert q.text == "考研经验"
        assert "site:" not in q.text

    def test_site_mid_text_and_case(self):
        """site: 在文本中间、大小写混合、带尾斜杠均正确归一。"""
        q = self._q("考研 避坑 SITE:www.ZhiHu.com/ 经验")
        assert q.domains == ("www.zhihu.com",)
        assert q.text == "考研 避坑 经验"

    def test_multiple_site_merged_with_existing_domains(self):
        """多个 site: 与既有 domains 合并去重保序。"""
        q = self._q("site:a.com x site:b.com", domains=("b.com", "c.com"))
        assert q.domains == ("b.com", "c.com", "a.com")

    def test_no_site_text_unchanged(self):
        """阴性对照：无 site: 的文本原样不动（含「网站说明」这类不含语法的词）。"""
        q = self._q("网站说明 考研经验")
        assert q.domains == ()
        assert q.text == "网站说明 考研经验"

    def test_site_space_not_parsed(self):
        """`site: 说明`（冒号后带空格）不是语法，不解析。"""
        q = self._q("site: 说明文档")
        assert q.domains == ()
        assert q.text == "site: 说明文档"


# ════════════════════════════════════════════════════════════════
# 2. serve-stale 全源冷却夹 limit（P1）
# ════════════════════════════════════════════════════════════════

class TestStaleServeLimit:
    """全源冷却时 stale 兜底结果必须夹到 q.limit。"""

    def test_all_cooling_stale_truncated_to_limit(self, monkeypatch: pytest.MonkeyPatch):
        """[红线] 多源合并的 stale 结果按 limit 截断。

        修复前：全源冷却分支直接 return stale（多源各 10 条合并），
        实测「要 5 条给 18 条」；正常路径有 ranked[:q.limit] 截断，
        该分支绕过。阴性对照：去掉 [: q.limit] → 红。
        """
        from tools.search import service as svc_mod
        from tools.search.models import SearchQuery, SearchResult

        svc = svc_mod.SearchService(providers=[])

        # 打桩：冷却名单非空 + stale 返回 18 条
        monkeypatch.setattr(svc, "cooling_down", lambda names: [("bing", "反爬冷却")])
        fake_stale = [
            SearchResult(title=f"t{i}", url=f"https://e.com/{i}",
                         engine="bing", extra={"stale": True})
            for i in range(18)
        ]
        monkeypatch.setattr(svc, "_stale_for", lambda names, q: list(fake_stale))

        resp = svc.search(SearchQuery(text="q", limit=5))
        assert len(resp.results) == 5
        assert resp.candidates == 18


# ════════════════════════════════════════════════════════════════
# 3. RAG 索引四科扫描（P1）
# ════════════════════════════════════════════════════════════════

class TestIndexerFourSubjects:
    """load_materials 扫四科参考资料目录；chunk_id 带科目前缀防撞。"""

    @staticmethod
    def _fake_root(tmp_path: Path) -> Path:
        """造一个四科结构的最小工作区。"""
        for subj, fname in (("01-数学", "高数笔记.md"), ("02-英语", "长难句.md"),
                            ("03-思想政治理论", "马原.md"), ("04-专业课", "真题.md")):
            d = tmp_path / subj / "参考资料"
            d.mkdir(parents=True)
            (d / fname).write_text(f"# {fname}\n内容 A", encoding="utf-8")
        return tmp_path

    def test_scans_all_four_subjects(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """[红线] 四科资料都被扫到；修复前只有 04-专业课。

        阴性对照：还原只扫 04-专业课 → 本用例红（缺 3 科）。
        """
        import tools.search.indexer as idx

        fake = self._fake_root(tmp_path)
        monkeypatch.setattr(idx, "ROOT", fake)

        docs = list(idx.load_materials())
        sources = {d.source.replace("\\", "/") for d in docs}
        assert any(s.startswith("01-数学/参考资料") for s in sources)
        assert any(s.startswith("02-英语/参考资料") for s in sources)
        assert any(s.startswith("03-思想政治理论/参考资料") for s in sources)
        assert any(s.startswith("04-专业课/参考资料") for s in sources)

    def test_chunk_id_subject_prefixed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """chunk_id 带科目前缀：不同科目同名文件不撞 id。"""
        import tools.search.indexer as idx

        fake = self._fake_root(tmp_path)
        # 再放两个同名文件进不同科目
        for subj in ("01-数学", "02-英语"):
            (fake / subj / "参考资料" / "真题.md").write_text("# 同名\n内容", encoding="utf-8")
        monkeypatch.setattr(idx, "ROOT", fake)

        docs = list(idx.load_materials())
        ids = [d.id for d in docs]
        assert len(ids) == len(set(ids)), "chunk_id 撞了"
        assert any(i.startswith("material-01-数学-") for i in ids)
        assert any(i.startswith("material-02-英语-") for i in ids)

    def test_managed_prefixes_cover_four(self):
        """MANAGED_SOURCE_PREFIXES 与四科目录同步（prune 语义一致）。"""
        import tools.search.indexer as idx
        for subj in idx.MATERIAL_SUBJECT_DIRS:
            assert f"{subj}/参考资料" in idx.MANAGED_SOURCE_PREFIXES
        assert "universities/" in idx.MANAGED_SOURCE_PREFIXES


# ════════════════════════════════════════════════════════════════
# 4. 向量链路（P0-2）
# ════════════════════════════════════════════════════════════════

class TestVectorPipeline:
    """向量链路 5 缺陷修复：常量 / 加载 / tokenizer / 输入 / 维度回滚。"""

    def test_embedding_dim_consistency(self):
        """[红线] 维度常量单一真源：vector 与 knowledge_store 都是 512。

        修复前两处硬编码 384（bge-small-en 维度），中文版实际 512。
        阴性对照：任一处改回 384 → 红。
        """
        from tools.search import knowledge_store as ks_mod
        from tools.search import vector as vec_mod
        assert vec_mod.EMBEDDING_DIM == 512
        assert ks_mod.DEFAULT_EMBEDDING_DIM == 512
        assert vec_mod.EMBEDDING_DIM == ks_mod.DEFAULT_EMBEDDING_DIM

    def test_sqlite_vec_package_api_preferred(self, monkeypatch: pytest.MonkeyPatch,
                                              tmp_path: Path):
        """[红线] 加载顺序：sqlite_vec.load(conn) 优先于裸名 load_extension。

        修复前只试裸名（vec0 / vec0.dll…），pip 装的 sqlite-vec 动态库在
        包目录内、裸名必然失败（实测 has_vector 恒 False）。阴性对照：
        去掉 sqlite_vec 分支 → 本用例红（走裸名分支 → 返回 False）。
        """
        import types

        import tools.search.knowledge_store as ks_mod

        called = {"pkg": 0, "bare": 0}

        fake_sqlite_vec = types.ModuleType("sqlite_vec")

        def fake_load(conn: object) -> None:
            called["pkg"] += 1

        fake_sqlite_vec.load = fake_load  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "sqlite_vec", fake_sqlite_vec)

        # [2026-10-13 CI 修复·macOS 特有红] 不再依赖真连接的
        # enable_load_extension：macOS 的 python.org 构建禁用 SQLite 扩展
        # 加载（调用抛 NotSupportedError）→ 旧写法在 macOS 走「enable 失败
        # → return False」分支，断言 ok is True 必红（Windows/Linux 的
        # Python 默认启用扩展加载，故仅 macOS 三格红）。鸭子类型假连接使
        # 判定与平台无关：enable 放行 + vec_version 探测返回假版本。
        class _FakeConn:
            def enable_load_extension(self, enable: bool) -> None:
                pass

            def execute(self, sql: str) -> "_FakeConn":
                return self

            def fetchone(self) -> tuple:
                return ("v0.1.9-fake",)

        # 用 __new__ 绕开构造期（构造会先自调一次加载，干扰计数）
        store = ks_mod.KnowledgeStore.__new__(ks_mod.KnowledgeStore)
        conn = _FakeConn()
        ok = store._try_load_vec_extension(conn)
        assert ok is True
        assert called["pkg"] == 1, "应优先走 sqlite_vec.load"

    def test_sqlite_vec_missing_falls_back_to_bare(self, monkeypatch: pytest.MonkeyPatch):
        """阴性对照：sqlite_vec 包不可用时回落裸名加载（不直接失败）。"""
        import sqlite3

        import tools.search.knowledge_store as ks_mod

        # 确保 import sqlite_vec 抛 ImportError
        monkeypatch.setitem(sys.modules, "sqlite_vec", None)

        # [2026-10-12 环境无关化] 裸名加载能否成功是环境真态：Windows 上
        # 同进程内若已有真实加载（另一文件的真库用例）把 vec0.dll 映射进
        # 进程，LoadLibrary 按基名直接命中已加载模块 → 裸名「成功」；且
        # sqlite3.Connection 是 C 类型，类属性不可 monkeypatch。判据只验
        # 「裸名不可用 → 返回 False 而非崩」，故用鸭子类型假连接让裸名
        # 加载必然失败（环境无关、顺序无关）。
        class _FakeConn:
            def enable_load_extension(self, enable: bool) -> None:
                pass

            def load_extension(self, name: str) -> None:
                raise sqlite3.OperationalError(f"no such module: {name}")

        store = ks_mod.KnowledgeStore.__new__(ks_mod.KnowledgeStore)
        conn = _FakeConn()
        # 裸名 load_extension("vec0") 在无扩展路径时抛错 → 全部分支失败
        ok = store._try_load_vec_extension(conn)
        assert ok is False

    def test_tokenizers_tokenizer_branch(self):
        """[红线] tokenizers.Tokenizer（encode_batch）可编码。

        修复前门禁 hasattr(tokenizer, 'encode_plus')：tokenizers.Tokenizer
        没有该方法 → 永远 raise「无可用 tokenizer」。阴性对照：还原门禁 → 红。
        """
        from tools.search.vector import VectorEncoder

        class _Enc:
            def __init__(self, ids: list[int]):
                self.ids = ids

        class _FakeTok:
            def encode_batch(self, texts: list[str]):
                return [_Enc([1, 2, 3]) for _ in texts]

            def token_to_id(self, tok: str):
                return 0

        enc = VectorEncoder.__new__(VectorEncoder)
        enc.max_length = 512
        enc.tokenizer = _FakeTok()
        out = enc._tokenize(["a", "b"])
        assert out["input_ids"].shape == (2, 3)
        assert out["attention_mask"].tolist() == [[1, 1, 1], [1, 1, 1]]

    def test_transformers_callable_branch(self):
        """[红线] transformers 风格 callable tokenizer 可编码（v5 无 encode_plus）。

        阴性对照：门禁改回 hasattr('encode_plus') → 本假对象（无 encode_plus）
        被拒 → 红。
        """
        import numpy as np

        from tools.search.vector import VectorEncoder

        class _FakeHF:
            def __call__(self, texts: list[str], padding: object = None,
                         truncation: object = None, max_length: object = None,
                         return_tensors: object = None):
                n = len(texts)
                return {
                    "input_ids": np.ones((n, 4), dtype=np.int32),
                    "attention_mask": np.ones((n, 4), dtype=np.int32),
                }

        enc = VectorEncoder.__new__(VectorEncoder)
        enc.max_length = 512
        enc.tokenizer = _FakeHF()
        out = enc._tokenize(["a", "b"])
        assert out["input_ids"].shape == (2, 4)
        assert out["input_ids"].dtype == np.int64

    def test_token_type_ids_fed_when_required(self):
        """[红线] 模型签名含 token_type_ids 时 feed 必须带它（全零）。

        修复前只传 input_ids/attention_mask → onnxruntime 报
        "Required inputs are missing"。阴性对照：还原固定两输入 → 红。
        """
        import numpy as np

        from tools.search.vector import VectorEncoder

        captured = {}

        class _Inp:
            def __init__(self, name: str):
                self.name = name

        class _Session:
            def get_inputs(self):
                return [_Inp("input_ids"), _Inp("attention_mask"),
                        _Inp("token_type_ids")]

            def run(self, out: object, feed: dict):
                captured["feed"] = feed
                return [np.zeros((1, 2, 8), dtype=np.float32)]

        enc = VectorEncoder.__new__(VectorEncoder)
        enc._loaded = True
        enc.session = _Session()
        enc.max_length = 512
        enc.embedding_dim = 512
        enc.tokenizer = None
        enc._tokenize = lambda texts: {
            "input_ids": np.ones((1, 2), dtype=np.int64),
            "attention_mask": np.ones((1, 2), dtype=np.int64),
        }
        out = enc.encode("测试")
        assert set(captured["feed"]) == {"input_ids", "attention_mask", "token_type_ids"}
        assert captured["feed"]["token_type_ids"].tolist() == [[0, 0]]
        assert len(out) == 8

    def test_dim_mismatch_skips_vector_keeps_text(self, tmp_path: Path):
        """[红线] 维度不符只跳向量、文本照常入库（不整批回滚）。

        修复前：512 维向量撞 384 维表 → INSERT 异常 → 整个 add_chunks
        回滚返回 0（连文本一起丢，「新库一个片段都写不进」）。现文本
        入库成功、向量行跳过（返回 len(chunks)）。阴性对照：去掉维度
        前置校验且让向量写入异常冒泡 → 返回 0 → 红。
        """
        from tools.search.knowledge_store import Chunk, KnowledgeStore

        store = KnowledgeStore(tmp_path / "dim.db", embedding_dim=4)
        # 伪造 has_vector=True + 建出 vec_chunks 表（不依赖真实 sqlite-vec）
        store.has_vector = True
        conn = store.conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS vec_chunks (chunk_id TEXT, embedding BLOB)")
        conn.commit()

        chunks = [
            Chunk(id="c1", text="文本一", source="s", metadata={},
                  embedding=[0.0] * 8),  # 8 维 ≠ 表 4 维 → 跳过
            Chunk(id="c2", text="文本二", source="s", metadata={},
                  embedding=None),
        ]
        n = store.add_chunks(chunks)
        assert n == 2, "文本必须全部入库（向量行跳过不拖累）"
        rows = conn.execute("SELECT id FROM chunks ORDER BY id").fetchall()
        assert [r[0] for r in rows] == ["c1", "c2"]
        # 向量表无 c1（维度不符被跳过）
        vec_rows = conn.execute("SELECT chunk_id FROM vec_chunks").fetchall()
        assert vec_rows == []
        store.close()

    def test_download_model_source_and_dim(self):
        """download_model 配置：Xenova 源（非 404 的 BAAI onnx 路径）+ 512 维。"""
        from tools.search.download_model import MODEL_CONFIG
        cfg = MODEL_CONFIG["bge-small-zh-v1.5"]
        assert "Xenova/bge-small-zh-v1.5" in cfg["onnx_url"]
        assert cfg["embedding_dim"] == 512
        assert "tokenizer_config_url" in cfg
