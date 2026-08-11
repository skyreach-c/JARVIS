import os
from pathlib import Path

import pytest

from jarvis_core.llm.config import (
    DeepSeekSettings,
    PackyCodeSettings,
    find_project_root,
    load_deepseek_settings,
    load_llm_settings,
)

DEEPSEEK_ENV_VARS = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "DEEPSEEK_THINKING_MODE",
)
JARVIS_LLM_ENV_VARS = (
    "JARVIS_CHAT_PROFILE",
    "JARVIS_PACKYCODE_BASE_URL",
    "JARVIS_PACKYCODE_API_KEY",
    "JARVIS_PROFILE_REASONING_STRONG_MODEL",
    "JARVIS_PROFILE_REASONING_STRONG_EFFORT",
)


@pytest.fixture(autouse=True)
def clean_deepseek_environment(monkeypatch: pytest.MonkeyPatch):
    for name in (*DEEPSEEK_ENV_VARS, *JARVIS_LLM_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    yield
    for name in (*DEEPSEEK_ENV_VARS, *JARVIS_LLM_ENV_VARS):
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


def test_llm_settings_define_default_profiles_without_requiring_packycode_key(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text("JARVIS_PACKYCODE_API_KEY=\n", encoding="utf-8")

    settings = load_llm_settings(tmp_path)

    assert settings.chat_profile == "chat_default"
    assert settings.packycode == PackyCodeSettings(
        api_key=None,
        base_url="https://www.packyapi.com/v1",
    )
    assert settings.reasoning_strong_model == "gpt-5.6-sol"
    assert settings.reasoning_strong_effort == "low"


def test_llm_settings_allow_environment_profile_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env").write_text(
        "JARVIS_CHAT_PROFILE=chat_default\n"
        "JARVIS_PACKYCODE_API_KEY=file-key\n"
        "JARVIS_PROFILE_REASONING_STRONG_EFFORT=low\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("JARVIS_CHAT_PROFILE", "reasoning_strong")
    monkeypatch.setenv("JARVIS_PACKYCODE_API_KEY", "process-key")
    monkeypatch.setenv("JARVIS_PROFILE_REASONING_STRONG_MODEL", "custom-sol")
    monkeypatch.setenv("JARVIS_PROFILE_REASONING_STRONG_EFFORT", "medium")

    settings = load_llm_settings(tmp_path)

    assert settings.chat_profile == "reasoning_strong"
    assert settings.packycode.api_key == "process-key"
    assert settings.reasoning_strong_model == "custom-sol"
    assert settings.reasoning_strong_effort == "medium"


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("JARVIS_CHAT_PROFILE", "structured_router"),
        ("JARVIS_CHAT_PROFILE", "agent_brain"),
        ("JARVIS_CHAT_PROFILE", "unknown"),
        ("JARVIS_PROFILE_REASONING_STRONG_EFFORT", "automatic"),
    ],
)
def test_llm_settings_reject_invalid_chat_profile_or_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match=variable):
        load_llm_settings(tmp_path)
