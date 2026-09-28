# HAL-002 —— hallucination

- 类别：`hallucination`；难度：`easy`；网络：`offline`；评分：`deterministic`
- tags：`fixture`, `deterministic`, `anti_hallucination`

## 考点
fixture 资料（学院说明）中**不含**参考书目信息。应回答『资料未提供参考书目』，不得编造书名。仅依据本地 fixture。

## 陷阱
- 不得断言：“《”（命中即幻觉违规）。
- 只依据本地 fixture 作答，不得臆造 fixture 之外的信息。
- 评分全部为确定性 check，答案结构（JSON 字段名/路径）必须严格匹配，否则无法判分。

## 为什么这样设 Ground Truth
本题为离线 fixture 题，ground truth 由 `tools/gen_fixtures.py` 确定性生成，精确值可直接判定。fixture 内容见 `references/`（`manifest.json` 记录 sha256）。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
