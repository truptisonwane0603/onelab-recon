"""Streamlit interface for the October 2026 payments reconciliation."""

from decimal import Decimal
from pathlib import Path

import streamlit as st

from generate_data import generate_data
from reconcile import GAP_TYPES, ReconciliationResult, reconcile_files


DATA_DIR = Path(__file__).resolve().parent / "data"
PLATFORM_FILE = DATA_DIR / "platform_transactions.csv"
BANK_FILE = DATA_DIR / "bank_settlements.csv"


def _format_money(value: object) -> str:
    return f"INR {format(value, 'f')}"


def _display_result(result: ReconciliationResult) -> None:
    st.subheader("Reconciliation bridge")
    metrics = st.columns(3)
    metrics[0].metric("Platform total", _format_money(result.platform_total))
    metrics[1].metric("Bank total", _format_money(result.bank_total))
    metrics[2].metric(
        "Bridge leftover",
        _format_money(result.leftover.quantize(Decimal("0.01"))),
    )

    bridge_display = result.bridge.copy()
    bridge_display["amount"] = bridge_display["amount"].map(
        lambda amount: format(amount, "f")
    )
    st.dataframe(bridge_display, width="stretch", hide_index=True)

    gap_types = [gap_type for gap_type in GAP_TYPES if gap_type != "MATCHED"]
    selected_types = st.multiselect(
        "Gap types to show",
        options=gap_types,
        default=gap_types,
    )
    gap_rows = result.detail[
        result.detail["classification"].isin(selected_types)
    ].copy()
    display_rows = gap_rows.copy()
    for column in ("platform_amount", "bank_amount", "difference"):
        display_rows[column] = display_rows[column].map(
            lambda amount: format(amount, "f")
        )
    st.dataframe(display_rows, width="stretch", hide_index=True)
    st.download_button(
        "Download gap rows as CSV",
        data=gap_rows.to_csv(index=False).encode("utf-8"),
        file_name="october_2026_reconciliation_gaps.csv",
        mime="text/csv",
    )


def main() -> None:
    st.set_page_config(page_title="Payments Reconciliation", layout="wide")
    st.title("Payments reconciliation")
    st.caption(
        "Compare October 2026 platform transactions with bank settlements "
        "using exact Decimal arithmetic."
    )

    generate_column, reconcile_column = st.columns(2)
    with generate_column:
        if st.button("Generate October test data (seed 42)"):
            gaps = generate_data(DATA_DIR, seed=42)
            st.session_state.pop("reconciliation_result", None)
            st.success(
                f"Generated 200 transactions. Planted {len(gaps['rounding_txn_ids'])} "
                "rounding rows and the requested timing, duplicate, orphan, "
                "and missing-payment gaps."
            )

    data_exists = PLATFORM_FILE.is_file() and BANK_FILE.is_file()
    with reconcile_column:
        if st.button("Run reconciliation", type="primary", disabled=not data_exists):
            try:
                st.session_state["reconciliation_result"] = reconcile_files(
                    PLATFORM_FILE, BANK_FILE
                )
            except (OSError, ValueError, ArithmeticError) as error:
                st.error(f"Reconciliation failed: {error}")
                st.stop()

    if not data_exists:
        st.info("Generate the October data to enable reconciliation.")

    result = st.session_state.get("reconciliation_result")
    if result is not None:
        _display_result(result)


if __name__ == "__main__":
    main()
