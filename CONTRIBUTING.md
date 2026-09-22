# 考研学习链 (Kaoyan AI Study Chain) · 开发者与贡献指南

欢迎参与**考研学习链 (Kaoyan AI Study Chain)** 的开源建设与维护！
本项目致力于为中国考研学子打造一套零幻觉、高纪律、以得分为唯一导向的数字化 AI 私人教师备考工程。

---

## 📑 目录

- [一、项目完整架构与目录规范全景树](#一项目完整架构与目录规范全景树)
- [二、本地开发环境配置](#二本地开发环境配置)
- [三、自动化测试与代码质量工程](#三自动化测试与代码质量工程)
- [四、多智能体协议开发规范 (Agentic Protocols)](#四多智能体协议开发规范-agentic-protocols)
- [五、代码贡献与 Pull Request 流程](#五代码贡献与-pull-request-流程)

---

## 一、项目完整架构与目录规范全景树

本项目遵循 Local-First（本地优先）与外置状态机驱动架构，目录规范如下：

```text
kaoyan_chain/
├── .github/workflows/deploy-pages.yml   # GitHub Actions 自动化看板部署流水线
├── .github/workflows/test.yml           # GitHub Actions 自动化回归测试流水线
├── .gitignore                           # Local-First 隐私防泄露安全规则
├── .cursorrules                         # Cursor 编辑器智能加载协议
├── .clinerules                          # Roo Code / Cline 编辑器加载协议
├── LICENSE                              # MIT 开源许可证
├── README.md                            # 项目门户总说明与快速开箱指引
├── 操作手册.md                          # 📘 学员专用实操全流程指南（39子命令全景）
├── CONTRIBUTING.md                      # 🛠️ 开发者与贡献指南（架构树、测试与规范）
├── SETUP.md                             # 进阶部署配置与看板开发手册
├── CHANGELOG.md                         # 版本历史与升级方式
├── pyproject.toml                       # Python 打包标准与 CLI 入口声明
├── ky.bat                               # Windows 一键启动 CLI 私教
├── GUI.bat / 启动GUI.bat                # Windows 一键启动桌面 GUI（普通 / 静默模式）
├── 调试模式启动GUI.bat                  # 带控制台输出的 GUI 启动器（排错用）
├── AGENTS.md                            # 全科总教练 Agent 路由中枢与风格设定
├── GEMINI.md                            # Gemini / Antigravity 入口配置
├── 更新看板.bat                         # Windows 本地编译看板脚本
├── installer.iss                        # Inno Setup 安装包脚本（构建生成）
├── KaoyanStudyChain.spec                # 路径无关的 PyInstaller 打包配置
│
├── data/                                # 高校研招权威数据库
│   └── universities/                    # 全国高校研招名录与站点拓扑
│       ├── registry.json                # 57 所院校详细档案（代码、别名与官网二级域名库）
│       ├── <省份>/<高校>.yaml           # 57 所详细档案 + 1,841 所基础名录分省结构化配置
│       └── school_data/                 # 各高校结构化招生简章与专业目录缓存
│
├── 01-数学/                             # 数学专属私教体系（数一/二/三/396通用）
│   ├── AGENTS.md                        # 防超纲、解题步骤规范、防计算失误协议
│   ├── 考试大纲.md                      # 核心考纲与知识点清单模板
│   ├── 参考资料/                        # 本地专属教材与真题目录 (已由 .gitignore 忽略)
│   ├── 00_数学备考总规划模板.md         # 阶段节奏与题量规划模板
│   ├── 01_每日作业提示词模板.md         # 派题与分步批改 Prompt
│   ├── _状态/*.template.md              # 任务、档案、雷达、进度表头模板
│   ├── 错题本/                          # 错题记录模板与索引
│   └── 每日笔记/                        # 笔记沉淀模板
│
├── 02-英语/                             # 英语专属私教体系（英一/英二通用）
│   ├── AGENTS.md                        # 搭积木拆长难句、三步定位解题法
│   ├── 考试大纲.md                      # 考纲词汇与核心模块清单
│   ├── 参考资料/                        # 本地专属真题与语料库目录 (忽略)
│   ├── 00_英语备考总规划模板.md         # 题型分值与阶段突破计划
│   ├── 01_每日使用提示词.md             # 拆句与选项排查 Prompt
│   ├── 作文语料库/通用作文语料.md       # 高频功能句与图表作文语料
│   ├── _状态/*.template.md              # 英语学情状态模板
│   └── 错题与长难句本/                  # 长难句切分与错题索引模板
│
├── 03-思想政治理论/                     # 思想政治理论体系（70分减负提效）
│   ├── AGENTS.md                        # 核心考点秒杀、大题模型化协议
│   ├── 考试大纲.md                      # 政治新大纲核心考点清单
│   ├── 参考资料/                        # 本地专属习题集与模拟卷目录 (忽略)
│   ├── 00_政治备考总规划模板.md         # 三阶段作战安排
│   ├── 01_每日使用提示词.md             # 选择题抽测与帽子词自测 Prompt
│   ├── _状态/核心速记_帽子词与历史节点.md# 政治高频核心速记卡
│   ├── _状态/*.template.md              # 政治学情状态模板
│   └── 错题本/                          # 政治选择题错因归纳模板
│
├── 04-专业课/                           # 专业课通用体系（统考 / 高校自命题通用）
│   ├── AGENTS.md                        # 专业课专属私教协议模板
│   ├── 考试大纲.md                      # 报考院校官方考纲要点清单
│   ├── 参考资料/                        # 本地指定教材与历年真题目录 (忽略)
│   ├── 00_专业课备考总规划模板.md       # 阶段作战计划模板
│   ├── 01_考纲拆解与分值地图模板.md     # 考纲分值拆解模板
│   ├── 02_核心公式与考点速查模板.md     # 核心结论速记模板
│   ├── 03_题源核验与抽题协议模板.md     # 权威题源白名单门禁
│   ├── 双校考情对比_对比院校B_VS_对比院校B_计算机.md # ky compare 自动生成横向对标研报
│   ├── 目标院校情报_对比院校B_计算机.md # ky scout / admission 权威招考情报研报
│   ├── 学情档案.template.md             # 章节掌握度记忆中枢模板
│   ├── 每日作业/                        # 每日作业记录模板
│   └── 错题本/                          # 错题记录模板与索引
│
├── 05-考研看板/                         # 看板构建工程
│   ├── build.py                         # 构建编排（取数 → 渲染 → 落盘）
│   ├── web/                             # 构建支撑模块（配置/Markdown/卡片/雷达/快照/主题/资源）
│   │   └── template.html                # 页面模板（含 {{占位符}}）
│   ├── docs/index.html                  # 编译生成的单文件移动端看板
│   └── README.md                        # 看板二次开发指引
│
├── rust_ext/                            # [可选] Rust PyO3 原生加速扩展源码
│   ├── Cargo.toml                       # Rust 包管理与 PyO3 绑定配置
│   └── src/                             # chunker, hasher, context_compactor, extractor 四模块
│
├── docs/                                # GitHub Pages 发布源镜像与高清矢量图谱
│   ├── assets/                          # 印刷级 SVG 架构图与演示素材
│   ├── experiences/                     # 社媒实名去噪高分经验与避坑档案库
│   ├── index.html                       # 移动端 6-Tab 自测看板发布源
│   ├── live.html                        # 印刷级 KaTeX 实时可视化网页伴侣
│   └── state_snapshot.json              # 学情脱敏快照数据集
│
└── tools/                               # 跨平台运维与管理工具包
    ├── agent/                           # 工业级自主智能体内核 (Loop, Hooks, Memory, MCP, Sandbox)
    ├── gui/                             # [v2.6+] PySide6 桌面 GUI 模块
    │   ├── __init__.py                  # GUI 包入口
    │   ├── main_window.py               # 主窗口：组装界面 + 事件分发（数据/样式已外移）
    │   ├── theme_apply.py               # 主题解析、应用与 QSettings 偏好持久化
    │   ├── services/                    # 纯数据服务（可离屏单测，不依赖 Qt 控件）
    │   └── widgets/                     # 对话框与自定义控件 (FunctionCard / WeChatSearchDialog)
    ├── theme/                           # 设计系统单一真源：token + 三端编译器（QSS / CSS / ANSI）
    ├── state/                           # 四端共享状态层（今日任务、倒计时、配置摘要统一解析）
    ├── version.py                       # 项目版本号单一真源
    ├── intelligence/                    # KaoYan Intelligence 招考全景情报与证据链引擎
    │   ├── chsi_connector.py            # 研招网 S 级权威目录连接器
    │   ├── comparator.py                # 双校招考核心指标横向深度对标引擎
    │   ├── discovery.py                 # 官方研究生院与二级学院站点拓扑发现
    │   ├── evidence_engine.py           # S/A/B/C/D 证据链构建与多源冲突仲裁
    │   ├── extractor.py                 # 网页/PDF 专业代码与拟招指标智能提取
    │   ├── fetcher.py                   # 智能重试与网页深度降噪正文提取
    │   ├── models.py                    # 证据对象、高校实体与数据模型
    │   ├── registry.py                  # 高校代码解析与通用自适应实体合成器
    │   ├── scout_engine.py              # 社媒(知乎/B站/小红书)就读体验口碑引擎
    │   ├── syllabus_diff.py             # 考纲 AST 变迁对比与动荡率分析引擎
    │   └── watcher.py                   # 招生简章动态指纹监控与变动雷达
    ├── skills/                          # 考研专有能力技能中枢
    │   ├── school_scout.py              # 目标高校研招与社媒口碑侦察专属技能
    │   ├── wechat_searcher.py           # [v2.6+] 微信公众号考研经验贴检索与 Markdown 清洗管道
    │   ├── syllabus_diff.py             # 考纲版本对比技能
    │   ├── material_ingestion.py        # 试题智能分块切片入库管道 (ky ingest)
    │   ├── exam_composer.py             # 靶向自测组卷与盲盒排版引擎 (ky compose)
    │   ├── variant_retrieval.py         # 同源变式检索与防伪水印引擎 (ky variant)
    │   └── ...                          # 验算/抽题/图谱/诊断各技能实现
    ├── tui_navigator.py                 # 终端全景智能中枢 TUI v2.5 极客控制台
    ├── ky_gui.py                        # [v2.6+] PySide6 GUI 启动入口 (ky gui)
    ├── doctor.py                        # 全系统健康诊断工具 (ky doctor)
    ├── init_workspace.py                # 跨平台工作区全能初始化向导
    ├── ky_cli.py                        # 专有终端私教与多端 IM 网关入口（39 个子命令）
    ├── ky_io.py                         # 原子写 + 跨进程文件锁 + 只读模式闸门（所有落盘的唯一入口）
    ├── fsrs_scheduler.py                # FSRS 自适应复测调度器（全项目间隔计算唯一真源）
    ├── protocol_loader.py               # 顶层协议加载器（兼容源码模式与 wheel 安装模式）
    ├── note_lock.py                     # 笔记只读锁定（frontmatter locked: true 后禁止被自动改写）
    ├── llm_client.py                    # OpenAI 兼容端点客户端（重试 / 超时 / gzip 解压容错）
    ├── exam_calendar.py                 # 初试倒计时与考试日历
    ├── privacy_policy.py                # 隐私策略与内容级脱敏规则的**单一事实源**
    ├── sync_publish.py                  # 导出公开仓库副本（脱敏镜像，先清空再重建）
    ├── build_package.py                 # PyInstaller 打包（默认做内容级身份脱敏）
    ├── lint_check.py                    # 零依赖静态检查（只卡 ERROR 级问题）
    ├── check_dashboard.py               # 看板产物守卫（JS 语法 + 前端契约 + 真浏览器运行）
    ├── evaluate_pipeline.py             # 离线评测：FSRS 校准度 (RMSE/LogLoss) 与引文忠实度
    ├── gui_launcher.py                  # 桌面端启动与诊断 launcher
    ├── build_svg_assets.py              # 文档 / 看板 SVG 配图生成（开发工具）
    ├── export_logo_and_animations.py    # Logo 与动画导出（开发工具）
    ├── generate_perfect_loading_animations.py  # 加载动画生成（开发工具）
    ├── simulate_workflow.py             # 工作流模拟（开发工具）
    ├── study_planner.py                 # 个人定制化方案设计引擎与防疲劳预警
    ├── syllabus_manager.py              # 官方考纲智能匹配与切换管理器
    ├── test_ky_suite.py                 # 完整自动化回归测试与 CLI smoke test (26 组, 304 项)
    ├── test_new_features.py             # [v2.6+] 新增功能专项测试 (WeChat + Rust + GUI + CLI, 130 项)
    ├── update_dashboard.py              # 自动化看板生成与同步脚本
    └── verify_health.py                 # 全科规范与关键文件健康度巡检脚本
```

---

## 二、本地开发环境配置

### 1. 环境准备
- **Python 版本**：要求 **Python 3.10+** (推荐 3.11 / 3.12)
- **操作系统**：Windows 10/11, macOS, Linux 通用

### 2. 可编辑模式安装
推荐以 editable 模式挂载本地开发包：
```bash
git clone https://github.com/moyetian/kaoyan_chain.git
cd kaoyan_chain
python -m pip install -e .
```
安装后，可直接在终端中随时使用 `ky` 命令行工具。

### 3. 安装扩展依赖（可选增强）
```bash
pip install -r requirements.txt
```
核心功能不依赖任何三方库（纯 Python 标准库零依赖即可运行），扩展依赖仅用于数学高精符号运算 (SymPy)、PDF 提取 (pypdf)、图像批改 (Pillow) 等特定技能。

### 4. 可选增强模块（v2.8.0 沿用）

| 模块 | 安装方式 | 作用 | 缺失时行为 |
|---|---|---|---|
| **PySide6** (桌面 GUI) | `pip install PySide6` | 启用 `ky gui` 桌面可视化界面 | `ky gui` 提示安装，测试自动跳过 |
| **ky_rust_ext** (Rust 加速) | `cd rust_ext && maturin develop --release` | chunk_text / sha256_hash / estimate_tokens 原生加速 | 透明回退纯 Python 实现，功能不受影响 |

> **CI 环境说明**：GitHub Actions CI 不安装 PySide6 和 Rust 工具链，相关测试项会被标记为 `[SKIP]` 而非失败，确保 CI 流水线绿色通过。

---

## 三、自动化测试与代码质量工程

提交任何代码修改前，必须运行并通过全套自动化测试套件：

### 1. 系统健康体检
```bash
python tools/doctor.py
# 或
ky doctor
```
检查 Python 运行时、依赖项就绪状态、工作区规范与 Git 隐私隔离配置。

### 2. 全量回归测试
```bash
# ⚠️ 安全守卫：该套件会真实改写工作区用户数据（备考方案 / 今日任务 / 大纲），
#    检测到真实考生工作区会拒绝运行（exit 2）。请在干净副本里跑：
#      git archive HEAD | tar -x -C /tmp/ky_copy && cd /tmp/ky_copy && python tools/test_ky_suite.py
#    或先备份 ky_config.json，再设 KY_TEST_ALLOW_REAL_WORKSPACE=1 显式放行。
python tools/test_ky_suite.py
```
该套件包含 **26 组测试项**（共 **304 测试点**：Git 工作区内 304 通过 + 0 跳过；干净检出无 `.git` 时
为 299 通过 + 5 跳过 = 304，差在「测试组 7 Git 隐私隔离」需 `.git`，两种环境总数一致），覆盖：
- 配置文件解析与默认兜底
- 四科 Prompt 与防书目幻觉门禁
- Webhook 格式与模拟并发处理
- 艾宾浩斯记忆与上下文防爆压缩
- 权限管理（Safe / Plan / Auto / Ask 模式）与沙箱路径阻断
- KaoYan Intelligence 招考情报与考纲 AST 变迁分析
- CLI 真实子进程级 Smoke Tests
- **[v2.6+] 新增功能专项集成测试**（微信公众号检索、Rust 双模一致性、PySide6 GUI 离屏校验）

### 3. 新增功能专项测试
```bash
python tools/test_new_features.py
```
独立运行 v2.6.0 新增模块的专项测试（4 组 A/B/C/D，共 **130 测试点**，0 跳过；与是否 Git 工作区无关），可选依赖缺失时自动 `[SKIP]`：
- **组 A**：微信公众号经验贴检索、HTML→Markdown 清洗、院校档案联动
- **组 B**：Rust PyO3 扩展与纯 Python 双模一致性校验（需 `ky_rust_ext`，否则跳过）
- **组 C**：PySide6 GUI 离屏实例化与 QSS 主题完整性（需 `PySide6`，否则跳过）
- **组 D**：CLI 子命令路由（`ky gui`/`ky wechat`/`ky wx`）与 TUI 菜单挂载

> **计数是环境相关的**（2026-09-21 实测）：上述数字随是否 Git 工作区、是否构建 `dist/`、
> 是否配置 `study_plan.school` 而变。改文档计数时请连同环境前提一起写。

**准入标准**：测试结果必须为 `失败 0 项`（100% 通过或跳过），不允许任何断言失败。

---

## 四、多智能体协议开发规范 (Agentic Protocols)

在为本项目开发新功能或修改 Agent 行为时，请严格遵守以下核心法则：

### 1. 绝对防幻觉与白名单题源原则
- 严禁让 AI 自编缺少权威采分标准的题目；
- 派发练习必须抽取自学员本地 `参考资料/` 实际存放的文件或官方考纲白名单；
- 若本地未放置真实资料，必须明确提示学员导入，禁止伪造“本题来自李林880/张宇1000”等未核验题源。

### 2. 考纲红线机制 (Syllabus Guard)
- 数学二绝不允许派发三重积分、曲面积分或无穷级数；
- 工具层与 Hook 层必须实施前置拦截；
- 允许学员在笔记、复盘、总结等“否定/元语境”（如“数二不考三重积分”）中自由记录，不得误阻断正常学术笔记。

### 3. Local-First 隐私防泄露
- 仓库提交中严禁携带任何个人做题草稿、错题内容、敏感 API Key 或大体积版权 PDF；
- 所有敏感文件必须纳入 `.gitignore`；
- 修改配置文件写入时，必须使用原子替换，禁止截断或擦除配置；
- **两条「内容离开本机」的出口必须同源**：`tools/sync_publish.py`（公开仓库副本）与
  `tools/build_package.py`（PyInstaller 发布包）都从 `tools/privacy_policy.py` 取策略与
  脱敏规则，不得各自维护名单 —— 历史上正是「各修一条」导致另一条长期裸奔。
  改动 `privacy_policy` / `sync_publish` / `build_package` 时**必须配阴性验证**：
  把修复注释掉，对应测试必须变红，否则等于没锁住。
- 打包产物默认做**内容级身份脱敏**（`*.md/*.html/*.svg/*.py`），并在构建末尾对全树
  （含 `_internal/`）做残留自检，检出真实身份即中止构建；给自己打「带我的方案」的包时
  用 `--keep-identity` 跳过。规则由 `ky_config.json` 的 `study_plan` 现算，
  没有该文件的贡献者 / CI 会自然跳过。

---

## 五、代码贡献与 Pull Request 流程

1. **Fork** 本仓库并从 `main` 分支拉出特性分支（如 `feature/new-syllabus-ast`）；
2. 编写功能代码与对应单测；
3. 执行 `python tools/doctor.py` 与 `python tools/test_ky_suite.py` 确保 100% 通过
   （`test_ky_suite.py` 会改写工作区用户数据，默认拒绝在真实考生工作区运行，请在干净副本里跑，
   或设 `KY_TEST_ALLOW_REAL_WORKSPACE=1` 显式放行并先备份 `ky_config.json`）；
4. 提交清晰规范的 Git Commit 记录；
5. 若变更影响用户可见行为（新增/修改命令、配置项、打包或隐私策略），
   请同步更新 [CHANGELOG.md](CHANGELOG.md) 与相关文档
   （[README.md](README.md) / [操作手册.md](操作手册.md) / [SETUP.md](SETUP.md)）；
6. 创建 Pull Request，详细描述变更背景、测试结果与设计决策。

> 📝 版本历史、升级步骤与跨版本注意事项统一记录在 [CHANGELOG.md](CHANGELOG.md)。
