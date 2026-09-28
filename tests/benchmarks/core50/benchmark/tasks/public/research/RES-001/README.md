# RES-001 —— research

- 类别：`research`；难度：`hard`；网络：`offline`；评分：`deterministic`
- tags：`fixture`, `deterministic`, `report`

## 考点
从本地资料集（镜像站 HTML + CSV）产出一份院校分析报告，须含 6 个必需字段（院校、专业代码、招生人数、初试科目、历年最低分、风险提示）。仅依据本地 fixture。

## 陷阱
- 只依据本地 fixture 作答，不得臆造 fixture 之外的信息。
- 评分全部为确定性 check，答案结构（JSON 字段名/路径）必须严格匹配，否则无法判分。

## 为什么这样设 Ground Truth
本题为联网题且涉及真实院校，按方案 6.5 硬规则：`ground_truth` 必须为 `null`，改用 `must_find` 要点 + `required_sources`（E4+）+ 年份要求 + `must_not_claim` 判定。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
