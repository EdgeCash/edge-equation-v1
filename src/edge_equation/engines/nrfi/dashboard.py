"""Streamlit dashboard for the NRFI/YRFI engine.

Run with:

    streamlit run nrfi/dashboard.py

Falls back to a plain CLI table if Streamlit is missing.
"""

from __future__ import annotations

import json
from datetime import date

from .config import get_default_config
from .data.storage import NRFIStore
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
                    "`python -m nrfi.run_daily` first.")
        return

    df = df.sort_values("nrfi_prob", ascending=False)
    df["color"] = df["nrfi_pct"].apply(gradient_hex)

    def style_row(row):
        return [f"background-color: {row.color}; color: black"
                for _ in row.index]

    show_cols = ["away_team", "home_team", "nrfi_pct", "lambda_total",
                  "color_band", "signal", "mc_low", "mc_high",
                  "edge", "kelly_units"]
    show_cols = [c for c in show_cols if c in df.columns]
    st.dataframe(df[show_cols + ["color"]].style.apply(style_row, axis=1),
                  use_container_width=True, hide_index=True)

    with st.expander("SHAP top drivers per pick"):
        for _, row in df.iterrows():
            shap = row.get("shap_drivers")
            if not shap:
                continue
            try:
                pairs = json.loads(shap)
            except Exception:
                pairs = []
            st.markdown(f"**{row.away_team} @ {row.home_team}** — NRFI {row.nrfi_pct:.1f}%")
            for name, val in pairs:
                arrow = "↑" if val > 0 else "↓"
                st.write(f"  {arrow} `{name}`  ({val:+.4f})")


def _render_cli() -> None:
    cfg = get_default_config()
    store = NRFIStore(cfg.duckdb_path)
    df = store.predictions_for_date(date.today().isoformat())
    if df.empty:
        print("No predictions for today — run `python -m nrfi.run_daily` first.")
        return
    df = df.sort_values("nrfi_prob", ascending=False)
    print(df.to_string(index=False))


def main() -> int:
    if _streamlit_available():
        _render_streamlit()
    else:
        print("(streamlit not installed — falling back to CLI view)\n")
        _render_cli()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
