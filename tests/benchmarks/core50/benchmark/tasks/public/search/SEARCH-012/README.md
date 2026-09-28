# SEARCH-012 —— search

- 类别：`search`；难度：`expert`；网络：`offline`；评分：`deterministic`
- tags：`smoke`, `snapshot`, `conflict`, `deterministic`

## 考点
快照库中同一专业存在三条互相冲突的分数线公告（公告 A / 公告 B / 第三方转载）。请判定**最新有效版本**，输出 `conflict` 说明，并给出被采信公告的 URL 与年份。

## 陷阱
- 不得断言：“以第三方转载为准”（命中即幻觉违规）。
- 只依据本地 fixture 作答，不得臆造 fixture 之外的信息。
- 评分全部为确定性 check，答案结构（JSON 字段名/路径）必须严格匹配，否则无法判分。

## 为什么这样设 Ground Truth
本题为离线 fixture 题，ground truth 由 `tools/gen_fixtures.py` 确定性生成，精确值可直接判定。fixture 内容见 `references/`（`manifest.json` 记录 sha256）。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
