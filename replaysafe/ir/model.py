"""Immutable semantic models consumed by ReplaySafe rules."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any

from replaysafe.ir.enums import Confidence, Severity, TimeDependencyKind, WriteMode


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """A precise, normalized source range."""

    file: str
    start_line: int
    start_col: int | None = None
    end_line: int | None = None
    end_col: int | None = None


@dataclass(frozen=True, slots=True)
class DataAsset:
    """A data source or destination known to the static analyzer."""

    name: str
    kind: str = "table"
    dialect: str | None = None


@dataclass(frozen=True, slots=True)
class Predicate:
    """A row-selection expression independent of the SQL parser AST."""

    expression: str
    context: str
    location: SourceLocation
    columns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Pagination:
    """Normalized LIMIT/OFFSET and ordering semantics."""

    limit: int | None
    offset: int | None
    order_by: tuple[str, ...]
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class TimeDependency:
    """A time expression and the semantic context where it is used."""

    kind: TimeDependencyKind
    expression: str
    location: SourceLocation
    context: str


@dataclass(frozen=True, slots=True)
class WindowSelection:
    """A window used to select a survivor or ordered row."""

    function: str
    partition_by: tuple[str, ...]
    order_by: tuple[str, ...]
    survivor_selection: bool
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class ReadOperation:
    """A read from a normalized data asset."""

    source: DataAsset
    predicates: tuple[Predicate, ...]
    pagination: Pagination | None
    location: SourceLocation
    statement_index: int = 0


@dataclass(frozen=True, slots=True)
class WriteOperation:
    """A normalized destination mutation."""

    target: DataAsset
    mode: WriteMode
    keys: tuple[str, ...]
    partition_keys: tuple[str, ...]
    transactional_group: str | None
    location: SourceLocation
    statement_index: int = 0
    conflict_handling: bool = False
    evidence: str = ""


type Operation = ReadOperation | WriteOperation


@dataclass(frozen=True, slots=True)
class TransactionGroup:
    """A sequence of statements proven to share an explicit transaction."""

    group_id: str
    statement_indexes: tuple[int, ...]
    explicit: bool
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class ExternalSideEffect:
    """A statically visible external mutation such as HTTP POST."""

    kind: str
    expression: str
    idempotency_key: bool
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class StatementSemantics:
    """Semantic facts extracted from one SQL statement."""

    index: int
    kind: str
    text: str
    location: SourceLocation
    predicates: tuple[Predicate, ...] = ()
    pagination: Pagination | None = None
    time_dependencies: tuple[TimeDependency, ...] = ()
    windows: tuple[WindowSelection, ...] = ()
    operations: tuple[Operation, ...] = ()
    transactional_group: str | None = None


@dataclass(frozen=True, slots=True)
class TaskSemantics:
    """Recovery-relevant facts for a statically inferred pipeline task."""

    task_id: str | None
    retries: int | None
    max_active_runs: int | None
    logical_time_symbols: tuple[str, ...]
    operations: tuple[Operation, ...]
    location: SourceLocation
    statements: tuple[StatementSemantics, ...] = ()
    time_dependencies: tuple[TimeDependency, ...] = ()
    windows: tuple[WindowSelection, ...] = ()
    external_effects: tuple[ExternalSideEffect, ...] = ()
    transaction_groups: tuple[TransactionGroup, ...] = ()


@dataclass(frozen=True, slots=True)
class PipelineModel:
    """Parser-independent model evaluated by the rule engine."""

    file: str
    tasks: tuple[TaskSemantics, ...]
    model_name: str | None = None
    dependencies: tuple[str, ...] = ()
    unique_key: tuple[str, ...] = ()
    materialization: str | None = None
    dbt_unique_id: str | None = None
    relation_name: str | None = None
    dependency_relations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Evidence:
    """Sanitized source evidence supporting a finding."""

    text: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class Finding:
    """A deterministic, explainable recovery-safety finding."""

    rule_id: str
    severity: Severity
    title: str
    message: str
    location: SourceLocation
    evidence: tuple[Evidence, ...]
    failure_scenario: tuple[str, ...]
    consequence: str
    remediation: tuple[str, ...]
    confidence: Confidence
    fingerprint: str


def to_plain_dict(value: Any) -> Any:
    """Recursively serialize IR values into JSON-compatible primitives."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_plain_dict(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple | list):
        return [to_plain_dict(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_plain_dict(item) for key, item in value.items()}
    return value
