"""RS002: retry-unsafe append writes without a visible replay guard."""

from replaysafe.ir import PipelineModel, Severity, WriteMode, WriteOperation
from replaysafe.rules.base import AnalysisContext, FindingIterable, RuleMetadata, make_finding
from replaysafe.rules.semantics import is_replacement_pair

METADATA = RuleMetadata(
    "RS002",
    "Retry-unsafe blind append",
    Severity.HIGH,
    True,
    "An append can be committed and repeated after an ambiguous task failure.",
    "Use MERGE/upsert, atomic overwrite, conflict handling, or another explicit idempotency key.",
)


class BlindAppendRule:
    """Find append writes lacking explicit retry protection."""

    metadata = METADATA

    def evaluate(self, model: PipelineModel, context: AnalysisContext) -> FindingIterable:
        """Yield append writes without visible replay protection."""

        for task in model.tasks:
            writes = tuple(item for item in task.operations if isinstance(item, WriteOperation))
            for write in writes:
                if write.mode != WriteMode.APPEND or write.conflict_handling:
                    continue
                metadata = context.config.asset(write.target.name)
                guarded_by_delete = any(
                    item.mode == WriteMode.DELETE and is_replacement_pair(task, item, write)
                    for item in writes
                )
                if metadata.duplicate_tolerant or guarded_by_delete:
                    continue
                if model.materialization == "incremental" and (
                    model.unique_key or metadata.unique_key
                ):
                    continue
                yield make_finding(
                    metadata=self.metadata,
                    context=context,
                    location=write.location,
                    evidence=write.evidence or f"INSERT INTO {write.target.name}",
                    semantic_key=f"{task.task_id}:append:{write.target.name}",
                    message=f"Append to {write.target.name} has no visible replay guard.",
                    scenario=(
                        "The database commits the appended rows.",
                        "The worker fails before the orchestrator records success.",
                        "The orchestrator retries the same task input.",
                        "The same rows are appended again.",
                    ),
                    consequence=f"Duplicate rows can be introduced in {write.target.name}.",
                    remediation=(
                        "Use MERGE/UPSERT with a stable business key.",
                        "Use atomic partition overwrite where supported.",
                        "Use a visible uniqueness constraint with conflict handling.",
                    ),
                )
