use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use regex::Regex;

/// 从原文本中智能分块切片出题目 (Rust 加速版)
#[pyfunction]
pub fn chunk_text(
    py: Python<'_>,
    raw_text: &str,
    default_source: &str,
) -> PyResult<Vec<Py<PyDict>>> {
    let mut chunks = Vec::new();
    if raw_text.trim().is_empty() {
        return Ok(chunks);
    }

    let text = raw_text.replace("\r\n", "\n").replace('\r', "\n");

    // 大题分段匹配
    let sec_pattern = Regex::new(
        r"(?m)^[#*\s]*(?:第?[一二三四五六七八九十]+[部分题大题]*[、\.\s]*)?(?P<sec_title>(?:单项?选择题|多项?选择题|选择题|填空题|解答题|综合题|综合应用题|计算题|证明题|算法题|简答题)[^\n]*)$"
    ).map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    // 题号行首匹配
    let q_pattern = Regex::new(
        r"(?m)(?:^|\n)[ \t]*(?:(?P<num>\d+)[\.、][ \t]*|[【\[](?:第|题|Q)?(?P<num2>\d+)[】\]题][ \t]*)"
    ).map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    let matches: Vec<_> = q_pattern.find_iter(&text).collect();

    if matches.is_empty() {
        if text.trim().len() > 20 {
            let single = parse_single_block_dict(py, text.trim(), 1, default_source, "")?;
            chunks.push(single);
        }
        return Ok(chunks);
    }

    let sections: Vec<_> = sec_pattern.captures_iter(&text).collect();

    for i in 0..matches.len() {
        let m = &matches[i];
        let start_pos = m.start();
        let end_pos = if i + 1 < matches.len() {
            matches[i + 1].start()
        } else {
            text.len()
        };
        let block = text[start_pos..end_pos].trim();

        let mut sec_hint = String::new();
        for s in &sections {
            if let Some(s_m) = s.get(0) {
                if s_m.start() <= start_pos {
                    if let Some(title) = s.name("sec_title") {
                        sec_hint = title.as_str().to_string();
                    }
                }
            }
        }

        let num_val = {
            let caps = q_pattern.captures(m.as_str());
            if let Some(c) = caps {
                c.name("num")
                    .or_else(|| c.name("num2"))
                    .and_then(|m| m.as_str().parse::<usize>().ok())
                    .unwrap_or(i + 1)
            } else {
                i + 1
            }
        };

        let chunk_dict = parse_single_block_dict(py, block, num_val, default_source, &sec_hint)?;
        // 检查 stem 是否为空
        let stem: String = chunk_dict.bind(py).get_item("stem")?
            .and_then(|o| o.extract::<String>().ok())
            .unwrap_or_default();
        if !stem.is_empty() {
            chunks.push(chunk_dict);
        }
    }

    Ok(chunks)
}

/// 解析单个题块为 Python 字典
#[pyfunction]
#[pyo3(signature = (block, num, source, sec_hint=None))]
pub fn parse_single_block(
    py: Python<'_>,
    block: &str,
    num: usize,
    source: &str,
    sec_hint: Option<&str>,
) -> PyResult<Py<PyDict>> {
    parse_single_block_dict(py, block, num, source, sec_hint.unwrap_or(""))
}

fn parse_single_block_dict(
    py: Python<'_>,
    raw_block: &str,
    num: usize,
    source: &str,
    sec_hint: &str,
) -> PyResult<Py<PyDict>> {
    // 剥离末尾粘连的大题标题
    let tail_pattern = Regex::new(
        r"(?m)\n+[#*\s]*(?:第?[一二三四五六七八九十]+[部分题大题]*[、\.\s]*)?(?:单项?选择题|多项?选择题|选择题|填空题|解答题|综合题|综合应用题|计算题|证明题|算法题)[^\n]*$"
    ).unwrap();
    let block = tail_pattern.replace(raw_block, "").trim().to_string();

    // 提取分值
    let score_pattern = Regex::new(r"(?:本题满分|满分|分值|共)?\s*(\d+)\s*分").unwrap();
    let score: usize = score_pattern
        .captures(&block)
        .and_then(|c| c.get(1))
        .and_then(|m| m.as_str().parse().ok())
        .unwrap_or(0);

    // 分割题干与答案/解析
    let split_pattern = Regex::new(
        r"(【答案】|【参考答案】|参考答案[：:]|【解】|解[：:]|答案[：:]|【解析】|解析[：:])"
    ).unwrap();

    let parts: Vec<&str> = split_pattern.splitn(&block, 2).collect();
    let raw_stem = parts[0].trim();
    let mut answer = String::new();
    let mut analysis = String::new();
    let mut rubric_list = Vec::new();

    if parts.len() > 1 {
        let ans_and_ana = parts[1];
        let ana_pattern = Regex::new(r"(?:【解析】|解析[：:]|【评析】)").unwrap();
        let ana_parts: Vec<&str> = ana_pattern.splitn(ans_and_ana, 2).collect();
        if ana_parts.len() > 1 {
            let clean_ans_pattern = Regex::new(r"^(?:【答案】|【参考答案】|参考答案[：:]|【解】|解[：:]|答案[：:])\s*").unwrap();
            answer = clean_ans_pattern.replace(ana_parts[0].trim(), "").trim().to_string();
            analysis = ana_parts[1].trim().to_string();
        } else {
            answer = ans_and_ana.trim().to_string();
        }
    }

    // 清洗题干
    let stem_clean_pattern = Regex::new(
        r"^[#*\s]*(?:第?[一二三四五六七八九十]+[部分题大题]*[、\.\s]*)?(?:单项?选择题|多项?选择题|选择题|填空题|解答题|综合题|综合应用题|计算题|证明题|算法题)[^\n]*\n+"
    ).unwrap();
    let mut stem = stem_clean_pattern.replace(raw_stem, "").trim().to_string();

    let num_prefix_pattern = Regex::new(r"^(?:\d+[\.、\s]+|[【\[](?:题|Q)?\d+[】\]])").unwrap();
    stem = num_prefix_pattern.replace(&stem, "").trim().to_string();

    let score_prefix_pattern = Regex::new(r"^(?:（\d+分）|\(\d+分\)|\[\d+分\]|【\d+分】)\s*").unwrap();
    stem = score_prefix_pattern.replace(&stem, "").trim().to_string();

    // 识别题型与选项
    let opt_pattern = Regex::new(r"(?:\n|^|\s+)([A-D])[\.、\s]+([^\n\rA-D]+)").unwrap();
    let opt_matches: Vec<_> = opt_pattern.captures_iter(&stem).collect();
    let q_type: String;
    let final_score: usize;
    let mut options = Vec::new();

    if opt_matches.len() >= 2 || (sec_hint.contains("选择") && !opt_matches.is_empty()) {
        q_type = "choice".to_string();
        final_score = if score > 0 { score } else if sec_hint.contains("选择") { 2 } else if source.contains("408") || source.contains("专业") { 5 } else { 2 };
        for om in &opt_matches {
            let letter = om.get(1).map(|m| m.as_str().trim()).unwrap_or("");
            let content = om.get(2).map(|m| m.as_str().trim()).unwrap_or("");
            options.push(format!("{}. {}", letter, content));
        }
        if let Some(first_opt) = opt_pattern.find(&stem) {
            stem = stem[..first_opt.start()].trim().to_string();
        }
    } else if stem.contains("____") || block.contains("填空") || sec_hint.contains("填空") {
        q_type = "blank".to_string();
        final_score = if score > 0 { score } else { 5 };
    } else {
        q_type = "essay".to_string();
        final_score = if score > 0 { score } else { 10 };
    }

    // 提取采分点 (Rubric)
    if !analysis.is_empty() || !answer.is_empty() {
        let full_ans = format!("{}\n{}", answer, analysis);
        let rub_pattern = Regex::new(r"\[([\+＋]?\d+分)\]").unwrap();
        let strip_rub_pattern = Regex::new(r"\[[\+＋]?\d+分\]").unwrap();

        for line in full_ans.lines() {
            let l_str = line.trim();
            if l_str.is_empty() {
                continue;
            }
            if let Some(cap) = rub_pattern.captures(l_str) {
                let mut pts = cap.get(1).map(|m| m.as_str().replace('＋', "+")).unwrap_or_default();
                if !pts.starts_with('+') {
                    pts = format!("+{}", pts);
                }
                let desc = strip_rub_pattern.replace(l_str, "").trim().to_string();
                rubric_list.push(format!("[{}] {}", pts, desc));
            }
        }

        // 启发式拆分采分点
        if rubric_list.is_empty() {
            let step_lines: Vec<&str> = full_ans.lines().map(|l| l.trim()).filter(|l| !l.is_empty()).collect();
            let mut allocated = 0;
            let step_val = (final_score / step_lines.len().max(1)).max(1);
            for (s_idx, sl) in step_lines.iter().take(4).enumerate() {
                if sl.len() >= 6 {
                    let this_score = if allocated + step_val <= final_score {
                        step_val
                    } else {
                        final_score - allocated
                    };
                    if this_score > 0 {
                        let truncated = if sl.chars().count() > 60 {
                            format!("{}...", sl.chars().take(60).collect::<String>())
                        } else {
                            sl.to_string()
                        };
                        rubric_list.push(format!("[+{}分] 步骤{}：{}", this_score, s_idx + 1, truncated));
                        allocated += this_score;
                    }
                }
            }
        }
    }

    // 提炼考点关键词
    let mut points = Vec::new();
    let kw_candidates = [
        "二叉树", "平衡二叉树", "图的遍历", "Dijkstra", "快速排序", "分页存储", "虚拟内存", "TCP", "三次握手",
        "中值定理", "洛必达法则", "定积分", "二重积分", "特征值", "特征向量", "正定二次型", "马原", "毛中特"
    ];
    let combined = format!("{} {} {}", stem, answer, analysis);
    for kw in kw_candidates {
        if combined.contains(kw) && !points.contains(&kw.to_string()) {
            points.push(kw.to_string());
        }
    }

    let dict = PyDict::new_bound(py);
    dict.set_item("number", num)?;
    dict.set_item("q_type", q_type)?;
    dict.set_item("score", final_score)?;
    dict.set_item("stem", stem)?;
    dict.set_item("options", PyList::new_bound(py, options))?;
    dict.set_item("answer", answer)?;
    dict.set_item("analysis", analysis)?;
    dict.set_item("rubric", PyList::new_bound(py, rubric_list))?;
    dict.set_item("points", PyList::new_bound(py, points))?;
    dict.set_item("source", source)?;

    Ok(dict.unbind())
}
