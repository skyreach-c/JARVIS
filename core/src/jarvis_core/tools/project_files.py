from __future__ import annotations

import ntpath
import os
import stat
import unicodedata
from pathlib import Path, PureWindowsPath
from typing import Literal

type ProjectPathPolicyErrorCode = Literal[
    "invalid_path",
    "protected_path",
    "unsafe_path",
    "path_unavailable",
]

_INVALID_PROJECT_ROOT = "invalid_project_root"
_PROTECTED_COMPONENTS = frozenset(
    {
        ".git",
        ".tmp",
        ".venv",
        "venv",
        "node_modules",
        "target",
        "dist",
        "build",
        ".idea",
        ".vscode",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
    }
)
_PRIVATE_KEY_BASENAMES = frozenset(
    {
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)
_PRIVATE_KEY_EXTENSIONS = (".pem", ".key", ".p12", ".pfx", ".ppk")


class ProjectPathPolicyError(Exception):
    """A path-policy rejection carrying only a stable, non-sensitive code."""

    def __init__(self, code: ProjectPathPolicyErrorCode) -> None:
        self.code = code
        super().__init__(code)


class ProjectPathPolicy:
    """Preflight Windows-style project-relative paths within one canonical root."""

    __slots__ = ("_project_root",)

    def __init__(self, project_root: Path) -> None:
        if not isinstance(project_root, Path) or not project_root.is_absolute():
            raise ValueError(_INVALID_PROJECT_ROOT)

        try:
            root_metadata = project_root.lstat()
            if _is_reparse_or_symlink(root_metadata) or not stat.S_ISDIR(root_metadata.st_mode):
                raise ValueError(_INVALID_PROJECT_ROOT)

            canonical_root = Path(os.path.realpath(project_root, strict=True))
            canonical_metadata = canonical_root.lstat()
            if _is_reparse_or_symlink(canonical_metadata) or not stat.S_ISDIR(
                canonical_metadata.st_mode
            ):
                raise ValueError(_INVALID_PROJECT_ROOT)
        except OSError, ValueError:
            raise ValueError(_INVALID_PROJECT_ROOT) from None

        self._project_root = canonical_root

    @property
    def project_root(self) -> Path:
        return self._project_root

    def resolve(self, relative_path: str) -> Path:
        components = _validate_relative_path(relative_path)
        _reject_protected_components(components)

        candidate = self._project_root.joinpath(*components)
        self._reject_unsafe_existing_components(components)

        try:
            resolved = Path(
                os.path.realpath(
                    candidate,
                    strict=os.path.ALLOW_MISSING,
                )
            )
        except OSError:
            raise ProjectPathPolicyError("path_unavailable") from None

        if resolved != self._project_root and not resolved.is_relative_to(self._project_root):
            raise ProjectPathPolicyError("unsafe_path")

        canonical_components = resolved.relative_to(self._project_root).parts
        _reject_protected_components(canonical_components)
        return resolved

    def _reject_unsafe_existing_components(self, components: tuple[str, ...]) -> None:
        current = self._project_root
        paths = [current]
        for component in components:
            current /= component
            paths.append(current)

        for index, path in enumerate(paths):
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                if index == 0:
                    raise ProjectPathPolicyError("path_unavailable") from None
                return
            except OSError:
                raise ProjectPathPolicyError("path_unavailable") from None

            if _is_reparse_or_symlink(metadata):
                raise ProjectPathPolicyError("unsafe_path")
            if index == 0 and not stat.S_ISDIR(metadata.st_mode):
                raise ProjectPathPolicyError("path_unavailable")


def _validate_relative_path(relative_path: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str) or relative_path == "":
        raise ProjectPathPolicyError("invalid_path")
    if ":" in relative_path or any(
        character == "\x00" or unicodedata.category(character) == "Cc"
        for character in relative_path
    ):
        raise ProjectPathPolicyError("invalid_path")

    windows_path = PureWindowsPath(relative_path)
    if windows_path.drive or windows_path.root:
        raise ProjectPathPolicyError("invalid_path")

    components = windows_path.parts
    if any(component == ".." or ntpath.isreserved(component) for component in components):
        raise ProjectPathPolicyError("invalid_path")
    return components


def _reject_protected_components(components: tuple[str, ...]) -> None:
    if any(_is_protected_component(component) for component in components):
        raise ProjectPathPolicyError("protected_path")


def _is_protected_component(component: str) -> bool:
    basename = component.casefold()
    return (
        basename in _PROTECTED_COMPONENTS
        or basename == ".env"
        or basename == "memory.db"
        or basename.startswith((".env.", "memory.db-"))
        or basename in _PRIVATE_KEY_BASENAMES
        or basename.endswith(_PRIVATE_KEY_EXTENSIONS)
    )


def _is_reparse_or_symlink(metadata: os.stat_result) -> bool:
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )
