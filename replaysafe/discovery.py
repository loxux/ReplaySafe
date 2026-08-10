"""Deterministic and bounded source-file discovery."""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

from replaysafe.diagnostics import Diagnostic
from replaysafe.ir import SourceLocation

DEFAULT_EXCLUDES = (
    ".git/**",
    ".venv/**",
    "venv/**",
    "node_modules/**",
    "build/**",
    "dist/**",
    "target/**",
    "dbt_packages/**",
    "logs/**",
    ".mypy_cache/**",
    ".pytest_cache/**",
    ".ruff_cache/**",
    "__pycache__/**",
)
SOURCE_SUFFIXES = frozenset({".py", ".sql"})


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    """A source file with a stable path relative to the scan root."""

    absolute_path: Path
    relative_path: str


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Files and non-fatal diagnostics returned by discovery."""

    files: tuple[DiscoveredFile, ...]
    diagnostics: tuple[Diagnostic, ...]


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    path = path.replace("\\", "/")
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def discover_files(root: Path, excludes: tuple[str, ...] = ()) -> DiscoveryResult:
    """Discover Python and SQL files without following directory symlinks."""

    root = root.resolve()
    patterns = DEFAULT_EXCLUDES + tuple(item.replace("\\", "/") for item in excludes)
    files: list[DiscoveredFile] = []
    diagnostics: list[Diagnostic] = []

    if root.is_file():
        if root.suffix.lower() in SOURCE_SUFFIXES:
            return DiscoveryResult((DiscoveredFile(root, root.name),), ())
        return DiscoveryResult((), ())
    if not root.exists() or not root.is_dir():
        location = SourceLocation(str(root), 1)
        return DiscoveryResult(
            (),
            (Diagnostic("DISCOVERY_NOT_FOUND", f"Scan path does not exist: {root}", location),),
        )

    def on_error(error: OSError) -> None:
        path = str(error.filename or root)
        diagnostics.append(
            Diagnostic(
                "DISCOVERY_UNREADABLE",
                f"Cannot inspect path: {error.strerror or error}",
                SourceLocation(path, 1),
            )
        )

    for current, dirnames, filenames in os.walk(
        root, topdown=True, onerror=on_error, followlinks=False
    ):
        current_path = Path(current)
        kept_dirs: list[str] = []
        for dirname in sorted(dirnames):
            candidate = current_path / dirname
            relative = candidate.relative_to(root).as_posix()
            if candidate.is_symlink() or _matches(f"{relative}/", patterns):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in sorted(filenames):
            path = current_path / filename
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            if _matches(relative, patterns):
                continue
            files.append(DiscoveredFile(path, relative))

    files.sort(key=lambda item: item.relative_path)
    diagnostics.sort(key=lambda item: (item.location.file, item.code))
    return DiscoveryResult(tuple(files), tuple(diagnostics))
