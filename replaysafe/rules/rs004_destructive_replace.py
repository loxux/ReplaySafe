"""RS004: DELETE followed by INSERT without an explicit transaction."""

from replaysafe.ir import PipelineModel, Severity, WriteMode, WriteOperation
from replaysafe.rules.base import AnalysisContext, FindingIterable, RuleMetadata, make_finding
from replaysafe.rules.semantics import is_replacement_pair

METADATA = RuleMetadata(
    "RS004",
    "Non-atomic destructive replacement",
    Severity.CRITICAL,
    True,
    "DELETE and replacement INSERT are not visibly atomic.",
    "Wrap the scoped replacement in one transaction or use an atomic overwrite construct.",
)


class DestructiveReplacementRule:
    """Find clear same-task destructive replacements lacking atomicity."""

    metadata = METADATA

    def evaluate(self, model: PipelineModel, context: AnalysisContext) -> FindingIterable:
        """Yield clear non-atomic same-target replacements."""

        for task in model.tasks:
            writes = sorted(
                (item for item in task.operations if isinstance(item, WriteOperation)),
                key=lambda item: item.statement_index,
            )
            for delete in writes:
                if delete.mode != WriteMode.DELETE:
                    continue
                insert = next(
                    (
                        item
                        for item in writes
                        if item.mode == WriteMode.APPEND and is_replacement_pair(task, delete, item)
                    ),
                    None,
                )
                if insert is None:
                    continue
                same_explicit_group = bool(
                    delete.transactional_group
                    and delete.transactional_group == insert.transactional_group
                )
                if same_explicit_group:
                    continue
                yield make_finding(
                    metadata=self.metadata,
                    context=context,
                    location=delete.location,
                    evidence=f"{delete.evidence}; then {insert.evidence}",
                    semantic_key=f"{task.task_id}:delete-insert:{delete.target.name}",
                    message=f"Replacement of {delete.target.name} is not visibly atomic.",
                    scenario=(
                        "The DELETE commits successfully.",
                        "The following INSERT fails or is interrupted.",
                        "Retry has no atomic snapshot that restores the removed rows.",
                    ),
                    consequence=f"{delete.target.name} can remain empty or partially populated.",
                    remediation=(
                        "Execute the scoped DELETE and INSERT in one explicit transaction.",
                        "Prefer INSERT OVERWRITE or another documented atomic replacement primitive.",
                    ),
                )
