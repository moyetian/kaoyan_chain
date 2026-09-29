# SEARCH-005 —— search

- 类别：`search`；难度：`medium`；网络：`online`；评分：`hybrid`
- tags：`official_source`, `publication_form`

## 考点
找到『考研数学（一）考试大纲』的官方发布渠道页面，并说明官方以何种形式发布该大纲（纸质出版物，官方不提供免费 PDF 电子版）。要求：官方来源（E4+）、显式标注大纲年份。

## 陷阱
- 涉及真实院校时**不得编造当年精确数字**：本题 ground_truth 为 null，只判定要点命中、来源等级与年份标识。
- 全国统考大纲（数学等）**官方从不以免费电子文档形式发布**：中国教育考试网「考试大纲」栏目最新数学条目停留于 2022 版（且为图片形式），此后各年大纲均由出版社以纸质图书形式出版发行。编造一个 `.pdf` 直链即偏离事实；如实说明发布形式才是正解。
- v1.1 修订记录：原 c2 为 `regex: https?://[^\s]+\.pdf`（要求 PDF 直链）、r1 问「官方大纲页与 PDF 直链」——该要求与事实冲突（不存在官方免费 PDF），会奖励编造链接，与反幻觉原则相悖；v1.1 改为「发布渠道 + 发布形式」判定。

## 为什么这样设 Ground Truth
本题为联网题且涉及真实院校，按方案 6.5 硬规则：`ground_truth` 必须为 `null`，改用 `must_find` 要点 + `required_sources`（E4+）+ 年份要求 + `must_not_claim` 判定。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.1`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
