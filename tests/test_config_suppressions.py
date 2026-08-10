from datetime import date
from pathlib import Path

import pytest

from replaysafe.analysis import scan_repository
from replaysafe.config import ConfigError, ConfigSuppression, ReplaySafeConfig, parse_config


def test_config_validation_and_manifest_types() -> None:
    config = parse_config(
        {
            "version": 1,
            "dialect": "postgres",
            "fail_on": "critical",
            "rules": {"RS002": {"severity": "medium"}},
            "assets": {"dst": {"unique_key": "id", "write_semantics": "upsert"}},
        }
    )
    assert config.assets["dst"].unique_key == ("id",)
    assert config.assets["dst"].write_semantics.value == "upsert"
    with pytest.raises(ConfigError, match="Unknown rule"):
        parse_config({"rules": {"RS999": {"enabled": True}}})
    with pytest.raises(ConfigError, match="reason"):
        parse_config({"suppressions": [{"rule": "RS002", "file": "x.sql"}]}, ci=True)
    with pytest.raises(ConfigError, match="write_semantics"):
        parse_config({"assets": {"dst": {"write_semantics": "magic"}}})


def test_config_and_inline_suppressions(tmp_path: Path) -> None:
    (tmp_path / "configured.sql").write_text("INSERT INTO dst SELECT * FROM src", encoding="utf-8")
    (tmp_path / "inline.sql").write_text(
        '-- replaysafe: ignore RS002 reason="external uniqueness"\n'
        "INSERT INTO other SELECT * FROM src",
        encoding="utf-8",
    )
    config = ReplaySafeConfig(
        suppressions=(
            ConfigSuppression("RS002", "configured.sql", "external uniqueness", date(2099, 1, 1)),
        ),
        require_suppression_reason=True,
    )
    result = scan_repository(tmp_path, config, selected_rules=frozenset({"RS002"}))
    assert result.findings == ()
    assert not any(item.code == "UNUSED_SUPPRESSION" for item in result.diagnostics)


def test_unused_suppression_reported(tmp_path: Path) -> None:
    (tmp_path / "safe.sql").write_text("SELECT 1", encoding="utf-8")
    config = ReplaySafeConfig(suppressions=(ConfigSuppression("RS002", "missing.sql", "legacy"),))
    result = scan_repository(tmp_path, config)
    assert any(item.code == "UNUSED_SUPPRESSION" for item in result.diagnostics)
