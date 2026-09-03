# 考研学习看板

手机端查看四科学习进度、必背清单与薄弱点，用于碎片时间。

考试：**2026-12-20 上午 8:30–11:30**（初试 12/19–12/20）

---

## 这是什么

一个**静态网页生成器**。它读取你本地四科的 `_状态/` 笔记，生成一个自包含的 `docs/index.html`，
推到 GitHub 后可以用手机随时打开。

```
本地四科笔记（不上传）
   ├── C:\...\考研数学\数学知识讲解\_状态\
   ├── 02-英语\_状态\
   ├── C:\...\考研政治\政治学习计划\_状态\
   └── C:\...\考研专业课\[你的目标院校]\学习进度\
            ↓  build.py 读取并抽取章节
       docs/index.html （唯一上传的文件）
            ↓  git push
       GitHub → Cloudflare Pages / GitHub Pages
            ↓
          手机浏览器
```

> **只有渲染后的看板会被推送，四科的原始笔记、错题详情、真题 PDF 都留在本地。**

---

## 每天怎么用

在 Antigravity 里学完、四科都 `交作业` 归档之后：

**双击 `更新看板.bat`** —— 它会依次执行：生成 → 提交 → 推送。约 1 分钟后手机上就是最新的。

手动等价命令：

```bash
python build.py
git add -A && git commit -m "update" && git push
```

---

## 看板有四个页签

| 页签 | 内容 | 什么时候看 |
|---|---|---|
| 📋 **今日** | 四科今日任务 | 早上确认今天干什么；晚上核对有没有漏 |
| 🧠 **必背** | 数学公式默写卡 + 复发错误、政治易混帽子词 + 史纲时间线、英语长难句、807 核心公式 | ★ **碎片时间主力**：排队、等车、睡前 |
| 🎯 **薄弱** | 各科模块掌握度、错题重做队列、题型能力评估 | 每周复盘时看 |
| 📊 **数据** | 错因五分类、计算失误统计、量化指标 | 检查点判定时看 |

### 必背页签的「遮罩自测」

点顶部 **👁 开启遮罩自测**，公式列会被斜纹遮住 —— 先自己默写，再点格子逐个揭示。
这比单纯"看一遍"有效得多，且适合手机单手操作。

---

## 首次配置

### 1. 建私有 GitHub 仓库

在 GitHub 新建仓库（**选 Private**），然后：

```bash
cd 05-考研看板
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

### 2. 部署（推荐 Cloudflare Pages）

| 方案 | 私有仓库 | 费用 | 密码保护 |
|---|---|---|---|
| **Cloudflare Pages** ← 推荐 | ✅ 支持 | 免费 | ✅ Cloudflare Access 免费版 |
| GitHub Pages | ❌ 需 Pro | $4/月 | ❌ |
| 自己的云服务器 | ✅ | 已有 | ✅ nginx basic auth |

**Cloudflare Pages 步骤**：
1. dash.cloudflare.com → Workers & Pages → Create → Pages → Connect to Git
2. 授权并选择这个仓库
3. 构建设置：**Framework preset = None**，**Build command 留空**，**Build output directory = `docs`**
4. Deploy。之后每次 `git push` 自动重新部署

**加密码**（可选）：Cloudflare Zero Trust → Access → Applications → 添加你的 Pages 域名 → 规则设为"仅允许我的邮箱"。

### 3. 手机上加到主屏

用手机浏览器打开地址 → 分享 → **添加到主屏幕**。之后像 App 一样一点就开。

---

## 维护

### 改看板内容

编辑 `build.py` 顶部的 `SECTION_MAP`：

```python
"math": [
    ("_状态/薄弱点雷达.md",  "公式默写卡",  "memo"),
    #  相对路径              标题关键词      放到哪个页签
],
```

- `标题关键词` 用 `None` 表示整个文件
- 页签取值：`today` / `memo` / `weak` / `stat`
- 找不到的章节会自动跳过，不会报错

### 目录搬家了

改 `build.py` 顶部的 `MATH` / `ENG` / `POL` / `PRO` 四个路径。

### Python 路径

`更新看板.bat` 里写死了 `D:\Python\Python312\python.exe`。
如果 Python 换了位置，改那一行；找不到时会自动回退到 `py`。

---

## 注意

- 看板里**包含你的分数和错因**。仓库务必设为 **Private**，部署最好加 Cloudflare Access 密码。
- 页面用 CDN 加载 KaTeX 渲染数学公式。断网时公式会显示为原始 LaTeX，不影响其他内容。
- `build.py` 只读不写四科目录，不会动你的笔记。
