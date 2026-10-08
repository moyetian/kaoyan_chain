# -*- coding: utf-8 -*-
"""
考研学习链 · FSRS 自适应记忆调度器 (Free Spaced Repetition Scheduler)

提供：

- ``compute_next_interval(stage, rating, today, history=None)`` —— **纯函数，无状态**。
   这是全项目复测间隔计算的**唯一真源**，供 ``skills.error_logger``
   依据错题卡片中已持久化的 ``stage``（已完成复测档位）推导下次复测日。

   语义约定（与错题卡片 Markdown 的 ``stage`` 字段一一对应）：
     - ``stage`` = 该错题**已完成的复测次数**（新建记录时为 0）；
     - 推导时先在 FSRS 内部以 "good" 快进 ``stage`` 次，重建该卡片的
       记忆稳定性(stability)与难度(difficulty)，再施加**本次**评级，
       从而得到真正依赖历史的自适应间隔；
     - 返回值第三项 ``interval_days`` 为「距今天的复测间隔天数」
       （不足 1 天的学习步长统一提升为 1 天，避免刚归档就当天到期）。

   [P1 修复·2026-10-08 K2 历史重建失真] 全按 "good" 快进只在"历史恰好全是 good"
   时正确：反复 hard 的弱项（如 hard,hard 后本次 good）真实间隔 ≈9 天，
   按 good 快进却给 46 天（**高估约 5 倍**），复测被系统性推迟。
   现支持 ``history`` 参数：传入该卡**当前周期**的真实评级序列
   （自上次 again 重置之后的逐次评级，长度 == stage），按真实序列回放；
   历史不可信（缺失/断链/含 again 重置/长度不符）时保守回退既有快进路径。
   :func:`load_review_history` 从 ``.memory/review_log.jsonl``（复测事件日志，
   由 error_logger.record_review_event 逐次追加）读取并校验该序列。

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

依赖：本模块基于 py-fsrs **5.x / 6.x** 的 ``Scheduler`` API：
      ``Scheduler.review_card(card, rating, review_datetime) -> (Card, ReviewLog)``。
      ``pyproject.toml`` / ``requirements.txt`` 的约束已同步为 ``fsrs>=5.0.0``。
      注意 4.x 及更早版本的 ``fsrs.FSRS`` / ``Scheduler.repeat`` 已在本版本移除，
      不可再使用。
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from fsrs import Card, Rating, Scheduler

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root

__all__ = [
    "RATING_MAP",
    "make_scheduler",
    "compute_next_interval",
    "load_review_history",
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

#: [修复 2026-10-05·超大 stage 卡死] 快进档位上限。``stage`` 来自错题卡片
#: Markdown 的整数字段（外部数据）：损坏或被篡改成极大值（如 10**9）时，
#: ``compute_next_interval`` 的逐档快进循环会长时间卡死（每次 review_card
#: 都是完整 FSRS 计算）。
#: 上限取 50（而非 100）：实测 stage>=90 时 FSRS 内部
#: ``card.due = review_datetime + next_interval`` 会抛 OverflowError（日期越界），
#: 而本函数承诺「不抛异常、错题沉淀主链路永不被中断」；50 档已远超真实考研
#: 周期（校准表 stage 0→5 间隔到 1346 天），且留足安全余量。
#: 返回值 ``new_stage`` 仍按原始 stage 递推，保证「可复算」不变式成立。
_MAX_STAGE = 50

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


#: [P1 修复·2026-10-08 K2] 复测事件日志相对工作区根的路径（与 error_logger.REVIEW_LOG_FILE 同源）。
_REVIEW_LOG_REL = Path(".memory") / "review_log.jsonl"


def _default_review_log() -> Path | None:
    """默认复测事件日志路径；工作区解析失败时返回 None（调用方按无日志处理）。"""
    try:
        return resolve_workspace_root(__file__) / _REVIEW_LOG_REL
    except Exception:  # pragma: no cover - 极端环境下不阻断
        return None


def _normalize_history(history, expected_stage: int):
    """校验回放历史；不可信时返回 None（调用方回退 good 快进路径）。

    合法条件（刻意严格，宁可不回放也不给出错误间隔）：
      - 长度 == expected_stage（当前周期已完成的复测次数）；
      - 每项都是已知评级，且**不含 again** —— again 在项目语义中把整个
        周期重置为 0，合法"当前周期"序列不可能含 again。
    """
    if history is None:
        return None
    try:
        hist = [str(r or "").strip().lower() for r in history]
    except TypeError:
        return None
    if not hist or len(hist) != expected_stage:
        return None
    if any(r not in RATING_MAP or r == "again" for r in hist):
        return None
    return hist


def load_review_history(subject, title, expected_stage, log_file=None):
    """[P1 修复·2026-10-08 K2] 从复测事件日志提取某道错题**当前周期**的真实评级序列。

    数据源为 ``.memory/review_log.jsonl``（error_logger.record_review_event
    在每次复测回写时追加一行 JSON）。返回按时间顺序的评级列表
    （如 ``["hard", "hard"]``），或 None 表示日志不可用/不可信：

      - 日志缺失、无匹配事件、事件字段损坏；
      - ``stage_before`` 链不连续（断链：日志缺早期事件、或同标题多卡
        交错污染 —— 链校验是防"张冠李戴"的关键防线）；
      - 链终点与 ``expected_stage``（卡片当前 stage）不一致；
      - 标题采用双向包含匹配（调用方可能传入标题前缀），歧义由链校验兜底。

    调用方（当前为白名单外的 error_logger.mark_error_status，见交付报告）
    应把返回值原样传给 ``compute_next_interval(..., history=...)``；
    返回 None 时维持既有 good 快进行为。
    """
    try:
        stage_target = max(0, int(expected_stage or 0))
    except (TypeError, ValueError):
        return None
    path = Path(log_file) if log_file else _default_review_log()
    if path is None or not path.exists():
        return None

    subj_key = str(subject or "").strip().lower()
    title_key = str(title or "").strip()
    if not subj_key or not title_key:
        return None

    filtered = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:  # pragma: no cover - 读取失败按不可用处理
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if str(event.get("subject") or "").strip().lower() != subj_key:
            continue
        ev_title = str(event.get("title") or "").strip()
        # 标题双向包含匹配（len>=2 防单字误配）；跨卡污染由下方 stage 链校验拦截
        if not (ev_title == title_key
                or (len(ev_title) >= 2 and ev_title in title_key)
                or (len(title_key) >= 2 and title_key in ev_title)):
            continue
        filtered.append(event)

    if not filtered:
        return None

    expected = 0
    cycle: list = []
    for event in filtered:
        rating = str(event.get("rating") or "").strip().lower()
        if rating not in RATING_MAP:
            return None
        try:
            stage_before = int(event.get("stage_before"))
        except (TypeError, ValueError):
            return None
        if stage_before != expected:
            return None  # 断链：日志与卡片状态不连续
        if rating == "again":
            # again = 整个周期重置（与 compute_next_interval 的 stage 语义一致）
            cycle = []
            expected = 0
        else:
            cycle.append(rating)
            expected = stage_before + 1
    if expected != stage_target:
        return None  # 链终点与卡片 stage 不符
    return cycle


def compute_next_interval(
    stage: int = 0,
    rating: str = "good",
    today: date | None = None,
    history=None,
):
    """依据已完成的复测档位与本次评级，计算下一次复测安排。

    Args:
        stage: 该错题**已完成**的复测次数（≥0）。
        rating: 本次复测评级，``again`` / ``hard`` / ``good`` / ``easy``。
        today: 计算基准日，缺省为本地今天。
        history: [P1 修复·2026-10-08 K2] 该卡**当前周期**的真实评级序列（自上次 again
            重置之后，长度应为 stage）。传 None 或不可信序列时维持既有的
            「stage 次 good 快进」重建（向后兼容）；传入可信序列时按真实
            评级回放重建记忆状态 —— 反复 hard 的弱项不再被高估间隔。

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
    # [修复 2026-10-05] 钳到 _MAX_STAGE：防止外部损坏/被篡改的超大 stage
    # 让下方逐档快进循环卡死（见 _MAX_STAGE 说明）。
    effective_stage = min(effective_stage, _MAX_STAGE)

    sched = _scheduler()
    # 以基准日零点(UTC)为时间轴原点，逐次快进重建记忆稳定性
    now = datetime.combine(today, time.min).replace(tzinfo=timezone.utc)
    card = Card()

    # [P1 修复·2026-10-08 K2] 有可信真实历史 → 按真实评级序列回放；否则维持 good 快进。
    # 回放路径与快进路径共用同一条时间轴推进规则（沿真实到期时刻推进）。
    replay = _normalize_history(history, effective_stage)
    if replay is not None:
        for past_rating in replay:
            card, _log = sched.review_card(card, _resolve_rating(past_rating), now)
            now = card.due
    else:
        for _ in range(effective_stage):
            card, _log = sched.review_card(card, _BUILD_UP_RATING, now)
            now = card.due  # 沿真实到期时刻推进，保持 FSRS 时间轴正确

    card, _log = sched.review_card(card, target, now)

    interval_days = _gap_days(now, card.due)
    new_stage = 0 if target is Rating.Again else stage + 1
    return new_stage, today + timedelta(days=interval_days), interval_days

