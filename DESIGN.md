# DESIGN.md —— 考研学习链 · 四端设计系统规范

> 本文件是 CLI / GUI / TUI / Web 四端**共用的设计规范**，与 `tools/theme/` 的
> token 单一真源一一对应。改视觉先改这里或 `tools/theme/`，再让编译器把变更
> 渲染到各端 —— 不要在任何一端手写颜色、字号、阴影。

---

## 0. 一条原则

**「一套 token + 三个编译器」**：`tools/theme/tokens.py` 是唯一真源，
`compile_web.py`（CSS 变量）/ `compile_qt.py`（QSS）/ `compile_ansi.py`（终端色）
把同一组语义 token 渲染成各端产物。四端只允许消费 token，不允许自造视觉常量。

```
tools/theme/tokens.py ──┬── compile_web.py  → docs/index.html 的 CSS 变量
                        ├── compile_qt.py   → GUI 的 QSS
                        ├── compile_ansi.py → 纯文本 TUI 的 ANSI 调色板
                        └── icons.py        → docs/assets/icons.svg（Lucide 子集）
```

---

## 1. 色板

### 1.1 语义色（每套预设一套取值，共 5 套：dark / light / eye-green / pink / hc）

| Token | 语义 | 用在哪 |
|---|---|---|
| `bg` | 页面底色 | 最外层背景 |
| `surf` / `surf2` / `surf3` | 表面三级 | 卡片 / 卡片内嵌块 / 更深的嵌块 |
| `fg` / `mut` | 正文 / 次要文字 | 文字只用这两个（不要自造灰度） |
| `line` | 分隔线 | 边框、分割线 |
| `acc` / `acc-sub` / `on-acc` | 主色 / 主色淡化底 / 主色上的文字 | 强调、选中、主按钮 |
| `acc-hover` / `acc-press` | 主色悬停 / 按下 | 交互态（派生自动） |
| `focus-ring` | 焦点环 | 键盘导航可见性 |
| `ok` / `warn` / `bad` | 成功 / 警示 / 错误 | 状态语义**唯一来源** |

### 1.2 学科色板（图表 / 进度环 / 科目 chip）

| Token | 建议映射 | 说明 |
|---|---|---|
| `chart-1` | 英语 | 蓝 |
| `chart-2` | 专业课 | 绿 |
| `chart-3` | 政治 | 琥珀 |
| `chart-4` | 备用 / 第四科 | 粉 |

- 四色对 `surf` 的对比度**必须 ≥ 3:1**（非文字界面元素门限），由
  `contrast.py` 在主题加载时自动把关，五套预设实测最低 3.8:1。
- 图表色**不随主色覆盖而变**（独立于 `acc`）—— 用户换主色时图表语义不乱。

### 1.3 阴影（elevation）

| Token | 层级 | 用在哪 |
|---|---|---|
| `elev-1` | 贴地 | 卡片默认态、输入框 |
| `elev-2` | 悬浮 | 卡片 hover、下拉、浮层 |
| `elev-3` | 模态 | 对话框、命令面板 |

> `sh` / `sh2` / `sh-3` 是 `elev-1` / `elev-2` / `elev-3` 的**兼容别名**
> （既有 QSS/CSS 消费方零改动），新代码一律用 `elev-*`。

---

## 2. 字阶（type-scale）

| Token | 尺度（font-scale=1.0） | 用在哪 |
|---|---|---|
| `fs-hero` | 40px | 首屏大数字（倒计时、总分） |
| `fs-title` | 20px | 卡片标题、页面标题 |
| `fs-body` | 14px | 正文 |
| `fs-caption` | 12px | 辅助说明、标签、时间戳 |

- 全部随 `font-scale` 联动（用户调字号时整链缩放）。
- 旧 token `fs-xl/lg/base/sm`（18/16/13/12）保留兼容，**新代码用上表四档**。
- **W13-4 补档**（只补缺档，**不改任何现值**）：`fs-xs`(11px) / `fs-md`(13px) /
  `fs-2xl`(26px) / `fs-3xl`(34px) 已入库，供后续批次消费。`fs-md` 与 `fs-base`
  值重叠（均 13px）——刻意保留两者，`fs-md` 为 v4.0 命名对齐。v4.0 建议的
  `fs-base`=14 / `fs-xl`=20 / `fs-hero`=52 与本项目现值（13/18/40）不同，
  **值收敛留后续批次**。
- 大数字一律加 `font-variant-numeric: tabular-nums`（等宽数字，跳动不抖）。
- 字体栈见 `font-family` token；项目**不内置字体文件**，回落系统栈
  （Segoe UI / PingFang SC / Microsoft YaHei），跨平台一致但不保证字形完全一致。
- `font-num`：等宽数字字体栈（JetBrains Mono → 平台系统等宽 → 中文等宽回落
  Sarasa Mono SC / Noto Sans Mono CJK SC），与 `tabular-nums` 配合用于
  倒计时、分数等会跳动的数字。

---

## 3. 间距网格（space）

| Token | 值 | 典型用途 |
|---|---|---|
| `space-1` | 4px | 图标与文字之间 |
| `space-2` | 8px | 同组元素之间 |
| `space-3` | 12px | 卡片内边距（紧凑） |
| `space-4` | 16px | 卡片内边距（标准） |
| `space-5` | 24px | 卡片之间 |
| `space-6` | 32px | 区块之间 |
| `space-7` | 40px | 大区块分隔（W13-4 补档） |
| `space-8` | 56px | 页面级留白（W13-4 补档） |

- 固定 4px 基准栅格，**不随 density 缩放**；`density` 只影响组件内边距
  `pad / pad-sm / pad-lg`（QSS 用）。
- **偏差表（W13-4 记录）**：v4.0 的 `space-5`=20px 与本项目既有 `space-5`=24px
  语义冲突 → 本项目**整档跳过 20**，序列为 4/8/12/16/24/32/40/56。
  消费方不得假定「4 的倍数序列连续」。

### 3.1 圆角档位（W13-4 补档）

| Token | 值（无单位） | 典型用途 |
|---|---|---|
| `radius` | 16 | 卡片（默认） |
| `radius-sm` | 8 | 按钮、chip |
| `r-xl` | 20 | 大容器、对话框 |
| `r-md` | 12 | 中间档（分段控件等） |
| `r-pill` | 999 | 胶囊（标签、药丸按钮） |

- `r-*` 与 `radius`/`radius-sm` **同制**：值是无单位数字。QSS 侧用 `-px`
  派生副本（`radius-px` / `radius-sm-px` / `r-xl-px` / `r-md-px` / `r-pill-px`），
  Web 侧用 `calc(var(--r-xl) * 1px)` 自行拼单位（与既有 `--radius` 消费方式一致）。

### 3.2 边框与焦点环组合（W13-4 补档）

- `hair`：`1px solid {line}` 的合成值 —— 「发丝边框」的单一写法
  （Web `border:var(--hair)`；QSS 直接内联）。
- `ring`：焦点环 box-shadow 组合值，派生期由 `focus-w` + `bg` + `focus-ring`
  合成：`0 0 0 {focus-w}px {bg}, 0 0 0 {focus-w+2}px {focus-ring}`。
  `focus-ring`（色）与 `focus-w`（宽）仍是单一真源 —— 改色/改宽时 `ring`
  随之重派生（用户显式给值时以用户值为准）。

---

## 4. 图标系统

- **图标集**：Lucide（https://lucide.dev，ISC 协议，抽取锁定 v1.48.0）。
- **单一真源**：`tools/theme/icons.py` 的 `ICONS`（语义名 → Lucide 名，43 个；
  W13-5 新增 `shield` —— 题源徽章语义，为后续题源溯源展示铺路）。
- **产物**：`docs/assets/icons.svg`（子集 sprite，symbol id 形如 `i-today`，
  带 `data-lucide` 记录原图标名）。
- **尺寸/描边 token**：`icon-sm`(16px) / `icon-md`(20px) / `icon-lg`(24px) /
  `icon-stroke`(1.75)。图标颜色一律 `currentColor`（随文字色，自动适配主题）。
- **各端消费**：
  - Web：构建期把 sprite **内联**进 HTML（`{{ICON_SPRITE}}`），引用
    `<svg class="ic"><use href="#i-today"/></svg>`。
    *为什么必须内联*：`file://` 下跨文件 `<use href="assets/icons.svg#...">`
    会被浏览器同源策略拒绝，外链方案在离线看板上不可用。
  - GUI：`QIcon` 从 sprite 抽取渲染（P2 落地）。
  - CLI / TUI：终端不支持 SVG，用 Unicode 符号兜底（各端自行映射）。
- **禁止**：新增 emoji 当图标（跨平台字形不一致、无法换色）。

---

## 5. 动效

| Token | 值 | 用途 |
|---|---|---|
| `dur-fast` | 180ms | 悬停、按下等即时反馈 |
| `dur-base` | 240ms | 展开、切换 |
| `dur-slow` | 320ms | 入场、大块位移 |
| `ease-std` | cubic-bezier(.2,.8,.2,1) | 标准缓动 |
| `ease-emph` | cubic-bezier(.3,1.4,.5,1) | 强调缓动（轻微回弹） |

- 所有动效**必须**包在 `prefers-reduced-motion: reduce` 的关闭分支里
  （Web 看板已有 `fx-toggle` 开关 + 媒体查询）。
- 不做无限循环动画（备考场景要长时间盯屏）。

---

## 6. 组件规范（状态机）

四端同一组件在不同技术栈下**状态语义必须一致**：

### 6.1 卡片（Card）

| 状态 | 视觉 |
|---|---|
| 默认 | `surf` 底 + 1px `line` 边 + `radius` + `elev-1` |
| 悬停 | 边框转 `acc-line`，阴影升 `elev-2`，位移 0（不做上浮，避免布局抖动） |
| 焦点 | `focus-ring` 2px 外环（键盘可达性） |
| 激活/选中 | 左侧 3px `acc` 强调条 + `acc-sub` 底 |

### 6.2 按钮（Button）

| 类型 | 视觉 |
|---|---|
| 主按钮 | `acc` 底 + `on-acc` 字 + `radius-sm` |
| 次按钮 | `surf2` 底 + `line` 边 + `fg` 字 |
| 幽灵按钮 | 无底无边，`mut` 字，悬停 `surf2` 底 |
| 禁用 | 不透明度 0.5，无悬停响应 |

### 6.3 导航（Nav）

| 状态 | 视觉 |
|---|---|
| 默认项 | `mut` 字 + 图标，高度 ~40px |
| 激活项 | `acc` 字 + `acc-sub` 底 + 左侧 3px `acc` 条（**禁止**整块巨型高亮） |
| 分组标题 | `fs-caption` + `mut` + 全大写/字距加大 |

### 6.4 空状态（Empty State）

任何列表/图表**禁止裸奔**，必须包含三件套：
1. 图标（`icon-lg`，`mut` 色）；
2. 一句话说明「这里会有什么」；
3. 一个 CTA（告诉用户下一步动作，如「去 Agent 发报到」）。

### 6.5 信息架构词表（W13 新增）

四端共享的两组**唯一词表** —— 新增页面 / 面板条目时从这里取词，不得另造同义词：

**命令面板四桶**（GUI Ctrl+K，顺序即标题行出现顺序；真源
`tools/gui/views/nav_rail.py` 的 `PALETTE_GROUP_ORDER`，14 条 = 4 视图 + 10 工具）：

| 桶 | 条目 |
|---|---|
| 日常 | 私教对话 / 今日任务 / 任务打卡 |
| 自测 | 错题本 / 靶向组卷 / 同源变式 |
| 情报 | 研招情报 / 考纲Diff / 院校侦察 / 双校对标 / 简章监控 / 公众号检索 |
| 系统 | 切片入库 / 看板更新 |

**看板 5 域**（Web 底栏页签，顺序即底栏顺序；真源
`05-考研看板/web/template.html` 的 `.bar`）：

| 域 | `data-p` | 承载内容 |
|---|---|---|
| 今日 | `today` | 今日四科任务与打卡 |
| 必背 | `memo` | 遮罩自测卡片（公式 / 单词 / 帽子词） |
| 错题 | `weak` | 薄弱点与错题队列 |
| 进度 | `stat` | 掌握度雷达与趋势统计（含「知识图谱」二级入口） |
| 考情 | `radar` | 研招情报与考情趋势 |

> 词表变更必须同步消费端：四桶改 `nav_rail.PALETTE_GROUP_ORDER` 并跑
> `tests/test_gui_redesign.py` 的分组契约；5 域改模板 `.bar` 并跑
> `tools/check_dashboard.py`（运行时 5 键逐键断言 + 二级入口可达）。

---

## 7. 四端映射

| 概念 | Web | Qt/GUI | 纯文本 TUI | textual TUI |
|---|---|---|---|---|
| 颜色 | `var(--acc)` | `{{acc}}` 占位符 | ANSI 转义（`compile_ansi`） | `$accent` 主题变量 |
| 字号 | `var(--fs-title)` | `{{fs-title}}` | 不适用 | textual 字号类 |
| 间距 | `var(--space-4)` | `{{pad-lg}}` | 不适用 | CSS padding |
| 图标 | `<use href="#i-today">` | QIcon(sprite) | Unicode 符号 | Unicode 符号 |
| 阴影 | `var(--elev-1)` | 不支持（用边框替代） | 不适用 | 不支持 |

---

## 8. 扩展指南

### 加一个 token
1. 在 `tokens.py` 的 `STRUCTURE_TOKENS`（结构类）或预设 `extra`（颜色类）里加；
2. 需要派生就写进 `derive_tokens()`；
3. 若是颜色且需可读性把关，加进 `contrast.py` 的 `REQUIRED_PAIRS` 或校验循环；
4. 跑 `py -m pytest tests/test_theme.py tests/test_design_system.py`。

### 加一个图标
1. 在 `icons.py` 的 `ICONS` 里加「语义名 → Lucide 名」；
2. 重新抽取：`py tools/theme/icons.py --source <官方全量 sprite.svg>`；
3. 校验：`py tools/theme/icons.py --check`（测试也会断言清单与 sprite 一致）。

### 加一套主题预设
1. 在 `tokens.py` 的 `PRESETS` 里用 `_base(...)` 构造（给核心色 + `elev-1/2/3` + `chart-1..4`）；
2. 加进 `PRESET_ORDER`；
3. 跑 `validate_all_presets()`（不达标会拒绝加载并回退默认）。

---

## 9. 门禁与测试

| 门禁 | 位置 | 阈值 |
|---|---|---|
| 正文对比度 | `contrast.py` `REQUIRED_PAIRS` | ≥ 4.5:1 |
| 大字/状态色/图标 | 同上 | ≥ 3.0:1 |
| 学科色板 | 同上（`CHART_KEYS` 循环） | ≥ 3.0:1 |
| 模板占位符完整性 | `compile_qt.py` `render_qss` | 缺 token 直接报错 |
| 图标清单一致性 | `tests/test_design_system.py` | sprite 必须覆盖 `ICONS` |
| 非法单位连缀 | `compile_qt.py` | 产物含 `pxpx` 直接报错 |

跑法：`py -m pytest tests/test_theme.py tests/test_design_system.py -q`
