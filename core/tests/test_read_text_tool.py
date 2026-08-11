import asyncio
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis_core.tools import text_files
from jarvis_core.tools.contracts import ToolCall, ToolResult
from jarvis_core.tools.filesystem import GetMetadataArguments
from jarvis_core.tools.project_files import ProjectPathPolicy, ProjectPathPolicyError
from jarvis_core.tools.registry import ToolRegistry


def make_project(tmp_path: Path) -> tuple[Path, ProjectPathPolicy]:
    project_root = tmp_path / "PRIVATE-project-root"
    project_root.mkdir()
    return project_root, ProjectPathPolicy(project_root)


def assert_failure(
    result: ToolResult,
    code: str,
    *private_fragments: str,
) -> None:
    assert result.success is False
    assert result.data is None
    assert result.error is not None
    assert result.error.code == code
    assert result.error.retryable is False
    assert result.metadata == {}
    serialized = repr(result)
    for fragment in private_fragments:
        assert fragment not in serialized


def copy_metadata(
    metadata: os.stat_result,
    *,
    mode: int | None = None,
    file_attributes: int | None = None,
    inode: int | None = None,
    size: int | None = None,
    mtime_ns: int | None = None,
    ctime_ns: int | None = None,
    birthtime_ns: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        st_mode=metadata.st_mode if mode is None else mode,
        st_size=metadata.st_size if size is None else size,
        st_dev=metadata.st_dev,
        st_ino=metadata.st_ino if inode is None else inode,
        st_mtime_ns=metadata.st_mtime_ns if mtime_ns is None else mtime_ns,
        st_ctime_ns=metadata.st_ctime_ns if ctime_ns is None else ctime_ns,
        st_birthtime_ns=(
            getattr(metadata, "st_birthtime_ns", 0)
            if birthtime_ns is None
            else birthtime_ns
        ),
        st_file_attributes=(
            getattr(metadata, "st_file_attributes", 0)
            if file_attributes is None
            else file_attributes
        ),
    )


def secret_canary(kind: str) -> str:
    if kind == "pem_private_key":
        return "-----BEGIN " + "PRIVATE KEY-----"
    if kind == "pgp_private_key":
        return "-----BEGIN PGP " + "PRIVATE KEY BLOCK-----"
    if kind == "authorization_bearer":
        return "Authorization: " + "Bearer " + "Q7_" * 10
    if kind == "authorization_basic":
        return "Authorization = \"" + "Basic " + "QWxh" * 8 + "==\""
    if kind == "authorization_json_bearer":
        return '"Authorization": "' + "Bearer " + "R8_" * 10 + '"'
    if kind == "aws_access_key":
        return "AK" + "IA" + "A1" * 8
    if kind == "aws_secret_key":
        return "aws_secret_access_key = \"" + "aA1/" * 10 + "\""
    if kind == "github_token":
        return "gh" + "p_" + "A1" * 20
    if kind == "sk_token":
        return "s" + "k-" + "A1_" * 10
    if kind == "jwt":
        return "eyJ" + "A" * 12 + "." + "B" * 16 + "." + "C" * 20
    if kind in {
        "api_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "password",
    }:
        return f'{kind} = "' + "L1v3_" * 5 + '"'
    if kind == "typed_password":
        return 'password: str = "' + "Production" + "Secret123!" + '"'
    raise AssertionError("unknown secret canary kind")


async def call_read_text(
    policy: ProjectPathPolicy,
    arguments: dict[str, object],
) -> ToolResult:
    registry = ToolRegistry()
    text_files.register_read_text_tool(registry, path_policy=policy)
    return await registry.execute(
        ToolCall(
            tool_name=text_files.READ_TEXT_TOOL_NAME,
            arguments=arguments,
        ),
        request_id="request-read-text",
    )


def test_registration_has_strict_schema_read_only_risk_and_fixed_budgets(
    tmp_path: Path,
) -> None:
    _, policy = make_project(tmp_path)
    registry = ToolRegistry()

    definition = text_files.register_read_text_tool(registry, path_policy=policy)

    assert definition.name == "filesystem.read_text"
    assert definition.risk_level == "read_only"
    assert definition.input_schema == {
        "additionalProperties": False,
        "properties": {
            "relative_path": {
                "minLength": 1,
                "title": "Relative Path",
                "type": "string",
            },
            "start_line": {
                "default": 1,
                "minimum": 1,
                "title": "Start Line",
                "type": "integer",
            },
            "max_lines": {
                "default": 200,
                "maximum": 200,
                "minimum": 1,
                "title": "Max Lines",
                "type": "integer",
            },
        },
        "required": ["relative_path"],
        "title": "ReadTextArguments",
        "type": "object",
    }
    assert text_files.READ_TEXT_TOOL_TIMEOUT_SECONDS == 2.0
    assert text_files.MAX_SOURCE_BYTES == 262_144
    assert text_files.MAX_RETURN_LINES == 200
    assert text_files.MAX_RETURN_CHARS == 20_000
    assert text_files.MAX_RETURN_UTF8_BYTES == 65_536
    assert registry.definitions() == (definition,)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"relative_path": ""},
        {"relative_path": 1},
        {"relative_path": False},
        {"relative_path": None},
        {"relative_path": "file.txt", "start_line": 0},
        {"relative_path": "file.txt", "start_line": False},
        {"relative_path": "file.txt", "start_line": 1.0},
        {"relative_path": "file.txt", "start_line": "1"},
        {"relative_path": "file.txt", "max_lines": 0},
        {"relative_path": "file.txt", "max_lines": 201},
        {"relative_path": "file.txt", "max_lines": True},
        {"relative_path": "file.txt", "max_lines": 1.0},
        {"relative_path": "file.txt", "max_lines": "1"},
        {"relative_path": "file.txt", "extra": "forbidden"},
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

    monkeypatch.setattr(text_files.ReadTextExecutor, "execute", forbidden_execute)

    result = await call_read_text(policy, arguments)

    assert result.success is False
    assert result.data is None
    assert result.error is not None
    assert result.error.code == "tool_invalid_arguments"
    assert result.metadata == {}
    assert executor_called is False


async def test_executor_uses_to_thread_and_returns_exact_untrusted_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    content = "alpha\n中文🙂\n"
    (project_root / "note.md").write_text(content, encoding="utf-8", newline="")
    calls: list[tuple[object, tuple[object, ...]]] = []

    async def immediate_to_thread(function: object, *args: object) -> object:
        calls.append((function, args))
        return function(*args)  # type: ignore[operator]

    monkeypatch.setattr(text_files.asyncio, "to_thread", immediate_to_thread)

    result = await call_read_text(policy, {"relative_path": "note.md"})

    assert result.success is True
    assert result.data == {
        "scope": "project",
        "relative_path": "note.md",
        "encoding": "utf-8",
        "content_trust": "untrusted_data",
        "instruction_authority": "none",
        "line_start": 1,
        "line_end": 2,
        "total_lines": 2,
        "lines_returned": 2,
        "chars_returned": len(content),
        "utf8_bytes_returned": len(content.encode("utf-8")),
        "content": content,
        "truncated": False,
        "truncation_reason": None,
        "next_start_line": None,
    }
    assert result.error is None
    assert result.metadata == {}
    assert len(calls) == 1
    assert str(project_root) not in repr(result)


async def test_executor_rejects_the_wrong_validated_arguments_model(tmp_path: Path) -> None:
    _, policy = make_project(tmp_path)
    executor = text_files.ReadTextExecutor(path_policy=policy)

    result = await executor.execute(GetMetadataArguments(relative_path="file.txt"))

    assert result.success is False
    assert result.data is None
    assert result.error is not None
    assert result.error.code == "tool_invalid_arguments"
    assert result.metadata == {}


@pytest.mark.parametrize(
    "suffix",
    [
        ".py",
        ".md",
        ".txt",
        ".toml",
        ".json",
        ".yaml",
        ".yml",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".cxx",
        ".hh",
        ".hpp",
        ".hxx",
        ".rs",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".xml",
        ".ini",
        ".cfg",
        ".conf",
        ".ps1",
        ".sh",
    ],
)
async def test_every_allowlisted_suffix_is_readable_case_insensitively(
    tmp_path: Path,
    suffix: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    actual_suffix = suffix.upper() if suffix == ".md" else suffix
    filename = f"source{actual_suffix}"
    (project_root / filename).write_bytes(b"ordinary source\n")

    result = await call_read_text(policy, {"relative_path": filename})

    assert result.success is True
    assert result.data is not None
    assert result.data["content"] == "ordinary source\n"


@pytest.mark.parametrize(
    "filename",
    ["README", "archive.exe", "image.png", "source.pyc"],
)
async def test_extensionless_and_non_allowlisted_files_are_unsupported(
    tmp_path: Path,
    filename: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    (project_root / filename).write_bytes(b"PRIVATE-UNSUPPORTED-CONTENT")

    result = await call_read_text(policy, {"relative_path": filename})

    assert_failure(
        result,
        "filesystem_text_unsupported",
        filename,
        "PRIVATE-UNSUPPORTED-CONTENT",
        str(project_root),
    )


@pytest.mark.parametrize("directory", [".ssh", ".aws", ".azure", ".kube", ".docker"])
async def test_exact_sensitive_directory_components_are_rejected(
    tmp_path: Path,
    directory: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "nested" / directory.upper() / "config.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"ordinary": true}')

    result = await call_read_text(
        policy,
        {"relative_path": f"nested/{directory.upper()}/config.json"},
    )

    assert_failure(result, "filesystem_sensitive_content", directory, str(project_root))


@pytest.mark.parametrize(
    "filename",
    [
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".git-credentials",
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        "token.json",
        "tokens.json",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "auth.json",
        "client_secret.json",
        "service-account-key.json",
        "service_account_key.json",
        "client_secret_google.json",
        "service-account-key_ci-1.json",
        "service_account_key_robot.json",
    ],
)
async def test_exact_and_narrow_sensitive_basenames_are_rejected(
    tmp_path: Path,
    filename: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    (project_root / filename).write_bytes(b'{"ordinary": true}')

    result = await call_read_text(policy, {"relative_path": filename})

    assert_failure(result, "filesystem_sensitive_content", filename, str(project_root))


@pytest.mark.parametrize(
    "filename",
    [
        "secret_scanner.py",
        "tokenizer.py",
        "auth.py",
        "test_token_parser.py",
        "credential_validator.ts",
        "credentials_backup.json",
        "token-helper.json",
        "client_secrets_google.json",
        "client_secret_.json",
        "service-account-key_.json",
    ],
)
async def test_sensitive_name_near_misses_remain_readable(
    tmp_path: Path,
    filename: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    (project_root / filename).write_bytes(b"ordinary source\n")

    result = await call_read_text(policy, {"relative_path": filename})

    assert result.success is True
    assert result.data is not None
    assert result.data["content"] == "ordinary source\n"


@pytest.mark.parametrize(
    ("relative_path", "expected_code"),
    [
        ("../escape.txt", "tool_invalid_arguments"),
        (r"C:\\private.txt", "tool_invalid_arguments"),
        ("bad:name.txt", "tool_invalid_arguments"),
        (".git/config.txt", "filesystem_path_unavailable"),
        ("missing.txt", "filesystem_path_unavailable"),
    ],
)
async def test_project_scope_and_policy_failures_are_sanitized(
    tmp_path: Path,
    relative_path: str,
    expected_code: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    (project_root / ".git").mkdir()

    result = await call_read_text(policy, {"relative_path": relative_path})

    assert_failure(result, expected_code, relative_path, str(project_root))


async def test_directories_are_not_read_as_text_files(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    (project_root / "folder.txt").mkdir()

    result = await call_read_text(policy, {"relative_path": "folder.txt"})

    assert_failure(result, "filesystem_path_unavailable", "folder.txt", str(project_root))


@pytest.mark.parametrize(
    "attribute",
    [
        stat.FILE_ATTRIBUTE_HIDDEN,
        stat.FILE_ATTRIBUTE_SYSTEM,
        stat.FILE_ATTRIBUTE_OFFLINE,
        stat.FILE_ATTRIBUTE_REPARSE_POINT,
    ],
)
async def test_hidden_system_offline_and_reparse_files_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attribute: int,
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "PRIVATE-flagged.txt"
    target.write_bytes(b"PRIVATE-FLAGGED-CONTENT")
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

    result = await call_read_text(policy, {"relative_path": "PRIVATE-flagged.txt"})

    assert_failure(
        result,
        "filesystem_path_unavailable",
        "PRIVATE-flagged.txt",
        "PRIVATE-FLAGGED-CONTENT",
        str(project_root),
    )


async def test_binary_fd_uses_noninheritable_nofollow_flags_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "source.txt"
    target.write_bytes(b"safe\n")
    real_open = os.open
    observed_flags: list[int] = []

    def recording_open(path: os.PathLike[str] | str, flags: int, mode: int = 0o777) -> int:
        observed_flags.append(flags)
        return real_open(path, flags, mode)

    monkeypatch.setattr(text_files.os, "open", recording_open)

    result = await call_read_text(policy, {"relative_path": "source.txt"})

    assert result.success is True
    assert len(observed_flags) == 1
    required_flags = os.O_RDONLY
    for flag_name in ("O_BINARY", "O_NOINHERIT", "O_NOFOLLOW"):
        required_flags |= getattr(os, flag_name, 0)
    assert observed_flags[0] & required_flags == required_flags


@pytest.mark.parametrize(
    "mutation",
    ["identity", "size", "mtime", "ctime", "birthtime", "kind", "attributes"],
)
async def test_open_handle_metadata_mismatch_discards_the_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    marker = "PRIVATE-OPEN-RACE-CONTENT"
    target = project_root / "racing.txt"
    target.write_text(marker, encoding="utf-8")
    real_read = os.read
    read_calls = 0

    def changed_fstat(unused_file_descriptor: int) -> SimpleNamespace:
        metadata = target.lstat()
        if mutation == "identity":
            return copy_metadata(metadata, inode=metadata.st_ino + 1)
        if mutation == "size":
            return copy_metadata(metadata, size=metadata.st_size + 1)
        if mutation == "mtime":
            return copy_metadata(metadata, mtime_ns=metadata.st_mtime_ns + 1)
        if mutation == "ctime":
            return copy_metadata(metadata, ctime_ns=metadata.st_ctime_ns + 1)
        if mutation == "birthtime":
            return copy_metadata(
                metadata,
                birthtime_ns=getattr(metadata, "st_birthtime_ns", 0) + 1,
            )
        if mutation == "kind":
            return copy_metadata(metadata, mode=stat.S_IFDIR | 0o700)
        return copy_metadata(
            metadata,
            file_attributes=(
                getattr(metadata, "st_file_attributes", 0) | stat.FILE_ATTRIBUTE_HIDDEN
            ),
        )

    def recording_read(file_descriptor: int, count: int) -> bytes:
        nonlocal read_calls
        read_calls += 1
        return real_read(file_descriptor, count)

    monkeypatch.setattr(text_files.os, "fstat", changed_fstat)
    monkeypatch.setattr(text_files.os, "read", recording_read)

    result = await call_read_text(policy, {"relative_path": "racing.txt"})

    assert_failure(
        result,
        "filesystem_read_unavailable",
        marker,
        "racing.txt",
        str(project_root),
    )
    assert read_calls == 0


@pytest.mark.parametrize(
    "mutation",
    ["identity", "size", "mtime", "ctime", "kind", "attributes"],
)
async def test_open_handle_change_during_read_discards_the_buffer_before_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    marker = "PRIVATE-HANDLE-RACE-CONTENT"
    target = project_root / "handle-racing.txt"
    target.write_text(marker, encoding="utf-8")
    real_read = os.read
    real_close = os.close
    fstat_calls = 0
    events: list[str] = []

    def changing_fstat(unused_file_descriptor: int) -> SimpleNamespace:
        nonlocal fstat_calls
        fstat_calls += 1
        events.append("fstat")
        metadata = target.lstat()
        if fstat_calls == 1:
            return copy_metadata(metadata)
        if mutation == "identity":
            return copy_metadata(metadata, inode=metadata.st_ino + 1)
        if mutation == "size":
            return copy_metadata(metadata, size=metadata.st_size + 1)
        if mutation == "mtime":
            return copy_metadata(metadata, mtime_ns=metadata.st_mtime_ns + 1)
        if mutation == "ctime":
            return copy_metadata(metadata, ctime_ns=metadata.st_ctime_ns + 1)
        if mutation == "kind":
            return copy_metadata(metadata, mode=stat.S_IFDIR | 0o700)
        return copy_metadata(
            metadata,
            file_attributes=(
                getattr(metadata, "st_file_attributes", 0) | stat.FILE_ATTRIBUTE_HIDDEN
            ),
        )

    def recording_read(file_descriptor: int, count: int) -> bytes:
        events.append("read")
        return real_read(file_descriptor, count)

    def recording_close(file_descriptor: int) -> None:
        events.append("close")
        real_close(file_descriptor)

    monkeypatch.setattr(text_files.os, "fstat", changing_fstat)
    monkeypatch.setattr(text_files.os, "read", recording_read)
    monkeypatch.setattr(text_files.os, "close", recording_close)

    result = await call_read_text(policy, {"relative_path": "handle-racing.txt"})

    assert_failure(
        result,
        "filesystem_read_unavailable",
        marker,
        "handle-racing.txt",
        str(project_root),
    )
    assert fstat_calls == 2
    assert events[0] == "fstat"
    assert "read" in events[1:-2]
    assert events[-2:] == ["fstat", "close"]


@pytest.mark.parametrize("mutation", ["identity", "size", "mtime", "ctime", "kind", "attributes"])
async def test_post_read_metadata_mismatch_discards_the_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    marker = "PRIVATE-POST-RACE-CONTENT"
    target = project_root / "racing.txt"
    target.write_text(marker, encoding="utf-8")
    real_lstat = Path.lstat
    target_calls = 0

    def changed_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        nonlocal target_calls
        metadata = real_lstat(path)
        if path != target:
            return metadata
        target_calls += 1
        if target_calls < 3:
            return metadata
        if mutation == "identity":
            return copy_metadata(metadata, inode=metadata.st_ino + 1)
        if mutation == "size":
            return copy_metadata(metadata, size=metadata.st_size + 1)
        if mutation == "mtime":
            return copy_metadata(metadata, mtime_ns=metadata.st_mtime_ns + 1)
        if mutation == "ctime":
            return copy_metadata(metadata, ctime_ns=metadata.st_ctime_ns + 1)
        if mutation == "kind":
            return copy_metadata(metadata, mode=stat.S_IFDIR | 0o700)
        return copy_metadata(
            metadata,
            file_attributes=(
                getattr(metadata, "st_file_attributes", 0) | stat.FILE_ATTRIBUTE_HIDDEN
            ),
        )

    monkeypatch.setattr(Path, "lstat", changed_lstat)

    result = await call_read_text(policy, {"relative_path": "racing.txt"})

    assert target_calls >= 3
    assert_failure(
        result,
        "filesystem_read_unavailable",
        marker,
        "racing.txt",
        str(project_root),
    )


async def test_post_read_canonical_target_change_discards_the_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    original = project_root / "original.txt"
    replacement = project_root / "replacement.txt"
    original.write_bytes(b"PRIVATE-ORIGINAL-CONTENT")
    replacement.write_bytes(b"PRIVATE-REPLACEMENT-CONTENT")
    real_resolve = ProjectPathPolicy.resolve
    resolve_calls = 0

    def changing_resolve(self: ProjectPathPolicy, relative_path: str) -> Path:
        nonlocal resolve_calls
        resolve_calls += 1
        if resolve_calls == 2:
            return replacement
        return real_resolve(self, relative_path)

    monkeypatch.setattr(ProjectPathPolicy, "resolve", changing_resolve)

    result = await call_read_text(policy, {"relative_path": "original.txt"})

    assert resolve_calls == 2
    assert_failure(
        result,
        "filesystem_read_unavailable",
        "PRIVATE-ORIGINAL-CONTENT",
        "PRIVATE-REPLACEMENT-CONTENT",
        str(project_root),
    )


@pytest.mark.parametrize("operation", ["open", "fstat", "read"])
async def test_fd_os_errors_return_sanitized_read_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    operation: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "private-input.txt"
    target.write_bytes(b"PRIVATE-FD-CONTENT")
    private_error = f"PRIVATE {operation} failure at {target}"

    def fail(*unused_args: object, **unused_kwargs: object) -> object:
        raise OSError(private_error)

    monkeypatch.setattr(text_files.os, operation, fail)

    with caplog.at_level("DEBUG"):
        result = await call_read_text(policy, {"relative_path": "private-input.txt"})

    assert_failure(
        result,
        "filesystem_read_unavailable",
        private_error,
        "private-input.txt",
        "PRIVATE-FD-CONTENT",
        str(project_root),
    )
    assert private_error not in caplog.text
    assert "private-input.txt" not in caplog.text
    assert str(project_root) not in caplog.text


@pytest.mark.parametrize(
    "magic",
    [
        b"\x7fELF",
        b"MZ",
        b"PK\x03\x04",
        b"\x89PNG\r\n\x1a\n",
        b"\xff\xd8\xff",
        b"%PDF-",
        b"\x1f\x8b",
        b"7z\xbc\xaf\x27\x1c",
    ],
)
async def test_known_binary_magic_is_rejected_before_text_decoding(
    tmp_path: Path,
    magic: bytes,
) -> None:
    project_root, policy = make_project(tmp_path)
    marker = b"PRIVATE-BINARY-MARKER"
    (project_root / "disguised.txt").write_bytes(magic + b"\xff" + marker)

    result = await call_read_text(policy, {"relative_path": "disguised.txt"})

    assert_failure(
        result,
        "filesystem_text_unsupported",
        marker.decode(),
        "disguised.txt",
        str(project_root),
    )


async def test_raw_nul_is_rejected_before_text_decoding(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    marker = "PRIVATE-NUL-MARKER"
    (project_root / "embedded.txt").write_bytes(b"safe\x00\xff" + marker.encode())

    result = await call_read_text(policy, {"relative_path": "embedded.txt"})

    assert_failure(
        result,
        "filesystem_text_unsupported",
        marker,
        "embedded.txt",
        str(project_root),
    )


async def test_utf8_bom_is_accepted_and_removed_once(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    content = "第一行\nsecond\n"
    (project_root / "bom.txt").write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))

    result = await call_read_text(policy, {"relative_path": "bom.txt"})

    assert result.success is True
    assert result.data is not None
    assert result.data["content"] == content
    assert result.data["chars_returned"] == len(content)
    assert result.data["utf8_bytes_returned"] == len(content.encode("utf-8"))


async def test_invalid_utf8_is_rejected_without_content(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    marker = b"PRIVATE-INVALID-UTF8"
    (project_root / "invalid.txt").write_bytes(b"ordinary\xff" + marker)

    result = await call_read_text(policy, {"relative_path": "invalid.txt"})

    assert_failure(
        result,
        "filesystem_text_unsupported",
        marker.decode(),
        "invalid.txt",
        str(project_root),
    )


@pytest.mark.parametrize("control", ["\x01", "\x0b", "\x1f", "\x7f", "\x80", "\x85", "\x9f"])
async def test_decoded_c0_and_c1_controls_are_rejected(
    tmp_path: Path,
    control: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    marker = "PRIVATE-CONTROL-MARKER"
    (project_root / "control.txt").write_text(
        f"safe{control}{marker}",
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(policy, {"relative_path": "control.txt"})

    assert_failure(
        result,
        "filesystem_text_unsupported",
        marker,
        "control.txt",
        str(project_root),
    )


async def test_tab_cr_lf_and_crlf_are_preserved_verbatim(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    content = "one\tvalue\r\ntwo\rthree\nfour"
    (project_root / "newlines.txt").write_bytes(content.encode("utf-8"))

    result = await call_read_text(policy, {"relative_path": "newlines.txt"})

    assert result.success is True
    assert result.data is not None
    assert result.data["content"] == content
    assert result.data["total_lines"] == 4
    assert result.data["line_end"] == 4


async def test_empty_file_has_exact_success_payload(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    (project_root / "empty.txt").write_bytes(b"")

    result = await call_read_text(policy, {"relative_path": "empty.txt"})

    assert result.success is True
    assert result.data == {
        "scope": "project",
        "relative_path": "empty.txt",
        "encoding": "utf-8",
        "content_trust": "untrusted_data",
        "instruction_authority": "none",
        "line_start": None,
        "line_end": None,
        "total_lines": 0,
        "lines_returned": 0,
        "chars_returned": 0,
        "utf8_bytes_returned": 0,
        "content": "",
        "truncated": False,
        "truncation_reason": None,
        "next_start_line": None,
    }
    assert result.error is None
    assert result.metadata == {}


async def test_source_size_exact_cap_is_allowed(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    payload = b"x\n" * (text_files.MAX_SOURCE_BYTES // 2)
    assert len(payload) == text_files.MAX_SOURCE_BYTES
    (project_root / "exact.txt").write_bytes(payload)

    result = await call_read_text(policy, {"relative_path": "exact.txt"})

    assert result.success is True
    assert result.data is not None
    assert result.data["lines_returned"] == text_files.MAX_RETURN_LINES
    assert result.data["truncated"] is True
    assert result.data["truncation_reason"] == "line_limit"


async def test_source_size_over_cap_is_rejected_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    marker = "PRIVATE-OVERSIZE-MARKER"
    (project_root / "oversize.txt").write_bytes(
        marker.encode() + b"x" * (text_files.MAX_SOURCE_BYTES + 1 - len(marker))
    )

    def forbidden_open(*unused_args: object, **unused_kwargs: object) -> int:
        raise AssertionError("an oversized source must be rejected before open")

    monkeypatch.setattr(text_files.os, "open", forbidden_open)

    result = await call_read_text(policy, {"relative_path": "oversize.txt"})

    assert_failure(
        result,
        "filesystem_text_too_large",
        marker,
        "oversize.txt",
        str(project_root),
    )


async def test_growth_during_read_is_a_sanitized_race_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "growing.txt"
    target.write_bytes(b"x" * text_files.MAX_SOURCE_BYTES)
    real_read = os.read
    read_calls = 0

    def growing_read(file_descriptor: int, count: int) -> bytes:
        nonlocal read_calls
        read_calls += 1
        if read_calls == 1:
            with target.open("ab") as writer:
                writer.write(b"y")
        return real_read(file_descriptor, count)

    monkeypatch.setattr(text_files.os, "read", growing_read)

    result = await call_read_text(policy, {"relative_path": "growing.txt"})

    assert read_calls >= 1
    assert_failure(
        result,
        "filesystem_read_unavailable",
        "growing.txt",
        str(project_root),
    )


async def test_pagination_returns_complete_lines_and_exact_next_page(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    content = "one\n二\nthree"
    (project_root / "pages.txt").write_text(content, encoding="utf-8", newline="")

    result = await call_read_text(
        policy,
        {"relative_path": "pages.txt", "start_line": 2, "max_lines": 1},
    )

    assert result.success is True
    assert result.data == {
        "scope": "project",
        "relative_path": "pages.txt",
        "encoding": "utf-8",
        "content_trust": "untrusted_data",
        "instruction_authority": "none",
        "line_start": 2,
        "line_end": 2,
        "total_lines": 3,
        "lines_returned": 1,
        "chars_returned": 2,
        "utf8_bytes_returned": 4,
        "content": "二\n",
        "truncated": True,
        "truncation_reason": "line_limit",
        "next_start_line": 3,
    }
    assert result.error is None
    assert result.metadata == {}


async def test_start_beyond_eof_is_successful_empty_page(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    (project_root / "short.txt").write_bytes(b"one\ntwo\n")

    result = await call_read_text(
        policy,
        {"relative_path": "short.txt", "start_line": 5},
    )

    assert result.success is True
    assert result.data is not None
    assert result.data["line_start"] is None
    assert result.data["line_end"] is None
    assert result.data["total_lines"] == 2
    assert result.data["lines_returned"] == 0
    assert result.data["content"] == ""
    assert result.data["truncated"] is False
    assert result.data["truncation_reason"] is None
    assert result.data["next_start_line"] is None


async def test_character_limit_keeps_only_complete_lines(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    line = "a" * 9_999 + "\n"
    content = line + line + "tail\n"
    (project_root / "chars.txt").write_text(content, encoding="utf-8", newline="")

    result = await call_read_text(policy, {"relative_path": "chars.txt"})

    assert result.success is True
    assert result.data is not None
    assert result.data["content"] == line + line
    assert result.data["chars_returned"] == text_files.MAX_RETURN_CHARS
    assert result.data["lines_returned"] == 2
    assert result.data["truncated"] is True
    assert result.data["truncation_reason"] == "content_limit"
    assert result.data["next_start_line"] == 3


async def test_utf8_byte_limit_keeps_only_complete_multibyte_lines(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    line = "🙂" * 8_000 + "\n"
    content = line + line + "🙂" * 400 + "\n"
    (project_root / "bytes.txt").write_text(content, encoding="utf-8", newline="")

    result = await call_read_text(policy, {"relative_path": "bytes.txt"})

    assert result.success is True
    assert result.data is not None
    assert result.data["content"] == line + line
    assert result.data["chars_returned"] == len(line + line)
    assert result.data["utf8_bytes_returned"] == len((line + line).encode("utf-8"))
    assert result.data["utf8_bytes_returned"] < text_files.MAX_RETURN_UTF8_BYTES
    assert result.data["truncated"] is True
    assert result.data["truncation_reason"] == "content_limit"
    assert result.data["next_start_line"] == 3


@pytest.mark.parametrize(
    "line",
    [
        "a" * (text_files.MAX_RETURN_CHARS + 1),
        "🙂" * (text_files.MAX_RETURN_UTF8_BYTES // 4) + "a",
    ],
    ids=["character_limit", "utf8_byte_limit"],
)
async def test_first_selected_line_over_a_content_limit_fails_entire_read(
    tmp_path: Path,
    line: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    marker = "PRIVATE-LONG-LINE-MARKER"
    (project_root / "long.txt").write_text(
        f"prefix\n{line}{marker}\n",
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(
        policy,
        {"relative_path": "long.txt", "start_line": 2},
    )

    assert_failure(
        result,
        "filesystem_text_too_large",
        marker,
        "long.txt",
        str(project_root),
    )


async def test_exact_utf8_byte_output_boundary_is_allowed(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    content = "🙂" * 16_383 + "abcd"
    assert len(content) < text_files.MAX_RETURN_CHARS
    assert len(content.encode("utf-8")) == text_files.MAX_RETURN_UTF8_BYTES
    (project_root / "exact-bytes.txt").write_text(content, encoding="utf-8", newline="")

    result = await call_read_text(policy, {"relative_path": "exact-bytes.txt"})

    assert result.success is True
    assert result.data is not None
    assert result.data["content"] == content
    assert result.data["utf8_bytes_returned"] == text_files.MAX_RETURN_UTF8_BYTES
    assert result.data["truncated"] is False


@pytest.mark.parametrize(
    "kind",
    [
        "pem_private_key",
        "pgp_private_key",
        "authorization_bearer",
        "authorization_basic",
        "authorization_json_bearer",
        "aws_access_key",
        "aws_secret_key",
        "github_token",
        "sk_token",
        "jwt",
        "api_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "password",
        "typed_password",
    ],
)
async def test_high_confidence_secret_categories_reject_the_entire_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    kind: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    canary = secret_canary(kind)
    (project_root / "sensitive.txt").write_text(
        f"ordinary before\n{canary}\nordinary after\n",
        encoding="utf-8",
        newline="",
    )

    with caplog.at_level("DEBUG"):
        result = await call_read_text(policy, {"relative_path": "sensitive.txt"})

    assert_failure(
        result,
        "filesystem_sensitive_content",
        canary,
        "sensitive.txt",
        str(project_root),
    )
    assert canary not in caplog.text
    assert "sensitive.txt" not in caplog.text
    assert str(project_root) not in caplog.text


@pytest.mark.parametrize(
    "content",
    [
        'api_key = ""',
        'password = "************"',
        'password = "***REDACTED***"',
        'access_token = "${ACCESS_TOKEN}"',
        "access_token = ACCESS_TOKEN",
        'refresh_token = os.getenv("REFRESH_TOKEN")',
        "client_secret = process.env.CLIENT_SECRET",
        'api_key = "test-secret-value"',
        'api_key = "fake-secret-value"',
        'api_key = "mock-secret-value"',
        'api_key = "example-secret-value"',
        'api_key = "placeholder-secret-value"',
        'api_key = "replace-this-value"',
        'api_key = "dummy-secret-value"',
        'api_key = "sample-secret-value"',
        'api_key = "test"',
        'api_key = "fake"',
        'api_key = "mock"',
        'api_key = "example"',
        'api_key = "placeholder"',
        'api_key = "replace"',
        'api_key = "dummy"',
        'api_key = "sample"',
        'password == "otherwise-literal-value"',
        "password: str",
        "password: string",
        "password: required",
        "api_key = settings.api_key",
        "password = config.password",
        '"password": {"type": "string"}',
        "Authorization: Bearer ${ACCESS_TOKEN}",
        "sk-example_placeholder_value_123456789",
    ],
)
async def test_placeholders_env_references_and_comparisons_are_not_secrets(
    tmp_path: Path,
    content: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    (project_root / "template.txt").write_text(content, encoding="utf-8", newline="")

    result = await call_read_text(policy, {"relative_path": "template.txt"})

    assert result.success is True
    assert result.data is not None
    assert result.data["content"] == content


@pytest.mark.parametrize(
    ("key", "value_parts"),
    [
        ("api_key", ("Con", "testWinner-Prod-2026!")),
        ("password", ("Prod", "(Secret)2026!")),
        ("client_secret", ("$", "uperSecret2026!")),
        ("access_token", ("LatestCon", "testWinner2026!")),
    ],
)
async def test_real_literals_are_not_mistaken_for_placeholders(
    tmp_path: Path,
    key: str,
    value_parts: tuple[str, ...],
) -> None:
    project_root, policy = make_project(tmp_path)
    value = "".join(value_parts)
    content = f'{key} = "{value}"'
    (project_root / "real-secret.txt").write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(policy, {"relative_path": "real-secret.txt"})

    assert_failure(
        result,
        "filesystem_sensitive_content",
        value,
        content,
        "real-secret.txt",
        str(project_root),
    )


@pytest.mark.parametrize(
    "key",
    [
        "DEEPSEEK_API_KEY",
        "JARVIS_PACKYCODE_API_KEY",
        "DB_PASSWORD",
        "MY_ACCESS_TOKEN",
        "_API_KEY",
    ],
)
async def test_namespaced_sensitive_identifiers_reject_quoted_literals(
    tmp_path: Path,
    key: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    value_prefix = "Production"
    value = value_prefix + "Credential2026!"
    content = f'{key} = "{value}"'
    (project_root / "namespaced-secret.txt").write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(policy, {"relative_path": "namespaced-secret.txt"})

    assert_failure(
        result,
        "filesystem_sensitive_content",
        value,
        content,
        "namespaced-secret.txt",
        str(project_root),
    )


@pytest.mark.parametrize(
    "key",
    [
        "DEEPSEEK_API_KEY",
        "JARVIS_PACKYCODE_API_KEY",
        "DB_PASSWORD",
        "MY_ACCESS_TOKEN",
        "_API_KEY",
    ],
)
@pytest.mark.parametrize(
    "raw_value",
    ['""', '"placeholder"', '"${SECRET_REFERENCE}"'],
)
async def test_namespaced_sensitive_identifiers_allow_safe_values(
    tmp_path: Path,
    key: str,
    raw_value: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    content = f"{key} = {raw_value}"
    (project_root / "namespaced-template.txt").write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(policy, {"relative_path": "namespaced-template.txt"})

    assert result.success is True
    assert result.data is not None
    assert result.data["content"] == content


@pytest.mark.parametrize(
    "key",
    [
        "serviceApiKey",
        "dbPassword",
        "apiKey",
        "accessToken",
        "refreshToken",
        "clientSecret",
        "awsSecretAccessKey",
        "APIKey",
    ],
)
async def test_camel_case_sensitive_suffixes_reject_quoted_literals(
    tmp_path: Path,
    key: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    value_prefix = "Production"
    value = value_prefix + "CamelCredential2026!"
    content = f'{key} = "{value}"'
    (project_root / "camel-secret.txt").write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(policy, {"relative_path": "camel-secret.txt"})

    assert_failure(
        result,
        "filesystem_sensitive_content",
        value,
        content,
        "camel-secret.txt",
        str(project_root),
    )


@pytest.mark.parametrize(
    "identifier",
    ["serviceApiKeyboard", "dbPasswordless", "_API_KEY_SUFFIX"],
)
async def test_camel_case_sensitive_words_inside_identifiers_are_not_keys(
    tmp_path: Path,
    identifier: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    value_prefix = "Production"
    value = value_prefix + "OrdinaryValue2026!"
    content = f'{identifier} = "{value}"'
    (project_root / "ordinary-identifier.txt").write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(
        policy,
        {"relative_path": "ordinary-identifier.txt"},
    )

    assert result.success is True
    assert result.data is not None
    assert result.data["content"] == content


@pytest.mark.parametrize(
    "content",
    [
        "password = password",
        "password = suppliedPassword",
        '"password": password',
        'password = config["password"]',
        "password: Optional[str]",
        "password: SecretStr",
        "password: list[str]",
    ],
)
async def test_password_references_and_type_annotations_are_not_literals(
    tmp_path: Path,
    content: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    (project_root / "password-reference.txt").write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(policy, {"relative_path": "password-reference.txt"})

    assert result.success is True
    assert result.data is not None
    assert result.data["content"] == content


@pytest.mark.parametrize(
    "template",
    [
        'password: SecretStr = "{value}"',
        '"password": "{value}"',
    ],
)
async def test_typed_assignment_and_quoted_mapping_reject_real_literals(
    tmp_path: Path,
    template: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    value_prefix = "Production"
    value = value_prefix + "Password2026!"
    content = template.format(value=value)
    (project_root / "password-literal.txt").write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(policy, {"relative_path": "password-literal.txt"})

    assert_failure(
        result,
        "filesystem_sensitive_content",
        value,
        content,
        "password-literal.txt",
        str(project_root),
    )


@pytest.mark.parametrize(
    "annotation",
    ["Annotated[str, Secret()]", "Union[str, bytes]", "dict[str, str]"],
)
async def test_complex_type_annotations_are_not_literals(
    tmp_path: Path,
    annotation: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    content = f"password: {annotation}"
    (project_root / "complex-annotation.txt").write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(policy, {"relative_path": "complex-annotation.txt"})

    assert result.success is True
    assert result.data is not None
    assert result.data["content"] == content


@pytest.mark.parametrize(
    "annotation",
    ["Annotated[str, Secret()]", "Union[str, bytes]", "dict[str, str]"],
)
async def test_complex_typed_assignments_reject_quoted_literals(
    tmp_path: Path,
    annotation: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    value_prefix = "Production"
    value = value_prefix + "TypedPassword2026!"
    content = f'password: {annotation} = "{value}"'
    (project_root / "complex-typed-secret.txt").write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(
        policy,
        {"relative_path": "complex-typed-secret.txt"},
    )

    assert_failure(
        result,
        "filesystem_sensitive_content",
        value,
        content,
        "complex-typed-secret.txt",
        str(project_root),
    )


@pytest.mark.parametrize(
    ("key", "wrapper"),
    [
        ("password", "SecretStr"),
        ("api_key", "str"),
        ("password", "SecretStr "),
        ("password", "pydantic.SecretStr"),
    ],
)
async def test_string_wrapped_real_literals_are_rejected(
    tmp_path: Path,
    key: str,
    wrapper: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    value_prefix = "Production"
    value = value_prefix + "WrappedCredential2026"
    content = f'{key} = {wrapper}("{value}")'
    (project_root / "wrapped-secret.txt").write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(policy, {"relative_path": "wrapped-secret.txt"})

    assert_failure(
        result,
        "filesystem_sensitive_content",
        value,
        content,
        "wrapped-secret.txt",
        str(project_root),
    )


@pytest.mark.parametrize(
    "content",
    [
        'password = os.getenv("KEY")',
        'password = SecretStr("${ENV}")',
        'password = SecretStr ("${ENV}")',
        'password = pydantic.SecretStr(os.getenv("PASSWORD"))',
        "password = get_password()",
        'password = get_password("db")',
        'password = get_password("db", fallback=config.get("password"))',
        'password = config.get("password")',
        "api_key = vault.read()",
        'api_key = vault.read("prod")',
        'password = SecretStr(os.getenv("PASSWORD"))',
    ],
)
async def test_reference_and_placeholder_calls_are_allowed(
    tmp_path: Path,
    content: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    (project_root / "wrapped-template.txt").write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(policy, {"relative_path": "wrapped-template.txt"})

    assert result.success is True
    assert result.data is not None
    assert result.data["content"] == content


@pytest.mark.parametrize(
    "placeholder",
    ["YOUR_TOKEN", "{token}", "ACCESS_TOKEN", "TOKEN"],
)
async def test_authorization_bearer_placeholders_are_allowed(
    tmp_path: Path,
    placeholder: str,
) -> None:
    project_root, policy = make_project(tmp_path)
    content = f"Authorization: Bearer {placeholder}"
    (project_root / "authorization-template.txt").write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(
        policy,
        {"relative_path": "authorization-template.txt"},
    )

    assert result.success is True
    assert result.data is not None
    assert result.data["content"] == content


async def test_authorization_bearer_real_literal_is_rejected(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    value_prefix = "Production"
    value = value_prefix + "BearerCredential2026"
    content = f"Authorization: Bearer {value}"
    (project_root / "authorization-secret.txt").write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(
        policy,
        {"relative_path": "authorization-secret.txt"},
    )

    assert_failure(
        result,
        "filesystem_sensitive_content",
        value,
        content,
        "authorization-secret.txt",
        str(project_root),
    )


async def test_secret_scan_covers_unselected_lines_in_the_whole_file(tmp_path: Path) -> None:
    project_root, policy = make_project(tmp_path)
    canary = secret_canary("sk_token")
    (project_root / "whole-file.txt").write_text(
        f"safe first\nsafe second\n{canary}\n",
        encoding="utf-8",
        newline="",
    )

    result = await call_read_text(
        policy,
        {"relative_path": "whole-file.txt", "start_line": 1, "max_lines": 1},
    )

    assert_failure(
        result,
        "filesystem_sensitive_content",
        canary,
        "whole-file.txt",
        str(project_root),
    )


async def test_registry_timeout_remains_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, policy = make_project(tmp_path)
    private_marker = "PRIVATE-SLOW-EXECUTOR-MARKER"

    async def slow_execute(self: object, validated: object) -> ToolResult:
        await asyncio.sleep(1)
        raise AssertionError(private_marker)

    monkeypatch.setattr(text_files, "READ_TEXT_TOOL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(text_files.ReadTextExecutor, "execute", slow_execute)

    with caplog.at_level("WARNING"):
        result = await call_read_text(policy, {"relative_path": "source.txt"})

    assert result.success is False
    assert result.data is None
    assert result.error is not None
    assert result.error.code == "tool_timeout"
    assert result.error.retryable is True
    assert result.metadata == {}
    assert private_marker not in repr(result)
    assert private_marker not in caplog.text
    assert "source.txt" not in caplog.text


async def test_external_cancellation_propagates_without_a_tool_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, policy = make_project(tmp_path)
    started = asyncio.Event()

    async def blocked_execute(self: object, validated: object) -> ToolResult:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(text_files.ReadTextExecutor, "execute", blocked_execute)
    task = asyncio.create_task(call_read_text(policy, {"relative_path": "source.txt"}))
    await asyncio.wait_for(started.wait(), timeout=1)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_pre_lstat_os_error_is_sanitized_path_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "private-pre.txt"
    target.write_bytes(b"PRIVATE-PRE-CONTENT")
    private_error = f"PRIVATE pre-lstat failure at {target}"

    def resolved_target(self: ProjectPathPolicy, relative_path: str) -> Path:
        assert self is policy
        assert relative_path == "private-pre.txt"
        return target

    def failing_lstat(path: Path) -> os.stat_result:
        assert path == target
        raise OSError(private_error)

    monkeypatch.setattr(ProjectPathPolicy, "resolve", resolved_target)
    monkeypatch.setattr(Path, "lstat", failing_lstat)

    with caplog.at_level("DEBUG"):
        result = await call_read_text(policy, {"relative_path": "private-pre.txt"})

    assert_failure(
        result,
        "filesystem_path_unavailable",
        private_error,
        "private-pre.txt",
        "PRIVATE-PRE-CONTENT",
        str(project_root),
    )
    assert private_error not in caplog.text


async def test_close_os_error_discards_the_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    target = project_root / "private-close.txt"
    target.write_bytes(b"PRIVATE-CLOSE-CONTENT")
    private_error = f"PRIVATE close failure at {target}"
    real_close = os.close

    def failing_close(file_descriptor: int) -> None:
        real_close(file_descriptor)
        raise OSError(private_error)

    monkeypatch.setattr(text_files.os, "close", failing_close)

    result = await call_read_text(policy, {"relative_path": "private-close.txt"})

    assert_failure(
        result,
        "filesystem_read_unavailable",
        private_error,
        "private-close.txt",
        "PRIVATE-CLOSE-CONTENT",
        str(project_root),
    )


async def test_post_resolve_os_error_discards_the_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    marker = "PRIVATE-POST-ERROR-CONTENT"
    (project_root / "private-post.txt").write_text(marker, encoding="utf-8")
    real_resolve = ProjectPathPolicy.resolve
    resolve_calls = 0

    def failing_second_resolve(self: ProjectPathPolicy, relative_path: str) -> Path:
        nonlocal resolve_calls
        resolve_calls += 1
        if resolve_calls == 2:
            raise ProjectPathPolicyError("path_unavailable")
        return real_resolve(self, relative_path)

    monkeypatch.setattr(ProjectPathPolicy, "resolve", failing_second_resolve)

    result = await call_read_text(policy, {"relative_path": "private-post.txt"})

    assert resolve_calls == 2
    assert_failure(
        result,
        "filesystem_read_unavailable",
        marker,
        "private-post.txt",
        str(project_root),
    )


async def test_unexpected_early_eof_discards_the_partial_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, policy = make_project(tmp_path)
    marker = "PRIVATE-CONTENT-AFTER-PARTIAL"
    (project_root / "partial.txt").write_text(
        f"prefix-{marker}",
        encoding="utf-8",
        newline="",
    )
    chunks = iter([b"prefix-", b""])

    def partial_read(unused_file_descriptor: int, unused_count: int) -> bytes:
        return next(chunks)

    monkeypatch.setattr(text_files.os, "read", partial_read)

    result = await call_read_text(policy, {"relative_path": "partial.txt"})

    assert_failure(
        result,
        "filesystem_read_unavailable",
        marker,
        "partial.txt",
        str(project_root),
    )
