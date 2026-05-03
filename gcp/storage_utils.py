"""
gcp/storage_utils.py
─────────────────────────────────────────────────────────────────────────────
Cloud Storage utilities for the session analytics pipeline.

Pull raw session exports from a shared GCS bucket and push processed
metric outputs back for downstream consumption by PowerBI or Looker.

Configuration (via .env):
  GCP_PROJECT_ID, GCS_BUCKET_NAME, GCS_INPUT_PREFIX, GCS_OUTPUT_PREFIX
  GOOGLE_APPLICATION_CREDENTIALS — path to service account key (gitignored)
─────────────────────────────────────────────────────────────────────────────
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from google.cloud import storage

load_dotenv()

GCP_PROJECT_ID    = os.getenv("GCP_PROJECT_ID")
GCS_BUCKET_NAME   = os.getenv("GCS_BUCKET_NAME")
GCS_INPUT_PREFIX  = os.getenv("GCS_INPUT_PREFIX",  "session-analytics/input/")
GCS_OUTPUT_PREFIX = os.getenv("GCS_OUTPUT_PREFIX", "session-analytics/output/")


def _client() -> storage.Client:
    return storage.Client(project=GCP_PROJECT_ID)


def upload_file(local_path: str, blob_name: str, bucket_name: str = None, prefix: str = "") -> str:
    """Upload a local file to GCS. Returns the full gs:// URI."""
    bucket_name    = bucket_name or GCS_BUCKET_NAME
    full_blob_name = f"{prefix}{blob_name}" if prefix else blob_name
    bucket         = _client().bucket(bucket_name)
    bucket.blob(full_blob_name).upload_from_filename(local_path)
    uri = f"gs://{bucket_name}/{full_blob_name}"
    print(f"Uploaded: {local_path} → {uri}")
    return uri


def download_file(blob_name: str, local_path: str, bucket_name: str = None, prefix: str = "") -> str:
    """Download a GCS blob to a local file. Returns the local path."""
    bucket_name    = bucket_name or GCS_BUCKET_NAME
    full_blob_name = f"{prefix}{blob_name}" if prefix else blob_name
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    _client().bucket(bucket_name).blob(full_blob_name).download_to_filename(local_path)
    print(f"Downloaded: gs://{bucket_name}/{full_blob_name} → {local_path}")
    return local_path


def list_blobs(prefix: str = None, bucket_name: str = None) -> list[str]:
    """List blob names in a bucket, optionally filtered by prefix."""
    bucket_name = bucket_name or GCS_BUCKET_NAME
    return [b.name for b in _client().list_blobs(bucket_name, prefix=prefix)]


def upload_output(local_path: str, blob_name: str = None, bucket_name: str = None) -> str:
    """Convenience: upload a file to the configured output prefix."""
    blob_name = blob_name or Path(local_path).name
    return upload_file(local_path, blob_name, bucket_name=bucket_name, prefix=GCS_OUTPUT_PREFIX)


def download_input(blob_name: str, local_path: str = None, bucket_name: str = None) -> str:
    """Convenience: download a file from the configured input prefix."""
    local_path = local_path or f"data/{blob_name}"
    return download_file(blob_name, local_path, bucket_name=bucket_name, prefix=GCS_INPUT_PREFIX)
