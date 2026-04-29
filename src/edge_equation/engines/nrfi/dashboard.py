"""Streamlit dashboard for the NRFI/YRFI engine.

Run with:

    streamlit run src/edge_equation/engines/nrfi/dashboard.py

Falls back to a plain CLI table if Streamlit is missing.
"""

from __future__ import annotations

import json
from datetime import date

from .config import get_default_config
from .data.storage import NRFIStore
from .ledger import render_ledger_section
from .utils.colors import gradient_hex


def _streamlit_available() -> bool:
    try:
        import streamlit  # noqa: F401
        return True
    except ImportError:
        return False


def _render_streamlit() -> None:
    import streamlit as st  # type: ignore
    import pandas as pd

    cfg = get_default_config()
    store = NRFIStore(cfg.duckdb_path)

    st.set_page_config(page_title="Edge Equation — NRFI/YRFI",
                        layout="wide", page_icon="⚾")
    st.title("Edge Equation — Elite NRFI/YRFI Engine")
    st.caption("Analytics. Not Feelings.")

    target = st.date_input("Slate date", value=date.today())
    df = store.predictions_for_date(target.isoformat())
    if df.empty:
        st.warning("No predictions stored for this date — run "
                    "`python -m edge_equation.engines.nrfi.run_daily` first.")
        return

    df = _prepare_board(df).head(6)
    df["color"] = df["nrfi_pct"].apply(gradient_hex)

    def style_row(row):
        return [f"background-color: {row.color}; color: black"
                for _ in row.index]

    show_cols = ["away_team", "home_team", "probability_display", "tier",
                  "tier_band", "nrfi_pct", "lambda_total", "mc_band_pp",
                  "edge_pp", "kelly_suggestion", "driver_text", "color_band",
                  "signal"]
    show_cols = [c for c in show_cols if c in df.columns]
    st.subheader("Top 6 by edge")
    st.dataframe(df[show_cols + ["color"]].style.apply(style_row, axis=1),
                  use_container_width=True, hide_index=True)

    ledger_text = render_ledger_section(store, season=target.year)
    if ledger_text:
        st.subheader("Per-tier YTD ledger")
        st.code(ledger_text)

    with st.expander("SHAP top drivers per pick"):
        for _, row in df.iterrows():
            shap = row.get("shap_drivers")
            if not shap:
                continue
            try:
                pairs = json.loads(shap)
            except Exception:
                pairs = []
            display = row.get("probability_display") or f"NRFI {row.nrfi_pct:.1f}%"
            st.markdown(f"**{row.away_team} @ {row.home_team}** — {display}")
            for name, val in pairs:
                arrow = "↑" if val > 0 else "↓"
                st.write(f"  {arrow} `{name}`  ({val:+.4f})")


def _render_cli() -> None:
    cfg = get_default_config()
    store = NRFIStore(cfg.duckdb_path)
    df = store.predictions_for_date(date.today().isoformat())
    if df.empty:
        print("No predictions for today — run "
              "`python -m edge_equation.engines.nrfi.run_daily` first.")
        return
    df = _prepare_board(df).head(6)
    cols = [
        "away_team", "home_team", "probability_display", "tier",
        "lambda_total", "mc_band_pp", "edge_pp", "kelly_suggestion",
        "driver_text",
    ]
    cols = [c for c in cols if c in df.columns]
    print("NRFI Elite Board — Top 6 by edge")
    print(df[cols].to_string(index=False))
    ledger_text = render_ledger_section(store, season=date.today().year)
    if ledger_text:
        print("\n" + ledger_text)


def _prepare_board(df):
    """Sort by model edge when present, otherwise by distance from 50%."""
    if "edge" in df.columns:
        fallback = (df["nrfi_prob"].astype(float) - 0.5).abs()
        df["_sort_edge"] = df["edge"].fillna(fallback)
    else:
        df["_sort_edge"] = (df["nrfi_prob"].astype(float) - 0.5).abs()
    return df.sort_values("_sort_edge", ascending=False)


def main() -> int:
    if _streamlit_available():
        _render_streamlit()
    else:
        print("(streamlit not installed — falling back to CLI view)\n")
        _render_cli()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
