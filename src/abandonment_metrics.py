"""
abandonment_metrics.py
─────────────────────────────────────────────────────────────────────────────
Aggregation layer for abandonment metrics.

Reads the structured output from process_session_data.py, filters to
interactive sessions (is_non_interactive == FALSE), and produces a ranked
frequency table of abandonment questions — showing which point in the
info-collecting bot flow drives the most drop-off.

Designed to feed directly into PowerBI as a summary table, or to be pushed
to BigQuery for trend tracking across reporting periods.

Configuration (via .env):
  METRICS_INPUT_CSV — output CSV from process_session_data.py

GCP integration (optional):
  from gcp.bigquery_utils import push_abandonment_metrics
  push_abandonment_metrics(metrics_df)
─────────────────────────────────────────────────────────────────────────────
"""

import os

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

METRICS_INPUT_CSV = os.getenv("METRICS_INPUT_CSV", "output/session_metrics.csv")


def compute_abandonment_metrics(input_file: str) -> pd.DataFrame:
    """
    Load session metrics CSV and return a ranked abandonment frequency table.

    Returns:
        DataFrame with columns: abandoned_I_bot_question, Count, Percentage
    """
    df = pd.read_csv(input_file)

    # Normalise boolean column
    df["is_non_interactive"] = df["is_non_interactive"].astype(str).str.strip().str.upper()

    # Filter to interactive sessions only
    interactive = df[df["is_non_interactive"] == "FALSE"]

    # Extract and clean the abandonment question column
    valid = interactive["abandoned_I_bot_question"].dropna()
    valid = valid[valid.str.strip() != ""]

    counts      = valid.value_counts()
    percentages = (counts / counts.sum() * 100).round(2)

    metrics_df = pd.DataFrame({
        "Count":      counts,
        "Percentage": percentages,
    })

    return metrics_df


def main():
    print(f"Computing abandonment metrics from: {METRICS_INPUT_CSV}\n")

    metrics_df = compute_abandonment_metrics(METRICS_INPUT_CSV)
    print(metrics_df.to_string())

    # ── Optional: push to BigQuery ────────────────────────────────────────────
    # from gcp.bigquery_utils import push_abandonment_metrics
    # push_abandonment_metrics(metrics_df.reset_index())


if __name__ == "__main__":
    main()
