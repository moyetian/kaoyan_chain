# -*- coding: utf-8 -*-
"""P26/P27 回归测试：GUI 报到不得用「局部配置字典」覆盖学员真实备考方案。

背景（多角色端到端测试审查报告 P25 排查中发现的现场事故）：
    tools/gui/workers/agent_worker.py 的科目报到分支原本这样写：

        cfg = dict(self.config) if self.config else {}
        ...
        cfg["active_subject"] = subj
        save_config(cfg)          # ← 写入硬编码的 ROOT/ky_config.json

    self.config 是**调用方传入的局部字典**（GUI 快捷药丸、单元测试等场景下
    可能只有 {"api_key","model"} 两个键）。它被原样写盘后，磁盘上的
    study_plan（目标院校 / 报考专业 / 各科目标分 / 每日时长）、webhooks、
    base_url 等全部键被整份覆盖 —— 学员辛苦填的备考方案无声消失，
    现场只留 3 个键、且没有任何报错。

    事故现场：仓库真实 ky_config.json 被测成了
        {"api_key":"sk-test-fake","model":"deepseek-chat","active_subject":"pol"}
    而完整方案只侥幸从 .memory/agents_backups/AGENTS_backup_*.md 里找回。

本测试锁定两件事：
    P26 —— 报到写回必须是「以磁盘配置为基底的合并」，不得丢键；
    P27 —— 测试自身必须把配置路径重定向到 tmp，绝不碰开发者真实配置。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _shared_modules():
    """本项目存在 cli.shared / tools.cli.shared 双导入，两边都要打补丁。

    注意 tools/ky_cli.py 的导入顺序是 **优先** ``tools.cli.shared``、失败才回退
    ``cli.shared``，而二者是两个独立的模块对象、各有一份 CONFIG_FILE 全局。
    只补一个会让 save_config 写回真实仓库配置（本测试的 P27 就是为此而写）。
    """
    mods = []
    for name in ("tools.cli.shared", "cli.shared"):
        try:
            __import__(name)
        except ImportError:
            continue
        mods.append(sys.modules[name])
    if not mods:
        raise RuntimeError("无法导入 cli.shared / tools.cli.shared，测试环境异常")
    return mods


_FULL_CONFIG = {
    "api_provider": "custom",
    "base_url": "https://api.example.test/v1",
    "api_key": "sk-real-user-key",
    "model": "deepseek-chat",
    "temperature": 0.3,
    "active_subject": "math",
    "webhooks": {"dingtalk": "https://hook.example.test/ding", "wechat": ""},
    "onboarding_completed": True,
    "study_plan": {
        "target_year": "2027",
        "exam_date": "2026-12-19",
        "stage_name": "强化题型攻坚阶段",
        "school": "中国人民大学",
        "major": "030100 法学",
        "math_key": "none",
        "math_name": "不考数学",
        "pro_name": "610 法学基础 810 法学综合",
        "total_hours": 6.5,
        "eng_target": "65+ 分",
        "pol_target": "70+ 分",
        "pro_target": "120-130 分",
    },
    "completion_history": {"2026-09-18": 3},
}


def _run_checkin(tmp_cfg_file, monkeypatch, worker_config, user_input="政治报到"):
    """在受控配置路径下驱动一次 GUI 科目报到。"""
    tmp_cfg_file.write_text(
        json.dumps(_FULL_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for m in _shared_modules():
        monkeypatch.setattr(m, "CONFIG_FILE", tmp_cfg_file, raising=False)

    # 屏蔽真实大模型调用：报到命中后代码会继续走 AgentRunner
    try:
        import agent.loop as loop_mod  # noqa: PLC0415
    except ImportError:
        import tools.agent.loop as loop_mod  # noqa: PLC0415
    monkeypatch.setattr(loop_mod.AgentRunner, "run",
                        lambda self, *a, **k: "【私教带背】今日政治核心考点…",
                        raising=True)

    from tools.gui.workers.agent_worker import AgentWorker  # noqa: PLC0415
    worker = AgentWorker(config=worker_config, user_input=user_input)
    replies = []
    worker.finished_signal.connect(replies.append)
    worker.run()
    return json.loads(tmp_cfg_file.read_text(encoding="utf-8"))


def test_partial_config_does_not_destroy_study_plan(tmp_path, monkeypatch):
    """P26：只带 api_key/model 的局部字典报到后，备考方案必须完好。"""
    after = _run_checkin(
        tmp_path / "ky_config.json", monkeypatch,
        worker_config={"api_key": "sk-test-fake", "model": "deepseek-chat"},
    )

    assert after["active_subject"] == "pol", "报到未切换当前科目"
    assert "study_plan" in after, "局部配置覆盖导致 study_plan 整份丢失"
    assert after["study_plan"]["school"] == "中国人民大学"
    assert after["study_plan"]["major"] == "030100 法学"
    assert after["study_plan"]["total_hours"] == 6.5
    assert after["study_plan"]["pro_name"].startswith("610 法学基础")
    assert after["webhooks"]["dingtalk"] == "https://hook.example.test/ding"
    assert after["base_url"] == "https://api.example.test/v1"
    assert after["completion_history"] == {"2026-09-18": 3}


def test_partial_config_values_still_win_for_their_own_keys(tmp_path, monkeypatch):
    """合并语义：调用方显式给的键仍应生效，只是不能牵连其它键。"""
    after = _run_checkin(
        tmp_path / "ky_config.json", monkeypatch,
        worker_config={"api_key": "sk-override", "model": "mimo-v2.5"},
    )
    assert after["api_key"] == "sk-override"
    assert after["model"] == "mimo-v2.5"
    assert after["study_plan"]["school"] == "中国人民大学"


def test_empty_config_keeps_disk_values(tmp_path, monkeypatch):
    """config 为空时不得把磁盘配置清空。"""
    after = _run_checkin(
        tmp_path / "ky_config.json", monkeypatch, worker_config={},
    )
    assert after["study_plan"]["school"] == "中国人民大学"
    assert after["active_subject"] == "pol"


@pytest.mark.parametrize("cmd", ["数学报到", "英语报到", "专业课报到"])
def test_all_checkin_commands_preserve_plan(tmp_path, monkeypatch, cmd):
    """四个科目的报到口令都不得丢键。"""
    after = _run_checkin(
        tmp_path / "ky_config.json", monkeypatch,
        worker_config={"api_key": "sk-test-fake", "model": "deepseek-chat"},
        user_input=cmd,
    )
    assert after["study_plan"]["school"] == "中国人民大学"


def test_real_repo_config_is_never_touched(tmp_path, monkeypatch):
    """P27：测试必须把配置路径重定向到 tmp，绝不动仓库真实 ky_config.json。"""
    real_cfg = ROOT / "ky_config.json"
    before = real_cfg.read_bytes() if real_cfg.exists() else None

    _run_checkin(
        tmp_path / "ky_config.json", monkeypatch,
        worker_config={"api_key": "sk-test-fake", "model": "deepseek-chat"},
    )

    after = real_cfg.read_bytes() if real_cfg.exists() else None
    assert after == before, (
        "测试污染了开发者真实 ky_config.json —— 必须 monkeypatch CONFIG_FILE 到 tmp 路径"
    )
