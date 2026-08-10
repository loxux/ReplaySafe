from pathlib import Path

import pytest

from replaysafe.analysis import scan_repository
from replaysafe.config import AssetMetadata, ReplaySafeConfig, RuleOverride
from replaysafe.ir import WriteMode


def scan_sql(
    tmp_path: Path,
    sql: str,
    rule_id: str,
    config: ReplaySafeConfig | None = None,
) -> list[str]:
    (tmp_path / "case.sql").write_text(sql, encoding="utf-8")
    result = scan_repository(tmp_path, config, selected_rules=frozenset({rule_id}))
    return [item.rule_id for item in result.findings]


def test_positive_mvp_rules(tmp_path: Path) -> None:
    cases = {
        "RS001": "SELECT * FROM events WHERE event_date = CURRENT_DATE",
        "RS002": "INSERT INTO analytics.events SELECT * FROM staged_events",
        "RS003": "INSERT INTO dst SELECT * FROM src WHERE src.updated_at > (SELECT MAX(updated_at) FROM dst)",
        "RS004": "DELETE FROM dst WHERE day=:day; INSERT INTO dst SELECT * FROM src WHERE day=:day",
        "RS006": "SELECT * FROM events LIMIT 100 OFFSET 200",
        "RS008": "SELECT * FROM events WHERE updated_at > :last_ts LIMIT 100",
        "RS017": "SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY id) AS rn FROM events) x WHERE rn = 1",
    }
    for index, (rule_id, sql) in enumerate(cases.items()):
        folder = tmp_path / str(index)
        folder.mkdir()
        assert scan_sql(folder, sql, rule_id) == [rule_id]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT CURRENT_TIMESTAMP AS processed_at FROM events",
        "SELECT 'CURRENT_DATE' AS label FROM events",
        "SELECT * FROM events WHERE event_date = :logical_date",
        "SELECT current_date_column FROM events",
        "SELECT * FROM events -- CURRENT_DATE is intentionally documented",
    ],
)
def test_rs001_safe_negatives(tmp_path: Path, sql: str) -> None:
    assert scan_sql(tmp_path, sql, "RS001") == []


@pytest.mark.parametrize(
    "sql",
    [
        "MERGE INTO dst USING src ON dst.id=src.id WHEN MATCHED THEN UPDATE SET id=src.id",
        "INSERT OVERWRITE TABLE dst SELECT * FROM src",
        "INSERT INTO dst SELECT * FROM src ON CONFLICT (id) DO NOTHING",
        "BEGIN; DELETE FROM dst; INSERT INTO dst SELECT * FROM src; COMMIT",
        "DELETE FROM dst; INSERT INTO dst SELECT * FROM src",
    ],
)
def test_rs002_safe_negatives(tmp_path: Path, sql: str) -> None:
    assert scan_sql(tmp_path, sql, "RS002") == []


def test_rs002_explicit_duplicate_tolerance(tmp_path: Path) -> None:
    config = ReplaySafeConfig(
        assets={"dst": AssetMetadata(append_only=True, duplicate_tolerant=True)}
    )
    assert scan_sql(tmp_path, "INSERT INTO dst SELECT * FROM src", "RS002", config) == []


def test_rs002_anti_join_guards(tmp_path: Path) -> None:
    guarded = (
        "INSERT INTO dst SELECT s.* FROM src s "
        "LEFT JOIN dst existing ON existing.id = s.id WHERE existing.id IS NULL"
    )
    assert scan_sql(tmp_path, guarded, "RS002") == []


def test_rs002_starrocks_primary_key_catalog_across_files(tmp_path: Path) -> None:
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE analytics.kraken (p_key BIGINT, value STRING) "
        "PRIMARY KEY (p_key) DISTRIBUTED BY HASH(p_key)",
        encoding="utf-8",
    )
    (tmp_path / "merge.sql").write_text(
        "INSERT INTO analytics.kraken SELECT * FROM staging.kraken",
        encoding="utf-8",
    )
    config = ReplaySafeConfig(dialect="starrocks")
    result = scan_repository(tmp_path, config, selected_rules=frozenset({"RS002"}))
    assert result.findings == ()


def test_rs002_ambiguous_unqualified_primary_key_target_stays_visible(tmp_path: Path) -> None:
    (tmp_path / "schemas.sql").write_text(
        "CREATE TABLE first.orders (id BIGINT) PRIMARY KEY (id) DISTRIBUTED BY HASH(id);"
        "CREATE TABLE second.orders (id BIGINT) PRIMARY KEY (id) DISTRIBUTED BY HASH(id)",
        encoding="utf-8",
    )
    (tmp_path / "load.sql").write_text(
        "INSERT INTO orders SELECT * FROM staging.orders",
        encoding="utf-8",
    )
    config = ReplaySafeConfig(dialect="starrocks")
    result = scan_repository(tmp_path, config, selected_rules=frozenset({"RS002"}))
    assert [finding.rule_id for finding in result.findings] == ["RS002"]


def test_rs002_configured_upsert_semantics(tmp_path: Path) -> None:
    config = ReplaySafeConfig(
        assets={"analytics.kraken": AssetMetadata(write_semantics=WriteMode.UPSERT)}
    )
    assert (
        scan_sql(
            tmp_path,
            "INSERT INTO analytics.kraken SELECT * FROM staging.kraken",
            "RS002",
            config,
        )
        == []
    )


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO dst SELECT * FROM src WHERE ts > (SELECT MAX(ts) FROM other)",
        "SELECT * FROM src WHERE ts > (SELECT MAX(ts) FROM dst)",
        "INSERT INTO dst SELECT * FROM src WHERE ts > (SELECT MIN(ts) FROM dst)",
        "INSERT INTO dst SELECT * FROM src WHERE ts > :checkpoint",
        "INSERT INTO dst SELECT * FROM src",
    ],
)
def test_rs003_safe_negatives(tmp_path: Path, sql: str) -> None:
    assert scan_sql(tmp_path, sql, "RS003") == []


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM old; INSERT INTO new SELECT * FROM src",
        "INSERT INTO dst SELECT * FROM src; DELETE FROM dst",
        "BEGIN; DELETE FROM dst; INSERT INTO dst SELECT * FROM src; COMMIT",
        "INSERT OVERWRITE TABLE dst SELECT * FROM src",
        "DELETE FROM dst",
        "INSERT INTO dst SELECT * FROM src",
    ],
)
def test_rs004_safe_negatives(tmp_path: Path, sql: str) -> None:
    assert scan_sql(tmp_path, sql, "RS004") == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM src LIMIT 10",
        "SELECT * FROM src ORDER BY id LIMIT 10 OFFSET 20",
        "SELECT * FROM src WHERE id > :last_id ORDER BY id LIMIT 10",
        "SELECT * FROM src",
        "SELECT ROW_NUMBER() OVER (ORDER BY id) AS rn FROM src",
    ],
)
def test_rs006_safe_negatives(tmp_path: Path, sql: str) -> None:
    assert scan_sql(tmp_path, sql, "RS006") == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM src WHERE updated_at > :last_ts",
        "SELECT * FROM src WHERE ts > :last_ts OR (ts = :last_ts AND id > :last_id) ORDER BY ts,id LIMIT 100",
        "SELECT * FROM src WHERE id > :last_id LIMIT 100",
        "SELECT * FROM src WHERE updated_at = :last_ts LIMIT 100",
        "SELECT * FROM src LIMIT 100",
        "INSERT INTO dst SELECT * FROM src WHERE updated_at > (SELECT MAX(updated_at) FROM dst) LIMIT 100",
    ],
)
def test_rs008_safe_negatives(tmp_path: Path, sql: str) -> None:
    assert scan_sql(tmp_path, sql, "RS008") == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT ROW_NUMBER() OVER (PARTITION BY id) AS rn FROM src",
        "SELECT * FROM (SELECT ROW_NUMBER() OVER () AS rn FROM src) x WHERE rn=1",
        "SELECT * FROM (SELECT ROW_NUMBER() OVER (PARTITION BY id ORDER BY updated_at DESC) AS rn FROM src) x WHERE rn=1",
        "SELECT DISTINCT id FROM src",
        "SELECT id, MAX(updated_at) FROM src GROUP BY id",
        "SELECT RANK() OVER (PARTITION BY id) AS display_rank FROM src",
    ],
)
def test_rs017_safe_negatives(tmp_path: Path, sql: str) -> None:
    assert scan_sql(tmp_path, sql, "RS017") == []


def test_rs014_disabled_then_enabled(tmp_path: Path) -> None:
    source = """
@task(retries=2)
def notify():
    requests.post("https://example.test/pay")
"""
    (tmp_path / "dag.py").write_text(source, encoding="utf-8")
    assert scan_repository(tmp_path).findings == ()
    config = ReplaySafeConfig(rules={"RS014": RuleOverride(enabled=True)})
    result = scan_repository(tmp_path, config, selected_rules=frozenset({"RS014"}))
    assert [item.rule_id for item in result.findings] == ["RS014"]


def test_python_wall_clock_assignment_reaches_rs001(tmp_path: Path) -> None:
    source = """
@task(retries=1)
def load():
    start = datetime.now()
    sql = f"SELECT * FROM events WHERE created_at > {start}"
    warehouse.execute(sql)
"""
    (tmp_path / "dag.py").write_text(source, encoding="utf-8")
    result = scan_repository(tmp_path, selected_rules=frozenset({"RS001"}))
    assert [item.rule_id for item in result.findings] == ["RS001"]


def test_python_transaction_wrapper_suppresses_rs004(tmp_path: Path) -> None:
    source = """
with connection.begin():
    warehouse.execute("DELETE FROM dst")
    warehouse.execute("INSERT INTO dst SELECT * FROM src")
"""
    (tmp_path / "job.py").write_text(source, encoding="utf-8")
    result = scan_repository(tmp_path, selected_rules=frozenset({"RS004"}))
    assert result.findings == ()


def test_unrelated_delete_range_does_not_hide_blind_append(tmp_path: Path) -> None:
    sql = """
DELETE FROM dst WHERE day = '2026-01-01';
INSERT INTO dst SELECT * FROM src WHERE day = '2026-01-02';
"""
    (tmp_path / "ranges.sql").write_text(sql, encoding="utf-8")
    result = scan_repository(tmp_path, selected_rules=frozenset({"RS002", "RS004"}))
    assert [item.rule_id for item in result.findings] == ["RS002"]
