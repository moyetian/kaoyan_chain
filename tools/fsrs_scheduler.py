# -*- coding: utf-8 -*-
"""
考研学习链 · FSRS 自适应记忆调度器 (Free Spaced Repetition Scheduler)

提供两层能力：

1. ``compute_next_interval(stage, rating, today)`` —— **纯函数，无状态**。
   这是全项目复测间隔计算的**唯一真源**，供 ``skills.error_logger``
   依据错题卡片中已持久化的 ``stage``（已完成复测档位）推导下次复测日。

   语义约定（与错题卡片 Markdown 的 ``stage`` 字段一一对应）：
     - ``stage`` = 该错题**已完成的复测次数**（新建记录时为 0）；
     - 推导时先在 FSRS 内部以 "good" 快进 ``stage`` 次，重建该卡片的
       记忆稳定性(stability)与难度(difficulty)，再施加**本次**评级，
       从而得到真正依赖历史的自适应间隔；
     - 返回值第三项 ``interval_days`` 为「距今天的复测间隔天数」
       （不足 1 天的学习步长统一提升为 1 天，避免刚归档就当天到期）。

   经校准后的实际间隔序列（参数见 ``make_scheduler``，可复现）：

   ==========  ============================================
   评级        stage 0→5 的间隔（天）
   ==========  ============================================
   again       1, 1, 1, 1, 1, 1      （遗忘 → 整个周期重置，次日重做）
   hard        1, 8, 32, 116, 364, 1007
   good        2, 11, 46, 163, 497, 1346
   easy        8, 19, 77, 265, 790, 2086
   ==========  ============================================

   三条可验证的不变式：① 同评级下间隔随 stage 不减；
   ② 同 stage 下 ``easy > good > hard > again``；
   ③ 同输入结果完全可复现（同 ``(stage, rating, today)`` 恒等）。
   ``again`` 与 stage 无关恒为 1 天，是实现"周期重置"语义的直接结果。

2. ``FSRSScheduler`` —— 带持久化卡片状态的调度器（``.memory/fsrs_db.json``），
   适用于后续接入「完整复习日志 + 逐卡稳定性追踪」的场景。
   **当前生产链路未调用**（见模块末尾说明），保留为既有能力。

依赖：本模块基于 py-fsrs **5.x / 6.x** 的 ``Scheduler`` API：
      ``Scheduler.review_card(card, rating, review_datetime) -> (Card, ReviewLog)``。
      ``pyproject.toml`` / ``requirements.txt`` 的约束已同步为 ``fsrs>=5.0.0``。
      注意 4.x 及更早版本的 ``fsrs.FSRS`` / ``Scheduler.repeat`` 已在本版本移除，
      不可再使用。
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from fsrs import Card, Rating, Scheduler

__all__ = [
    "RATING_MAP",
    "make_scheduler",
    "compute_next_interval",
    "FSRSScheduler",
]

# 字符串评级 → FSRS 枚举。键名与错题卡片中记录的 rating 文本保持一致。
RATING_MAP = {
    "again": Rating.Again,
    "hard": Rating.Hard,
    "good": Rating.Good,
    "easy": Rating.Easy,
}

# 快进历史时使用的评级：错题「已通过 stage 次」按 good 还原最保守的历史轨迹
_BUILD_UP_RATING = Rating.Good

_SCHEDULER: Scheduler | None = None


def make_scheduler() -> Scheduler:
    """构造本项目的 FSRS Scheduler 实例（唯一构造入口，保证两端配置一致）。

    与 fsrs 库默认值的三处**有意偏离**，均为本产品的正确性前提：

    1. ``enable_fuzzing=False``
       库默认 True 会给间隔叠加随机抖动。实测同一 ``(stage, rating)``
       连续 5 次调用可得 ``[9, 11, 12, 9, 14]`` 天 —— 间隔不可复现，
       同一道错题重复复测会得到不同到期日，且自动化测试必然随机失败。
       学习规划需要确定性，故关闭。

    2. ``learning_steps=()`` / ``relearning_steps=()``
       库默认含 1 分钟 / 10 分钟级学习步进。本项目错题卡片只持久化
       ``stage``（已完成复测次数）而不保存分钟级时间轴，若保留学习步进，
       快进 history 时卡片会在 Learning 与 Review 状态间来回跳，
       实测导致 ``easy`` 档位出现 ``[8, 4, 19, ...]`` 的非单调序列
       （stage 增大反而间隔变短），与"档位越高间隔越长"的模型语义冲突。
       清空学习步进后各评级随 stage 严格单调，模型自洽。

    3. ``desired_retention`` 保持库默认 0.9（90% 记忆保留率），
       属于考研复测的合理目标区间，不做改动。
    """
    global _SCHEDULER
    if _SCHEDULER is None:
        _SCHEDULER = Scheduler(
            desired_retention=0.9,
            learning_steps=(),
            relearning_steps=(),
            enable_fuzzing=False,
        )
    return _SCHEDULER


# 兼容旧调用名（历史代码/测试可能引用 _scheduler）
_scheduler = make_scheduler


def _resolve_rating(raw) -> Rating:
    """把 "good" / Rating.Good 之类的输入统一解析为 Rating 枚举。"""
    if isinstance(raw, Rating):
        return raw
    return RATING_MAP.get(str(raw or "good").strip().lower(), Rating.Good)


def _gap_days(before: datetime, after: datetime) -> int:
    """两个时刻之间的间隔天数；不足 1 天的学习步长（如 10 分钟）提升为 1 天。"""
    seconds = (after - before).total_seconds()
    return max(1, math.ceil(seconds / 86400.0))


def compute_next_interval(
    stage: int = 0,
    rating: str = "good",
    today: date | None = None,
):
    """依据已完成的复测档位与本次评级，计算下一次复测安排。

    Args:
        stage: 该错题**已完成**的复测次数（≥0）。
        rating: 本次复测评级，``again`` / ``hard`` / ``good`` / ``easy``。
        today: 计算基准日，缺省为本地今天。

    Returns:
        ``(new_stage, next_due_date, interval_days)``
          - ``new_stage``: 本次复测后的新档位；``again`` 重置为 0，其余 +1。
          - ``next_due_date``: 下次到期日（date）。
          - ``interval_days``: 距 ``today`` 的天数（最小 1）。
        ``again`` 的间隔同样按"重置后的 0 档"推导，因此
        ``(new_stage, interval_days)`` 始终自洽、可复算。

    异常：
        参数非法一路降级（stage 负数取 0、未知 rating 取 good），
        本函数**不抛异常**，以保证错题沉淀主链路永不被记忆算法中断。
    """
    if today is None:
        today = date.today()
    stage = max(0, int(stage or 0))
    target = _resolve_rating(rating)

    # [一致性修正] 遗忘评级(again)语义为"整个复测周期重置"，因此间隔也必须
    # 从**重置后的 0 档**推导，而不是从重置前的旧档位推导。
    # 否则会出现 `stage=0` 却写着 `下次到期 +7 天` 的自相矛盾记录
    # （该组合无法由 compute_next_interval(0, "good") 复现，回写不可追溯）。
    effective_stage = 0 if target is Rating.Again else stage

    sched = _scheduler()
    # 以基准日零点(UTC)为时间轴原点，逐次快进重建记忆稳定性
    now = datetime.combine(today, time.min).replace(tzinfo=timezone.utc)
    card = Card()
    for _ in range(effective_stage):
        card, _log = sched.review_card(card, _BUILD_UP_RATING, now)
        now = card.due  # 沿真实到期时刻推进，保持 FSRS 时间轴正确

    card, _log = sched.review_card(card, target, now)

    interval_days = _gap_days(now, card.due)
    new_stage = 0 if target is Rating.Again else stage + 1
    return new_stage, today + timedelta(days=interval_days), interval_days


class FSRSScheduler:
    """带持久化卡片状态的 FSRS 调度器（数据文件：``.memory/fsrs_db.json``）。

    .. note::
       本类当前**未被生产链路调用**：错题复测的间隔计算走
       ``compute_next_interval``（无状态、以错题卡片的 ``stage`` 为唯一状态）。
       保留本类是为了后续接入「逐卡稳定性追踪」时无需重写调度层；
       若确定不再采用，可直接删除本类（不影响任何现有功能）。
    """

    def __init__(self, db_path: str = ".memory/fsrs_db.json"):
        self.db_path = Path(db_path)
        self.scheduler = make_scheduler()  # 与无状态路径共用同一套校准参数
        self.cards: dict[str, Card] = {}
        self._load()

    # ── 持久化 ────────────────────────────────────────────────
    def _load(self) -> None:
        if not self.db_path.exists():
            return
        try:
            data = json.loads(self.db_path.read_text(encoding="utf-8"))
            for card_id, payload in data.items():
                # 直接复用 py-fsrs 官方序列化，避免手工罗列字段导致的版本漂移
                self.cards[card_id] = Card.from_dict(payload)
        except Exception as e:  # 损坏的数据文件不应阻断主流程
            logging.warning(f"Failed to load FSRS db ({self.db_path}): {e}")

    def _save(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = {cid: card.to_dict() for cid, card in self.cards.items()}
            self.db_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            logging.error(f"Failed to save FSRS db ({self.db_path}): {e}")

    # ── 调度 ──────────────────────────────────────────────────
    def get_card(self, item_id: str) -> Card:
        """取回既有卡片；不存在则创建一张新卡（Lazy init）。"""
        if item_id not in self.cards:
            self.cards[item_id] = Card()
        return self.cards[item_id]

    def review_item(
        self,
        item_id: str,
        rating: Rating | str,
        now: datetime | None = None,
    ) -> Card:
        """对某个记忆项执行一次复习，落盘并返回更新后的卡片。"""
        if now is None:
            now = datetime.now(timezone.utc)
        card = self.get_card(item_id)
        updated_card, _log = self.scheduler.review_card(
            card, _resolve_rating(rating), now
        )
        self.cards[item_id] = updated_card
        self._save()
        return updated_card

    def get_due_items(self, now: datetime | None = None) -> list[str]:
        """返回所有已到期（``due <= now``）的记忆项 id。"""
        if now is None:
            now = datetime.now(timezone.utc)
        # 卡片 due 可能为 naive（历史数据），统一按 UTC 比较，避免 TypeError
        due_items = []
        for item_id, card in self.cards.items():
            card_due = card.due
            if card_due.tzinfo is None:
                card_due = card_due.replace(tzinfo=timezone.utc)
            if card_due <= now:
                due_items.append(item_id)
        return due_items
