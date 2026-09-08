use pyo3::prelude::*;
use sha2::{Sha256, Digest};
use regex::Regex;
use rayon::prelude::*;

/// 计算字符串的 SHA256 哈希值
#[pyfunction]
pub fn sha256_hash(content: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(content.as_bytes());
    let result = hasher.finalize();
    format!("{:x}", result)
}

/// 批量多校指纹比对 (Rayon 多核并行计算)
/// 输入: Vec<(url, old_hash, html_content)>
/// 输出: Vec<(url, is_changed, new_hash, new_titles)>
#[pyfunction]
pub fn batch_fingerprint_compare(
    items: Vec<(String, String, String)>,
) -> PyResult<Vec<(String, bool, String, Vec<String>)>> {
    let results: Vec<(String, bool, String, Vec<String>)> = items
        .par_iter()
        .map(|(url, old_hash, html_content)| {
            let new_hash = sha256_hash(html_content);
            let is_changed = new_hash != *old_hash;
            let titles = extract_titles_impl(html_content);
            (url.clone(), is_changed, new_hash, titles)
        })
        .collect();
    Ok(results)
}

/// 从 HTML 提取通知列表标题 (Rust 正则抽取)
#[pyfunction]
pub fn extract_titles(html_text: &str) -> Vec<String> {
    extract_titles_impl(html_text)
}

fn extract_titles_impl(html_text: &str) -> Vec<String> {
    let link_pattern = Regex::new(r"(?is)<a[^>]+>(.*?)</a>").unwrap();
    let tag_pattern = Regex::new(r"<[^>]+>").unwrap();
    let whitespace_pattern = Regex::new(r"\s+").unwrap();

    let skip_keywords = ["版权所有", "网站地图", "关于我们", "联系我们"];

    let mut titles = Vec::new();
    for cap in link_pattern.captures_iter(html_text) {
        let raw = cap.get(1).map(|m| m.as_str()).unwrap_or("");
        let cleaned = tag_pattern.replace_all(raw, "").trim().to_string();
        let cleaned = whitespace_pattern.replace_all(&cleaned, " ").to_string();

        if cleaned.len() >= 8 && cleaned.len() <= 60 {
            if !skip_keywords.iter().any(|kw| cleaned.contains(kw)) {
                if !titles.contains(&cleaned) {
                    titles.push(cleaned);
                }
            }
        }
    }
    titles
}
