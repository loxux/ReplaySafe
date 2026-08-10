"""Read-only dbt manifest enrichment with schema-variation guards."""

from __future__ import annotations

import json
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class DbtManifest:
    """A normalized lookup of dbt nodes by repository path."""

    nodes_by_path: dict[str, DbtNode]
    diagnostics: tuple[Diagnostic, ...] = ()


def _tuple_string(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


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
    nodes = raw.get("nodes", {}) if isinstance(raw, dict) else {}
    if not isinstance(nodes, dict):
        nodes = {}
    normalized: dict[str, DbtNode] = {}
    for unique_id, payload in nodes.items():
        if not isinstance(payload, dict) or payload.get("resource_type") not in {None, "model"}:
            continue
        original = payload.get("original_file_path") or payload.get("path")
        if not isinstance(original, str):
            continue
        raw_config = payload.get("config")
        config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
        depends_on = payload.get("depends_on")
        dependency_nodes = depends_on.get("nodes", []) if isinstance(depends_on, dict) else []
        node = DbtNode(
            str(unique_id),
            str(payload.get("name") or unique_id),
            original.replace("\\", "/"),
            str(config.get("materialized")) if config.get("materialized") else None,
            _tuple_string(config.get("unique_key")),
            tuple(str(item) for item in dependency_nodes if isinstance(item, str)),
        )
        normalized[node.original_file_path] = node
    return DbtManifest(normalized)
