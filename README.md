# ReplaySafe

ReplaySafe is a deterministic static analyzer for data pipelines that cannot safely retry, replay, backfill, partially fail, or run concurrently. The v0.1 scanner focuses on SQL plus statically extractable Python/Airflow and dbt-style SQL. It runs locally and never imports or executes scanned repository code.

## Five-minute quick start

ReplaySafe requires Python 3.12 or newer.

```bash
python -m pip install replaysafe
replaysafe scan dags/
```

Scan output explains the exact evidence, failure sequence, likely consequence, remediation, and deterministic confidence. A finding at or above `fail_on` exits with status 1.

```bash
replaysafe scan . --dialect postgres --format json
replaysafe scan . --format sarif > replaysafe.sarif
replaysafe explain RS003
replaysafe init
```

## What v0.1 checks

| Rule | Default | Proven pattern |
| --- | --- | --- |
| RS001 Wall-clock dependency | High | Wall-clock function in `WHERE`, `JOIN`, `HAVING`, or `QUALIFY` |
| RS002 Retry-unsafe blind append | High | `INSERT` append without a visible replay guard |
| RS003 Target-derived unsafe watermark | Critical | `> (SELECT MAX(...) FROM target)` where the task writes target |
| RS004 Non-atomic destructive replacement | Critical | Same-task `DELETE` then `INSERT` outside an explicit transaction |
| RS006 Unstable pagination | High | `LIMIT/OFFSET` without `ORDER BY` |
| RS008 Non-unique watermark | High | Bounded scalar time cursor without a secondary key |
| RS014 External side effect | Critical, disabled | Retried Airflow task with obvious HTTP POST and no idempotency key |
| RS017 Non-deterministic deduplication | High | Proven `ROW_NUMBER`/`RANK = 1` survivor selection without ordering |

ReplaySafe prefers no finding over a speculative blocking result. See [rule boundaries](docs/rules.md) and the [precision report](docs/precision-report.md).

## Supported inputs

| Input | v0.1 support |
| --- | --- |
| Standalone SQL | Multi-statement parsing and recovery semantics |
| PostgreSQL, Snowflake, BigQuery, StarRocks | SQLGlot parsing with explicit dialect selection |
| Python | Literal/f-string/local-variable SQL passed to known execution methods |
| Airflow | Static `@dag`, `@task`, common SQL operators, retries, task IDs, logical-time Jinja |
| dbt | Optional read-only `manifest.json` enrichment; dbt is not invoked |

Dynamic Python assembly, arbitrary symbolic execution, runtime warehouse metadata, lineage catalogs, Spark plans, and generic data-quality checks are intentionally outside v0.1.

## Configuration and suppressions

Generate a documented starting point with `replaysafe init`. A fuller example is:

```yaml
version: 1
dialect: snowflake
fail_on: high
exclude:
  - "tests/**"
rules:
  RS014:
    enabled: false
assets:
  analytics.audit_log:
    append_only: true
    duplicate_tolerant: true
suppressions:
  - rule: RS002
    file: dags/audit.py
    reason: "Warehouse DDL enforces an event ID outside this repository."
    expires: "2027-01-01"
```

Inline exceptions apply to the following nearby statement and should explain the external guarantee:

```sql
-- replaysafe: ignore RS002 reason="event_id is unique in warehouse DDL"
INSERT INTO analytics.audit_log SELECT * FROM staged_events;
```

Use `--ci` (or set `CI`) to require suppression reasons. Unused config suppressions produce diagnostics instead of silently going stale.

## Output and exit codes

- `text` groups findings by file and presents the full failure path.
- `json` uses deterministic schema version `1.0.0`.
- `sarif` emits SARIF 2.1.0 with rule help and stable fingerprints.
- Exit 0: scan completed below the failure threshold.
- Exit 1: at least one finding reached `fail_on`.
- Exit 2: invalid invocation, configuration, or scan path.

The Python API is the core interface:

```python
from pathlib import Path
from replaysafe.analysis import scan_repository

result = scan_repository(Path("dags"))
```

## GitHub Actions

The repository includes a [SARIF workflow](.github/workflows/replaysafe.yml). It uploads results even when the configured finding threshold fails, then preserves the failed check. For stable install time, cache pip using `actions/setup-python` and pin the ReplaySafe release in production workflows.

## Security model

Default scans are local, offline, and parser-only. ReplaySafe does not import DAGs, call `eval`/`exec`, invoke dbt, connect to a warehouse, follow directory symlinks, or upload source. File size is bounded and likely credentials are redacted from evidence. See [SECURITY.md](SECURITY.md) for the threat boundary and reporting process.

## Development

```bash
python -m pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
mypy replaysafe
```

Contributions should add positive, safe-negative, and ambiguous fixtures before changing rule behavior. See [CONTRIBUTING.md](CONTRIBUTING.md).

The included 1,000-file smoke benchmark and current indicative result are documented in [docs/performance.md](docs/performance.md).

Licensed under Apache-2.0.
