"""Value types used by ReplaySafe's parser-independent IR."""

from enum import StrEnum


class Severity(StrEnum):
    """Finding and diagnostic severity."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Return the deterministic ordering rank for this severity."""

        return {self.LOW: 0, self.MEDIUM: 1, self.HIGH: 2, self.CRITICAL: 3}[self]

    def reaches(self, threshold: "Severity") -> bool:
        """Return whether this severity reaches a configured threshold."""

        return self.rank >= threshold.rank


class WriteMode(StrEnum):
    """Normalized write behavior relevant to replay safety."""

    APPEND = "append"
    MERGE = "merge"
    UPSERT = "upsert"
    OVERWRITE = "overwrite"
    DELETE = "delete"
    UPDATE = "update"
    UNKNOWN = "unknown"


class TimeDependencyKind(StrEnum):
    """Source of time used by a pipeline expression."""

    WALL_CLOCK = "wall_clock"
    LOGICAL_TIME = "logical_time"
    PARAMETER = "parameter"
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    """Evidence confidence assigned by a deterministic rule."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
