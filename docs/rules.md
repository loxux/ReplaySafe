# Rule reference

ReplaySafe rules consume semantic IR, not SQLGlot or Python AST nodes. Severity can be overridden in `replaysafe.yml`; RS014 is disabled until explicitly enabled.

## RS001 - Wall-clock dependency

Flags `CURRENT_DATE`, `CURRENT_TIMESTAMP`, `NOW()`, `SYSDATE`, `GETDATE()` and equivalents only when they participate in row-selection predicates. Projection-only processing metadata is outside the blocking boundary. Parameterize a logical start/end interval; in Airflow use `data_interval_start` and `data_interval_end`.

## RS002 - Retry-unsafe blind append

Flags an append where the same semantic task has no visible MERGE/upsert, overwrite, conflict handling, delete/insert replacement, dbt incremental unique key, or explicit duplicate-tolerant asset metadata. For manifest-backed dbt models, ReplaySafe also interprets `merge`, `delete+insert`, `insert_overwrite`, and `microbatch` strategies. ReplaySafe does not infer safety from destination names.

## RS003 - Target-derived unsafe watermark

Flags a strict-greater-than scalar `MAX` read from the same target the task writes. A partial target write can advance the cursor past unwritten source rows. Use an independent durable checkpoint and idempotent destination writes.

## RS004 - Non-atomic destructive replacement

Flags a clear same-task `DELETE` followed by append to the same target when no explicit `BEGIN`/`COMMIT` group or overwrite primitive is visible. Unrelated targets are not paired.

## RS006 - Unstable pagination

Flags `OFFSET` when `ORDER BY` is absent. One-shot `LIMIT` without offset is not reported. The v0.1 rule does not speculate about whether a present ordering is unique.

## RS008 - Non-unique watermark without tie-breaker

Flags only a bounded batch with a scalar time-like cursor parameter. A recognized compound strict/equality cursor is safe. Target-derived cases are reported once as RS003.

## RS014 - Retry-unsafe external side effect

Disabled by default. When enabled, flags an obvious HTTP POST inside a statically inferred task with retries greater than zero and no visible idempotency-key evidence.

## RS017 - Non-deterministic deduplication

Flags only proven survivor selection (`ROW_NUMBER` or `RANK`, partitioned by key and filtered to 1) without `ORDER BY`. An ordered window passes; v0.1 does not guess that a present ordering has ties.
