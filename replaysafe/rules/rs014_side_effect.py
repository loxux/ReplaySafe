"""RS014: retry-unsafe HTTP side effects (disabled by default)."""

from replaysafe.ir import PipelineModel, Severity
from replaysafe.rules.base import AnalysisContext, FindingIterable, RuleMetadata, make_finding

METADATA = RuleMetadata(
    "RS014",
    "Retry-unsafe external side effect",
    Severity.CRITICAL,
    False,
    "A retried task performs an external mutation without visible idempotency evidence.",
    "Send a stable idempotency key or record an outbox/checkpoint around the mutation.",
)


class ExternalSideEffectRule:
    """Find obvious HTTP POST calls inside tasks with retries enabled."""

    metadata = METADATA

    def evaluate(self, model: PipelineModel, context: AnalysisContext) -> FindingIterable:
        """Yield obvious non-idempotent external mutations in retried tasks."""

        for task in model.tasks:
            if not task.retries or task.retries <= 0:
                continue
            for effect in task.external_effects:
                if effect.idempotency_key:
                    continue
                yield make_finding(
                    metadata=self.metadata,
                    context=context,
                    location=effect.location,
                    evidence=effect.expression,
                    semantic_key=f"{task.task_id}:{effect.kind}:{effect.expression}",
                    message="A retried task performs HTTP POST without a visible idempotency key.",
                    scenario=(
                        "The remote service commits the side effect.",
                        "The task fails before Airflow records success.",
                        "Airflow retries and repeats the mutation.",
                    ),
                    consequence="The external action can happen more than once.",
                    remediation=(
                        "Send a stable idempotency key derived from the logical task input.",
                        "Use an outbox or durable completion record when the API lacks idempotency support.",
                    ),
                )
