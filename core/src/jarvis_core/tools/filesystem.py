from __future__ import annotations

import asyncio
import os
import stat
from itertools import islice
from pathlib import Path

from pydantic import Field

from jarvis_core.tools.contracts import (
    JsonValue,
    ToolArguments,
    ToolDefinition,
    ToolError,
    ToolResult,
)
from jarvis_core.tools.project_files import (
    ProjectPathPolicy,
    ProjectPathPolicyError,
)
from jarvis_core.tools.registry import ToolRegistry

LIST_DIRECTORY_TOOL_NAME = "filesystem.list_directory"
LIST_DIRECTORY_TOOL_TIMEOUT_SECONDS = 2.0
GET_METADATA_TOOL_NAME = "filesystem.get_metadata"
GET_METADATA_TOOL_TIMEOUT_SECONDS = 2.0

_MAX_RAW_ENTRIES = 1000
_FILTERED_ATTRIBUTES = (
    stat.FILE_ATTRIBUTE_HIDDEN
    | stat.FILE_ATTRIBUTE_SYSTEM
    | stat.FILE_ATTRIBUTE_OFFLINE
    | stat.FILE_ATTRIBUTE_REPARSE_POINT
)
_INVALID_ARGUMENTS_MESSAGE = "The project-relative directory path is invalid."
_PATH_UNAVAILABLE_MESSAGE = "The requested project directory is unavailable."
_GET_METADATA_INVALID_ARGUMENTS_MESSAGE = "The project-relative path is invalid."
_GET_METADATA_PATH_UNAVAILABLE_MESSAGE = "The requested project path is unavailable."


class ListDirectoryArguments(ToolArguments):
    relative_path: str = "."
    limit: int = Field(default=50, ge=1, le=100)


class GetMetadataArguments(ToolArguments):
    relative_path: str = Field(min_length=1)


class ListDirectoryExecutor:
    __slots__ = ("_path_policy",)

    def __init__(self, *, path_policy: ProjectPathPolicy) -> None:
        self._path_policy = path_policy

    async def execute(self, arguments: ToolArguments) -> ToolResult:
        if not isinstance(arguments, ListDirectoryArguments):
            return _invalid_arguments()
        return await asyncio.to_thread(self._execute_sync, arguments)

    def _execute_sync(self, arguments: ListDirectoryArguments) -> ToolResult:
        try:
            target = self._path_policy.resolve(arguments.relative_path)
        except ProjectPathPolicyError as error:
            if error.code == "invalid_path":
                return _invalid_arguments()
            return _path_unavailable()

        try:
            before_metadata = target.lstat()
        except OSError:
            return _path_unavailable()
        if not _is_available_directory(before_metadata):
            return _path_unavailable()

        canonical_label = _canonical_label(
            project_root=self._path_policy.project_root,
            target=target,
        )
        if canonical_label is None:
            return _path_unavailable()

        entries: list[dict[str, JsonValue]] = []
        raw_count = 0
        try:
            with os.scandir(target) as scanner:
                for child in islice(scanner, _MAX_RAW_ENTRIES):
                    raw_count += 1
                    name = child.name
                    if not isinstance(name, str):
                        continue

                    child_label = name if canonical_label == "." else f"{canonical_label}/{name}"
                    try:
                        child_path = self._path_policy.resolve(child_label)
                    except ProjectPathPolicyError as error:
                        if error.code in {"invalid_path", "protected_path", "unsafe_path"}:
                            continue
                        return _path_unavailable()

                    if child_path.parent != target:
                        continue
                    try:
                        child_metadata = child_path.lstat()
                    except FileNotFoundError:
                        continue
                    except OSError:
                        return _path_unavailable()

                    entry = _safe_entry(name=name, metadata=child_metadata)
                    if entry is not None:
                        entries.append(entry)
        except OSError:
            return _path_unavailable()

        try:
            target_after = self._path_policy.resolve(arguments.relative_path)
            after_metadata = target_after.lstat()
        except ProjectPathPolicyError, OSError:
            return _path_unavailable()
        if (
            target_after != target
            or not _is_available_directory(after_metadata)
            or _identity(after_metadata) != _identity(before_metadata)
        ):
            return _path_unavailable()

        entries.sort(key=lambda entry: (str(entry["name"]).casefold(), str(entry["name"])))
        truncated = raw_count == _MAX_RAW_ENTRIES or len(entries) > arguments.limit
        return ToolResult(
            success=True,
            data={
                "scope": "project",
                "relative_path": canonical_label,
                "entries": entries[: arguments.limit],
                "truncated": truncated,
            },
            error=None,
            metadata={},
        )


class GetMetadataExecutor:
    __slots__ = ("_path_policy",)

    def __init__(self, *, path_policy: ProjectPathPolicy) -> None:
        self._path_policy = path_policy

    async def execute(self, arguments: ToolArguments) -> ToolResult:
        if not isinstance(arguments, GetMetadataArguments):
            return _get_metadata_invalid_arguments()
        return await asyncio.to_thread(self._execute_sync, arguments)

    def _execute_sync(self, arguments: GetMetadataArguments) -> ToolResult:
        try:
            target = self._path_policy.resolve(arguments.relative_path)
        except ProjectPathPolicyError as error:
            if error.code == "invalid_path":
                return _get_metadata_invalid_arguments()
            return _get_metadata_path_unavailable()

        canonical_label = _canonical_label(
            project_root=self._path_policy.project_root,
            target=target,
        )
        if canonical_label is None:
            return _get_metadata_path_unavailable()

        try:
            before_metadata = target.lstat()
        except FileNotFoundError:
            return self._confirm_missing(
                arguments=arguments,
                target=target,
                canonical_label=canonical_label,
            )
        except OSError:
            return _get_metadata_path_unavailable()

        if _has_filtered_attributes(before_metadata):
            return _get_metadata_path_unavailable()
        before_kind = _metadata_kind(before_metadata)

        # This preflight recheck narrows TOCTOU exposure, but cannot prevent
        # same-identity ABA races without holding an operating-system handle.
        try:
            target_after = self._path_policy.resolve(arguments.relative_path)
            after_metadata = target_after.lstat()
        except ProjectPathPolicyError, OSError:
            return _get_metadata_path_unavailable()

        canonical_label_after = _canonical_label(
            project_root=self._path_policy.project_root,
            target=target_after,
        )
        after_kind = _metadata_kind(after_metadata)
        if (
            target_after != target
            or canonical_label_after != canonical_label
            or _identity(after_metadata) != _identity(before_metadata)
            or after_kind != before_kind
            or _filtered_state(after_metadata) != _filtered_state(before_metadata)
            or _has_filtered_attributes(after_metadata)
        ):
            return _get_metadata_path_unavailable()

        return _metadata_success(
            relative_path=canonical_label,
            exists=True,
            kind=after_kind,
            size_bytes=after_metadata.st_size if after_kind == "file" else None,
        )

    def _confirm_missing(
        self,
        *,
        arguments: GetMetadataArguments,
        target: Path,
        canonical_label: str,
    ) -> ToolResult:
        try:
            target_after = self._path_policy.resolve(arguments.relative_path)
        except ProjectPathPolicyError:
            return _get_metadata_path_unavailable()

        try:
            target_after.lstat()
        except FileNotFoundError:
            canonical_label_after = _canonical_label(
                project_root=self._path_policy.project_root,
                target=target_after,
            )
            if target_after != target or canonical_label_after != canonical_label:
                return _get_metadata_path_unavailable()
            return _metadata_success(
                relative_path=canonical_label,
                exists=False,
                kind=None,
                size_bytes=None,
            )
        except OSError:
            return _get_metadata_path_unavailable()
        return _get_metadata_path_unavailable()


def register_list_directory_tool(
    registry: ToolRegistry,
    *,
    path_policy: ProjectPathPolicy,
) -> ToolDefinition:
    return registry.register(
        name=LIST_DIRECTORY_TOOL_NAME,
        description="List safe metadata for one project directory without recursion.",
        arguments_model=ListDirectoryArguments,
        executor=ListDirectoryExecutor(path_policy=path_policy),
        risk_level="read_only",
        timeout_seconds=LIST_DIRECTORY_TOOL_TIMEOUT_SECONDS,
    )


def register_get_metadata_tool(
    registry: ToolRegistry,
    *,
    path_policy: ProjectPathPolicy,
) -> ToolDefinition:
    return registry.register(
        name=GET_METADATA_TOOL_NAME,
        description="Return safe metadata for one project-relative path.",
        arguments_model=GetMetadataArguments,
        executor=GetMetadataExecutor(path_policy=path_policy),
        risk_level="read_only",
        timeout_seconds=GET_METADATA_TOOL_TIMEOUT_SECONDS,
    )


def _canonical_label(*, project_root: Path, target: Path) -> str | None:
    try:
        relative = target.relative_to(project_root)
    except ValueError:
        return None
    return "." if relative == Path(".") else relative.as_posix()


def _is_available_directory(metadata: os.stat_result) -> bool:
    return stat.S_ISDIR(metadata.st_mode) and not _has_filtered_attributes(metadata)


def _has_filtered_attributes(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _FILTERED_ATTRIBUTES) or stat.S_ISLNK(
        metadata.st_mode
    )


def _filtered_state(metadata: os.stat_result) -> tuple[int, bool]:
    return (
        getattr(metadata, "st_file_attributes", 0) & _FILTERED_ATTRIBUTES,
        stat.S_ISLNK(metadata.st_mode),
    )


def _metadata_kind(metadata: os.stat_result) -> str:
    if stat.S_ISREG(metadata.st_mode):
        return "file"
    if stat.S_ISDIR(metadata.st_mode):
        return "directory"
    return "other"


def _safe_entry(*, name: str, metadata: os.stat_result) -> dict[str, JsonValue] | None:
    if _has_filtered_attributes(metadata):
        return None
    if stat.S_ISREG(metadata.st_mode):
        kind = "file"
        size_bytes: int | None = metadata.st_size
    elif stat.S_ISDIR(metadata.st_mode):
        kind = "directory"
        size_bytes = None
    else:
        kind = "other"
        size_bytes = None
    return {"name": name, "kind": kind, "size_bytes": size_bytes}


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _metadata_success(
    *,
    relative_path: str,
    exists: bool,
    kind: str | None,
    size_bytes: int | None,
) -> ToolResult:
    return ToolResult(
        success=True,
        data={
            "scope": "project",
            "relative_path": relative_path,
            "exists": exists,
            "kind": kind,
            "size_bytes": size_bytes,
        },
        error=None,
        metadata={},
    )


def _invalid_arguments() -> ToolResult:
    return ToolResult(
        success=False,
        data=None,
        error=ToolError(
            code="tool_invalid_arguments",
            message=_INVALID_ARGUMENTS_MESSAGE,
            retryable=False,
        ),
        metadata={},
    )


def _path_unavailable() -> ToolResult:
    return ToolResult(
        success=False,
        data=None,
        error=ToolError(
            code="filesystem_path_unavailable",
            message=_PATH_UNAVAILABLE_MESSAGE,
            retryable=False,
        ),
        metadata={},
    )


def _get_metadata_invalid_arguments() -> ToolResult:
    return ToolResult(
        success=False,
        data=None,
        error=ToolError(
            code="tool_invalid_arguments",
            message=_GET_METADATA_INVALID_ARGUMENTS_MESSAGE,
            retryable=False,
        ),
        metadata={},
    )


def _get_metadata_path_unavailable() -> ToolResult:
    return ToolResult(
        success=False,
        data=None,
        error=ToolError(
            code="filesystem_path_unavailable",
            message=_GET_METADATA_PATH_UNAVAILABLE_MESSAGE,
            retryable=False,
        ),
        metadata={},
    )
