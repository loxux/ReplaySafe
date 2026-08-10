"""Built-in ReplaySafe rule registry."""

from replaysafe.rules.base import AnalysisContext, Rule, RuleMetadata
from replaysafe.rules.rs001_wall_clock import WallClockRule
from replaysafe.rules.rs002_blind_append import BlindAppendRule
from replaysafe.rules.rs003_target_watermark import TargetWatermarkRule
from replaysafe.rules.rs004_destructive_replace import DestructiveReplacementRule
from replaysafe.rules.rs006_pagination import UnstablePaginationRule
from replaysafe.rules.rs008_watermark_tie import WatermarkTieRule
from replaysafe.rules.rs014_side_effect import ExternalSideEffectRule
from replaysafe.rules.rs017_dedup import NondeterministicDedupRule

RULES: tuple[Rule, ...] = (
    WallClockRule(),
    BlindAppendRule(),
    TargetWatermarkRule(),
    DestructiveReplacementRule(),
    UnstablePaginationRule(),
    WatermarkTieRule(),
    ExternalSideEffectRule(),
    NondeterministicDedupRule(),
)
RULE_METADATA: dict[str, RuleMetadata] = {rule.metadata.id: rule.metadata for rule in RULES}

__all__ = ["RULES", "RULE_METADATA", "AnalysisContext", "Rule", "RuleMetadata"]
