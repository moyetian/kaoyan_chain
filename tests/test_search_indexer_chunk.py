# -*- coding: utf-8 -*-
"""检索索引器 ``chunk_text`` 边界回归测试（对应审查编号 B4）。

背景：``chunk_text`` 是公开函数，旧实现在 ``chunk_size <= 0`` 时**死循环**
（实测 ``timeout 8`` 返回 exit 124）—— ``overlap(0) >= chunk_size(0)`` 归零、
``len(text) <= 0`` 为假，进入循环后 ``end = start + 0`` 恒等、
``start = end - overlap`` 永不推进。本文件锁定：

  1. ``chunk_size <= 0`` 快速抛 ``ValueError``（fail-fast，不得挂死）；
  2. ``overlap >= chunk_size`` 时能正常终止并返回非空结果；
  3. 正常参数切片结果与预期一致（回归保护）；
  4. 空串 / 纯空白返回 ``[]``。

[W13-3 · R5-a] 追加「切片去重叠（开关式）」契约：

  5. ``overlap=0`` 时各 chunk 拼接 == 原文（无跨块重复）——确定性断言，
     不依赖探针 / 检索质量结论；
  6. ``KY_RAG_OVERLAP`` 开关解析（未设置→50 / 0→去重叠 / 非法值回退 50）
     与生产调用点（``load_materials``）的实际透传；
  7. 默认值防回退（函数签名与模块常量都必须是 50）。

边界说明：中文场景无文献支撑 + 探针样本小，本批只提供机制、**不宣称**
「检索质量不降」；去重叠收益待 W14+ 实测观察。

测试数据一律使用中性占位（``abcdefg`` 等），不含任何真实身份信息。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.search import indexer  # noqa: E402
from tools.search.indexer import chunk_text, resolve_overlap  # noqa: E402


class TestChunkTextBoundaries:
    def test_non_positive_chunk_size_raises_fast(self):
        """chunk_size=0 必须 fail-fast 抛 ValueError，而不是死循环。"""
        with pytest.raises(ValueError):
            chunk_text("abcdefg", chunk_size=0, overlap=0)

    def test_negative_chunk_size_raises(self):
        """负数 chunk_size 同样拒绝（公开函数入口校验）。"""
        with pytest.raises(ValueError):
            chunk_text("abcdefg", chunk_size=-5, overlap=0)

    def test_overlap_not_smaller_than_chunk_size_terminates(self):
        """overlap >= chunk_size 时必须正常终止并返回非空结果。"""
        result = chunk_text("abcdefg", chunk_size=5, overlap=5)
        assert result  # 非空
        assert result == ["abcde", "fg"]

    def test_normal_params_regression(self):
        """正常参数（500/50）切片结果与预期一致（无标点，按字符步进）。"""
        text = "abcdefghij" * 120  # 1200 字符，无标点
        result = chunk_text(text, chunk_size=500, overlap=50)
        assert result == [text[0:500], text[450:950], text[900:1200]]

    def test_empty_and_whitespace_return_empty_list(self):
        """空串 / 纯空白返回 []（旧实现返回 [""]，下游会插入空 chunk）。"""
        assert chunk_text("") == []
        assert chunk_text("   \n\t  ") == []
        assert chunk_text(None) == []


class TestChunkTextOverlap:
    """[W13-3 · R5-a] ``overlap`` 参数化的确定性断言（无跨块重复）。"""

    #: 2000 字符，无标点、无空白 —— 避免句子边界调整与 ``strip()`` 干扰
    #: 「拼接 == 原文」的精确比较（有标点时 chunk 尾会被裁到句号处，
    #: 但 start 同步调整，拼接仍等于原文；此处用最简输入降低理解成本）。
    TEXT = "甲乙丙丁戊己庚辛壬癸" * 200

    def test_zero_overlap_concat_equals_original(self):
        """overlap=0：各 chunk 直接拼接 == 原文（无跨块重复）。"""
        result = chunk_text(self.TEXT, chunk_size=500, overlap=0)
        assert "".join(result) == self.TEXT
        assert result == [self.TEXT[i:i + 500] for i in range(0, 2000, 500)]

    def test_overlap_50_has_cross_chunk_duplication(self):
        """对照：overlap=50 时存在跨块重复（拼接 > 原文，相邻块共享 50 字符）。"""
        result = chunk_text(self.TEXT, chunk_size=500, overlap=50)
        joined = "".join(result)
        assert joined != self.TEXT
        assert len(joined) == len(self.TEXT) + 50 * (len(result) - 1)
        assert result[1][:50] == result[0][-50:]


class TestOverlapEnvSwitch:
    """[W13-3 · R5-a] ``KY_RAG_OVERLAP`` 开关：解析 + 生产调用透传。"""

    def test_resolve_default_when_unset(self, monkeypatch):
        monkeypatch.delenv(indexer.RAG_OVERLAP_ENV, raising=False)
        assert resolve_overlap() == 50

    def test_resolve_zero_enables_dedup(self, monkeypatch):
        """开关启用（=0）→ 返回 0（去重叠）。"""
        monkeypatch.setenv(indexer.RAG_OVERLAP_ENV, "0")
        assert resolve_overlap() == 0

    def test_resolve_explicit_values_passthrough(self, monkeypatch):
        monkeypatch.setenv(indexer.RAG_OVERLAP_ENV, "10")
        assert resolve_overlap() == 10
        monkeypatch.setenv(indexer.RAG_OVERLAP_ENV, "50")
        assert resolve_overlap() == 50

    @pytest.mark.parametrize("bad", ["abc", "-1", "1.5", "   "])
    def test_resolve_invalid_falls_back_to_default(self, monkeypatch, bad):
        """非法值（非整数 / 负数 / 空白）→ 回退 50，不得抛崩或产生负重叠。"""
        monkeypatch.setenv(indexer.RAG_OVERLAP_ENV, bad)
        assert resolve_overlap() == 50

    @staticmethod
    def _write_material(tmp_path: Path, text: str) -> None:
        materials = tmp_path / "04-专业课" / "参考资料"
        materials.mkdir(parents=True)
        (materials / "sample.md").write_text(text, encoding="utf-8")

    def test_load_materials_uses_env_overlap(self, tmp_path, monkeypatch):
        """开关启用时 ``load_materials`` 实际按 overlap=0 切片（透传验证）。"""
        text = "甲乙丙丁戊己庚辛壬癸" * 320  # 3200 字符，无标点无空白
        self._write_material(tmp_path, text)
        monkeypatch.setattr(indexer, "ROOT", tmp_path)
        monkeypatch.setenv(indexer.RAG_OVERLAP_ENV, "0")

        docs = list(indexer.load_materials())
        assert "".join(d.text for d in docs) == text  # 无重叠 → 还原原文

    def test_load_materials_default_overlap_when_unset(self, tmp_path, monkeypatch):
        """未启用开关时 ``load_materials`` 仍用 50（默认行为不变，对照）。"""
        text = "甲乙丙丁戊己庚辛壬癸" * 320
        self._write_material(tmp_path, text)
        monkeypatch.setattr(indexer, "ROOT", tmp_path)
        monkeypatch.delenv(indexer.RAG_OVERLAP_ENV, raising=False)

        docs = list(indexer.load_materials())
        joined = "".join(d.text for d in docs)
        assert joined != text
        assert len(joined) > len(text)  # 重叠使字符总量增加


class TestDefaultOverlapUnchanged:
    """[W13-3 · R5-a] 默认值防回退：保守 50 是契约，不得静默改变。"""

    def test_chunk_text_signature_default_is_50(self):
        assert inspect.signature(chunk_text).parameters["overlap"].default == 50
        assert indexer.DEFAULT_OVERLAP == 50

    def test_omitted_overlap_behaves_like_50(self):
        text = "abcdefghij" * 120  # 1200 字符
        assert chunk_text(text) == chunk_text(text, overlap=50)
