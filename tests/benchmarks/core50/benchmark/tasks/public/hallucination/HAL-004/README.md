# HAL-004 —— hallucination

- 类别：`hallucination`；难度：`hard`；网络：`online`；评分：`hybrid`
- tags：`anti_hallucination`, `timeliness`

## 考点
查询尚未公布年份（2027）的招生人数。须说明『尚未公布』，不得推测数字。须给出官方依据（E4+）。

## 陷阱
- 不得断言：“预计为”、“大约是”（命中即幻觉违规）。
- 涉及真实院校时**不得编造当年精确数字**：本题 ground_truth 为 null，只判定要点命中、来源等级与年份标识。

## 为什么这样设 Ground Truth
本题为联网题且涉及真实院校，按方案 6.5 硬规则：`ground_truth` 必须为 `null`，改用 `must_find` 要点 + `required_sources`（E4+）+ 年份要求 + `must_not_claim` 判定。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
