import json
import pytest
from tools.gui.services.settings import save_settings


def values(**updates):
    return dict(api_key="test-key", base_url="http://localhost:11434/v1", model="test",
                school="测试大学", major="哲学", exam_date="2026-12-19",
                style="温和启发", **updates)


def test_settings_update_consumed_fields_without_losing_existing_config(tmp_path):
    path = tmp_path / "ky_config.json"
    path.write_text(json.dumps({"study_plan": {"math_key": "none"}, "webhooks": {"x": "y"}}))
    saved = save_settings(path, **values())
    assert saved["base_url"] == "http://localhost:11434/v1"
    assert saved["study_plan"]["school"] == "测试大学"
    assert saved["study_plan"]["math_key"] == "none"
    assert saved["study_plan"]["style_name"] == saved["coaching_style"]
    assert saved["webhooks"] == {"x": "y"}


def test_invalid_and_corrupted_settings_are_not_overwritten(tmp_path):
    path = tmp_path / "ky_config.json"
    path.write_text("{broken")
    with pytest.raises(ValueError):
        save_settings(path, **values())
    assert path.read_text() == "{broken"
    options = values()
    options["exam_date"] = "2026-02-30"
    with pytest.raises(ValueError):
        save_settings(path, **options)
    assert path.read_text() == "{broken"
