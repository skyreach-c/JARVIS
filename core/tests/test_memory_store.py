import importlib
import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path

import pytest


def memory_store_module():  # type: ignore[no-untyped-def]
    return importlib.import_module("jarvis_core.memory_store")


def create_database(tmp_path: Path, schema: str) -> Path:
    database_path = tmp_path / "memory.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(schema)
    return database_path


def test_memory_constants_are_centrally_defined() -> None:
    module = memory_store_module()

    assert module.DEFAULT_MAX_PINNED_MEMORIES == 20
    assert module.MAX_PINNED_MEMORY_CHARS == 500
    assert module.MEMORY_DB_FILENAME == "memory.db"


def test_explicit_absolute_data_dir_has_highest_priority(tmp_path: Path) -> None:
    module = memory_store_module()
    explicit_dir = tmp_path / "explicit"
    environment: Mapping[str, str] = {
        "JARVIS_DATA_DIR": str(tmp_path / "environment"),
        "LOCALAPPDATA": str(tmp_path / "local-app-data"),
    }

    database_path = module.resolve_memory_database_path(
        data_dir=explicit_dir,
        environ=environment,
    )

    assert database_path == explicit_dir / "memory.db"


def test_environment_override_precedes_local_app_data(tmp_path: Path) -> None:
    module = memory_store_module()
    override_dir = tmp_path / "override"

    database_path = module.resolve_memory_database_path(
        environ={
            "JARVIS_DATA_DIR": str(override_dir),
            "LOCALAPPDATA": str(tmp_path / "local-app-data"),
        }
    )

    assert database_path == override_dir / "memory.db"


def test_local_app_data_default_uses_jarvis_subdirectory(tmp_path: Path) -> None:
    module = memory_store_module()

    database_path = module.resolve_memory_database_path(
        environ={"LOCALAPPDATA": str(tmp_path)}
    )

    assert database_path == tmp_path / "JARVIS" / "memory.db"


@pytest.mark.parametrize("source", ["explicit", "environment", "local-app-data"])
def test_relative_data_directory_is_rejected(source: str) -> None:
    module = memory_store_module()
    kwargs: dict[str, object] = {"environ": {}}
    if source == "explicit":
        kwargs["data_dir"] = Path("relative")
    elif source == "environment":
        kwargs["environ"] = {"JARVIS_DATA_DIR": "relative"}
    else:
        kwargs["environ"] = {"LOCALAPPDATA": "relative"}

    with pytest.raises(ValueError, match="absolute"):
        module.resolve_memory_database_path(**kwargs)


def test_missing_data_directory_source_fails_fast() -> None:
    module = memory_store_module()

    with pytest.raises(RuntimeError, match="LOCALAPPDATA"):
        module.resolve_memory_database_path(environ={})


def test_path_resolution_does_not_depend_on_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = memory_store_module()
    data_dir = tmp_path / "data"
    unrelated_cwd = tmp_path / "unrelated"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)

    database_path = module.resolve_memory_database_path(
        environ={"JARVIS_DATA_DIR": str(data_dir)}
    )

    assert database_path == data_dir / "memory.db"


def test_new_store_creates_and_reopens_expected_schema(tmp_path: Path) -> None:
    module = memory_store_module()
    database_path = tmp_path / "nested" / "memory.db"

    module.SQLiteMemoryStore(database_path)
    module.SQLiteMemoryStore(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = connection.execute(
            "PRAGMA table_info(pinned_memories)"
        ).fetchall()
        schema_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'pinned_memories'"
        ).fetchone()[0]

    assert [(row[1], row[2], row[3], row[4], row[5]) for row in columns] == [
        ("id", "INTEGER", 0, None, 1),
        ("content", "TEXT", 1, None, 0),
    ]
    assert "AUTOINCREMENT" in schema_sql.upper()
    assert "UNIQUE" in schema_sql.upper()


@pytest.mark.parametrize(
    "schema",
    [
        "CREATE TABLE pinned_memories (content TEXT NOT NULL UNIQUE);",
        (
            "CREATE TABLE pinned_memories "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, extra TEXT);"
        ),
        (
            "CREATE TABLE pinned_memories "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL UNIQUE, "
            "extra TEXT);"
        ),
        (
            "CREATE TABLE pinned_memories "
            "(id TEXT PRIMARY KEY, content TEXT NOT NULL UNIQUE);"
        ),
        (
            "CREATE TABLE pinned_memories "
            "(id INTEGER PRIMARY KEY, content TEXT NOT NULL UNIQUE);"
        ),
        (
            "CREATE TABLE pinned_memories "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, content INTEGER NOT NULL UNIQUE);"
        ),
        (
            "CREATE TABLE pinned_memories "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT UNIQUE);"
        ),
        (
            "CREATE TABLE pinned_memories "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "content TEXT NOT NULL DEFAULT 'x' UNIQUE);"
        ),
        (
            "CREATE TABLE pinned_memories "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL);"
        ),
    ],
)
def test_incompatible_columns_and_constraints_fail_fast(
    tmp_path: Path,
    schema: str,
) -> None:
    module = memory_store_module()
    database_path = create_database(tmp_path, schema)

    with pytest.raises(module.IncompatibleMemorySchemaError):
        module.SQLiteMemoryStore(database_path)


def test_composite_unique_index_is_not_accepted(tmp_path: Path) -> None:
    module = memory_store_module()
    database_path = create_database(
        tmp_path,
        """
        CREATE TABLE pinned_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL
        );
        CREATE UNIQUE INDEX unique_content_and_id
        ON pinned_memories(content, id);
        """,
    )

    with pytest.raises(module.IncompatibleMemorySchemaError):
        module.SQLiteMemoryStore(database_path)


def test_nocase_unique_index_is_not_accepted(tmp_path: Path) -> None:
    module = memory_store_module()
    database_path = create_database(
        tmp_path,
        """
        CREATE TABLE pinned_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL
        );
        CREATE UNIQUE INDEX unique_content_nocase
        ON pinned_memories(content COLLATE NOCASE);
        """,
    )

    with pytest.raises(module.IncompatibleMemorySchemaError):
        module.SQLiteMemoryStore(database_path)


def test_partial_unique_index_is_not_accepted(tmp_path: Path) -> None:
    module = memory_store_module()
    database_path = create_database(
        tmp_path,
        """
        CREATE TABLE pinned_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL
        );
        CREATE UNIQUE INDEX partial_unique_content
        ON pinned_memories(content)
        WHERE content <> '';
        """,
    )

    with pytest.raises(module.IncompatibleMemorySchemaError):
        module.SQLiteMemoryStore(database_path)


def test_single_column_binary_unique_index_is_accepted(tmp_path: Path) -> None:
    module = memory_store_module()
    database_path = create_database(
        tmp_path,
        """
        CREATE TABLE pinned_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL
        );
        CREATE UNIQUE INDEX unique_content_binary
        ON pinned_memories(content COLLATE BINARY);
        CREATE INDEX ordinary_content_index ON pinned_memories(content);
        """,
    )

    module.SQLiteMemoryStore(database_path)


def test_incompatible_schema_is_not_modified(tmp_path: Path) -> None:
    module = memory_store_module()
    database_path = create_database(
        tmp_path,
        "CREATE TABLE pinned_memories "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT);",
    )
    before = database_path.read_bytes()

    with pytest.raises(module.IncompatibleMemorySchemaError):
        module.SQLiteMemoryStore(database_path)

    assert database_path.read_bytes() == before


def test_corrupt_database_fails_fast(tmp_path: Path) -> None:
    module = memory_store_module()
    database_path = tmp_path / "memory.db"
    database_path.write_bytes(os.urandom(128))

    with pytest.raises(module.MemoryStoreError):
        module.SQLiteMemoryStore(database_path)


def test_store_starts_empty_and_supports_crud(tmp_path: Path) -> None:
    module = memory_store_module()
    store = module.SQLiteMemoryStore(tmp_path / "memory.db")

    assert store.list_memories() == ()
    first = store.remember("first memory")
    second = store.remember("second memory")

    assert first == module.RememberResult(
        memory=module.PinnedMemory(id=1, content="first memory"),
        created=True,
    )
    assert second.memory.id == 2
    assert store.list_memories() == (first.memory, second.memory)
    assert store.forget(first.memory.id) is True
    assert store.forget(first.memory.id) is False
    assert store.list_memories() == (second.memory,)


def test_second_store_instance_reads_persisted_records(tmp_path: Path) -> None:
    module = memory_store_module()
    database_path = tmp_path / "memory.db"
    first_store = module.SQLiteMemoryStore(database_path)
    saved = first_store.remember("persistent memory").memory

    second_store = module.SQLiteMemoryStore(database_path)

    assert second_store.list_memories() == (saved,)


def test_normalization_is_idempotent_and_preserves_internal_text(tmp_path: Path) -> None:
    module = memory_store_module()
    store = module.SQLiteMemoryStore(tmp_path / "memory.db")
    original = "line one\n  line two,  Keep Case"

    created = store.remember(f"  {original}\n")
    duplicate = store.remember(original)

    assert created.created is True
    assert duplicate == module.RememberResult(memory=created.memory, created=False)
    assert created.memory.content == original


def test_duplicate_matching_is_case_sensitive(tmp_path: Path) -> None:
    module = memory_store_module()
    store = module.SQLiteMemoryStore(tmp_path / "memory.db")

    lower = store.remember("alpha")
    upper = store.remember("Alpha")

    assert lower.memory.id != upper.memory.id
    assert len(store.list_memories()) == 2


def test_exactly_500_python_characters_are_accepted(tmp_path: Path) -> None:
    module = memory_store_module()
    store = module.SQLiteMemoryStore(tmp_path / "memory.db")
    content = "x" * 500

    result = store.remember(f" {content} ")

    assert result.memory.content == content
    assert len(result.memory.content) == 500


def test_501_python_characters_are_rejected_without_writing(tmp_path: Path) -> None:
    module = memory_store_module()
    store = module.SQLiteMemoryStore(tmp_path / "memory.db")

    with pytest.raises(ValueError, match="500"):
        store.remember("x" * 501)

    assert store.list_memories() == ()


def test_empty_trimmed_content_is_rejected(tmp_path: Path) -> None:
    module = memory_store_module()
    store = module.SQLiteMemoryStore(tmp_path / "memory.db")

    with pytest.raises(ValueError, match="empty"):
        store.remember(" \t\r\n ")


def test_emoji_length_uses_python_len_not_grapheme_count(tmp_path: Path) -> None:
    module = memory_store_module()
    store = module.SQLiteMemoryStore(tmp_path / "memory.db")
    multi_code_point_emoji = "👨‍👩‍👧‍👦"
    assert len(multi_code_point_emoji) > 1
    content = multi_code_point_emoji * 72
    assert len(content) > 500

    with pytest.raises(ValueError, match="500"):
        store.remember(content)


def test_limit_rejects_new_memory_but_allows_existing_duplicate(
    tmp_path: Path,
) -> None:
    module = memory_store_module()
    store = module.SQLiteMemoryStore(tmp_path / "memory.db", max_memories=2)
    first = store.remember("one")
    store.remember("two")

    duplicate = store.remember(" one ")
    with pytest.raises(module.MemoryLimitReachedError) as error:
        store.remember("three")

    assert duplicate == module.RememberResult(memory=first.memory, created=False)
    assert error.value.limit == 2
    assert [memory.content for memory in store.list_memories()] == ["one", "two"]


def test_default_limit_rejects_the_twenty_first_memory(tmp_path: Path) -> None:
    module = memory_store_module()
    store = module.SQLiteMemoryStore(tmp_path / "memory.db")
    for index in range(20):
        store.remember(f"memory {index}")

    with pytest.raises(module.MemoryLimitReachedError) as error:
        store.remember("memory 20")

    assert error.value.limit == 20
    assert len(store.list_memories()) == 20


def test_deleted_ids_are_not_reused(tmp_path: Path) -> None:
    module = memory_store_module()
    store = module.SQLiteMemoryStore(tmp_path / "memory.db")
    first = store.remember("first").memory
    assert store.forget(first.id) is True

    second = store.remember("second").memory

    assert second.id > first.id


def test_clear_all_deletes_exact_snapshot_and_returns_executor_result(
    tmp_path: Path,
) -> None:
    module = memory_store_module()
    database_path = tmp_path / "memory.db"
    store = module.SQLiteMemoryStore(database_path)
    first = store.remember("first").memory
    second = store.remember("second").memory

    result = store.clear_all((first.id, second.id))

    assert result == module.ClearAllResult(
        status="cleared",
        cleared_ids=(first.id, second.id),
        cleared_count=2,
    )
    assert module.SQLiteMemoryStore(database_path).list_memories() == ()


def test_clear_all_snapshot_change_is_atomic_noop(tmp_path: Path) -> None:
    module = memory_store_module()
    store = module.SQLiteMemoryStore(tmp_path / "memory.db")
    first = store.remember("first").memory
    second = store.remember("second").memory
    expected_ids = (first.id, second.id)
    third = store.remember("created after confirmation").memory

    result = store.clear_all(expected_ids)

    assert result == module.ClearAllResult(
        status="snapshot_changed",
        cleared_ids=(),
        cleared_count=0,
    )
    assert store.list_memories() == (first, second, third)


def test_clear_all_executor_failure_rolls_back_every_delete(tmp_path: Path) -> None:
    module = memory_store_module()
    database_path = tmp_path / "memory.db"
    store = module.SQLiteMemoryStore(database_path)
    first = store.remember("first").memory
    second = store.remember("second").memory
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_clear_all
            BEFORE DELETE ON pinned_memories
            BEGIN
                SELECT RAISE(ABORT, 'blocked');
            END
            """
        )

    with pytest.raises(module.MemoryStoreError) as error:
        store.clear_all((first.id, second.id))

    assert error.value.operation == "clear_all"
    assert store.list_memories() == (first, second)


@pytest.mark.parametrize("invalid_id", [0, -1, 1.5, True])
def test_forget_requires_a_positive_integer(tmp_path: Path, invalid_id: object) -> None:
    module = memory_store_module()
    store = module.SQLiteMemoryStore(tmp_path / "memory.db")

    with pytest.raises(ValueError, match="positive integer"):
        store.forget(invalid_id)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid_limit", [0, -1, 1.5, True])
def test_store_limit_requires_a_positive_integer(
    tmp_path: Path,
    invalid_limit: object,
) -> None:
    module = memory_store_module()

    with pytest.raises(ValueError, match="positive integer"):
        module.SQLiteMemoryStore(
            tmp_path / "memory.db",
            max_memories=invalid_limit,
        )


def test_sqlite_errors_are_wrapped_without_memory_content(tmp_path: Path) -> None:
    module = memory_store_module()
    database_path = tmp_path / "memory.db"
    store = module.SQLiteMemoryStore(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE pinned_memories")

    secret_content = "private memory must not be logged"
    with pytest.raises(module.MemoryStoreError) as error:
        store.remember(secret_content)

    assert error.value.operation == "remember"
    assert error.value.error_type == "OperationalError"
    assert secret_content not in str(error.value)


def test_failed_insert_transaction_leaves_no_partial_record(tmp_path: Path) -> None:
    module = memory_store_module()
    database_path = tmp_path / "memory.db"
    store = module.SQLiteMemoryStore(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_insert
            BEFORE INSERT ON pinned_memories
            BEGIN
                SELECT RAISE(ABORT, 'blocked');
            END
            """
        )

    with pytest.raises(module.MemoryStoreError):
        store.remember("must not persist")

    assert store.list_memories() == ()
