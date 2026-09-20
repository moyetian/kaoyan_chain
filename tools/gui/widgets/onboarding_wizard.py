# -*- coding: utf-8 -*-
"""考研学习链 · 全功能图形化新手引导与个性化学情建档向导 (GUI Onboarding Wizard)"""

from __future__ import annotations

import json
import os
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QDate, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPalette
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QCompleter, QDateEdit, QDialog,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QTextBrowser,
    QVBoxLayout, QWidget,
)

try:
    from ..services.settings import (
        is_unconfigured,
        read_config,
        save_onboarding_config,
        test_api_connectivity,
    )
except ImportError:
    from tools.gui.services.settings import (  # type: ignore
        is_unconfigured,
        read_config,
        save_onboarding_config,
        test_api_connectivity,
    )

try:
    from tools.intelligence.registry import UniversityRegistry
except ImportError:
    UniversityRegistry = None  # type: ignore


# 预设模型服务商
PROVIDER_PRESETS = {
    "DeepSeek (官方)": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "阿里百炼 (通义千问)": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    "智谱 AI (GLM)": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
    },
    "火山方舟 (豆包)": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-pro-32k",
    },
    "快跑 AI (中转)": {
        "base_url": "https://kuaipao.ai/v1",
        "model": "deepseek-chat",
    },
    "自定义 (Custom OpenAI API)": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
}

PROVIDER_CONSOLE_URLS = {
    "DeepSeek (官方)": "https://platform.deepseek.com/api_keys",
    "阿里百炼 (通义千问)": "https://bailian.console.aliyun.com/?apiKey=1#/api-key",
    "智谱 AI (GLM)": "https://open.bigmodel.cn/usercenter/apikeys",
    "火山方舟 (豆包)": "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
    "快跑 AI (中转)": "https://kuaipao.ai/",
    "自定义 (Custom OpenAI API)": "https://platform.openai.com/api-keys",
}

COACHING_STYLES = [
    (
        "温和启发·减负鼓励型 (Encouraging Mentor)",
        "🌱 耐心倾听、正向激励、将复杂难题拆解为微小提示，降低复习挫败感与焦虑内耗。",
    ),
    (
        "严格把关·保姆提分型 (Strict & Disciplined)",
        "🛡️ 以真题阅卷人严苛视角审视解答，步骤严格赋分，计算失误零容忍，强制错题闭环。",
    ),
    (
        "高效应试·高频秒杀型 (High-Yield Hacker)",
        "⚡ 贯彻 80/20 法则，传授排除法、帽子词秒杀与阅读长难句速抓模板，直击必考核心分。",
    ),
    (
        "深度原理·学霸溯源型 (Deep Conceptual Master)",
        "🧠 知其然更知其所以然，追溯底层数理背景与命题设计陷阱，构建跨章节完整知识图谱。",
    ),
]


class ProbeModelsWorker(QThread):
    """异步上游模型探测工作线程"""
    finished_signal = Signal(dict)

    def __init__(self, api_key: str, base_url: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.api_key = api_key
        self.base_url = base_url

    def run(self):
        try:
            try:
                from tools.llm_client import fetch_upstream_models
            except ImportError:
                from llm_client import fetch_upstream_models  # type: ignore
            ok, models, msg = fetch_upstream_models(self.api_key, self.base_url, timeout=12.0)
            self.finished_signal.emit({"ok": ok, "models": models, "msg": msg})
        except Exception as e:
            self.finished_signal.emit({"ok": False, "models": [], "msg": f"探测异常: {e}"})


class ConnectivityWorker(QThread):
    """异步 API 与网络检索连通性探测工作线程"""
    finished_signal = Signal(dict)

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        search_provider: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.search_provider = search_provider

    def run(self):
        res = test_api_connectivity(
            self.api_key,
            self.base_url,
            self.model,
            self.search_provider,
            timeout=25.0,
        )
        self.finished_signal.emit(res)


class OnboardingWizard(QDialog):
    """考研学习链 · 现代化 5 步全能引导与配置向导"""

    config_saved = Signal(dict)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        workspace_root: Optional[Path | str] = None,
        config_path: Optional[Path | str] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("考研学习链 · 新手引导与个性化学情建档向导")
        self.resize(840, 640)
        self.setMinimumSize(800, 560)

        if config_path:
            self.config_path = Path(config_path)
            self.workspace_root = Path(workspace_root) if workspace_root else self.config_path.parent
        else:
            self.workspace_root = Path(workspace_root) if workspace_root else Path.cwd()
            self.config_path = self.workspace_root / "ky_config.json"
        self._initial_config = self._load_initial_config()

        self._current_step = 0
        self._total_steps = 5
        self._worker: Optional[ConnectivityWorker] = None
        self._probe_worker: Optional[ProbeModelsWorker] = None

        # 高校注册表
        self._registry = None
        if UniversityRegistry is not None:
            try:
                self._registry = UniversityRegistry()
            except Exception:
                self._registry = None

        self._init_ui()
        self._load_from_config(self._initial_config)
        self._refresh_material_badges()
        self._update_step_view()

    def exec(self) -> int:
        """非阻塞守护：在 offscreen 离屏自动化测试中避免 modal 阻塞导致超时。"""
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and os.environ.get("PYTEST_CURRENT_TEST"):
            self.show()
            return int(QDialog.DialogCode.Accepted)
        return super().exec()

    def _get_theme_colors(self) -> dict:
        try:
            from tools.gui.theme_apply import resolve_theme
            t = resolve_theme(self.workspace_root)
            is_d = getattr(t, "is_dark", None)
            dark = is_d() if callable(is_d) else (getattr(t, "mode", "dark") == "dark")
            return {
                "fg": t.color("fg") or ("#f8fafc" if dark else "#0f172a"),
                "mut": t.color("mut") or ("#94a3b8" if dark else "#64748b"),
                "acc": t.color("acc") or ("#a78bfa" if dark else "#7c3aed"),
                "on_acc": t.color("on-acc") or ("#1e1b4b" if dark else "#ffffff"),
                "ok": t.color("ok") or ("#34d399" if dark else "#059669"),
                "warn": t.color("warn") or ("#fbbf24" if dark else "#b45309"),
                "bad": t.color("bad") or ("#f87171" if dark else "#ef4444"),
                "surf": t.color("surf") or ("#111827" if dark else "#ffffff"),
                "surf2": t.color("surf2") or ("#1e293b" if dark else "#f1f5f9"),
                "line": t.color("line") or ("#1e293b" if dark else "#e2e8f0"),
                "is_dark": dark,
            }
        except Exception:
            return {
                "fg": "#f8fafc", "mut": "#94a3b8", "acc": "#a78bfa", "on_acc": "#1e1b4b",
                "ok": "#34d399", "warn": "#fbbf24", "bad": "#f87171",
                "surf": "#111827", "surf2": "#1e293b", "line": "#1e293b", "is_dark": True,
            }

    def _c(self) -> dict:
        return self._get_theme_colors()

    def _load_initial_config(self) -> dict:
        try:
            return read_config(self.config_path)
        except Exception:
            return {}

    def _init_ui(self):
        c = self._c()
        # 对话框级调色板与全局 QSS 约束（确保在深色模式下所有子控件文字对比度达标，杜绝融底）
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(c["surf"]))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(c["fg"]))
        pal.setColor(QPalette.ColorRole.Text, QColor(c["fg"]))
        pal.setColor(QPalette.ColorRole.Base, QColor(c["surf2"]))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(c["fg"]))
        self.setPalette(pal)

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {c['surf']};
                color: {c['fg']};
            }}
            QLabel {{
                color: {c['fg']};
                background: transparent;
            }}
            QScrollArea, QScrollArea > QWidget > QWidget {{
                background-color: transparent;
            }}
            QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
                background-color: {c['surf2']};
                color: {c['fg']};
                border: 1px solid {c['line']};
                border-radius: 6px;
                padding: 6px 8px;
            }}
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{
                border: 1.5px solid {c['acc']};
            }}
            QRadioButton {{
                color: {c['fg']};
            }}
        """)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(24, 20, 24, 18)
        root_layout.setSpacing(14)

        # ── 顶部步骤指示器 ─────────────────────────
        self.indicator_layout = QHBoxLayout()
        self.indicator_layout.setSpacing(10)
        self.step_labels: List[QLabel] = []
        self.step_arrows: List[QLabel] = []
        step_names = [
            "1. 目标院校与专业",
            "2. 科目与考纲",
            "3. 战役节奏与目标",
            "4. 学情诊断与风格",
            "5. AI与检索引擎",
        ]
        for idx, name in enumerate(step_names):
            lbl = QLabel(name)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"font-size: 13px; font-weight: 500; padding: 6px 10px; border-radius: 6px; color: {c['mut']};")
            self.step_labels.append(lbl)
            self.indicator_layout.addWidget(lbl)
            if idx < len(step_names) - 1:
                arrow = QLabel("➔")
                arrow.setStyleSheet(f"color: {c['mut']}; font-size: 12px;")
                self.step_arrows.append(arrow)
                self.indicator_layout.addWidget(arrow)

        root_layout.addLayout(self.indicator_layout)

        # 分割线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        root_layout.addWidget(sep)

        # ── 中间 5 步页面堆栈 ─────────────────────
        self.stacked_widget = QStackedWidget()
        self.page_1 = self._build_page_1()
        self.page_2 = self._build_page_2()
        self.page_3 = self._build_page_3()
        self.page_4 = self._build_page_4()
        self.page_5 = self._build_page_5()

        self.stacked_widget.addWidget(self.page_1)
        self.stacked_widget.addWidget(self.page_2)
        self.stacked_widget.addWidget(self.page_3)
        self.stacked_widget.addWidget(self.page_4)
        self.stacked_widget.addWidget(self.page_5)

        root_layout.addWidget(self.stacked_widget, stretch=1)

        # 分割线
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        root_layout.addWidget(sep2)

        # ── 底部导航按钮 ─────────────────────────
        btn_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("稍后配置")
        self.btn_cancel.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {c['mut']}; border: 1px solid {c['line']}; "
            f"border-radius: 6px; padding: 7px 16px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {c['surf2']}; color: {c['fg']}; }}"
        )
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_prev = QPushButton("上一步")
        self.btn_prev.setStyleSheet(
            f"QPushButton {{ background: {c['surf2']}; color: {c['fg']}; border: 1px solid {c['line']}; "
            f"border-radius: 6px; padding: 7px 16px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {c['line']}; color: {c['fg']}; }}"
        )
        self.btn_prev.clicked.connect(self._on_prev_step)

        self.btn_next = QPushButton("下一步")
        self.btn_next.setStyleSheet(
            "QPushButton { background-color: #7c3aed; color: #ffffff; border: 1px solid #8b5cf6; "
            "border-radius: 6px; padding: 7px 20px; font-weight: bold; }"
            "QPushButton:hover { background-color: #6d28d9; color: #ffffff; }"
        )
        self.btn_next.clicked.connect(self._on_next_step)

        self.btn_finish = QPushButton("🚀 完成建档并即时生效")
        self.btn_finish.setStyleSheet(
            "QPushButton { background-color: #059669; color: #ffffff; border: 1px solid #10b981; "
            "border-radius: 6px; padding: 7px 22px; font-weight: bold; }"
            "QPushButton:hover { background-color: #047857; color: #ffffff; }"
        )
        self.btn_finish.clicked.connect(self._on_finish)

        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_prev)
        btn_layout.addWidget(self.btn_next)
        btn_layout.addWidget(self.btn_finish)

        root_layout.addLayout(btn_layout)

    # ════════════════════════════════════════════════════════════
    # 步骤页面构建
    # ════════════════════════════════════════════════════════════

    def _build_page_1(self) -> QWidget:
        """Step 1: 目标院校与专业"""
        c = self._c()
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)

        tip = QLabel(f"🎯 <b>第一步：选择您的考研目标高校与报考专业</b><br>"
                     f"<span style='color:{c['mut']};'>系统内置全国高校权威数据库与智能启发推断，将自动匹配办学层次、录取趋势并关联考情。</span>")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)

        # 目标院校
        self.school_edit = QLineEdit()
        self.school_edit.setPlaceholderText("例如：示例院校A / 示例院校B")
        if self._registry:
            names = set()
            for entity in self._registry._entities.values():
                if entity.name:
                    names.add(entity.name)
                for a in entity.aliases:
                    if a:
                        names.add(a)
            completer = QCompleter(sorted(names), self)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self.school_edit.setCompleter(completer)
        self.school_edit.textChanged.connect(self._on_school_changed)
        form.addRow("目标院校 *:", self.school_edit)

        # 高校层次与属性标签
        self.school_badge_label = QLabel("待输入目标院校...")
        self.school_badge_label.setStyleSheet(f"color: {c['acc']}; font-weight: 500; font-size: 12px;")
        form.addRow("高校属性:", self.school_badge_label)

        # 报考专业
        self.major_edit = QLineEdit()
        self.major_edit.setPlaceholderText("例如：081200 计算机科学与技术 / 085400 电子信息")
        form.addRow("报考专业 *:", self.major_edit)

        # 备选院校 (可选)
        self.backup_school_edit = QLineEdit()
        self.backup_school_edit.setPlaceholderText("选填，用于后续一键双校对标（如：示例院校B）")
        form.addRow("备选院校:", self.backup_school_edit)

        layout.addLayout(form)
        layout.addStretch()
        return widget

    def _build_page_2(self) -> QWidget:
        """Step 2: 考试科目与考纲绑定 + 实体参考资料一键放置入库"""
        c = self._c()
        container = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 14, 4)
        layout.setSpacing(10)

        tip = QLabel(f"📚 <b>第二步：确认研考方案模式与考纲规格 · 放置参考资料</b><br>"
                     f"<span style='color:{c['mut']};'>全面适配统考工学/理学、不考数学双专业课(如两门三位代码自命题科目)、199管综等多套方案；可直接导入资料锁定出题门禁。</span>")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        # 考试方案模式选择器
        self.exam_mode_combo = QComboBox()
        self.exam_mode_combo.addItem("模式 A：统考四科 (政治 + 英语 + 数学 + 专业课) [总分 500]", "mode_a")
        self.exam_mode_combo.addItem("模式 B：不考数学 · 双自命题专业课 (政治 + 英语 + 专业课一 + 专业课二) [总分 500]", "mode_b")
        self.exam_mode_combo.addItem("模式 C：管理类联考 199 (199管综 + 英语二 · 初试不考政治与统考数学) [总分 300]", "mode_c")
        self.exam_mode_combo.addItem("模式 D：经济类联考 396 / 特殊自选模式 [总分 500]", "mode_d")
        self.exam_mode_combo.currentIndexChanged.connect(self._on_exam_mode_changed)
        form.addRow("研考方案模式 *:", self.exam_mode_combo)

        # 数学
        self.math_combo = QComboBox()
        self.math_combo.addItem("不考数学 (文科/管理类/医学等)", "none")
        self.math_combo.addItem("数学一 (301) - 工学统考", "math1")
        self.math_combo.addItem("数学二 (302) - 专硕/轻工等统考", "math2")
        self.math_combo.addItem("数学三 (303) - 经管统考", "math3")
        self.math_combo.addItem("396 经济类综合能力 (含数学微积分与概率)", "396")
        self.math_combo.currentIndexChanged.connect(self._on_math_selection_changed)
        form.addRow("科目一 (数学):", self.math_combo)

        # 外国语
        self.eng_combo = QComboBox()
        self.eng_combo.addItem("英语一 (201) - 学硕/高要求专硕", "eng1")
        self.eng_combo.addItem("英语二 (204) - 多数专业型硕士", "eng2")
        self.eng_combo.addItem("单独命题外语 / 日语 / 俄语", "other")
        form.addRow("科目二 (英语):", self.eng_combo)

        # 思想政治理论
        self.pol_label = QLabel("101 思想政治理论 (全国统考 · 必考核心基本盘)")
        self.pol_label.setStyleSheet(f"color: {c['fg']}; font-weight: 500;")
        form.addRow("科目三 (政治):", self.pol_label)

        # 专业课类型
        self.pro_type_combo = QComboBox()
        self.pro_type_combo.addItem("院校自主命题专业课 (自命题单科或双科合并)", "custom")
        self.pro_type_combo.addItem("管科综合 / 管理科学自命题 (如 803/842 运筹与管理)", "mgmt_sci")
        self.pro_type_combo.addItem("408 计算机学科专业基础综合 (全国统考)", "408")
        self.pro_type_combo.addItem("199 管理类联考综合能力 (统考)", "199")
        self.pro_type_combo.addItem("396 经济类综合能力 (统考)", "396")
        self.pro_type_combo.addItem("333 教育综合 (全国统考/自命题)", "333")
        self.pro_type_combo.addItem("法律硕士联考专业基础 (397/398 统考)", "law")
        self.pro_type_combo.currentIndexChanged.connect(self._on_pro_type_changed)
        form.addRow("专业课类别:", self.pro_type_combo)

        # 专业课科目名称与代码
        self.pro_name_edit = QLineEdit()
        self.pro_name_edit.setPlaceholderText("例如：814 自命题科目一 861 自命题科目二")
        self.pro_name_row_label = QLabel("专业课名称与代码 *:")
        form.addRow(self.pro_name_row_label, self.pro_name_edit)

        # 专业课二科目名称与代码 (仅 Mode B 双自命题模式展示)
        self.pro2_name_edit = QLineEdit()
        self.pro2_name_edit.setPlaceholderText("例如：861 自命题科目二")
        self.pro2_row_label = QLabel("专业课二名称与代码 *:")
        self.pro2_row_widget = QWidget()
        p2_layout = QHBoxLayout(self.pro2_row_widget)
        p2_layout.setContentsMargins(0, 0, 0, 0)
        p2_layout.addWidget(self.pro2_name_edit)
        form.addRow(self.pro2_row_label, self.pro2_row_widget)
        self.pro2_row_label.setVisible(False)
        self.pro2_row_widget.setVisible(False)

        layout.addLayout(form)

        # ── 实体参考资料放置与入库卡片 ──
        mat_card = QFrame()
        mat_card.setFrameShape(QFrame.Shape.StyledPanel)
        mat_card.setStyleSheet(f"background: rgba(124, 58, 237, 0.06); border: 1px solid rgba(124, 58, 237, 0.25); border-radius: 8px; padding: 10px;")
        m_layout = QVBoxLayout(mat_card)
        m_layout.setSpacing(8)

        m_title = QLabel("📂 <b>参考资料与实体真题快速入库 (白名单题源出题防虚构)</b>")
        m_desc = QLabel("可直接点击下方按钮为对应科目导入真题讲义、教材或考纲（支持 PDF / Word / 图片），或打开所在目录拖入。私教仅基于您放置的实体文件出题。")
        m_desc.setStyleSheet(f"color: {c['acc']}; font-size: 11px;")
        m_desc.setWordWrap(True)
        m_layout.addWidget(m_title)
        m_layout.addWidget(m_desc)

        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setColumnStretch(1, 1)

        # 政治
        grid.addWidget(QLabel("<b>政治资料:</b>"), 0, 0)
        self.pol_mat_lbl = QLabel("检查中...")
        grid.addWidget(self.pol_mat_lbl, 0, 1)
        btn_imp_pol = QPushButton("➕ 导入政治资料")
        btn_imp_pol.clicked.connect(lambda: self._import_materials("pol"))
        grid.addWidget(btn_imp_pol, 0, 2)
        btn_open_pol = QPushButton("📂 打开目录")
        btn_open_pol.clicked.connect(lambda: self._open_folder("pol"))
        grid.addWidget(btn_open_pol, 0, 3)

        # 英语
        grid.addWidget(QLabel("<b>英语资料:</b>"), 1, 0)
        self.eng_mat_lbl = QLabel("检查中...")
        grid.addWidget(self.eng_mat_lbl, 1, 1)
        btn_imp_eng = QPushButton("➕ 导入英语资料")
        btn_imp_eng.clicked.connect(lambda: self._import_materials("eng"))
        grid.addWidget(btn_imp_eng, 1, 2)
        btn_open_eng = QPushButton("📂 打开目录")
        btn_open_eng.clicked.connect(lambda: self._open_folder("eng"))
        grid.addWidget(btn_open_eng, 1, 3)

        # 专业课
        self.pro_mat_title = QLabel("<b>专业课资料:</b>")
        grid.addWidget(self.pro_mat_title, 2, 0)
        self.pro_mat_lbl = QLabel("检查中...")
        grid.addWidget(self.pro_mat_lbl, 2, 1)
        btn_imp_pro = QPushButton("➕ 导入专业课资料")
        btn_imp_pro.clicked.connect(lambda: self._import_materials("pro"))
        grid.addWidget(btn_imp_pro, 2, 2)
        btn_open_pro = QPushButton("📂 打开目录")
        btn_open_pro.clicked.connect(lambda: self._open_folder("pro"))
        grid.addWidget(btn_open_pro, 2, 3)

        # 专业课二 (用于双自命题模式)
        self.pro2_mat_title = QLabel("<b>专业课二:</b>")
        grid.addWidget(self.pro2_mat_title, 3, 0)
        self.pro2_mat_lbl = QLabel("检查中...")
        grid.addWidget(self.pro2_mat_lbl, 3, 1)
        self.btn_imp_pro2 = QPushButton("➕ 导入第二门资料")
        self.btn_imp_pro2.clicked.connect(lambda: self._import_materials("pro2"))
        grid.addWidget(self.btn_imp_pro2, 3, 2)
        self.btn_open_pro2 = QPushButton("📂 打开目录")
        self.btn_open_pro2.clicked.connect(lambda: self._open_folder("pro2"))
        grid.addWidget(self.btn_open_pro2, 3, 3)
        self.pro2_mat_title.setVisible(False)
        self.pro2_mat_lbl.setVisible(False)
        self.btn_imp_pro2.setVisible(False)
        self.btn_open_pro2.setVisible(False)

        # 数学
        self.math_mat_title = QLabel("<b>数学资料:</b>")
        grid.addWidget(self.math_mat_title, 4, 0)
        self.math_mat_lbl = QLabel("检查中...")
        grid.addWidget(self.math_mat_lbl, 4, 1)
        self.btn_import_math = QPushButton("➕ 导入数学资料")
        self.btn_import_math.clicked.connect(lambda: self._import_materials("math"))
        grid.addWidget(self.btn_import_math, 4, 2)
        self.btn_open_math = QPushButton("📂 打开目录")
        self.btn_open_math.clicked.connect(lambda: self._open_folder("math"))
        grid.addWidget(self.btn_open_math, 4, 3)

        m_layout.addLayout(grid)
        layout.addWidget(mat_card)

        # 考纲绑定提示卡片
        syllabus_card = QFrame()
        syllabus_card.setFrameShape(QFrame.Shape.StyledPanel)
        syllabus_card.setStyleSheet(f"background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 10px;")
        s_layout = QVBoxLayout(syllabus_card)
        s_title = QLabel("📌 <b>考纲自动挂载机制</b>")
        s_desc = QLabel("向导完成后，工作区将自动写入教育部考试大纲至各科目录，并基于真实题源锁定出题门禁。")
        s_desc.setStyleSheet(f"color: {c['ok']}; font-size: 11px;")
        s_desc.setWordWrap(True)
        s_layout.addWidget(s_title)
        s_layout.addWidget(s_desc)
        layout.addWidget(syllabus_card)

        layout.addStretch()

        scroll.setWidget(widget)
        outer_layout = QVBoxLayout(container)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroll)
        return container

    def _build_page_3(self) -> QWidget:
        """Step 3: 战役节奏与目标矩阵"""
        c = self._c()
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)

        tip = QLabel(f"⏱️ <b>第三步：设定战役时间表与各科精力预算</b><br>"
                     f"<span style='color:{c['mut']};'>精确计算初试倒计时，科学划分每日各科投入时长，不考数学时数学自动归零锁定。</span>")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        # 初试日期
        date_row = QHBoxLayout()
        self.exam_date_edit = QLineEdit("2026-12-19")
        self.exam_date_edit.setPlaceholderText("YYYY-MM-DD")
        self.exam_date_edit.textChanged.connect(self._on_exam_date_changed)
        self.countdown_badge = QLabel("⏱️ 距初试约 -- 天")
        self.countdown_badge.setStyleSheet(f"color: {c['warn']}; font-weight: bold; padding-left: 8px;")
        date_row.addWidget(self.exam_date_edit)
        date_row.addWidget(self.countdown_badge)
        date_row_w = QWidget()
        date_row_w.setLayout(date_row)
        form.addRow("初试日期:", date_row_w)

        # 备考阶段
        self.stage_combo = QComboBox()
        self.stage_combo.addItem("强化题型攻坚阶段 (核心强化，主抓高频得分点)")
        self.stage_combo.addItem("基础夯实阶段 (全面扫盲，通读教材与大纲)")
        self.stage_combo.addItem("真题突破与查漏补缺 (全真推演，攻克命题陷阱)")
        self.stage_combo.addItem("临考点睛与模考冲刺 (步骤规范，全科定时演练)")
        form.addRow("当前备考阶段:", self.stage_combo)

        # 每日时间预算快速预设
        preset_row = QHBoxLayout()
        btn_p1 = QPushButton("在校备考 (6.5h)")
        btn_p1.clicked.connect(lambda: self._apply_time_preset(6.5))
        btn_p2 = QPushButton("全脱产 (8.5h)")
        btn_p2.clicked.connect(lambda: self._apply_time_preset(8.5))
        btn_p3 = QPushButton("在职轻量 (4.0h)")
        btn_p3.clicked.connect(lambda: self._apply_time_preset(4.0))
        preset_row.addWidget(btn_p1)
        preset_row.addWidget(btn_p2)
        preset_row.addWidget(btn_p3)
        preset_row_w = QWidget()
        preset_row_w.setLayout(preset_row)
        form.addRow("作息预设:", preset_row_w)

        # 分科时长 SpinBoxes
        time_grid = QGridLayout()
        time_grid.addWidget(QLabel("数学(h):"), 0, 0)
        self.math_hours_spin = QDoubleSpinBox()
        self.math_hours_spin.setRange(0.0, 10.0)
        self.math_hours_spin.setSingleStep(0.5)
        self.math_hours_spin.valueChanged.connect(self._recalc_total_hours)
        time_grid.addWidget(self.math_hours_spin, 0, 1)

        time_grid.addWidget(QLabel("英语(h):"), 0, 2)
        self.eng_hours_spin = QDoubleSpinBox()
        self.eng_hours_spin.setRange(0.0, 10.0)
        self.eng_hours_spin.setSingleStep(0.5)
        self.eng_hours_spin.setValue(1.5)
        self.eng_hours_spin.valueChanged.connect(self._recalc_total_hours)
        time_grid.addWidget(self.eng_hours_spin, 0, 3)

        self.pol_time_label = QLabel("政治(h):")
        time_grid.addWidget(self.pol_time_label, 1, 0)
        self.pol_hours_spin = QDoubleSpinBox()
        self.pol_hours_spin.setRange(0.0, 10.0)
        self.pol_hours_spin.setSingleStep(0.5)
        self.pol_hours_spin.setValue(0.5)
        self.pol_hours_spin.valueChanged.connect(self._recalc_total_hours)
        time_grid.addWidget(self.pol_hours_spin, 1, 1)

        self.pro_time_label = QLabel("专业课(h):")
        time_grid.addWidget(self.pro_time_label, 1, 2)
        self.pro_hours_spin = QDoubleSpinBox()
        self.pro_hours_spin.setRange(0.0, 10.0)
        self.pro_hours_spin.setSingleStep(0.5)
        self.pro_hours_spin.setValue(2.0)
        self.pro_hours_spin.valueChanged.connect(self._recalc_total_hours)
        time_grid.addWidget(self.pro_hours_spin, 1, 3)

        self.pro2_time_label = QLabel("专业课二(h):")
        self.pro2_hours_spin = QDoubleSpinBox()
        self.pro2_hours_spin.setRange(0.0, 10.0)
        self.pro2_hours_spin.setSingleStep(0.5)
        self.pro2_hours_spin.setValue(2.0)
        self.pro2_hours_spin.valueChanged.connect(self._recalc_total_hours)
        time_grid.addWidget(self.pro2_time_label, 2, 0)
        time_grid.addWidget(self.pro2_hours_spin, 2, 1)
        self.pro2_time_label.setVisible(False)
        self.pro2_hours_spin.setVisible(False)

        time_grid_w = QWidget()
        time_grid_w.setLayout(time_grid)
        form.addRow("各科时长微调:", time_grid_w)

        self.total_hours_label = QLabel("每日总投入: 4.0 小时")
        self.total_hours_label.setStyleSheet(f"font-weight: bold; color: {c['ok']};")
        form.addRow("总精力预算:", self.total_hours_label)

        # 目标成绩矩阵
        score_grid = QGridLayout()
        score_grid.addWidget(QLabel("数学目标:"), 0, 0)
        self.math_target_edit = QLineEdit("不考数学")
        score_grid.addWidget(self.math_target_edit, 0, 1)

        score_grid.addWidget(QLabel("英语目标:"), 0, 2)
        self.eng_target_edit = QLineEdit("65+ 分")
        score_grid.addWidget(self.eng_target_edit, 0, 3)

        self.pol_score_label = QLabel("政治目标:")
        score_grid.addWidget(self.pol_score_label, 1, 0)
        self.pol_target_edit = QLineEdit("70+ 分")
        score_grid.addWidget(self.pol_target_edit, 1, 1)

        self.pro_score_label = QLabel("专业课目标:")
        score_grid.addWidget(self.pro_score_label, 1, 2)
        self.pro_target_edit = QLineEdit("120-130 分")
        score_grid.addWidget(self.pro_target_edit, 1, 3)

        self.pro2_score_label = QLabel("专业课二目标:")
        self.pro2_target_edit = QLineEdit("120-130 分")
        score_grid.addWidget(self.pro2_score_label, 2, 0)
        score_grid.addWidget(self.pro2_target_edit, 2, 1)
        self.pro2_score_label.setVisible(False)
        self.pro2_target_edit.setVisible(False)

        score_grid.addWidget(QLabel("总分目标:"), 3, 0)
        self.total_target_edit = QLineEdit("370+ 分")
        score_grid.addWidget(self.total_target_edit, 3, 1, 1, 3)

        score_grid_w = QWidget()
        score_grid_w.setLayout(score_grid)
        form.addRow("提分目标矩阵:", score_grid_w)

        layout.addLayout(form)
        layout.addStretch()
        self._on_exam_date_changed(self.exam_date_edit.text())
        return widget

    def _build_page_4(self) -> QWidget:
        """Step 4: 学情痛点摸底与辅导风格"""
        c = self._c()
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)

        tip = QLabel(f"🧠 <b>第四步：各科摸底痛点录入与私教辅导风格设定</b><br>"
                     f"<span style='color:{c['mut']};'>直击薄弱盲区，AI 私教将根据所选风格调整审题引导与判分尺度。</span>")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        # 各科痛点
        self.math_weakness_edit = QLineEdit("无")
        self.math_weak_row_label = QLabel("数学薄弱点:")
        form.addRow(self.math_weak_row_label, self.math_weakness_edit)

        self.eng_weakness_edit = QLineEdit("看不懂题目、待诊断薄弱点困难")
        self.eng_weak_row_label = QLabel("英语薄弱点:")
        form.addRow(self.eng_weak_row_label, self.eng_weakness_edit)

        self.pol_weakness_edit = QLineEdit("待诊断薄弱点、多选题容易漏选")
        self.pol_weak_row_label = QLabel("政治薄弱点:")
        form.addRow(self.pol_weak_row_label, self.pol_weakness_edit)

        self.pro_weakness_edit = QLineEdit("待诊断薄弱点、论述题缺乏框架")
        self.pro_weak_row_label = QLabel("专业课薄弱点:")
        form.addRow(self.pro_weak_row_label, self.pro_weakness_edit)

        self.pro2_weakness_edit = QLineEdit("核心考点记不牢、论述题缺乏框架")
        self.pro2_weak_row_label = QLabel("专业课二薄弱点:")
        form.addRow(self.pro2_weak_row_label, self.pro2_weakness_edit)
        self.pro2_weak_row_label.setVisible(False)
        self.pro2_weakness_edit.setVisible(False)

        layout.addLayout(form)

        # 私教风格单选组
        style_box = QFrame()
        style_box.setFrameShape(QFrame.Shape.StyledPanel)
        style_box.setStyleSheet(f"background: rgba(124, 58, 237, 0.06); border: 1px solid rgba(124, 58, 237, 0.2); border-radius: 8px; padding: 8px;")
        sb_layout = QVBoxLayout(style_box)
        sb_layout.setSpacing(6)
        sb_title = QLabel("🎓 <b>当前激活辅导风格选择：</b>")
        sb_layout.addWidget(sb_title)

        self.style_btn_group = QButtonGroup(self)
        self.style_radios: List[QRadioButton] = []
        for idx, (s_name, s_desc) in enumerate(COACHING_STYLES):
            rb = QRadioButton(s_name)
            rb.setToolTip(s_desc)
            if idx == 0:
                rb.setChecked(True)
            self.style_btn_group.addButton(rb, idx)
            self.style_radios.append(rb)
            sb_layout.addWidget(rb)
            desc_lbl = QLabel(f"   <span style='color:{c['mut']}; font-size:11px;'>{s_desc}</span>")
            sb_layout.addWidget(desc_lbl)

        layout.addWidget(style_box)
        layout.addStretch()
        return widget

    def _build_page_5(self) -> QWidget:
        """Step 5: 大模型与检索引擎配置 + 连通性自检"""
        c = self._c()
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)

        tip = QLabel(f"⚡ <b>第五步：配置大模型 API 与网络检索引擎</b><br>"
                     f"<span style='color:{c['mut']};'>支持快速直达官方控制台获取 API Key，提供智能上游模型探查与一键连通性自检。</span>")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        # 服务商预设 + 官方控制台快捷跳转按钮
        acc_text = "#c4b5fd" if c.get("is_dark", True) else "#6d28d9"
        border_col = "#7c3aed" if c.get("is_dark", True) else "#8b5cf6"
        prov_row = QHBoxLayout()
        self.provider_combo = QComboBox()
        for p_name in PROVIDER_PRESETS:
            self.provider_combo.addItem(p_name)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_preset_changed)
        self.btn_open_console = QPushButton("🔗 获取 Key / 控制台")
        self.btn_open_console.setToolTip("在浏览器中直接打开所选官方模型服务商控制台")
        self.btn_open_console.setStyleSheet(
            f"QPushButton {{ background: rgba(124, 58, 237, 0.15); color: {acc_text}; border: 1px solid {border_col}; "
            f"font-weight: bold; padding: 5px 12px; border-radius: 6px; }}"
            f"QPushButton:hover {{ background: #7c3aed; color: #ffffff; }}"
        )
        self.btn_open_console.clicked.connect(self._open_provider_console)
        prov_row.addWidget(self.provider_combo, stretch=1)
        prov_row.addWidget(self.btn_open_console)
        prov_row_w = QWidget()
        prov_row_w.setLayout(prov_row)
        form.addRow("服务商预设:", prov_row_w)

        # API Key (带明文切换)
        key_row = QHBoxLayout()
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("sk-...")
        self.btn_toggle_key = QPushButton("👁️")
        self.btn_toggle_key.setFixedWidth(36)
        self.btn_toggle_key.setStyleSheet(
            f"QPushButton {{ background: {c['surf2']}; color: {c['fg']}; border: 1px solid {c['line']}; border-radius: 6px; }}"
            f"QPushButton:hover {{ background: {c['line']}; color: {c['fg']}; }}"
        )
        self.btn_toggle_key.clicked.connect(self._toggle_api_key_visibility)
        key_row.addWidget(self.api_key_edit)
        key_row.addWidget(self.btn_toggle_key)
        key_row_w = QWidget()
        key_row_w.setLayout(key_row)
        form.addRow("API Key *:", key_row_w)

        # Base URL
        self.base_url_edit = QLineEdit("https://api.deepseek.com/v1")
        form.addRow("Base URL *:", self.base_url_edit)

        # Model Name + 探查模型按钮
        model_row = QHBoxLayout()
        self.model_name_edit = QLineEdit("deepseek-chat")
        self.model_name_edit.setPlaceholderText("填入模型名称或点击右侧探查")
        self.model_selector = QComboBox()
        self.model_selector.setToolTip("从上游探查到的模型列表中快速选择")
        self.model_selector.setVisible(False)
        self.model_selector.currentTextChanged.connect(self._on_model_selected_from_probe)
        self.btn_probe_models = QPushButton("🔍 探查模型")
        self.btn_probe_models.setToolTip("自动探测该端点支持的全部上游模型并生成下拉选项")
        self.btn_probe_models.setStyleSheet(
            f"QPushButton {{ background: rgba(124, 58, 237, 0.15); color: {acc_text}; border: 1px solid {border_col}; "
            f"font-weight: bold; padding: 5px 12px; border-radius: 6px; }}"
            f"QPushButton:hover {{ background: #7c3aed; color: #ffffff; }}"
            f"QPushButton:disabled {{ background: {c['surf2']}; color: {c['mut']}; border: 1px solid {c['line']}; }}"
        )
        self.btn_probe_models.clicked.connect(self._probe_upstream_models)

        model_row.addWidget(self.model_name_edit, stretch=2)
        model_row.addWidget(self.model_selector, stretch=2)
        model_row.addWidget(self.btn_probe_models)
        model_row_w = QWidget()
        model_row_w.setLayout(model_row)
        form.addRow("模型名称 *:", model_row_w)

        # 联网检索引擎 (默认推荐免 Key 的通用引擎)
        self.search_combo = QComboBox()
        self.search_combo.addItem("Bing (推荐通用网页与高校官网检索 · 免Key)", "bing")
        self.search_combo.addItem("微信搜狗 (sogou-weixin · 考研公众号定向)", "sogou-weixin")
        self.search_combo.addItem("DuckDuckGo (无需 API Key 免费引擎)", "ddg")
        self.search_combo.addItem("Tavily (AI 搜索 API · 需环境变量)", "tavily")
        form.addRow("网络检索引擎:", self.search_combo)

        layout.addLayout(form)

        # 连通性测试区
        test_frame = QFrame()
        test_frame.setFrameShape(QFrame.Shape.StyledPanel)
        test_frame.setStyleSheet(
            f"background: {c['surf2']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 12px;"
        )
        tf_layout = QVBoxLayout(test_frame)
        tf_layout.setSpacing(8)

        test_head = QHBoxLayout()
        tf_title = QLabel(f"🔌 <b style='color:{c['fg']}; font-size: 13px;'>连通性自检面板</b>")
        self.btn_test_api = QPushButton("⚡ 一键测试 API 与搜索连通性")
        self.btn_test_api.setStyleSheet(
            "QPushButton { background-color: #7c3aed; color: #ffffff; font-weight: bold; padding: 7px 16px; border-radius: 6px; border: 1px solid #8b5cf6; }"
            "QPushButton:hover { background-color: #6d28d9; color: #ffffff; }"
            "QPushButton:pressed { background-color: #5b21b6; color: #ffffff; }"
            f"QPushButton:disabled {{ background-color: {c['surf2']}; color: {c['mut']}; border: 1px solid {c['line']}; }}"
        )
        self.btn_test_api.clicked.connect(self._run_connectivity_test)
        test_head.addWidget(tf_title)
        test_head.addStretch()
        test_head.addWidget(self.btn_test_api)
        tf_layout.addLayout(test_head)

        self.test_result_label = QLabel("<span style='color:#cbd5e1;'>尚未进行连通性测试。建议填写配置后点击右上角自测。</span>")
        self.test_result_label.setWordWrap(True)
        self.test_result_label.setStyleSheet(f"color: {c['fg']}; font-size: 13px; line-height: 1.6;")
        tf_layout.addWidget(self.test_result_label)

        layout.addWidget(test_frame)
        layout.addStretch()
        return widget

    # ════════════════════════════════════════════════════════════
    # 联动逻辑与表单控制
    # ════════════════════════════════════════════════════════════

    def _on_exam_mode_changed(self, index: int = 0):
        """响应研考方案模式切换，动态调控专业课二、政治与数学控件的显隐与互锁"""
        mode = self.exam_mode_combo.currentData() or "mode_a"
        is_mode_b = (mode in ("mode_b", "no_math_dual_pro"))
        is_mode_c = (mode in ("mode_c", "mgmt_199"))
        is_mode_d = (mode in ("mode_d", "econ_396"))

        # 1. 专业课二在页面 2、3、4 中的显隐
        self.pro2_row_label.setVisible(is_mode_b)
        self.pro2_row_widget.setVisible(is_mode_b)
        if is_mode_b:
            self.pro_name_row_label.setText("专业课一名称与代码 *:")
            if self.pro_name_edit.text() == "自命题专业课科目":
                self.pro_name_edit.setText("自命题科目1")
                self.pro2_name_edit.setText("自命题科目2")
            elif not self.pro2_name_edit.text():
                self.pro2_name_edit.setText("自命题科目2")
        else:
            self.pro_name_row_label.setText("专业课名称与代码 *:")

        # 2. 资料卡片中的专业课二显隐
        if hasattr(self, "pro2_mat_title"):
            self.pro2_mat_title.setVisible(is_mode_b)
            self.pro2_mat_lbl.setVisible(is_mode_b)
            self.btn_imp_pro2.setVisible(is_mode_b)
            self.btn_open_pro2.setVisible(is_mode_b)

        # 3. 目标与时长中的专业课二显隐
        if hasattr(self, "pro2_time_label"):
            self.pro2_time_label.setVisible(is_mode_b)
            self.pro2_hours_spin.setVisible(is_mode_b)
            self.pro2_score_label.setVisible(is_mode_b)
            self.pro2_target_edit.setVisible(is_mode_b)
            self.pro2_weak_row_label.setVisible(is_mode_b)
            self.pro2_weakness_edit.setVisible(is_mode_b)

        # 4. 模式专属规则（数学、政治、英语及满分基数）
        if is_mode_b:
            # 模式 B：双自命题，初试不考数学
            idx_none = self.math_combo.findData("none")
            if idx_none >= 0:
                self.math_combo.setCurrentIndex(idx_none)
            self.math_combo.setEnabled(False)
            self.pol_hours_spin.setEnabled(True)
            self.pol_target_edit.setEnabled(True)
            self.pol_weakness_edit.setEnabled(True)
        elif is_mode_c:
            # 模式 C：管理类联考 199 (199管综200分 + 英语二100分，初试不考政治与统考数学)
            idx_none = self.math_combo.findData("none")
            if idx_none >= 0:
                self.math_combo.setCurrentIndex(idx_none)
            self.math_combo.setEnabled(False)

            idx_eng2 = self.eng_combo.findData("eng2")
            if idx_eng2 >= 0:
                self.eng_combo.setCurrentIndex(idx_eng2)

            idx_199 = self.pro_type_combo.findData("199")
            if idx_199 >= 0:
                self.pro_type_combo.setCurrentIndex(idx_199)
            if not self.pro_name_edit.text() or "马克思" in self.pro_name_edit.text():
                self.pro_name_edit.setText("199 管理类综合能力")

            self.pol_hours_spin.setValue(0.0)
            self.pol_hours_spin.setEnabled(False)
            self.pol_target_edit.setText("不考政治")
            self.pol_target_edit.setEnabled(False)
            self.pol_weakness_edit.setText("无")
            self.pol_weakness_edit.setEnabled(False)
            if self.total_target_edit.text() in ("370+ 分", "380+ 分"):
                self.total_target_edit.setText("210+ 分 (满分300)")
        elif is_mode_d:
            # 模式 D：经济类联考 396
            self.math_combo.setEnabled(True)
            idx_396 = self.math_combo.findData("396")
            if idx_396 >= 0:
                self.math_combo.setCurrentIndex(idx_396)
            self.pol_hours_spin.setEnabled(True)
            self.pol_target_edit.setEnabled(True)
            self.pol_weakness_edit.setEnabled(True)
        else:
            # 模式 A：统考四科
            self.math_combo.setEnabled(True)
            self.pol_hours_spin.setEnabled(True)
            self.pol_target_edit.setEnabled(True)
            self.pol_weakness_edit.setEnabled(True)

        self._on_math_selection_changed(self.math_combo.currentIndex())
        self._refresh_material_badges()
        self._recalc_total_hours()

    def _on_school_changed(self, text: str):
        query = text.strip()
        if not query:
            self.school_badge_label.setText("待输入目标院校...")
            return
        if self._registry:
            entity = self._registry.resolve(query)
            if entity:
                level_str = " / ".join(entity.level) if entity.level else "普通高校"
                region_str = entity.region or "全国"
                self.school_badge_label.setText(f"🏛️ {entity.name} · {region_str} · {level_str}")
                return
        self.school_badge_label.setText(f"🏛️ {query} · 地方本科高校 / 双非")

    def _on_math_selection_changed(self, index: int):
        math_key = self.math_combo.currentData()
        is_none = (math_key == "none") or not self.math_combo.isEnabled()
        if is_none:
            self.math_hours_spin.setValue(0.0)
            self.math_hours_spin.setEnabled(False)
            self.math_target_edit.setText("不考数学")
            self.math_target_edit.setEnabled(False)
            self.math_weakness_edit.setText("无")
            self.math_weakness_edit.setEnabled(False)
        else:
            self.math_hours_spin.setEnabled(True)
            if self.math_hours_spin.value() <= 0.0:
                self.math_hours_spin.setValue(2.5)
            self.math_target_edit.setEnabled(True)
            if self.math_target_edit.text() == "不考数学":
                self.math_target_edit.setText("110+ 分")
            self.math_weakness_edit.setEnabled(True)
            if self.math_weakness_edit.text() == "无":
                self.math_weakness_edit.setText("计算失误、题型归纳不足")
        if hasattr(self, "btn_import_math"):
            self.btn_import_math.setEnabled(not is_none)
        if hasattr(self, "btn_open_math"):
            self.btn_open_math.setEnabled(not is_none)
        self._refresh_material_badges()
        self._recalc_total_hours()

    def _import_materials(self, subj_key: str):
        """为特定科目批量导入实体参考资料并复制入库"""
        dir_map = {
            "math": self.workspace_root / "01-数学" / "参考资料",
            "eng": self.workspace_root / "02-英语" / "参考资料",
            "pol": self.workspace_root / "03-思想政治理论" / "参考资料",
            "pro": self.workspace_root / "04-专业课" / "参考资料",
            "pro2": self.workspace_root / "04-专业课" / "参考资料_专业课二",
        }
        target_dir = dir_map.get(subj_key)
        if not target_dir:
            return
        target_dir.mkdir(parents=True, exist_ok=True)

        files, _ = QFileDialog.getOpenFileNames(
            self,
            f"选择【{target_dir.name}】考研资料 (可多选)",
            "",
            "考研资料 (*.pdf *.docx *.doc *.txt *.md *.png *.jpg *.jpeg);;所有文件 (*.*)",
        )
        if not files:
            return

        count = 0
        for src in files:
            p_src = Path(src)
            if p_src.is_file():
                dest = target_dir / p_src.name
                shutil.copy2(p_src, dest)
                count += 1

        self._refresh_material_badges()
        QMessageBox.information(
            self,
            "导入成功",
            f"已成功将 {count} 份实体资料导入至【{target_dir.relative_to(self.workspace_root)}/】！\n私教将基于该目录题源进行针对性出题与辅导。",
        )

    def _open_folder(self, subj_key: str):
        """一键在 Windows 文件管理器中打开对应科目的参考资料目录"""
        dir_map = {
            "math": self.workspace_root / "01-数学" / "参考资料",
            "eng": self.workspace_root / "02-英语" / "参考资料",
            "pol": self.workspace_root / "03-思想政治理论" / "参考资料",
            "pro": self.workspace_root / "04-专业课" / "参考资料",
            "pro2": self.workspace_root / "04-专业课" / "参考资料_专业课二",
        }
        target_dir = dir_map.get(subj_key)
        if not target_dir:
            return
        target_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target_dir.resolve())))

    def _refresh_material_badges(self):
        """刷新各科资料徽标显示"""
        if not hasattr(self, "pol_mat_lbl"):
            return
        dir_map = {
            "math": (self.workspace_root / "01-数学" / "参考资料", getattr(self, "math_mat_lbl", None)),
            "eng": (self.workspace_root / "02-英语" / "参考资料", getattr(self, "eng_mat_lbl", None)),
            "pol": (self.workspace_root / "03-思想政治理论" / "参考资料", getattr(self, "pol_mat_lbl", None)),
            "pro": (self.workspace_root / "04-专业课" / "参考资料", getattr(self, "pro_mat_lbl", None)),
            "pro2": (self.workspace_root / "04-专业课" / "参考资料_专业课二", getattr(self, "pro2_mat_lbl", None)),
        }
        is_none_math = (self.math_combo.currentData() == "none") or not self.math_combo.isEnabled()
        mode = self.exam_mode_combo.currentData() if hasattr(self, "exam_mode_combo") else "mode_a"
        is_mode_b = (mode in ("mode_b", "no_math_dual_pro"))

        c = self._c()
        for key, (p, lbl) in dir_map.items():
            if lbl is None:
                continue
            if key == "math" and is_none_math:
                lbl.setText("🚫 当前方案不考数学（无需放置）")
                lbl.setStyleSheet(f"color: {c['mut']}; font-size: 11px;")
                continue
            if key == "pro2" and not is_mode_b:
                lbl.setText("🚫 当前模式未启用第二门专业课")
                lbl.setStyleSheet(f"color: {c['mut']}; font-size: 11px;")
                continue
            if not p.exists():
                lbl.setText("📁 暂无文件 (按大纲标准出题)")
                lbl.setStyleSheet(f"color: {c['mut']}; font-size: 11px;")
                continue
            flist = [f.name for f in p.iterdir() if f.is_file() and not f.name.startswith(".")]
            if flist:
                names_preview = ", ".join(flist[:2])
                if len(flist) > 2:
                    names_preview += f" 等共 {len(flist)} 份"
                lbl.setText(f"✅ 已挂载 {len(flist)} 份: {names_preview}")
                lbl.setStyleSheet(f"color: {c['ok']}; font-size: 11px; font-weight: 500;")
            else:
                lbl.setText("📁 暂无文件 (按大纲标准出题)")
                lbl.setStyleSheet(f"color: {c['mut']}; font-size: 11px;")

    def _on_pro_type_changed(self, index: int):
        val = self.pro_type_combo.currentData()
        if val == "408":
            self.pro_name_edit.setText("408 计算机学科专业基础综合")
        elif val == "199":
            self.pro_name_edit.setText("199 管理类综合能力")

    def _on_exam_date_changed(self, date_text: str):
        try:
            d = date.fromisoformat(date_text.strip())
            days = (d - date.today()).days
            self.countdown_badge.setText(f"⏱️ 距初试约 {days} 天")
        except Exception:
            self.countdown_badge.setText("⏱️ 日期格式不正确 (YYYY-MM-DD)")

    def _apply_time_preset(self, total: float):
        mode = self.exam_mode_combo.currentData() if hasattr(self, "exam_mode_combo") else "mode_a"
        is_mode_b = (mode in ("mode_b", "no_math_dual_pro"))
        is_mode_c = (mode in ("mode_c", "mgmt_199"))
        is_none_math = (self.math_combo.currentData() == "none") or not self.math_combo.isEnabled()

        if is_mode_c:
            self.math_hours_spin.setValue(0.0)
            self.pol_hours_spin.setValue(0.0)
            if total >= 8.0:
                self.eng_hours_spin.setValue(2.5)
                self.pro_hours_spin.setValue(5.5)
            elif total >= 6.0:
                self.eng_hours_spin.setValue(2.0)
                self.pro_hours_spin.setValue(4.5)
            else:
                self.eng_hours_spin.setValue(1.5)
                self.pro_hours_spin.setValue(2.5)
        elif is_mode_b:
            self.math_hours_spin.setValue(0.0)
            if total >= 8.0:
                self.eng_hours_spin.setValue(2.0)
                self.pol_hours_spin.setValue(1.5)
                self.pro_hours_spin.setValue(2.5)
                self.pro2_hours_spin.setValue(2.5)
            elif total >= 6.0:
                self.eng_hours_spin.setValue(1.5)
                self.pol_hours_spin.setValue(1.0)
                self.pro_hours_spin.setValue(2.0)
                self.pro2_hours_spin.setValue(2.0)
            else:
                self.eng_hours_spin.setValue(1.0)
                self.pol_hours_spin.setValue(0.5)
                self.pro_hours_spin.setValue(1.2)
                self.pro2_hours_spin.setValue(1.3)
        elif is_none_math:
            self.math_hours_spin.setValue(0.0)
            if total >= 8.0:
                self.eng_hours_spin.setValue(2.5)
                self.pol_hours_spin.setValue(1.5)
                self.pro_hours_spin.setValue(4.5)
            elif total >= 6.0:
                self.eng_hours_spin.setValue(1.5)
                self.pol_hours_spin.setValue(1.0)
                self.pro_hours_spin.setValue(4.0)
            else:
                self.eng_hours_spin.setValue(1.0)
                self.pol_hours_spin.setValue(0.5)
                self.pro_hours_spin.setValue(2.5)
        else:
            if total >= 8.0:
                self.math_hours_spin.setValue(3.0)
                self.eng_hours_spin.setValue(2.0)
                self.pol_hours_spin.setValue(1.0)
                self.pro_hours_spin.setValue(2.5)
            elif total >= 6.0:
                self.math_hours_spin.setValue(2.5)
                self.eng_hours_spin.setValue(1.5)
                self.pol_hours_spin.setValue(0.5)
                self.pro_hours_spin.setValue(2.0)
            else:
                self.math_hours_spin.setValue(1.5)
                self.eng_hours_spin.setValue(1.0)
                self.pol_hours_spin.setValue(0.5)
                self.pro_hours_spin.setValue(1.0)
        self._recalc_total_hours()

    def _recalc_total_hours(self):
        m_val = 0.0 if (self.math_combo.currentData() == "none" or not self.math_hours_spin.isEnabled()) else self.math_hours_spin.value()
        pol_val = 0.0 if not self.pol_hours_spin.isEnabled() else self.pol_hours_spin.value()
        pro2_val = self.pro2_hours_spin.value() if (hasattr(self, "pro2_hours_spin") and self.pro2_hours_spin.isVisible()) else 0.0
        tot = (
            m_val
            + self.eng_hours_spin.value()
            + pol_val
            + self.pro_hours_spin.value()
            + pro2_val
        )
        self.total_hours_label.setText(f"每日总投入: {tot:.1f} 小时")

    def _on_provider_preset_changed(self, index: int):
        p_name = self.provider_combo.currentText()
        preset = PROVIDER_PRESETS.get(p_name)
        if preset:
            self.base_url_edit.setText(preset["base_url"])
            self.model_name_edit.setText(preset["model"])

    def _open_provider_console(self):
        """在用户系统默认浏览器中打开对应服务商官方控制台"""
        p_name = self.provider_combo.currentText()
        url = PROVIDER_CONSOLE_URLS.get(p_name)
        if not url or "自定义" in p_name:
            b_url = self.base_url_edit.text().strip().lower()
            if "deepseek" in b_url:
                url = "https://platform.deepseek.com/api_keys"
            elif "aliyun" in b_url or "dashscope" in b_url or "bailian" in b_url:
                url = "https://bailian.console.aliyun.com/?apiKey=1#/api-key"
            elif "bigmodel" in b_url or "zhipu" in b_url:
                url = "https://open.bigmodel.cn/usercenter/apikeys"
            elif "volces" in b_url or "doubao" in b_url or "volcengine" in b_url:
                url = "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey"
            elif "moonshot" in b_url or "kimi" in b_url:
                url = "https://platform.moonshot.cn/console/api-keys"
            elif "kuaipao" in b_url:
                url = "https://kuaipao.ai/"
            elif not url:
                url = "https://platform.openai.com/api-keys"
        QDesktopServices.openUrl(QUrl(url))

    def _probe_upstream_models(self):
        """异步拉取上游 /models 接口探测可用模型"""
        api_key = self.api_key_edit.text().strip()
        base_url = self.base_url_edit.text().strip()
        if not api_key:
            QMessageBox.warning(self, "提示", "请先输入 API Key 再进行模型探查。")
            return
        self.btn_probe_models.setEnabled(False)
        self.btn_probe_models.setText("⏳ 探查中...")
        self._probe_worker = ProbeModelsWorker(api_key, base_url, self)
        self._probe_worker.finished_signal.connect(self._on_probe_models_finished)
        self._probe_worker.start()

    def _on_probe_models_finished(self, res: Any, err: Optional[str] = None):
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
            QMessageBox.warning(self, "探查结果", f"探查上游模型失败或返回为空：\n{err or '无可用模型'}\n\n您仍可直接在左侧输入框手动输入模型名称。")
            return
        self.model_selector.blockSignals(True)
        self.model_selector.clear()
        for m in models:
            self.model_selector.addItem(m)
        self.model_selector.setVisible(True)
        self.model_selector.blockSignals(False)

        cur_model = self.model_name_edit.text().strip()
        idx = self.model_selector.findText(cur_model)
        if idx >= 0:
            self.model_selector.setCurrentIndex(idx)
        else:
            self.model_selector.setCurrentIndex(0)
            self.model_name_edit.setText(models[0])

        QMessageBox.information(
            self,
            "探查成功",
            f"🎉 成功探查到 {len(models)} 个上游可用模型！\n已生成模型下拉选择框，您可直接下拉点选，或继续手动输入编辑。"
        )

    def _on_model_selected_from_probe(self, model_name: str):
        if model_name:
            self.model_name_edit.setText(model_name)

    def _toggle_api_key_visibility(self):
        if self.api_key_edit.echoMode() == QLineEdit.EchoMode.Password:
            self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_key.setText("🔒")
        else:
            self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_key.setText("👁️")

    def _run_connectivity_test(self):
        api_key = self.api_key_edit.text().strip()
        base_url = self.base_url_edit.text().strip()
        model = self.model_name_edit.text().strip()
        search_provider = self.search_combo.currentData() or "bing"

        if not api_key:
            QMessageBox.warning(self, "提示", "请先输入 API Key 再进行连通性测试。")
            return

        c = self._c()
        self.btn_test_api.setEnabled(False)
        self.test_result_label.setText("⏳ 正在探测 LLM 对话端点与搜索引擎响应，请稍候...")
        self.test_result_label.setStyleSheet(f"color: {c['warn']}; font-size: 12px; line-height: 1.5;")

        self._worker = ConnectivityWorker(api_key, base_url, model, search_provider, self)
        self._worker.finished_signal.connect(self._on_connectivity_finished)
        self._worker.start()

    def _on_connectivity_finished(self, res: dict):
        self.btn_test_api.setEnabled(True)
        c = self._c()
        llm_ok = res.get("llm_ok", False)
        llm_detail = res.get("llm_detail", "")
        search_ok = res.get("search_ok", False)
        search_detail = res.get("search_detail", "")

        llm_icon = "🟢" if llm_ok else "🔴"
        search_icon = "🟢" if search_ok else "🔴"
        llm_color = "#34d399" if llm_ok else "#f87171"
        search_color = "#34d399" if search_ok else "#fbbf24"

        # 若搜索引擎为 Tavily 且因缺少环境变量失败，提供友好建议
        if not search_ok and "TAVILY_API_KEY" in search_detail:
            search_detail += "（提示：可将上方检索引擎切换为 Bing 或 DuckDuckGo 享受开箱即用免 Key 检索）"

        text = (
            f"<div style='color:{c['fg']}; font-size:13px; line-height:1.7;'>"
            f"<b style='color:{c['fg']}; font-size:13px;'>自检报告：</b><br>"
            f"<b>大模型 API</b>: {llm_icon} <span style='color:{llm_color}; font-weight:600;'>{llm_detail}</span><br>"
            f"<b>网络检索</b>: {search_icon} <span style='color:{search_color}; font-weight:500;'>{search_detail}</span>"
            f"</div>"
        )
        self.test_result_label.setText(text)
        self.test_result_label.setStyleSheet(f"color: {c['fg']}; font-size: 13px; line-height: 1.7;")

    # ════════════════════════════════════════════════════════════
    # 步骤导航与视图刷新
    # ════════════════════════════════════════════════════════════

    def _update_step_view(self):
        c = self._c()
        self.stacked_widget.setCurrentIndex(self._current_step)
        is_dark = c.get("is_dark", True)
        for idx, lbl in enumerate(self.step_labels):
            if idx == self._current_step:
                lbl.setStyleSheet(
                    "background: #7c3aed; color: #ffffff; font-weight: bold; font-size: 13px; "
                    "padding: 6px 12px; border-radius: 6px; border: 1px solid #a78bfa;"
                )
            elif idx < self._current_step:
                if is_dark:
                    lbl.setStyleSheet(
                        "background: rgba(124, 58, 237, 0.25); color: #c4b5fd; font-weight: 600; font-size: 13px; "
                        "padding: 6px 12px; border-radius: 6px; border: 1px solid rgba(167, 139, 250, 0.4);"
                    )
                else:
                    lbl.setStyleSheet(
                        "background: #ede9fe; color: #6d28d9; font-weight: 600; font-size: 13px; "
                        "padding: 6px 12px; border-radius: 6px; border: 1px solid #c4b5fd;"
                    )
            else:
                if is_dark:
                    lbl.setStyleSheet(
                        "background: rgba(255, 255, 255, 0.05); color: #94a3b8; font-size: 13px; "
                        "padding: 6px 12px; border-radius: 6px; border: 1px solid rgba(255, 255, 255, 0.1);"
                    )
                else:
                    lbl.setStyleSheet(
                        "background: #f1f5f9; color: #64748b; font-size: 13px; "
                        "padding: 6px 12px; border-radius: 6px; border: 1px solid #e2e8f0;"
                    )

        arrow_color = "#a78bfa" if is_dark else "#7c3aed"
        for arrow in self.step_arrows:
            arrow.setStyleSheet(f"color: {arrow_color}; font-size: 13px; font-weight: bold;")

        self.btn_prev.setEnabled(self._current_step > 0)
        is_last = (self._current_step == self._total_steps - 1)
        self.btn_next.setVisible(not is_last)
        self.btn_finish.setVisible(is_last)

    def _validate_current_step(self) -> bool:
        if self._current_step == 0:
            school = self.school_edit.text().strip()
            major = self.major_edit.text().strip()
            if not school:
                QMessageBox.warning(self, "请填写目标院校", "目标院校为必填项，请输入您要报考的高校名称。")
                self.school_edit.setFocus()
                return False
            if not major:
                QMessageBox.warning(self, "请填写报考专业", "报考专业为必填项，请输入您的报考专业。")
                self.major_edit.setFocus()
                return False
        elif self._current_step == 1:
            pro_name = self.pro_name_edit.text().strip()
            if not pro_name:
                QMessageBox.warning(self, "请填写专业课名称", "专业课名称与科目代码为必填项。")
                self.pro_name_edit.setFocus()
                return False
            mode = self.exam_mode_combo.currentData() or "mode_a"
            if mode in ("mode_b", "no_math_dual_pro"):
                pro2_name = self.pro2_name_edit.text().strip()
                if not pro2_name:
                    QMessageBox.warning(self, "请填写专业课二名称", "在双自命题模式下，专业课二名称与科目代码为必填项。")
                    self.pro2_name_edit.setFocus()
                    return False
        elif self._current_step == 2:
            try:
                date.fromisoformat(self.exam_date_edit.text().strip())
            except Exception:
                QMessageBox.warning(self, "日期格式错误", "初试日期格式须为 YYYY-MM-DD，如 2026-12-19。")
                self.exam_date_edit.setFocus()
                return False
        return True

    def _on_next_step(self):
        if not self._validate_current_step():
            return
        if self._current_step < self._total_steps - 1:
            self._current_step += 1
            self._update_step_view()

    def _on_prev_step(self):
        if self._current_step > 0:
            self._current_step -= 1
            self._update_step_view()

    # ════════════════════════════════════════════════════════════
    # 数据回填与提交
    # ════════════════════════════════════════════════════════════

    def _load_from_config(self, cfg: dict):
        if not cfg:
            # 默认值回填
            self.school_edit.setText("目标院校")
            self.major_edit.setText("目标专业 (专业代码)")
            self.pro_name_edit.setText("自命题专业课科目")
            self.pro2_name_edit.setText("自命题科目2")
        else:
            plan = cfg.get("study_plan") or {}
            school = plan.get("school") or cfg.get("target_school", "目标院校")
            major = plan.get("major") or cfg.get("target_major", "目标专业 (专业代码)")
            self.school_edit.setText(school)
            self.major_edit.setText(major)
            self.backup_school_edit.setText(plan.get("backup_school", ""))

            # 方案模式
            mode_val = plan.get("exam_mode") or cfg.get("exam_mode", "mode_a")
            if mode_val in ("standard", "unified_4"):
                mode_val = "mode_a"
            elif mode_val in ("no_math_dual_pro", "dual_pro"):
                mode_val = "mode_b"
            elif mode_val in ("mgmt_199", "199"):
                mode_val = "mode_c"
            elif mode_val in ("econ_396", "396"):
                mode_val = "mode_d"
            idx_m = self.exam_mode_combo.findData(mode_val)
            if idx_m >= 0:
                self.exam_mode_combo.setCurrentIndex(idx_m)

            # 数学
            m_key = plan.get("math_key", "none" if plan.get("math_name") == "不考数学" else "math1")
            idx = self.math_combo.findData(m_key)
            if idx >= 0:
                self.math_combo.setCurrentIndex(idx)

            # 英语
            e_key = plan.get("eng_key", "eng1")
            idx = self.eng_combo.findData(e_key)
            if idx >= 0:
                self.eng_combo.setCurrentIndex(idx)

            # 专业课
            pro_name = plan.get("pro_name", "自命题专业课科目")
            self.pro_name_edit.setText(pro_name)
            self.pro2_name_edit.setText(plan.get("pro2_name", "自命题科目2"))
            p_type = plan.get("pro_type", "custom")
            idx = self.pro_type_combo.findData(p_type)
            if idx >= 0:
                self.pro_type_combo.setCurrentIndex(idx)

            # 日期
            exam_date_val = plan.get("exam_date") or cfg.get("exam_date", "2026-12-19")
            self.exam_date_edit.setText(exam_date_val)

            # 时长
            self.math_hours_spin.setValue(float(plan.get("math_hours", 0.0 if m_key == "none" else 2.5)))
            self.eng_hours_spin.setValue(float(plan.get("eng_hours", 1.5)))
            self.pol_hours_spin.setValue(float(plan.get("pol_hours", 0.5)))
            self.pro_hours_spin.setValue(float(plan.get("pro_hours", 2.0)))
            self.pro2_hours_spin.setValue(float(plan.get("pro2_hours", 2.0)))
            self._recalc_total_hours()

            # 目标分
            self.math_target_edit.setText(plan.get("math_target", "不考数学" if m_key == "none" else "110+ 分"))
            self.eng_target_edit.setText(plan.get("eng_target", "65+ 分"))
            self.pol_target_edit.setText(plan.get("pol_target", "70+ 分"))
            self.pro_target_edit.setText(plan.get("pro_target", "120-130 分"))
            self.pro2_target_edit.setText(plan.get("pro2_target", "120-130 分"))
            self.total_target_edit.setText(plan.get("total_target", "370+ 分"))

            # 痛点
            self.math_weakness_edit.setText(plan.get("math_weakness", "无" if m_key == "none" else "计算失误"))
            self.eng_weakness_edit.setText(plan.get("eng_weakness", "看不懂题目、待诊断薄弱点困难"))
            self.pol_weakness_edit.setText(plan.get("pol_weakness", "待诊断薄弱点、多选题容易漏选"))
            self.pro_weakness_edit.setText(plan.get("pro_weakness", "待诊断薄弱点、论述题缺乏框架"))
            self.pro2_weakness_edit.setText(plan.get("pro2_weakness", "待诊断薄弱点、论述题缺乏框架"))

            # 辅导风格
            cur_style = plan.get("style_name") or cfg.get("coaching_style", COACHING_STYLES[0][0])
            for idx, (s_name, _) in enumerate(COACHING_STYLES):
                if s_name == cur_style or s_name.split()[0] in cur_style:
                    self.style_radios[idx].setChecked(True)
                    break

            # API 配置
            self.api_key_edit.setText(cfg.get("api_key", ""))
            self.base_url_edit.setText(cfg.get("base_url", "https://api.deepseek.com/v1"))
            self.model_name_edit.setText(cfg.get("model", "deepseek-chat"))
            sp = cfg.get("search_provider") or cfg.get("search_engine", "bing")
            idx = self.search_combo.findData(sp)
            if idx >= 0:
                self.search_combo.setCurrentIndex(idx)

        # 触发选科模式与数学选科互锁状态
        self._on_exam_mode_changed(self.exam_mode_combo.currentIndex())

    def collect_config(self) -> Dict[str, Any]:
        """收集 5 个步骤中的全部输入项生成统一配置字典"""
        mode_key = self.exam_mode_combo.currentData() or "mode_a"
        is_mode_b = (mode_key in ("mode_b", "no_math_dual_pro"))
        is_mode_c = (mode_key in ("mode_c", "mgmt_199"))

        m_key = self.math_combo.currentData()
        m_name = self.math_combo.currentText().split()[0]
        if m_key == "none" or not self.math_hours_spin.isEnabled():
            m_name = "不考数学"
            m_hours = 0.0
            m_target = "不考数学"
            m_weakness = "无"
        else:
            m_hours = self.math_hours_spin.value()
            m_target = self.math_target_edit.text().strip()
            m_weakness = self.math_weakness_edit.text().strip()

        e_key = self.eng_combo.currentData()
        e_name = self.eng_combo.currentText().split()[0]
        e_hours = self.eng_hours_spin.value()
        e_target = self.eng_target_edit.text().strip()
        e_weakness = self.eng_weakness_edit.text().strip()

        if is_mode_c:
            pol_hours = 0.0
            pol_target = "不考政治"
            pol_weakness = "无"
            pol_baseline = "不考政治"
        else:
            pol_hours = self.pol_hours_spin.value()
            pol_target = self.pol_target_edit.text().strip()
            pol_weakness = self.pol_weakness_edit.text().strip()
            pol_baseline = "摸底53"

        pro_name = self.pro_name_edit.text().strip()
        pro_hours = self.pro_hours_spin.value()
        pro_target = self.pro_target_edit.text().strip()
        pro_weakness = self.pro_weakness_edit.text().strip()

        pro2_name = self.pro2_name_edit.text().strip() if is_mode_b else ""
        pro2_hours = self.pro2_hours_spin.value() if is_mode_b else 0.0
        pro2_target = self.pro2_target_edit.text().strip() if is_mode_b else ""
        pro2_weakness = self.pro2_weakness_edit.text().strip() if is_mode_b else ""

        exam_date_val = self.exam_date_edit.text().strip()
        days_left = 90
        try:
            days_left = (date.fromisoformat(exam_date_val) - date.today()).days
        except Exception:
            pass

        selected_style_idx = self.style_btn_group.checkedId()
        if 0 <= selected_style_idx < len(COACHING_STYLES):
            selected_style = COACHING_STYLES[selected_style_idx][0]
        else:
            selected_style = COACHING_STYLES[0][0]

        tot_hours = round(
            m_hours
            + e_hours
            + pol_hours
            + pro_hours
            + pro2_hours,
            1,
        )

        study_plan = {
            "school": self.school_edit.text().strip(),
            "major": self.major_edit.text().strip(),
            "backup_school": self.backup_school_edit.text().strip(),
            "exam_date": exam_date_val,
            "days_left": days_left,
            "stage_name": self.stage_combo.currentText().split()[0],
            "style_name": selected_style,
            "exam_mode": mode_key,
            "math_key": m_key,
            "math_name": m_name,
            "eng_key": e_key,
            "eng_name": e_name,
            "pro_type": self.pro_type_combo.currentData(),
            "pro_name": pro_name,
            "pro2_name": pro2_name,
            "math_hours": m_hours,
            "eng_hours": e_hours,
            "pol_hours": pol_hours,
            "pro_hours": pro_hours,
            "pro2_hours": pro2_hours,
            "total_hours": tot_hours,
            "math_baseline": "不考数学" if (m_key == "none" or not self.math_hours_spin.isEnabled()) else "摸底60",
            "eng_baseline": "基础不好",
            "pol_baseline": pol_baseline,
            "pro_baseline": "基础不好",
            "pro2_baseline": "基础不好" if is_mode_b else "",
            "math_target": m_target,
            "eng_target": e_target,
            "pol_target": pol_target,
            "pro_target": pro_target,
            "pro2_target": pro2_target,
            "total_target": self.total_target_edit.text().strip(),
            "math_weakness": m_weakness,
            "eng_weakness": e_weakness,
            "pol_weakness": pol_weakness,
            "pro_weakness": pro_weakness,
            "pro2_weakness": pro2_weakness,
            "pol_disabled": is_mode_c,
        }

        full_config = {
            "onboarding_completed": True,
            "target_school": study_plan["school"],
            "target_major": study_plan["major"],
            "coaching_style": selected_style,
            "exam_mode": mode_key,
            "api_key": self.api_key_edit.text().strip(),
            "base_url": self.base_url_edit.text().strip(),
            "model": self.model_name_edit.text().strip(),
            "search_provider": self.search_combo.currentData(),
            "search_engine": self.search_combo.currentData(),
            "study_plan": study_plan,
        }
        return full_config

    def _on_finish(self):
        """保存配置并即时生效"""
        if not self._validate_current_step():
            return
        full_cfg = self.collect_config()
        try:
            saved = save_onboarding_config(self.config_path, full_cfg, self.workspace_root)
            self.config_saved.emit(saved)
            QMessageBox.information(
                self,
                "建档成功",
                f"🎉 考研学情档案已创建成功并即时生效！\n\n"
                f"目标院校：{saved['target_school']}\n"
                f"报考专业：{saved['target_major']}\n"
                f"初试倒计时：{saved['study_plan'].get('days_left')} 天\n"
                f"当前辅导风格：{saved['coaching_style']}\n\n"
                f"教育部考纲与各科复习档案已同步配置完毕。",
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"学情档案持久化过程中发生异常：{e}")


__all__ = ["COACHING_STYLES", "ConnectivityWorker", "OnboardingWizard", "PROVIDER_PRESETS"]
