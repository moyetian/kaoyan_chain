# -*- coding: utf-8 -*-
"""
本地知识库检索命令模块 (search.py)
包含 rag —— C5「RAG 显式降级」的用户可达入口

[为什么需要这个入口] 改造前 ``tools/search/hybrid.py`` 的混合检索链路**没有任何
用户可达入口**：唯一调用方 ``cli_integration.py`` 自身也是孤儿模块（全仓无人
import）。后果是「向量不可用时自动降级」这条路径既没人走、也没人看见 ——
降级提示只写进日志，用户永远不知道自己拿到的是纯词法结果。
本模块把检索接到 ``ky rag`` 与 REPL ``/rag``，让降级提示真正可见。
"""

import sys
from typing import List, Optional

try:
    from tools.cli.dispatch import Command, register
    from tools.cli.repl.renderer import C, colorize, print_rag_results
except ImportError:
    from cli.dispatch import Command, register
    from cli.repl.renderer import C, colorize, print_rag_results

_USAGE = """
本地知识库检索 (ky rag) —— 词法 + 向量 RRF 融合，降级时显式提示
用法：
  ky rag <关键词> [--top=5] [--source=关键字]
示例：
  ky rag 剩余价值 --top=8
  ky rag 矛盾的普遍性 --source=马原
说明：
  检索的是本地知识库 (data/knowledge/embeddings.db)，内容来自「院校库 + 各科
  参考资料/ 目录」的切片索引。建索引：
    1) ky ingest <试题/资料文件>     # 把题卡归档到 参考资料/
    2) python tools/search/indexer.py  # 切片入知识库（暂无 ky 子命令）
  向量分支需要 sqlite-vec 扩展 + 本地 ONNX 模型；二者缺失时**自动降级为纯词法
  检索**，并在结果上方给出降级原因（不会静默降级）。
"""


def run_rag_search(query: str, top_k: int = 5,
                   source_filter: Optional[str] = None) -> int:
    """执行一次本地知识库检索并渲染结果（``ky rag`` 与 REPL ``/rag`` 共用）。

    Returns:
        0 成功；1 参数缺失；2 知识库尚未建立（不代为建库）。
    """
    query = (query or "").strip()
    if not query:
        print(colorize(_USAGE, C.YELLOW))
        return 1

    # 只读语义：知识库不存在时**不**代为建库（KnowledgeStore() 一构造就会
    # 建库建表，在空工作区里凭空造出一个空库）。先判存在，再决定要不要打开。
    try:
        from tools.search.knowledge_store import DEFAULT_DB_PATH
    except ImportError:
        from search.knowledge_store import DEFAULT_DB_PATH  # type: ignore

    if not DEFAULT_DB_PATH.exists():
        print(colorize(
            f"\n[!] 本地知识库尚未建立: {DEFAULT_DB_PATH}\n"
            "    1) ky ingest <试题/资料文件>        # 题卡归档到 参考资料/\n"
            "    2) python tools/search/indexer.py   # 切片入知识库\n", C.YELLOW))
        return 2

    try:
        from tools.search.hybrid import search_with_diagnostics
    except ImportError:
        from search.hybrid import search_with_diagnostics  # type: ignore

    outcome = search_with_diagnostics(
        query=query,
        top_k=max(1, int(top_k)),
        source_filter=source_filter or None,
    )
    print_rag_results(outcome, query)
    return 0


def _cmd_rag(args: List[str]) -> None:
    """``ky rag <关键词>`` 命令处理器。"""
    if "--help" in args or "-h" in args:
        print(colorize(_USAGE, C.YELLOW))
        sys.exit(0)

    query_parts: List[str] = []
    top_k = 5
    source_filter = ""
    for a in args[1:]:
        if a.startswith("--top="):
            try:
                top_k = int(a.split("=", 1)[1])
            except ValueError:
                print(colorize(f"[!] --top 需要整数，收到: {a}", C.RED))
                sys.exit(1)
        elif a.startswith("--source="):
            source_filter = a.split("=", 1)[1].strip()
        elif not a.startswith("-"):
            query_parts.append(a)

    code = run_rag_search(" ".join(query_parts), top_k=top_k,
                          source_filter=source_filter)
    if code == 1 and not query_parts:
        sys.exit(1)
    sys.exit(0 if code == 0 else 1)


register(Command(
    'rag', ("rag", "search", "--rag", "--search"),
    '<关键词> [--top=5] [--source=关键字]',
    '本地知识库检索（词法+向量 RRF 融合；向量不可用时显式提示已降级）',
    handler=_cmd_rag,
))
