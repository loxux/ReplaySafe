"""SQLGlot-backed extraction into ReplaySafe's semantic IR."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import cast

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError

from replaysafe.diagnostics import Diagnostic
from replaysafe.ir import (
    AssetDefinition,
    DataAsset,
    Pagination,
    Predicate,
    ReadOperation,
    Severity,
    SourceLocation,
    StatementSemantics,
    TimeDependency,
    TimeDependencyKind,
    TransactionGroup,
    WindowSelection,
    WriteMode,
    WriteOperation,
)

_WALL_CLOCK = re.compile(
    r"\b(?:CURRENT_DATE|CURRENT_TIMESTAMP|CURRENT_DATETIME|NOW\s*\(|SYSDATE\b|"
    r"GETDATE\s*\(|LOCALTIMESTAMP\b|DATETIME\.NOW\s*\(|DATE\.TODAY\s*\(|"
    r"PENDULUM\.NOW\s*\(|TIME\.TIME\s*\()",
    re.IGNORECASE,
)
_SURVIVOR_TEMPLATE = r"(?:\b{alias}\b\s*=\s*1|1\s*=\s*\b{alias}\b)"
_SUPPORTED_DIALECTS = frozenset({"auto", "postgres", "snowflake", "bigquery", "starrocks"})
_JINJA_EXPRESSION = re.compile(r"\{\{(?P<body>.*?)\}\}", re.DOTALL)
_JINJA_BLOCK = re.compile(r"\{%.*?%\}", re.DOTALL)
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
_PYTHON_PLACEHOLDER = re.compile(r"\{[^{}]+\}")


@dataclass(frozen=True, slots=True)
class SqlAnalysis:
    """Semantic SQL statements plus recoverable parser diagnostics."""

    statements: tuple[StatementSemantics, ...]
    transaction_groups: tuple[TransactionGroup, ...]
    diagnostics: tuple[Diagnostic, ...]
    asset_definitions: tuple[AssetDefinition, ...] = ()


def _split_statements(sql: str) -> list[tuple[str, int]]:
    """Split on top-level semicolons while preserving starting line numbers."""

    result: list[tuple[str, int]] = []
    start = 0
    start_line = 1
    line = 1
    quote: str | None = None
    line_comment = False
    block_comment = False
    dollar_quote: str | None = None
    index = 0
    while index < len(sql):
        char = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if char == "\n":
            line += 1
            line_comment = False
        if line_comment:
            index += 1
            continue
        if dollar_quote:
            if sql.startswith(dollar_quote, index):
                index += len(dollar_quote)
                dollar_quote = None
            else:
                index += 1
            continue
        if block_comment:
            if char == "*" and following == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            if char == quote:
                if following == quote and quote in {"'", '"'}:
                    index += 2
                    continue
                quote = None
            elif char == "\\" and following:
                index += 2
                continue
            index += 1
            continue
        if char == "-" and following == "-":
            line_comment = True
            index += 2
            continue
        if char == "/" and following == "*":
            block_comment = True
            index += 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "$":
            delimiter = re.match(r"\$[A-Za-z_0-9]*\$", sql[index:])
            if delimiter:
                dollar_quote = delimiter.group(0)
                index += len(dollar_quote)
                continue
        elif char == ";":
            fragment = sql[start:index].strip()
            if fragment:
                leading = sql[start:index].find(fragment)
                fragment_line = start_line + sql[start : start + max(leading, 0)].count("\n")
                result.append((fragment, fragment_line))
            start = index + 1
            start_line = line
        index += 1
    fragment = sql[start:].strip()
    if fragment:
        leading = sql[start:].find(fragment)
        fragment_line = start_line + sql[start : start + max(leading, 0)].count("\n")
        result.append((fragment, fragment_line))
    return result


def _node_location(
    node: exp.Expression, context: SourceLocation, statement_line: int
) -> SourceLocation:
    relative_line = int(node.meta.get("line") or 1)
    column = node.meta.get("col")
    absolute_line = context.start_line + statement_line + relative_line - 2
    return SourceLocation(context.file, absolute_line, int(column) if column else None)


def _fragment_location(
    text: str,
    fragment: str,
    context: SourceLocation,
    statement_line: int,
    fallback: exp.Expression,
) -> SourceLocation:
    """Locate normalized SQL evidence in its original statement when SQLGlot lacks metadata."""

    match = re.search(re.escape(fragment), text, re.IGNORECASE)
    if match is None:
        return _node_location(fallback, context, statement_line)
    prefix = text[: match.start()]
    line = context.start_line + statement_line + prefix.count("\n") - 1
    column = match.start() - prefix.rfind("\n")
    return SourceLocation(context.file, line, column)


def _table_name(table: exp.Table) -> str:
    parts: list[str] = []
    for key in ("catalog", "db", "name"):
        value = getattr(table, key, None)
        if value:
            parts.append(str(value))
    return ".".join(parts).lower()


def _asset(table: exp.Table, dialect: str) -> DataAsset:
    return DataAsset(_table_name(table), "table", None if dialect == "auto" else dialect)


def _same_asset(left: str, right: str) -> bool:
    """Match exact names, or an unqualified name against a qualified one."""

    if left == right:
        return True
    return ("." not in left or "." not in right) and left.rsplit(".", 1)[-1] == right.rsplit(
        ".", 1
    )[-1]


def _table_definition(
    statement: exp.Expression,
    text: str,
    dialect: str,
    location: SourceLocation,
) -> AssetDefinition | None:
    """Extract StarRocks Primary Key Model semantics from CREATE TABLE."""

    if (
        not isinstance(statement, exp.Create)
        or str(statement.args.get("kind", "")).upper() != "TABLE"
    ):
        return None
    starrocks_ddl = dialect == "starrocks" or bool(
        re.search(
            r"\bPRIMARY\s+KEY\s*\([^)]*\).*\bDISTRIBUTED\s+BY\b", text, re.IGNORECASE | re.DOTALL
        )
    )
    if not starrocks_ddl:
        return None
    primary_key = statement.find(exp.PrimaryKey)
    if primary_key is None:
        return None
    target = (
        statement.this if isinstance(statement.this, exp.Table) else statement.this.find(exp.Table)
    )
    if target is None:
        return None
    keys = tuple(
        str(getattr(item, "name", None) or item.sql(dialect=None)).lower()
        for item in primary_key.expressions
    )
    if not keys:
        return None
    return AssetDefinition(_asset(target, dialect), keys, WriteMode.UPSERT, location)


def _integer(expression: exp.Expression | None) -> int | None:
    if expression is None:
        return None
    raw = getattr(expression, "name", None) or expression.sql()
    try:
        return int(str(raw))
    except ValueError:
        return -1


def _statement_kind(statement: exp.Expression, text: str) -> str:
    upper = text.lstrip().upper()
    for prefix, kind in (
        ("START TRANSACTION", "begin"),
        ("BEGIN", "begin"),
        ("COMMIT", "commit"),
        ("ROLLBACK", "rollback"),
    ):
        if upper.startswith(prefix):
            return kind
    return str(getattr(statement, "key", statement.__class__.__name__)).lower()


def _parse(text: str, dialect: str) -> exp.Expression:
    if dialect not in _SUPPORTED_DIALECTS:
        raise ValueError(
            f"Unsupported dialect '{dialect}'. Choose auto, postgres, snowflake, bigquery, or starrocks."
        )
    candidates: tuple[str | None, ...] = (
        (dialect,)
        if dialect != "auto"
        else (None, "postgres", "snowflake", "bigquery", "starrocks")
    )
    first_error: SqlglotError | None = None
    for candidate in candidates:
        try:
            return cast(exp.Expression, parse_one(text, read=candidate))
        except SqlglotError as error:
            first_error = first_error or error

    def replace_jinja(match: re.Match[str]) -> str:
        body = match.group("body").strip().lower()
        if body.startswith("config("):
            return ""
        if body.startswith(("ref(", "source(")):
            return "__replaysafe_relation"
        return "__replaysafe_parameter"

    templated = _JINJA_COMMENT.sub(" ", text)
    templated = _JINJA_BLOCK.sub(" ", _JINJA_EXPRESSION.sub(replace_jinja, templated))
    templated = _PYTHON_PLACEHOLDER.sub("__replaysafe_parameter", templated)
    if templated != text:
        for candidate in candidates:
            try:
                return cast(exp.Expression, parse_one(templated, read=candidate))
            except SqlglotError:
                pass
    assert first_error is not None
    raise first_error


def _predicates(
    statement: exp.Expression,
    text: str,
    context: SourceLocation,
    statement_line: int,
    dialect: str,
) -> tuple[Predicate, ...]:
    found: list[Predicate] = []
    nodes: list[tuple[str, exp.Expression]] = []
    for where in statement.find_all(exp.Where):
        nodes.append(("where", where.this))
    for having in statement.find_all(exp.Having):
        nodes.append(("having", having.this))
    for qualify in statement.find_all(exp.Qualify):
        nodes.append(("qualify", qualify.this))
    for join in statement.find_all(exp.Join):
        on = join.args.get("on")
        if isinstance(on, exp.Expression):
            nodes.append(("join", on))
    for kind, node in nodes:
        columns = tuple(sorted({column.sql(dialect=None) for column in node.find_all(exp.Column)}))
        expression = node.sql(dialect=None)
        found.append(
            Predicate(
                expression,
                kind,
                _fragment_location(text, expression, context, statement_line, node),
                columns,
            )
        )
    return tuple(found)


def _pagination(
    statement: exp.Expression, text: str, context: SourceLocation, statement_line: int
) -> Pagination | None:
    limit = statement.find(exp.Limit)
    offset = statement.find(exp.Offset)
    order = statement.find(exp.Order)
    if limit is None and offset is None:
        return None
    limit_value = _integer(limit.args.get("expression")) if limit is not None else None
    offset_value = _integer(offset.args.get("expression")) if offset is not None else None
    ordered = tuple(item.sql(dialect=None) for item in order.expressions) if order else ()
    node = offset or limit
    assert node is not None
    keyword = "OFFSET" if offset is not None else "LIMIT"
    return Pagination(
        limit_value,
        offset_value,
        ordered,
        _fragment_location(text, keyword, context, statement_line, node),
    )


def _windows(
    statement: exp.Expression,
    text: str,
    predicates: tuple[Predicate, ...],
    context: SourceLocation,
    statement_line: int,
) -> tuple[WindowSelection, ...]:
    result: list[WindowSelection] = []
    predicate_text = " ".join(item.expression for item in predicates)
    for window in statement.find_all(exp.Window):
        function = window.this
        name = str(getattr(function, "key", function.__class__.__name__)).upper()
        if name not in {"ROWNUMBER", "ROW_NUMBER", "RANK"}:
            continue
        partition = tuple(item.sql(dialect=None) for item in window.args.get("partition_by") or ())
        order = window.args.get("order")
        order_by = tuple(item.sql(dialect=None) for item in order.expressions) if order else ()
        parent = window.parent
        alias = parent.alias if isinstance(parent, exp.Alias) else ""
        survivor = bool(
            alias
            and re.search(
                _SURVIVOR_TEMPLATE.format(alias=re.escape(alias)), predicate_text, re.IGNORECASE
            )
        )
        if not survivor and any(item.context == "qualify" for item in predicates):
            survivor = "= 1" in predicate_text and window.sql(dialect=None) in predicate_text
        result.append(
            WindowSelection(
                name.replace("ROWNUMBER", "ROW_NUMBER"),
                partition,
                order_by,
                survivor,
                _fragment_location(text, window.sql(dialect=None), context, statement_line, window),
            )
        )
    return tuple(result)


def _time_dependencies(
    predicates: tuple[Predicate, ...],
    text: str,
    context: SourceLocation,
    statement_line: int,
) -> tuple[TimeDependency, ...]:
    dependencies: list[TimeDependency] = []
    for predicate in predicates:
        for match in _WALL_CLOCK.finditer(predicate.expression):
            column = (
                predicate.location.start_col + match.start()
                if predicate.location.start_col is not None
                else None
            )
            dependencies.append(
                TimeDependency(
                    TimeDependencyKind.WALL_CLOCK,
                    match.group(0).rstrip("("),
                    SourceLocation(predicate.location.file, predicate.location.start_line, column),
                    predicate.context,
                )
            )
    known = {(item.expression.upper(), item.location.start_line) for item in dependencies}
    clause_pattern = re.compile(r"\b(SELECT|WHERE|HAVING|QUALIFY|ON)\b", re.IGNORECASE)
    for match in _WALL_CLOCK.finditer(text):
        clauses = tuple(clause_pattern.finditer(text, 0, match.start()))
        clause = clauses[-1].group(1).lower() if clauses else ""
        if clause not in {"where", "having", "qualify", "on"}:
            continue
        prefix = text[: match.start()]
        location = SourceLocation(
            context.file,
            context.start_line + statement_line + prefix.count("\n") - 1,
            match.start() - prefix.rfind("\n"),
        )
        key = (match.group(0).rstrip("(").upper(), location.start_line)
        if key in known:
            continue
        dependencies.append(
            TimeDependency(
                TimeDependencyKind.WALL_CLOCK,
                match.group(0).rstrip("("),
                location,
                "join" if clause == "on" else clause,
            )
        )
    return tuple(dependencies)


def _correlates_target(expression: exp.Expression, target_aliases: set[str]) -> bool:
    """Return whether an equality links a target alias to a different relation."""

    for equality in expression.find_all(exp.EQ):
        qualifiers = {
            str(column.table).lower() for column in equality.find_all(exp.Column) if column.table
        }
        if qualifiers & target_aliases and qualifiers - target_aliases:
            return True
    return False


def _required_conjunct(node: exp.Expression, root: exp.Expression) -> bool:
    """Reject a candidate guard that can be bypassed through an OR branch."""

    current: exp.Expr | None = node
    while current is not None and current is not root:
        current = current.parent
        if isinstance(current, exp.Or):
            return False
    return current is root


def _has_left_anti_join(query: exp.Expression, target_name: str) -> bool:
    """Recognize LEFT JOIN target ... WHERE target.key IS NULL."""

    where = query.args.get("where")
    if not isinstance(where, exp.Where):
        return False
    joins = query.args.get("joins") or ()
    for join in joins:
        if not isinstance(join, exp.Join) or str(join.args.get("side", "")).upper() != "LEFT":
            continue
        table = join.this if isinstance(join.this, exp.Table) else join.this.find(exp.Table)
        if table is None or not _same_asset(_table_name(table), target_name):
            continue
        target_alias = str(table.alias_or_name).lower()
        on = join.args.get("on")
        if not isinstance(on, exp.Expression) or not _correlates_target(on, {target_alias}):
            continue
        for predicate in where.this.find_all(exp.Is):
            column = predicate.this
            if (
                isinstance(column, exp.Column)
                and isinstance(predicate.expression, exp.Null)
                and str(column.table).lower() == target_alias
                and _required_conjunct(predicate, where.this)
            ):
                return True
    return False


def _has_not_exists_guard(query: exp.Expression, target_name: str) -> bool:
    """Recognize a correlated NOT EXISTS probe against the insert target."""

    where = query.args.get("where")
    if not isinstance(where, exp.Where):
        return False
    for negation in where.this.find_all(exp.Not):
        exists = negation.this
        if not isinstance(exists, exp.Exists) or not isinstance(exists.this, exp.Expression):
            continue
        subquery = exists.this
        target_aliases = {
            str(table.alias_or_name).lower()
            for table in subquery.find_all(exp.Table)
            if _same_asset(_table_name(table), target_name)
        }
        subquery_where = subquery.args.get("where")
        if (
            target_aliases
            and isinstance(subquery_where, exp.Where)
            and _correlates_target(subquery_where.this, target_aliases)
            and _required_conjunct(negation, where.this)
        ):
            return True
    return False


def _has_insert_guard(statement: exp.Insert, target_name: str) -> bool:
    query = statement.args.get("expression")
    return isinstance(query, exp.Expression) and (
        _has_left_anti_join(query, target_name) or _has_not_exists_guard(query, target_name)
    )


def _write_operation(
    statement: exp.Expression,
    text: str,
    dialect: str,
    location: SourceLocation,
    index: int,
    group: str | None,
) -> WriteOperation | None:
    target: exp.Table | None = None
    mode = WriteMode.UNKNOWN
    conflict = False
    upper = text.upper()
    if isinstance(statement, exp.Insert):
        target = (
            statement.this
            if isinstance(statement.this, exp.Table)
            else statement.this.find(exp.Table)
        )
        overwrite = bool(statement.args.get("overwrite")) or bool(
            re.search(r"\bINSERT\s+OVERWRITE\b", upper)
        )
        conflict = bool(statement.args.get("conflict")) or bool(
            re.search(r"\bON\s+CONFLICT\b|\bON\s+DUPLICATE\s+KEY\b", upper)
        )
        if target is not None:
            conflict = conflict or _has_insert_guard(statement, _table_name(target))
        mode = (
            WriteMode.OVERWRITE
            if overwrite
            else (WriteMode.UPSERT if conflict else WriteMode.APPEND)
        )
    elif isinstance(statement, exp.Merge):
        target = (
            statement.this
            if isinstance(statement.this, exp.Table)
            else statement.this.find(exp.Table)
        )
        mode = WriteMode.MERGE
    elif isinstance(statement, exp.Update):
        target = (
            statement.this
            if isinstance(statement.this, exp.Table)
            else statement.this.find(exp.Table)
        )
        mode = WriteMode.UPDATE
    elif isinstance(statement, exp.Delete):
        target = (
            statement.this
            if isinstance(statement.this, exp.Table)
            else statement.this.find(exp.Table)
        )
        mode = WriteMode.DELETE
    elif isinstance(statement, exp.Create) and statement.args.get("expression") is not None:
        target = (
            statement.this
            if isinstance(statement.this, exp.Table)
            else statement.this.find(exp.Table)
        )
        mode = WriteMode.OVERWRITE if "OR REPLACE" in upper else WriteMode.UNKNOWN
    if target is None:
        return None
    return WriteOperation(
        _asset(target, dialect),
        mode,
        (),
        (),
        group,
        location,
        index,
        conflict,
        " ".join(text.split())[:500],
    )


def _read_operations(
    statement: exp.Expression,
    target: str | None,
    predicates: tuple[Predicate, ...],
    pagination: Pagination | None,
    dialect: str,
    context: SourceLocation,
    statement_line: int,
    index: int,
) -> tuple[ReadOperation, ...]:
    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    seen: set[str] = set()
    reads: list[ReadOperation] = []
    for table in statement.find_all(exp.Table):
        name = _table_name(table)
        if not name or name == target or name in cte_names or name in seen:
            continue
        seen.add(name)
        reads.append(
            ReadOperation(
                _asset(table, dialect),
                predicates,
                pagination,
                _node_location(table, context, statement_line),
                index,
            )
        )
    return tuple(reads)


class SqlAnalyzer:
    """Parse SQL text into immutable, parser-independent semantic facts."""

    def analyze(
        self, sql: str, dialect: str, context: SourceLocation, *, index_offset: int = 0
    ) -> SqlAnalysis:
        """Analyze all statements, continuing after an individual parse failure."""

        statements: list[StatementSemantics] = []
        diagnostics: list[Diagnostic] = []
        groups: list[TransactionGroup] = []
        asset_definitions: list[AssetDefinition] = []
        active_group: str | None = None
        active_indexes: list[int] = []
        group_location: SourceLocation | None = None
        for local_index, (text, statement_line) in enumerate(_split_statements(sql)):
            index = index_offset + local_index
            location = SourceLocation(context.file, context.start_line + statement_line - 1)
            try:
                parsed = _parse(text, dialect)
            except (SqlglotError, ValueError) as error:
                diagnostics.append(
                    Diagnostic(
                        "SQL_PARSE_ERROR",
                        f"Could not parse SQL statement: {str(error).splitlines()[0]}",
                        location,
                        Severity.MEDIUM,
                    )
                )
                continue
            kind = _statement_kind(parsed, text)
            definition = _table_definition(parsed, text, dialect, location)
            if definition is not None:
                asset_definitions.append(definition)
            if kind == "begin":
                active_group = f"tx-{index}"
                active_indexes = []
                group_location = location
            if active_group is not None and kind not in {"begin", "commit", "rollback"}:
                active_indexes.append(index)

            predicates = _predicates(parsed, text, context, statement_line, dialect)
            pagination = _pagination(parsed, text, context, statement_line)
            windows = _windows(parsed, text, predicates, context, statement_line)
            dependencies = _time_dependencies(predicates, text, context, statement_line)
            write = _write_operation(parsed, text, dialect, location, index, active_group)
            target = write.target.name if write else None
            reads = _read_operations(
                parsed,
                target,
                predicates,
                pagination,
                dialect,
                context,
                statement_line,
                index,
            )
            operations = reads + ((write,) if write else ())
            statements.append(
                StatementSemantics(
                    index,
                    kind,
                    text,
                    location,
                    predicates,
                    pagination,
                    dependencies,
                    windows,
                    operations,
                    active_group,
                )
            )
            if kind in {"commit", "rollback"} and active_group is not None:
                groups.append(
                    TransactionGroup(
                        active_group,
                        tuple(active_indexes),
                        True,
                        group_location or location,
                    )
                )
                active_group = None
                active_indexes = []
                group_location = None

        if active_group is not None:
            diagnostics.append(
                Diagnostic(
                    "SQL_UNCLOSED_TRANSACTION",
                    "BEGIN/START TRANSACTION has no visible COMMIT or ROLLBACK.",
                    group_location or context,
                    Severity.LOW,
                )
            )
            statements = [
                replace(item, transactional_group=None)
                if item.transactional_group == active_group
                else item
                for item in statements
            ]
            normalized: list[StatementSemantics] = []
            for item in statements:
                operations = tuple(
                    replace(operation, transactional_group=None)
                    if isinstance(operation, WriteOperation)
                    and operation.transactional_group == active_group
                    else operation
                    for operation in item.operations
                )
                normalized.append(replace(item, operations=operations))
            statements = normalized
        return SqlAnalysis(
            tuple(statements),
            tuple(groups),
            tuple(diagnostics),
            tuple(asset_definitions),
        )
