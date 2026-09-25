# -*- coding: utf-8 -*-
"""
图标系统：语义名 → Lucide 图标（单一真源）

[为什么需要这一层] 改造前四端用 emoji 当图标（📅 💬 🎯 🧠…）：跨平台渲染不一致
（Windows / Android / iOS 三套字形）、风格廉价，且无法随主题换色（emoji 是彩色
位图字形，`currentColor` 对它无效）。本模块把「语义名 → Lucide 图标名」固定为
单一真源，各端消费同一份子集 sprite：

  * Web  ``<svg class="ic"><use href="#i-today"/></svg>``
         —— 构建期由 ``build.py`` 把 sprite **内联**进 HTML（``{{ICON_SPRITE}}``），
            因为 ``file://`` 下跨文件 ``<use href="assets/icons.svg#...">`` 会被
            浏览器的同源策略拒绝，外链方案在离线看板上不可用。
  * GUI  用 ``QIcon`` 从 sprite 的 symbol 抽取渲染（``tools/gui/...``）。
  * CLI/TUI  终端不支持 SVG，用 Unicode 符号兜底（各端自行映射，不在本模块）。

图标来源：Lucide（https://lucide.dev，ISC 协议，抽取时锁定 v1.48.0）。
子集 sprite 落盘 ``docs/assets/icons.svg``，保持离线自包含；
``extract_sprite()`` 是可复现的抽取工具（需要时用 ``--source`` 指向官方全量
sprite 重新生成）。

用法校验：``missing_icons()`` 供测试断言 sprite 与 ICONS 清单一致 ——
防止「清单里加了图标但忘了重新抽取 sprite」这类静默缺失。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

#: 子集 sprite 相对工作区根的路径
SPRITE_REL = Path("docs") / "assets" / "icons.svg"

#: 抽取时锁定的 Lucide 版本（写入产物注释，便于追溯）
LUCIDE_VERSION = "1.48.0"

#: 语义名 → Lucide 图标名（单一真源；改这里必须重跑 extract_sprite）
ICONS: Dict[str, str] = {
    # ── 导航（看板侧栏 / GUI rail）────────────────────────────
    "today": "calendar",
    "memo": "brain",
    "weak": "target",
    "stats": "chart-column",
    "map": "map",
    "radar": "radar",
    # ── 头部与系统 ───────────────────────────────────────────
    "sun": "sun",
    "moon": "moon",
    "sparkles": "sparkles",
    "settings": "settings",
    "refresh": "refresh-cw",
    "close": "x",
    "plus": "plus",
    "search": "search",
    "info": "info",
    # ── 状态与反馈 ───────────────────────────────────────────
    "check": "check",
    "check-circle": "circle-check",
    "alert": "triangle-alert",
    "alert-circle": "circle-alert",
    "star": "star",
    "flame": "flame",
    "award": "award",
    "hourglass": "hourglass",
    "timer": "timer",
    "clock": "clock",
    # ── 数据与趋势 ───────────────────────────────────────────
    "trend-up": "trending-up",
    "trend-down": "trending-down",
    # ── 学习与内容 ───────────────────────────────────────────
    "book": "book-open",
    "pen": "pen-line",
    "chat": "message-circle",
    "clipboard": "clipboard-list",
    "file": "file-text",
    "list": "list-checks",
    "folder": "folder",
    "cap": "graduation-cap",
    "eye": "eye",
    "eye-off": "eye-off",
    # ── 操作 ─────────────────────────────────────────────────
    "upload": "upload",
    "send": "send",
    "external": "external-link",
    "chevron-right": "chevron-right",
    "play": "play",
}

#: 用途说明（供 DESIGN.md 与评审；键与 ICONS 一致）
ICON_USAGE: Dict[str, str] = {
    "today": "今日任务页签",
    "memo": "必背页签 / 背诵自测",
    "weak": "薄弱点页签 / 错题雷达",
    "stats": "数据统计页签 / KPI 卡",
    "map": "知识图谱页签",
    "radar": "研招考情页签",
    "sun": "浅色主题",
    "moon": "深色主题",
    "sparkles": "动效开关 / 亮点标记",
    "settings": "设置入口",
    "refresh": "刷新 / 重新生成",
    "close": "关闭 / 清除",
    "plus": "新增",
    "search": "检索",
    "info": "说明 / 提示",
    "check": "完成 / 通过",
    "check-circle": "达成 / 达标",
    "alert": "警告 / 注意",
    "alert-circle": "错误 / 异常",
    "star": "重点标记",
    "flame": "连续打卡 / 热度",
    "award": "成就 / 里程碑",
    "hourglass": "等待 / 待办",
    "timer": "倒计时 / 计时",
    "clock": "时间 / 时长",
    "trend-up": "上升趋势",
    "trend-down": "下降趋势",
    "book": "知识 / 教材",
    "pen": "作答 / 写作",
    "chat": "对话 / 私教",
    "clipboard": "任务清单 / 组卷",
    "file": "文档 / 试卷",
    "list": "列表 / 清单",
    "folder": "目录 / 归档",
    "cap": "院校 / 学位",
    "eye": "查看 / 显示",
    "eye-off": "遮罩 / 隐藏",
    "upload": "上传 / 导入",
    "send": "发送",
    "external": "外部链接",
    "chevron-right": "进入 / 下一级",
    "play": "开始 / 执行",
}

#: 匹配官方全量 sprite 里的 symbol 块
_SYMBOL_RE = re.compile(
    r'<symbol id="(?P<id>[^"]+)"[^>]*>(?P<body>.*?)</symbol>', re.DOTALL)

#: 匹配已抽取子集 sprite 里的 symbol id
_SUBSET_ID_RE = re.compile(r'<symbol id="i-([^"]+)"')


def sprite_path(workspace_root: Optional[Path] = None) -> Path:
    """子集 sprite 的绝对路径（默认以本模块上溯三级为工作区根）。"""
    root = Path(workspace_root) if workspace_root else Path(__file__).resolve().parent.parent.parent
    return root / SPRITE_REL


def load_sprite(workspace_root: Optional[Path] = None) -> str:
    """读取子集 sprite 文本；不存在时抛 FileNotFoundError（调用方决定兜底）。"""
    return sprite_path(workspace_root).read_text(encoding="utf-8")


def missing_icons(sprite_text: str) -> List[str]:
    """返回清单里「sprite 中缺失」的语义名（空列表 = 一致）。"""
    present = set(_SUBSET_ID_RE.findall(sprite_text))
    return [name for name in ICONS if name not in present]


def extract_sprite(source_text: str) -> str:
    """从官方全量 sprite 抽取 ICONS 子集，返回子集 sprite 文本。

    :raises KeyError: 某个 Lucide 图标名在源 sprite 中不存在（清单写错或
        版本变更导致改名 —— 宁可响亮失败，也不要静默少图标）。
    """
    found: Dict[str, str] = {}
    for m in _SYMBOL_RE.finditer(source_text):
        found[m.group("id")] = m.group("body").strip()

    blocks: List[str] = []
    for semantic, lucide in ICONS.items():
        body = found.get(lucide)
        if body is None:
            raise KeyError(
                f"Lucide 源 sprite 中不存在图标 {lucide!r}（语义名 {semantic!r}）——"
                f"请核对 ICONS 清单或 Lucide 版本（当前锁定 {LUCIDE_VERSION}）")
        blocks.append(
            f'  <symbol id="i-{semantic}" data-lucide="{lucide}" '
            f'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.75" stroke-linecap="round" '
            f'stroke-linejoin="round">\n    {body}\n  </symbol>')

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" '
        f'style="display:none">\n'
        f'  <!-- 图标子集 · 来源 Lucide v{LUCIDE_VERSION}（ISC 协议）'
        f' · 由 tools/theme/icons.py 抽取 · 请勿手工编辑 -->\n'
        + "\n".join(blocks) + "\n</svg>\n")


def write_sprite(source_path: Path, out_path: Optional[Path] = None) -> Path:
    """一次性工具：从官方全量 sprite 生成子集并落盘，返回产物路径。"""
    out = Path(out_path) if out_path else sprite_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(extract_sprite(Path(source_path).read_text(encoding="utf-8")),
                   encoding="utf-8")
    return out


def _main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Lucide sprite 子集抽取/校验（图标系统单一真源工具）")
    parser.add_argument("--source", type=str, default="",
                        help="官方全量 sprite.svg 路径（提供则重新抽取）")
    parser.add_argument("--out", type=str, default="",
                        help="产物路径（默认 docs/assets/icons.svg）")
    parser.add_argument("--check", action="store_true",
                        help="只校验现有子集是否覆盖 ICONS 清单")
    args = parser.parse_args(argv)

    if args.source:
        out = write_sprite(Path(args.source), Path(args.out) if args.out else None)
        print(f"已生成 {out}（{len(ICONS)} 个图标）")
        return 0

    text = load_sprite()
    missing = missing_icons(text)
    if missing:
        print("缺失图标: " + ", ".join(missing))
        return 1
    print(f"子集完整：{len(ICONS)} 个图标全部在位")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())


__all__ = [
    "ICONS",
    "ICON_USAGE",
    "LUCIDE_VERSION",
    "SPRITE_REL",
    "extract_sprite",
    "load_sprite",
    "missing_icons",
    "sprite_path",
    "write_sprite",
]
