# 更新日志 (CHANGELOG)

本文件记录**考研学习链 (Kaoyan AI Study Chain)** 的版本变更与升级方式。
版本号以 `pyproject.toml` / `tools/version.py` 为准，亦可运行：

```bash
python -c "import sys; sys.path.insert(0, 'tools'); from version import get_version; print(get_version())"
```

---

## [未发布] — 2026-09-22

> 2.8.0 发布之后累积的修复，尚未并入版本号。以下按主题归并。

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

## [2.8.0] — 2026-09-20（当前发布版本）

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

