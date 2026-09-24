<div align="center">

<img src="docs/assets/logo_transparent.png" alt="考研学习链 Logo" width="140" />

# 考研学习链 (Kaoyan AI Study Chain) · 数字化备考工程

**基于 AI Agent 私人教师协议、外置状态机驱动与自动化自测看板的开源考研备考工程**

<p align="center">
  <a href="https://moyetian.github.io/kaoyan_chain/"><img src="https://img.shields.io/badge/🌐_在线看板体验-Live_Demo-6366f1?style=for-the-badge&logo=githubpages&logoColor=white" alt="在线看板体验" /></a>
  <a href="https://github.com/moyetian/kaoyan_chain/releases"><img src="https://img.shields.io/badge/📦_下载开箱即用程序包-Releases-2ea44f?style=for-the-badge&logo=github&logoColor=white" alt="下载开箱即用程序包" /></a>
  <a href="#-用户本地化部署与快速上手流程-3-分钟开箱"><img src="https://img.shields.io/badge/🚀_快速上手-Quick_Start-10b981?style=for-the-badge&logo=rocket&logoColor=white" alt="快速上手" /></a>
  <a href="操作手册.md"><img src="https://img.shields.io/badge/📘_学员实操手册-Handbook-3b82f6?style=for-the-badge&logo=read-the-docs&logoColor=white" alt="学员实操手册" /></a>
  <a href="CONTRIBUTING.md"><img src="https://img.shields.io/badge/🛠️_开发与贡献-Contributing-f59e0b?style=for-the-badge&logo=git&logoColor=white" alt="开发与贡献" /></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/%E7%89%88%E6%9C%AC-v2.8.0-blue?style=flat-square&logo=github&logoColor=white" alt="版本 v2.8.0" />
  <img src="https://img.shields.io/badge/Tests-1700%20Passed-10b981?style=flat-square&logo=checkmarx&logoColor=white" alt="Tests 1700 Passed" />
  <img src="https://img.shields.io/badge/GUI-PySide6%20Desktop-6366f1?style=flat-square&logo=qt&logoColor=white" alt="PySide6 GUI" />
  <img src="https://img.shields.io/badge/Rust-PyO3%20Native%20Fast-DEA584?style=flat-square&logo=rust&logoColor=white" alt="Rust Native" />
  <img src="https://img.shields.io/badge/WeChat-Scraper%20%26%20Search-07C160?style=flat-square&logo=wechat&logoColor=white" alt="WeChat Searcher" />
  <img src="https://img.shields.io/badge/KaoYan%20Intelligence-57%2B%E6%89%80%E9%AB%98%E6%A0%A1%E6%A1%A3%E6%A1%88-blueviolet?style=flat-square&logo=googleearthengine&logoColor=white" alt="KaoYan Intelligence" />
  <img src="https://img.shields.io/badge/Terminal%20TUI-%E6%9E%81%E5%AE%A2%E6%8E%A7%E5%88%B6%E5%8F%B0-3b82f6?style=flat-square&logo=gnometerminal&logoColor=white" alt="Terminal TUI" />
  <img src="https://img.shields.io/badge/Dashboard-6%20Tabs-6366f1?style=flat-square&logo=speedtest&logoColor=white" alt="Dashboard" />
  <img src="https://img.shields.io/badge/Memory-3--Tier%20Pruning-f59e0b?style=flat-square&logo=speedtest&logoColor=white" alt="Memory" />
  <img src="https://img.shields.io/badge/Privacy-Local--First-10b981?style=flat-square&logo=shield&logoColor=white" alt="Privacy" />
  <img src="https://img.shields.io/badge/License-MIT-lightgrey?style=flat-square" alt="License" />
</p>

<p align="center">
  <a href="#-用户本地化部署与快速上手流程-3-分钟开箱">🚀 极速开箱</a> •
  <a href="#1-🖥️-桌面可视化操作端-pyside6-desktop-client--ky-gui">🖥️ 桌面 GUI</a> •
  <a href="#4-📟-终端全景智能中枢-tui-v25-极客控制台">📟 终端 TUI</a> •
  <a href="#2-📱-微信公众号考研经验检索与爬虫-ky-wechat--ky-wx">📱 经验检索</a> •
  <a href="#6-🏛️-kaoyan-intelligence-招考全景情报与证据链引擎">🏛️ 研招情报</a> •
  <a href="#-常用私教交互与指令速查">⌨️ 指令速查</a> •
  <a href="操作手册.md">📘 实操手册</a> •
  <a href="CONTRIBUTING.md">🛠️ 开发贡献</a> •
  <a href="CHANGELOG.md">📝 更新日志</a>
</p>

<p align="center">
  <img src="docs/assets/hero_banner.jpg" alt="考研学习链 · 功能全景" style="max-width:100%;border-radius:12px;box-shadow:0 8px 24px rgba(0,0,0,0.12);" />
</p>

</div>

---

## 📖 这是什么？

<p align="center">
  <img src="docs/assets/readme_system_overview.svg" alt="考研学习链系统架构与工作流全景" style="max-width:100%;border-radius:10px;" />
</p>

<p align="center">
  <img src="docs/assets/feature_comparison.svg" alt="传统 AI 对话与考研学习链对比" style="max-width:100%;border-radius:10px;" />
</p>

**考研学习链 (Kaoyan AI Study Chain)** 是一套面向考研学子的**数字化、工程化 AI 私人教师备考系统**。

传统使用大语言模型（ChatGPT、Claude、DeepSeek 等）备考经常遇到四大痛点：

1. **幻觉与超纲**：AI 随性自编缺少权威采分标准的题目，或派发超出大纲范围的偏题怪题；
2. **缺乏记忆与连续性**：多轮对话后 AI 遗忘你之前的薄弱考点、错题记录与复习节奏；
3. **缺乏采分点闭环**：直接给出终极答案，缺乏行级步骤分、公式分与“错因五分类”归因；
4. **缺少移动端自测工具**：碎片时间（排队、通勤、睡前）难以高效进行公式与核心考点默写。

本项目将 **Agent 私教协议（AGENTS.md）**、**外置状态机记忆（External State）**、**白名单题源抽题门禁（Source Verification）** 与 **自动化轻量 Web 看板（Static Site Generator）** 深度整合，帮助考研人构建属于自己的高纪律、零幻觉、稳拿基本盘的数字化私教系统。

---

## 🗺️ 文档地图：我该读哪一份？

仓库里有五份主要文档，内容各有侧重。

| 文档                                 | 面向         | 主要内容                                                  | 什么时候读             |
| ---------------------------------- | ---------- | ----------------------------------------------------- | ----------------- |
| **README.md**（本页）                  | 所有人        | 项目介绍、快速上手、目录结构、注意事项、隐私承诺、免责声明                         | **第一次接触项目时**      |
| [操作手册.md](操作手册.md)                 | 考研学员       | 三大界面（GUI / TUI / REPL）、每日 5 步法、全部指令实战、看板与 IM 绑定       | **开始日常使用之后**      |
| [SETUP.md](SETUP.md)               | 要部署 / 排障的人 | 进阶配置、看板发布（Pages / Cloudflare）、机器人接入、健康检查、**常见问题 FAQ** | **装不上或想折腾时**      |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发者        | 完整架构树、开发环境、测试与质量门禁、Agent 协议规范、PR 流程                   | **想改代码 / 提 PR 时** |
| [CHANGELOG.md](CHANGELOG.md)       | 所有人        | 版本历史与升级方式                                             | **升级前后**          |

> 快速直达：安装不上 → [SETUP 第 8 章 FAQ](SETUP.md)；不知道某条命令怎么用 → [操作手册 第 8 章「39 项子命令全景速查」](操作手册.md)；担心隐私 → 本页 [🔒 隐私优先](#-隐私优先与主流-ai-agent-接入) 与 [⚠️ 使用注意事项](#-使用注意事项) 第 7 条。

---

## 📦 开箱即用：不懂技术也能直接用（推荐）

**v2.8.0 最重要的变化**：本项目现已提供**开箱即用的程序包**。你**不需要安装 Python、不需要懂命令行**，下载后跟着界面引导填几项配置就能开始用。

### 三步开始使用

1. **下载**：到 [Releases 页面](https://github.com/moyetian/kaoyan_chain/releases) 下载最新版程序包。
   当前提供 **`KaoyanStudyChain-v2.8.0.zip`** —— 解压即用的免装目录（压缩包约 380 MB，解压后约 1 GB）。
   > 安装版（`KaoyanStudyChain_Setup_v2.8.0.exe`）需要用 Inno Setup 编译，当前版本暂未提供，后续补上。
2. **解压 / 启动**：把 zip 解压到任意文件夹（**不要放在需要管理员权限的目录**，
   如 `C:\Program Files`），运行 `KaoyanStudyChain.exe`，或双击 `启动GUI.bat`。
3. **启动并跟随引导配置**：运行 `KaoyanStudyChain`（或双击 `启动GUI.bat`），
   首次启动会引导你完成：
   - 填写**大模型 API Key**（DeepSeek / GLM / Qwen / Kimi / OpenAI / 本地 Ollama 均可，兼容 OpenAI 接口）；
   - 选择**报考院校、专业与考试科目**；
   - 设定**每日学习时长与作息**；
   之后即可开始「[科目]报到 → 做题 → 交作业」的日常流程（详见 [操作手册.md](操作手册.md)）。

> 💡 引导过程中随时可以跳过，之后再在「设置」里补填；所有配置都只存在**你自己的电脑里**。

### 关于这个程序包，你需要注意

| 事项 | 说明 |
|---|---|
| **它是脱敏的通用包** | 程序包**不含**任何人的报考方案、学情数据或 API Key —— 首次运行需要**你自己配置**。 |
| **体积较大** | 程序包含完整运行环境（Python + PySide6 + 依赖），约 **1 GB**。 |
| **首次启动较慢** | 解压/加载运行环境需要一点时间，属正常现象。 |
| **联网需求** | 做题、判分、看板可离线；「研招情报」类功能与模型调用需联网。 |
| **安全软件误报** | PyInstaller 打包的程序偶尔被杀软误报，可添加信任/白名单后运行。 |
| **获取更新** | 关注 [Releases](https://github.com/moyetian/kaoyan_chain/releases) 或 [CHANGELOG.md](CHANGELOG.md)，下载新版本覆盖安装即可。 |

> 🔧 想自己从源码构建程序包（比如要打一份「带我的报考方案」的私人包），
> 见下方 [🧪 自动化质量工程与开源协议](#-自动化质量工程与开源协议)
> 中的「独立发布包构建」小节。

---

## 🚀 用户本地化部署与快速上手流程 (3 分钟开箱)

> 这一节面向**愿意用源码 / 想改代码**的同学。若你只想直接使用程序，请看上一节
> [📦 开箱即用](#-开箱即用不懂技术也能直接用推荐)。

### 系统要求

| 项目     | 要求                         | 说明                                                                  |
| ------ | -------------------------- | ------------------------------------------------------------------- |
| 操作系统   | Windows 10/11、macOS、Linux  | 三者均已验证                                                              |
| Python | **3.10+**（推荐 3.11 / 3.12）  | 低于 3.10 会因语言特性与类型标注报错                                               |
| 磁盘空间   | 源码约 300 MB；**打包发布物约 1 GB** | `dist/` 为构建产物，不需要时可直接删除                                             |
| 网络     | 可选                         | 仅情报类命令（`scout` / `admission` / `watch` / `wechat` / `fetch`）与模型调用需要 |
| 大模型    | 任意 OpenAI 兼容端点             | DeepSeek / GLM / Qwen / Kimi / OpenAI / 本地 Ollama 均可                |

### 依赖矩阵：核心零依赖，增强按需安装

| 依赖                                | 必需性    | 用途                              | 不装会怎样                |
| --------------------------------- | ------ | ------------------------------- | -------------------- |
| Python 标准库                        | **必需** | REPL 对话、协议路由、状态管理、看板生成          | —                    |
| `pip install -r requirements.txt` | 可选     | PDF 抽取、数学符号验算、图像识别等增强技能         | 对应技能提示安装，其余功能照常      |
| PySide6                           | 可选     | `ky gui` 桌面可视化界面                | `ky gui` 提示安装，测试自动跳过 |
| `ky_rust_ext`                     | 可选     | chunk\_text / sha256 / 分词估算原生加速 | 透明回退纯 Python，功能与结果一致 |

> 一句话：**不装任何第三方库也能跑起来**；`pip install -r requirements.txt` 只在你
> 需要 PDF / 公式 / 图像 / GUI 这类增强能力时才执行。

### 极速 5 步走：

```bash
# 1. 克隆项目仓库到本地
git clone https://github.com/moyetian/kaoyan_chain.git
cd kaoyan_chain

# 2. (可选) 安装增强依赖 —— 按需安装，核心功能无需任何第三方库
pip install -r requirements.txt

# 3. 运行跨平台交互式初始化向导 (锁定考研倒计时、科目大纲与提分目标)
python tools/init_workspace.py

# 4. 配置大模型 API Key (支持 DeepSeek、GLM、Qwen、Kimi、OpenAI 或本地 Ollama)
python tools/ky_cli.py config
# 或 Windows 双击 ky.bat 后输入 config

# 5. 启动终端私教开始复习！
ky
# 或 python tools/ky_cli.py
```

> [!IMPORTANT]
> **第 3 步不可跳过**。初始化向导会生成 `ky_config.json`（已被 `.gitignore` 保护）。
> 缺少该文件时，倒计时、任务编排与部分指令会不可用。

> [!TIP]
> 考研学习链提供 **三大操作端** 供你自由选用：
>
> - 🖥️ **桌面可视化端**：输入 `ky gui`，享受 PySide6 构建的高颜值多主题看板（5 套主题预设含护眼绿/樱粉/高对比）、倒计时与可视化做题面板！
> - 📟 **终端全景中枢**：输入 `ky menu`，启动 TUI 极客控制台，键鼠双控、零多余依赖！
> - 💬 **流式对话私教**：输入 `ky`，即刻开启多轮推演与真题采分点打分！

> [!TIP]
> **装完先自检**：执行 `python tools/doctor.py`，一次性核对 Python 版本、可选依赖、四科协议、
> `ky_config.json`、API 连通性与 Git 隐私隔离是否就绪；如需验证功能完整性，
> 请在**干净副本**里跑 `python tools/test_ky_suite.py`（26 组 305 项断言，数分钟）。

> [!WARNING]
> **`tools/test_ky_suite.py` 会真实改写工作区用户数据**（备考方案 / 今日任务 / 大纲等），
> 因此它内置了安全守卫：检测到真实考生工作区（`ky_config.json` 含真实报考方案或 API Key）
> 会**拒绝运行**（exit 2），这是设计如此、不是套件坏了。两种正确跑法：
>
> 1. **干净副本（推荐）**：`git archive HEAD | tar -x -C /tmp/ky_copy && cd /tmp/ky_copy && python tools/test_ky_suite.py`；
> 2. 确实要在本工作区跑：**先自行备份 `ky_config.json`**，再 `KY_TEST_ALLOW_REAL_WORKSPACE=1 python tools/test_ky_suite.py` 显式放行。

---

## 🌟 核心功能全景亮点

<p align="center">
  <img src="docs/assets/persona_triad_matrix.svg" alt="三类考研学生画像与差异化提分闭环矩阵" style="max-width:100%;border-radius:10px;" />
</p>

### 1. 🖥️ 桌面可视化操作端 (PySide6 Desktop Client · `ky gui`)

- **5 套主题预设**：基于 Qt/PySide6 构建，明暗（`dark` / `light`）之外另备**护眼绿**、**樱粉**与**高对比无障碍**主题；
配色不再散落在样式表里，而是由 `tools/theme` 的**语义 token** 统一编译，四端（GUI / Web 看板 / 终端 / 实时伴侣）共用同一套色；
想改风格只需在 `ui_theme.json` 里覆盖一个主色，悬停色与焦点环会自动跟着协调（内置 WCAG 对比度校验，看不清的配色会被拒绝）；
- **L2 主题旋钮设置面板**：点击头栏「设置」按钮，即时预览 5 个旋钮——预设切换、自定义主色（选色器）、圆角、密度、字号缩放，写入 QSettings 重启不丢；
- **全战役状态大盘**：Header 实时联动初试倒计时、学员目标院校/专业、当前激活辅导风格；
- **10 大功能卡片直达**：今日任务、靶向组卷、同源变式、考纲Diff、切片入库、院校侦察、双校对标、简章监控、看板更新、公众号检索；卡片图标采用**内联 SVG 矢量渲染**，跨系统字形一致、颜色跟随主题主色自动重绘；
- **4 大深度交互分页**：**私教对话**（多线程异步防卡死、流式打字机输出）、**今日任务**（打卡进度条）、**错题本**（高频归因透视）、**研招情报**（高校监控雷达）；
功能卡片支持 **Tab 键聚焦 + Enter/Space 激活**，焦点环由主题 QSS 统一提供，键盘用户可达。

### 2. 📱 微信公众号考研经验检索与爬虫 (`ky wechat` / `ky wx`)

- **多源智能检索**：搜狗微信搜索 (主源) ➔ Bing 微信定向搜索 (备用源) ➔ 本地经验库 (离线兜底)；
- **HTML ➔ Markdown 强力清洗管道**：剥离广告、样式与追踪脚本，提取公众号名、发布时间与正文内容；
- **本地沉淀与口碑档案联动**：通过 `--save` 自动落地至 `.memory/experiences/` (本地隐私目录，不入库)，并无缝追加进目标高校社媒口碑研报。

### 3. ⚡ Rust (PyO3) 原生性能加速与透明零依赖降级 (`ky_rust_ext`)

- **原生动态库赋能**：在 `rust_ext/` 下采用 PyO3 + Maturin 构建高性能模块，试题智能分块、高校简章指纹 SHA-256、上下文 Token 压缩等性能敏感路径提速数十倍；
- **极致 Local-First 兼容**：在未安装 Rust 编译环境或特定架构下，自动透明降级为纯 Python 基准实现，零破坏、零报错。

### 4. 📟 终端全景智能中枢 (TUI 极客控制台)

- **键鼠双控面板**：运行 `ky menu`，呈现考研总战役倒计时、今日四科推进进度条、目标院校雷达与 10 大核心功能直达入口；
方向键/鼠标选择、Enter 执行，长任务在后台线程运行不冻结界面，执行输出保留在右侧日志区不再一闪而过；
- **纯终端极速体验**：数字快捷键 `1`-`9` 秒级穿透，资源占用极低；未安装 `textual`（或非交互式终端）时自动回落到纯文本控制台，功能不缺失；

### 5. 📊 6-Tab 移动端自测看板 (`docs/index.html`)

- **零服务器依赖**：自包含单文件 HTML，手机浏览器打开即用，支持 **PWA 添加到手机主屏幕**；
- **独创遮罩自测**：在 `🧠 必背` 页签开启高斯模糊遮罩，触碰卡片秒测数学核心公式、英语高频词与政治帽子词；
- **5 套主题预设一键轮换**：曜石黑 / 晨曦白 / 护眼绿 / 樱粉 / 高对比（无障碍）随按钮循环切换，选择自动记忆；
- **备考节律主题**：按距初试天数自动推荐主题（基础期深色 → 强化期护眼绿 → 冲刺期亮色 → 临考月暖粉 → 决战周高对比），手动选择后不再被覆盖；
- **全科掌握度雷达**：动态提取复习增量与掌握度分值，实时展示考情与学情趋势。

### 6. 🏛️ KaoYan Intelligence 招考全景情报与证据链引擎

- **全国 57 所详细档案 + 1,841 所基础名录**：收录教育部代码、官方站点拓扑树（官网 ➔ 研究生院 ➔ 招生网 ➔ 二级学院）；
- **S 级研招网直连与证据链** (`ky admission`)：输出带有信源级别、置信度与时间戳的权威招考指标，锁定考研年份；
- **双校深度横向对标** (`ky compare`)：一键 PK 两校办学层次、初试科目差异（408 vs 自命题）、复试线与一志愿保护度；
- **招生简章动态指纹雷达** (`ky watch`)：SHA256 毫秒级比对目标院校主页，每年 8\~10 月第一时间捕获新简章；
- **社媒实名去噪研报** (`ky scout`)：聚合知乎、B站、小红书实名讨论，智能剔除营销卖课，出具就读体验研报。

### 7. ⚔️ 靶向组卷、同源变式与三级记忆闭环

- **靶向拼卷自测** (`ky compose` / `ky exam`)：针对薄弱章节与历史错题反向靶向拼卷，自动抹去答案供独立推演；
- **同源变式检索** (`ky variant`)：四科白名单同类真题变式精准检索，严加防伪水印与防幻觉溯源；
- **整卷多题诊断** (`ky diagnose`)：整卷级多题批改，输出章节失分排行与薄弱处方；
- **三级分层记忆体系** (`ky memory`)：Session / Project / Decisions 三级分离，防范 Token 爆炸与上下文污染。

---

## 💬 常用私教交互与指令速查

系统提供 39 个工业级 CLI 子命令与对话交互口令，完整参数说明请参阅 [📘 学员实操手册](操作手册.md)：

| 交互场景      | 推荐口令 / 子命令                         | 行为说明                                      |
| --------- | ---------------------------------- | ----------------------------------------- |
| **桌面操作**  | `ky gui` / `ky-gui`                | 启动 PySide6 桌面可视化图形操作端 (内置 5 套主题预设)        |
| **公众号检索** | `ky wechat 408经验 --save` / `ky wx` | 多源检索微信公众号考研文章与上岸经验贴，沉淀至本地并联动研报            |
| **晨起看盘**  | `ky status` / `ky today`           | 查看初试倒计时、今日四科任务攻坚清单与进度                     |
| **学科报到**  | `数学报到` / `英语报到` / `ky`             | 私教调取昨日错题，从白名单题库抽取题目派发                     |
| **提交作业**  | `交作业` / `/submit`                  | 逐行给出采分点 `[+2分]`/`[-1分]`，追查错因五分类并归档错题      |
| **微步提示**  | `/hint`                            | 唤醒苏格拉底三级脚手架（破题定性 ➔ 首步搭桥 ➔ 避坑），拒绝剧透        |
| **错题重测**  | `ky review` / `ky exam`            | 调取 FSRS 自适应到期错题，隐去原答案进行盲盒自测               |
| **智能减负**  | `ky relieve` / `ky fatigue`        | 检查疲劳警报；一键将时间预算下调 25%，切换为鼓励型风格             |
| **院校对标**  | `ky compare 示例院校A 示例院校B 计算机`       | 横向深度对标两校招考指标、408/自命题、复试线与一志愿保护            |
| **考纲比对**  | `ky fetch diff --school=示例院校A`     | 解析新旧大纲 AST，标注考点增删与考查要求跃迁，测算动荡率            |
| **切片入库**  | `ky ingest 2024真题.md`              | 试卷智能切片与标准化题卡入库 (支持 Rust 毫秒级加速)            |
| **刷新看板**  | `ky build`                         | 重新编译并刷新本地与移动端自测看板（默认离线构建，`--cdn` 可切回 CDN） |
| **系统体检**  | `ky doctor`                        | 7 维度全系统健康诊断（Python/依赖/状态/API/端口/Git隐私/降级评估） |

---

## 📁 项目目录结构

```text
考研学习链/
├── AGENTS.md                    # 顶层中枢协议：AI 私教的最高指令与调度路由
├── README.md                    # 项目门户总说明与快速开箱指引（你正在读这份）
├── 操作手册.md                  # 学员实操通关手册（日常使用主文档）
├── SETUP.md                     # 部署、进阶配置与排障（看板/IM/FAQ）
├── CONTRIBUTING.md              # 开发者与贡献指南（含完整架构树）
├── CHANGELOG.md                 # 版本历史与升级方式
├── GEMINI.md                    # Gemini / Antigravity 适配入口
├── .cursorrules / .clinerules   # Cursor / Roo·Cline 编辑器适配规则（与 AGENTS.md 同源）
├── LICENSE                      # MIT 开源许可证
├── pyproject.toml               # 打包元数据（Python ≥3.10，当前版本 2.8.0）
├── requirements.txt             # 可选增强依赖清单（核心功能零依赖）
├── installer.iss                # Inno Setup 安装包脚本（由构建生成）
├── KaoyanStudyChain.spec        # 路径无关的 PyInstaller 打包配置
├── ky.bat                       # Windows 一键启动 CLI 私教
├── GUI.bat / 启动GUI.bat        # Windows 一键启动桌面 GUI（普通 / 静默模式）
├── 调试模式启动GUI.bat          # 带控制台输出的 GUI 启动器（排错用）
├── 更新看板.bat                 # Windows 一键刷新手机看板
├── 00_考研全科总战役规划.md      # 总战役规划（由 ky plan 依据你的配置生成，仅本地）
├── 00_考研全科总战役规划.example.md  # 脱敏示例规划（随仓库公开的模板）
├── .memory/                     # 三级分层记忆 session / project / decisions（仅本地）
├── logs/                        # 运行日志（仅本地）
│
├── .github/workflows/
│   ├── deploy-pages.yml         # GitHub Actions：看板自动部署到 Pages
│   └── test.yml                 # GitHub Actions：多平台 / 多 Python 版本回归测试
│
├── 01-数学/  02-英语/  03-思想政治理论/  04-专业课/
│   ├── AGENTS.md                # 该科目的专属私教协议
│   ├── 考试大纲.md              # 该科目考纲（公共课内置；专业课需 ky plan 生成）
│   ├── 00_XX备考总规划.md       # 该科目的阶段作战规划
│   ├── _状态/                   # 学情状态（*模板* 入库，个人数据不上云）
│   ├── 错题本/                  # 错题卡片与索引（*模板* 入库）
│   ├── 每日笔记/                # 每日笔记（*模板* 入库）
│   └── 参考资料/                # 你的真题与教材（个人资料，绝不上云）
│
├── 05-考研看板/                 # 移动端自测看板的构建工程
│   ├── build.py                 # 看板生成器（生成 docs/index.html 与脱敏快照）
│   ├── web/                     # 构建支撑模块与模板（主题变量、第三方资源、解析与渲染）
│   └── docs/                    # 看板产物与静态资源
├── docs/                        # 发布到 GitHub Pages 的静态看板与配图
│   ├── index.html               # 6-Tab 自测看板（单文件、可离线）
│   ├── live.html                # 在线演示用看板（脱敏示例数据）
│   ├── state_snapshot.json      # 看板数据源（仓库内为脱敏示例快照）
│   ├── BOT_INTEGRATION_GUIDE.md # 微信 / QQ / 钉钉 / 飞书 机器人接入说明
│   └── assets/                  # 配图、Logo、SVG 与 KaTeX 本地副本
├── data/                        # 数据目录（均为公开数据或本地可重建产物）
│   ├── universities/            # 内置公开高校研招档案（随仓库分发）
│   │   ├── registry.json        # 57 所院校详细档案索引（院校代码 → 院校信息）
│   │   ├── national_institutions.json / exam_subjects.json
│   │   ├── 北京/ 上海/ 天津/ …（20 个省级目录）
│   │   └── _sources/            # 公开数据源原始快照与出处说明
│   └── knowledge/               # 检索知识库（向量索引，运行时生成，仅本地）
├── build/ dist/                 # 构建产物（PyInstaller / 打包输出，可安全删除）
├── rust_ext/                    # Rust (PyO3) 原生加速扩展源码（可选构建）
└── tools/                       # 全部 Python 源码
    ├── ky_cli.py                # 主命令行入口，39 个子命令（表驱动分发）
    ├── tui_navigator.py         # 终端全景智能中枢 (TUI)
    ├── ky_gui.py / gui/         # PySide6 桌面可视化操作端
    ├── agent/                   # Agent 内核：沙箱 / 权限 / 记忆 / 生命周期钩子
    ├── skills/                  # 私教技能：判分 / 错题 / 组卷 / 变式 / 检索 / 切片
    ├── intelligence/            # 研招情报、考纲 Diff、证据链与引文溯源引擎
    ├── study_planner.py         # 个人化方案设计引擎与防疲劳预警
    ├── syllabus_manager.py      # 官方考纲智能匹配与切换管理器
    ├── exam_calendar.py         # 初试倒计时与考试日历
    ├── llm_client.py            # OpenAI 兼容端点客户端（重试 / 超时 / 解压容错）
    ├── update_dashboard.py      # 看板构建与同步（本地构建；--push 才提交推送）
    ├── build_package.py         # PyInstaller 打包（默认做内容级身份脱敏）
    ├── sync_publish.py          # 导出公开仓库副本（脱敏镜像）
    ├── privacy_policy.py        # 隐私策略与内容级脱敏规则的**单一事实源**
    ├── fsrs_scheduler.py        # FSRS 自适应复测调度器（全项目间隔计算唯一真源）
    ├── ky_io.py                 # 原子写 + 跨进程文件锁 + 只读模式闸门（所有落盘的统一入口）
    ├── note_lock.py             # 笔记只读锁定：frontmatter 声明 locked: true 后禁止被自动改写
    ├── protocol_loader.py       # 顶层协议加载器（兼容源码模式与 wheel 安装模式）
    ├── accel/                   # Rust 加速协同层：能力协商 + 已知缺陷黑名单 + 通用原语降级
    ├── evaluate_pipeline.py     # 离线评测：FSRS 校准度 (RMSE/LogLoss) 与引文忠实度
    ├── lint_check.py            # 零依赖静态检查（语法/裸except/未用导入/导入副作用）
    ├── check_dashboard.py       # 看板产物守卫（构建 + 产物 JS 语法 + 前端契约）
    ├── doctor.py                # 系统体检入口
    ├── test_ky_suite.py         # 全链路回归套件（本地开发用，不进入 wheel）
    └── test_new_features.py     # 新功能专项套件（本地开发用，不进入 wheel）
├── tests/                       # pytest 单元/进程层测试（CLI 入口、并发与原子性压测）
├── scripts/
│   ├── gui_real_session_check.py  # GUI 真实图形会话验证脚本（带看门狗，不留悬挂窗口）
│   └── legacy/                  # 历史开发脚本（不随仓库分发，仅本地参考）
```

> 说明 ①：`tools/` 下尚有 `llm_client` 之外的若干辅助模块（如 `verify_health.py`、
> `version.py`、`gui_launcher.py`、动画与 Logo 导出脚本等），此处只列出与日常使用
> 和二次开发最相关的部分；完整清单见 [CONTRIBUTING.md](CONTRIBUTING.md) 的架构全景树。
>
> 说明 ②：`test_ky_suite.py` / `test_new_features.py` / `simulate_workflow.py` /
> `build_svg_assets.py` 属开发工具，已在 `pyproject.toml` 中排除，不会随 wheel 分发。
>
> 仓库中所有 `*.example.md` / `*.template.md` 均为**脱敏模板**；与之同名的真实文件
> （如 `00_考研全科总战役规划.md`、各科 `_状态/` 与 `错题本/` 下的学情数据）
> 由本地 `ky plan` 与日常使用生成，已被 `.gitignore` 排除，**不会上云**。

---

## ⚠️ 使用注意事项

1. **先初始化，再使用**
首次克隆后务必运行 `python tools/init_workspace.py` 生成 `ky_config.json`，否则倒计时、任务编排与部分指令不可用。
2. **API Key 安全**
`ky_config.json` / `ky_history.json` 已被 `.gitignore` 保护，**切勿提交或分享**。
若不小心推送了含 Key 的文件，请立即在服务商处吊销并更换密钥。
3. **联网功能与离线可用性**
`ky scout`、`ky admission`、`ky watch`、`ky wechat`、`ky fetch` 等情报类命令需要连接网络；  
断网或受限网络下会给出明确提示。做题、判分、看板生成等核心功能**可部分离线使用**。
4. **大模型端点要求**
任何 OpenAI 兼容端点（DeepSeek / GLM / Qwen / Kimi / OpenAI / 本地 Ollama）均可接入；
但请注意部分第三方中转站存在 **\~60 秒网关响应超时**，推理型模型的长讲解会被中途掐断（表现为 `Remote end closed connection`）。遇到此类报错请优先换用官方端点或响应更快的模型。
5. **自命题科目需自行生成考纲**
`01-数学` / `02-英语` / `03-思想政治理论` 三门公共课考纲随项目内置；
专业课为院校自命题，需运行 `ky plan` 生成。若大纲科目与报考科目不符，系统会主动告警。
6. **资料版权与白名单门禁**
`参考资料/` 目录与所有 PDF 均已由 `.gitignore` 排除，不会上云。
请自行确保资料的合法来源，**不要公开传播受版权保护的教材与真题**。
AI 私教只从你放入的白名单资料出题，白名单为空时仅按官方考纲命题，绝不虚构书目。
7. **隐私边界**
个人学情状态（`_状态/*.md`、`学情档案.md`）、错题本、每日笔记与经验档案均在忽略名单内。
只要不绕过 Git 强推，这些数据不会被误上传。
8. **Rust 加速为可选（需本地编译，没有 pip extra）**
未编译 `ky_rust_ext` 原生扩展时，系统会自动降级为等价的纯 Python 实现，功能与结果一致，仅性能不同。
该扩展**未发布到 PyPI**，因此不存在 `pip install '.[fast]'` 这类安装方式（曾经声明的 `[fast]` extra 已移除）；需要加速时请在本地用 Maturin 从 `rust_ext/` 源码构建：

   ```bash
   pip install maturin
   cd rust_ext
   maturin develop --release     # 直接装进当前虚拟环境（开发用）
   # 或： maturin build --release  → 安装 target/wheels/ 下产出的 wheel（分发用）
   ```

9. **Windows 终端编码**
中文输出建议在 Windows Terminal 下运行，或先执行 `chcp 65001` 切换到 UTF-8 代码页，避免乱码。
10. **Python 版本**
低于 3.10 会因语言特性与类型标注报错，请升级到 3.11 / 3.12 获得最佳兼容性。
11. **提交 PR 前请自测**
运行 `python tools/test_ky_suite.py` 与 `python tools/test_new_features.py`，确认无失败后再提交。注意 `test_ky_suite.py` 会改写工作区用户数据，**默认拒绝在真实考生工作区运行**（exit 2）—— 请在干净副本里跑，或设 `KY_TEST_ALLOW_REAL_WORKSPACE=1` 显式放行（先备份 `ky_config.json`）。
更多排查思路见 [SETUP.md](SETUP.md) 与 [操作手册.md](操作手册.md) 第 7 章「常见突发场景速查」。
12. **看板默认离线，公式断网也能渲染**
`docs/index.html` 与 `docs/state_snapshot.json` 是**脱敏后的示例快照**（院校、专业等字段为占位符），仅用于展示看板效果；你在本地执行 `ky build` 生成的真实看板默认只落盘本地，不会被提交。
`ky build` 默认把 KaTeX 公式渲染资源内联为本地副本（`docs/assets/vendor/`，随仓库分发），
因此**地铁、图书馆破网、自习室断网**等场景下公式不会退化成 LaTeX 源码串。
需要改回境外 CDN 时：`ky build --cdn`，或设 `KY_VENDOR_MODE=cdn`。
13. **应用统计（025200 / 432 统计学）考生说明**
本项目已内置 `432 统计学`通用考纲框架（含参数估计、假设检验、方差分析与回归等 6 大模块），`ky plan` 选择自命题并填入 `432 统计学`即可自动写入 `04-专业课/考试大纲.md`，`ky map pro` 可直接生成 37 项考点图谱。
数学三（303）与英语二（204）均有官方考纲兜底；`ky variant <考点> --subject=pro` 可优先命中本地 `参考资料/`中的 432 真题。
14. **未收录院校的降级行为**
内置库收录 57 所详细档案 + 1,841 所基础名录（如云南大学 10673：211 / 双一流B类，以 registry.json 为准）。
若备选院校（如部分新增硕士点高校）未被收录，`ky scout / admission / compare` 会明确标注“未收录 / 待查 / 离线基准”，
不虚构复试线与报录比，报考前请务必以院校研究生院当年招生简章与专业目录为准。
15. **Fork 与二次分发**
若 Fork、镜像或二次分发本项目，请保留 [MIT License](LICENSE) 与作者署名，
并**不要移除 `.gitignore` 中的隐私规则**，否则可能导致个人学情数据被误传。

---

## ⚖️ 免责声明

在使用本项目前，请务必阅读并理解以下声明：

1. **本项目非官方产品**
考研学习链是**独立的开源个人项目**，与教育部、各招生单位、研招网及任何考试机构**没有任何隶属、授权或合作关系**。项目中的院校信息、专业目录与考纲内容均整理自公开渠道，不代表官方立场。
2. **AI 输出仅供参考，不构成建议**
私教给出的讲解、判分、组卷与备考建议由大语言模型生成，**可能存在错误或不严谨之处**。它**不构成**报考决策、志愿填报或考试作答的权威依据，请务必结合教材、真题与自身判断。
3. **招生信息以官方为准**
分数线、报录比、招生名额、自命题科目与复试要求等**可能随时变动**，且存在高校未收录或数据滞后的情况。报考前请**以目标院校研究生院当年发布的招生简章与专业目录为准**。
4. **资料版权由使用者负责**
本项目不提供、也不代为分发任何受版权保护的教材与真题。请自行确保放入 `参考资料/` 的资料来源合法，且**仅在个人学习范围内使用**。
5. **按“现状”提供，不承担使用后果**
本项目按 [MIT License](LICENSE) “原样”提供，作者不对使用本项目所产生的任何备考结果、数据损失或决策后果承担责任。

---

## 🔒 隐私优先与主流 AI Agent 接入

### 1. Local-First 本地优先隐私承诺

- 本项目严守 **Local-First** 隐私原则，做题草稿、个人笔记、错题详情及放在 `参考资料/` 中的个人版权资料**已全部由 `.gitignore` 保护，绝不上云**；
- 支持在纯离线环境下运行做题、考纲Diff、双校对标与状态机管理。

### 2. 主流 AI Agent 无缝接入

本项目采用标准开放的 Agentic Markdown 架构，原生适配市面主流 AI 智能体：

- **Google Antigravity**：原生适配，打开工作区输入 `数学报到` 自动完成协议加载与状态写回；
- **Cursor**：内置 [`.cursorrules`](.cursorrules)，`Ctrl + I` 发送口令即可自动批改；
- **Trae (字节跳动)**：在 Chat 中开启 Agent 模式，输入 `@workspace 专业课报到` 自动联动；
- **Cherry Studio**：将根目录 [`AGENTS.md`](AGENTS.md) 设为系统提示词，四科目录挂载为知识库；
- **微信 / QQ / 钉钉 / 飞书**：运行 `ky serve` 或 `ky clawbot`，随时随地在手机群聊中提问讲题。

---

## 🧪 自动化质量工程与开源协议

本项目包含覆盖全链路功能、权限沙箱、研招情报、Rust加速与真实 CLI 进程级 smoke test 的自动化回归套件：

```bash
# 运行全套自动化质量回归测试 (26 组全链路回归测试，共 305 项断言)
# ⚠️ 该套件会真实改写工作区用户数据，默认拒绝在真实考生工作区运行；
#    请在干净副本里跑，或设 KY_TEST_ALLOW_REAL_WORKSPACE=1 显式放行（先备份 ky_config.json）。
python tools/test_ky_suite.py

# 专项测试新增功能 (WeChat 检索、Rust 双模一致性、PySide6 桌面端、开放题判分等，共 130 项断言)
python tools/test_new_features.py

# pytest 单元与进程层测试（CLI 入口、并发与原子性、主题设计系统、看板资产、SVG 图标与设置面板等，共 1268 项）
python -m pytest -q
```

> [!NOTE]
> **测试计数已做成环境无关，但前提仍要写清**（下列数字为 2026-09-24 实测）：
>
> - `test_ky_suite.py`：26 组共 **305 项断言**。Git 工作区内为 305 通过 / 0 跳过；
> 干净检出（`git archive` 导出、无 `.git`）为 **300 通过 + 5 跳过 = 305** ——
> 「测试组 7 Git 隐私隔离」的 5 条断言需 `.git`，无 `.git` 时逐条记为跳过，故**两种环境总数恒为 305**。
> - `test_new_features.py`：**130 项断言**（0 跳过），与是否 Git 工作区无关。
> - `pytest tests/`：共 **1268 项**（完整工作区、已配 `study_plan` 的 `ky_config.json`、
> **含** `dist/` 构建产物，实测 1265 通过 + 3 跳过）。跳过项为联网测试未设
> `KY_LIVE_TEST=1`（2 条）与一条需特定 registry 探测串的守卫（1 条）。
> 在**无** `dist/` 的副本里跑，6 条打包断言会转为跳过 —— 收集总数不变，通过数下降。
> 在**无 `cat` 的 Windows 裸机**（Git usr\bin 未加入 PATH）上，2 条沙箱阴性对照会转为
> 跳过 —— 收集总数不变，通过数下降（该路径由 CI 的 Linux/macOS 作业与 Git 自带 coreutils 覆盖）。
> 在**公开副本**里跑还会少一整份 `tests/test_fix_publish_privacy.py`（私有工作区实测 106 项）：
> 它测的 `tools/sync_publish.py` / `tools/build_package.py` 在公开副本里是刻意保留的占位文件，
> 该测试只对私有工作区有意义，导出时按 `privacy_policy.PRIVATE_WORKSPACE_ONLY_PATHS` 剔除。
> ⚠️ 造副本时排除目录**必须锚定根级路径**（如 `--exclude=./dist`）：写成裸 `dist` 会连
> `docs/assets/vendor/katex/0.16.9/dist/` 一起排掉（KaTeX npm 包内部结构），导致 vendor 资产假缺失、
> `test_web_assets.py` 假失败 —— robocopy 的 `/XD "dist"` 与 tar 的 `--exclude=dist` 同样会踩。
>
> 改文档里的计数时，请**把对应环境一并写上**，否则下一个人会误判为「数字漂移」。

- **测试保障**：主套件 26 组测试集 **305 项断言** + 新功能专项 **130 项断言** + pytest 单元/进程层 **1265 项**（合计 **1700 项通过**，另有 3 项按环境跳过），全链路覆盖工业级 Agent Loop、权限沙箱、研招情报、多模型开放题判分等；
- **门禁脚本**：`python tools/lint_check.py`（零依赖静态检查，只卡 ERROR 级问题）、`python tools/check_dashboard.py`（看板产物守卫）已接入 CI；
- **CI 流水线**：内置 GitHub Actions 多平台 (Linux/Windows) 与多 Python 版本自动化测试保障；
- **开发者文档**：如需参与贡献或了解完整项目架构树，请参阅 [🛠️ 开发者与贡献指南 (CONTRIBUTING.md)](CONTRIBUTING.md)。

### 📦 独立发布包构建（开发者/打包者）

普通用户请直接下载 [Releases](#-开箱即用不懂技术也能直接用推荐) 现成的程序包。
如需自行构建（例如打一份**带自己报考方案**的私人包）：

```bash
python tools/build_package.py --dry-run                  # 预演：校验入口与资源清单
python tools/build_package.py --build                    # 构建独立免装目录 dist/KaoyanStudyChain/
python tools/build_package.py --build --keep-identity    # 保留个人报考方案（不打码）
```

- 产物**默认做内容级身份脱敏**（改写真实校名 / 专业 / 自命题科目 / 学情薄弱点），
  脱敏后校验 `*.py` 仍可编译，并对整个产物做残留自检 —— **检出真实身份即中止构建**。
- `ky_config.json` 缺失（贡献者 / CI）时无身份可脱敏，自动跳过；
  存在但 `study_plan` 是占位值时**直接报错**，堵住「脱敏照跑却一个字没改」的静默失效。
- 构建带 `--specpath=build/`，自动生成的 `.spec` 落到 gitignore 的 `build/`，
  不会覆盖仓库里手写的「路径无关」`KaoyanStudyChain.spec`。

---

## 🤝 交流与反馈 (Contact &amp; Community)

- 🐛 **问题反馈 / 功能建议**：[提交 GitHub Issue](https://github.com/moyetian/kaoyan_chain/issues)
- 🔧 **参与共建**：欢迎阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 后提交 Pull Request
- 📘 **详细实操通关指南**：请阅读 [操作手册.md](操作手册.md)
- 🐧 **QQ 号**：`296528868`
- 📮 **电子邮箱**：`moyetian@foxmail.com`

> 愿每一位披星戴月的考研人都能稳住节奏、拒绝内耗，一战成硕，顺利上岸！🌟

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源。
