"""Airflow support is implemented as static enrichment in the Python adapter.

This module intentionally contains no Airflow import: importing a scanned DAG or
requiring Airflow at scan time would violate ReplaySafe's security model.
"""

from replaysafe.parsers.python import PythonAnalyzer

__all__ = ["PythonAnalyzer"]
