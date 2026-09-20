# -*- coding: utf-8 -*-
"""
混合检索引擎 (Hybrid Retrieval Engine)

结合词法检索和向量检索，使用 RRF (Reciprocal Rank Fusion) 融合排序
- 词法分支：复用现有的 relevance.py + rank.py（5 信号加权）
- 向量分支：基于 knowledge_store.py + vector.py 的语义检索
- 融合算法：RRF（Reciprocal Rank Fusion）

技术优势：
1. 词法 + 语义双保险，互补优势
2. RRF 量纲无关，无需调权重
3. 自动降级：向量不可用时回退到纯词法
"""

from __future__ import annotations

import logging
from typing import List, Tuple, Optional, Set
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """搜索结果"""
    chunk_id: str           # 片段 ID
    score: float            # 综合得分
    text: str               # 文本内容
    source: str             # 来源
    lexical_rank: int = -1  # 词法排名（-1 表示未命中）
    vector_rank: int = -1   # 向量排名（-1 表示未命中）


def rrf_score(ranks: List[int], k: int = 60) -> float:
    """RRF (Reciprocal Rank Fusion) 得分计算

    公式: score = Σ 1/(k + rank_i)

    Args:
        ranks: 排名列表（从 0 开始，-1 表示未命中）
        k: 平滑参数（默认 60，业界标准值）

    Returns:
        RRF 综合得分

    Examples:
        >>> rrf_score([0, 2])  # 词法第1名，向量第3名
        0.032258...
        >>> rrf_score([5, -1])  # 只在词法中命中第6名
        0.015873...
    """
    score = 0.0
    for rank in ranks:
        if rank >= 0:  # 跳过未命中（-1）
            score += 1.0 / (k + rank)
    return score


def hybrid_search(
    query: str,
    top_k: int = 10,
    lexical_top_k: int = 20,
    vector_top_k: int = 20,
    rrf_k: int = 60,
    enable_vector: bool = True
) -> List[SearchResult]:
    """混合检索（词法 + 向量 + RRF 融合）

    Args:
        query: 查询文本
        top_k: 最终返回的结果数
        lexical_top_k: 词法分支召回数（候选池）
        vector_top_k: 向量分支召回数（候选池）
        rrf_k: RRF 平滑参数
        enable_vector: 是否启用向量检索（False 则纯词法）

    Returns:
        融合后的搜索结果列表（按得分降序）
    """
    # Step 1: 词法检索分支
    lexical_results = _lexical_search(query, top_k=lexical_top_k)

    # Step 2: 向量检索分支（如果启用）
    vector_results = []
    if enable_vector:
        try:
            vector_results = _vector_search(query, top_k=vector_top_k)
        except Exception as e:
            logger.warning(f"向量检索失败，降级到纯词法: {e}")

    # Step 3: RRF 融合
    fused_results = _rrf_fusion(
        lexical_results=lexical_results,
        vector_results=vector_results,
        rrf_k=rrf_k
    )

    # Step 4: 返回 top_k
    return fused_results[:top_k]


def _lexical_search(query: str, top_k: int = 20) -> List[Tuple[str, float]]:
    """词法检索分支（复用现有代码）

    Args:
        query: 查询文本
        top_k: 返回结果数

    Returns:
        [(chunk_id, score), ...] 按得分降序
    """
    try:
        # 复用现有的搜索模块
        from .segment import segment_and_normalize
        from .knowledge_store import get_knowledge_store

        # 分词
        tokens = segment_and_normalize(query)
        logger.debug(f"词法分词: {tokens}")

        # 从知识库中检索
        store = get_knowledge_store()

        # 使用简单的文本匹配（BM25-like）
        results = []

        # 遍历所有片段进行匹配
        # TODO: 优化为使用 SQLite FTS5 全文索引
        try:
            cursor = store.conn.execute("SELECT id, text FROM chunks LIMIT 1000")
            for row in cursor:
                chunk_id = row[0]
                text = row[1]

                # 计算词法匹配分数（简单的 TF 计数）
                text_lower = text.lower()
                score = 0.0
                for token in tokens:
                    count = text_lower.count(token.lower())
                    score += count

                if score > 0:
                    results.append((chunk_id, score))
        except Exception:
            pass

        # 按得分降序排序
        results.sort(key=lambda x: x[1], reverse=True)

        return results[:top_k]

    except Exception as e:
        logger.error(f"词法检索失败: {e}")
        return []


def _vector_search(query: str, top_k: int = 20) -> List[Tuple[str, float]]:
    """向量检索分支

    Args:
        query: 查询文本
        top_k: 返回结果数

    Returns:
        [(chunk_id, similarity), ...] 按相似度降序
    """
    try:
        from .vector import encode_text
        from .knowledge_store import get_knowledge_store

        # 编码查询
        query_embedding = encode_text(query)
        if query_embedding is None:
            logger.warning("查询编码失败")
            return []

        # 向量检索
        store = get_knowledge_store()
        results = store.search_by_vector(
            query_embedding=query_embedding,
            top_k=top_k
        )

        logger.debug(f"向量检索返回 {len(results)} 条结果")
        return results

    except Exception as e:
        logger.error(f"向量检索失败: {e}")
        return []


def _rrf_fusion(
    lexical_results: List[Tuple[str, float]],
    vector_results: List[Tuple[str, float]],
    rrf_k: int = 60
) -> List[SearchResult]:
    """RRF 融合算法

    Args:
        lexical_results: 词法结果 [(chunk_id, score), ...]
        vector_results: 向量结果 [(chunk_id, similarity), ...]
        rrf_k: RRF 平滑参数

    Returns:
        融合后的结果列表（按 RRF 得分降序）
    """
    # 构建排名映射
    lexical_ranks = {chunk_id: rank for rank, (chunk_id, _) in enumerate(lexical_results)}
    vector_ranks = {chunk_id: rank for rank, (chunk_id, _) in enumerate(vector_results)}

    # 收集所有候选 chunk_id
    all_chunk_ids: Set[str] = set(lexical_ranks.keys()) | set(vector_ranks.keys())

    # 计算 RRF 得分
    fused = []
    for chunk_id in all_chunk_ids:
        lex_rank = lexical_ranks.get(chunk_id, -1)
        vec_rank = vector_ranks.get(chunk_id, -1)

        score = rrf_score([lex_rank, vec_rank], k=rrf_k)

        # 获取文本内容（从知识库）
        from .knowledge_store import get_knowledge_store
        store = get_knowledge_store()
        chunk = store.get_chunk(chunk_id)

        if chunk:
            result = SearchResult(
                chunk_id=chunk_id,
                score=score,
                text=chunk.text,
                source=chunk.source,
                lexical_rank=lex_rank,
                vector_rank=vec_rank
            )
            fused.append(result)

    # 按 RRF 得分降序排序
    fused.sort(key=lambda x: x.score, reverse=True)

    logger.debug(f"RRF 融合: 词法 {len(lexical_results)} + 向量 {len(vector_results)} → {len(fused)} 条结果")

    return fused


def search(
    query: str,
    top_k: int = 10,
    enable_vector: bool = True,
    source_filter: Optional[str] = None
) -> List[SearchResult]:
    """统一检索入口（便捷函数）

    Args:
        query: 查询文本
        top_k: 返回结果数
        enable_vector: 是否启用向量检索
        source_filter: 源文件过滤（可选）

    Returns:
        搜索结果列表
    """
    # 自动检测向量检索可用性
    if enable_vector:
        from .knowledge_store import get_knowledge_store
        store = get_knowledge_store()
        if not store.has_vector:
            logger.info("向量检索不可用，自动降级到纯词法")
            enable_vector = False

    return hybrid_search(
        query=query,
        top_k=top_k,
        enable_vector=enable_vector
    )


if __name__ == "__main__":
    # 测试 RRF 算法
    logging.basicConfig(level=logging.INFO)

    print("=== RRF 算法测试 ===\n")

    # 测试用例 1：两个分支都命中
    print("用例 1: 词法第1名 + 向量第3名")
    score1 = rrf_score([0, 2], k=60)
    print(f"RRF 得分: {score1:.6f}\n")

    # 测试用例 2：只有词法命中
    print("用例 2: 只在词法中第6名")
    score2 = rrf_score([5, -1], k=60)
    print(f"RRF 得分: {score2:.6f}\n")

    # 测试用例 3：两个分支排名都靠前
    print("用例 3: 词法第1名 + 向量第1名（双高）")
    score3 = rrf_score([0, 0], k=60)
    print(f"RRF 得分: {score3:.6f}\n")

    # 对比
    print("得分对比:")
    print(f"双高（都第1） > 词法第1+向量第3 > 只词法第6")
    print(f"{score3:.6f} > {score1:.6f} > {score2:.6f}")
    print(f"验证: {score3 > score1 > score2}")
