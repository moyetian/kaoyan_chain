# 考研学习链 (Kaoyan Study Chain) · 部署与环境配置指引

本文档指导你如何将本项目部署到 GitHub、配置 GitHub Pages 或 Cloudflare Pages 移动端看板，并与你的 AI 辅助编程环境（Antigravity、Cursor、VS Code、Claude Code 等）协同运作。

---

## 1. 极速环境依赖（零额外三方包）

本项目看板构建器 `build.py` 采用 **Python 3.8+ 原生标准库**（`re`, `json`, `html`, `datetime`, `pathlib`），**无需任何 `pip install` 繁琐依赖**，开箱即用。

只需确保本地已安装 Python：
```bash
python --version
# 或
py --version
```

---

## 2. 首次推送到 GitHub

### 步骤 A：在 GitHub 上创建仓库
1. 打开 [GitHub 新建仓库页面](https://github.com/new)；
2. 输入仓库名（如 `kaoyan-study-chain`）；
3. **推荐选择 Private（私有仓库）**，因为错题记录和个人复习分数属于个人隐私数据；
4. **不要勾选** "Initialize this repository with a README"（本地已具备完备结构）。

### 步骤 B：本地初始化与关联远程
在本项目根目录下打开 PowerShell 终端：

```powershell
# 1. 初始化本地仓库
git init

# 2. 切换主分支
git branch -M main

# 3. 关联你的远程仓库
git remote add origin https://github.com/<你的GitHub用户名>/<你的仓库名>.git

# 4. 提交所有代码与笔记
git add -A
git commit -m "feat: initialize kaoyan-study-chain repository"

# 5. 推送到 GitHub
git push -u origin main
```

---

## 3. 配置移动端在线看板

你可以通过 **GitHub Pages** 或 **Cloudflare Pages** 获得一个完全属于你自己的手机端看板网址。

### 方案 A：GitHub Pages 自动部署（内置 Actions 工作流）

本项目已在 `.github/workflows/deploy-pages.yml` 内置了自动构建与发布流水线：

1. 进入 GitHub 仓库页面 -> 点击 **Settings** -> 侧边栏选择 **Pages**；
2. 在 **Build and deployment** 下：
   - **Source** 下拉菜单选择 **GitHub Actions**；
3. 之后只要推送更新，GitHub Actions 就会自动执行 `build.py` 并将编译好的 `docs/` 发布到 Pages；
4. 发布完成后，页面上方会显示你的访问网址（例如：`https://<username>.github.io/<repo>/`）。

> [!NOTE]
> 如果仓库设为 Private，免费版 GitHub 账户可能需要升级为 Pro 才能开启 Pages。如果你使用的是免费版私有仓库，强烈推荐使用**方案 B（Cloudflare Pages）**。

### 方案 B：Cloudflare Pages 部署（推荐私有仓库使用，完全免费）

Cloudflare Pages 支持直接连接 GitHub 私有仓库并免费部署：

1. 登录 [Cloudflare Dashboard](https://dash.cloudflare.com/) -> 进入 **Workers & Pages** -> 点击 **Create application** -> 选择 **Pages** -> **Connect to Git**；
2. 授权访问你的 GitHub 账号并选中本仓库；
3. 构建配置填入：
   - **Framework preset**: `None`
   - **Build command**: `python 05-考研看板/build.py`
   - **Build output directory**: `docs`
4. 点击 **Save and Deploy** 即可完成部署！
5. **安全保护（可选）**：在 Cloudflare Zero Trust -> Access -> Applications 中，可以一键为该域名增加邮箱验证码登录，防止未授权访问。

---

## 4. 手机端“PWA 级”体验设置

1. 使用手机浏览器（iOS Safari 或 Android Chrome/Edge）打开部署好的网页；
2. 点击浏览器的 **分享**（或菜单按钮） -> 选择 **添加到主屏幕**（Add to Home Screen）；
3. 手机桌面将生成一个独立的考研看板图标，打开即为全屏自适应 App 体验：
   - 支持单手切换今日任务、必背卡片、薄弱雷达与数据统计；
   - 支持离线 KaTeX 数学公式渲染；
   - 支持遮罩自测默写模式。

---

## 5. 日常使用与复盘工作流

1. **日常学习**：
   - 在 VS Code / Antigravity / Cursor 中打开本仓库；
   - 输入口令（如 `数学报到`、`派题`、`交作业`），AI 私教会按照你的 `AGENTS.md` 协议进行互动，并将错题与掌握度记录到本地状态；
2. **隐私隔离**：
   - 你的实际做题记录和状态受 `.gitignore` 保护，只保留在你的本地电脑中，不会意外泄漏到 GitHub；
3. **更新看板**：
   - 每天复盘完毕，双击运行根目录下的 `更新看板.bat`（或在终端运行 `python 05-考研看板/build.py`）；
   - 脚本会自动解析最新的本地状态，重新编译 `docs/index.html` 并推送至 GitHub，手机端约 1 分钟后自动同步最新自测卡片与掌握度！