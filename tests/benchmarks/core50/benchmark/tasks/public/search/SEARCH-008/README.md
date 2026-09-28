# SEARCH-008 —— search

- 类别：`search`；难度：`medium`；网络：`online`；评分：`hybrid`
- tags：`diff`, `official_source`

## 考点
找到同一所高校 2024 与 2025 两版硕士招生简章页面的链接，并列出两版之间至少 2 处差异。差异须有页面依据（E4+），不得凭空推测。

## 陷阱
- 不得断言：“完全相同”（命中即幻觉违规）。
- 涉及真实院校时**不得编造当年精确数字**：本题 ground_truth 为 null，只判定要点命中、来源等级与年份标识。

## 为什么这样设 Ground Truth
本题为联网题且涉及真实院校，按方案 6.5 硬规则：`ground_truth` 必须为 `null`，改用 `must_find` 要点 + `required_sources`（E4+）+ 年份要求 + `must_not_claim` 判定。
**反指令泄漏（anti-echo）说明**：版本差异改用 `json_schema` 校验 `versions`/`changes` 结构，年份用 `regex` 匹配 `20(24|25)`，
并要求 `answer_format=json`；题面不再罗列年份或差异点字面量。必须真正检索并结构化输出才能命中。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
