# -*- coding: utf-8 -*-
"""
混合检索引擎 (Hybrid Retrieval Engine)

结合词法检索和向量检索，使用 RRF (Reciprocal Rank Fusion) 融合排序
- 词法分支：直接对知识库 chunks 表做分词 TF 计数打分（**不复用** relevance.py /
  rank.py —— 那两个模块服务的是「网页检索线索」链路，与本地片段检索无关；
  此处原先的注释声称复用它们，属不实描述，已更正）
- 向量分支：基于 knowledge_store.py + vector.py 的语义检索
- 融合算法：RRF（Reciprocal Rank Fusion）

技术优势：
1. 词法 + 语义双保险，互补优势
2. RRF 量纲无关，无需调权重
3. 自动降级：向量不可用时回退到纯词法，并**显式**给出降级原因
   （`SearchResult.degraded` / `degrade_reason`；0 条结果时用
   `search_with_diagnostics()` 取诊断）
"""

from __future__ import annotations

import logging
from typing import List, Tuple, Optional, Set
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class VectorUnavailable(RuntimeError):
    """向量分支不可用（异常消息即人话降级原因，可直接展示给用户）。"""


@dataclass
class SearchResult:
    """一条**本地知识库片段**命中。

    命名提醒：``tools/search/models.py`` 里也有一个 ``SearchResult``，那是
    「网页检索线索」（title / url / snippet / authority），与本类
    （chunk_id / text / source / rank）语义无关。此处保留历史名以免破坏既有调用方。
    """
    chunk_id: str           # 片段 ID
    score: float            # 综合得分
    text: str               # 文本内容
    source: str             # 来源
    lexical_rank: int = -1  # 词法排名（-1 表示未命中）
    vector_rank: int = -1   # 向量排名（-1 表示未命中）
    #: 本次检索是否降级（向量分支未参与，结果全部来自词法分支）。
    #: 降级是「一次检索」的属性，这里逐条冗余一份，便于只拿到结果列表的
    #: 调用方也能感知；**0 条结果**的场景请改用 search_with_diagnostics()。
    degraded: bool = False
    #: 降级原因（人话，可直接展示）；未降级时为空串。
    degrade_reason: str = ""


@dataclass
class SearchOutcome:
    """一次混合检索的完整结果：命中列表 + 降级诊断。

    存在的理由：降级是「本次检索」的属性而非单条结果的属性。0 条结果时
    ``List[SearchResult]`` 里没有任何对象可以承载提示，调用方就无法区分
    「真的没有相关内容」与「向量分支挂了所以只搜到这些」。
    """
    results: List[SearchResult]
    degraded: bool = False
    degrade_reason: str = ""
    lexical_count: int = 0   # 词法分支召回条数（融合前）
    vector_count: int = 0    # 向量分支召回条数（融合前）


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
    # [S3 修复] k<=0 时 1/(k+rank) 在 rank=0 处除零；钳到最小 1。
    k = max(1, int(k))
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
    enable_vector: bool = True,
    source_filter: Optional[str] = None
) -> List[SearchResult]:
    """混合检索（词法 + 向量 + RRF 融合）

    Args:
        query: 查询文本
        top_k: 最终返回的结果数
        lexical_top_k: 词法分支召回数（候选池）
        vector_top_k: 向量分支召回数（候选池）
        rrf_k: RRF 平滑参数
        enable_vector: 是否启用向量检索（False 则纯词法）
        source_filter: 源文件过滤（[G1 修复] 此前 search() 收了该参数却静默
            丢弃；现透传给词法与向量两分支，与 search_by_vector 同口径 LIKE）

    Returns:
        融合后的搜索结果列表（按得分降序）。每条结果带 ``degraded`` /
        ``degrade_reason``；需要「0 条结果也有诊断」时改用
        :func:`search_with_diagnostics`。
    """
    return _run_hybrid(
        query=query,
        top_k=top_k,
        lexical_top_k=lexical_top_k,
        vector_top_k=vector_top_k,
        rrf_k=rrf_k,
        enable_vector=enable_vector,
        source_filter=source_filter,
    ).results


def _run_hybrid(
    query: str,
    top_k: int = 10,
    lexical_top_k: int = 20,
    vector_top_k: int = 20,
    rrf_k: int = 60,
    enable_vector: bool = True,
    source_filter: Optional[str] = None,
    forced_reason: str = "",
) -> SearchOutcome:
    """混合检索主流程（带降级诊断的唯一实现）。

    Args:
        forced_reason: 调用方已探明的降级原因（如 sqlite-vec 未加载）。给了它
            就意味着向量分支被**外部**判定不可用，本次直接走纯词法，并把它
            作为 ``degrade_reason`` 原样上报 —— 避免把「扩展缺失」笼统说成
            「调用方关闭了向量分支」。
    """
    # Step 1: 词法检索分支
    lexical_results = _lexical_search(query, top_k=lexical_top_k,
                                      source_filter=source_filter)

    # Step 2: 向量检索分支（如果启用）
    vector_results: List[Tuple[str, float]] = []
    degraded = False
    degrade_reason = ""

    if forced_reason:
        degraded = True
        degrade_reason = forced_reason
    elif not enable_vector:
        degraded = True
        degrade_reason = "未启用向量分支（enable_vector=False）；本次为纯词法检索"
    else:
        try:
            vector_results = _vector_search(query, top_k=vector_top_k,
                                            source_filter=source_filter)
        except VectorUnavailable as e:
            degraded = True
            degrade_reason = f"{e}；本次为纯词法检索"
            logger.warning("向量检索不可用，降级到纯词法: %s", e)
        except Exception as e:
            degraded = True
            degrade_reason = (f"向量检索异常（{type(e).__name__}: {e}）；"
                              "本次已回退纯词法检索")
            logger.warning("向量检索异常，降级到纯词法: %s", e)

    # Step 3: RRF 融合
    fused_results = _rrf_fusion(
        lexical_results=lexical_results,
        vector_results=vector_results,
        rrf_k=rrf_k
    )

    # Step 4: 逐条打上本次检索的降级标记 + 返回 top_k
    results = fused_results[:top_k]
    if degraded:
        for r in results:
            r.degraded = True
            r.degrade_reason = degrade_reason

    return SearchOutcome(
        results=results,
        degraded=degraded,
        degrade_reason=degrade_reason,
        lexical_count=len(lexical_results),
        vector_count=len(vector_results),
    )


def _lexical_search(query: str, top_k: int = 20,
                    source_filter: Optional[str] = None) -> List[Tuple[str, float]]:
    """词法检索分支（复用现有代码）

    Args:
        query: 查询文本
        top_k: 返回结果数
        source_filter: 源文件过滤（LIKE 口径，与 search_by_vector 一致）

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

        # 词法打分：见下方「纯 TF 计数」说明（**不是** BM25）
        results = []

        # 遍历所有片段进行匹配
        # TODO: 优化为使用 SQLite FTS5 全文索引
        # [G7 修复] 旧实现 LIMIT 1000 使超千片段的库永久丢召回，且异常被
        # 裸 except: pass 吞掉（表缺失也报"无结果"）。现去掉 LIMIT（游标流式
        # 遍历，内存仍只保留命中项），异常记 warning 携带上下文。
        try:
            if source_filter:
                cursor = store.conn.execute(
                    "SELECT id, text FROM chunks WHERE source LIKE ?",
                    (f"%{source_filter}%",))
            else:
                cursor = store.conn.execute("SELECT id, text FROM chunks")
            for row in cursor:
                chunk_id = row[0]
                text = row[1]

                # 计算词法匹配分数：**纯 TF 计数**（无 IDF、无长度归一、
                # 无字段权重）。此前注释把它说成 BM25 家族算法，与实现不符。
                text_lower = text.lower()
                score = 0.0
                for token in tokens:
                    count = text_lower.count(token.lower())
                    score += count

                if score > 0:
                    results.append((chunk_id, score))
        except Exception as e:
            logger.warning(f"词法检索片段遍历失败（source_filter={source_filter!r}）: {e}")

        # 按得分降序排序
        results.sort(key=lambda x: x[1], reverse=True)

        return results[:top_k]

    except Exception as e:
        logger.error(f"词法检索失败: {e}")
        return []


def _vector_search(query: str, top_k: int = 20,
                   source_filter: Optional[str] = None) -> List[Tuple[str, float]]:
    """向量检索分支

    Args:
        query: 查询文本
        top_k: 返回结果数
        source_filter: 源文件过滤（透传给 search_by_vector）

    Returns:
        [(chunk_id, similarity), ...] 按相似度降序

    Raises:
        VectorUnavailable: 向量分支不可用（消息即人话原因，供上层拼装
            用户可见的降级提示）。此前这里吞掉一切异常返回 ``[]``，导致
            「模型缺失」与「真的没搜到」在调用方看来完全一样。
    """
    try:
        from .vector import encode_text
    except Exception as e:
        raise VectorUnavailable(
            f"向量编码模块不可用（{type(e).__name__}: {e}）") from e

    try:
        query_embedding = encode_text(query)
    except Exception as e:
        raise VectorUnavailable(
            f"查询向量编码失败（{type(e).__name__}: {e}）") from e

    if query_embedding is None:
        raise VectorUnavailable(
            "查询向量编码未就绪（常见原因：ONNX 模型缺失 "
            "data/models/bge-small-zh-v1.5.onnx，或未安装 onnxruntime）")

    from .knowledge_store import get_knowledge_store
    store = get_knowledge_store()
    results = store.search_by_vector(
        query_embedding=query_embedding,
        top_k=top_k,
        source_filter=source_filter
    )

    logger.debug(f"向量检索返回 {len(results)} 条结果")
    return results


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
        搜索结果列表（每条带 ``degraded`` / ``degrade_reason``）
    """
    return search_with_diagnostics(
        query=query,
        top_k=top_k,
        enable_vector=enable_vector,
        source_filter=source_filter,
    ).results


def search_with_diagnostics(
    query: str,
    top_k: int = 10,
    enable_vector: bool = True,
    source_filter: Optional[str] = None
) -> SearchOutcome:
    """统一检索入口（带降级诊断）—— **用户可见提示应基于本函数**。

    与 :func:`search` 的唯一差别是会先把「向量能力到底有没有」探明，并把
    原因如实写进 ``SearchOutcome.degrade_reason``：这样即使 0 条命中，调用方
    也能明确告诉用户「是没搜到」还是「向量分支没起来，只搜了词法」。

    Returns:
        SearchOutcome(results=..., degraded=..., degrade_reason=...)
    """
    forced_reason = ""
    if enable_vector:
        from .knowledge_store import get_knowledge_store
        store = get_knowledge_store()
        if not store.has_vector:
            forced_reason = ("向量索引扩展 (sqlite-vec) 未加载"
                             "（装好扩展并重建索引后自动启用）")
            enable_vector = False

    return _run_hybrid(
        query=query,
        top_k=top_k,
        enable_vector=enable_vector,
        source_filter=source_filter,
        forced_reason=forced_reason,
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
