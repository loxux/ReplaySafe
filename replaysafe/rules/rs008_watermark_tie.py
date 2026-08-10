"""RS008: scalar batching cursor without a stable tie-breaker."""

from __future__ import annotations

import re

from replaysafe.ir import PipelineModel, Severity, WriteOperation
from replaysafe.rules.base import AnalysisContext, FindingIterable, RuleMetadata, make_finding
from replaysafe.rules.rs003_target_watermark import is_target_watermark

METADATA = RuleMetadata(
    "RS008",
    "Non-unique watermark without tie-breaker",
    Severity.HIGH,
    True,
    "A bounded batch advances a scalar cursor that can contain tied values.",
    "Use a compound cursor such as (timestamp, stable_id) in filtering and ordering.",
)
_CURSOR = re.compile(
    r"(?P<column>[A-Za-z_][\w.]*(?:time|date|timestamp|_at|watermark)[\w.]*)\s*>\s*"
    r"(?P<value>:[A-Za-z_]\w*|\$\d+|\?|\{+[^}]+\}+)",
    re.IGNORECASE,
)


def _has_compound_cursor(text: str, column: str) -> bool:
    normalized = " ".join(text.split())
    equal = re.search(rf"\b{re.escape(column)}\b\s*=", normalized, re.IGNORECASE)
    greater_columns = {
        match.group(1).lower()
        for match in re.finditer(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*>", normalized)
    }
    return " OR " in normalized.upper() and bool(equal) and len(greater_columns) >= 2


class WatermarkTieRule:
    """Find bounded scalar watermark batches where a tie can be split."""

    metadata = METADATA

    def evaluate(self, model: PipelineModel, context: AnalysisContext) -> FindingIterable:
        """Yield bounded scalar watermark batches without a tie-breaker."""

        for task in model.tasks:
            targets = {
                item.target.name for item in task.operations if isinstance(item, WriteOperation)
            }
            for statement in task.statements:
                if statement.pagination is None or statement.pagination.limit is None:
                    continue
                for predicate in statement.predicates:
                    match = _CURSOR.search(predicate.expression) or _CURSOR.search(statement.text)
                    if match is None:
                        continue
                    if _has_compound_cursor(statement.text, match.group("column")):
                        continue
                    if any(is_target_watermark(predicate.expression, target) for target in targets):
                        continue
                    yield make_finding(
                        metadata=self.metadata,
                        context=context,
                        location=predicate.location,
                        evidence=predicate.expression,
                        semantic_key=(
                            f"{task.task_id}:scalar-watermark:{match.group('column')}:"
                            f"{statement.text}"
                        ),
                        message="A limited batch uses a scalar watermark without a secondary key.",
                        scenario=(
                            "More rows share one watermark value than fit in the batch.",
                            "The checkpoint advances after only part of that tied value is processed.",
                            "The next strict-greater-than query excludes the remaining tied rows.",
                        ),
                        consequence="Rows tied at the batch boundary can be permanently skipped.",
                        remediation=(
                            "Filter and order by (watermark, stable_id).",
                            "Persist both cursor components only after successful processing.",
                        ),
                    )
