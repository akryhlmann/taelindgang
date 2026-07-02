#!/usr/bin/env python3
"""
Diagnosticerer Google Sheets-forbindelsen til Device 2.

Tjekker trin for trin:
  1. gspread + google-auth installeret?
  2. Credentials-fil til stede og gyldig JSON?
  3. Kan autorisere service account?
  4. Kan åbne spreadsheet?
  5. Kan finde/oprette worksheet?
  6. Kan skrive en test-række?

Usage:
    python tools/sheets_test.py [--config device2/config.yaml]
    python tools/sheets_test.py --creds ~/config/service_account.json \
                                 --id 1YOipFBhkIKjSxegqOIpOaN9grYlBB90s_eQxi90gcT8
"""
import argparse
import json
import os
import sys
import time


SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

OK  = "\033[32m✓\033[0m"
ERR = "\033[31m✗\033[0m"
INF = "\033[33m→\033[0m"


def step(label):
    print(f"\n{INF} {label} ...", end=" ", flush=True)

def ok(msg=""):
    print(f"{OK} {msg}")

def fail(msg):
    print(f"{ERR} {msg}")
    raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="device2/config.yaml",
                        help="Path to device2/config.yaml")
    parser.add_argument("--creds", help="Override credentials file path")
    parser.add_argument("--id",    help="Override spreadsheet ID")
    parser.add_argument("--sheet", default=None, help="Override worksheet name")
    args = parser.parse_args()

    # --- Read config if no overrides ---
    creds_file = args.creds
    spreadsheet_id = args.id
    worksheet_name = args.sheet

    if not (creds_file and spreadsheet_id):
        step("Indlæser config")
        try:
            import yaml
        except ImportError:
            fail("PyYAML ikke installeret: pip install pyyaml")
        cfg_path = os.path.expanduser(args.config)
        if not os.path.exists(cfg_path):
            fail(f"Config-fil ikke fundet: {cfg_path}")
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        gs = cfg.get("google_sheets", {})
        creds_file      = creds_file      or os.path.expanduser(gs.get("credentials_file", ""))
        spreadsheet_id  = spreadsheet_id  or gs.get("spreadsheet_id", "")
        worksheet_name  = worksheet_name  or gs.get("worksheet_name", "Visitor Counts")
        ok(f"config={cfg_path}")
    else:
        creds_file     = os.path.expanduser(creds_file)
        worksheet_name = worksheet_name or "Visitor Counts"

    print(f"\n  Credentials : {creds_file}")
    print(f"  Spreadsheet : {spreadsheet_id}")
    print(f"  Worksheet   : {worksheet_name}")

    # --- 1. gspread ---
    step("Importerer gspread + google-auth")
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        ok(f"gspread {gspread.__version__}")
    except ImportError as e:
        fail(f"{e}\n  Kør: pip install gspread google-auth")

    # --- 2. Credentials-fil ---
    step("Tjekker credentials-fil")
    if not creds_file:
        fail("credentials_file er ikke sat i config.yaml")
    if not os.path.exists(creds_file):
        fail(f"Fil ikke fundet: {creds_file}")
    try:
        with open(creds_file) as f:
            data = json.load(f)
        svc_email = data.get("client_email", "(ukendt)")
        proj      = data.get("project_id",   "(ukendt)")
        ok(f"service_account={svc_email}  project={proj}")
    except json.JSONDecodeError as e:
        fail(f"Ugyldig JSON: {e}")

    # --- 3. Autoriser ---
    step("Autoriserer service account")
    try:
        creds  = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        client = gspread.authorize(creds)
        ok()
    except Exception as e:
        fail(str(e))

    # --- 4. Åbn spreadsheet ---
    step("Åbner spreadsheet")
    try:
        sh = client.open_by_key(spreadsheet_id)
        ok(f'"{sh.title}"')
    except gspread.exceptions.APIError as e:
        status = e.response.status_code if hasattr(e, "response") else "?"
        fail(
            f"API-fejl {status}: {e}\n\n"
            "  Mulige årsager:\n"
            "  • Spreadsheet delt med service account-emailen ovenfor?\n"
            "    (Del → Tilføj person → indsæt client_email, Editor)\n"
            "  • Google Sheets API aktiveret i Google Cloud Console?\n"
            "    https://console.cloud.google.com/apis/library/sheets.googleapis.com\n"
            "  • Google Drive API aktiveret?\n"
            "    https://console.cloud.google.com/apis/library/drive.googleapis.com"
        )
    except Exception as e:
        fail(str(e))

    # --- 5. Find worksheet ---
    step(f"Finder worksheet '{worksheet_name}'")
    try:
        ws = sh.worksheet(worksheet_name)
        rows = ws.row_count
        ok(f"{rows} rækker")
    except gspread.exceptions.WorksheetNotFound:
        print(f"\n  Worksheet ikke fundet — opretter den ...", end=" ", flush=True)
        try:
            ws = sh.add_worksheet(title=worksheet_name, rows=10000, cols=10)
            ws.append_row(
                ["Timestamp", "Device ID", "Count In", "Count Out", "Total"],
                value_input_option="RAW",
            )
            ok("oprettet med header")
        except Exception as e:
            fail(str(e))

    # --- 6. Skriv test-række ---
    step("Skriver test-række")
    import datetime
    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ws.append_row(
            [now_str, "TEST_DEVICE", 1, 0, 1],
            value_input_option="RAW",
        )
        ok(f"Tilføjede række med timestamp {now_str}")
    except Exception as e:
        fail(str(e))

    print(f"\n{OK} Alle trin bestået — Google Sheets-forbindelsen virker!\n")


if __name__ == "__main__":
    main()
