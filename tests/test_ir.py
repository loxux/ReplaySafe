from dataclasses import FrozenInstanceError

import pytest

from replaysafe.ir import (
    DataAsset,
    Severity,
    SourceLocation,
    WriteMode,
    WriteOperation,
    to_plain_dict,
)


def test_ir_equality_immutability_and_serialization() -> None:
    location = SourceLocation("models/orders.sql", 4, 2)
    first = WriteOperation(DataAsset("analytics.orders"), WriteMode.APPEND, (), (), None, location)
    second = WriteOperation(DataAsset("analytics.orders"), WriteMode.APPEND, (), (), None, location)

    assert first == second
    with pytest.raises(FrozenInstanceError):
        first.mode = WriteMode.MERGE  # type: ignore[misc]
    assert to_plain_dict(first) == {
        "target": {"name": "analytics.orders", "kind": "table", "dialect": None},
        "mode": "append",
        "keys": [],
        "partition_keys": [],
        "transactional_group": None,
        "location": {
            "file": "models/orders.sql",
            "start_line": 4,
            "start_col": 2,
            "end_line": None,
            "end_col": None,
        },
        "statement_index": 0,
        "conflict_handling": False,
        "evidence": "",
    }
    assert Severity.CRITICAL.reaches(Severity.HIGH)
