# ReplaySafe v0.1 precision report

The release target is at least 90% precision for blocking findings on a manually reviewed corpus. This repository establishes deterministic regression boundaries; it does not claim that a broad public-repository validation study has already been completed.

## Semantic assumptions and safe negatives

### RS001

Assumption: only predicates affect business-row selection. Safe negatives include projection-only timestamps, quoted text, comments, logical interval parameters, ordinary date columns, and SQL with no wall clock. Dynamic Python selection that cannot be resolved is diagnostic-only.

### RS002

Assumption: an ordinary insert is append unless the statement or explicit metadata proves otherwise. Safe negatives include MERGE, insert overwrite, conflict handling, transactional delete/insert, non-transactional delete/insert delegated to RS004, and explicitly duplicate-tolerant assets. Unique constraints not present in code/config are deliberately unknown.

### RS003

Assumption: the subquery reads `MAX` from the exact target written by the same task. Safe negatives include a different target, independent checkpoint tables, MIN aggregation, non-watermark subqueries, a target read without a write, and a write without the cursor predicate.

### RS004

Assumption: operation order and target identity are clear within one inferred task. Safe negatives include different targets, insert before delete, explicit transactions, overwrite, delete-only statements, and append-only statements. Python transaction-manager inference remains limited.

### RS006

Assumption: any present `ORDER BY` is sufficient evidence for v0.1. Safe negatives include LIMIT-only sampling, ordered offset, keyset pagination, no pagination, ordered dynamic limits, and window ordering unrelated to an offset-free query.

### RS008

Assumption: batch LIMIT can end inside a tied time-like scalar cursor. Safe negatives include no limit, compound cursor, stable secondary-key cursor, non-time cursor, equality-only filters, and target-derived cursors handled by RS003. Parameter styles outside recognized placeholders are unknown.

### RS017

Assumption: the window alias is proven to be filtered to survivor position 1. Safe negatives include no survivor filter, no partition, an ordered window, aggregate deduplication, DISTINCT, and a window used only for display. Present but incomplete ordering is not accused without proof of ties.

## Known limitations

- SQL embedded through arbitrary Python control/data flow is not resolved.
- SQLGlot dialect coverage is bounded by the tested constructs and versions in `pyproject.toml`.
- Transaction wrappers implemented in custom Python libraries are not inferred.
- Runtime schema constraints and warehouse-specific atomicity are unknown unless represented in config/dbt metadata.
- dbt macro expansion is not executed; simple Jinja placeholders are tolerated only for surrounding SQL parsing.
- RS014 remains disabled because generic external-call precision needs a larger corpus.

Overlapping RS003/RS008 causes are emitted as RS003 only; fingerprints omit raw line numbers so unrelated line movement does not create a new identity.

