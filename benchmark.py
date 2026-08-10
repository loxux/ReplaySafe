"""Generate a temporary 1,000-file corpus and report scanner wall time."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from replaysafe.analysis import scan_repository


def main() -> None:
    """Run a lightweight local performance smoke benchmark."""

    with tempfile.TemporaryDirectory(prefix="replaysafe-benchmark-") as directory:
        root = Path(directory)
        query = "SELECT id FROM source WHERE updated_at >= :start ORDER BY id LIMIT 100"
        for index in range(1_000):
            (root / f"model_{index:04}.sql").write_text(query, encoding="utf-8")
        started = time.perf_counter()
        result = scan_repository(root)
        elapsed = time.perf_counter() - started
        print(f"Scanned {len(result.files)} files in {elapsed:.3f}s")


if __name__ == "__main__":
    main()
