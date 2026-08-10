"""Rule protocol, context, metadata, and deterministic finding helpers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from replaysafe.config import ReplaySafeConfig
from replaysafe.ir import Confidence, Evidence, Finding, PipelineModel, Severity, SourceLocation

_SECRET_PATTERNS = (
    re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key)\s*[:=]\s*(['\"]?)[^\s,'\";]+"),
    re.compile(r"(?i)://([^:/\s]+):([^@/\s]+)@"),
)

type FindingIterable = Iterable[Finding]


@dataclass(frozen=True, slots=True)
class RuleMetadata:
    """Stable documentation and defaults for a rule."""

    id: str
    title: str
    default_severity: Severity
    enabled_by_default: bool
    summary: str
    help: str


@dataclass(frozen=True, slots=True)
class AnalysisContext:
    """Validated repository-owned metadata available to pure rules."""

    config: ReplaySafeConfig


class Rule(Protocol):
    """A deterministic pure function over semantic IR."""

    metadata: RuleMetadata

    def evaluate(self, model: PipelineModel, context: AnalysisContext) -> Iterable[Finding]:
        """Yield findings supported by semantic evidence."""


def sanitize_evidence(text: str) -> str:
    """Redact common credential forms before rendering source evidence."""

    sanitized = " ".join(text.split())[:1000]
    sanitized = _SECRET_PATTERNS[0].sub(lambda match: f"{match.group(1)}=<redacted>", sanitized)
    return _SECRET_PATTERNS[1].sub(r"://\1:<redacted>@", sanitized)


def make_finding(
    *,
    metadata: RuleMetadata,
    context: AnalysisContext,
    location: SourceLocation,
    evidence: str,
    semantic_key: str,
    message: str,
    scenario: tuple[str, ...],
    consequence: str,
    remediation: tuple[str, ...],
    confidence: Confidence = Confidence.HIGH,
) -> Finding:
    """Construct a finding with stable severity, redaction, and fingerprinting."""

    normalized = " ".join(semantic_key.lower().split())
    identity = f"{metadata.id}|{location.file.replace('\\', '/')}|{normalized}"
    fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return Finding(
        metadata.id,
        context.config.rule_severity(metadata.id, metadata.default_severity),
        metadata.title,
        message,
        location,
        (Evidence(sanitize_evidence(evidence), location),),
        scenario,
        consequence,
        remediation,
        confidence,
        fingerprint,
    )
