from __future__ import annotations
"""
setup_optisigns.py
One-time script: creates a web page asset pointing to the Netlify dashboard
and pushes it to the CG Optisign Player screen.

Run once:
    python setup_optisigns.py
"""

import json
import logging
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

GRAPHQL_URL = "https://graphql-gateway.optisigns.com/graphql"
DASHBOARD_URL = "https://cg-sales-dashboard.netlify.app/"
SCREEN_NAME = "CG Optisign Player"
ASSET_NAME = "CG Sales Dashboard"


def headers() -> dict:
    token = os.getenv("OPTISIGNS_API_KEY", "")
    if not token:
        sys.exit("ERROR: OPTISIGNS_API_KEY not set in .env")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def gql(query: str, variables: dict | None = None) -> dict:
    resp = requests.post(
        GRAPHQL_URL,
        json={"query": query, "variables": variables or {}},
        headers=headers(),
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        sys.exit(f"GraphQL error: {json.dumps(body['errors'], indent=2)}")
    return body.get("data", {})


def create_webpage_asset() -> str:
    """Create (or find existing) web page asset pointing to the dashboard URL."""
    log.info("Creating web page asset: '%s'", ASSET_NAME)
    mutation = """
    mutation SaveAsset($payload: AssetInput!) {
      saveAsset(payload: $payload) {
        _id
        originalFileName
      }
    }
    """
    data = gql(mutation, {"payload": {
        "type": "webpage",
        "webLink": DASHBOARD_URL,
        "webType": "webpage",
        "originalFileName": ASSET_NAME,
        "refreshInterval": 3600,   # refresh the page every hour
        "duration": 0,             # display indefinitely
    }})
    asset_id = data["saveAsset"]["_id"]
    log.info("Asset created: _id=%s", asset_id)
    return asset_id


def find_screen() -> dict:
    log.info("Looking for screen: '%s'", SCREEN_NAME)
    data = gql("{ devices(query: {}) { page { edges { node { _id deviceName currentScheduleId currentType } } } } }")
    edges = data.get("devices", {}).get("page", {}).get("edges", [])
    for e in edges:
        n = e["node"]
        if n.get("deviceName", "").strip().lower() == SCREEN_NAME.strip().lower():
            log.info("Found: _id=%s", n["_id"])
            return n
    names = [e["node"].get("deviceName") for e in edges]
    sys.exit(f"Screen '{SCREEN_NAME}' not found. Available: {names}")


def push_to_screen(device: dict, asset_id: str):
    log.info("Pushing asset to screen '%s'…", SCREEN_NAME)
    mutation = """
    mutation PushToScreens($payload: PushToScreensInput!) {
      pushToScreens(payload: $payload) {
        _id
      }
    }
    """
    gql(mutation, {"payload": {
        "deviceIds": [device["_id"]],
        "currentAssetId": asset_id,
        "currentType": "ASSET",
        "type": "NOW",
    }})
    log.info("Pushed!")


if __name__ == "__main__":
    print(f"\nSetting up OptiSigns to display:\n  {DASHBOARD_URL}\non screen: {SCREEN_NAME}\n")
    asset_id = create_webpage_asset()
    device = find_screen()
    push_to_screen(device, asset_id)
    print(f"\nDone! Asset _id: {asset_id}")
    print("The screen should now show the Netlify dashboard.")
    print("OptiSigns will refresh the page every hour automatically.")
