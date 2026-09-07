# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · 新增功能专项自动化测试套件
全面覆盖：
  1. 微信公众号经验贴检索与 Markdown 清洗管道 (wechat_searcher)
  2. 院校档案联动 (school_scout.append_experience_to_dossier)
  3. Rust PyO3 扩展模块功能校验 (ky_rust_ext)
  4. Rust 与纯 Python 回退的一致性校验 (chunk_text, sha256_hash, estimate_tokens, extract_title)
  5. 核心模块透明回退机制校验 (material_ingestion, watcher, context_engine, extractor)
  6. PySide6 GUI 离屏实例化与组件测试 (MainWindow, WeChatSearchDialog, QSS 主题)
  7. CLI 与 TUI 入口路由测试 (ky gui, ky wechat, tui_navigator)
"""

import sys
import os
import json
import hashlib
import tempfile
import shutil
import subprocess
from pathlib import Path

# Windows UTF-8 控制台兼容
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


class TestRunner:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.errors = []

    def assert_true(self, condition, test_name):
        if condition:
            print(f"  [PASS] {test_name}")
            self.passed += 1
        else:
            print(f"  [FAIL] {test_name}")
            self.failed += 1
            self.errors.append(test_name)

    def skip(self, test_name, reason=""):
        msg = f"  [SKIP] {test_name}"
        if reason:
            msg += f" ({reason})"
        print(msg)
        self.skipped += 1

    def print_summary(self):
        print("\n" + "=" * 60)
        parts = [f"通过 {self.passed} 项", f"失败 {self.failed} 项"]
        if self.skipped > 0:
            parts.append(f"跳过 {self.skipped} 项")
        print(f" 新功能测试统计: {', '.join(parts)}")
        if self.failed == 0:
            print(" 🎉 全部新功能测试项 100% 通过！升级模块稳健可靠！")
        else:
            print(f" ❌ 以下测试未通过: {', '.join(self.errors)}")
        print("=" * 60)
        return self.failed == 0


def run_new_feature_tests():
    runner = TestRunner()
    print("============================================================")
    print(" 🧪 开始对 考研学习链 新增功能 进行严格自动化测试")
    print("============================================================\n")

    # ============================================================
    # 测试组 A: 微信公众号经验贴检索与解析
    # ============================================================
    print("[测试组 A: 微信公众号检索与清洗管道]")
    try:
        from tools.skills.wechat_searcher import (
            WeChatArticleFetcher,
            WeChatSearchEngine,
            SearchResult,
            search_wechat_experiences,
        )

        # A.1 HTML to Markdown 清洗功能
        sample_html = """
        <html>
        <head><title>【经验贴】华中科技大学计算机834考研400分一战上岸秘籍</title></head>
        <body>
            <script>var x = 1;</script>
            <style>.hide { display: none; }</style>
            <div id="img-content">
                <h1 class="rich_media_title" id="activity-name">华中科技大学计算机834考研400分一战上岸秘籍</h1>
                <div class="rich_media_meta_list">
                    <a class="rich_media_meta_nickname" id="js_name">CS考研圈</a>
                </div>
                <div class="rich_media_content" id="js_content">
                    <p>大家好，我是2024届华科计算机学长。</p>
                    <p>数学二备考心得：</p>
                    <p>第一阶段全书必须吃透，第二阶段做李林880与历年真题。</p>
                    <p>专业课834备考重点：数据结构严抓代码手写，操作系统关注PV操作大题。</p>
                </div>
            </div>
        </body>
        </html>
        """
        fetcher = WeChatArticleFetcher()
        test_item = SearchResult(title="", url="https://mp.weixin.qq.com/s/test")
        fetcher._extract_metadata(sample_html, test_item)
        markdown_text = fetcher._html_to_markdown(sample_html)
        test_item.content_markdown = markdown_text

        runner.assert_true("400分一战上岸秘籍" in test_item.title, "WeChatArticleFetcher: 正确提取文章标题")
        runner.assert_true("CS考研圈" in test_item.source_account, "WeChatArticleFetcher: 正确提取公众号名称")
        runner.assert_true("大家好，我是2024届华科计算机学长" in markdown_text, "WeChatArticleFetcher: 正确清洗提取正文内容")
        runner.assert_true("<script>" not in markdown_text, "WeChatArticleFetcher: 成功剥离 script 标签")
        runner.assert_true("<style>" not in markdown_text, "WeChatArticleFetcher: 成功剥离 style 标签")
        runner.assert_true(len(markdown_text) > 50, "WeChatArticleFetcher: Markdown 文本长度充足")

        # A.2 文章沉淀到 Markdown 文件
        from tools.skills.wechat_searcher import WeChatContentPipeline
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = WeChatContentPipeline()
            pipeline.EXPERIENCES_DIR = Path(tmpdir) / "experiences"

            saved_path = pipeline.save_article(test_item, category="计算机考研经验")
            runner.assert_true(Path(saved_path).exists(), "WeChatContentPipeline: 经验贴成功落地为 Markdown 文件")
            content = Path(saved_path).read_text(encoding="utf-8")
            runner.assert_true("华中科技大学" in content and "CS考研圈" in content, "WeChatContentPipeline: 保存内容包含标题与公众号名")

        # A.3 搜索本地降级与备用结果
        engine = WeChatSearchEngine()
        fallback_results = engine.search_local_cache("计算机", 3)
        runner.assert_true(len(fallback_results) > 0, "WeChatSearchEngine: 本地降级搜索命中备用经验库数据")
        runner.assert_true(isinstance(fallback_results[0], SearchResult), "WeChatSearchEngine: 搜索结果类型正确")

        # A.4 院校档案联动 (school_scout)
        from tools.skills.school_scout import append_experience_to_dossier
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dossier = Path(tmpdir) / "test_dossier.md"
            test_dossier.write_text("# 华中科技大学 计算机 招考研报\n\n## 招生计划\n- 计划招收100人\n", encoding="utf-8")
            
            exp_info = {
                "title": "华科计算机高分上岸经验",
                "source": "CS考研圈",
                "url": "https://mp.weixin.qq.com/s/sample",
                "snippet": "复试注重上机与项目细节...",
            }
            append_experience_to_dossier("华中科技大学", "计算机", exp_info, dossier_path=test_dossier)
            updated_text = test_dossier.read_text(encoding="utf-8")
            runner.assert_true("### 📱 [微信公众号]" in updated_text, "school_scout: 成功挂载公众号经验条目")
            runner.assert_true("华科计算机高分上岸经验" in updated_text, "school_scout: 经验贴标题正确写入研报")

    except Exception as e:
        runner.assert_true(False, f"测试组 A 异常: {e}")

    # ============================================================
    # 测试组 B: Rust PyO3 扩展模块与双模一致性
    # ============================================================
    print("\n[测试组 B: Rust 原生扩展与 Python 回退一致性校验]")
    _HAS_RUST = False
    try:
        import ky_rust_ext as rust_mod
        _HAS_RUST = True
    except ImportError:
        pass

    if not _HAS_RUST:
        # Rust 扩展未安装 (CI 环境) — 跳过 Rust 对比测试，仅验证纯 Python 回退
        rust_skip_names = [
            "ky_rust_ext 模块成功加载",
            "Rust vs Python: sha256_hash 结果 100% 精确一致",
            "Rust chunk_text: 成功切片出 2 道题目",
            "Rust vs Python: 切片题目数量一致",
            "material_ingestion.chunk_text 路由 Rust 加速输出题目数量正确",
            "Rust vs Python: 第1题题干与答案解析完全一致",
            "Rust vs Python: 第2题题干与答案解析完全一致",
            "Rust vs Python: Token 估算结果 100% 精确一致",
            "Rust vs Python: extract_title 提取结果一致",
            "extractor: 正确抽取科目信息",
        ]
        for name in rust_skip_names:
            runner.skip(name, "ky_rust_ext 未安装")
        # B.5 纯 Python 回退仍可独立验证
        try:
            from tools.skills.material_ingestion import _chunk_text_python, chunk_text
            from tools.skills import material_ingestion
            sample_q = "1. 题目A\n【答案】A\n【解析】解析A\n\n2. 题目B\n【答案】B\n【解析】解析B\n"
            orig_has_rust = material_ingestion._HAS_RUST_EXT
            try:
                material_ingestion._HAS_RUST_EXT = False
                fallback_res = material_ingestion.chunk_text(sample_q, "测试真题")
                runner.assert_true(len(fallback_res) == 2, "material_ingestion: 模拟 _HAS_RUST_EXT=False 时无缝回退纯 Python")
            finally:
                material_ingestion._HAS_RUST_EXT = orig_has_rust
        except Exception as e:
            runner.assert_true(False, f"测试组 B (纯 Python 回退) 异常: {e}")
    else:
        # Rust 扩展已安装 — 执行完整双模一致性校验
        try:
            runner.assert_true(rust_mod is not None, "ky_rust_ext 模块成功加载")

            # B.1 SHA-256 哈希校验
            test_strings = [
                "hello world",
                "考研数学二高等数学导数中值定理",
                "408 计算机学科专业基础综合 数据结构 算法导论",
                "",
                "A" * 10000,
            ]
            all_hash_match = True
            for s in test_strings:
                py_hash = hashlib.sha256(s.encode("utf-8")).hexdigest()
                rust_hash = rust_mod.sha256_hash(s)
                if py_hash != rust_hash:
                    all_hash_match = False
                    break
            runner.assert_true(all_hash_match, "Rust vs Python: sha256_hash 结果 100% 精确一致")

            # B.2 题目智能切片 (chunk_text) 一致性校验
            from tools.skills.material_ingestion import _chunk_text_python, chunk_text
            sample_questions = """一、单项选择题
1. 函数 f(x) = |x| 在 x=0 处：
A. 连续不可导
B. 不连续
C. 可导
D. 极限不存在
【答案】A
【解析】左导数为-1，右导数为1，故不可导。

2. 设矩阵 A 为 3 阶方阵，若 |A| = 2，则 |2A| 为：
A. 4
B. 8
C. 16
D. 2
【答案】C
【解析】|2A| = 2^3 * |A| = 8 * 2 = 16。
"""
            py_chunks = _chunk_text_python(sample_questions, "2024真题")
            rust_chunks = rust_mod.chunk_text(sample_questions, "2024真题")
            unified_chunks = chunk_text(sample_questions, "2024真题")

            runner.assert_true(len(rust_chunks) == 2, f"Rust chunk_text: 成功切片出 2 道题目 (实际: {len(rust_chunks)})")
            runner.assert_true(len(py_chunks) == len(rust_chunks), f"Rust vs Python: 切片题目数量一致 ({len(py_chunks)} vs {len(rust_chunks)})")
            runner.assert_true(len(unified_chunks) == len(rust_chunks), "material_ingestion.chunk_text 路由 Rust 加速输出题目数量正确")
            runner.assert_true(
                py_chunks[0].stem == rust_chunks[0]["stem"] and py_chunks[0].answer == rust_chunks[0]["answer"],
                "Rust vs Python: 第1题题干与答案解析完全一致"
            )
            runner.assert_true(
                py_chunks[1].stem == rust_chunks[1]["stem"] and py_chunks[1].answer == rust_chunks[1]["answer"],
                "Rust vs Python: 第2题题干与答案解析完全一致"
            )

            # B.3 Token 估算一致性校验
            from tools.agent.context_engine import ContextEngine
            engine = ContextEngine(workspace_root=ROOT)
            message_cases = [
                [{"role": "user", "content": "Hello world"}],
                [{"role": "user", "content": "考研数学二强化冲刺"}, {"role": "assistant", "content": "好的，今天我们攻克中值定理。"}],
                [{"role": "user", "content": "def foo(x): return x * 2\n# 注释内容"}],
                [{"role": "user", "content": "408真题切片：计算机网络TCP三次握手与四次挥手协议细节分析", "tool_calls": [{"name": "search", "args": {}}]}],
            ]
            all_tokens_match = True
            for msgs in message_cases:
                rust_tokens = rust_mod.estimate_tokens(msgs)
                engine._force_python = True
                py_tokens = engine.estimate_tokens(msgs)
                engine._force_python = False
                unified_tokens = engine.estimate_tokens(msgs)
                if py_tokens != rust_tokens or unified_tokens != rust_tokens:
                    all_tokens_match = False
                    break
            runner.assert_true(all_tokens_match, "Rust vs Python: Token 估算结果 100% 精确一致")

            # B.4 标题与科目提取
            from tools.intelligence.extractor import _extract_title, _extract_subjects
            html_sample = "<html><head><title>武汉大学2025年硕士研究生招生简章与专业目录</title></head><body>数学二、408计算机</body></html>"
            rust_title = rust_mod.extract_title(html_sample)
            py_title = _extract_title(html_sample)
            runner.assert_true(rust_title == py_title and "武汉大学" in rust_title, "Rust vs Python: extract_title 提取结果一致")

            extracted_subjs = _extract_subjects(html_sample)
            runner.assert_true("数学二" in extracted_subjs or "408" in extracted_subjs, "extractor: 正确抽取科目信息")

            # B.5 核心模块在无 Rust 时的透明回退验证
            from tools.skills import material_ingestion
            orig_has_rust = material_ingestion._HAS_RUST_EXT
            try:
                material_ingestion._HAS_RUST_EXT = False
                fallback_res = material_ingestion.chunk_text(sample_questions, "测试真题")
                runner.assert_true(len(fallback_res) == 2, "material_ingestion: 模拟 _HAS_RUST_EXT=False 时无缝回退纯 Python")
            finally:
                material_ingestion._HAS_RUST_EXT = orig_has_rust

        except Exception as e:
            runner.assert_true(False, f"测试组 B 异常: {e}")

    # ============================================================
    # 测试组 C: PySide6 GUI 桌面组件与离屏启动
    # ============================================================
    print("\n[测试组 C: PySide6 GUI 组件与离屏测试]")
    _HAS_PYSIDE6 = False
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication  # noqa: F811
        _HAS_PYSIDE6 = True
    except Exception:
        # ImportError (包未安装) 或 OSError (libEGL.so.1 等系统库缺失) 均视为不可用
        pass

    if not _HAS_PYSIDE6:
        # PySide6 未安装 (CI 环境) — 跳过 GUI 实例化测试，仅验证 QSS 文件完整性
        gui_skip_names = [
            "GUI: MainWindow 成功实例化",
            "GUI: MainWindow 窗口标题正确",
            "GUI: Tab 分页完备",
            "GUI: 10个功能卡片按钮已全部注册",
            "GUI: WeChatSearchDialog 成功实例化",
            "GUI: 微信搜索对话框控件完整",
        ]
        for name in gui_skip_names:
            runner.skip(name, "PySide6 未安装")
        # QSS 样式表完整性可独立验证（纯文件读取）
        try:
            dark_qss_path = ROOT / "tools" / "gui" / "theme" / "dark.qss"
            light_qss_path = ROOT / "tools" / "gui" / "theme" / "light.qss"
            if dark_qss_path.exists() and light_qss_path.exists():
                dark_qss = dark_qss_path.read_text(encoding="utf-8")
                light_qss = light_qss_path.read_text(encoding="utf-8")
                runner.assert_true(len(dark_qss) > 200 and "QMainWindow" in dark_qss, "GUI: 暗黑主题 dark.qss 规则定义完整")
                runner.assert_true(len(light_qss) > 200 and "QMainWindow" in light_qss, "GUI: 明亮主题 light.qss 规则定义完整")
            else:
                runner.skip("GUI: QSS 样式表完整性", "QSS 文件不存在")
        except Exception as e:
            runner.assert_true(False, f"测试组 C (QSS 校验) 异常: {e}")
    else:
        try:
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            from PySide6.QtWidgets import QApplication
            from tools.gui.main_window import MainWindow
            from tools.gui.widgets.wechat_search_dialog import WeChatSearchDialog

            app = QApplication.instance()
            if app is None:
                app = QApplication(sys.argv)

            # C.1 主窗口实例化
            win = MainWindow()
            runner.assert_true(win is not None, "GUI: MainWindow 成功实例化")
            runner.assert_true("考研学习链" in win.windowTitle(), "GUI: MainWindow 窗口标题正确")
            runner.assert_true(win.tab_widget.count() == 4, f"GUI: Tab 分页完备 (共 {win.tab_widget.count()} 个Tab)")
            runner.assert_true(len(win._feature_buttons) == 10, f"GUI: 10个功能卡片按钮已全部注册 (实际: {len(win._feature_buttons)})")

            # C.2 微信搜索对话框实例化
            dialog = WeChatSearchDialog(parent=win)
            runner.assert_true(dialog is not None, "GUI: WeChatSearchDialog 成功实例化")
            runner.assert_true(dialog.school_input is not None and dialog.keyword_input is not None, "GUI: 微信搜索对话框控件完整")

            # C.3 QSS 样式表完整性
            dark_qss = (ROOT / "tools" / "gui" / "theme" / "dark.qss").read_text(encoding="utf-8")
            light_qss = (ROOT / "tools" / "gui" / "theme" / "light.qss").read_text(encoding="utf-8")
            runner.assert_true(len(dark_qss) > 200 and "QMainWindow" in dark_qss, "GUI: 暗黑主题 dark.qss 规则定义完整")
            runner.assert_true(len(light_qss) > 200 and "QMainWindow" in light_qss, "GUI: 明亮主题 light.qss 规则定义完整")

            # 销毁窗口释放资源
            win.close()
            dialog.close()

        except Exception as e:
            runner.assert_true(False, f"测试组 C 异常: {e}")

    # ============================================================
    # 测试组 D: CLI 与 TUI 路由验证
    # ============================================================
    print("\n[测试组 D: CLI 与 TUI 路由验证]")
    cli_path = ROOT / "tools" / "ky_cli.py"

    def run_cli(*args):
        return subprocess.run(
            [sys.executable, str(cli_path), *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )

    try:
        # D.1 ky gui --help
        gui_help = run_cli("gui", "--help")
        runner.assert_true(gui_help.returncode == 0 and "桌面可视化图形界面" in gui_help.stdout, "CLI: ky gui --help 正确输出帮助信息")

        # D.2 ky wechat --help 与别名 wx
        wx_help = run_cli("wechat", "--help")
        runner.assert_true(wx_help.returncode == 0 and "检索微信公众号" in wx_help.stdout, "CLI: ky wechat --help 正确输出帮助信息")
        alias_help = run_cli("wx", "--help")
        runner.assert_true(alias_help.returncode == 0 and "检索微信公众号" in alias_help.stdout, "CLI: ky wx 别名命令正确响应")

        # D.3 TUI Navigator 菜单项检查
        tui_content = (ROOT / "tools" / "tui_navigator.py").read_text(encoding="utf-8")
        runner.assert_true("wechat_search" in tui_content, "TUI: tui_navigator.py 已成功挂载 wechat_search 动作")
        runner.assert_true("微信公众号考研经验" in tui_content, "TUI: tui_navigator.py 菜单列表显示微信经验检索项")

    except Exception as e:
        runner.assert_true(False, f"测试组 D 异常: {e}")

    # ============================================================
    # 测试组 E: TUI / GUI 与后端技能模块契约守卫 (Contract Guard)
    # 防止菜单/按钮调用到后端不存在的函数 (历史缺陷: compose_exam / retrieve_variants /
    # run_syllabus_diff_flow / ingest_material_file / compare_two_schools)
    # ============================================================
    print("\n[测试组 E: TUI / GUI 后端契约守卫]")
    try:
        from tools.skills import exam_composer, variant_retriever, material_ingestion
        from tools.intelligence import (
            syllabus_diff, comparator, watcher as intel_watcher, current_exam_year,
        )

        # E.1 后端真实 API 存在性
        runner.assert_true(hasattr(exam_composer, "compose_exam_paper"),
                           "契约: exam_composer.compose_exam_paper 存在")
        runner.assert_true(not hasattr(exam_composer, "compose_exam") or hasattr(exam_composer, "compose_exam_paper"),
                           "契约: 组卷入口以 compose_exam_paper 为准")
        runner.assert_true(hasattr(variant_retriever, "search_real_variant"),
                           "契约: variant_retriever.search_real_variant 存在")
        runner.assert_true(hasattr(material_ingestion, "get_material_ingestion_pipeline"),
                           "契约: material_ingestion.get_material_ingestion_pipeline 存在")
        pipe = material_ingestion.get_material_ingestion_pipeline()
        runner.assert_true(hasattr(pipe, "ingest_file") and hasattr(pipe, "ingest_text"),
                           "契约: 切片管道具备 ingest_file / ingest_text 方法")
        runner.assert_true(hasattr(syllabus_diff, "get_syllabus_diff_generator"),
                           "契约: syllabus_diff.get_syllabus_diff_generator 存在")
        runner.assert_true(hasattr(comparator, "get_school_comparator") and hasattr(comparator.SchoolComparator, "compare"),
                           "契约: comparator 以 SchoolComparator.compare 对标双校")
        runner.assert_true(hasattr(intel_watcher.AdmissionWatcher, "check_updates"),
                           "契约: AdmissionWatcher.check_updates 存在")

        # E.2 TUI 源码不得再引用不存在的旧 API
        tui_src = (ROOT / "tools" / "tui_navigator.py").read_text(encoding="utf-8")
        ghost_apis = ["compose_exam(", "format_exam_paper", "retrieve_variants",
                      "run_syllabus_diff_flow", "ingest_material_file",
                      "compare_two_schools", "format_comparison_report",
                      "volatility_rate"]
        ghost_hits = [g for g in ghost_apis if g in tui_src]
        runner.assert_true(not ghost_hits, f"TUI: 已无幽灵 API 引用 (命中: {ghost_hits or '无'})")

        # E.3 TUI 调用的后端返回字段真实存在 (dict key 校验)
        runner.assert_true("volatility_percentage" in tui_src, "TUI: 考纲Diff 读取 metrics.volatility_percentage")
        runner.assert_true("ingest_file" in tui_src and "ingest_material_file" not in tui_src,
                           "TUI: 切片入库走 pipe.ingest_file 正确入口")

        # E.4 GUI 源码不得再引用不存在的旧 API
        gui_src = (ROOT / "tools" / "gui" / "main_window.py").read_text(encoding="utf-8")
        gui_ghost = [g for g in ghost_apis if g in gui_src]
        runner.assert_true(not gui_ghost, f"GUI: 已无幽灵 API 引用 (命中: {gui_ghost or '无'})")
        runner.assert_true("compose_exam_paper" in gui_src, "GUI: 错题盲盒走 compose_exam_paper 正确入口")

        # E.5 syllabus_diff metrics 键名一致性
        gen = syllabus_diff.get_syllabus_diff_generator()
        rep = gen.compare_texts(
            "- **掌握**：函数极限；\n- **理解**：定积分定义；\n",
            "- **掌握**：函数极限；\n- **掌握**：不定积分计算；\n",
            school="契约测试", major="contract", year_old=2026, year_new=current_exam_year()
        )
        need_keys = {"total_old", "total_new", "added_count", "removed_count",
                     "modified_count", "unchanged_count", "volatility_percentage", "stability_grade"}
        runner.assert_true(need_keys.issubset(rep["metrics"].keys()),
                           "契约: compare_texts metrics 字段完整")
        runner.assert_true(rep["metrics"]["added_count"] >= 1 and rep["metrics"]["removed_count"] >= 1,
                           "契约: 增删考点识别在最小样例上正确")
        saved = gen.save_diff_report(rep, output_path=ROOT / "tools" / "scratch" / "_contract_diff_report.md")
        runner.assert_true(saved.exists() and saved.stat().st_size > 200,
                           "契约: save_diff_report 落盘成功 (返回 Path)")

    except Exception as e:
        runner.assert_true(False, f"测试组 E 异常: {e}")

    return runner.print_summary()


if __name__ == "__main__":
    success = run_new_feature_tests()
    sys.exit(0 if success else 1)
