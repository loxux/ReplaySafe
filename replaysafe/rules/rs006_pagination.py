"""RS006: OFFSET pagination without deterministic ordering."""

from replaysafe.ir import PipelineModel, Severity
from replaysafe.rules.base import AnalysisContext, FindingIterable, RuleMetadata, make_finding

METADATA = RuleMetadata(
    "RS006",
    "Unstable pagination",
    Severity.HIGH,
    True,
    "LIMIT/OFFSET pagination has no ORDER BY.",
    "Use a deterministic ORDER BY with a unique tie-breaker, preferably as keyset pagination.",
)


class UnstablePaginationRule:
    """Find OFFSET pagination whose page membership is undefined."""

    metadata = METADATA

    def evaluate(self, model: PipelineModel, context: AnalysisContext) -> FindingIterable:
        """Yield OFFSET queries without deterministic ordering."""

        for task in model.tasks:
            for statement in task.statements:
                pagination = statement.pagination
                if pagination is None or pagination.offset is None or pagination.order_by:
                    continue
                yield make_finding(
                    metadata=self.metadata,
                    context=context,
                    location=pagination.location,
                    evidence=statement.text,
                    semantic_key=(
                        f"{task.task_id}:offset-no-order:{statement.kind}:{statement.text}"
                    ),
                    message="OFFSET is used without a deterministic ORDER BY.",
                    scenario=(
                        "One page is read and processing continues with a later OFFSET.",
                        "The database returns rows in a different physical order.",
                        "Page membership shifts between calls.",
                    ),
                    consequence="Rows can be skipped or processed more than once.",
                    remediation=(
                        "Add ORDER BY ending in a stable unique key.",
                        "Prefer keyset pagination with a compound cursor.",
                    ),
                )
