import ast
import json
import logging
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import pytest

from jarvis_core.tools import filesystem
from jarvis_core.tools.contracts import ToolCall, ToolError, ToolResult
from jarvis_core.tools.project_files import ProjectPathPolicy
from jarvis_core.tools.registry import ToolRegistry


def make_project(tmp_path: Path) -> tuple[Path, ProjectPathPolicy]:
    project_root = tmp_path / "PRIVATE-project-root"
    project_root.mkdir()
    return project_root, ProjectPathPolicy(project_root)


async def call_list_directory(
    policy: ProjectPathPolicy,
    arguments: dict[str, object] | None = None,
) -> ToolResult:
    registry = ToolRegistry()
    filesystem.register_list_directory_tool(registry, path_policy=policy)
    return await registry.execute(
        ToolCall(
            tool_name=filesystem.LIST_DIRECTORY_TOOL_NAME,
            arguments={} if arguments is None else arguments,
        ),
        request_id="request-list-directory",
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


class NameOnlyEntry:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeScandir:
    def __init__(self, names: list[str]) -> None:
        self._entries = iter(NameOnlyEntry(name) for name in names)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *unused: object) -> None:
        return None

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> NameOnlyEntry:
        return next(self._entries)


class CountingScandir:
    def __init__(self, total: int) -> None:
        self.total = total
        self.yielded = 0
        self.next_calls = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *unused: object) -> None:
        return None

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> NameOnlyEntry:
        self.next_calls += 1
        if self.yielded >= self.total:
            raise StopIteration
        self.yielded += 1
        return NameOnlyEntry(".env")


def test_registration_schema_defaults_bounds_and_risk(tmp_path: Path) -> None:
    _, policy = make_project(tmp_path)
    registry = ToolRegistry()

    definition = filesystem.register_list_directory_tool(registry, path_policy=policy)

    assert definition.name == "filesystem.list_directory"
    assert definition.risk_level == "read_only"
    assert definition.input_schema["type"] == "object"
    assert definition.input_schema["additionalProperties"] is False
    assert definition.input_schema["properties"]["relative_path"] == {
        "default": ".",
        "title": "Relative Path",
        "type": "string",
    }
    assert definition.input_schema["properties"]["limit"] == {
        "default": 50,
        "maximum": 100,
        "minimum": 1,
        "title": "Limit",
        "type": "integer",
    }
    assert "required" not in definition.input_schema
    assert filesystem.LIST_DIRECTORY_TOOL_TIMEOUT_SECONDS == 2.0
    assert registry.definitions() == (definition,)


@pytest.mark.parametrize(
    "arguments",
    [
        {"relative_path": 1},
        {"relative_path": False},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"limit": "10"},
        {"limit": 1.0},
        {"extra": "forbidden"},
    ],
)
async def test_invalid_schema_arguments_never_call_executor(
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

    monkeypatch.setattr(filesystem.ListDirectoryExecutor, "execute", forbidden_execute)

    result = await call_list_directory(policy, arguments)

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "tool_invalid_arguments"
    assert executor_called is False


async def test_default_arguments_use_to_thread_and_return_root_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    calls: list[tuple[object, tuple[object, ...]]] = []

    async def immediate_to_thread(function: object, *args: object) -> object:
        calls.append((function, args))
        return function(*args)  # type: ignore[operator]

    monkeypatch.setattr(filesystem.asyncio, "to_thread", immediate_to_thread)

    result = await call_list_directory(policy)

    assert result.success is True
    assert result.data == {
        "scope": "project",
        "relative_path": ".",
        "entries": [],
        "truncated": False,
    }
    assert result.error is None
    assert result.metadata == {}
    assert len(calls) == 1
    assert str(project_root) not in repr(result)


async def test_files_directories_and_other_entries_have_exact_safe_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    payload = b"seven!!"
    (project_root / "file.txt").write_bytes(payload)
    (project_root / "folder").mkdir()
    other = project_root / "other.node"
    other.write_bytes(b"not exposed")
    real_lstat = Path.lstat

    def fake_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        metadata = real_lstat(path)
        if path == other:
            return copy_metadata(metadata, mode=stat.S_IFIFO | 0o600)
        return metadata

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    result = await call_list_directory(policy)

    assert result.success is True
    assert result.data == {
        "scope": "project",
        "relative_path": ".",
        "entries": [
            {"name": "file.txt", "kind": "file", "size_bytes": len(payload)},
            {"name": "folder", "kind": "directory", "size_bytes": None},
            {"name": "other.node", "kind": "other", "size_bytes": None},
        ],
        "truncated": False,
    }
    assert result.error is None
    assert result.metadata == {}
    assert all(set(entry) == {"name", "kind", "size_bytes"} for entry in result.data["entries"])


async def test_entries_are_stably_sorted_before_limit_is_applied(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    for name in ["z.txt", "ß.txt", "b.txt", "ss.txt"]:
        (project_root / name).write_bytes(name.encode())

    result = await call_list_directory(policy, {"limit": 3})

    assert result.success is True
    assert result.data is not None
    assert [entry["name"] for entry in result.data["entries"]] == [
        "b.txt",
        "ss.txt",
        "ß.txt",
    ]
    assert result.data["truncated"] is True


@pytest.mark.parametrize(
    ("raw_total", "expected_truncated"),
    [(999, False), (1000, True), (1001, True)],
)
async def test_raw_scan_cap_never_consumes_entry_1001_and_is_conservative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_total: int,
    expected_truncated: bool,
) -> None:
    _, policy = make_project(tmp_path)
    scanner = CountingScandir(raw_total)
    monkeypatch.setattr(filesystem.os, "scandir", lambda unused: scanner)

    result = await call_list_directory(policy)

    assert result.success is True
    assert result.data is not None
    assert result.data["entries"] == []
    assert result.data["truncated"] is expected_truncated
    assert scanner.yielded == min(raw_total, 1000)
    assert scanner.next_calls <= 1000


async def test_protected_invalid_attributes_and_reparse_children_are_filtered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    names = [
        "visible.txt",
        ".env",
        "secret.pem",
        "bad:name",
        "hidden.txt",
        "system.txt",
        "offline.txt",
        "reparse.txt",
    ]
    for name in names:
        if name != "bad:name":
            (project_root / name).write_bytes(b"x")
    flagged = {
        project_root / "hidden.txt": stat.FILE_ATTRIBUTE_HIDDEN,
        project_root / "system.txt": stat.FILE_ATTRIBUTE_SYSTEM,
        project_root / "offline.txt": stat.FILE_ATTRIBUTE_OFFLINE,
        project_root / "reparse.txt": stat.FILE_ATTRIBUTE_REPARSE_POINT,
    }
    real_lstat = Path.lstat

    def fake_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        metadata = real_lstat(path)
        if path in flagged:
            attributes = getattr(metadata, "st_file_attributes", 0) | flagged[path]
            return copy_metadata(metadata, file_attributes=attributes)
        return metadata

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    monkeypatch.setattr(filesystem.os, "scandir", lambda unused: FakeScandir(names))

    result = await call_list_directory(policy)

    assert result.success is True
    assert result.data is not None
    assert result.data["entries"] == [{"name": "visible.txt", "kind": "file", "size_bytes": 1}]
    assert result.data["truncated"] is False


async def test_child_disappearing_during_scan_is_silently_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, policy = make_project(tmp_path)
    monkeypatch.setattr(
        filesystem.os,
        "scandir",
        lambda unused: FakeScandir(["vanished.txt"]),
    )

    result = await call_list_directory(policy)

    assert result.success is True
    assert result.data is not None
    assert result.data["entries"] == []
    assert result.data["truncated"] is False


@pytest.mark.parametrize("relative_path", ["", "..", r"C:\\private"])
async def test_policy_invalid_raw_paths_return_fixed_invalid_arguments(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    relative_path: str,
) -> None:
    project_root, policy = make_project(tmp_path)

    with caplog.at_level(logging.DEBUG):
        result = await call_list_directory(policy, {"relative_path": relative_path})

    assert result.success is False
    assert result.data is None
    assert result.error is not None
    assert result.error.code == "tool_invalid_arguments"
    assert result.error.retryable is False
    assert result.metadata == {}
    if relative_path:
        assert relative_path not in repr(result)
        assert relative_path not in caplog.text
    assert str(project_root) not in repr(result)
    assert str(project_root) not in caplog.text


async def test_missing_non_directory_and_protected_targets_share_fixed_failure(
    tmp_path: Path,
) -> None:
    project_root, policy = make_project(tmp_path)
    (project_root / "ordinary-file.txt").write_text("not a directory", encoding="utf-8")
    (project_root / ".git").mkdir()

    missing = await call_list_directory(policy, {"relative_path": "PRIVATE-missing"})
    file_result = await call_list_directory(policy, {"relative_path": "ordinary-file.txt"})
    protected = await call_list_directory(policy, {"relative_path": ".git"})

    missing_error = assert_unavailable(missing, "PRIVATE-missing", str(project_root))
    assert assert_unavailable(file_result, "ordinary-file.txt", str(project_root)) == missing_error
    assert assert_unavailable(protected, ".git", str(project_root)) == missing_error


@pytest.mark.parametrize(
    "attribute",
    [
        stat.FILE_ATTRIBUTE_HIDDEN,
        stat.FILE_ATTRIBUTE_SYSTEM,
        stat.FILE_ATTRIBUTE_OFFLINE,
        stat.FILE_ATTRIBUTE_REPARSE_POINT,
    ],
)
async def test_unsafe_or_unavailable_target_attributes_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attribute: int,
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "PRIVATE-target"
    target.mkdir()
    real_lstat = Path.lstat

    def fake_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        metadata = real_lstat(path)
        if path == target:
            attributes = getattr(metadata, "st_file_attributes", 0) | attribute
            return copy_metadata(metadata, file_attributes=attributes)
        return metadata

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    result = await call_list_directory(policy, {"relative_path": "PRIVATE-target"})

    assert_unavailable(result, "PRIVATE-target", str(project_root))


async def test_target_and_child_os_errors_fail_without_private_text_or_partial_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_root, policy = make_project(tmp_path)
    good = project_root / "a-good.txt"
    broken = project_root / "b-broken.txt"
    good.write_text("safe metadata only", encoding="utf-8")
    broken.write_text("not returned", encoding="utf-8")
    real_lstat = Path.lstat
    private_text = f"PRIVATE lstat failure inside {project_root}"

    def fail_child(path: Path) -> os.stat_result:
        if path == broken:
            raise PermissionError(private_text)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_child)
    with caplog.at_level(logging.DEBUG):
        child_result = await call_list_directory(policy)

    assert_unavailable(child_result, private_text, str(project_root), "a-good.txt")
    assert private_text not in caplog.text

    def fail_target(path: Path) -> os.stat_result:
        if path == project_root:
            raise OSError(private_text)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_target)
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        target_result = await call_list_directory(policy)

    assert_unavailable(target_result, private_text, str(project_root))
    assert private_text not in caplog.text


async def test_listing_is_nonrecursive_and_never_reads_file_content(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_root, policy = make_project(tmp_path)
    content_marker = "PRIVATE-CONTENT-MARKER-DO-NOT-READ"
    (project_root / "visible.txt").write_text(content_marker, encoding="utf-8")
    nested = project_root / "nested"
    nested.mkdir()
    (nested / "PRIVATE-deep-name.txt").write_text(content_marker, encoding="utf-8")

    with caplog.at_level(logging.DEBUG):
        result = await call_list_directory(policy)

    serialized = json.dumps(result.data)
    assert result.success is True
    assert "visible.txt" in serialized
    assert "nested" in serialized
    assert "PRIVATE-deep-name.txt" not in serialized
    assert content_marker not in serialized
    assert content_marker not in caplog.text


async def test_success_uses_canonical_posix_label_and_ignores_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "nested" / "child"
    target.mkdir(parents=True)
    unrelated = tmp_path / "unrelated-cwd"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    result = await call_list_directory(
        policy,
        {"relative_path": r"nested\child\."},
    )

    assert result.success is True
    assert result.data is not None
    assert result.data["relative_path"] == "nested/child"
    assert str(project_root) not in json.dumps(result.data)


async def test_pre_post_target_identity_change_discards_scanned_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    (project_root / "visible-before-race.txt").write_text("x", encoding="utf-8")
    real_lstat = Path.lstat
    root_lstat_calls = 0

    def changing_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        nonlocal root_lstat_calls
        metadata = real_lstat(path)
        if path == project_root:
            root_lstat_calls += 1
            if root_lstat_calls >= 4:
                return copy_metadata(metadata, inode=metadata.st_ino + 1)
        return metadata

    monkeypatch.setattr(Path, "lstat", changing_lstat)

    result = await call_list_directory(policy)

    assert root_lstat_calls >= 4
    assert_unavailable(result, "visible-before-race.txt", str(project_root))


async def test_pre_post_canonical_target_change_discards_scanned_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    replacement = project_root / "replacement"
    replacement.mkdir()
    real_resolve = ProjectPathPolicy.resolve
    calls = 0

    def changing_resolve(self: ProjectPathPolicy, relative_path: str) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            return replacement
        return real_resolve(self, relative_path)

    monkeypatch.setattr(ProjectPathPolicy, "resolve", changing_resolve)
    monkeypatch.setattr(filesystem.os, "scandir", lambda unused: FakeScandir([]))

    result = await call_list_directory(policy)

    assert calls == 2
    assert_unavailable(result, str(project_root), str(replacement))


def test_module_has_no_content_recursive_command_or_cross_domain_access() -> None:
    assert filesystem.__file__ is not None
    source = Path(filesystem.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module or "<relative-import>")

    forbidden_import_prefixes = {
        "hashlib",
        "subprocess",
        "jarvis_core.agent",
        "jarvis_core.conversation",
        "jarvis_core.llm",
        "jarvis_core.memory",
        "jarvis_core.provider",
        "jarvis_core.providers",
        "websockets",
    }
    assert all(
        not any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for prefix in forbidden_import_prefixes
        )
        for imported in imported_modules
    )

    forbidden_calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            forbidden_calls.append("open")
        elif isinstance(node.func, ast.Attribute) and node.func.attr in {
            "glob",
            "open",
            "read",
            "read_bytes",
            "read_text",
            "rglob",
            "walk",
        }:
            forbidden_calls.append(node.func.attr)

    assert forbidden_calls == []

    forbidden_metadata = {
        "st_atime",
        "st_birthtime",
        "st_ctime",
        "st_gid",
        "st_mtime",
        "st_uid",
    }
    referenced_attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert forbidden_metadata.isdisjoint(referenced_attributes)
