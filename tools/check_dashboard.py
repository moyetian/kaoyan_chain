# -*- coding: utf-8 -*-
"""
考研学习链 · 看板产物回归守卫 (Dashboard Artifact Check)

背景：看板（本地 ``docs/.local/index.html`` / 发布 ``docs/index.html``）是学员
手机主屏上的核心交付物，但其前端代码由 ``05-考研看板/build.py`` 以字符串模板
拼装，**没有任何编译期保护**。
历史上曾因模板里一段单引号字符串内嵌单引号属性，产生 ``SyntaxError``，
导致整页 ``<script>`` 失效 —— 页面照常打开、样式正常，但所有交互（页签/遮罩/
闪卡/图表/主题）全部无响应，且**能顺利通过当时的 CI**。

本脚本把该类问题变成可自动拦截的门禁：
  1. 重新构建看板（或检查既有产物）
  2. 抽取全部 ``<script>`` 块，若本机有 Node.js 则执行 ``node --check`` 语法校验
  3. 校验关键 DOM 契约（页签 data-p、KaTeX 降级函数、趋势图容器）
  4. 校验"空图标容器"（``<i></i>`` / 空的 ``.ei``）—— 图标迁移未完成时会留下空壳
  5. 重建走**本地完整模式**（``KY_SNAPSHOT_OPT_IN=0``，与 更新看板.bat / ky build
     一致）：本地跑守卫不会把考生的完整看板产物覆盖成脱敏版。发布脱敏由发布链路
     （sync_publish / update_dashboard --push / deploy-pages 三道闸）负责。
  6. **真浏览器运行时校验**：若本机装有 ``playwright-cli``，则用无头浏览器加载产物，
     逐页签点击后断言 —— 无 pageerror / console 报错、图标容器真正渲染成 SVG
     （历史上曾出现图标被 esc() 转义成裸文本、且其长 token 把 390px 手机视口撑破
     825px 的事故，node --check 与 DOM 契约检查均无法发现）。未装 playwright-cli
     时该阶段自动跳过；``--require-runtime`` 可将其变为硬性要求。

用法：``python tools/check_dashboard.py [--no-build] [--skip-runtime] [--require-runtime]``
退出码：0 = 通过；1 = 存在问题。
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
from pathlib import Path
from typing import List, Optional, Tuple

# [缺陷修复·GBK 控制台崩溃] 本脚本会打印 ✅/❌。Windows 默认控制台编码为
# cp936/GBK，直接 print 这些字符会抛 UnicodeEncodeError 并**让门禁本身崩掉**：
# CI 里因为显式设了 PYTHONIOENCODING=utf-8 而看不到，本地一跑就现形。
# 与 doctor.py / ky_cli.py 等保持一致，统一在 Windows 上切到 UTF-8 输出。
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = ROOT / "05-考研看板" / "build.py"
#: 静态检查的产物：**新构建产物**（本地完整模式，未跟踪的 ``docs/.local/``）
#: 在前，已提交的**发布快照**（根 ``docs/``）在后。旧实现检查内层
#: ``05-考研看板/docs/`` 与根 ``docs/`` —— 前者只由发布模式构建写入，检查它
#: 等于检查过期文件（本地入口的完整产物现统一落在 ``.local``，见 AGENTS.md）。
ARTIFACTS = [ROOT / "docs" / ".local" / "index.html", ROOT / "docs" / "index.html"]

#: 看板必须包含的前端契约（缺失即视为功能被破坏）
REQUIRED_MARKERS = [
    ('data-p="today"', "页签：今日"),
    ('data-p="memo"', "页签：必背"),
    ('data-p="weak"', "页签：错题"),
    ('data-p="stat"', "页签：进度"),
    # [W13-6] 图谱页已下沉为「进度」页内的二级入口：底栏不再有它的页签位，
    # 但两个字面量仍必须存在——data-p="map" 供 ky_suite S3-4 契约，
    # data-goto="map" 保证入口可达（防「删了页签又没留入口」的静默丢失）。
    ('data-p="map"', "图谱页（进度页二级入口）"),
    ('data-goto="map"', "图谱二级入口可达性"),
    ('data-p="radar"', "页签：考情"),
    ("fallbackMathUnicode", "KaTeX 离线降级解析器"),
    ("stat-trend", "7 日完成率趋势容器"),
]

#: 判定"空图标容器"的模式（图标迁移未完成时会命中）
EMPTY_ICON_PATTERNS = [
    (re.compile(r"<i>\s*</i>"), "空的导航图标 <i></i>"),
    (re.compile(r"<div class=['\"]ei['\"]>\s*</div>"), "空的空态图标 <div class='ei'></div>"),
]


def build_dashboard() -> bool:
    if not BUILD_SCRIPT.exists():
        print(f"[!] 未找到构建脚本: {BUILD_SCRIPT}")
        return False
    # [W13 收口·本地入口分模式] 显式完整模式：此前不传 env，走 snapshot_opt_in()
    # 缺省（=1 脱敏），本地跑一次守卫就把考生的完整看板产物覆盖成脱敏版
    # （今日任务正文/卡背答案消失）。与更新看板.bat / ky build 保持一致。
    # [R2 输出隔离补全] 完整产物含私人学情，须落在未跟踪的 docs/.local/
    # （与 ky build / update_dashboard / 各 .bat 一致）；否则守卫每次运行都会
    # 把根 docs/ 的**已提交脱敏快照**覆盖成完整版 —— 正是「误发布」风险源。
    proc = subprocess.run([sys.executable, str(BUILD_SCRIPT)],
                          cwd=str(BUILD_SCRIPT.parent), capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          env={**os.environ, "KY_SNAPSHOT_OPT_IN": "0",
                               "KY_DASHBOARD_OUTPUT_DIR": "docs/.local"})
    ok = proc.returncode == 0
    print(f"  {'✅' if ok else '❌'} 构建看板 (exit={proc.returncode})")
    if not ok:
        print((proc.stderr or proc.stdout or "")[-800:])
    return ok


def extract_scripts(html: str) -> List[str]:
    return re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)


def check_js_syntax(html: str, label: str) -> Tuple[bool, str]:
    """用 Node.js 校验内嵌 JS 语法；本机无 node 时返回 (True, '跳过')。"""
    node = shutil.which("node")
    if not node:
        return True, "本机无 Node.js，跳过 JS 语法校验（CI 环境请确保 node 可用）"
    blocks = extract_scripts(html)
    if not blocks:
        return False, "未找到任何 <script> 块"
    tmp = ROOT / f".dashboard_check_{abs(hash(label)) % 100000}.js"
    try:
        tmp.write_text("\n;\n".join(blocks), encoding="utf-8")
        proc = subprocess.run([node, "--check", str(tmp)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            return True, f"{len(blocks)} 个 script 块语法通过"
        err = (proc.stderr or "").strip().splitlines()
        return False, "JS 语法错误: " + " ".join(err[:3])
    finally:
        tmp.unlink(missing_ok=True)


# ════════════════════════════════════════════════════════════════
# 6) 真浏览器运行时校验（可选依赖：playwright-cli + 系统浏览器）
# ════════════════════════════════════════════════════════════════

#: 注入浏览器的探针。约定：返回 JSON；``__URL__`` 由 Python 侧替换。
#: 注意保持 Python 字符串内嵌 JS 的兼容——不用模板字符串、不用反引号。
RUNTIME_PROBE_JS = r"""
async page => {
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + (e && e.message ? e.message : String(e))));
  page.on('console', m => {
    if (m.type() === 'error' && !/favicon\.ico/.test(m.text())) errors.push('console: ' + m.text());
  });
  await page.goto('__URL__', { waitUntil: 'load', timeout: 30000 });
  await page.waitForTimeout(600);
  /* [W13-6] 页签收敛为 5 键（图谱已下沉为进度页二级入口）。逐键点击并
     逐键断言面板可见：改造前是 if (b) 静默跳过——按钮缺失时探针照样
     报绿，与「防静默降级」的目标相悖。 */
  const tabs = [];
  for (const t of ['today', 'memo', 'weak', 'stat', 'radar']) {
    const found = await page.evaluate(tt => {
      const b = document.querySelector('[role="tab"][data-p="' + tt + '"]');
      if (!b) return false;
      b.click();
      return true;
    }, t);
    await page.waitForTimeout(220);
    const paneOn = await page.evaluate(tt => {
      const p = document.getElementById('p-' + tt);
      return !!(p && p.classList.contains('on'));
    }, t);
    tabs.push({ tab: t, found: found, paneOn: paneOn });
  }
  const rawSvg = await page.evaluate(() => {
    const bad = [];
    document.querySelectorAll('*').forEach(el => {
      if (el.tagName === 'SCRIPT' || el.children.length) return;
      if ((el.textContent || '').indexOf('<svg') >= 0) bad.push(String(el.className || el.tagName.toLowerCase()));
    });
    return bad;
  });
  const icons = await page.evaluate(() => {
    const hosts = Array.from(document.querySelectorAll('.si, .ic'));
    return { total: hosts.length, withSvg: hosts.filter(e => e.querySelector('svg')).length };
  });
  /* 主题预设选择器：点一圈必须按顺序走完 5 套预设，且选择要落盘。
     node --check 只能证明语法没错，证明不了「点了没反应」。 */
  const theme = await page.evaluate(async () => {
    const order = (typeof KY_PRESETS === 'undefined') ? [] : KY_PRESETS.map(function(p){ return p.k; });
    const first = document.documentElement.getAttribute('data-t');
    const seen = [];
    for (let i = 0; i < order.length; i++) {
      const b = document.getElementById('th-btn');
      if (b) b.click();
      await new Promise(r => setTimeout(r, 60));
      seen.push(document.documentElement.getAttribute('data-t'));
    }
    return {
      first: first,
      order: order,
      seen: seen,
      saved: (function(){ try { return localStorage.getItem('kytheme'); } catch (e) { return null; } })(),
      bg: getComputedStyle(document.documentElement).getPropertyValue('--bg').trim(),
      label: ((document.querySelector('#th-btn .th-name') || {}).textContent || '').trim()
    };
  });
  /* [W13-6] 图谱二级入口可达性（下沉防静默降级，三重断言）：
     ① 切到「进度」页后入口可见；② 点击后 #p-map 显示、宿主页签「进度」
     保持 aria-selected、入口 aria-current=page、kytab 落盘为 map；
     ③ 预置 kytab=map 刷新 → 仍在图谱页（恢复路径对二级入口可达）。 */
  const mapEntry = await page.evaluate(async () => {
    const go = document.querySelector('[data-goto="map"]');
    if (!go) return { found: false };
    const statTab = document.querySelector('[role="tab"][data-p="stat"]');
    if (statTab) statTab.click();
    await new Promise(r => setTimeout(r, 150));
    const visible = !!(go.offsetParent || go.getClientRects().length);
    go.click();
    await new Promise(r => setTimeout(r, 250));
    const pane = document.getElementById('p-map');
    return {
      found: true,
      visible: visible,
      paneOn: !!(pane && pane.classList.contains('on')),
      statSelected: !!(statTab && statTab.getAttribute('aria-selected') === 'true'),
      current: go.getAttribute('aria-current'),
      stored: (function(){ try { return localStorage.getItem('kytab'); } catch (e) { return null; } })(),
    };
  });
  await page.evaluate(() => { try { localStorage.setItem('kytab', 'map'); } catch (e) {} });
  await page.reload({ waitUntil: 'load' });
  await page.waitForTimeout(500);
  const mapRestore = await page.evaluate(() => {
    const pane = document.getElementById('p-map');
    const entry = document.querySelector('[data-goto="map"]');
    return {
      paneOn: !!(pane && pane.classList.contains('on')),
      current: entry ? entry.getAttribute('aria-current') : null,
    };
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const overflow = await page.evaluate(() => ({
    vw: document.documentElement.clientWidth,
    scrollW: document.documentElement.scrollWidth,
  }));
  await page.setViewportSize({ width: 1280, height: 900 });
  return { errors: errors, rawSvg: rawSvg, icons: icons, overflow: overflow, theme: theme,
           tabs: tabs, mapEntry: mapEntry, mapRestore: mapRestore };
}
"""

_SESSION = "kydash-guard"

#: 依次尝试的浏览器渠道（Windows 常见 Edge 优先；找不到则退回 CLI 默认值）
_RUNTIME_BROWSERS = ["msedge", "chrome", None]


def _pcli(*args: str, timeout: int = 120) -> Tuple[bool, str]:
    """调用 playwright-cli 专用会话；返回 (成功, 输出)。

    Windows 上 ``playwright-cli`` 解析为 ``.cmd`` 垫片；subprocess 经 cmd.exe
    执行它会把探针 JS 里的括号/引号等元字符转义破坏（实测报 ``SyntaxError:
    Unexpected token ')'``）。因此解析出垫片背后的 node 入口脚本，直接以
    ``node playwright-cli.js`` 方式调用，彻底绕开 cmd.exe。
    """
    exe = shutil.which("playwright-cli")
    if not exe:
        return False, "playwright-cli 不在 PATH"
    cmd = [exe, "-s=" + _SESSION, *args]
    exe_path = Path(exe)
    if exe_path.suffix.lower() in (".cmd", ".bat"):
        node_js = exe_path.parent / "node_modules" / "@playwright" / "cli" / "playwright-cli.js"
        node = shutil.which("node")
        if node_js.exists() and node:
            cmd = [node, str(node_js), "-s=" + _SESSION, *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(ROOT),
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"调用失败: {e}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out


def _start_docs_server(docs_dir: Path):
    """在本机随机端口起一个临时静态服务器（file:// 下 localStorage 行为不可靠）。"""

    class _Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):  # 静默访问日志
            pass

    factory = functools.partial(_Quiet, directory=str(docs_dir))
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), factory)
    server.daemon_threads = True
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    return server, th


def check_runtime(artifact: Path) -> Tuple[Optional[bool], str]:
    """真浏览器运行时校验。

    Returns:
        (True, 明细)  —— 全部断言通过；
        (False, 明细) —— 发现运行时缺陷（阻塞级）；
        (None, 原因)  —— 环境不支持，跳过。
    """
    if not shutil.which("playwright-cli"):
        return None, "本机未安装 playwright-cli，跳过运行时校验（npm i -g @playwright/cli）"

    server = th = None
    try:
        server, th = _start_docs_server(artifact.parent)
        port = server.server_address[1]
        url = f"http://127.0.0.1:{port}/{artifact.name}"

        opened = False
        last_err = ""
        for browser in _RUNTIME_BROWSERS:
            args = ["open"]
            if browser:
                args.append("--browser=" + browser)
            args.append(url)
            ok, out = _pcli(*args, timeout=90)
            if ok:
                opened = True
                break
            last_err = out.strip().splitlines()[-1] if out.strip() else "未知错误"
        if not opened:
            _pcli("close", timeout=30)
            return None, f"无法启动浏览器，跳过运行时校验（{last_err[:120]}）"

        probe_js = RUNTIME_PROBE_JS.replace("__URL__", url)
        ok, out = _pcli("run-code", probe_js, timeout=180)

        result = None
        if ok:
            lines = out.splitlines()
            for i, line in enumerate(lines):
                if line.strip() == "### Result" and i + 1 < len(lines):
                    try:
                        result = json.loads(lines[i + 1])
                    except json.JSONDecodeError:
                        result = None
                    break

        if not ok or result is None:
            return False, "运行时探针未能返回结果（可能是浏览器会话异常）: " + (out.strip()[-300:] if out.strip() else "无输出")

        problems = []
        if result.get("errors"):
            problems.append(f"运行时报错 × {len(result['errors'])}: " + " ｜ ".join(result["errors"][:3]))
        if result.get("rawSvg"):
            problems.append(f"图标被转义成裸 SVG 文本 × {len(result['rawSvg'])}: " + ", ".join(result["rawSvg"][:4]))
        icons = result.get("icons") or {}
        if icons.get("total", 0) > 0 and icons.get("withSvg") != icons.get("total"):
            problems.append(f"图标容器未渲染成图形: {icons.get('withSvg')}/{icons.get('total')}")
        ov = result.get("overflow") or {}
        if ov and ov.get("scrollW", 0) > ov.get("vw", 0) + 1:
            problems.append(f"390px 手机视口横向溢出: 内容宽 {ov.get('scrollW')} > 视口 {ov.get('vw')}")

        th = result.get("theme") or {}
        order = th.get("order") or []
        if not order:
            problems.append("主题预设清单缺失（KY_PRESETS 未注入）")
        else:
            seen = th.get("seen") or []
            start = order.index(th.get("first")) if th.get("first") in order else 0
            expected = [order[(start + i + 1) % len(order)] for i in range(len(order))]
            if seen != expected:
                problems.append(f"主题按钮轮换异常: 期望 {expected} 实得 {seen}")
            if seen and th.get("saved") != seen[-1]:
                problems.append(f"主题选择未落盘: localStorage={th.get('saved')}")
            if not th.get("bg"):
                problems.append("切换后 --bg 取不到值（预设 CSS 规则未生效）")

        # [W13-6] 5 键页签逐键断言（防「按钮缺失静默跳过」的假绿）
        tabs = result.get("tabs") or []
        if len(tabs) != 5:
            problems.append(f"页签点击探针数量异常: {len(tabs)}（应为 5）")
        else:
            for item in tabs:
                if not item.get("found"):
                    problems.append(f"页签缺失: {item.get('tab')}")
                elif not item.get("paneOn"):
                    problems.append(f"页签点击后面板未显示: {item.get('tab')}")

        # [W13-6] 图谱二级入口可达性（下沉防静默降级）
        me = result.get("mapEntry") or {}
        if not me.get("found"):
            problems.append('图谱二级入口缺失（[data-goto="map"] 不存在——页签下沉未落地）')
        else:
            if not me.get("visible"):
                problems.append("图谱二级入口在「进度」页内不可见")
            if not me.get("paneOn"):
                problems.append('图谱二级入口点击后 #p-map 未显示（下沉不可达）')
            if not me.get("statSelected"):
                problems.append("进入图谱后宿主页签「进度」未保持 aria-selected（层级悬空）")
            if me.get("current") != "page":
                problems.append(f'图谱二级入口 aria-current 未置位: {me.get("current")!r}')
            if me.get("stored") != "map":
                problems.append(f'图谱二级入口未写入 kytab: {me.get("stored")!r}')
        mr = result.get("mapRestore") or {}
        if not mr.get("paneOn"):
            problems.append("kytab=map 刷新恢复失败：重载后图谱页未显示")
        elif mr.get("current") != "page":
            problems.append(f"kytab 恢复后二级入口 aria-current 未同步: {mr.get('current')!r}")

        if problems:
            return False, "；".join(problems)
        detail = (f"逐页签无报错（5 键逐键断言通过）；{icons.get('total', '?')} 个图标容器全部渲染成 SVG；"
                  f"图谱二级入口可达、kytab=map 刷新恢复正确；"
                  f"390px 视口无横向溢出（内容宽 {ov.get('scrollW')}）；"
                  f"主题按钮按 {len(order)} 套预设轮换正常（当前 {th.get('first')}）")
        return True, detail
    finally:
        _pcli("close", timeout=30)
        if server is not None:
            server.shutdown()
            server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="看板产物回归守卫")
    parser.add_argument("--no-build", action="store_true", help="跳过重新构建，仅检查既有产物")
    parser.add_argument("--skip-runtime", action="store_true", help="跳过真浏览器运行时校验")
    parser.add_argument("--require-runtime", action="store_true",
                        help="环境不支持运行时校验时按失败处理（CI 严格模式）")
    args = parser.parse_args()

    problems: List[str] = []
    if not args.no_build:
        if not build_dashboard():
            problems.append("看板构建失败")

    for artifact in ARTIFACTS:
        rel = artifact.relative_to(ROOT)
        print(f"\n── 检查 {rel} ──")
        if not artifact.exists():
            problems.append(f"{rel} 不存在")
            print("  ❌ 文件不存在")
            continue
        html = artifact.read_text(encoding="utf-8")

        ok, msg = check_js_syntax(html, str(rel))
        print(f"  {'✅' if ok else '❌'} JS 语法: {msg}")
        if not ok:
            problems.append(f"{rel}: {msg}")

        for marker, desc in REQUIRED_MARKERS:
            if marker not in html:
                problems.append(f"{rel}: 缺少{desc} ({marker})")
                print(f"  ❌ 缺少{desc}")
        if all(m in html for m, _ in REQUIRED_MARKERS):
            print(f"  ✅ 前端契约完整（{len(REQUIRED_MARKERS)} 项）")

        for pattern, desc in EMPTY_ICON_PATTERNS:
            found = pattern.findall(html)
            if found:
                problems.append(f"{rel}: {desc} × {len(found)}")
                print(f"  ❌ {desc} × {len(found)}")
        if not any(p.search(html) for p, _ in EMPTY_ICON_PATTERNS):
            print("  ✅ 无空图标容器")

    # ── 6) 真浏览器运行时校验（产物级，跑一次即可）──
    if args.skip_runtime:
        print("\n── 运行时校验：已按要求跳过 ──")
    else:
        print("\n── 真浏览器运行时校验 ──")
        # [R2] 探针打在**新构建产物**上（ARTIFACTS[0] = docs/.local）：模板级
        # 运行时回归（如历史「图标被转义成裸文本」事故）只体现在新产物里；
        # 已提交的发布快照是旧构建的产物，用它探测会掩盖回归。CI 无
        # playwright-cli 时自动跳过（不判失败）。
        ok, msg = check_runtime(ARTIFACTS[0])
        if ok is True:
            print(f"  ✅ 运行时: {msg}")
        elif ok is False:
            print(f"  ❌ 运行时: {msg}")
            problems.append(f"运行时校验: {msg}")
        else:
            print(f"  ⏭️  {msg}")
            if args.require_runtime:
                problems.append(f"运行时校验被要求强制执行: {msg}")

    print("\n=== 看板守卫汇总 ===")
    if problems:
        for p in problems:
            print(f"  ❌ {p}")
        return 1
    print("  ✅ 看板产物全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
