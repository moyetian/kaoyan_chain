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

    for k, v in MATH_SYM_MAP.items():
        expr = expr.replace(k, v)
    for k, v in GREEK_MAP.items():
        expr = expr.replace(k, v)

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
