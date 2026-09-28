# SEARCH-011 —— search

- 类别：`search`；难度：`hard`；网络：`offline`；评分：`deterministic`
- tags：`snapshot`, `deterministic`

## 考点
在本地快照库中检索『北原大学 2025 年推荐免试研究生接收办法』（虚构院校），输出：①快照 URL；②该快照内容的 content_sha256。仅依据本地快照，不得联网或臆造。

## 陷阱
- 不得断言：“2024 年接收办法”（命中即幻觉违规）。
- 只依据本地 fixture 作答，不得臆造 fixture 之外的信息。
- 评分全部为确定性 check，答案结构（JSON 字段名/路径）必须严格匹配，否则无法判分。

## 为什么这样设 Ground Truth
本题为联网题且涉及真实院校，按方案 6.5 硬规则：`ground_truth` 必须为 `null`，改用 `must_find` 要点 + `required_sources`（E4+）+ 年份要求 + `must_not_claim` 判定。
**反指令泄漏（anti-echo）说明**：快照 URL 判定用 `regex` 匹配 `northplain.edu.example` 域名（题面保留院校名但判定基于答案里的 URL），
哈希校验值用 `regex` 匹配 64 位十六进制串，杜绝 `point_hit` 把题面字面量当答案。
同时补上此前缺失的 `must_not_claim` check（`patterns:["2024 年接收办法"]`，`critical=true`）——此前 `expected.must_not_claim` 非空却无对应 check，
抗幻觉能力实际未被评测，现已在 `validate` 中消除该 warn。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
