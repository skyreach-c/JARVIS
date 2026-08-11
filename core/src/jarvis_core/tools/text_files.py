"""Bounded, project-scoped text file reading Tool."""

from __future__ import annotations

import asyncio
import codecs
import os
import re
import stat
from pathlib import Path

from pydantic import Field

from jarvis_core.tools.contracts import (
    ToolArguments,
    ToolDefinition,
    ToolError,
    ToolResult,
)
from jarvis_core.tools.project_files import ProjectPathPolicy, ProjectPathPolicyError
from jarvis_core.tools.registry import ToolRegistry

READ_TEXT_TOOL_NAME = "filesystem.read_text"
READ_TEXT_TOOL_TIMEOUT_SECONDS = 2.0

MAX_SOURCE_BYTES = 262_144
MAX_RETURN_LINES = 200
MAX_RETURN_CHARS = 20_000
MAX_RETURN_UTF8_BYTES = 65_536

_ALLOWED_SUFFIXES = frozenset(
    {
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
    }
)
_SENSITIVE_DIRECTORY_COMPONENTS = frozenset({".ssh", ".aws", ".azure", ".kube", ".docker"})
_SENSITIVE_BASENAMES = frozenset(
    {
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
    }
)
_SENSITIVE_BASENAME_PATTERNS = (
    re.compile(r"client_secret_[a-z0-9][a-z0-9_-]*\.json", re.IGNORECASE),
    re.compile(r"service-account-key_[a-z0-9][a-z0-9_-]*\.json", re.IGNORECASE),
    re.compile(r"service_account_key_[a-z0-9][a-z0-9_-]*\.json", re.IGNORECASE),
)
_FILTERED_ATTRIBUTES = (
    stat.FILE_ATTRIBUTE_HIDDEN
    | stat.FILE_ATTRIBUTE_SYSTEM
    | stat.FILE_ATTRIBUTE_OFFLINE
    | stat.FILE_ATTRIBUTE_REPARSE_POINT
)
_ERROR_MESSAGES = {
    "tool_invalid_arguments": "The text file request arguments are invalid.",
    "filesystem_path_unavailable": "The requested project text file is unavailable.",
    "filesystem_text_unsupported": "The requested project file is not supported text.",
    "filesystem_text_too_large": "The requested project text exceeds the fixed size limit.",
    "filesystem_sensitive_content": "The requested project text is protected.",
    "filesystem_read_unavailable": "The project text read could not be verified.",
}
_BINARY_MAGIC_PREFIXES = (
    b"\x7fELF",
    b"MZ",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"%PDF-",
    b"\x1f\x8b",
    b"Rar!\x1a\x07",
    b"7z\xbc\xaf\x27\x1c",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    b"SQLite format 3\x00",
    b"\x00asm",
    b"\xca\xfe\xba\xbe",
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
)
_PRIVATE_KEY_HEADER_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----",
    re.IGNORECASE,
)
_AUTHORIZATION_PATTERN = re.compile(
    r"\bauthorization\b[\"']?\s*[:=]\s*[\"']?(?:bearer|basic)\s+"
    r"(?P<value>[A-Za-z0-9._~+/=${}<>%:-]{4,})",
    re.IGNORECASE,
)
_AUTHORIZATION_BARE_PLACEHOLDER_PATTERN = re.compile(
    r"(?:[A-Z][A-Z0-9]*_)*TOKEN"
)
_TOKEN_PATTERNS = (
    re.compile(
        r"(?<![A-Z0-9])(?P<value>(?:AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA|ASCA)"
        r"[A-Z0-9]{16})(?![A-Z0-9])"
    ),
    re.compile(
        r"(?<![A-Za-z0-9])(?P<value>(?:gh[pousr]_[A-Za-z0-9]{30,}|"
        r"github_pat_[A-Za-z0-9_]{20,}))(?![A-Za-z0-9_])"
    ),
    re.compile(
        r"(?<![A-Za-z0-9])(?P<value>sk-[A-Za-z0-9_-]{20,})(?![A-Za-z0-9_-])"
    ),
    re.compile(
        r"(?<![A-Za-z0-9_-])(?P<value>eyJ[A-Za-z0-9_-]{7,}\."
        r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})(?![A-Za-z0-9_-])"
    ),
)
_ASSIGNMENT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])[\"']?(?P<key>[A-Za-z_][A-Za-z0-9_-]*)[\"']?\s*(?:"
    r":\s*[A-Za-z_][A-Za-z0-9_.\[\], |()]*\s*=\s*"
    r"(?P<typed_value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;#\r\n]+)|"
    r"(?::|=(?!=))\s*"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;#\r\n]+))",
    re.IGNORECASE,
)
_SENSITIVE_SNAKE_CASE_SUFFIXES = (
    "api_key",
    "access_token",
    "refresh_token",
    "client_secret",
    "password",
    "aws_secret_access_key",
)
_SENSITIVE_CAMEL_CASE_SUFFIXES = (
    "ApiKey",
    "AccessToken",
    "RefreshToken",
    "ClientSecret",
    "Password",
    "AwsSecretAccessKey",
)
_ASSIGNMENT_DESCRIPTOR_WORDS = frozenset(
    {
        "any",
        "array",
        "bool",
        "boolean",
        "int",
        "number",
        "object",
        "required",
        "str",
        "string",
        "unknown",
    }
)
_REFERENCE_EXPRESSION_PATTERN = re.compile(
    r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*"
    r"(?:\[(?:[A-Za-z_$][A-Za-z0-9_$]*|\"[^\"\r\n]*\"|'[^'\r\n]*')\])*"
)
_STRING_WRAPPER_CALL_PATTERN = re.compile(
    r"(?:[A-Za-z_$][A-Za-z0-9_$]*\.)*(?:SecretStr|str)[ \t]*\(\s*(?P<value>"
    r"\"[^\"\r\n]*\"|'[^'\r\n]*'|"
    r"(?:os\.)?getenv\(\s*[\"'][A-Za-z_][A-Za-z0-9_]*[\"']\s*\)"
    r")\s*\)"
)
_REFERENCE_CALL_HEAD_PATTERN = re.compile(
    r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*[ \t]*\("
)
_TYPE_ANNOTATION_LINE_PATTERN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*\s*:\s*"
    r"[A-Za-z_][A-Za-z0-9_.]*(?:\[[A-Za-z0-9_., |()\[\]]+\])?\s*"
)
_PLACEHOLDER_WORDS = frozenset(
    {
        "test",
        "fake",
        "mock",
        "example",
        "placeholder",
        "replace",
        "dummy",
        "sample",
    }
)
_STRUCTURED_PLACEHOLDER_PATTERN = re.compile(
    r"(?:"
    r"(?:test|fake|mock|example|placeholder|dummy|sample)"
    r"(?:[-_ ]secret)?[-_ ]value|"
    r"replace[-_ ]this[-_ ]value|"
    r"sk-(?:test|fake|mock|example|placeholder|replace|dummy|sample)"
    r"(?:[_-](?:test|fake|mock|example|placeholder|replace|dummy|sample|"
    r"secret|this|value|[0-9]+))*"
    r")",
    re.IGNORECASE,
)
_ENV_REFERENCE_PATTERNS = (
    re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}"),
    re.compile(r"\$[A-Z_][A-Z0-9_]*"),
    re.compile(r"%[A-Za-z_][A-Za-z0-9_]*%"),
    re.compile(r"\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}"),
    re.compile(
        r"(?:os\.)?getenv\(\s*[\"'][A-Za-z_][A-Za-z0-9_]*[\"']\s*\)",
        re.IGNORECASE,
    ),
    re.compile(
        r"os\.environ\[\s*[\"'][A-Za-z_][A-Za-z0-9_]*[\"']\s*\]",
        re.IGNORECASE,
    ),
    re.compile(r"process\.env\.[A-Za-z_][A-Za-z0-9_]*", re.IGNORECASE),
    re.compile(
        r"env\(\s*[\"'][A-Za-z_][A-Za-z0-9_]*[\"']\s*\)",
        re.IGNORECASE,
    ),
    re.compile(r"<[A-Z_][A-Z0-9_]*>"),
    re.compile(r"YOUR_[A-Z][A-Z0-9_]*"),
    re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}"),
)


class ReadTextArguments(ToolArguments):
    relative_path: str = Field(min_length=1)
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=MAX_RETURN_LINES, ge=1, le=MAX_RETURN_LINES)


class ReadTextExecutor:
    __slots__ = ("_path_policy",)

    def __init__(self, *, path_policy: ProjectPathPolicy) -> None:
        self._path_policy = path_policy

    async def execute(self, arguments: ToolArguments) -> ToolResult:
        if not isinstance(arguments, ReadTextArguments):
            return _failure("tool_invalid_arguments")
        return await asyncio.to_thread(self._execute_sync, arguments)

    def _execute_sync(self, arguments: ReadTextArguments) -> ToolResult:
        try:
            target = self._path_policy.resolve(arguments.relative_path)
        except ProjectPathPolicyError as error:
            code = (
                "tool_invalid_arguments"
                if error.code == "invalid_path"
                else "filesystem_path_unavailable"
            )
            return _failure(code)

        canonical_label = _canonical_label(
            project_root=self._path_policy.project_root,
            target=target,
        )
        if canonical_label is None:
            return _failure("filesystem_path_unavailable")

        path_rule_failure = _path_rule_failure(target=target, canonical_label=canonical_label)
        if path_rule_failure is not None:
            return _failure(path_rule_failure)

        try:
            before_metadata = target.lstat()
        except OSError:
            return _failure("filesystem_path_unavailable")
        if not _is_available_regular_file(before_metadata):
            return _failure("filesystem_path_unavailable")
        if before_metadata.st_size > MAX_SOURCE_BYTES:
            return _failure("filesystem_text_too_large")

        try:
            handle_read = _read_from_binary_handle(target, before_metadata)
        except OSError:
            return _failure("filesystem_read_unavailable")
        if handle_read is None:
            return _failure("filesystem_read_unavailable")
        raw, open_metadata = handle_read

        try:
            target_after = self._path_policy.resolve(arguments.relative_path)
            after_metadata = target_after.lstat()
        except ProjectPathPolicyError, OSError:
            return _failure("filesystem_read_unavailable")
        canonical_label_after = _canonical_label(
            project_root=self._path_policy.project_root,
            target=target_after,
        )
        if (
            target_after != target
            or canonical_label_after != canonical_label
            or not _is_available_regular_file(open_metadata)
            or not _is_available_regular_file(after_metadata)
            or not _same_open_metadata(before_metadata, open_metadata)
            or _metadata_fingerprint(after_metadata) != _metadata_fingerprint(before_metadata)
        ):
            return _failure("filesystem_read_unavailable")

        if len(raw) != before_metadata.st_size:
            return _failure("filesystem_read_unavailable")
        if len(raw) > MAX_SOURCE_BYTES:
            return _failure("filesystem_text_too_large")
        if any(raw.startswith(magic) for magic in _BINARY_MAGIC_PREFIXES) or b"\x00" in raw:
            return _failure("filesystem_text_unsupported")

        encoded_text = raw.removeprefix(codecs.BOM_UTF8)
        try:
            content = encoded_text.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return _failure("filesystem_text_unsupported")
        if _has_unsupported_control_character(content):
            return _failure("filesystem_text_unsupported")
        if _contains_high_confidence_secret(content):
            return _failure("filesystem_sensitive_content")

        lines = content.splitlines(keepends=True)
        start_index = arguments.start_line - 1
        selected: list[str] = []
        returned_chars = 0
        returned_utf8_bytes = 0
        truncation_reason: str | None = None
        for line in lines[start_index:]:
            if len(selected) >= arguments.max_lines:
                truncation_reason = "line_limit"
                break

            line_chars = len(line)
            line_utf8_bytes = len(line.encode("utf-8"))
            if (
                returned_chars + line_chars > MAX_RETURN_CHARS
                or returned_utf8_bytes + line_utf8_bytes > MAX_RETURN_UTF8_BYTES
            ):
                if not selected:
                    return _failure("filesystem_text_too_large")
                truncation_reason = "content_limit"
                break

            selected.append(line)
            returned_chars += line_chars
            returned_utf8_bytes += line_utf8_bytes

        returned_content = "".join(selected)
        truncated = start_index + len(selected) < len(lines)
        if not truncated:
            truncation_reason = None
        line_start = arguments.start_line if selected else None
        line_end = arguments.start_line + len(selected) - 1 if selected else None
        return ToolResult(
            success=True,
            data={
                "scope": "project",
                "relative_path": canonical_label,
                "encoding": "utf-8",
                "content_trust": "untrusted_data",
                "instruction_authority": "none",
                "line_start": line_start,
                "line_end": line_end,
                "total_lines": len(lines),
                "lines_returned": len(selected),
                "chars_returned": returned_chars,
                "utf8_bytes_returned": returned_utf8_bytes,
                "content": returned_content,
                "truncated": truncated,
                "truncation_reason": truncation_reason,
                "next_start_line": (
                    line_end + 1 if truncated and line_end is not None else None
                ),
            },
            error=None,
            metadata={},
        )


def register_read_text_tool(
    registry: ToolRegistry,
    *,
    path_policy: ProjectPathPolicy,
) -> ToolDefinition:
    return registry.register(
        name=READ_TEXT_TOOL_NAME,
        description="Read bounded UTF-8 text from one project-relative file.",
        arguments_model=ReadTextArguments,
        executor=ReadTextExecutor(path_policy=path_policy),
        risk_level="read_only",
        timeout_seconds=READ_TEXT_TOOL_TIMEOUT_SECONDS,
    )


def _canonical_label(*, project_root: Path, target: Path) -> str | None:
    try:
        relative = target.relative_to(project_root)
    except ValueError:
        return None
    return relative.as_posix()


def _path_rule_failure(*, target: Path, canonical_label: str) -> str | None:
    components = tuple(component.casefold() for component in Path(canonical_label).parts)
    if any(component in _SENSITIVE_DIRECTORY_COMPONENTS for component in components[:-1]):
        return "filesystem_sensitive_content"

    basename = target.name.casefold()
    if basename in _SENSITIVE_BASENAMES or any(
        pattern.fullmatch(basename) is not None for pattern in _SENSITIVE_BASENAME_PATTERNS
    ):
        return "filesystem_sensitive_content"
    if target.suffix.casefold() not in _ALLOWED_SUFFIXES:
        return "filesystem_text_unsupported"
    return None


def _is_available_regular_file(metadata: os.stat_result) -> bool:
    return stat.S_ISREG(metadata.st_mode) and not (
        stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0) & _FILTERED_ATTRIBUTES
    )


def _has_unsupported_control_character(content: str) -> bool:
    for character in content:
        codepoint = ord(character)
        if (codepoint < 0x20 and character not in "\t\r\n") or 0x7F <= codepoint <= 0x9F:
            return True
    return False


def _contains_high_confidence_secret(content: str) -> bool:
    if _PRIVATE_KEY_HEADER_PATTERN.search(content) is not None:
        return True

    for match in _AUTHORIZATION_PATTERN.finditer(content):
        value = match.group("value")
        if (
            _AUTHORIZATION_BARE_PLACEHOLDER_PATTERN.fullmatch(value) is None
            and not _is_placeholder_or_reference(value)
        ):
            return True

    for pattern in _TOKEN_PATTERNS:
        for match in pattern.finditer(content):
            if not _is_placeholder_or_reference(match.group("value")):
                return True

    for match in _ASSIGNMENT_PATTERN.finditer(content):
        if not _is_sensitive_assignment_key(match.group("key")):
            continue
        if match.group("typed_value") is None and _is_standalone_type_annotation(
            content,
            match.start(),
        ):
            continue
        raw_value = _explicit_assignment_literal(content, match)
        if raw_value is not None and not _is_placeholder_or_reference(
            raw_value,
            bare_identifier_is_reference=True,
        ):
            return True
    return False


def _is_sensitive_assignment_key(raw_key: str) -> bool:
    identifier = raw_key.lstrip("_")
    normalized = identifier.replace("-", "_").casefold()
    if any(
        normalized == suffix or normalized.endswith(f"_{suffix}")
        for suffix in _SENSITIVE_SNAKE_CASE_SUFFIXES
    ):
        return True
    folded_identifier = identifier.casefold()
    return any(
        folded_identifier.endswith(suffix.casefold())
        for suffix in _SENSITIVE_CAMEL_CASE_SUFFIXES
    )


def _is_standalone_type_annotation(content: str, match_start: int) -> bool:
    line_start = content.rfind("\n", 0, match_start) + 1
    line_end = content.find("\n", match_start)
    if line_end == -1:
        line_end = len(content)
    line = content[line_start:line_end].strip()
    return _TYPE_ANNOTATION_LINE_PATTERN.fullmatch(line) is not None


def _explicit_assignment_literal(content: str, match: re.Match[str]) -> str | None:
    value_group = "typed_value" if match.group("typed_value") is not None else "value"
    raw_value = match.group(value_group)
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return raw_value

    call_expression = _complete_reference_call(content, match.start(value_group))
    if call_expression is not None:
        wrapper_match = _STRING_WRAPPER_CALL_PATTERN.fullmatch(call_expression)
        if wrapper_match is not None:
            return wrapper_match.group("value")
        return None

    lowered = value.casefold()
    if lowered in _ASSIGNMENT_DESCRIPTOR_WORDS or value.startswith(("{", "[")):
        return None
    if _REFERENCE_EXPRESSION_PATTERN.fullmatch(value) is not None:
        return None
    return raw_value


def _complete_reference_call(content: str, value_start: int) -> str | None:
    line_end = content.find("\n", value_start)
    if line_end == -1:
        line_end = len(content)
    candidate = content[value_start:line_end].rstrip("\r")
    head = _REFERENCE_CALL_HEAD_PATTERN.match(candidate)
    if head is None:
        return None

    expected_closers = [")"]
    quote: str | None = None
    escaped = False
    closer_for = {"(": ")", "[": "]", "{": "}"}
    for index in range(head.end(), len(candidate)):
        character = candidate[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in "\"'`":
            quote = character
        elif character in closer_for:
            expected_closers.append(closer_for[character])
        elif character in ")]}":
            if character != expected_closers[-1]:
                return None
            expected_closers.pop()
            if not expected_closers:
                tail = candidate[index + 1 :].lstrip()
                if (
                    not tail
                    or tail.startswith(("#", "//"))
                    or tail[0] in ",;)]}"
                ):
                    return candidate[: index + 1]
                return None
    return None


def _is_placeholder_or_reference(
    raw_value: str,
    *,
    bare_identifier_is_reference: bool = False,
) -> bool:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if not value:
        return True

    lowered = value.casefold()
    if lowered in _PLACEHOLDER_WORDS:
        return True
    if _STRUCTURED_PLACEHOLDER_PATTERN.fullmatch(value) is not None:
        return True
    if re.fullmatch(r"[*x._-]*(?:redacted|masked)[*x._-]*", lowered):
        return True
    if bare_identifier_is_reference and re.fullmatch(
        r"(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Z][A-Z0-9_]*",
        value,
    ):
        return True
    if lowered in {
        "none",
        "null",
        "true",
        "false",
        "str",
        "int",
        "bool",
        "todo",
        "changeme",
        "redacted",
        "masked",
    }:
        return True
    if any(pattern.fullmatch(value) is not None for pattern in _ENV_REFERENCE_PATTERNS):
        return True
    return re.fullmatch(r"[*x._•-]+", lowered) is not None


def _metadata_fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        getattr(metadata, "st_file_attributes", 0),
    )


def _same_open_metadata(before: os.stat_result, opened: os.stat_result) -> bool:
    stable_fields_match = (
        before.st_dev,
        before.st_ino,
        stat.S_IFMT(before.st_mode),
        before.st_size,
        before.st_mtime_ns,
        getattr(before, "st_file_attributes", 0),
    ) == (
        opened.st_dev,
        opened.st_ino,
        stat.S_IFMT(opened.st_mode),
        opened.st_size,
        opened.st_mtime_ns,
        getattr(opened, "st_file_attributes", 0),
    )
    if not stable_fields_match:
        return False

    if os.name != "nt":
        return before.st_ctime_ns == opened.st_ctime_ns

    # CPython on Windows can expose path-stat ctime as birth time while
    # fd-stat ctime exposes the last-write time. Accept only those two exact
    # representations, and independently pin the explicit birth timestamp.
    if opened.st_ctime_ns not in {before.st_ctime_ns, before.st_mtime_ns}:
        return False
    return getattr(before, "st_birthtime_ns", None) == getattr(
        opened,
        "st_birthtime_ns",
        None,
    )


def _read_from_binary_handle(
    target: Path,
    before_metadata: os.stat_result,
) -> tuple[bytes, os.stat_result] | None:
    flags = os.O_RDONLY
    for flag_name in ("O_BINARY", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= getattr(os, flag_name, 0)

    file_descriptor = os.open(target, flags)
    try:
        open_metadata = os.fstat(file_descriptor)
        if not _is_available_regular_file(open_metadata) or not _same_open_metadata(
            before_metadata,
            open_metadata,
        ):
            return None
        raw = _read_bounded(file_descriptor)
        after_read_metadata = os.fstat(file_descriptor)
        if (
            not _is_available_regular_file(after_read_metadata)
            or _metadata_fingerprint(open_metadata)
            != _metadata_fingerprint(after_read_metadata)
        ):
            return None
    finally:
        os.close(file_descriptor)
    return raw, open_metadata


def _read_bounded(file_descriptor: int) -> bytes:
    remaining = MAX_SOURCE_BYTES + 1
    chunks: list[bytes] = []
    while remaining:
        chunk = os.read(file_descriptor, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _failure(code: str) -> ToolResult:
    return ToolResult(
        success=False,
        data=None,
        error=ToolError(code=code, message=_ERROR_MESSAGES[code], retryable=False),
        metadata={},
    )
