"""Compose parser adapters into parser-independent pipeline models."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from replaysafe.diagnostics import Diagnostic
from replaysafe.ir import (
    ExternalSideEffect,
    PipelineModel,
    SourceLocation,
    StatementSemantics,
    TaskSemantics,
    TransactionGroup,
    WriteOperation,
)
from replaysafe.parsers import DbtNode, ExtractedSql, PythonAnalyzer, SqlAnalyzer


def _task_from_statements(
    *,
    task_id: str | None,
    retries: int | None,
    max_active_runs: int | None,
    logical_symbols: tuple[str, ...],
    location: SourceLocation,
    statements: tuple[StatementSemantics, ...],
    groups: tuple[TransactionGroup, ...],
    effects: tuple[ExternalSideEffect, ...] = (),
) -> TaskSemantics:
    operations = tuple(operation for statement in statements for operation in statement.operations)
    dependencies = tuple(item for statement in statements for item in statement.time_dependencies)
    windows = tuple(item for statement in statements for item in statement.windows)
    return TaskSemantics(
        task_id,
        retries,
        max_active_runs,
        logical_symbols,
        operations,
        location,
        statements,
        dependencies,
        windows,
        effects,
        groups,
    )


def build_sql_model(
    text: str,
    file: str,
    dialect: str,
    dbt_node: DbtNode | None = None,
) -> tuple[PipelineModel, tuple[Diagnostic, ...]]:
    """Build one standalone-SQL pipeline model."""

    location = SourceLocation(file, 1)
    parsed = SqlAnalyzer().analyze(text, dialect, location)
    task = _task_from_statements(
        task_id=dbt_node.name if dbt_node else None,
        retries=None,
        max_active_runs=None,
        logical_symbols=tuple(
            symbol
            for symbol in ("data_interval_start", "data_interval_end", "logical_date", "ds")
            if symbol in text
        ),
        location=location,
        statements=parsed.statements,
        groups=parsed.transaction_groups,
    )
    model = PipelineModel(
        file,
        (task,),
        dbt_node.name if dbt_node else None,
        dbt_node.dependencies if dbt_node else (),
        dbt_node.unique_key if dbt_node else (),
        dbt_node.materialization if dbt_node else None,
    )
    return model, parsed.diagnostics


def _reindex_statement(statement: StatementSemantics, index: int) -> StatementSemantics:
    operations = tuple(
        replace(operation, statement_index=index) for operation in statement.operations
    )
    return replace(statement, index=index, operations=operations)


def _force_transaction(statement: StatementSemantics, group: str) -> StatementSemantics:
    operations = tuple(
        replace(operation, transactional_group=group)
        if isinstance(operation, WriteOperation)
        else operation
        for operation in statement.operations
    )
    return replace(statement, operations=operations, transactional_group=group)


def build_python_model(
    text: str, file: str, dialect: str, *, airflow_enabled: bool = True
) -> tuple[PipelineModel, tuple[Diagnostic, ...]]:
    """Build statically inferred task models from a Python/Airflow file."""

    python_analysis = PythonAnalyzer().analyze(
        text, SourceLocation(file, 1), airflow_enabled=airflow_enabled
    )
    diagnostics: list[Diagnostic] = list(python_analysis.diagnostics)
    grouped_sql: dict[tuple[str | None, int | None, int | None], list[ExtractedSql]] = defaultdict(
        list
    )
    symbols: dict[tuple[str | None, int | None, int | None], set[str]] = defaultdict(set)
    locations: dict[tuple[str | None, int | None, int | None], SourceLocation] = {}
    for extracted in python_analysis.sql:
        key = (extracted.task_id, extracted.retries, extracted.max_active_runs)
        grouped_sql[key].append(extracted)
        symbols[key].update(extracted.logical_time_symbols)
        locations.setdefault(key, extracted.location)
    grouped_effects: dict[tuple[str | None, int | None, int | None], list[ExternalSideEffect]] = (
        defaultdict(list)
    )
    for task_id, retries, effect in python_analysis.side_effects:
        matching = next(
            (key for key in grouped_sql if key[0] == task_id and key[1] == retries),
            (task_id, retries, None),
        )
        grouped_effects[matching].append(effect)
        locations.setdefault(matching, effect.location)

    tasks: list[TaskSemantics] = []
    all_keys = sorted(
        set(grouped_sql) | set(grouped_effects),
        key=lambda item: (item[0] or "", item[1] if item[1] is not None else -1),
    )
    for key in all_keys:
        statements: list[StatementSemantics] = []
        groups: list[TransactionGroup] = []
        wrapper_groups: dict[str, list[int]] = defaultdict(list)
        next_index = 0
        for extracted in grouped_sql.get(key, []):
            parsed = SqlAnalyzer().analyze(
                extracted.text,
                dialect,
                extracted.location,
                index_offset=next_index,
            )
            diagnostics.extend(parsed.diagnostics)
            extracted_statements = parsed.statements
            if extracted.transaction_group:
                extracted_statements = tuple(
                    _force_transaction(item, extracted.transaction_group)
                    for item in extracted_statements
                )
                wrapper_groups[extracted.transaction_group].extend(
                    item.index for item in extracted_statements
                )
            statements.extend(extracted_statements)
            groups.extend(parsed.transaction_groups)
            if parsed.statements:
                next_index = max(item.index for item in parsed.statements) + 1
        normalized = tuple(_reindex_statement(item, index) for index, item in enumerate(statements))
        groups.extend(
            TransactionGroup(group_id, tuple(indexes), True, locations[key])
            for group_id, indexes in sorted(wrapper_groups.items())
        )
        tasks.append(
            _task_from_statements(
                task_id=key[0],
                retries=key[1],
                max_active_runs=key[2],
                logical_symbols=tuple(sorted(symbols.get(key, set()))),
                location=locations[key],
                statements=normalized,
                groups=tuple(groups),
                effects=tuple(grouped_effects.get(key, [])),
            )
        )
    return PipelineModel(file, tuple(tasks)), tuple(diagnostics)
