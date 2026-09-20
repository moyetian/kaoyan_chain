# -*- coding: utf-8 -*-
"""自动化测试套件：多研考方案模式 (Mode A/B/C/D) 与核心技能 LLM 真实注入测试

测试覆盖：
1. 多模式切换 (模式 A 标准四科 / 模式 B 不考数学双专业课 / 模式 C 199 管理类联考 / 模式 D 396 经济类联考)
2. 上游模型探查 (fetch_upstream_models & 下拉选择器)
3. 官方服务商控制台跳转逻辑
4. 今日任务 (study_planner.py) 真实 LLM 调用与模板降级、双专业课今日任务写入
5. 靶向组卷 (exam_composer.py) 真实 LLM 试题命制与答案生成、模板降级
6. 同源变式 (variant_retriever.py) 真实 LLM 变式题生成与防虚构水印
7. 考纲 DIFF (syllabus_diff.py) 真实 LLM 战术研判生成与模板降级
8. 切片入库 (material_ingestion.py) 真实 LLM 采分点推演与模板降级
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6", reason="未安装 PySide6，跳过 GUI 测试")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from tools.gui.widgets.onboarding_wizard import OnboardingWizard, PROVIDER_CONSOLE_URLS
from tools.gui.widgets.settings_dialog import SettingsDialog
from tools.gui.services import settings as settings_svc
from tools import study_planner
from tools.skills import exam_composer, variant_retriever, material_ingestion
from tools.intelligence import syllabus_diff
from tools import llm_client


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def temp_workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    for sub in ["01-数学", "02-英语", "03-思想政治理论", "04-专业课"]:
        (ws / sub / "_状态").mkdir(parents=True)
        (ws / sub / "参考资料").mkdir(parents=True)
    return ws


# ─────────────────────────────────────────────────────────────
# 1. 多方案研考模式 (Mode A/B/C/D) 测试
# ─────────────────────────────────────────────────────────────

def test_onboarding_wizard_mode_b_dual_pro(qapp, temp_workspace):
    """测试模式 B (不考数学 · 双自命题专业课) 控件联动与数据持久化"""
    cfg_file = temp_workspace / "ky_config.json"
    wizard = OnboardingWizard(config_path=cfg_file, workspace_root=temp_workspace)

    # 切换至模式 B
    idx_b = wizard.exam_mode_combo.findData("mode_b")
    assert idx_b >= 0
    wizard.exam_mode_combo.setCurrentIndex(idx_b)

    # 断言：专业课二控件显示，数学被禁用
    assert not wizard.pro2_row_widget.isHidden()
    assert not wizard.math_combo.isEnabled()
    assert wizard.math_combo.currentData() == "none"
    assert wizard.math_hours_spin.value() == 0.0

    # 填写专业课一与专业课二
    wizard.pro_name_edit.setText("自命题科目1")
    wizard.pro2_name_edit.setText("自命题科目2")
    wizard.pro_hours_spin.setValue(2.0)
    wizard.pro2_hours_spin.setValue(2.0)

    cfg = wizard.collect_config()
    plan = cfg["study_plan"]
    assert plan["exam_mode"] == "mode_b"
    assert plan["pro_name"] == "自命题科目1"
    assert plan["pro2_name"] == "自命题科目2"
    assert plan["math_hours"] == 0.0
    assert plan["pro2_hours"] == 2.0


def test_onboarding_wizard_mode_c_mgmt_199(qapp, temp_workspace):
    """测试模式 C (管理类联考 199: 199管综200分 + 英语二100分，初试不考政治与统考数学)"""
    cfg_file = temp_workspace / "ky_config.json"
    wizard = OnboardingWizard(config_path=cfg_file, workspace_root=temp_workspace)

    idx_c = wizard.exam_mode_combo.findData("mode_c")
    assert idx_c >= 0
    wizard.exam_mode_combo.setCurrentIndex(idx_c)

    # 断言：数学与政治均被禁用且小时归零
    assert not wizard.math_combo.isEnabled()
    assert wizard.math_hours_spin.value() == 0.0
    assert not wizard.pol_hours_spin.isEnabled()
    assert wizard.pol_hours_spin.value() == 0.0
    assert wizard.pol_target_edit.text() == "不考政治"
    assert wizard.eng_combo.currentData() == "eng2"

    cfg = wizard.collect_config()
    plan = cfg["study_plan"]
    assert plan["exam_mode"] == "mode_c"
    assert plan["math_hours"] == 0.0
    assert plan["pol_hours"] == 0.0
    assert plan["pol_disabled"] is True


# ─────────────────────────────────────────────────────────────
# 2. 上游模型探查与控制台跳转测试
# ─────────────────────────────────────────────────────────────

def test_fetch_upstream_models_mock():
    """测试通过 GET /models 探查上游可用模型"""
    mock_payload = {
        "data": [
            {"id": "deepseek-chat"},
            {"id": "deepseek-coder"},
            {"id": "deepseek-reasoner"},
        ]
    }
    raw_bytes = json.dumps(mock_payload).encode("utf-8")

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = raw_bytes
        mock_resp.headers.get.return_value = "application/json"
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        ok, models, detail = llm_client.fetch_upstream_models("sk-test", "https://api.deepseek.com/v1")
        assert ok is True
        assert "deepseek-chat" in models
        assert "deepseek-reasoner" in models


def test_provider_console_urls():
    """验证主流官方服务商控制台 URL 映射正确"""
    assert "deepseek" in PROVIDER_CONSOLE_URLS["DeepSeek (官方)"]
    assert "aliyun" in PROVIDER_CONSOLE_URLS["阿里百炼 (通义千问)"]
    assert "bigmodel" in PROVIDER_CONSOLE_URLS["智谱 AI (GLM)"]
    assert "volcengine" in PROVIDER_CONSOLE_URLS["火山方舟 (豆包)"]


# ─────────────────────────────────────────────────────────────
# 3. 今日任务 LLM 注入与模板降级测试
# ─────────────────────────────────────────────────────────────

def test_study_planner_llm_and_fallback(temp_workspace):
    """测试 study_planner 在 LLM 配置有效时生成定制任务，并在无效时降级"""
    plan = {
        "school": "目标院校",
        "major": "目标专业 (专业代码)",
        "exam_date": "2026-12-19",
        "days_left": 90,
        "stage_name": "强化题型攻坚阶段",
        "exam_mode": "mode_b",
        "math_key": "none",
        "math_name": "不考数学",
        "eng_name": "英语一",
        "pro_name": "自命题科目1",
        "pro2_name": "自命题科目2",
        "math_hours": 0.0,
        "eng_hours": 2.0,
        "pol_hours": 1.0,
        "pro_hours": 2.0,
        "pro2_hours": 2.0,
        "pro_weakness": "唯物史观与剩余价值论述题",
        "pro2_weakness": "中国式现代化理论框架",
    }

    # 1. 模拟未配置 LLM 时的模板生成
    with patch.object(study_planner, "ROOT", temp_workspace):
        with patch("tools.llm_client.is_llm_configured", return_value=False):
            study_planner.generate_plan_and_today_files(plan)

            p1_task = temp_workspace / "04-专业课" / "_状态" / "今日任务.md"
            p2_task = temp_workspace / "04-专业课" / "_状态" / "今日任务_专业课二.md"
            assert p1_task.exists()
            assert p2_task.exists()
            assert "今日专业课一任务" in p1_task.read_text(encoding="utf-8")
            assert "今日专业课二任务" in p2_task.read_text(encoding="utf-8")

    # 2. 模拟 LLM 已配置并生成定制任务
    mock_llm_output = (
        "# 今日专业课一任务 (2026-09-19)\n\n"
        "> 研考倒计时：90 天 ｜ 当前阶段：强化题型攻坚阶段 ｜ 今日目标用时：120 分钟\n\n"
        "| 模块 | 任务内容 | 预计用时 | 完成状态 |\n"
        "|---|---|---|---|\n"
        "| 概念攻坚 | 梳理唯物史观社会存在与社会意识辩证关系 | 30 分钟 | [ ] |\n"
        "| 习题精练 | 书写剩余价值论综合论述题标准步骤 | 60 分钟 | [ ] |\n"
        "| 采分订正 | 依据采分点查漏补缺 | 30 分钟 | [ ] |\n\n"
        "> **私教提示**：输入 /pro 即可提交论述作答！"
    )
    with patch.object(study_planner, "ROOT", temp_workspace):
        with patch("tools.llm_client.is_llm_configured", return_value=True):
            with patch("tools.llm_client.chat_completion", return_value=mock_llm_output):
                study_planner.generate_plan_and_today_files(plan)
                p1_content = (temp_workspace / "04-专业课" / "_状态" / "今日任务.md").read_text(encoding="utf-8")
                assert "梳理唯物史观社会存在与社会意识辩证关系" in p1_content


# ─────────────────────────────────────────────────────────────
# 4. 靶向组卷 (exam_composer.py) LLM 试题命制测试
# ─────────────────────────────────────────────────────────────

def test_exam_composer_llm_question_generation(temp_workspace):
    """测试靶向组卷在错题不足时调用 LLM 命制规范真题"""
    mock_resp = json.dumps({
        "title": "马原核心概念攻坚自测",
        "question": "试结合唯物辩证法矛盾普遍性与特殊性辩证关系原理，论述坚持具体问题具体分析对推动高质量发展的现实指导意义。",
        "standard_answer": "1. 阐明矛盾普遍性与特殊性的辩证统一关系；2. 结合高质量发展实际展开论述。",
        "score": 15
    })

    with patch.object(exam_composer, "ROOT", temp_workspace):
        with patch("tools.llm_client.is_llm_configured", return_value=True):
            with patch("tools.llm_client.chat_completion", return_value=mock_resp):
                q = exam_composer._generate_synthetic_question_llm("pro", "专业课", "矛盾辩证法", "缺乏论述框架")
                assert q["is_synthetic"] is True
                assert "矛盾普遍性与特殊性" in q["question"]
                assert q["score"] == "15"
                assert "阐明矛盾普遍性" in q["standard_answer"]


# ─────────────────────────────────────────────────────────────
# 5. 同源变式 (variant_retriever.py) LLM 变式题生成测试
# ─────────────────────────────────────────────────────────────

def test_variant_retriever_llm_variant(temp_workspace):
    """测试变式题检索在本地未命中时调用 LLM 动态命制并附带防虚构水印"""
    mock_variant_text = (
        "已知矩阵 A 满足 A^2 - 2A - 3E = 0，求 A 的特征值集合，并证明 A 可对角化。"
    )
    with patch.object(variant_retriever, "ROOT", temp_workspace):
        with patch("tools.llm_client.is_llm_configured", return_value=True):
            with patch("tools.llm_client.chat_completion", return_value=mock_variant_text):
                res = variant_retriever._generate_synthetic_variant("math", "矩阵对角化")
                assert len(res) == 1
                q = res[0]["question"]
                assert "【⚠️ 私教自拟变式 · 题源未挂载本地实体资料】" in q
                assert "A^2 - 2A - 3E = 0" in q


# ─────────────────────────────────────────────────────────────
# 6. 考纲 DIFF (syllabus_diff.py) LLM 战术建议测试
# ─────────────────────────────────────────────────────────────

def test_syllabus_diff_llm_advice():
    """测试考纲比对在有变动时调用 LLM 生成专属战术研报"""
    generator = syllabus_diff.SyllabusDiffGenerator()
    report_data = {
        "school": "目标院校",
        "major": "目标专业 (专业代码)",
        "year_old": 2026,
        "year_new": 2027,
        "items": [],
        "added": [],
        "removed": [],
        "modified": [],
        "volatility": 0.05,
        "summary": "微调",
    }

    mock_llm_advice = (
        "1. **新增考点攻坚**：针对中国式现代化五大特征开展专题背诵；\n"
        "2. **题型防范**：警惕论述题结合最新重大方针。"
    )
    with patch("tools.llm_client.is_llm_configured", return_value=True):
        with patch("tools.llm_client.chat_completion", return_value=mock_llm_advice):
            md = generator.format_diff_markdown(report_data)
            assert "针对中国式现代化五大特征开展专题背诵" in md


# ─────────────────────────────────────────────────────────────
# 7. 切片入库 (material_ingestion.py) LLM 采分点推演测试
# ─────────────────────────────────────────────────────────────

def test_material_ingestion_llm_rubric(temp_workspace):
    """测试资料切片入库缺失步骤分时调用 LLM 推演采分点与考点"""
    pipeline = material_ingestion.MaterialIngestionPipeline(workspace_root=temp_workspace)
    chunk = material_ingestion.QuestionChunk(
        number=1,
        q_type="essay",
        score=10,
        stem="请简述马克思主义劳动价值论的核心内容及其现实意义。",
        answer="劳动二重性决定商品二重性，具体劳动创造使用价值，抽象劳动形成价值。",
        analysis="",
        rubric=[],
        points=[],
    )

    mock_llm_resp = json.dumps({
        "rubric": [
            "[+4分] 阐述劳动二重性与商品二重性的内在逻辑",
            "[+4分] 说明具体劳动与抽象劳动的作用与区别",
            "[+2分] 总结劳动价值论对现代经济发展的指导意义"
        ],
        "points": ["劳动二重性", "价值形成过程"],
        "analysis": "考查马克思主义政治经济学劳动价值论核心基石。"
    })

    with patch("tools.llm_client.is_llm_configured", return_value=True):
        with patch("tools.llm_client.chat_completion", return_value=mock_llm_resp):
            card_md = pipeline.format_question_card(chunk, subject="pro")
            assert "劳动二重性与商品二重性的内在逻辑" in card_md
            assert "劳动二重性" in card_md
            assert "考查马克思主义政治经济学" in card_md


# ─────────────────────────────────────────────────────────────
# 8. HTTP 400 容错重试与 GZIP 解压缩测试
# ─────────────────────────────────────────────────────────────

def test_connectivity_400_retry_and_gzip(monkeypatch):
    """测试当反代在首个请求返回 400 (带 GZIP 压缩) 时，自动解压并使用精简载荷重试成功"""
    import gzip
    import urllib.request
    import urllib.error

    calls = []

    def mock_urlopen(req, timeout=10.0):
        calls.append(req)
        if len(calls) == 1:
            # 模拟首个请求因包含非标参数被返回 400，且中转站用 GZIP 压缩了报错响应
            err_json = json.dumps({"error": {"message": "Invalid parameter: max_tokens not supported"}}).encode("utf-8")
            compressed_err = gzip.compress(err_json)
            headers = {"Content-Encoding": "gzip"}
            from io import BytesIO
            fp = BytesIO(compressed_err)
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", headers, fp)
        else:
            # 第二次重试使用精简 payload，成功返回
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"choices": [{"message": {"content": "pong"}}]}'
            mock_resp.headers = {"Content-Encoding": "identity"}
            mock_resp.status = 200
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    res = settings_svc.test_api_connectivity(
        api_key="sk-test-key",
        base_url="https://kuaipao.ai/v1",
        model="deepseek-chat",
        search_provider="bing",
        timeout=5.0,
    )
    assert res["llm_ok"] is True
    assert res["llm_status"] == "ok"
    assert len(calls) == 2
    # 验证第二次重试载荷中确实移除了 max_tokens
    retry_payload = json.loads(calls[1].data.decode("utf-8"))
    assert "max_tokens" not in retry_payload


def test_onboarding_wizard_mgmt_science(qapp, temp_workspace):
    """测试专业课类别选择管科综合 (mgmt_sci) 并在自定义时正常保存"""
    cfg_file = temp_workspace / "ky_config.json"
    wizard = OnboardingWizard(config_path=cfg_file, workspace_root=temp_workspace)

    idx_mgmt = wizard.pro_type_combo.findData("mgmt_sci")
    assert idx_mgmt >= 0
    wizard.pro_type_combo.setCurrentIndex(idx_mgmt)
    wizard.pro_name_edit.setText("842 管理科学基础")

    cfg = wizard.collect_config()
    assert cfg["study_plan"]["pro_type"] == "mgmt_sci"
    assert cfg["study_plan"]["pro_name"] == "842 管理科学基础"

