use pyo3::prelude::*;
use pyo3::types::PyDict;
use regex::Regex;

/// 从页面提取网页标题
#[pyfunction]
pub fn extract_title(html_text: &str) -> String {
    let re = Regex::new(r"(?is)<title[^>]*>(.*?)</title>").unwrap();
    if let Some(cap) = re.captures(html_text) {
        let raw = cap.get(1).map(|m| m.as_str().trim()).unwrap_or("");
        let suffix_re = Regex::new(r"[-_]\s*.*?(?:研究生院|招生网|大学官网)$").unwrap();
        let cleaned = suffix_re.replace(raw, "").trim().to_string();
        if !cleaned.is_empty() {
            return cleaned;
        }
    }
    "高校研招官方通知".to_string()
}

/// 提取 HTML 中的 PDF 附件链接
#[pyfunction]
pub fn extract_pdf_links(
    py: Python<'_>,
    html_text: &str,
    base_url: &str,
) -> PyResult<Vec<Py<PyDict>>> {
    let mut results = Vec::new();
    let re = Regex::new(r#"(?is)<a[^>]+href=["']([^"']+\.pdf)["'][^>]*>(.*?)</a>"#).unwrap();
    let tag_re = Regex::new(r"<[^>]+>").unwrap();

    let base_trimmed = base_url.trim_end_matches('/');

    for cap in re.captures_iter(html_text).take(5) {
        let link = cap.get(1).map(|m| m.as_str().trim()).unwrap_or("");
        let raw_text = cap.get(2).map(|m| m.as_str().trim()).unwrap_or("");
        let clean_text = tag_re.replace_all(raw_text, "").trim().to_string();
        let name = if clean_text.is_empty() {
            "招生专业目录/自命题大纲 PDF".to_string()
        } else {
            clean_text
        };

        let full_url = if link.starts_with("http") {
            link.to_string()
        } else if link.starts_with('/') {
            format!("{}{}", base_trimmed, link)
        } else {
            format!("{}/{}", base_trimmed, link)
        };

        let dict = PyDict::new_bound(py);
        dict.set_item("name", name)?;
        dict.set_item("url", full_url)?;
        results.push(dict.unbind());
    }

    Ok(results)
}

/// 提取初试统考与自命题科目
///
/// [正确性修复] 旧实现用 7 条独立正则分别匹配“固定统考科目”与“任意三位代码科目”，
/// 其中最后一条 `(\(\d{3}\)[\u4e00-\u9fa5]+)` 会把 `(204)英语(二)` 截断成
/// `(204)英语`；该错误条目同样占用配额，于是**真正的自命题科目**
/// （如 `(814)通信原理`）被挤出结果 —— 对自命题考生影响严重。
///
/// 现改为「按三位代码归并 + 同代码取最长名称 + 按首次出现顺序输出」，
/// 与 Python 侧 `DocumentExtractor._extract_subjects` 保持完全一致。
///
/// 注：Rust `regex` crate 不支持环视(lookaround)，故用匹配位置手工校验代码前后
/// 字符，排除 `2026年`、`085400` 这类更长数字串的误命中。
#[pyfunction]
pub fn extract_subjects(html_text: &str) -> Vec<String> {
    use std::collections::HashMap;

    let coded = Regex::new(
        r"[（(]?(\d{3})[）)]?\s*([\u{4e00}-\u{9fa5}]{2,12}(?:[（(][^）)]{1,6}[）)])?)",
    )
    .unwrap();
    let plain = Regex::new(
        r"(思想政治理论|英语[一二]|数学[一二三]|计算机学科专业基础|管理类综合能力|经济类综合能力)",
    )
    .unwrap();

    let bytes = html_text.as_bytes();
    let mut order: Vec<String> = Vec::new();
    let mut best: HashMap<String, String> = HashMap::new();

    for cap in coded.captures_iter(html_text) {
        let (code_m, name_m) = match (cap.get(1), cap.get(2)) {
            (Some(a), Some(b)) => (a, b),
            _ => continue,
        };
        // 代码前后紧邻数字 -> 属于更长数字串（2026 年 / 085400），丢弃
        if code_m.start() > 0 && bytes[code_m.start() - 1].is_ascii_digit() {
            continue;
        }
        if code_m.end() < bytes.len() && bytes[code_m.end()].is_ascii_digit() {
            continue;
        }
        let code = code_m.as_str().to_string();
        let name = name_m.as_str().to_string();
        if code.is_empty() || name.is_empty() {
            continue;
        }
        match best.get(&code) {
            None => {
                order.push(code.clone());
                best.insert(code, name);
            }
            // 同一代码出现多次时保留更完整的名称（更长者）
            Some(prev) if name.chars().count() > prev.chars().count() => {
                best.insert(code, name);
            }
            _ => {}
        }
    }

    let mut subjects: Vec<String> = Vec::new();
    for code in order {
        if let Some(name) = best.get(&code) {
            let item = format!("({}){}", code, name);
            if !subjects.contains(&item) {
                subjects.push(item);
            }
            if subjects.len() >= 4 {
                return subjects;
            }
        }
    }

    // 未带代码的裸科目名（如 “英语二”）作为补充
    for cap in plain.captures_iter(html_text) {
        if let Some(m) = cap.get(1) {
            let item = m.as_str().to_string();
            if !subjects.contains(&item) {
                subjects.push(item);
            }
            if subjects.len() >= 4 {
                break;
            }
        }
    }

    subjects
}

/// 清洗 HTML 为纯文本
#[pyfunction]
pub fn clean_html_to_text(html_text: &str) -> String {
    let script_re = Regex::new(r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)>").unwrap();
    let no_scripts = script_re.replace_all(html_text, "");
    let tag_re = Regex::new(r"<[^>]+>").unwrap();
    let no_tags = tag_re.replace_all(&no_scripts, " ");
    let ws_re = Regex::new(r"\s+").unwrap();
    ws_re.replace_all(&no_tags, " ").trim().to_string()
}
