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
import re
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
        """显式跳过：环境依赖缺失时如实记录，不计入通过数 (对齐 9.1 诚实原则)"""
        print(f"  [SKIP] {test_name} ({reason})")
        self.skipped += 1

    def print_summary(self):
        print("\n" + "=" * 60)
        _skip_part = f", 跳过 {self.skipped} 项" if self.skipped else ""
        print(f" 测试结果统计: 通过 {self.passed} 项, 失败 {self.failed} 项{_skip_part}")
        if self.failed == 0:
            print(" 🎉 全部已执行测试项通过！系统各模块运转稳健！")
            if self.skipped > 0:
                print(f" ⚠️ 存在 {self.skipped} 项可选依赖跳过，补齐环境后可实现全覆盖")
        else:
            print(f" ❌ 以下测试未通过: {', '.join(self.errors)}")
        print("=" * 60)
        return self.failed == 0

def run_tests():
    runner = TestRunner()
    # 回归测试可能生成报告、快照和临时数据；恢复所有已跟踪文件，保证
    # 运行测试不会把测试时间或夹具结果写回用户工作区。
    #
    # [根因修复·测试污染真实数据 / P0]
    # 旧实现有两个致命问题：
    #   1) 快照时机太晚 —— ky_config.json 与 AGENTS.md 的备份写在「测试组 16」
    #      （约第 887 行），而「测试组 10」在第 476/500 行就已经调用
    #      study_planner.run_study_plan_wizard() 把默认计划写回磁盘。
    #      快照拍在损坏之后，finally 里"还原"的其实是已被覆盖的内容。
    #      （实锤：跑完套件后 ky_config.json 的 school/major 变成模板占位符
    #       「目标院校」「报考专业」，AGENTS.md 的科目四变成「408 计算机学科专业基础」。）
    #   2) 覆盖范围太窄 —— 只跟踪了 10 个路径，漏掉各科 AGENTS.md、
    #      各科 00_*备考总规划.md、各科 _状态/ 下的今日任务与模板等，
    #      而这些都在 run_study_plan_wizard 的写入清单里。
    # 现改为：在主流程开始前，按模式一次性快照全部可变用户数据，
    # 并在结束时按内容还原、删除测试期间新产生的残留。
    _GUARDED_PATTERNS = (
        "ky_config.json",
        "AGENTS.md",
        "00_考研全科总战役规划.md",
        "*/AGENTS.md",
        "*/00_*备考总规划.md",
        "*/考试大纲.md",
        "*/学情档案.md",
        "*/学情档案.template.md",
        "*/_状态/*.md",
        ".memory/*.md",
        ".memory/**/*.md",
        ".memory/exam_keys/*.json",
        "docs/index.html",
        "docs/state_snapshot.json",
        "05-考研看板/docs/index.html",
        "05-考研看板/docs/state_snapshot.json",
        "04-专业课/双校考情对比_*.md",
        "04-专业课/目标院校情报_*.md",
        "04-专业课/考纲变动分析_*.md",
        "data/universities/registry.json",
        # [P5 修复] 这两类此前不在快照/还原范围，测试期间新生成的文件既不会被
        # 还原、也不被 .gitignore 覆盖（.gitignore 只忽略 双校考情对比_* /
        # 目标院校情报_* / data/universities/_sources/），于是会以
        # 「未跟踪的真实校名文件」永久留在工作区。
        "04-专业课/双校对标_*.md",
        "data/universities/*/*.yaml",
    )

    tracked_restore = {}   # rel -> bytes（运行前的原始内容）
    tracked_existing = set()  # 运行前已存在的受控文件（用于识别测试残留）

    # ── R2-D7 工作区守卫（自包含；先于快照与所有测试执行）──────────────────
    # 事故背景（实测）：本套件会真实改写工作区用户数据 —— 测试组 10 的
    # run_study_plan_wizard() 把 ky_config.json 的 study_plan 换成默认模板
    # （school=目标院校/math_key=math2/pro_name=408/total_hours=8.5）；
    # 测试组 18 的 apply_scout_to_config 曾把真实配置冲成 {浙江大学/人工智能/408}。
    # 旧兜底是「内存快照 + finally + atexit」，但 SIGKILL / 超时 / 关控制台时
    # 三者都不执行 → 残缺配置留在盘上；更糟的是下一次运行会在 _snapshot_guarded()
    # 拍到这份已污染内容，还原时「忠实地」写回 → 自锁。
    # 现加三道防线：
    #   1) 快照落盘（.pytest_tmp/ky_suite_guard/）+ running.lock：被强杀也能在
    #      下一次启动时**先自愈再开跑**；
    #   2) 真实用户工作区**默认拒跑**（需 KY_TEST_ALLOW_REAL_WORKSPACE=1 显式放行），
    #      从源头保证用户数据不被写；
    #   3) 放行时按「自愈 → 快照 → 落盘 → 开跑」顺序执行。
    #
    # [R2-D7 追加·判据可见 + 副本情形说明]
    # 判据（_guard_real_workspace_reason）：
    #   命中任一即判为真实工作区 ——
    #     * study_plan.school 非空且不是「目标院校 / 未指定」；
    #     * api_key 非空且不在占位符集合（_PLACEHOLDER_API_KEYS）里。
    #   找不到 ky_config.json / 解析失败 / 根层级非对象 → 判为非真实（不拦）。
    # 拒跑时会打印 ROOT 绝对路径 + 命中的具体字段值，用户可一眼核对是否认错地方。
    #
    # 已知边界（不是 bug，是判据的固有限制）：判据只看 ky_config.json 的**内容**，
    # 所以「忠实副本」（复制目录 / robocopy，带着考生的 ky_config.json）与真实工作区
    # 无法自动区分 → 忠实副本一样被拒跑。三种情形的正确做法：
    #   A 忠实副本（数据可丢弃）→ 在副本里用 KY_TEST_ALLOW_REAL_WORKSPACE=1 放行；
    #   B git archive 副本 → 守卫不触发（无未跟踪的 ky_config.json），但 .memory/、
    #     错题本等也未跟踪数据一并缺失，通过/跳过计数与真实工作区不一致；
    #   C 真实工作区 → 先备份 ky_config.json 再用 KY_TEST_ALLOW_REAL_WORKSPACE=1。
    import base64 as _b64
    _GUARD_DIR = ROOT / ".pytest_tmp" / "ky_suite_guard"
    _GUARD_SNAP = _GUARD_DIR / "snapshot.json"
    _GUARD_LOCK = _GUARD_DIR / "running.lock"
    _PLACEHOLDER_API_KEYS = {"", "sk-test-fake", "sk-test", "sk-xxx", "YOUR_API_KEY_HERE"}

    def _guard_real_workspace_reason():
        """判定「是否像一个真实考生工作区」，并**同时给出判定依据**。

        [R2-D7 追加] 原来只返回 bool，用户被拒跑时看不到「它凭什么这么判」，
        也无法判断守卫是不是认错了地方。现返回 ``(is_real, reason)``，reason 里
        带上命中的具体字段值，配合调用处打印的 ROOT 绝对路径即可一眼核对。

        注意：判据是 ky_config.json 的**内容**，因此「忠实副本」（复制目录 /
        robocopy，带着考生的 ky_config.json）与真实工作区**无法自动区分** ——
        这是本判据的固有限制，不是 bug；调用处的文案已按此说明。
        """
        cfg_path = ROOT / "ky_config.json"
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False, "未找到 ky_config.json（未跟踪文件，git archive 副本里没有）"
        except Exception as e:
            return False, f"ky_config.json 读取/解析失败（{type(e).__name__}: {e}）"
        if not isinstance(cfg, dict):
            return False, "ky_config.json 根层级不是 JSON 对象"
        sp = cfg.get("study_plan") if isinstance(cfg.get("study_plan"), dict) else {}
        school = str(sp.get("school") or "").strip()
        if school and school not in ("目标院校", "未指定"):
            return True, f"study_plan.school={school!r}（非模板占位）"
        api_key = str(cfg.get("api_key") or "").strip()
        if api_key not in _PLACEHOLDER_API_KEYS:
            return True, "api_key 已配置真实值（非占位符）"
        return False, (f"study_plan.school={school!r} 为模板/空，"
                       f"api_key 为占位符 → 判定为非真实工作区")

    def _py_hint() -> str:
        """用户应键入的 Python 解释器名（与 tools/cli/shared.py:interpreter_hint 同口径）。

        Windows 上 ``python`` 常被微软商店 stub 劫持（exit 49 零输出），提示文案里
        写 ``python`` 会让考生照着敲却跑不起来。
        """
        try:
            try:
                from tools.cli.shared import interpreter_hint
            except ImportError:
                from cli.shared import interpreter_hint
            return interpreter_hint()
        except Exception:
            return "python"

    def _guard_persist():
        """把内存快照落盘，并写下 running.lock（供下次启动自愈）。"""
        try:
            _GUARD_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "pid": os.getpid(),
                "files": {rel: _b64.b64encode(b).decode("ascii")
                          for rel, b in tracked_restore.items()},
            }
            _GUARD_SNAP.write_text(json.dumps(payload), encoding="utf-8")
            _GUARD_LOCK.write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass

    def _guard_clear():
        try:
            _GUARD_LOCK.unlink()
        except OSError:
            pass
        try:
            _GUARD_SNAP.unlink()
        except OSError:
            pass

    def _guard_heal():
        """上次运行未完成（lock 仍在）→ 用落盘快照还原，返回还原文件数。"""
        if not (_GUARD_LOCK.exists() and _GUARD_SNAP.exists()):
            return 0
        healed = 0
        try:
            payload = json.loads(_GUARD_SNAP.read_text(encoding="utf-8"))
        except Exception:
            _guard_clear()
            return 0
        for rel, b64 in (payload.get("files") or {}).items():
            try:
                content = _b64.b64decode(b64)
                target = ROOT / rel
                if not target.exists() or target.read_bytes() != content:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    healed += 1
            except (OSError, ValueError):
                continue
        _guard_clear()
        return healed

    _healed = _guard_heal()
    if _healed:
        print(f"\n  [!] 检测到上一次运行未正常结束，已自动还原 {_healed} 个用户数据文件。\n")

    _is_real_ws, _ws_reason = _guard_real_workspace_reason()
    if _is_real_ws and os.environ.get("KY_TEST_ALLOW_REAL_WORKSPACE") != "1":
        _py = _py_hint()
        print("=" * 68)
        print("  [已拒绝运行] 本套件会真实改写工作区用户数据，已中止。")
        print(f"  ROOT = {ROOT}")
        print(f"  判定依据：{_ws_reason}")
        print("")
        print("  ⚠️ 该判据只看 ky_config.json 的内容，因此【忠实副本】（复制目录 /")
        print("     robocopy，带着你的 ky_config.json）与真实工作区**无法自动区分**，")
        print("     一样会被拒跑。这不是认错地方 —— 请按下面三种情形对号入座：")
        print("")
        print("  情形 A｜忠实副本（数据可丢弃，最常用）")
        print("     守卫仍会拒跑（无法自动区分）；在副本里显式放行是安全的：")
        print(f"       KY_TEST_ALLOW_REAL_WORKSPACE=1 {_py} tools/test_ky_suite.py")
        print("")
        print("  情形 B｜git archive 副本（验证代码本身）")
        print("       git archive HEAD | tar -x -C /tmp/ky_copy")
        print(f"       cd /tmp/ky_copy && {_py} tools/test_ky_suite.py")
        print("     守卫不会触发（副本里没有未跟踪的 ky_config.json）。但注意：")
        print("     .memory/ 错题本、ky_config.json 等未跟踪数据也一并缺失，")
        print("     通过/跳过计数会与真实工作区**不一致**。")
        print("")
        print("  情形 C｜确实要在真实工作区跑（风险最高）")
        print("     先手工备份 ky_config.json（或整目录），再：")
        print(f"       KY_TEST_ALLOW_REAL_WORKSPACE=1 {_py} tools/test_ky_suite.py")
        print("=" * 68)
        sys.exit(2)

    def _snapshot_guarded():
        for pat in _GUARDED_PATTERNS:
            try:
                hits = sorted(ROOT.glob(pat))
            except (OSError, ValueError):
                continue
            for path in hits:
                if not path.is_file():
                    continue
                try:
                    rel = path.relative_to(ROOT).as_posix()
                except ValueError:
                    continue
                if rel in tracked_existing:
                    continue
                tracked_existing.add(rel)
                try:
                    tracked_restore[rel] = path.read_bytes()
                except OSError:
                    pass

    _snapshot_guarded()
    _guard_persist()   # [R2-D7] 快照落盘 + 写 running.lock，供强杀后下次启动自愈

    def _restore_guarded():
        """还原受控文件内容，并清理测试运行期间新产生的残留文件。"""
        restored = 0
        for rel, content in tracked_restore.items():
            target = ROOT / rel
            try:
                if not target.exists() or target.read_bytes() != content:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    restored += 1
            except OSError:
                pass
        removed = 0
        for pat in _GUARDED_PATTERNS:
            try:
                hits = sorted(ROOT.glob(pat))
            except (OSError, ValueError):
                continue
            for path in hits:
                if not path.is_file():
                    continue
                try:
                    rel = path.relative_to(ROOT).as_posix()
                except ValueError:
                    continue
                if rel not in tracked_existing:
                    try:
                        path.unlink()
                        removed += 1
                    except OSError:
                        pass
        # [R2-D7] 现场已还原 → 清掉落盘快照与 running.lock，表示本次运行正常收尾。
        _guard_clear()
        return restored, removed

    # 安全网：即便套件在 try/finally 覆盖范围之外崩溃（例如「测试组 1~15」抛异常，
    # 而 clobber 正是发生在测试组 10），进程退出时仍强制还原现场。
    # _restore_guarded 按内容比对、幂等，重复调用无副作用。
    import atexit as _atexit
    _atexit.register(_restore_guarded)

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

    # 钉钉加签算法校验 (真实产品函数与 Golden Value 比对)
    secret = "SECtestsecret123"
    ts = "1600000000000"
    sign = ky_cli._dingtalk_sign(secret, ts)
    expected_sign = "R1BmbVl3GwTONO5nCeA0LVuIzyKwygJAlXbu0iABmio%3D"
    runner.assert_true(sign == expected_sign, "钉钉 HMAC-SHA256 加签签名产品函数计算值与黄金基准精确吻合")

    # ------------------------------------------------------------
    # 测试 4: 晨报与任务卡片提取引擎
    # ------------------------------------------------------------
    print("\n[测试组 4: 每日晨报与自测卡片自动提取]")
    # 模拟静默广播调用（空 webhook 模式，不应抛出任何异常）
    # [P0 修复] 确定性分支验证：空 webhook 下捕获 stdout，断言给出配置引导提示而非恒真
    import io as _io_bc
    from contextlib import redirect_stdout as _redirect_stdout_bc
    _bc_buf = _io_bc.StringIO()
    try:
        with _redirect_stdout_bc(_bc_buf):
            ky_cli.broadcast_briefing(cfg, custom_msg="【自动化测试】考研学习链自检中")
        _bc_out = _bc_buf.getvalue()
        runner.assert_true("暂未检测到已配置的 IM 机器人 Webhook" in _bc_out,
                           "广播引擎：空 Webhook 时给出清晰配置引导而非静默忽略或崩溃")
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

    # [P1 修复·测试夹具超时失配] 网关的部分端点（QQ OneBot / OpenAI 兼容 /
    # 钉钉通用 HTTP）按协议约定是「同步返回解答正文」，其耗时取决于上游 LLM
    # 的真实推理时间（本机实测约 12~15 秒）。此前统一用 timeout=5 请求，
    # 必然在等 LLM 时超时 → 恒报「网关通信异常: timed out」，
    # 造成「代码没坏、测试却红」的假红噪音，掩盖真实回归信号。
    # 现区分两类：纯本地端点（飞书握手/钉钉异步ack//live//api/live）仍用 5s
    # 快速失败；需等待 LLM 的端点用 _LLM_TIMEOUT（默认 60s，可用环境变量覆盖）。
    _LLM_TIMEOUT = float(os.environ.get("KY_TEST_LLM_TIMEOUT", "60"))

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
        with urllib.request.urlopen(req, timeout=_LLM_TIMEOUT) as resp:
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
        with urllib.request.urlopen(q_req, timeout=_LLM_TIMEOUT) as resp:
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
        with urllib.request.urlopen(c_req, timeout=_LLM_TIMEOUT) as resp:
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
            # [P0 修复] 断言语义与实现对齐：此处校验的是渲染引擎"引用声明"，
            # 离线可用性 (katex/marked 内联) 属 P2 前端自包含改造，不在此冒充行为验证
            runner.assert_true("katex" in html_text.lower() and "marked" in html_text.lower(),
                               "Web 伴侣前端同时声明 KaTeX 公式与 marked 渲染引擎引用")
        
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
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                            cwd=str(ROOT), capture_output=True, text=True)
    targets = [
        "ky_config.json",
        "docs/experiences/华中科技大学_计算机.md",
        ".memory/experiences/test.md",
        "04-专业课/双校考情对比_华中科技大学_VS_武汉大学_计算机.md",
        "04-专业课/目标院校情报_测试.md"
    ]
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        for t in targets:
            runner.skip(f"隐私目标应被 .gitignore 忽略: {t}", "非 Git 工作区")
    else:
        for t in targets:
            chk = subprocess.run(["git", "check-ignore", "--no-index", "-q", t], cwd=str(ROOT))
            runner.assert_true(chk.returncode == 0, f"隐私目标应被 .gitignore 忽略: {t}")

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

    # 验证本地 RapidOCR 图像提取引擎（能力感知：无兼容 OCR 运行环境时显式跳过而非误报失败）
    if test_img.exists():
        try:
            import rapidocr_onnxruntime  # noqa: F401
            _HAS_LOCAL_OCR = True
        except ImportError:
            _HAS_LOCAL_OCR = False
        if _HAS_LOCAL_OCR:
            ocr_res = skills.vision_solver.extract_text_with_local_ocr(str(test_img))
            runner.assert_true(ocr_res is not None and len(ocr_res) > 0, "本地 RapidOCR 成功识别并提取图片文字内容")
        else:
            runner.skip("本地 RapidOCR 图像文本提取", "rapidocr-onnxruntime 未安装 (当前 Python 无兼容发行版)")

    # [P0 修复] 剪贴板抓取改为打桩行为级验证：注入模拟剪贴板图像，
    # 验证真实走通「读取 → 暂存 PNG → 返回路径」链路，而非依赖本机剪贴板状态的恒真断言
    try:
        import PIL.ImageGrab as _IG  # noqa: F401
        _HAS_PIL_GRAB = True
    except Exception:
        _HAS_PIL_GRAB = False
    if _HAS_PIL_GRAB:
        class _FakeClipImage:
            def save(self, path, fmt=None):
                Path(path).write_bytes(b"\x89PNG\r\n\x1a\nfake-clipboard-image")
        _saved_grab = _IG.grabclipboard
        try:
            _IG.grabclipboard = lambda: _FakeClipImage()
            clip_test = ky_cli.grab_clipboard_image()
            runner.assert_true(isinstance(clip_test, Path) and clip_test.exists(),
                               "剪贴板图像抓取：模拟剪贴板图像成功暂存为 PNG 并返回有效路径")
        finally:
            _IG.grabclipboard = _saved_grab
    else:
        runner.assert_true(callable(ky_cli.grab_clipboard_image), "剪贴板图像抓取：PIL 未安装，仅验证入口函数可调用")

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
        "eng_baseline": "四级已过 / 摸底水平分",
        "eng_weakness": "待诊断薄弱点、细节定位",
        "pol_baseline": "基础刚起步 / 摸底40分",
        "pol_weakness": "马原唯物辩证法、多选题漏选",
        "pro_baseline": "科班有基础 / 摸底水平分",
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
    orig_scan = study_planner.scan_local_materials
    try:
        study_planner.scan_local_materials = lambda s: []
        # [R2-D8 连带修正] 这里必须显式给 preset_data，不能依赖「不带 preset 的非交互
        # 向导会回落内置默认值」—— 那条路径已被修掉：非交互且未给 preset 时，向导
        # 现在以工作区既有 ky_config.json 为基线（否则每次重跑都会把考生方案重置成
        # 内置默认模板，这正是 2026-09-19 两次真实数据被覆盖的根因）。
        # 上面第 668 行的向导刚把 preset 里的「同济教材+…」写进了配置，若不显式给
        # 基线，本用例读到的 math_books 就是那份旧值，断言会假失败。
        # 本用例要验的是「本地无资料 → 不虚构书目」，所以给一份不含书目的最小基线。
        no_book_plan = study_planner.run_study_plan_wizard(
            interactive=False,
            preset_data={"school": "目标院校", "major": "报考专业", "math_key": "math2"},
        )
        runner.assert_true("暂未放置实体资料" in no_book_plan.get("math_books", "") and "李林" not in no_book_plan.get("math_books", ""), "无实体资料时向导严格杜绝虚构书目")
        clean_agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        runner.assert_true("暂未放置实体资料" in clean_agents, "AGENTS.md 真实记录无资料状态，杜绝任何硬编码假书目")
    finally:
        study_planner.scan_local_materials = orig_scan

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
    runner.assert_true("error_logger" in all_sks and "盲盒" in all_sks["error_logger"]["name"], "错题引擎已升级为 FSRS 盲盒复测")

    # 2. 检验 math_verifier 高阶微积分与代数能力
    ode_res = mv_test.run_math_query("ode y'' + 4*y = 0")
    runner.assert_true("sin" in ode_res.lower() or "cos" in ode_res.lower(), "数学高阶验算：常微分方程通解计算准确")

    quad_res = mv_test.run_math_query("quad [[2, 1], [1, 2]]")
    runner.assert_true("正定" in quad_res and "\\Delta_1" in quad_res, "数学高阶验算：二次型正定性与顺序主子式判定准确")

    sum_res = mv_test.run_math_query("sum 1/n^2 from 1 to oo")
    runner.assert_true("pi" in sum_res.lower() or "\\pi" in sum_res, "数学高阶验算：巴塞尔级数求和计算准确")

    solve_res = mv_test.run_math_query("solve x^2 - 5*x + 6 = 0")
    runner.assert_true("2" in solve_res and "3" in solve_res, "数学高阶验算：代数方程与极值驻点求解准确")

    # 2b. [G-1 回归] 未安装 sympy 时的纯 Python 降级引擎
    # 阴性测试：把 HAS_SYMPY 强制置 False，验证降级路径**真的算出了结果**，
    # 而不是像修复前那样无论问什么都只回一段"建议安装 sympy"的静态提示。
    # 注意：降级结果里也会提到"未安装 sympy"，故必须用静态提示独有的前缀来区分。
    _STATIC_HINT_MARK = "【提示】当前环境未安装"
    _sympy_backup = mv_test.HAS_SYMPY
    try:
        mv_test.HAS_SYMPY = False
        deg_diff = mv_test.run_math_query("diff x^3")
        runner.assert_true(
            "降级引擎" in deg_diff and "3x^2" in deg_diff.replace(" ", "")
            and _STATIC_HINT_MARK not in deg_diff,
            "数学降级引擎：无 sympy 时 diff x^3 真实算出 3x^2（而非仅回安装提示）")

        deg_int = mv_test.run_math_query("int x^2 dx")
        runner.assert_true(
            "(1/3)x^3" in deg_int.replace(" ", ""),
            "数学降级引擎：无 sympy 时 int x^2 dx 真实算出 (1/3)x^3 + C")

        # 覆盖不到的命令必须**诚实回落**到静态提示，不得假装算出了结果
        deg_limit = mv_test.run_math_query("limit sin(x)/x as x->0")
        runner.assert_true(
            _STATIC_HINT_MARK in deg_limit and "降级引擎" not in deg_limit,
            "数学降级引擎：覆盖不到的 limit 命令诚实回落安装提示，不伪造结果")

        deg_defint = mv_test.run_math_query("int x^2 dx from 0 to 1")
        runner.assert_true(
            _STATIC_HINT_MARK in deg_defint and "降级引擎" not in deg_defint,
            "数学降级引擎：定积分不在降级覆盖范围，诚实回落安装提示")
    finally:
        mv_test.HAS_SYMPY = _sympy_backup
    runner.assert_true(mv_test.HAS_SYMPY is _sympy_backup,
                       "数学降级引擎：测试后 HAS_SYMPY 状态已还原（无测试污染）")

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
        # [重构修正] 新记录的下次到期日由 FSRS 实时给出（stage=0/good 实测为 2 天后，
        # 不再是固定阶梯的「明天」），会被 FSRS 严格日期门控滤掉（该门控本身也是被测行为）。
        # 为使复测队列可测，把沙箱内记录的「下次到期」一律回拨至今日 —— 按字段正则改写，
        # 不再依赖"恰好是明天"这一假设，避免记忆算法调参后测试再次失效。
        import datetime as _dt
        _today = _dt.date.today().strftime("%Y-%m-%d")
        for _f in (_el_sandbox / "01-数学" / "错题本").glob("错题记录_*.md"):
            _raw = _f.read_text(encoding="utf-8")
            _fixed = re.sub(r"(下次到期\s*`)\d{4}-\d{2}-\d{2}(`)", rf"\g<1>{_today}\g<2>", _raw)
            if _fixed != _raw:
                _f.write_text(_fixed, encoding="utf-8")
        rec_list = el_test.scan_error_records("math")
        runner.assert_true(len(rec_list) > 0, "错题解析引擎：成功结构化提取 Markdown 错题卡片")

        due_list = el_test.get_due_reviews("math")
        runner.assert_true(len(due_list) > 0, "FSRS 复测：成功提取待复测到期错题队列")

        blind_card = el_test.generate_blind_quiz(due_list[0])
        runner.assert_true("盲盒重测" in blind_card and "隐去历史推导过程" in blind_card, "错题盲盒引擎：成功生成无答案的盲盒复测试题")

        up_ok, up_msg = el_test.mark_error_status("math", due_list[0]["file_name"], due_list[0]["title"], new_status="已掌握")
        runner.assert_true(up_ok is True, "闭环状态回写：成功将复测合格题目更新标记为 [已掌握]")

        # [缺陷修复回归·阴性测试] 错因五分类写入链路必须收敛到白名单：
        # 传入非法分类名（模拟 LLM 幻觉）时，落盘内容不得原样写入非法值。
        el_test.log_error_record("math", "白名单校验探针", "LLM幻觉出的非法分类", "d", "p")
        _probe_files = sorted((_el_sandbox / "01-数学" / "错题本").glob("错题记录_*.md"))
        _probe_raw = _probe_files[-1].read_text(encoding="utf-8")
        runner.assert_true(
            "LLM幻觉出的非法分类" not in _probe_raw and "`概念漏洞`" in _probe_raw,
            "错因五分类：写入链路把非法分类名收敛到白名单（阴性测试）",
        )
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

    # [P0 验证] 沙箱写/删模式阻断工作区外扩展名穿越，且 .json 不再豁免
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf_md:
        out_md = Path(tf_md.name)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf_json:
        out_json = Path(tf_json.name)

    caught_write_out = False
    try:
        sb_test.resolve_safe_path(out_md, read_only=False)
    except SecurityException:
        caught_write_out = True
    runner.assert_true(caught_write_out is True, "沙箱防护：修改/删除模式严禁穿越到外部 .md 文件")

    caught_json_out = False
    try:
        sb_test.resolve_safe_path(out_json, read_only=True)
    except SecurityException:
        caught_json_out = True
    runner.assert_true(caught_json_out is True, "沙箱防护：外部 .json 文件坚决阻断只读豁免，防密钥窃取")

    out_md.unlink(missing_ok=True)
    out_json.unlink(missing_ok=True)

    # 2. 权限分级系统 (Safe / Auto / Ask 策略)
    pm_safe = PermissionManager(mode="safe")
    tr_safe = ToolRegistry(sandbox=sb_test, permissions=pm_safe)
    safe_rej = tr_safe.execute_tool("write_file", {"path": "test_perm.txt", "content": "hello"})
    runner.assert_true("PermissionDenied" in safe_rej, "权限引擎：严格安全模式 (--permission=safe) 成功阻断非只读写入")

    # [P0 验证] run_command 白名单制拦截非白名单与高危指令
    cmd_rej = tr_safe.execute_tool("run_command", {"command": "find . -delete"})
    runner.assert_true("安全拦截" in cmd_rej or "PermissionDenied" in cmd_rej, "沙箱防护：run_command 成功拦截非白名单或高危命令")

    pm_auto = PermissionManager(mode="auto")
    tr_auto = ToolRegistry(sandbox=sb_test, permissions=pm_auto)
    ro_res = tr_auto.execute_tool("list_directory", {"path": ".", "max_depth": 1})
    runner.assert_true("README.md" in ro_res, "权限引擎：只读探索工具 (Level 0) 全自动秒级放行")

    # [P0 验证] python -c 任意代码执行与破坏性 git 操作必须被拦截
    # （补丁清单修复 5 的原始验收用例：python 在白名单内，shell=False 挡不住其自身代码执行）
    py_rce_rej = tr_auto.execute_tool("run_command", {"command": "python -c \"import shutil;shutil.rmtree('/')\""})
    runner.assert_true("安全拦截" in py_rce_rej, "沙箱防护：python -c 任意代码执行被显式拒绝 (RCE 主通道封死)")
    py_pip_rej = tr_auto.execute_tool("run_command", {"command": "python -m pip install evil"})
    runner.assert_true("安全拦截" in py_pip_rej, "沙箱防护：python -m pip 环境改动操作被拒绝")
    git_destroy_rej = tr_auto.execute_tool("run_command", {"command": "git reset --hard"})
    runner.assert_true(
        "安全拦截" in git_destroy_rej or "SecurityError" in git_destroy_rej or "安全黑名单" in git_destroy_rej,
        "沙箱防护：git reset --hard 等破坏性子命令被拦截 (沙箱黑名单或命令白名单双层防御)"
    )
    # 放行验证：白名单内的只读命令与工作区脚本不受误伤
    ro_cmd_res = tr_auto.execute_tool("run_command", {"command": "grep -rn subprocess tools/agent/sandbox.py"})
    runner.assert_true("安全拦截" not in ro_cmd_res, "沙箱防护：只读命令 grep 检索源码不被高危模式误伤")

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

    # [根因修复·测试污染真实数据] 此处原先二次备份 ky_config.json / AGENTS.md，
    # 但备份发生在「测试组 10 已写坏磁盘」之后，属于"损坏后快照"，还原无效。
    # 现在统一由 run_tests() 顶部 _snapshot_guarded() 的主流程前快照负责。

    try:
        # 1. S2-1 错题反向靶向组卷 (exam_composer)
        # [P0 门禁回归·2026-09-24] 显式 allow_placeholder：CI 全新检出（无任何真实
        # 题源）下组卷会被白名单门禁拒绝（success=False），而本组断言测的是
        # 「密钥落盘 → 批改 → 诊断报告」闭环本身，必须有卷可测。占位题无标准答案，
        # 批改走「0 分 + 转人工复核」分支，与"有题源但未登记答案"的真实场景同构。
        paper = exam_composer.compose_exam_paper("math", count=2, save_file=False,
                                                 allow_placeholder=True)
        p_id = paper.get("paper_id", "")
        key_file = ROOT / ".memory" / "exam_keys" / f"{p_id}.json"
        key_raw = key_file.read_text(encoding="utf-8") if key_file.exists() else ""
        runner.assert_true(
            ("EXAM_PAPER_ID" in paper["content"] and key_file.exists()
             and key_raw.startswith("ENC1:") and "standard_answer" not in key_raw)
            or ("EXAM_ANSWER_KEYS" in paper["content"]),
            "教学闭环 S2-1：自测卷答案密钥以 ENC1 加密落盘，密钥文件不可直接读出答案"
        )

        # [P0 修复·测试健壮性] 原固定样例作答 "得出极限为 1/3" 隐含假设抽题必命中极限题，
        # 而抽题池来自学员真实到期错题（数据强耦合），在真实学情数据下必然失配 → 假失败。
        # 改为：解密本轮密钥，取标准答案动态作答；若抽到的题均未登记标准答案，
        # 则按 [P1 修复] 契约断言「0 分 + 转人工复核」，两条分支均为正确产品行为。
        _grade_answer = "1. 答案推导步骤充分有效，得出极限为 1/3"
        try:
            _k_list = json.loads(exam_composer._open_keys_payload(p_id, key_raw)) if key_raw.startswith("ENC1:") else []
        except Exception:
            _k_list = []
        for _k in _k_list:
            _sa = str(_k.get("standard_answer", "") or "").strip()
            if _sa:
                _grade_answer = f"1. {_sa}"
                break
        grade_res = exam_composer.grade_exam_paper(paper["content"], _grade_answer, auto_advance=False)
        _has_std_ans = any(str(k.get("standard_answer", "") or "").strip() for k in _k_list)
        if _has_std_ans:
            runner.assert_true(grade_res.get("success") is True and grade_res.get("score") > 0,
                               "教学闭环 S2-1：自动批改自测卷作答并计算得分与通过率")
        else:
            runner.assert_true(
                grade_res.get("success") is True and grade_res.get("score") == 0
                and "待人工复核" in grade_res.get("report", ""),
                "教学闭环 S2-1：无标准答案题正确执行 0 分 + 转人工复核 (拒绝虚高通过率)"
            )
        runner.assert_true("自动阅卷与采分诊断报告" in grade_res.get("report", ""), "教学闭环 S2-1：生成规范采分点批改诊断报告")

        # [P0 验证] 错题回写空标题拒绝回写防损坏机制
        from skills import error_logger
        ok_empty, msg_empty = error_logger.mark_error_status("math", "test.md", title_keyword="", rating="good")
        runner.assert_true(ok_empty is False and "缺少定位标题" in msg_empty, "错题状态机：空标题显式拒绝回写，杜绝静默篡改数据")

        # [P0 验证] HTTPFetcher 模块定义与 download_file 上下文存在性
        import intelligence.fetcher as ifetcher
        runner.assert_true(hasattr(ifetcher, "_DEFAULT_SSL_CONTEXT"), "KaoYan Intelligence：fetcher 模块正确定义 _DEFAULT_SSL_CONTEXT")

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
        #    [测试隔离修正] check_fatigue_alert 只取 completion_history 里"排序后最后两个日期"，
        #    若工作区残留真实打卡历史（例如考生 09-14 的记录），注入的 09-01/09-02 便不是最后两项，
        #    断言会因历史数据而非代码缺陷假失败。此处先快照并清空历史，测试结束后原样还原。
        _cfg_path = Path(__file__).resolve().parent.parent / "ky_config.json"
        _cfg_snapshot = _cfg_path.read_text(encoding="utf-8") if _cfg_path.exists() else None
        try:
            _cfg_now = json.loads(_cfg_snapshot) if _cfg_snapshot else {}
            _cfg_now["completion_history"] = {}
            _cfg_path.write_text(json.dumps(_cfg_now, ensure_ascii=False, indent=2), encoding="utf-8")

            study_planner.record_daily_completion(rate=50.0, total=4, completed=2, date_str="2026-09-01")
            study_planner.record_daily_completion(rate=40.0, total=5, completed=2, date_str="2026-09-02")
            fatigue_alert = study_planner.check_fatigue_alert()
            runner.assert_true(fatigue_alert["alert"] is True and fatigue_alert["consecutive_low_days"] == 2, "教学闭环 S2-4：成功侦测连续 2 天低完成率并触发防疲劳警报")
            runner.assert_true("防疲劳保障提醒" in fatigue_alert["message"], "教学闭环 S2-4：输出科学减负与心理疏导建议")
        finally:
            if _cfg_snapshot is not None:
                _cfg_path.write_text(_cfg_snapshot, encoding="utf-8")

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
        # (复盘自动化 / 记忆治理 / Plan沙箱 / 看板6Tab+趋势 / FSRS自适应 / 考前节律)
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

            # 5. S3-5 FSRS 自适应复测间隔算法（行为级断言）
            #    [重构修正] 旧断言写死 "again=1 / hard=3 / good=7 / easy=28"，
            #    那是**固定天数阶梯**的期望值，并非 FSRS 输出；旧实现调用的
            #    fsrs.FSRS / Scheduler.repeat 在 fsrs>=5 中已移除，必抛 AttributeError。
            #    现改为校验 FSRS 的三条不变式 + 档位语义 + 确定性与降级容错。
            import datetime as _dt_probe
            _base = _dt_probe.date(2026, 9, 14)
            _seq = {
                _r: [error_logger.calc_fsrs_interval(stage=_s, rating=_r, today=_base)[2]
                     for _s in range(0, 6)]
                for _r in ("again", "hard", "good", "easy")
            }
            # ① 同评级下间隔随已完成档位单调不减（自适应性核心）
            runner.assert_true(
                all(all(b >= a for a, b in zip(v, v[1:])) for v in _seq.values()),
                f"体验生态 S3-5：FSRS 间隔随 stage 单调不减（实测 {_seq}）",
            )
            # ② 同档位下评级严格序 easy > good > hard > again
            runner.assert_true(
                all(_seq["easy"][s] > _seq["good"][s] > _seq["hard"][s] > _seq["again"][s]
                    for s in range(1, 6)),
                "体验生态 S3-5：同档位下评级序 easy > good > hard > again 成立",
            )
            # ③ 确定性：同输入必须可复现（FSRS 随机抖动须已关闭）
            runner.assert_true(
                len({error_logger.calc_fsrs_interval(stage=3, rating="good", today=_base)[2]
                     for _ in range(8)}) == 1,
                "体验生态 S3-5：同 stage+rating 结果完全可复现（已关闭 FSRS 随机抖动）",
            )
            # ④ 档位推进语义：again 重置为 0 且 1 天后复测；good 推进 +1 档
            _st_again, _due_again, _d_again = error_logger.calc_fsrs_interval(
                stage=2, rating="again", today=_base)
            _st_good, _due_good, _d_good = error_logger.calc_fsrs_interval(
                stage=2, rating="good", today=_base)
            runner.assert_true(
                _st_again == 0 and _d_again == 1 and _st_good == 3,
                "体验生态 S3-5：again 重置 stage=0 并 1 天后复测，good 推进至下一档",
            )
            # ⑤ 到期日与间隔天数自洽
            runner.assert_true(
                _due_good == _base + _dt_probe.timedelta(days=_d_good),
                "体验生态 S3-5：下次到期日与间隔天数自洽",
            )
            # ⑥ 降级容错：未知评级安全回落为 good，主链路不得抛异常
            _st_bad, _due_bad, _d_bad = error_logger.calc_fsrs_interval(
                stage=1, rating="不存在的评级", today=_base)
            runner.assert_true(
                _d_bad >= 1 and _st_bad == 2,
                "体验生态 S3-5：未知评级安全降级为 good 且不抛异常",
            )

            # 6. S3-6 考前心理节律关怀与 CLI 命令集成
            # [P0 修复] 行为级验证：捕获真实 stdout，必须输出非空态势内容而非仅验证函数存在
            import io as _io2
            from contextlib import redirect_stdout as _redirect_stdout2
            _ps_buf = _io2.StringIO()
            try:
                with _redirect_stdout2(_ps_buf):
                    ky_cli.print_status_summary()
                _ps_out = _ps_buf.getvalue()
                runner.assert_true(len(_ps_out.strip()) > 20,
                                   "体验生态 S3-6：print_status_summary 真实输出大盘态势/倒计时内容")
            except Exception as e:
                runner.assert_true(False, f"体验生态 S3-6 print_status_summary 异常: {e}")

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
            # [收敛] 该辅助函数已随检索实现统一迁到 tools/search/providers/_http.py，
            # school_scout 不再自建一份（避免两套解析逐渐漂移）。
            from search.providers._http import clean_bing_url, clean_ddg_url
            raw_ddg_sample = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fgs.hust.edu.cn%2Finfo%2F1010%2F123.htm&rut=..."
            cleaned_url = clean_ddg_url(raw_ddg_sample)
            runner.assert_true(cleaned_url.startswith("https://gs.hust.edu.cn/info/1010/123.htm"), "检索 18-3：clean_ddg_url 正确还原真实目标 URL")
            raw_bing_sample = "https://www.bing.com/ck/a?!&&p=x&u=a1aHR0cHM6Ly9ncy5odXN0LmVkdS5jbi9pbmZvLzEwMTAvMTIzLmh0bQ&ntb=1"
            runner.assert_true(clean_bing_url(raw_bing_sample).startswith("https://gs.hust.edu.cn/"), "检索 18-3b：clean_bing_url 正确解出 /ck/a 跳转目标")

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
            # [缺陷修复·院校解析漂移] 回归：别名匹配原用裸子串，
            # 使「华北理工大学」（含子串「北理工」）被解析成「北京理工大学」(10007)，
            # 双校对标因此凭空把学员的备选院校替换成一所层次完全不同的 985。
            try:
                from intelligence.registry import resolve_university as _res_univ
            except ImportError:
                from tools.intelligence.registry import resolve_university as _res_univ

            def _res_name(_q):
                _ent = _res_univ(_q)
                return getattr(_ent, "name", None)

            runner.assert_true(_res_name("华北理工大学") == "华北理工大学",
                               "Registry 18-9a：华北理工大学不得被解析为北京理工大学（裸子串误匹配回归）")
            runner.assert_true(_res_name("北理工") == "北京理工大学",
                               "Registry 18-9b：别名「北理工」仍应正确解析为北京理工大学")
            runner.assert_true(_res_name("华科") == "华中科技大学",
                               "Registry 18-9c：精确别名「华科」解析正确")

            # [缺陷修复·读操作被当成写操作拦截] manage_memory 曾用静态 level，
            # 导致 action='read' 在非交互 ask 模式下也被当成写操作直接拒绝，
            # Agent 连会话记忆都读不到。现按 action 动态定级，须同时满足正反两侧。
            from agent.tools_impl import ToolRegistry as _TR, PermissionManager as _PM, Sandbox as _SB
            _pm_ask = _PM(workspace_root=test_sandbox_dir, mode="ask")
            _tr_ask = _TR(sandbox=_SB(workspace_root=test_sandbox_dir), permissions=_pm_ask)
            _mr = _tr_ask.execute_tool("manage_memory", {"action": "read", "scope": "session"}, interactive=False)
            runner.assert_true(not _mr.startswith("PermissionDenied"),
                               "Permission 18-9d：manage_memory(action='read') 在非交互 ask 模式应放行（读操作误判回归）")
            _mw = _tr_ask.execute_tool("manage_memory",
                                       {"action": "write", "scope": "session", "content": "x"}, interactive=False)
            runner.assert_true(_mw.startswith("PermissionDenied"),
                               "Permission 18-9e：manage_memory(action='write') 在非交互 ask 模式仍应被拦截（写防线未破）")

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
            # [拆分适配] 社媒经验档案逻辑已独立成 experience_dossier 模块，
            # 配置路径改为**显式传参**（比 patch 模块全局更稳：实现换模块也不会失效）。
            apply_ok = school_scout.apply_scout_to_config(
                "浙江大学", "人工智能",
                metrics={"subjects_hint": ["408 计算机学科专业基础 (全国统考)"]},
                config_path=test_cfg_file)

        finally:
            shutil.rmtree(test_sandbox_dir, ignore_errors=True)

        # =========================================================================
        # 19. KaoYan Intelligence 考研招考情报与证据链引擎测试
        # =========================================================================
        print("\n[测试组 19: KaoYan Intelligence 考研招考情报与证据链引擎 (28 项验证)]")
        # [测试隔离修正·离线确定性] 本组（19~23 共用一个 try）里的
        # SchoolComparator.compare() 对**不在内置 8 校考情库**里的高校（如「武汉大学」）
        # 会落到 tools/intelligence/agentic_research.research_university_profile()：
        # 只要 ky_config.json 配了真实 API Key，它就会发起真实、**计费**的 LLM+联网检索。
        # 实测带来两个问题：
        #   ① 偶发崩溃 —— 模型把 majors 返回成 [{...}] 对象数组时，下游
        #      comparator._analyze_differences() 的 " ".join(info["majors"]) 抛
        #      TypeError: sequence item 0: expected str instance, dict found；
        #      （生产侧已由 agentic_research.coerce_str_list 在边界收口，这里再断掉
        #        测试对真实模型的依赖，双保险）
        #   ② 整组耗时与断言结果随网络/模型漂移，而 CI（无 ky_config.json，走离线
        #      fallback）永远绿 —— 「本地红、CI 绿」最难查。
        # 沿用本文件既有的 ky_config 快照/还原范式，临时清空 api_key。
        _g19_cfg_path = Path(__file__).resolve().parent.parent / "ky_config.json"
        _g19_cfg_snap = _g19_cfg_path.read_text(encoding="utf-8") if _g19_cfg_path.exists() else None
        if _g19_cfg_snap is not None:
            try:
                _g19_cfg_now = json.loads(_g19_cfg_snap)
                if isinstance(_g19_cfg_now, dict):
                    _g19_cfg_now["api_key"] = ""
                    _g19_cfg_path.write_text(
                        json.dumps(_g19_cfg_now, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        try:
            import intelligence as ki
            
            # 19-1: 院校注册表与别名模糊解析
            reg = ki.get_registry()
            runner.assert_true(reg.count() >= 50, f"Intelligence 19-1：注册表高校数量达标 ({reg.count()} >= 50 所)")
            
            hust = reg.resolve("华科")
            runner.assert_true(hust is not None and hust.name == "华中科技大学" and hust.chsi_code == "10487", "Intelligence 19-2：华科 成功解析为 华中科技大学 (10487)")
            
            smu = reg.resolve("南医大")
            runner.assert_true(smu is not None and smu.name == "南方医科大学" and smu.chsi_code == "12111", "Intelligence 19-3：南医大 成功解析为 南方医科大学 (12111)")

            uestc = reg.resolve("成电")
            runner.assert_true(uestc is not None and uestc.name == "电子科技大学", "Intelligence 19-4：成电 成功解析为 电子科技大学")

            # 19-5: 官方站点有向图谱
            site_graph = reg.build_site_graph(hust, "计算机")
            runner.assert_true(site_graph["domains"]["graduate_school"] == "http://gszs.hust.edu.cn", "Intelligence 19-5：成功提取华科研究生院官方招生域名")
            runner.assert_true("cs.hust.edu.cn" in str(site_graph["domains"]["college"]), "Intelligence 19-6：成功构建计算机二级学院有向站点入口")
            runner.assert_true("yz.chsi.com.cn" in site_graph["chsi_portals"]["zsml_catalog"], "Intelligence 19-7：成功生成研招网官方目录直达通道")

            # 19-8: 证据对象与信源分级
            ev_s = ki.build_evidence("招生人数", 60, "人", 2027, "chsi", "研招网", "https://yz.chsi.com.cn")
            runner.assert_true(ev_s.source.level == "S" and ev_s.confidence == 1.0 and ev_s.status == "VERIFIED", "Intelligence 19-8：研招网生成 S 级证据且置信度为 100%")

            ev_c = ki.build_evidence("就读评价", "学风良好", "项", 2027, "social_media", "知乎", "https://zhihu.com")
            runner.assert_true(ev_c.source.level == "C" and ev_c.confidence == 0.30, "Intelligence 19-9：社媒信源生成 C 级证据且置信度为 30%")

            # [P0 验证] SSL 校验失败降级时强制标为 D 级与 UNVERIFIED，杜绝假官方认证
            ev_unverified = ki.build_evidence("招生人数", 60, "人", 2027, "chsi", "研招网", "https://yz.chsi.com.cn", ssl_verified=False)
            runner.assert_true(ev_unverified.status == "UNVERIFIED" and ev_unverified.source.level == "D" and ev_unverified.confidence <= 0.30, "Intelligence：SSL 证书校验未通过时强制降为 D 级与 UNVERIFIED")

            # 19-10: 年份锁定机制 (Exam Year Locking)
            ev_old = ki.build_evidence("招生人数", 50, "人", 2024, "graduate_school", "研究生院", "http://test.edu.cn", target_year=2027)
            runner.assert_true(ev_old.status == "OUTDATED" and "年份预警" in (ev_old.conflict_detail or ""), "Intelligence 19-10：历史旧年份数据触发 OUTDATED 年份锁预警")

            # 19-11: 字段级多源冲突仲裁 (Conflict Resolver)
            ev_a = ki.build_evidence("招生人数", 58, "人", 2027, "college_official", "计算机学院", "http://cs.test.edu.cn")
            conflicts = ki.resolve_conflicts([ev_s, ev_a])
            runner.assert_true(len(conflicts) == 2, "Intelligence 19-11：多源冲突时保留全部双方证据链")
            runner.assert_true(all(c.status == "CONFLICT" for c in conflicts), "Intelligence 19-12：冲突证据状态显式标记为 CONFLICT")
            runner.assert_true("研招网" in conflicts[0].conflict_detail and "学院" in conflicts[0].conflict_detail, "Intelligence 19-13：冲突仲裁生成双源比对与研判建议")

            # 19-14: 研招网连接器 (CHSI Connector)
            connector = ki.CHSIConnector()
            chsi_url = connector.build_catalog_url("华中科技大学", "085404", "湖北武汉")
            runner.assert_true("dwmc=%E5%8D%8E%E4%B8%AD%E7%A7%91%E6%8A%80%E5%A4%A7%E5%AD%A6" in chsi_url and "ssdm=42" in chsi_url, "Intelligence 19-14：研招网专业目录精确参数化构造成功")
            chsi_evs = connector.query_catalog("华中科技大学", "085404", target_year=2027)
            # 行为正确性断言（不强依赖外网）：
            #   联网抓取成功 → S 级 VERIFIED 权威证据；
            #   联网失败兜底 → 必须诚实标注 offline_baseline / C 级 / UNVERIFIED，严禁伪装 S 级官方数据
            _ok_chsi = len(chsi_evs) > 0 and (
                (chsi_evs[0].source.level == "S" and chsi_evs[0].status == "VERIFIED")
                or (chsi_evs[0].source.type == "offline_baseline"
                    and chsi_evs[0].status == "UNVERIFIED"
                    and "离线基准" in chsi_evs[0].source.name)
            )
            runner.assert_true(_ok_chsi, "Intelligence 19-15：研招网抓取成功返回S级核验证据，联网失败时兜底诚实标注离线基准(C级/未核验)")

            # 19-16: 文档抽取器 (Document Extractor)
            mock_html = "<html><head><title>2027年硕士研究生招生简章 - 华中科技大学研究生院</title></head><body><p>拟招收硕士研究生 150 人，初试科目包含(101)思想政治理论、(204)英语(二)、(302)数学(二)、(408)计算机学科专业基础。</p><a href='/doc/2027_zsml.pdf'>2027招生专业目录.pdf</a></body></html>"
            extractor = ki.DocumentExtractor()
            ext_evs = extractor.extract_from_html(mock_html, "http://gszs.hust.edu.cn/notice/1.htm", "华中科技大学", target_year=2027)
            runner.assert_true(any(e.field == "拟招生人数" and e.value == 150 for e in ext_evs), "Intelligence 19-16：成功从 HTML 中抽取拟招生人数 150 人")
            runner.assert_true(any("408" in str(e.value) for e in ext_evs), "Intelligence 19-17：成功从 HTML 中抽取初试 408 统考科目")
            runner.assert_true(any(e.field == "官方PDF招生目录附件" for e in ext_evs), "Intelligence 19-18：成功捕获招生专业目录 PDF 附件")

            # 19-19: 招生动态监控器 (Admission Watcher)
            watcher = ki.AdmissionWatcher()
            add_res = watcher.add_watch("华科")
            runner.assert_true(add_res["success"] is True, "Intelligence 19-19：AdmissionWatcher 成功将华科纳入动态监控雷达")
            runner.assert_true(len(watcher.list_watched()) >= 1, "Intelligence 19-20：list_watched 正确返回已监控高校列表")
            runner.assert_true(watcher.remove_watch("华科") is True, "Intelligence 19-21：remove_watch 成功解除高校监控")

            # 19-22: PDF 深度解析提取器 (extract_from_pdf)
            mock_pdf_content = (
                "华中科技大学2027年硕士研究生招生专业目录\n"
                "085404 计算机技术\n"
                "初试科目：(101)思想政治理论 (204)英语(二) (302)数学(二) (408)计算机学科专业基础\n"
                "拟招收人数：85人\n"
            )
            pdf_evs = extractor.extract_from_pdf(
                pdf_path_or_bytes=mock_pdf_content,
                source_url="http://gszs.hust.edu.cn/doc/2027_zsml.pdf",
                school_name="华中科技大学",
                target_year=2027,
                major_keyword="085404"
            )
            runner.assert_true(any("085404" in str(e.value) for e in pdf_evs), "Intelligence 19-22：extract_from_pdf 成功抽取专业代码与名称")
            runner.assert_true(any(e.field == "PDF拟招生计划人数" and e.value == 85 for e in pdf_evs), "Intelligence 19-23：extract_from_pdf 成功抽取拟招生计划 85 人")

            # 19-24: 双校招考横向对比引擎 (School Comparator)
            comparator = ki.SchoolComparator()
            comp_res = comparator.compare("华中科技大学", "武汉大学", "计算机", save_report=True)
            runner.assert_true(comp_res["school1"] == "华中科技大学" and comp_res["school2"] == "武汉大学", "Intelligence 19-24：SchoolComparator 成功对标双校办学层次与教育部代码")
            runner.assert_true("408" in comp_res["analysis"]["subject_diff"] or "统考" in comp_res["analysis"]["subject_diff"], "Intelligence 19-25：SchoolComparator 智能识别初试科目与自命题差异")
            runner.assert_true(comp_res.get("saved_path") and Path(comp_res["saved_path"]).exists(), "Intelligence 19-26：SchoolComparator 成功导出双校横向对标 Markdown 研报至 04-专业课")

            # 19-27: 学情量化报考风险与提分门槛诊断 (User State Gap Analysis)
            intel_engine = ki.get_intelligence_engine()
            intel_res = intel_engine.query("华中科技大学", "085404", save_report=False)
            runner.assert_true("个人学情量化报考风险与提分门槛诊断" in intel_res["markdown_report"], "Intelligence 19-27：研报成功包含 User State Gap Analysis 学情量化诊断")
            runner.assert_true("370+" in intel_res["markdown_report"] or "目标分" in intel_res["markdown_report"], "Intelligence 19-28：学情诊断成功联动学员目标成绩与名校自划线门槛")

            # =========================================================================
            # 20. Syllabus Diff 考研大纲考点版本比对引擎与 ky fetch 统一调度
            # =========================================================================
            print("\n[测试组 20: Syllabus Diff 考研大纲考点版本比对引擎与 ky fetch 调度 (9 项验证)]")
            diff_gen = ki.get_syllabus_diff_generator()
            runner.assert_true(diff_gen is not None, "Syllabus Diff 20-1：成功载入 SyllabusDiffGenerator 引擎单例")

            sample_s1 = """## 一、高等数学
### 1. 函数、极限、连续
- **掌握**：极限四则运算法则、等价无穷小代换
- **理解**：闭区间连续函数零点定理
- **了解**：函数奇偶性与周期性
### 2. 一元函数微分学
- **掌握**：洛必达法则求极限、导数物理意义
- **理解**：拉格朗日中值定理
"""
            sample_s2 = """## 一、高等数学
### 1. 函数、极限、连续
- **掌握**：极限四则运算法则、等价无穷小代换
- **掌握**：闭区间连续函数零点定理
### 2. 一元函数微分学
- **掌握**：洛必达法则求极限
- **掌握**：泰勒公式与麦克劳林展开
- **理解**：拉格朗日中值定理
"""
            diff_res = diff_gen.compare_texts(
                old_text=sample_s1,
                new_text=sample_s2,
                school="测试大学",
                major="高等数学",
                year_old=2026,
                year_new=2027
            )
            m_diff = diff_res["metrics"]
            runner.assert_true(m_diff["total_old"] == 7, "Syllabus Diff 20-2：准确统计基准原子考点总数 (7 项)")
            runner.assert_true(m_diff["total_new"] == 6, "Syllabus Diff 20-3：准确统计新版原子考点总数 (6 项)")
            runner.assert_true(m_diff["added_count"] >= 1, "Syllabus Diff 20-4：成功识别新增考点 (泰勒公式与麦克劳林展开)")
            runner.assert_true(m_diff["removed_count"] >= 1, "Syllabus Diff 20-5：成功识别被剔除考点 (了解级别的奇偶性与周期性)")
            runner.assert_true(m_diff["modified_count"] >= 1, "Syllabus Diff 20-6：成功识别考查要求提升 (零点定理 理解➔掌握)")
            runner.assert_true(m_diff["volatility_percentage"] > 0, "Syllabus Diff 20-7：量化计算大纲波动率与稳定性等级")

            md_diff_rep = diff_gen.format_diff_markdown(diff_res)
            runner.assert_true("新增考点清单" in md_diff_rep and "高危必看" in md_diff_rep, "Syllabus Diff 20-8：成功生成高可读性大纲异动 Markdown 深度研报")

            # 验证 Agent 工具箱中集成 diff_syllabus
            from agent.tools_impl import ToolRegistry, PermissionManager, Sandbox
            sb_diff = Sandbox(workspace_root=test_sandbox_dir)
            pm_diff = PermissionManager(workspace_root=test_sandbox_dir, mode="auto")
            pm_diff.force_allow_all = True
            tr_diff = ToolRegistry(sandbox=sb_diff, permissions=pm_diff)
            runner.assert_true("diff_syllabus" in tr_diff.tools, "Syllabus Diff 20-9：ToolRegistry 成功注册 diff_syllabus 专属 Agent 工具")

            # =========================================================================
            # 21. Material Ingestion 试题智能切片入库管道与 ky ingest (9 项验证)
            # =========================================================================
            print("\n[测试组 21: Material Ingestion 试题智能切片入库管道与 ky ingest (9 项验证)]")
            try:
                from skills import material_ingestion as mi
            except ImportError:
                from tools.skills import material_ingestion as mi

            runner.assert_true(mi is not None, "Material Ingest 21-1：成功载入 material_ingestion 技能模块")
            skills_reg = ky_cli.list_skills()
            runner.assert_true("material_ingestion" in skills_reg, "Material Ingest 21-2：SKILLS_REGISTRY 正确注册 material_ingestion")

            sample_exam_text = """一、单项选择题
1. 某二叉树的先序与后序遍历相同，则其形态为 ( )
A. 只有根结点
B. 只有左子树
C. 只有右子树
D. 无度为2的结点
【答案】A
【解析】先序后序相同必定仅有一个根结点。

二、综合应用题
41. (15分) 某系统采用分页存储管理，逻辑地址空间为 32 位：
(1) 计算页表项大小；
(2) 说明 TLB 快表命中时的地址转换流程。
【参考答案】
解：
(1) 页表项计算：
步骤1：根据页面大小划分页号与页内偏移量。[+6分]
(2) TLB 地址转换：
步骤2：并行比对 TLB 标签并计算物理地址。[+9分]
【解析】考察虚拟内存分页机制与 TLB 命中。
"""
            pipe = mi.get_material_ingestion_pipeline()
            chunks = pipe.chunk_text(sample_exam_text, default_source="2025统考模拟")
            runner.assert_true(len(chunks) == 2, f"Material Ingest 21-3：成功分块切片 2 道独立大题 (当前切出 {len(chunks)} 题)")

            c_choice = chunks[0]
            runner.assert_true(c_choice.q_type == "choice" and len(c_choice.options) == 4, "Material Ingest 21-4：单选题识别准确，完整提取 A/B/C/D 四个选项")
            runner.assert_true("二叉树" in c_choice.stem or "只有根结点" in str(c_choice.options), "Material Ingest 21-5：准确提取选择题原题干与核心选项")

            c_essay = chunks[1]
            runner.assert_true(c_essay.q_type == "essay" and c_essay.score == 15, "Material Ingest 21-6：综合大题识别准确并捕获 15 分满分")
            runner.assert_true(len(c_essay.rubric) >= 2, "Material Ingest 21-7：成功解析并提取步骤级采分点 [+6分] 与 [+9分]")

            card_sample = pipe.format_question_card(c_essay, subject="pro")
            runner.assert_true("采分步骤" in card_sample and "[+6分]" in card_sample, "Material Ingest 21-8：成功排版为标准白名单真题卡片格式")

            runner.assert_true("ingest_exam_material" in tr_diff.tools, "Material Ingest 21-9：ToolRegistry 成功注册 ingest_exam_material 专属工具")

            # A test case for TOC page to prevent regression of P0-1 (TypeError on num=num)
            toc_text = "目  录\n第一章 函数、极限、连续 ................. 1\n第二章 导数与微分 ....................... 12"
            toc_chunks = pipe.chunk_text(toc_text, default_source="TOC Test")
            runner.assert_true(len(toc_chunks) > 0, "Material Ingest 21-10：成功解析含目录页的真题而不抛出异常")

            # [缺陷修复·表格型真题] 回归：以 Markdown 表格承载的 Q&A 必须被正确抽取，
            # 不得把"考点列"拼成题干，也不得把压扁的考点清单当成题目。
            # 修复前实测：一份含 10 选择 + 10 填空 + 4 计算的真实真题 → 「选择 0 / 填空 13 / 大题 5」。
            table_exam = (
                "## 选择题（2 题）\n\n"
                "| # | 题目 | 答案 | 考点 |\n|---|---|---|---|\n"
                "| 1 | 连续周期信号 f(t) 的频谱 F(jω) 的特点是 | **D 离散、非周期** | 周期信号频谱特点 |\n"
                "| 2 | 已知 F(z)=z/(z−2)，则原函数 f(n) 为 | **D 无法确定** | 未给 ROC 的陷阱 |\n\n"
                "## 填空题（1 题）\n\n"
                "| # | 题目 | 答案 | 考点 |\n|---|---|---|---|\n"
                "| 1 | aⁿu(n) 的 z 变换为 | **z/(z−a)** | 常用 z 变换对 |\n"
            )
            t_chunks = pipe.chunk_text(table_exam, default_source="南医807真题回忆版转录")
            t_choice = [c for c in t_chunks if c.q_type == "choice"]
            t_blank = [c for c in t_chunks if c.q_type == "blank"]
            runner.assert_true(len(t_choice) == 2,
                               f"Material Ingest 21-11：表格型真题的选择题被正确识别（期望 2，实得 {len(t_choice)}）")
            runner.assert_true(len(t_blank) == 1,
                               f"Material Ingest 21-12：表格型真题的填空题被正确识别（期望 1，实得 {len(t_blank)}）")
            runner.assert_true(all("|" not in c.stem and "考点" not in c.stem for c in t_chunks),
                               "Material Ingest 21-13：题干不含表格分隔符/考点列碎片")
            runner.assert_true(any(c.answer and "z/(z−a)" in c.answer for c in t_blank),
                               "Material Ingest 21-14：表格填空题正确带回标准答案")

            # [缺陷修复·虚假认证] 回忆版/转录版即使文件名含"真题"也不得盖 VERIFIED
            card_t = pipe.format_question_card(t_choice[0], subject="pro")
            runner.assert_true("USER_IMPORTED" in card_t and "VERIFIED" not in card_t,
                               "Material Ingest 21-15：转录/回忆版材料默认标注待核验，不伪造 VERIFIED 认证")

            # [缺陷修复·演示样例跨科] `ky diff` 演示模式的"新增考点"必须取自**本科目**考纲，
            # 不得写死 408 计算机考点（旧实现给 812 信号与系统考生演示「B+树在索引文件中的应用」）。
            try:
                _demo_fn = ky_cli.build_demo_syllabus_text
            except AttributeError:
                _demo_fn = None
            if _demo_fn:
                _sig_outline = (
                    "# 04-专业课 · 【812 信号与系统】官方考试大纲\n\n"
                    "## 核心考查章节与重点要求：\n"
                    "- 第一章：信号与系统的基本概念（连续与离散、线性时不变系统性质） (要求：掌握)\n"
                    "- 第二章：连续时间系统的时域分析（卷积积分、微分方程解法） (要求：掌握)\n"
                    "- 第三章：傅里叶变换与频域分析（抽样定理） (要求：掌握)\n"
                )
                _demo_txt = _demo_fn(_sig_outline, "2027")
                runner.assert_true(
                    "傅里叶变换" in _demo_txt and "演示样例" in _demo_txt,
                    "Syllabus Diff 21-16：演示样例按本科目考纲生成（含本科目考点）")
                runner.assert_true(
                    not any(k in _demo_txt for k in ("红黑树", "B+树", "外部排序")),
                    "Syllabus Diff 21-17：演示样例不含 408 等跨科目考点")

            # =========================================================================
            # 22. 社媒经验降噪过滤与分省高校注册表 (Sprint 6 - 8 项验证)
            # =========================================================================
            print("\n[测试组 22: 社媒经验降噪过滤与分省高校注册表 (Sprint 6 - 8 项验证)]")
            try:
                from skills import school_scout
            except ImportError:
                from tools.skills import school_scout

            mock_social_posts = {
                "zhihu": [
                    {
                        "title": "2025华科计算机85404考研一战410分经验贴：初试各科复盘与复试不歧视双非全记录",
                        "url": "https://zhuanlan.zhihu.com/p/mock123",
                        "snippet": "总分410，政治78英语84数学122专业课126，专业排名前五。华科一志愿保护非常好，复试公开透明不压分。"
                    },
                    {
                        "title": "华科计算机专业课资料出售！学姐独家笔记加微信咨询",
                        "url": "https://zhuanlan.zhihu.com/p/mock_spam",
                        "snippet": "需要真题笔记加微信：kaoyan6688，淘宝搜学姐考研，包过密卷，名额有限私聊。"
                    }
                ],
                "bilibili": [
                    {
                        "title": "【24考研复盘】华中科技大学408复试机试避坑指南",
                        "url": "https://www.bilibili.com/video/BVmock",
                        "snippet": "华科复试机试难度适中，老师面试非常和蔼，差额复试比严格执行1:1.2，绝不压分！"
                    }
                ]
            }

            filtered_posts = school_scout.filter_community_experiences(mock_social_posts, school="华中科技大学", major="计算机")
            runner.assert_true(len(filtered_posts) == 3, "Sprint 6 22-1：成功清洗并输出全部 3 篇社媒经验")

            # 验证高分且近期的经验置信度处于高分段
            high_post = next((p for p in filtered_posts if "410分" in p["title"]), None)
            runner.assert_true(high_post and high_post["confidence"] >= 75 and high_post["quality"] == "HIGH", "Sprint 6 22-2：包含分数明细与近期高分经验贴赋予高置信度 (>=75)")

            # 验证营销商业垃圾贴被降权并打上垃圾标签
            spam_post = next((p for p in filtered_posts if "加微信" in p["title"]), None)
            runner.assert_true(spam_post and spam_post["is_spam"] and spam_post["confidence"] < 50, "Sprint 6 22-3：商业卖课与引流垃圾贴成功识别并降权惩处")

            # 验证经验档案落盘
            test_exp_file = school_scout.save_experience_dossier("华中科技大学", "计算机", filtered_posts, metrics={"level": "985", "positive_signals": ["保护一志愿"]})
            runner.assert_true(test_exp_file.exists() and "华中科技大学" in test_exp_file.read_text(encoding="utf-8"), "Sprint 6 22-4：成功归档并落盘经验档案至 .memory/experiences/ (隐私目录)")

            # 验证分省 YAML 导出与 Schema 校验
            from intelligence.registry import export_to_provincial_yamls, validate_university_yaml, get_registry
            exported_yamls = export_to_provincial_yamls()
            runner.assert_true(len(exported_yamls) >= 50, f"Sprint 6 22-5：高校注册表成功按省份分流导出为 YAML 档案 (生成 {len(exported_yamls)} 个)")

            sample_hust_yaml = ROOT / "data" / "universities" / "湖北" / "华中科技大学.yaml"
            ok_val, err_val = validate_university_yaml(sample_hust_yaml)
            runner.assert_true(ok_val and len(err_val) == 0, "Sprint 6 22-6：validate_university_yaml 校验符合严格 Schema 标准")

            # 验证非法 YAML 被拦截
            invalid_ok, invalid_errs = validate_university_yaml({"name": ""})
            runner.assert_true(not invalid_ok and len(invalid_errs) >= 3, "Sprint 6 22-7：validate_university_yaml 准确拦截缺失必填字段的数据")

            # 验证 ContextEngine 动态挂载
            from agent.context_engine import ContextEngine
            ce_test = ContextEngine(workspace_root=ROOT, active_subject="pro")
            test_prompt = ce_test.build_system_prompt()
            runner.assert_true("目标院校" in test_prompt or "考情与社媒" in test_prompt, "Sprint 6 22-8：ContextEngine 成功将考情与考纲情报动态注入系统提示词")

            # =========================================================================
            # 23. TUI 交互中枢与考情看板雷达 (Sprint 7 - 8 项验证)
            # =========================================================================
            print("\n[测试组 23: TUI 交互中枢与考情看板雷达 (Sprint 7 - 8 项验证)]")
            try:
                import tui_navigator
            except ImportError:
                from tools import tui_navigator

            runner.assert_true(tui_navigator is not None, "Sprint 7 23-1：成功载入 tui_navigator 模块")
            menu_hdr = tui_navigator.render_header()
            runner.assert_true("考研学习链" in menu_hdr and "倒计时" in menu_hdr, "Sprint 7 23-2：TUI 导航器正确渲染终端看板 Banner 与倒计时")

            menu_txt = tui_navigator.render_menu()
            runner.assert_true("[1] 今日任务" in menu_txt and "[4] 考纲Diff" in menu_txt and "[8] 简章监控" in menu_txt, "Sprint 7 23-3：TUI 菜单完整囊括全功能 9 大动作入口")

            # 测试退出信号
            exit_flag = tui_navigator.execute_action("0", interactive=False)
            runner.assert_true(exit_flag is False, "Sprint 7 23-4：执行 '0' 指令正确产生系统安全退出信号")

            # 验证 AdmissionWatcher 状态可被正常检索
            from intelligence.watcher import AdmissionWatcher
            watcher = AdmissionWatcher()
            watched_list = watcher.list_watched()
            runner.assert_true(isinstance(watched_list, list), "Sprint 7 23-5：AdmissionWatcher 正常输出已监控高校清单")

            # 验证 05-考研看板 build_radar_html
            sys.path.insert(0, str(ROOT / "05-考研看板"))
            import build as board_build
            radar_html = board_build.build_radar_html(ROOT)
            runner.assert_true("目标院校简章监控雷达" in radar_html, "Sprint 7 23-6：build_radar_html 成功构建简章监控雷达卡片")
            runner.assert_true("考纲版本异动与动荡率分析" in radar_html, "Sprint 7 23-7：build_radar_html 成功构建考纲 AST 异动雷达卡片")

            # 验证 docs/index.html 包含考情雷达与导航切换
            docs_html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
            runner.assert_true("id=\"p-radar\"" in docs_html and "data-p=\"radar\"" in docs_html, "Sprint 7 23-8：docs/index.html 成功挂载考情雷达页签并与底部导航栏实现双向响应联动")

        except Exception as e:
            runner.assert_true(False, f"Sprint 6/7 模块测试异常: {e}")
        finally:
            # 还原上面为「离线确定性」临时清空的 api_key（缺失时不动盘）。
            if _g19_cfg_snap is not None:
                _g19_cfg_path.write_text(_g19_cfg_snap, encoding="utf-8")

        # =========================================================================
        # 24. CLI 真实进程级 smoke tests
        # 这些检查通过当前 Python 解释器启动 ky_cli.py，验证用户实际调用的
        # 入口、退出码和关键输出，而不是只调用内部函数或检查文件存在。
        # =========================================================================
        print("\n[测试组 24: CLI 真实进程级 smoke tests]")
        cli_script = ROOT / "tools" / "ky_cli.py"

        def run_cli(*cli_args, timeout=30):
            return subprocess.run(
                [sys.executable, str(cli_script), *cli_args],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )

        try:
            help_res = run_cli("--help")
            runner.assert_true(help_res.returncode == 0 and "子命令" in help_res.stdout, "CLI smoke：--help 真实进程启动并输出子命令")

            status_res = run_cli("status")
            runner.assert_true(status_res.returncode == 0 and "倒计时" in status_res.stdout, "CLI smoke：status 返回 0 且输出战役态势")

            today_res = run_cli("today", "--json")
            try:
                today_obj = json.loads(today_res.stdout)
            except json.JSONDecodeError:
                today_obj = {}
            runner.assert_true(today_res.returncode == 0 and isinstance(today_obj.get("subjects"), dict), "CLI smoke：today --json 返回可解析结构化数据")

            map_res = run_cli("map", "math", "--json")
            try:
                map_obj = json.loads(map_res.stdout)
            except json.JSONDecodeError:
                map_obj = {}
            runner.assert_true(map_res.returncode == 0 and map_obj.get("subject") == "math" and map_obj.get("total_topics", 0) > 0, "CLI smoke：map --json 返回知识点图谱")

            for command, marker in (("review", "错题"), ("fatigue", "复习节奏"), ("bridge", "双向对话"), ("watch", "监控高校"), ("fetch", "考研招考情报")):
                args = (command, "--list") if command == "watch" else ((command, "--help") if command == "fetch" else (command,))
                result = run_cli(*args)
                # Windows 子进程在不同控制台编码下可能无法稳定还原中文；
                # 退出码 + 非空输出仍验证了真实入口，若能解码则额外核对语义标记。
                output_ok = marker in result.stdout or (command == "fatigue" and bool(result.stdout.strip()))
                runner.assert_true(result.returncode == 0 and output_ok, f"CLI smoke：{command} 入口真实运行")

            variant_res = run_cli("variant", "导数中值定理")
            runner.assert_true(variant_res.returncode == 0 and "私教自拟变式" in variant_res.stdout, "CLI smoke：variant 真实执行并标注题源")

            # [P0 门禁回归·2026-09-24] exam 的题源门禁已从「免责声明」改为「真门禁」：
            #   · 完全无真实题源（CI 全新检出）→ 拒绝组卷 + 上手引导，退出码 2；
            #   · 有题源但不足 → 降题量并卷首「题量声明」缺口，绝不凑数。
            # 本机若配了真实 API Key 且薄弱点雷达存在 C/D 行，抽题会真实调用大模型
            # （计费 + 单次 15s 超时，30s 超时上限内可能变红），沿用本文件既有快照
            # 范式临时清空 api_key，强制走与 CI 一致的确定性离线分支。
            _exam_cfg_path = Path(__file__).resolve().parent.parent / "ky_config.json"
            _exam_cfg_snap = _exam_cfg_path.read_text(encoding="utf-8") if _exam_cfg_path.exists() else None
            try:
                if _exam_cfg_snap is not None:
                    _exam_cfg_now = json.loads(_exam_cfg_snap)
                    if isinstance(_exam_cfg_now, dict):
                        _exam_cfg_now["api_key"] = ""
                        _exam_cfg_path.write_text(
                            json.dumps(_exam_cfg_now, ensure_ascii=False, indent=2), encoding="utf-8")
                exam_res = run_cli("exam", "math", "--count=3")
            finally:
                if _exam_cfg_snap is not None:
                    _exam_cfg_path.write_text(_exam_cfg_snap, encoding="utf-8")
            _default_blocks = re.findall(r"^### 📝 第 .*?$", exam_res.stdout, re.MULTILINE)
            if exam_res.returncode == 0:
                runner.assert_true(
                    len(_default_blocks) <= 3 and (len(_default_blocks) == 3 or "题量声明" in exam_res.stdout),
                    "CLI smoke：exam 题源不足时降题量并卷首声明缺口（不凑数）",
                )
            else:
                runner.assert_true(
                    exam_res.returncode == 2 and "白名单题源门禁" in exam_res.stdout,
                    "CLI smoke：exam 无真实题源时拒绝组卷并给出上手引导",
                )

            # 显式 --allow-placeholder 时才恢复「足量且不重复」的题量契约（占位题须逐题标注来源）。
            exam_res = run_cli("exam", "math", "--count=3", "--allow-placeholder")
            question_blocks = re.findall(r"^### 📝 第 .*?$", exam_res.stdout, re.MULTILINE)
            question_texts = re.findall(r"\*\*题目设问与题干\*\*：\n```text\n(.*?)\n```", exam_res.stdout, re.DOTALL)
            runner.assert_true(
                exam_res.returncode == 0 and len(question_blocks) == 3 and len(question_texts) == len(set(question_texts)),
                "CLI smoke：exam --count=3 --allow-placeholder 返回足量且不重复的题目",
            )

            # [测试隔离修正·离线确定性] 本项断言的是「未命中院校库时的离线回退语义」，
            # 与 CI 一致（CI 由 actions/checkout 全新检出，没有 ky_config.json）。
            # 但本机运行时若 ky_config.json 里配了真实 API Key，
            # comparator._get_school_profile() 会落到
            # tools/intelligence/agentic_research.research_university_profile()：
            # 对两所不存在的高校发起真实、**计费**的 LLM + 联网检索
            # （实测同一个沙箱连续三次 26.6s / 40.4s / 100.1s，远超本项 30s 超时，
            #  随机变红；且会真实消耗额度）。无配置时同一命令仅 0.8s。
            # 这里沿用本文件既有的 ky_config 快照/还原范式，临时清空 api_key，
            # 强制走「无 Key → dynamic_fallback_profile」的确定性离线分支。
            _cmp_cfg_path = Path(__file__).resolve().parent.parent / "ky_config.json"
            _cmp_cfg_snap = _cmp_cfg_path.read_text(encoding="utf-8") if _cmp_cfg_path.exists() else None
            try:
                if _cmp_cfg_snap is not None:
                    _cmp_cfg_now = json.loads(_cmp_cfg_snap)
                    if isinstance(_cmp_cfg_now, dict):
                        _cmp_cfg_now["api_key"] = ""
                        _cmp_cfg_path.write_text(
                            json.dumps(_cmp_cfg_now, ensure_ascii=False, indent=2), encoding="utf-8")
                compare_res = run_cli("compare", "不存在的甲校", "不存在的乙校", "计算机")
            finally:
                if _cmp_cfg_snap is not None:
                    _cmp_cfg_path.write_text(_cmp_cfg_snap, encoding="utf-8")
            runner.assert_true(compare_res.returncode == 0 and "未核验" in compare_res.stdout, "CLI smoke：未命中院校数据库时明确标注未核验")

            missing_diag = run_cli("diagnose", "missing-answer-card.txt")
            runner.assert_true(missing_diag.returncode != 0 and "找不到答题卡文件" in missing_diag.stdout, "CLI smoke：diagnose 不存在文件返回失败而非伪造报告")

            missing_ingest = run_cli("ingest", "missing-material.md")
            runner.assert_true(missing_ingest.returncode != 0 and "找不到文件" in missing_ingest.stdout, "CLI smoke：ingest 不存在文件返回失败")

            update_res = run_cli("build", timeout=60)
            runner.assert_true(update_res.returncode == 0 and (ROOT / "docs" / "index.html").stat().st_size > 10000, "CLI smoke：build 真实重编译看板")

            snapshot = json.loads((ROOT / "docs" / "state_snapshot.json").read_text(encoding="utf-8"))
            runner.assert_true(snapshot.get("meta", {}).get("sanitized") is True, "CLI smoke：默认 build 产物为脱敏快照")
            public_html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
            private_markers = ("暂未放置实体资料", "题库切片_", "四级已过 / 摸底水平分", "导数中值定理、计算失误")
            runner.assert_true(not any(marker in public_html for marker in private_markers), "发布安全：公开看板 HTML 不包含私有任务/资料文本")

            # [H-0 回归] 脱敏必须真正剥离可识别文本，而非只置 meta.sanitized=True。
            # 阴性测试：注入真实自命题科目名与自由文本告警，确认发布前被泛化/剥离。
            sys.path.insert(0, str(ROOT / "05-考研看板"))
            from web.snapshot import sanitize_public_data as _sanitize
            _leak = "自命题专业课科目"
            _probe = {
                "subjects": [{"key": "pro", "name": "专业课"}],
                "maps": {"pro": {"subject": "pro", "subject_name": _leak,
                                 "syllabus_warning": f"【{_leak}】考试大纲.md 仍为占位模板",
                                 "modules": {"x": 1}, "chapters": [], "total_points": 0}},
            }
            _safe = _sanitize(_probe)
            _safe_pro = _safe["maps"]["pro"]
            runner.assert_true(
                _leak not in json.dumps(_safe, ensure_ascii=False)
                and _safe_pro.get("subject_name") == "专业课"
                and "syllabus_warning" not in _safe_pro
                and "modules" not in _safe_pro,
                "发布安全：脱敏真实剥离自命题科目全称与 syllabus_warning（H-0 阴性测试）",
            )

            # 真实产物内容级校验：各 maps.subject_name 必须已泛化为通用短名，且不含 syllabus_warning
            _snap_data = snapshot.get("data", {})
            _generic = {s.get("key"): s.get("name") for s in _snap_data.get("subjects", [])}
            _bad = [sk for sk, m in (_snap_data.get("maps") or {}).items()
                    if isinstance(m, dict) and (("syllabus_warning" in m)
                                                or (sk in _generic and m.get("subject_name") != _generic[sk]))]
            runner.assert_true(
                not _bad,
                f"发布安全：state_snapshot.json 科目名已泛化且无 syllabus_warning（越界: {_bad}）",
            )

            # [H-0b 阴性测试] 雷达节的监控院校名/研究生院官网 URL 来自隐私目录
            # .memory/admission_watch.json，而 docs/index.html 会随 GitHub Pages 公开
            # （实测曾泄露 https://gra.example.edu.cn）。注入哨兵院校确认脱敏拦截。
            import tempfile as _tf
            import shutil as _sh
            _rroot = Path(_tf.mkdtemp(prefix="ky_radar_"))
            try:
                (_rroot / ".memory").mkdir(parents=True, exist_ok=True)
                (_rroot / ".memory" / "admission_watch.json").write_text(json.dumps({
                    "99999": {"name": "哨兵测试大学", "chsi_code": "99999",
                              "url": "https://gra.sentinel-probe.edu.cn",
                              "added_at": "2026-09-19 00:00", "last_check": "2026-09-19 00:00",
                              "recent_titles": ["哨兵测试大学2027年推荐免试研究生接收办法"]},
                }, ensure_ascii=False), encoding="utf-8")
                _radar_html = board_build.build_radar_html(_rroot)
                runner.assert_true(
                    "哨兵测试大学" not in _radar_html
                    and "sentinel-probe.edu.cn" not in _radar_html
                    and "推荐免试研究生接收办法" not in _radar_html,
                    "发布安全：雷达节在脱敏模式下不回显监控院校名/官网 URL/简章标题（H-0b 阴性测试）",
                )
                runner.assert_true(
                    "已配置 <b>1</b> 所监控院校" in _radar_html,
                    "发布安全：雷达节脱敏后仍如实输出已配置院校数量",
                )
            finally:
                _sh.rmtree(_rroot, ignore_errors=True)

            updater_text = (ROOT / "tools" / "update_dashboard.py").read_text(encoding="utf-8")
            default_guard = '"--push" not in sys.argv' in updater_text
            explicit_push = 'subprocess.run(["git", "push"]' in updater_text
            runner.assert_true(default_guard and explicit_push, "发布安全：update_dashboard 默认本地构建，仅 --push 才同步")

            updater_res = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "update_dashboard.py"), "--local"],
                cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            runner.assert_true(
                updater_res.returncode == 0 and "已跳过 Git 提交与推送" in updater_res.stdout,
                "发布安全：update_dashboard --local 真实执行且明确跳过推送",
            )

            # Test sync_publish.py --force to prevent regression of P0-2。
            # 公开发布副本中的 sync_publish.py 是刻意的占位模块，真实发布守卫
            # 只存在私有工作区；公开 CI 不应把占位模块缺少内部属性误报为回归。
            from tools import sync_publish as _sync_publish_mod
            if not hasattr(_sync_publish_mod, "DRY_RUN"):
                runner.skip(
                    "发布安全：sync_publish.py 私有导出守卫",
                    "公开副本使用 sync_publish.py 占位模块")
            else:
                # 本体守卫（与环境无关）：加 --allow-placeholder-identity 显式放行门禁，
                # 断言脚本「强制执行不崩溃」。逃生舱语义见 sync_publish.py 的 main()。
                sync_res = subprocess.run(
                    [sys.executable, str(ROOT / "tools" / "sync_publish.py"),
                     "--force", "--allow-placeholder-identity"],
                    cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                )
                runner.assert_true(
                    sync_res.returncode == 0 and "files copied" in sync_res.stdout,
                    "发布安全：sync_publish.py --force --allow-placeholder-identity 强制执行成功且不崩溃",
                )
                # 再跑一次**不带逃生舱**，按环境分支断言门禁行为。
                # CI 由 actions/checkout 全新检出，工作区里没有 ky_config.json
                #（.gitignore:40 保护、未跟踪）→ 动态身份规则必然无效 → 门禁应
                # fail-closed（exit 3 且打印「拒绝导出」），这本身是有价值的断言。
                from privacy_policy import identity_rules_effective
                _eff, _eff_reason = identity_rules_effective(ROOT)
                gate_res = subprocess.run(
                    [sys.executable, str(ROOT / "tools" / "sync_publish.py"), "--force"],
                    cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                )
                if _eff:
                    runner.assert_true(
                        gate_res.returncode == 0 and "files copied" in gate_res.stdout,
                        "发布安全：sync_publish.py --force（有身份规则时正常导出）",
                    )
                else:
                    runner.assert_true(
                        gate_res.returncode == 3 and "拒绝导出" in gate_res.stdout,
                        "发布安全：sync_publish.py --force（无身份规则时 fail-closed 拒绝导出）",
                    )
        except Exception as e:
            runner.assert_true(False, f"CLI 进程级 smoke tests 异常: {e}")

        # =========================================================================
        # 25. 新增功能专项集成测试 (WeChat 公众号经验检索、Rust 扩展双模加速、PySide6 桌面 GUI)
        # =========================================================================
        print("\n[测试组 25: 升级新功能专项集成校验 (WeChat + Rust + GUI)]")
        try:
            new_features_res = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "test_new_features.py")],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            # CI 环境下可选依赖 (PySide6/ky_rust_ext) 缺失时部分测试被 [SKIP]：
            # [P0 修复·对齐 9.1] 集成判定与子套件守门规则一致 ——
            # 只有出现「失败 N 项 (N>0)」才判红；仅有 SKIP 时提示补齐依赖但不阻断主套件，
            # 避免子套件"不能判定为全绿"的非零退出码被误读为功能失败。
            import re as _re_mod
            _nf_fail_m = _re_mod.search(r"失败 (\d+) 项", new_features_res.stdout)
            _nf_failed = int(_nf_fail_m.group(1)) if _nf_fail_m else -1
            _nf_skip_m = _re_mod.search(r"跳过 (\d+) 项", new_features_res.stdout)
            nf_passed = _nf_failed == 0
            runner.assert_true(
                nf_passed,
                "升级新功能专项：微信检索、Rust双模一致性与PySide6离屏测试全部通过 (可选依赖缺失项已安全跳过)",
            )
            if nf_passed and _nf_skip_m and int(_nf_skip_m.group(1)) > 0:
                print(f"    [NOTE] 子套件存在 {_nf_skip_m.group(1)} 项可选依赖跳过 (ky_rust_ext 等)，不判定为全绿")
            if not nf_passed:
                # 输出子进程详细信息帮助 CI 排错
                print(f"    [DEBUG] test_new_features.py returncode={new_features_res.returncode}")
                for line in new_features_res.stdout.splitlines()[-10:]:
                    print(f"    [DEBUG] {line}")
                for line in new_features_res.stderr.splitlines()[-5:]:
                    print(f"    [DEBUG stderr] {line}")
        except Exception as e:
            runner.assert_true(False, f"新功能集成测试异常: {e}")

        # =========================================================================
        # 26. 全国高校数据库构建器 (national_institutions.json)
        # 覆盖 2026-09-19 新增的三条硬约束：
        #   ① 已证伪的抓取阶段（研招网详情页/专业库）不得复活；
        #   ② 合并时手工条目权威度更高：标量覆盖、列表取并集；
        #   ③ 官网域名只来自 xioajiumi，绝不写入研招网样例模板（pku.edu.cn）。
        # =========================================================================
        print("\n[测试组 26: 全国高校数据库构建器 (反幻觉 / 合并语义 / 域名来源)]")
        import tempfile
        import shutil as _shutil
        try:
            sys.path.insert(0, str(ROOT / "tools" / "intelligence"))
            import university_db_builder as _ub

            # --- 26-1 已证伪的抓取阶段不得复活 ---
            runner.assert_true(
                not hasattr(_ub, "crawl_details") and not hasattr(_ub, "crawl_majors")
                and not hasattr(_ub, "CATEGORY_IDS"),
                "高校库 26-1：研招网详情页/专业库抓取阶段已删除（实测分别返回北大样例模板与「请登录」）",
            )
            _rejected = False
            try:
                _ub.main(["crawl-details"])
            except SystemExit as _se:
                _rejected = _se.code == 2
            runner.assert_true(_rejected, "高校库 26-2：已废弃子命令 crawl-details 被 argparse 拒绝")

            # --- 26-3 纯函数：空值判定与官网归一 ---
            runner.assert_true(
                _ub._is_blank("") and _ub._is_blank("待查") and _ub._is_blank(None)
                and not _ub._is_blank("河南"),
                "高校库 26-3：_is_blank 正确识别空串/待查/None",
            )
            runner.assert_true(
                _ub._official_domain("https://www.tsinghua.edu.cn/") == "https://www.tsinghua.edu.cn"
                and _ub._official_domain("http://x.edu.cn/deep/path?q=1") == "http://x.edu.cn"
                and _ub._official_domain("") == "",
                "高校库 26-4：_official_domain 归一为根地址，非法输入返回空串",
            )

            # --- 26-5 合并语义（用最小 fixture 真实跑一遍 merge） ---
            _tmp = Path(tempfile.mkdtemp(prefix="ky_uni_"))
            try:
                _src = _tmp / "src"
                _out = _tmp / "out"
                _data = _tmp / "data"
                for _d in (_src, _out, _data):
                    _d.mkdir(parents=True, exist_ok=True)

                (_src / "chsi_schools.json").write_text(json.dumps({
                    "count": 1,
                    "schools": [{
                        "name": "甲大学", "sch_id": "111", "dwdm": "10001",
                        "region": "河南", "authority": "河南省",
                        "tags": ["研究生院"], "source_url": "https://yz.chsi.com.cn/sch/x.dhtml",
                    }],
                }, ensure_ascii=False), encoding="utf-8")
                (_src / "chsi_408_offerings.json").write_text(json.dumps({
                    "items": [{
                        "schoolCode": "10001", "schoolName": "甲大学", "region": "河南",
                        "collegeName": "信息与管理科学学院",
                        "majorCode": "081200", "majorName": "计算机科学与技术",
                        "subjects": [{"code": "101", "name": "思想政治理论"},
                                     {"code": "408", "name": "计算机学科专业基础"}],
                    }],
                }, ensure_ascii=False), encoding="utf-8")
                (_src / "fjw_universities.json").write_text(json.dumps({
                    "provinces": {"河南": {"all": [{"name": "甲大学", "tags": ["211", "本科", "河南"]}]}},
                }, ensure_ascii=False), encoding="utf-8")
                (_src / "xioajiumi_universities.json").write_text(json.dumps({
                    "count": 1,
                    "universities": [{"name": "甲大学", "name_eng": "Jia University",
                                      "type": "综合", "official_link": "https://www.jia.edu.cn/"}],
                }, ensure_ascii=False), encoding="utf-8")
                (_src / "zsts_places.json").write_text(json.dumps({
                    "count": 1, "places": {"甲大学": {"province": "河南省", "city": "郑州市"}},
                }, ensure_ascii=False), encoding="utf-8")
                # 既有手工条目（权威度更高）
                (_data / "national_institutions.json").write_text(json.dumps({
                    "甲大学": {
                        "chsi_code": "10001", "name": "甲大学",
                        "aliases": ["甲大", "jia"], "level": ["省部共建高校"],
                        "region": "河南郑州", "official_domain": "https://www.jia.edu.cn",
                        "graduate_domain": "https://gra.jia.edu.cn",
                        "departments": {"马克思主义理论": {"college_name": "马克思主义学院",
                                                        "subjects": ["(101)思想政治理论"]}},
                    },
                }, ensure_ascii=False), encoding="utf-8")

                _old_src, _old_data = _ub.SOURCES_DIR, _ub.DATA_DIR
                _ub.SOURCES_DIR, _ub.DATA_DIR = _src, _data
                try:
                    _stats = _ub.merge(out_dir=_out)
                finally:
                    _ub.SOURCES_DIR, _ub.DATA_DIR = _old_src, _old_data

                _rec = json.loads((_out / "national_institutions.json").read_text(encoding="utf-8"))["甲大学"]
                runner.assert_true(
                    "甲大" in _rec["aliases"] and "jia" in _rec["aliases"] and "10001" in _rec["aliases"],
                    "高校库 26-5：手工 aliases 与抓取别名取并集，不被批量数据冲掉",
                )
                runner.assert_true(
                    "省部共建高校" in _rec["level"] and "硕士研究生招生单位" in _rec["level"],
                    "高校库 26-6：手工 level 与生成标签取并集（阴性回归：旧实现会整段覆盖）",
                )
                runner.assert_true(
                    _rec["official_domain"] == "https://www.jia.edu.cn"
                    and _rec["graduate_domain"] == "https://gra.jia.edu.cn",
                    "高校库 26-7：手工官网/研究生院域名优先于批量抓取值",
                )
                runner.assert_true(
                    set(_rec["departments"]) == {"马克思主义理论", "(081200)计算机科学与技术"},
                    "高校库 26-8：departments 以「学科专业」为键（院系名进 college_name），与消费侧关键词匹配语义一致",
                )
                runner.assert_true(
                    _rec["departments"]["(081200)计算机科学与技术"]["college_name"] == "信息与管理科学学院",
                    "高校库 26-9：408 科目挂到学科键上且保留院系名",
                )
                runner.assert_true(
                    not any("pku.edu.cn" in (v.get("official_domain") or "")
                            for v in json.loads((_out / "national_institutions.json").read_text(encoding="utf-8")).values()),
                    "高校库 26-10：合并产物不含研招网样例模板域名 pku.edu.cn（反幻觉阴性测试）",
                )
                runner.assert_true(
                    _stats.get("with_official") == 1 and _stats.get("enriched_manual") == 1,
                    f"高校库 26-11：合并统计正确 (with_official/enriched_manual)，实得 {_stats}",
                )
            finally:
                _shutil.rmtree(_tmp, ignore_errors=True)

            # --- 26-12 真实全国库：无代码院校不得把校名写进 chsi_code ---
            from tools.intelligence.registry import UniversityRegistry
            _reg = UniversityRegistry()
            _ni = json.loads((ROOT / "data" / "universities" / "national_institutions.json").read_text(encoding="utf-8"))
            _nocode = [k for k, v in _ni.items() if not v.get("chsi_code")]
            runner.assert_true(len(_nocode) > 0, "高校库 26-12：全国库存在无教育部代码院校（本科院校底座）")
            _probe_name = _nocode[0]
            _probe = _reg.resolve(_probe_name)
            runner.assert_true(
                _probe is not None and _probe.chsi_code != _probe_name
                and "UNLISTED" in _reg._name_map.get(_probe_name, ""),
                f"高校库 26-13：无代码院校 {_probe_name} 的 chsi_code 未被填成校名，且标记为 UNLISTED（未核验）",
            )
            # 用教育部代码解析该公开库条目，避免把某一所院校名写进
            # 会被导出脱敏的测试源码；代码本身是公开数据库键，不是私有身份。
            _henau = _reg.resolve("10466")
            runner.assert_true(
                _henau is not None and _henau.chsi_code == "10466"
                and "pku.edu.cn" not in _henau.official_domain,
                "高校库 26-14：公开院校代码 10466 可解析且官网非样例域名",
            )
            _polluted = [e.name for e in {id(x): x for x in _reg._entities.values()}.values()
                         if "pku.edu.cn" in (e.official_domain or "") and e.name != "北京大学"]
            runner.assert_true(not _polluted, f"高校库 26-15：全国库无样例域名污染（越界: {_polluted[:5]}）")
        except Exception as e:
            runner.assert_true(False, f"全国高校数据库测试异常: {e}")

    finally:
        # 还原现场：把主流程前快照的受控用户数据写回，并清理测试残留文件。
        restored, removed = _restore_guarded()
        print(f"\n  [i] 测试现场已还原：{restored} 个用户数据文件回滚，{removed} 个测试残留清理")

    # 统计并返回
    success = runner.print_summary()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    run_tests()
