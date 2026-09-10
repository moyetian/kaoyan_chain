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
        # [P0 修复] SKIP 不能计入全绿通过，杜绝伪绿通过
        if self.failed == 0 and self.skipped == 0:
            print(" 🎉 全部新功能测试项 100% 通过！升级模块稳健可靠！")
            print("=" * 60)
            return True
        elif self.failed == 0 and self.skipped > 0:
            print(f" ⚠️ 存在 {self.skipped} 项跳过，不能判定为全绿（请补齐依赖环境以实现全覆盖）")
            print("=" * 60)
            return False
        else:
            print(f" ❌ 以下测试未通过: {', '.join(self.errors)}")
            print("=" * 60)
            return False


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
        if not fallback_results:
            # 在干净环境或 CI 中自动写入测试种子档案以保障测试自包含与幂等性
            _pipe = WeChatContentPipeline()
            _pipe.save_article(test_item, category="计算机考研经验")
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
                # [P0 修复] 行为级校验：QSS 必须覆盖 GUI 实际 setObjectName 的全部核心控件
                _required_selectors = ("QMainWindow", "#HeaderBar", "#FunctionCard", "#TaskRow", "#QuickPill")
                runner.assert_true(len(dark_qss) > 200 and all(s in dark_qss for s in _required_selectors),
                                   "GUI: 暗黑主题 dark.qss 覆盖 GUI 实际使用的全部核心控件规则")
                runner.assert_true(len(light_qss) > 200 and all(s in light_qss for s in _required_selectors),
                                   "GUI: 明亮主题 light.qss 覆盖 GUI 实际使用的全部核心控件规则")
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
            # [P0 修复] 行为级校验：QSS 必须覆盖 GUI 实际 setObjectName 的全部核心控件
            _required_selectors = ("QMainWindow", "#HeaderBar", "#FunctionCard", "#TaskRow", "#QuickPill")
            runner.assert_true(len(dark_qss) > 200 and all(s in dark_qss for s in _required_selectors),
                               "GUI: 暗黑主题 dark.qss 覆盖 GUI 实际使用的全部核心控件规则")
            runner.assert_true(len(light_qss) > 200 and all(s in light_qss for s in _required_selectors),
                               "GUI: 明亮主题 light.qss 覆盖 GUI 实际使用的全部核心控件规则")

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

        # E.1 后端真实 API 契约校验
        # [P0 修复] 由"仅 hasattr 存在性"升级为签名级行为契约：
        # 验证入口存在、且关键形参未被静默改名（改坏签名即红），行为级覆盖见各组专项测试
        import inspect
        _sig_compose = inspect.signature(exam_composer.compose_exam_paper)
        runner.assert_true({"subject", "count", "save_file"}.issubset(_sig_compose.parameters),
                           "契约: 组卷入口 compose_exam_paper(subject, count, save_file) 签名稳定")
        runner.assert_true(hasattr(exam_composer, "compose_exam_paper") and callable(exam_composer.compose_exam_paper),
                           "契约: 组卷标准入口 compose_exam_paper 存在且可调用")
        _sig_variant = inspect.signature(variant_retriever.search_real_variant)
        runner.assert_true({"subject", "keyword", "limit"}.issubset(_sig_variant.parameters),
                           "契约: 变式检索 search_real_variant(subject, keyword, limit) 签名稳定")
        pipe = material_ingestion.get_material_ingestion_pipeline()
        _sig_ingest_file = inspect.signature(pipe.ingest_file)
        runner.assert_true("file_path" in _sig_ingest_file.parameters or len(_sig_ingest_file.parameters) >= 1,
                           "契约: 切片管道具备可调用的 ingest_file / ingest_text 方法")
        _sig_gen = inspect.signature(syllabus_diff.get_syllabus_diff_generator)
        runner.assert_true(_sig_gen and callable(syllabus_diff.get_syllabus_diff_generator),
                           "契约: 考纲Diff 工厂 get_syllabus_diff_generator 可调用")
        _cmp = comparator.get_school_comparator()
        _sig_compare = inspect.signature(_cmp.compare)
        runner.assert_true(len(_sig_compare.parameters) >= 2,
                           "契约: SchoolComparator.compare 至少接收双校参数")
        _sig_watch = inspect.signature(intel_watcher.AdmissionWatcher.check_updates)
        runner.assert_true("school_query" in _sig_watch.parameters,
                           "契约: AdmissionWatcher.check_updates(school_query) 签名稳定")

        # E.2 TUI 源码不得再引用不存在的旧 API（静态 lint 防回退；
        #     行为级验证由 E.5 后端实测与 CLI smoke 测试组承担）
        tui_src = (ROOT / "tools" / "tui_navigator.py").read_text(encoding="utf-8")
        ghost_apis = ["compose_exam(", "format_exam_paper", "retrieve_variants",
                      "run_syllabus_diff_flow", "ingest_material_file",
                      "compare_two_schools", "format_comparison_report",
                      "volatility_rate"]
        ghost_hits = [g for g in ghost_apis if g in tui_src]
        runner.assert_true(not ghost_hits, f"TUI: 已无幽灵 API 引用 (命中: {ghost_hits or '无'})")

        # E.3 TUI 与后端字段命名对齐（静态 lint；metrics 键的行为级校验见 E.5 实测）
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

    # ============================================================
    # 测试组 F: 开放题多模型判分引擎 (open_grader)
    # 全部用例通过注入的 mock client 完成，不依赖真实 API 可用性
    # ============================================================
    print("\n[测试组 F: 开放题多模型判分引擎 (open_grader)]")
    try:
        from tools.skills import open_grader

        def _mk_cfg(reviewers=None, judge=None, **over):
            cfg = {
                "enabled": True,
                "pass_threshold": 6.0,
                "divergence_threshold": 2.0,
                "gray_zone": 2.0,
                "min_confidence": 0.6,
                "min_valid_reviews": 2,
                "cache_rubric": False,          # 关闭缓存以隔离用例
                "max_retries": 0,
                "per_call_timeout": 5.0,
                "total_budget": 30.0,
                "reviewers": reviewers if reviewers is not None else [
                    {"name": n, "role": "reviewer", "base_url": "http://mock/v1",
                     "api_key": "k", "model": "m", "weight": 1.0, "temperature": 0.2}
                    for n in ("A", "B", "C")
                ],
                "judge": judge if judge is not None else {
                    "name": "judge", "base_url": "http://mock/v1", "api_key": "k",
                    "model": "m", "weight": 2.0, "temperature": 0.0, "enabled": True},
            }
            cfg.update(over)
            return cfg

        def _mk_client(scores, judge_score=6.0, conf=0.9, judge_conf=0.9,
                       mistake="计算失误", fail_names=(), garbage_names=()):
            """构造 mock LLM 调用器；按 endpoint.name 决定返回内容。"""
            state = {"rubric": 0, "review": 0, "judge": 0}

            def _client(messages, endpoint):
                content = messages[-1].get("content", "")
                if "生成评分要点" in content:
                    state["rubric"] += 1
                    return json.dumps({
                        "rubric": [{"id": 1, "point": "核心公式与定理条件", "score": 5.0},
                                   {"id": 2, "point": "推导步骤与最终结论", "score": 5.0}],
                        "derived_from": "reference"})
                name = endpoint.get("name", "")
                if "不一致" in content or "仲裁" in content:
                    state["judge"] += 1
                    return json.dumps({
                        "total": judge_score, "rubric_hits": [],
                        "mistake_type": mistake, "confidence": judge_conf,
                        "reason": "仲裁依据"})
                state["review"] += 1
                if name in fail_names:
                    return None
                if name in garbage_names:
                    return "这不是 JSON，模型输出失控了"
                return json.dumps({
                    "total": scores.get(name, 5.0), "rubric_hits": [],
                    "mistake_type": mistake, "confidence": conf, "reason": "复核意见"})
            return _client, state

        Q = "请推导不定积分 ∫x·e^x dx 并写出所用定理条件。"
        GOOD = "使用分部积分法：令 u=x, dv=e^x dx，则 du=dx, v=e^x，得 x·e^x - e^x + C。"
        BAD = "不会。"

        # F.1 未启用时保持「转人工复核」，不调用任何模型
        r = open_grader.grade_open_question(Q, GOOD, config={"enabled": False})
        runner.assert_true(r.match_level == 1 and r.degraded,
                           "F.1 未启用时回落转人工复核且不臆造分数")

        # F.2 空作答直接判 0 分（无需调用模型）
        r = open_grader.grade_open_question(Q, "   ", config=_mk_cfg())
        runner.assert_true(r.match_level == 0 and r.score == 0.0,
                           "F.2 空作答判 0 分（不发起模型调用）")

        # F.3 三模型一致高分 → 通过
        c, st = _mk_client({"A": 9.0, "B": 9.0, "C": 8.5})
        r = open_grader.grade_open_question(Q, GOOD, config=_mk_cfg(), llm_client=c)
        runner.assert_true(r.match_level == 2 and r.score >= 6.0,
                           f"F.3 完整正确作答判为通过 (score={r.score})")

        # F.4 三模型一致低分（仅抄题干/放弃）→ 不通过，且不得满分
        c, st = _mk_client({"A": 0.5, "B": 0.0, "C": 1.0}, mistake="概念漏洞")
        r = open_grader.grade_open_question(Q, BAD, config=_mk_cfg(), llm_client=c)
        runner.assert_true(r.match_level == 0 and r.score < 6.0,
                           f"F.4 放弃作答判为不通过 (score={r.score})")

        # F.5 结论分歧大 → 触发主审仲裁（不得直接取平均了事）
        c, st = _mk_client({"A": 8.0, "B": 8.0, "C": 4.0}, judge_score=5.0)
        r = open_grader.grade_open_question(Q, GOOD, config=_mk_cfg(), llm_client=c)
        runner.assert_true(st["judge"] >= 1, "F.5 评审分歧触发主审仲裁")
        runner.assert_true(r.arbitrated, "F.5 结果标记已仲裁")

        # F.6 有效评审不足（3 个中 2 个失败）→ 转人工
        c, st = _mk_client({"A": 9.0}, fail_names=("B", "C"))
        r = open_grader.grade_open_question(Q, GOOD, config=_mk_cfg(), llm_client=c)
        runner.assert_true(r.match_level == 1 and r.degraded,
                           "F.6 有效评审不足时转人工复核")

        # F.7 全部模型失败 → 转人工，绝不产出分数结论
        c, st = _mk_client({}, fail_names=("A", "B", "C"))
        r = open_grader.grade_open_question(Q, GOOD, config=_mk_cfg(), llm_client=c)
        runner.assert_true(r.match_level == 1 and r.score == 0.0,
                           "F.7 全部模型失败时转人工且不给分")

        # F.8 某模型返回非 JSON → 该模型弃权，其余仍可聚合
        c, st = _mk_client({"A": 9.0, "B": 9.0, "C": 9.0}, garbage_names=("A",))
        r = open_grader.grade_open_question(Q, GOOD, config=_mk_cfg(), llm_client=c)
        runner.assert_true(len(r.reviews) == 2 and r.match_level == 2,
                           f"F.8 输出非 JSON 的模型被弃权 (有效 {len(r.reviews)}/3)")

        # F.9 置信度过低 → 即使高分也转人工复核
        c, st = _mk_client({"A": 9.0, "B": 9.0, "C": 9.0}, conf=0.3, judge_conf=0.3)
        r = open_grader.grade_open_question(Q, GOOD, config=_mk_cfg(), llm_client=c)
        runner.assert_true(r.match_level == 1,
                           f"F.9 低置信度转人工复核 (conf={r.confidence})")

        # F.10 灰区分数（通过线 6，灰区 ±2）→ 转人工而非直接判不通过
        c, st = _mk_client({"A": 5.5, "B": 5.5, "C": 5.5}, judge_score=5.5)
        r = open_grader.grade_open_question(Q, GOOD, config=_mk_cfg(), llm_client=c)
        runner.assert_true(r.match_level == 1,
                           f"F.10 灰区分数转人工复核 (score={r.score})")

        # F.11 错因归类：多数一致优先
        c, st = _mk_client({"A": 2.0, "B": 2.0, "C": 2.0}, mistake="公式记错",
                           judge_score=2.0)
        r = open_grader.grade_open_question(Q, "随便写一个错误答案",
                                            config=_mk_cfg(), llm_client=c)
        runner.assert_true(r.mistake_type == "公式记错",
                           f"F.11 错因多数值归类正确 (={r.mistake_type})")

        # F.12 评分要点缓存生效（同题二次调用不再重复抽取）
        cfg_cached = _mk_cfg(cache_rubric=True)
        c, st = _mk_client({"A": 9.0, "B": 9.0, "C": 9.0})
        open_grader.grade_open_question(Q, GOOD, config=cfg_cached, llm_client=c)
        first = st["rubric"]
        open_grader.grade_open_question(Q, GOOD, config=cfg_cached, llm_client=c)
        runner.assert_true(first >= 1 and st["rubric"] == first,
                           f"F.12 评分要点缓存生效 (抽取次数 {st['rubric']})")

        # F.13 集成：未启用时 exam-submit 开放题链路与既有行为一致（转人工）
        # 注意：项目同时存在 skills.X 与 tools.skills.X 两条导入路径，二者是**不同的
        # 模块实例**。集成测试必须拿到与 exam_composer 内部相同的实例，
        # 否则 monkeypatch 不生效，会产生「看似通过」的假阳性。
        import importlib
        try:
            og = importlib.import_module("skills.open_grader")
        except Exception:
            og = importlib.import_module("tools.skills.open_grader")

        try:
            from tools.skills import exam_composer
            lvl, basis = exam_composer._grade_open_by_llm(
                {"question": Q, "grading_mode": "open", "subject": "math"},
                GOOD, "math")
            runner.assert_true(lvl == 1 and "转人工" in basis,
                               "F.13 集成：未启用时开放题回落转人工复核")
        except Exception as e:
            runner.assert_true(False, f"F.13 集成测试异常: {e}")

        # F.14 集成：启用 + 注入 mock 后，exam-submit 开放题可真正判出「通过」
        try:
            from tools.skills import exam_composer
            og.set_injected_client(_mk_client({"A": 9.0, "B": 9.0, "C": 9.0})[0])
            orig_loader = og._load_grading_config
            og._load_grading_config = lambda: _mk_cfg()
            try:
                lvl, basis = exam_composer._grade_open_by_llm(
                    {"question": Q, "grading_mode": "open", "subject": "math"},
                    GOOD, "math")
                runner.assert_true(lvl == 2 and "通过" in basis,
                                   f"F.14 集成：启用后开放题判出通过 (level={lvl})")
                # F.15 集成：启用但模型全部失败时，仍回落转人工（绝不臆造 0 分结论）
                og.set_injected_client(_mk_client({}, fail_names=("A", "B", "C"))[0])
                lvl2, basis2 = exam_composer._grade_open_by_llm(
                    {"question": Q, "grading_mode": "open", "subject": "math"},
                    GOOD, "math")
                runner.assert_true(lvl2 == 1 and "转人工" in basis2,
                                   f"F.15 集成：模型失败时回落转人工 (level={lvl2})")
            finally:
                og._load_grading_config = orig_loader
                og.set_injected_client(None)
        except Exception as e:
            runner.assert_true(False, f"F.14/F.15 集成测试异常: {e}")

    except Exception as e:
        runner.assert_true(False, f"测试组 F 异常: {e}")

    # ============================================================
    # 测试组 G: 判分引擎打磨项回归（每条用例对应一个「修复前会出错」的缺陷）
    # ============================================================
    print("\n[测试组 G: 判分引擎打磨项回归 (open_grader hardening)]")
    try:
        import time as _time
        from tools.skills import open_grader as G

        def _gcfg(**over):
            c = {
                "enabled": True, "pass_threshold": 6.0, "divergence_threshold": 2.0,
                "gray_zone": 2.0, "min_confidence": 0.6, "min_valid_reviews": 2,
                "cache_rubric": False, "max_retries": 0, "per_call_timeout": 5.0,
                "total_budget": 30.0, "rubric_cache_size": 256,
                "max_question_chars": 4000, "max_answer_chars": 6000,
                "reviewers": [
                    {"name": n, "base_url": "http://mock/v1", "api_key": "k",
                     "model": "m", "weight": 1.0, "temperature": 0.2}
                    for n in ("A", "B", "C")],
                "judge": {"name": "judge", "base_url": "http://mock/v1", "api_key": "k",
                          "model": "m", "weight": 2.0, "enabled": True},
            }
            c.update(over)
            return c

        def _client(scores, derived="reference", conf=0.9, seen=None,
                    fail_names=(), http_429_names=(), http_401_names=(),
                    counters=None, delay=0.0):
            """可配置 mock：支持统计各评审调用次数、注入 HTTP 错误、抓取提示词。
            delay 为「抽取评分要点」阶段的模拟耗时，用于验证 total_budget。"""
            st = {"rubric": 0, "review": 0, "judge": 0}

            def _c(messages, endpoint):
                content = messages[-1].get("content", "")
                if seen is not None:
                    seen.append(messages)
                if "生成评分要点" in content:
                    st["rubric"] += 1
                    if delay:
                        _time.sleep(delay)
                    return json.dumps({
                        "rubric": [{"id": 1, "point": "核心公式与最终结论", "score": 10.0}],
                        "derived_from": derived})
                name = endpoint.get("name", "")
                if "不一致" in content or "仲裁" in content:
                    st["judge"] += 1
                    return json.dumps({"total": 6.0, "rubric_hits": [],
                                       "mistake_type": "无", "confidence": conf,
                                       "reason": "仲裁依据"})
                st["review"] += 1
                n = 0
                if counters is not None:
                    counters[name] = counters.get(name, 0) + 1
                    n = counters[name]
                if name in http_429_names and n <= 2:
                    raise RuntimeError("HTTP 429")
                if name in http_401_names:
                    raise RuntimeError("HTTP 401")
                if name in fail_names:
                    return None
                return json.dumps({"total": scores.get(name, 5.0), "rubric_hits": [],
                                   "mistake_type": "计算失误", "confidence": conf,
                                   "reason": "复核意见"})
            return _c, st

        QQ = "请说明拉格朗日中值定理的适用条件与结论。"
        AA = "需在闭区间连续、开区间可导，则存在一点导数等于平均变化率。"

        # G.1【可复现性】缓存命中不得丢失 derived 标记
        #     修复前：第二次命中缓存时 derived 恒为 False → 置信度封顶失效
        #     → 同一份作答两次判分结论不一致
        G.clear_rubric_cache()
        cfg_cached = _gcfg(cache_rubric=True)
        c1, s1 = _client({"A": 9.0, "B": 9.0, "C": 9.0}, derived="question_only")
        r1 = G.grade_open_question(QQ, AA, config=cfg_cached, llm_client=c1)
        c2, s2 = _client({"A": 9.0, "B": 9.0, "C": 9.0}, derived="question_only")
        r2 = G.grade_open_question(QQ, AA, config=cfg_cached, llm_client=c2)
        runner.assert_true(s2["rubric"] == 0, "G.1 缓存命中不再重复抽取评分要点")
        runner.assert_true(r1.confidence == r2.confidence == 0.7,
                           f"G.1 缓存命中不丢 derived 置信度封顶 "
                           f"(首次 {r1.confidence} / 二次 {r2.confidence}，期望均 0.7)")

        # G.2【可用性】HTTP 429 限流必须重试（修复前被当作 4xx 直接放弃）
        counters = {}
        c, _st = _client({"A": 9.0, "B": 9.0, "C": 9.0},
                         http_429_names=("A",), counters=counters)
        r = G.grade_open_question("题面", "作答内容",
                                  config=_gcfg(max_retries=2), llm_client=c)
        runner.assert_true(counters.get("A") == 3,
                           f"G.2 429 应重试至成功 (A 实际调用 {counters.get('A')} 次，期望 3)")
        runner.assert_true(len(r.reviews) == 3,
                           f"G.2 429 重试成功后计入有效评审 (有效 {len(r.reviews)}/3)")

        # G.3【成本】HTTP 401 属配置错误，不得重试（避免白烧配额）
        counters = {}
        c, _st = _client({"A": 9.0, "B": 9.0, "C": 9.0},
                         http_401_names=("A",), counters=counters)
        r = G.grade_open_question("题面", "作答内容",
                                  config=_gcfg(max_retries=3), llm_client=c)
        runner.assert_true(counters.get("A") == 1,
                           f"G.3 401 不重试 (A 实际调用 {counters.get('A')} 次，期望 1)")

        # G.4【端点兼容】修复前 endswith("/v1") 判断会把 Gemini 兼容层拼坏
        _n = G._normalize_openai_url
        runner.assert_true(_n("https://api.deepseek.com", "chat/completions")
                           == "https://api.deepseek.com/v1/chat/completions",
                           "G.4 裸域名自动补 /v1")
        runner.assert_true(_n("https://x.com/v1/", "chat/completions")
                           == "https://x.com/v1/chat/completions",
                           "G.4 已带 /v1 不重复拼接")
        runner.assert_true(
            _n("https://generativelanguage.googleapis.com/v1beta/openai", "chat/completions")
            == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "G.4 /v1beta/openai 兼容根不被错误拼接")
        runner.assert_true(_n("https://x.com/v1/chat/completions", "chat/completions")
                           == "https://x.com/v1/chat/completions",
                           "G.4 已是完整端点则原样返回")

        # G.5【解析鲁棒性】修复前贪婪正则遇到两个 JSON 块必然解析失败
        _ex = G._extract_json
        obj = _ex('思考中：{"note":"我先读题"}\n最终结果：{"total":8,"confidence":0.9}')
        runner.assert_true(isinstance(obj, dict) and obj.get("total") == 8,
                           f"G.5 多 JSON 块能取到真正的评分对象 (得到 {obj})")
        obj = _ex('```json\n{"total":7.5}\n```')
        runner.assert_true(isinstance(obj, dict) and obj.get("total") == 7.5,
                           "G.5 代码围栏包裹可解析")
        runner.assert_true(_ex("这不是 JSON，模型输出失控了") is None,
                           "G.5 纯文本返回 None")

        # G.6【健壮性】空有效评审不得抛 ValueError（修复前 max([]) 会直接崩）
        res = G._finalize(G.OpenGradeResult(), [], None, _gcfg(), False, 0.0)
        runner.assert_true(res.match_level == 1,
                           "G.6 _finalize 空评审防御性返回「转人工」而非抛异常")
        c, _st = _client({}, fail_names=("A", "B", "C"))
        r = G.grade_open_question("题面", "作答内容",
                                  config=_gcfg(min_valid_reviews=0), llm_client=c)
        runner.assert_true(r.match_level == 1 and r.score == 0.0,
                           "G.6 全模型失败时转人工且不给分（不抛异常）")

        # G.7【可观测性】弃权原因必须透传，修复前 degraded=True 却无任何原因
        c, _st = _client({}, fail_names=("A", "B", "C"))
        r = G.grade_open_question("题面", "作答内容", config=_gcfg(), llm_client=c)
        runner.assert_true("A" in (r.error or ""),
                           f"G.7 弃权原因透传到 error 字段 (error={r.error!r})")

        # G.8【成本/边界】超长作答按 max_answer_chars 截断并留痕
        seen = []
        c, _st = _client({"A": 9.0, "B": 9.0, "C": 9.0}, seen=seen)
        r = G.grade_open_question("题面", "步骤" * 2000,
                                  config=_gcfg(max_answer_chars=200), llm_client=c)
        review_users = [m for ms in seen for m in ms
                        if m.get("role") == "user" and "学员作答" in m.get("content", "")]
        runner.assert_true(bool(review_users)
                           and all("已截断" in m["content"] for m in review_users),
                           f"G.8 超长作答被截断且留痕 (评审提示 {len(review_users)} 条)")

        # G.9【安全】三条提示词的 system 均须含提示注入防线
        seen = []
        c, _st = _client({"A": 9.0, "B": 9.0, "C": 9.0}, seen=seen)
        G.grade_open_question("题面", "作答内容", config=_gcfg(), llm_client=c)
        sys_msgs = [m for ms in seen for m in ms if m.get("role") == "system"]
        runner.assert_true(len(sys_msgs) >= 4
                           and all("不可信输入" in m["content"] for m in sys_msgs),
                           f"G.9 全部 system 提示含注入防线 (共 {len(sys_msgs)} 条)")

        # G.10【一致性】需要仲裁却无主审可用时，必须显式标注而非静默通过
        cfg_nj = _gcfg(judge=None)
        for _rv in cfg_nj["reviewers"]:
            _rv["same_source"] = True
        c, _st = _client({"A": 9.0, "B": 9.0, "C": 9.0})
        r = G.grade_open_question("题面", "作答内容", config=cfg_nj, llm_client=c)
        runner.assert_true(r.degraded and "未获主审交叉仲裁" in r.reason,
                           f"G.10 仲裁不可用时显式标注 (degraded={r.degraded})")

        # G.11【预算】total_budget 必须真正生效（修复前被池 shutdown(wait=True) 抵消）
        #      用「抽取要点耗时 0.3s + 预算 0.05s」构造确定性超预算——
        #      不能用极小预算（如 0.0001s）：Windows time.time() 粒度约 15ms，
        #      小于一个时钟滴答的预算根本测不出来，会产生假阴性。
        c, _st = _client({"A": 9.0, "B": 9.0, "C": 9.0}, delay=0.3)
        r = G.grade_open_question("题面", "作答内容",
                                  config=_gcfg(total_budget=0.05), llm_client=c)
        runner.assert_true(r.match_level == 1 and "超时" in r.reason,
                           f"G.11 total_budget 生效（超预算转人工，reason={r.reason[:28]}…）")
        runner.assert_true(bool(r.error) and "超预算" in r.error,
                           f"G.11 超预算原因被记录 (error={r.error!r})")

    except Exception as e:
        runner.assert_true(False, f"测试组 G 异常: {e}")

    # ============================================================
    # 测试组 H: 代码审查修复项回归 (R-1..R-9)
    #   每条用例都刻意构造「修复前必然给出错误结果」的输入，
    #   因此它们不只是覆盖率装饰，而是真正锁死回归的负向/边界用例。
    # ============================================================
    print("\n[测试组 H: 代码审查修复项回归 (audit fixes R-1..R-9)]")
    _h_tmp = Path(tempfile.mkdtemp(prefix="ky_h_"))
    try:
        from tools import ky_io as K

        # ---------- H.1 【R-4】原子写：不留半截文件 ----------
        _tgt = _h_tmp / "sub" / "ky_config.json"
        K.atomic_write_text(_tgt, '{"a": 1}')
        runner.assert_true(
            _tgt.exists() and json.loads(_tgt.read_text(encoding="utf-8"))["a"] == 1,
            "H.1 原子写自动创建父目录且内容正确")
        runner.assert_true(
            not list((_h_tmp / "sub").glob("*.tmp")),
            "H.1 原子写完成后目录内无 .tmp 残留")

        # 构造「写入中途失败」（ascii 编码器无法编码中文）：
        # 修复前裸 write_text 会把目标截断/写坏；修复后目标必须保持旧内容完好。
        K.atomic_write_text(_tgt, '{"b": 2}')
        _raised = False
        try:
            K.atomic_write_text(_tgt, "中文内容", encoding="ascii")
        except Exception:
            _raised = True
        runner.assert_true(_raised, "H.1 编码失败会向上抛出（不静默吞错）")
        runner.assert_true(
            json.loads(_tgt.read_text(encoding="utf-8")).get("b") == 2
            and not list((_h_tmp / "sub").glob("*.tmp")),
            "H.1 写入失败后旧文件内容完好且无 .tmp 残留")

        # ---------- H.2 【R-6】safe_filename：路径穿越 / 保留名 / 边界 ----------
        _payload = "../../04-专业课/考试大纲.md"
        _flat = K.safe_filename(_payload)
        # 安全不变量：结果必须是「单层文件名」——不含路径分隔符、取 name 后与原值相等，
        # 且不能退化成 "." / ".." 这两个真正的目录引用。
        # 注意：只在内部出现的 ".." 子串（如 ".._x"）不构成穿越，不应误判为失败。
        runner.assert_true(
            "/" not in _flat and "\\" not in _flat
            and Path(_flat).name == _flat and _flat not in (".", ".."),
            f"H.2 路径穿越载荷被压平为单层文件名 (得到 {_flat!r})")
        runner.assert_true(_flat.endswith(".md"), "H.2 穿越载荷仍保留扩展名")
        runner.assert_true(
            K.safe_filename("..") == "未命名" and K.safe_filename(".") == "未命名",
            "H.2 纯目录引用被回退为默认名（不产生可穿越的 ..）")
        runner.assert_true(
            K.safe_filename("CON") == "_CON"
            and K.safe_filename("con.txt").startswith("_"),
            "H.2 Windows 保留设备名被前缀转义")
        runner.assert_true(
            K.safe_filename("") == "未命名" and K.safe_filename("   ") == "未命名",
            "H.2 空名回退默认值")
        _long = K.safe_filename("a" * 300 + ".md", max_length=40)
        runner.assert_true(
            len(_long) <= 40 and _long.endswith(".md"),
            f"H.2 超长名截断且保留扩展名 (len={len(_long)})")
        runner.assert_true(
            K.safe_filename("x:y*z?.md") == "x_y_z_.md",
            "H.2 非法字符替换为下划线")

        # ---------- H.3 【R-7】read_text_fallback：GBK 不再静默丢字 ----------
        _gbk = _h_tmp / "gbk_source.txt"
        _content = "极限与连续：拉格朗日中值定理的适用条件。"
        _gbk.write_bytes(_content.encode("gbk"))
        _lossy = _gbk.read_text(encoding="utf-8", errors="ignore")
        _recovered = K.read_text_fallback(_gbk)
        runner.assert_true(_recovered == _content, "H.3 GBK 资料按回退编码无损读出")
        runner.assert_true(
            _lossy != _content,
            f"H.3 对照：errors='ignore' 确会丢字（丢失后得到 {_lossy!r}）")
        _raised = False
        try:
            K.read_text_fallback(_h_tmp / "not_exist.txt")
        except Exception:
            _raised = True
        runner.assert_true(_raised, "H.3 文件不存在时显式抛出而非返回空串")

        # ---------- H.10 【R-4 配套】is_within 目录边界判定 ----------
        runner.assert_true(
            K.is_within(_h_tmp / "a" / "b.txt", _h_tmp / "a") is True
            and K.is_within(_h_tmp / "b.txt", _h_tmp / "a") is False,
            "H.10 is_within 正确判定目录边界（含自身、排除外部）")

        # ---------- H.4 【R-1】SessionEnd 钩子达成率不再恒为 0% ----------
        from tools.agent import hooks as HK
        import study_planner as SP

        _ws = _h_tmp / "hooks_ws"
        _tdir = _ws / "01-数学" / "_状态"
        _tdir.mkdir(parents=True)
        (_tdir / "今日任务.md").write_text(
            "| 模块 | 任务 | 完成状态 |\n"
            "|---|---|---|\n"
            "| 高数 | 极限 | [x] |\n"
            "| 高数 | 导数 | [ ] |\n"
            "| 线代 | 矩阵 | [x] |\n"
            "| 概率 | 分布 | [ ] |\n",
            encoding="utf-8")

        _captured = {}
        _orig_rec = SP.record_daily_completion

        def _fake_record(rate, total=0, completed=0, date_str=None):
            _captured.update(rate=rate, total=total, completed=completed)

        SP.record_daily_completion = _fake_record
        try:
            _hm = HK.HookManager(workspace_root=_ws)
            _debrief = [f for _p, f in _hm.hooks[HK.HookEvent.SESSION_END]
                        if getattr(f, "__name__", "") == "session_end_debrief_hook"]
            runner.assert_true(len(_debrief) == 1, "H.4 日终复盘钩子已注册")
            _ctx = {}
            _debrief[0](_ctx)
        finally:
            SP.record_daily_completion = _orig_rec

        runner.assert_true(
            _captured.get("total") == 4 and _captured.get("completed") == 2,
            f"H.4 今日任务统计正确 (total={_captured.get('total')}, "
            f"done={_captured.get('completed')})")
        runner.assert_true(
            _captured.get("rate") == 50.0,
            f"H.4 达成率被真实计算而非恒 0 (rate={_captured.get('rate')})")
        runner.assert_true(
            "50.0%" in _ctx.get("debrief_summary", ""),
            "H.4 复盘简报回显 50.0% 达成率")

        # ---------- H.5 【R-5】非法/非相邻日期不得误触发防疲劳减负 ----------
        _bad = SP.check_fatigue_alert({"completion_history": {
            "2026-13-99": {"rate": 10.0}, "not-a-date": {"rate": 20.0}}})
        runner.assert_true(
            _bad["alert"] is False,
            f"H.5 非法日期不得判定为连续疲劳 (alert={_bad['alert']})")
        _gap = SP.check_fatigue_alert({"completion_history": {
            "2026-09-01": {"rate": 10.0}, "2026-09-10": {"rate": 20.0}}})
        runner.assert_true(
            _gap["alert"] is False,
            f"H.5 间隔 9 天不得判定为连续疲劳 (alert={_gap['alert']})")
        _ok = SP.check_fatigue_alert({"completion_history": {
            "2026-09-09": {"rate": 10.0}, "2026-09-10": {"rate": 20.0}}})
        runner.assert_true(
            _ok["alert"] is True and _ok["consecutive_low_days"] == 2,
            f"H.5 真连续两天低完成率仍正确触发 (alert={_ok['alert']})")

        # ---------- H.6 【R-3】发布默认处于预览模式（安全默认值） ----------
        from tools import sync_publish as SPUB
        runner.assert_true(
            SPUB.DRY_RUN is True,
            "H.6 发布默认处于预览模式（DRY_RUN 默认 True，杜绝误推送）")

        # ---------- H.7 【R-7】不可解码文本显式报错而非静默残废入库 ----------
        from tools.skills.material_ingestion import get_material_ingestion_pipeline
        _bad_txt = _h_tmp / "undecodable.txt"
        _bad_txt.write_bytes(b"\xff\xfe\xff\xfe\xff")   # utf-8 / gbk 均无法解码
        _res = get_material_ingestion_pipeline().ingest_file(str(_bad_txt), subject="math")
        runner.assert_true(
            _res.get("success") is False and "编码" in str(_res.get("msg", "")),
            f"H.7 不可解码文本显式报错 (msg={str(_res.get('msg', ''))[:36]})")
        runner.assert_true(
            _res.get("count") == 0, "H.7 报错时不虚报入库数量")

        # ---------- H.8 【R-8】沙箱：相对穿越拒绝 / .json 不豁免 / 白名单只读仍可用 ----------
        from tools.agent.sandbox import Sandbox, SecurityException
        _ws2 = _h_tmp / "sb_ws"
        _ws2.mkdir(parents=True)
        _sb = Sandbox(workspace_root=_ws2)

        _blocked = False
        try:
            _sb.resolve_safe_path("../../secret.md", read_only=True)
        except SecurityException:
            _blocked = True
        runner.assert_true(_blocked, "H.8 相对路径穿越出工作区被拒绝")

        _inside = _sb.resolve_safe_path("01-数学/真题.md", allow_create=True)
        runner.assert_true(
            str(_inside).startswith(str(_ws2.resolve())),
            "H.8 工作区内相对路径不受影响（不误伤正常功能）")

        _ext_json = _h_tmp / "creds.json"
        _ext_json.write_text("{}", encoding="utf-8")
        _json_blocked = False
        try:
            _sb.resolve_safe_path(str(_ext_json), read_only=True)
        except SecurityException:
            _json_blocked = True
        runner.assert_true(
            _json_blocked, "H.8 工作区外 .json 只读豁免被取消（防凭据外泄）")

        _ext_md = _h_tmp / "photo_notes.md"
        _ext_md.write_text("# 真题笔记", encoding="utf-8")
        try:
            _read_back = _sb.resolve_safe_path(str(_ext_md), read_only=True)
            _ext_ok = _read_back.exists() and _read_back == _ext_md.resolve()
        except SecurityException:
            _ext_ok = False
        runner.assert_true(
            _ext_ok, "H.8 工作区外白名单只读（拍照真题/图片批改）仍可用")

        # ---------- H.9 【R-9】python 仅允许运行工作区内脚本 ----------
        from tools.agent.permissions import PermissionManager
        from tools.agent.tools_impl import ToolRegistry
        _ws3 = _h_tmp / "reg_ws"
        _ws3.mkdir(parents=True)
        _sb3 = Sandbox(workspace_root=_ws3)
        _perm = PermissionManager(mode="auto", workspace_root=_ws3)
        _perm.force_allow_all = True
        _reg = ToolRegistry(sandbox=_sb3, permissions=_perm)

        _evil = _h_tmp / "evil.py"
        _evil.write_text("print('pwned')", encoding="utf-8")
        _out = _reg.execute_tool(
            "run_command", {"command": f'python "{_evil}"'}, interactive=False)
        runner.assert_true(
            "安全拦截" in _out,
            f"H.9 工作区外 .py 脚本被执行前拦截 (out={str(_out)[:56]})")

        _out2 = _reg.execute_tool(
            "run_command", {"command": 'python -c "print(1)"'}, interactive=False)
        runner.assert_true("安全拦截" in str(_out2), "H.9 python -c 仍被禁用")

        # ---------- H.11 【R-6】error_logger 穿越载荷不得跨目录回写 ----------
        from tools.skills import error_logger as EL
        _elws = _h_tmp / "el_root"
        (_elws / "01-数学" / "错题本").mkdir(parents=True)
        (_elws / "02-英语" / "错题本").mkdir(parents=True)
        _decoy = _elws / "02-英语" / "错题本" / "decoy.md"
        _decoy_text = "## 📌 [2026-09-01] 目标标题\n- **复测节奏**：`stage=1`\n"
        _decoy.write_text(_decoy_text, encoding="utf-8")

        _orig_el_root = EL.ROOT
        EL.ROOT = _elws
        try:
            _ok11, _msg11 = EL.mark_error_status(
                "math", "../02-英语/错题本/decoy.md", title_keyword="目标标题")
        finally:
            EL.ROOT = _orig_el_root
        runner.assert_true(
            _ok11 is False,
            f"H.11 穿越载荷不得跨目录回写 (ok={_ok11}, msg={str(_msg11)[:40]})")
        runner.assert_true(
            _decoy.read_text(encoding="utf-8") == _decoy_text,
            "H.11 被瞄准的目标文件内容未被篡改")

        _ok11b, _msg11b = EL.mark_error_status("math", "", title_keyword="x")
        runner.assert_true(
            _ok11b is False and "空" in _msg11b, "H.11 空文件名被显式拒绝")
        _ok11c, _msg11c = EL.mark_error_status("math", "错题本.md", title_keyword="")
        runner.assert_true(
            _ok11c is False and "标题" in _msg11c,
            "H.11 空定位标题被显式拒绝（防误改首条错题）")

    except Exception as e:
        runner.assert_true(False, f"测试组 H 异常: {e}")
    finally:
        shutil.rmtree(_h_tmp, ignore_errors=True)

    return runner.print_summary()


if __name__ == "__main__":
    success = run_new_feature_tests()
    sys.exit(0 if success else 1)
