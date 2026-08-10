"""Non-fatal diagnostics produced during repository analysis."""

from dataclasses import dataclass

from replaysafe.ir import Severity, SourceLocation


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """An actionable parser, discovery, or configuration message."""

    code: str
    message: str
    location: SourceLocation
    severity: Severity = Severity.MEDIUM
