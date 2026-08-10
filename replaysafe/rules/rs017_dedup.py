"""RS017: survivor selection without deterministic ordering."""

from replaysafe.ir import PipelineModel, Severity
from replaysafe.rules.base import AnalysisContext, FindingIterable, RuleMetadata, make_finding

METADATA = RuleMetadata(
    "RS017",
    "Non-deterministic deduplication",
    Severity.HIGH,
    True,
    "ROW_NUMBER/RANK survivor selection has no ORDER BY.",
    "Order survivor selection by business precedence and a final stable unique tie-breaker.",
)


class NondeterministicDedupRule:
    """Find proven survivor filters over unordered partitioned windows."""

    metadata = METADATA

    def evaluate(self, model: PipelineModel, context: AnalysisContext) -> FindingIterable:
        """Yield proven unordered survivor selections."""

        for task in model.tasks:
            for window in task.windows:
                if not window.survivor_selection or not window.partition_by or window.order_by:
                    continue
                yield make_finding(
                    metadata=self.metadata,
                    context=context,
                    location=window.location,
                    evidence=f"{window.function}() OVER (PARTITION BY {', '.join(window.partition_by)})",
                    semantic_key=(
                        f"{task.task_id}:{window.function}:{','.join(window.partition_by)}"
                    ),
                    message="Deduplication selects one row per key without defining a survivor order.",
                    scenario=(
                        "Multiple rows exist for the same business key.",
                        "The database assigns window positions using an unspecified order.",
                        "A replay can choose a different row as position 1.",
                    ),
                    consequence="Identical input can produce a different surviving record on replay.",
                    remediation=(
                        "Add ORDER BY for explicit business precedence.",
                        "End the ordering with a stable unique tie-breaker.",
                    ),
                )
