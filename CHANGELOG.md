# 更新日志 (CHANGELOG)

本文件记录**考研学习链 (Kaoyan AI Study Chain)** 的版本变更与升级方式。
版本号以 `pyproject.toml` / `tools/version.py` 为准，亦可运行：

```bash
python -c "import sys; sys.path.insert(0, 'tools'); from version import get_version; print(get_version())"
```

---

## [3.1.2] — 2026-10-01 ~ 10-02（未发布）

> 自 v3.1.1 以来的累积更新：Agent 内核修复重构（K1–K9）、2026-09-30 安全审查
> 全量修复（P0×3 / P1×11 / 性能项）、Teal 玻璃风 UI 重构（GUI / Web / TUI）、
> 多角色沙箱实测消缺（UT3 / UT4）与 2026-10-02 第二轮审查加固。版本号真源
> `pyproject.toml`。

### 🤖 Agent 内核修复重构（K1–K9）

- **K1 判卷口径一致**：`total_score` 与 `pass_rate` 改为同分母（`graded_max`），
  新增渲染前一致性校验（违规只告警不丢报告）；CLI 失败分支改读 `msg` 键
  （旧实现死分支会吞真实原因）；
- **K2 版本工程**：`build_package` / CLI 兜底不再伪装具体版本号
  （统一 `0.0.0+unknown`）；新建 `tools/check_version_consistency.py`
  （pyproject.toml / installer.iss / 运行时三处核对）并接入 CI 与 `ky doctor`；
  修复冻结环境下 `_MEIPASS` 版本读取回落（v3.1.1 安装版曾读不到版本）；
- **K3 session_log 降级与小项清理**：写失败改为有界内存队列（512）+ 周期性
  重放；`knowledge_store` 启用 WAL；`material_ingestion` 未知科目显式报错；
  删除 FSRS 死类；
- **K4 LLM 统一客户端**：六套 HTTP 客户端（loop / engine / open_grader /
  vision_solver / study_planner / llm_client）收敛为 `request_chat` 单一入口；
  结构化异常体系（可重试 / 确定性 / 响应超限 / 空流）+ 退避公式与 Retry-After
  逐字保持既有行为；
- **K5 工具注册表分级**：`ToolDefinition` 增 tier / source 分级；
  `skill_bridge` 把 6 个技能模块桥接为注册工具（不改技能本体）；新增
  `ky tools` 审计命令（CLI 子命令 **42→43**）；
- **K6 KaoyanContext 统一上下文**：frozen dataclass 收敛科目 / 数学编码 /
  目标校 / 初试日等散落 20+ 文件的读取；新增 `school_scope_guard` 院校范围
  守卫（校名别名归一，原则「误放行 ≫ 误阻断」）；
- **K7 RunLoop 扩展点**：新增 PREPARE_NEXT_TURN / PREPARE_REQUEST / FINISH_TURN /
  FINISH_RUN 钩子；W10/W11 拦截计数迁 `block_streak_guard`（对照快照逐字段
  一致）；`AfterCompact` 事件复活；GUI 关窗触发 SessionEnd（幂等）；
- **K8 TurnRecovery**：doom-loop 熔断（同签名连续 3 次跳过执行并引导改道）；
  错误分类分流（401/403 跳过收尾链、上下文超长强制 compact 重试一次）；
  工具输出预算单一真源（超限截断 + 完整原文落盘 `.memory/tool_outputs/`）；
- **K9 运行控制平面**：`RunRuntime` 生命周期状态机 + 四维预算（步数 / 时长 /
  工具调用 / Token，`agent.runtime` 配置项）；试卷**内容寻址身份注册表**
  （`PAPER-<hash>`，入库与组卷不再靠时间戳关联）；`ky doctor` 新增持久化
  状态完整性检查；网关 / 网页对话改走统一 LLM 客户端。

### 🛡️ 2026-09-30 安全审查全量修复（P0×3 / P1×11 / 性能项）

- **P0-1 数学验算白名单解析**：`math_verifier` 16 处 `sympify` 调用点全部改为
  白名单安全解析（杜绝任意代码执行），`verify_math` 提级 SHELL_EXEC；
- **P0-2 测试执行纳入写入闸门**：`pytest` / `unittest` 纳入「本会话写入脚本」
  闸门与受控目录白名单；
- **P0-3 网关跨站闸门**：Origin / Sec-Fetch-Site 校验 + ACAO 精确回显 +
  Referrer-Policy + 413 体积上限；
- **P1×11**：沙箱授权目录 `is_relative_to` 边界判定、safe 模式判定前移、
  `ky_config.json` 写保护与 MCP 工作区归属校验、git 配置注入收口、
  `run_command` 超时上限、出站收敛至 `net_guard.safe_urlopen`（14 处）、
  网关并发上限、Windows 权限收紧（icacls）、内联 JSON 一次性转义、
  看板直推链路内容级脱敏 + 残留自检硬阻断；
- **性能项**：GUI 状态读取 mtime 指纹缓存、残留扫描分块解码、脱敏规则
  预编译等 6 项（语义逐字节不变）。

### 🎨 Teal 玻璃风 UI 重构（GUI / Web / TUI）

- 浅色 / 深色预设升级为 **Teal 蓝绿主色 + 毛玻璃质感**（WCAG AA 对比度验算，
  新增玻璃派生 token，高对比预设显式不透明）；
- GUI：rail 双态折叠（252↔56px）、不对称圆角气泡、情报页指标卡双栏、
  空状态居中；**顶栏倒计时改按 `exam_date` 现算**（修复配置快照陈旧）；
- Web 看板与 TUI 同步换色，全项目内联样式清零。

### 🩹 多角色沙箱实测消缺（UT3 / UT4 / 5 角色）

- UT3 三角色（材料力学 / 应用数学 / 经济学）：P1×5 + P2 逐条修复
  （含隐私中性化补漏与图谱分隔线回归修复）；
- UT4 三角色（通信四科 / 物理自命题 / 西医综合）：P1×6 + P2×8 修复
  （含 REPL 斜杠指令被 MSYS 管道改写的真因修复）；
- 5 角色实测 13 项缺陷修复（随 UI 重构批次）。

### 🔐 2026-10-02 第二轮审查加固（安全 / 稳健性 / 体验）

- **出站收敛补全**：QQ OneBot 从裸 `urlopen` 例外改为 `safe_urlopen(allow_loopback=True)`
  窄通道（仍拒绝私网 / 链路本地 / 云元数据）；企业 DNS 劫持兼容改为显式开关
  `KY_ALLOW_BENCHMARK_DNS`（默认关闭，防 SSRF 绕过）；微信检索 / 研究引擎 / 研招抓取 /
  模型探活等全部响应读取补上体积上限；`lint_check` 新增两条 AST 门禁（禁止裸
  `urlopen` 与裸 `response.read()`）；
- **MCP 收紧**：`npx` / `uvx` 默认拒绝（需 `allow_remote_packages=true` 显式放行）；
  拦截运行时注入环境变量（`PYTHONPATH` / `NODE_OPTIONS` / `LD_PRELOAD` 等）；
  「本会话记住」按完整工具名收口（不再 `mcp_*` 全局放行）；
- **沙箱与提示注入防线**：新增符号链接组件 TOCTOU 检查（写前逐组件 `lstat`）；
  `AGENTS.md` 与 `.memory` 禁止 Agent 写入（切断「写提示词 → 下轮进系统提示」链）；
  工具结果统一加「不可信数据」围栏；`run_command` 含路径可执行文件必须解析到
  PATH 受信程序（或当前解释器）；
- **网关**：`/webhook` 短窗口限流（30 次 / 60s）+ 请求 ID 去重（防重放）；回调密钥
  优先 `X-KY-Webhook-Token` 请求头（查询参数仅兼容旧平台）；非回环地址无鉴权拒绝启动；
- **隐私**：会话日志与 LLM 错误体敏感值脱敏（token / 邮箱 / 手机号）；会话日志
  保留策略（30 天 / 最多 100 文件）；公开快照不再把自命题科目全称写回（防绕过泛化
  防线）；诊断元数据只保留稳定键；`real_name` / `qq_target_id` 纳入身份脱敏；
- **GUI**：默认权限模式改回最小权限 `ask`（与 CLI 一致，写操作走审批通道）；
- **看板**：本地完整模式产物隔离至未跟踪 `docs/.local/`
  （`KY_DASHBOARD_OUTPUT_DIR`），`docs/` 保持脱敏示例快照；
- **修复与体验**：复制版真题 `**第1题（4分）**：` 格式正确分题（不再把下一题
  污染进上一题答案）；双校对标显式专业优先于工作区配置；组卷校验改用原始题干
  （消除 ingest 合法卡片被误判篡改）；`lim(x→0)` 自然书写归一化；SymPy 惰性加载
  （`import skills` 不再拉起重依赖）；新增 312 心理学预设与北师大心理学部档案；
  招生雷达并行抓取 + 15 分钟短缓存（`CACHED` 状态）；robots.txt
  `Disallow` / `Crawl-Delay` 支持；`ky_io` 锁路径缓存与写前体积短路。

### 📄 文档

- README 程序包体积数字回填 380→415 MB（v3.1.1 实际打包 413.9 MB）；
- 命令矩阵与速查同步 **43 项**子命令；
- README 下载文件名同步 v3.1.2、微信检索口径、招考情报引擎说明与看板双模式
  （`docs/.local/`）说明更新；BOT_INTEGRATION_GUIDE 回调密钥改为请求头优先。

**测试**：全量 pytest **2870 通过 + 4 跳过**（收集 2874）；
`test_ky_suite.py` **307 项**（Git 口径 307+0）；`test_new_features.py` **131 通过**。
新增 `tests/test_round2_audit_fixes.py`（第二轮审查窄回归）。

## [3.1.1] — 2026-09-29（当前发布版本）

> 自 v3.1.0 以来的累积更新：作答质量与评测批次（W1–W13）、发布链路隐私补漏与
> 本地完整 / 发布脱敏双模式、四端界面升级，外加收尾答案恢复修复与多轮实测消缺。
> 版本号真源 `pyproject.toml`。

### 🩹 三端实测 12 条修复（P0×3 / P1×5 / P2×4）

以护理考生模拟实例对 CLI / TUI / GUI 三端做全链路实测，逐条复现后修复：

- **P0-1 TUI 导入崩溃**：`tools/intel_imports.py` 收敛研招模块导入为单一真源
  （此前 TUI 直写 `from intelligence import ...`，包布局变化即 ImportError）；
- **P0-2 `ky compare` 卡死**：离线 `--quick` 分支不再触发在线检索（超时兜底）；
- **P0-3 `init_workspace --help` 误落盘**：`--help` 不再执行副作用初始化；
- **P1-4 `ky scout` 传参**：TUI 入口透传 `--school1/--major` 不再丢失；
- **P1-5 考纲 Diff 命名**：生成文件名随实际科目（英语 → 「全国统考_英语」），
  专业课无目录时走待核验口径；
- **P1-6 `ky status` 真源**：资料白名单标题与目标矩阵行均按 `study_plan` 同源
  重建（占位形态重建标题、真实书目原样保留；改科目后矩阵与白名单不再自相矛盾）；
- **P1-7 研招雷达降级**：`chsi_url` 为占位域名时仍可监控并标注「未核验」；
- **P1-8 `ky mount` 副作用**：默认只读盘点，写回必须显式 `--apply`（此前
  0 份资料也会偷改白名单与雷达）；
- **P2-9 微信检索口径**：`--no-fetch` 报告显式列出「检索源」清单；
- **P2-10 空题库退出码**：TUI 组卷无题源时 EXIT=2 并给出「未组卷」文案；
- **P2-11 GUI 双校对标**：第二校预填档案中的备选院校（此前恒为空）；
- **P2-12 医学门类预设**：`ky subject` 支持护理等专业代码预设与大纲骨架。

### 🔒 发布链路隐私补漏（推送前全量审查）

- **`.checkpoint/` 写前快照整棵排除**：`privacy_policy.DEV_SCRATCH_DIRS` +
  `sync_publish.EXCLUDE_DIRS` 双层（快照 json 内嵌本机绝对路径、含被改文件
  内容副本，且 json 不走内容脱敏 —— 此前会被镜像进公开副本）；
- **《双校对标》研报整类排除**：进 `NON_PUBLISH_PATH_PATTERNS`（正文含对比
  院校真实域名，脱敏规则只覆盖学员身份院校），与同族 `双校考情对比_*` 同口径。

**测试**：全量 pytest **2346 通过 + 3 跳过**（收集 2349）；
`test_ky_suite.py` 305 项（副本口径 300+5）；`test_new_features.py` 131 通过。
新增 `tests/test_w12_report_fixes.py`（10 项）与
`tests/test_fix_checkpoint_exclusion.py`（14 项，含两次单点变异阴性对照）。

### 🧪 W13 技术侧 —— 评测资产与检索（W13-0/1/2/3）

- **W13-0 评测口径统一**：C1 / C2 评测集条数口径三源不一已统一，以数据文件实际行数为
  唯一真源 —— C1 考纲守卫 = **137 条**（`tests/benchmarks/syllabus_guard.jsonl`）、
  C2 引文忠实度 = **109 条**（`tests/benchmarks/citation_faithfulness.jsonl`）；
  README 与评测流水线文档中的旧值（128 / 108 条）同步更正，并新增**派生计数测试**
  （读 jsonl 实际行数断言文档字符串）防再次漂移。v3.1.0 发布段中的历史数字保持原样。
- **W13-1（R2）C2 双指标契约化**：新增 `citation_recall`（拦截侧通过率）与
  `citation_precision`（放行侧精度），附**置零规则**（`recall=0 ⇒ precision=0`，
  防「全放行」0/0 虚高）；`by_category` 按实际 kind 五类分层通过率 + `judge_identity`
  （`rules:citation_engine@3.1.0`）+ 构念声明（闸门功能口径，禁止与 ALCE 生成侧
  recall/precision 横向比较）。
- **W13-2（R1）canary 防污染**：两评测集 246 行**逐行**加 `_canary` 字段（GUID 逐字符
  一致，禁写注释行）；`.gitignore` + `privacy_policy.NON_PUBLISH_PATH_PATTERNS`
  双保险排除（sync_publish 不读 .gitignore）。边界：canary 只防「被爬进训练语料」，
  ≠ 防污染达成；影子集机制留 W14。
- **W13-3（R5-a）切片去重叠开关**：`search/indexer.py` 的 `chunk_text()` 参数化 ——
  `KY_RAG_OVERLAP=0` 启用 `overlap=0`，**默认 50 行为不变**；chunk_size 保持 500
  （不做「512 对齐」，那是英文 token 口径）。阴性断言：各 chunk 拼接 == 原文。

### 🎨 W13 UI 侧 —— 四端可见升级（W13-4/5/6/7）

- **W13-4（U1）结构 token 补档**：新增圆角 `r-xl / r-md / r-pill`、间距
  `space-7 / space-8`（**整档跳过 20**，偏差表入 DESIGN.md）、`sh-3 / hair / ring`
  合成值、字阶 `fs-xs / fs-md / fs-2xl / fs-3xl`、`font-num` 等宽数字栈 ——
  **只补缺档、不改任何现值**（值收敛留后续批次）。
- **W13-5（U2）`shield` 图标入库**：Web sprite 42→43 symbol（`icons.py --check`
  43 个全部在位；GUI 走独立图标库，零改动）。
- **W13-6（U3）看板 6→5 Tab**：底栏收敛为 今日 / 必背 / 错题 / 进度 / 考情 五键，
  知识图谱下沉为进度页二级入口（保留 `data-p="map"` 字面量契约，不带 `role=tab`）；
  JS 五触点统一走 `tabBtn()`；**双产物同一次 build 运行内 sha256 相等**
  （重建前「85 vs 82 天」漂移已消除）。
- **W13-7（U4）命令面板四桶分组**：Ctrl+K 面板按 日常 / 自测 / 情报 / 系统 分桶
  （标题行不可选中、键盘流跳过）；**42 命令覆盖边界显式声明**（GUI 可达 10 /
  不可达 32，audit 测试与 CLI 注册表逐一对账）。

### 📝 W13-8（U6）文档收口

- 命令数 39→42 修正（实测 CLI 注册 42 个主命令；操作手册第 8 章表格同步补
  `gain` / `rag` / `session` 三行）；测试计数徽章更新至 **2816**（验收修复前口径）；
  `DESIGN.md` 补信息架构词表（命令面板四桶 / 看板 5 域）与图标清单 42→43。

### 🔐 W13 验收修复 —— 本地完整 / 发布脱敏双模式 + 易用性 6+4

四路用户视角实测（CLI / TUI / GUI / Web）发现 1 P0 + 10 P1 + 25 P2，逐条核实后按
「必修 6 + 顺手 4」修复；收口时全仓扫描再补 5 处同族缺陷。

- **P0 本地完整 / 发布脱敏双模式**：本地入口（`更新看板.bat` ×2、`ky build`、
  `update_dashboard` 本地分支、`init_workspace`）此前不设 `KY_SNAPSHOT_OPT_IN`，
  走缺省脱敏 —— 考生本机看板看不到今日任务正文与卡背答案。现本地入口一律
  **完整模式**（env=0）；发布链路（`sync_publish` / `update_dashboard --push` /
  deploy-pages）**强制脱敏**：镜像/推送前自动脱敏重建、事后恢复本地完整版；
  产物注入机器可读标记 `data-sanitized="1"`，CI 三道闸（文件 + 标记 +
  `meta.sanitized`）拒绝完整模式产物上线。
- **收口补漏（5 处同族重建点）**：全仓扫描发现 `check_dashboard` 守卫 / TUI
  build 动作 / `study_planner` 引导 / REPL 两处口令（「更新看板」与 `/build`）
  也直调 build.py 且未传 env —— 跑一次守卫即把完整产物覆盖为脱敏版。全部统一
  完整模式 + 新增 AST 级回归测试（含阴性对照）。
- **CLI-1 `ky help` 与注册表脱钩**：help 手写 37 条 vs 注册 42 条 → 改为从
  注册表动态生成 + diff 转发说明；`register()` 按 name 判重（修双路径导入
  产生的 9 条重复行）。
- **CLI-2 未知指令推荐错**：不考数学时 `/math` 类指令明确拒绝；未知指令提示
  「输入 / 展开指令大盘」+ 动态科目串。
- **CLI-3 `./ky` 探测不足**：逐个候选验证 `version_info >= (3,10)`，全失败给
  ky.bat 对齐指引；`./ky --version` 从 exit 49 零输出 → 正常输出。
- **GUI 命令面板空结果**：零提示 → 「未找到匹配命令，试试：错题 / 组卷 / 看板」；
  口语别名（"看板" 等）并入匹配。
- **Web 错题页签默认 deck 错位**：错题重做队列/索引置顶（此前首个 deck 是
  模块掌握度雷达）。
- **顺手 4 组**：GUI 工具项关键词 / 备考天数估算标注（「按初试前 180 天估算」）/
  CLI 一行 P2 组（`ky menu` batch、search usage 解释器提示、`/paste` 补档、
  TUI `[0-10]`）/ 文档残留（操作手册 `ky commands`、SETUP 5 Tab、README 编号）。

**测试**：全量 pytest **2441 通过 + 3 跳过**（收集 2444）；`test_ky_suite.py`
**307 项**（Git 工作区口径；副本口径 302+5）；`test_new_features.py` 131 通过；
`ci_evaluate_gate` 三路径全绿；`lint_check` 0 错误；`check_dashboard` 全绿
（含真浏览器运行时）；模拟真实用户端到端 **27/27**（双模式四段 + 阴性对照 +
97 个受控文件字节不变）。

### 🩹 Agent 收尾答案 —— 步数耗尽不再返回空串（KaoYanBench 实测 +4.01 分）

- **缺陷**：`AgentRunner.run()` 只在「模型某一步不带 tool_calls」时才把该步
  content 作为最终答复；模型每一步都在调工具时（KaoYanBench core50 实测
  45/50 题如此），步数耗尽后直接返回空串 —— 结构化输出类检查成建制判负。
- **修复**（`tools/agent/loop.py`）：
  1. 步数耗尽 / 模型空回复 → 追加「禁用工具」的收尾指令再请求一次
     （`_call_llm(..., allow_tools=False)`，HTTP payload 不含 `tools` / `tool_choice`）；
  2. 仍无内容 → 回退「最后一条非空 assistant 文本」；
  3. 都没有 → 返回空串（不伪造答案）；
  4. API 硬失败不介入，保持空串，让 GUI/REPL 走各自的「未返回有效回复」诊断提示。
- **测试**：新增 `tests/test_fix_final_answer_recovery.py`（8 项，含 HTTP payload
  层断言 `allow_tools` 开关真实生效）；两组阴性对照（禁用收尾 / 禁用回退）
  分别 5 红、2 红，按预期失败；全量 pytest 1897 通过 + 3 跳过。
- **评测验证**（KaoYanBench core50，同模型同 grader，tag `v3.1.0-recover` 对比 `v3.1.0`）：
  - 空 `final_answer`：**45/50 → 18/50**（27 题修复，0 题退化）；
  - 平均分 **48.35 → 52.36**（+4.01），成功率 **30.0% → 34.0%**（+4.0pp），
    中位分 49.23 → 52.50，P90 80.02 → 85.24；
  - 来源精确率均值 **50.0% → 72.9%**（+22.9pp）；幻觉率维持 0.0%；
  - 门禁 4 项全 PASS（任务成功率 +4.0pp / 幻觉率 0.0pp / 耗时 P90 1.34× / 引用跳过），
    `kaoyanbench regression` 退出码 0；
  - 代价与收益：平均耗时 110.5s → 137.9s（多一次收尾请求），平均 Token
    8,078 → 5,204（收尾请求不带工具定义）；
  - 残留短板：`json_schema`（22/22）与 `numeric`（10/10）仍全灭 —— 收尾答案的
    **格式遵从**（评测契约要求纯 JSON 输出）是下一步改进方向。

### 📦 打包修复 —— 冻结产物版本读取（发布前发现）

- **冻结程序版本显示失真**：`pyproject.toml` 此前未随包分发，冻结环境下
  `tools/version.py` 的回落链（dist-info → pyproject → 兜底）全部失败，
  GUI / CLI / TUI 版本显示一律为 `0.0.0+unknown`（实测旧产物
  `_internal/tools/version.py` 输出确认，v3.1.0 及更早包均受影响）。
  修复 = 把版本真源 `pyproject.toml` 加入 `--add-data` 根文件清单；
  新增回归测试 `test_pyproject_toml_included_for_frozen_version`。

### 🛡️ 发布包 PYZ 层身份残留修复（发布前发现）

- **根因**：`build_package.py` 的 `--collect-all tools` 把**真实源码**编译进
  exe 内嵌 PYZ（zlib 压缩），而内容级脱敏只改产物树里的**文本副本**、改不到
  PYZ —— `tools/` 源码中的身份/学情字面量（首启向导默认值、LLM schema 示例、
  模板默认值、测试探针等 11 个文件 51 处）会以编译形态残留在发布包 exe 中
  （实测 v3.1.0 公开包 9 个模块、修复前 v3.1.1 构建 11 个模块残留）。
- **修复**：11 个文件按出口脱敏**同规则**中性化（本地源码 == 远端脱敏形态，
  逐文件 diff 归零）；免疫断言从 `tests/` 扩展至 `tools/`（新增
  `test_tools_dir_is_immune_to_py_sanitization`，含阴性对照），防回归。
- **影响面**：v3.1.0（实测）与 v3.0.0（同因）的公开发布资产含此残留 ——
  已撤回，v3.1.1 为修复后首个干净版本。

### 🩹 构建可靠性 —— 产物只读属性防护（重构建 WinError 5）

- **根因**：工作区 `01-数学/_状态` 等目录带 Windows ReadOnly 属性，
  `shutil.copytree` 复制进产物后属性继承 —— 下一次构建时 PyInstaller 清理
  旧 `dist/` 报 `PermissionError: [WinError 5]`（不手工清属性则重构建必失败，
  已反复踩两次）。
- **修复**：`build_package.py` 在 PyInstaller 启动前自动递归清除
  `dist/KaoyanStudyChain` 与 `build/` 的只读属性（`_clear_readonly_attrs`，
  非 Windows 平台 no-op）；新增回归测试
  `test_clear_readonly_attrs_unlocks_dir_for_rebuild`。

### 📦 其他累积批次（W1–W11 · 浏览器采集 · D0）

v3.1.0 之后的作答质量与基础设施批次同版发布，主要条目：

- **W1 埋点底座**：会话级 `llm_call` 事件（耗时 / 用量 / 错误分类）；
- **W2 core50 评测入库**：数据集镜像 + 适配层 + CI 门禁（PR 冒烟 / 夜间全量）；
- **W3 headless 契约**：受控放行行为契约（点名网络工具 / 高危仍拒 / 越界写入拒）；
- **W4–W9 作答质量**：契约注入 / 引用保护 / PDF 续读 / 检索接线 / 流式客户端 / 产物闸门；
- **W10 作答质量**：JSON 自修复 / 检索直抓拦截 + 引导；
- **W11 批次**：拦截引导升级 / 全源冷却话术 / compare 两校并行 / 微信源快失败；
- **D0 最小事务化**：写前快照任何模式通用 + 按文件 / 按检查点精确回滚；
- **浏览器采集整合**：阶段 0 安全加固 / 微信兜底 / 两级采集 / 媒体硬约束；
- **多轮消缺**：compare 卡死三轮修复、批改告警收紧、R1+R3 作答强化、占位值自指
  改写闸门、E1/E2 消缺。

---

## [3.1.0] — 2026-09-25（上一发布版本）

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

