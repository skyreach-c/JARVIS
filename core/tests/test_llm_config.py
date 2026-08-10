import os
from pathlib import Path

import pytest

from jarvis_core.llm.config import (
    DeepSeekSettings,
    find_project_root,
    load_deepseek_settings,
)

DEEPSEEK_ENV_VARS = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "DEEPSEEK_THINKING_MODE",
)


@pytest.fixture(autouse=True)
def clean_deepseek_environment(monkeypatch: pytest.MonkeyPatch):
    for name in DEEPSEEK_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield
    for name in DEEPSEEK_ENV_VARS:
        os.environ.pop(name, None)


def test_settings_use_defaults_and_normalize_an_empty_api_key(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")

    settings = load_deepseek_settings(tmp_path)

    assert settings == DeepSeekSettings(
        api_key=None,
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        thinking_mode="disabled",
    )


def test_process_environment_takes_precedence_over_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env").write_text(
        "DEEPSEEK_API_KEY=file-key\n"
        "DEEPSEEK_BASE_URL=https://file.example\n"
        "DEEPSEEK_MODEL=file-model\n"
        "DEEPSEEK_THINKING_MODE=disabled\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "process-model")
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "enabled")

    settings = load_deepseek_settings(tmp_path)

    assert settings.api_key == "process-key"
    assert settings.base_url == "https://file.example"
    assert settings.model == "process-model"
    assert settings.thinking_mode == "enabled"


def test_invalid_thinking_mode_is_rejected(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DEEPSEEK_THINKING_MODE=automatic\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="DEEPSEEK_THINKING_MODE"):
        load_deepseek_settings(tmp_path)


def test_project_root_location_is_independent_of_current_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    anchor = project_root / "core" / "src" / "jarvis_core" / "llm" / "config.py"
    anchor.parent.mkdir(parents=True)
    anchor.write_text("", encoding="utf-8")
    (project_root / "core" / "pyproject.toml").write_text("", encoding="utf-8")
    unrelated_cwd = tmp_path / "elsewhere"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)

    assert find_project_root(anchor) == project_root.resolve()
