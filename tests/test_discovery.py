from pathlib import Path

from replaysafe.discovery import discover_files


def test_discovery_is_sorted_unicode_and_excluded(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "nested" / "é.sql").write_text("select 1", encoding="utf-8")
    (tmp_path / "a.py").write_text("pass", encoding="utf-8")
    (tmp_path / "skip.sql").write_text("select 1", encoding="utf-8")
    (tmp_path / ".git" / "hidden.py").write_text("pass", encoding="utf-8")

    result = discover_files(tmp_path, ("skip.sql",))

    assert [item.relative_path for item in result.files] == ["a.py", "nested/é.sql"]
    assert result.diagnostics == ()


def test_discovery_accepts_single_file(tmp_path: Path) -> None:
    source = tmp_path / "one.sql"
    source.write_text("select 1", encoding="utf-8")
    assert discover_files(source).files[0].relative_path == "one.sql"
