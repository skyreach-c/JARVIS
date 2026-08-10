import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from dotenv import load_dotenv

ThinkingMode = Literal["disabled", "enabled"]


@dataclass(frozen=True)
class DeepSeekSettings:
    api_key: str | None
    base_url: str
    model: str
    thinking_mode: ThinkingMode


def find_project_root(anchor: Path) -> Path:
    resolved_anchor = anchor.resolve()
    start = resolved_anchor.parent if resolved_anchor.is_file() else resolved_anchor
    for candidate in (start, *start.parents):
        if (candidate / "core" / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(f"project root not found from {resolved_anchor}")


def load_deepseek_settings(project_root: Path) -> DeepSeekSettings:
    load_dotenv(project_root.resolve() / ".env", override=False)

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
