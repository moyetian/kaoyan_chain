# POL-002 —— policy

- 类别：`policy`；难度：`medium`；网络：`online`；评分：`hybrid`
- tags：`diff`, `timeliness`

## 考点
比较相邻两年（2025 与 2026）推免政策文件的差异点（≥2 处），并禁止用旧政策回答新问题。须给出 E4+ 来源。

## 陷阱
- 涉及真实院校时**不得编造当年精确数字**：本题 ground_truth 为 null，只判定要点命中、来源等级与年份标识。

## 为什么这样设 Ground Truth
本题为联网题且涉及真实院校，按方案 6.5 硬规则：`ground_truth` 必须为 `null`，改用 `must_find` 要点 + `required_sources`（E4+）+ 年份要求 + `must_not_claim` 判定。
**反指令泄漏（anti-echo）说明**：差异点判定改用 `json_schema` 校验 `$.changes` 结构，生效年份改用 `regex` 匹配 `2026`；
题面不再枚举 2024/2025/2026 等年份，也不出现「差异」以外的判定字面量。`json_schema`/`regex` 均不被 anti-echo 扫描，
且必须真正比对两年政策文件才能产出结构化的差异清单。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
