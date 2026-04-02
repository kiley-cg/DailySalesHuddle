from __future__ import annotations
"""
gcs_uploader.py
Uploads a PNG to a public Google Cloud Storage bucket and returns the
public URL. Used as the hosting layer before registering the asset in
OptiSigns via saveAsset(webLink: ...).
"""

import logging
import os
from pathlib import Path

from google.cloud import storage
from google.oauth2 import service_account

log = logging.getLogger(__name__)

GCS_PUBLIC_URL = "https://storage.googleapis.com/{bucket}/{blob}"


def _client(credentials_path: str) -> storage.Client:
    creds = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return storage.Client(credentials=creds)


def upload_png(png_path: str, cfg: dict) -> str:
    """
    Upload png_path to the GCS bucket and return the public HTTPS URL.
    The blob is named 'sales_snapshot_YYYY-MM-DD.png' (derived from the file name).
    A 'latest.png' alias is also written for convenience.
    """
    bucket_name = cfg["gcs_bucket"]
    creds_path = cfg.get("gcs_credentials", "gcs_credentials.json")

    client = _client(creds_path)
    bucket = client.bucket(bucket_name)

    blob_name = Path(png_path).name  # e.g. sales_snapshot_2026-04-01.png
    log.info("Uploading %s → gs://%s/%s", png_path, bucket_name, blob_name)

    blob = bucket.blob(blob_name)
    blob.upload_from_filename(png_path, content_type="image/png")
    # bucket-level allUsers:Storage Object Viewer makes this publicly readable
    log.info("Uploaded: %s", blob.public_url)

    # Also overwrite latest.png so there is always a stable URL
    latest = bucket.blob("latest.png")
    latest.upload_from_filename(png_path, content_type="image/png")
    log.info("Updated latest.png alias")

    return blob.public_url  # https://storage.googleapis.com/BUCKET/BLOB


def delete_old_snapshots(current_blob_name: str, cfg: dict):
    """Delete any sales_snapshot_*.png blobs that are not current_blob_name."""
    bucket_name = cfg["gcs_bucket"]
    creds_path = cfg.get("gcs_credentials", "gcs_credentials.json")

    client = _client(creds_path)
    bucket = client.bucket(bucket_name)

    blobs = list(client.list_blobs(bucket_name, prefix="sales_snapshot_"))
    for blob in blobs:
        if blob.name != current_blob_name:
            log.info("Deleting old GCS blob: %s", blob.name)
            blob.delete()
