"""RS001: wall-clock values used for data selection."""

from replaysafe.ir import PipelineModel, Severity
from replaysafe.rules.base import AnalysisContext, FindingIterable, RuleMetadata, make_finding

METADATA = RuleMetadata(
    "RS001",
    "Wall-clock dependency",
    Severity.HIGH,
    True,
    "Business-row selection depends on physical execution time.",
    "Use the pipeline's logical execution interval instead of CURRENT_DATE/NOW equivalents.",
)


class WallClockRule:
    """Find replay-sensitive wall-clock expressions in predicates."""

    metadata = METADATA

    def evaluate(self, model: PipelineModel, context: AnalysisContext) -> FindingIterable:
        """Yield proven wall-clock selection dependencies."""

        for task in model.tasks:
            for dependency in task.time_dependencies:
                if dependency.context not in {
                    "where",
                    "join",
                    "having",
                    "qualify",
                    "python_selection",
                }:
                    continue
                airflow = bool(task.logical_time_symbols) or task.task_id is not None
                remediation = (
                    (
                        "Use the orchestrator's logical execution interval instead of the wall clock.",
                        "In Airflow, bind data_interval_start/data_interval_end (or logical_date).",
                    )
                    if airflow
                    else ("Pass an explicit logical interval/start-end parameter into the query.",)
                )
                yield make_finding(
                    metadata=self.metadata,
                    context=context,
                    location=dependency.location,
                    evidence=dependency.expression,
                    semantic_key=f"{task.task_id}:{dependency.context}:{dependency.expression}",
                    message="Physical execution time controls which business rows are selected.",
                    scenario=(
                        "A historical interval is selected for replay.",
                        "The task executes on a later physical date.",
                        f"{dependency.expression} evaluates to the later date.",
                        "The task reads a different business interval than the replay requested.",
                    ),
                    consequence="Historical replay can read or write the wrong business period.",
                    remediation=remediation,
                )
