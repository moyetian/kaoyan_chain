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


#: 发布用通用科目短名（与 subjects[].name 同源；仅作 subjects 缺失时的兜底）
_GENERIC_SUBJECT_NAMES = {"math": "数学", "eng": "英语", "pol": "政治", "pro": "专业课"}


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
    # 科目全称（如「自命题专业课科目」），
    # 会随 Pages 公开发布。发布前一律泛化为通用短名（与看板卡片所用名一致）。
    generic_names = dict(_GENERIC_SUBJECT_NAMES)
    for s in data.get("subjects", []):
        if s.get("key") and s.get("name"):
            generic_names[s["key"]] = s["name"]
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
        snapshot_payload = {"meta": meta, "data": safe_data}
        print("[OK] 已生成脱敏快照（默认安全模式），可安全提交至公开仓库。")

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(snapshot_payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return True
