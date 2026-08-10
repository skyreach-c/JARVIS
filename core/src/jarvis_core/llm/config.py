import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from dotenv import load_dotenv

from jarvis_core.llm.profiles import ChatProfileName, ReasoningEffort

ThinkingMode = Literal["disabled", "enabled"]


@dataclass(frozen=True)
class DeepSeekSettings:
    api_key: str | None
    base_url: str
    model: str
    thinking_mode: ThinkingMode


@dataclass(frozen=True)
class PackyCodeSettings:
    api_key: str | None
    base_url: str


@dataclass(frozen=True)
class LLMSettings:
    deepseek: DeepSeekSettings
    packycode: PackyCodeSettings
    chat_profile: ChatProfileName
    reasoning_strong_model: str
    reasoning_strong_effort: ReasoningEffort


def find_project_root(anchor: Path) -> Path:
    resolved_anchor = anchor.resolve()
    start = resolved_anchor.parent if resolved_anchor.is_file() else resolved_anchor
    for candidate in (start, *start.parents):
        if (candidate / "core" / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(f"project root not found from {resolved_anchor}")


def load_deepseek_settings(project_root: Path) -> DeepSeekSettings:
    load_dotenv(project_root.resolve() / ".env", override=False)

    return _read_deepseek_settings()


def load_llm_settings(project_root: Path) -> LLMSettings:
    load_dotenv(project_root.resolve() / ".env", override=False)

    chat_profile_value = (
        os.environ.get("JARVIS_CHAT_PROFILE") or "chat_default"
    ).strip()
    if chat_profile_value not in {"chat_default", "reasoning_strong"}:
        raise ValueError(
            "JARVIS_CHAT_PROFILE must be 'chat_default' or 'reasoning_strong'"
        )

    effort_value = (
        os.environ.get("JARVIS_PROFILE_REASONING_STRONG_EFFORT") or "low"
    ).strip().lower()
    allowed_efforts = {"none", "low", "medium", "high", "xhigh", "max"}
    if effort_value not in allowed_efforts:
        raise ValueError(
            "JARVIS_PROFILE_REASONING_STRONG_EFFORT must be one of "
            "'none', 'low', 'medium', 'high', 'xhigh', or 'max'"
        )

    packycode_api_key = (
        os.environ.get("JARVIS_PACKYCODE_API_KEY") or ""
    ).strip() or None
    packycode_base_url = (
        os.environ.get("JARVIS_PACKYCODE_BASE_URL")
        or "https://www.packyapi.com/v1"
    ).strip()
    reasoning_strong_model = (
        os.environ.get("JARVIS_PROFILE_REASONING_STRONG_MODEL")
        or "gpt-5.6-sol"
    ).strip()
    if not packycode_base_url:
        raise ValueError("JARVIS_PACKYCODE_BASE_URL must not be empty")
    if not reasoning_strong_model:
        raise ValueError("JARVIS_PROFILE_REASONING_STRONG_MODEL must not be empty")

    return LLMSettings(
        deepseek=_read_deepseek_settings(),
        packycode=PackyCodeSettings(
            api_key=packycode_api_key,
            base_url=packycode_base_url,
        ),
        chat_profile=cast(ChatProfileName, chat_profile_value),
        reasoning_strong_model=reasoning_strong_model,
        reasoning_strong_effort=cast(ReasoningEffort, effort_value),
    )


def _read_deepseek_settings() -> DeepSeekSettings:

    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip() or None
    base_url = (
        os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    ).strip()
    model = (os.environ.get("DEEPSEEK_MODEL") or "deepseek-v4-flash").strip()
    thinking_mode_value = (
        os.environ.get("DEEPSEEK_THINKING_MODE") or "disabled"
    ).strip().lower()
    if thinking_mode_value not in {"disabled", "enabled"}:
        raise ValueError("DEEPSEEK_THINKING_MODE must be 'disabled' or 'enabled'")

    return DeepSeekSettings(
        api_key=api_key,
        base_url=base_url,
        model=model,
        thinking_mode=cast(ThinkingMode, thinking_mode_value),
    )
