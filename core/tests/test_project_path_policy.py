import ast
import logging
import os
import stat
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from jarvis_core.tools import project_files
from jarvis_core.tools.project_files import (
    ProjectPathPolicy,
    ProjectPathPolicyError,
)


def assert_policy_error(
    error: ProjectPathPolicyError,
    expected_code: str,
    *private_fragments: str,
) -> None:
    assert error.code == expected_code
    assert error.args == (expected_code,)
    serialized = repr(error)
    for fragment in private_fragments:
        assert fragment not in serialized


@pytest.mark.parametrize("root_kind", ["relative", "missing", "file"])
def test_project_root_must_be_an_absolute_existing_directory(
    tmp_path: Path,
    root_kind: str,
) -> None:
    if root_kind == "relative":
        project_root = Path("PRIVATE-relative-project-root")
    elif root_kind == "missing":
        project_root = tmp_path / "PRIVATE-missing-project-root"
    else:
        project_root = tmp_path / "PRIVATE-project-root-file"
        project_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        ProjectPathPolicy(project_root)

    assert str(project_root) not in repr(exc_info.value)


def test_project_root_is_canonical_and_read_only(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    nested = project_root / "nested"
    nested.mkdir(parents=True)
    policy = ProjectPathPolicy(nested / "..")

    assert policy.project_root == project_root.resolve(strict=True)
    with pytest.raises(AttributeError):
        policy.project_root = tmp_path / "replacement"  # type: ignore[misc]


def test_absolute_project_root_does_not_depend_on_current_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    unrelated_cwd = tmp_path / "unrelated"
    project_root.mkdir()
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)

    policy = ProjectPathPolicy(project_root)

    assert policy.resolve("notes/today.txt") == project_root / "notes" / "today.txt"


def test_dot_resolves_to_project_root_and_empty_string_is_invalid(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    policy = ProjectPathPolicy(project_root)

    assert policy.resolve(".") == policy.project_root
    with pytest.raises(ProjectPathPolicyError) as exc_info:
        policy.resolve("")
    assert_policy_error(exc_info.value, "invalid_path")


def test_missing_tail_is_allowed_after_existing_ancestors(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    existing = project_root / "existing"
    existing.mkdir(parents=True)
    policy = ProjectPathPolicy(project_root)

    resolved = policy.resolve("existing/missing/deeper/file.txt")

    assert isinstance(resolved, Path)
    assert resolved == existing / "missing" / "deeper" / "file.txt"


def test_project_root_disappearing_after_construction_is_unavailable(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    policy = ProjectPathPolicy(project_root)
    project_root.rmdir()

    with pytest.raises(ProjectPathPolicyError) as exc_info:
        policy.resolve(".")

    assert_policy_error(exc_info.value, "path_unavailable")


@pytest.mark.parametrize(
    "relative_path",
    [
        r"C:\absolute.txt",
        r"C:drive-relative.txt",
        r"\rooted.txt",
        r"\\server\share\file.txt",
        r"\\?\C:\device.txt",
        r"\\.\PhysicalDrive0",
        r"//?/UNC/server/share/file.txt",
    ],
)
def test_windows_anchors_drives_unc_and_device_namespaces_are_invalid(
    tmp_path: Path,
    relative_path: str,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    policy = ProjectPathPolicy(project_root)

    with pytest.raises(ProjectPathPolicyError) as exc_info:
        policy.resolve(relative_path)

    assert_policy_error(exc_info.value, "invalid_path", relative_path)


@pytest.mark.parametrize(
    "relative_path",
    [
        "..",
        "../outside.txt",
        "safe/../outside.txt",
        r"safe\..\outside.txt",
        "file.txt:secret-stream",
        "folder:name/file.txt",
        "nul\x00byte.txt",
        "control\x1fbyte.txt",
        "control\u0085byte.txt",
        "CON",
        "con.txt",
        "AUX.log",
        "NUL.json",
        "COM1",
        "LPT9.txt",
        "COM\u00b9.txt",
        "CONIN$",
        "wild*.txt",
        "wild?.txt",
        "less<than.txt",
        "greater>than.txt",
        'double"quote.txt',
        "pipe|name.txt",
        "trailing-dot.",
        "trailing-space ",
        "nested/trailing-dot./file.txt",
    ],
)
def test_traversal_ads_controls_and_windows_reserved_names_are_invalid(
    tmp_path: Path,
    relative_path: str,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    policy = ProjectPathPolicy(project_root)

    with pytest.raises(ProjectPathPolicyError) as exc_info:
        policy.resolve(relative_path)

    assert_policy_error(exc_info.value, "invalid_path", relative_path)


@pytest.mark.parametrize(
    "relative_path",
    [
        ".git/config",
        "src/.TMP/cache.bin",
        ".VENV/pyvenv.cfg",
        "venv/Scripts/python.exe",
        "app/NODE_MODULES/package.json",
        "target/output.bin",
        "dist/bundle.js",
        "build/report.txt",
        ".idea/workspace.xml",
        ".vscode/settings.json",
        "__PYCACHE__/module.pyc",
        ".PYTEST_CACHE/state",
        ".RUFF_CACHE/state",
        ".env",
        ".ENV.Example",
        "config/.env.local",
        "memory.db",
        "state/MEMORY.DB-wal",
        "state/memory.db-shm",
        "id_rsa",
        "ID_DSA",
        "keys/id_ecdsa",
        "keys/ID_ED25519",
        "secret.pem",
        "secret.KEY",
        "secret.p12",
        "secret.PFX",
        "secret.ppk",
    ],
)
def test_protected_components_and_sensitive_basenames_are_rejected(
    tmp_path: Path,
    relative_path: str,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    policy = ProjectPathPolicy(project_root)

    with pytest.raises(ProjectPathPolicyError) as exc_info:
        policy.resolve(relative_path)

    assert_policy_error(exc_info.value, "protected_path", relative_path)


@pytest.mark.parametrize(
    "relative_path",
    [
        "safe..name",
        ".environment",
        "memory.db.backup",
        "id_rsa.pub",
        "certificate.crt",
        "distill/file.txt",
        "builder/output.txt",
    ],
)
def test_safe_near_miss_names_remain_allowed(
    tmp_path: Path,
    relative_path: str,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    policy = ProjectPathPolicy(project_root)

    resolved = policy.resolve(relative_path)

    assert resolved == project_root.joinpath(*PureWindowsPath(relative_path).parts)


def test_fake_reparse_attribute_on_existing_ancestor_is_unsafe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    trapped_ancestor = project_root / "ordinary-looking-directory"
    trapped_ancestor.mkdir(parents=True)
    policy = ProjectPathPolicy(project_root)
    real_lstat = Path.lstat

    def fake_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        result = real_lstat(path)
        if path == trapped_ancestor:
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_file_attributes=(
                    getattr(result, "st_file_attributes", 0) | stat.FILE_ATTRIBUTE_REPARSE_POINT
                ),
            )
        return result

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(ProjectPathPolicyError) as exc_info:
        policy.resolve("ordinary-looking-directory/missing.txt")

    assert_policy_error(exc_info.value, "unsafe_path")


def test_fake_reparse_project_root_is_invalid_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "PRIVATE-reparse-project-root"
    project_root.mkdir()
    real_lstat = Path.lstat

    def fake_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        result = real_lstat(path)
        if path == project_root:
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_file_attributes=(
                    getattr(result, "st_file_attributes", 0) | stat.FILE_ATTRIBUTE_REPARSE_POINT
                ),
            )
        return result

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(ValueError) as exc_info:
        ProjectPathPolicy(project_root)

    assert str(project_root) not in repr(exc_info.value)


def test_real_symlink_is_unsafe_when_platform_allows_creating_one(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    outside = tmp_path / "outside"
    project_root.mkdir()
    outside.mkdir()
    symlink = project_root / "linked-directory"
    try:
        symlink.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("platform cannot create test symlink: WinError 1314")
        raise
    policy = ProjectPathPolicy(project_root)

    with pytest.raises(ProjectPathPolicyError) as exc_info:
        policy.resolve("linked-directory/private.txt")

    assert_policy_error(exc_info.value, "unsafe_path")


def test_canonical_escape_is_unsafe_even_after_lexical_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    outside = tmp_path / "PRIVATE-outside"
    project_root.mkdir()
    policy = ProjectPathPolicy(project_root)

    def escape_project(_path: os.PathLike[str] | str, *, strict: object = False) -> str:
        assert strict is os.path.ALLOW_MISSING
        return str(outside / "private.txt")

    monkeypatch.setattr(project_files.os.path, "realpath", escape_project)

    with pytest.raises(ProjectPathPolicyError) as exc_info:
        policy.resolve("safe-looking.txt")

    assert_policy_error(
        exc_info.value,
        "unsafe_path",
        str(outside),
        "safe-looking.txt",
    )


def test_canonical_protected_components_are_checked_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    policy = ProjectPathPolicy(project_root)

    def redirect_to_protected(
        _path: os.PathLike[str] | str,
        *,
        strict: object = False,
    ) -> str:
        assert strict is os.path.ALLOW_MISSING
        return str(project_root / ".git" / "config")

    monkeypatch.setattr(project_files.os.path, "realpath", redirect_to_protected)

    with pytest.raises(ProjectPathPolicyError) as exc_info:
        policy.resolve("safe-looking.txt")

    assert_policy_error(exc_info.value, "protected_path")


@pytest.mark.parametrize("exception_type", [PermissionError, OSError])
def test_lstat_os_errors_fail_closed_without_disclosing_private_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    exception_type: type[OSError],
) -> None:
    project_root = tmp_path / "PRIVATE-project-root"
    project_root.mkdir()
    policy = ProjectPathPolicy(project_root)
    private_text = f"PRIVATE failure while probing {project_root}"

    def fail_lstat(_path: Path) -> os.stat_result:
        raise exception_type(private_text)

    monkeypatch.setattr(Path, "lstat", fail_lstat)

    with caplog.at_level(logging.DEBUG), pytest.raises(ProjectPathPolicyError) as exc_info:
        policy.resolve("PRIVATE-input.txt")

    assert_policy_error(
        exc_info.value,
        "path_unavailable",
        private_text,
        str(project_root),
        "PRIVATE-input.txt",
    )
    assert private_text not in caplog.text


def test_canonicalization_os_error_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_root = tmp_path / "PRIVATE-project-root"
    project_root.mkdir()
    policy = ProjectPathPolicy(project_root)
    private_text = f"PRIVATE canonicalization failure at {project_root}"

    def fail_realpath(
        _path: os.PathLike[str] | str,
        *,
        strict: object = False,
    ) -> str:
        raise PermissionError(private_text)

    monkeypatch.setattr(project_files.os.path, "realpath", fail_realpath)

    with caplog.at_level(logging.DEBUG), pytest.raises(ProjectPathPolicyError) as exc_info:
        policy.resolve("PRIVATE-input.txt")

    assert_policy_error(
        exc_info.value,
        "path_unavailable",
        private_text,
        str(project_root),
        "PRIVATE-input.txt",
    )
    assert private_text not in caplog.text


def test_policy_module_has_no_content_reads_or_cross_domain_imports() -> None:
    assert project_files.__file__ is not None
    source = Path(project_files.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module or "<relative-import>")

    forbidden_import_prefixes = {
        "jarvis_core.agent",
        "jarvis_core.conversation",
        "jarvis_core.llm",
        "jarvis_core.memory",
        "jarvis_core.provider",
        "jarvis_core.providers",
        "jarvis_core.tools.contracts",
        "jarvis_core.tools.registry",
        "websockets",
    }
    assert all(
        not any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for prefix in forbidden_import_prefixes
        )
        for imported in imported_modules
    )

    content_read_calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            content_read_calls.append("open")
        elif isinstance(node.func, ast.Attribute) and node.func.attr in {
            "open",
            "read",
            "read_bytes",
            "read_text",
        }:
            content_read_calls.append(node.func.attr)

    assert content_read_calls == []
