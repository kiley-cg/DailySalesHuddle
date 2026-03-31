"""
main.py
Wires together the full pipeline:
  Gmail → table_parser → renderer → OptiSigns → notify
"""

import logging
import os
import sys
from datetime import datetime, time as dtime
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Bootstrap: ensure output and log directories exist before logging setup
# ---------------------------------------------------------------------------

def _load_cfg() -> dict:
    cfg_path = Path("config.yaml")
    if not cfg_path.exists():
        print("ERROR: config.yaml not found. Run from the sales_snapshot/ directory.")
        sys.exit(1)
    with open(cfg_path) as f:
        return yaml.safe_load(f)


CFG = _load_cfg()

Path(CFG.get("output_folder", "output")).mkdir(parents=True, exist_ok=True)
log_dir = Path(CFG.get("log_folder", "logs"))
log_dir.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging():
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_dir / f"{today}.log"
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )


_setup_logging()
log = logging.getLogger(__name__)

# Deferred imports (after logging)
import gmail_watcher
import table_parser
import renderer
import optisigns_client

# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _notify(title: str, message: str):
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name="Sales Huddle",
            timeout=10,
        )
    except Exception as exc:
        log.debug("Desktop notification unavailable: %s", exc)


def notify_success(date_str: str):
    _notify("✅ Sales Huddle updated", f"Dashboard refreshed for {date_str}")


def notify_failure():
    _notify("⚠️ Sales Huddle update FAILED", "Check logs for details.")


def notify_no_email():
    _notify("⚠️ No daily email received", "Screen was not updated — check your inbox.")


# ---------------------------------------------------------------------------
# No-email deadline check
# ---------------------------------------------------------------------------

def _past_deadline(cfg: dict) -> bool:
    """Return True if we are past the no-email alert time on a weekday."""
    now = datetime.now()
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    alert_str = cfg.get("no_email_alert_time", "08:45")
    h, m = map(int, alert_str.split(":"))
    return now.time() >= dtime(h, m)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(html_body: str):
    today = datetime.now().strftime("%Y-%m-%d")
    log.info("=== Pipeline start for %s ===", today)

    # Step 2: Parse
    log.info("Parsing email HTML…")
    data = table_parser.parse(html_body)
    log.info(
        "Parsed: %d rows, YTD=%s",
        len(data.get("rows", [])),
        data.get("metrics", {}).get("ytd", {}).get("Sales YTD", "?"),
    )

    # Step 3: Render
    log.info("Rendering dashboard PNG…")
    png_path = renderer.render(data, CFG)
    log.info("PNG saved: %s", png_path)

    # Step 4: Push to OptiSigns
    log.info("Pushing to OptiSigns…")
    asset_id = optisigns_client.push_to_optisigns(png_path, CFG)
    log.info("OptiSigns asset _id: %s", asset_id)

    # Step 5: Notify
    notify_success(today)
    log.info("=== Pipeline complete for %s ===", today)


def on_email(html_body: str):
    try:
        run_pipeline(html_body)
    except Exception:
        log.exception("Pipeline failed.")
        notify_failure()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info("Starting Sales Huddle automation.")

    email_received = {"flag": False}

    def _callback(html_body: str):
        email_received["flag"] = True
        on_email(html_body)

    # Wrap watch in a deadline-check thread
    import threading

    def _deadline_watcher():
        import time
        while True:
            time.sleep(60)
            if not email_received["flag"] and _past_deadline(CFG):
                log.warning("No email received by deadline — sending alert.")
                notify_no_email()
                # Only alert once per day; stop monitoring until next run
                break

    t = threading.Thread(target=_deadline_watcher, daemon=True)
    t.start()

    # Blocking poll loop
    gmail_watcher.watch(CFG, _callback)


if __name__ == "__main__":
    main()
