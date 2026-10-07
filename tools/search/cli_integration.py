# -*- coding: utf-8 -*-
"""
CLI 命令集成 - 混合检索

为 CLI 命令提供混合检索能力的便捷函数
"""

import sys
from pathlib import Path

# [F3 修复·脚本直跑导入引导] `py tools/search/cli_integration.py` 时
# sys.path[0] 是 tools/search/，`from workspace`（在 tools/ 下）与
# `from tools.workspace`（需仓库根）双双失败（实测 ModuleNotFoundError）。
# 按 init_workspace.py 既有模式把 tools/search、tools、仓库根插入 path 后
# 再导入。
_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE), str(_HERE.parent), str(_HERE.parent.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root
from typing import List, Optional

ROOT = resolve_workspace_root(__file__)


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
        # [修复 2026-10-05·只读检索不得建库] 改用 search_with_diagnostics：
        # 库文件不存在时 hybrid 层直接返回降级诊断（不再 get_knowledge_store()
        # 凭空建库）；0 条命中时把降级原因写进日志，调用方能区分「没搜到」
        # 与「知识库未建立/向量不可用」。
        from tools.search.hybrid import search_with_diagnostics

        outcome = search_with_diagnostics(
            query=query,
            top_k=top_k,
            enable_vector=True,
            source_filter=source_filter
        )
        results = outcome.results
        if not results and outcome.degraded:
            import logging
            logging.getLogger(__name__).warning(
                "本地知识库检索为空：%s", outcome.degrade_reason or "未命中任何片段")

        # 转换为简单字典格式
        return [
            {
                "text": r.text,
                "source": r.source,
                "score": r.score,
                "lexical_rank": r.lexical_rank,
                "vector_rank": r.vector_rank,
                # [C5] 降级标记随结果一起透出：调用方不应把纯词法结果
                # 当成「词法+向量融合」结果来宣传。
                "degraded": r.degraded,
                "degrade_reason": r.degrade_reason,
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
