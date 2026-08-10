"""Validated YAML configuration for ReplaySafe scans."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from replaysafe.ir import Severity, WriteMode

KNOWN_RULE_IDS = frozenset({"RS001", "RS002", "RS003", "RS004", "RS006", "RS008", "RS014", "RS017"})


class ConfigError(ValueError):
    """An actionable configuration validation error."""


@dataclass(frozen=True, slots=True)
class RuleOverride:
    """Per-rule enablement and severity overrides."""

    enabled: bool | None = None
    severity: Severity | None = None


@dataclass(frozen=True, slots=True)
class AssetMetadata:
    """Explicit destination semantics supplied by the repository owner."""

    append_only: bool = False
    duplicate_tolerant: bool = False
    unique_key: tuple[str, ...] = ()
    write_semantics: WriteMode | None = None


@dataclass(frozen=True, slots=True)
class ConfigSuppression:
    """A documented file/rule exception."""

    rule: str
    file: str
    reason: str
    expires: date | None = None

    def applies(self, rule_id: str, relative_file: str, today: date | None = None) -> bool:
        """Return whether this suppression currently matches a finding."""

        current = today or date.today()
        return (
            self.rule == rule_id
            and fnmatch.fnmatchcase(relative_file.replace("\\", "/"), self.file)
            and (self.expires is None or self.expires >= current)
        )


@dataclass(frozen=True, slots=True)
class ReplaySafeConfig:
    """Fully validated scan configuration."""

    version: int = 1
    dialect: str = "auto"
    fail_on: Severity = Severity.HIGH
    exclude: tuple[str, ...] = ()
    rules: dict[str, RuleOverride] = field(default_factory=dict)
    assets: dict[str, AssetMetadata] = field(default_factory=dict)
    suppressions: tuple[ConfigSuppression, ...] = ()
    require_suppression_reason: bool = False

    def rule_enabled(self, rule_id: str, default: bool) -> bool:
        """Resolve rule enablement against the rule default."""

        override = self.rules.get(rule_id)
        return default if override is None or override.enabled is None else override.enabled

    def rule_severity(self, rule_id: str, default: Severity) -> Severity:
        """Resolve rule severity against the rule default."""

        override = self.rules.get(rule_id)
        return default if override is None or override.severity is None else override.severity

    def asset(self, name: str) -> AssetMetadata:
        """Return explicit asset metadata using case-insensitive normalized names."""

        return self.assets.get(name.lower(), AssetMetadata())


MINIMAL_CONFIG = """# ReplaySafe configuration
version: 1
dialect: auto
fail_on: high
exclude:
  - "tests/**"
  - "vendor/**"
rules:
  RS014:
    enabled: false
# suppressions require an explicit reason in CI:
# assets:
#   analytics.orders:
#     write_semantics: upsert
#     unique_key: [order_id]
# suppressions:
#   - rule: RS002
#     file: dags/audit.py
#     reason: "Destination has an external uniqueness guarantee."
"""


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{context} must be a mapping.")
    return {str(key): item for key, item in value.items()}


def _severity(value: Any, context: str) -> Severity:
    try:
        return Severity(str(value).lower())
    except ValueError as error:
        raise ConfigError(f"{context} must be low, medium, high, or critical.") from error


def _strings(value: Any, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{context} must be a list of strings.")
    return tuple(value)


def _optional_date(value: Any, context: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise ConfigError(f"{context} must use YYYY-MM-DD format.") from error


def parse_config(raw: Any, *, ci: bool = False) -> ReplaySafeConfig:
    """Validate a decoded YAML configuration object."""

    data = _mapping(raw, "Configuration")
    version = data.get("version", 1)
    if version != 1:
        raise ConfigError("Only configuration version 1 is supported.")
    dialect = str(data.get("dialect", "auto")).lower()
    if dialect not in {"auto", "postgres", "snowflake", "bigquery", "starrocks"}:
        raise ConfigError(f"Unsupported dialect '{dialect}'.")
    fail_on = _severity(data.get("fail_on", "high"), "fail_on")
    exclude = _strings(data.get("exclude", []), "exclude")

    rules: dict[str, RuleOverride] = {}
    for rule_id, payload in _mapping(data.get("rules", {}), "rules").items():
        if rule_id not in KNOWN_RULE_IDS:
            raise ConfigError(f"Unknown rule ID in configuration: {rule_id}")
        item = _mapping(payload, f"rules.{rule_id}")
        enabled = item.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise ConfigError(f"rules.{rule_id}.enabled must be true or false.")
        severity = (
            _severity(item["severity"], f"rules.{rule_id}.severity") if "severity" in item else None
        )
        rules[rule_id] = RuleOverride(enabled, severity)

    assets: dict[str, AssetMetadata] = {}
    for name, payload in _mapping(data.get("assets", {}), "assets").items():
        item = _mapping(payload, f"assets.{name}")
        append_only = item.get("append_only", False)
        duplicate_tolerant = item.get("duplicate_tolerant", False)
        if not isinstance(append_only, bool) or not isinstance(duplicate_tolerant, bool):
            raise ConfigError(f"assets.{name} boolean fields must be true or false.")
        unique = item.get("unique_key", [])
        unique_key: tuple[str, ...]
        if isinstance(unique, str):
            unique_key = (unique,)
        else:
            unique_key = _strings(unique, f"assets.{name}.unique_key")
        raw_semantics = item.get("write_semantics")
        write_semantics: WriteMode | None = None
        if raw_semantics is not None:
            try:
                write_semantics = WriteMode(str(raw_semantics).lower())
            except ValueError as error:
                raise ConfigError(
                    f"assets.{name}.write_semantics must be append, upsert, merge, or overwrite."
                ) from error
            if write_semantics not in {
                WriteMode.APPEND,
                WriteMode.UPSERT,
                WriteMode.MERGE,
                WriteMode.OVERWRITE,
            }:
                raise ConfigError(
                    f"assets.{name}.write_semantics must be append, upsert, merge, or overwrite."
                )
        assets[name.lower()] = AssetMetadata(
            append_only,
            duplicate_tolerant,
            unique_key,
            write_semantics,
        )

    require_reason = bool(data.get("require_suppression_reason", ci))
    suppressions: list[ConfigSuppression] = []
    raw_suppressions = data.get("suppressions", [])
    if not isinstance(raw_suppressions, list):
        raise ConfigError("suppressions must be a list.")
    for index, payload in enumerate(raw_suppressions):
        item = _mapping(payload, f"suppressions[{index}]")
        rule = str(item.get("rule", ""))
        if rule not in KNOWN_RULE_IDS:
            raise ConfigError(f"Unknown rule ID in suppressions[{index}]: {rule or '<missing>'}")
        file_pattern = str(item.get("file", ""))
        reason = str(item.get("reason", "")).strip()
        if not file_pattern:
            raise ConfigError(f"suppressions[{index}].file is required.")
        if require_reason and not reason:
            raise ConfigError(f"suppressions[{index}].reason is required in CI mode.")
        suppressions.append(
            ConfigSuppression(
                rule,
                file_pattern.replace("\\", "/"),
                reason,
                _optional_date(item.get("expires"), f"suppressions[{index}].expires"),
            )
        )
    return ReplaySafeConfig(
        version,
        dialect,
        fail_on,
        exclude,
        rules,
        assets,
        tuple(suppressions),
        require_reason,
    )


def load_config(path: Path | None, *, ci: bool = False) -> ReplaySafeConfig:
    """Read a config with yaml.safe_load; a missing implicit config uses defaults."""

    if path is None:
        return ReplaySafeConfig(require_suppression_reason=ci)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"Could not read configuration {path}: {error}") from error
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ConfigError(f"Malformed YAML in {path}: {error}") from error
    if raw is None:
        raw = {}
    return parse_config(raw, ci=ci)
