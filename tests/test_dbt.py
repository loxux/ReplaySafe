import json
from pathlib import Path

from replaysafe.analysis import scan_repository
from replaysafe.analysis.pipeline_builder import build_sql_model
from replaysafe.ir import WriteMode, WriteOperation
from replaysafe.parsers.dbt import DbtNode, load_manifest


def test_manifest_current_shape(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.demo.orders": {
                        "resource_type": "model",
                        "name": "orders",
                        "original_file_path": "models/orders.sql",
                        "database": "warehouse",
                        "schema": "analytics",
                        "alias": "orders",
                        "compiled_code": "select * from raw.orders",
                        "config": {
                            "materialized": "incremental",
                            "unique_key": "id",
                            "incremental_strategy": "merge",
                        },
                        "depends_on": {"nodes": ["source.demo.raw_orders"]},
                    }
                },
                "sources": {
                    "source.demo.raw_orders": {
                        "resource_type": "source",
                        "source_name": "raw",
                        "name": "raw_orders",
                        "database": "warehouse",
                        "schema": "raw",
                        "identifier": "orders",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    node = load_manifest(path).nodes_by_path["models/orders.sql"]
    assert node.materialization == "incremental"
    assert node.unique_key == ("id",)
    assert node.incremental_strategy == "merge"
    assert node.compiled_sql == "select * from raw.orders"
    assert node.relation_name == "warehouse.analytics.orders"
    assert node.dependency_relations == ("warehouse.raw.orders",)


def test_manifest_legacy_path_and_list_unique_key(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.demo.orders": {
                        "name": "orders",
                        "path": "models/orders.sql",
                        "config": {"unique_key": ["tenant_id", "id"]},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    node = load_manifest(path).nodes_by_path["models/orders.sql"]
    assert node.unique_key == ("tenant_id", "id")


def test_malformed_manifest_is_diagnostic(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("{", encoding="utf-8")
    assert load_manifest(path).diagnostics[0].code == "DBT_MANIFEST_ERROR"


def test_scan_auto_detects_manifest_and_models_incremental_write(tmp_path: Path) -> None:
    models = tmp_path / "models"
    target = tmp_path / "target"
    models.mkdir()
    target.mkdir()
    (models / "orders.sql").write_text("{{ custom_model(ref('raw_orders')) }}", encoding="utf-8")
    (target / "ignored.sql").write_text("INSERT INTO duplicate SELECT 1", encoding="utf-8")
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "nodes": {
                    "model.demo.orders": {
                        "resource_type": "model",
                        "name": "orders",
                        "original_file_path": "models/orders.sql",
                        "database": "warehouse",
                        "schema": "analytics",
                        "alias": "orders",
                        "compiled_code": "SELECT * FROM warehouse.raw.orders",
                        "config": {"materialized": "incremental"},
                        "depends_on": {"nodes": ["source.demo.raw_orders"]},
                    }
                },
                "sources": {
                    "source.demo.raw_orders": {
                        "resource_type": "source",
                        "database": "warehouse",
                        "schema": "raw",
                        "identifier": "orders",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = scan_repository(tmp_path)

    assert result.files == ("models/orders.sql",)
    model = result.models[0]
    assert model.dbt_unique_id == "model.demo.orders"
    assert model.relation_name == "warehouse.analytics.orders"
    assert model.dependency_relations == ("warehouse.raw.orders",)
    writes = tuple(
        operation
        for operation in model.tasks[0].operations
        if isinstance(operation, WriteOperation)
    )
    assert writes[0].mode == WriteMode.APPEND
    assert any(finding.rule_id == "RS002" for finding in result.findings)


def test_dbt_compiled_sql_and_incremental_unique_key_improve_precision(tmp_path: Path) -> None:
    models = tmp_path / "models"
    target = tmp_path / "target"
    models.mkdir()
    target.mkdir()
    (models / "events.sql").write_text(
        "{{ custom_model(ref('events'), var('predicate')) }}", encoding="utf-8"
    )
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "nodes": {
                    "model.demo.events": {
                        "resource_type": "model",
                        "name": "events",
                        "original_file_path": "models/events.sql",
                        "schema": "analytics",
                        "alias": "events",
                        "compiled_sql": (
                            "SELECT * FROM raw.events WHERE event_date = CURRENT_DATE"
                        ),
                        "config": {
                            "materialized": "incremental",
                            "unique_key": ["tenant_id", "event_id"],
                            "incremental_strategy": "merge",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = scan_repository(tmp_path)

    assert any(finding.rule_id == "RS001" for finding in result.findings)
    assert not any(finding.rule_id == "RS002" for finding in result.findings)
    write = next(
        operation
        for operation in result.models[0].tasks[0].operations
        if isinstance(operation, WriteOperation)
    )
    assert write.mode == WriteMode.UPSERT
    assert write.keys == ("tenant_id", "event_id")


def test_dbt_synthetic_write_does_not_duplicate_explicit_dml() -> None:
    node = DbtNode(
        "model.demo.orders",
        "orders",
        "models/orders.sql",
        "incremental",
        (),
        (),
        relation_name="analytics.orders",
    )
    model, diagnostics = build_sql_model(
        "INSERT INTO analytics.orders SELECT * FROM raw.orders",
        "models/orders.sql",
        "auto",
        node,
    )
    writes = tuple(
        operation
        for operation in model.tasks[0].operations
        if isinstance(operation, WriteOperation)
    )
    assert not diagnostics
    assert len(writes) == 1
