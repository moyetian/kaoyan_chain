# -*- coding: utf-8 -*-
"""主题 L2 旋钮设置面板

暴露 5 个可即时预览的旋钮，写入 QSettings，点「应用」即时生效（无需重启）：

  * **预设** —— dark / light / eye-green / pink / hc（下拉选）
  * **主色** —— 自定义 accent 色（选色器 + 可清空回退预设原色）
  * **圆角** —— 0~32px（QSpinBox）
  * **密度** —— 0.7~1.3（QDoubleSpinBox，影响 padding）
  * **字号** —— 0.8~1.4（QDoubleSpinBox，影响 fs-lg/base/sm）

L2 约定见 ``tools/theme/tokens.py`` 的 docstring。
"""

from __future__ import annotations

try:  # pragma: no cover - 取决于运行方式
    from theme import list_presets, PRESET_ORDER
    from gui import theme_apply
except ImportError:  # pragma: no cover
    from tools.theme import list_presets, PRESET_ORDER  # type: ignore
    from tools.gui import theme_apply  # type: ignore

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
    QWidget,
)

#: 各旋钮的默认值（与 tokens.py STRUCTURE_TOKENS 对齐）
_DEFAULTS = {"radius": 16, "density": 1.0, "font_scale": 1.0}


class SettingsDialog(QDialog):
    """主题 L2 旋钮设置对话框。"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.setWindowTitle("主题设置")
        self.setMinimumWidth(380)
        self._win = win

        prefs = theme_apply.read_prefs()
        current_preset = prefs.get("preset") or win._theme.name
        self._initial = dict(prefs)          # 供「取消」回退

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # ── 预设 ──────────────────────────────────────────────
        self.preset_combo = QComboBox()
        for key, name, _mode in list_presets():
            self.preset_combo.addItem(name, key)
        idx = self.preset_combo.findData(current_preset)
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)
        form.addRow("预设:", self.preset_combo)

        # ── 主色 ──────────────────────────────────────────────
        acc_row = QHBoxLayout()
        self.acc_edit = QLineEdit(str(prefs.get("acc", "")))
        self.acc_edit.setPlaceholderText("留空 = 用预设原色")
        self.acc_edit.setMaximumWidth(120)
        acc_btn = QPushButton("选色…")
        acc_btn.clicked.connect(self._pick_color)
        acc_clear = QPushButton("清除")
        acc_clear.clicked.connect(lambda: self.acc_edit.clear())
        acc_row.addWidget(self.acc_edit)
        acc_row.addWidget(acc_btn)
        acc_row.addWidget(acc_clear)
        acc_widget = QWidget()
        acc_widget.setLayout(acc_row)
        form.addRow("主色:", acc_widget)

        # ── 圆角 ──────────────────────────────────────────────
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(0, 32)
        self.radius_spin.setValue(_to_float(prefs.get("radius"), _DEFAULTS["radius"]))
        form.addRow("圆角 (px):", self.radius_spin)

        # ── 密度 ──────────────────────────────────────────────
        self.density_spin = QDoubleSpinBox()
        self.density_spin.setRange(0.7, 1.3)
        self.density_spin.setSingleStep(0.1)
        self.density_spin.setValue(_to_float(prefs.get("density"), _DEFAULTS["density"]))
        form.addRow("密度:", self.density_spin)

        # ── 字号 ──────────────────────────────────────────────
        self.font_scale_spin = QDoubleSpinBox()
        self.font_scale_spin.setRange(0.8, 1.4)
        self.font_scale_spin.setSingleStep(0.1)
        self.font_scale_spin.setValue(_to_float(prefs.get("font_scale"), _DEFAULTS["font_scale"]))
        form.addRow("字号缩放:", self.font_scale_spin)

        layout.addLayout(form)

        # ── 按钮 ──────────────────────────────────────────────
        btn_bar = QHBoxLayout()
        apply_btn = QPushButton("应用")
        apply_btn.clicked.connect(self._apply)
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self._ok)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self._cancel)
        btn_bar.addStretch()
        btn_bar.addWidget(apply_btn)
        btn_bar.addWidget(ok_btn)
        btn_bar.addWidget(cancel_btn)
        layout.addLayout(btn_bar)

    # ── 动作 ────────────────────────────────────────────────────

    def _write_prefs(self):
        """把当前控件值写入 QSettings。"""
        theme_apply.write_pref(theme_apply.KEY_PRESET,
                               self.preset_combo.currentData())
        acc = self.acc_edit.text().strip()
        if acc:
            theme_apply.write_pref(theme_apply.KEY_ACCENT, acc)
        else:
            theme_apply.write_pref(theme_apply.KEY_ACCENT, "")
        theme_apply.write_pref(theme_apply.KEY_RADIUS, self.radius_spin.value())
        theme_apply.write_pref(theme_apply.KEY_DENSITY, self.density_spin.value())
        theme_apply.write_pref(theme_apply.KEY_FONT_SCALE, self.font_scale_spin.value())

    def _apply(self):
        """写入 QSettings 并即时应用主题（不关对话框，可继续微调）。"""
        self._write_prefs()
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            self._win._theme = theme_apply.apply_prefs(app, self._win.workspace_root)
            self._win._sync_theme_button()
            self._win._refresh_card_icons()

    def _ok(self):
        self._apply()
        self.accept()

    def _cancel(self):
        """回退到打开前的 QSettings 值（未设过的键清空为空串，read_prefs 会跳过）。"""
        keys = {"preset": theme_apply.KEY_PRESET,
                "acc": theme_apply.KEY_ACCENT,
                "radius": theme_apply.KEY_RADIUS,
                "density": theme_apply.KEY_DENSITY,
                "font_scale": theme_apply.KEY_FONT_SCALE}
        for name, key in keys.items():
            theme_apply.write_pref(key, self._initial.get(name, ""))
        self._apply()
        self.reject()

    def _pick_color(self):
        """打开系统选色器，结果以 hex 写入主色输入框。"""
        old = self.acc_edit.text().strip()
        initial = QColor(old) if old else self._win._theme.color("acc")
        color = QColorDialog.getColor(initial or QColor("#a78bfa"), self, "选择主色")
        if color.isValid():
            self.acc_edit.setText(color.name())


def _to_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


__all__ = ["SettingsDialog"]
