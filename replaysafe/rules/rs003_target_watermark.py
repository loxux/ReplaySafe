"""RS003: target-derived scalar watermarks advanced by partial writes."""

from __future__ import annotations

import re

from replaysafe.ir import PipelineModel, Severity, WriteOperation
from replaysafe.rules.base import AnalysisContext, FindingIterable, RuleMetadata, make_finding

METADATA = RuleMetadata(
    "RS003",
    "Target-derived unsafe watermark",
    Severity.CRITICAL,
    True,
    "A target-derived MAX watermark can advance after only part of a batch is written.",
    "Persist an independent checkpoint only after success, or use a compound cursor and idempotent writes.",
)


def is_target_watermark(expression: str, target: str) -> bool:
    """Return whether an expression visibly reads MAX(cursor) from its write target."""

    normalized = expression.lower().replace('"', "").replace("`", "")
    target_pattern = re.escape(target.lower())
    return bool(
        re.search(
            rf">\s*\(\s*select\s+max\s*\([^)]*\)\s+from\s+{target_pattern}\b",
            normalized,
            re.IGNORECASE,
        )
    )


class TargetWatermarkRule:
    """Find target-derived cursors for targets written by the same task."""

    metadata = METADATA

    def evaluate(self, model: PipelineModel, context: AnalysisContext) -> FindingIterable:
        """Yield same-target MAX watermark hazards."""

        for task in model.tasks:
            targets = {
                item.target.name for item in task.operations if isinstance(item, WriteOperation)
            }
            for statement in task.statements:
                for predicate in statement.predicates:
                    for target in sorted(targets):
                        if not is_target_watermark(predicate.expression, target):
                            continue
                        yield make_finding(
                            metadata=self.metadata,
                            context=context,
                            location=predicate.location,
                            evidence=predicate.expression,
                            semantic_key=(
                                f"{task.task_id}:target-watermark:{target}:{predicate.expression}"
                            ),
                            message=f"The cursor is derived from {target}, which this task also writes.",
                            scenario=(
                                "The task writes only part of a source batch to the target.",
                                "That partial write advances MAX(target watermark).",
                                "The task fails before the remaining rows are written.",
                                "Retry starts after the advanced watermark and skips the unwritten rows.",
                            ),
                            consequence="Rows can be permanently omitted after a partial-write failure.",
                            remediation=(
                                "Store the input checkpoint independently and advance it only after success.",
                                "Use idempotent writes and a compound (watermark, stable_id) cursor.",
                            ),
                        )
