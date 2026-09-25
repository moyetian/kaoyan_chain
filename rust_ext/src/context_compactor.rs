use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

// ── 启发式单价（以 1/100 token 为单位）──────────────────────────────────
// 必须与 Python 侧 `tools/agent/tokenizer.py` 的 `_PRICE_*` 完全一致：
// 两处任一漂移，同一向量就会算出不同结果。
// 数值来自 2026-09-24 对本机实网模型的真实 `usage.prompt_tokens` 标定。
const PRICE_HAN: usize = 52;    // 汉字 U+4E00–U+9FFF
const PRICE_PUNCT: usize = 150; // 中文标点/全角/中文引号
const PRICE_ALPHA: usize = 18;  // ASCII 字母
const PRICE_SPACE: usize = 6;   // ASCII 空白
const PRICE_SYM: usize = 95;    // 其余（数字、ASCII 符号、其它 Unicode）
const PRICE_DEN: usize = 100;

#[derive(Default)]
struct CharCounts {
    han: usize,
    punct: usize,
    alpha: usize,
    space: usize,
    sym: usize,
}

/// CJK 统一表意文字（汉字）。**必须与 Python 侧 `is_cjk` 完全一致**。
#[inline]
fn is_cjk(c: char) -> bool {
    ('\u{4e00}'..='\u{9fff}').contains(&c)
}

/// 中文标点/全角字符（含中文引号、破折号、省略号）。
/// **必须与 Python 侧 `is_cjk_punct` 完全一致**。
#[inline]
fn is_cjk_punct(c: char) -> bool {
    ('\u{3000}'..='\u{303f}').contains(&c)
        || ('\u{ff00}'..='\u{ffef}').contains(&c)
        || matches!(
            c,
            '\u{201c}' | '\u{201d}' | '\u{2018}' | '\u{2019}' | '\u{2026}' | '\u{2014}'
                | '\u{2013}' | '\u{00b7}'
        )
}

/// 按五类字符累计计数（分类顺序必须与 Python 侧 `classify_chars` 一致）。
#[inline]
fn classify_chars(s: &str, c: &mut CharCounts) {
    for ch in s.chars() {
        if is_cjk(ch) {
            c.han += 1;
        } else if is_cjk_punct(ch) {
            c.punct += 1;
        } else if matches!(ch, ' ' | '\t' | '\n' | '\r') {
            c.space += 1;
        } else if ch.is_ascii_alphabetic() {
            c.alpha += 1;
        } else {
            c.sym += 1;
        }
    }
}

/// 启发式的**单一公式入口**（纯整数运算）。
/// 先求和再整除一次，保证与 Python 侧 `tokens_from_counts` 逐位相同 ——
/// 刻意不用浮点：浮点乘法在边界值上的截断方向不保证跨语言一致。
#[inline]
fn tokens_from_counts(c: &CharCounts) -> usize {
    (c.han * PRICE_HAN
        + c.punct * PRICE_PUNCT
        + c.alpha * PRICE_ALPHA
        + c.space * PRICE_SPACE
        + c.sym * PRICE_SYM)
        / PRICE_DEN
}

/// 估算消息列表的 Token 量（五类字符启发式，见 Python 侧 tokenizer 模块）。
/// 输入: List[Dict]
///
/// [P0-1 修复·假 tokenizer] 旧实现是 `total_chars * 0.6`，中文被低估 2 倍以上。
#[pyfunction]
pub fn estimate_tokens(_py: Python<'_>, messages: &Bound<'_, PyList>) -> PyResult<usize> {
    let mut counts = CharCounts::default();

    for item in messages.iter() {
        if let Ok(dict) = item.downcast::<PyDict>() {
            if let Ok(Some(content_obj)) = dict.get_item("content") {
                if let Ok(s) = content_obj.extract::<String>() {
                    classify_chars(&s, &mut counts);
                }
            }
            // `tool_calls` 键缺失或值为 None 一律跳过（与 Python 侧
            // `if "tool_calls" in m and m["tool_calls"] is not None` 对齐；
            // 否则 None 会被 to_string() 数成 4 个字符 "None"）。
            if let Ok(Some(tc_obj)) = dict.get_item("tool_calls") {
                if !tc_obj.is_none() {
                    let tc_str = tc_obj.to_string();
                    classify_chars(&tc_str, &mut counts);
                }
            }
        }
    }

    Ok(tokens_from_counts(&counts))
}

/// 上下文压缩 (Context Compaction) 辅助
#[pyfunction]
pub fn compact_messages(
    _py: Python<'_>,
    messages: &Bound<'_, PyList>,
    max_tokens: usize,
    keep_recent: usize,
) -> PyResult<PyObject> {
    let total = messages.len();
    if total <= keep_recent * 2 + 2 {
        return Ok(messages.clone().into());
    }

    let estimated = estimate_tokens(_py, messages)?;
    if estimated <= max_tokens {
        return Ok(messages.clone().into());
    }

    Ok(messages.clone().into())
}
