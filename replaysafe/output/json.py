"""Versioned byte-stable JSON renderer."""

from __future__ import annotations

import json

from replaysafe.analysis import ScanResult
from replaysafe.ir import to_plain_dict
from replaysafe.rules.base import sanitize_evidence


def render_json(result: ScanResult) -> str:
    """Render the complete structured scan result."""

    payload = {
        "schema_version": "1.0.0",
        "root": result.root,
        "summary": {
            "files": len(result.files),
            "python_files": result.python_files,
            "sql_files": result.sql_files,
            "findings": len(result.findings),
            "diagnostics": len(result.diagnostics),
        },
        "findings": [to_plain_dict(item) for item in result.findings],
        "diagnostics": [
            {**to_plain_dict(item), "message": sanitize_evidence(item.message)}
            for item in result.diagnostics
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
