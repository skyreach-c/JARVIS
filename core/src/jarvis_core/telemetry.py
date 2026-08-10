from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from enum import StrEnum
from typing import Literal

from jarvis_core.llm.profiles import ModelProfile

LOGGER = logging.getLogger("jarvis_core.perf")

type RequestStatus = Literal["success", "error"]
type MemoryRouterAction = Literal[
    "chat",
    "remember",
    "list",
    "forget",
    "clarify",
    "clear_all",
    "invalid",
    "error",
]
type Summary = dict[str, object]
type SummarySink = Callable[[Summary], None]


class FailurePhase(StrEnum):
    MEMORY_COMMAND = "memory_command"
    MEMORY_ROUTER = "memory_router"
    MEMORY_READ = "memory_read"
    PROMPT_BUILD = "prompt_build"
    PROVIDER_BEFORE_FIRST_TOKEN = "provider_before_first_token"
    PROVIDER_STREAM = "provider_stream"
    REQUEST_HANDLING = "request_handling"


def _log_summary(summary: Summary) -> None:
    LOGGER.info(
        "PERF %s",
        json.dumps(summary, ensure_ascii=True, separators=(",", ":")),
    )


class RequestTelemetry:
    def __init__(
        self,
        request_id: str,
        *,
        clock: Callable[[], float] = time.perf_counter,
        summary_sink: SummarySink = _log_summary,
        enabled: bool = True,
    ) -> None:
        self.request_id = request_id
        self.is_noop = not enabled
        self._clock = clock
        self._summary_sink = summary_sink
        self._request_started_at = self._now()
        self._request_kind: Literal["llm", "memory_command"] | None = None
        self._command: str | None = None
        self._profile: str | None = None
        self._provider: str | None = None
        self._model: str | None = None
        self._memory_router_profile: str | None = None
        self._memory_router_provider: str | None = None
        self._memory_router_model: str | None = None
        self._failure_phase: FailurePhase | None = None
        self._emitted = False

        self._memory_read_ms: float | None = None
        self._prompt_build_ms: float | None = None
        self._memory_operation_ms: float | None = None
        self._memory_router_ms: float | None = None
        self._memory_router_action: MemoryRouterAction | None = None
        self._provider_first_token_ms: float | None = None
        self._provider_stream_ms: float | None = None
        self._total_llm_ms: float | None = None
        self._first_delta_ms: float | None = None

        self._provider_first_token_at: float | None = None
        self._provider_first_token_seen = False
        self._history_turns: int | None = None
        self._pinned_memory_count: int | None = None
        self._message_count: int | None = None
        self._prompt_chars: int | None = None

    def mark_llm_request(
        self,
        *,
        history_turns: int,
        profile: ModelProfile | None = None,
    ) -> None:
        if self.is_noop:
            return
        self._request_kind = "llm"
        self._history_turns = history_turns
        if profile is not None:
            self._profile = profile.name
            self._provider = profile.provider
            self._model = profile.model

    def mark_memory_command(self, command: str) -> None:
        if self.is_noop:
            return
        self._request_kind = "memory_command"
        self._command = command

    def set_llm_context(
        self,
        *,
        pinned_memory_count: int,
        message_count: int,
        prompt_chars: int,
    ) -> None:
        if self.is_noop:
            return
        self._pinned_memory_count = pinned_memory_count
        self._message_count = message_count
        self._prompt_chars = prompt_chars

    @contextmanager
    def measure_phase(
        self,
        field: Literal[
            "memory_read_ms",
            "prompt_build_ms",
            "memory_operation_ms",
        ],
        failure_phase: FailurePhase,
    ) -> Iterator[None]:
        started_at = self._now()
        try:
            yield
        except BaseException:
            self.mark_failure(failure_phase)
            raise
        finally:
            elapsed = self._elapsed_ms(started_at, self._now())
            if elapsed is not None and not self.is_noop:
                setattr(self, f"_{field}", elapsed)

    def start_provider(self) -> float | None:
        return self._now()

    def start_memory_router(
        self,
        *,
        profile: ModelProfile | None = None,
    ) -> float | None:
        if not self.is_noop and profile is not None:
            self._memory_router_profile = profile.name
            self._memory_router_provider = profile.provider
            self._memory_router_model = profile.model
        return self._now()

    def finish_memory_router(
        self,
        started_at: float | None,
        *,
        action: MemoryRouterAction,
    ) -> None:
        if self.is_noop:
            return
        self._memory_router_ms = self._elapsed_ms(started_at, self._now())
        self._memory_router_action = action

    def record_provider_first_token(self, provider_started_at: float | None) -> None:
        if self.is_noop or self._provider_first_token_seen:
            return
        self._provider_first_token_seen = True
        self._provider_first_token_at = self._now()
        self._provider_first_token_ms = self._elapsed_ms(
            provider_started_at,
            self._provider_first_token_at,
        )

    def finish_provider(self, provider_started_at: float | None) -> None:
        if self.is_noop:
            return
        finished_at = self._now()
        self._total_llm_ms = self._elapsed_ms(provider_started_at, finished_at)
        if self._provider_first_token_seen:
            self._provider_stream_ms = self._elapsed_ms(
                self._provider_first_token_at,
                finished_at,
            )

    def fail_provider(self, provider_started_at: float | None) -> None:
        self.finish_provider(provider_started_at)
        if self._provider_first_token_seen:
            self.mark_failure(FailurePhase.PROVIDER_STREAM)
        else:
            self.mark_failure(FailurePhase.PROVIDER_BEFORE_FIRST_TOKEN)

    def record_first_delta(self) -> None:
        if self.is_noop or self._first_delta_ms is not None:
            return
        self._first_delta_ms = self._elapsed_ms(
            self._request_started_at,
            self._now(),
        )

    def mark_failure(self, phase: FailurePhase) -> None:
        if self.is_noop or self._failure_phase is not None:
            return
        self._failure_phase = phase

    def finish(self, *, status: RequestStatus) -> None:
        if self.is_noop or self._emitted:
            return
        self._emitted = True
        total_request_ms = self._elapsed_ms(
            self._request_started_at,
            self._now(),
        )
        summary = self._build_summary(status, total_request_ms)
        try:
            self._summary_sink(summary)
        except Exception:  # noqa: BLE001 - telemetry must never fail the request
            return

    def _build_summary(
        self,
        status: RequestStatus,
        total_request_ms: float | None,
    ) -> Summary:
        summary: Summary = {
            "request_id": self.request_id,
            "status": status,
            "request_kind": self._request_kind or "unknown",
        }
        if self._request_kind == "llm":
            summary.update(
                {
                    "memory_read_ms": self._memory_read_ms,
                    "prompt_build_ms": self._prompt_build_ms,
                    "provider_first_token_ms": self._provider_first_token_ms,
                    "provider_stream_ms": self._provider_stream_ms,
                    "total_llm_ms": self._total_llm_ms,
                    "first_delta_ms": self._first_delta_ms,
                    "total_request_ms": total_request_ms,
                    "history_turns": self._history_turns,
                    "pinned_memory_count": self._pinned_memory_count,
                    "message_count": self._message_count,
                    "prompt_chars": self._prompt_chars,
                }
            )
            if self._profile is not None:
                summary.update(
                    {
                        "profile": self._profile,
                        "provider": self._provider,
                        "model": self._model,
                    }
                )
        elif self._request_kind == "memory_command":
            summary.update(
                {
                    "command": self._command,
                    "memory_operation_ms": self._memory_operation_ms,
                    "first_delta_ms": self._first_delta_ms,
                    "total_request_ms": total_request_ms,
                }
            )
        else:
            summary.update(
                {
                    "first_delta_ms": self._first_delta_ms,
                    "total_request_ms": total_request_ms,
                }
            )
        if self._memory_router_action is not None:
            summary["memory_router_ms"] = self._memory_router_ms
            summary["memory_router_action"] = self._memory_router_action
            if self._memory_router_profile is not None:
                summary.update(
                    {
                        "memory_router_profile": self._memory_router_profile,
                        "memory_router_provider": self._memory_router_provider,
                        "memory_router_model": self._memory_router_model,
                    }
                )
        if status == "error":
            summary["failure_phase"] = str(
                self._failure_phase or FailurePhase.REQUEST_HANDLING
            )
        return summary

    def _now(self) -> float | None:
        if self.is_noop:
            return None
        try:
            return self._clock()
        except Exception:  # noqa: BLE001 - timing is intentionally best-effort
            return None

    @staticmethod
    def _elapsed_ms(started_at: float | None, finished_at: float | None) -> float | None:
        if started_at is None or finished_at is None:
            return None
        return round((finished_at - started_at) * 1000, 3)


_NOOP_TELEMETRY = RequestTelemetry("", enabled=False)
_REQUEST_TELEMETRY: ContextVar[RequestTelemetry] = ContextVar(
    "jarvis_request_telemetry",
    default=_NOOP_TELEMETRY,
)


def current_request_telemetry() -> RequestTelemetry:
    return _REQUEST_TELEMETRY.get()


def bind_request_telemetry(
    telemetry: RequestTelemetry,
) -> Token[RequestTelemetry]:
    return _REQUEST_TELEMETRY.set(telemetry)


def reset_request_telemetry(token: Token[RequestTelemetry]) -> None:
    _REQUEST_TELEMETRY.reset(token)
