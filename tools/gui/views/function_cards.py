# -*- coding: utf-8 -*-
"""功能卡清单（导航 rail 的「工具」组唯一数据源）

P2 改造后，这 10 项不再以 2×5 平铺卡片渲染，而是作为左侧 rail 的
「工具」组条目（见 ``views/nav_rail.py``）与命令面板条目；动作分发逻辑不变
（仍由 ``MainWindow._on_card_clicked`` 承担）。清单本身是唯一真源，因此保留在
本模块，供 rail、命令面板与自检脚本共用。
"""

from __future__ import annotations

#: 功能项清单（SVG 图标 key / 标题 / 说明 / 动作别名）
#: 注：首项标题用「任务打卡」而非「今日任务」—— rail 的「视图」组已有同名
#: 页签，两处并排重名会让人分不清区别（动作相同，都是切到今日任务页）。
CARD_ITEMS = (
    ("today", "任务打卡", "查看四科任务量与推进打卡", "today"),
    ("compose", "靶向组卷", "按考点与难度智能拼卷演练", "compose"),
    ("variant", "同源变式", "薄弱考点同源变式真题检索", "variant"),
    ("diff", "考纲Diff", "新旧考纲层级对比与动荡率", "diff"),
    ("ingest", "切片入库", "真题/模拟卷结构化切片入库", "ingest"),
    ("scout", "院校侦察", "研招网与社媒实名口碑研报", "scout"),
    ("compare", "双校对标", "双校初复试指标横向对标", "compare"),
    ("watch", "简章监控", "高校研究生院简章变动预警", "watch"),
    ("build", "看板更新", "重新编译掌握度雷达看板", "build"),
    ("wechat_search", "公众号检索", "微信公众号考研文章检索与沉淀", "wechat_search"),
)

#: 历史遗留：曾用于 2×5 平铺布局的每行列数（rail 改造后不再使用）
COLUMNS = 5


__all__ = ["CARD_ITEMS", "COLUMNS"]
