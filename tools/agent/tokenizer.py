# -*- coding: utf-8 -*-
"""
考研学习链 (ky-cli) · Tokenizer 与上下文预算 (Tokenizer & Context Budget)

本模块是「token 计数」与「上下文窗口/预算」的**唯一实现处**（CLI / TUI / GUI /
Agent Loop 共用），别再在别处写死窗口或系数。

职责：
1. 真实 token 计数：``tiktoken`` 可用则精确；否则按语言分频启发式降级；
2. 上下文窗口查表与预算解析：``resolve_context_window`` / ``resolve_budget``，
   落实「未知模型不得假定大窗口」；
3. 常量：``RESERVED_OUTPUT_TOKENS``（输出预留位）、``COMPACT_THRESHOLD``（压缩水位）。

[P0-1 修复·假 tokenizer] 旧实现 ``int(total_chars * 0.6)`` 对所有字符一视同仁，
而中英的实际 token 密度差 3 倍以上。**核实修正**：报告原文称「0.6 系数会把中文
低估 2 倍以上」，但 2026-09-24 对本机实网模型实测，汉字 ≈0.52 token/字 —— 旧
系数对中文其实是轻微**高估**（+15%），真正被严重高估的是英文与代码（0.6 vs
0.18，约 3.3 倍）。净效果是：中文会话压缩过早、英文/代码会话压缩过晚，两头都不准。

现改为**五类字符的分语言启发式**（汉字/中文标点/ASCII 字母/空白/其余），系数由
2026-09-24 对本机实网模型的真实 ``usage.prompt_tokens`` 标定得出（见
``tests/fixtures/tokenizer_golden_vectors.json`` 的 ``_meta``）。报告原文给的
「CJK 1.4 / 其余 0.25」两类系数在本机模型上实测误差 >50%，故未沿用。

[P0-1 修复·硬编码窗口] 旧实现把 ``max_context_tokens`` 硬编码为 48000。现改为
「``context.max_tokens`` 配置项 > 模型查表 > 保守默认」，且**未知模型一律取默认
值，绝不因某个型号号称 1M 就假定大窗口**（高估窗口 = 直接 400 溢出；低估窗口
只是提前压缩，是安全的失败方向）。

启发式一律走**整数运算**（系数以 1/100 token 为单位，先求和再整除一次）而非
浮点：浮点乘法在边界值上的截断方向不保证跨语言一致，整数运算保证 Rust 侧
``ky_rust_ext.estimate_tokens`` 与 Python 侧在同一向量上**逐位相同**。
"""

from typing import Any, Dict, List, NamedTuple, Optional, Tuple

# ── 常量 ────────────────────────────────────────────────────────────────

#: 输出预留位：模型窗口不能全部用于输入，必须给模型的回复留出空间。
RESERVED_OUTPUT_TOKENS = 8_000

#: 压缩水位：可用预算（窗口 − 预留）的 70% 处触发压缩。
COMPACT_THRESHOLD = 0.70

# 启发式系数（**全部以 1/100 token 为单位**，整数运算）：五类字符各自的经验单价。
# 数值来自 2026-09-24 对本机实网模型的真实 `usage.prompt_tokens` 标定（差值法抵消
# 固定开销，10 个样本：7 条黄金向量 + 3 份留出样本），最大误差 8.0%（净 ≥20 token
# 的样本）。详见 tests/fixtures/tokenizer_golden_vectors.json 的 `_meta`。
#
# 为什么不是报告里的「CJK 1.4 / 其余 0.25」两类：实测该模型对中文的合并能力远强于
# 旧式估算（汉字 ≈0.52 而非 1.4），而中文标点几乎不合并（≈1.5）；英文与代码的差异
# 也不在"是否 CJK"上。两类模型在黄金向量上最大误差 >50%，达不到验收线。
_PRICE_HAN = 52       # 汉字 U+4E00–U+9FFF
_PRICE_PUNCT = 150    # 中文标点/全角/中文引号
_PRICE_ALPHA = 18     # ASCII 字母
_PRICE_SPACE = 6      # ASCII 空白
_PRICE_SYM = 95       # 其余（数字、ASCII 符号、其它 Unicode）
_PRICE_DEN = 100

#: CJK 统一表意文字区间（与 Rust 侧 `is_cjk` 必须完全一致）。
_CJK_START, _CJK_END = "\u4e00", "\u9fff"

#: 中文标点/全角字符：CJK 符号与标点、全角形式，外加散落在通用标点区的
#: 中文引号与破折号（`“”‘’…—–·`）—— 它们同样是中文排版的一部分，单价与
#: 全角标点同档，若并入"其余"会被当成英文符号而严重低估。
_PUNCT_RANGES = (("\u3000", "\u303f"), ("\uff00", "\uffef"))
_PUNCT_CHARS = "\u201c\u201d\u2018\u2019\u2026\u2014\u2013\u00b7"

_WS_CHARS = " \t\n\r"

#: 模型窗口查表（token）。**只收录可核实的基线窗口**；给未核实的型号编造数字
#: 与「严禁虚构书目」同罪 —— 宁可走 ``DEFAULT_CONTEXT_WINDOW`` 也不臆造。
#: 命中顺序：先精确 id（最长匹配优先），再按前缀族匹配。
#: 注意：表里**没有**中文模型族（qwen / deepseek / glm / kimi …）—— 本机实网
#: 模型族确实查不到可核实窗口，故一律走默认值（source=``default``）。
CONTEXT_BUDGET: Dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4.1": 1_000_000,
    "claude": 200_000,
}

#: 未知模型的兜底窗口。取 128K 作为 2026 年主流对话模型的保守基线，
#: **不上探 200K/1M**；若实际窗口更小，请在 ``ky_config.json`` 里显式设置
#: ``{"context": {"max_tokens": <真实窗口>}}`` 覆盖。
DEFAULT_CONTEXT_WINDOW = 128_000

# ── 启发式计数 ──────────────────────────────────────────────────────────


def is_cjk(ch: str) -> bool:
    """是否为 CJK 统一表意文字（汉字）。"""
    return _CJK_START <= ch <= _CJK_END


def is_cjk_punct(ch: str) -> bool:
    """是否为中文标点/全角字符（含中文引号、破折号、省略号）。"""
    for lo, hi in _PUNCT_RANGES:
        if lo <= ch <= hi:
            return True
    return ch in _PUNCT_CHARS


def classify_chars(text: str) -> Tuple[int, int, int, int, int]:
    """把文本拆成 ``(han, punct, alpha, space, sym)`` 五类字符计数。

    分类必须与 Rust 侧 ``classify_chars`` 完全一致 —— 任一类归属漂移都会让
    同一向量在两侧算出不同结果。
    """
    han = punct = alpha = space = sym = 0
    for ch in text:
        if _CJK_START <= ch <= _CJK_END:
            han += 1
        elif is_cjk_punct(ch):
            punct += 1
        elif ch in _WS_CHARS:
            space += 1
        elif ch.isascii() and ch.isalpha():
            alpha += 1
        else:
            sym += 1
    return han, punct, alpha, space, sym


def tokens_from_counts(han: int, punct: int, alpha: int, space: int, sym: int) -> int:
    """启发式的**单一公式入口**（纯整数运算）。

    先求和再整除一次（而非逐项取整），这样"分多条消息"与"合并成一条"口径一致，
    也保证 Rust 侧 ``estimate_tokens`` 与本函数逐位相同。
    """
    return (han * _PRICE_HAN + punct * _PRICE_PUNCT + alpha * _PRICE_ALPHA
            + space * _PRICE_SPACE + sym * _PRICE_SYM) // _PRICE_DEN


def heuristic_count(text: str) -> int:
    """单段文本的启发式计数。"""
    if not text:
        return 0
    return tokens_from_counts(*classify_chars(text))


def heuristic_count_messages(messages: List[Dict[str, Any]]) -> int:
    """对整个消息列表做启发式计数（与 Rust ``estimate_tokens`` 语义一致）。

    累计所有 ``content`` 与 ``tool_calls`` 的五类字符后**一次性**套用系数
    （而非逐条取整），这样聚合口径与 Rust 侧完全相同。
    """
    han = punct = alpha = space = sym = 0
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, str):
            h, p, a, s, y = classify_chars(content)
            han += h; punct += p; alpha += a; space += s; sym += y
        if "tool_calls" in m and m["tool_calls"] is not None:
            h, p, a, s, y = classify_chars(str(m["tool_calls"]))
            han += h; punct += p; alpha += a; space += s; sym += y
    return tokens_from_counts(han, punct, alpha, space, sym)


# ── Tokenizer ───────────────────────────────────────────────────────────


class Tokenizer:
    """真实 token 计数器：tiktoken 精确路径 + 启发式降级。

    tiktoken 属可选依赖（``pip install 'kaoyan-study-chain[tokenizer]'``）：
    可用则精确；不可用则降级到分语言启发式，**绝不因缺少可选依赖而报错**。
    """

    def __init__(self, model: str = ""):
        self.model = model or ""
        self._enc = None
        self.mode = "heuristic"
        self._load()

    def _load(self) -> None:
        try:
            import tiktoken  # type: ignore
        except Exception:
            return
        # 先按模型名取编码；未知模型名退回 o200k_base（GPT-4o/4.1 世代的编码）。
        for cand in (self.model, "o200k_base"):
            if not cand:
                continue
            try:
                if cand == "o200k_base":
                    self._enc = tiktoken.get_encoding(cand)
                else:
                    self._enc = tiktoken.encoding_for_model(cand)
                self.mode = "tiktoken"
                return
            except Exception:
                continue

    @property
    def has_encoder(self) -> bool:
        return self._enc is not None

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is not None:
            try:
                return len(self._enc.encode(text))
            except Exception:
                pass
        return heuristic_count(text)

    def count_messages(self, messages: List[Dict[str, Any]]) -> int:
        """消息列表计数：有编码器逐条精确计数，否则整体走启发式。"""
        if self._enc is not None:
            total = 0
            for m in messages or []:
                if not isinstance(m, dict):
                    continue
                content = m.get("content")
                if isinstance(content, str):
                    total += self.count(content)
                if "tool_calls" in m and m["tool_calls"] is not None:
                    total += self.count(str(m["tool_calls"]))
            return total
        return heuristic_count_messages(messages)


# ── 上下文窗口与预算 ────────────────────────────────────────────────────


class Budget(NamedTuple):
    """上下文预算（token）。``budget = window − reserved``，``watermark`` 为触发线。"""

    window: int
    reserved: int
    budget: int
    watermark: int
    source: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "window": self.window,
            "reserved": self.reserved,
            "budget": self.budget,
            "watermark": self.watermark,
            "source": self.source,
        }


def lookup_context_window(model: str) -> Tuple[int, str]:
    """按模型名查窗口，返回 ``(window, source)``；未命中返回默认值。

    ``source`` 形如 ``table:gpt-4o`` / ``table:claude`` / ``default``，
    便于上层把「正在假定一个窗口」这件事暴露给用户。
    """
    name = (model or "").strip().lower()
    if name:
        # 1) 精确 id（最长匹配优先，避免 gpt-4o 被 gpt-4 抢走）
        for key in sorted(CONTEXT_BUDGET, key=len, reverse=True):
            if name == key.lower():
                return CONTEXT_BUDGET[key], f"table:{key}"
        # 2) 前缀族
        for key in sorted(CONTEXT_BUDGET, key=len, reverse=True):
            if name.startswith(key.lower()):
                return CONTEXT_BUDGET[key], f"table:{key}"
    return DEFAULT_CONTEXT_WINDOW, "default"


def resolve_context_window(
    model: str = "", config: Optional[Dict[str, Any]] = None
) -> Tuple[int, str]:
    """解析上下文窗口：``config['context']['max_tokens']`` > 模型查表 > 默认。

    配置项非法（非正整数 / 布尔 / 字符串乱码）时静默回落到查表，不抛异常 ——
    上下文预算出错不该让整个会话起不来。
    """
    cfg = config if isinstance(config, dict) else {}
    ctx = cfg.get("context")
    if isinstance(ctx, dict):
        raw = ctx.get("max_tokens")
        if raw is not None and not isinstance(raw, bool):
            val = 0
            if isinstance(raw, int):
                val = raw
            elif isinstance(raw, float) and raw.is_integer():
                val = int(raw)          # JSON 里写成 128000.0 是常见的，按整数收
            elif isinstance(raw, str):
                try:
                    val = int(raw.strip())
                except ValueError:
                    val = 0
            if val > 0:
                return val, "config"
    return lookup_context_window(model)


def make_budget(window: int, source: str = "explicit") -> Budget:
    """由窗口算出预算与压缩水位。``budget`` 与 ``watermark`` 下限为 0。"""
    try:
        win = int(window)
    except (TypeError, ValueError):
        win = DEFAULT_CONTEXT_WINDOW
        source = "default"
    if win <= 0:
        win = DEFAULT_CONTEXT_WINDOW
        source = "default"
    budget = max(0, win - RESERVED_OUTPUT_TOKENS)
    watermark = int(budget * COMPACT_THRESHOLD)
    return Budget(window=win, reserved=RESERVED_OUTPUT_TOKENS,
                  budget=budget, watermark=watermark, source=source)


def resolve_budget(
    model: str = "", config: Optional[Dict[str, Any]] = None
) -> Budget:
    """一步到位解析预算（供 CLI / GUI / AgentRunner 直接调用）。"""
    window, source = resolve_context_window(model, config)
    return make_budget(window, source)
