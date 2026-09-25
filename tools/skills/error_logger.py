# -*- coding: utf-8 -*-
"""
考研错题沉淀与 FSRS 盲盒复测闭环引擎 (Error Logger & FSRS Review Engine)
核心使命：
  1. 将批改产生的错题规范化沉淀到对应科目的「错题本/」
  2. 自动追踪 FSRS 自适应记忆周期（间隔由 fsrs 库按记忆稳定性动态给出，
     不再使用固定天数阶梯）
  3. 支撑 /review 与 /quiz 指令：自动隐去历史解析，生成“盲盒复测试卷”
  4. 闭环状态回写：复测通过标记 [已掌握]，复测失误重置周期，联动更新薄弱点雷达！

复测间隔的唯一真源为 ``fsrs_scheduler.compute_next_interval``（纯函数、可复现）。
本模块不再自带任何间隔常量或本地推算逻辑，避免"文案一套、实现一套"。
"""

import re
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

try:
    from skills import get_subject_name
except Exception:
    try:
        from tools.skills import get_subject_name
    except Exception:
        get_subject_name = lambda s, d=None: SUBJECT_NAMES.get(s, s)

try:  # 公共 IO 工具：原子写 + 文件名净化 + 路径包含断言（双导入路径兼容）
    from ky_io import atomic_write_text, guard_write, safe_filename, is_within
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text, guard_write, safe_filename, is_within

try:  # 笔记锁定闸门：frontmatter 中 locked: true 的卡片禁止被自动改写
    from note_lock import NoteLockedError, assert_writable
except ImportError:  # pragma: no cover - 兼容 tools. 包式导入
    from tools.note_lock import NoteLockedError, assert_writable

SUBJECT_DIRS = {
    "math": "01-数学",
    "eng": "02-英语",
    "pol": "03-思想政治理论",
    "pro": "04-专业课",
}

# ── 科目名归一化（P2 修复）────────────────────────────────────────
# log_mistake 工具的 schema 只声明 math/eng/pol/pro，但 LLM 不保证遵守；
# 此前 SUBJECT_DIRS.get(subject, "01-数学") 会把任何无法识别的科目名（含中文名）
# 静默写进数学错题本并回报「已成功归档」—— 不考数学的文科考生错题全堆进 01-数学，
# 专业课错题队列永远为空。现统一归一化，无法识别时显式报错，绝不静默回退。
_SUBJECT_ALIASES = {
    # 数学
    "math": "math", "数学": "math",
    "数一": "math", "数二": "math", "数三": "math",
    "数学一": "math", "数学二": "math", "数学三": "math",
    "数1": "math", "数2": "math", "数3": "math",
    "math1": "math", "math2": "math", "math3": "math",
    "math396": "math", "396": "math",
    # 英语
    "eng": "eng", "英语": "eng",
    "英一": "eng", "英二": "eng", "英语一": "eng", "英语二": "eng",
    "eng1": "eng", "eng2": "eng",
    # 政治
    "pol": "pol", "政治": "pol", "思想政治理论": "pol",
    "政治理论": "pol", "思政": "pol",
    # 专业课（含统考代码 408 计算机 / 199 管综 / 432 统计）
    "pro": "pro", "专业课": "pro", "专业课一": "pro", "专业课二": "pro",
    "专业": "pro", "自命题": "pro", "pro2": "pro",
    "408": "pro", "199": "pro", "432": "pro",
}


def _configured_pro_name() -> str:
    """读取 ky_config.json 的 study_plan.pro_name（失败返回空串）。"""
    import json
    try:
        cfg = json.loads((ROOT / "ky_config.json").read_text(encoding="utf-8"))
        return str((cfg.get("study_plan") or {}).get("pro_name") or "").strip()
    except Exception:
        return ""


def normalize_subject(subject) -> str:
    """把科目名/别名归一化为 math / eng / pol / pro。

    支持中文名与常见别名（数学/数一/数二/数三、英语/英一/英二、
    政治/思想政治理论/思政、专业课/专业/自命题），以及配置里的专业课名
    （如 study_plan.pro_name 含 408/199 时识别为 pro）。

    无法识别时抛 ``ValueError`` —— 调用方必须显式处理，严禁静默回退到数学。
    """
    raw = str(subject or "").strip()
    if not raw:
        raise ValueError("科目名为空，无法归档错题；请传入 math/eng/pol/pro 或对应中文名")
    key = raw.lower()
    if key in _SUBJECT_ALIASES:
        return _SUBJECT_ALIASES[key]
    pro_name = _configured_pro_name()
    if pro_name:
        pn = pro_name.lower()
        if pn and (pn in key or key in pn):
            return "pro"
    raise ValueError(
        f"无法识别的科目名 {subject!r}，请使用 math/eng/pol/pro 或对应中文名；"
        "已拒绝归档，以免错题串入其他科目"
    )

# 仅作 config 缺失时的中性回退；实际科目名以 ky_config.json 的 study_plan 为准
_SUBJECT_NAME_FALLBACK = {
    "math": "数学",
    "eng": "英语",
    "pol": "思想政治理论",
    "pro": "专业课",
}
SUBJECT_NAMES = dict(_SUBJECT_NAME_FALLBACK)

# 复测间隔计算统一委托给 FSRS 调度器（唯一真源）。
# 注：此处曾定义 FSRS_GOOD_INTERVALS / FSRS_EASY_INTERVALS 两个固定阶梯常量，
# 且全项目零引用（死常量），同时与真实 FSRS 计算结果不一致，已删除。
try:
    from fsrs_scheduler import compute_next_interval
except ImportError:  # pragma: no cover - 兼容 tools. 包式导入
    from tools.fsrs_scheduler import compute_next_interval

# [C3 题源溯源] 错题卡身份读取：backfill 补录过「题源ID / 题源校验和」的错题卡，
# 在进组卷/复测队列前必须校验题干与身份是否一致（与白名单卡同一防篡改闸门）。
try:
    from question_source import ORIGIN_MISTAKE as _ORIGIN_MISTAKE
    from question_source import has_declared_identity, source_from_card
except ImportError:  # pragma: no cover - 兼容 tools. 包式导入
    from tools.skills.question_source import ORIGIN_MISTAKE as _ORIGIN_MISTAKE
    from tools.skills.question_source import has_declared_identity, source_from_card


# ── 终端框线宽度工具 ──────────────────────────────────────────────
# 中文/全角/emoji 在等宽终端占 2 列，直接用 f"{s:<20}" 按字符数补白会
# 导致右侧框线错位。以下工具按真实显示宽度补白。
_BOX_INNER_W = 72  # 盲盒考核卡片的内框宽度（总宽 74）


def _display_width(text: str) -> int:
    """字符串在等宽终端下的显示列数（East Asian Wide/Fullwidth 计 2 列）。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in str(text))


def _pad_box(text: str, width: int = _BOX_INNER_W) -> str:
    """把 ``text`` 按显示宽度右侧补白到 ``width`` 列；超宽则原样返回。"""
    text = str(text)
    pad = width - _display_width(text)
    return text + (" " * pad if pad > 0 else "")


def calc_fsrs_interval(stage: int = 0, rating: str = "good", today: date = None):
    """FSRS 自适应复测间隔计算（薄封装，实现见 fsrs_scheduler）。

    Args:
        stage: 该错题已完成的复测次数。
        rating: 本次评级 ``again`` / ``hard`` / ``good`` / ``easy``。
        today: 计算基准日，缺省为本地今天。

    Returns:
        ``(new_stage, next_due_date, interval_days)``，语义详见
        :func:`fsrs_scheduler.compute_next_interval`。

    设计约束：错题沉淀与回写属主链路，任何记忆算法异常都不允许中断它，
    故此处对异常做兜底降级（回退为 1 天后复测）而非向上抛出。
    """
    try:
        return compute_next_interval(stage=stage, rating=rating, today=today)
    except Exception as e:  # noqa: BLE001 - 主链路必须可降级
        import logging
        logging.warning(f"FSRS 间隔计算失败，降级为 1 天后复测: {e}")
        base = today or date.today()
        new_stage = 0 if str(rating or "").strip().lower() == "again" else max(0, int(stage or 0)) + 1
        return new_stage, base + timedelta(days=1), 1


def _next_due(today: date, stage_index: int = 0, rating: str = "good"):
    """返回下次到期日（向后兼容既有调用方）。"""
    _, next_due_d, _ = calc_fsrs_interval(stage=stage_index, rating=rating, today=today)
    return next_due_d


def next_due_with_interval(today: date, stage_index: int = 0, rating: str = "good"):
    """返回 ``(下次到期日, 间隔天数)``，供记录模板一次性写入两者。"""
    _, next_due_d, interval_days = calc_fsrs_interval(
        stage=stage_index, rating=rating, today=today
    )
    return next_due_d, interval_days


def health_check() -> dict:
    """[B4] 结构化健康自检：``{"status": READY/DEGRADED/UNAVAILABLE, "reason": str}``。

    判据是"复测调度是否真的算得出来"：用 0 档 good 评级**真跑一次** FSRS 最小用例
    （纯本地、毫秒级、不联网）。一旦 fsrs 版本 API 不兼容（如缺
    ``Scheduler.review_card``），这里会抛异常 —— 错题归档与盲盒复测主链路瘫痪，
    必须报 UNAVAILABLE 而不是显示"已就绪"。这正是旧硬编码状态最危险的盲区：
    doctor 报全绿，真到记错题才炸。
    """
    try:
        calc_fsrs_interval(stage=0, rating="good", today=date.today())
    except Exception as e:  # noqa: BLE001 - 自检异常必须收敛为可见状态
        return {"status": "UNAVAILABLE",
                "reason": f"FSRS 复测调度不可用（{type(e).__name__}: {e}），错题归档与盲盒复测将无法排期"}
    return {"status": "READY", "reason": "FSRS 自适应复测调度可用（错题归档与到期复测闭环正常）"}


# ── 复测事件日志（供 FSRS 校准度评测使用）─────────────────────────────
# 仅凭错题卡片里的 stage/到期日**无法**做真实的 RMSE/LogLoss 评测：
# 校准度需要「模型预测的回忆概率」与「实际是否想起」的成对样本。
# 因此在每次复测回写时追加一条机器可读事件到 .memory/review_log.jsonl。
REVIEW_LOG_FILE = ROOT / ".memory" / "review_log.jsonl"


def record_review_event(
    subject: str,
    title: str,
    stage_before: int,
    rating: str,
    due_before: str = "",
    days_late: int = 0,
    interval_after: int = 0,
    due_after: str = "",
) -> bool:
    """追加一条复测事件（JSONL，单行一条），失败只告警不阻断主链路。"""
    import json as _json
    event = {
        "ts": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "subject": str(subject or ""),
        "title": str(title or ""),
        "stage_before": int(stage_before or 0),
        "rating": str(rating or "").strip().lower(),
        "due_before": str(due_before or ""),
        "days_late": int(days_late or 0),
        "interval_after": int(interval_after or 0),
        "due_after": str(due_after or ""),
    }
    try:
        # [safe 模式收口] 这里原先是裸 ``open(..., "a")`` 追加，绕开了 ky_io 的统一
        # 写闸门。虽然当前唯一调用方 ``mark_error_status`` 会先经 atomic_write_text
        # 写错题卡片（已被闸门拦住、走不到这里），但「写盘点必须过闸门」不应依赖
        # 调用顺序 —— 先过 guard_write，只读模式下直接拒绝。
        guard_write("追加复测事件日志", REVIEW_LOG_FILE)
        REVIEW_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(REVIEW_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(_json.dumps(event, ensure_ascii=False) + "\n")
        return True
    except Exception as e:  # noqa: BLE001 - 日志失败不得影响错题闭环
        import logging
        logging.warning(f"复测事件日志写入失败: {e}")
        return False


def log_error_record(subject="math", title="错题记录", error_type="计算失误", detail="", prescription="", question=""):
    """向对应科目的错题本追加一条结构化错题记录"""
    # [缺陷修复·错因五分类硬约束] AGENTS.md 规定错因只能取五分类（+「无」）。
    # 此前写入链路不校验，任意字符串（含 LLM 幻觉出的分类名）会被原样落盘，
    # 污染薄弱点雷达的错因统计口径。此处统一收敛到 open_grader 的白名单。
    try:
        from skills.open_grader import MISTAKE_TYPES
    except ImportError:  # pragma: no cover
        from tools.skills.open_grader import MISTAKE_TYPES  # type: ignore
    if error_type not in MISTAKE_TYPES:
        error_type = "概念漏洞"
    # [P2 修复] 科目名先归一化；无法识别时抛 ValueError，绝不静默回退数学
    subject = normalize_subject(subject)
    subj_folder = SUBJECT_DIRS[subject]
    mistake_dir = ROOT / subj_folder / "错题本"
    if subject == "eng":
        mistake_dir = ROOT / subj_folder / "错题与长难句本"

    mistake_dir.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_d = date.today()
    # 首次复测安排：stage=0（尚未复测）按 good 评级推导，间隔由 FSRS 实时给出
    next_due_d, first_interval = next_due_with_interval(today_d, 0, "good")
    next_due_str = next_due_d.strftime("%Y-%m-%d")
    record_file = mistake_dir / f"错题记录_{today_str}.md"

    q_block = f"\n- **题干设问**：\n```text\n{question.strip()}\n```\n" if question else ""

    record_md = f"""
## 📌 [{today_str}] {title}
- **掌握状态**：`[待复测]` (FSRS 自适应复测中)
- **错因分类**：`{error_type}` (概念漏洞 / 审题偏差 / 公式记错 / 计算失误 / 书写丢分){q_block}
- **复测节奏**：`stage=0` · 下次到期 `{next_due_str}`（距今日 {first_interval} 天 · FSRS good 评级自适应）
- **错题现场与漏洞分析**：
{detail.strip()}
- **专家处方与改进建议**：
{prescription.strip()}
- **复习规划**：排入 FSRS 自适应复测队列，复测日 `{next_due_str}` 由 get_due_reviews 自动筛选
---
"""
    # 学员可对错题卡片声明 `locked: true` 将其钉死（追加与新建都在此拦截）
    assert_writable(record_file)
    if record_file.exists():
        with open(record_file, "a", encoding="utf-8") as f:
            f.write(record_md)
    else:
        atomic_write_text(record_file,
                          f"# {subj_folder} · 错题积累集 ({today_str})\n" + record_md)

    # 联动更新雷达错因累计
    _sync_radar_error_count(subject, error_type, title)

    return f"已成功将错题归档至: {record_file.relative_to(ROOT)}"


def _sync_radar_error_count(subject: str, error_type: str, title: str):
    """联动更新对应科目薄弱点雷达/学情档案中的错因统计次数"""
    folder_name = SUBJECT_DIRS.get(subject, "01-数学")
    candidates = [
        ROOT / folder_name / "_状态" / "薄弱点雷达.md",
        ROOT / folder_name / "学情档案.md",
        ROOT / folder_name / "_状态" / "学情档案.md",
    ]
    for r_file in candidates:
        if not r_file.exists():
            continue
        try:
            content = r_file.read_text(encoding="utf-8")
            lines = content.splitlines()
            new_lines = []
            updated = False
            for line in lines:
                if error_type and error_type in line and line.strip().startswith("|") and line.strip().endswith("|"):
                    parts = [p.strip() for p in line.strip().split("|")[1:-1]]
                    if parts:
                        for idx in reversed(range(len(parts))):
                            if parts[idx].isdigit():
                                cur_val = int(parts[idx])
                                parts[idx] = str(cur_val + 1)
                                line = "| " + " | ".join(parts) + " |"
                                updated = True
                                break
                new_lines.append(line)
            if updated:
                atomic_write_text(r_file, "\n".join(new_lines))
        except Exception as e:
            # 不再静默吞错：雷达统计回写失败必须可见，否则错因计数会悄悄失真
            print(f"[warn] 薄弱点雷达回写失败 ({r_file.name}): {e}", file=sys.stderr)


def scan_error_records(subject=None):
    """
    扫描各科目「错题本/」下的全部错题记录
    返回结构化字典列表
    """
    subjs = [subject] if subject and subject in SUBJECT_DIRS else list(SUBJECT_DIRS.keys())
    results = []

    for s in subjs:
        folder_name = SUBJECT_DIRS[s]
        mistake_dir = ROOT / folder_name / "错题本"
        if s == "eng" and not mistake_dir.exists():
            mistake_dir = ROOT / folder_name / "错题与长难句本"

        if not mistake_dir.exists():
            continue

        for md_file in mistake_dir.glob("*.md"):
            if md_file.name.startswith(("_", ".", "自测卷_")) or "模板" in md_file.name or "索引" in md_file.name:
                continue
            try:
                content = md_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            # 按 ## 📌 [YYYY-MM-DD] 划分错题卡片
            sections = re.split(r"\n(?=##\s+📌)", content)
            for sec in sections:
                if not sec.strip().startswith("## 📌"):
                    continue
                # 提取日期与标题
                header_m = re.search(r"##\s+📌\s*\[(\d{4}-\d{2}-\d{2})\]\s*(.*)", sec)
                if not header_m:
                    continue
                rec_date = header_m.group(1).strip()
                title = header_m.group(2).strip()

                # 提取状态
                status = "待复测"
                status_m = re.search(r"-\s+\*\*掌握状态\*\*[：:]\s*`?\[?(.*?)\]?`?(?:\s|$)", sec)
                if status_m:
                    status = status_m.group(1).strip()

                # 提取错因
                err_type = "需强化复练"
                err_m = re.search(r"-\s+\*\*错因分类\*\*[：:]\s*`?(.*?)`?(?:\s|\(|$)", sec)
                if err_m:
                    err_type = err_m.group(1).strip().strip("`")

                # 提取题干设问
                q_text = ""
                q_m = re.search(r"-\s+\*\*题干设问\*\*[：:]\s*```text\s*(.*?)\s*```", sec, re.DOTALL)
                if q_m:
                    q_text = q_m.group(1).strip()

                # 提取错题现场与解析
                detail_text = ""
                d_m = re.search(r"-\s+\*\*错题现场与漏洞分析\*\*[：:]\s*(.*?)(?=\n-\s+\*\*专家处方|\n-\s+\*\*复习规划|\Z)", sec, re.DOTALL)
                if d_m:
                    detail_text = d_m.group(1).strip()

                # 提取复测节奏 stage 与 next_due_date
                stage = 0
                next_due = ""
                sched_m = re.search(r"-\s+\*\*复测节奏\*\*[：:]\s*`?stage=(\d+)`?(?:.*|)\s*下次到期\s*`?(\d{4}-\d{2}-\d{2})`?", sec, re.DOTALL)
                if sched_m:
                    try:
                        stage = int(sched_m.group(1))
                    except Exception:
                        stage = 0
                    next_due = sched_m.group(2).strip()

                # 提取【标准答案】用于自动判卷比对。
                # 注意：detail_text 是「错因描述」，绝不能当作标准答案参与比对，
                # 否则会出现"答 999 也判通过"的致命误判。
                std_ans = ""
                sa_m = re.search(
                    r"-\s+\*\*标准答案\*\*[：:]\s*(.*?)(?=\n-\s+\*\*|\n---|\Z)", sec, re.DOTALL)
                if not sa_m:
                    sa_m = re.search(
                        r"###\s*三[、.．]\s*标准规范解答[：:]?\s*(.*?)(?=\n###|\n---|\Z)", sec, re.DOTALL)
                if sa_m:
                    std_ans = sa_m.group(1).strip()

                def _clean_fallback_stem(text: str) -> str:
                    if not text:
                        return ""
                    bad_markers = ["未作答或明确放弃", "判卷未通过", "由自测卷链路自动归档"]
                    if any(marker in text for marker in bad_markers):
                        return ""
                    return text[:200]

                question_text = q_text or _clean_fallback_stem(detail_text)
                # [C3 题源溯源] 身份闭环：backfill 给错题卡补录过「题源ID / 题源校验和」
                # 的，声明过的身份必须与题干自洽 —— 否则卡片被改动过，标记
                # ``source_tampered`` 供组卷侧排除（与白名单卡同一闸门）。
                # 未声明过身份的存量卡：惰性构建（不落盘），进卷身份 = 当前题干。
                src = (source_from_card(sec, origin=_ORIGIN_MISTAKE, kind="mistake",
                                        fallback_stem=question_text)
                       if question_text else None)
                tampered = bool(has_declared_identity(sec, kind="mistake")
                                and (src is None or not src.verify(question_text)))

                results.append({
                    "subject": s,
                    "subject_name": get_subject_name(s, SUBJECT_NAMES.get(s, s)),
                    "file_path": str(md_file),
                    "file_name": md_file.name,
                    "date": rec_date,
                    "title": title,
                    "status": status,
                    "error_type": err_type,
                    "stage": stage,
                    "next_due": next_due,
                    "question": question_text,
                    "detail": detail_text or q_text or "",
                    "standard_answer": std_ans,
                    # [C3 题源溯源] 题源身份（落盘读回或惰性构建）
                    "source_id": src.source_id if src else "",
                    "source_checksum": src.checksum if src else "",
                    "source_tampered": tampered,
                    "raw_section": sec
                })

    return results

def get_due_reviews(subject=None, max_count=5):
    """
    真正的 FSRS 到期筛选：只返回 next_due_date ≤ today 的待复测题。
    - 旧记录（无 next_due 字段）按首次到期处理：date + 1天 ≤ today 即到期
    - 缺失 next_due 但归档时间已 ≥1 天，保守视为到期（向后兼容）
    - 按到期天数倒序排，最久没复测的优先
    """
    all_records = scan_error_records(subject)
    today = date.today()
    due_items = []

    for item in all_records:
        if "已掌握" in item["status"]:
            continue

        # 计算 next_due：优先用记录字段，缺失则回退到 date + 1天
        next_due = item.get("next_due") or ""
        if next_due:
            try:
                next_due_d = datetime.strptime(next_due, "%Y-%m-%d").date()
            except Exception:
                next_due_d = None
        else:
            next_due_d = None

        try:
            rec_d = datetime.strptime(item["date"], "%Y-%m-%d").date()
        except Exception:
            rec_d = None

        if next_due_d is None and rec_d is not None:
            from datetime import timedelta
            next_due_d = rec_d + timedelta(days=1)  # 向后兼容：旧记录视为首次到期

        if next_due_d is None:
            continue

        days_until = (next_due_d - today).days
        if days_until > 0:
            # 还没到期：不进入队列
            item["days_until_due"] = days_until
            continue

        item["days_until_due"] = days_until  # 0 或负数
        item["days_overdue"] = -days_until
        item["days_ago"] = max(0, (today - rec_d).days) if rec_d else 0
        due_items.append(item)

    # 逾期最久的优先（days_overdue 越大越优先）
    due_items.sort(key=lambda x: (x.get("days_overdue", 0), x["date"]), reverse=True)
    return due_items[:max_count]

def generate_blind_quiz(error_item):
    """
    生成盲盒测试题面：抹去原答案与解题过程，只保留题干背景与错因提示
    """
    s_name = error_item.get("subject_name", "考研科目")
    e_type = error_item.get("error_type", "综合漏洞")
    title = error_item.get("title", "核心错题复测")
    q_content = error_item.get("question", "").strip()

    days_ago = error_item.get('days_ago')
    if days_ago is None and error_item.get('date'):
        try:
            d_obj = datetime.strptime(error_item['date'], "%Y-%m-%d").date()
            days_ago = max(0, (date.today() - d_obj).days)
        except Exception:
            days_ago = 0
    days_ago = days_ago or 0

    # 卡片内容行需按**终端显示宽度**补白：中文/全角字符占 2 列，
    # 用 f"{s:<20}" 这类按"字符数"补齐必然对不齐右框线（实测短 5 列）。
    quiz_text = f"""
╭────────────────────────────────────────────────────────────────────────╮
│  🎯 【FSRS 错题盲盒重测 · 闭环考核】                                   │
│{_pad_box('  科目: ' + s_name + ' 错因预警: ' + e_type, _BOX_INNER_W)}│
╰────────────────────────────────────────────────────────────────────────╯

📌 【考核题目】: {title}
⏱️ 【历史记录时间】: {error_item.get('date')} ({days_ago} 天前)

📝 【题面核心内容与设问】：
{q_content}

💡 【盲盒重测规则】：
  1. 本界面已自动隐去历史推导过程与标准答案；
  2. 请在草稿纸上独立推导完整步骤；
  3. 推导完毕后，直接输入你的最终结果或解答核心步骤进行核对；
  4. 回答正确将自动标记为 [√ 已掌握] 并从待测队列出库！
"""
    return quiz_text.strip()

def _norm_name(s: str) -> str:
    """文件名归一化：去除扩展名、空白、常见分隔符与全角符号，便于模糊匹配。"""
    s = str(s or "").strip()
    s = re.sub(r"\.(md|txt|markdown)$", "", s, flags=re.IGNORECASE)
    return re.sub(r"[\s_\-—·（）()【】\[\]、,，.。:：]+", "", s).lower()


def _resolve_mistake_file(mistake_dir, safe_name: str, title_keyword: str):
    """[P3 修复·D10] 在错题目录内安全解析目标文件。

    解析顺序（任一命中唯一即返回）：
      ① 原文件名 / 追加 .md；
      ② 归一化文件名模糊匹配（双向包含，命中唯一才采用）；
      ③ 全目录内容锚定：文件正文含「## 📌 [日期] 标题」且 title_keyword 命中唯一。
    连续多命中视为歧义，一律放弃（返回 None），避免误改无关错题。
    """
    if not mistake_dir.exists():
        return None

    cand = mistake_dir / safe_name
    if cand.exists() and cand.is_file():
        return cand
    if not safe_name.lower().endswith(".md"):
        cand2 = mistake_dir / f"{safe_name}.md"
        if cand2.exists() and cand2.is_file():
            return cand2

    target_norm = _norm_name(safe_name)
    if target_norm:
        fuzzy = [f for f in mistake_dir.glob("*.md")
                 if f.is_file() and (_norm_name(f.name) in target_norm or target_norm in _norm_name(f.name))]
        if len(fuzzy) == 1:
            return fuzzy[0]

    kw = str(title_keyword or "").strip()
    if kw:
        anchored = []
        for f in mistake_dir.glob("*.md"):
            if not f.is_file():
                continue
            try:
                txt = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if re.search(rf"##\s+📌\s*\[\d{{4}}-\d{{2}}-\d{{2}}\]\s*{re.escape(kw)}", txt):
                anchored.append(f)
        if len(anchored) == 1:
            print(f"[info] 错题文件名不匹配，已按标题「{kw}」锚定到: {anchored[0].name}",
                  file=sys.stderr)
            return anchored[0]

    return None


def mark_error_status(subject, file_name, title_keyword=None, new_status="已掌握", rating=None, passed=None, **kwargs):
    """
    回写错题卡片的掌握状态 + 联动更新复测节奏（FSRS 自适应）：
      - rating: "again" / "hard" / "good" / "easy"（可选）
      - passed: bool（可选，向后兼容：False -> again，True -> good）
      - new_status: 显式指定掌握状态（默认为 "已掌握"）
      - 调用方拿到 (ok, msg)；状态回写失败时返回 (False, 原因)，由上层提示学员
    """
    if title_keyword is None:
        title_keyword = kwargs.get("title", "")
    title_keyword = str(title_keyword or "").strip()
    # [P0 修复] 空标题会让下方正则退化为"匹配第一条记录"，
    # 导致静默篡改无关错题（不可逆的数据损坏），必须显式拒绝。
    if not title_keyword:
        return False, "缺少定位标题（title_keyword 为空），已拒绝回写以避免误改错题卡片"
    folder_name = SUBJECT_DIRS.get(subject, "01-数学")

    # [安全] file_name 来自外部载荷（答卷密钥 / 切片题源出处），必须先取 basename。
    # 未净化时可被构造成 "../../02-英语/错题与长难句本/错题本.md"，
    # 从而用本错题的内容跨目录整体覆盖另一份已存在的文件（不可逆的数据损坏）。
    # 这里取最后一个路径分量而不是直接拒绝：合法的 file_name 本就是单层文件名，
    # 取 basename 对它完全无影响；而任何穿越片段都会因此失效，落入下列「未找到」分支。
    raw_name = str(file_name or "").strip()
    safe_name = Path(raw_name).name
    if not safe_name:
        return False, "错题文件名为空，已拒绝回写以避免误改文件"
    if safe_name != raw_name:
        print(f"[warn] 错题文件名含路径分量，已收敛为单层文件名: "
              f"{raw_name!r} -> {safe_name!r}", file=sys.stderr)

    mistake_dir = ROOT / folder_name / "错题本"
    target_file = mistake_dir / safe_name
    if subject == "eng" and not target_file.exists():
        mistake_dir = ROOT / folder_name / "错题与长难句本"
        target_file = mistake_dir / safe_name

    # 双保险：解析后必须仍落在预期的错题目录内
    if not is_within(target_file, mistake_dir):
        return False, f"错题文件路径越界，已拒绝回写: {raw_name}"

    if not target_file.exists():
        # [P3 修复·D10] 组卷时写入题卡的 file_name 来自切片源名（如「2025天工大814真题回忆版」），
        # 与错题本实际文件名（如「814错题本.md」）经常对不上，导致批改结果无法回写、闭环断裂。
        # 现改为四级安全解析：① 补 .md 后缀；② 目录内文件名模糊匹配；
        # ③ 全目录按「标题内容锚定」定位（命中唯一才采用）；④ 仍失败则给出候选清单。
        resolved = _resolve_mistake_file(mistake_dir, safe_name, title_keyword)
        if resolved is None:
            return False, f"未找到错题文件: {file_name}"
        target_file = resolved

    content = target_file.read_text(encoding="utf-8", errors="ignore")
    today_d = date.today()

    # 锚定到指定标题的小节（DOTALL 直到下一个 ## 📌 或文件末尾）
    section_pattern = rf"(##\s+📌\s*\[(\d{{4}}-\d{{2}}-\d{{2}})\]\s*{re.escape(title_keyword)}.*?)(?=\n##\s+📌|\Z)"
    m_sec = re.search(section_pattern, content, re.DOTALL)
    if not m_sec:
        return False, f"未在文件 {file_name} 中找到匹配标题「{title_keyword}」的错题卡片，请检查拼写"

    section_text = m_sec.group(1)

    # 提取当前 stage
    cur_stage_m = re.search(r"-\s+\*\*复测节奏\*\*[：:]\s*`?stage=(\d+)`?", section_text)
    cur_stage = int(cur_stage_m.group(1)) if cur_stage_m else 0

    # 解析 rating
    if rating is None:
        if passed is not None:
            rating = "good" if passed else "again"
        elif new_status == "已掌握":
            rating = "good"
        else:
            rating = "again"
    else:
        rating = str(rating).strip().lower()

    # 计算新 stage 与到期日
    new_stage, next_due_d, interval_days = calc_fsrs_interval(stage=cur_stage, rating=rating, today=today_d)
    next_due_str = next_due_d.strftime("%Y-%m-%d")

    actual_status = "待复测" if rating == "again" else new_status

    # 行级精确替换：仅替换 [xxx] 这一对中括号内的内容
    status_pattern = r"(-\s+\*\*掌握状态\*\*[：:]\s*`\[)[^\]]*(\])"
    new_section, n_status = re.subn(
        status_pattern,
        rf"\g<1>{actual_status}\g<2>",
        section_text,
        count=1
    )
    if n_status == 0:
        # 容错：历史遗留的损坏行（如旧版写入的 `[已掌握 (艾宾浩斯复测中)` 缺少右中括号；
        # 该字面量为 FSRS 重构前的存量数据，不得改写未知数据，故仅做容错匹配）
        # 精确正则匹配不到时，退化为整行重写，把该行恢复为规范格式
        new_section, n_status = re.subn(
            r"(-\s+\*\*掌握状态\*\*[：:]\s*)`?[^\n`]*",
            rf"\g<1>`[{actual_status}]`",
            section_text,
            count=1
        )
    if n_status == 0:
        return False, f"错题卡片中未找到「掌握状态」行，无法更新状态（文件: {file_name}）"
    # 替换复测节奏行
    new_section = re.sub(
        r"(-\s+\*\*复测节奏\*\*[：:]).*",
        f"\\g<1> `stage={new_stage}` · 下次到期 `{next_due_str}`（FSRS 自适应: 距今日 {interval_days} 天 | 评级 {rating}）",
        new_section,
        count=1
    )

    new_content = content.replace(section_text, new_section, 1)
    # 学员可对该卡片声明 `locked: true` 将其钉死；此处是回写的唯一写点，故在此拦截
    try:
        assert_writable(target_file)
    except NoteLockedError as e:
        return False, str(e)
    atomic_write_text(target_file, new_content)

    # 记录复测事件（供 evaluate_pipeline 做真实的 FSRS 校准度评测）
    _due_before_m = re.search(r"下次到期\s*`?(\d{4}-\d{2}-\d{2})`?", section_text)
    _due_before = _due_before_m.group(1) if _due_before_m else ""
    _days_late = 0
    if _due_before:
        try:
            _days_late = (today_d - datetime.strptime(_due_before, "%Y-%m-%d").date()).days
        except Exception:
            _days_late = 0
    record_review_event(
        subject=subject,
        title=str(title_keyword or ""),
        stage_before=cur_stage,
        rating=rating,
        due_before=_due_before,
        days_late=_days_late,
        interval_after=interval_days,
        due_after=next_due_str,
    )

    if actual_status == "已掌握":
        msg = f"已掌握，下一次复测日 {next_due_str}（stage={new_stage}, 间隔 {interval_days} 天, 评级: {rating}）"
    else:
        msg = f"已重置为待复测，下次复测日 {next_due_str}（stage={new_stage}, 评级: {rating}）"
    return True, msg
