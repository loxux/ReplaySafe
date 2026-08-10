# Changelog

## 0.2.1 - 2026-08-10

- Refresh the PyPI project description to describe current dbt support without stale v0.1 labels.

## 0.2.0 - 2026-08-10

- Auto-detect `target/manifest.json` when scanning a dbt project.
- Analyze dbt `compiled_code`/`compiled_sql` while reporting against the original model path.
- Model dbt relations, `ref`/`source` dependencies, materializations, incremental strategies, and synthetic destination writes.
- Exclude generated `target`, `logs`, and `dbt_packages` trees from duplicate source scanning.

## 0.1.1 - 2026-08-10

- Convert SQLGlot tokenizer failures into recoverable diagnostics instead of tracebacks.
- Tolerate dbt Jinja comments and Python f-string placeholders during SQL analysis.
- Expand supported SQLGlot versions through the 30.x release line.

## 0.1.0 - 2026-08-10

- Initial library-first CLI and deterministic discovery pipeline.
- Immutable semantic IR plus SQLGlot, Python/Airflow, and dbt manifest adapters.
- RS001, RS002, RS003, RS004, RS006, RS008, and RS017 enabled; RS014 available but disabled.
- Text, versioned JSON, SARIF, configuration, suppressions, examples, and CI guidance.
