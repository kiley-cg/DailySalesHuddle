from __future__ import annotations
"""
optisigns_client.py
Uploads a PNG to OptiSigns, pushes it to the Main Left zone of the
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


def _gql(query: str, variables: dict | None = None) -> dict:
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

    # Step 1a: get pre-signed upload options
    # getFileUploadOptions returns a JSONObject scalar — no subfield selection
    query = """
    query GetFileUploadOptions($name: String!, $contentType: String!) {
      getFileUploadOptions(name: $name, contentType: $contentType)
    }
    """
    data = _gql(query, {"name": asset_name, "contentType": "image/png"})
    upload_opts = data["getFileUploadOptions"]
    log.info("getFileUploadOptions response: %s", upload_opts)
    upload_url = upload_opts["uploadUrl"]
    file_url = upload_opts["fileUrl"]
    # fields may be a dict of extra form fields for S3 multipart, or null
    fields = upload_opts.get("fields") or {}

    log.info("Got upload URL, fileUrl=%s", file_url)

    # Step 1b: PUT/POST the PNG to the pre-signed URL
    # If there are extra fields it's a multipart POST (S3), otherwise a plain PUT
    if fields:
        with open(png_path, "rb") as f:
            form_data = {k: (None, v) for k, v in fields.items()}
            form_data["file"] = (asset_name, f, "image/png")
            put_resp = requests.post(upload_url, files=form_data, timeout=60)
    else:
        with open(png_path, "rb") as f:
            put_resp = requests.put(
                upload_url,
                data=f,
                headers={"Content-Type": "image/png"},
                timeout=60,
            )
    put_resp.raise_for_status()
    log.info("PNG uploaded successfully")

    # Step 1c: register the asset in OptiSigns library
    mutation = """
    mutation SaveAsset($payload: AssetInput!) {
      saveAsset(payload: $payload) {
        _id
        name
      }
    }
    """
    asset_payload = {
        "name": asset_name,
        "type": "image",
        "fileUrl": file_url,
        "contentType": "image/png",
    }
    confirm_data = _gql(mutation, {"payload": asset_payload})
    asset_id = confirm_data["saveAsset"]["_id"]
    log.info("Asset saved: _id=%s name='%s'", asset_id, asset_name)
    return asset_id


# ---------------------------------------------------------------------------
# Step 2 — Find the Sales Huddle screen
# ---------------------------------------------------------------------------

def find_screen(screen_name: str) -> dict:
    """
    Return the device node for the given screen name.
    Raises ValueError if not found.
    """
    query = """
    query ListDevices($query: DeviceQueryInput) {
      devices(query: $query) {
        page {
          edges {
            node {
              _id
              deviceName
              currentScheduleId
              currentType
            }
          }
        }
      }
    }
    """
    data = _gql(query, {"query": {}})
    edges = data.get("devices", {}).get("page", {}).get("edges", [])
    devices = [e["node"] for e in edges]

    for device in devices:
        if device.get("deviceName", "").strip().lower() == screen_name.strip().lower():
            log.info("Found screen: _id=%s deviceName='%s'", device["_id"], device["deviceName"])
            return device

    raise ValueError(
        f"Screen '{screen_name}' not found. "
        f"Available: {[d.get('deviceName') for d in devices]}"
    )


# ---------------------------------------------------------------------------
# Step 3 — Push asset to screen (NOW playback)
# ---------------------------------------------------------------------------

def push_asset_to_screen(device: dict, asset_id: str):
    """
    Use pushToScreens to immediately display the asset on the device.
    This overrides the current schedule for an instant push.
    """
    mutation = """
    mutation PushToScreens($payload: PushToScreensInput!) {
      pushToScreens(payload: $payload) {
        _id
      }
    }
    """
    push_payload = {
        "deviceIds": [device["_id"]],
        "currentAssetId": asset_id,
        "currentType": "ASSET",
        "type": "NOW",
    }
    result = _gql(mutation, {"payload": push_payload})
    log.info("Pushed asset %s to screen %s", asset_id, device["_id"])
    return result


# ---------------------------------------------------------------------------
# Step 3b — Update schedule item in Main Left zone (alternative)
# ---------------------------------------------------------------------------

def update_zone_asset(device: dict, zone_name: str, asset_id: str):
    """
    Find the schedule item in the named zone and update its asset.
    Falls back to pushToScreens if zone-based update isn't available.
    """
    schedule_id = device.get("currentScheduleId")
    if not schedule_id:
        log.warning("Device has no currentScheduleId; using pushToScreens instead.")
        return push_asset_to_screen(device, asset_id)

    # Query schedule items for the zone
    query = """
    query GetScheduleItems($query: QueryScheduleItemsInput!) {
      scheduleItems(query: $query) {
        page {
          edges {
            node {
              _id
              assetId
              zoneName
            }
          }
        }
      }
    }
    """
    data = _gql(query, {"query": {"scheduleId": schedule_id}})
    edges = data.get("scheduleItems", {}).get("page", {}).get("edges", [])
    items = [e["node"] for e in edges]

    target_item = None
    for item in items:
        if item.get("zoneName", "").strip().lower() == zone_name.strip().lower():
            target_item = item
            break

    if target_item is None:
        log.warning(
            "Zone '%s' not found in schedule (zones: %s); using pushToScreens.",
            zone_name,
            [i.get("zoneName") for i in items],
        )
        return push_asset_to_screen(device, asset_id)

    # Update the schedule item
    mutation = """
    mutation UpdateScheduleItem($payload: UpdateScheduleItemInput!) {
      updateScheduleItem(payload: $payload) {
        _id
        assetId
      }
    }
    """
    result = _gql(mutation, {"payload": {"_id": target_item["_id"], "assetId": asset_id}})
    log.info(
        "Zone '%s' updated: item _id=%s → assetId=%s",
        zone_name,
        target_item["_id"],
        asset_id,
    )
    return result


# ---------------------------------------------------------------------------
# Step 4 — Delete yesterday's snapshot asset
# ---------------------------------------------------------------------------

def delete_old_snapshots(today_asset_id: str):
    """
    Query all assets whose name matches 'Sales Snapshot YYYY-MM-DD' and
    delete any that are not today's asset.
    """
    query = """
    query ListAssets($query: AssetQueryInput) {
      assets(query: $query) {
        page {
          edges {
            node {
              _id
              name
            }
          }
        }
      }
    }
    """
    data = _gql(query, {"query": {"search": "Sales Snapshot"}})
    edges = data.get("assets", {}).get("page", {}).get("edges", [])
    items = [e["node"] for e in edges]

    pattern = re.compile(r"^Sales Snapshot \d{4}-\d{2}-\d{2}$", re.I)
    delete_mutation = """
    mutation DeleteObjects($payload: DeleteObjectInput!) {
      deleteObjects(payload: $payload) {
        success
      }
    }
    """
    to_delete = []
    for asset in items:
        if not pattern.match(asset.get("name", "")):
            continue
        if asset["_id"] == today_asset_id:
            log.debug("Keeping today's asset: %s", asset["name"])
            continue
        to_delete.append(asset["_id"])
        log.info("Queued for deletion: _id=%s name='%s'", asset["_id"], asset["name"])

    if to_delete:
        try:
            _gql(delete_mutation, {"payload": {"ids": to_delete, "type": "ASSET"}})
            log.info("Deleted %d old snapshot(s).", len(to_delete))
        except Exception as exc:
            log.warning("Failed to delete old snapshots: %s", exc)
    else:
        log.info("No old snapshots to delete.")


# ---------------------------------------------------------------------------
# Public API — full update sequence
# ---------------------------------------------------------------------------

def push_to_optisigns(png_path: str, cfg: dict) -> str:
    """
    Full sequence:
      1. Upload PNG as 'Sales Snapshot YYYY-MM-DD'
      2. Find the target screen
      3. Update Main Left zone (or push directly)
      4. Delete old snapshots

    Returns the new asset _id.
    """
    today = date.today().strftime("%Y-%m-%d")
    asset_name = f"Sales Snapshot {today}"
    screen_name = cfg.get("optisigns_screen_name", "Sales Huddle")
    zone_name = cfg.get("optisigns_zone_name", "Main Left")

    asset_id = upload_asset(png_path, asset_name)
    device = find_screen(screen_name)
    update_zone_asset(device, zone_name, asset_id)
    delete_old_snapshots(asset_id)

    log.info("OptiSigns update complete. Asset _id: %s", asset_id)
    return asset_id


# ---------------------------------------------------------------------------
# CLI diagnostics
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import json

    logging.basicConfig(level=logging.INFO)
    load_dotenv()

    cmd = sys.argv[1] if len(sys.argv) > 1 else "list-devices"

    if cmd == "list-devices":
        print("Listing devices…")
        query = """
        query {
          devices(query: {}) {
            page {
              edges {
                node {
                  _id
                  deviceName
                  currentScheduleId
                  currentType
                }
              }
            }
          }
        }
        """
        try:
            resp = requests.post(
                GRAPHQL_URL,
                json={"query": query},
                headers=_headers(),
                timeout=15,
            )
            print(f"HTTP {resp.status_code}")
            body = resp.json()
            if "errors" in body:
                print(f"GraphQL error: {json.dumps(body['errors'], indent=2)}")
            else:
                edges = body.get("data", {}).get("devices", {}).get("page", {}).get("edges", [])
                if edges:
                    print(f"Found {len(edges)} device(s):")
                    for e in edges:
                        n = e["node"]
                        print(f"  _id={n['_id']}  name='{n.get('deviceName','?')}'  scheduleId={n.get('currentScheduleId','?')}")
                else:
                    print(f"No devices found. Raw: {json.dumps(body.get('data'), indent=2)}")
        except Exception as exc:
            print(f"Error: {exc}")

    elif cmd == "list-assets":
        print("Listing assets matching 'Sales Snapshot'…")
        query = """
        query {
          assets(query: { search: "Sales Snapshot" }) {
            page {
              edges {
                node { _id name }
              }
            }
          }
        }
        """
        try:
            resp = requests.post(GRAPHQL_URL, json={"query": query}, headers=_headers(), timeout=15)
            print(f"HTTP {resp.status_code}")
            body = resp.json()
            if "errors" in body:
                print(f"GraphQL error: {json.dumps(body['errors'], indent=2)}")
            else:
                edges = body.get("data", {}).get("assets", {}).get("page", {}).get("edges", [])
                print(f"Found {len(edges)} asset(s):")
                for e in edges:
                    n = e["node"]
                    print(f"  _id={n['_id']}  name='{n.get('name','?')}'")
        except Exception as exc:
            print(f"Error: {exc}")

    elif cmd == "test-auth":
        print("Testing authentication…")
        query = "query { me { _id email } }"
        try:
            resp = requests.post(GRAPHQL_URL, json={"query": query}, headers=_headers(), timeout=15)
            print(f"HTTP {resp.status_code}")
            body = resp.json()
            print(json.dumps(body, indent=2))
        except Exception as exc:
            print(f"Error: {exc}")

    elif cmd == "test-upload":
        png = sys.argv[2] if len(sys.argv) > 2 else "output/latest.png"
        if not os.path.exists(png):
            print(f"PNG not found: {png}")
            print("Usage: python optisigns_client.py test-upload [path/to/file.png]")
            sys.exit(1)
        print(f"Testing full upload+push with {png}…")
        import yaml
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
        try:
            asset_id = push_to_optisigns(png, cfg)
            print(f"\nSUCCESS — asset _id: {asset_id}")
            print("Check your OptiSigns screen — it should now show the PNG.")
        except Exception as exc:
            print(f"\nFAILED: {exc}")

    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python optisigns_client.py [list-devices | list-assets | test-auth | test-upload [file.png]]")
