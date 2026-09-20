# -*- coding: utf-8 -*-
"""GUI 数据服务包（纯数据/后端调用，不依赖 Qt 控件）"""

from .actions import (
    compare_schools,
    diff_syllabus,
    ingest_file,
    make_error_quiz,
    run_action_capture,
)
from .dashboard import (
    SUBJECT_DIRS,
    countdown_days,
    error_queue_markdown,
    header_info,
    intel_markdown,
    load_state,
    subject_labels,
    subject_progress,
)
from .settings import (
    is_unconfigured,
    read_config,
    save_onboarding_config,
    test_api_connectivity,
    update_agents_md,
)

__all__ = [
    # dashboard
    "SUBJECT_DIRS",
    "countdown_days",
    "error_queue_markdown",
    "header_info",
    "intel_markdown",
    "load_state",
    "subject_labels",
    "subject_progress",
    # actions
    "compare_schools",
    "diff_syllabus",
    "ingest_file",
    "make_error_quiz",
    "run_action_capture",
    # settings
    "is_unconfigured",
    "read_config",
    "save_onboarding_config",
    "test_api_connectivity",
    "update_agents_md",
]
