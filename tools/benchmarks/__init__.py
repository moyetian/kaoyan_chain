# -*- coding: utf-8 -*-
"""评测基准包：C 系列（可证）的共用评测 runner 与数据契约。当前内容：

  * ``runner.py`` —— 通用 jsonl 评测引擎（C1 考纲守卫 / C2 引文忠实度 /
    C4 判分 pilot 共用）。数据文件放在 ``tests/benchmarks/*.jsonl``
    （与规划口径一致）。
  * ``citation_judge.py`` —— C2 引文忠实度 judge（按 kind 分发到四个被测对象）。
  * ``grading_judge.py`` —— C4 开放题判分评测（快照回放三指标：采分点命中
    一致率 / 总分 MAE / 错因一致率）。**pilot 阶段不进 CI 门禁**（元测试钉住）。
"""
