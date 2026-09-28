# PLAN-002 —— planning

- 类别：`planning`；难度：`medium`；网络：`offline`；评分：`deterministic`
- tags：`deterministic`, `constraint`

## 考点
制定 100 天复习计划：每天 6 小时；数学≥2h、专业课≥2h、英语≥1h、政治≥0.5h；时段之间不得重叠。输出 `schedule` JSON。

## 陷阱
- 每天总时长必须恰好为 6，且各科目时长满足下限；时段区间不得重叠（`no_overlap`）。
- 只依据本地 fixture 作答，不得臆造 fixture 之外的信息。
- 评分全部为确定性 check，答案结构（JSON 字段名/路径）必须严格匹配，否则无法判分。

## 为什么这样设 Ground Truth
本题为**离线**约束题（`deterministic`）：不设 `ground_truth`（`null`），由 `constraint` check 对结构化的 `schedule` 做机器校验——
`total_days=100`、`daily_total_hours=6`、四门课 `subject_min_hours` 下限、`no_overlap=true`，外加 `json_schema` 校验数组长度 ≥100。
**反指令泄漏（anti-echo）说明**：日程的各项约束都写在对 `schedule` 的结构化判定上（`constraint`/`json_schema` 不被 anti-echo 扫描），
且要求答案必须真正排出一份合法的 100 天日程才能通过——回显题面（只有文字、没有合法 schedule 结构）无法满足任何一条判定。
因此本题题面虽含「6h / ≥2h」等必要约束词，但不构成泄漏：抄题面不能直接满足判定。
另：本题原设计的「每周休半天」与「每天恰好 6h + 各科目下限」在检查器语义下互相矛盾（休息日必然击穿每日时长/科目下限），
已移除该规则，改以 `no_overlap` 作为质量约束，规则集自洽且可满足。

## 更新注意事项
- 变更 fixture 后必须重跑 `python tools/gen_fixtures.py`，并让 `manifest.json` 的 sha256 同步更新（`kaoyanbench validate` 会校验）。
- 变更 ground_truth 时必须提升 `version`（当前 `1.0`）。
- 本任务 fixture 内全部院校/专业/人名/链接均为虚构（北原大学 / 东岭工业大学 / northplain.edu.example），仅用于基准测试。
