# -*- coding: utf-8 -*-
"""
考研学习链 · 本地参考资料智能扫描与白名单挂载技能 (Material Scanner & Mounter)

核心功能：
  1. 扫描 01-数学、02-英语、03-思想政治理论、04-专业课 下的「参考资料/」真实目录
  2. 智能过滤空文件、README.md 与系统隐藏文件，识别真实试卷与真题书籍 (PDF/MD/TXT)
  3. 原子写入更新 ky_config.json 与根目录 AGENTS.md 中的「手头资料白名单」
  4. 自动识别学员当前目标院校 (如华南理工大学)，自动激活研招动态监控并生成专属研报
"""

import os
import re
import json
from pathlib import Path
from typing import Dict, List, Any, Optional

ROOT = Path(__file__).resolve().parent.parent.parent

SUBJECT_FOLDER_MAP = {
    "math": ("01-数学", "math_books", "数学"),
    "eng": ("02-英语", "eng_books", "英语"),
    "pol": ("03-思想政治理论", "pol_books", "政治"),
    "pro": ("04-专业课", "pro_books", "专业课"),
}

IGNORE_NAMES = {"readme.md", ".gitkeep", ".gitignore", ".ds_store", "thumbs.db"}


def scan_subject_materials(workspace_root: Path, folder_name: str) -> List[Path]:
    """扫描指定科目目录下的全部真实参考资料文件"""
    ref_dir = workspace_root / folder_name / "参考资料"
    if not ref_dir.exists():
        return []
    valid_files = []
    for f in ref_dir.iterdir():
        if f.is_file() and f.name.lower() not in IGNORE_NAMES:
            if f.stat().st_size > 50:  # 排除空占位文件
                valid_files.append(f)
    return sorted(valid_files, key=lambda x: x.name)


def scan_and_mount_materials(workspace_root: Optional[Path] = None, auto_scout_school: bool = True) -> Dict[str, Any]:
    """
    全量扫描四科参考资料，并原子写回 ky_config.json 和 AGENTS.md
    """
    ws = Path(workspace_root) if workspace_root else ROOT
    results = {}
    details = {}
    total_found = 0

    cfg_file = ws / "ky_config.json"
    cfg = {}
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}

    study_plan = cfg.setdefault("study_plan", {})

    # 1. 逐科扫描
    for key, (folder, config_key, label) in SUBJECT_FOLDER_MAP.items():
        found_files = scan_subject_materials(ws, folder)
        details[key] = [f.name for f in found_files]
        total_found += len(found_files)

        if found_files:
            summary_str = f"[本地资料库已就绪]: " + ", ".join(f.name for f in found_files)
        else:
            subj_title = study_plan.get(f"{key}_name", label)
            summary_str = f"暂未放置实体资料（私教严格按【{subj_title}】官方考纲出题，严禁虚构书目）"

        study_plan[config_key] = summary_str

    # 2. 目标高校自动纳入简章监控与专属情报初始化
    target_school = study_plan.get("school") or cfg.get("target_school") or ""
    target_major = study_plan.get("major") or cfg.get("target_major") or "人工智能"
    school_watch_status = ""

    if target_school and target_school not in ("未指定", "目标院校"):
        try:
            from intelligence.watcher import AdmissionWatcher
            watcher = AdmissionWatcher()
            if target_school not in watcher.list_watched():
                w_res = watcher.add_watch(target_school)
                school_watch_status = f"已将目标高校【{target_school}】自动纳入简章动态指纹监控雷达"
        except Exception:
            pass

        # 检查是否已为目标高校沉淀专属情报
        dossier_file = ws / "04-专业课" / f"目标院校情报_{target_school}_{target_major}.md"
        if auto_scout_school and not dossier_file.exists():
            try:
                from skills import school_scout
                scout_res = school_scout.scout_school(
                    school=target_school,
                    major=target_major,
                    include_social=True,
                    save_report=True
                )
                if scout_res.get("saved_path"):
                    results["scout_report"] = str(scout_res["saved_path"])
            except Exception:
                pass

    # 3. 原子写回 ky_config.json
    try:
        cfg_tmp = cfg_file.with_suffix(".json.tmp")
        cfg_tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        cfg_tmp.replace(cfg_file)
    except Exception as e:
        return {"success": False, "msg": f"写入 ky_config.json 失败: {e}"}

    # 4. 同步更新 AGENTS.md 中的白名单说明
    agents_file = ws / "AGENTS.md"
    if agents_file.exists():
        try:
            agents_text = agents_file.read_text(encoding="utf-8")
            for key, (folder, config_key, label) in SUBJECT_FOLDER_MAP.items():
                val = study_plan[config_key]
                # 正则替换对应科目行
                pattern = rf"(  - {label}: `)([^`]+)(`)"
                if re.search(pattern, agents_text):
                    agents_text = re.sub(pattern, rf"\g<1>{val}\g<3>", agents_text)
            agents_file.write_text(agents_text, encoding="utf-8")
        except Exception:
            pass

    return {
        "success": True,
        "total_files": total_found,
        "details": details,
        "school_watch": school_watch_status,
        "target_school": target_school
    }
