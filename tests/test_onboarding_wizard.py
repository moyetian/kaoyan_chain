# -*- coding: utf-8 -*-
"""自动化测试套件：全功能 GUI 新手引导向导与设置 (Milestone M2 / R2)

涵盖：
1. is_unconfigured 干净状态判定覆盖
2. 向导 5 步状态机、控件联动（学校模糊解析、不考数学联动、倒计时与阶段）
3. 双向持久化断言（ky_config.json + AGENTS.md）
4. GUI 主界面即时热更新（on_config_updated、顶栏与对话框）
5. 设置中心启动向导入口按钮
6. LLM API 与检索引擎连通性自测（包含 Mock 成功、鉴权失败、超时等各种网络情况）
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 测试")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tools.gui.main_window import MainWindow  # noqa: E402
from tools.gui.services import settings as settings_svc  # noqa: E402
from tools.gui.widgets.onboarding_wizard import OnboardingWizard  # noqa: E402
from tools.gui.widgets.settings_dialog import SettingsDialog  # noqa: E402


SAMPLE_AGENTS_MD = """# AGENTS.md —— 考研全科 AI 私人教师中枢 · 总控系统协议

- **目标院校**：`目标院校`
- **报考专业**：`报考专业`
- **初试日期**：`2026-12-19` (倒计时约 90 天)
- **当前备考阶段**：`基础夯实阶段`
- **当前激活辅导风格**：`严格把关·保姆提分型 (Strict & Disciplined)`
- **各科目标矩阵（示例模板）**：

| 科目                    | 摸底/基准分     | 目标成绩          | 每日基准投入 | 核心提分盘与策略                       |
| --------------------- | ---------- | ------------- | ------ | ------------------------------ |
| **科目一：数学一 (301)** | 摸底60 | **110+ 分** | 2.5 小时 | 攻克必考核心题型，严防超纲，规避计算失误，步骤规范化 |
| **科目二：英语一 (201)** | 摸底62 | **65+ 分** | 2.0 小时 | 搭积木拆解长难句，定位阅读选项逻辑，固化作文功能句模板 |
| **科目三：思想政治理论** | 摸底62 | **70+ 分** | 1.0 小时 | 单选+多选得分盘（38~42分），帽子词秒杀，后期背诵闭环 |
| **科目四：专业课** | 摸底75 | **120-130 分** | 2.0 小时 | 权威教材体系+历年真题深度解剖，白名单题源抽题门禁 |
| **合计** | [摸底总分] | **370+ 分** | 7.5 小时 | **结构性提分，稳拿基本盘，拒绝偏难怪题** |

---

### 【个性化学情与作息调节机制】 (系统已锁定)
- **每日时间预算**: 每日投入 `7.5 小时` (数学: 2.5h / 英语: 2.0h / 政治: 1.0h / 专业课: 2.0h)
- **每周休整窗口**: `每周日晚 18:00~22:30 放松休整`
- **每月模考复盘**: `每月最后一个周日全天闭卷模考与全科雷达复盘`
- **手头资料白名单 (AI 严守范围)**:
  - 数学: `官方考纲出题`
  - 英语: `官方考纲出题`
  - 政治: `官方考纲出题`
  - 专业课: `官方考纲出题`
- **核心薄弱诊断与攻坚防线**:
  - 数学薄弱点: `计算失误`
  - 英语薄弱点: `阅读定位不熟练`
  - 政治薄弱点: `多选题易漏选`
  - 专业课薄弱点: `知识点记忆不牢`

### 二、四种私教辅导风格设定（按需切换）
"""


@pytest.fixture(scope="module")
def app():
    inst = QApplication.instance() or QApplication(sys.argv)
    yield inst
    inst.setStyleSheet("")


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """离屏自动化测试中，将阻塞式弹窗 mock 为无害直接确认。"""
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)


# ════════════════════════════════════════════════════════════════
# Tier 1: is_unconfigured 纯数据层干净状态检测
# ════════════════════════════════════════════════════════════════

def test_is_unconfigured_states(tmp_path):
    # 1. 配置文件不存在
    assert settings_svc.is_unconfigured(tmp_path) is True

    # 2. 配置文件格式损坏
    cfg_file = tmp_path / "ky_config.json"
    cfg_file.write_text("{broken-json", encoding="utf-8")
    assert settings_svc.is_unconfigured(tmp_path) is True

    # 3. 未标记 onboarding_completed
    cfg_file.write_text(json.dumps({"onboarding_completed": False}), encoding="utf-8")
    assert settings_svc.is_unconfigured(tmp_path) is True

    # 4. 院校为默认占位符 "目标院校"
    cfg_file.write_text(json.dumps({
        "onboarding_completed": True,
        "study_plan": {"school": "目标院校", "major": "030100 法学"}
    }), encoding="utf-8")
    assert settings_svc.is_unconfigured(tmp_path) is True

    # 5. 专业为默认占位符 "报考专业"
    cfg_file.write_text(json.dumps({
        "onboarding_completed": True,
        "study_plan": {"school": "中国人民大学", "major": "报考专业"}
    }), encoding="utf-8")
    assert settings_svc.is_unconfigured(tmp_path) is True

    # 6. 完备合法配置
    cfg_file.write_text(json.dumps({
        "onboarding_completed": True,
        "study_plan": {"school": "中国人民大学", "major": "030100 法学"}
    }), encoding="utf-8")
    assert settings_svc.is_unconfigured(tmp_path) is False


@pytest.mark.parametrize("placeholder", [
    "报考专业",
    "目标专业",
    "目标专业 (专业代码)",
    "目标专业 (专业代码-方向)",
])
def test_is_unconfigured_detects_all_major_placeholders(tmp_path, placeholder):
    """[P10 回归] 所有已知专业占位符都必须被判为「未配置」。

    名单来自 ``privacy_policy.MAJOR_PLACEHOLDERS``（单一事实源）。
    修复前 ``settings.py`` 本地硬编码的只有
    ``("报考专业", "目标专业 (专业代码-方向)")``，而规则表实际还会产出
    ``"目标专业 (专业代码)"`` 与 ``"目标专业"`` —— 后两者会被误判成「已配置」。
    阴性对照：把 settings.py 的名单换回旧的硬编码两项，本用例后两个参数变红。
    """
    cfg_file = tmp_path / "ky_config.json"
    cfg_file.write_text(json.dumps({
        "onboarding_completed": True,
        "study_plan": {"school": "中国人民大学", "major": placeholder},
    }), encoding="utf-8")
    assert settings_svc.is_unconfigured(tmp_path) is True, placeholder


def test_is_unconfigured_false_for_real_major(tmp_path):
    """反向确认（防过度拦截）：真实专业名不得被判为「未配置」。"""
    cfg_file = tmp_path / "ky_config.json"
    cfg_file.write_text(json.dumps({
        "onboarding_completed": True,
        "study_plan": {"school": "中国人民大学", "major": "030100 法学"},
    }), encoding="utf-8")
    assert settings_svc.is_unconfigured(tmp_path) is False


# ════════════════════════════════════════════════════════════════
# Tier 2: 向导组件实例化与 5 步导航状态机
# ════════════════════════════════════════════════════════════════

def test_onboarding_wizard_instantiation_and_defaults(app, tmp_path):
    wizard = OnboardingWizard(workspace_root=tmp_path)
    try:
        assert wizard.stacked_widget.count() == 5
        assert wizard._current_step == 0
        assert wizard.btn_prev.isEnabled() is False
        assert not wizard.btn_next.isHidden()
        assert wizard.btn_finish.isHidden()
    finally:
        wizard.close()


def test_onboarding_wizard_step1_school_match(app, tmp_path):
    wizard = OnboardingWizard(workspace_root=tmp_path)
    try:
        # 模糊输入并触发校名解析
        wizard.school_edit.setText("中国人民大学")
        badge = wizard.school_badge_label.text()
        assert "中国人民大学" in badge

        wizard.school_edit.setText("华中科技大学")
        badge2 = wizard.school_badge_label.text()
        assert "华中科技大学" in badge2
    finally:
        wizard.close()


def test_onboarding_wizard_step2_math_interlock(app, tmp_path):
    wizard = OnboardingWizard(workspace_root=tmp_path)
    try:
        # 选择不考数学
        idx_none = wizard.math_combo.findData("none")
        wizard.math_combo.setCurrentIndex(idx_none)
        assert wizard.math_hours_spin.isEnabled() is False
        assert wizard.math_hours_spin.value() == 0.0
        assert wizard.math_target_edit.isEnabled() is False
        assert wizard.math_target_edit.text() == "不考数学"
        assert wizard.math_weakness_edit.isEnabled() is False
        assert wizard.math_weakness_edit.text() == "无"

        # 切回数学一
        idx_math1 = wizard.math_combo.findData("math1")
        wizard.math_combo.setCurrentIndex(idx_math1)
        assert wizard.math_hours_spin.isEnabled() is True
        assert wizard.math_hours_spin.value() > 0.0
        assert wizard.math_target_edit.isEnabled() is True
        assert wizard.math_weakness_edit.isEnabled() is True
    finally:
        wizard.close()


def test_onboarding_wizard_math_interlock_on_existing_config(app, tmp_path):
    cfg_path = tmp_path / "ky_config.json"
    cfg_data = {
        "onboarding_completed": True,
        "target_school": "中国人民大学",
        "target_major": "030100 法学",
        "study_plan": {
            "school": "中国人民大学",
            "major": "030100 法学",
            "math_key": "none",
            "math_name": "不考数学",
            "math_hours": 0.0,
            "eng_hours": 1.5,
            "pol_hours": 0.5,
            "pro_hours": 2.0,
            "total_hours": 4.0,
        }
    }
    cfg_path.write_text(json.dumps(cfg_data, ensure_ascii=False), encoding="utf-8")

    wiz = OnboardingWizard(workspace_root=tmp_path)
    try:
        assert wiz.math_hours_spin.isEnabled() is False
        assert wiz.math_target_edit.isEnabled() is False
        assert wiz.math_weakness_edit.isEnabled() is False
        assert wiz.math_hours_spin.value() == 0.0
        assert wiz.collect_config()["study_plan"]["math_hours"] == 0.0
        assert wiz.collect_config()["study_plan"]["math_target"] == "不考数学"
        assert wiz.collect_config()["study_plan"]["math_weakness"] == "无"
    finally:
        wiz.close()

    # 防御性测试：即使 JSON 脏数据中 math_hours 非零，加载后也必须强制锁定归零
    dirty_cfg = {
        "onboarding_completed": True,
        "study_plan": {
            "school": "中国人民大学",
            "major": "030100 法学",
            "math_key": "none",
            "math_name": "不考数学",
            "math_hours": 2.5,
            "math_target": "120+",
            "math_weakness": "概念模糊",
        }
    }
    cfg_path.write_text(json.dumps(dirty_cfg, ensure_ascii=False), encoding="utf-8")
    wiz2 = OnboardingWizard(workspace_root=tmp_path)
    try:
        assert wiz2.math_hours_spin.isEnabled() is False
        assert wiz2.math_target_edit.isEnabled() is False
        assert wiz2.math_weakness_edit.isEnabled() is False
        assert wiz2.math_hours_spin.value() == 0.0
        collected = wiz2.collect_config()
        assert collected["study_plan"]["math_hours"] == 0.0
        assert collected["study_plan"]["math_target"] == "不考数学"
        assert collected["study_plan"]["math_weakness"] == "无"
    finally:
        wiz2.close()


def test_onboarding_wizard_navigation_and_validation(app, tmp_path):
    wizard = OnboardingWizard(workspace_root=tmp_path)
    try:
        # Step 0: 空院校拦截
        wizard.school_edit.setText("")
        wizard._on_next_step()
        assert wizard._current_step == 0

        # Step 0: 空专业拦截
        wizard.school_edit.setText("中国人民大学")
        wizard.major_edit.setText("")
        wizard._on_next_step()
        assert wizard._current_step == 0

        # 填全 Step 0
        wizard.major_edit.setText("030100 法学")
        wizard._on_next_step()
        assert wizard._current_step == 1

        # Step 1 -> Step 2 -> Step 3 -> Step 4
        wizard._on_next_step()
        assert wizard._current_step == 2
        wizard._on_next_step()
        assert wizard._current_step == 3
        wizard._on_next_step()
        assert wizard._current_step == 4

        # 第 5 步：下一步隐藏，完成按钮出现
        assert not wizard.btn_finish.isHidden()
        assert wizard.btn_next.isHidden()

        # 后退
        wizard._on_prev_step()
        assert wizard._current_step == 3
        assert wizard.btn_finish.isHidden()
        assert not wizard.btn_next.isHidden()
    finally:
        wizard.close()


# ════════════════════════════════════════════════════════════════
# Tier 3: 双向持久化验证 (ky_config.json + AGENTS.md)
# ════════════════════════════════════════════════════════════════

def test_onboarding_wizard_dual_persistence(app, tmp_path):
    # 准备环境中的 AGENTS.md
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text(SAMPLE_AGENTS_MD, encoding="utf-8")

    wizard = OnboardingWizard(workspace_root=tmp_path)
    try:
        wizard.school_edit.setText("中国人民大学")
        wizard.major_edit.setText("030100 法学")
        idx_none = wizard.math_combo.findData("none")
        wizard.math_combo.setCurrentIndex(idx_none)
        wizard.pro_name_edit.setText("610 法学基础 810 法学综合")
        wizard.exam_date_edit.setText("2026-12-19")
        wizard.api_key_edit.setText("sk-mock-auth-token")
        wizard.base_url_edit.setText("https://api.deepseek.com/v1")
        wizard.model_name_edit.setText("deepseek-chat")

        # 选中「温和启发」风格
        for rb in wizard.style_radios:
            if "温和启发" in rb.text():
                rb.setChecked(True)
                break

        full_cfg = wizard.collect_config()
        saved = settings_svc.save_onboarding_config(tmp_path / "ky_config.json", full_cfg, tmp_path)

        # 1. 验证 ky_config.json
        cfg_on_disk = json.loads((tmp_path / "ky_config.json").read_text(encoding="utf-8"))
        assert cfg_on_disk["onboarding_completed"] is True
        assert cfg_on_disk["target_school"] == "中国人民大学"
        assert cfg_on_disk["target_major"] == "030100 法学"
        assert cfg_on_disk["study_plan"]["math_key"] == "none"
        assert cfg_on_disk["api_key"] == "sk-mock-auth-token"
        assert "温和启发" in cfg_on_disk["coaching_style"]

        # 2. 验证 AGENTS.md 规范更新
        agents_text = agents_file.read_text(encoding="utf-8")
        assert "- **目标院校**：`中国人民大学`" in agents_text
        assert "- **报考专业**：`030100 法学`" in agents_text
        assert "- **初试日期**：`2026-12-19`" in agents_text
        assert "温和启发" in agents_text
        assert "不考数学" in agents_text
    finally:
        wizard.close()


# ════════════════════════════════════════════════════════════════
# Tier 4: GUI 即时热更新与设置面板按钮集成
# ════════════════════════════════════════════════════════════════

def test_gui_hot_update_on_config_updated(app, tmp_path):
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text(SAMPLE_AGENTS_MD, encoding="utf-8")
    cfg_file = tmp_path / "ky_config.json"
    cfg_file.write_text(json.dumps({
        "onboarding_completed": True,
        "study_plan": {"school": "中国人民大学", "major": "030100 法学", "exam_date": "2026-12-19"}
    }), encoding="utf-8")

    win = MainWindow(workspace_root=tmp_path)
    try:
        new_config = {
            "onboarding_completed": True,
            "target_school": "中国人民大学",
            "target_major": "030100 法学",
            "coaching_style": "温和启发·减负鼓励型 (Encouraging Mentor)",
            "study_plan": {
                "school": "中国人民大学",
                "major": "030100 法学",
                "exam_date": "2026-12-19",
                "days_left": 92,
                "style_name": "温和启发·减负鼓励型 (Encouraging Mentor)",
                "math_key": "none",
            },
        }
        win.on_config_updated(new_config)

        # 顶栏应即刻热刷新
        assert "中国人民大学" in win.meta_label.text()
        assert "030100 法学" in win.meta_label.text()
        assert "温和启发" in win.meta_label.text()
        assert "初试倒计时" in win.countdown_label.text()

        # 对话框应留有热生效通知记录
        chat_text = win.chat_display.toPlainText()
        assert "中国人民大学" in chat_text
        assert "030100 法学" in chat_text
    finally:
        win.close()


def test_settings_dialog_has_wizard_button(app, tmp_path):
    win = MainWindow(workspace_root=tmp_path)
    try:
        dlg = SettingsDialog(win)
        try:
            assert hasattr(dlg, "wizard_btn")
            assert "🚀" in dlg.wizard_btn.text()
            assert "向导" in dlg.wizard_btn.text()
        finally:
            dlg.close()
    finally:
        win.close()


# ════════════════════════════════════════════════════════════════
# Tier 5: 连通性自测 (Mock 网络隔离与异常边界)
# ════════════════════════════════════════════════════════════════

class DummyHTTPResponse:
    def __init__(self, body=b'{"choices": [{"message": {"content": "pong"}}]}', status=200):
        self.body = body
        self.status = status

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class DummySearchProvider:
    def search(self, query, limit=1):
        return [{"title": "考研真题与官方大纲", "url": "https://yz.chsi.com.cn"}]


def test_api_connectivity_mocked_success(monkeypatch):
    import urllib.request

    monkeypatch.setattr(settings_svc, "safe_urlopen", lambda req, timeout=10.0: DummyHTTPResponse())

    try:
        from tools.search import providers
        monkeypatch.setattr(providers, "make_provider", lambda name: DummySearchProvider())
    except Exception:
        pass

    res = settings_svc.test_api_connectivity(
        api_key="sk-test-key",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        search_provider="bing",
        timeout=5.0,
    )
    assert res["llm_ok"] is True
    assert res["llm_status"] == "ok"
    assert res["llm_latency_ms"] >= 0
    assert "连通成功" in res["llm_detail"]


def test_api_connectivity_mocked_auth_error(monkeypatch):
    import urllib.request

    def raise_401(req, timeout=10.0):
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b'{"error": "invalid_api_key"}')
        )

    monkeypatch.setattr(settings_svc, "safe_urlopen", raise_401)

    res = settings_svc.test_api_connectivity(
        api_key="sk-invalid-key",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        search_provider="bing",
    )
    assert res["llm_ok"] is False
    assert res["llm_status"] == "auth_error"
    assert "鉴权失败" in res["llm_detail"]


def test_api_connectivity_mocked_timeout(monkeypatch):
    import urllib.request

    def raise_timeout(req, timeout=10.0):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(settings_svc, "safe_urlopen", raise_timeout)

    res = settings_svc.test_api_connectivity(
        api_key="sk-test-key",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        search_provider="bing",
    )
    assert res["llm_ok"] is False
    assert res["llm_status"] == "timeout"
    assert "超时" in res["llm_detail"]


def test_api_connectivity_missing_config():
    res = settings_svc.test_api_connectivity("", "", "")
    assert res["llm_ok"] is False
    assert res["llm_status"] == "missing_config"
    assert "缺少" in res["llm_detail"]
