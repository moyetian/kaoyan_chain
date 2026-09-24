# -*- coding: utf-8 -*-
"""
整卷级模考与多题诊断引擎 (Exam Diagnoser)
对标：讯飞星火整卷诊断（诊病因、给方案全流程）
核心功能：
  1. 聚合整套自测卷/模考卷的多题批改结果
  2. 输出三维诊断报告：
     - 章节失分重灾区排行
     - 错因五分类聚类占比 (概念漏洞 / 审题偏差 / 公式记错 / 计算失误 / 书写丢分)
     - 动态精力重分配建议 (针对下周各科与各章节的权重调优)
"""

from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

try:
    from skills import get_subject_name
except Exception:
    try:
        from tools.skills import get_subject_name
    except Exception:
        get_subject_name = lambda s, d=None: SUBJECT_NAMES.get(s, s)

# 仅作 config 缺失时的中性回退；实际科目名以 ky_config.json 的 study_plan 为准
_SUBJECT_NAME_FALLBACK = {
    "math": "数学",
    "eng": "英语",
    "pol": "思想政治理论",
    "pro": "专业课",
}
SUBJECT_NAMES = dict(_SUBJECT_NAME_FALLBACK)


def diagnose_mock_exam(subject="math", exam_input="", **kwargs):
    """
    对模考答卷或多题作答记录进行整卷级聚合诊断
    exam_input: 可以是文本、错题摘要或包含题号/错因的批改报告
    """
    if "subject" in kwargs:
        exam_input = subject
        subject = kwargs["subject"]
    elif subject not in SUBJECT_NAMES and not exam_input:
        exam_input = subject
        subject = "math"

    subj_name = get_subject_name(subject, SUBJECT_NAMES.get(subject, subject))

    # 1. 启发式解析错题项与错因分类
    # 统计错因五分类
    categories = {
        "概念漏洞": 0,
        "审题偏差": 0,
        "公式记错": 0,
        "计算失误": 0,
        "书写丢分": 0,
    }

    # 常见章节失分关键词匹配
    chapter_keywords = {
        "math": ["极限与连续", "微分中值定理", "不定积分与定积分", "二重积分", "微分方程", "线性代数二次型"],
        "eng": ["长难句主干", "阅读细节定位", "词汇派生辨析", "翻译意群切分", "小作文格式", "大作文论点"],
        "pol": ["马原唯物辩证法", "毛中特经济政治", "史纲重大历史节点", "思修道德法律", "形势与政策"],
        "pro": ["线性表与链表", "树与二叉树遍历", "图最短路径", "CPU数据通路", "虚拟内存分页", "TCP拥塞控制"]
    }

    sub_keywords = chapter_keywords.get(subject, ["核心基础模块", "综合应用大题"])
    chapter_loss = {kw: 0 for kw in sub_keywords}

    lines = exam_input.splitlines() if isinstance(exam_input, str) else []
    total_detected_errors = 0

    for line in lines:
        for cat in categories:
            if cat in line:
                categories[cat] += 1
                total_detected_errors += 1
        for chap in sub_keywords:
            if chap in line:
                chapter_loss[chap] += 1

    # 若输入文本未检测到有效错因与失分关键词，诚实返回样本不足提示，绝不伪造虚假数据
    if total_detected_errors == 0:
        empty_report = f"""============================================================
  🩺 考研全科 AI 私人教师 · 整卷级模考诊断报告 ({subj_name})
============================================================
评估时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
诊断样本: 聚合 0 处核心丢分点与步骤失分

【提示】当前输入的答题卡文本未匹配到明确的失分与错因记录样本。
建议：
  1. 请提供包含错因标注的答题卡记录（如“第3题 概念漏洞”、“第5题 计算失误”）；
  2. 或指明章节失分点（如“泰勒展开未写全”、“二重积分计算失误”）；
  3. 系统将基于真实丢分情况为您生成章节失分排行与针对性复习建议。
============================================================
"""
        return {
            "subject": subject,
            "subject_name": subj_name,
            "total_errors": 0,
            "top_chapter": "无明显失分",
            "top_cause": "无明显错因",
            "chapter_loss": chapter_loss,
            "cause_distribution": categories,
            "report": empty_report
        }

    # 2. 排序失分最高的章节与错因
    sorted_chapters = sorted(chapter_loss.items(), key=lambda x: x[1], reverse=True)
    sorted_causes = sorted(categories.items(), key=lambda x: x[1], reverse=True)

    # [根因修复·零命中退化] 输入里没有任何章节关键词命中时 chapter_loss 全为 0，
    # 旧代码仍取 sorted_chapters[0][0] —— 拿到的其实是「章节表里的第一项」，与数据无关。
    # 于是 814 考生输入「泰勒展开」会被建议去攻坚「线性表与链表」（那是 408 的章节），
    # 且第 3 条还宣称「已自动录入 FSRS 待复测队列」—— 而本函数根本没有任何写入调用，
    # 属于程序臆造自身行为。这里：零命中即不选章节、不排名、不声称已录入。
    chapter_hits = sum(chapter_loss.values())
    top_chap = sorted_chapters[0][0] if (sorted_chapters and chapter_hits > 0) else None
    top_cause = sorted_causes[0][0] if sorted_causes else "基础概念"

    adjustment_tips = [
        f"2. 【防踩坑处方】本套试卷头号丢分杀手为「{top_cause}」，推导时务必在草稿纸标注采分步骤，拒绝跳步与心算；",
    ]
    if top_chap:
        adjustment_tips.insert(
            0,
            f"1. 【重点攻坚】下周向【{top_chap}】倾斜每日 30~45 分钟专项攻坚，优先扫清该模块例题；")
        adjustment_tips.append(
            f"3. 【变式题闭环】建议针对【{top_chap}】在 48 小时内使用 /exam 触发一次针对性重测"
            f"（如需进入 FSRS 复测队列，请先手动登记错题为凭）。")
    else:
        adjustment_tips.insert(
            0,
            "1. 【样本不足】本次输入未匹配到章节级失分点，暂不生成章节攻坚建议；"
            "请补充「章节名 + 错因」形式的记录（如「卷积积分 计算失误」）。")

    report = f"""
============================================================
  🩺 考研全科 AI 私人教师 · 整卷级模考诊断报告 ({subj_name})
============================================================
评估时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
诊断样本: 聚合 {total_detected_errors} 处核心丢分点与步骤失分

【一、章节失分重灾区排行榜】
"""
    if chapter_hits > 0:
        for rank, (chap, cnt) in enumerate(sorted_chapters[:4], 1):
            bar = "█" * (cnt * 2)
            report += f"  {rank}. {chap:<14} 扣分频次: {cnt} 次  {bar}\n"
    else:
        report += "  未识别到章节级失分信号（本次输入未命中任何章节关键词），暂不排名。\n"

    report += f"\n【二、错因五分类分布占比】\n"
    for cat, cnt in sorted_causes:
        pct = round(cnt / total_detected_errors * 100, 1) if total_detected_errors > 0 else 0
        report += f"  • {cat:<8} : {cnt} 次 ({pct}%)\n"

    report += f"\n【三、下周复习精力动态重分配建议】\n"
    for tip in adjustment_tips:
        report += f"  {tip}\n"

    report += f"============================================================\n"

    return {
        "subject": subject,
        "subject_name": subj_name,
        "total_errors": total_detected_errors,
        "top_chapter": top_chap or "无明显章节失分",
        "top_cause": top_cause,
        "chapter_loss": chapter_loss,
        "cause_distribution": categories,
        "report": report
    }


def format_diagnosis_report(diag_result: dict) -> str:
    """返回格式化好的整卷诊断报告文本"""
    if isinstance(diag_result, dict):
        return diag_result.get("report", "")
    return str(diag_result)


def health_check() -> dict:
    """[B4] 结构化健康自检：``{"status": READY/DEGRADED/UNAVAILABLE, "reason": str}``。

    整卷诊断是纯本地聚合（读配置只为科目名，不写盘、不联网）：用**真调一次最小
    用例**验证"解析→聚类→出报告"链路没断（只查函数存在会漏掉内部逻辑损坏）。
    """
    try:
        diag = diagnose_mock_exam(subject="math", exam_input="样本：第 1 题 计算失误 扣 2 分")
        report = format_diagnosis_report(diag)
    except Exception as e:  # noqa: BLE001 - 自检异常必须收敛为可见状态
        return {"status": "UNAVAILABLE",
                "reason": f"整卷诊断链路失败（{type(e).__name__}: {e}），ky diagnose 将不可用"}
    if not report or not str(report).strip():
        return {"status": "UNAVAILABLE", "reason": "整卷诊断产出为空，ky diagnose 将不可用"}
    return {"status": "READY", "reason": "整卷失分聚类与诊断报告可用（纯本地逻辑，无外部依赖）"}
