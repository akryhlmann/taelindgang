import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


def _try_import_gspread():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        return True, gspread, Credentials
    except ImportError:
        return False, None, None


SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GoogleSheetsSync:
    def __init__(
        self,
        credentials_file: str,
        spreadsheet_id: str,
        worksheet_name: str = "Visitor Counts",
    ):
        self._credentials_file = credentials_file
        self._spreadsheet_id = spreadsheet_id
        self._worksheet_name = worksheet_name
        self._client = None
        self._spreadsheet = None
        self._worksheet = None
        self._summary_sheet = None
        self._mock_mode = False

        available, self._gspread, self._Credentials = _try_import_gspread()
        if not available:
            logger.warning("gspread not available, GoogleSheetsSync in mock mode")
            self._mock_mode = True
        else:
            self._connect()

    def _connect(self) -> None:
        try:
            creds = self._Credentials.from_service_account_file(
                self._credentials_file, scopes=SCOPES
            )
            self._client = self._gspread.authorize(creds)
            self._spreadsheet = self._client.open_by_key(self._spreadsheet_id)
            self._worksheet = self._get_or_create_worksheet(self._worksheet_name)
            self._summary_sheet = self._get_or_create_worksheet("Summary")
            logger.info("Connected to Google Sheets: %s", self._spreadsheet_id)
        except Exception as exc:
            logger.error("Google Sheets connection failed: %s", exc)
            self._client = None

    def _get_or_create_worksheet(self, name: str):
        try:
            return self._spreadsheet.worksheet(name)
        except self._gspread.exceptions.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(title=name, rows=10000, cols=10)
            if name == self._worksheet_name:
                ws.append_row(
                    ["Timestamp", "Device ID", "Count In", "Count Out", "Total"],
                    value_input_option="RAW",
                )
            elif name == "Summary":
                ws.append_row(
                    ["Metric", "Value", "Updated At"],
                    value_input_option="RAW",
                )
            return ws

    def test_connection(self) -> bool:
        if self._mock_mode:
            logger.info("Google Sheets mock connection: OK")
            return True
        try:
            if self._client is None:
                self._connect()
            if self._client is None:
                return False
            _ = self._spreadsheet.title
            return True
        except Exception as exc:
            logger.error("Google Sheets connection test failed: %s", exc)
            return False

    def sync(self, data: List[dict]) -> None:
        if self._mock_mode:
            logger.info("Google Sheets mock sync: %d rows", len(data))
            return
        if not data:
            return
        if self._client is None:
            self._connect()
        if self._client is None:
            logger.warning("Google Sheets sync skipped (not connected)")
            return
        try:
            import datetime
            rows = []
            for item in data:
                ts = item.get("timestamp", 0)
                dt_str = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                rows.append([
                    dt_str,
                    item.get("device_id", ""),
                    item.get("count_in", 0),
                    item.get("count_out", 0),
                    item.get("total", 0),
                ])
            self._worksheet.append_rows(rows, value_input_option="RAW")
            logger.info("Synced %d rows to Google Sheets", len(rows))
        except Exception as exc:
            logger.error("Google Sheets sync error: %s", exc)

    def update_summary_row(
        self,
        current_total: int,
        peak: int,
        total_in: int,
        total_out: int,
    ) -> None:
        if self._mock_mode:
            logger.info(
                "Google Sheets mock summary: current=%d peak=%d in=%d out=%d",
                current_total, peak, total_in, total_out,
            )
            return
        if self._client is None:
            self._connect()
        if self._client is None:
            return
        try:
            import datetime
            updated_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            self._summary_sheet.clear()
            self._summary_sheet.append_row(["Metric", "Value", "Updated At"])
            self._summary_sheet.append_rows([
                ["Current Visitors", current_total, updated_at],
                ["Peak Visitors", peak, updated_at],
                ["Total In", total_in, updated_at],
                ["Total Out", total_out, updated_at],
            ])
            logger.debug("Summary sheet updated")
        except Exception as exc:
            logger.error("Google Sheets summary update error: %s", exc)
