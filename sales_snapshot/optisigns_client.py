from typing import Optional
"""
optisigns_client.py
Uploads a PNG to OptiSigns, assigns it to the Main Left zone of the
"Sales Huddle" screen, and cleans up old snapshot assets.

API: https://graphql-gateway.optisigns.com/graphql
Auth: Bearer token from .env → OPTISIGNS_API_KEY
"""

import logging
import os
import re
import time
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

GRAPHQL_URL = "https://graphql-gateway.optisigns.com/graphql"
MAX_RETRIES = 3
BACKOFF_SECONDS = [5, 10, 20]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers() -> dict:
    token = os.getenv("OPTISIGNS_API_KEY", "")
    if not token:
        raise EnvironmentError("OPTISIGNS_API_KEY is not set in .env")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _gql(query: str, variables: Optional[dict] = None) -> dict:
    """Execute a GraphQL request with retry/backoff."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                GRAPHQL_URL, json=payload, headers=_headers(), timeout=30
            )
            if not resp.ok:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            body = resp.json()
            if "errors" in body:
                raise RuntimeError(f"GraphQL errors: {body['errors']}")
            return body.get("data", {})
        except Exception as exc:
            log.warning("Attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES - 1:
                wait = BACKOFF_SECONDS[attempt]
                log.info("Retrying in %ds…", wait)
                time.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# Step 1 — Upload asset
# ---------------------------------------------------------------------------

def upload_asset(png_path: str, asset_name: str) -> str:
    """
    Upload a PNG file to OptiSigns media library.
    Returns the new asset _id.
    """
    log.info("Uploading asset '%s' from %s", asset_name, png_path)

    # Step 1a: request a pre-signed upload URL
    mutation = """
    mutation CreateUploadUrl($name: String!, $contentType: String!) {
      createUploadUrl(name: $name, contentType: $contentType) {
        uploadUrl
        fileId
      }
    }
    """
    data = _gql(mutation, {"name": asset_name, "contentType": "image/png"})
    upload_url = data["createUploadUrl"]["uploadUrl"]
    file_id = data["createUploadUrl"]["fileId"]

    # Step 1b: PUT the PNG to the pre-signed URL
    with open(png_path, "rb") as f:
        put_resp = requests.put(
            upload_url,
            data=f,
            headers={"Content-Type": "image/png"},
            timeout=60,
        )
    put_resp.raise_for_status()
    log.info("Uploaded PNG (fileId=%s)", file_id)

    # Step 1c: confirm the upload and get the asset _id
    confirm_mutation = """
    mutation ConfirmUpload($fileId: String!, $name: String!) {
      confirmUpload(fileId: $fileId, name: $name) {
        _id
        name
      }
    }
    """
    confirm_data = _gql(confirm_mutation, {"fileId": file_id, "name": asset_name})
    asset_id = confirm_data["confirmUpload"]["_id"]
    log.info("Asset confirmed: _id=%s name='%s'", asset_id, asset_name)
    return asset_id


# ---------------------------------------------------------------------------
# Step 2 — Find the Sales Huddle screen
# ---------------------------------------------------------------------------

def find_screen(screen_name: str) -> dict:
    """
    Return the device/screen object for the given screen name.
    Raises ValueError if not found.
    """
    query = """
    query ListDevices($search: String) {
      devices(search: $search, limit: 50) {
        items {
          _id
          name
          schedule {
            zones {
              name
              items {
                _id
                assetId
              }
            }
          }
        }
      }
    }
    """
    data = _gql(query, {"search": screen_name})
    items = data.get("devices", {}).get("items", [])
    for device in items:
        if device.get("name", "").strip().lower() == screen_name.strip().lower():
            log.info("Found screen: _id=%s name='%s'", device["_id"], device["name"])
            return device
    raise ValueError(
        f"Screen '{screen_name}' not found. "
        f"Available: {[d.get('name') for d in items]}"
    )


# ---------------------------------------------------------------------------
# Step 3 — Update Main Left zone
# ---------------------------------------------------------------------------

def update_zone_asset(screen: dict, zone_name: str, new_asset_id: str):
    """
    Replace the asset in the named zone of the given screen.
    The Main Right zone is never modified.
    """
    zones = screen.get("schedule", {}).get("zones", [])
    target_zone = None
    for zone in zones:
        if zone.get("name", "").strip().lower() == zone_name.strip().lower():
            target_zone = zone
            break

    if target_zone is None:
        raise ValueError(
            f"Zone '{zone_name}' not found on screen '{screen['name']}'. "
            f"Available zones: {[z.get('name') for z in zones]}"
        )

    zone_items = target_zone.get("items", [])
    if not zone_items:
        raise ValueError(f"Zone '{zone_name}' has no items to replace.")

    # Replace the first slot in the zone
    slot_id = zone_items[0]["_id"]

    mutation = """
    mutation UpdateScheduleItem($itemId: ID!, $assetId: ID!) {
      updateScheduleItem(itemId: $itemId, assetId: $assetId) {
        _id
        assetId
      }
    }
    """
    result = _gql(mutation, {"itemId": slot_id, "assetId": new_asset_id})
    log.info(
        "Zone '%s' updated: slot _id=%s → assetId=%s",
        zone_name,
        slot_id,
        new_asset_id,
    )
    return result


# ---------------------------------------------------------------------------
# Step 4 — Delete yesterday's snapshot asset
# ---------------------------------------------------------------------------

def delete_old_snapshots(today_asset_id: str):
    """
    Query all assets whose name matches 'Sales Snapshot *' and delete
    any that are not today's asset.
    """
    query = """
    query ListAssets($search: String) {
      assets(search: $search, limit: 100) {
        items {
          _id
          name
        }
      }
    }
    """
    data = _gql(query, {"search": "Sales Snapshot"})
    items = data.get("assets", {}).get("items", [])

    pattern = re.compile(r"^Sales Snapshot \d{4}-\d{2}-\d{2}$", re.I)
    delete_mutation = """
    mutation DeleteAsset($id: ID!) {
      deleteAsset(id: $id) {
        _id
      }
    }
    """
    deleted = 0
    for asset in items:
        if not pattern.match(asset.get("name", "")):
            continue
        if asset["_id"] == today_asset_id:
            log.debug("Keeping today's asset: %s", asset["name"])
            continue
        log.info("Deleting old snapshot: _id=%s name='%s'", asset["_id"], asset["name"])
        try:
            _gql(delete_mutation, {"id": asset["_id"]})
            deleted += 1
        except Exception as exc:
            log.warning("Failed to delete %s: %s", asset["_id"], exc)

    log.info("Deleted %d old snapshot(s).", deleted)


# ---------------------------------------------------------------------------
# Public API — full update sequence
# ---------------------------------------------------------------------------

def push_to_optisigns(png_path: str, cfg: dict) -> str:
    """
    Full sequence:
      1. Upload PNG as 'Sales Snapshot YYYY-MM-DD'
      2. Find the target screen
      3. Update Main Left zone
      4. Delete old snapshots

    Returns the new asset _id.
    """
    today = date.today().strftime("%Y-%m-%d")
    asset_name = f"Sales Snapshot {today}"
    screen_name = cfg.get("optisigns_screen_name", "Sales Huddle")
    zone_name = cfg.get("optisigns_zone_name", "Main Left")

    asset_id = upload_asset(png_path, asset_name)
    screen = find_screen(screen_name)
    update_zone_asset(screen, zone_name, asset_id)
    delete_old_snapshots(asset_id)

    log.info("OptiSigns update complete. Asset _id: %s", asset_id)
    return asset_id


# ---------------------------------------------------------------------------
# CLI: list devices (Step 6 verification)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import json

    logging.basicConfig(level=logging.INFO)
    load_dotenv()

    # Try several query shapes to find the correct OptiSigns schema
    queries = [
        ("screens",  "query { screens(limit:50) { items { _id name } } }"),
        ("devices",  "query { devices(limit:50) { items { _id name } } }"),
        ("getScreens", "query { getScreens { _id name } }"),
        ("screenList", "query { screenList { _id name } }"),
    ]

    for label, query in queries:
        print(f"Trying '{label}' query…")
        try:
            resp = requests.post(
                GRAPHQL_URL,
                json={"query": query},
                headers=_headers(),
                timeout=15,
            )
            print(f"  HTTP {resp.status_code}")
            body = resp.json()
            if "errors" in body:
                print(f"  GraphQL error: {body['errors'][0].get('message','?')}")
                continue
            data = body.get("data", {})
            items = (data.get(label) or {}).get("items") or data.get(label) or []
            if items:
                print(f"  ✓ Found {len(items)} screen(s):")
                for s in items:
                    print(f"    _id={s.get('_id','?')}  name='{s.get('name','?')}'")
                break
            else:
                print(f"  No items in response: {json.dumps(data)[:200]}")
        except Exception as exc:
            print(f"  Error: {exc}")
