use pyo3::prelude::*;

mod chunker;
mod hasher;
mod context_compactor;
mod extractor;

/// 考研学习链 Rust 加速扩展模块 (ky_rust_ext)
#[pymodule]
fn ky_rust_ext(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // 题目切片
    m.add_function(wrap_pyfunction!(chunker::chunk_text, m)?)?;
    m.add_function(wrap_pyfunction!(chunker::parse_single_block, m)?)?;

    // 哈希与指纹比对
    m.add_function(wrap_pyfunction!(hasher::sha256_hash, m)?)?;
    m.add_function(wrap_pyfunction!(hasher::batch_fingerprint_compare, m)?)?;
    m.add_function(wrap_pyfunction!(hasher::extract_titles, m)?)?;

    // 上下文压缩
    m.add_function(wrap_pyfunction!(context_compactor::estimate_tokens, m)?)?;
    m.add_function(wrap_pyfunction!(context_compactor::compact_messages, m)?)?;

    // HTML 文档抽取
    m.add_function(wrap_pyfunction!(extractor::extract_title, m)?)?;
    m.add_function(wrap_pyfunction!(extractor::extract_pdf_links, m)?)?;
    m.add_function(wrap_pyfunction!(extractor::extract_subjects, m)?)?;
    m.add_function(wrap_pyfunction!(extractor::clean_html_to_text, m)?)?;

    Ok(())
}
