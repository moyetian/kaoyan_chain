# -*- coding: utf-8 -*-
"""输出预算分档（单一真源）——把「输出多长」变成考生可知情、可选择的参数。

[为什么要有这个模块] R4 真机对照（优化前 d327a8c / 优化后 cb847cb，各 3 次）
测出耗时 ≈ f(输出量)：3979 字→65.9s、767 字→34.4s、237 字→18.3s。而同轮实测
**耗时 CV 20.8% 超标**（优化前后几乎一样：22.8% → 20.8%），说明 R4 修的两条根因
（Agent 路径缺 max_tokens、主循环无熔断）解决的是「无上限地跑下去」，**不降方差**。

真正能降方差的是**控住输出量**，而本轮实验给出了关键反直觉结论：

    软性字数约束**无效**——prompt 写「不少于 1200 字」，模型输出 3979 字（超标 3.3 倍）；
    结构化条数约束**有效**——「每节 N 条、每条 N 句」两档实测字符 CV 4.89% / 9.38%、
    耗时 CV 14.66% / 13.45%（均 ≤15% 达标），且长度可调档（237 ↔ 767 字）。

所以本模块提供**三档预算**：每档同时给出「max_tokens 硬上界」+「骨架软约束」，
缺一不可——只有硬上界会把回答拦腰截断，只有骨架则无兜底。

[为什么硬上界必须分档] 固定 4096 对「查概念」太贵（学生要等 60s+ 拿一大段），
对「模拟考详解」又太紧。分档让**快问快答与深挖细究各自合适**。

[不做什么]
  * 不改任何现有 prompt —— 本模块只提供档位表与取用函数，是否采用由调用点决定；
  * 不在渲染层偷偷截断 —— 触达上界时**显式标注**（见 :func:`truncation_notice`），
    让考生知道「已达本档预算，可切深档」，而不是静默丢内容。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: ``ky_config.json`` 里承载档位的键
CONFIG_KEY = "output_budget"

#: 缺省档位（与 R4 真机验证的「standard」一致：字符 CV 9.38%、耗时 CV 13.45%）
DEFAULT_LEVEL = "standard"

#: 档位表（单一真源）。``max_tokens`` 是**硬上界**，``skeleton`` 是**软约束**。
#: 数值来自 2026-10-06 真机实测：standard 档（5 节 × 2-4 条 / 每条 2-3 句）产出
#: 平均 767 字符、耗时 CV 13.45%；brief 档实测 237 字符、耗时 CV 14.66%。
BUDGET_LEVELS: Dict[str, Dict[str, Any]] = {
    "brief": {
        "label": "速览",
        "max_tokens": 1024,
        "target_chars": 300,
        "hint": "查概念、确认方向用；要展开推导请切到标准档或深入档",
        "skeleton": [
            "1. 概念界定（2 条，每条一行）",
            "2. 关键结论（3 条，每条一行）",
            "3. 例子（2 个，每个 1 句）",
            "4. 下一步（1 句）",
        ],
    },
    "standard": {
        "label": "标准",
        "max_tokens": 2048,
        "target_chars": 800,
        "hint": "日常讲题、错题复盘的默认档",
        "skeleton": [
            "1. 概念界定（2 条，每条 2-3 句）",
            "2. 论证过程（4 条，每条 2-3 句）",
            "3. 具体例子（3 个，每个 3-4 句）",
            "4. 常见误区辨析（3 条，每条 2-3 句）",
            "5. 收束（1 段，3-4 句）",
        ],
    },
    "deep": {
        "label": "深入",
        "max_tokens": 4096,
        "target_chars": 2000,
        "hint": "难点突破、模拟考详解用；耗时最长（真机实测 60s+）",
        "skeleton": [
            "1. 概念界定（3 条，含边界与易混点）",
            "2. 完整推导（4-6 步，每步写清依据）",
            "3. 多角度论证（3 条，每条 3-4 句）",
            "4. 典型例题精讲（2 个，含逐步演算）",
            "5. 变式与陷阱（3 条）",
            "6. 复盘要点（3 条，每条 1-2 句）",
        ],
    },
}

#: 非法档位时的回落提示（写进配置前先校验，避免脏值落盘）
_VALID = tuple(BUDGET_LEVELS.keys())


def available_levels() -> List[str]:
    """可用档位名列表（按快→深）。"""
    return list(BUDGET_LEVELS.keys())


def get_level(config: Optional[Dict[str, Any]]) -> str:
    """读当前档位；缺省或非法一律回落 :data:`DEFAULT_LEVEL`。

    [为什么回落而不是抛错] 档位写错不该让私教不可用——它只影响输出长度，
    不影响能否作答。与 ``max_tokens`` 的既有回落口径保持一致。
    """
    raw = (config or {}).get(CONFIG_KEY)
    if isinstance(raw, str) and raw in BUDGET_LEVELS:
        return raw
    # 也接受 {level: "deep"} 形态（未来配置分层时无需改读法）
    if isinstance(raw, dict):
        lv = raw.get("level")
        if isinstance(lv, str) and lv in BUDGET_LEVELS:
            return lv
    return DEFAULT_LEVEL


def is_valid_level(level: Any) -> bool:
    return isinstance(level, str) and level in BUDGET_LEVELS


def set_level(config: Dict[str, Any], level: str) -> bool:
    """把档位写进 ``config``（原地修改）；非法档位返回 ``False`` 且不写。

    只存档位名、**不复制档位表**进配置——档位表是代码侧单一真源，
    复制会出现「配置里的 max_tokens 与代码不同步」的第二事实源。
    """
    if not is_valid_level(level):
        return False
    config[CONFIG_KEY] = level
    return True


def max_tokens_for(config: Optional[Dict[str, Any]]) -> int:
    """按档位取输出硬上界。

    [优先级] 显式 ``config["max_tokens"]`` > 档位表 > :data:`DEFAULT_LEVEL` 的上界。
    保留显式值优先是为了**向后兼容**：已有用户/测试若在配置里写死 max_tokens，
    行为不变。R4 已修的「不允许缺省成 None」在这里继续成立——本函数**永不返回 None/0**。
    """
    try:
        val = int((config or {}).get("max_tokens"))
        if val > 0:
            return val
    except (TypeError, ValueError):
        pass
    return int(BUDGET_LEVELS[get_level(config)]["max_tokens"])


def skeleton_for(config: Optional[Dict[str, Any]]) -> List[str]:
    """当前档位的骨架条目（软约束，每节一行的列表）。"""
    return list(BUDGET_LEVELS[get_level(config)]["skeleton"])


def skeleton_prompt(config: Optional[Dict[str, Any]], task: str = "") -> str:
    """渲染成可直接嵌入 prompt 的骨架说明。

    [为什么不写「不超过 N 字」] 真机实测：prompt 写「不少于 1200 字」→ 输出 3979 字
    （**超标 3.3 倍**），软性字数约束不可靠；而「每节 N 条、每条 N 句」这种结构化
    条数约束实测能把字符 CV 压到 4.89%~9.38%。故骨架只写**条数与句数**，不写总字数。
    """
    level = get_level(config)
    lines = BUDGET_LEVELS[level]["skeleton"]
    head = f"请按以下固定结构回答{'「' + task + '」' if task else ''}，每节只给要点："
    body = "\n".join(lines)
    tail = (f"\n（当前输出档位：{BUDGET_LEVELS[level]['label']}；"
            f"若需更深入可切换档位后重问）")
    return f"{head}\n{body}{tail}"


def describe(config: Optional[Dict[str, Any]]) -> str:
    """考生可读的档位说明（供 ``ky budget`` 与设置页展示）。

    [为什么要显式提示覆盖关系] 真实工作区里存在**显式** ``max_tokens``（实测用户配置
    为 16384），按「显式优先」它会压过档位。若不提示，考生切了档位却发现输出长度
    没变——会以为功能坏了。故此处直接写明「谁在生效、怎么改」。
    """
    cur = get_level(config)
    explicit = None
    try:
        val = int((config or {}).get("max_tokens"))
        if val > 0:
            explicit = val
    except (TypeError, ValueError):
        pass
    level_cap = int(BUDGET_LEVELS[cur]["max_tokens"])
    effective = explicit or level_cap
    head = f"当前档位：{BUDGET_LEVELS[cur]['label']}（{cur}）"
    if explicit and explicit != level_cap:
        head += (f" · **实际生效上界 {effective} tokens**"
                 f"（配置里的显式 max_tokens={explicit} 优先于档位）")
    else:
        head += f" · 上界 {effective} tokens"
    rows = [head]
    for name in available_levels():
        mark = " ← 当前" if name == cur else ""
        info = BUDGET_LEVELS[name]
        extra = ""
        if name == cur and explicit and explicit != info["max_tokens"]:
            extra = f"（被显式 {explicit} 覆盖）"
        rows.append(f"  {name:9s} {info['label']:4s} 约 {info['target_chars']} 字 / "
                    f"{info['max_tokens']} tokens{mark}{extra}")
    if explicit and explicit != level_cap:
        rows.append(f"注意：显式 max_tokens={explicit} 优先于档位，"
                    f"切档不会改变实际上界；如需按档位控制，"
                    f"请从 ky_config.json 移除该字段（或设为 {level_cap}）。")
    rows.append(f"提示：{BUDGET_LEVELS[cur]['hint']}")
    return "\n".join(rows)


def truncation_notice(finish_reason: Optional[str], config: Optional[Dict[str, Any]] = None) -> str:
    """硬上界触达时的**显式标注**（空串表示未触达）。

    [为什么必须显式] 静默截断会让考生以为「这就是完整答案」——按档位标注后，
    考生知道内容被本档预算截住、可切深档继续。这是「预算可知情」原则的落点。

    判据用上游上报的 ``finish_reason == "length"``（``llm_client`` 已透传），
    **不**用「长度接近上界」的启发式——那会在正常长回答上误报。
    """
    if (finish_reason or "").strip().lower() != "length":
        return ""
    level = get_level(config)
    info = BUDGET_LEVELS[level]
    return (f"\n\n[已达「{info['label']}」档输出预算（{info['max_tokens']} tokens）"
            f"而截断；若需完整内容，可切换到更深的档位后重问。]")


__all__ = [
    "CONFIG_KEY", "DEFAULT_LEVEL", "BUDGET_LEVELS",
    "available_levels", "get_level", "is_valid_level", "set_level",
    "max_tokens_for", "skeleton_for", "skeleton_prompt", "describe",
    "truncation_notice",
]
