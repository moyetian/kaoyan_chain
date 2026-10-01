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


def _safe_href(url) -> str:
    """外部 URL 白名单：只放行 http(s)，其余（``javascript:`` / ``data:`` / 空）回退为 ``#``。

    ``.memory/admission_watch.json`` 由网络巡检写入，属于外部内容；未加白名单时
    ``href='javascript:...'`` 会成为可点击的 XSS 载体（实测可注入）。
    """
    u = str(url or "").strip()
    return u if re.match(r"^https?://", u, re.I) else "#"


# ── 社媒经验档案的身份归属判定 ──────────────────────────────────────────────
# [修复·社媒区渲染历史残留卡片] 四位/五角色实测：看板社媒区把
# ``.memory/experiences/*.md`` 全部文件原样渲染（最多 4 张），于是换了身份的
# 考生会在自己看板里看到别人（甚至是「目标院校_报考专业」占位档案）的院校名，
# 既困惑又在发布模式下明文泄漏报考意向。此前只有排序偏好（匹配的排前面），
# 没有「属不属于当前考生」的过滤，故此处补身份过滤。
# 占位档案（未替换的模板占位符）与不匹配当前身份的档案一律剔除，
# 过滤后为空则回落既有空态分支。
_PLACEHOLDER_TOKENS = ("目标院校", "报考专业", "目标专业", "通用院校", "未指定", "待填写")


def _norm_id(s) -> str:
    """身份串归一化：去空白与常见括号标点，避免「040200 心理学」/「040200心理学」判为不同。"""
    return re.sub(r"[\s（）()【】\[\]〔〕·・,，、:：`'\"']", "", str(s or ""))


def _is_placeholder(*parts) -> bool:
    """任一身份字段仍是未替换的模板占位符（如「目标院校」「报考专业」）即为占位档案。"""
    for p in parts:
        s = str(p or "")
        if not s:
            continue
        for tok in _PLACEHOLDER_TOKENS:
            if tok in s:
                return True
    return False


def _exp_identity(path: pathlib.Path, txt: str):
    """从经验档案里取 ``(院校, 专业)``，优先正文「目标高校 / 学科专业」字段，回落文件名。

    文件名形如「<院校>_<专业>.md」；追加写入的档案可能只有「<院校>.md」且无正文字段。
    """
    school = ""
    major = ""
    m = re.search(r"目标高校[^\n]*?`([^`\n]+)`", txt)
    if m:
        school = m.group(1).strip()
    m = re.search(r"学科专业[^\n]*?`([^`\n]+)`", txt)
    if m:
        major = m.group(1).strip()
    stem = path.stem
    if "_" in stem:
        f_school, _, f_major = stem.partition("_")
        school = school or f_school.strip()
        major = major or f_major.strip()
    else:
        school = school or stem.strip()
    return school, major


def _same_id(a: str, b: str) -> bool:
    """双向包含匹配：兼容「北大 / 北京大学」「心理学 / 040200 心理学」这类简写。"""
    if not a or not b:
        return False
    return a in b or b in a


def _exp_matches_identity(path: pathlib.Path, target_school: str, target_major: str) -> bool:
    """该经验档案是否属于当前考生身份（院校必须一致；专业已知且冲突则剔除）。"""
    if not target_school:
        # 未配置身份时无法判定归属，宁可不渲染，避免他人院校名串进看板
        return False
    if _is_placeholder(path.stem):
        return False
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        txt = ""
    f_school, f_major = _exp_identity(path, txt)
    if _is_placeholder(f_school, f_major):
        return False
    if not _same_id(_norm_id(target_school), _norm_id(f_school)):
        return False
    nm, fm = _norm_id(target_major), _norm_id(f_major)
    if nm and fm and not _same_id(nm, fm):
        return False
    return True


def _school_watch_state(it: dict) -> str:
    """监控院校卡片的三态判定：UPDATED / UNCHANGED / PENDING_BASELINE。

    [P2 修复·基线未建立假正常] 无 updates 时旧实现一律 UNCHANGED（绿色
    「指纹正常」）——但基线未建立（首次抓取失败 / last_hash 为空）时系统
    根本没有可比对的指纹，绿色「正常」是假信号（三沙箱实测：TLS 抓取失败后
    雷达仍显示绿色正常）。判定口径供本地卡片与脱敏聚合徽章共用，避免
    「同一状态两处实现、只修一处」。

    外部巡检 JSON 自带的 ``status`` 字段优先（兼容旧形状）；无 status 时
    按 ``updates`` / ``baseline_complete`` + ``last_hash`` 推断。
    """
    st = it.get("status")
    if st:
        return str(st)
    if it.get("updates"):
        return "UPDATED"
    _baseline_ok = bool(it.get("baseline_complete")) and bool(it.get("last_hash"))
    return "UNCHANGED" if _baseline_ok else "PENDING_BASELINE"


def build_radar_html(root_path: pathlib.Path) -> str:
    """构建【📡 招考与考纲变动雷达】全景 HTML 模块 (Sprint 7)"""
    sections = []

    # 获取学员当前目标院校
    target_school = ""
    target_major = ""
    cfg_file = root_path / "ky_config.json"
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            target_school = cfg.get("study_plan", {}).get("school") or cfg.get("target_school") or ""
            target_major = cfg.get("study_plan", {}).get("major") or ""
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
    # （实测曾把 `https://gra.example.edu.cn` 写进 docs/index.html）——
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
        # [P2 同族修复·聚合徽章假正常] 脱敏模式（手机端发布的默认形态）下
        # 旧实现无条件显示绿色「指纹轮询正常」；基线未建立时同样是假信号。
        # 按与本地卡片同源的 _school_watch_state 聚合：有变动 > 基线未建立 > 正常。
        _states = [_school_watch_state(it) for it in watch_items]
        if "UPDATED" in _states:
            _agg_cls, _agg_text = "radar-badge add", "发现新简章/变动"
        elif "PENDING_BASELINE" in _states:
            _agg_cls, _agg_text = "radar-badge mod", "基线未建立（官网抓取失败）"
        else:
            _agg_cls, _agg_text = "radar-badge del", "指纹轮询正常"
        w_html.append(
            "<div style='font-size:12px;color:var(--mut);margin-bottom:8px'>"
            f"已配置 <b>{n}</b> 所监控院校，系统自动轮询其研究生院公告并比对哈希指纹变动：</div>"
        )
        w_html.append(
            "<div class='radar-card'><div class='radar-card-h'>"
            "<span>监控中院校（已脱敏）</span>"
            f"<span class='{_agg_cls}'>{_agg_text}</span></div>"
            "<div style='font-size:12px;color:var(--mut);'>院校名称、研究生院官网与简章标题仅保留在本机 "
            "<code>.memory/admission_watch.json</code>，不随公开看板发布。</div></div>"
        )
    elif watch_items:
        w_html.append("<div style='font-size:12px;color:var(--mut);margin-bottom:8px'>系统自动每隔周期轮询目标高校研究生院公告，比对哈希指纹变动：</div>")
        for it in watch_items:
            # [问题4 同族修复·键不匹配] 落盘记录（watcher._save 写入）的字段是
            # ``name`` / ``updates`` / ``recent_titles``；此前这里读
            # ``school`` / ``status`` / ``alert_titles``，全部回落默认值 ——
            # 本地完整模式下卡片长期显示「高校 · 指纹正常·未见变动」占位，
            # 考生看不到任何有效信息（实测）。现按落盘结构取值，并保留对
            # ``school``/``status``/``alert_titles`` 形状的兼容（外部巡检 JSON）。
            _updates = it.get("updates") or []
            _last_update = _updates[-1] if _updates else {}
            # [P2 修复·基线未建立假正常] 无 updates 时旧实现一律 UNCHANGED
            # 「指纹正常·未见变动」——但基线未建立（首次抓取失败 / last_hash
            # 为空）时系统根本没有可比对的指纹，绿色"正常"是假信号（三沙箱
            # 实测：TLS 抓取失败后雷达仍显示绿色正常）。现区分三态：变动 /
            # 正常 / 基线未建立（橙色徽章如实提示）。判定与脱敏聚合徽章共用
            # _school_watch_state，避免两处实现漂移。
            st = _school_watch_state(it)
            is_new = st == "UPDATED"
            is_pending = st == "PENDING_BASELINE"
            if is_new:
                badge_cls, st_text = "radar-badge add", "发现新简章/变动"
            elif is_pending:
                badge_cls, st_text = "radar-badge mod", "基线未建立（官网抓取失败）"
            else:
                badge_cls, st_text = "radar-badge del", "指纹正常·未见变动"
            w_html.append("<div class='radar-card'>")
            # [P2-6b 修复·显式 null] 这些字段来自外部巡检 JSON，默认值只对「缺键」
            # 生效，对显式 `"school": null` 无效 —— `html.escape(None)` 直接抛
            # AttributeError，而 build.py 调用本函数时无 try 包裹，整个看板构建失败。
            # 故一律先 `str(it.get(k) or 默认值)` 再转义。
            _card_name = it.get('school') or it.get('name') or '高校'
            w_html.append(f"<div class='radar-card-h'><span>{html.escape(str(_card_name))}</span><span class='{badge_cls}'>{st_text}</span></div>")
            # [P2-6 修复·属性注入] last_check / url 均来自外部巡检 JSON，必须转义；
            # url 另加协议白名单（只放行 http(s)），否则 'javascript:' 与单引号闭合可注入。
            w_html.append(
                "<div style='font-size:12px;color:var(--mut);margin-bottom:4px'>最近检测: "
                f"{html.escape(str(it.get('last_check') or '未巡检'), quote=True)} ｜ 官方通道: "
                f"<a href='{html.escape(_safe_href(it.get('url')), quote=True)}' target='_blank' "
                "rel='noopener noreferrer' style='color:var(--acc);text-decoration:none;'>研究生院/招办官网 ↗</a></div>"
            )
            # 「简章线索」只认真实巡检发现的 alert_titles（updates 历史）；
            # 基线建立初期没有 updates 时回落展示 recent_titles，但必须如实
            # 标注为「标题样本」——它们可能只是官网导航栏目，不是新简章。
            _real_alerts = it.get("alert_titles") or _last_update.get("alert_titles") or []
            _sample_titles = [] if _real_alerts else (it.get("recent_titles") or [])
            _shown_titles = _real_alerts or _sample_titles
            if _shown_titles:
                _titles_label = "最新简章线索" if _real_alerts else "最近页面标题样本"
                w_html.append("<div style='font-size:12px;margin-top:6px;background:var(--surf);padding:6px 10px;border-radius:6px;'>")
                w_html.append(f"<b>{_titles_label}:</b><ul style='margin:4px 0 0 16px;padding:0;'>")
                for at in (_shown_titles or [])[:3]:
                    # 列表项同样可能含显式 null，先转成 str 再转义（否则同样 AttributeError）
                    w_html.append(f"<li>{html.escape(str(at or ''))}</li>")
                w_html.append("</ul></div>")
            w_html.append("</div>")
    else:
        w_html.append("<div class='empty' style='padding:16px;'><div class='ei'><svg class=\"icn\" width=\"24\" height=\"24\" aria-hidden=\"true\" focusable=\"false\"><use href=\"#i-radar\"/></svg></div>暂未配置实时监控高校<br><small>在终端输入 <code>ky fetch watch 目标高校</code> 即可开启招生简章动态指纹轮询</small></div>")
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
        diff_html.append("<div class='empty' style='padding:16px;'><div class='ei'><svg class=\"icn\" width=\"24\" height=\"24\" aria-hidden=\"true\" focusable=\"false\"><use href=\"#i-file\"/></svg></div>暂无大纲对比研报<br><small>在终端输入 <code>ky fetch diff --school 目标院校</code> 即可生成逐级 AST 差异透视与突破处方</small></div>")
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
    # [修复·社媒区渲染历史残留卡片] 只保留属于当前考生身份（院校一致、专业不冲突）
    # 且非占位模板的档案；历史身份与「目标院校_报考专业」占位卡片一律剔除，
    # 过滤后为空则走下方既有空态分支。
    exp_files = [p for p in exp_files if _exp_matches_identity(p, target_school, target_major)]
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
        exp_html.append("<div class='empty' style='padding:16px;'><div class='ei'><svg class=\"icn\" width=\"24\" height=\"24\" aria-hidden=\"true\" focusable=\"false\"><use href=\"#i-chat\"/></svg></div>暂无沉淀的社媒经验贴<br><small>在终端输入 <code>ky fetch info 目标院校 目标专业 --save</code> 即可自动清洗并归档学长学姐实名经验</small></div>")
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
