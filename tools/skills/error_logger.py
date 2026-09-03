# -*- coding: utf-8 -*-
"""
错题自动沉淀与薄弱点更新技能 (Error Logger & Radar Updater Skill)
功能：
  1. 将批改产生的错题规范化沉淀到对应科目的「错题本/」
  2. 自动更新「_状态/薄弱点雷达.md」中的错误计数与掌握度
  3. 写入艾宾浩斯复习重做队列 (1天/3天/7天后重测)
"""

from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

SUBJECT_DIRS = {
    "math": "01-数学",
    "eng": "02-英语",
    "pol": "03-思想政治理论",
    "pro": "04-专业课",
}

def log_error_record(subject="math", title="错题记录", error_type="计算失误", detail="", prescription=""):
    """向对应科目的错题本追加一条规范化错题记录"""
    subj_folder = SUBJECT_DIRS.get(subject, "01-数学")
    mistake_dir = ROOT / subj_folder / "错题本"
    if subject == "eng":
        mistake_dir = ROOT / subj_folder / "错题与长难句本"

    mistake_dir.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    record_file = mistake_dir / f"错题记录_{today_str}.md"

    record_md = f"""
## 📌 [{today_str}] {title}
- **错因分类**：`{error_type}` (概念漏洞 / 审题偏差 / 公式记错 / 计算失误 / 书写丢分)
- **错题现场与漏洞分析**：
{detail.strip()}
- **专家处方与改进建议**：
{prescription.strip()}
- **复习规划**：放入 1天 / 3天 / 7天 艾宾浩斯重做队列
---
"""
    if record_file.exists():
        with open(record_file, "a", encoding="utf-8") as f:
            f.write(record_md)
    else:
        record_file.write_text(f"# {subj_folder} · 错题积累集 ({today_str})\n" + record_md, encoding="utf-8")

    return f"已成功将错题归档至: {record_file.relative_to(ROOT)}"
