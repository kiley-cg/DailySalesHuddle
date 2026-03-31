"""
gmail_watcher.py
Polls Gmail for the daily Atease leaderboard email and returns its HTML body.
Tracks processed message IDs in SQLite to avoid duplicates.
"""

import base64
import logging
import os
import sqlite3
import time
from datetime import date, datetime
from email import message_from_bytes
from pathlib import Path

import yaml
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_PATH = "token.json"
CREDENTIALS_PATH = "credentials.json"
DB_PATH = "processed.db"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_gmail_service():
    """Authenticate with OAuth2 and return a Gmail API service object."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(
                    "credentials.json not found. Download it from Google Cloud Console "
                    "(APIs & Services → Credentials → OAuth 2.0 Client ID → Download JSON)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as token_file:
            token_file.write(creds.to_json())
        log.info("OAuth2 token saved to %s", TOKEN_PATH)

    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# SQLite tracking
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS processed_messages "
        "(message_id TEXT PRIMARY KEY, processed_at TEXT)"
    )
    conn.commit()
    return conn


def is_processed(conn, message_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM processed_messages WHERE message_id = ?", (message_id,)
    ).fetchone()
    return row is not None


def mark_processed(conn, message_id: str):
    conn.execute(
        "INSERT OR IGNORE INTO processed_messages (message_id, processed_at) VALUES (?, ?)",
        (message_id, datetime.utcnow().isoformat()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Email fetching
# ---------------------------------------------------------------------------

def build_query(cfg: dict) -> str:
    sender = cfg.get("gmail_filter_sender", "").strip()
    subject = cfg.get("gmail_filter_subject", "").strip()
    parts = ["is:unread"]
    if sender:
        parts.append(f"from:{sender}")
    if subject:
        parts.append(f"subject:{subject}")
    return " ".join(parts)


def extract_html_body(service, message_id: str) -> str | None:
    """Return the HTML body of a Gmail message, or None if not found."""
    try:
        msg = service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
    except HttpError as exc:
        log.error("Failed to fetch message %s: %s", message_id, exc)
        return None

    def _walk(payload):
        mime = payload.get("mimeType", "")
        if mime == "text/html":
            data = payload.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        for part in payload.get("parts", []):
            result = _walk(part)
            if result:
                return result
        return None

    return _walk(msg.get("payload", {}))


def poll_once(cfg: dict, service, db_conn) -> str | None:
    """
    Check Gmail for a new matching email.
    Returns the HTML body string if found and unprocessed, else None.
    """
    query = build_query(cfg)
    log.debug("Gmail query: %s", query)
    try:
        result = service.users().messages().list(
            userId="me", q=query, maxResults=10
        ).execute()
    except HttpError as exc:
        log.error("Gmail list error: %s", exc)
        return None

    messages = result.get("messages", [])
    if not messages:
        log.info("No matching unread messages found.")
        return None

    for msg_stub in messages:
        mid = msg_stub["id"]
        if is_processed(db_conn, mid):
            log.debug("Message %s already processed — skipping.", mid)
            continue

        log.info("Found new matching message: %s", mid)
        html_body = extract_html_body(service, mid)
        if html_body:
            mark_processed(db_conn, mid)
            return html_body
        else:
            log.warning("Message %s had no HTML body.", mid)

    return None


def watch(cfg: dict, on_email_callback):
    """
    Main polling loop. Calls on_email_callback(html_body) whenever a new
    matching email arrives.
    """
    interval = cfg.get("check_interval_minutes", 5) * 60
    service = get_gmail_service()
    db_conn = init_db()

    log.info(
        "Watching Gmail every %d minutes for: sender=%s subject=%s",
        cfg.get("check_interval_minutes", 5),
        cfg.get("gmail_filter_sender", "(any)"),
        cfg.get("gmail_filter_subject", "(any)"),
    )

    while True:
        html_body = poll_once(cfg, service, db_conn)
        if html_body:
            try:
                on_email_callback(html_body)
            except Exception:
                log.exception("Callback raised an exception.")
        time.sleep(interval)
