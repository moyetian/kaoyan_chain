# -*- coding: utf-8 -*-
"""
Provider 注册表

新增一个检索源 = 写一个 `SearchProvider` 子类 + 在这里登记，
不需要改 `service.py`，也不需要动 Agent 的工具代码 —— 这是「让搜索能力可插拔」
的实际含义。

登记失败的模块会被跳过并留痕，而不是让整个搜索子系统导入即崩
（可选依赖缺失、第三方 API 客户端没装，都不该让 `ky` 起不来）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Type

from .base import NullProvider, ProviderError, SearchProvider

_LOG = logging.getLogger(__name__)

#: 名称 → 实现类
_REGISTRY: Dict[str, Type[SearchProvider]] = {}


def register(cls: Type[SearchProvider]) -> Type[SearchProvider]:
    """类装饰器：把一个 provider 登记进注册表。"""
    name = getattr(cls, "name", "") or cls.__name__.lower()
    if name in _REGISTRY and _REGISTRY[name] is not cls:
        _LOG.warning("provider 名称重复，后者覆盖前者: %s", name)
    _REGISTRY[name] = cls
    return cls


def provider_names() -> List[str]:
    return sorted(_REGISTRY)


def get_provider_class(name: str) -> Optional[Type[SearchProvider]]:
    return _REGISTRY.get(str(name).strip().lower())


def make_provider(name: str, **kwargs: Any) -> Optional[SearchProvider]:
    """按名实例化 provider；不存在或实例化失败返回 None（并留痕）。"""
    cls = get_provider_class(name)
    if cls is None:
        return None
    try:
        return cls(**kwargs)
    except Exception as exc:                      # pragma: no cover - 取决于实现
        _LOG.warning("provider 实例化失败: %s -> %s", name, exc)
        return None


def available_providers(names: Optional[Any] = None) -> List[SearchProvider]:
    """返回当前可用的 provider 实例列表。

    :param names: 限定名称集合（空/None 表示全部）
    """
    wanted = [str(n).strip().lower() for n in (names or []) if str(n).strip()]
    out: List[SearchProvider] = []
    for name in (wanted or provider_names()):
        cls = get_provider_class(name)
        if cls is None:
            _LOG.warning("未知 provider，已跳过: %s", name)
            continue
        try:
            if not cls.is_available():
                _LOG.info("provider 当前不可用，已跳过: %s", name)
                continue
            out.append(cls())
        except Exception as exc:                  # pragma: no cover
            _LOG.warning("provider 初始化失败，已跳过: %s -> %s", name, exc)
    return out


def describe_providers() -> List[Dict[str, Any]]:
    """供 CLI/文档展示：每个 provider 的名称、类型、可用性与说明。"""
    rows: List[Dict[str, Any]] = []
    for name in provider_names():
        cls = _REGISTRY[name]
        try:
            ok = cls.is_available()
        except Exception:                         # pragma: no cover
            ok = False
        rows.append({
            "name": name,
            "engine_type": getattr(cls, "engine_type", ""),
            "requires_key": bool(getattr(cls, "requires_key", False)),
            "available": bool(ok),
            "description": getattr(cls, "description", ""),
        })
    return rows


# ── 内建 provider 登记 ──────────────────────────────────────────
# 逐个 import，任一失败只影响它自己（可选依赖/版本差异不该拖垮整体）。

_BUILTIN_MODULES = ("bing", "ddg", "sogou", "tavily")


def _load_builtins() -> None:
    import importlib

    for mod_name in _BUILTIN_MODULES:
        try:
            importlib.import_module(f"{__name__}.{mod_name}")
        except ImportError as exc:
            _LOG.debug("内建 provider 未加载（可选）: %s -> %s", mod_name, exc)
        except Exception as exc:                  # pragma: no cover - 实现内部错误
            _LOG.warning("内建 provider 加载异常: %s -> %s", mod_name, exc)


_load_builtins()


__all__ = [
    "NullProvider",
    "ProviderError",
    "SearchProvider",
    "available_providers",
    "describe_providers",
    "get_provider_class",
    "make_provider",
    "provider_names",
    "register",
]
