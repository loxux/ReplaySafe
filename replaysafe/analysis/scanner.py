"""Reusable repository scanner; the CLI is intentionally a thin adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from replaysafe.analysis.pipeline_builder import build_python_model, build_sql_model
from replaysafe.config import ReplaySafeConfig
from replaysafe.diagnostics import Diagnostic
from replaysafe.discovery import discover_files
from replaysafe.ir import Finding, PipelineModel, Severity, SourceLocation
from replaysafe.parsers import DbtManifest, DbtNode, load_manifest
from replaysafe.rules import RULES, AnalysisContext
from replaysafe.suppressions import apply_suppressions

MAX_SOURCE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Complete deterministic result returned by the public scanning API."""

    root: str
    files: tuple[str, ...]
    python_files: int
    sql_files: int
    models: tuple[PipelineModel, ...]
    findings: tuple[Finding, ...]
    diagnostics: tuple[Diagnostic, ...]

    def failed(self, threshold: Severity) -> bool:
        """Return whether any finding reaches the configured failure threshold."""

        return any(item.severity.reaches(threshold) for item in self.findings)


def _dbt_node(manifest: DbtManifest | None, relative_path: str) -> DbtNode | None:
    if manifest is None:
        return None
    normalized = relative_path.replace("\\", "/")
    direct = manifest.nodes_by_path.get(normalized)
    if direct:
        return direct
    matches = [node for path, node in manifest.nodes_by_path.items() if normalized.endswith(path)]
    return matches[0] if len(matches) == 1 else None


def _read_source(path: Path, relative: str) -> tuple[str | None, Diagnostic | None]:
    try:
        size = path.stat().st_size
        if size > MAX_SOURCE_BYTES:
            return None, Diagnostic(
                "SOURCE_TOO_LARGE",
                f"File is {size} bytes; the static scan limit is {MAX_SOURCE_BYTES} bytes.",
                SourceLocation(relative, 1),
                Severity.LOW,
            )
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeError) as error:
        return None, Diagnostic(
            "SOURCE_UNREADABLE",
            f"Could not read source as UTF-8: {error}",
            SourceLocation(relative, 1),
            Severity.MEDIUM,
        )


def scan_repository(
    root: Path,
    config: ReplaySafeConfig | None = None,
    *,
    selected_rules: frozenset[str] | None = None,
    extra_excludes: tuple[str, ...] = (),
    dbt_manifest: Path | None = None,
    airflow_mode: str = "auto",
) -> ScanResult:
    """Discover, parse, analyze, suppress, and return a repository scan."""

    active_config = config or ReplaySafeConfig()
    if airflow_mode not in {"auto", "static", "off"}:
        raise ValueError("airflow_mode must be auto, static, or off")
    root = root.resolve()
    discovery = discover_files(root, active_config.exclude + extra_excludes)
    diagnostics: list[Diagnostic] = list(discovery.diagnostics)
    manifest: DbtManifest | None = None
    if dbt_manifest is not None:
        manifest = load_manifest(dbt_manifest)
        diagnostics.extend(manifest.diagnostics)
    models: list[PipelineModel] = []
    sources: dict[str, str] = {}
    for item in discovery.files:
        source, diagnostic = _read_source(item.absolute_path, item.relative_path)
        if diagnostic:
            diagnostics.append(diagnostic)
            continue
        assert source is not None
        sources[item.relative_path] = source
        if item.absolute_path.suffix.lower() == ".sql":
            model, parser_diagnostics = build_sql_model(
                source,
                item.relative_path,
                active_config.dialect,
                _dbt_node(manifest, item.relative_path),
            )
        else:
            model, parser_diagnostics = build_python_model(
                source,
                item.relative_path,
                active_config.dialect,
                airflow_enabled=airflow_mode != "off",
            )
        models.append(model)
        diagnostics.extend(parser_diagnostics)

    context = AnalysisContext(active_config)
    findings: list[Finding] = []
    for model in models:
        for rule in RULES:
            metadata = rule.metadata
            if selected_rules is not None and metadata.id not in selected_rules:
                continue
            if not active_config.rule_enabled(metadata.id, metadata.enabled_by_default):
                continue
            findings.extend(rule.evaluate(model, context))
    deduplicated = {finding.fingerprint: finding for finding in findings}
    ordered = tuple(
        sorted(
            deduplicated.values(),
            key=lambda item: (
                item.location.file,
                item.location.start_line,
                item.rule_id,
                item.fingerprint,
            ),
        )
    )
    suppression_result = apply_suppressions(
        ordered,
        active_config.suppressions,
        sources,
        require_reason=active_config.require_suppression_reason,
    )
    diagnostics.extend(suppression_result.diagnostics)
    diagnostics.sort(key=lambda item: (item.location.file, item.location.start_line, item.code))
    files = tuple(item.relative_path for item in discovery.files)
    return ScanResult(
        root.as_posix(),
        files,
        sum(path.endswith(".py") for path in files),
        sum(path.endswith(".sql") for path in files),
        tuple(models),
        suppression_result.findings,
        tuple(diagnostics),
    )
