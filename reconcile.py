"""Reconcile platform transactions against a calendar month's bank settlements."""

from __future__ import annotations

import argparse
import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TypedDict

import pandas as pd


CENT = Decimal("0.01")
ROUNDING_TOLERANCE = Decimal("0.01")
GAP_TYPES = (
    "MATCHED",
    "ROUNDING",
    "TIMING_NEXT_MONTH",
    "DUPLICATE",
    "ORPHAN_REFUND",
    "MISSING_UNEXPLAINED",
)
PLATFORM_COLUMNS = {
    "txn_id",
    "txn_date",
    "customer_id",
    "type",
    "amount",
    "original_txn_id",
}
BANK_COLUMNS = {"settlement_id", "txn_ref", "settlement_date", "amount"}


class DetailRecord(TypedDict):
    txn_id: str
    txn_date: str
    type: str
    settlement_id: str
    settlement_date: str
    platform_amount: Decimal
    bank_amount: Decimal
    difference: Decimal
    original_txn_id: str
    classification: str


@dataclass(frozen=True)
class ReconciliationResult:
    detail: pd.DataFrame
    bridge: pd.DataFrame
    platform_total: Decimal
    bank_total: Decimal
    calculated_bank_total: Decimal
    leftover: Decimal


def load_csv(path: Path | str) -> pd.DataFrame:
    """Load CSV fields as text so monetary values never pass through float."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _decimal(value: object, field: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError(f"Invalid decimal in {field}: {value!r}") from error
    if not amount.is_finite():
        raise ValueError(f"Non-finite decimal in {field}: {value!r}")
    return amount


def _validate_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"{label} is missing required columns: {', '.join(sorted(missing))}")


def _parse_date(value: object, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise ValueError(f"Invalid date in {field}: {value!r}") from error


def _sum_decimals(values: Iterable[object], field: str) -> Decimal:
    total = Decimal("0.00")
    for value in values:
        total += _decimal(value, field)
    return total


def reconcile_data(
    platform: pd.DataFrame,
    bank: pd.DataFrame,
    month: str = "2026-10",
) -> ReconciliationResult:
    """Classify ledger rows and build a bridge from platform to bank total."""
    _validate_columns(platform, PLATFORM_COLUMNS, "Platform transactions")
    _validate_columns(bank, BANK_COLUMNS, "Bank settlements")
    try:
        month_start = datetime.strptime(month, "%Y-%m").date().replace(day=1)
    except ValueError as error:
        raise ValueError(f"Month must use YYYY-MM format: {month!r}") from error
    if month_start.strftime("%Y-%m") != month:
        raise ValueError(f"Month must use YYYY-MM format: {month!r}")
    next_month = (
        month_start.replace(day=28) + timedelta(days=4)
    ).replace(day=1)
    month_end = next_month - timedelta(days=1)

    transactions = platform.copy()
    settlements = bank.copy()
    if transactions["txn_id"].duplicated().any():
        raise ValueError("Platform txn_id values must be unique")
    if settlements["settlement_id"].duplicated().any():
        raise ValueError("Bank settlement_id values must be unique")

    transactions["_amount"] = transactions["amount"].map(
        lambda value: _decimal(value, "platform amount")
    )
    transactions["_date"] = transactions["txn_date"].map(
        lambda value: _parse_date(value, "txn_date")
    )
    settlements["_amount"] = settlements["amount"].map(
        lambda value: _decimal(value, "bank amount")
    )
    settlements["_date"] = settlements["settlement_date"].map(
        lambda value: _parse_date(value, "settlement_date")
    )
    settlements = settlements[
        settlements["_date"].map(lambda value: value.strftime("%Y-%m") == month)
    ].copy()

    invalid_types = set(transactions["type"]) - {"PAYMENT", "REFUND"}
    if invalid_types:
        raise ValueError(f"Unsupported transaction types: {', '.join(sorted(invalid_types))}")
    invalid_signs = transactions.apply(
        lambda row: (
            row["type"] == "PAYMENT" and row["_amount"] <= 0
        )
        or (row["type"] == "REFUND" and row["_amount"] >= 0),
        axis=1,
    )
    if invalid_signs.any():
        raise ValueError("Payments must be positive and refunds must be negative")

    duplicate_refs = set(
        settlements.loc[
            settlements["txn_ref"].duplicated(keep=False), "txn_ref"
        ].tolist()
    )
    duplicate_indexes: set[int] = set()
    for txn_ref in duplicate_refs:
        indexes = settlements.index[settlements["txn_ref"] == txn_ref].tolist()
        duplicate_indexes.update(indexes[1:])
    primary_settlements = settlements.loc[
        [index for index in settlements.index if index not in duplicate_indexes]
    ]

    settlements_by_ref = {
        txn_ref: group
        for txn_ref, group in primary_settlements.groupby("txn_ref", sort=False)
    }
    known_txn_ids = set(transactions["txn_id"].tolist())
    records: list[DetailRecord] = []
    def add_record(
        classification: str,
        txn_id: str,
        txn_date: str,
        txn_type: str,
        settlement_id: str,
        settlement_date: str,
        platform_amount: Decimal,
        bank_amount: Decimal,
        original_txn_id: str,
    ) -> None:
        records.append(
            {
                "txn_id": txn_id,
                "txn_date": txn_date,
                "type": txn_type,
                "settlement_id": settlement_id,
                "settlement_date": settlement_date,
                "platform_amount": platform_amount,
                "bank_amount": bank_amount,
                "difference": bank_amount - platform_amount,
                "original_txn_id": original_txn_id,
                "classification": classification,
            }
        )

    for _, transaction in transactions.iterrows():
        txn_id = transaction["txn_id"]
        txn_date = transaction["_date"]
        platform_amount = transaction["_amount"]
        matches = settlements_by_ref.get(txn_id)
        if matches is not None:
            settlement = matches.iloc[0]
            bank_amount = settlement["_amount"]
            original_txn_id = transaction["original_txn_id"]
            if (
                transaction["type"] == "REFUND"
                and original_txn_id not in known_txn_ids
            ):
                classification = "ORPHAN_REFUND"
            elif bank_amount == platform_amount:
                classification = "MATCHED"
            elif abs(bank_amount - platform_amount) < ROUNDING_TOLERANCE:
                classification = "ROUNDING"
            else:
                classification = "MISSING_UNEXPLAINED"
            add_record(
                classification,
                txn_id,
                txn_date.isoformat(),
                transaction["type"],
                settlement["settlement_id"],
                settlement["_date"].isoformat(),
                platform_amount,
                bank_amount,
                original_txn_id,
            )
        else:
            timing_window_crosses_month = (
                transaction["type"] == "PAYMENT"
                and txn_date + timedelta(days=2) > month_end
            )
            classification = (
                "TIMING_NEXT_MONTH"
                if timing_window_crosses_month
                else "MISSING_UNEXPLAINED"
            )
            add_record(
                classification,
                txn_id,
                txn_date.isoformat(),
                transaction["type"],
                "",
                "",
                platform_amount,
                Decimal("0.00"),
                transaction["original_txn_id"],
            )

        if txn_id in duplicate_refs:
            for index in sorted(duplicate_indexes):
                duplicate = settlements.loc[index]
                if duplicate["txn_ref"] != txn_id:
                    continue
                add_record(
                    "DUPLICATE",
                    txn_id,
                    txn_date.isoformat(),
                    transaction["type"],
                    duplicate["settlement_id"],
                    duplicate["_date"].isoformat(),
                    Decimal("0.00"),
                    duplicate["_amount"],
                    transaction["original_txn_id"],
                )

    for _, settlement in settlements.iterrows():
        if settlement.name in duplicate_indexes:
            if settlement["txn_ref"] not in known_txn_ids:
                add_record(
                    "DUPLICATE",
                    settlement["txn_ref"],
                    "",
                    "",
                    settlement["settlement_id"],
                    settlement["_date"].isoformat(),
                    Decimal("0.00"),
                    settlement["_amount"],
                    "",
                )
            continue
        if settlement["txn_ref"] in known_txn_ids:
            continue
        add_record(
            "MISSING_UNEXPLAINED",
            settlement["txn_ref"],
            "",
            "",
            settlement["settlement_id"],
            settlement["_date"].isoformat(),
            Decimal("0.00"),
            settlement["_amount"],
            "",
        )

    detail = pd.DataFrame.from_records(
        records,
        columns=[
            "txn_id",
            "txn_date",
            "type",
            "settlement_id",
            "settlement_date",
            "platform_amount",
            "bank_amount",
            "difference",
            "original_txn_id",
            "classification",
        ],
    )
    platform_total = _sum_decimals(transactions["_amount"], "platform amount")
    bank_total = _sum_decimals(settlements["_amount"], "bank amount")
    adjustments: dict[str, Decimal] = {}
    for gap_type in GAP_TYPES:
        adjustment = Decimal("0.00")
        for record in records:
            if record["classification"] == gap_type:
                adjustment += record["difference"]
        adjustments[gap_type] = adjustment
    row_counts = {
        gap_type: sum(
            record["classification"] == gap_type for record in records
        )
        for gap_type in GAP_TYPES
    }
    calculated_bank_total = platform_total
    for adjustment in adjustments.values():
        calculated_bank_total += adjustment
    leftover = calculated_bank_total - bank_total
    if leftover != 0:
        raise ArithmeticError(
            f"Reconciliation bridge does not balance: leftover {leftover}"
        )

    bridge_rows: list[dict[str, object]] = [
        {
            "line_item": "PLATFORM_TOTAL",
            "row_count": len(transactions),
            "amount": platform_total,
        }
    ]
    bridge_rows.extend(
        {
            "line_item": gap_type,
            "row_count": row_counts[gap_type],
            "amount": adjustments[gap_type],
        }
        for gap_type in GAP_TYPES
    )
    bridge_rows.extend(
        [
            {
                "line_item": "CALCULATED_BANK_TOTAL",
                "row_count": len(settlements),
                "amount": calculated_bank_total,
            },
            {
                "line_item": "ACTUAL_BANK_TOTAL",
                "row_count": len(settlements),
                "amount": bank_total,
            },
            {
                "line_item": "LEFTOVER",
                "row_count": 0,
                "amount": leftover.quantize(CENT),
            },
        ]
    )
    return ReconciliationResult(
        detail=detail,
        bridge=pd.DataFrame.from_records(bridge_rows),
        platform_total=platform_total,
        bank_total=bank_total,
        calculated_bank_total=calculated_bank_total,
        leftover=leftover,
    )


def reconcile_files(
    platform_path: Path | str,
    bank_path: Path | str,
    month: str = "2026-10",
) -> ReconciliationResult:
    return reconcile_data(load_csv(platform_path), load_csv(bank_path), month)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", type=Path, default=Path("platform_transactions.csv"))
    parser.add_argument("--bank", type=Path, default=Path("bank_settlements.csv"))
    parser.add_argument("--month", default="2026-10")
    parser.add_argument("--output-csv", type=Path)
    args = parser.parse_args()

    result = reconcile_files(args.platform, args.bank, args.month)
    print(result.bridge.to_string(index=False))
    print("\nGap counts:")
    print(result.detail["classification"].value_counts().reindex(GAP_TYPES, fill_value=0))
    if args.output_csv:
        result.detail.loc[
            result.detail["classification"] != "MATCHED"
        ].to_csv(args.output_csv, index=False, quoting=csv.QUOTE_MINIMAL)
        print(f"\nWrote gap rows to {args.output_csv}")


if __name__ == "__main__":
    main()
