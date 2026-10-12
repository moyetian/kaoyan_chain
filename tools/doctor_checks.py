# -*- coding: utf-8 -*-
"""考研学习链 (Kaoyan AI Study Chain) · ky doctor 体检的 RAG / MCP 检查段。

本模块由 tools/doctor.py 拆出（纯位置移动，行为零变化）：承载体检 4.2 节
「本地 RAG 向量检索链路」与 4.5 节「MCP 外部工具挂载」的检查函数，以及
它们共用的终端着色常量与小工具。doctor.py 受 review_gate 单文件行数
ratchet 基线约束，检查段移出以回落到基线内。

导入兼容：doctor.py 以两种方式导入本模块 —— 脚本式 ``from doctor_checks
import ...``（``python tools/doctor.py`` 直跑时 sys.path[0] 为 tools/ 目录）；
包式 ``from tools.doctor_checks import ...``（``from tools import doctor`` /
CLI 入口）。两种路径指向同一份源码文件，模块内无状态，重复加载无害。
"""

import contextlib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class C:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def color(text: str, code: str) -> str:
    return f"{code}{text}{C.RESET}"


def check_item(title: str, ok: bool, detail_ok: str = "", detail_fail: str = "",
               warn: bool = False) -> bool:
    status_tag = color("[√ 通过]", C.GREEN) if ok else (color("[! 提示]", C.YELLOW) if warn else color("[× 异常]", C.RED))
    detail = detail_ok if ok else detail_fail
    detail_str = f" - {detail}" if detail else ""
    print(f"  {status_tag} {title}{detail_str}")
    return ok


def _check_rag_vector_chain() -> None:
    """[P0 修复·2026-10-11 第五轮深审] 本地 RAG 向量检索链路逐环自检（体检 4.2 节）。

    向量检索是「装齐依赖才启用」的可选增强，但旧实现缺任何一环都只是静默降级为
    纯词法，用户无从知道「名义功能」缺在哪、怎么补。逐环自检 + 缺失时给出
    **可执行的处方**（pip 命令 + 下载脚本路径），而非一句"未启用"。
    """
    print(color("\n【4.2 本地 RAG 向量检索链路】", C.BOLD))
    try:
        _check_rag_dependencies()
        _check_rag_knowledge_base()
    except Exception as _e:
        check_item("本地 RAG 向量检索链路", False, "", f"体检异常: {type(_e).__name__}: {_e}", warn=True)


def _check_rag_dependencies() -> None:
    """sqlite-vec / tokenizer / ONNX 模型三项依赖逐环自检（缺失给可执行处方）。"""
    import tempfile as _tempfile

    from tools.search.knowledge_store import KnowledgeStore

    _vec_ok = False
    _vec_detail = ""
    try:
        with _tempfile.TemporaryDirectory() as _td:
            _ks = KnowledgeStore(Path(_td) / "probe.db")
            _vec_ok = bool(_ks.has_vector)
            _vec_detail = f"已加载（维度 {_ks.embedding_dim}）"
            with contextlib.suppress(Exception):   # 探测句柄，close 失败不影响结论
                _ks.close()
    except Exception as _e:
        _vec_detail = f"{type(_e).__name__}: {_e}"
    if _vec_ok:
        check_item("sqlite-vec 向量扩展", True, _vec_detail)
    else:
        check_item("sqlite-vec 向量扩展", False, "",
                   "未安装或加载失败（可选）：pip install sqlite-vec 后重跑体检；"
                   "缺它时 ky rag 自动降级纯词法检索，功能不受阻断", warn=True)

    # tokenizer（transformers 或 tokenizers 任一可用即可）
    _tok = ""
    try:
        import transformers  # type: ignore
        _tok = f"transformers {transformers.__version__}"
    except Exception:
        try:
            import tokenizers  # type: ignore
            _tok = f"tokenizers {tokenizers.__version__}"
        except Exception:
            _tok = ""
    if _tok:
        check_item("向量 tokenizer 依赖", True, _tok)
    else:
        check_item("向量 tokenizer 依赖", False, "",
                   "未安装（可选）：pip install transformers tokenizers；"
                   "缺它时向量编码自动降级纯词法检索", warn=True)

    # ONNX 模型文件（data/models/）
    _model = ROOT / "data" / "models" / "bge-small-zh-v1.5.onnx"
    if _model.exists():
        _size = _model.stat().st_size / 1024 / 1024
        check_item("本地向量模型", True, f"bge-small-zh-v1.5.onnx ({_size:.0f}MB)")
    else:
        check_item("本地向量模型", False, "",
                   "未下载（可选）：python tools/search/download_model.py"
                   "（~95MB，Xenova 源；下载后放 data/models/）", warn=True)


def _check_rag_knowledge_base() -> None:
    """知识库现状（只读）：已建/未建都给下一步命令。"""
    from tools.search.knowledge_store import DEFAULT_DB_PATH
    if DEFAULT_DB_PATH.exists():
        check_item("本地知识库", True, f"已建立（{DEFAULT_DB_PATH}）；重建：ky index")
    else:
        check_item("本地知识库", True, "尚未建立（可选）：ky index 一键切片入库")


def _check_mcp_mount(cfg: object) -> int:
    """MCP 外部工具挂载自检（体检 4.5 节）。返回本节新增的 warnings 数。

    [B4 新增] MCP 是可选能力：未配置时零开销、不拉任何子进程；配置了才做
    **轻量探活**（3s 握手超时）并逐个汇报 名称/状态/工具数/失败原因 ——
    此前崩溃的 MCP server 在体检里完全不可见，用户只在会话里莫名少工具。
    """
    print(color("\n【4.5 MCP 外部工具挂载】", C.BOLD))
    _mcp_cfg = cfg.get("mcp_servers") if isinstance(cfg, dict) else None
    if not isinstance(_mcp_cfg, dict) or not _mcp_cfg:
        check_item("外部 MCP Server 挂载", True,
                   "未配置（可选能力；在 ky_config.json 的 mcp_servers 段挂载外部工具）")
        return 0
    try:
        try:
            from agent.mcp_client import MCPClientManager
        except ImportError:
            from tools.agent.mcp_client import MCPClientManager
        _mgr = MCPClientManager(workspace_root=ROOT)
        try:
            _mgr.load_from_config(_mcp_cfg, start_timeout=3)
            _health = _mgr.mcp_health()
        finally:
            # 体检不留常驻子进程：取到健康快照后立即回收
            _mgr.close_all()
        _all_ok = _health["degraded"] == 0 and _health["dead"] == 0
        check_item(
            "外部 MCP Server 挂载",
            _all_ok,
            f"共 {_health['total']} 个 server 全部健康，工具可发现",
            f"{_health['healthy']}/{_health['total']} 健康、"
            f"{_health['degraded']} 降级、{_health['dead']} 崩溃",
        )
        added = 0 if _all_ok else 1
        for _s in _health["servers"]:
            if _s["status"] == "healthy":
                check_item(f"  └ MCP [{_s['name']}]", True, f"{_s['tool_count']} 个工具可用")
            else:
                check_item(f"  └ MCP [{_s['name']}]", False, "",
                           f"[{_s['status']}] {_s['reason']}", warn=True)
        return added
    except Exception as _e:
        check_item("外部 MCP Server 挂载", False, "", f"MCP 体检失败: {_e}", warn=True)
        return 1
