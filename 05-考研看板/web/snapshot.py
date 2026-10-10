# -*- coding: utf-8 -*-
"""
脱敏与状态快照写出

两条硬要求：
1. **默认脱敏**：`KY_SNAPSHOT_OPT_IN` 默认即为安全模式，未脱敏快照不得入库
   （CI 的 deploy-pages.yml 会断言 `meta.sanitized is True`）。
2. **体积**：脱敏快照会随 Pages 一起发布，冗余字段要剥离
   （如 maps.<subj>.modules 是 chapters 的纯投影，前端从不读取）。
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path


def snapshot_opt_in():
    """Return True only for the publish-safe/sanitized mode."""
    import os
    return os.environ.get("KY_SNAPSHOT_OPT_IN", "1").lower() in ("1", "true", "yes", "on")


#: 发布用通用科目短名（subjects[].name 与 maps[].subject_name 泛化的唯一真源）
#: [R11 修复·科目名泄漏] 补 pro2：双专业课考生的第二门自命题科目名此前不在表中，
#: subjects[].name 会原样把真实自命题科目全称带进公开快照。
_GENERIC_SUBJECT_NAMES = {"math": "数学", "eng": "英语", "pol": "政治",
                          "pro": "专业课", "pro2": "专业课二"}


def sanitize_public_data(data: dict) -> dict:
    """Remove answer/detail text and identifying free text before publishing."""
    safe_memo, safe_weak = [], []
    for key in ("memo", "weak"):
        target = safe_memo if key == "memo" else safe_weak
        for d in data.get(key, []):
            d2 = dict(d)
            d2["cards"] = [{"f": c.get("f", ""), "b": []} for c in d.get("cards", [])]
            target.append(d2)
    safe_metrics = []
    for g in data.get("metrics", []):
        g2 = {k: v for k, v in g.items() if k != "title"}
        g2["items"] = [{k: v for k, v in it.items() if k in ("label", "text", "pct", "count", "target", "dir")} for it in g.get("items", [])]
        safe_metrics.append(g2)
    safe_subjects = [{k: s.get(k) for k in ("key", "name", "icon", "color", "dark", "notes", "ok")} for s in data.get("subjects", [])]
    # 通用科目短名表：maps[].subject_name 取自考纲文件标题，可能是真实自命题
    # 科目全称（如「601 数学分析 801 高等代数」），
    # 会随 Pages 公开发布。发布前一律泛化为通用短名（与看板卡片所用名一致）。
    generic_names = dict(_GENERIC_SUBJECT_NAMES)
    # [R11 修复·科目名泄漏] subjects[].name 此前原样透传：mode_b/mode_c 分支里
    # 它是 ky_config/study_plan 的真实自命题科目名（如「618 示例科目」），
    # 绕过下方 maps 的泛化防线直接进公开快照（45-50 行注释自称「永远使用通用短名」，
    # 实现却没有落实）。按 key 一律泛化，与 maps[].subject_name 同源同口径。
    for s2 in safe_subjects:
        if s2.get("key") in generic_names:
            s2["name"] = generic_names[s2["key"]]
    # 公开快照永远使用通用短名；不能把 ky_config/考纲里的自命题科目全称
    # 重新写回白名单，否则「subjects[].name」会绕过 maps 的泛化防线。
    # [G-3 体积治理] maps.<subj>.modules 是 chapters 的**纯投影**
    # （见 skills/knowledge_map.py: {c["title"]: c["points"] for c in chapters}），
    # 而前端只读 chapters（HTML 模板中的 m.chapters），从不读 modules。
    # 发布产物里再带一份派生副本会让 4 个科目各冗余约 9KB——实测占脱敏快照 40%。
    # 此处剥离该字段（不丢信息：可由同 payload 内的 chapters 完全重建）。
    # syllabus_warning 同属自由文本，会把真实科目全称原样带出，且前端不消费，一并剥离。
    safe_maps = {}
    for sk, m in (data.get("maps") or {}).items():
        if isinstance(m, dict):
            m = {k: v for k, v in m.items() if k not in ("modules", "syllabus_warning")}
            if sk in generic_names:
                m["subject_name"] = generic_names[sk]
        safe_maps[sk] = m
    return {"memo": safe_memo, "weak": safe_weak, "metrics": safe_metrics,
            "subjects": safe_subjects, "plan": data.get("plan", {}),
            "maps": safe_maps, "trend": data.get("trend", [])}
def write_state_snapshot(data: dict, snapshot_path: "Path", parse_warnings=None, sections_status=None):
    """
    把 build 出来的 data 序列化到 state_snapshot.json，作为 Pages 部署时的"真相源"。
    行为受 KY_SNAPSHOT_OPT_IN 控制：
      - KY_SNAPSHOT_OPT_IN=1 → 写出"可发布"快照（脱敏，不含任何可识别字符串）
      - 未设置/=0 → 仍然写出快照但打 WARNING，提示用户不要把未脱敏版本推送到公开仓库
    parse_warnings / sections_status 由 build() 提供，会一并写入 meta 便于诊断。
    """
    import os
    # Default to the publish-safe snapshot. Full personal data requires an explicit opt-out.
    snapshot_mode = os.environ.get("KY_SNAPSHOT_OPT_IN", "1").lower()
    opt_in = snapshot_mode in ("1", "true", "yes", "on")

    snapshot_data = dict(data)  # 浅拷贝

    meta = {
        "snapshot_version": "ky-snapshot/1.1",
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "opt_in": opt_in,
        "subjects_count": len(data.get("subjects", [])),
        "memo_cards": sum(len(d.get("cards", [])) for d in data.get("memo", [])),
        "weak_cards": sum(len(d.get("cards", [])) for d in data.get("weak", [])),
        "metrics_count": len(data.get("metrics", [])),
        "parse_warnings": parse_warnings or [],
        "sections_status": sections_status or [],
    }

    if not opt_in:
        # 公开版会泄露个人学情；打印强提示并把 full 字段置空作为警示
        print("[WARNING] KY_SNAPSHOT_OPT_IN=0：当前生成完整本地学情快照，请勿提交到公开仓库。")
        print("          例如: set KY_SNAPSHOT_OPT_IN=1 && python build.py   (Windows)")
        print("                 export KY_SNAPSHOT_OPT_IN=1 && python build.py   (macOS/Linux)")
        snapshot_payload = {"meta": meta, "data": snapshot_data}
    else:
        safe_data = sanitize_public_data(data)
        meta["sanitized"] = True
        # 诊断元数据同样属于公开面：只保留稳定键，不携带源文件路径、
        # 自定义科目名或自由文本错误消息。
        meta["parse_warnings"] = [
            {k: item.get(k) for k in ("severity", "subject", "kw", "status")
             if k in item}
            for item in (parse_warnings or []) if isinstance(item, dict)
        ]
        meta["sections_status"] = [
            {k: item.get(k) for k in ("subject", "kw", "status", "tab")
             if k in item}
            for item in (sections_status or []) if isinstance(item, dict)
        ]
        snapshot_payload = {"meta": meta, "data": safe_data}
        print("[OK] 已生成脱敏快照（默认安全模式），可安全提交至公开仓库。")

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(snapshot_payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return True
