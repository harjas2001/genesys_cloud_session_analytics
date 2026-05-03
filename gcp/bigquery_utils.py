"""
gcp/bigquery_utils.py
─────────────────────────────────────────────────────────────────────────────
BigQuery utilities for the session analytics pipeline.

Push processed session metrics and abandonment summaries to BigQuery for
long-term storage, trend tracking, and BI consumption (PowerBI, Looker).

Configuration (via .env):
  GCP_PROJECT_ID, BQ_DATASET_ID, BQ_TABLE_SESSION_METRICS,
  BQ_TABLE_ABANDONMENT, GOOGLE_APPLICATION_CREDENTIALS
─────────────────────────────────────────────────────────────────────────────
"""

import os
import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery

load_dotenv()

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
BQ_DATASET_ID  = os.getenv("BQ_DATASET_ID", "conversational_ai")


def _client() -> bigquery.Client:
    return bigquery.Client(project=GCP_PROJECT_ID)


def push_dataframe(
    df: pd.DataFrame,
    table_id: str,
    dataset_id: str = None,
    project_id: str = None,
    if_exists: str = "append",
) -> None:
    """
    Push a DataFrame to a BigQuery table.

    Args:
        df:         DataFrame to upload.
        table_id:   Target table name.
        dataset_id: BQ dataset (defaults to BQ_DATASET_ID).
        project_id: GCP project (defaults to GCP_PROJECT_ID).
        if_exists:  'append' (default), 'replace', or 'fail'.
    """
    project_id     = project_id or GCP_PROJECT_ID
    dataset_id     = dataset_id or BQ_DATASET_ID
    full_table_id  = f"{project_id}.{dataset_id}.{table_id}"

    disposition_map = {
        "append":  bigquery.WriteDisposition.WRITE_APPEND,
        "replace": bigquery.WriteDisposition.WRITE_TRUNCATE,
        "fail":    bigquery.WriteDisposition.WRITE_EMPTY,
    }

    job_config = bigquery.LoadJobConfig(
        write_disposition=disposition_map.get(if_exists, bigquery.WriteDisposition.WRITE_APPEND),
        autodetect=True,
    )

    job = _client().load_table_from_dataframe(df, full_table_id, job_config=job_config)
    job.result()
    table = _client().get_table(full_table_id)
    print(f"Pushed {len(df):,} rows → {full_table_id} (total: {table.num_rows:,})")


def query_to_dataframe(query: str, project_id: str = None) -> pd.DataFrame:
    """Run a SQL query and return results as a DataFrame."""
    project_id = project_id or GCP_PROJECT_ID
    df = _client().query(query, project=project_id).to_dataframe()
    print(f"Query returned {len(df):,} rows.")
    return df


def ensure_dataset_exists(dataset_id: str = None, location: str = "australia-southeast1") -> None:
    """Create the BQ dataset if it does not already exist."""
    dataset_id     = dataset_id or BQ_DATASET_ID
    full_dataset_id = f"{GCP_PROJECT_ID}.{dataset_id}"
    dataset         = bigquery.Dataset(full_dataset_id)
    dataset.location = location
    _client().create_dataset(dataset, exists_ok=True)
    print(f"Dataset ready: {full_dataset_id} ({location})")


def push_session_metrics(df: pd.DataFrame) -> None:
    """Convenience: push session metrics output to its configured BQ table."""
    table_id = os.getenv("BQ_TABLE_SESSION_METRICS", "session_metrics")
    push_dataframe(df, table_id=table_id)


def push_abandonment_metrics(df: pd.DataFrame) -> None:
    """Convenience: push abandonment metrics summary to its configured BQ table."""
    table_id = os.getenv("BQ_TABLE_ABANDONMENT", "abandonment_metrics")
    push_dataframe(df, table_id=table_id)
