# -*- coding: utf-8 -*-
"""
考研数学精确验算与符号计算技能 (Math & Symbolic Verifier Skill)
功能：
  1. 高精度极限计算与渐近线校验 (Limits & Asymptotes)
  2. 导数与微分方程严格验算 (Derivatives & ODEs)
  3. 不定积分与定积分计算 (Integrals)
  4. 线性代数：矩阵行列式、特征值、特征向量与二次型正定性判定 (Linear Algebra & Quadratic Forms)
  5. 泰勒公式与麦克劳林级数展开 (Taylor Expansions)
  6. 代数方程组与极值驻点求解 (Equations & Stationary Points)
  7. 级数求和与收敛性分析 (Series Summation)
  8. 杜绝大模型计算幻觉，提供 100% 绝对精确的步骤核对！
"""

import sys
import math
import re
import ast

# 检测是否已安装 SymPy
try:
    import sympy as sp
    from sympy import (
        symbols, limit, diff, integrate, Matrix, series, oo,
        sin, cos, tan, exp, log, sqrt, simplify, latex, Function, Eq, dsolve, solve, summation
    )
    HAS_SYMPY = True
except ImportError:
    HAS_SYMPY = False

def get_status():
    """获取数学引擎当前就绪状态"""
    if HAS_SYMPY:
        return "SymPy 高精度符号计算引擎 (已就绪 · 全功能激活 · 含微分方程/二次型/级数)"
    return "轻量级纯 Python 计算引擎 (建议运行: pip install sympy 获取全部高等数学验算能力)"

def _parse_infinity(token: str):
    """把 'inf' / 'oo' / '-inf' / '-oo' 安全转成 sympy 符号（避免 -inf 被误判为 +oo）。
    当 sympy 不可用或 token 不是 inf/oo 时，回退到 sp.sympify(token)。"""
    if not HAS_SYMPY:
        return token
    t = (token or "").strip().lower().lstrip("(").rstrip(")")
    neg = t.startswith("-")
    t = t.lstrip("-")
    if t in ("inf", "oo", "infinity"):
        return -oo if neg else oo
    return sp.sympify(token)


def run_math_query(query_str):
    """
    智能解析并执行数学命令
    支持格式示例：
      - diff x^3 * sin(x)
      - limit (sin(x) - x) / x^3 as x->0
      - int x * exp(x) dx
      - int x^2 dx from 0 to 1
      - det [[1,2],[3,4]]
      - taylor exp(x) at 0 order 4
      - ode y'' + 4*y = 0 或 ode y' + 2*y = exp(x)
      - quad [[2,1],[1,2]]
      - solve x^2 - 5*x + 6 = 0 或 solve [x^2 + y^2 - 1, x - y]
      - sum 1/n^2 from 1 to oo
    """
    if not HAS_SYMPY:
        return (
            "⚠️ 【提示】当前环境未安装 `sympy` 科学计算库。\n"
            "建议在终端运行：`pip install sympy` 即可一键激活 100% 精确的考研微积分、线代符号验算引擎！\n\n"
            f"收到待验算式: {query_str}"
        )

    q = query_str.strip().lower()
    x, y, z, t, u, v = symbols('x y z t u v')
    n, k = symbols('n k', integer=True)

    try:
        # 1. 常微分方程 (ode / dsolve / 微分方程)
        if q.startswith("ode ") or "微分方程" in q or q.startswith("dsolve "):
            raw_eq = re.sub(r"^(ode|dsolve|微分方程)\s*", "", query_str, flags=re.IGNORECASE).strip()
            y_func = Function('y')
            # 语法替换：y'' -> diff(y(x), x, 2), y' -> diff(y(x), x), y -> y(x)
            s_eq = raw_eq.replace("y''", "diff(y(x), x, 2)").replace("y'", "diff(y(x), x)")
            # 独立单词 y 且后面不带 (
            s_eq = re.sub(r"\by\b(?!\()", "y(x)", s_eq)
            # 处理指数符号 ^ 为 **
            s_eq = s_eq.replace("^", "**")

            local_dict = {'x': x, 'y': y_func, 'diff': diff, 'exp': exp, 'sin': sin, 'cos': cos, 'tan': tan, 'log': log}

            if "=" in s_eq:
                left_str, right_str = s_eq.split("=", 1)
                left_expr = sp.sympify(left_str.strip(), locals=local_dict)
                right_expr = sp.sympify(right_str.strip(), locals=local_dict)
                ode_eq = Eq(left_expr, right_expr)
            else:
                ode_eq = sp.sympify(s_eq.strip(), locals=local_dict)

            sol = dsolve(ode_eq, y_func(x))
            return (
                f"⚙️ 【常微分方程精确通解结果】\n"
                f"待解微分方程: $${latex(ode_eq)}$$\n"
                f"方程通解: $${latex(sol)}$$"
            )

        # 2. 线性代数二次型与正定性判定 (quad / 二次型 / 正定)
        if q.startswith("quad ") or "二次型" in q or "正定" in q:
            m_mat = re.search(r"(\[\[.*?\]\])", query_str)
            if m_mat:
                mat_data = ast.literal_eval(m_mat.group(1))
                mat = Matrix(mat_data)
                n_dim = mat.rows
                # 各阶顺序主子式
                minors = [mat[:i, :i].det() for i in range(1, n_dim + 1)]
                minors_latex = [f"\\Delta_{i} = {latex(m)}" for i, m in enumerate(minors, 1)]
                eigenvals = mat.eigenvals()
                eval_list = list(eigenvals.keys())

                # 判断正定性：分离符号/复数项，遇符号则保守标注"无法判定"
                real_minors = []
                non_real_or_symbolic = False
                for m in minors:
                    if hasattr(m, 'is_real') and m.is_real is True and not m.free_symbols:
                        real_minors.append(float(m))
                    else:
                        non_real_or_symbolic = True
                        break

                if non_real_or_symbolic:
                    is_pos_def = None
                    is_semi_pos_def = None
                else:
                    is_pos_def = all(m > 0 for m in real_minors)
                    real_evs, ev_unknown = [], False
                    for ev in eval_list:
                        if hasattr(ev, 'is_real') and ev.is_real is True and not ev.free_symbols:
                            real_evs.append(float(ev))
                        else:
                            ev_unknown = True
                            break
                    is_semi_pos_def = None if ev_unknown else all(ev >= 0 for ev in real_evs)

                if is_pos_def is None:
                    status_text = "⚠️ 含符号项或非实数项，无法用主元子式法严格判定（请代入具体数值再验）"
                elif is_pos_def:
                    status_text = "正定二次型 (Positive Definite)"
                elif is_semi_pos_def:
                    status_text = "半正定二次型 (Positive Semi-Definite)"
                else:
                    status_text = "不定或非正定二次型"

                return (
                    f"💎 【二次型矩阵与正定性精准判定】\n"
                    f"二次型矩阵 $A$:\n$${latex(mat)}$$\n"
                    f"各阶顺序主子式: ${', '.join(minors_latex)}$\n"
                    f"特征值谱: ${latex(eigenvals)}$\n"
                    f"正定性判定结论: **{status_text}**"
                )

        # 3. 级数求和 (sum / summation / 级数)
        if q.startswith("sum ") or "级数" in q or "求和" in q:
            # 格式: sum expr from a to b
            m_sum = re.search(r"sum\s+(.*?)\s+from\s+([^\s]+)\s+to\s+([^\s]+)", query_str, re.IGNORECASE)
            if m_sum:
                expr_str, a_str, b_str = m_sum.group(1).strip(), m_sum.group(2).strip(), m_sum.group(3).strip()
                expr_str = expr_str.replace("^", "**")
                a = sp.sympify(a_str)
                b = _parse_infinity(b_str)
                expr = sp.sympify(expr_str, locals={'n': n, 'k': k, 'x': x})
                res = summation(expr, (n, a, b))
                return (
                    f"∑ 【级数求和精确计算结果】\n"
                    f"级数表达式: $\\sum_{{n={a_str}}}^{{{b_str}}} {latex(expr)}$\n"
                    f"收敛和: $${latex(res)}$$"
                )

        # 4. 方程求解与驻点极值 (solve / 方程 / 极值)
        if q.startswith("solve ") or "方程" in q:
            eq_str = re.sub(r"^(solve|方程)\s*", "", query_str, flags=re.IGNORECASE).strip()
            eq_str = eq_str.replace("^", "**")
            if eq_str.startswith("[") and eq_str.endswith("]"):
                # 方程组
                eq_list = ast.literal_eval(eq_str)
                parsed_eqs = [sp.sympify(item) for item in eq_list]
                res = solve(parsed_eqs, (x, y))
                return (
                    f"🎯 【代数方程组精确解集】\n"
                    f"方程组: ${latex(parsed_eqs)}$\n"
                    f"解集驻点: $${latex(res)}$$"
                )
            else:
                if "=" in eq_str:
                    l_s, r_s = eq_str.split("=", 1)
                    eq = Eq(sp.sympify(l_s), sp.sympify(r_s))
                else:
                    eq = sp.sympify(eq_str)
                res = solve(eq, x)
                return (
                    f"🎯 【代数方程精确解】\n"
                    f"待解方程: ${latex(eq)}$\n"
                    f"解集 $x$: $${latex(res)}$$"
                )

        # 5. 求导 (diff / 导数)
        if q.startswith("diff ") or "求导" in q or q.startswith("d/dx "):
            expr_str = re.sub(r"^(diff|d/dx|求导)\s*", "", query_str, flags=re.IGNORECASE).strip()
            expr_str = expr_str.replace("^", "**")
            expr = sp.sympify(expr_str)
            res = diff(expr, x)
            return (
                f"📐 【导数精确计算结果】\n"
                f"原函数: $f(x) = {latex(expr)}$\n"
                f"一阶导: $f'(x) = {latex(res)}$\n"
                f"化简式: ${latex(simplify(res))}$"
            )

        # 6. 求极限 (limit / 极限)
        if q.startswith("limit ") or "极限" in q:
            # 格式: limit f(x) as x->0
            m = re.search(r"limit\s+(.*?)\s+(?:as\s+)?x\s*->\s*([^\s]+)", query_str, re.IGNORECASE)
            if m:
                expr_str, dest_str = m.group(1).strip(), m.group(2).strip()
                expr_str = expr_str.replace("^", "**")
                dest = _parse_infinity(dest_str)
                expr = sp.sympify(expr_str)
                res = limit(expr, x, dest)
                return (
                    f"🎯 【极限精确计算结果】\n"
                    f"表达式: $\\lim_{{x \\to {dest_str}}} {latex(expr)}$\n"
                    f"极限值: ${latex(res)}$"
                )

        # 7. 积分 (int / integrate / 积分)
        if q.startswith("int ") or "积分" in q:
            # 定积分判断 from a to b
            m_def = re.search(r"int\s+(.*?)\s+(?:dx\s+)?from\s+([^\s]+)\s+to\s+([^\s]+)", query_str, re.IGNORECASE)
            if m_def:
                expr_str, a_str, b_str = m_def.group(1).strip(), m_def.group(2).strip(), m_def.group(3).strip()
                expr_str = expr_str.replace("^", "**")
                a = sp.sympify(a_str)
                b = sp.sympify(b_str)
                expr = sp.sympify(expr_str)
                res = integrate(expr, (x, a, b))
                return (
                    f"∫ 【定积分精确计算结果】\n"
                    f"定积分式: $\\int_{{{a_str}}}^{{{b_str}}} {latex(expr)} \\,dx$\n"
                    f"计算结果: ${latex(res)}$"
                )
            else:
                expr_str = re.sub(r"^(int|integrate|积分)\s*", "", query_str, flags=re.IGNORECASE)
                expr_str = re.sub(r"\s*dx$", "", expr_str, flags=re.IGNORECASE).strip()
                expr_str = expr_str.replace("^", "**")
                expr = sp.sympify(expr_str)
                res = integrate(expr, x)
                return (
                    f"∫ 【不定积分精确计算结果】\n"
                    f"积分表达式: $\\int {latex(expr)} \\,dx$\n"
                    f"原函数: ${latex(res)} + C$"
                )

        # 8. 行列式与矩阵 (det / matrix / 矩阵)
        if q.startswith("det ") or "行列式" in q or "matrix" in q:
            m_mat = re.search(r"(\[\[.*?\]\])", query_str)
            if m_mat:
                mat_data = ast.literal_eval(m_mat.group(1))
                mat = Matrix(mat_data)
                det_val = mat.det()
                return (
                    f"🔲 【线性代数矩阵运算】\n"
                    f"矩阵 $A$:\n$${latex(mat)}$$\n"
                    f"行列式 $|A| = {latex(det_val)}$\n"
                    f"矩阵的秩: $r(A) = {mat.rank()}$\n"
                    f"特征值: ${latex(mat.eigenvals())}$"
                )

        # 9. 泰勒展开 (taylor / 级数展开)
        if q.startswith("taylor ") or "泰勒" in q:
            expr_str = re.sub(r"^(taylor|泰勒)\s*", "", query_str, flags=re.IGNORECASE).strip()
            order = 4
            m_ord = re.search(r"order\s+(\d+)", expr_str)
            if m_ord:
                order = int(m_ord.group(1))
                expr_str = re.sub(r"order\s+\d+", "", expr_str).strip()
            expr_str = expr_str.replace("^", "**")
            expr = sp.sympify(expr_str)
            res = series(expr, x, 0, order)
            return (
                f"📈 【麦克劳林 / 泰勒级数展开】\n"
                f"原函数: $f(x) = {latex(expr)}$\n"
                f"展开至 $O(x^{order})$:\n"
                f"$${latex(res)}$$"
            )

        # 通用计算化简尝试
        clean_q = query_str.replace("^", "**")
        expr = sp.sympify(clean_q)
        return f"💡 【精确化简结果】\n原式: ${latex(expr)}$\n化简: ${latex(simplify(expr))}$"

    except Exception as e:
        return f"❌ 【计算解析异常】: {e}\n提示：请检查符号语法是否标准，如乘号请用 `*`，幂次请用 `^` 或 `**`。"
