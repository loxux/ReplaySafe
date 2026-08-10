"""Read-only dbt manifest enrichment with schema-variation guards."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from replaysafe.diagnostics import Diagnostic
from replaysafe.ir import Severity, SourceLocation


@dataclass(frozen=True, slots=True)
class DbtNode:
    """Recovery-relevant dbt model metadata."""

    unique_id: str
    name: str
    original_file_path: str
    materialization: str | None
    unique_key: tuple[str, ...]
    dependencies: tuple[str, ...]
    compiled_sql: str | None = None
    relation_name: str | None = None
    incremental_strategy: str | None = None
    dependency_relations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DbtManifest:
    """A normalized lookup of dbt nodes by repository path."""

    nodes_by_path: dict[str, DbtNode]
    diagnostics: tuple[Diagnostic, ...] = ()
    relations_by_unique_id: dict[str, str] = field(default_factory=dict)


def _tuple_string(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _relation_name(payload: dict[str, Any]) -> str | None:
    identifier = (
        _optional_string(payload.get("alias"))
        or _optional_string(payload.get("identifier"))
        or _optional_string(payload.get("name"))
    )
    parts = tuple(
        item
        for item in (
            _optional_string(payload.get("database")),
            _optional_string(payload.get("schema")),
            identifier,
        )
        if item
    )
    if parts:
        return ".".join(parts)
    raw = _optional_string(payload.get("relation_name"))
    if raw is None:
        return None
    return ".".join(part.strip().strip('`"[]') for part in raw.split("."))


def _payload_mapping(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def load_manifest(path: Path) -> DbtManifest:
    """Load a dbt manifest without invoking dbt or evaluating project code."""

    location = SourceLocation(path.as_posix(), 1)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return DbtManifest(
            {},
            (
                Diagnostic(
                    "DBT_MANIFEST_ERROR",
                    f"Could not read dbt manifest: {error}",
                    location,
                    Severity.MEDIUM,
                ),
            ),
        )
    nodes = _payload_mapping(raw.get("nodes")) if isinstance(raw, dict) else {}
    sources = _payload_mapping(raw.get("sources")) if isinstance(raw, dict) else {}
    relations = {
        unique_id: relation
        for unique_id, payload in {**nodes, **sources}.items()
        if (relation := _relation_name(payload)) is not None
    }
    normalized: dict[str, DbtNode] = {}
    for unique_id, payload in nodes.items():
        if payload.get("resource_type") not in {None, "model"}:
            continue
        original = payload.get("original_file_path") or payload.get("path")
        if not isinstance(original, str):
            continue
        raw_config = payload.get("config")
        config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
        depends_on = payload.get("depends_on")
        dependency_nodes = depends_on.get("nodes", []) if isinstance(depends_on, dict) else []
        dependencies = tuple(str(item) for item in dependency_nodes if isinstance(item, str))
        compiled_sql = _optional_string(payload.get("compiled_code")) or _optional_string(
            payload.get("compiled_sql")
        )
        node = DbtNode(
            str(unique_id),
            str(payload.get("name") or unique_id),
            original.replace("\\", "/"),
            str(config.get("materialized")) if config.get("materialized") else None,
            _tuple_string(config.get("unique_key")),
            dependencies,
            compiled_sql,
            relations.get(str(unique_id)),
            _optional_string(config.get("incremental_strategy")),
            tuple(relations.get(item, item) for item in dependencies),
        )
        normalized[node.original_file_path] = node
    return DbtManifest(normalized, (), relations)
