# -*- coding: utf-8 -*-
"""
CLI 命令集成 - 混合检索

为 CLI 命令提供混合检索能力的便捷函数
"""

from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent


def cli_search(query: str, top_k: int = 5, source_filter: Optional[str] = None) -> List[dict]:
    """CLI 检索便捷函数

    Args:
        query: 查询文本
        top_k: 返回结果数
        source_filter: 源文件过滤

    Returns:
        搜索结果列表 [{"text": ..., "source": ..., "score": ...}, ...]
    """
    try:
        from tools.search.hybrid import search as hybrid_search

        results = hybrid_search(
            query=query,
            top_k=top_k,
            enable_vector=True,
            source_filter=source_filter
        )

        # 转换为简单字典格式
        return [
            {
                "text": r.text,
                "source": r.source,
                "score": r.score,
                "lexical_rank": r.lexical_rank,
                "vector_rank": r.vector_rank
            }
            for r in results
        ]

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"检索失败: {e}")
        return []


def search_universities(query: str, top_k: int = 3) -> List[dict]:
    """检索院校信息

    Args:
        query: 查询文本（院校名或别名）
        top_k: 返回结果数

    Returns:
        院校信息列表
    """
    results = cli_search(query, top_k=top_k)

    # 过滤院校类型
    university_results = [
        r for r in results
        if 'universities/' in r.get('source', '')
    ]

    return university_results[:top_k]


def search_materials(query: str, top_k: int = 5) -> List[dict]:
    """检索参考资料

    Args:
        query: 查询文本
        top_k: 返回结果数

    Returns:
        资料片段列表
    """
    results = cli_search(query, top_k=top_k)

    # 过滤资料类型
    material_results = [
        r for r in results
        if '04-专业课' in r.get('source', '') or 'material' in r.get('source', '')
    ]

    return material_results[:top_k]


if __name__ == "__main__":
    # 测试
    print("=== CLI 检索集成测试 ===\n")

    # 测试院校检索
    print("1. 院校检索: 华中科技大学")
    results = search_universities("华中科技大学")
    print(f"   找到 {len(results)} 条结果\n")

    # 测试资料检索
    print("2. 资料检索: 计算机考研复试")
    results = search_materials("计算机考研复试")
    print(f"   找到 {len(results)} 条结果\n")
