"""Shared semantic relationships used by more than one recovery rule."""

from __future__ import annotations

import re

from replaysafe.ir import Predicate, TaskSemantics, WriteOperation


def _predicates(task: TaskSemantics, statement_index: int) -> tuple[Predicate, ...]:
    statement = next((item for item in task.statements if item.index == statement_index), None)
    return statement.predicates if statement else ()


def _normalized(expression: str) -> str:
    without_qualifiers = re.sub(r"\b[A-Za-z_]\w*\.", "", expression)
    return " ".join(without_qualifiers.lower().split())


def is_replacement_pair(
    task: TaskSemantics, delete: WriteOperation, insert: WriteOperation
) -> bool:
    """Return whether ordered same-target writes visibly replace the same range."""

    if delete.target.name != insert.target.name or delete.statement_index >= insert.statement_index:
        return False
    delete_predicates = _predicates(task, delete.statement_index)
    insert_predicates = _predicates(task, insert.statement_index)
    if not delete_predicates:
        return True
    if not insert_predicates:
        return False
    deleted_ranges = {_normalized(item.expression) for item in delete_predicates}
    inserted_ranges = {_normalized(item.expression) for item in insert_predicates}
    return bool(deleted_ranges & inserted_ranges)
