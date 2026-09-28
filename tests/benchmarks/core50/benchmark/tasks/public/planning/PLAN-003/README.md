# PLAN-003 —— planning

- 类别：`planning`；难度：`medium`；网络：`offline`；评分：`deterministic`
- tags：`deterministic`, `constraint`, `stages`

## 考点
在 PLAN-002 基础上增加『每周模考』与『基础/强化/冲刺三阶段』约束，输出计划并校验阶段边界与各阶段时长占比。

## 陷阱
- 只依据本地 fixture 作答，不得臆造 fixture 之外的信息。
- 评分全部为确定性 check，答案结构（JSON 字段名/路径）必须严格匹配，否则无法判分。

## 为什么这样设 Ground Truth
本题为**离线**约束题（`deterministic`）：不设 `ground_truth`（`null`），由 `constraint` + `regex` check 对结构化的 `schedule`/`stages` 做机器校验。
**反指令泄漏（anti-echo）说明**：阶段判定不再直接匹配题面里的「基础/强化/冲刺」字面量，而是用
`regex` 在 `$.stages` 字段上做**跨阶段有序**匹配（`基础[\s\S]*强化[\s\S]*冲刺`），并单独用 `regex` 判定「模考/自测/模拟」类活动；
这些判定必须真正产出带阶段边界的计划结构才能命中，回显题面（无结构化 `stages`）无法满足。
题面保留「三阶段 / 模考」等必要信息，但判定改到「答案中才应有的结构信息」上，故不构成泄漏。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
