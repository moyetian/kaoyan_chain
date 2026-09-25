# 更新日志 (CHANGELOG)

本文件记录**考研学习链 (Kaoyan AI Study Chain)** 的版本变更与升级方式。
版本号以 `pyproject.toml` / `tools/version.py` 为准，亦可运行：

```bash
python -c "import sys; sys.path.insert(0, 'tools'); from version import get_version; print(get_version())"
```

---

## [3.1.0] — 2026-09-25（当前发布版本）

> 阶段三「可证」：把护城河变成**可评测资产** —— 六条评测基准（C1–C6）落地，
> 覆盖考纲守卫 / 引文忠实度 / 题源溯源 / 判分一致 / 检索降级 / 学习增益；
> 外加四端 UI 设计系统统一（P0–P4）。版本号真源 `pyproject.toml`。

### 🧪 阶段三 · 可证（C1–C6）

- **C1 · 考纲守卫评测**（`--syllabus`）：128 条评测集（否定 / 笔记语境 31.2%）
  + 共用评测引擎 `tools/benchmarks/runner.py`（退出码 0/1/2 语义全仓统一）。
  建集时实测修复 hook 真缺陷：口语否定词缺失（「别讲曲面积分」等 7 变体被误拦）。
- **C2 · 引文忠实度评测**（`--ragas`）：109 条评测集，覆盖无据引用 / 伪造 URL /
  跨校混淆 / 过期数据 / 二手源冒充五类；双指标（拦截侧必须 100%、放行侧 ≥98%）。
  建集时修复 `citation_engine` 对非法字段载体的契约逃逸（统一收敛为 fail-closed）。
- **C3 · 题源溯源 ID**：`tools/skills/question_source.py` —— 每张题卡携带
  `source_id`（来源 + 题干指纹）与独立校验和；渲染侧注入、解析侧校验，
  不符 → 排除出卷；`tools/backfill_source_ids.py` 提供补录 CLI（--dry-run/--json）。
- **C4 · 判分评测 pilot**（`--grading`）：30 份 AI 预标 + 真实 LLM 响应快照
  （一次性采集后冻结，回放零网络零成本）；首份真实基线：采分点命中一致率
  Jaccard 92.78% / 总分 MAE 0.69 / 错因一致率 56.67%。**不进 CI**（无人工金标准前
  测的是「与预标一致率」，元测试钉住）。
- **C5 · RAG 显式降级**：`SearchOutcome` 让降级成为「一次检索」的属性，
  四条降级原因如实（sqlite-vec 未加载 / ONNX 模型缺失 / 非预期异常 / 显式关闭），
  互不误报；新增用户可达入口 `ky rag`（别名 `ky search`）与 REPL `/rag`、`/search`，
  safe 只读白名单放行、不代为建库。
- **C6 · 学习增益代理指标**：`ky gain` / REPL `/gain` —— 错题复测通过率周趋势
  （通过 = good/easy）/ 同类错因复发（跨 ≥2 天弱判据）/ 计划完成率（加权）；
  报告本地落盘 `.memory/learning_gain_report.md`、不上传、非门禁（退出码只有 0/2）。

### 🎨 改进 · 四端 UI 设计系统（P0–P4）

- **P0 设计地基**：`tools/theme/tokens.py` 五类 token 扩展 + Lucide 图标系统
  （42 图标，可复现抽取）+ `DESIGN.md`；对比度门禁新增 chart 色 ≥3:1（五套预设最低 3.8:1）。
- **P1 Web 看板重构**：修复媒体查询反向覆盖根因（桌面规则被同特异性基础规则覆盖）；
  hero 环形进度卡 + KPI 卡 + 品牌侧栏；sprite 内联（file:// 同源策略下外链 `<use>` 被拒）。
- **P2 GUI 改造**：左侧导航 rail + Ctrl+K 命令面板 + 卡片族 + 对话气泡（自建组件族，
  零新依赖）；修复 QSS 全局 `min-height` 静默覆盖 rail 项的真缺陷。
- **P3 CLI / TUI**：CLI 全面 Rich 化（`rich` 升为核心依赖，README「零依赖」承诺同步更正）；
  修复 textual 8.x 高亮死选择器（`.--highlight` → `-highlight`）。
- **P4 回归守护**：四端截图基线 + 看板守卫（31 图标容器全 SVG / 真实 390px 视口
  无横向溢出 / 5 套预设轮换）。

### 🔒 修复 · 隐私与可用性

- `privacy_policy` 补漏 `.memory`（任意深度排除）：此前 `should_publish('.memory/...')`
  返回 True，清扫层（`purge_leaked_files_in_dst`）对嵌套 `.memory/` 残留会放行不删。
- REPL `/gain --no-save` 曾被静默忽略（硬编码落盘）→ 已解析参数。
- `ky --help` 子命令列表 / REPL 指令大盘 / `_HANDLERS_WITH_OWN_HELP` 三处补齐
  `rag`、`gain`（详版帮助可达）。
- 报告类 Markdown 表格转义 `|`（错因名含管道符曾破表）。
- **公开副本「占位值 → 占位值」自指改写修复**：副本的 `ky_config.json` 是占位值时，
  重跑脱敏会生成自指规则（如 `待诊断 → 待诊断薄弱点`），把 7 个副本文件（含
  `tools/study_planner.py` 与 GUI 功能代码）改写、元测试变红（公开用户初始化后
  跑 pytest 必红）。现由 `privacy_policy.is_placeholder_value()` 统一闸门拦下
  （校名 / 专业 / 自命题科目 / 薄弱点 / 书目五类字段，6 处规则生成点接入）。

### ✅ 质量

- pytest **1889 通过 + 3 跳过**（收集 1892）；`test_ky_suite` 305 项；
  `test_new_features` 131 项；合计 **2325 项通过**。
- 新增评测集：syllabus 128 / citation 109 / grading 30 份；
  评测门禁 `ci_evaluate_gate` 三路径全绿。

---

## [3.0.1] — 2026-09-25（上一发布版本）

> 导出缺陷修复：公开仓库副本的 CI 配置与 Rust 源码长期被冻结在旧版本 ——
> 3.0.0 及更早的公开副本从未收到 `.github/workflows/test.yml` 与
> `rust_ext/` 的任何更新。版本号真源 `pyproject.toml`。

### 🔧 修复 · 公开副本导出冻结（sync_publish）

- **根因**：`tools/sync_publish.py` 把 `.github`（CI 配置）与 `rust_ext`
  （Rust 加速源码）列为「公开副本自有、不由私有工作区镜像」的保留内容
  （`PUBLIC_PRESERVE_ROOTS`）—— 既不复制、也不删除。而 `rust_ext` 还被
  `privacy_policy.BUILD_ARTIFACT_DIRS` 误分类为「构建产物」（它实际是源码
  目录，`.gitignore` 明示「源码保留、二进制产物不入库」）。两者叠加的后果：
  副本里的旧版本被 `--force` 永久保护，私有侧的后续修改**永远到不了公开仓库**。
- **实际影响**（实测）：公开副本 `rust_ext` 停在 v2.7.0（A2 的 tokenizer
  五类单价同步 `3f34665` 从未到达，同一向量在 Python / Rust 两侧算出不同
  结果）；`test.yml` 停在 `2de15ea`（A4 的评测门禁与 rust-ext 硬门禁
  `9b0bb14` 从未到达）。外部复评报告据此判定「CHANGELOG 与实现漂移」——
  本地实现为真，漂移在副本侧。
- **修复**：`rust_ext` 移出 `BUILD_ARTIFACT_DIRS`、`.github` 移出
  `sync_publish.EXCLUDE_DIRS`、`PUBLIC_PRESERVE_ROOTS` 收窄为
  （错题本 / 每日作业）；新增 `rust_ext/target` 排除（Cargo 构建产物，
  含数百 MB 二进制，与源码放行配套）。
- **验证**：临时目标真实 `--force` 导出（492 文件）后逐字节比对 ——
  `.github/workflows/*.yml` 与 `rust_ext/src/*.rs` 均与本地真源一致、
  `rust_ext/target` 未泄漏、副本已有骨架保留；身份残留探针 0 命中
  （exit 0）；新增 7 项锁定测试（含三条阴性对照：把任一排除规则改回去必红）。

---

## [3.0.0] — 2026-09-24（上一发布版本）

> 阶段二「可靠（Reliability）」：B1–B4 四批（B2、B3 各拆 2 个子批）并入本版。
> 本版集中解决四类**会静默发生**的问题：长会话丢约束、沙箱边界靠自觉、
> 会话不可恢复、能力声明与实现不符。版本号真源 `pyproject.toml`。

### 🧠 阶段二 · 可靠（B1–B4）

- **B1 · 上下文压缩升级**：旧压缩把被压缩历史逐条压成
  「学员此前曾提问: …」并只保留最后 10 行 —— 早期考纲约束 / 错因 / 待复习
  一旦被挤出「保留窗口 + 摘要窗口」即**静默丢弃**。新增
  `tools/agent/compaction.py` 结构化摘要引擎（goal / progress / key_info /
  file_ops / pending 五分区，每分区有界 12 条）；考纲约束 / 错因 / 待复习
  整行保留（单条上限 400 字符，旧实现 100 字符截断正是丢约束根因之一）。
  摘要支持 LLM 模式（`agent.compact_mode`，异常 / 非法返回一律降级回规则
  摘要并提示）；新增 `validate_compacted` 结构校验（孤儿 tool / 缺配对 /
  非法 role）。`compact_context` 新增 `focus` 参数。
- **B2 · 沙箱加固**：
  - **B2a · 脚本执行收紧**：堵住「先用 `write_file` 落一个 `evil.py`、再
    `python evil.py`」这条**唯一能绕过审批执行任意代码**的路径。两道闸门：
    ① 脚本路径白名单（`tools/`、`tests/`、`rust_ext/`，硬拒绝不走审批）；
    ② 会话污染闸门 —— 本会话被写过 / 改过的脚本，执行前必须经审批通道
    显式批准，`auto` 模式不得自动放行、headless 一律拒绝。残余已在代码中
    诚实标注：`pytest <脚本>` 等同样能执行代码的入口未纳入本闸门。
  - **B2b · 外部读取授权**：工作区外只读豁免改为**默认拒绝 + 需授权**
    （敏感路径 / 凭据 / 穿越永不可授权）；交互式弹卡可「信任该目录」
    （授权记忆进程级、不落盘）、headless 拒绝并给双出路引导；`doctor` 新增
    【3.6 沙箱能力边界（逻辑隔离，非 OS 沙箱）】，REPL / GUI 审批弹窗同步明示。
- **B3 · 会话持久化**：
  - **B3a · JSONL 事件日志 + resume**：会话事件 append-only 落盘
    `.memory/sessions/<session_id>.jsonl`（AgentEvent schema v1，parent 串链，
    7 类事件）；崩溃现场半截 JSON 行丢弃容错、未知事件不崩（新旧版本互读）；
    `compose_history()` 让「resume 上下文 == 实时上下文」可逐条断言；写失败
    降级纯内存不中断对话；`SessionStart` 改为会话首次仅一次、`close()` 幂等。
  - **B3b · fork / replay + `ky session` 命令**：`ky session ls | resume |
    fork --at | rm | prune`（删除默认 dry-run，`--yes` 才真删；id 支持唯一
    前缀）；fork 点自动对齐 `tool_call`/`tool_result` 配对边界、**只读**源文件；
    GUI 跨消息复用同一 `session_id`（每条消息仍新建 runner）。
- **B4 · MCP 完形 + Skills 真实性**：MCP 客户端补齐 `resources/list|read` 与
  `prompts/list|get`（此前 initialize 声明三类 capability 却只实现 `tools/*`）；
  `start()` 失败写 `last_error`（命令不存在 / 进程退出 / 握手超时 / 非 JSON-RPC
  各有专属文案），新增 `health()` 三档（healthy / degraded / dead）与
  `mcp_health()` 汇总，崩溃不再无痕迹。Skills 15 项状态全部改为**运行时计算**
  （READY / DEGRADED / UNAVAILABLE + reason；此前 13 项硬编码「已就绪」，
  依赖缺失也显示就绪），`_SKILL_META` 与健康提供者在构建期强校验防回退；
  REPL `/skills` 面板按真实档位着色，`/img` `/calc` `/pdf` 对不可用技能
  给出技能级原因与修复建议。

### 🧪 测试与验证

- 新增 6 组守门测试（共 170 项）：`tests/test_b1_compaction.py`（34）、
  `tests/test_b2a_script_exec_gate.py`（27）、`tests/test_b2b_external_read_gate.py`（32）、
  `tests/test_b3a_session_log.py`（20）、`tests/test_b3b_session_fork.py`（41）、
  `tests/test_b4_mcp_skills_health.py`（16）。
- 核心验收均带**阴性对照**与端到端实测：如 B1 把 `extract_key_info` 变异为空桶
  后「早期约束存活」用例必须变红；B2a 摘掉守卫后必须复现「脚本真的被执行」；
  B3a 用独立探针脚本验证 5 轮 / 2 次压缩下 `rebuild == 实时`；B4 拔掉 sympy
  后 math_verifier 必须从 READY 变 DEGRADED 且带 reason。
- B2b 起 `test_new_features` 的 H.8 与 SSRF 阴性对照按新契约更新。

---

## [2.9.0] — 2026-09-24（上一发布版本）

> 阶段一「可信（Trust）」：A1–A4 四批 + 2.8.0 之后累积的修复一并并入本版。
> 版本号真源 `pyproject.toml`；本版同时把 CI 门禁收紧，并修掉两处会静默
> 降级的安全缺陷（审批模式解析、脱敏导出的等价问题见下）。

### 🔒 阶段一 · 可信（A1–A4）

- **A1 · FSRS 评测红灯修复**：`evaluate_pipeline.py --srs` 此前把「未复习卡
  （`stage_before=0`）」也当成"预测遗忘"，导致 RMSE=1.0 / LogLoss=13.8 的
  满屏红灯。现按 FSRS 可校准域排除 stage0 与非法样本、并把预测时间线对齐到
  `due_before`，同时打印「已排除样本」明细。退出码语义固定为
  `0=达标 / 1=不达标 / 2=样本不足`，并归档 4 份真实残留数据作夹具。
- **A2 · 真 tokenizer + 上下文预算查表**：`int(总字符数 * 0.6)` 的假 tokenizer
  与硬编码 `max_context_tokens=48000` 一并移除。新增 `tools/agent/tokenizer.py`
  作为唯一实现处：装了 `tiktoken` 走精确计数，否则走**五类字符单价**启发式
  （汉字 52 / 中文标点 150 / ASCII 字母 18 / 空白 6 / 其余 95，单位 1/100
  token），由 7 条黄金向量 + 3 份留出样本真实标定，最大误差 8.0%；上下文窗口
  按模型查表并预留输出位，可用 `{"context": {"max_tokens": N}}` 覆盖；Rust
  侧同步同一模型，与 Python 逐位一致。
- **A3 · 审批四通道 + GUI 缺陷修复**：把「怎么问」从权限策略里抽成
  `agent.approval` 的审批通道（TTY 卡片 / headless 策略 / GUI 弹窗 / 网关），
  策略只管「该不该问」。修掉两处**静默降级**缺陷：
  1. 桌面端 `acceptEdits` 未被识别 → 静默回退 `ask` + 非交互 → Level 1+
     写操作**全被拒**（桌面端完全写不了文件，且没有任何报错）；
  2. 反方向更危险：`--permission=SAFE` 这类大小写变体同样被判非法 → 静默
     降级成 `ask`，用户以为只读、实际拿到"可批准写操作"的权限。
  现模式解析收敛到单一实现处，未知模式**显式报错**；无交互环境新增
  `agent.headless_write_policy`（`deny_all` 默认 / `allow_list` /
  `auto_within_workspace`），非法配置一律回落 `deny_all`。
- **A4 · CI 门禁收紧**：评测（`--srs` / `--ragas`）进 CI，用提交的夹具日志跑通
  0/1/2 三条路径（样本不足告警跳过、不达标判红）；`rust-ext` 去掉
  `continue-on-error` 转为硬门禁并补上"产物装上真能用"的验证；macOS 的
  pymupdf 规避补告警留痕与跟踪说明。

### 🐛 审查消缺（agent / CLI / 看板 / 运维 / 导出侧）

- **agent 内核**：MCP reader 句柄回收（含 `stop()` 2s 上限 join，防滞留）、笔记只读锁兜底、
  异常体内的导入、`chsi_code` 置空。
- **CLI 与网关**：密钥明文打码、`webhook_token` 全链路透传、`/submit` 真正落地、
  `/save` 尾随备注容忍（`/save <备注>` 按首 token 路由）、`chunk_text` 死循环。
- **组卷引擎**：白名单题源门禁从「免责声明」改为「真门禁」—— 无真实题源时拒绝组卷并给出上手引导（`success=False`），
  题源不足时降题量并卷首声明缺口（`shortfall > 0`），占位题仅在 `--allow-placeholder` 下产出且逐题标注来源；
  题源顺序修正为 错题本 → 白名单真题卡 → 大模型变式 → 占位题，全流程 `origin` 标签供判分端区分。
- **看板前端**：4 项真实缺陷（取数 / 渲染 / 前端契约）。
- **运维与文档**：git 闸门收紧、部署校验补页面、院校库口径统一为「57 所详细档案 + 1,841 所基础名录」。
- **导出侧**：发布副本排除私有工具测试与原始快照 / 运行时向量库；补齐公开副本的占位模块适配；
  目标目录环境变量改为全大写 `KAOYAN_PUBLISH_DST`（旧名 `KAoyan_PUBLISH_DST` 仍兼容）。
- **测试中性化**：仓库内不再出现真实院校身份（`tests/test_agentic_research.py` 等样本改为非身份院校）。

### 🔁 CI 回归矩阵

- 逐作业定位并修复公开副本 CI 矩阵的 5 处根因：`llm_client` 别名自注入缺父包属性、
  测试伪造全局 `os.name`、剪贴板用例缺 Windows 平台守卫、批量启动用例依赖解释器探测、
  macOS 侧卸载 `pymupdf` 与无依据的 Qt `ignore`。
- **本机环境加固适配（F5）**：加固主机常见的两类配置会让套件在本机误报，
  CI（GitHub runner 默认环境）则全绿 —— 属环境差异，非产品缺陷：
  1. `NoDefaultCurrentDirectoryInExePath=1` 时 `cmd /c GUI.bat` 裸名无法从 cwd 解析
     → 3 个批处理启动用例改走显式相对路径 `.\<脚本>`（cwd 仍为仓库根）；
  2. Git `usr\bin` 未加入 PATH 时 `cat` 不存在 → 2 条沙箱阴性对照按 `shutil.which`
     显式跳过（该路径由 CI 的 Linux/macOS 作业覆盖）；
  3. `sync_publish.py` 补上与 `update_dashboard.py` 同口径的 stdout UTF-8 重配置，
     本机 cp936 控制台下子进程输出断言不再因乱码误判。

### 📚 文档与实现对齐

- **补齐文档已宣称、REPL 未实现的斜杠指令**：
  - `/save` —— 一键把当前题干与错因记入错题本（与数字快捷键 `/2` 同源）；
  - `/menu` / `/tui` —— 退出 REPL 并打开 TUI 终端全景导航（与 CLI 侧 `ky menu` / `ky tui` 同一入口，别名集两端对齐）。
- **修正与实现不符的命令与选项**：考纲对比统一为 `ky fetch diff --old=<旧> --new=<新>`；
  `ky ingest` 用 `--subject=pro`（无 `--save`）；`ky compose` 去掉不存在的 `--type=choice`；
  `ky config` 菜单项改为实测 7 项；区分 CLI 的 `ky view` 与 REPL 内的 `/view`。
- **修正过期数字与版本**：TUI 版本号改为动态读取（不再写死 `v2.5`）、测试计数
  （1637 → 1686、1206 → 1255）、Python `3.8+` → `3.10+`、看板「5 Tab / 五大」→「6 Tab / 六大」。
- **修正架构树失效路径**：`data/universities/school_data/`（不存在）、`docs/experiences/`
  （已迁至 `.memory/experiences/`）、`tools/skills/syllabus_diff.py`（真实位置在 `tools/intelligence/`）、
  `variant_retrieval.py` → `variant_retriever.py`；并补列 `tools/accel|cli|search|tui` 与
  `docs/BOT_INTEGRATION_GUIDE.md`。
- **看板 `SETUP.md` 的 `SECTION_MAP` 整节改写**为真实存在的
  `05-考研看板/web/config.py` 的 `SECTIONS`，并补充排错提示：
  `memo` / `weak` / `stat` 章节必须写成标准 Markdown 表格，否则卡片会被**静默丢弃**。
- **`docs/BOT_INTEGRATION_GUIDE.md`** 补 `ky serve --webhook-token=<密钥>` 的显式参数示例。
- **新增守门测试**：`/save`、`/menu`（含 `/tui` 别名，参数化覆盖）的正向用例 + 阴性对照（拼错指令不得触发）。

### 🧪 测试与验证

- **F1–F8 本轮消缺回归**（评审后修复，见下方「第二轮审查消缺」）：
  新增/加固 8 条守门用例（`/save 尾随备注`、批处理显式相对路径、`cat` 缺失显式跳过、
  判负幂等三级判重、网关半开连接写保护），并修正 `sync_publish.py` 的 stdout 编码口径。
- **判断「工作区是否被测试污染」只认 `sha256`，不要用 `mtime`**：本地全量套件
  `tools/test_ky_suite.py`（私有工具，发布副本不含）收尾会「还原测试现场」（重写 23 个用户数据文件、清理 8 个测试残留），
  故 `ky_config.json` 的 `mtime` 每次跑完都会刷新，而内容 `sha256` 不变（实测恒为
  `ed4684e2119dbbbc`）。同理 `git status` 也无效——被 `.gitignore` 忽略的文件其改动永远不报告。

### 🔒 隐私口径再对齐（2026-09-24 检查补漏：导出 / 打包三路径同口径）

- **导出层与 `.gitignore` 补漏**：`00_考研全科总战役规划.md`（个人学情）、
  `05-考研看板/docs/`（看板构建产物）、`scripts/gui_shots/`（含真实界面截图）、
  `04-专业课/演示样例/`，以及 `双校考情对比_*.md` / `目标院校情报_*.md` / `20XX考纲_*.md`
  三类研报与考纲生成物 —— 此前只被 `.gitignore` 保护，而导出走**文件系统遍历**，
  一次导出即进公开副本。现按三层清单（顶层文件 / 路径前缀 / 路径+文件名通配）
  收敛到 `tools/privacy_policy.py`。
- **打包骨架部署补漏（dist 实测）**：`deploy_workspace_skeleton()` 此前只套
  `_is_junk()`，`data/universities/_sources/`（未授权汇编的原始快照）、
  `exam_subjects.json`、看板构建产物、研报/考纲生成物被原样复制进发布包根目录；
  现新增 `privacy_policy.is_local_artifact()` 统一判据，导出 / staging / 骨架部署
  三条路径同口径。
- **测试适配**：`test_ky_suite` 的 exam smoke 与教学闭环用例适配白名单题源门禁
  （无真实题源时组卷被拒、题源不足时降题量），新增 `--allow-placeholder` 契约断言；
  `test_fix_packaging` 的对照组改用 `考试大纲.md`（`20XX考纲_*` 已全量剔除），
  并新增本地产物剔除断言（staging 与骨架部署两侧）。
- **看板文档**：`05-考研看板/README.md` 的资产引用补 `../` 前缀（此前 GitHub 上
  相对路径 404）。
- **计数校正**：pytest 1255 → 1268（1265 通过 + 3 跳过）、ky_suite 304 → 305、
  合计 1686 → 1700（README / CONTRIBUTING / SETUP / 看板 README 同步）。

---

## 第二轮审查消缺（OCR 全量代码审查，2026-09-22）

> 对工作区未提交改动 + 最近 6 个提交批次做逐文件审查，并真实执行
> `lint_check` / `pytest tests/`（1255 项）/ `test_new_features`（130 项）/
> `check_dashboard` / `test_ky_suite`（干净副本）后的修复清单。

| 编号 | 位置 | 类型 | 修复内容 |
| --- | --- | --- | --- |
| F1 | `tools/sync_publish.py` | 编码 | 补 stdout/stderr UTF-8 重配置：cp936 控制台下 `sync_publish --force` 的「拒绝导出」文案此前按 GBK 输出，测试按 UTF-8 解码即误判 fail-closed 失效（门禁本身 exit 3 正确）。 |
| F2 | `tools/sync_publish.py` | 跨平台 | 发布目录环境变量改全大写 `KAOYAN_PUBLISH_DST`（Linux/macOS 大小写敏感，旧混合大小写名曾静默失效）；旧名保留兼容读取，报错文案同步更新。 |
| F3 | `tools/agent/mcp_client.py` | 资源回收 | `stop()` 增加 2s 上限 `join()`：此前只把 `_reader_thread` 置 None 不回收，进程 terminate+kill 双失败时旧线程滞留。 |
| F4 | `tools/cli/repl/loop.py` | 健壮性 | `/save <备注>` 按首 token 路由（此前尾随文本被挤进「未知指令」），并补回归用例。 |
| F5 | `tests/test_launcher_diagnostics.py`、`tests/test_fix_agent_sandbox_and_ssrf.py` | 测试可移植 | 批处理用例改显式相对路径 `.\<脚本>`（加固机 `NoDefaultCurrentDirectoryInExePath=1` 下裸名不可解析）；沙箱阴性对照在无 `cat` 的机器上显式跳过（而非 WinError 2 假失败）。 |
| F6 | `README.md`、`CHANGELOG.md` | 文档 | 测试计数按实测校正：pytest 1245→1252 通过、合计 1679→1686；补充「无 cat 裸机」跳过项说明。 |
| F7 | `tools/cli/gateway.py` | 健壮性 | `/api/clear` 与 `/webhook` 响应写入加半开连接保护：客户端超时先断时不再抛 `ConnectionAbortedError` 刷 stderr（业务已处理完毕）。 |
| F8 | `tools/skills/exam_grading.py` | 幂等 | 判负归档前新增三级判重（精确标题 / 题干预览 / 题干指纹，`_find_existing_mistake_record`）：同一题整卷重判不再重复新建错题、不再重复污染 FSRS 队列与错因统计；扫描异常时回落既有新建路径（宁可重复，不破坏闭环）。 |

已验证：`lint_check` 0 错误；`pytest tests/` 全绿（含上述新增用例）；
`test_new_features` 130/130；`test_ky_suite` 干净副本 299 通过 / 0 失败 / 5 跳过（F1 修复前为 298/1/5）。

---

## [2.8.0] — 2026-09-20

### 📦 开箱即用程序包（本版本最重要的变化）

- 新增**开箱即用的程序包**（PyInstaller 打包 + Inno Setup 安装向导）：
  **不懂技术、没装 Python 的用户也可以直接下载 Release 包，跟随界面引导完成配置后使用**。
  - Windows 安装版：`KaoyanStudyChain_Setup_v2.8.0.exe`（图形安装向导）
  - 免装目录：`KaoyanStudyChain-v2.8.0.zip`（解压即用）
- 首次启动引导会依次完成：**填写大模型 API Key → 选择报考院校/专业/科目 → 设定每日时长与作息**。
- 程序包是**脱敏的通用包**：不含任何人的报考方案、学情数据与 API Key，首次运行需自行配置。
- 新增 `--keep-identity` 逃生舱：给自己打「带个人报考方案」的私人包时使用。
- 构建命令加 `--specpath=build/`：PyInstaller 自动生成的 `.spec` 落到 gitignore 的 `build/`，
  不再覆盖仓库里手写的「路径无关」`KaoyanStudyChain.spec`。

### 🔒 打包产物的内容级身份脱敏

- 产物按 `tools/privacy_policy.py` 的规则改写 `*.md` / `*.html` / `*.svg` / `*.py` 里的
  真实校名、报考专业、自命题科目与学情薄弱点；脱敏后校验 `.py` 仍可编译，
  并对整个产物（含 `_internal/`）做残留自检，**检出真实身份即中止构建**。
- 修掉两个真实构建中才暴露的问题：
  - 规则表文件（`tools/privacy_policy.py`）被自己的规则改写导致**规则自毁**；
  - 产物把同一份内容同时放在根与 `_internal/` 下，导致公开库的豁免判据失效、
    **构建被自己的自检挡下**。
- 脱敏引擎与规则表收敛到 `tools/privacy_policy.py`（**单一事实源**），
  打包路径与发布副本路径共用同一套实现，避免两条出口口径漂移。

### 🔧 版本与文档

- 版本号统一升至 **2.8.0**（`pyproject.toml` 为真源，CLI / TUI / GUI / doctor 均动态读取）。
- 公开文档补全：**免责声明**、**文档地图**、**系统要求与依赖矩阵**、**CHANGELOG**，
  并修正过期徽章与不完整的目录结构说明（详见 `README.md`）。

---

## [2.7.0] — 2026-09-20

### 🔒 隐私与安全（本版本主线）

- **脱敏规则不再静默失效**：导出公开副本时，若 `ky_config.json` 缺失、`study_plan` 为空
或校名被冲成占位值，构建/导出会**直接报错中止**，而不是「照常跑却一个字没改」。
- **补齐 URL 百分号编码形态的脱敏**：此前链接文字已脱敏、但 `href` 里的
`%E6%B2%B3...` 这类编码形态原样保留，一解码就还原。现规则同时覆盖原文与解码后的文本。
- **动态身份规则由 26 条扩到 32 条**：新增学情自由文本字段（薄弱点 / 摸底水平 / 资料白名单 /
备选院校）的替换规则；同时加**通用值闸门**，`无` / `计算失误` / `不考数学` /
`暂未放置实体资料…` 这类公开推荐值与正文用词不会被误替换。
- **safe 模式改为 fail-closed**：只读闸门以命令注册表的 `write` 元数据为唯一事实源，
未注册命令一律拒绝（旧实现是手写黑名单 + 兜底放行）；修好 `tools.ky_io` / `ky_io`
双别名导致闸门在两个模块对象之间失效的问题。
- **发布副本导出幂等**：改为先清空目标目录再镜像，并保留 `.gitignore` 之外的
`.git`。修掉「重复导出文件数只增不减」与重名计数器逐轮累加。
- **补齐根级残留目录排除**：`.cargo_target`、`.git_broken_backup` 这类本机目录
此前从未进过任何排除名单，每次导出都被镜像进公开副本。

### 📦 打包与发布物

- **打包产物新增内容级身份脱敏**（默认开启）：
按 `tools/privacy_policy.py` 的规则改写产物里 `*.md` / `*.html` / `*.svg` / `*.py`
的真实校名、报考专业、自命题科目与学情薄弱点；脱敏后校验 `.py` 仍可编译，
并对整个产物（含 `_internal/`）做残留自检，**检出真实身份即中止构建**。
    - 给自己打「带我的方案」的包：加 `--keep-identity` 跳过。
    - 个人运行时配置（`ky_config.json` 等）本就不随包分发，不再出现真实 API Key。
- 构建命令加 `--specpath=build/`：PyInstaller 自动生成的 `.spec` 落到 `build/`，
不再覆盖仓库里手写的「路径无关」`KaoyanStudyChain.spec`（此前每次构建都会弄脏工作区）。
- 脱敏引擎与规则表收敛到 `tools/privacy_policy.py`（**单一事实源**），
打包路径与发布副本路径共用同一套实现，避免两条出口口径漂移。

### 🏛️ 研招情报

- 新增**全国高校数据库**（1841 所）；降级引擎改为**诚实标注**（不再谎报来源）。
- 考情雷达脱敏修正；研招网数据获取改为只使用可用入口（院校库列表页）。

### 🖥️ 桌面端 GUI

- 院校侦察与双校对标全面异步化，避免主线程卡死。
- 对话栏新增图片与文件上传，打通 `/img` 视觉批改与 `/file` 考纲挂载。
- 修复 QSS 白框、快捷药丸联动、思考流式显示等问题。

### 📟 CLI 与考纲

- CLI 重构为子命令模块（表驱动注册表，当前 **39 个子命令**）。
- 「不考数学」贯穿：占位大纲判定、清洗幂等、文案与看板图表一致。
- 数二 / 数三禁区清单两侧对齐；否定语境误拦修复。
- safe 模式不再写 `ky_config.json`；`ky doctor` 探活参数与错误透传修正。

### 🧪 测试与工程

- 自动化测试合计 **1279 项**：`tools/test_ky_suite.py` 304 项断言 +
`tools/test_new_features.py` 130 项 + `tests/` pytest 845 项（837 通过 + 8 跳过）。
- 修掉套件在配了真实 API Key 的机器上会**发起真实计费 LLM 调用**导致超时/崩溃的问题：
相关用例改为确定性的离线分支。
- 修掉 LLM 返回值形状不受约束导致 `ky compare` 崩溃的问题（边界类型收口）。

---

## 如何升级

### 程序包用户（直接下载 Release 的用户，推荐）

到 [Releases 页面](https://github.com/moyetian/kaoyan_chain/releases) 下载新版本程序包，
**覆盖安装**即可。你的 `ky_config.json`（API Key 与报考方案）与学情数据都保存在
**你自己的电脑上**，不会被安装包带走或覆盖。

### 源码用户

在项目根目录执行：

```bash
# 1. 拉取最新代码（先确认自己的改动已提交或备份）
git pull

# 2. （可选）若 requirements.txt 有变化，重新安装增强依赖
python -m pip install -r requirements.txt

# 3. 重新体检，确认环境、协议与 Git 隐私隔离都正常
python tools/ky_cli.py doctor
# 或 Windows 双击 ky.bat 后输入 doctor
```

> ⚠️ 升级不会改动 `参考资料/`、`_状态/`、`错题本/` 等个人学情数据；
> 但跨版本升级前建议自行备份 `ky_config.json`。

### 从 2.6.x 升级的注意事项

- 打包产物默认会做**内容级脱敏**。若你需要一份带个人报考方案的本地包，
记得加 `--keep-identity`。
- 导出公开副本时，若看到「脱敏规则无效」并中止，请检查 `ky_config.json` 的
`study_plan.school` / `major` / `pro_name` 是否被冲成了占位值。

---

## [2.6.0]

- v2.6 五大系统升级：UI 高对比度重构、双专业课 199 管联、管科综合架构扩展、
HTTP 400 容错与 GZIP 解压、LLM 真实注入。
- 全量 449 项测试通过。

> 更早的变更未单独归档，可参考仓库提交历史 `git log`。

