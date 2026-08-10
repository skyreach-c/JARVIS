from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from jarvis_core.llm.client import LLMClient, StructuredLLMClient

if TYPE_CHECKING:
    from jarvis_core.llm.config import DeepSeekSettings, PackyCodeSettings

type ProviderId = Literal["deepseek", "packycode"]
type ProfileName = Literal[
    "chat_default",
    "reasoning_strong",
    "structured_router",
]
type ChatProfileName = Literal["chat_default", "reasoning_strong"]
type ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True, slots=True)
class ModelProfile:
    name: ProfileName
    provider: ProviderId
    model: str
    reasoning_effort: ReasoningEffort | None = None


def build_model_profiles(
    *,
    deepseek_model: str,
    reasoning_strong_model: str,
    reasoning_strong_effort: ReasoningEffort,
) -> Mapping[ProfileName, ModelProfile]:
    return {
        "chat_default": ModelProfile(
            name="chat_default",
            provider="deepseek",
            model=deepseek_model,
        ),
        "reasoning_strong": ModelProfile(
            name="reasoning_strong",
            provider="packycode",
            model=reasoning_strong_model,
            reasoning_effort=reasoning_strong_effort,
        ),
        "structured_router": ModelProfile(
            name="structured_router",
            provider="deepseek",
            model=deepseek_model,
        ),
    }


def create_chat_client(
    profile: ModelProfile,
    *,
    deepseek_settings: DeepSeekSettings,
    packycode_settings: PackyCodeSettings,
) -> LLMClient:
    if profile.name not in {"chat_default", "reasoning_strong"}:
        raise ValueError(f"profile {profile.name!r} is not a chat profile")
    if profile.provider == "deepseek":
        from jarvis_core.llm.deepseek import DeepSeekClient

        return DeepSeekClient(deepseek_settings)
    if profile.provider == "packycode":
        from jarvis_core.llm.packycode import PackyCodeResponsesClient

        return PackyCodeResponsesClient(
            packycode_settings,
            model=profile.model,
            reasoning_effort=profile.reasoning_effort,
        )
    raise ValueError(f"unsupported chat provider: {profile.provider!r}")


def create_structured_client(
    profile: ModelProfile,
    *,
    deepseek_settings: DeepSeekSettings,
) -> StructuredLLMClient:
    if profile.name != "structured_router" or profile.provider != "deepseek":
        raise ValueError("structured_router must use the deepseek provider")
    from jarvis_core.llm.deepseek import DeepSeekStructuredClient

    return DeepSeekStructuredClient(deepseek_settings)
