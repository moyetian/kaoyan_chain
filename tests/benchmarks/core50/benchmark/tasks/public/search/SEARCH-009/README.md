# SEARCH-009 —— search

- 类别：`search`；难度：`hard`；网络：`online`；评分：`hybrid`
- tags：`table`, `multi_year`, `official_source`

## 考点
汇总某专业 2023–2025 三年复试线的官方公告，输出结构化表格：3 行（每年一行）× 4 列（年份、总分线、单科线、来源 URL）。必须逐年给出 E4+ 来源，不得用第三方整理数据代替官方公告。

## 陷阱
- 涉及真实院校时**不得编造当年精确数字**：本题 ground_truth 为 null，只判定要点命中、来源等级与年份标识。

## 为什么这样设 Ground Truth
本题为联网题且涉及真实院校，按方案 6.5 硬规则：`ground_truth` 必须为 `null`，改用 `must_find` 要点 + `required_sources`（E4+）+ 年份要求 + `must_not_claim` 判定。
**反指令泄漏（anti-echo）说明**：三年时间线判定改用 `regex` 跨段有序匹配（`2023[\s\S]*2024[\s\S]*2025`），
判定词不再以独立字面量出现在题面。必须真正检索并按年份顺序组织答案才能命中。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
