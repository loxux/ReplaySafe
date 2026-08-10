# Performance note

`benchmark.py` creates 1,000 small SQL files in an isolated temporary directory and scans them through the same public API as the CLI.

On 2026-08-10, the development Windows environment completed the corpus in 0.758 seconds. This is an indicative smoke result, not a cross-platform service-level guarantee. The product target remains under approximately 10 seconds for 1,000 typical SQL/Python files on a modern laptop.

The implementation parses each source once, evaluates immutable per-file models, does not retain SQLGlot or Python AST objects in IR, and orders output only after findings are collected.
