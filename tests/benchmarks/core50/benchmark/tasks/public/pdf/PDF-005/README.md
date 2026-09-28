# PDF-005 —— pdf

- 类别：`pdf`；难度：`expert`；网络：`offline`；评分：`hybrid`
- tags：`fixture`, `pdf`, `long`, `citation`, `hybrid`

## 考点
在长文档 PDF 中回答 6 个字段（学制/学费/招生人数/复试方式/复试权重/参考书目），逐字段给出**页码**与原文引用。引用准确率计入评分。

## 陷阱
- 不得断言：“参考书目为《”（命中即幻觉违规）。
- 只依据本地 fixture 作答，不得臆造 fixture 之外的信息。

## 为什么这样设 Ground Truth
本题为联网题且涉及真实院校，按方案 6.5 硬规则：`ground_truth` 必须为 `null`，改用 `must_find` 要点 + `required_sources`（E4+）+ 年份要求 + `must_not_claim` 判定。
**反指令泄漏（anti-echo）说明**：复试权重判定由 `point_hit(["8000","3 years","60"])` 改为 `regex` 匹配「40% / 40％ / retest…40」，
避免误把 echo runner 输出里的 `tool_limit=60` 当成答案命中；题面也不再出现「复试方式」等判定词。
该值必须真正从 PDF 正文读出（题面不含权重数值），回显题面无法命中。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
