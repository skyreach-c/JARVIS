from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

DEFAULT_MAX_PINNED_MEMORIES = 20
MAX_PINNED_MEMORY_CHARS = 500
MEMORY_DB_FILENAME = "memory.db"

_CREATE_PINNED_MEMORIES_TABLE = """
CREATE TABLE IF NOT EXISTS pinned_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL UNIQUE
)
"""


@dataclass(frozen=True, slots=True)
class PinnedMemory:
    id: int
    content: str


@dataclass(frozen=True, slots=True)
class RememberResult:
    memory: PinnedMemory
    created: bool


@dataclass(frozen=True, slots=True)
class ClearAllResult:
    status: Literal["cleared", "snapshot_changed"]
    cleared_ids: tuple[int, ...]
    cleared_count: int


class MemoryStore(Protocol):
    def remember(self, content: str) -> RememberResult: ...

    def list_memories(self) -> tuple[PinnedMemory, ...]: ...

    def forget(self, memory_id: int) -> bool: ...

    def clear_all(self, expected_ids: tuple[int, ...]) -> ClearAllResult: ...


class MemoryLimitReachedError(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"pinned memory limit reached: {limit}")
        self.limit = limit


class MemoryStoreError(Exception):
    def __init__(self, *, operation: str, error_type: str) -> None:
        super().__init__(f"memory store operation failed: {operation} ({error_type})")
        self.operation = operation
        self.error_type = error_type


class IncompatibleMemorySchemaError(MemoryStoreError):
    def __init__(self, reason: str) -> None:
        super().__init__(operation="initialize", error_type="incompatible_schema")
        self.reason = reason


def normalize_memory_content(content: str) -> str:
    normalized_content = content.strip()
    if not normalized_content:
        raise ValueError("memory content must not be empty")
    if len(normalized_content) > MAX_PINNED_MEMORY_CHARS:
        raise ValueError(
            f"memory content must contain at most {MAX_PINNED_MEMORY_CHARS} characters"
        )
    return normalized_content


def resolve_memory_database_path(
    *,
    data_dir: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    environment = os.environ if environ is None else environ

    if data_dir is not None:
        selected_dir = Path(data_dir)
    elif environment.get("JARVIS_DATA_DIR"):
        selected_dir = Path(environment["JARVIS_DATA_DIR"])
    else:
        local_app_data = environment.get("LOCALAPPDATA")
        if not local_app_data:
            raise RuntimeError(
                "LOCALAPPDATA is required when JARVIS_DATA_DIR is not configured"
            )
        selected_dir = Path(local_app_data) / "JARVIS"

    if not selected_dir.is_absolute():
        raise ValueError("JARVIS memory data directory must be an absolute path")
    return selected_dir / MEMORY_DB_FILENAME


class SQLiteMemoryStore:
    def __init__(
        self,
        database_path: Path | str,
        *,
        max_memories: int = DEFAULT_MAX_PINNED_MEMORIES,
    ) -> None:
        path = Path(database_path)
        if not path.is_absolute():
            raise ValueError("memory database path must be absolute")
        if (
            isinstance(max_memories, bool)
            or not isinstance(max_memories, int)
            or max_memories < 1
        ):
            raise ValueError("max_memories must be a positive integer")

        self.database_path = path
        self.max_memories = max_memories
        self._initialize()

    def remember(self, content: str) -> RememberResult:
        normalized_content = normalize_memory_content(content)
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT id, content FROM pinned_memories WHERE content = ?",
                    (normalized_content,),
                ).fetchone()
                if existing is not None:
                    return RememberResult(
                        memory=PinnedMemory(id=existing[0], content=existing[1]),
                        created=False,
                    )

                memory_count = connection.execute(
                    "SELECT COUNT(*) FROM pinned_memories"
                ).fetchone()[0]
                if memory_count >= self.max_memories:
                    raise MemoryLimitReachedError(self.max_memories)

                cursor = connection.execute(
                    "INSERT INTO pinned_memories (content) VALUES (?)",
                    (normalized_content,),
                )
                return RememberResult(
                    memory=PinnedMemory(
                        id=int(cursor.lastrowid),
                        content=normalized_content,
                    ),
                    created=True,
                )
        except MemoryLimitReachedError:
            raise
        except sqlite3.Error as error:
            raise MemoryStoreError(
                operation="remember",
                error_type=type(error).__name__,
            ) from error

    def list_memories(self) -> tuple[PinnedMemory, ...]:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT id, content FROM pinned_memories ORDER BY id ASC"
                ).fetchall()
        except sqlite3.Error as error:
            raise MemoryStoreError(
                operation="list_memories",
                error_type=type(error).__name__,
            ) from error

        return tuple(PinnedMemory(id=row[0], content=row[1]) for row in rows)

    def forget(self, memory_id: int) -> bool:
        if (
            isinstance(memory_id, bool)
            or not isinstance(memory_id, int)
            or memory_id < 1
        ):
            raise ValueError("memory_id must be a positive integer")

        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    "DELETE FROM pinned_memories WHERE id = ?",
                    (memory_id,),
                )
                return cursor.rowcount > 0
        except sqlite3.Error as error:
            raise MemoryStoreError(
                operation="forget",
                error_type=type(error).__name__,
            ) from error

    def clear_all(self, expected_ids: tuple[int, ...]) -> ClearAllResult:
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                current_ids = tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT id FROM pinned_memories ORDER BY id ASC"
                    ).fetchall()
                )
                if current_ids != expected_ids:
                    connection.rollback()
                    return ClearAllResult(
                        status="snapshot_changed",
                        cleared_ids=(),
                        cleared_count=0,
                    )

                cursor = connection.execute("DELETE FROM pinned_memories")
                if cursor.rowcount != len(current_ids):
                    raise sqlite3.DatabaseError("unexpected clear_all row count")
                connection.commit()
                return ClearAllResult(
                    status="cleared",
                    cleared_ids=current_ids,
                    cleared_count=cursor.rowcount,
                )
        except sqlite3.Error as error:
            raise MemoryStoreError(
                operation="clear_all",
                error_type=type(error).__name__,
            ) from error

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def _initialize(self) -> None:
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection, connection:
                connection.execute(_CREATE_PINNED_MEMORIES_TABLE)
                self._validate_schema(connection)
        except IncompatibleMemorySchemaError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise MemoryStoreError(
                operation="initialize",
                error_type=type(error).__name__,
            ) from error

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        schema_row = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'pinned_memories'"
        ).fetchone()
        if schema_row is None or not isinstance(schema_row[0], str):
            raise IncompatibleMemorySchemaError("missing pinned_memories table")

        columns = connection.execute(
            "PRAGMA table_info(pinned_memories)"
        ).fetchall()
        if [row[1] for row in columns] != ["id", "content"]:
            raise IncompatibleMemorySchemaError("unexpected columns")

        id_column, content_column = columns
        if (
            id_column[2].upper() != "INTEGER"
            or id_column[4] is not None
            or id_column[5] != 1
        ):
            raise IncompatibleMemorySchemaError("incompatible id column")
        if (
            content_column[2].upper() != "TEXT"
            or content_column[3] != 1
            or content_column[4] is not None
            or content_column[5] != 0
        ):
            raise IncompatibleMemorySchemaError("incompatible content column")
        if "AUTOINCREMENT" not in schema_row[0].upper():
            raise IncompatibleMemorySchemaError("AUTOINCREMENT is required")
        if not SQLiteMemoryStore._has_binary_content_unique_index(connection):
            raise IncompatibleMemorySchemaError(
                "a single-column BINARY unique content index is required"
            )

    @staticmethod
    def _has_binary_content_unique_index(connection: sqlite3.Connection) -> bool:
        for index_row in connection.execute(
            "PRAGMA index_list(pinned_memories)"
        ).fetchall():
            index_name = index_row[1]
            is_unique = index_row[2] == 1
            is_partial = len(index_row) > 4 and index_row[4] == 1
            if not is_unique or is_partial:
                continue

            quoted_name = index_name.replace('"', '""')
            index_columns = connection.execute(
                f'PRAGMA index_xinfo("{quoted_name}")'
            ).fetchall()
            key_columns = [row for row in index_columns if row[5] == 1]
            if len(key_columns) != 1:
                continue
            column = key_columns[0]
            column_name = column[2]
            collation = column[4]
            if column_name == "content" and str(collation).upper() == "BINARY":
                return True
        return False
