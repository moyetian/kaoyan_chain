# -*- coding: utf-8 -*-
"""
考研学习链 · 本地参考资料智能扫描与白名单挂载技能 (Material Scanner & Mounter)

核心功能：
  1. 扫描 01-数学、02-英语、03-思想政治理论、04-专业课 下的「参考资料/」真实目录
  2. 智能过滤空文件、README.md 与系统隐藏文件，识别真实试卷与真题书籍 (PDF/MD/TXT)
  3. **默认只读盘点**：仅返回将发生的变更预览（config/AGENTS.md 白名单/研招雷达），
     不落盘；`apply=True` 时才原子写入 ky_config.json 与根目录 AGENTS.md 白名单
  4. `apply=True` 时同步识别学员当前目标院校并激活研招动态监控与专属研报
"""

import re
import json
from pathlib import Path

try:  # 双导入路径兼容（源码脚本式 / tools 包式）
    from workspace import resolve_workspace_root
except ImportError:  # pragma: no cover
    from tools.workspace import resolve_workspace_root
from datetime import datetime
from typing import Dict, List, Any, Optional

try:  # 双导入路径兼容（项目同时存在 tools.X 与 X 两种导入方式）
    from ky_io import atomic_write_text, guard_write, PermissionDeniedError  # noqa: E402
except ImportError:  # pragma: no cover
    from tools.ky_io import atomic_write_text, guard_write, PermissionDeniedError  # noqa: E402

# [W12 P0-1] intelligence 包解析三端统一（此前此处为裸 from intelligence.watcher import）
try:
    from intel_imports import resolve_intel_import
except ImportError:  # pragma: no cover - 包式导入上下文
    from tools.intel_imports import resolve_intel_import

ROOT = resolve_workspace_root(__file__)

SUBJECT_FOLDER_MAP = {
    "math": ("01-数学", "math_books", "数学"),
    "eng": ("02-英语", "eng_books", "英语"),
    "pol": ("03-思想政治理论", "pol_books", "政治"),
    "pro": ("04-专业课", "pro_books", "专业课"),
}

IGNORE_NAMES = {"readme.md", ".gitkeep", ".gitignore", ".ds_store", "thumbs.db"}


def _math_disabled(study_plan: dict) -> bool:
    """方案是否为「不考数学」（与 study_planner / syllabus_manager 口径一致）。"""
    return (str(study_plan.get("math_key") or "").strip().lower()
            in {"none", "no", "不考数学"}
            or str(study_plan.get("math_name") or "").strip() == "不考数学")


def _is_custom_math(study_plan: dict) -> bool:
    """[UT4 修复·PLANNER-6] 数学是否为「院校自主命题」（无全国统考大纲）。

    判定单一真源在 study_planner.is_custom_math_key（此处函数级局部 import，
    与该模块无循环依赖）；探测失败时按字面量 custom 判定兜底。
    """
    try:
        try:
            from study_planner import is_custom_math_key
        except ImportError:  # pragma: no cover - 包式导入上下文
            from tools.study_planner import is_custom_math_key
        return is_custom_math_key((study_plan or {}).get("math_key"))
    except Exception:  # pragma: no cover - 导入失败兜底
        return str((study_plan or {}).get("math_key") or "").strip().lower() == "custom"


def iter_material_files(ref_dir: Path, min_size: int = 0,
                        valid_exts: Optional[set] = None) -> List[Path]:
    """递归枚举「参考资料/」下的真实文件（三端共用的扫描单一实现）。

    [问题5 根因修复·子目录资料不识别] 旧实现三处各写一遍 ``iterdir()`` 单层
    扫描（向导 / 白名单挂载 / Agent 上下文）：学员把资料整理进子目录
    （如 ``参考资料/英语真题/2020.pdf``）后全部识别不到，报到仍显示
    「未放置资料」。现统一递归；过滤隐藏文件、README 与空占位文件。

    :param min_size: 最小字节数（0 = 不过滤；白名单挂载侧传 50 排除空占位）。
    :param valid_exts: 扩展名白名单（None = 不限制；无扩展名文件始终放行）。
    """
    ref_dir = Path(ref_dir)
    if not ref_dir.is_dir():
        return []
    found: List[Path] = []
    for f in ref_dir.rglob("*"):
        try:
            if not f.is_file():
                continue
            name_low = f.name.lower()
            if f.name.startswith(".") or name_low in IGNORE_NAMES or name_low.startswith("readme"):
                continue
            if valid_exts is not None and f.suffix and f.suffix.lower() not in valid_exts:
                continue
            if min_size and f.stat().st_size < min_size:
                continue
        except OSError:
            continue
        found.append(f)
    return sorted(found, key=lambda p: str(p.relative_to(ref_dir)).lower())


def relative_label(ref_dir: Path, file_path: Path) -> str:
    """文件在「参考资料/」内的相对路径标签（统一用 ``/`` 分隔，便于展示）。"""
    try:
        return str(file_path.relative_to(ref_dir)).replace("\\", "/")
    except ValueError:  # pragma: no cover - 防御：跨盘符等异常
        return file_path.name


def scan_subject_materials(workspace_root: Path, folder_name: str) -> List[Path]:
    """扫描指定科目目录下的全部真实参考资料文件（递归含子目录）。"""
    ref_dir = workspace_root / folder_name / "参考资料"
    return iter_material_files(ref_dir, min_size=50)


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


def scan_and_mount_materials(
    workspace_root: Optional[Path] = None,
    auto_scout_school: bool = True,
    apply: bool = False,
) -> Dict[str, Any]:
    """全量扫描四科参考资料；**仅在 ``apply=True`` 时**写回 ky_config.json 与 AGENTS.md。

    [P1-8 修复·默认只读] 此前该函数无条件写回：0 份资料时跑一次 ``ky mount`` 也会
    把目标高校塞进简章雷达、重写 config 与 AGENTS.md 白名单（实测偷改志愿雷达）。
    现拆为两段：
      - ``apply=False``（默认）：只读盘点，返回将发生的变更预览 ``changes`` /
        ``would_watch`` / ``would_scout``，绝不落盘；
      - ``apply=True``：在只读盘点之上执行写回（且仅在确有变更时写文件）。
    """
    ws = Path(workspace_root) if workspace_root else ROOT
    changes: List[Dict[str, Any]] = []
    details: Dict[str, List[str]] = {}
    total_found = 0

    cfg_file = ws / "ky_config.json"
    cfg = {}
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}

    study_plan = cfg.setdefault("study_plan", {})

    # [P20 修复·下沉] 政治科目名缺失时规范化为「思想政治理论」（而非 SUBJECT_FOLDER_MAP
    # 的 label「政治」）。此前该补齐逻辑在 ky mount 命令层「写回前」执行；现下沉到本函数
    # 的内存规范化：预览与实际写回同源，且 apply=False 时不落盘。
    if not str(study_plan.get("pol_name") or "").strip():
        study_plan["pol_name"] = "思想政治理论"
        changes.append({
            "target": "ky_config.json",
            "field": "study_plan.pol_name",
            "old": None,
            "new": "思想政治理论",
        })

    # 1. 逐科扫描（并记录白名单摘要将发生的变更）
    for key, (folder, config_key, label) in SUBJECT_FOLDER_MAP.items():
        ref_dir = ws / folder / "参考资料"
        found_files = scan_subject_materials(ws, folder)
        details[key] = [relative_label(ref_dir, f) for f in found_files]
        total_found += len(found_files)

        if found_files:
            summary_str = f"[本地资料库已就绪]: " + ", ".join(
                relative_label(ref_dir, f) for f in found_files)
        elif key == "math" and _math_disabled(study_plan):
            # [问题8 修复·不考数学却要求按纲出题] 不考数学时不得生成
            # 「暂未放置实体资料（私教严格按【不考数学】官方考纲出题，严禁虚构书目）」
            # 这类自相矛盾文案（3/5 角色复现，且会污染根 AGENTS.md 白名单段落）。
            # 与向导口径一致，写中性表述「不考数学」。
            summary_str = "不考数学"
        else:
            subj_title = study_plan.get(f"{key}_name", label)
            if key == "pro":
                # [问题5 修复] 专业课占位文案按大纲真实状态分档（收口在
                # syllabus_manager.pro_books_placeholder_text）：骨架/缺失
                # 大纲时不得写「私教严格按官方考纲出题」的虚假承诺。
                try:
                    try:
                        from syllabus_manager import pro_books_placeholder_text
                    except ImportError:
                        from tools.syllabus_manager import pro_books_placeholder_text
                    summary_str = pro_books_placeholder_text(ws, str(subj_title))
                except Exception:
                    summary_str = f"暂未放置实体资料（私教严格按【{subj_title}】官方考纲出题，严禁虚构书目）"
            else:
                if key == "math" and _is_custom_math(study_plan):
                    # [UT4 修复·PLANNER-6] 自命题数学无全国统考大纲，白名单缺省
                    # 文案不得宣称「官方考纲」（UT4 理论物理沙箱 BUG-5），与
                    # study_planner 向导缺省文案同口径。
                    summary_str = "暂未放置实体资料（私教按目标院校自命题大纲出题，严禁虚构书目）"
                else:
                    summary_str = f"暂未放置实体资料（私教严格按【{subj_title}】官方考纲出题，严禁虚构书目）"

        old_val = study_plan.get(config_key)
        if old_val != summary_str:
            changes.append({
                "target": "ky_config.json",
                "field": f"study_plan.{config_key}",
                "old": old_val,
                "new": summary_str,
            })
        study_plan[config_key] = summary_str

    # 2. 目标高校监控与专属情报：先算预览，写操作仅 apply 时执行
    target_school = study_plan.get("school") or cfg.get("target_school") or ""
    # [P1 修复·默认专业按人工智能] 此前空专业默认"人工智能"，scout 全套走 CS 模板。
    # 现保持为空，scout 侧按未知专业走待核验口径。
    target_major = study_plan.get("major") or cfg.get("target_major") or ""
    school_watch_status = ""
    would_watch = False
    would_scout = ""
    scout_report = ""

    if target_school and target_school not in ("未指定", "目标院校"):
        try:
            # [问题3 补修] 显式传 ws：无参实例化只认模块级真实 ROOT，
            # 测试传 tmp 工作区时监控条目会落进真实仓库（实测污染）。
            watcher = resolve_intel_import().AdmissionWatcher(workspace_root=ws)
            _watched_names = [str(w.get("name") or "") for w in watcher.list_watched()]
            if target_school not in _watched_names:
                would_watch = True
                if apply:
                    watcher.add_watch(target_school)
                    school_watch_status = f"已将目标高校【{target_school}】自动纳入简章动态指纹监控雷达"
        except Exception:
            pass

        # 检查是否已为目标高校沉淀专属情报
        dossier_file = ws / "04-专业课" / f"目标院校情报_{target_school}_{target_major}.md"
        if auto_scout_school and not dossier_file.exists():
            would_scout = dossier_file.name
            if apply:
                try:
                    try:
                        from tools.skills import school_scout
                    except ImportError:  # pragma: no cover - 直跑脚本上下文
                        from skills import school_scout
                    scout_res = school_scout.scout_school(
                        school=target_school,
                        major=target_major,
                        include_social=True,
                        save_report=True
                    )
                    if scout_res.get("saved_path"):
                        scout_report = str(scout_res["saved_path"])
                except Exception:
                    pass

    # 3. AGENTS.md 白名单预览（读文本 + 计算新文本），写盘仅在 apply 时
    agents_file = ws / "AGENTS.md"
    agents_original = ""
    agents_updated = ""
    if agents_file.exists():
        try:
            agents_original = agents_file.read_text(encoding="utf-8")
            agents_updated = agents_original
            for key, (folder, config_key, label) in SUBJECT_FOLDER_MAP.items():
                val = study_plan[config_key]
                # 正则替换对应科目行
                pattern = rf"(  - {re.escape(label)}: `)([^`]+)(`)"
                m = re.search(pattern, agents_updated)
                if m and m.group(2) != val:
                    changes.append({
                        "target": "AGENTS.md",
                        "field": f"{label} 白名单",
                        "old": m.group(2),
                        "new": val,
                    })
                    # [P1 修复] 改用 lambda 替换：val 是真实文件名列表，
                    # 直接拼进 repl 字符串时，若文件名含反斜杠或 \g 会被解释为
                    # 正则反向引用而破坏替换结果（甚至抛 re.error）。
                    agents_updated = re.sub(
                        pattern,
                        lambda mm, _v=val: f"{mm.group(1)}{_v}{mm.group(3)}",
                        agents_updated)
        except Exception as e:  # 白名单同步失败不应让整次扫描失败，但必须可见
            print(f"  [!] AGENTS.md 白名单同步失败（其余结果不受影响）: {e}")

    # 4. 写回阶段（仅 apply=True；且仅在确有变更时写文件）
    if apply:
        if any(c["target"] == "ky_config.json" for c in changes):
            try:
                # 原子性由 ky_io.atomic_write_text 统一保证
                atomic_write_text(cfg_file, json.dumps(cfg, ensure_ascii=False, indent=2))
            except Exception as e:
                return {"success": False, "msg": f"写入 ky_config.json 失败: {e}"}

        if agents_updated != agents_original:
            try:
                # [数据保护] 白名单确实会被改写时先落一份带时间戳的备份。
                # 否则学员临时移走资料再跑一次 ky scan，原有白名单会被静默清空
                # 且无从找回（AGENTS.md 不在版本控制内）。
                backup = _backup_agents(agents_file, agents_original)
                # [P1 修复] 原子写回：AGENTS.md 是主协议文件，直接 write_text 若中途
                # 失败会留下被截断的损坏内容，改为临时文件 + replace 原子替换。
                atomic_write_text(agents_file, agents_updated)
                if backup:
                    print(f"  [i] 原 AGENTS.md 已备份至: {backup.name}")
            except Exception as e:  # 白名单同步失败不应让整次扫描失败，但必须可见
                print(f"  [!] AGENTS.md 白名单同步失败（其余结果不受影响）: {e}")

        # [UT4 修复·PLANNER-4] mount --apply 落盘白名单后即时刷新当日「今日任务」：
        # 270bd73 已修「报到」路径的「待导入」外科替换，但 mount 后磁盘文件仍残留
        # 「（⚠️ 专业课大纲与真题待导入）」（UT4 西医沙箱 P2-1 实测：22:33 mount
        # 后 22:17 建档版文件不回写，要等下一次报到才刷新）。此处对四科复用
        # ensure_subject_today_task 的既有刷新逻辑（当日文件存在时只改题源短语行，
        # [x] 勾选与其余内容保留；禁用科目自动 disabled 跳过）。函数级局部 import
        # 防循环依赖；study_plan 已是本函数内存中更新过白名单的最新方案。
        if changes:
            try:
                try:
                    from study_planner import ensure_subject_today_task
                except ImportError:  # pragma: no cover - 包式导入上下文
                    from tools.study_planner import ensure_subject_today_task
                for _sk in SUBJECT_FOLDER_MAP:
                    try:
                        ensure_subject_today_task(study_plan, _sk, workspace_root=ws)
                    except Exception:
                        pass
            except Exception:
                pass

    return {
        "success": True,
        "mode": "apply" if apply else "scan-only",
        "applied": bool(apply),
        "total_files": total_found,
        "details": details,
        "changes": changes,
        "school_watch": school_watch_status,
        "would_watch": would_watch,
        "would_scout": would_scout,
        "scout_report": scout_report,
        "target_school": target_school,
    }


#: [B3 自描述契约] 桥接为 Agent 工具（skill_bridge.build_self_described_specs 消费）
TOOL_SPEC = {
    "name": "mount_materials",
    "description": (
        "盘点学员四科「参考资料/」目录（递归）：返回各科真实资料清单，以及将写入"
        "ky_config.json / AGENTS.md 白名单与研招监控的变更预览。apply=false（默认）"
        "只读盘点；apply=true 才真正写回。学员说「挂载资料」「扫描资料库」「盘点"
        "参考资料」时调用。"),
    "parameters": {
        "type": "object",
        "properties": {
            "auto_scout_school": {
                "type": "boolean",
                "description": "资料就绪时是否自动侦察目标院校（默认 true）",
            },
            "apply": {
                "type": "boolean",
                "description": "是否写回配置与白名单（默认 false：只读预览不落盘）",
            },
        },
    },
    # 动态定级：apply=true 写 ky_config.json / AGENTS.md（SAFE_EDIT）；
    # 缺省/false 为纯只读盘点（READ_ONLY，safe 模式也放行）。
    "level": lambda a: "safe_edit" if (a or {}).get("apply") else "read_only",
}


def execute(args, ctx=None):
    """[B3] 桥接入口：盘点（可选写回）四科参考资料，输出模型可读摘要。"""
    args = args or {}
    ws = (ctx or {}).get("workspace_root")
    apply_ = bool(args.get("apply"))
    auto_scout = bool(args.get("auto_scout_school", True))
    try:
        res = scan_and_mount_materials(workspace_root=ws, auto_scout_school=auto_scout,
                                       apply=apply_)
    except Exception as e:  # noqa: BLE001 - 工具失败必须转可读文案
        return f"Error 资料盘点失败: {e}"
    if not isinstance(res, dict):
        return f"Error 资料盘点返回异常: {type(res).__name__}"
    if not res.get("success"):
        return f"Error 资料盘点失败: {res.get('msg') or '未知错误'}"
    applied = bool(res.get("applied"))
    lines = [f"【参考资料盘点 · {'已写回配置' if applied else '只读预览'}】"
             f"四科共发现 {res.get('total_files', 0)} 份真实资料"]
    details = res.get("details") or {}
    for key, (folder, _cfg, label) in SUBJECT_FOLDER_MAP.items():
        files = details.get(key) or []
        if files:
            shown = ", ".join(str(f) for f in files[:8])
            more = f" …等 {len(files)} 份" if len(files) > 8 else ""
            lines.append(f"- {label}（{folder}）: {shown}{more}")
        else:
            lines.append(f"- {label}（{folder}）: 暂无资料")
    changes = res.get("changes") or []
    if changes:
        head = "已写回变更" if applied else "将发生的变更预览"
        lines.append(f"{head} {len(changes)} 项:")
        for c in changes[:10]:
            if not isinstance(c, dict):
                continue
            lines.append(f"  · {c.get('target', '')} {c.get('field', '')}: "
                         f"{c.get('old')} → {c.get('new')}")
    if res.get("would_watch"):
        lines.append("研招简章监控: " + ", ".join(map(str, res["would_watch"])))
    if res.get("would_scout"):
        lines.append("院校侦察: " + ", ".join(map(str, res["would_scout"])))
    if not applied:
        lines.append("（只读预览未落盘；确认无误后可 apply=true 写回）")
    return "\n".join(lines)
