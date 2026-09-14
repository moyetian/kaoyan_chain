# -*- coding: utf-8 -*-
"""GUI 真实图形会话验证（审查报告 §四-2 专项）

与 test_new_features.py 测试组 C 的区别：那里用 ``QT_QPA_PLATFORM=offscreen``
虚拟渲染；本脚本在**真实 Windows 图形会话**中启动 MainWindow，实际展示窗口、
遍历 4 个页签、往返切换主题、刷新三条数据链路，并对窗口做真实屏幕截图。

用法：``py scripts/gui_real_session_check.py``
退出码：0 = 全部通过；1 = 存在失败。截图落盘 ``scripts/gui_shots/``。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 关键：显式清掉离屏平台，走真实图形会话
os.environ.pop("QT_QPA_PLATFORM", None)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

SHOTS = ROOT / "scripts" / "gui_shots"
SHOTS.mkdir(parents=True, exist_ok=True)

results = []


def rec(name, ok, detail=""):
    results.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from tools.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()

    # 看门狗：任何一步卡死都在 20s 后强制退出，绝不悬挂会话
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(lambda: (print("[watchdog] 强制退出"), app.quit()))
    watchdog.start(20_000)

    steps = {"i": 0}

    def grab_screen(tag: str) -> str:
        """真实屏幕截图（不是离屏渲染）。"""
        try:
            screen = app.primaryScreen()
            pix = screen.grabWindow(int(win.winId()))
            path = SHOTS / f"{tag}.png"
            pix.save(str(path))
            return path.name
        except Exception as e:  # 截图失败不算功能失败，但要留痕
            rec(f"截图 {tag}", False, f"grab 异常: {e}")
            return ""

    def step():
        i = steps["i"]
        steps["i"] += 1
        try:
            if i == 0:
                win.show()
                win.raise_()
                win.activateWindow()
            elif i == 1:
                rec("真实会话: 窗口已展示", win.isVisible() and win.width() > 300,
                    f"visible={win.isVisible()} size={win.width()}x{win.height()}")
                rec("真实会话: 窗口标题", "考研学习链" in win.windowTitle(), win.windowTitle())
                rec("真实会话: 10 个功能卡片", len(win._feature_buttons) == 10, str(len(win._feature_buttons)))
                rec("真实会话: 4 个页签", win.tab_widget.count() == 4, str(win.tab_widget.count()))
                grab_screen("01_今日页")
            elif 2 <= i <= 5:
                idx = i - 2
                win.tab_widget.setCurrentIndex(idx)
                names = [win.tab_widget.tabText(k) for k in range(4)]
                app.processEvents()
                rec(f"页签切换 #{idx}", True, names[idx])
                grab_screen(f"0{i + 1}_页签{idx}")
            elif i == 6:
                theme_before = app.styleSheet()[:60]
                win._toggle_theme()
                app.processEvents()
                theme_after = app.styleSheet()[:60]
                rec("主题切换（真实会话）", theme_before != theme_after,
                    f"{len(theme_before)}B -> {len(theme_after)}B")
                grab_screen("06_主题切换后")
                win._toggle_theme()  # 切回
            elif i == 7:
                win._load_today_task_progress()
                win._refresh_error_tab()
                win._refresh_intel_tab()
                app.processEvents()
                rec("三条数据链路刷新（今日/错题/情报）", True)
                grab_screen("07_刷新后")
            elif i == 8:
                rec("会话收尾: 正常关闭", True)
                watchdog.stop()
                app.quit()
        except Exception as e:
            rec(f"步骤 {i}", False, f"异常: {e}")
            watchdog.stop()
            app.quit()

    timer = QTimer()
    timer.timeout.connect(step)
    timer.start(700)  # 每 700ms 推进一步，给真实窗口留出渲染时间
    app.exec()

    ok = all(r["ok"] for r in results)
    (SHOTS / "result.json").write_text(
        json.dumps({"passed": ok, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n=== GUI 真实会话验证: {'通过' if ok else '存在失败'} ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
