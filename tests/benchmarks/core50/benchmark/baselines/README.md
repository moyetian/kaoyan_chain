# Baselines（回归基线，入库版本化）

本目录存放 CI 回归门禁的**版本化基线**，随代码提交入库（不受 `results/` gitignore 影响）。

- `smoke__mock__baseline.suite.json`：`smoke` 套件 × `mock` Agent 的基线
  （6 题全离线确定性，`grader_mode=full`，`task_success_rate=100%`）。
  由维护者本地 `kaoyanbench run --suite smoke --agent mock --tag baseline`
  生成后拷贝至此。

## 为什么放在这里（P0 教训）

GitHub Actions 每次都是全新 runner，`results/` 被 gitignore 且 CI 不提交，
旧流程把 baseline 写在 `results/baseline/` 下必然每次缺失、被迫「自比对」、
门禁恒绿。基线必须入库，CI 只读不写。

## 更新基线（维护者，发版时）

```bash
kaoyanbench run --suite smoke --agent mock --tag baseline
cp results/suites/smoke__mock__baseline.suite.json \
   benchmark/baselines/smoke__mock__baseline.suite.json
# 确认 grader_mode=full 后随代码提交
```

`smoke.yml` 在 baseline 缺失时直接 `::error::` + 退出码 2，
绝不静默自比对。
