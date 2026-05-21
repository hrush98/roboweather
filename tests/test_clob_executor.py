from __future__ import annotations

import pytest

from weather_trader.execution.clob_executor import _parse_balance_allowance


def test_parse_balance_allowance_normalizes_raw_units() -> None:
    raw = {"balance": "5264064", "allowance": "9999999"}

    balance, allowance = _parse_balance_allowance(raw, decimals=6)

    assert balance == pytest.approx(5.264064)
    assert allowance == pytest.approx(9.999999)


def test_parse_balance_allowance_no_decimals() -> None:
    raw = {"balance": "5264064", "allowance": "9999999"}

    balance, allowance = _parse_balance_allowance(raw)

    assert balance == pytest.approx(5264064.0)
    assert allowance == pytest.approx(9999999.0)


def test_parse_balance_allowance_uses_max_contract_allowance() -> None:
    raw = {
        "balance": "143035280",
        "allowances": {
            "0xexchangeA": "0",
            "0xexchangeB": "25000000",
        },
    }

    balance, allowance = _parse_balance_allowance(raw, decimals=6)

    assert balance == pytest.approx(143.03528)
    assert allowance == pytest.approx(25.0)


def test_parse_balance_allowance_keeps_balance_when_allowance_is_bad() -> None:
    raw = {"balance": "143035280", "allowances": {"0xexchange": "not-a-number"}}

    balance, allowance = _parse_balance_allowance(raw, decimals=6)

    assert balance == pytest.approx(143.03528)
    assert allowance is None
