"""
gcp/
────
Google Cloud Platform utilities for the conversational AI session analytics pipeline.

Modules:
  storage_utils   — upload / download session data files via Cloud Storage
  bigquery_utils  — push processed metrics to BigQuery for BI / trending

Authentication:
  Set GOOGLE_APPLICATION_CREDENTIALS in .env pointing to your service account
  key file, or use Application Default Credentials (ADC) if running on GCP.
"""
