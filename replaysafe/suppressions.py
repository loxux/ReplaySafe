"""Config and inline suppression matching with unused-suppression reporting."""

from __future__ import annotations

import re
from dataclasses import dataclass

from replaysafe.config import ConfigSuppression
from replaysafe.diagnostics import Diagnostic
from replaysafe.ir import Finding, Severity, SourceLocation

_INLINE = re.compile(
    r"replaysafe:\s*ignore\s+(?P<rules>RS\d{3}(?:\s*,\s*RS\d{3})*)"
    r"(?:\s+reason\s*=\s*[\"'](?P<reason>[^\"']+)[\"'])?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SuppressionResult:
    """Filtered findings plus stale/invalid-suppression diagnostics."""

    findings: tuple[Finding, ...]
    diagnostics: tuple[Diagnostic, ...]


def _inline_suppressed(
    finding: Finding, source: str, require_reason: bool
) -> tuple[bool, Diagnostic | None]:
    lines = source.splitlines()
    target = finding.location.start_line - 1
    for index in range(max(0, target - 3), min(len(lines), target + 1)):
        match = _INLINE.search(lines[index])
        if not match:
            continue
        rules = {item.strip().upper() for item in match.group("rules").split(",")}
        if finding.rule_id not in rules:
            continue
        reason = (match.group("reason") or "").strip()
        if require_reason and not reason:
            return (
                False,
                Diagnostic(
                    "SUPPRESSION_REASON_REQUIRED",
                    f"Inline suppression for {finding.rule_id} requires a reason in CI mode.",
                    SourceLocation(finding.location.file, index + 1),
                    Severity.MEDIUM,
                ),
            )
        return True, None
    return False, None


def apply_suppressions(
    findings: tuple[Finding, ...],
    configured: tuple[ConfigSuppression, ...],
    sources: dict[str, str],
    *,
    require_reason: bool,
) -> SuppressionResult:
    """Apply documented exceptions and report configured exceptions never used."""

    kept: list[Finding] = []
    diagnostics: list[Diagnostic] = []
    used: set[int] = set()
    for finding in findings:
        suppressed = False
        for index, item in enumerate(configured):
            if item.applies(finding.rule_id, finding.location.file):
                used.add(index)
                suppressed = True
                break
        if suppressed:
            continue
        source = sources.get(finding.location.file, "")
        inline, diagnostic = _inline_suppressed(finding, source, require_reason)
        if diagnostic:
            diagnostics.append(diagnostic)
        if not inline:
            kept.append(finding)
    for index, item in enumerate(configured):
        if index not in used:
            diagnostics.append(
                Diagnostic(
                    "UNUSED_SUPPRESSION",
                    f"Suppression for {item.rule} matching '{item.file}' did not match any finding.",
                    SourceLocation(item.file, 1),
                    Severity.LOW,
                )
            )
    return SuppressionResult(tuple(kept), tuple(diagnostics))
