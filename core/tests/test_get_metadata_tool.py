import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis_core.tools import filesystem
from jarvis_core.tools.contracts import ToolCall, ToolError, ToolResult
from jarvis_core.tools.project_files import ProjectPathPolicy, ProjectPathPolicyError
from jarvis_core.tools.registry import ToolRegistry


def make_project(tmp_path: Path) -> tuple[Path, ProjectPathPolicy]:
    project_root = tmp_path / "PRIVATE-project-root"
    project_root.mkdir()
    return project_root, ProjectPathPolicy(project_root)


async def call_get_metadata(
    policy: ProjectPathPolicy,
    arguments: dict[str, object],
) -> ToolResult:
    registry = ToolRegistry()
    filesystem.register_get_metadata_tool(registry, path_policy=policy)
    return await registry.execute(
        ToolCall(
            tool_name=filesystem.GET_METADATA_TOOL_NAME,
            arguments=arguments,
        ),
        request_id="request-get-metadata",
    )


def assert_unavailable(result: ToolResult, *private_fragments: str) -> ToolError:
    assert result.success is False
    assert result.data is None
    assert result.error is not None
    assert result.error.code == "filesystem_path_unavailable"
    assert result.error.retryable is False
    assert result.metadata == {}
    serialized = repr(result)
    for fragment in private_fragments:
        assert fragment not in serialized
    return result.error


def copy_metadata(
    metadata: os.stat_result,
    *,
    mode: int | None = None,
    file_attributes: int | None = None,
    inode: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        st_mode=metadata.st_mode if mode is None else mode,
        st_size=metadata.st_size,
        st_dev=metadata.st_dev,
        st_ino=metadata.st_ino if inode is None else inode,
        st_file_attributes=(
            getattr(metadata, "st_file_attributes", 0)
            if file_attributes is None
            else file_attributes
        ),
    )


def test_registration_has_required_strict_path_schema_and_read_only_risk(
    tmp_path: Path,
) -> None:
    _, policy = make_project(tmp_path)
    registry = ToolRegistry()

    definition = filesystem.register_get_metadata_tool(registry, path_policy=policy)

    assert definition.name == "filesystem.get_metadata"
    assert definition.risk_level == "read_only"
    assert definition.input_schema == {
        "additionalProperties": False,
        "properties": {
            "relative_path": {
                "minLength": 1,
                "title": "Relative Path",
                "type": "string",
            }
        },
        "required": ["relative_path"],
        "title": "GetMetadataArguments",
        "type": "object",
    }
    assert filesystem.GET_METADATA_TOOL_TIMEOUT_SECONDS == 2.0
    assert registry.definitions() == (definition,)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"relative_path": ""},
        {"relative_path": 1},
        {"relative_path": False},
        {"relative_path": None},
        {"relative_path": 1.0},
        {"relative_path": ".", "extra": "forbidden"},
    ],
)
async def test_schema_invalid_arguments_never_call_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, object],
) -> None:
    _, policy = make_project(tmp_path)
    executor_called = False

    async def forbidden_execute(self: object, validated: object) -> ToolResult:
        nonlocal executor_called
        executor_called = True
        raise AssertionError("executor must not run for schema-invalid arguments")

    monkeypatch.setattr(filesystem.GetMetadataExecutor, "execute", forbidden_execute)

    result = await call_get_metadata(policy, arguments)

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "tool_invalid_arguments"
    assert executor_called is False


async def test_executor_uses_to_thread_and_root_has_exact_directory_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    calls: list[tuple[object, tuple[object, ...]]] = []

    async def immediate_to_thread(function: object, *args: object) -> object:
        calls.append((function, args))
        return function(*args)  # type: ignore[operator]

    monkeypatch.setattr(filesystem.asyncio, "to_thread", immediate_to_thread)

    result = await call_get_metadata(policy, {"relative_path": "."})

    assert result.success is True
    assert result.data == {
        "scope": "project",
        "relative_path": ".",
        "exists": True,
        "kind": "directory",
        "size_bytes": None,
    }
    assert result.error is None
    assert result.metadata == {}
    assert len(calls) == 1
    assert str(project_root) not in repr(result)


async def test_regular_file_has_exact_safe_metadata(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    payload = b"seven!!"
    (project_root / "file.txt").write_bytes(payload)

    result = await call_get_metadata(policy, {"relative_path": "file.txt"})

    assert result.success is True
    assert result.data == {
        "scope": "project",
        "relative_path": "file.txt",
        "exists": True,
        "kind": "file",
        "size_bytes": len(payload),
    }
    assert set(result.data) == {
        "scope",
        "relative_path",
        "exists",
        "kind",
        "size_bytes",
    }
    assert result.error is None
    assert result.metadata == {}


async def test_directory_has_exact_safe_metadata(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    (project_root / "folder").mkdir()

    result = await call_get_metadata(policy, {"relative_path": "folder"})

    assert result.success is True
    assert result.data == {
        "scope": "project",
        "relative_path": "folder",
        "exists": True,
        "kind": "directory",
        "size_bytes": None,
    }
    assert result.error is None
    assert result.metadata == {}


async def test_other_kind_never_exposes_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "other.node"
    target.write_bytes(b"not exposed")
    real_lstat = Path.lstat

    def fake_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        metadata = real_lstat(path)
        if path == target:
            return copy_metadata(metadata, mode=stat.S_IFIFO | 0o600)
        return metadata

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    result = await call_get_metadata(policy, {"relative_path": "other.node"})

    assert result.success is True
    assert result.data == {
        "scope": "project",
        "relative_path": "other.node",
        "exists": True,
        "kind": "other",
        "size_bytes": None,
    }
    assert result.error is None
    assert result.metadata == {}


async def test_ordinary_missing_path_is_success_only_after_double_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "ordinary-missing.txt"
    real_resolve = ProjectPathPolicy.resolve
    real_lstat = Path.lstat
    resolve_calls = 0
    target_lstat_calls = 0

    def recording_resolve(self: ProjectPathPolicy, relative_path: str) -> Path:
        nonlocal resolve_calls
        resolve_calls += 1
        return real_resolve(self, relative_path)

    def recording_lstat(path: Path) -> os.stat_result:
        nonlocal target_lstat_calls
        if path == target:
            target_lstat_calls += 1
        return real_lstat(path)

    monkeypatch.setattr(ProjectPathPolicy, "resolve", recording_resolve)
    monkeypatch.setattr(Path, "lstat", recording_lstat)

    result = await call_get_metadata(policy, {"relative_path": "ordinary-missing.txt"})

    assert result.success is True
    assert result.data == {
        "scope": "project",
        "relative_path": "ordinary-missing.txt",
        "exists": False,
        "kind": None,
        "size_bytes": None,
    }
    assert result.error is None
    assert result.metadata == {}
    assert resolve_calls == 2
    assert target_lstat_calls >= 4
    assert str(project_root) not in json.dumps(result.data)


async def test_success_uses_canonical_posix_label_and_ignores_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "nested" / "child" / "file.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"safe")
    unrelated = tmp_path / "unrelated-cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    result = await call_get_metadata(
        policy,
        {"relative_path": r"nested\child\.\file.txt"},
    )

    assert result.success is True
    assert result.data is not None
    assert result.data["relative_path"] == "nested/child/file.txt"
    assert str(project_root) not in json.dumps(result.data)


@pytest.mark.parametrize("relative_path", ["..", r"C:\\PRIVATE-drive", "bad:name"])
async def test_policy_invalid_raw_paths_return_fixed_invalid_arguments(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    relative_path: str,
) -> None:
    project_root, policy = make_project(tmp_path)

    with caplog.at_level("DEBUG"):
        result = await call_get_metadata(policy, {"relative_path": relative_path})

    assert result.success is False
    assert result.data is None
    assert result.error is not None
    assert result.error.code == "tool_invalid_arguments"
    assert result.error.retryable is False
    assert result.metadata == {}
    assert relative_path not in repr(result)
    assert relative_path not in caplog.text
    assert str(project_root) not in repr(result)
    assert str(project_root) not in caplog.text


async def test_protected_and_unsafe_paths_share_one_sanitized_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_root, policy = make_project(tmp_path)
    (project_root / ".git").mkdir()
    protected = await call_get_metadata(policy, {"relative_path": ".git"})
    real_resolve = ProjectPathPolicy.resolve

    def unsafe_resolve(self: ProjectPathPolicy, relative_path: str) -> Path:
        if relative_path == "PRIVATE-unsafe-target":
            raise ProjectPathPolicyError("unsafe_path")
        return real_resolve(self, relative_path)

    monkeypatch.setattr(ProjectPathPolicy, "resolve", unsafe_resolve)
    with caplog.at_level("DEBUG"):
        unsafe = await call_get_metadata(
            policy,
            {"relative_path": "PRIVATE-unsafe-target"},
        )

    protected_error = assert_unavailable(protected, ".git", str(project_root))
    unsafe_error = assert_unavailable(
        unsafe,
        "PRIVATE-unsafe-target",
        str(project_root),
    )
    assert unsafe_error == protected_error
    assert "PRIVATE-unsafe-target" not in caplog.text
    assert str(project_root) not in caplog.text


@pytest.mark.parametrize(
    "attribute",
    [
        stat.FILE_ATTRIBUTE_HIDDEN,
        stat.FILE_ATTRIBUTE_SYSTEM,
        stat.FILE_ATTRIBUTE_OFFLINE,
        stat.FILE_ATTRIBUTE_REPARSE_POINT,
    ],
)
async def test_hidden_system_offline_and_reparse_targets_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attribute: int,
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "PRIVATE-flagged-target.txt"
    target.write_bytes(b"must not be exposed")
    real_lstat = Path.lstat

    def flagged_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        metadata = real_lstat(path)
        if path == target:
            return copy_metadata(
                metadata,
                file_attributes=getattr(metadata, "st_file_attributes", 0) | attribute,
            )
        return metadata

    monkeypatch.setattr(Path, "lstat", flagged_lstat)

    result = await call_get_metadata(
        policy,
        {"relative_path": "PRIVATE-flagged-target.txt"},
    )

    assert_unavailable(
        result,
        "PRIVATE-flagged-target.txt",
        str(project_root),
    )


@pytest.mark.parametrize("exception_type", [PermissionError, OSError])
async def test_metadata_probe_os_errors_are_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    exception_type: type[OSError],
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "PRIVATE-input.txt"
    private_text = f"PRIVATE probe failure at {target}"

    def resolved_target(self: ProjectPathPolicy, relative_path: str) -> Path:
        assert self is policy
        assert relative_path == "PRIVATE-input.txt"
        return target

    def failing_lstat(path: Path) -> os.stat_result:
        assert path == target
        raise exception_type(private_text)

    monkeypatch.setattr(ProjectPathPolicy, "resolve", resolved_target)
    monkeypatch.setattr(Path, "lstat", failing_lstat)

    with caplog.at_level("DEBUG"):
        result = await call_get_metadata(
            policy,
            {"relative_path": "PRIVATE-input.txt"},
        )

    assert_unavailable(
        result,
        private_text,
        "PRIVATE-input.txt",
        str(project_root),
    )
    assert private_text not in caplog.text
    assert "PRIVATE-input.txt" not in caplog.text
    assert str(project_root) not in caplog.text


async def test_missing_path_that_appears_during_confirmation_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "PRIVATE-racing-missing.txt"
    template = project_root / "template.txt"
    template.write_bytes(b"appeared")
    template_metadata = template.lstat()
    real_lstat = Path.lstat
    target_lstat_calls = 0

    def appearing_lstat(path: Path) -> os.stat_result:
        nonlocal target_lstat_calls
        if path == target:
            target_lstat_calls += 1
            if target_lstat_calls >= 4:
                return template_metadata
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", appearing_lstat)

    result = await call_get_metadata(
        policy,
        {"relative_path": "PRIVATE-racing-missing.txt"},
    )

    assert target_lstat_calls >= 4
    assert_unavailable(
        result,
        "PRIVATE-racing-missing.txt",
        str(project_root),
    )


async def test_missing_path_whose_canonical_target_changes_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    replacement = project_root / "PRIVATE-replacement-missing.txt"
    real_resolve = ProjectPathPolicy.resolve
    resolve_calls = 0

    def changing_resolve(self: ProjectPathPolicy, relative_path: str) -> Path:
        nonlocal resolve_calls
        resolve_calls += 1
        if resolve_calls == 2:
            return replacement
        return real_resolve(self, relative_path)

    monkeypatch.setattr(ProjectPathPolicy, "resolve", changing_resolve)

    result = await call_get_metadata(
        policy,
        {"relative_path": "PRIVATE-original-missing.txt"},
    )

    assert resolve_calls == 2
    assert_unavailable(
        result,
        "PRIVATE-original-missing.txt",
        "PRIVATE-replacement-missing.txt",
        str(project_root),
    )


@pytest.mark.parametrize("mutation", ["identity", "kind", "filtered_attribute"])
async def test_revalidation_discards_identity_kind_or_filtered_attribute_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "PRIVATE-racing-file.txt"
    target.write_bytes(b"PRIVATE-CONTENT-MUST-BE-DISCARDED")
    real_lstat = Path.lstat
    target_lstat_calls = 0

    def changing_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        nonlocal target_lstat_calls
        metadata = real_lstat(path)
        if path != target:
            return metadata
        target_lstat_calls += 1
        if target_lstat_calls < 4:
            return metadata
        if mutation == "identity":
            return copy_metadata(metadata, inode=metadata.st_ino + 1)
        if mutation == "kind":
            return copy_metadata(metadata, mode=stat.S_IFDIR | 0o700)
        return copy_metadata(
            metadata,
            file_attributes=(
                getattr(metadata, "st_file_attributes", 0) | stat.FILE_ATTRIBUTE_HIDDEN
            ),
        )

    monkeypatch.setattr(Path, "lstat", changing_lstat)

    result = await call_get_metadata(
        policy,
        {"relative_path": "PRIVATE-racing-file.txt"},
    )

    assert target_lstat_calls >= 4
    assert_unavailable(
        result,
        "PRIVATE-racing-file.txt",
        "PRIVATE-CONTENT-MUST-BE-DISCARDED",
        str(project_root),
    )


async def test_revalidation_discards_canonical_target_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    original = project_root / "PRIVATE-original.txt"
    replacement = project_root / "PRIVATE-replacement.txt"
    original.write_bytes(b"original")
    replacement.write_bytes(b"replacement")
    real_resolve = ProjectPathPolicy.resolve
    resolve_calls = 0

    def changing_resolve(self: ProjectPathPolicy, relative_path: str) -> Path:
        nonlocal resolve_calls
        resolve_calls += 1
        if resolve_calls == 2:
            return replacement
        return real_resolve(self, relative_path)

    monkeypatch.setattr(ProjectPathPolicy, "resolve", changing_resolve)

    result = await call_get_metadata(
        policy,
        {"relative_path": "PRIVATE-original.txt"},
    )

    assert resolve_calls == 2
    assert_unavailable(
        result,
        "PRIVATE-original.txt",
        "PRIVATE-replacement.txt",
        str(project_root),
    )


async def test_runtime_never_scans_recurses_or_reads_file_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_root, policy = make_project(tmp_path)
    content_marker = "PRIVATE-CONTENT-MARKER-DO-NOT-READ"
    (project_root / "visible.txt").write_text(content_marker, encoding="utf-8")

    def forbidden_call(*unused_args: object, **unused_kwargs: object) -> object:
        raise AssertionError("metadata tool must not scan, recurse, or read content")

    monkeypatch.setattr(filesystem.os, "scandir", forbidden_call)
    monkeypatch.setattr(filesystem.os, "listdir", forbidden_call)
    monkeypatch.setattr(filesystem.os, "walk", forbidden_call)
    monkeypatch.setattr(Path, "iterdir", forbidden_call)
    monkeypatch.setattr(Path, "glob", forbidden_call)
    monkeypatch.setattr(Path, "rglob", forbidden_call)
    monkeypatch.setattr(Path, "open", forbidden_call)
    monkeypatch.setattr(Path, "read_bytes", forbidden_call)
    monkeypatch.setattr(Path, "read_text", forbidden_call)

    with caplog.at_level("DEBUG"):
        result = await call_get_metadata(policy, {"relative_path": "visible.txt"})

    assert result.success is True
    assert result.data is not None
    assert result.data["kind"] == "file"
    assert content_marker not in repr(result)
    assert content_marker not in caplog.text
    assert str(project_root) not in repr(result)
    assert str(project_root) not in caplog.text
