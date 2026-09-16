# -*- coding: utf-8 -*-
"""
检索质量评测

离线模式（默认、CI 可跑）验证流水线机械正确性；联网模式（KY_LIVE_TEST=1）测召回指标。
用法：::

    py -c "from tools.search.benchmark.runner import evaluate_offline as e; print(e().summary())"
"""

from .runner import (
    BenchmarkResult,
    DATASET_PATH,
    FIXTURES_PATH,
    evaluate_live,
    evaluate_offline,
    load_dataset,
    load_fixtures,
)

__all__ = [
    "BenchmarkResult",
    "DATASET_PATH",
    "FIXTURES_PATH",
    "evaluate_live",
    "evaluate_offline",
    "load_dataset",
    "load_fixtures",
]
