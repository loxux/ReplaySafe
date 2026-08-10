# Contributing

ReplaySafe treats false positives in blocking rules as release defects.

1. Open an issue describing the retry/replay failure sequence or the safe pattern being accused.
2. Add a minimal fixture: positive, safe negative, and ambiguous cases where relevant.
3. Keep parsing, semantic IR, rules, and renderers separated. Rules must not inspect SQLGlot or Python AST nodes.
4. Do not import or execute scanned code and do not add network access to default scans.
5. Run `pytest`, `ruff check .`, `ruff format --check .`, and `mypy replaysafe`.

Behavior changes require tests and an update to the rule boundary documentation. New blocking rules need dialect coverage, golden output, and an explicit false-positive boundary.

