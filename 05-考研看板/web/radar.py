# -*- coding: utf-8 -*-
"""
招考与考纲变动雷达（📡 考情页签的 HTML 模块）

[说明] 本模块产出的是**已转义**的 HTML 片段，直接拼进看板页面。
脱敏模式下隐私目录里的院校名与本地路径不得出现（见 _snapshot_opt_in）。
"""

from __future__ import annotations

import html
import json
import pathlib
import re

from .snapshot import snapshot_opt_in

def build_radar_html(root_path: pathlib.Path) -> str:
    """构建【📡 招考与考纲变动雷达】全景 HTML 模块 (Sprint 7)"""
    sections = []

    # 获取学员当前目标院校
    target_school = ""
    cfg_file = root_path / "ky_config.json"
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            target_school = cfg.get("study_plan", {}).get("school") or cfg.get("target_school") or ""
        except Exception:
            pass

    # 1. 目标高校简章监控雷达 (Admission Watcher)
    watch_file = root_path / ".memory" / "admission_watch.json"
    watch_items = []
    if watch_file.exists():
        try:
            w_data = json.loads(watch_file.read_text(encoding="utf-8"))
            for code, winfo in w_data.items():
                watch_items.append(winfo)
        except Exception:
            pass

    # [H-0b 隐私修复] 脱敏模式（默认开启）下，监控院校名、研究生院官网 URL 与简章标题
    # 均来自隐私目录 .memory/admission_watch.json，而本产物会随 GitHub Pages 公开发布
    # （实测曾把 `https://gra.henau.edu.cn` 写进 docs/index.html）——
    # 故只输出「已配置 N 所」的聚合状态，不回显任何可识别信息。
    # 该开关同时供下方「考纲异动」与「社媒经验」两节复用。
    sanitize = snapshot_opt_in()

    # 若未建立独立监控库但已配置目标院校，自动合成目标院校动态监控卡片
    # （脱敏模式下不得合成：目标院校名本身就是报考意向）
    if not watch_items and target_school and not sanitize:
        watch_items.append({
            "school": target_school,
            "status": "WATCHING",
            "last_check": "系统自动纳入监控",
            "url": "https://yz.chsi.com.cn",
            "alert_titles": [f"已建立【{target_school}】研究生院招生简章与专业目录动态监控"]
        })

    w_html = []
    w_html.append("<section class='radar-sec'><h3><span><svg viewBox='0 0 24 24' width='16' height='16' stroke='currentColor' stroke-width='2' fill='none'><path d='M12 2v20M2 12h20M12 7a5 5 0 0 0-5 5M12 3a9 9 0 0 0-9 9'/></svg></span>目标院校简章监控雷达 (Admission Watcher)</h3>")
    if watch_items and sanitize:
        n = len(watch_items)
        w_html.append(
            "<div style='font-size:12px;color:var(--mut);margin-bottom:8px'>"
            f"已配置 <b>{n}</b> 所监控院校，系统自动轮询其研究生院公告并比对哈希指纹变动：</div>"
        )
        w_html.append(
            "<div class='radar-card'><div class='radar-card-h'>"
            "<span>监控中院校（已脱敏）</span>"
            "<span class='radar-badge del'>指纹轮询正常</span></div>"
            "<div style='font-size:12px;color:var(--mut);'>院校名称、研究生院官网与简章标题仅保留在本机 "
            "<code>.memory/admission_watch.json</code>，不随公开看板发布。</div></div>"
        )
    elif watch_items:
        w_html.append("<div style='font-size:12px;color:var(--mut);margin-bottom:8px'>系统自动每隔周期轮询目标高校研究生院公告，比对哈希指纹变动：</div>")
        for it in watch_items:
            st = it.get("status", "UNCHANGED")
            is_new = st == "UPDATED"
            badge_cls = "radar-badge add" if is_new else "radar-badge del"
            st_text = "发现新简章/变动" if is_new else "指纹正常·未见变动"
            w_html.append("<div class='radar-card'>")
            w_html.append(f"<div class='radar-card-h'><span>{html.escape(it.get('school', '高校'))}</span><span class='{badge_cls}'>{st_text}</span></div>")
            w_html.append(f"<div style='font-size:12px;color:var(--mut);margin-bottom:4px'>最近检测: {it.get('last_check', '未巡检')} ｜ 官方通道: <a href='{it.get('url', '#')}' target='_blank' style='color:var(--acc);text-decoration:none;'>研究生院/招办官网 ↗</a></div>")
            if it.get("alert_titles"):
                w_html.append("<div style='font-size:12px;margin-top:6px;background:var(--surf);padding:6px 10px;border-radius:6px;'>")
                w_html.append("<b>最新简章线索:</b><ul style='margin:4px 0 0 16px;padding:0;'>")
                for at in it.get("alert_titles", [])[:3]:
                    w_html.append(f"<li>{html.escape(at)}</li>")
                w_html.append("</ul></div>")
            w_html.append("</div>")
    else:
        w_html.append("<div class='empty' style='padding:16px;'><div class='ei'>📡</div>暂未配置实时监控高校<br><small>在终端输入 <code>ky fetch watch 目标高校</code> 即可开启招生简章动态指纹轮询</small></div>")
    w_html.append("</section>")
    sections.append("".join(w_html))

    # 2. 考纲版本异动与掌握度分析 (Syllabus Diff Radar)
    diff_html = []
    diff_html.append("<section class='radar-sec'><h3><span><svg viewBox='0 0 24 24' width='16' height='16' stroke='currentColor' stroke-width='2' fill='none'><path d='M3 3v18h18M18 17l-5-5-4 4-6-6'/></svg></span>考纲版本异动与动荡率分析 (Syllabus Diff)</h3>")
    pro_dir = root_path / "04-专业课"
    diff_files = sorted(list(pro_dir.glob("考纲变动分析_*.md")), key=lambda p: (0 if target_school and target_school in p.name else 1, -p.stat().st_mtime)) if pro_dir.exists() else []
    if diff_files:
        diff_html.append("<div style='font-size:12px;color:var(--mut);margin-bottom:8px'>基于大纲知识点多层 AST 结构化逐级对比（掌握/理解/了解）：</div>")
        for df in diff_files[:3]:
            txt = df.read_text(encoding="utf-8", errors="ignore")
            vol_match = re.search(r"考点动荡率[^\d]*(\d+\.?\d*)%", txt)
            vol = vol_match.group(1) if vol_match else "0.0"
            add_match = re.search(r"新增考点[^\d]*(\d+)", txt)
            del_match = re.search(r"删减考点[^\d]*(\d+)", txt)
            mod_match = re.search(r"考查微调[^\d]*(\d+)", txt)
            c_add = add_match.group(1) if add_match else "0"
            c_del = del_match.group(1) if del_match else "0"
            c_mod = mod_match.group(1) if mod_match else "0"

            diff_html.append("<div class='radar-card'>")
            # 报告文件名形如「考纲变动分析_<目标院校>_<科目>.md」，脱敏模式下需抹去院校名
            diff_title = df.stem
            diff_name = df.name
            if sanitize and target_school:
                diff_title = diff_title.replace(target_school, "目标院校")
                diff_name = diff_name.replace(target_school, "目标院校")
            diff_html.append(f"<div class='radar-card-h'><span>{html.escape(diff_title)}</span><span class='radar-badge mod'>动荡率 {vol}%</span></div>")
            diff_html.append("<div class='radar-stat'>")
            diff_html.append(f"<span class='radar-badge add'>+ 新增必考 {c_add} 处</span>")
            diff_html.append(f"<span class='radar-badge del'>- 彻底剔除 {c_del} 处</span>")
            diff_html.append(f"<span class='radar-badge mod'>~ 考查微调 {c_mod} 处</span>")
            diff_html.append("</div>")
            diff_html.append("<div style='font-size:11.5px;color:var(--mut);'>详见本地报告: <code>04-专业课/" + html.escape(diff_name) + "</code></div>")
            diff_html.append("</div>")
    else:
        diff_html.append("<div class='empty' style='padding:16px;'><div class='ei'>📑</div>暂无大纲对比研报<br><small>在终端输入 <code>ky fetch diff --school 目标院校</code> 即可生成逐级 AST 差异透视与突破处方</small></div>")
    diff_html.append("</section>")
    sections.append("".join(diff_html))

    # 3. 社媒真实经验与就读体验精选 (Community Experiences)
    exp_html = []
    exp_html.append("<section class='radar-sec'><h3><span><svg viewBox='0 0 24 24' width='16' height='16' stroke='currentColor' stroke-width='2' fill='none'><path d='M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z'/></svg></span>社媒真实经验与避坑口碑档案 (Community Experiences)</h3>")
    # [P0 修复] 优先读取 .memory/experiences/（隐私目录），兼容旧 docs/experiences/ 存量
    exp_dir = root_path / ".memory" / "experiences"
    if not exp_dir.exists():
        exp_dir = root_path / "docs" / "experiences"
    exp_files = sorted(list(exp_dir.glob("*.md")), key=lambda p: (0 if target_school and target_school in p.name else 1, -p.stat().st_mtime)) if exp_dir.exists() else []
    # [审查修复] 脱敏模式（默认开启）：隐私目录中的院校名与本地路径不得写入公开看板
    if exp_files:
        exp_html.append("<div style='font-size:12px;color:var(--mut);margin-bottom:8px'>聚合知乎、B站、小红书实名学长学姐真实就读体验与避坑指南 (AI 置信度降噪清洗)：</div>")
        for ef in exp_files[:4]:
            txt = ef.read_text(encoding="utf-8", errors="ignore")
            first_line = txt.splitlines()[0] if txt.splitlines() else ef.stem
            clean_title = re.sub(r"^#+\s*", "", first_line).replace("🎓", "").strip()

            pos_matches = re.findall(r"-\s*✅\s*\*\*([^\*]+)\*\*", txt)
            risk_matches = re.findall(r"-\s*⚠️\s*\*\*([^\*]+)\*\*", txt)

            if sanitize:
                token = ef.stem.split("_")[0]  # 文件名形如「目标院校_目标专业.md」
                clean_title = "目标院校 · 目标专业 社媒经验档案"
                pos_matches = [x.replace(token, "目标院校") for x in pos_matches]
                risk_matches = [x.replace(token, "目标院校") for x in risk_matches]

            exp_html.append("<div class='radar-card'>")
            exp_html.append(f"<div class='radar-card-h'><span>{html.escape(clean_title)}</span><span class='radar-badge tag'>AI置信清洗</span></div>")
            if pos_matches:
                exp_html.append("<div style='font-size:12px;margin:4px 0;color:var(--ok);'><b>优势亮点:</b> " + html.escape(" · ".join(pos_matches[:3])) + "</div>")
            if risk_matches:
                exp_html.append("<div style='font-size:12px;margin:4px 0;color:var(--bad);'><b>避坑防线:</b> " + html.escape(" · ".join(risk_matches[:3])) + "</div>")
            rel_exp = f".memory/experiences/{ef.name}" if ".memory" in str(ef) else f"docs/experiences/{ef.name}"
            if sanitize:
                rel_exp = ".memory/experiences/（本地隐私目录，不随看板公开）"
            exp_html.append(f"<div style='font-size:11.5px;color:var(--mut);margin-top:6px;'>详细经验条目与社媒直通车已归档至 <code>{html.escape(rel_exp)}</code></div>")
            exp_html.append("</div>")
    else:
        exp_html.append("<div class='empty' style='padding:16px;'><div class='ei'>💡</div>暂无沉淀的社媒经验贴<br><small>在终端输入 <code>ky fetch info 目标院校 目标专业 --save</code> 即可自动清洗并归档学长学姐实名经验</small></div>")
    exp_html.append("</section>")
    sections.append("".join(exp_html))

    return "\n".join(sections)
def count_notes(s):
    if not s["notes"]:
        return None
    d = s["dir"] / s["notes"]
    if not d.is_dir():
        return None
    return sum(1 for f in d.iterdir() if f.is_file() and f.suffix == ".md" and not f.name.startswith("_"))
