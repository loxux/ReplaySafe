from pathlib import Path

import pytest

from replaysafe.analysis import scan_repository
from replaysafe.ir import SourceLocation
from replaysafe.output import render_json, render_sarif, render_text
from replaysafe.parsers.python import PythonAnalyzer
from replaysafe.parsers.sql import SqlAnalyzer


@pytest.mark.parametrize("dialect", ["postgres", "snowflake", "bigquery", "starrocks"])
def test_wall_clock_dialect_coverage(dialect: str) -> None:
    sql = "SELECT * FROM events WHERE event_date = CURRENT_DATE"
    result = SqlAnalyzer().analyze(sql, dialect, SourceLocation("dialect.sql", 1))
    assert not result.diagnostics
    assert result.statements[0].time_dependencies


def test_dbt_jinja_relation_is_tolerated() -> None:
    sql = "{{ config(materialized='incremental') }} SELECT * FROM {{ ref('events') }}"
    result = SqlAnalyzer().analyze(sql, "auto", SourceLocation("models/events.sql", 1))
    assert not result.diagnostics
    assert result.statements


def test_classic_airflow_context() -> None:
    source = """
with DAG("orders", max_active_runs=2) as dag:
    load = SQLExecuteQueryOperator(
        task_id="load_orders",
        retries=4,
        sql="SELECT * FROM src WHERE day = '{{ ds }}'",
    )
"""
    result = PythonAnalyzer().analyze(source, SourceLocation("classic.py", 1))
    assert result.sql[0].task_id == "load_orders"
    assert result.sql[0].retries == 4
    assert result.sql[0].max_active_runs == 2
    assert result.sql[0].logical_time_symbols == ("ds",)


def test_invalid_utf8_diagnostic_does_not_abort_scan(tmp_path: Path) -> None:
    (tmp_path / "broken.sql").write_bytes(b"\xff\xfe")
    (tmp_path / "safe.sql").write_text("SELECT 1", encoding="utf-8")
    result = scan_repository(tmp_path)
    assert "safe.sql" in result.files
    assert any(item.code == "SOURCE_UNREADABLE" for item in result.diagnostics)


def test_diagnostics_are_redacted_in_structured_outputs(tmp_path: Path) -> None:
    (tmp_path / "broken.sql").write_text("SELECT FROM password=hunter2", encoding="utf-8")
    result = scan_repository(tmp_path)
    outputs = render_json(result) + render_sarif(result)
    assert "hunter2" not in outputs


def test_golden_terminal_output() -> None:
    root = Path(__file__).parent / "fixtures" / "golden_case"
    output = render_text(scan_repository(root))
    expected = (Path(__file__).parent / "golden" / "unsafe.txt").read_text(encoding="utf-8")
    assert output == expected
