# RES-003 —— research

- 类别：`research`；难度：`expert`；网络：`online`；评分：`hybrid`
- tags：`long_horizon`, `official_source`

## 考点
为给定背景的用户选定 3 所院校（冲/稳/保），输出对比表 + 官方来源清单（E4+，≥3 条）。

## 陷阱
- 涉及真实院校时**不得编造当年精确数字**：本题 ground_truth 为 null，只判定要点命中、来源等级与年份标识。

## 为什么这样设 Ground Truth
本题为联网题且涉及真实院校，按方案 6.5 硬规则：`ground_truth` 必须为 `null`，改用 `must_find` 要点 + `required_sources`（E4+）+ 年份要求 + `must_not_claim` 判定。
**反指令泄漏（anti-echo）说明**：冲/稳/保三档由 `set_includes` 改为 `regex` 前瞻匹配（`(?=[\s\S]*冲)(?=[\s\S]*稳)(?=[\s\S]*保)`），
来源清单由 `set_includes` 改为 `regex` 匹配 `https?://…`，判定词不再以字面量形式出现在题面。
必须真正完成选校对比、给出带链接的官方来源才能命中。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
