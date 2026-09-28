# SEARCH-001 —— search

- 类别：`search`；难度：`easy`；网络：`online`；评分：`hybrid`
- tags：`official_source`, `core`

## 考点
请找到中国研究生招生信息网（研招网）关于『硕士研究生招生考试初试时间与科目安排』的官方说明页面，给出：①该页面的官方 URL；②该安排适用的招生年份。必须给出至少 1 条官方（E4 及以上）来源，并显式标注年份。

## 陷阱
- 涉及真实院校时**不得编造当年精确数字**：本题 ground_truth 为 null，只判定要点命中、来源等级与年份标识。

## 为什么这样设 Ground Truth
本题为联网题且涉及真实院校，按方案 6.5 硬规则：`ground_truth` 必须为 `null`，改用 `must_find` 要点 + `required_sources`（E4+）+ 年份要求 + `must_not_claim` 判定。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
