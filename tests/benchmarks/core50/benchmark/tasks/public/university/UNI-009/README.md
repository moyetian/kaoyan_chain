# UNI-009 —— university

- 类别：`university`；难度：`expert`；网络：`offline`；评分：`deterministic`
- tags：`fixture`, `conflict`, `deterministic`

## 考点
镜像站里，学校研究生院主页与下属计算机学院页面给出的 081200 专业计划名额对不上。请结构化输出 JSON：`conflict`（布尔值，true 表示两处数字确实不一致）、`a_plan`（研究生院目录页 source_01.html 的数值）、`b_plan`（学院页 source_02.html 的数值）、`a_source` 与 `b_source`（各自来源文件名）、`conclusion`（你采信的结论及理由）。仅依据本地 fixture。

## 陷阱
- 不得断言：“两页完全一致”（命中即幻觉违规）。
- 只依据本地 fixture 作答，不得臆造 fixture 之外的信息。
- 评分全部为确定性 check，答案结构（JSON 字段名/路径）必须严格匹配，否则无法判分。

## 为什么这样设 Ground Truth
本题为离线 fixture 题，ground truth 由 `tools/gen_fixtures.py` 确定性生成，精确值可直接判定。fixture 内容见 `references/`（`manifest.json` 记录 sha256）。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.1`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
