# Payments reconciliation tool

A small Streamlit app for comparing October 2026 platform transactions with
bank settlements. Monetary values are read and calculated as `Decimal`; pandas
is used for tabular input and output.

## Setup

From the project directory, install the runtime and test dependencies:

```powershell
python -m pip install pandas streamlit pytest
```

## Generate the sample ledgers

```powershell
python generate_data.py
```

This writes `platform_transactions.csv`, `bank_settlements.csv`, and
`planted_gaps.json` in the current directory. The generator defaults to seed
42 and can write to another directory with `--output-dir`.

The October bank file contains 1–2 calendar-day settlements dated in October.
The timing-gap transactions have November settlement dates recorded in
`planted_gaps.json` and are excluded from the October bank file.

## Run reconciliation

```powershell
python reconcile.py
```

The command prints the bridge and classifications. To save only gap rows:

```powershell
python reconcile.py --output-csv reconciliation_gaps.csv
```

## Launch the app

```powershell
streamlit run app.py
```

Use **Generate October test data (seed 42)**, then **Run reconciliation**.
The app's generated files are stored in the `data` directory. The bridge
summary and filterable gap table appear below the controls; use the download
button to export the currently selected gap rows.

## Run tests

```powershell
python -m pytest -q
```

## Assumptions

- INR is the only currency.
- The reconciliation match key is `txn_id` on the platform and `txn_ref` at
  the bank.
- Settlement lag is 1–2 calendar days; weekends do not alter the day count.
- The reporting period is the calendar month, October 1–31, 2026. Bank rows
  are included according to `settlement_date`, not transaction date.
- Refund amounts are negative, with `original_txn_id` identifying the original
  payment. A refund pointing to an unknown transaction is flagged as
  `ORPHAN_REFUND`.
- The per-row rounding tolerance is strictly less than INR 0.01. Rounding
  differences are retained in the bridge rather than discarded.
- Bank amounts are rounded to two decimal places using decimal
  round-half-up when generating the sample rounding rows.
- Missing transactions whose 1–2 day settlement window crosses calendar
  month-end are classified as `TIMING_NEXT_MONTH`. A duplicate bank row is
  separated from the primary match and contributes its amount to the bridge.
- A refund may be an `ORPHAN_REFUND` and still contribute zero to the bridge
  when its amount is present on both ledgers; this is a reference-integrity
  issue, not a balance difference.
