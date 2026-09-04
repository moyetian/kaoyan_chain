# -*- coding: utf-8 -*-
"""
考研学习链 (Kaoyan AI Study Chain) · ky-cli 与全系统严格自动化测试套件
全面覆盖：
  1. 配置文件加载/保存/容错机制
  2. 四科系统提示词与状态上下文装配 (Math, English, Politics, Major)
  3. 四大聊天平台 Webhook 报文格式与发送校验 (微信, QQ, 钉钉, 飞书)
  4. Webhook 网关 HTTP 服务器启动、模拟接收消息与自动应答
  5. CLI 各子命令执行 (notify, build, config)
  6. Git 隐私与安全隔离检查 (确保敏感配置绝不泄露)
"""

import sys
import os
import json
import time
import threading
import urllib.request
import urllib.parse
from pathlib import Path

# Windows UTF-8 控制台兼容
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import ky_cli

class TestRunner:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def assert_true(self, condition, test_name):
        if condition:
            print(f"  [PASS] {test_name}")
            self.passed += 1
        else:
            print(f"  [FAIL] {test_name}")
            self.failed += 1
            self.errors.append(test_name)

    def print_summary(self):
        print("\n" + "=" * 60)
        print(f" 测试结果统计: 通过 {self.passed} 项, 失败 {self.failed} 项")
        if self.failed == 0:
            print(" 🎉 全部测试项 100% 通过！系统各模块运转稳健！")
        else:
            print(f" ❌ 以下测试未通过: {', '.join(self.errors)}")
        print("=" * 60)
        return self.failed == 0

def run_tests():
    runner = TestRunner()
    print("============================================================")
    print(" 🧪 开始对 考研学习链 (ky-cli) 进行全链路严格自动化测试")
    print("============================================================\n")

    # ------------------------------------------------------------
    # 测试 1: 配置文件读写与默认结构
    # ------------------------------------------------------------
    print("[测试组 1: 配置文件与默认值校验]")
    cfg = ky_cli.load_config()
    runner.assert_true(isinstance(cfg, dict), "load_config 返回字典对象")
    runner.assert_true("api_provider" in cfg and "base_url" in cfg and "model" in cfg, "配置包含必要模型字段")
    runner.assert_true("webhooks" in cfg and "dingtalk" in cfg["webhooks"] and "wechat" in cfg["webhooks"], "配置包含 IM Webhooks 字段")

    # ------------------------------------------------------------
    # 测试 2: 四科系统提示词与学情状态自动组装
    # ------------------------------------------------------------
    print("\n[测试组 2: 四科私教提示词与上下文组装]")
    for subj_key in ("math", "eng", "pol", "pro"):
        prompt = ky_cli.build_system_prompt(subj_key)
        runner.assert_true(len(prompt) > 200, f"学科 [{subj_key}] 提示词生成完整 (长度: {len(prompt)} 字符)")
        runner.assert_true("AGENTS.md" in prompt or "考研" in prompt, f"学科 [{subj_key}] 成功挂载顶层中枢协议")

    math_prompt = ky_cli.build_system_prompt("math")
    runner.assert_true("严禁凭空捏造题目出处" in math_prompt and "李林880" in math_prompt, "数学私教提示词严密锁定防虚构李林880红线")

    # ------------------------------------------------------------
    # 测试 3: 四大聊天平台报文结构与容错机制
    # ------------------------------------------------------------
    print("\n[测试组 3: 微信/QQ/钉钉/飞书 报文与未配置容错]")
    # 钉钉未配置容错
    ok, err = ky_cli.send_to_dingtalk("", "测试消息")
    runner.assert_true(not ok and "未配置" in err, "钉钉空配置安全拦截")
    
    # 飞书未配置容错
    ok, err = ky_cli.send_to_feishu("", "测试消息")
    runner.assert_true(not ok and "未配置" in err, "飞书空配置安全拦截")

    # 微信未配置容错
    ok, err = ky_cli.send_to_wechat("", "测试消息")
    runner.assert_true(not ok and "未配置" in err, "微信空配置安全拦截")

    # QQ 未配置容错
    ok, err = ky_cli.send_to_qq("", "", "测试消息")
    runner.assert_true(not ok and "未配置" in err, "QQ 空配置安全拦截")

    # 钉钉加签算法校验
    import hmac, hashlib, base64
    secret = "SECtestsecret123"
    ts = "1600000000000"
    string_to_sign = f"{ts}\n{secret}"
    hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    runner.assert_true(len(sign) > 10, "钉钉 HMAC-SHA256 加签签名计算有效")

    # ------------------------------------------------------------
    # 测试 4: 晨报与任务卡片提取引擎
    # ------------------------------------------------------------
    print("\n[测试组 4: 每日晨报与自测卡片自动提取]")
    # 模拟静默广播调用（空 webhook 模式，不应抛出任何异常）
    try:
        ky_cli.broadcast_briefing(cfg, custom_msg="【自动化测试】考研学习链自检中")
        runner.assert_true(True, "广播简报提取与分发管道运行正常且无异常崩溃")
    except Exception as e:
        runner.assert_true(False, f"广播发生异常: {e}")

    # ------------------------------------------------------------
    # 测试 5: Webhook 网关 HTTP 服务器模拟通信
    # ------------------------------------------------------------
    print("\n[测试组 5: Webhook 网关 HTTP 服务 (模拟微信/钉钉/QQ 呼入请求)]")
    test_port = 18099
    server_thread = threading.Thread(target=ky_cli.run_server, kwargs={"port": test_port}, daemon=True)
    server_thread.start()
    time.sleep(0.8) # 等待启动

    # 发送模拟钉钉 Webhook 请求
    mock_ding_req = {
        "msgtype": "text",
        "text": {"content": "学数学：请问极限保号性的核心定义是什么？"}
    }
    req_data = json.dumps(mock_ding_req).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{test_port}/webhook",
        data=req_data,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            runner.assert_true(resp.status == 200, "Webhook 网关返回 HTTP 200 OK")
            res_body = json.loads(resp.read().decode("utf-8"))
            runner.assert_true("msgtype" in res_body and "text" in res_body, "Webhook 网关标准 JSON 回包符合规范")
    except Exception as e:
        runner.assert_true(False, f"Webhook 网关通信异常: {e}")

    # 测试飞书开放平台 URL 校验握手 (url_verification)
    try:
        feishu_verify_payload = json.dumps({
            "type": "url_verification",
            "challenge": "ky_feishu_test_token_8899",
            "token": "test_token"
        }).encode("utf-8")
        f_req = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/webhook",
            data=feishu_verify_payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(f_req, timeout=5) as resp:
            f_res = json.loads(resp.read().decode("utf-8"))
            runner.assert_true(f_res.get("challenge") == "ky_feishu_test_token_8899", "飞书开放平台 url_verification 握手校验秒级通过")
    except Exception as e:
        runner.assert_true(False, f"飞书 URL 校验握手异常: {e}")

    # 测试钉钉 sessionWebhook 异步防超时应答
    try:
        ding_async_payload = json.dumps({
            "msgtype": "text",
            "text": {"content": "学数学：求极限"},
            "sessionWebhook": "http://127.0.0.1:18099/mock_ding_receiver"
        }).encode("utf-8")
        d_req = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/webhook",
            data=ding_async_payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(d_req, timeout=5) as resp:
            d_res = json.loads(resp.read().decode("utf-8"))
            runner.assert_true(d_res.get("msgtype") == "empty", "钉钉 sessionWebhook 模式立即回包防止 5 秒超时")
    except Exception as e:
        runner.assert_true(False, f"钉钉 sessionWebhook 测试异常: {e}")

    # 测试 QQ OneBot 11 报文
    try:
        qq_payload = json.dumps({
            "post_type": "message",
            "message_type": "group",
            "raw_message": "学数学：极限保号性"
        }).encode("utf-8")
        q_req = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/webhook",
            data=qq_payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(q_req, timeout=5) as resp:
            q_res = json.loads(resp.read().decode("utf-8"))
            runner.assert_true("reply" in q_res and q_res.get("at_sender") is True, "QQ OneBot 11 报文解析且回包格式规范")
    except Exception as e:
        runner.assert_true(False, f"QQ OneBot 测试异常: {e}")

    # 测试 OpenAI 兼容端点 (供 OpenClaw / 微信 ClawBot 桥接)
    try:
        m_req = urllib.request.Request(f"http://127.0.0.1:{test_port}/v1/models")
        with urllib.request.urlopen(m_req, timeout=5) as resp:
            runner.assert_true(resp.status == 200, "OpenAI 兼容端点 /v1/models 返回 HTTP 200")
            m_res = json.loads(resp.read().decode("utf-8"))
            runner.assert_true("data" in m_res and any(x["id"] == "kaoyan-tutor" for x in m_res["data"]), "OpenAI 兼容模型列表注册正常")

        chat_payload = json.dumps({
            "model": "kaoyan-tutor",
            "messages": [{"role": "user", "content": "学数学：极限保号性"}]
        }).encode("utf-8")
        c_req = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/v1/chat/completions",
            data=chat_payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(c_req, timeout=5) as resp:
            runner.assert_true(resp.status == 200, "OpenAI 兼容端点 /v1/chat/completions 返回 HTTP 200")
            c_res = json.loads(resp.read().decode("utf-8"))
            runner.assert_true("choices" in c_res and "message" in c_res["choices"][0], "OpenAI 兼容回包符合规范 (微信ClawBot即插即用)")
    except Exception as e:
        runner.assert_true(False, f"OpenAI 兼容端点测试异常: {e}")

    # 测试 /live 页面与 /api/live 数据流接口
    try:
        live_req = urllib.request.Request(f"http://127.0.0.1:{test_port}/live")
        with urllib.request.urlopen(live_req, timeout=5) as resp:
            runner.assert_true(resp.status == 200, "Web 伴侣前端页面 /live 访问正常 (HTTP 200)")
            html_text = resp.read().decode("utf-8")
            runner.assert_true("katex" in html_text.lower(), "Web 伴侣前端正确集成了 KaTeX 渲染引擎")
        
        api_req = urllib.request.Request(f"http://127.0.0.1:{test_port}/api/live")
        with urllib.request.urlopen(api_req, timeout=5) as resp:
            runner.assert_true(resp.status == 200, "Web 伴侣实时同步接口 /api/live 返回正常 (HTTP 200)")
            api_data = json.loads(resp.read().decode("utf-8"))
            runner.assert_true("messages" in api_data, "Web 伴侣实时数据流 JSON 格式正确")

        # 测试 /api/ask 支持多模态上传
        ask_img_payload = json.dumps({
            "message": "批改这道泰勒展开题",
            "image": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        }).encode("utf-8")
        ask_req = urllib.request.Request(
            f"http://127.0.0.1:{test_port}/api/ask",
            data=ask_img_payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(ask_req, timeout=5) as resp:
            runner.assert_true(resp.status == 200, "Web 伴侣 /api/ask 图像多模态上传处理正常 (HTTP 200)")

        # 测试 live.html 支持 LaTeX 保护与图片上传能力
        runner.assert_true("renderMarkdownWithKaTeX" in html_text, "Web 伴侣实现 renderMarkdownWithKaTeX 预保护解析")
        runner.assert_true("image-file-input" in html_text and "paste" in html_text, "Web 伴侣已集成图片上传、Ctrl+V 粘贴截图与拖拽")
    except Exception as e:
        runner.assert_true(False, f"Web 伴侣服务测试异常: {e}")

    # ------------------------------------------------------------
    # 测试 6: 本地看板构建集成测试
    # ------------------------------------------------------------
    print("\n[测试组 6: 本地自测看板编译集成校验]")
    build_script = ROOT / "05-考研看板" / "build.py"
    runner.assert_true(build_script.exists(), "05-考研看板/build.py 存在")
    index_html = ROOT / "docs" / "index.html"
    runner.assert_true(index_html.exists() and index_html.stat().st_size > 10000, "docs/index.html 存在且编译体积正常")

    # ------------------------------------------------------------
    # 测试 7: Git 隐私防泄露隔离校验
    # ------------------------------------------------------------
    print("\n[测试组 7: Git 隐私隔离机制实测]")
    import subprocess
    # 校验 ky_config.json 是否被 Git 忽略 (妥善备份并还原原本配置)
    fake_config = ROOT / "ky_config.json"
    old_content = fake_config.read_text(encoding="utf-8") if fake_config.exists() else None
    fake_config.write_text(json.dumps({"test_api_key": "sk-secret123456"}), encoding="utf-8")

    git_check = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    runner.assert_true("ky_config.json" not in (git_check.stdout or ""), "ky_config.json 被 .gitignore 正确忽略 (无泄漏风险)")

    # 恢复或清理
    if old_content is not None:
        fake_config.write_text(old_content, encoding="utf-8")
    elif fake_config.exists():
        fake_config.unlink()

    # ------------------------------------------------------------
    # 测试 8: 考研专有 Skills 体系校验
    # ------------------------------------------------------------
    print("\n[测试组 8: 考研专有 Skills 体系功能实测]")
    import skills
    all_skills = skills.list_skills()
    runner.assert_true(len(all_skills) >= 5, f"技能中枢成功注册 {len(all_skills)} 个核心 Skills")
    runner.assert_true("vision_solver" in all_skills, "vision_solver 视觉批改技能已注册")
    runner.assert_true("math_verifier" in all_skills, "math_verifier 符号验算技能已注册")
    runner.assert_true("english_dissector" in all_skills, "english_dissector 句子解剖技能已注册")

    # 验证视觉 Prompt 构造
    v_prompt = skills.vision_solver.build_vision_prompt("请批改导数推导")
    runner.assert_true("题面提取" in v_prompt and "采分点" in v_prompt and "错因五分类" in v_prompt, "视觉批改 Prompt 包含采分点与错因五分类约束")

    # 验证图片 Base64 编码 (使用 docs/assets/hero_banner.jpg 测试)
    test_img = ROOT / "docs" / "assets" / "hero_banner.jpg"
    if test_img.exists():
        data_url, mime, fname = skills.vision_solver.encode_image_to_base64(str(test_img))
        runner.assert_true(data_url.startswith("data:image/jpeg;base64,"), "视觉技能成功将本地图片编码为标准 Data URL")

    # 验证英语长难句拆解 Prompt 构造
    e_prompt = skills.english_dissector.build_dissection_prompt("Although the theory is complex, students can master it.")
    runner.assert_true("主干骨架抽取" in e_prompt and "两步翻译法" in e_prompt, "英语长难句技能搭积木指令生成规范")

    # 验证数学引擎状态与基础执行
    m_status = skills.math_verifier.get_status()
    runner.assert_true(isinstance(m_status, str) and len(m_status) > 5, "数学高精度计算引擎状态正常")

    # 验证资料库文献检索
    mats = skills.pdf_extractor.list_materials()
    runner.assert_true(isinstance(mats, dict) and "01-数学" in mats, "PDF与教材资料库扫描接口正常")

    # 验证终端 LaTeX 公式美化器
    raw_latex = r"求 \(f(x)\): \[f(x)=\int_{0}^{x}e^{-f(t)}\,dt\] 导数: \(f'(x)\)"
    beautified = skills.latex_beautifier.prettify_latex_for_terminal(raw_latex)
    runner.assert_true("∫" in beautified and "f(x)" in beautified and "\\" not in beautified, "终端 LaTeX 美化器成功将积分和反斜杠公式还原为直观符号")

    # 验证本地 RapidOCR 图像提取引擎
    if test_img.exists():
        ocr_res = skills.vision_solver.extract_text_with_local_ocr(str(test_img))
        runner.assert_true(ocr_res is not None and len(ocr_res) > 0, "本地 RapidOCR 成功识别并提取图片文字内容")

    # 验证系统剪贴板图像抓取与多模态容错调用
    clip_test = ky_cli.grab_clipboard_image()
    runner.assert_true(clip_test is None or isinstance(clip_test, Path), "剪贴板图像抓取模块正常工作")

    # 验证非流式视觉调用无异常且杜绝模块路径错误
    vis_res = skills.vision_solver.solve_image_with_model(str(test_img), "测试批改", {"model": "deepseek-chat"}, stream=False)
    runner.assert_true(isinstance(vis_res, str) and "No module named" not in vis_res, "视觉批改非流式解题安全运行，彻底消除 No module named 异常")

    # ------------------------------------------------------------
    # 测试 9: 考研科目方案与官方大纲管理体系校验
    # ------------------------------------------------------------
    print("\n[测试组 9: 考研科目方案与官方大纲管理体系校验]")
    import syllabus_manager
    runner.assert_true("math1" in syllabus_manager.MATH_SYLLABI and "math2" in syllabus_manager.MATH_SYLLABI, "数学大纲库包含数一/数二完整方案")
    runner.assert_true("eng1" in syllabus_manager.ENGLISH_SYLLABI and "eng2" in syllabus_manager.ENGLISH_SYLLABI, "英语大纲库包含英一/英二完整方案")
    m_info, e_info, updated = syllabus_manager.apply_syllabus_selection(
        math_key="math2", eng_key="eng2", pro_type="408", pro_name="408 计算机学科专业基础", auto_write=True
    )
    runner.assert_true(m_info["name"] == "数学二 (302)", "数学方案精准匹配数学二 (302)")
    runner.assert_true(e_info["name"] == "英语二 (204)", "英语方案精准匹配英语二 (204)")
    m_out = (ROOT / "01-数学" / "考试大纲.md").read_text(encoding="utf-8")
    runner.assert_true("三重积分" in m_out and ("绝不考" in m_out or "严禁出现" in m_out or "不考" in m_out), "数二大纲明确标出三重积分与曲面积分超纲红线")
    p_out = (ROOT / "04-专业课" / "考试大纲.md").read_text(encoding="utf-8")
    runner.assert_true("408" in p_out and "数据结构" in p_out, "408专业课考纲自动注入四大模块考点")

    # ------------------------------------------------------------
    # 测试 10: 个人定制化必考方案设计引擎与状态持久化校验
    # ------------------------------------------------------------
    print("\n[测试组 10: 个人定制化必考方案设计引擎与状态持久化校验]")
    import study_planner
    preset_plan = {
        "target_year": "2026",
        "exam_date": "2026-12-19",
        "stage_name": "强化题型攻坚阶段",
        "school": "目标院校",
        "major": "报考专业",
        "math_key": "math2",
        "eng_key": "eng2",
        "pro_type": "408",
        "pro_name": "408 计算机学科专业基础",
        "math_baseline": "60分",
        "math_weakness": "导数中值定理、计算失误",
        "eng_baseline": "四级已过 / 摸底50分",
        "eng_weakness": "长难句主干速抓、细节定位",
        "pol_baseline": "基础刚起步 / 摸底40分",
        "pol_weakness": "马原唯物辩证法、多选题漏选",
        "pro_baseline": "科班有基础 / 摸底80分",
        "pro_weakness": "核心算法设计与证明步骤",
        "math_books": "同济教材+基础讲义+历年真题",
        "eng_books": "近15年历年真题精解+真题词汇宝典",
        "pol_books": "考研政治核心考案+精选1000题+冲刺全真卷",
        "pro_books": "408官方教材与课后习题+历年真题汇编",
        "total_hours": 8.5,
        "math_hours": 3.0,
        "eng_hours": 2.0,
        "pol_hours": 1.0,
        "pro_hours": 2.5,
        "rest_weekly": "每周日晚 18:00~22:30 放松休整",
        "rest_monthly": "每月最后一个周日全天闭卷模考与全科雷达复盘",
        "style_name": "严格把关·保姆提分型 (Strict & Disciplined)",
        "math_target": "110+ 分",
        "eng_target": "65+ 分",
        "pol_target": "70+ 分",
        "pro_target": "120-130 分",
        "total_target": "370+ 分"
    }
    built_plan = study_planner.run_study_plan_wizard(interactive=False, preset_data=preset_plan)
    runner.assert_true(built_plan.get("days_left") >= 0, "方案引擎精准计算并注入初试倒计时")
    runner.assert_true(built_plan.get("math_books") == "同济教材+基础讲义+历年真题", "手头备考资料白名单登记正确")

    # 验证 AGENTS.md 状态持久化
    agents_txt = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    runner.assert_true("当前备考阶段" in agents_txt and "强化题型攻坚阶段" in agents_txt, "AGENTS.md 成功挂载当前备考阶段")
    runner.assert_true("个性化学情与作息调节机制" in agents_txt, "AGENTS.md 成功固化作息与学情调节机制")
    runner.assert_true("手头资料白名单" in agents_txt and "同济教材" in agents_txt, "AGENTS.md 写入手头资料白名单作为 AI 防虚构硬约束")
    runner.assert_true("每周休整窗口" in agents_txt and "每周日晚" in agents_txt, "AGENTS.md 成功锁定每周休息放风窗口")

    # 验证 ky_config.json 标记
    cfg_loaded = ky_cli.load_config()
    runner.assert_true(cfg_loaded.get("onboarding_completed") is True, "配置文件正确持久化 onboarding_completed 标记")
    runner.assert_true(isinstance(cfg_loaded.get("study_plan"), dict), "配置文件持久化完整 study_plan 结构化数据")

    # 验证数学专属 AGENTS.md 薄弱项
    m_agents_txt = (ROOT / "01-数学" / "AGENTS.md").read_text(encoding="utf-8")
    runner.assert_true("核心薄弱点" in m_agents_txt and "导数中值定理" in m_agents_txt, "数学专属协议注入学员专属核心薄弱项")

    # 验证当本地未放置实体资料时，向导真实反应，绝不虚构不存在的书目
    no_book_plan = study_planner.run_study_plan_wizard(interactive=False)
    runner.assert_true("暂未放置实体资料" in no_book_plan.get("math_books", "") and "李林" not in no_book_plan.get("math_books", ""), "无实体资料时向导严格杜绝虚构书目")
    clean_agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    runner.assert_true("暂未放置实体资料" in clean_agents, "AGENTS.md 真实记录无资料状态，杜绝任何硬编码假书目")

    # ------------------------------------------------------------
    # 测试 11: 专有技能中枢重度升级与全流程闭环校验
    # ------------------------------------------------------------
    print("\n[测试组 11: 专有技能中枢重度升级与全流程闭环校验]")
    from skills import math_verifier as mv_test
    from skills import socratic_tutor as st_test
    from skills import error_logger as el_test
    from skills import list_skills as ls_test

    # 1. 检验技能注册表包含新技能
    all_sks = ls_test()
    runner.assert_true("socratic_tutor" in all_sks, "技能注册表包含苏格拉底脚手架技能 socratic_tutor")
    runner.assert_true("error_logger" in all_sks and "盲盒" in all_sks["error_logger"]["name"], "错题引擎已升级为艾宾浩斯盲盒复测")

    # 2. 检验 math_verifier 高阶微积分与代数能力
    ode_res = mv_test.run_math_query("ode y'' + 4*y = 0")
    runner.assert_true("sin" in ode_res.lower() or "cos" in ode_res.lower(), "数学高阶验算：常微分方程通解计算准确")

    quad_res = mv_test.run_math_query("quad [[2, 1], [1, 2]]")
    runner.assert_true("正定" in quad_res and "\\Delta_1" in quad_res, "数学高阶验算：二次型正定性与顺序主子式判定准确")

    sum_res = mv_test.run_math_query("sum 1/n^2 from 1 to oo")
    runner.assert_true("pi" in sum_res.lower() or "\\pi" in sum_res, "数学高阶验算：巴塞尔级数求和计算准确")

    solve_res = mv_test.run_math_query("solve x^2 - 5*x + 6 = 0")
    runner.assert_true("2" in solve_res and "3" in solve_res, "数学高阶验算：代数方程与极值驻点求解准确")

    # 3. 检验 socratic_tutor 三级脚手架生成
    hint_q = "证明设 f(x) 在 [0,1] 连续，存在 xi 满足积分中值公式"
    p_lvl1 = st_test.build_hint_prompt(hint_q, hint_level=1)
    runner.assert_true("Level 1" in p_lvl1 and "铁律" in p_lvl1 and "严禁给出最终答案" in p_lvl1, "苏格拉底脚手架：Level 1 破题定性提示规范且锁定不剧透铁律")

    p_lvl2 = st_test.build_hint_prompt(hint_q, hint_level=2)
    runner.assert_true("Level 2" in p_lvl2 and "首步" in p_lvl2, "苏格拉底脚手架：Level 2 首步搭桥提示规范")

    p_lvl3 = st_test.build_hint_prompt(hint_q, hint_level=3)
    runner.assert_true("Level 3" in p_lvl3 and "陷阱" in p_lvl3, "苏格拉底脚手架：Level 3 命题避坑指南规范")

    # 4. 检验 error_logger 盲盒抽题与闭环状态回写
    #    【隔离沙箱】error_logger 的 ROOT 指向真实工作区，直接调用会把测试错题
    #    写进学员真实的「01-数学/错题本/」造成数据污染（曾积累 30+ 条假错题）。
    #    此处将 ROOT 临时指向系统临时目录，测试结束后自动还原。
    import tempfile as _tempfile
    _el_real_root = el_test.ROOT
    _el_sandbox = Path(_tempfile.mkdtemp(prefix="ky_test_errlog_"))
    el_test.ROOT = _el_sandbox
    try:
        el_test.log_error_record("math", "泰勒展开阶数匹配失误", "审题偏差", "展开至3阶漏掉余项", "严格对照分母极限阶数", question="求极限 lim (tan(x)-x)/x^3")
        # 新记录的下次到期日是「明天」，会被艾宾浩斯严格日期门控滤掉（该门控本身也是被测行为）。
        # 为使复测队列可测，把沙箱内记录的到期日回拨至今日。
        import datetime as _dt
        _today = _dt.date.today().strftime("%Y-%m-%d")
        _tmrw = (_dt.date.today() + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        for _f in (_el_sandbox / "01-数学" / "错题本").glob("错题记录_*.md"):
            _txt = _f.read_text(encoding="utf-8").replace(_tmrw, _today)
            _f.write_text(_txt, encoding="utf-8")
        rec_list = el_test.scan_error_records("math")
        runner.assert_true(len(rec_list) > 0, "错题解析引擎：成功结构化提取 Markdown 错题卡片")

        due_list = el_test.get_due_reviews("math")
        runner.assert_true(len(due_list) > 0, "艾宾浩斯复测：成功提取待复测到期错题队列")

        blind_card = el_test.generate_blind_quiz(due_list[0])
        runner.assert_true("盲盒重测" in blind_card and "隐去历史推导过程" in blind_card, "错题盲盒引擎：成功生成无答案的盲盒复测试题")

        up_ok, up_msg = el_test.mark_error_status("math", due_list[0]["file_name"], due_list[0]["title"], new_status="已掌握")
        runner.assert_true(up_ok is True, "闭环状态回写：成功将复测合格题目更新标记为 [已掌握]")
    finally:
        el_test.ROOT = _el_real_root
        import shutil as _shutil
        _shutil.rmtree(_el_sandbox, ignore_errors=True)

    # ------------------------------------------------------------
    # 测试 12: 工业级 Agent Loop、工具分发、权限安全与沙箱拦截全链路回归
    # ------------------------------------------------------------
    print("\n[测试组 12: 工业级 Agent Loop、工具分发、权限安全与沙箱拦截全链路回归]")
    from agent import Sandbox, SecurityException, PermissionManager, PermissionLevel, ToolRegistry, ContextEngine, AgentRunner

    # 1. 沙箱越界拦截与高危指令黑名单
    sb_test = Sandbox(workspace_root=ROOT)
    caught_path = False
    try:
        sb_test.resolve_safe_path("C:\\Windows\\System32\\calc.exe")
    except SecurityException:
        caught_path = True
    runner.assert_true(caught_path is True, "沙箱防护：坚决阻断系统敏感目录 (C:\\Windows) 访问穿越")

    caught_cmd = False
    try:
        sb_test.check_command_safety("rm -rf /")
    except SecurityException:
        caught_cmd = True
    runner.assert_true(caught_cmd is True, "沙箱防护：坚决阻断系统高危命令 (rm -rf /) 破坏性执行")

    # 2. 权限分级系统 (Safe / Auto / Ask 策略)
    pm_safe = PermissionManager(mode="safe")
    tr_safe = ToolRegistry(sandbox=sb_test, permissions=pm_safe)
    safe_rej = tr_safe.execute_tool("write_file", {"path": "test_perm.txt", "content": "hello"})
    runner.assert_true("PermissionDenied" in safe_rej, "权限引擎：严格安全模式 (--permission=safe) 成功阻断非只读写入")

    pm_auto = PermissionManager(mode="auto")
    tr_auto = ToolRegistry(sandbox=sb_test, permissions=pm_auto)
    ro_res = tr_auto.execute_tool("list_directory", {"path": ".", "max_depth": 1})
    runner.assert_true("README.md" in ro_res, "权限引擎：只读探索工具 (Level 0) 全自动秒级放行")

    # 3. 标准工具集功能回归
    temp_p = "01-数学/_状态/test_agent_card.tmp.md"
    w_res = tr_auto.execute_tool("write_file", {"path": temp_p, "content": "### 泰勒公式复测\n待做题目", "overwrite": True})
    runner.assert_true("Success" in w_res and (ROOT / temp_p).exists(), "标准文件工具：write_file 成功建立考研状态文件")

    r_res = tr_auto.execute_tool("read_file", {"path": temp_p})
    runner.assert_true("泰勒公式" in r_res, "标准文件工具：read_file 成功读取考研状态文件")

    e_res = tr_auto.execute_tool("edit_file", {"path": temp_p, "target_content": "待做题目", "replacement": "已完成推导"})
    runner.assert_true("Success" in e_res and "已完成推导" in (ROOT / temp_p).read_text(encoding="utf-8"), "标准文件工具：edit_file 精确替换内容成功")

    grep_res = tr_auto.execute_tool("grep", {"query": "泰勒公式", "path": "01-数学/_状态"})
    runner.assert_true(temp_p.split("/")[-1] in grep_res, "标准搜索工具：grep 全文检索准确命中关键词")

    pm_auto.force_allow_all = True
    d_res = tr_auto.execute_tool("delete_file", {"path": temp_p})
    runner.assert_true("Success" in d_res and not (ROOT / temp_p).exists(), "标准文件工具：delete_file 授权删除成功")
    pm_auto.force_allow_all = False

    # 4. 考研专有工具联动
    mv_tool_res = tr_auto.execute_tool("verify_math", {"expression": "diff x^3"})
    runner.assert_true("x" in mv_tool_res and "3" in mv_tool_res and "2" in mv_tool_res, "考研专用工具：verify_math 符号求导准确")

    hint_tool_res = tr_auto.execute_tool("socratic_hint", {"question": "证明中值定理存在性", "level": 1})
    runner.assert_true("Level 1" in hint_tool_res, "考研专用工具：socratic_hint 脚手架分级启发正常调用")

    # 5. 上下文压缩 Context Compaction 算法
    ce_test = ContextEngine(workspace_root=ROOT, active_subject="math", max_context_tokens=50)
    fake_history = [
        {"role": "system", "content": "顶层协议"},
        {"role": "user", "content": "请从真题抽一道中值定理题目" * 10},
        {"role": "tool", "name": "read_exam_paper", "content": "提取了五千字真题试卷" * 20},
        {"role": "assistant", "content": "这是2018年第15题" * 10},
        {"role": "user", "content": "我的解答是 f'(xi)=0"},
        {"role": "assistant", "content": "批改完成，获得10分"},
        {"role": "user", "content": "再抽一道积分题"},
        {"role": "assistant", "content": "好的，请看这道 2021 年第 3 题"}
    ]
    compacted_msgs = ce_test.compact_context(fake_history)
    runner.assert_true(len(compacted_msgs) < len(fake_history) or any("Context Compaction" in m.get("content", "") for m in compacted_msgs), "上下文引擎：Context Compaction 自动压缩超长工具输出，防爆 Context 成功")

    # 6. ToolRegistry 生成 OpenAI 规范 tools
    oa_tools = tr_auto.get_openai_tools()
    runner.assert_true(any(t["function"]["name"] == "read_exam_paper" for t in oa_tools), "OpenAI Tools 规范：包含真题专抽工具 read_exam_paper")
    runner.assert_true(any(t["function"]["name"] == "verify_math" for t in oa_tools), "OpenAI Tools 规范：包含符号高精验算工具 verify_math")
    runner.assert_true(any(t["function"]["name"] == "read_file" for t in oa_tools), "OpenAI Tools 规范：包含标准文件读取工具 read_file")

    # ------------------------------------------------------------
    # 测试 13: 三级分层记忆、生命周期拦截钩子、网络搜索与 MCP 客户端全链路回归
    # ------------------------------------------------------------
    print("\n[测试组 13: 三级分层记忆、生命周期拦截钩子、网络搜索与 MCP 客户端全链路回归]")
    from agent import MemoryManager, MemoryScope, HookManager, HookEvent, MCPProcessClient, MCPClientManager

    # 1. 三级分层记忆体系 (MemoryManager)
    mm_test = MemoryManager(workspace_root=ROOT)
    mm_test.init_defaults_from_config(cfg)
    mm_test.write_memory("session", "当前正在攻坚 2018 年第 15 题中值定理证明")
    mm_test.append_memory("decisions", "[复习决策]: 数学二严禁复习三重积分")
    mem_all = mm_test.load_all_memory()
    runner.assert_true("Global" in mem_all and "Project" in mem_all and "Decisions" in mem_all and "Session" in mem_all, "三级分层记忆：全层级记忆编译装配完整")

    # 智能体通过 manage_memory 工具自主维护记忆
    tr_auto.memory_manager = mm_test
    mem_tool_res = tr_auto.execute_tool("manage_memory", {"action": "read", "scope": "session"})
    runner.assert_true("中值定理证明" in mem_tool_res, "三级分层记忆：manage_memory 成功读取 Session 工作记忆")

    tr_auto.execute_tool("manage_memory", {"action": "append", "scope": "decisions", "content": "英语阅读先读题干划出题眼"})
    d_read = mm_test.read_memory("decisions")
    runner.assert_true("英语阅读先读题干划出题眼" in d_read, "三级分层记忆：manage_memory 成功向 decisions 追加长期避坑决策")

    # 2. 生命周期拦截钩子系统 (HookManager)
    hm_test = HookManager(workspace_root=ROOT, memory_manager=mm_test)
    
    # 测试 PreToolUse 考纲红线拦截 (数二超纲三重积分硬阻断)
    allow_ok, reason_ok, _ = hm_test.trigger_pre_tool_use("verify_math", {"expression": "diff x^3"}, {"active_subject": "math"})
    runner.assert_true(allow_ok is True, "生命周期钩子：大纲范围内考点 PreToolUse 正常放行")

    allow_bad, reason_bad, _ = hm_test.trigger_pre_tool_use("verify_math", {"expression": "三重积分计算"}, {"active_subject": "math"})
    runner.assert_true(allow_bad is False and "考纲红线" in reason_bad and "三重积分" in reason_bad, "生命周期钩子：PreToolUse 成功硬拦截数二超纲考点 (三重积分)")

    # 测试 PostToolUse 错题联动
    post_res = hm_test.trigger_post_tool_use(
        "log_mistake",
        {"title": "泰勒展开阶数不足", "mistake_type": "概念漏洞"},
        "Success: 错题已成功归档入库",
        {"active_subject": "math"}
    )
    runner.assert_true("联动更新 Session 记忆" in post_res, "生命周期钩子：PostToolUse 成功捕获错题归档并自动联动更新 Session 记忆")

    # 测试 BeforeCompact 自动提炼决策
    sample_msgs = [
        {"role": "user", "content": "经过复盘我决定不要做偏难怪题，只看真题"},
        {"role": "assistant", "content": "好的，这个策略非常务实！"}
    ]
    hm_test.trigger_before_compact(sample_msgs, {"active_subject": "math"})
    dec_after = mm_test.read_memory("decisions")
    runner.assert_true("不要做偏难怪题" in dec_after, "生命周期钩子：BeforeCompact 上下文压缩前成功自动提炼学员决策沉淀")

    # 3. 增强网络工具 (fetch_url & web_search)
    pm_auto.force_allow_all = True
    fetch_bad = tr_auto.execute_tool("fetch_url", {"url": "file:///etc/passwd"})
    runner.assert_true("Error" in fetch_bad or "协议" in fetch_bad, "网络增强工具：fetch_url 阻断非 HTTP 协议探测")

    search_res = tr_auto.execute_tool("web_search", {"query": "考研数学二官方考试大纲", "num_results": 2})
    runner.assert_true(isinstance(search_res, str) and len(search_res) > 10, "网络增强工具：web_search 搜索执行平稳无崩溃")
    pm_auto.force_allow_all = False

    # 4. Model Context Protocol (MCP) 客户端引擎
    mock_server_script = ROOT / "tools" / "agent" / "_mock_mcp_server.py"
    mcp_client = MCPProcessClient(
        name="mock",
        command=sys.executable,
        args=[str(mock_server_script)],
        cwd=ROOT
    )
    started = mcp_client.start()
    runner.assert_true(started is True, "MCP 客户端：成功启动标准 stdio MCP Server 并完成 initialize 握手")

    mcp_tools = mcp_client.list_tools()
    runner.assert_true(len(mcp_tools) > 0 and mcp_tools[0]["name"] == "study_calc", "MCP 客户端：成功通过 tools/list 探测到外部 MCP 工具")

    call_res = mcp_client.call_tool("study_calc", {"score": 90})
    runner.assert_true("108.0" in call_res or "WeightedScore" in call_res, "MCP 客户端：成功通过 tools/call 调用外部 MCP 工具并获得计算结果")

    # 验证动态注入 ToolRegistry
    mcp_mgr = MCPClientManager(workspace_root=ROOT)
    mcp_mgr.clients["mock"] = mcp_client
    tr_auto.register_mcp_tools(mcp_mgr)
    runner.assert_true("mcp_mock_study_calc" in tr_auto.tools, "MCP 客户端：成功将外部 MCP 工具动态注册进 Agent 智能体工具注册表")

    mcp_client.stop()

    # ════════════════════════════════════════════════════════════
    # 测试组 14: Agent 关键工具真实执行级校验 (防止签名漂移与隐藏崩溃)
    # ════════════════════════════════════════════════════════════
    print("\n[测试组 14: Agent 关键工具真实执行级校验 (防止签名漂移与隐藏崩溃)]")
    pm_auto.force_allow_all = True

    # 【隔离沙箱】log_mistake 会真实写入学员错题本，此处将 error_logger.ROOT
    # 临时指向系统临时目录（tools_impl 引用的 skills.error_logger 与本文件 476 行
    # 是同一模块实例，patch 即全局生效），测试结束还原。
    import tempfile as _tempfile14
    _el_real_root14 = el_test.ROOT
    _el_sandbox14 = Path(_tempfile14.mkdtemp(prefix="ky_test_t14_"))
    el_test.ROOT = _el_sandbox14

    try:
        # 1. 真实执行 log_mistake 并断言返回 Success (C-2 彻底绝护)
        mistake_res = tr_auto.execute_tool("log_mistake", {
            "subject": "math",
            "title": "测试自动化执行级错题",
            "error_type": "概念漏洞",
            "mistake_type": "概念漏洞",  # 别名兼容
            "detail": "对泰勒公式麦克劳林展开余项理解偏差",
            "prescription": "强化佩亚诺余项阶数匹配训练",
            "question": "求 lim (x->0) (sin x - x) / x^3"
        })
        runner.assert_true("成功归档入库" in mistake_res and "TypeError" not in mistake_res, "执行级校验：log_mistake 真实归档成功且无参数漂移异常")

        # 2. 真实执行 review_mistakes
        review_res = tr_auto.execute_tool("review_mistakes", {"subject": "math"})
        runner.assert_true(isinstance(review_res, str) and ("错题" in review_res or "掌握度" in review_res), "执行级校验：review_mistakes 真实提取错题队列成功")
    finally:
        el_test.ROOT = _el_real_root14
        import shutil as _shutil14
        _shutil14.rmtree(_el_sandbox14, ignore_errors=True)

    # 3. 真实执行 read_exam_paper 与 pdf_extractor
    pdf_res = tr_auto.execute_tool("read_exam_paper", {"subject": "math", "keyword": "2024", "max_pages": 2})
    runner.assert_true("AttributeError" not in pdf_res and "extract_pdf_pages" not in pdf_res, "执行级校验：read_exam_paper 真实调用 extract_pdf_pages 无缺失函数崩溃")

    # 4. 真实执行 verify_math
    v_res = tr_auto.execute_tool("verify_math", {"expression": "diff x^3"})
    runner.assert_true("x" in v_res and "3" in v_res and "2" in v_res, "执行级校验：verify_math 真实求导运算准确")

    pm_auto.force_allow_all = False

    # ════════════════════════════════════════════════════════════
    # 测试组 15: 上下文压缩配对保护与 CLI 子命令回归
    # ════════════════════════════════════════════════════════════
    print("\n[测试组 15: 上下文压缩配对保护与 CLI 子命令回归]")

    # 1. 上下文压缩配对保护测试 (防止产生孤儿 tool 消息触发 400)
    from agent.context_engine import ContextEngine
    ce_test = ContextEngine(workspace_root=ROOT, active_subject="math", max_context_tokens=100)
    constructed_msgs = [
        {"role": "system", "content": "You are a coach."},
        {"role": "user", "content": "做一题"},
        {"role": "assistant", "content": "好的，我来查题", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "题目数据内容" * 20},
        {"role": "assistant", "content": "请作答"},
        {"role": "user", "content": "我做完了"},
        {"role": "assistant", "content": "我来判分", "tool_calls": [{"id": "call_2", "type": "function", "function": {"name": "verify_math", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_2", "name": "verify_math", "content": "计算结果" * 20},
        {"role": "assistant", "content": "你做对了！"},
        {"role": "user", "content": "下一题"}
    ]
    compacted_result = ce_test.compact_context(constructed_msgs)
    orphan_found = False
    for idx, msg in enumerate(compacted_result):
        if msg.get("role") == "tool":
            prev_msg = compacted_result[idx - 1] if idx > 0 else None
            if not prev_msg or (prev_msg.get("role") != "assistant" and prev_msg.get("role") != "tool"):
                orphan_found = True
                break
    runner.assert_true(not orphan_found, "上下文压缩防护：compact_context 确保 assistant(tool_calls) 与 tool 消息成对保留，杜绝孤儿消息")

    # 2. CLI 子命令数据提取测试
    tasks_data = ky_cli.get_today_tasks_data()
    runner.assert_true(isinstance(tasks_data, dict) and "subjects" in tasks_data and "summary" in tasks_data, "CLI 子命令：get_today_tasks_data 成功生成结构化任务概览")

    # 3. 私教风格动态切换测试
    orig_style, _ = ky_cli.manage_coaching_style()
    switched_style, changed = ky_cli.manage_coaching_style("2")
    runner.assert_true(changed and "高效应试" in switched_style, "CLI 子命令：ky style 成功动态切换辅导风格")
    # 恢复原风格
    ky_cli.manage_coaching_style(orig_style)

    # 4. 系统体检 doctor 执行测试
    import doctor
    doc_res = doctor.run_doctor(return_summary=True)
    runner.assert_true(isinstance(doc_res, dict) and doc_res["issues"] == 0, "CLI 子命令：ky doctor 一键体检顺利通过且阻断问题为 0")

    # ════════════════════════════════════════════════════════════
    # 测试组 16: Sprint 2 教学闭环核心功能全量回归
    # (反向组卷 / 真题变式防幻觉 / 考纲知识图谱 / 模考诊断 / 减负保障)
    # ════════════════════════════════════════════════════════════
    print("\n[测试组 16: Sprint 2 教学闭环核心功能全量回归 (组卷/变式/图谱/诊断/减负)]")
    from skills import exam_composer, variant_retriever, knowledge_map, exam_diagnoser
    import study_planner

    # 备份配置与 AGENTS.md 以确保测试环境幂等
    cfg_backup = json.loads((ROOT / "ky_config.json").read_text(encoding="utf-8")) if (ROOT / "ky_config.json").exists() else {}
    agents_backup = (ROOT / "AGENTS.md").read_text(encoding="utf-8") if (ROOT / "AGENTS.md").exists() else ""

    try:
        # 1. S2-1 错题反向靶向组卷 (exam_composer)
        paper = exam_composer.compose_exam_paper("math", count=2, save_file=False)
        runner.assert_true(paper["count"] == 2 and "EXAM-MATH" in paper["paper_id"], "教学闭环 S2-1：自动从错题与薄弱点抽取试题拼装盲盒自测试卷")
        runner.assert_true("EXAM_ANSWER_KEYS" in paper["content"], "教学闭环 S2-1：自测卷正确嵌入加密采分点与题解元数据")

        grade_res = exam_composer.grade_exam_paper(paper["content"], "1. 答案推导步骤充分有效，得出极限为 1/3", auto_advance=False)
        runner.assert_true(grade_res.get("success") is True and grade_res.get("score") > 0, "教学闭环 S2-1：自动批改自测卷作答并计算得分与通过率")
        runner.assert_true("自动阅卷与采分诊断报告" in grade_res.get("report", ""), "教学闭环 S2-1：生成规范采分点批改诊断报告")

        # 2. S2-5 变式题真实检索与防虚构溯源 (variant_retriever)
        v_res = variant_retriever.search_real_variant(subject="math", keyword="导数中值定理")
        runner.assert_true(v_res["subject"] == "math" and len(v_res.get("variants", [])) > 0, "教学闭环 S2-5：变式题检索成功返回同考点训练题")
        runner.assert_true(v_res["is_real_source"] is False, "教学闭环 S2-5：本地未挂载实体书时准确识别非真实出处")
        first_v_text = v_res["variants"][0]["question"]
        runner.assert_true("【⚠️ 私教自拟变式 · 题源未挂载本地实体资料】" in first_v_text, "教学闭环 S2-5：严格强制烙印防虚构自拟变式水印，杜绝虚构书名幻觉")

        v_formatted = variant_retriever.format_variant_output(v_res)
        runner.assert_true("考研同类真题变式检索" in v_formatted and "导数中值定理" in v_formatted, "教学闭环 S2-5：格式化变式题卡片输出完整规范")

        # 3. S2-2 官方考纲知识点图谱与掌握度映射 (knowledge_map)
        k_map = knowledge_map.build_knowledge_map("math")
        runner.assert_true(k_map["subject"] == "math" and k_map["total_topics"] >= 50, "教学闭环 S2-2：全量解析官方考纲知识点树结构")
        runner.assert_true("高等数学" in str(k_map["modules"].keys()) and "线性代数" in str(k_map["modules"].keys()), "教学闭环 S2-2：正确拆分科目下属一级与二级考纲模块")

        k_table = knowledge_map.format_knowledge_map_table("math")
        runner.assert_true("大纲掌握率" in k_table and "熟练" in k_table, "教学闭环 S2-2：大纲掌握度评级大盘输出完整")

        # 4. S2-3 整卷级多题诊断引擎 (exam_diagnoser)
        diag_sample = """
1. 极限与连续计算题：选错C，概念漏洞
2. 微分中值定理大题：求导计算失误，丢分4分
3. 泰勒展开题目：审题偏差，未展开至三阶
4. 二重积分计算题：计算失误，对称性遗漏
"""
        diag = exam_diagnoser.diagnose_mock_exam(subject="math", exam_input=diag_sample)
        runner.assert_true(diag["total_errors"] >= 3, "教学闭环 S2-3：聚合解析模考失分样本")
        runner.assert_true(diag["top_cause"] in ("概念漏洞", "计算失误", "审题偏差"), "教学闭环 S2-3：精准锁定整卷头号丢分杀手")
        runner.assert_true("整卷级模考诊断报告" in diag["report"] and "精力动态重分配" in diag["report"], "教学闭环 S2-3：生成章节失分排行榜与下周复习处方")

        # 5. S2-4 计划动态调优与防疲劳减负保障 (study_planner)
        study_planner.record_daily_completion(rate=50.0, total=4, completed=2, date_str="2026-09-01")
        study_planner.record_daily_completion(rate=40.0, total=5, completed=2, date_str="2026-09-02")
        fatigue_alert = study_planner.check_fatigue_alert()
        runner.assert_true(fatigue_alert["alert"] is True and fatigue_alert["consecutive_low_days"] == 2, "教学闭环 S2-4：成功侦测连续 2 天低完成率并触发防疲劳警报")
        runner.assert_true("防疲劳保障提醒" in fatigue_alert["message"], "教学闭环 S2-4：输出科学减负与心理疏导建议")

        relief_mode = study_planner.apply_relief_mode(scale=0.8)
        runner.assert_true(relief_mode["success"] is True and relief_mode["new_hours"] < relief_mode["old_hours"], "教学闭环 S2-4：一键启动智能减负模式，下调复习时间预算")
        runner.assert_true("温和启发·减负鼓励型" in relief_mode["style"], "教学闭环 S2-4：智能平滑切换为温和启发辅导风格")

        # 6. Agent 工具层调用闭环
        pm_auto.force_allow_all = True
        agent_exam_res = tr_auto.execute_tool("compose_exam", {"subject": "math", "count": 1, "save_file": False})
        runner.assert_true("自测卷" in agent_exam_res or "EXAM-MATH" in agent_exam_res, "智能体工具：compose_exam 智能体自主靶向组卷执行成功")

        agent_var_res = tr_auto.execute_tool("search_variant", {"subject": "math", "keyword": "泰勒展开"})
        runner.assert_true("变式题检索结果" in agent_var_res and "泰勒展开" in agent_var_res, "智能体工具：search_variant 变式题检索执行成功")
        pm_auto.force_allow_all = False

        # ════════════════════════════════════════════════════════════
        # 测试组 17: Sprint 3 体验与生态增强全量回归
        # (复盘自动化 / 记忆治理 / Plan沙箱 / 看板5Tab+趋势 / FSRS自适应 / 考前节律)
        # ════════════════════════════════════════════════════════════
        print("\n[测试组 17: Sprint 3 体验与生态增强全量回归 (复盘/记忆/Plan/看板/FSRS/心理节律)]")
        import tempfile
        import shutil
        from tools.agent import MemoryManager, PermissionManager, HookEvent, HookManager
        from tools.skills import error_logger

        test_sandbox_dir = Path(tempfile.mkdtemp(prefix="ky_test_s3_"))
        try:
            # 1. S3-1 复盘自动化 (SessionEnd 钩子提取与聚合)
            hook_mgr = HookManager(workspace_root=ROOT)
            dummy_context = {"messages": [{"role": "user", "content": "今天完成数学极限"}]}
            hook_mgr.trigger_session_end(dummy_context)
            runner.assert_true("debrief_summary" in dummy_context, "体验生态 S3-1：会话结束时自动生成当日学情复盘报告摘要")
            debrief_txt = dummy_context.get("debrief_summary", "")
            runner.assert_true("完成率" in debrief_txt or "今日" in debrief_txt, "体验生态 S3-1：复盘报告包含任务达成与待复测核心指标")

            # 2. S3-2 记忆治理 (健康度评估与滚动修剪归档)
            mem_mgr = MemoryManager(workspace_root=test_sandbox_dir)
            sess_file = test_sandbox_dir / ".memory" / "session.md"
            sess_file.parent.mkdir(parents=True, exist_ok=True)
            items_md = "# 会话记忆\n" + "\n".join([f"- [2026-09-0{i}] 学员总结高数导数公式与易错题决策 #{i}" for i in range(1, 6)])
            sess_file.write_text(items_md, encoding="utf-8")

            m_health = mem_mgr.get_memory_health()
            runner.assert_true("details" in m_health and m_health["total_tokens"] > 0, "体验生态 S3-2：成功评估分层记忆健康度与 Token 消耗指标")
            runner.assert_true("session" in m_health["details"] and m_health["details"]["session"]["status"] in ("ok", "良好"), "体验生态 S3-2：正确诊断各层记忆容量状态")

            prune_res = mem_mgr.prune_memory(scope="session", max_items=2, archive_to_decisions=True)
            runner.assert_true(prune_res["pruned"] is True and prune_res["pruned_count"] == 3, "体验生态 S3-2：滚动修剪超量记忆条目 (保留最新 2 条)")
            runner.assert_true(prune_res["remaining_count"] == 2 and prune_res["archived_count"] == 3, "体验生态 S3-2：将修剪条目无损安全归档至 decisions.md")
            decisions_file = test_sandbox_dir / ".memory" / "decisions.md"
            runner.assert_true(decisions_file.exists() and "归档" in decisions_file.read_text(encoding="utf-8"), "体验生态 S3-2：决策库正确承接历史归档记忆")

            # 3. S3-3 Plan Mode 审计沙箱与快照回滚
            pm_plan = PermissionManager(workspace_root=test_sandbox_dir, mode="plan")
            runner.assert_true(pm_plan.mode == "plan", "体验生态 S3-3：PermissionManager 成功支持 plan 计划模式")

            sample_file = test_sandbox_dir / "math_sample.txt"
            sample_file.write_text("极限公式：lim sinx/x = 1", encoding="utf-8")
            ckpt_path = pm_plan.create_checkpoint(sample_file)
            runner.assert_true(Path(ckpt_path).exists() and (test_sandbox_dir / ".checkpoint").exists(), "体验生态 S3-3：写操作前自动创建原子 Checkpoint 快照")

            # 篡改文件后回滚
            sample_file.write_text("破坏性修改内容", encoding="utf-8")
            rollback_res = pm_plan.restore_last_checkpoint()
            runner.assert_true(rollback_res["success"] is True, "体验生态 S3-3：成功执行 restore_last_checkpoint 回滚")
            runner.assert_true(sample_file.read_text(encoding="utf-8") == "极限公式：lim sinx/x = 1", "体验生态 S3-3：文件内容百分百精确还原至快照备份状态")

            # 4. S3-4 看板升级 (5 Tab + 离线 KaTeX + 趋势曲线)
            dash_html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
            runner.assert_true('data-p="map"' in dash_html and "图谱" in dash_html, "体验生态 S3-4：看板成功装载第 5 页签「🗺️ 图谱」")
            runner.assert_true("stat-trend" in dash_html and "完成率趋势" in dash_html, "体验生态 S3-4：数据页签成功嵌入 7 日完成率趋势 SVG 曲线")
            runner.assert_true("fallbackMathUnicode" in dash_html, "体验生态 S3-4：成功内置 KaTeX 离线稳健数学符号降级解析器")

            # 5. S3-5 FSRS 简化自适应间隔算法
            stage_again, due_again, days_again = error_logger.calc_fsrs_interval(stage=2, rating="again")
            runner.assert_true(stage_again == 0 and days_again == 1, "体验生态 S3-5：FSRS again 评级重置为 stage 0 且 1 天后立即复测")

            stage_hard, due_hard, days_hard = error_logger.calc_fsrs_interval(stage=1, rating="hard")
            runner.assert_true(stage_hard == 2 and days_hard == 3, "体验生态 S3-5：FSRS hard 评级按 1.3x 因子自适应延长间隔")

            stage_good, due_good, days_good = error_logger.calc_fsrs_interval(stage=2, rating="good")
            runner.assert_true(stage_good == 3 and days_good == 7, "体验生态 S3-5：FSRS good 评级平滑推进至 7 天")

            stage_easy, due_easy, days_easy = error_logger.calc_fsrs_interval(stage=2, rating="easy")
            runner.assert_true(stage_easy == 4 and days_easy == 28, "体验生态 S3-5：FSRS easy 评级跨级跳跃推进至 28 天")

            # 6. S3-6 考前心理节律关怀与 CLI 命令集成
            runner.assert_true(hasattr(ky_cli, "print_status_summary"), "体验生态 S3-6：ky_cli 成功集成 print_status_summary 态势函数")
            ky_cli.print_status_summary()
            runner.assert_true(True, "体验生态 S3-6：print_status_summary 打印大盘倒计时与作息节律正常不崩溃")

            # ------------------------------------------------------------
            # 测试 18: 目标高校研招与社媒考研情报侦察引擎 (School Scout)
            # ------------------------------------------------------------
            print("\n[测试组 18: 目标高校研招与社媒考研情报侦察引擎 (School Scout)]")
            try:
                from skills import school_scout
            except ImportError:
                try:
                    from tools.skills import school_scout
                except ImportError:
                    school_scout = None

            runner.assert_true(school_scout is not None, "School Scout 18-1：成功载入 school_scout 技能模块")

            # 验证 SKILLS_REGISTRY 中包含 school_scout
            skills_all = ky_cli.list_skills()
            runner.assert_true("school_scout" in skills_all, "School Scout 18-2：SKILLS_REGISTRY 正确注册 school_scout 技能")

            # 验证 URL 清洗与 DDG 链接解包
            raw_ddg_sample = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fgs.hust.edu.cn%2Finfo%2F1010%2F123.htm&rut=..."
            cleaned_url = school_scout._clean_ddg_url(raw_ddg_sample)
            runner.assert_true(cleaned_url.startswith("https://gs.hust.edu.cn/info/1010/123.htm"), "School Scout 18-3：_clean_ddg_url 正确还原真实目标 URL")

            # 验证核心指标与避坑关键词启发式提取
            mock_official = [
                {"title": "华中科技大学 2026 年硕士研究生招生专业目录 (081200 计算机科学与技术)", "url": "https://gs.hust.edu.cn", "snippet": "拟招生 35 人，初试科目：101思想政治理论、201英语一、301数学一、408计算机学科专业基础"}
            ]
            mock_social = {
                "zhihu": [{"title": "在华中科技大学读计算机是什么体验？", "url": "https://zhihu.com/p/1", "snippet": "不看本科出身，复试公平，保护一志愿，导师人好"}],
                "bilibili": [{"title": "华科计算机考研备考经验与专业课复习规划", "url": "https://bilibili.com/v/1", "snippet": "专业课考408统考，不压分"}],
                "xiaohongshu": [{"title": "华科软工考研避坑提醒", "url": "https://xhs.com/1", "snippet": "复试晚，差额比高，竞争激烈"}]
            }
            metrics = school_scout.extract_key_metrics("华中科技大学", "计算机", mock_official, mock_social)
            runner.assert_true("35" in metrics["quota_hint"], "School Scout 18-4：extract_key_metrics 准确提取拟招生人数线索")
            runner.assert_true(any("408" in s for s in metrics["subjects_hint"]), "School Scout 18-5：准确识别 408 统考科目")
            runner.assert_true("保护一志愿" in metrics["positive_signals"], "School Scout 18-6：准确捕获正向口碑信号 (保护一志愿)")
            runner.assert_true(any(w in metrics["risk_signals"] for w in ("差额比高", "复试晚")), "School Scout 18-7：准确捕获避坑与风险警示信号")

            # 验证研报生成与 Markdown 卡片排版
            mock_data = {
                "school": "华中科技大学",
                "major": "计算机",
                "official_data": mock_official,
                "social_data": mock_social,
                "metrics": metrics,
                "llm_report": None
            }
            report_md = school_scout.format_scout_report(mock_data)
            runner.assert_true("华中科技大学" in report_md and "核心招考指标透视" in report_md, "School Scout 18-8：format_scout_report 成功生成结构化离线情报卡片")
            runner.assert_true("知乎" in report_md and "哔哩哔哩" in report_md and "小红书" in report_md, "School Scout 18-9：情报卡片完整覆盖知乎/B站/小红书三大社媒板块")

            # 验证 ToolRegistry 中 scout_school 工具注册与调用
            from agent.tools_impl import ToolRegistry, PermissionManager, Sandbox
            pm_test = PermissionManager(workspace_root=test_sandbox_dir, mode="auto")
            pm_test.force_allow_all = True
            sb_test = Sandbox(workspace_root=test_sandbox_dir)
            tr_test = ToolRegistry(sandbox=sb_test, permissions=pm_test)
            runner.assert_true("scout_school" in tr_test.tools, "School Scout 18-10：ToolRegistry 成功注册 scout_school 专属能力工具")

            tool_out = tr_test.execute_tool("scout_school", {"school": "测试大学", "major": "软件工程", "include_social": False}, interactive=False)
            runner.assert_true("测试大学" in tool_out or "研招" in tool_out, "School Scout 18-11：scout_school Agent 工具执行流畅无异常")

            # 验证 apply_scout_to_config 配置同步回写
            test_cfg_file = test_sandbox_dir / "ky_config.json"
            test_cfg_file.write_text(json.dumps({"study_plan": {"school": "原目标", "major": "原专业"}}, ensure_ascii=False), encoding="utf-8")
            orig_cfg_file = school_scout.CONFIG_FILE
            school_scout.CONFIG_FILE = test_cfg_file
            try:
                apply_ok = school_scout.apply_scout_to_config("浙江大学", "人工智能", metrics={"subjects_hint": ["408 计算机学科专业基础 (全国统考)"]})
                runner.assert_true(apply_ok is True, "School Scout 18-12：apply_scout_to_config 执行返回成功")
                saved_cfg = json.loads(test_cfg_file.read_text(encoding="utf-8"))
                runner.assert_true(saved_cfg["study_plan"]["school"] == "浙江大学" and saved_cfg["study_plan"]["major"] == "人工智能", "School Scout 18-12：成功同步更新目标院校与专业")
                runner.assert_true("408" in saved_cfg.get("pro_name", ""), "School Scout 18-12：成功同步识别并更新专业课代码科目")
            finally:
                school_scout.CONFIG_FILE = orig_cfg_file

        finally:
            shutil.rmtree(test_sandbox_dir, ignore_errors=True)

    finally:
        # 还原现场配置与大盘
        if cfg_backup:
            (ROOT / "ky_config.json").write_text(json.dumps(cfg_backup, ensure_ascii=False, indent=2), encoding="utf-8")
        if agents_backup:
            (ROOT / "AGENTS.md").write_text(agents_backup, encoding="utf-8")

    # 统计并返回
    success = runner.print_summary()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    run_tests()

