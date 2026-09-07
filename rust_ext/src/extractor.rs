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
#[pyfunction]
pub fn extract_subjects(html_text: &str) -> Vec<String> {
    let mut subjects = Vec::new();
    let patterns = [
        r"(\(101\)思想政治理论|101思想政治理论|思想政治理论)",
        r"(\(201\)英语\(一\)|201英语一|英语一)",
        r"(\(204\)英语\(二\)|204英语二|英语二)",
        r"(\(301\)数学\(一\)|301数学一|数学一)",
        r"(\(302\)数学\(二\)|302数学二|数学二)",
        r"(\(408\)计算机学科专业基础|408计算机学科专业基础|408)",
        r"(\(\d{3}\)[\u{4e00}-\u{9fa5}]+)",
    ];

    for p in patterns {
        if let Ok(re) = Regex::new(p) {
            for cap in re.captures_iter(html_text) {
                if let Some(m) = cap.get(1) {
                    let item = m.as_str().to_string();
                    if !subjects.contains(&item) && subjects.len() < 4 {
                        subjects.push(item);
                    }
                }
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
