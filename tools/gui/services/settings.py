# -*- coding: utf-8 -*-
"""配置中心的读写契约：嵌套学情字段和 CLI 使用同一数据源。"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

try:
    from ky_io import atomic_write_text
except ImportError:
    from tools.ky_io import atomic_write_text

try:
    from llm_client import fetch_upstream_models, normalize_openai_url
except ImportError:
    from tools.llm_client import fetch_upstream_models, normalize_openai_url


ROOT = Path(__file__).resolve().parent.parent.parent.parent


def read_config(path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("配置文件必须是 JSON 对象，原文件未被修改。")
    return config


def is_unconfigured(workspace_root: Optional[Path | str] = None) -> bool:
    """检测工作区是否处于未初始化或干净状态。

    返回 True 的情形：
    - ky_config.json 不存在或非有效 JSON
    - onboarding_completed 不为 True
    - study_plan 为空、缺失
    - 目标院校为空或等于默认占位符 "目标院校"
    - 报考专业为空或等于默认占位符 "报考专业" / "目标专业 (专业代码-方向)"
    """
    ws = Path(workspace_root) if workspace_root else ROOT
    cfg_path = ws / "ky_config.json"
    if not cfg_path.exists():
        return True
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return True
    if not isinstance(cfg, dict):
        return True
    if not cfg.get("onboarding_completed"):
        return True
    plan = cfg.get("study_plan")
    if not isinstance(plan, dict):
        return True
    school = str(plan.get("school", "")).strip()
    major = str(plan.get("major", "")).strip()
    if not school or school in ("目标院校",):
        return True
    if not major or major in ("报考专业", "目标专业 (专业代码-方向)"):
        return True
    return False


def update_agents_md(workspace_root: Path | str, plan: dict) -> None:
    """使用正则将学情方案高保真同步写入根目录 AGENTS.md。"""
    ws = Path(workspace_root) if workspace_root else ROOT
    agents_path = ws / "AGENTS.md"
    if not agents_path.exists():
        return

    content = agents_path.read_text(encoding="utf-8")

    school = plan.get("school", "目标院校")
    major = plan.get("major", "报考专业")
    exam_date = plan.get("exam_date", "2026-12-19")
    days_left = plan.get("days_left")
    if days_left is None:
        try:
            days_left = (date.fromisoformat(exam_date) - date.today()).days
        except Exception:
            days_left = 90

    stage_name = plan.get("stage_name", "强化题型攻坚阶段")
    style_name = plan.get("style_name", "温和启发·减负鼓励型 (Encouraging Mentor)")

    content = re.sub(r"- \*\*目标院校\*\*：.*", f"- **目标院校**：`{school}`", content)
    content = re.sub(r"- \*\*报考专业\*\*：.*", f"- **报考专业**：`{major}`", content)
    content = re.sub(r"- \*\*初试日期\*\*：.*", f"- **初试日期**：`{exam_date}` (倒计时约 {days_left} 天)", content)

    if "- **当前备考阶段**：" in content:
        content = re.sub(r"- \*\*当前备考阶段\*\*：.*", f"- **当前备考阶段**：`{stage_name}`", content)
    else:
        content = re.sub(r"(- \*\*初试日期\*\*：.*?\n)", r"\1" + f"- **当前备考阶段**：`{stage_name}`\n", content)

    content = re.sub(r"- \*\*当前激活辅导风格\*\*：.*", f"- **当前激活辅导风格**：`{style_name}`", content)

    # 模式判定
    exam_mode = plan.get("exam_mode")
    pro2_name = plan.get("pro2_name", "").strip()
    is_mode_c = exam_mode == "mode_c" or plan.get("pol_disabled") or ("199" in str(plan.get("pro_name", "")))
    is_mode_b = exam_mode == "mode_b" or bool(pro2_name)

    # 科目行
    m_name = plan.get("math_name", "不考数学")
    e_name = plan.get("eng_name", "英语一 (201)")
    p_name = "思想政治理论"
    pro_name = plan.get("pro_name", "专业课")

    math_off = is_mode_b or is_mode_c or str(plan.get("math_key", "")).lower() in {"none", "no", "不考数学"} or m_name == "不考数学"

    table_header = "| 科目                    | 摸底/基准分     | 目标成绩          | 每日基准投入 | 核心提分盘与策略                       |\n| --------------------- | ---------- | ------------- | ------ | ------------------------------ |"

    if is_mode_c:
        table_rows = [
            f"| **科目一：{pro_name}** | {plan.get('pro_baseline', '摸底120')} | **{plan.get('pro_target', '140+ 分')}** | {plan.get('pro_hours', 3.5)} 小时 | 199 管理类综合能力 (初数75分+逻辑60分+写作65分) |",
            f"| **科目二：{e_name}** | {plan.get('eng_baseline', '摸底水平')} | **{plan.get('eng_target', '70+ 分')}** | {plan.get('eng_hours', 2.5)} 小时 | 英语二核心得分盘，阅读主干与功能句型固化 |",
            f"| **合计** | [摸底总分] | **{plan.get('total_target', '215+ 分')}** | {plan.get('total_hours', 6.0)} 小时 | **199联考总分300分，初试不考政治与统考数学** |",
        ]
    elif is_mode_b:
        table_rows = [
            "| **科目一：不考数学** | 不考数学 | **不考数学** | 0.0 小时 | 攻克必考核心题型，严防超纲，规避计算失误，步骤规范化 |",
            f"| **科目二：{e_name}** | {plan.get('eng_baseline', '摸底水平')} | **{plan.get('eng_target', '65+ 分')}** | {plan.get('eng_hours', 2.0)} 小时 | 搭积木拆解长难句，定位阅读选项逻辑，固化作文功能句模板 |",
            f"| **科目三：{p_name}** | {plan.get('pol_baseline', '摸底水平')} | **{plan.get('pol_target', '70+ 分')}** | {plan.get('pol_hours', 1.0)} 小时 | 单选+多选得分盘（38~42分），帽子词秒杀，后期背诵闭环 |",
            f"| **科目四：{pro_name}** | {plan.get('pro_baseline', '摸底水平')} | **{plan.get('pro_target', '120-130 分')}** | {plan.get('pro_hours', 2.0)} 小时 | 权威教材体系+历年真题深度解剖，白名单题源抽题门禁 |",
            f"| **科目五：{pro2_name or '专业课二'}** | {plan.get('pro2_baseline', '摸底水平')} | **{plan.get('pro2_target', '120-130 分')}** | {plan.get('pro2_hours', 2.0)} 小时 | 针对第二门自命题考纲深化推导与背诵闭环 |",
            f"| **合计** | [摸底总分] | **{plan.get('total_target', '375+ 分')}** | {plan.get('total_hours', 7.0)} 小时 | **结构性提分，稳拿基本盘，拒绝偏难怪题** |",
        ]
    else:
        m_row = "| **科目一：不考数学** | 不考数学 | **不考数学** | 0.0 小时 | 攻克必考核心题型，严防超纲，规避计算失误，步骤规范化 |" if math_off else f"| **科目一：{m_name}** | {plan.get('math_baseline', '摸底60')} | **{plan.get('math_target', '110+ 分')}** | {plan.get('math_hours', 2.5)} 小时 | 攻克必考核心题型，严防超纲，规避计算失误，步骤规范化 |"
        table_rows = [
            m_row,
            f"| **科目二：{e_name}** | {plan.get('eng_baseline', '摸底水平')} | **{plan.get('eng_target', '65+ 分')}** | {plan.get('eng_hours', 2.0)} 小时 | 搭积木拆解长难句，定位阅读选项逻辑，固化作文功能句模板 |",
            f"| **科目三：{p_name}** | {plan.get('pol_baseline', '摸底水平')} | **{plan.get('pol_target', '70+ 分')}** | {plan.get('pol_hours', 1.0)} 小时 | 单选+多选得分盘（38~42分），帽子词秒杀，后期背诵闭环 |",
            f"| **科目四：{pro_name}** | {plan.get('pro_baseline', '摸底水平')} | **{plan.get('pro_target', '120-130 分')}** | {plan.get('pro_hours', 2.0)} 小时 | 权威教材体系+历年真题深度解剖，白名单题源抽题门禁 |",
            f"| **合计** | [摸底总分] | **{plan.get('total_target', '370+ 分')}** | {plan.get('total_hours', 6.5)} 小时 | **结构性提分，稳拿基本盘，拒绝偏难怪题** |",
        ]

    new_table_block = table_header + "\n" + "\n".join(table_rows)
    table_pattern = r"\|[ \t]*科目[ \t]*\|[^\n]*\n\|[ \t]*-+[^\n]*\n(?:\|[^\n]*\n)*?\|[ \t]*\*\*合计\*\*.*?\|[^\n]*"
    if re.search(table_pattern, content):
        content = re.sub(table_pattern, new_table_block, content, count=1)
    else:
        content = re.sub(r"\|\s*\*\*科目一.*", table_rows[0], content)
        content = re.sub(r"\|\s*\*\*科目二.*", table_rows[1], content)
        content = re.sub(r"\|\s*\*\*合计.*", table_rows[-1], content)

    # 每日时间预算与作息机制
    math_h_str = "0.0h" if math_off else f"{plan.get('math_hours', 2.5)}h"
    m_books = plan.get('math_books') or ("暂未放置实体资料（私教严格按【不考数学】官方考纲出题，严禁虚构书目）" if math_off else "官方考纲出题")
    e_books = plan.get('eng_books') or f"暂未放置实体资料（私教严格按【{e_name}】官方考纲出题，严禁虚构书目）"
    p_books = plan.get('pol_books') or "暂未放置实体资料（私教严格按【政治】官方考纲出题，严禁虚构书目）"
    pro_books = plan.get('pro_books') or f"暂未放置实体资料（私教严格按【{pro_name}】官方考纲出题，严禁虚构书目）"
    pro2_books = plan.get('pro2_books') or (f"暂未放置实体资料（私教严格按【{pro2_name}】官方考纲出题，严禁虚构书目）" if pro2_name else "")

    m_w = "无" if math_off else plan.get('math_weakness', '计算失误')
    e_w = plan.get('eng_weakness', '待诊断薄弱点')
    p_w = "无" if is_mode_c else plan.get('pol_weakness', '待诊断薄弱点')
    pro_w = plan.get('pro_weakness', '待诊断薄弱点')
    pro2_w = plan.get('pro2_weakness', '核心考点记不牢、论述题缺乏框架')

    if is_mode_c:
        budget_str = f"(199管综: {plan.get('pro_hours', 3.5)}h / 英语: {plan.get('eng_hours', 2.5)}h)"
        mat_section = f"""  - 199管综: `{pro_books}`\n  - 英语: `{e_books}`"""
        weak_section = f"""  - 199管综薄弱点: `{pro_w}`\n  - 英语薄弱点: `{e_w}`"""
    elif is_mode_b:
        budget_str = f"(英语: {plan.get('eng_hours', 2.0)}h / 政治: {plan.get('pol_hours', 1.0)}h / 专业课一: {plan.get('pro_hours', 2.0)}h / 专业课二: {plan.get('pro2_hours', 2.0)}h)"
        mat_section = f"""  - 英语: `{e_books}`\n  - 政治: `{p_books}`\n  - 专业课一: `{pro_books}`\n  - 专业课二: `{pro2_books}`"""
        weak_section = f"""  - 英语薄弱点: `{e_w}`\n  - 政治薄弱点: `{p_w}`\n  - 专业课一薄弱点: `{pro_w}`\n  - 专业课二薄弱点: `{pro2_w}`"""
    else:
        budget_str = f"(数学: {math_h_str} / 英语: {plan.get('eng_hours', 2.0)}h / 政治: {plan.get('pol_hours', 1.0)}h / 专业课: {plan.get('pro_hours', 2.0)}h)"
        mat_section = f"""  - 数学: `{m_books}`\n  - 英语: `{e_books}`\n  - 政治: `{p_books}`\n  - 专业课: `{pro_books}`"""
        weak_section = f"""  - 数学薄弱点: `{m_w}`\n  - 英语薄弱点: `{e_w}`\n  - 政治薄弱点: `{p_w}`\n  - 专业课薄弱点: `{pro_w}`"""

    schedule_section = f"""### 【个性化学情与作息调节机制】 (系统已锁定)
- **每日时间预算**: 每日投入 `{plan.get('total_hours', 6.5)} 小时` {budget_str}
- **每周休整窗口**: `{plan.get('rest_weekly', '每周六晚或周日半天放风，调节身心')}`
- **每月模考复盘**: `{plan.get('rest_monthly', '每月最后周日进行一次全科阶段性复盘测试')}`
- **手头资料白名单 (AI 严守范围)**:
{mat_section}
- **核心薄弱诊断与攻坚防线**:
{weak_section}"""

    if "### 【个性化学情与作息调节机制】" in content:
        content = re.sub(r"### 【个性化学情与作息调节机制】.*?(?=\n## 1\.|\n### 二、)", schedule_section + "\n\n", content, flags=re.DOTALL)
    else:
        content = content.replace("### 二、四种私教辅导风格设定", schedule_section + "\n\n### 二、四种私教辅导风格设定")

    atomic_write_text(agents_path, content)


def save_onboarding_config(
    config_path: Path | str,
    full_config: Dict[str, Any],
    workspace_root: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """原子更新 ky_config.json 并全量联动更新根目录与分科 AGENTS.md 以及考试大纲。"""
    path = Path(config_path)
    ws = Path(workspace_root) if workspace_root else path.parent

    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    merged = dict(existing)

    # 顶层模型与检索引擎配置
    for key in ("api_key", "base_url", "model", "temperature", "search_provider", "search_engine"):
        if key in full_config:
            merged[key] = full_config[key]

    # 学情规划合并
    plan = dict(merged.get("study_plan") or {})
    if "study_plan" in full_config and isinstance(full_config["study_plan"], dict):
        plan.update(full_config["study_plan"])

    # 扁平字段回填 study_plan
    plan_keys = (
        "school", "major", "backup_school", "exam_date", "days_left", "stage_name", "style_name",
        "exam_mode", "math_key", "math_name", "eng_key", "eng_name", "pro_type", "pro_name",
        "pro2_name", "pro2_type", "pol_disabled",
        "math_hours", "eng_hours", "pol_hours", "pro_hours", "pro2_hours", "total_hours",
        "math_baseline", "eng_baseline", "pol_baseline", "pro_baseline", "pro2_baseline",
        "math_target", "eng_target", "pol_target", "pro_target", "pro2_target", "total_target",
        "math_weakness", "eng_weakness", "pol_weakness", "pro_weakness", "pro2_weakness",
        "math_books", "eng_books", "pol_books", "pro_books", "pro2_books",
        "rest_weekly", "rest_monthly",
    )
    for k in plan_keys:
        if k in full_config and full_config[k] is not None:
            plan[k] = full_config[k]

    # 初试倒计时自动补算
    exam_date_str = plan.get("exam_date")
    if exam_date_str:
        try:
            plan["days_left"] = (date.fromisoformat(exam_date_str) - date.today()).days
        except Exception:
            pass

    style = full_config.get("coaching_style") or plan.get("style_name") or merged.get("coaching_style", "温和启发·减负鼓励型 (Encouraging Mentor)")
    plan["style_name"] = style
    merged["study_plan"] = plan
    merged["coaching_style"] = style
    merged["target_school"] = plan.get("school", "")
    merged["target_major"] = plan.get("major", "")
    merged["onboarding_completed"] = True
    merged["relief_mode_active"] = False

    # 1. 原子落盘 ky_config.json
    atomic_write_text(path, json.dumps(merged, ensure_ascii=False, indent=2))

    # 2. 正则更新根目录 AGENTS.md
    update_agents_md(ws, plan)

    # 3. 同步更新各子目录 AGENTS.md
    try:
        try:
            from tools import study_planner
        except ImportError:
            import study_planner
        study_planner.update_subject_agents(plan, workspace_root=ws)
    except Exception:
        pass

    # 4. 同步更新四科考试大纲
    try:
        try:
            from tools import syllabus_manager
        except ImportError:
            import syllabus_manager
        syllabus_manager.apply_syllabus_selection(
            math_key=plan.get("math_key", "none" if plan.get("math_name") == "不考数学" else "math1"),
            eng_key=plan.get("eng_key", "eng1"),
            pro_type=plan.get("pro_type", "custom"),
            pro_name=plan.get("pro_name", "专业课"),
            pro2_name=plan.get("pro2_name", ""),
            school=plan.get("school", "目标院校"),
            major=plan.get("major", "报考专业"),
            auto_write=True,
            workspace_root=ws,
        )
    except Exception:
        pass

    # 5. 自动重置院校监控
    try:
        try:
            from tools.intelligence.watcher import AdmissionWatcher
        except ImportError:
            from intelligence.watcher import AdmissionWatcher
        watcher = AdmissionWatcher()
        target_school = plan.get("school", "").strip()
        if target_school and target_school != "目标院校":
            for item in watcher.list_watched():
                code = item.get("chsi_code")
                if code:
                    watcher.remove_watch(code)
            watcher.add_watch(target_school)
    except Exception:
        pass

    return merged


def save_settings(path, *, api_key, base_url, model, school, major, exam_date, style):
    """读回最新配置后合并字段，保护未知设置和对话期间的其他配置修改。"""
    date.fromisoformat(exam_date)
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("模型服务地址须为有效的 http:// 或 https:// 地址。")
    if not model.strip():
        raise ValueError("请填写模型名称。")
    config = read_config(path)
    plan = dict(config.get("study_plan") or {})
    plan.update(school=school, major=major, exam_date=exam_date, style_name=style)
    try:
        plan["days_left"] = (date.fromisoformat(exam_date) - date.today()).days
    except Exception:
        pass
    config.update(api_key=api_key, base_url=base_url, model=model,
                  study_plan=plan, coaching_style=style)
    atomic_write_text(path, json.dumps(config, ensure_ascii=False, indent=2))
    ws = Path(path).parent
    try:
        update_agents_md(ws, plan)
    except Exception:
        pass
    return config


def test_api_connectivity(
    api_key: str,
    base_url: str,
    model: str,
    search_provider: str = "bing",
    timeout: float = 25.0,
) -> Dict[str, Any]:
    """同时探活 LLM 端点 (chat/completions ping) 与检索引擎连通性，返回测速毫秒数与状态描述。"""
    res = {
        "llm_ok": False,
        "llm_status": "untested",
        "llm_latency_ms": 0,
        "llm_detail": "",
        "search_ok": False,
        "search_status": "untested",
        "search_latency_ms": 0,
        "search_detail": "",
    }

    # 1. 探活 LLM
    if not api_key or not base_url or not model:
        res["llm_ok"] = False
        res["llm_status"] = "missing_config"
        res["llm_detail"] = "缺少 API Key、Base URL 或模型名称"
    else:
        chat_url = normalize_openai_url(base_url, "chat/completions")
        body = json.dumps({
            "model": model.strip(),
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 10,
            "stream": False,
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key.strip()}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Kaoyan-Study-Chain/1.0",
            "Connection": "close",
            "Accept-Encoding": "identity",
        }

        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(chat_url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_bytes = resp.read()
                resp_headers = getattr(resp, "headers", None)
                enc = (getattr(resp_headers, "get", lambda *_: "")("Content-Encoding") or "").lower() if resp_headers else ""
                if enc == "gzip" or raw_bytes.startswith(b"\x1f\x8b"):
                    import gzip
                    try:
                        raw_bytes = gzip.decompress(raw_bytes)
                    except Exception:
                        pass
                latency = max(1, int((time.perf_counter() - t0) * 1000))
                code = getattr(resp, "status", 200)
                res["llm_ok"] = True
                res["llm_status"] = "ok"
                res["llm_latency_ms"] = latency
                res["llm_detail"] = f"连通成功 (HTTP {code}, {latency}ms)"
        except urllib.error.HTTPError as e:
            latency = max(1, int((time.perf_counter() - t0) * 1000))
            res["llm_latency_ms"] = latency
            err_body = ""
            try:
                raw_err = e.read()
                e_headers = getattr(e, "headers", None)
                enc = (getattr(e_headers, "get", lambda *_: "")("Content-Encoding") or "").lower() if e_headers else ""
                if enc == "gzip" or raw_err.startswith(b"\x1f\x8b"):
                    import gzip
                    try:
                        raw_err = gzip.decompress(raw_err)
                    except Exception:
                        pass
                err_body = raw_err.decode("utf-8", errors="ignore")
            except Exception:
                pass
            err_msg = ""
            try:
                err_json = json.loads(err_body)
                if isinstance(err_json, dict) and "error" in err_json:
                    err_val = err_json["error"]
                    err_msg = err_val.get("message", "") if isinstance(err_val, dict) else str(err_val)
                elif isinstance(err_json, dict) and "message" in err_json:
                    err_msg = str(err_json["message"])
            except Exception:
                pass

            # 针对 HTTP 400 无条件使用最简标准载荷重试一次（去除 max_tokens 与 stream 等非标参数）
            if e.code == 400:
                try:
                    retry_body = json.dumps({
                        "model": model.strip(),
                        "messages": [{"role": "user", "content": "ping"}],
                    }).encode("utf-8")
                    retry_req = urllib.request.Request(chat_url, data=retry_body, headers=headers, method="POST")
                    with urllib.request.urlopen(retry_req, timeout=timeout) as retry_resp:
                        r_code = getattr(retry_resp, "status", 200)
                        latency = max(1, int((time.perf_counter() - t0) * 1000))
                        res["llm_ok"] = True
                        res["llm_status"] = "ok"
                        res["llm_latency_ms"] = latency
                        res["llm_detail"] = f"连通成功 (HTTP {r_code}, {latency}ms)"
                        err_msg = ""
                except Exception:
                    pass

            if not res["llm_ok"]:
                if e.code in (401, 403):
                    res["llm_ok"] = False
                    res["llm_status"] = "auth_error"
                    detail = f" (提示: {err_msg})" if err_msg else ""
                    res["llm_detail"] = f"鉴权失败 (HTTP {e.code}): 请检查 API Key{detail}"
                elif e.code == 429:
                    res["llm_ok"] = True
                    res["llm_status"] = "rate_limit"
                    detail = f" (提示: {err_msg})" if err_msg else ""
                    res["llm_detail"] = f"端点连通但触发频控限流 (HTTP 429, {latency}ms){detail}"
                elif e.code == 400:
                    res["llm_ok"] = False
                    res["llm_status"] = "http_error"
                    tip = ""
                    low_msg = (err_msg or "").lower()
                    if "model" in low_msg or "not exist" in low_msg or "invalid" in low_msg:
                        tip = f"（模型 '{model}' 可能不存在或无权访问，建议点击【🔍 探查模型】直接选择）"
                    elif "balance" in low_msg or "quota" in low_msg or "credit" in low_msg:
                        tip = "（账户额度不足或已欠费，请前往服务商控制台充值）"
                    res["llm_detail"] = f"HTTP 错误 400: {err_msg or '请求被服务商拒绝'} {tip}".strip()
                elif e.code == 404:
                    res["llm_ok"] = False
                    res["llm_status"] = "http_error"
                    res["llm_detail"] = f"HTTP 错误 404: 端点未找到，请检查 Base URL 是否正确 (通常以 /v1 结尾)。{err_msg}".strip()
                else:
                    res["llm_ok"] = False
                    res["llm_status"] = "http_error"
                    detail = f": {err_msg}" if err_msg else ""
                    res["llm_detail"] = f"HTTP 错误 {e.code}{detail}"
        except Exception as e:
            latency = max(1, int((time.perf_counter() - t0) * 1000))
            res["llm_latency_ms"] = latency
            err_str = str(e)
            if "timed out" in err_str.lower():
                res["llm_status"] = "timeout"
                res["llm_detail"] = f"请求超时 ({timeout}s)"
            else:
                res["llm_status"] = "network_error"
                res["llm_detail"] = f"连接失败: {err_str}"

    # 2. 探活 Search Provider
    sp_name = (search_provider or "bing").strip().lower()
    t1 = time.perf_counter()
    try:
        try:
            from tools.search.providers import make_provider
        except ImportError:
            from search.providers import make_provider
        provider = make_provider(sp_name)
        if provider is None:
            res["search_ok"] = False
            res["search_status"] = "unsupported"
            res["search_detail"] = f"未找到检索引擎: {sp_name}"
        else:
            items = provider.search("考研", limit=1)
            latency = max(1, int((time.perf_counter() - t1) * 1000))
            res["search_ok"] = True
            res["search_status"] = "ok"
            res["search_latency_ms"] = latency
            count = len(items) if items is not None else 0
            res["search_detail"] = f"检索成功 (返回 {count} 条结果, {latency}ms)"
    except Exception as e:
        latency = max(1, int((time.perf_counter() - t1) * 1000))
        res["search_latency_ms"] = latency
        res["search_ok"] = False
        res["search_status"] = "error"
        res["search_detail"] = f"检索失败: {e}"

    return res


__all__ = [
    "fetch_upstream_models",
    "is_unconfigured",
    "read_config",
    "save_onboarding_config",
    "save_settings",
    "test_api_connectivity",
    "update_agents_md",
]
