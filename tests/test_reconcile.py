import json
from decimal import Decimal

import pytest

from generate_data import generate_data
from reconcile import reconcile_files


@pytest.fixture
def generated_reconciliation(tmp_path):
    planted_gaps = generate_data(tmp_path, seed=42)
    result = reconcile_files(
        tmp_path / "platform_transactions.csv",
        tmp_path / "bank_settlements.csv",
    )
    manifest = json.loads((tmp_path / "planted_gaps.json").read_text(encoding="utf-8"))
    assert manifest == planted_gaps
    return result, manifest


def test_finds_all_next_month_timing_rows(generated_reconciliation):
    result, manifest = generated_reconciliation
    actual = set(
        result.detail.loc[
            result.detail["classification"] == "TIMING_NEXT_MONTH", "txn_id"
        ]
    )
    assert actual == set(manifest["timing_next_month_txn_ids"])


def test_finds_all_rounding_rows(generated_reconciliation):
    result, manifest = generated_reconciliation
    rows = result.detail[result.detail["classification"] == "ROUNDING"]
    assert set(rows["txn_id"]) == set(manifest["rounding_txn_ids"])
    assert all(
        Decimal("0") < abs(difference) < Decimal("0.01")
        for difference in rows["difference"]
    )


def test_finds_exactly_the_duplicated_settlement(generated_reconciliation):
    result, manifest = generated_reconciliation
    rows = result.detail[result.detail["classification"] == "DUPLICATE"]
    assert rows["txn_id"].tolist() == [manifest["duplicate_txn_ref"]]


def test_finds_orphan_refund(generated_reconciliation):
    result, manifest = generated_reconciliation
    rows = result.detail[
        result.detail["classification"] == "ORPHAN_REFUND"
    ]
    assert rows["txn_id"].tolist() == [manifest["orphan_refund_txn_id"]]
    assert rows["original_txn_id"].tolist() == [
        manifest["orphan_original_txn_id"]
    ]


def test_finds_unexplained_missing_payment(generated_reconciliation):
    result, manifest = generated_reconciliation
    rows = result.detail[
        result.detail["classification"] == "MISSING_UNEXPLAINED"
    ]
    assert rows["txn_id"].tolist() == [
        manifest["missing_unexplained_txn_id"]
    ]


def test_money_uses_decimal_without_float_values(generated_reconciliation):
    result, _ = generated_reconciliation
    for column in ("platform_amount", "bank_amount", "difference"):
        assert all(
            isinstance(value, Decimal)
            for value in result.detail[column]
        )
    assert all(isinstance(value, Decimal) for value in result.bridge["amount"])
    assert isinstance(result.platform_total, Decimal)
    assert isinstance(result.bank_total, Decimal)


def test_bridge_leftover_is_zero(generated_reconciliation):
    result, _ = generated_reconciliation
    assert result.leftover == Decimal("0.00")
    leftover = result.bridge.loc[
        result.bridge["line_item"] == "LEFTOVER", "amount"
    ].item()
    assert leftover == Decimal("0.00")
    assert result.calculated_bank_total == result.bank_total
