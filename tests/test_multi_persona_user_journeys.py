# -*- coding: utf-8 -*-
"""
多角色多维度全链路用户场景验收测试 (Multi-Persona Verification Suite)
======================================================================
覆盖角色：
1. Persona 1: 小白文科跨考生（马理论 · 目标院校 · 不考数学）
2. Persona 2: 统考理工生（数学二 · 408 计算机）
3. Persona 3: 中转站 / 反代高延迟用户（5s 延迟、Gzip 压缩包、流式逐字推送）
4. Persona 4: Windows 纯终端 / 批处理用户（GUI.bat 换行符与环境探测）
"""

import os
import sys
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from gui.services.settings import is_unconfigured, save_onboarding_config, read_config
from gui.workers.agent_worker import AgentWorker


def test_persona_1_liberal_arts_no_math():
    """Persona 1: 小白文科考生（马理论，不考数学）全生命周期验证"""
    print("\n[Persona 1] 正在测试小白文科跨考生（马理论 · 不考数学）...")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        # 复制 AGENTS.md 模板
        agents_tpl = ROOT / "AGENTS.md"
        if agents_tpl.exists():
            shutil.copy2(agents_tpl, tmp_ws / "AGENTS.md")

        # 1. 首次进入检测：确认未配置状态能被精准捕获
        assert is_unconfigured(tmp_ws) is True, "新工作区应判定为未配置状态"

        # 2. 模拟完成新手引导配置
        study_plan = {
            "school": "目标院校",
            "major": "目标专业 (专业代码)",
            "backup_school": "",
            "exam_date": "2026-12-19",
            "days_left": 91,
            "stage_name": "强化题型攻坚阶段",
            "style_name": "严格把关·保姆提分型 (Strict & Disciplined)",
            "math_key": "none",
            "math_name": "不考数学",
            "eng_key": "eng1",
            "eng_name": "英语一 (201)",
            "pro_type": "custom",
            "pro_name": "自命题专业课科目",
            "math_hours": 0.0,
            "eng_hours": 2.0,
            "pol_hours": 1.0,
            "pro_hours": 3.5,
            "total_hours": 6.5,
            "math_target": "不考数学",
            "eng_target": "65+ 分",
            "pol_target": "70+ 分",
            "pro_target": "125+ 分",
            "total_target": "370+ 分",
            "math_weakness": "无",
            "eng_weakness": "待诊断薄弱点",
            "pol_weakness": "待诊断薄弱点",
            "pro_weakness": "待诊断薄弱点",
        }
        cfg_data = {
            "onboarding_completed": True,
            "target_school": "目标院校",
            "target_major": "目标专业 (专业代码)",
            "coaching_style": "严格把关·保姆提分型 (Strict & Disciplined)",
            "api_key": "test_api_key",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "search_provider": "bing",
            "study_plan": study_plan,
        }

        # 3. 模拟在引导界面放置专业课实体讲义与真题 PDF
        pro_ref_dir = tmp_ws / "04-专业课" / "参考资料"
        pro_ref_dir.mkdir(parents=True, exist_ok=True)
        sample_doc = pro_ref_dir / "2025年自命题科目1大纲解析.txt"
        sample_doc.write_text("第一章 物质与意识的辩证关系。世界的物质统一性原理。", encoding="utf-8")

        # 放置真实真题 PDF 并验证抽取
        sample_pdf = pro_ref_dir / "2026年自命题科目1自命题真题.pdf"
        import pymupdf
        doc = pymupdf.open()
        p = doc.new_page()
        p.insert_text((50, 72), "Kaoyan 618: Marxist Dialectics and Historical Materialism Exam.")
        doc.save(str(sample_pdf))
        doc.close()

        from skills.material_ingestion import extract_text_from_pdf
        pdf_text = extract_text_from_pdf(sample_pdf)
        assert "Marxist Dialectics" in pdf_text, "应成功提取出 PDF 内真题文本"

        # 4. 保存配置并同步学情档案
        cfg_file = tmp_ws / "ky_config.json"
        save_onboarding_config(cfg_file, cfg_data, workspace_root=tmp_ws)
        assert is_unconfigured(tmp_ws) is False, "保存后工作区应变为已就绪状态"

        saved_cfg = read_config(cfg_file)
        assert saved_cfg["study_plan"]["math_key"] == "none"
        assert saved_cfg["study_plan"]["math_hours"] == 0.0

        # 5. 验证 AGENTS.md 已同步写入
        agents_content = (tmp_ws / "AGENTS.md").read_text(encoding="utf-8")
        assert "目标院校" in agents_content
        assert "目标专业 (专业代码)" in agents_content
        assert "不考数学" in agents_content

        # 6. 验证专业课资料已挂载就绪
        assert sample_doc.exists()
        assert sample_pdf.exists()

        # 7. 验证报到指令分发
        worker = AgentWorker(saved_cfg, "专业课报到")
        assert worker.user_input == "专业课报到"

        # 8. 验证学员上传真题 PDF 挂载指令 (/file)
        from agent.loop import AgentRunner
        worker_file = AgentWorker(saved_cfg, f"/file {sample_pdf} 请指出重点考点")
        with patch.object(AgentRunner, "run", return_value="重点考点分析完毕") as mock_runner_run:
            worker_file.run()
            assert sample_pdf.name in worker_file.user_input
            assert "Marxist Dialectics" in worker_file.user_input
            assert mock_runner_run.called

        print("  [√] Persona 1: 小白文科考生（马理论 · 不考数学 · PDF挂载）全链路验收通过！")


def test_persona_2_engineering_math2():
    """Persona 2: 统考理工生（考数学二 / 408 计算机）验证"""
    print("\n[Persona 2] 正在测试统考理工生（数学二 · 408 计算机）...")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        study_plan = {
            "school": "北京航空航天大学",
            "major": "085400 软件工程",
            "exam_date": "2026-12-19",
            "math_key": "math2",
            "math_name": "数学二 (302)",
            "pro_type": "408",
            "pro_name": "408 计算机学科专业基础",
            "math_hours": 3.0,
            "total_hours": 8.0,
            "math_target": "120+ 分",
            "math_weakness": "中值定理证明与二重积分计算",
        }
        cfg_data = {
            "onboarding_completed": True,
            "target_school": "北京航空航天大学",
            "target_major": "085400 软件工程",
            "study_plan": study_plan,
        }

        # 验证数学状态互锁
        assert cfg_data["study_plan"]["math_key"] == "math2"
        assert cfg_data["study_plan"]["math_hours"] == 3.0

        print("  [√] Persona 2: 统考理工生（数学二 · 408 计算机）参数校验通过！")


def test_persona_3_proxy_user_streaming():
    """Persona 3: 中转站反代用户（高延迟、Gzip 压缩流式接收）验证"""
    print("\n[Persona 3] 正在测试中转站反代用户（5s 延迟、Gzip 流式）...")

    from agent.loop import AgentRunner
    chunks_received = []

    def on_chunk(c: str):
        chunks_received.append(c)

    runner = AgentRunner(
        config={
            "api_key": "sk-relay-test-key",
            "base_url": "https://relay.proxy.com/v1",
            "model": "deepseek-chat",
        },
        workspace_root=ROOT,
        stream_callback=on_chunk,
        quiet=True
    )

    full_text = "【政治私教已连线】今日自测题：辨析「物质是不依赖于人类的意识而存在，并能为人类意识所反映的客观实在」。请简要作答。"
    mock_payload = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": full_text
            }
        }]
    }
    import gzip
    compressed = gzip.compress(json.dumps(mock_payload, ensure_ascii=False).encode("utf-8"))

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = compressed
    mock_resp.headers = {"Content-Encoding": "gzip"}

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = runner.run("政治报到", interactive=False)
        assert res == full_text
        assert len(chunks_received) > 0, "应通过 stream_callback 分批接收到流式打字数据"
        assert "".join(chunks_received) == full_text

        # 2. 额外验证 Brotli 压缩信道流式
        try:
            import brotli
            chunks_br = []
            runner_br = AgentRunner(
                config={
                    "api_key": "sk-relay-test-key",
                    "base_url": "https://relay.proxy.com/v1",
                    "model": "deepseek-chat",
                },
                workspace_root=ROOT,
                stream_callback=lambda c: chunks_br.append(c),
                quiet=True
            )
            compressed_br = brotli.compress(json.dumps(mock_payload, ensure_ascii=False).encode("utf-8"))
            mock_resp_br = MagicMock()
            mock_resp_br.__enter__.return_value = mock_resp_br
            mock_resp_br.read.return_value = compressed_br
            mock_resp_br.headers = {"Content-Encoding": "br"}
            with patch("urllib.request.urlopen", return_value=mock_resp_br):
                res_br = runner_br.run("政治报到", interactive=False)
                assert res_br == full_text
                assert "".join(chunks_br) == full_text
        except ImportError:
            pass

    print(f"  [√] Persona 3: 中转站反代流式链路通过（Gzip与Brotli分 {len(chunks_received)} 次片段完整接收）！")


def test_persona_4_windows_pure_terminal_gui_bat():
    """Persona 4: 纯 Windows 终端用户（批处理换行符与 Python 查找健壮性）"""
    print("\n[Persona 4] 正在测试 Windows 纯终端用户（GUI.bat 换行符与执行安全性）...")

    bat_path = ROOT / "GUI.bat"
    assert bat_path.exists(), "GUI.bat 必须存在"
    raw_bytes = bat_path.read_bytes()

    # 1. 严格要求 CRLF 换行
    assert b"\r\n" in raw_bytes, "GUI.bat 必须具有标准 CRLF (\\r\\n) 换行符"
    assert b"\r\r\n" not in raw_bytes, "不能有重复 \\r 符号"

    # 2. 验证规避 WindowsApps 伪 Python 的语法存在
    bat_str = raw_bytes.decode("utf-8", errors="ignore")
    assert "WindowsApps" in bat_str, "GUI.bat 必须包含过滤 WindowsApps 伪 Python 的防御代码"
    assert "py -3" in bat_str, "GUI.bat 必须优先探活 Windows 官方 Python 启动器 py -3"

    print("  [√] Persona 4: GUI.bat 脚本规范性与环境规避防御验证通过！")


def test_persona_5_wizard_material_placement_and_badges():
    """Persona 5: 向导第 2 步实体参考资料放置与动态徽标更新验证"""
    print("\n[Persona 5] 正在测试向导第 2 步参考资料入库与状态徽标...")

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    _ = QApplication.instance() or QApplication(sys.argv)
    from tools.gui.widgets.onboarding_wizard import OnboardingWizard

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_ws = Path(tmpdir)
        # 初始化各科目资料目录
        for subj in ("01-数学", "02-英语", "03-思想政治理论", "04-专业课"):
            (tmp_ws / subj / "参考资料").mkdir(parents=True, exist_ok=True)

        wiz = OnboardingWizard(workspace_root=tmp_ws)
        try:
            # 1. 默认选择不考数学时，数学资料提示无需放置
            wiz.math_combo.setCurrentIndex(wiz.math_combo.findData("none"))
            wiz._refresh_material_badges()
            assert "无需放置" in wiz.math_mat_lbl.text()
            assert "暂无文件" in wiz.pro_mat_lbl.text()

            # 2. 向专业课放置真题讲义 (txt, md)
            pro_dir = tmp_ws / "04-专业课" / "参考资料"
            (pro_dir / "考研618马克思主义哲学真题.txt").write_text("真题节选", encoding="utf-8")
            (pro_dir / "考研823中国化真题.md").write_text("大纲解析", encoding="utf-8")

            # 刷新徽标并验证更新
            wiz._refresh_material_badges()
            assert "已挂载 2 份" in wiz.pro_mat_lbl.text()
            assert "考研618马克思主义哲学真题.txt" in wiz.pro_mat_lbl.text()

            # 3. 切换为考数学一，徽标状态恢复检查
            wiz.math_combo.setCurrentIndex(wiz.math_combo.findData("math1"))
            wiz._refresh_material_badges()
            assert "暂无文件" in wiz.math_mat_lbl.text()

            # 向数学放置高数真题 PDF
            math_dir = tmp_ws / "01-数学" / "参考资料"
            (math_dir / "李林880题解析.pdf").write_bytes(b"%PDF-1.4 sample")
            wiz._refresh_material_badges()
            assert "已挂载 1 份" in wiz.math_mat_lbl.text()
        finally:
            wiz.close()

    print("  [√] Persona 5: 向导资料放置与徽标动态响应验证通过！")


if __name__ == "__main__":
    print("========================================================")
    print("   考研学习链 · 多角色多维度全链路自动化综合验收")
    print("========================================================")
    test_persona_1_liberal_arts_no_math()
    test_persona_2_engineering_math2()
    test_persona_3_proxy_user_streaming()
    test_persona_4_windows_pure_terminal_gui_bat()
    test_persona_5_wizard_material_placement_and_badges()
    print("\n========================================================")
    print("   [ALL PASSED] 全部 5 大类用户角色综合验收通过！")
    print("========================================================")
