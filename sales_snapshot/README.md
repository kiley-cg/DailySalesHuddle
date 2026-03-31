# Sales Huddle Automation

Watches Gmail for the Color Graphics Daily Leaderboard email (Atease Systems),
renders a branded 1152×1080 dashboard PNG, and pushes it to the
**Main Left zone** of the "Sales Huddle" OptiSigns screen.

---

## Quick-start

### 1. Prerequisites

```bash
cd sales_snapshot
pip install -r requirements.txt
```

### 2. Google Cloud Console (Gmail API)

1. Go to https://console.cloud.google.com → **APIs & Services → Library**
2. Enable **Gmail API**
3. **Credentials → Create Credentials → OAuth 2.0 Client ID**
   - Application type: **Desktop app**
   - Name: `Sales Huddle`
4. Download the JSON → save as `credentials.json` in this folder
5. First run will open a browser window for consent — approve it once

### 3. OptiSigns API key

1. Log in at https://app.optisigns.com
2. **Account → API → Generate API Key**
3. Copy the key into `.env`:

```
OPTISIGNS_API_KEY=your_key_here
```

### 4. Assets

```
assets/CG_Primary.png          ← place your CG_Primary.png here
assets/fonts/Barlow-Bold.ttf
assets/fonts/Barlow-SemiBold.ttf
assets/fonts/Barlow-Regular.ttf
```

Download Barlow from Google Fonts:
https://fonts.google.com/specimen/Barlow → Download family → extract TTFs

### 5. Configure

Edit `config.yaml`:
```yaml
gmail_filter_sender: "reports@atease.com"   # adjust to actual sender
gmail_filter_subject: "Daily Leaderboard"   # adjust to actual subject
```

### 6. Verify OptiSigns screen

```bash
python optisigns_client.py
```

Should list your devices including "Sales Huddle".

### 7. Render a preview (dummy data)

```bash
python renderer.py
# → output/sales_snapshot_YYYY-MM-DD.png
# → output/latest.png
```

Open `output/latest.png` to review the design before going live.

### 8. Run

```bash
python main.py
```

---

## Background service

### macOS (launchd)

Create `~/Library/LaunchAgents/com.colorgraphics.saleshuddle.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.colorgraphics.saleshuddle</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/python3</string>
    <string>/path/to/sales_snapshot/main.py</string>
  </array>
  <key>WorkingDirectory</key><string>/path/to/sales_snapshot</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/path/to/sales_snapshot/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>/path/to/sales_snapshot/logs/launchd.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.colorgraphics.saleshuddle.plist
```

### Windows (Task Scheduler)

Create a Basic Task:
- Trigger: At log on (or At startup)
- Action: Start a program → `python.exe`
- Arguments: `C:\path\to\sales_snapshot\main.py`
- Start in: `C:\path\to\sales_snapshot`

---

## File structure

```
sales_snapshot/
├── main.py               ← entry point
├── gmail_watcher.py      ← Gmail OAuth2 poll loop
├── table_parser.py       ← BeautifulSoup email parser
├── renderer.py           ← Pillow PNG renderer
├── optisigns_client.py   ← OptiSigns GraphQL client
├── config.yaml           ← all configuration
├── .env                  ← OPTISIGNS_API_KEY (gitignored)
├── credentials.json      ← Gmail OAuth2 (gitignored)
├── token.json            ← auto-generated after first auth (gitignored)
├── processed.db          ← SQLite: processed message IDs (gitignored)
├── assets/
│   ├── CG_Primary.png
│   └── fonts/
├── output/               ← rendered PNGs (gitignored)
└── logs/                 ← daily log files (gitignored)
```
