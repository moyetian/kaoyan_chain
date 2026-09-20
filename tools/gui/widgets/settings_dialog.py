# -*- coding: utf-8 -*-
"""主题 L2 旋钮与应用配置设置面板"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from ..services.settings import read_config, save_settings

try:  # pragma: no cover
    from theme import list_presets, PRESET_ORDER
    from gui import theme_apply
except ImportError:  # pragma: no cover
    from tools.theme import list_presets, PRESET_ORDER  # type: ignore
    from tools.gui import theme_apply  # type: ignore

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
    QWidget, QTabWidget, QMessageBox
)

#: 各旋钮的默认值（与 tokens.py STRUCTURE_TOKENS 对齐）
_DEFAULTS = {"radius": 16, "density": 1.0, "font_scale": 1.0}

class SettingsDialog(QDialog):
    """全局设置中心对话框。"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置中心")
        self.setMinimumWidth(450)
        self.setMinimumHeight(400)
        self._win = win
        self._workspace_root = win.workspace_root

        self.config_file = self._workspace_root / "ky_config.json"
        self._ky_config = self._load_ky_config()

        self._prefs = theme_apply.read_prefs()
        self._current_preset = self._prefs.get("preset") or (win._theme.name if hasattr(win, "_theme") and win._theme else "classic_dark")
        self._initial = dict(self._prefs)          # 供「取消」回退

        main_layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self._init_tabs()
        main_layout.addWidget(self.tabs)

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
        main_layout.addLayout(btn_bar)

    def _c(self) -> dict:
        try:
            from tools.gui.theme_apply import resolve_theme
            t = getattr(self._win, "_theme", None) or resolve_theme(self._workspace_root)
            is_d = getattr(t, "is_dark", None)
            dark = is_d() if callable(is_d) else (getattr(t, "mode", "dark") == "dark")
            return {
                "fg": t.color("fg") or ("#f8fafc" if dark else "#0f172a"),
                "mut": t.color("mut") or ("#94a3b8" if dark else "#64748b"),
                "acc": t.color("acc") or ("#a78bfa" if dark else "#7c3aed"),
                "ok": t.color("ok") or ("#34d399" if dark else "#059669"),
                "bad": t.color("bad") or ("#f87171" if dark else "#ef4444"),
                "surf2": t.color("surf2") or ("#1e293b" if dark else "#f1f5f9"),
                "line": t.color("line") or ("#1e293b" if dark else "#e2e8f0"),
            }
        except Exception:
            return {
                "fg": "#f8fafc", "mut": "#94a3b8", "acc": "#a78bfa",
                "ok": "#34d399", "bad": "#f87171", "surf2": "#1e293b", "line": "#1e293b",
            }

    def _init_tabs(self):
        c = self._c()
        # ── Tab 1: AI 大模型设置 ─────────────────────────
        self.tab_ai = QWidget()
        form_ai = QFormLayout(self.tab_ai)

        key_row = QHBoxLayout()
        self.api_key_edit = QLineEdit(self._ky_config.get("api_key", ""))
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.btn_open_console = QPushButton("🔗 控制台")
        self.btn_open_console.setToolTip("跳转至大模型官方服务商控制台获取 Key")
        self.btn_open_console.setStyleSheet(
            f"background: {c['surf2']}; color: {c['acc']}; border: 1px solid {c['acc']}; "
            f"font-weight: bold; padding: 4px 10px; border-radius: 4px;"
        )
        self.btn_open_console.clicked.connect(self._open_provider_console)
        key_row.addWidget(self.api_key_edit, stretch=1)
        key_row.addWidget(self.btn_open_console)
        key_w = QWidget()
        key_w.setLayout(key_row)
        form_ai.addRow("API Key:", key_w)

        self.base_url_edit = QLineEdit(self._ky_config.get("base_url", "https://api.deepseek.com/v1"))
        form_ai.addRow("Base URL:", self.base_url_edit)

        model_row = QHBoxLayout()
        self.model_name_edit = QLineEdit(self._ky_config.get("model", "deepseek-chat"))
        self.model_selector = QComboBox()
        self.model_selector.setVisible(False)
        self.model_selector.currentTextChanged.connect(lambda t: self.model_name_edit.setText(t) if t else None)
        self.btn_probe_models = QPushButton("🔍 探查模型")
        self.btn_probe_models.setToolTip("探测当前 Base URL 节点支持的所有可用模型")
        self.btn_probe_models.setStyleSheet(
            f"background: {c['surf2']}; color: {c['acc']}; border: 1px solid {c['acc']}; "
            f"font-weight: bold; padding: 4px 10px; border-radius: 4px;"
        )
        self.btn_probe_models.clicked.connect(self._probe_models)
        model_row.addWidget(self.model_name_edit, stretch=1)
        model_row.addWidget(self.model_selector, stretch=1)
        model_row.addWidget(self.btn_probe_models)
        model_w = QWidget()
        model_w.setLayout(model_row)
        form_ai.addRow("模型名称:", model_w)

        test_row = QHBoxLayout()
        self.btn_test = QPushButton("⚡ 一键自检")
        self.btn_test.clicked.connect(self._test_connectivity)
        self.test_lbl = QLabel("未测试")
        self.test_lbl.setStyleSheet(f"color: {c['mut']}; font-size: 11px;")
        test_row.addWidget(self.btn_test)
        test_row.addWidget(self.test_lbl, stretch=1)
        test_w = QWidget()
        test_w.setLayout(test_row)
        form_ai.addRow("连通测试:", test_w)

        self.tabs.addTab(self.tab_ai, "AI 大模型设置")

        # ── Tab 2: 考研信息配置 ─────────────────────────
        self.tab_study = QWidget()
        form_study = QFormLayout(self.tab_study)
        plan = self._ky_config.get("study_plan") or {}
        self.school_edit = QLineEdit(plan.get("school", self._ky_config.get("target_school", "")))
        self.major_edit = QLineEdit(plan.get("major", self._ky_config.get("target_major", "")))
        self.date_edit = QLineEdit(plan.get("exam_date", self._ky_config.get("exam_date", "2026-12-19")))

        form_study.addRow("目标院校:", self.school_edit)
        form_study.addRow("报考专业:", self.major_edit)
        form_study.addRow("初试日期:", self.date_edit)

        self.wizard_btn = QPushButton("🚀 启动 5 步全能向导 (重新建档与科目绑定)")
        self.wizard_btn.setStyleSheet(
            f"background: {c['surf2']}; color: {c['acc']}; "
            f"font-weight: bold; padding: 6px 12px; border: 1px solid {c['acc']}; "
            f"border-radius: 6px;"
        )
        self.wizard_btn.clicked.connect(self._launch_onboarding_wizard)
        form_study.addRow("", self.wizard_btn)
        self.tabs.addTab(self.tab_study, "考研信息配置")

        # ── Tab 3: 私教辅导风格 ─────────────────────────
        self.tab_coach = QWidget()
        form_coach = QFormLayout(self.tab_coach)
        self.coach_style_combo = QComboBox()
        styles = [
            "严格把关·保姆提分型 (Strict & Disciplined)",
            "高效应试·高频秒杀型 (High-Yield Hacker)",
            "温和启发·减负鼓励型 (Encouraging Mentor)",
            "深度原理·学霸溯源型 (Deep Conceptual Master)"
        ]
        self.coach_style_combo.addItems(styles)
        current_style = plan.get("style_name") or self._ky_config.get("coaching_style", styles[0])
        idx = self.coach_style_combo.findText(current_style)
        if idx >= 0:
            self.coach_style_combo.setCurrentIndex(idx)
        form_coach.addRow("辅导风格:", self.coach_style_combo)
        self.tabs.addTab(self.tab_coach, "私教辅导风格")

        # ── Tab 4: 界面外观 ──────────────────────────────
        self.tab_theme = QWidget()
        form_theme = QFormLayout(self.tab_theme)

        # 预设
        self.preset_combo = QComboBox()
        for key, name, _mode in list_presets():
            self.preset_combo.addItem(name, key)
        idx = self.preset_combo.findData(self._current_preset)
        if idx >= 0:
            self.preset_combo.setCurrentIndex(idx)
        form_theme.addRow("预设:", self.preset_combo)

        # 主色
        acc_row = QHBoxLayout()
        acc_row.setContentsMargins(0, 0, 0, 0)
        self.acc_edit = QLineEdit(str(self._prefs.get("acc", "")))
        self.acc_edit.setPlaceholderText("留空 = 用预设原色")
        self.acc_edit.setMaximumWidth(120)
        acc_btn = QPushButton("选色...")
        acc_btn.clicked.connect(self._pick_color)
        acc_clear = QPushButton("清除")
        acc_clear.clicked.connect(lambda: self.acc_edit.clear())
        acc_row.addWidget(self.acc_edit)
        acc_row.addWidget(acc_btn)
        acc_row.addWidget(acc_clear)
        acc_widget = QWidget()
        acc_widget.setLayout(acc_row)
        form_theme.addRow("主色:", acc_widget)

        # 圆角
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(0, 32)
        self.radius_spin.setValue(_to_float(self._prefs.get("radius"), _DEFAULTS["radius"]))
        form_theme.addRow("圆角 (px):", self.radius_spin)

        # 密度
        self.density_spin = QDoubleSpinBox()
        self.density_spin.setRange(0.7, 1.3)
        self.density_spin.setSingleStep(0.1)
        self.density_spin.setValue(_to_float(self._prefs.get("density"), _DEFAULTS["density"]))
        form_theme.addRow("密度:", self.density_spin)

        # 字号
        self.font_scale_spin = QDoubleSpinBox()
        self.font_scale_spin.setRange(0.8, 1.4)
        self.font_scale_spin.setSingleStep(0.1)
        self.font_scale_spin.setValue(_to_float(self._prefs.get("font_scale"), _DEFAULTS["font_scale"]))
        form_theme.addRow("字号缩放:", self.font_scale_spin)

        self.tabs.addTab(self.tab_theme, "界面外观")

    def _load_ky_config(self) -> dict:
        try:
            return read_config(self.config_file)
        except (ValueError, OSError):
            # 表单可打开检查；保存时再次严格读取，绝不以空配置覆盖损坏文件。
            return {}

    def _save_ky_config(self) -> None:
        self._ky_config = save_settings(
            self.config_file, api_key=self.api_key_edit.text().strip(),
            base_url=self.base_url_edit.text().strip(), model=self.model_name_edit.text().strip(),
            school=self.school_edit.text().strip(), major=self.major_edit.text().strip(),
            exam_date=self.date_edit.text().strip(), style=self.coach_style_combo.currentText())

    def _launch_onboarding_wizard(self):
        """关闭当前快速设置面板，唤起完备的 5 步新手引导与建档向导。"""
        self.reject()
        if hasattr(self._win, "_open_onboarding_wizard"):
            self._win._open_onboarding_wizard()

    def _open_provider_console(self):
        base_url = self.base_url_edit.text().strip().lower()
        if "deepseek" in base_url:
            url = "https://platform.deepseek.com/api_keys"
        elif "aliyun" in base_url or "dashscope" in base_url:
            url = "https://bailian.console.aliyun.com/"
        elif "bigmodel" in base_url or "zhipu" in base_url:
            url = "https://open.bigmodel.cn/usercenter/apikeys"
        elif "volces" in base_url or "doubao" in base_url or "volcengine" in base_url:
            url = "https://console.volcengine.com/ark/region:ark+cn-beijing/endpoint"
        elif "moonshot" in base_url or "kimi" in base_url:
            url = "https://platform.moonshot.cn/console/api-keys"
        else:
            url = "https://platform.openai.com/api-keys"
        QDesktopServices.openUrl(QUrl(url))

    def _probe_models(self):
        api_key = self.api_key_edit.text().strip()
        base_url = self.base_url_edit.text().strip()
        if not api_key:
            QMessageBox.warning(self, "提示", "请先输入 API Key 再进行模型探查。")
            return
        self.btn_probe_models.setEnabled(False)
        self.btn_probe_models.setText("探查中...")
        from .onboarding_wizard import ProbeModelsWorker
        self._probe_worker = ProbeModelsWorker(api_key, base_url, self)
        self._probe_worker.finished_signal.connect(self._on_probe_finished)
        self._probe_worker.start()

    def _on_probe_finished(self, res: Any, err: Optional[str] = None):
        self.btn_probe_models.setEnabled(True)
        self.btn_probe_models.setText("🔍 探查模型")
        if isinstance(res, dict):
            models = list(res.get("models", []))
            err = "" if res.get("ok") else str(res.get("msg", "探查失败"))
        elif isinstance(res, (list, tuple)):
            models = list(res)
            err = err or ""
        else:
            models = []
            err = str(res)

        if err or not models:
            QMessageBox.warning(self, "探查结果", f"探查上游模型失败：\n{err or '无可用模型'}")
            return
        self.model_selector.blockSignals(True)
        self.model_selector.clear()
        for m in models:
            self.model_selector.addItem(m)
        self.model_selector.setVisible(True)
        self.model_selector.blockSignals(False)
        cur = self.model_name_edit.text().strip()
        idx = self.model_selector.findText(cur)
        if idx >= 0:
            self.model_selector.setCurrentIndex(idx)
        else:
            self.model_selector.setCurrentIndex(0)
            self.model_name_edit.setText(models[0])
        QMessageBox.information(self, "探查成功", f"成功获取 {len(models)} 个可用模型，已更新下拉列表。")

    def _test_connectivity(self):
        api_key = self.api_key_edit.text().strip()
        base_url = self.base_url_edit.text().strip()
        model = self.model_name_edit.text().strip()
        if not api_key:
            QMessageBox.warning(self, "提示", "请先输入 API Key 再进行连通测试。")
            return
        self.btn_test.setEnabled(False)
        self.test_lbl.setText("测试中...")
        from .onboarding_wizard import ConnectivityWorker
        self._conn_worker = ConnectivityWorker(api_key, base_url, model, "bing", self)
        self._conn_worker.finished_signal.connect(self._on_test_finished)
        self._conn_worker.start()

    def _on_test_finished(self, res: dict):
        self.btn_test.setEnabled(True)
        c = self._c()
        llm_ok = res.get("llm_ok", False)
        detail = res.get("llm_detail", "")
        if llm_ok:
            self.test_lbl.setText(f"🟢 {detail}")
            self.test_lbl.setStyleSheet(f"color: {c['ok']}; font-size: 11px;")
        else:
            self.test_lbl.setText(f"🔴 {detail}")
            self.test_lbl.setStyleSheet(f"color: {c['bad']}; font-size: 11px;")

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
        """写入配置并即时应用主题（不关对话框，可继续微调）。"""
        try:
            self._save_ky_config()
        except (ValueError, OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "设置未保存", str(exc))
            return False
        self._write_prefs()

        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            self._win._theme = theme_apply.apply_prefs(app, self._win.workspace_root)
            self._win._sync_theme_button()
            self._win._refresh_card_icons()
        self._win._load_config()
        self._win._refresh_all()
        return True

    def _ok(self):
        if self._apply():
            self.accept()

    def _cancel(self):
        """回退到打开前的 QSettings 值。"""
        # 「应用」已经提交；取消只丢弃尚未应用的表单内容，绝不再次调用保存。
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
