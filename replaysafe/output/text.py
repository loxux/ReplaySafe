"""Human-readable terminal renderer."""

from __future__ import annotations

from collections import defaultdict

from replaysafe.analysis import ScanResult
from replaysafe.rules.base import sanitize_evidence


def render_text(result: ScanResult) -> str:
    """Render findings grouped by file without ANSI control codes."""

    lines: list[str] = []
    grouped = defaultdict(list)
    for finding in result.findings:
        grouped[finding.location.file].append(finding)
    for file in sorted(grouped):
        lines.append(file)
        for finding in grouped[file]:
            lines.append(f"{finding.rule_id} {finding.severity.value.upper()}  {finding.title}")
            location = f"line {finding.location.start_line}"
            if finding.location.start_col is not None:
                location += f":{finding.location.start_col}"
            lines.append(f"  {location}: {finding.evidence[0].text}")
            lines.append(f"  {finding.message}")
            lines.append("  Failure scenario:")
            lines.extend(
                f"  {index}. {step}" for index, step in enumerate(finding.failure_scenario, 1)
            )
            lines.append(f"  Potential consequence: {finding.consequence}")
            lines.append("  Suggested remediation:")
            lines.extend(f"  - {item}" for item in finding.remediation)
            lines.append(f"  Confidence: {finding.confidence.value.upper()}")
            lines.append("")
    if result.diagnostics:
        lines.append("Diagnostics")
        for diagnostic in result.diagnostics:
            message = sanitize_evidence(diagnostic.message)
            lines.append(
                f"{diagnostic.code} {diagnostic.severity.value.upper()} "
                f"{diagnostic.location.file}:{diagnostic.location.start_line} {message}"
            )
        lines.append("")
    high = sum(item.severity.value in {"high", "critical"} for item in result.findings)
    lines.append(
        f"Scanned {len(result.files)} files ({result.sql_files} SQL, {result.python_files} Python)."
    )
    if result.findings:
        lines.append(f"Scan result: {len(result.findings)} finding(s), {high} high/critical.")
    else:
        lines.append("Scan result: no recovery findings.")
    return "\n".join(lines) + "\n"
