from types import SimpleNamespace
import pytest
from tools.cli.menu_args import parse_menu_args
from tools.intelligence.watcher import AdmissionWatcher


@pytest.mark.parametrize("argv", [
    ["5", "--file=a.md", "--subject=pro"],
    ["--action", "5", "--file", "a.md", "--subject", "pro"],
    ["-a", "5", "-f", "a.md", "--subject=pro"],
])
def test_menu_forms_forward_file_and_subject(argv):
    action, listed, extra = parse_menu_args(argv)
    assert action == "5" and not listed
    assert extra["file"] == "a.md" and extra["subject"] == "pro"


def test_watch_migration_baselines_all_titles_before_alerting(monkeypatch):
    watcher = AdmissionWatcher.__new__(AdmissionWatcher)
    titles = [f"2027硕士招生简章第{i}项" for i in range(20)]
    watcher.watch_data = {"x": {"name": "测试大学", "url": "https://example.edu.cn",
                               "last_hash": "legacy", "recent_titles": titles[:8], "updates": []}}
    watcher.fetcher = SimpleNamespace(fetch=lambda url: SimpleNamespace(
        is_valid=True, content="page"))
    monkeypatch.setattr(watcher, "_extract_recent_titles", lambda text: list(titles))
    monkeypatch.setattr(watcher, "_save", lambda: None)
    assert watcher.check_updates()[0]["status"] == "BASELINED"
    assert watcher.check_updates()[0]["status"] == "UNCHANGED"
    titles.append("2027硕士招生专业目录调整公告")
    finding = watcher.check_updates()[0]
    assert finding["status"] == "UPDATED"
    assert finding["alert_titles"] == [titles[-1]]


def test_watch_first_check_with_short_baseline_does_not_false_alarm(monkeypatch):
    """P2-4 回归：旧基线只有 8 条标题但标了 baseline_complete（ky mount 自动建档
    或旧版本快照）时，首检必须只补基线、不把 2017-2026 历史公告当新动态误报；
    补完基线后真正的新增照常告警。"""
    watcher = AdmissionWatcher.__new__(AdmissionWatcher)
    old = [f"2017年报名录取统计（第{i}批）" for i in range(8)]
    current = list(old) + [f"2026年复试基本线（第{i}批）" for i in range(12)]
    watcher.watch_data = {"x": {"name": "测试大学", "url": "https://example.edu.cn",
                               "last_hash": "legacy", "recent_titles": list(old),
                               "baseline_complete": True, "updates": []}}
    watcher.fetcher = SimpleNamespace(fetch=lambda url: SimpleNamespace(
        is_valid=True, content="page"))
    monkeypatch.setattr(watcher, "_extract_recent_titles", lambda text: list(current))
    monkeypatch.setattr(watcher, "_save", lambda: None)
    first = watcher.check_updates()[0]
    assert first["status"] == "BASELINED", first
    current.append("2027硕士招生专业目录调整公告")
    second = watcher.check_updates()[0]
    assert second["status"] == "UPDATED"
    assert second["alert_titles"] == ["2027硕士招生专业目录调整公告"]
