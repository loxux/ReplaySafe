"""Thin Click adapter over ReplaySafe's reusable Python APIs."""

from __future__ import annotations

import os
from pathlib import Path

import click

from replaysafe import __version__
from replaysafe.analysis import ScanResult, scan_repository
from replaysafe.config import MINIMAL_CONFIG, ConfigError, ReplaySafeConfig, load_config
from replaysafe.ir import Severity
from replaysafe.output import render_json, render_sarif, render_text
from replaysafe.rules import RULE_METADATA


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="replaysafe")
@click.pass_context
def cli(context: click.Context) -> None:
    """Find data pipelines that cannot safely retry or replay."""

    if context.invoked_subcommand is None:
        click.echo(context.get_help())


def _split_csv(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(item.strip() for value in values for item in value.split(",") if item.strip())


def _render(result: ScanResult, output_format: str) -> str:
    if output_format == "json":
        return render_json(result)
    if output_format == "sarif":
        return render_sarif(result)
    return render_text(result)


@cli.command("scan")
@click.argument("path", type=click.Path(path_type=Path), default=Path("."))
@click.option(
    "output_format", "--format", type=click.Choice(["text", "json", "sarif"]), default="text"
)
@click.option(
    "severity_threshold",
    "--severity-threshold",
    type=click.Choice([item.value for item in Severity]),
    default="low",
    help="Only render findings at or above this severity.",
)
@click.option("config_path", "--config", type=click.Path(path_type=Path))
@click.option(
    "dialect",
    "--dialect",
    type=click.Choice(["auto", "postgres", "snowflake", "bigquery", "starrocks"]),
)
@click.option("fail_on", "--fail-on", type=click.Choice([item.value for item in Severity]))
@click.option("rules", "--rule", multiple=True, help="Comma-separated rule IDs to run.")
@click.option(
    "excludes", "--exclude", multiple=True, help="Additional comma-separated glob excludes."
)
@click.option(
    "dbt_manifest",
    "--dbt-manifest",
    type=click.Path(path_type=Path),
    help="Path to dbt manifest.json; defaults to PATH/target/manifest.json when present.",
)
@click.option(
    "airflow_mode", "--airflow-mode", type=click.Choice(["auto", "static", "off"]), default="auto"
)
@click.option("ci", "--ci", is_flag=True, help="Require reasons for suppressions.")
def scan_command(
    path: Path,
    output_format: str,
    severity_threshold: str,
    config_path: Path | None,
    dialect: str | None,
    fail_on: str | None,
    rules: tuple[str, ...],
    excludes: tuple[str, ...],
    dbt_manifest: Path | None,
    airflow_mode: str,
    ci: bool,
) -> None:
    """Scan PATH without executing repository code."""

    scan_root = path.resolve()
    implicit = (scan_root if scan_root.is_dir() else scan_root.parent) / "replaysafe.yml"
    selected_config = config_path or (implicit if implicit.exists() else None)
    try:
        config = load_config(selected_config, ci=ci or bool(os.environ.get("CI")))
    except ConfigError as error:
        click.echo(f"Error: {error}", err=True)
        raise click.exceptions.Exit(2) from error
    if dialect is not None or fail_on is not None:
        config = ReplaySafeConfig(
            config.version,
            dialect or config.dialect,
            Severity(fail_on) if fail_on else config.fail_on,
            config.exclude,
            config.rules,
            config.assets,
            config.suppressions,
            config.require_suppression_reason,
        )
    selected = frozenset(item.upper() for item in _split_csv(rules)) if rules else None
    unknown = (selected or frozenset()) - frozenset(RULE_METADATA)
    if unknown:
        click.echo(f"Error: Unknown rule ID(s): {', '.join(sorted(unknown))}", err=True)
        raise click.exceptions.Exit(2)
    result = scan_repository(
        scan_root,
        config,
        selected_rules=selected,
        extra_excludes=_split_csv(excludes),
        dbt_manifest=dbt_manifest,
        airflow_mode=airflow_mode,
    )
    threshold = Severity(severity_threshold)
    visible = tuple(item for item in result.findings if item.severity.reaches(threshold))
    rendered_result = ScanResult(
        result.root,
        result.files,
        result.python_files,
        result.sql_files,
        result.models,
        visible,
        result.diagnostics,
    )
    click.echo(_render(rendered_result, output_format), nl=False)
    if any(item.code == "DISCOVERY_NOT_FOUND" for item in result.diagnostics):
        raise click.exceptions.Exit(2)
    if result.failed(config.fail_on):
        raise click.exceptions.Exit(1)


@cli.command("rules")
def rules_command() -> None:
    """List built-in rules and their default status."""

    for rule_id in sorted(RULE_METADATA):
        item = RULE_METADATA[rule_id]
        status = "enabled" if item.enabled_by_default else "disabled"
        click.echo(f"{rule_id} {item.default_severity.value.upper():8} {status:8} {item.title}")


@cli.command("explain")
@click.argument("rule_id")
def explain_command(rule_id: str) -> None:
    """Explain one rule and its remediation boundary."""

    item = RULE_METADATA.get(rule_id.upper())
    if item is None:
        raise click.ClickException(f"Unknown rule ID: {rule_id}")
    click.echo(f"{item.id} - {item.title}")
    click.echo(f"Default severity: {item.default_severity.value}")
    click.echo(f"Enabled by default: {'yes' if item.enabled_by_default else 'no'}")
    click.echo(item.summary)
    click.echo(item.help)


@cli.command("init")
@click.option("path", "--path", type=click.Path(path_type=Path), default=Path("replaysafe.yml"))
@click.option("force", "--force", is_flag=True, help="Replace an existing config file.")
def init_command(path: Path, force: bool) -> None:
    """Write a minimal documented replaysafe.yml."""

    if path.exists() and not force:
        raise click.ClickException(f"Refusing to overwrite existing file: {path}")
    try:
        path.write_text(MINIMAL_CONFIG, encoding="utf-8")
    except OSError as error:
        raise click.ClickException(f"Could not write {path}: {error}") from error
    click.echo(f"Wrote {path}")


def main() -> None:
    """Run the command-line interface."""

    cli()


if __name__ == "__main__":
    main()
