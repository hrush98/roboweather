from __future__ import annotations

import pytest

from weather_trader.execution import clob_executor
from weather_trader.execution.clob_executor import ClobExecutor, _exception_submission, _parse_balance_allowance


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


def test_exception_submission_preserves_v2_order_id_from_error_dict() -> None:
    class FakePolyApiException(Exception):
        status_code = 400
        error_msg = {
            "error": "no orders found to match with FAK order",
            "orderID": "0xabc",
        }

        def __str__(self) -> str:
            return "PolyApiException[status_code=400]"

    submission = _exception_submission(FakePolyApiException())

    assert submission.success is False
    assert submission.status == "error"
    assert submission.order_id == "0xabc"
    assert submission.error_msg == "no orders found to match with FAK order"
    assert submission.raw["status_code"] == 400


def test_exception_submission_parses_v2_error_string() -> None:
    class FakePolyApiException(Exception):
        status_code = 400
        error_msg = "{'error': 'no match', 'orderID': '0xdef'}"

        def __str__(self) -> str:
            return "PolyApiException[status_code=400]"

    submission = _exception_submission(FakePolyApiException())

    assert submission.order_id == "0xdef"
    assert submission.error_msg == "no match"


class _FakeV2Client:
    def __init__(self) -> None:
        self.market_orders = []
        self.limit_orders = []

    def get_tick_size(self, token_id: str) -> str:
        assert token_id == "token-1"
        return "0.001"

    def get_neg_risk(self, token_id: str) -> bool:
        assert token_id == "token-1"
        return False

    def create_and_post_market_order(self, **kwargs):
        self.market_orders.append(kwargs)
        return {"success": False, "status": "error", "errorMsg": "wrong helper"}

    def create_and_post_order(self, **kwargs):
        self.limit_orders.append(kwargs)
        return {"success": True, "status": "live", "orderID": "0xgtc"}


def test_v2_gtc_order_uses_limit_order_helper_for_explicit_price_and_size(tmp_path) -> None:
    if clob_executor.OrderArgsV2 is None:
        pytest.skip("py_clob_client_v2 is not installed")

    executor = object.__new__(ClobExecutor)
    executor._client_version = "v2"
    executor._client = _FakeV2Client()
    executor._kill_switch_path = tmp_path / "kill-switch"

    submission = executor.place_gtc_order(token_id="token-1", side="BUY", price=0.58, amount=25.0)

    assert submission.success is True
    assert submission.order_id == "0xgtc"
    assert executor._client.market_orders == []
    assert len(executor._client.limit_orders) == 1
    payload = executor._client.limit_orders[0]
    assert payload["order_type"] == clob_executor._v2_order_type("GTC")
    assert payload["options"].tick_size == "0.001"
    assert payload["options"].neg_risk is False
    order_args = payload["order_args"]
    assert order_args.token_id == "token-1"
    assert order_args.price == 0.58
    assert order_args.size == 43.1
    assert payload["post_only"] is False


def test_v2_gtc_order_threads_post_only_flag(tmp_path) -> None:
    if clob_executor.OrderArgsV2 is None:
        pytest.skip("py_clob_client_v2 is not installed")

    executor = object.__new__(ClobExecutor)
    executor._client_version = "v2"
    executor._client = _FakeV2Client()
    executor._kill_switch_path = tmp_path / "kill-switch"

    submission = executor.place_gtc_order(token_id="token-1", side="BUY", price=0.58, amount=25.0, post_only=True)

    assert submission.success is True
    payload = executor._client.limit_orders[0]
    assert payload["post_only"] is True
