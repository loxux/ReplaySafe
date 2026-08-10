import json
from pathlib import Path

from replaysafe.parsers.dbt import load_manifest


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
                        "config": {"materialized": "incremental", "unique_key": "id"},
                        "depends_on": {"nodes": ["source.demo.raw_orders"]},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    node = load_manifest(path).nodes_by_path["models/orders.sql"]
    assert node.materialization == "incremental"
    assert node.unique_key == ("id",)


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
