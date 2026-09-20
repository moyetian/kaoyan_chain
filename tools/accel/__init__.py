# -*- coding: utf-8 -*-
"""
考研学习链 · 高性能加速协同层 (Acceleration Coordination Layer)

本模块是**项目内唯一**负责「可选 Rust 加速扩展（``ky_rust_ext``）能力协商」的地方，
不实现任何领域逻辑。它回答三个问题：

  1. **装载了吗？** —— :func:`native`：按 ``import ky_rust_ext`` 探测；
     仅当扩展**自己声明了** ``__abi__`` 时才校验 ABI 版本，避免因对方未声明
     而把可用的扩展误判为不可用。
  2. **这个能力可用吗？** —— :func:`available`：在装载成功的基础上，
     逐能力 ``hasattr`` 探测，并叠加**已知缺陷黑名单**（见
     :data:`KNOWN_RUST_DEFECTS`）。
  3. **该用哪条路径？** —— :func:`prefer`：给调用方一个统一入口，
     返回 ``None`` 表示"请走纯 Python 降级路径"。

设计原则
--------
* **正确性优先于性能**：一旦某个 Rust 能力与纯 Python 实现存在语义差异
  （实测存在过此类差异），必须由 :data:`KNOWN_RUST_DEFECTS` 显式禁用，
  而不是"快就行"。黑名单里每一条都带禁用原因与恢复条件。
* **绝不伪造降级结果**：本模块不提供任何"占位实现"。历史版本中
  ``review_score`` 在无 Rust 时静默返回 ``0``，属于**静默错误结果**，
  已删除 —— 若将来需要该能力，应在拥有等价纯 Python 实现后再提供。
* **无导入副作用**：装载探测结果是惰性缓存的，导入本模块不会做任何 IO。

诊断：``python tools/doctor.py`` 会调用 :func:`capabilities` 输出逐能力状态。
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

__all__ = [
    "ABI",
    "KNOWN_RUST_DEFECTS",
    "native",
    "available",
    "prefer",
    "capabilities",
    "reset_cache",
    "sha256_hash",
    "estimate_tokens",
]

#: 期望的扩展 ABI 版本；仅在扩展声明了 ``__abi__`` 时参与校验
ABI = "1"

#: 允许通过环境变量强制关闭加速（排障 / 双路径一致性测试用）
DISABLE_ENV = "KAOYAN_DISABLE_ACCEL"


#: 已知存在正确性缺陷、**暂不启用** Rust 快路径的能力。
#: key = 能力名，value = (缺陷说明, 恢复条件)
#:
#: 历史记录：``extract_subjects`` 曾因 Rust 版把 "(204)英语(二)" 截断为
#: "(204)英语" 而被禁用（该错误条目挤占 4 个名额，导致自命题科目被挤出）。
#: rust_ext/src/extractor.rs 已同步修复，重新编译后经双路径一致性回归
#: （test_new_features.py B.4b）验证通过，2026-09-14 从黑名单移除。
KNOWN_RUST_DEFECTS: Dict[str, tuple] = {}


@lru_cache(maxsize=1)
def native():
    """返回可用的 Rust 加速模块；任何不可用情形都返回 ``None``，绝不抛异常。"""
    if os.environ.get(DISABLE_ENV):
        return None
    try:
        import ky_rust_ext as module
    except ImportError:  # 未安装 / 平台无 wheel / 编译失败
        return None
    except Exception:  # 扩展存在但装载损坏（如 ABI 不匹配导致的 ImportError 变体）
        return None

    declared_abi = getattr(module, "__abi__", None)
    # 只有当扩展**主动声明**了 __abi__ 时才做版本校验：
    # 若对方根本没声明（历史构建产物即如此），据此外推"不兼容"会误杀可用能力。
    if declared_abi is not None and str(declared_abi) != ABI:
        return None
    return module


def reset_cache() -> None:
    """清空装载缓存（切换 ``KAOYAN_DISABLE_ACCEL`` 或重新编译扩展后调用）。"""
    native.cache_clear()


def available(capability: str) -> bool:
    """指定能力是否**可用且可信**（已装载 + 存在 + 未被列入缺陷黑名单）。"""
    if capability in KNOWN_RUST_DEFECTS:
        return False
    module = native()
    return module is not None and hasattr(module, capability)


def prefer(capability: str) -> Optional[Any]:
    """返回承载该能力的 Rust 模块（供调用方直接取属性），不可用时返回 ``None``。

    调用方约定::

        rust = accel.prefer("sha256_hash")
        if rust is not None:
            try:
                return rust.sha256_hash(text)
            except Exception:
                pass                      # 运行时异常同样降级
        return <纯 Python 实现>
    """
    return native() if available(capability) else None


def capabilities(names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """逐能力汇报状态，供 ``ky doctor`` 等诊断入口展示。

    Returns:
        每项形如 ``{"name", "present", "enabled", "note"}`` 的列表。
    """
    module = native()
    if names is None:
        # [缺陷修复] dir(module) 会把模块自身名（如 'ky_rust_ext'）等非能力属性也算进来，
        # 污染 doctor 报告。此处只保留可调用（真正的函数）的顶层属性。
        names = sorted(
            n for n in dir(module)
            if not n.startswith("_") and callable(getattr(module, n, None))
        ) if module else []
        # 黑名单里的能力即使扩展存在也要出现在报告里（否则用户看不到"被禁用"这一事实）
        names = sorted(set(names) | set(KNOWN_RUST_DEFECTS))

    report: List[Dict[str, Any]] = []
    for name in names:
        present = module is not None and hasattr(module, name)
        if name in KNOWN_RUST_DEFECTS:
            note = "已禁用：" + KNOWN_RUST_DEFECTS[name][0]
        elif module is None:
            note = "未装载 Rust 扩展，走纯 Python 路径"
        elif not present:
            note = "扩展未提供该能力"
        else:
            note = "Rust 快路径可用"
        report.append({
            "name": name,
            "present": present,
            "enabled": available(name),
            "note": note,
        })
    return report


# ════════════════════════════════════════════════════════════════
# 通用原语（自带权威纯 Python 实现，供全项目共用，避免各处各写一份降级）
# ════════════════════════════════════════════════════════════════

def sha256_hash(content: Any) -> str:
    """内容指纹（SHA256 十六进制）。

    Rust 与纯 Python 两条路径输出**逐字节一致**（已用空串、中文、emoji、
    10K 长文、混合空白等样本验证），故可安全地在两条路径间切换。
    """
    text = content if isinstance(content, str) else str(content or "")
    rust = prefer("sha256_hash")
    if rust is not None:
        try:
            return rust.sha256_hash(text)
        except Exception:
            pass
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def estimate_tokens(messages: Any) -> int:
    """估算消息列表的 Token 量（中英混合按 0.6 token/字符）。

    纯 Python 实现与 Rust 版同口径（``ceil/round(0.6 × 字符数)``），
    用于上下文压缩与提示词预算控制。
    """
    rust = prefer("estimate_tokens")
    if rust is not None:
        try:
            return int(rust.estimate_tokens(messages))
        except Exception:
            pass

    if not messages:
        return 0
    chars = 0
    for msg in messages:
        if isinstance(msg, dict):
            content = msg.get("content", "")
        else:
            content = getattr(msg, "content", "")
        if isinstance(content, (list, tuple)):
            content = "".join(str(c) for c in content)
        chars += len(str(content or ""))
    return int(round(chars * 0.6))
