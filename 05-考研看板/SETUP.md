# 05-考研看板 · 模块架构与构建配置手册

> [!NOTE]
> **【全局提示】** 考研学习链是一体化工作区，若需查阅整个备考系统的部署、Daily SOP、终端私教使用及完整 Cloudflare/GitHub Pages 配置，请优先查阅根目录官方主手册：
> 👉 **[根目录主操作手册 (`../SETUP.md`)](../SETUP.md)**

本文档专为对 **考研看板构建引擎 (`build.py`)** 进行本地二次开发、自定义抽取规则或排查前端渲染问题的同学提供技术参考。

---

## 一、 模块核心定位

本模块是考研学习链的**静态网站生成器 (Static Site Generator)**：
- **数据源输入**：扫描 `01-数学/`、`02-英语/`、`03-思想政治理论/`、`04-专业课/` 中的最新学情 Markdown 状态文件；
- **核心构建引擎**：`build.py`（纯 Python 3.8+ 原生标准库编写，零 pip 依赖）；
- **产物输出**：
  - `docs/index.html`（单文件自包含 HTML5 页面，原生内嵌 5 大 Tab、3D 翻转卡与毛玻璃遮罩）；
  - `docs/state_snapshot.json`（学情脱敏状态快照，供公开环境或第三方工具消费）。

---

## 二、 本地独立编译与运行

在根目录下或本子目录内均可一键触发看板编译：

```bash
# 方式 1：使用 ky-cli 终端命令编译 (推荐)
ky build

# 方式 2：使用项目一键批处理 (Windows 双击即可)
更新看板.bat

# 方式 3：直接调用 Python 原生脚本编译
python 05-考研看板/build.py
```

编译成功后，双击打开 `docs/index.html` 即可在任意浏览器中离线查看看板。

---

## 三、 自定义章节提取规则 (`SECTION_MAP`)

`build.py` 通过顶部的 `SECTION_MAP` 字典定义从各科 Markdown 状态文件中提取哪些表格与内容块。

若您自行修改了各科 `_状态/薄弱点雷达.md` 或 `核心速记.md` 的二级标题，请同步检查并修改 `build.py` 中的匹配关键词：

```python
# build.py 中的核心映射表
SECTION_MAP = {
    # 抽取为必背 3D 翻转卡 (memo)
    "公式": {"tab": "memo", "type": "formula"},
    "必背": {"tab": "memo", "type": "table"},
    "帽子词": {"tab": "memo", "type": "table"},
    
    # 抽取为薄弱掌握度雷达与错题队列 (weak)
    "掌握度": {"tab": "weak", "type": "radar"},
    "错题": {"tab": "weak", "type": "queue"},
    
    # 抽取为学情数据与错因五分类 (stat)
    "错因": {"tab": "stat", "type": "metric"},
    "失误": {"tab": "stat", "type": "metric"},
}
```

---

## 四、 隐私边界与脱敏模式

为杜绝个人真实错题或做题草稿外泄，系统定义了严格的隐私边界：

1. **绝对不会被上传的资产**：
   - 四科原始草稿、每日作业全文、教材与真题大体积 PDF 等（全部被根目录 `.gitignore` 阻断在本地）；
2. **编译产物的公开脱敏 (`KY_SNAPSHOT_OPT_IN`)**：
   - 默认模式下，生成的 `state_snapshot.json` 包含详细学情；
   - 若需将仓库推送到公开 GitHub Pages，建议开启脱敏开关：
     ```bash
     # Windows PowerShell
     $env:KY_SNAPSHOT_OPT_IN="1"; python tools/update_dashboard.py --local

     # macOS / Linux
     KY_SNAPSHOT_OPT_IN=1 python tools/update_dashboard.py --local
     ```
     开启后，引擎会自动模糊处理学员真实错题描述与笔记，仅输出结构化百分比指标。

---

## 五、 常见构建与渲染排错

| 现象 | 可能原因 | 解决办法 |
|---|---|---|
| 页面能打开但数学公式显示为原始 LaTeX | 离线无网且 CDN 无法加载 | 无需担心，看板已内置 `fallbackMathUnicode` 符号降级解析器，关键公式仍可正常阅读 |
| 看板中的任务依然是昨天的旧数据 | 在保存状态文件之前就运行了构建 | 确认各科 `今日任务.md` 已保存后，再次运行 `ky build` |
| 某科目的特定表格没有出现在看板中 | 表格的 Markdown 表头格式不标准，或章节标题被修改 | 确保使用标准 `\| col1 \| col2 \|` 表格语法，并对照 `SECTION_MAP` 检查标题 |

---

> 📖 **完整全流程操作与多端 IM 机器人打通指南**，请参阅：👉 **[根目录主操作手册 (../SETUP.md)](../SETUP.md)**

