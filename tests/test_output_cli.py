import json
from pathlib import Path

from click.testing import CliRunner

from replaysafe.analysis import scan_repository
from replaysafe.cli import cli
from replaysafe.output import render_json, render_sarif, render_text


def test_renderers_are_deterministic_and_complete(tmp_path: Path) -> None:
    (tmp_path / "unsafe.sql").write_text(
        "INSERT INTO dst SELECT * FROM src WHERE day=CURRENT_DATE", encoding="utf-8"
    )
    result = scan_repository(tmp_path)
    text = render_text(result)
    json_first = render_json(result)
    sarif = render_sarif(result)
    assert "Failure scenario:" in text
    assert render_json(result) == json_first
    payload = json.loads(json_first)
    assert payload["schema_version"] == "1.0.0"
    assert payload["findings"][0]["failure_scenario"]
    sarif_payload = json.loads(sarif)
    assert sarif_payload["version"] == "2.1.0"
    assert sarif_payload["runs"][0]["results"][0]["partialFingerprints"]
    assert "\x1b" not in json_first + sarif


def test_cli_version_scan_and_rules(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(cli, ["--version"]).exit_code == 0
    (tmp_path / "safe.sql").write_text("SELECT 1", encoding="utf-8")
    scan = runner.invoke(cli, ["scan", str(tmp_path)])
    assert scan.exit_code == 0
    assert "Scanned 1 files" in scan.output
    rules = runner.invoke(cli, ["rules"])
    assert rules.exit_code == 0
    assert "RS014" in rules.output and "disabled" in rules.output


def test_cli_failure_and_init(tmp_path: Path) -> None:
    runner = CliRunner()
    (tmp_path / "unsafe.sql").write_text("INSERT INTO dst SELECT * FROM src", encoding="utf-8")
    assert runner.invoke(cli, ["scan", str(tmp_path)]).exit_code == 1
    config = tmp_path / "custom.yml"
    created = runner.invoke(cli, ["init", "--path", str(config)])
    assert created.exit_code == 0
    assert "version: 1" in config.read_text(encoding="utf-8")


def test_secret_is_redacted(tmp_path: Path) -> None:
    (tmp_path / "unsafe.sql").write_text(
        "INSERT INTO dst SELECT 'password=hunter2' FROM src", encoding="utf-8"
    )
    output = render_text(scan_repository(tmp_path))
    assert "hunter2" not in output
    assert "<redacted>" in output


def test_cli_configuration_and_path_errors_exit_two(tmp_path: Path) -> None:
    runner = CliRunner()
    config = tmp_path / "bad.yml"
    config.write_text("rules: [not-a-mapping]", encoding="utf-8")
    invalid_config = runner.invoke(cli, ["scan", str(tmp_path), "--config", str(config)])
    assert invalid_config.exit_code == 2
    missing = runner.invoke(cli, ["scan", str(tmp_path / "missing")])
    assert missing.exit_code == 2
