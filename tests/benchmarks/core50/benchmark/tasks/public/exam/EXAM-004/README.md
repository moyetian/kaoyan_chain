# EXAM-004 —— exam

- 类别：`exam`；难度：`hard`；网络：`offline`；评分：`deterministic`
- tags：`fixture`, `deterministic`, `diff`

## 考点
对照大纲 TXT 与真题 CSV，输出 2024→2025 新增考点清单（≥3 项）。仅依据本地 fixture。

## 陷阱
- 不得断言：“2024 年新增了拥塞控制”（命中即幻觉违规）。
- 只依据本地 fixture 作答，不得臆造 fixture 之外的信息。
- 评分全部为确定性 check，答案结构（JSON 字段名/路径）必须严格匹配，否则无法判分。

## 为什么这样设 Ground Truth
本题为离线 fixture 题，ground truth 由 `tools/gen_fixtures.py` 确定性生成，精确值可直接判定。fixture 内容见 `references/`（`manifest.json` 记录 sha256）。
**反指令泄漏（anti-echo）说明**：新增考点年份判定改用 `regex` 在 `$.added` 字段上匹配 `2025`，
避免在 check 里直接写出「2024 年新增了拥塞控制」这类题面/答案字面量；判定词不再出现在 instruction。
必须真正对照大纲与真题、算出新增项才能命中。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
