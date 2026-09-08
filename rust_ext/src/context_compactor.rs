use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

/// 估算消息列表的 Token 量 (中英混合 1 字符约 0.6 token)
/// 输入: List[Dict]
#[pyfunction]
pub fn estimate_tokens(_py: Python<'_>, messages: &Bound<'_, PyList>) -> PyResult<usize> {
    let mut total_chars: usize = 0;

    for item in messages.iter() {
        if let Ok(dict) = item.downcast::<PyDict>() {
            if let Ok(Some(content_obj)) = dict.get_item("content") {
                if let Ok(s) = content_obj.extract::<String>() {
                    total_chars += s.chars().count();
                }
            }
            if let Ok(Some(tc_obj)) = dict.get_item("tool_calls") {
                let tc_str = tc_obj.to_string();
                total_chars += tc_str.chars().count();
            }
        }
    }

    Ok((total_chars as f64 * 0.6) as usize)
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
