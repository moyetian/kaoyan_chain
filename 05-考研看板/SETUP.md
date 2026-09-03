# 配置指南：私有 GitHub 仓库 + Cloudflare Pages

> 一次性配置，约 15 分钟。配完之后每天由 Antigravity 自动推送，你只管在手机上看。

---

## 为什么是这个组合

看板里有你的**分数、错因、薄弱点**（数学 62、英语 26、各科正确率），不适合公开。

| | 私有仓库 | 费用 | 密码保护 |
|---|---|---|---|
| **Cloudflare Pages** | ✅ | **免费** | ✅ 免费版 Access |
| GitHub Pages | ❌ 需 Pro | $4/月 | ❌ |

GitHub Pages 对私有仓库要收费，Cloudflare Pages 不要 —— 这是唯一的理由。

---

## 第一步 · 建私有 GitHub 仓库（5 分钟）

1. 打开 https://github.com/new
2. **Repository name**：`kaoyan-dashboard`（随意，但别用中文）
3. **★ 选中 Private**（这一步别选错）
4. **不要**勾选 "Add a README file"、"Add .gitignore"、"Choose a license" —— 本地已经有了，勾了会冲突
5. 点 **Create repository**

创建后页面会显示仓库地址，形如：
```
https://github.com/你的用户名/kaoyan-dashboard.git
```

---

## 第二步 · 把本地仓库推上去（3 分钟）

在 Antigravity 里按 <kbd>Ctrl</kbd>+<kbd>`</kbd> 打开终端，执行：

```bash
cd 05-考研看板
git remote add origin https://github.com/你的用户名/kaoyan-dashboard.git
git push -u origin main
```

**首次 push 会弹出浏览器让你登录 GitHub 授权**（Git Credential Manager 自动处理），
授权一次之后以后都不用再输密码。

> 如果没弹窗、而是要求输入 username/password：GitHub 已不支持密码推送，
> 需要去 https://github.com/settings/tokens 生成一个 **Personal Access Token（classic）**，
> 勾选 `repo` 权限，然后把 token 当密码粘进去。

**验证**：刷新 GitHub 仓库页面，应该能看到 `build.py`、`docs/index.html` 等文件。

---

## 第三步 · Cloudflare Pages 部署（5 分钟）

1. 注册/登录 https://dash.cloudflare.com
2. 左侧菜单 → **Workers & Pages** → **Create** → 选 **Pages** 标签 → **Connect to Git**
3. 点 **Connect GitHub**，授权 Cloudflare 访问。授权范围选 **Only select repositories** → 只勾 `kaoyan-dashboard`
4. 选中该仓库 → **Begin setup**
5. 构建配置**照抄下面这三项**：

| 字段 | 填什么 |
|---|---|
| Framework preset | **None** |
| Build command | **留空**（什么都不填） |
| Build output directory | **`docs`** |

6. 点 **Save and Deploy**

约 1 分钟后会给你一个地址，形如：
```
https://kaoyan-dashboard.pages.dev
```

**之后每次 `git push`，Cloudflare 会自动重新部署**，不需要任何操作。

---

## 第四步 · 加密码保护（可选但强烈建议，5 分钟）

`*.pages.dev` 地址虽然不会被搜索引擎收录，但知道地址的人就能打开。加一道锁：

1. Cloudflare 控制台左侧 → **Zero Trust**（首次进入会让你起一个 team name，随便取）
2. **Access** → **Applications** → **Add an application** → 选 **Self-hosted**
3. 填写：
   - Application name：`考研看板`
   - Session Duration：**1 month**（免得天天验证）
   - Application domain：填你的 `kaoyan-dashboard.pages.dev`
4. 下一步 **Add policy**：
   - Policy name：`only me`
   - Action：**Allow**
   - Include → **Emails** → 填你自己的邮箱
5. 保存

之后打开看板会先要求邮箱验证码，验证一次管一个月。

---

## 第五步 · 手机上加到主屏（1 分钟）

用手机浏览器打开看板地址：

- **iPhone Safari**：底部分享按钮 → 下滑找到「添加到主屏幕」
- **Android Chrome**：右上角三个点 → 「添加到主屏幕」

之后桌面上就有一个图标，点开即是看板，跟 App 一样。

---

## 完成后的日常

```
在 Antigravity 学完 → 四科分别「交作业」
        ↓
AI 批改 → 写回状态文件 → 自动运行 auto-update.bat
        ↓
生成看板 → git push → Cloudflare 自动部署（约1分钟）
        ↓
手机点开主屏图标 → 看今日任务 / 必背 / 薄弱点
```

**你什么都不用做。**

如果哪天 AI 忘了运行，或者你想手动刷新：双击 `更新看板.bat` 即可。

---

## 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| `push failed - remote not configured` | 第二步没做 | 执行 `git remote add origin ...` |
| push 时反复要密码 | 用了密码而非 token | 去 GitHub 生成 PAT（见第二步注释） |
| Cloudflare 部署成功但页面 404 | Build output directory 填错 | 必须是 `docs`，不是 `/docs` 也不是空 |
| 页面能开但公式显示成 `\frac{...}` | 断网，KaTeX CDN 没加载 | 联网后刷新即可 |
| 看板数据是旧的 | AI 在写回状态文件**之前**跑了脚本 | 手动双击 `更新看板.bat` 重新生成 |
| 某科内容块消失了 | 该科笔记的章节标题改了 | 改 `build.py` 里 `SECTION_MAP` 的关键词 |

---

## 隐私边界（明确一下）

**会上传的**：`docs/index.html` —— 即看板页面本身，含各科今日任务、必背清单、薄弱点表格、错因统计。

**不会上传的**：四科的原始笔记、每日批改全文、错题本详情、真题与教辅 PDF、题源核验记录。
`build.py` 只读取这些文件并抽取指定章节，从不复制原文件。

如果你觉得某个章节不该出现在看板上，删掉 `build.py` 里 `SECTION_MAP` 中对应的那一行即可。
