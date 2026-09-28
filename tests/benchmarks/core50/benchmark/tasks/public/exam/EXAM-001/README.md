# EXAM-001 —— exam

- 类别：`exam`；难度：`easy`；网络：`offline`；评分：`deterministic`
- tags：`smoke`, `fixture`, `deterministic`, `count`

## 考点
统计真题 CSV 中考点『数据结构』出现的次数，输出精确整数。仅依据本地 fixture。

## 陷阱
- 只依据本地 fixture 作答，不得臆造 fixture 之外的信息。
- 评分全部为确定性 check，答案结构（JSON 字段名/路径）必须严格匹配，否则无法判分。

## 为什么这样设 Ground Truth
本题为离线 fixture 题，ground truth 由 `tools/gen_fixtures.py` 确定性生成，精确值可直接判定。fixture 内容见 `references/`（`manifest.json` 记录 sha256）。
**反指令泄漏（anti-echo）说明**：科目判定改用 `point_hit{point_id:"p1"}`（引用 `expected.must_find`），
不把「数据结构」等判定词写进 check 关键词；计数值用 `numeric`（精确比对 fixture 计算出的整数）。
两项判定都必须真正读 fixtures/questions.csv 才能得出（题面不含次数），回显题面无法命中。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
