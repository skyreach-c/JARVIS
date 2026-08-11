import importlib

from jarvis_core.llm.profiles import ModelProfile


def telemetry_module():  # type: ignore[no-untyped-def]
    return importlib.import_module("jarvis_core.telemetry")


class ManualClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_llm_summary_records_phase_and_user_perceived_timings_once() -> None:
    module = telemetry_module()
    clock = ManualClock()
    summaries: list[dict[str, object]] = []
    telemetry = module.RequestTelemetry(
        "request-1",
        clock=clock,
        summary_sink=summaries.append,
    )
    telemetry.mark_llm_request(history_turns=2)

    with telemetry.measure_phase(
        "memory_read_ms",
        module.FailurePhase.MEMORY_READ,
    ):
        clock.value = 0.010
    with telemetry.measure_phase(
        "prompt_build_ms",
        module.FailurePhase.PROMPT_BUILD,
    ):
        clock.value = 0.015
    telemetry.set_llm_context(
        pinned_memory_count=3,
        message_count=6,
        prompt_chars=420,
    )

    chat_profile = ModelProfile(
        name="reasoning_strong",
        provider="packycode",
        model="gpt-5.6-sol",
        reasoning_effort="low",
    )
    provider_started = telemetry.start_chat(profile=chat_profile)
    clock.value = 0.115
    telemetry.record_chat_first_token(provider_started)
    clock.value = 0.120
    telemetry.record_first_delta()
    clock.value = 0.315
    telemetry.finish_chat(provider_started)
    clock.value = 0.400
    telemetry.finish(status="success")
    telemetry.finish(status="error")

    assert summaries == [
        {
            "request_id": "request-1",
            "status": "success",
            "request_kind": "llm",
            "memory_read_ms": 10.0,
            "prompt_build_ms": 5.0,
            "provider_first_token_ms": 100.0,
            "provider_stream_ms": 200.0,
            "total_llm_ms": 300.0,
            "chat_first_token_ms": 100.0,
            "chat_stream_ms": 200.0,
            "chat_total_ms": 300.0,
            "chat_profile": "reasoning_strong",
            "chat_provider": "packycode",
            "chat_model": "gpt-5.6-sol",
            "profile": "reasoning_strong",
            "provider": "packycode",
            "model": "gpt-5.6-sol",
            "first_delta_ms": 120.0,
            "total_request_ms": 400.0,
            "history_turns": 2,
            "pinned_memory_count": 3,
            "message_count": 6,
            "prompt_chars": 420,
        }
    ]


def test_memory_command_summary_has_no_llm_timings() -> None:
    module = telemetry_module()
    clock = ManualClock()
    summaries: list[dict[str, object]] = []
    telemetry = module.RequestTelemetry(
        "command-request",
        clock=clock,
        summary_sink=summaries.append,
    )
    telemetry.mark_memory_command("remember")

    with telemetry.measure_phase(
        "memory_operation_ms",
        module.FailurePhase.MEMORY_COMMAND,
    ):
        clock.value = 0.020
    clock.value = 0.025
    telemetry.record_first_delta()
    clock.value = 0.030
    telemetry.finish(status="success")

    assert summaries == [
        {
            "request_id": "command-request",
            "status": "success",
            "request_kind": "memory_command",
            "command": "remember",
            "memory_operation_ms": 20.0,
            "first_delta_ms": 25.0,
            "total_request_ms": 30.0,
        }
    ]
    assert not any(
        key.startswith("provider_") or key == "total_llm_ms"
        for key in summaries[0]
    )


def test_router_timings_are_optional_and_preserved_on_chat_fallthrough() -> None:
    module = telemetry_module()
    clock = ManualClock()
    summaries: list[dict[str, object]] = []
    telemetry = module.RequestTelemetry(
        "router-chat",
        clock=clock,
        summary_sink=summaries.append,
    )
    router_profile = ModelProfile(
        name="structured_router",
        provider="deepseek",
        model="deepseek-v4-flash",
    )
    router_started = telemetry.start_memory_router(profile=router_profile)
    clock.value = 0.075
    telemetry.finish_memory_router(router_started, action="chat")
    telemetry.mark_llm_request(history_turns=0)
    telemetry.start_chat(
        profile=ModelProfile(
            name="reasoning_strong",
            provider="packycode",
            model="gpt-5.6-sol",
            reasoning_effort="low",
        )
    )
    clock.value = 0.100
    telemetry.finish(status="success")

    assert summaries[0]["request_kind"] == "llm"
    assert summaries[0]["memory_router_ms"] == 75.0
    assert summaries[0]["memory_router_action"] == "chat"
    assert summaries[0]["memory_router_profile"] == "structured_router"
    assert summaries[0]["memory_router_provider"] == "deepseek"
    assert summaries[0]["memory_router_model"] == "deepseek-v4-flash"
    assert summaries[0]["profile"] == "reasoning_strong"
    assert summaries[0]["provider"] == "packycode"
    assert summaries[0]["model"] == "gpt-5.6-sol"
    assert summaries[0]["chat_profile"] == "reasoning_strong"
    assert summaries[0]["chat_provider"] == "packycode"
    assert summaries[0]["chat_model"] == "gpt-5.6-sol"


def test_router_failure_action_is_sanitized_without_payload_fields() -> None:
    module = telemetry_module()
    clock = ManualClock()
    summaries: list[dict[str, object]] = []
    telemetry = module.RequestTelemetry(
        "router-error",
        clock=clock,
        summary_sink=summaries.append,
    )
    telemetry.mark_memory_command("clarify")
    router_started = telemetry.start_memory_router()
    clock.value = 0.020
    telemetry.finish_memory_router(router_started, action="invalid")
    telemetry.finish(status="success")

    assert summaries[0]["memory_router_ms"] == 20.0
    assert summaries[0]["memory_router_action"] == "invalid"
    assert "user" not in str(summaries[0]).lower()
    assert "content" not in str(summaries[0]).lower()


def test_error_summary_uses_finite_failure_phase_and_unavailable_values() -> None:
    module = telemetry_module()
    clock = ManualClock()
    summaries: list[dict[str, object]] = []
    telemetry = module.RequestTelemetry(
        "failed-request",
        clock=clock,
        summary_sink=summaries.append,
    )
    telemetry.mark_llm_request(history_turns=0)
    provider_started = telemetry.start_chat()
    clock.value = 0.125
    telemetry.fail_chat(provider_started)
    clock.value = 0.150
    telemetry.finish(status="error")

    summary = summaries[0]
    assert summary["status"] == "error"
    assert summary["failure_phase"] == "provider_before_first_token"
    assert summary["provider_first_token_ms"] is None
    assert summary["provider_stream_ms"] is None
    assert summary["total_llm_ms"] == 125.0
    assert summary["chat_first_token_ms"] is None
    assert summary["chat_stream_ms"] is None
    assert summary["chat_total_ms"] == 125.0
    assert summary["first_delta_ms"] is None


def test_context_binding_restores_the_exact_previous_value() -> None:
    module = telemetry_module()
    outer = module.RequestTelemetry("outer")
    inner = module.RequestTelemetry("inner")

    assert module.current_request_telemetry().is_noop
    outer_token = module.bind_request_telemetry(outer)
    assert module.current_request_telemetry() is outer
    inner_token = module.bind_request_telemetry(inner)
    assert module.current_request_telemetry() is inner

    module.reset_request_telemetry(inner_token)
    assert module.current_request_telemetry() is outer
    module.reset_request_telemetry(outer_token)
    assert module.current_request_telemetry().is_noop


def test_clock_and_summary_sink_failures_are_best_effort() -> None:
    module = telemetry_module()

    def broken_clock() -> float:
        raise RuntimeError("clock failed")

    def broken_sink(summary: dict[str, object]) -> None:
        del summary
        raise RuntimeError("log handler failed")

    telemetry = module.RequestTelemetry(
        "best-effort",
        clock=broken_clock,
        summary_sink=broken_sink,
    )
    telemetry.mark_llm_request(history_turns=0)
    with telemetry.measure_phase(
        "memory_read_ms",
        module.FailurePhase.MEMORY_READ,
    ):
        pass
    telemetry.record_first_delta()
    telemetry.finish(status="success")


def test_tool_observation_sizes_are_optional_non_negative_integers() -> None:
    module = telemetry_module()
    summaries: list[dict[str, object]] = []
    telemetry = module.RequestTelemetry(
        "tool-observation-size",
        summary_sink=summaries.append,
    )
    telemetry.mark_llm_request(history_turns=0)

    telemetry.set_tool_observation_size(chars=7, utf8_bytes=11)
    for invalid_chars, invalid_bytes in (
        (True, 11),
        (7, False),
        (-1, 11),
        (7, -1),
        ("7", 11),
        (7, 11.0),
    ):
        telemetry.set_tool_observation_size(  # type: ignore[arg-type]
            chars=invalid_chars,
            utf8_bytes=invalid_bytes,
        )
    telemetry.finish(status="success")

    assert summaries[0]["tool_observation_chars"] == 7
    assert summaries[0]["tool_observation_utf8_bytes"] == 11


def test_zero_tool_observation_sizes_are_valid_and_unset_fields_are_absent() -> None:
    module = telemetry_module()
    with_size: list[dict[str, object]] = []
    telemetry = module.RequestTelemetry("zero-size", summary_sink=with_size.append)
    telemetry.mark_llm_request(history_turns=0)
    telemetry.set_tool_observation_size(chars=0, utf8_bytes=0)
    telemetry.finish(status="success")

    without_size: list[dict[str, object]] = []
    telemetry = module.RequestTelemetry("unset-size", summary_sink=without_size.append)
    telemetry.mark_llm_request(history_turns=0)
    telemetry.finish(status="success")

    assert with_size[0]["tool_observation_chars"] == 0
    assert with_size[0]["tool_observation_utf8_bytes"] == 0
    assert "tool_observation_chars" not in without_size[0]
    assert "tool_observation_utf8_bytes" not in without_size[0]


def test_agent_chat_and_memory_router_model_telemetry_are_isolated() -> None:
    module = telemetry_module()
    clock = ManualClock()
    summaries: list[dict[str, object]] = []
    telemetry = module.RequestTelemetry(
        "three-models",
        clock=clock,
        summary_sink=summaries.append,
    )
    telemetry.mark_llm_request(history_turns=1)
    router_started = telemetry.start_memory_router(
        profile=ModelProfile(
            name="structured_router",
            provider="deepseek",
            model="deepseek-v4-flash",
        )
    )
    clock.value = 0.010
    telemetry.finish_memory_router(router_started, action="chat")
    brain_started = telemetry.start_agent_brain(
        profile=ModelProfile(
            name="agent_brain",
            provider="deepseek",
            model="deepseek-v4-flash",
        )
    )
    clock.value = 0.050
    telemetry.finish_agent_brain(brain_started, action="call_tool")
    tool_started = telemetry.start_tool(
        tool_name="system.get_runtime_info",
        risk_level="read_only",
    )
    clock.value = 0.060
    telemetry.finish_tool(tool_started, status="success")
    chat_started = telemetry.start_chat(
        profile=ModelProfile(
            name="reasoning_strong",
            provider="packycode",
            model="gpt-5.6-sol",
            reasoning_effort="low",
        )
    )
    clock.value = 0.090
    telemetry.record_chat_first_token(chat_started)
    clock.value = 0.140
    telemetry.finish_chat(chat_started)
    telemetry.finish(status="success")

    summary = summaries[0]
    assert summary["agent_brain_decision_ms"] == 40.0
    assert summary["agent_brain_action"] == "call_tool"
    assert summary["agent_brain_profile"] == "agent_brain"
    assert summary["agent_brain_provider"] == "deepseek"
    assert summary["agent_brain_model"] == "deepseek-v4-flash"
    assert summary["chat_first_token_ms"] == 30.0
    assert summary["chat_stream_ms"] == 50.0
    assert summary["chat_total_ms"] == 80.0
    assert summary["chat_profile"] == "reasoning_strong"
    assert summary["chat_provider"] == "packycode"
    assert summary["chat_model"] == "gpt-5.6-sol"
    assert summary["memory_router_profile"] == "structured_router"
    assert summary["memory_router_provider"] == "deepseek"
    assert summary["memory_router_model"] == "deepseek-v4-flash"
    assert summary["tool_call_count"] == 1
    assert summary["tool_name"] == "system.get_runtime_info"
    assert summary["tool_risk_level"] == "read_only"
    assert summary["tool_status"] == "success"
    assert summary["tool_execution_ms"] == 10.0
    assert summary["provider"] == summary["chat_provider"] == "packycode"
    assert summary["profile"] == summary["chat_profile"] == "reasoning_strong"
    assert summary["model"] == summary["chat_model"] == "gpt-5.6-sol"


def test_brain_failure_has_no_chat_or_legacy_identity() -> None:
    module = telemetry_module()
    clock = ManualClock()
    summaries: list[dict[str, object]] = []
    telemetry = module.RequestTelemetry(
        "brain-failure",
        clock=clock,
        summary_sink=summaries.append,
    )
    telemetry.mark_llm_request(history_turns=0)
    started = telemetry.start_agent_brain(
        profile=ModelProfile(
            name="agent_brain",
            provider="deepseek",
            model="deepseek-v4-flash",
        )
    )
    clock.value = 0.025
    telemetry.finish_agent_brain(started, action="error")
    telemetry.mark_failure(module.FailurePhase.AGENT_BRAIN)
    telemetry.finish(status="error")

    summary = summaries[0]
    assert summary["agent_brain_action"] == "error"
    assert summary["agent_brain_provider"] == "deepseek"
    assert "chat_profile" not in summary
    assert "chat_provider" not in summary
    assert "chat_model" not in summary
    assert "profile" not in summary
    assert "provider" not in summary
    assert "model" not in summary
    assert summary["failure_phase"] == "agent_brain"
