# Security policy

## Supported versions

The latest v0.x release receives security fixes.

## Scanner threat model

Repositories are untrusted input. Default scanning uses file parsing only: Python is processed with `ast`, DAGs are never imported, YAML uses `safe_load`, dbt is not invoked, network calls are absent, directory symlinks are skipped, and files over 5 MiB are rejected. Evidence rendering redacts common password/token/connection-string forms.

Custom parser dependencies still process attacker-controlled text. CI users should pin releases and dependency hashes, use a least-privilege checkout token, and keep untrusted scans isolated from secrets not needed by the job.

## Reporting

Please report suspected vulnerabilities privately through GitHub's security advisory feature. Include a minimal reproduction and the affected version. Do not include live credentials.

