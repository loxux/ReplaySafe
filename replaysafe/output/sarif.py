"""SARIF 2.1.0 renderer compatible with GitHub code scanning."""

from __future__ import annotations

import json

from replaysafe import __version__
from replaysafe.analysis import ScanResult
from replaysafe.rules import RULE_METADATA
from replaysafe.rules.base import sanitize_evidence


def _level(severity: str) -> str:
    return {"critical": "error", "high": "error", "medium": "warning", "low": "note"}[severity]


def render_sarif(result: ScanResult) -> str:
    """Render deterministic SARIF with rule help and stable partial fingerprints."""

    used_rule_ids = sorted({finding.rule_id for finding in result.findings})
    rules = []
    for rule_id in used_rule_ids:
        metadata = RULE_METADATA[rule_id]
        rules.append(
            {
                "id": rule_id,
                "name": metadata.title.replace(" ", ""),
                "shortDescription": {"text": metadata.summary},
                "fullDescription": {"text": metadata.help},
                "help": {"text": metadata.help},
                "defaultConfiguration": {"level": _level(metadata.default_severity.value)},
            }
        )
    results = []
    for finding in result.findings:
        region: dict[str, int] = {"startLine": finding.location.start_line}
        if finding.location.start_col is not None:
            region["startColumn"] = max(1, finding.location.start_col)
        results.append(
            {
                "ruleId": finding.rule_id,
                "level": _level(finding.severity.value),
                "message": {
                    "text": f"{finding.message} Remediation: {' '.join(finding.remediation)}"
                },
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": finding.location.file},
                            "region": region,
                        }
                    }
                ],
                "partialFingerprints": {"replaysafe/v1": finding.fingerprint},
                "properties": {
                    "confidence": finding.confidence.value,
                    "failureScenario": list(finding.failure_scenario),
                    "consequence": finding.consequence,
                },
            }
        )
    notifications = [
        {
            "level": _level(item.severity.value),
            "message": {"text": f"{item.code}: {sanitize_evidence(item.message)}"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": item.location.file},
                        "region": {"startLine": item.location.start_line},
                    }
                }
            ],
        }
        for item in result.diagnostics
    ]
    payload = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "ReplaySafe",
                        "semanticVersion": __version__,
                        "informationUri": "https://github.com/replaysafe/replaysafe",
                        "rules": rules,
                    }
                },
                "results": results,
                "invocations": [
                    {
                        "executionSuccessful": True,
                        "toolExecutionNotifications": notifications,
                    }
                ],
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
