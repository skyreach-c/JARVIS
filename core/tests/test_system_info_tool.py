import ast
import importlib
import logging
import time
from collections import Counter
from pathlib import Path
from types import ModuleType

import pytest

from jarvis_core.tools.contracts import ToolCall
from jarvis_core.tools.registry import ToolRegistry


def load_system_info_module() -> ModuleType:
    return importlib.import_module("jarvis_core.tools.system_info")


def dotted_name(target: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        parts.append(target.id)
        return ".".join(reversed(parts))
    return f"<{type(target).__name__}>"


def test_os_info_module_stays_within_safe_probe_boundary() -> None:
    system_info = load_system_info_module()
    assert system_info.__file__ is not None
    source = Path(system_info.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module or "<relative-import>")

    forbidden_module_roots = {
        "ctypes",
        "getpass",
        "psutil",
        "socket",
        "subprocess",
        "winreg",
        "wmi",
    }
    imported_module_roots = {module.split(".", maxsplit=1)[0] for module in imported_modules}
    assert forbidden_module_roots.isdisjoint(imported_module_roots)

    call_counts = Counter(
        dotted_name(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    )
    required_probes = {
        "platform.system",
        "platform.release",
        "platform.version",
        "platform.machine",
        "os.cpu_count",
    }
    assert all(call_counts[probe] == 1 for probe in required_probes)

    referenced_attributes = {
        dotted_name(node) for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    forbidden_sensitive_references = {
        "getpass.getuser",
        "os.environ",
        "os.getenv",
        "os.getlogin",
        "os.uname",
        "platform.node",
        "platform.uname",
        "socket.getaddrinfo",
        "socket.getfqdn",
        "socket.gethostbyaddr",
        "socket.gethostbyname",
        "socket.gethostbyname_ex",
        "socket.gethostname",
        "uuid.getnode",
    }
    assert forbidden_sensitive_references.isdisjoint(referenced_attributes)


def test_os_info_registration_is_read_only_with_strict_empty_arguments() -> None:
    system_info = load_system_info_module()
    registry = ToolRegistry()

    definition = system_info.register_os_info_tool(registry)

    assert definition.name == "system.get_os_info"
    assert definition.risk_level == "read_only"
    assert definition.input_schema["type"] == "object"
    assert definition.input_schema["properties"] == {}
    assert definition.input_schema["additionalProperties"] is False
    assert registry.definitions() == (definition,)


async def test_os_info_returns_only_actual_safe_os_snapshot(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    system_info = load_system_info_module()
    monkeypatch.setattr(system_info.platform, "system", lambda: "TestOS")
    monkeypatch.setattr(system_info.platform, "release", lambda: "24.1")
    monkeypatch.setattr(system_info.platform, "version", lambda: "build-safe")
    monkeypatch.setattr(system_info.platform, "machine", lambda: "test-arch")
    monkeypatch.setattr(system_info.os, "cpu_count", lambda: 12)
    registry = ToolRegistry()
    system_info.register_os_info_tool(registry)

    result = await registry.execute(
        ToolCall(tool_name=system_info.OS_INFO_TOOL_NAME, arguments={}),
        request_id="request-os-info",
    )

    assert result.success is True
    assert result.error is None
    assert result.data == {
        "os_family": "TestOS",
        "os_release": "24.1",
        "os_version": "build-safe",
        "architecture": "test-arch",
        "logical_cpu_count": 12,
    }
    assert result.metadata == {}


async def test_os_info_rejects_extra_arguments_without_calling_executor(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    system_info = load_system_info_module()
    executor_called = False

    async def forbidden_execute(self, arguments):  # type: ignore[no-untyped-def]
        nonlocal executor_called
        executor_called = True
        raise AssertionError("executor must not run for invalid arguments")

    monkeypatch.setattr(system_info.OsInfoExecutor, "execute", forbidden_execute)
    registry = ToolRegistry()
    system_info.register_os_info_tool(registry)

    result = await registry.execute(
        ToolCall(
            tool_name=system_info.OS_INFO_TOOL_NAME,
            arguments={"include_private_details": True},
        ),
        request_id="request-os-info-extra-field",
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "tool_invalid_arguments"
    assert executor_called is False


async def test_os_info_slow_probe_respects_registry_timeout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    system_info = load_system_info_module()

    def slow_probe() -> str:
        time.sleep(0.05)
        return "TooLateOS"

    monkeypatch.setattr(system_info.platform, "system", slow_probe)
    monkeypatch.setattr(system_info, "OS_INFO_TOOL_TIMEOUT_SECONDS", 0.01)
    registry = ToolRegistry()
    system_info.register_os_info_tool(registry)

    result = await registry.execute(
        ToolCall(tool_name=system_info.OS_INFO_TOOL_NAME, arguments={}),
        request_id="request-os-info-timeout",
    )

    assert result.success is False
    assert result.data is None
    assert result.error is not None
    assert result.error.code == "tool_timeout"
    assert result.error.retryable is True


@pytest.mark.parametrize(
    ("module_name", "probe_name"),
    [
        ("platform", "system"),
        ("platform", "release"),
        ("platform", "version"),
        ("platform", "machine"),
        ("os", "cpu_count"),
    ],
)
async def test_os_info_executor_sanitizes_every_standard_library_exception(
    module_name: str,
    probe_name: str,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:  # type: ignore[no-untyped-def]
    system_info = load_system_info_module()
    private_exception_text = f"PRIVATE exception from {module_name}.{probe_name}"

    def fail_with_private_text() -> object:
        raise RuntimeError(private_exception_text)

    monkeypatch.setattr(
        getattr(system_info, module_name),
        probe_name,
        fail_with_private_text,
    )

    with caplog.at_level(logging.DEBUG):
        result = await system_info.OsInfoExecutor().execute(
            system_info.OsInfoArguments()
        )

    assert result.success is False
    assert result.data is None
    assert result.error is not None
    assert result.error.code == "tool_execution_failed"
    assert result.error.retryable is True
    assert private_exception_text not in repr(result)
    assert private_exception_text not in caplog.text
    assert result.metadata == {}
