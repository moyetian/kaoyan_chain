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

测试数据一律使用中性占位（``abcdefg`` 等），不含任何真实身份信息。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.search.indexer import chunk_text  # noqa: E402


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
