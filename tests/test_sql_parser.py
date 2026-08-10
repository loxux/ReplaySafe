from replaysafe.ir import SourceLocation, WriteMode, WriteOperation
from replaysafe.parsers.sql import SqlAnalyzer


def analyze(sql: str, dialect: str = "auto"):
    return SqlAnalyzer().analyze(sql, dialect, SourceLocation("query.sql", 1))


def writes(sql: str, dialect: str = "auto") -> list[WriteOperation]:
    result = analyze(sql, dialect)
    return [
        operation
        for statement in result.statements
        for operation in statement.operations
        if isinstance(operation, WriteOperation)
    ]


def test_write_modes_and_sources() -> None:
    assert writes("INSERT INTO dst SELECT * FROM src")[0].mode == WriteMode.APPEND
    assert (
        writes("MERGE INTO dst USING src ON dst.id=src.id WHEN MATCHED THEN UPDATE SET id=src.id")[
            0
        ].mode
        == WriteMode.MERGE
    )
    assert writes("UPDATE dst SET value=1")[0].mode == WriteMode.UPDATE
    assert writes("DELETE FROM dst WHERE day=:day")[0].mode == WriteMode.DELETE
    assert writes("INSERT OVERWRITE TABLE dst SELECT * FROM src")[0].mode == WriteMode.OVERWRITE


def test_multiple_statements_continue_after_parse_error() -> None:
    result = analyze("SELECT * FROM ok; SELECT FROM; INSERT INTO dst SELECT * FROM src")
    assert len(result.statements) == 2
    assert any(item.code == "SQL_PARSE_ERROR" for item in result.diagnostics)
    assert writes("SELECT 1; INSERT INTO dst SELECT * FROM src")[0].statement_index == 1


def test_predicate_wall_clock_not_projection() -> None:
    unsafe = analyze("SELECT * FROM events WHERE created_at::date = CURRENT_DATE", "postgres")
    safe = analyze("SELECT CURRENT_TIMESTAMP AS processed_at FROM events", "postgres")
    assert unsafe.statements[0].time_dependencies[0].expression == "CURRENT_DATE"
    assert safe.statements[0].time_dependencies == ()


def test_pagination_and_window_metadata() -> None:
    result = analyze(
        "SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY id) AS rn FROM src) x "
        "WHERE rn = 1 LIMIT 10 OFFSET 20"
    )
    statement = result.statements[0]
    assert statement.pagination is not None
    assert statement.pagination.offset == 20
    assert statement.windows[0].partition_by == ("id",)
    assert statement.windows[0].survivor_selection


def test_explicit_transaction_group() -> None:
    result = analyze("BEGIN; DELETE FROM dst; INSERT INTO dst SELECT * FROM src; COMMIT")
    writes_found = [
        operation
        for statement in result.statements
        for operation in statement.operations
        if isinstance(operation, WriteOperation)
    ]
    assert len(result.transaction_groups) == 1
    assert writes_found[0].transactional_group == writes_found[1].transactional_group


def test_insert_anti_join_guards_are_conflict_handling() -> None:
    left_join = writes(
        "INSERT INTO dst SELECT s.* FROM src s "
        "LEFT JOIN dst existing ON existing.id = s.id WHERE existing.id IS NULL"
    )[0]
    not_exists = writes(
        "INSERT INTO dst SELECT * FROM src s WHERE NOT EXISTS "
        "(SELECT 1 FROM dst existing WHERE existing.id = s.id)"
    )[0]
    assert left_join.conflict_handling
    assert not_exists.conflict_handling


def test_unrelated_or_uncorrelated_anti_joins_are_not_guards() -> None:
    unrelated = writes(
        "INSERT INTO dst SELECT s.* FROM src s "
        "LEFT JOIN other existing ON existing.id = s.id WHERE existing.id IS NULL"
    )[0]
    uncorrelated = writes(
        "INSERT INTO dst SELECT * FROM src s WHERE NOT EXISTS "
        "(SELECT 1 FROM dst WHERE status = 'complete')"
    )[0]
    bypassable = writes(
        "INSERT INTO dst SELECT s.* FROM src s "
        "LEFT JOIN dst existing ON existing.id = s.id "
        "WHERE existing.id IS NULL OR s.force_reload = 1"
    )[0]
    assert not unrelated.conflict_handling
    assert not uncorrelated.conflict_handling
    assert not bypassable.conflict_handling


def test_starrocks_primary_key_definition_is_upsert() -> None:
    result = analyze(
        "CREATE TABLE analytics.kraken (p_key BIGINT, value STRING) "
        "PRIMARY KEY (p_key) DISTRIBUTED BY HASH(p_key)",
        "starrocks",
    )
    definition = result.asset_definitions[0]
    assert definition.asset.name == "analytics.kraken"
    assert definition.primary_key == ("p_key",)
    assert definition.write_mode == WriteMode.UPSERT


def test_postgres_primary_key_is_not_inferred_as_upsert() -> None:
    result = analyze("CREATE TABLE dst (id BIGINT PRIMARY KEY)", "postgres")
    assert result.asset_definitions == ()
