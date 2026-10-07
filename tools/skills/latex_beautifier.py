# -*- coding: utf-8 -*-
r"""
终端数学公式与 LaTeX 纯文本美化器 (Terminal LaTeX Beautifier)
功能：
  将大模型输出的原始 LaTeX 代码（如 \(f(x)\)、\[\int_{0}^{x}e^{-f(t)}dt\]）
  在终端控制台中自动转换还原为直观易读的 Unicode 数学字符与排版！
"""

import re

GREEK_MAP = {
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ", r"\epsilon": "ε",
    r"\zeta": "ζ", r"\eta": "η", r"\theta": "θ", r"\lambda": "λ", r"\mu": "μ",
    r"\nu": "ν", r"\xi": "ξ", r"\pi": "π", r"\rho": "ρ", r"\sigma": "σ",
    r"\tau": "τ", r"\phi": "φ", r"\chi": "χ", r"\psi": "ψ", r"\omega": "ω",
    r"\Delta": "Δ", r"\Theta": "Θ", r"\Lambda": "Λ", r"\Pi": "Π", r"\Sigma": "Σ",
    r"\Phi": "Φ", r"\Omega": "Ω"
}

MATH_SYM_MAP = {
    r"\infty": "∞", r"\partial": "∂", r"\nabla": "∇", r"\pm": "±", r"\mp": "∓",
    r"\times": "×", r"\cdot": "·", r"\div": "÷", r"\le": "≤", r"\leq": "≤",
    r"\ge": "≥", r"\geq": "≥", r"\ne": "≠", r"\neq": "≠", r"\approx": "≈",
    r"\equiv": "≡", r"\sim": "∽", r"\propto": "∝", r"\in": "∈", r"\notin": "∉",
    r"\subset": "⊂", r"\subseteq": "⊆", r"\cup": "∪", r"\cap": "∩",
    r"\forall": "∀", r"\exists": "∃", r"\to": "→", r"\rightarrow": "→",
    r"\Leftarrow": "⇐", r"\Rightarrow": "⇒", r"\Leftrightarrow": "⇔",
    r"\quad": "  ", r"\qquad": "    ", r"\,": " ", r"\;": " ", r"\!": ""
}

#: [修复·\left 前缀吞噬] 未映射高频命令补键。
#: 背景：``\le`` 是映射键而 ``\left`` 不是 —— ``\left(`` 曾被啃成 ``≤ft(``
#: （GUI 接入美化后考生直接可见）。命令边界保护（见 :func:`_replace_commands`）
#: 已阻止任何吞噬；此处再补常用排版命令与函数名，让 ``\left( x \right)``
#: 这类结构真正还原为可读文本。
LATEX_EXTRA_MAP = {
    r"\left": "", r"\right": "",
    r"\leftarrow": "←", r"\leftrightarrow": "↔",
    r"\uparrow": "↑", r"\downarrow": "↓",
    r"\ldots": "…", r"\cdots": "…", r"\dots": "…",
    r"\ln": "ln", r"\log": "log", r"\sin": "sin", r"\cos": "cos",
    r"\tan": "tan", r"\exp": "exp", r"\max": "max", r"\min": "min",
}

SUP_MAP = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    "n": "ⁿ", "x": "ˣ", "t": "ᵗ"
}

SUB_MAP = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
    "a": "ₐ", "e": "ₑ", "i": "ᵢ", "o": "ₒ", "u": "ᵤ",
    "x": "ₓ", "n": "ₙ", "k": "ₖ", "t": "ₜ"
}

def replace_sub_sup(text):
    """将简单下标和上标转换为 Unicode 上下标字符"""
    def sup_repl(m):
        content = m.group(1) or m.group(2)
        if all(c in SUP_MAP for c in content):
            return "".join(SUP_MAP[c] for c in content)
        return f"^({content})"
    
    text = re.sub(r"\^(?:\{([^}]+)\}|([0-9n+-]))", sup_repl, text)

    def sub_repl(m):
        content = m.group(1) or m.group(2)
        if all(c in SUB_MAP for c in content):
            return "".join(SUB_MAP[c] for c in content)
        return f"_({content})"

    text = re.sub(r"_(?:\{([^}]+)\}|([0-9aeiounkxt+-]))", sub_repl, text)
    return text

def _replace_commands(expr, mapping):
    r"""按 key 长度倒序替换命令；字母结尾的键加命令边界（后随字母则不命中）。

    [缺陷修复·两代前缀吞噬] 共享映射表的替换必须防两类吞噬：
      1) 「映射键 × 映射键」：``\ge`` 会吞 ``\geq`` 的尾巴（``≥q``）——
         按 key 长度倒序替换解决；
      2) 「映射键 × 未映射长命令」：``\le`` 会啃 ``\left`` 的前缀
         （``≤ft(``）——倒序无解，必须加负向前瞻 ``(?![a-zA-Z])``
         保证只命中完整命令。

    非字母结尾的键（``\,`` ``\;`` ``\!``）直接字面替换。替换值经 lambda
    返回，避免 re.sub 把值里的反斜杠当组引用（当前表内无 ``\``，防御未来）。
    """
    for k, v in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
        if k[-1].isalpha():
            expr = re.sub(re.escape(k) + r"(?![a-zA-Z])",
                          lambda _m, _v=v: _v, expr)
        else:
            expr = expr.replace(k, v)
    return expr


def format_math_expr(expr):
    """美化单个数学公式内部的 LaTeX 语法"""
    def int_repl(m):
        lower = m.group(1) or ""
        upper = m.group(2) or ""
        if lower and upper:
            return f"∫[{lower} → {upper}] "
        elif lower:
            return f"∫[{lower}] "
        return "∫ "

    expr = re.sub(r"\\int(?:_\{([^}]+)\}|_([^\s^]))?(?:\^\{([^}]+)\}|\^([^\s_]))?", lambda m: int_repl(
        type('', (), {'group': lambda self, idx: (m.group(1) or m.group(2)) if idx==1 else (m.group(3) or m.group(4))})()
    ), expr)

    expr = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1 / \2)", expr)
    expr = re.sub(r"\\lim_\{([^}]+)\}", r"lim(\1)", expr)
    expr = re.sub(r"\\sqrt\{([^}]+)\}", r"√(\1)", expr)
    expr = re.sub(r"\\sum_\{([^}]+)\}\^\{([^}]+)\}", r"∑[\1 → \2]", expr)

    # 倒序 + 命令边界保护（见 _replace_commands）：两代吞噬缺陷一并修复 ——
    # 长键不被前缀键吞尾（\geq→≥q），未映射长命令不被短键啃前缀（\left→≤ft(）。
    # 三端（CLI/TUI/GUI）共享本函数，同源受益。
    expr = _replace_commands(expr, MATH_SYM_MAP)
    expr = _replace_commands(expr, GREEK_MAP)
    expr = _replace_commands(expr, LATEX_EXTRA_MAP)

    expr = replace_sub_sup(expr)
    expr = re.sub(r"\\text\{([^}]+)\}", r"\1", expr)
    expr = expr.replace("{", "").replace("}", "").replace("\\", "")
    return expr.strip()

def prettify_latex_for_terminal(text):
    r"""
    将包含 LaTeX 标记的全文转换为适合终端阅读的高可读性文本
    同时支持行间公式 \[ ... \] / $$ ... $$ 与行内公式 \( ... \) / $ ... $
    """
    if not text:
        return ""

    def display_repl(m):
        raw = m.group(1)
        formatted = format_math_expr(raw)
        return f"\n    ✨ 【公式】 {formatted}\n"

    res = re.sub(r"\\\[(.*?)\\\]", display_repl, text, flags=re.DOTALL)
    res = re.sub(r"\$\$(.*?)\$\$", display_repl, res, flags=re.DOTALL)

    def inline_repl(m):
        raw = m.group(1)
        if raw.isdigit():
            return f"${raw}$"
        formatted = format_math_expr(raw)
        return f"「{formatted}」"

    res = re.sub(r"\\\((.*?)\\\)", inline_repl, res)
    res = re.sub(r"(?<!\$)\$(?!\$)(.*?)(?<!\$)\$(?!\$)", inline_repl, res)
    return res


def health_check() -> dict:
    """[B4] 结构化健康自检：``{"status": READY/DEGRADED/UNAVAILABLE, "reason": str}``。

    纯本地正则渲染技能：用**真调一次最小用例**验证美化链路没坏（函数在但
    正则写错同样会在这里暴露）。
    """
    try:
        rendered = prettify_latex_for_terminal("样本公式：$x^2+1$")
    except Exception as e:  # noqa: BLE001 - 自检异常必须收敛为可见状态
        return {"status": "UNAVAILABLE",
                "reason": f"公式美化渲染失败（{type(e).__name__}: {e}），终端公式展示将不可用"}
    if not rendered or not str(rendered).strip():
        return {"status": "UNAVAILABLE", "reason": "公式美化渲染产出为空，终端公式展示将不可用"}
    return {"status": "READY", "reason": "LaTeX 终端美化与网页伴侣联动可用（纯本地正则，无外部依赖）"}
