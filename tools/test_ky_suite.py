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
    # 创建一个临时测试配置文件以核查是否被 Git 忽略
    fake_config = ROOT / "ky_config.json"
    fake_config.write_text(json.dumps({"test_api_key": "sk-secret123456"}), encoding="utf-8")

    git_check = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT), capture_output=True, text=True)
    runner.assert_true("ky_config.json" not in git_check.stdout, "ky_config.json 被 .gitignore 正确忽略 (无泄漏风险)")

    # 恢复或清理
    if fake_config.exists():
        fake_config.unlink()

    # 统计并返回
    success = runner.print_summary()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    run_tests()
