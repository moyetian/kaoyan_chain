# benchmark/fixtures —— 全虚构声明（D-04）

> **本目录及其生成的全部 fixture 内容均为虚构，仅用于基准测试（KaoyanBench v1.0）。**
> **不代表、不对应、也不影射任何真实院校、专业、人员、机构或链接。**

## 1. 声明要点

- 本目录下的**所有** CSV / TXT / HTML / PDF / XLSX / 快照内容：
  - 院校名（如「北原大学」「东岭工业大学」「南湾理工大学」）、专业、代码、人数、分数、政策条款、
    URL（如 `northplain.edu.example`）**全部为虚构**。
  - 域名一律使用 `.example` 保留域，**不指向任何真实站点**，也无法访问。
  - 人名、书目、联系方式（如 `admissions@northplain.edu.example`）均为占位虚构值。
- **快照库（SNAP）为本地固定假快照**，由生成器直接写出，**从不联网抓取**，保证可复现。
- 文件头均带「虚构数据，仅用于基准测试」声明（HTML 用注释、文本用首行注释）。

## 2. 为什么必须虚构

见方案第 8 节 R3：**真实院校当年招生数字不可编造**。为规避「写死真实数字 → 产出错误基准」的风险：

1. 联网题（涉及真实院校）的 `ground_truth` 一律为 `null`，只判定要点命中、来源等级与年份标识。
2. 需要精确数字判定的任务，一律使用**本目录的虚构 fixture**，从而既能 100% 确定性评分，又不触碰真实数据。

## 3. 目录结构

```
benchmark/fixtures/
├── README.md            # 本声明
├── seeds/               # 生成器输入源（供人 review）
│   ├── exam_questions.csv        # 真题考点表（虚构）
│   ├── admission_scores.csv      # 录取分数（虚构）
│   ├── lead_conflict.csv         # 自相矛盾数据（HAL-003 用）
│   ├── weak_points.csv           # 薄弱点诊断（PLAN-004 用）
│   └── policy_2024/2025/2026.txt # 三版政策文本（虚构）
└── templates/           # 虚构院校镜像站 HTML 模板
    ├── northplain_catalog.html
    ├── northplain_catalog_conflict.html
    ├── northplain_college.html
    └── northplain_hallucination.html
```

快照库（SNAP）不在本目录下，而是直接写入**运行时快照目录**
`benchmark/snapshots/<TASK_ID>/{index.json,meta.json,pages/<sha256>.html}`
（与 `config/default.yaml` 的 `evaluation.snapshot_dir` 一致），
以便 `kaoyanbench snapshot verify` 与 `--offline-replay` 能读到。

## 4. 生成与校验

```bash
python tools/gen_fixtures.py            # 确定性生成全部 fixture（幂等）
python tools/gen_fixtures.py --check    # 校验当前文件与预期一致（CI 可用）
kaoyanbench validate                    # 校验各任务 references/manifest.json 的 sha256
```

- 生成器**零第三方依赖**即可运行：PDF 走内置最小生成器（纯标准库），Excel 无 `openpyxl` 时降级 CSV 并告警。
- 固定 seed、无时间戳、无随机、无字典遍历顺序依赖 → **连续两次生成，每个文件 sha256 完全一致。**
- 各任务 `references/manifest.json` 记录每个 fixture 的 sha256 与大小，`validate` 会逐字节校验。
