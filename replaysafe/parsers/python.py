"""Safe static Python/Airflow extraction using only the standard AST."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from replaysafe.diagnostics import Diagnostic
from replaysafe.ir import ExternalSideEffect, Severity, SourceLocation

_SQL_METHODS = frozenset({"execute", "executemany", "query", "run", "get_records", "get_first"})
_SQL_OPERATOR_NAMES = frozenset(
    {
        "SQLExecuteQueryOperator",
        "PostgresOperator",
        "SnowflakeOperator",
        "BigQueryInsertJobOperator",
        "MySqlOperator",
    }
)
_LOGICAL_SYMBOLS = ("data_interval_start", "data_interval_end", "logical_date", "ds")


@dataclass(frozen=True, slots=True)
class ExtractedSql:
    """A SQL string and its statically inferred task context."""

    text: str
    location: SourceLocation
    task_id: str | None
    retries: int | None
    max_active_runs: int | None
    logical_time_symbols: tuple[str, ...]
    transaction_group: str | None = None


@dataclass(frozen=True, slots=True)
class PythonAnalysis:
    """Python extraction output that never requires importing scanned code."""

    sql: tuple[ExtractedSql, ...]
    side_effects: tuple[tuple[str | None, int | None, ExternalSideEffect], ...]
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True, slots=True)
class _TaskContext:
    task_id: str | None = None
    retries: int | None = None


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _literal_int(node: ast.AST | None) -> int | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, int) else None


def _literal_str(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _static_string(
    node: ast.AST,
    variables: dict[str, tuple[str, int]],
    dynamic_values: dict[str, str] | None = None,
) -> tuple[str, int] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, node.lineno
    if isinstance(node, ast.Name):
        return variables.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                expression = ast.unparse(value.value)
                if isinstance(value.value, ast.Name) and dynamic_values:
                    expression = dynamic_values.get(value.value.id, expression)
                parts.append("{" + expression + "}")
            else:
                return None
        return "".join(parts), node.lineno
    return None


def _decorator_task(node: ast.FunctionDef | ast.AsyncFunctionDef) -> _TaskContext | None:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if _call_name(target).split(".")[-1] != "task":
            continue
        task_id = node.name
        retries: int | None = None
        if isinstance(decorator, ast.Call):
            for keyword in decorator.keywords:
                if keyword.arg == "task_id":
                    task_id = _literal_str(keyword.value) or task_id
                elif keyword.arg == "retries":
                    retries = _literal_int(keyword.value)
        return _TaskContext(task_id, retries)
    return None


def _static_dicts(tree: ast.AST) -> dict[str, ast.Dict]:
    result: dict[str, ast.Dict] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                result[target.id] = node.value
    return result


def _dict_int(node: ast.AST | None, key: str) -> int | None:
    if not isinstance(node, ast.Dict):
        return None
    for raw_key, value in zip(node.keys, node.values, strict=True):
        if _literal_str(raw_key) == key:
            return _literal_int(value)
    return None


def _dag_defaults(tree: ast.AST) -> tuple[int | None, int | None]:
    static_dicts = _static_dicts(tree)
    max_active_runs: int | None = None
    default_retries: int | None = None
    for node in ast.walk(tree):
        call: ast.Call | None = None
        if isinstance(node, ast.Call) and _call_name(node.func).split(".")[-1] in {"dag", "DAG"}:
            call = node
        if call is not None:
            for keyword in call.keywords:
                if keyword.arg == "max_active_runs":
                    value = _literal_int(keyword.value)
                    if value is not None:
                        max_active_runs = value
                elif keyword.arg == "default_args":
                    default_args = keyword.value
                    if isinstance(default_args, ast.Name):
                        default_args = static_dicts.get(default_args.id, default_args)
                    retries = _dict_int(default_args, "retries")
                    if retries is not None:
                        default_retries = retries
    return max_active_runs, default_retries


def _python_operator_tasks(tree: ast.AST, default_retries: int | None) -> dict[str, _TaskContext]:
    result: dict[str, _TaskContext] = {}
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or _call_name(node.func).split(".")[-1] != "PythonOperator"
        ):
            continue
        task_id: str | None = None
        retries = default_retries
        callable_name: str | None = None
        for keyword in node.keywords:
            if keyword.arg == "task_id":
                task_id = _literal_str(keyword.value)
            elif keyword.arg == "retries":
                retries = _literal_int(keyword.value)
            elif keyword.arg == "python_callable" and isinstance(keyword.value, ast.Name):
                callable_name = keyword.value.id
        if callable_name:
            result[callable_name] = _TaskContext(task_id or callable_name, retries)
    return result


class _Visitor(ast.NodeVisitor):
    def __init__(
        self,
        file: str,
        max_active_runs: int | None,
        default_retries: int | None,
        operator_tasks: dict[str, _TaskContext],
        airflow_enabled: bool,
    ) -> None:
        self.file = file
        self.max_active_runs = max_active_runs
        self.default_retries = default_retries
        self.operator_tasks = operator_tasks
        self.airflow_enabled = airflow_enabled
        self.variables: list[dict[str, tuple[str, int]]] = [{}]
        self.dynamic_values: list[dict[str, str]] = [{}]
        self.tasks: list[_TaskContext] = [_TaskContext(retries=default_retries)]
        self.transaction_groups: list[str | None] = [None]
        self.sql: list[ExtractedSql] = []
        self.side_effects: list[tuple[str | None, int | None, ExternalSideEffect]] = []
        self.diagnostics: list[Diagnostic] = []

    @property
    def variables_now(self) -> dict[str, tuple[str, int]]:
        merged: dict[str, tuple[str, int]] = {}
        for scope in self.variables:
            merged.update(scope)
        return merged

    @property
    def dynamic_values_now(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for scope in self.dynamic_values:
            merged.update(scope)
        return merged

    def _location(self, node: ast.AST, line: int | None = None) -> SourceLocation:
        line_number = line if isinstance(line, int) else int(getattr(node, "lineno", 1) or 1)
        return SourceLocation(
            self.file,
            line_number,
            (getattr(node, "col_offset", 0) + 1) if hasattr(node, "col_offset") else None,
            getattr(node, "end_lineno", None),
            getattr(node, "end_col_offset", None),
        )

    def _add_sql(self, value: tuple[str, int], node: ast.AST, task: _TaskContext) -> None:
        text, line = value
        symbols = tuple(symbol for symbol in _LOGICAL_SYMBOLS if re.search(rf"\b{symbol}\b", text))
        self.sql.append(
            ExtractedSql(
                text,
                self._location(node, line),
                task.task_id,
                task.retries,
                self.max_active_runs,
                symbols,
                self.transaction_groups[-1],
            )
        )

    def visit_Assign(self, node: ast.Assign) -> None:
        value = _static_string(node.value, self.variables_now, self.dynamic_values_now)
        if value is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.variables[-1][target.id] = value
        if isinstance(node.value, ast.Call):
            call = _call_name(node.value.func).lower()
            if call in {"datetime.now", "date.today", "pendulum.now", "time.time"}:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.dynamic_values[-1][target.id] = f"{call}()"
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None and isinstance(node.target, ast.Name):
            value = _static_string(node.value, self.variables_now, self.dynamic_values_now)
            if value is not None:
                self.variables[-1][node.target.id] = value
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.variables.append({})
        self.dynamic_values.append({})
        inferred = (
            (_decorator_task(node) or self.operator_tasks.get(node.name))
            if self.airflow_enabled
            else None
        )
        if inferred is None:
            inferred = _TaskContext(retries=self.default_retries)
        elif inferred.retries is None:
            inferred = _TaskContext(inferred.task_id, self.default_retries)
        self.tasks.append(inferred)
        for statement in node.body:
            self.visit(statement)
        self.tasks.pop()
        self.dynamic_values.pop()
        self.variables.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        expressions = " ".join(ast.unparse(item.context_expr) for item in node.items)
        explicit = bool(re.search(r"\b(?:begin|transaction|atomic)\s*\(", expressions))
        inherited = self.transaction_groups[-1]
        self.transaction_groups.append(f"py-tx-{node.lineno}" if explicit else inherited)
        for statement in node.body:
            self.visit(statement)
        self.transaction_groups.pop()

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        short_name = name.split(".")[-1]
        task = self.tasks[-1]
        if short_name in _SQL_METHODS and node.args:
            value = _static_string(node.args[0], self.variables_now, self.dynamic_values_now)
            if value is not None:
                self._add_sql(value, node, task)
            else:
                self.diagnostics.append(
                    Diagnostic(
                        "PY_DYNAMIC_SQL",
                        "SQL passed to a known execution method is dynamic and was not analyzed.",
                        self._location(node.args[0]),
                        Severity.LOW,
                    )
                )
        elif self.airflow_enabled and short_name in _SQL_OPERATOR_NAMES:
            task_id = task.task_id
            retries = task.retries
            for keyword in node.keywords:
                if keyword.arg == "task_id":
                    task_id = _literal_str(keyword.value) or task_id
                elif keyword.arg == "retries":
                    retries = _literal_int(keyword.value)
            operator_task = _TaskContext(task_id, retries)
            for keyword in node.keywords:
                if keyword.arg in {"sql", "configuration"}:
                    value = _static_string(
                        keyword.value, self.variables_now, self.dynamic_values_now
                    )
                    if value is not None:
                        self._add_sql(value, keyword.value, operator_task)
                    else:
                        self.diagnostics.append(
                            Diagnostic(
                                "PY_DYNAMIC_SQL",
                                "Airflow operator SQL is dynamic and was not analyzed.",
                                self._location(keyword.value),
                                Severity.LOW,
                            )
                        )
        is_post = short_name.lower() == "post" or (
            short_name.lower() == "request"
            and any(
                keyword.arg == "method" and (_literal_str(keyword.value) or "").upper() == "POST"
                for keyword in node.keywords
            )
        )
        if is_post:
            expression = ast.unparse(node)
            has_key = bool(re.search(r"idempotenc(?:y|e)[-_ ]?key", expression, re.IGNORECASE))
            self.side_effects.append(
                (
                    task.task_id,
                    task.retries,
                    ExternalSideEffect(
                        "http_post", expression[:500], has_key, self._location(node)
                    ),
                )
            )
        self.generic_visit(node)


class PythonAnalyzer:
    """Extract SQL and Airflow task context without importing scanned modules."""

    def analyze(
        self, source: str, context: SourceLocation, *, airflow_enabled: bool = True
    ) -> PythonAnalysis:
        """Parse a Python file, returning a diagnostic instead of raising on syntax errors."""

        try:
            tree = ast.parse(source, filename=context.file)
        except (SyntaxError, ValueError) as error:
            line = getattr(error, "lineno", None) or context.start_line
            return PythonAnalysis(
                (),
                (),
                (
                    Diagnostic(
                        "PY_PARSE_ERROR",
                        f"Could not parse Python: {getattr(error, 'msg', str(error))}",
                        SourceLocation(context.file, line),
                        Severity.MEDIUM,
                    ),
                ),
            )
        max_active_runs, default_retries = _dag_defaults(tree) if airflow_enabled else (None, None)
        visitor = _Visitor(
            context.file,
            max_active_runs,
            default_retries,
            _python_operator_tasks(tree, default_retries) if airflow_enabled else {},
            airflow_enabled,
        )
        visitor.visit(tree)
        unique_sql = tuple(
            dict.fromkeys(
                (item.location.start_line, item.text, item.task_id, item.transaction_group)
                for item in visitor.sql
            )
        )
        lookup = {
            (item.location.start_line, item.text, item.task_id, item.transaction_group): item
            for item in visitor.sql
        }
        return PythonAnalysis(
            tuple(lookup[key] for key in unique_sql),
            tuple(visitor.side_effects),
            tuple(visitor.diagnostics),
        )
