from replaysafe.ir import SourceLocation
from replaysafe.parsers.python import PythonAnalyzer


def test_extracts_local_fstring_and_airflow_context_without_execution() -> None:
    source = """
from airflow.decorators import task

@task(task_id="load_orders", retries=3)
def load():
    sql = f"SELECT * FROM orders WHERE day = {dangerous()}"
    warehouse.execute(sql)

raise RuntimeError("must never execute")
"""
    result = PythonAnalyzer().analyze(source, SourceLocation("dags/orders.py", 1))
    assert len(result.sql) == 1
    assert result.sql[0].task_id == "load_orders"
    assert result.sql[0].retries == 3
    assert "{dangerous()}" in result.sql[0].text


def test_dynamic_sql_is_diagnostic_only() -> None:
    result = PythonAnalyzer().analyze(
        "warehouse.execute(build_sql())", SourceLocation("dynamic.py", 1)
    )
    assert result.sql == ()
    assert result.diagnostics[0].code == "PY_DYNAMIC_SQL"


def test_operator_sql_and_http_side_effect() -> None:
    source = """
SQLExecuteQueryOperator(task_id="load", retries=2, sql="SELECT 1")

@task(retries=2)
def notify():
    requests.post("https://example.test/events", json={"ok": True})
"""
    result = PythonAnalyzer().analyze(source, SourceLocation("dag.py", 1))
    assert result.sql[0].task_id == "load"
    assert result.side_effects[0][2].kind == "http_post"
    assert not result.side_effects[0][2].idempotency_key


def test_malformed_python_does_not_raise() -> None:
    result = PythonAnalyzer().analyze("def broken(:", SourceLocation("broken.py", 1))
    assert result.diagnostics[0].code == "PY_PARSE_ERROR"


def test_python_operator_maps_callable_and_dag_default_retries() -> None:
    source = """
default_args = {"retries": 5}

def load_orders():
    warehouse.execute("SELECT 1")

with DAG("orders", default_args=default_args, max_active_runs=1):
    PythonOperator(task_id="load_orders_task", python_callable=load_orders)
"""
    result = PythonAnalyzer().analyze(source, SourceLocation("classic.py", 1))
    assert result.sql[0].task_id == "load_orders_task"
    assert result.sql[0].retries == 5
    assert result.sql[0].max_active_runs == 1


def test_python_transaction_wrapper_is_recorded() -> None:
    source = """
with connection.begin():
    warehouse.execute("DELETE FROM dst")
    warehouse.execute("INSERT INTO dst SELECT * FROM src")
"""
    result = PythonAnalyzer().analyze(source, SourceLocation("transaction.py", 1))
    assert len(result.sql) == 2
    assert result.sql[0].transaction_group == result.sql[1].transaction_group
    assert result.sql[0].transaction_group is not None


def test_airflow_context_can_be_disabled() -> None:
    source = """
@task(retries=3)
def load():
    warehouse.execute("SELECT 1")
"""
    result = PythonAnalyzer().analyze(source, SourceLocation("dag.py", 1), airflow_enabled=False)
    assert result.sql[0].task_id is None
    assert result.sql[0].retries is None
