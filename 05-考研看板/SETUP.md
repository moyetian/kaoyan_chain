# 05-考研看板 · 模块架构与构建配置手册

> [!NOTE]
> **【全局提示】** 考研学习链是一体化工作区，若需查阅整个备考系统的部署、Daily SOP、终端私教使用及完整 Cloudflare/GitHub Pages 配置，请优先查阅根目录官方主手册：
> 👉 **[根目录主操作手册 (`../SETUP.md`)](../SETUP.md)**

本文档专为对 **考研看板构建引擎 (`build.py`)** 进行本地二次开发、自定义抽取规则或排查前端渲染问题的同学提供技术参考。

---

## 一、 模块核心定位

本模块是考研学习链的**静态网站生成器 (Static Site Generator)**：
- **数据源输入**：扫描 `01-数学/`、`02-英语/`、`03-思想政治理论/`、`04-专业课/` 中的最新学情 Markdown 状态文件；
- **核心构建引擎**：`build.py`（Python 3.10+ 标准库）；数学、PDF、图像和 OCR 增强技能按需安装根目录 `requirements.txt` 中的依赖；
- **产物输出**：
  - `docs/index.html`（单文件自包含 HTML5 页面，原生内嵌 6 大 Tab、3D 翻转卡与毛玻璃遮罩）；
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

## 三、 自定义章节提取规则 (`SECTIONS`)

看板不做关键词猜测：`05-考研看板/web/config.py` 中的 `SECTIONS` 字典，按**科目**声明「从哪个文件、抓哪个二级标题、生成哪类卡片」，`build.py` 只负责按这张表取数与渲染。

每个条目是一个四元组 `(文件相对路径, 章节标题, 卡片类型, 附加参数)`；章节标题写 `None` 表示整篇解析（`今日任务` 用）：

```python
# 05-考研看板/web/config.py 中的核心映射表（节选）
SECTIONS = {
    "math": [
        ("_状态/今日任务.md", None, "today", {}),
        ("_状态/薄弱点雷达.md", "公式默写卡", "memo", {"mode": "formula", "front": 1}),
        ("_状态/薄弱点雷达.md", "模块掌握度雷达", "weak", {"front": 0}),
        ("_状态/薄弱点雷达.md", "错因五分类", "stat", {"label": 1, "value": 4}),
    ],
    # eng / pol / pro 三科同理；mode_b（双专业课）会额外追加 "pro2"
}
```

卡片类型共四种：`today`（今日任务清单）、`memo`（必背翻转卡）、`weak`（薄弱雷达与错题队列）、`stat`（学情指标）。

> [!IMPORTANT]
> `memo` / `weak` / `stat` 三类章节的内容**必须是标准 Markdown 表格**（`| 列1 | 列2 |` 形式）。
> 若写成无序列表或纯段落，看板会**静默丢弃该章节的全部卡片**——页面不报错，只是卡片凭空消失。

若您自行修改了各科 `_状态/薄弱点雷达.md` 等状态文件的二级标题，请同步修改 `web/config.py` 中对应的标题字符串；`SECTIONS` 的科目键需与 `SUBJECTS` 保持一致。

---

## 四、 隐私边界与脱敏模式

为杜绝个人真实错题或做题草稿外泄，系统定义了严格的隐私边界：

1. **绝对不会被上传的资产**：
   - 四科原始草稿、每日作业全文、教材与真题大体积 PDF 等（全部被根目录 `.gitignore` 阻断在本地）；
2. **编译产物的公开脱敏 (`KY_SNAPSHOT_OPT_IN`)**：
   - 默认模式下，生成的 `state_snapshot.json` 自动脱敏，可直接用于公开 GitHub Pages；
   - 仅需在本地调试且明确接受隐私风险时，设置 `KY_SNAPSHOT_OPT_IN=0` 生成完整快照：
     ```bash
     # Windows PowerShell
     $env:KY_SNAPSHOT_OPT_IN="0"; python tools/update_dashboard.py --local

     # macOS / Linux
     KY_SNAPSHOT_OPT_IN=0 python tools/update_dashboard.py --local
     ```
     默认构建即为脱敏模式；只有明确设置 `KY_SNAPSHOT_OPT_IN=0` 才会保留完整学情。

---

## 五、 常见构建与渲染排错

| 现象 | 可能原因 | 解决办法 |
|---|---|---|
| 页面能打开但数学公式显示为原始 LaTeX | 离线无网且 CDN 无法加载 | 无需担心，看板已内置 `fallbackMathUnicode` 符号降级解析器，关键公式仍可正常阅读 |
| 看板中的任务依然是昨天的旧数据 | 在保存状态文件之前就运行了构建 | 确认各科 `今日任务.md` 已保存后，再次运行 `ky build` |
| 某科目的特定表格没有出现在看板中 | 表格的 Markdown 表头格式不标准，或章节标题被修改 | 确保使用标准 `\| col1 \| col2 \|` 表格语法，并对照 `web/config.py` 中的 `SECTIONS` 检查标题 |

---

> 📖 **完整全流程操作与多端 IM 机器人打通指南**，请参阅：👉 **[根目录主操作手册 (../SETUP.md)](../SETUP.md)**

