"""Generate deterministic October 2026 platform and bank reconciliation data."""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


CENT = Decimal("0.01")


def _money(value: Decimal) -> str:
    return format(value, "f")


def generate_data(output_dir: Path | str = ".", seed: int = 42) -> dict[str, Any]:
    """Write the two ledgers and planted-gap manifest, returning the manifest."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    transactions: list[dict[str, str]] = []
    payments: list[dict[str, str]] = []

    def add_payment(txn_id: str, txn_date: date, amount: Decimal) -> None:
        row = {
            "txn_id": txn_id,
            "txn_date": txn_date.isoformat(),
            "customer_id": f"C{rng.randint(1, 60):03d}",
            "type": "PAYMENT",
            "amount": _money(amount),
            "original_txn_id": "",
        }
        transactions.append(row)
        payments.append(row)

    timing_ids = ["T0001", "T0002", "T0003"]
    for txn_id, txn_date, amount in zip(
        timing_ids,
        (date(2026, 10, 30), date(2026, 10, 30), date(2026, 10, 31)),
        (Decimal("125.00"), Decimal("82.45"), Decimal("240.00")),
    ):
        add_payment(txn_id, txn_date, amount)

    rounding_ids = [f"T{number:04d}" for number in range(4, 44)]
    for txn_id in rounding_ids:
        txn_date = date(2026, 10, rng.randint(1, 28))
        amount = Decimal(rng.randint(20, 350)) + Decimal("0.333")
        add_payment(txn_id, txn_date, amount)

    missing_id = "T0044"
    add_payment(missing_id, date(2026, 10, 10), Decimal("175.00"))

    orphan_id = "T0045"
    transactions.append(
        {
            "txn_id": orphan_id,
            "txn_date": date(2026, 10, 14).isoformat(),
            "customer_id": f"C{rng.randint(1, 60):03d}",
            "type": "REFUND",
            "amount": "-37.50",
            "original_txn_id": "NONEXISTENT-TXN",
        }
    )

    for number in range(46, 201):
        txn_id = f"T{number:04d}"
        txn_date = date(2026, 10, rng.randint(1, 28))
        if len(payments) > 0 and rng.random() < 0.08:
            original = rng.choice(payments)
            original_amount = Decimal(original["amount"])
            refund_amount = (original_amount * Decimal("0.25")).quantize(CENT)
            transactions.append(
                {
                    "txn_id": txn_id,
                    "txn_date": txn_date.isoformat(),
                    "customer_id": original["customer_id"],
                    "type": "REFUND",
                    "amount": _money(-refund_amount),
                    "original_txn_id": original["txn_id"],
                }
            )
        else:
            whole_amount = Decimal(rng.randint(15, 500))
            cents = Decimal(rng.randint(0, 99)) / Decimal(100)
            add_payment(txn_id, txn_date, whole_amount + cents)

    missing_ids = {missing_id}
    timing_lags = {"T0001": 2, "T0002": 2, "T0003": 1}
    settlement_rows: list[dict[str, str]] = []
    settlement_counter = 1
    duplicate_txn_ref = "T0046"

    for transaction in transactions:
        txn_id = transaction["txn_id"]
        if txn_id in missing_ids:
            continue
        txn_date = date.fromisoformat(transaction["txn_date"])
        lag = timing_lags[txn_id] if txn_id in timing_lags else rng.randint(1, 2)
        settlement_date = txn_date + timedelta(days=lag)
        amount = Decimal(transaction["amount"])
        if txn_id in rounding_ids:
            amount = amount.quantize(CENT, rounding=ROUND_HALF_UP)
        settlement_rows.append(
            {
                "settlement_id": f"S{settlement_counter:04d}",
                "txn_ref": txn_id,
                "settlement_date": settlement_date.isoformat(),
                "amount": _money(amount),
            }
        )
        if txn_id == duplicate_txn_ref:
            settlement_rows.append(
                {
                    "settlement_id": f"S{settlement_counter + 1:04d}",
                    "txn_ref": txn_id,
                    "settlement_date": settlement_date.isoformat(),
                    "amount": _money(amount),
                }
            )
            settlement_counter += 1
        settlement_counter += 1

    october_settlements = [
        row
        for row in settlement_rows
        if date.fromisoformat(row["settlement_date"]).month == 10
    ]

    with (destination / "platform_transactions.csv").open(
        "w", newline="", encoding="utf-8"
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "txn_id",
                "txn_date",
                "customer_id",
                "type",
                "amount",
                "original_txn_id",
            ],
        )
        writer.writeheader()
        writer.writerows(transactions)

    with (destination / "bank_settlements.csv").open(
        "w", newline="", encoding="utf-8"
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["settlement_id", "txn_ref", "settlement_date", "amount"],
        )
        writer.writeheader()
        writer.writerows(october_settlements)

    planted_gaps: dict[str, Any] = {
        "seed": seed,
        "month": "2026-10",
        "timing_next_month_txn_ids": timing_ids,
        "timing_next_month_settlement_dates": {
            txn_id: (
                date.fromisoformat(
                    next(row["txn_date"] for row in transactions if row["txn_id"] == txn_id)
                )
                + timedelta(days=lag)
            ).isoformat()
            for txn_id, lag in timing_lags.items()
        },
        "rounding_txn_ids": rounding_ids,
        "duplicate_txn_ref": duplicate_txn_ref,
        "orphan_refund_txn_id": orphan_id,
        "orphan_original_txn_id": "NONEXISTENT-TXN",
        "missing_unexplained_txn_id": missing_id,
    }
    (destination / "planted_gaps.json").write_text(
        json.dumps(planted_gaps, indent=2) + "\n", encoding="utf-8"
    )
    return planted_gaps


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    gaps = generate_data(args.output_dir, args.seed)
    print(f"Generated 200 platform transactions in {args.output_dir}")
    print(f"Generated October bank settlements and planted_gaps.json")
    print(
        "Planted gaps: "
        f"{len(gaps['timing_next_month_txn_ids'])} timing, "
        f"{len(gaps['rounding_txn_ids'])} rounding, 1 duplicate, "
        "1 orphan refund, 1 unexplained missing payment"
    )


if __name__ == "__main__":
    main()
