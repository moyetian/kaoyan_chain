# -*- coding: utf-8 -*-
"""W2 core50 评测适配层 —— 回归测试。

对应升级规划 W2：数据集入库本仓库（``tests/benchmarks/core50/``）+ 适配层
（``tools/benchmarks/core50_runner.py``）接 CI。本文件覆盖：

1. **数据集完整性（离线，纯文件检查）**：冒烟题单 10 题 / 8 类、每题都有
   ``task.json``、数据集为纯数据（不含 .py）、运行产物 ``results/`` 被
   .gitignore 排除。
2. **适配层三态退出码**：0=通过 / 1=不达标 / 2=不可评测，逐条验证——
   评测器缺失、数据集缺失、题数不符、suite JSON 缺 grades、评测器执行失败
   全部归 2；mock 有题 FAIL 归 1；全 PASS 归 0。
3. **门禁阈值（--gate）**：成功率 -3pp / 任一维 -10 点；边界值 -10 恰好通过、
   -10.1 判失败；输入缺失/损坏归 2。
4. **评测器探测**：--bench-cli 显式路径命中；四路全不可用返回 None。

全程离线：用**桩评测器**（stub bench CLI，写进 tmp_path 的 .cmd/.sh 启动器）
替代真实 KaoyanBench —— 真实评测器只在 CI 的 core50 作业里跑（pip 安装）。
桩按环境变量切换行为（STUB_MODE / STUB_VALIDATE_RC），不依赖网络与 LLM。
"""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.benchmarks import core50_runner as cr  # noqa: E402

DATA_ROOT = ROOT / "tests" / "benchmarks" / "core50"

#: 桩评测器源码：模拟真实 CLI 的 --version / validate / run 三个子路径。
#: 环境变量：STUB_MODE=pass|fail|short|no_grades；STUB_VALIDATE_RC=<int>。
STUB_SRC = r'''# -*- coding: utf-8 -*-
import json, os, sys
from pathlib import Path

KNOWN_CMDS = {"list", "run", "grade", "report", "compare", "regression",
              "validate", "fixtures", "snapshot", "suite", "agent"}


def _arg(name, default=None):
    if name in sys.argv:
        return sys.argv[sys.argv.index(name) + 1]
    return default


if "--version" in sys.argv:
    print("stub-kaoyanbench 9.9.9")
    raise SystemExit(0)

cmd = next((a for a in sys.argv[1:] if a in KNOWN_CMDS), "")
root = Path(_arg("--root") or ".")
mode = os.environ.get("STUB_MODE", "pass")

if cmd == "validate":
    rc = int(os.environ.get("STUB_VALIDATE_RC", "0"))
    print("stub validate rc=%d" % rc)
    raise SystemExit(rc)

if cmd == "run":
    run_rc = int(os.environ.get("STUB_RUN_RC", "0"))
    if run_rc != 0:
        print("stub run failed rc=%d" % run_rc)
        raise SystemExit(run_rc)
    tasks_file = _arg("--tasks-file")
    suite = _arg("--suite")
    suite_id = "(tasks-file)" if tasks_file else (suite or "unknown")
    agent = _arg("--agent") or "mock"
    tag = _arg("--tag") or "untagged"
    n = int(os.environ.get("STUB_N", "0")) or (10 if tasks_file else 50)
    if mode == "short":
        n -= 1
    grades = []
    for i in range(n):
        ok = not (mode == "fail" and i == 0)
        grades.append({
            "task_id": "T-%03d" % i,
            "metrics": {"task_success": ok},
            "checks": [{"id": "c1", "passed": ok, "detail": "stub check %d" % i}],
            "degraded_reason": None,
        })
    rate = 1.0 if mode != "fail" else (max(n - 1, 0) / n if n else 0.0)
    data = {
        "schema_version": 1,
        "suite_id": suite_id,
        "tag": tag,
        "task_count": n,
        "aggregates": {"n_tasks": n, "task_success_rate": rate,
                       "score_mean": 90.0},
        "score_breakdown_mean": {"factuality": 90.0, "total": 90.0},
        # 保真要点：真实评测器（1.1.0）的 runs[].success 恒为 null —— 每题
        # 成败只看 grades[].metrics.task_success。桩必须复刻这一点，否则
        # 「按 runs[].success 判失败」的回归不会被测试抓住。
        "runs": [{"task_id": "T-%03d" % i, "success": None} for i in range(n)],
    }
    if mode != "no_grades":
        data["grades"] = grades
    out = root / "results" / "suites" / ("%s__%s__%s.suite.json" % (suite_id, agent, tag))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print("stub run wrote %s" % out)
    raise SystemExit(0)

print("stub: unknown command %r" % cmd)
raise SystemExit(3)
'''


@pytest.fixture()
def stub_bench(tmp_path: Path) -> Path:
    """桩评测器启动器：Windows 用 .cmd，POSIX 用 +x 的 sh 脚本。"""
    stub_py = tmp_path / "stub_bench.py"
    stub_py.write_text(STUB_SRC, encoding="utf-8")
    if os.name == "nt":
        launcher = tmp_path / "stub_bench.cmd"
        # [W12 门禁实测] .cmd 必须按 ANSI 码页（mbcs）写：cmd.exe 读批处理
        # 不用 UTF-8。若 tmp_path 含中文（如 basetemp 落在 D:\测试\ 或用户
        # 名是中文），UTF-8 写盘会被按 GBK 读成乱码路径 → 显式候选启动失败
        # → find_bench_cli 静默回退到系统里真实安装的 kaoyanbench → 6 条
        # runner 测试假红（本机实测 rc=2「can't open file '娴嬭瘯...'」）。
        launcher.write_text(
            f'@echo off\r\n"{sys.executable}" "{stub_py}" %*\r\n',
            encoding="mbcs")
    else:
        launcher = tmp_path / "stub_bench.sh"
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{stub_py}" "$@"\n',
            encoding="utf-8")
        launcher.chmod(0o755)
    return launcher


def _synthetic_data_root(tmp_path: Path) -> Path:
    """造一个最小数据集根（冒烟题单 10 题），供 run 模式测试用。"""
    root = tmp_path / "core50"
    root.mkdir()
    (root / "smoke10.tasks.txt").write_text(
        "\n".join(f"T-{i:03d}" for i in range(10)) + "\n", encoding="utf-8")
    return root


def _suite_json(tmp_path: Path, name: str, *, success_rate: float = 1.0,
                factuality: float = 90.0, raw: str = None) -> Path:
    """造一个门禁测试用的 suite JSON（结构对齐评测器真实输出）。"""
    path = tmp_path / name
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
        return path
    path.write_text(json.dumps({
        "task_count": 10,
        "aggregates": {"task_success_rate": success_rate, "score_mean": 90.0},
        "score_breakdown_mean": {"factuality": factuality, "total": 90.0},
    }, ensure_ascii=False), encoding="utf-8")
    return path


# ── 数据集完整性（离线文件检查）────────────────────────────────────────


def test_find_repo_root_is_repo():
    assert cr.find_repo_root() == ROOT


def test_real_data_root_layout():
    """入库的数据集必须保持上游布局的关键入口。"""
    assert (DATA_ROOT / "benchmark" / "tasks" / "public").is_dir()
    assert (DATA_ROOT / "config" / "agents" / "mock.yaml").is_file()
    assert (DATA_ROOT / "smoke10.tasks.txt").is_file()
    assert cr.resolve_data_root(None) == DATA_ROOT


def test_smoke_tasks_file_integrity():
    """冒烟题单：10 题、去重、8 类覆盖、每题在数据集里真实存在。"""
    lines = [ln.strip() for ln in
             (DATA_ROOT / "smoke10.tasks.txt").read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    assert len(lines) == cr.SMOKE_EXPECTED_N
    assert len(set(lines)) == len(lines)
    assert all(ln.replace("-", "").isalnum() and ln.split("-")[-1].isdigit()
               for ln in lines)
    prefixes = {ln.split("-")[0] for ln in lines}
    assert len(prefixes) == 8, f"冒烟题单应覆盖 8 类，实际 {sorted(prefixes)}"

    task_dirs = {p.name for p in
                 (DATA_ROOT / "benchmark" / "tasks" / "public").glob("*/*")}
    missing = [ln for ln in lines if ln not in task_dirs]
    assert not missing, f"题单里的题在数据集中不存在: {missing}"
    for tid in lines:
        assert list((DATA_ROOT / "benchmark" / "tasks" / "public").glob(f"*/{tid}/task.json")), \
            f"{tid} 缺 task.json"


def test_dataset_is_pure_data():
    """入库的只是数据集：不得夹带任何 .py（评测器代码保持独立仓库）。"""
    py_files = [p for p in DATA_ROOT.rglob("*.py")]
    assert py_files == [], f"数据集目录混入 Python 文件: {py_files[:5]}"


def test_results_dir_is_gitignored():
    """评测运行产物 results/ 必须被忽略，否则每次跑评测都污染工作区。"""
    gi = DATA_ROOT / ".gitignore"
    assert gi.is_file()
    content = gi.read_text(encoding="utf-8")
    assert "results/" in content


# ── 评测器探测 ─────────────────────────────────────────────────────────


def test_find_bench_cli_uses_explicit(stub_bench):
    found = cr.find_bench_cli(str(stub_bench))
    assert found is not None
    cmd, version = found
    assert cmd == [str(stub_bench)]
    assert version.startswith("stub-kaoyanbench")


def test_find_bench_cli_none_when_all_unavailable(monkeypatch):
    monkeypatch.delenv("KAOYANBENCH_CLI", raising=False)
    monkeypatch.setattr(cr.shutil, "which", lambda name: None)

    def _boom(*a, **k):
        raise OSError("not found")

    monkeypatch.setattr(cr.subprocess, "run", _boom)
    assert cr.find_bench_cli(None) is None


# ── main：不可评测（2）的各类入口 ──────────────────────────────────────


def test_main_insufficient_when_bench_missing(monkeypatch, capsys):
    monkeypatch.setattr(cr, "find_bench_cli", lambda explicit=None: None)
    rc = cr.main(["--smoke"])
    out = capsys.readouterr().out
    assert rc == cr.EXIT_INSUFFICIENT == 2
    assert "pip install" in out


def test_main_insufficient_when_data_root_missing(tmp_path, stub_bench):
    rc = cr.main(["--check", "--data-root", str(tmp_path / "nope"),
                  "--bench-cli", str(stub_bench)])
    assert rc == cr.EXIT_INSUFFICIENT


def test_check_mode_passthrough_ok(tmp_path, stub_bench, monkeypatch):
    monkeypatch.setenv("STUB_VALIDATE_RC", "0")
    rc = cr.main(["--check", "--data-root", str(_synthetic_data_root(tmp_path)),
                  "--bench-cli", str(stub_bench)])
    assert rc == cr.EXIT_OK


def test_check_mode_failure_maps_to_insufficient(tmp_path, stub_bench, monkeypatch):
    """校验失败 = 数据集坏了 = 2（不可评测），不是 1（评测结论为负）。"""
    monkeypatch.setenv("STUB_VALIDATE_RC", "3")
    rc = cr.main(["--check", "--data-root", str(_synthetic_data_root(tmp_path)),
                  "--bench-cli", str(stub_bench)])
    assert rc == cr.EXIT_INSUFFICIENT


# ── run 模式：三态 ─────────────────────────────────────────────────────


def test_smoke_all_pass_returns_ok(tmp_path, stub_bench, capsys):
    rc = cr.main(["--smoke", "--data-root", str(_synthetic_data_root(tmp_path)),
                  "--bench-cli", str(stub_bench), "--tag", "t_pass"])
    out = capsys.readouterr().out
    assert rc == cr.EXIT_OK
    assert "全部通过" in out


def test_smoke_failure_returns_fail(tmp_path, stub_bench, monkeypatch, capsys):
    monkeypatch.setenv("STUB_MODE", "fail")
    rc = cr.main(["--smoke", "--data-root", str(_synthetic_data_root(tmp_path)),
                  "--bench-cli", str(stub_bench), "--tag", "t_fail"])
    out = capsys.readouterr().out
    assert rc == cr.EXIT_FAIL == 1
    assert "未全部通过" in out
    assert "FAIL T-000" in out and "stub check 0" in out


def test_smoke_task_count_mismatch_returns_insufficient(tmp_path, stub_bench,
                                                        monkeypatch):
    monkeypatch.setenv("STUB_MODE", "short")
    rc = cr.main(["--smoke", "--data-root", str(_synthetic_data_root(tmp_path)),
                  "--bench-cli", str(stub_bench), "--tag", "t_short"])
    assert rc == cr.EXIT_INSUFFICIENT


def test_smoke_missing_grades_returns_insufficient(tmp_path, stub_bench,
                                                   monkeypatch, capsys):
    """grades 段缺失 = 评测器版本不兼容 → 2，绝不按 runs[].success 瞎判。"""
    monkeypatch.setenv("STUB_MODE", "no_grades")
    rc = cr.main(["--smoke", "--data-root", str(_synthetic_data_root(tmp_path)),
                  "--bench-cli", str(stub_bench), "--tag", "t_nog"])
    out = capsys.readouterr().out
    assert rc == cr.EXIT_INSUFFICIENT
    assert "grades" in out


def test_smoke_tasks_file_missing_returns_insufficient(tmp_path, stub_bench):
    root = tmp_path / "empty_root"
    root.mkdir()
    rc = cr.main(["--smoke", "--data-root", str(root),
                  "--bench-cli", str(stub_bench)])
    assert rc == cr.EXIT_INSUFFICIENT


def test_smoke_bench_run_crash_returns_insufficient(tmp_path, stub_bench,
                                                    monkeypatch):
    """评测器 run 子命令返回非 0（执行失败）→ 2。"""
    monkeypatch.setenv("STUB_RUN_RC", "4")
    rc = cr.main(["--smoke", "--data-root", str(_synthetic_data_root(tmp_path)),
                  "--bench-cli", str(stub_bench), "--tag", "t_crash"])
    assert rc == cr.EXIT_INSUFFICIENT


def test_full_mode_writes_core50_suite(tmp_path, stub_bench):
    """--full 走 core50 suite（50 题），suite JSON 命名按 suite__agent__tag。"""
    root = _synthetic_data_root(tmp_path)
    rc = cr.main(["--full", "--data-root", str(root),
                  "--bench-cli", str(stub_bench), "--tag", "t_full"])
    assert rc == cr.EXIT_OK
    expected = root / "results" / "suites" / "core50__mock__t_full.suite.json"
    assert expected.is_file()


# ── 门禁（--gate）：阈值与边界 ─────────────────────────────────────────


def test_gate_missing_args_returns_insufficient():
    assert cr.main(["--gate"]) == cr.EXIT_INSUFFICIENT


def test_gate_missing_file_returns_insufficient(tmp_path):
    base = _suite_json(tmp_path, "base.json")
    rc = cr.main(["--gate", "--baseline", str(base),
                  "--current", str(tmp_path / "absent.json")])
    assert rc == cr.EXIT_INSUFFICIENT


def test_gate_identical_passes(tmp_path):
    base = _suite_json(tmp_path, "base.json")
    same = _suite_json(tmp_path, "same.json")
    assert cr.main(["--gate", "--baseline", str(base), "--current", str(same)]) == cr.EXIT_OK


def test_gate_success_rate_drop_fails(tmp_path, capsys):
    base = _suite_json(tmp_path, "base.json", success_rate=1.0)
    cur = _suite_json(tmp_path, "cur.json", success_rate=0.95)  # -5pp > 3pp
    rc = cr.main(["--gate", "--baseline", str(base), "--current", str(cur)])
    out = capsys.readouterr().out
    assert rc == cr.EXIT_FAIL
    assert "成功率下降" in out


def test_gate_success_rate_within_tolerance_passes(tmp_path):
    base = _suite_json(tmp_path, "base.json", success_rate=1.0)
    cur = _suite_json(tmp_path, "cur.json", success_rate=0.98)  # -2pp ≤ 3pp
    assert cr.main(["--gate", "--baseline", str(base), "--current", str(cur)]) == cr.EXIT_OK


def test_gate_dim_drop_fails(tmp_path, capsys):
    base = _suite_json(tmp_path, "base.json", factuality=90.0)
    cur = _suite_json(tmp_path, "cur.json", factuality=79.0)  # -11 点 > 10
    rc = cr.main(["--gate", "--baseline", str(base), "--current", str(cur)])
    out = capsys.readouterr().out
    assert rc == cr.EXIT_FAIL
    assert "factuality" in out


def test_gate_dim_drop_at_threshold_passes(tmp_path):
    """边界：恰好 -10.0 通过（判据是「降超 10 点」才失败）。"""
    base = _suite_json(tmp_path, "base.json", factuality=90.0)
    cur = _suite_json(tmp_path, "cur.json", factuality=80.0)
    assert cr.main(["--gate", "--baseline", str(base), "--current", str(cur)]) == cr.EXIT_OK


# ── load_suite_json：结构防线 ──────────────────────────────────────────


@pytest.mark.parametrize("payload", [
    "{not json",
    "[1, 2, 3]",
    '{"task_count": 10}',
    '{"aggregates": []}',
])
def test_load_suite_json_rejects_malformed(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_text(payload, encoding="utf-8")
    assert cr.load_suite_json(path) is None
    assert cr.load_suite_json(tmp_path / "absent.json") is None
