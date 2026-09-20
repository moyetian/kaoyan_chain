# -*- coding: utf-8 -*-
"""
考研学习链 · 本地参考资料智能扫描与白名单挂载技能 (Material Scanner & Mounter)

核心功能：
  1. 扫描 01-数学、02-英语、03-思想政治理论、04-专业课 下的「参考资料/」真实目录
  2. 智能过滤空文件、README.md 与系统隐藏文件，识别真实试卷与真题书籍 (PDF/MD/TXT)
  3. 原子写入更新 ky_config.json 与根目录 AGENTS.md 中的「手头资料白名单」
  4. 自动识别学员当前目标院校 (如目标院校)，自动激活研招动态监控并生成专属研报
"""

import re
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text, guard_write, PermissionDeniedError  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text, guard_write, PermissionDeniedError  # noqa: E402

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


#: 保留的 AGENTS.md 备份份数上限（超出后删除最旧的）
AGENTS_BACKUP_KEEP = 5


def _backup_agents(agents_file: Path, content: str) -> Optional[Path]:
    """把 AGENTS.md 的**改写前内容**另存为带时间戳的备份，返回备份路径。

    AGENTS.md 记录着学员的真实资料白名单与战役参数，且**不在版本控制内**；
    一旦被 scan 覆盖就无从找回。此处只在内容确实发生变化时调用。

    [收尾修复] 备份不再散落在工作区根（此前每次 doctor/scan 都在根目录多一个
    `AGENTS_backup_*`，目录噪音大），统一收敛到 `<workspace>/.memory/agents_backups/`。
    """
    backup_dir = agents_file.parent / ".memory" / "agents_backups"
    # [safe 模式收口] 闸门必须早于 mkdir：否则只读模式下仍会在工作区里凭空建出
    # `.memory/agents_backups/` 目录 —— 只读的承诺是「零工作区副作用」，不只是
    # 「不落文件」。放这里同时也覆盖了下面 atomic_write_text 的那道闸门。
    guard_write("备份 AGENTS.md", backup_dir)
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        backup_dir = agents_file.parent
    try:
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        backup = backup_dir / f"{agents_file.stem}_backup_{stamp}.md"
        # [safe 模式收口] 这里是 AGENTS.md 家族里唯一一处**裸 write_text**：它绕开了
        # ky_io 的统一写闸门（guard_write），只读模式下仍会凭空落一份备份文件。
        # 改为走 atomic_write_text，既接上闸门也顺带获得原子性。
        atomic_write_text(backup, content)
        # 只保留最近 N 份，避免备份无限堆积
        olds = sorted(
            (p for p in backup_dir.glob(f"{agents_file.stem}_backup_*.md")),
            key=lambda p: p.stat().st_mtime,
        )
        for stale in olds[:-AGENTS_BACKUP_KEEP]:
            try:
                stale.unlink()
            except OSError:
                pass
        return backup
    except PermissionDeniedError:
        # 只读模式：备份被闸门拒绝不是「备份失败」，不得静默吞掉 —— 冒泡给调用方，
        # 由它统一提示（调用方随后写 AGENTS.md 时同样会被闸门拦住）。
        raise
    except Exception:
        return None


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
    # [P1 修复·默认专业按人工智能] 此前空专业默认"人工智能"，scout 全套走 CS 模板。
    # 现保持为空，scout 侧按未知专业走待核验口径。
    target_major = study_plan.get("major") or cfg.get("target_major") or ""
    school_watch_status = ""

    if target_school and target_school not in ("未指定", "目标院校"):
        try:
            from intelligence.watcher import AdmissionWatcher
            watcher = AdmissionWatcher()
            _watched_names = [str(w.get("name") or "") for w in watcher.list_watched()]
            if target_school not in _watched_names:
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

    # 3. 原子写回 ky_config.json（原子性由 ky_io.atomic_write_text 统一保证）
    try:
        atomic_write_text(cfg_file, json.dumps(cfg, ensure_ascii=False, indent=2))
    except Exception as e:
        return {"success": False, "msg": f"写入 ky_config.json 失败: {e}"}

    # 4. 同步更新 AGENTS.md 中的白名单说明
    agents_file = ws / "AGENTS.md"
    if agents_file.exists():
        try:
            agents_text = agents_file.read_text(encoding="utf-8")
            original_text = agents_text
            for key, (folder, config_key, label) in SUBJECT_FOLDER_MAP.items():
                val = study_plan[config_key]
                # 正则替换对应科目行
                pattern = rf"(  - {re.escape(label)}: `)([^`]+)(`)"
                if re.search(pattern, agents_text):
                    # [P1 修复] 改用 lambda 替换：val 是真实文件名列表，
                    # 直接拼进 repl 字符串时，若文件名含反斜杠或 \g 会被解释为
                    # 正则反向引用而破坏替换结果（甚至抛 re.error）。
                    agents_text = re.sub(
                        pattern,
                        lambda m, _v=val: f"{m.group(1)}{_v}{m.group(3)}",
                        agents_text)
            if agents_text != original_text:
                # [数据保护] 白名单确实会被改写时先落一份带时间戳的备份。
                # 否则学员临时移走资料再跑一次 ky scan，原有白名单会被静默清空
                # 且无从找回（AGENTS.md 不在版本控制内）。
                backup = _backup_agents(agents_file, original_text)
                # [P1 修复] 原子写回：AGENTS.md 是主协议文件，直接 write_text 若中途
                # 失败会留下被截断的损坏内容，改为临时文件 + replace 原子替换。
                atomic_write_text(agents_file, agents_text)
                if backup:
                    print(f"  [i] 原 AGENTS.md 已备份至: {backup.name}")
        except Exception as e:  # 白名单同步失败不应让整次扫描失败，但必须可见
            print(f"  [!] AGENTS.md 白名单同步失败（其余结果不受影响）: {e}")

    return {
        "success": True,
        "total_files": total_found,
        "details": details,
        "school_watch": school_watch_status,
        "target_school": target_school
    }
