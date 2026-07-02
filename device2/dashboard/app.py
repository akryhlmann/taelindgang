import base64
import datetime
import logging
import mimetypes
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Børneland brand colors — used for KPI card borders and chart traces
_BL_RED    = "#D11416"
_BL_TEAL   = "#2BA8B0"
_BL_ORANGE = "#F5A623"
_BL_PINK   = "#E91E8C"
_BL_BLUE   = "#4FC3F7"
BRAND_COLORS = [_BL_RED, _BL_TEAL, _BL_ORANGE, _BL_PINK, _BL_BLUE]


def _load_logo(cfg: dict) -> Optional[str]:
    """Load logo from dashboard.logo_path and return a base64 data URI, or None."""
    path = cfg.get("dashboard", {}).get("logo_path", "")
    if not path:
        return None
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        logger.warning("Logo not found: %s", path)
        return None
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"

# Danish day names for display
_DA = {
    "monday": "Mandag", "tuesday": "Tirsdag", "wednesday": "Onsdag",
    "thursday": "Torsdag", "friday": "Fredag", "saturday": "Lørdag",
    "sunday": "Søndag",
}


def _parse_schedule(schedule: dict) -> list:
    """Return list of (day_en, open_dt_today, close_dt_today) sorted by weekday.
    Uses this week's actual calendar dates."""
    DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    today = datetime.date.today()
    # Find Monday of the current week
    monday = today - datetime.timedelta(days=today.weekday())
    result = []
    for day_en in DAY_ORDER:
        if day_en not in schedule:
            continue
        sched = schedule[day_en]
        day_idx = DAY_ORDER.index(day_en)
        day_date = monday + datetime.timedelta(days=day_idx)
        open_h,  open_m  = map(int, sched["open"].split(":"))
        close_h, close_m = map(int, sched["close"].split(":"))
        open_dt  = datetime.datetime(day_date.year, day_date.month, day_date.day,
                                     open_h, open_m, 0)
        close_dt = datetime.datetime(day_date.year, day_date.month, day_date.day,
                                     close_h, close_m, 0)
        result.append({
            "day_en":   day_en,
            "day_da":   _DA.get(day_en, day_en.capitalize()),
            "date":     day_date,
            "open_dt":  open_dt,
            "close_dt": close_dt,
            "open_ts":  open_dt.timestamp(),
            "close_ts": close_dt.timestamp(),
        })
    return result


def _time_ago(ts):
    if ts is None:
        return "–"
    diff = int(time.time() - ts)
    if diff < 60:
        return f"{diff} sek. siden"
    if diff < 3600:
        return f"{diff // 60} min. siden"
    return f"{diff // 3600} t. siden"


def _rssi_badge(rssi, dbc):
    if rssi is None:
        return dbc.Badge("–", color="secondary", className="ms-2")
    if rssi >= -70:
        color, label = "success", "Fremragende"
    elif rssi >= -85:
        color, label = "warning", "God"
    elif rssi >= -100:
        color, label = "orange", "Middel"
    else:
        color, label = "danger", "Svag"
    return dbc.Badge(f"{rssi} dBm  {label}", color=color, className="ms-2")


class DashApp:
    def __init__(self, storage, config: dict, sheets=None):
        self._storage = storage
        self._cfg = config
        self._sheets = sheets
        self._app = None
        self._setup_app()

    def _setup_app(self) -> None:
        try:
            import dash
            from dash import dcc, html, Input, Output, dash_table
            import dash_bootstrap_components as dbc
            import plotly.graph_objects as go
            import pandas as pd
            import datetime as _dt
        except ImportError as exc:
            logger.error("Dashboard dependencies not available: %s", exc)
            return

        dash_cfg  = self._cfg.get("dashboard", {})
        event_cfg = self._cfg.get("event", {})
        refresh_ms = dash_cfg.get("refresh_interval", 30) * 1000
        event_name = event_cfg.get("name", "Event")
        schedule = event_cfg.get("schedule", {})

        logo_src = _load_logo(self._cfg)

        _assets = os.path.join(os.path.dirname(__file__), "assets")
        app = dash.Dash(
            __name__,
            assets_folder=_assets,
            external_stylesheets=[dbc.themes.BOOTSTRAP],
            title=f"{event_name} — Besøgende",
        )
        self._app = app

        # Build tab list from schedule
        days = _parse_schedule(schedule)
        tab_ids = [d["day_en"] for d in days] + ["oversigt"]

        def make_day_tab(d):
            return dbc.Tab(
                label=f"{d['day_da']} {d['date'].strftime('%d/%m')}",
                tab_id=d["day_en"],
            )

        tabs = [make_day_tab(d) for d in days] + [
            dbc.Tab(label="Alle dage", tab_id="oversigt")
        ]

        app.layout = dbc.Container(
            [
                dcc.Interval(id="interval", interval=refresh_ms, n_intervals=0),
                dbc.Row(
                    dbc.Col(
                        html.Div(
                            [
                                html.Img(
                                    src=logo_src,
                                    style={
                                        "height": "90px",
                                        "objectFit": "contain",
                                        "marginRight": "20px",
                                    },
                                ) if logo_src else None,
                                html.H1(
                                    event_name,
                                    className="text-primary fw-bold mb-0",
                                    style={"fontSize": "2.4rem"},
                                ),
                            ],
                            className="d-flex align-items-center justify-content-center my-4",
                        )
                    )
                ),
                # ── Enhedsvælger ──
                dbc.Row(
                    dbc.Col(
                        dbc.InputGroup(
                            [
                                dbc.InputGroupText("Enhed"),
                                dcc.Dropdown(
                                    id="device-filter",
                                    options=[{"label": "Alle enheder", "value": "__all__"}],
                                    value="__all__",
                                    clearable=False,
                                    style={"minWidth": "220px", "flex": "1"},
                                ),
                            ],
                            className="w-auto",
                        ),
                        width="auto",
                    ),
                    justify="end",
                    className="mb-3",
                ),
                # ── Top KPI-kort (altid synlige, viser aktuel dag) ──
                dbc.Row(
                    [
                        dbc.Col(dbc.Card(dbc.CardBody([
                            html.H6("Besøgende nu", className="card-subtitle text-muted"),
                            html.H2(id="kpi-current", className="card-title display-4",
                                    style={"color": _BL_RED}),
                        ]), className="shadow text-center",
                           style={"borderTop": f"4px solid {_BL_RED}"}), md=4),
                        dbc.Col(dbc.Card(dbc.CardBody([
                            html.H6("Ind i dag", className="card-subtitle text-muted"),
                            html.H2(id="kpi-in", className="card-title display-4",
                                    style={"color": _BL_TEAL}),
                        ]), className="shadow text-center",
                           style={"borderTop": f"4px solid {_BL_TEAL}"}), md=4),
                        dbc.Col(dbc.Card(dbc.CardBody([
                            html.H6("Ud i dag", className="card-subtitle text-muted"),
                            html.H2(id="kpi-out", className="card-title display-4",
                                    style={"color": _BL_ORANGE}),
                        ]), className="shadow text-center",
                           style={"borderTop": f"4px solid {_BL_ORANGE}"}), md=4),
                    ],
                    className="mb-3",
                ),
                html.Div(id="event-status-bar", className="mb-3"),
                # ── Dagsfaner ──
                dbc.Card(
                    dbc.CardBody([
                        dbc.Tabs(
                            tabs,
                            id="day-tabs",
                            active_tab="oversigt",
                        ),
                        html.Div(id="tab-content", className="mt-3"),
                    ]),
                    className="shadow mb-4",
                ),
                # ── LoRa + Google Sheets status ──
                dbc.Row(
                    [
                        dbc.Col(dbc.Card(dbc.CardBody([
                            html.H5("LoRa signalstatus", className="card-title"),
                            html.Div(id="lora-status"),
                        ]), className="shadow"), md=6),
                        dbc.Col(dbc.Card(dbc.CardBody([
                            html.H5("Google Sheets synkronisering", className="card-title"),
                            html.Div(id="sheets-status"),
                        ]), className="shadow"), md=6),
                    ],
                    className="mb-4",
                ),
            ],
            fluid=True,
        )

        # ── Populate device dropdown ──
        @app.callback(
            Output("device-filter", "options"),
            Input("interval", "n_intervals"),
        )
        def update_device_options(n):
            devices = self._storage.get_all_devices()
            opts = [{"label": "Alle enheder", "value": "__all__"}]
            opts += [{"label": d, "value": d} for d in devices]
            return opts

        # ── Auto-select correct day tab on load ──
        @app.callback(
            Output("day-tabs", "active_tab"),
            Input("interval", "n_intervals"),
        )
        def set_active_tab(n):
            if n > 0:
                raise dash.exceptions.PreventUpdate
            today_en = _dt.datetime.now().strftime("%A").lower()
            if today_en in tab_ids:
                return today_en
            return "oversigt"

        # ── Main callback ──
        @app.callback(
            Output("kpi-current",      "children"),
            Output("kpi-in",           "children"),
            Output("kpi-out",          "children"),
            Output("event-status-bar", "children"),
            Output("tab-content",      "children"),
            Output("lora-status",      "children"),
            Output("sheets-status",    "children"),
            Input("interval",       "n_intervals"),
            Input("day-tabs",       "active_tab"),
            Input("device-filter",  "value"),
        )
        def update_all(n, active_tab, device_filter):
            now_dt  = _dt.datetime.now()
            today_en = now_dt.strftime("%A").lower()
            selected_device = None if device_filter in (None, "__all__") else device_filter

            # Today's event window for the KPI bar
            today_sched = schedule.get(today_en, {})
            if today_sched:
                oh, om = map(int, today_sched["open"].split(":"))
                ch, cm = map(int, today_sched["close"].split(":"))
                today_open  = now_dt.replace(hour=oh, minute=om, second=0, microsecond=0)
                today_close = now_dt.replace(hour=ch, minute=cm, second=0, microsecond=0)
                today_stats = self._storage.get_stats_for_period(
                    today_open.timestamp(), today_close.timestamp(),
                    device_id=selected_device,
                )
            else:
                today_stats = {"current": 0, "total_in": 0, "total_out": 0}

            kpi_current = str(today_stats["current"])
            kpi_in      = str(today_stats["total_in"])
            kpi_out     = str(today_stats["total_out"])

            # ── Event status bar ──
            if today_sched:
                if now_dt < today_open:
                    mins = int((today_open - now_dt).total_seconds() // 60)
                    status_bar = dbc.Alert(
                        f"⏳  Event åbner om {mins} minutter ({today_sched['open']})",
                        color="info", className="mb-0 py-2",
                    )
                elif now_dt <= today_close:
                    mins_left = int((today_close - now_dt).total_seconds() // 60)
                    status_bar = dbc.Alert(
                        f"🟢  Event er åbent — lukker om {mins_left} minutter ({today_sched['close']})",
                        color="success", className="mb-0 py-2",
                    )
                else:
                    status_bar = dbc.Alert(
                        f"🔴  Event lukket for i dag ({today_sched['close']})",
                        color="secondary", className="mb-0 py-2",
                    )
            else:
                status_bar = dbc.Alert(
                    "Ingen event planlagt i dag", color="light", className="mb-0 py-2"
                )

            # ── Tab content ──
            if active_tab == "oversigt":
                tab_content = _make_overview_tab(
                    days, self._storage, go, _dt, dbc, html, dash_table,
                    device_id=selected_device,
                )
            else:
                day_info = next((d for d in days if d["day_en"] == active_tab), None)
                if day_info:
                    tab_content = _make_day_tab_content(
                        day_info, self._storage, go, _dt, dbc, html, dash_table,
                        device_id=selected_device,
                    )
                else:
                    tab_content = html.P("Ingen data.", className="text-muted")

            # ── LoRa status ──
            lora_stats = self._storage.get_device_lora_stats()
            if lora_stats:
                lora_cards = dbc.Row([
                    dbc.Col(
                        dbc.Card(dbc.CardBody([
                            html.H6(s["device_id"], className="card-subtitle text-muted mb-2"),
                            html.Div([
                                html.Span("Seneste pakke:", className="text-muted me-1"),
                                html.Strong(_time_ago(s["last_seen"])),
                            ], className="mb-1"),
                            html.Div([
                                html.Span("Seneste RSSI:", className="text-muted me-1"),
                                _rssi_badge(s["latest_rssi"], dbc),
                            ], className="mb-1"),
                            html.Div([
                                html.Span("Gns. RSSI (10 pkter):", className="text-muted me-1"),
                                _rssi_badge(
                                    round(s["avg_rssi"]) if s["avg_rssi"] is not None else None,
                                    dbc
                                ),
                            ]),
                        ]), className="shadow-sm"),
                        md=4, className="mb-2",
                    )
                    for s in lora_stats
                ])
            else:
                lora_cards = html.P("Ingen LoRa-enheder registreret endnu.", className="text-muted")

            # ── Google Sheets status ──
            sheets = self._sheets
            if sheets is None:
                sheets_card = html.P("Google Sheets sync ikke konfigureret.", className="text-muted")
            else:
                if sheets._mock_mode:
                    conn_badge = dbc.Badge("Mock tilstand", color="secondary")
                    error_div  = html.Small("gspread ikke installeret", className="text-muted")
                elif sheets.connected:
                    conn_badge = dbc.Badge("Forbundet", color="success")
                    error_div  = html.Span()
                else:
                    conn_badge = dbc.Badge("Ikke forbundet", color="danger")
                    err_text   = (sheets.last_error or "Ukendt fejl")[:80]
                    error_div  = html.Small(err_text, className="text-danger d-block mt-1")

                sync_text = (
                    f"{_time_ago(sheets.last_sync_time)}  ({sheets.last_sync_rows} rækker)"
                    if sheets.last_sync_time else "Ikke synkroniseret endnu"
                )
                sheets_card = html.Div([
                    html.Div([
                        html.Span("Status:", className="text-muted me-2"),
                        conn_badge,
                    ], className="mb-2"),
                    html.Div([
                        html.Span("Seneste sync:", className="text-muted me-1"),
                        html.Strong(sync_text),
                    ], className="mb-1"),
                    error_div,
                ])

            return (kpi_current, kpi_in, kpi_out,
                    status_bar, tab_content,
                    lora_cards, sheets_card)

    def run(self, host: str = "0.0.0.0", port: int = 8050, debug: bool = False) -> None:
        if self._app is None:
            logger.error("Dash app not initialized, cannot run dashboard")
            return
        dash_cfg = self._cfg.get("dashboard", {})
        self._app.run(
            host=dash_cfg.get("host", host),
            port=dash_cfg.get("port", port),
            debug=debug,
        )

    @property
    def server(self):
        if self._app:
            return self._app.server
        return None


# ── Helper: content for a single day tab ──────────────────────────────────────

def _make_day_tab_content(day_info, storage, go, _dt, dbc, html, dash_table, device_id=None):
    import datetime
    now_dt = _dt.datetime.now()
    is_future = day_info["date"] > now_dt.date()
    is_today  = day_info["date"] == now_dt.date()

    stats = storage.get_stats_for_period(
        day_info["open_ts"], day_info["close_ts"], device_id=device_id
    )
    ts_data = storage.get_timeseries_for_period(
        day_info["open_ts"], day_info["close_ts"], interval_minutes=15, device_id=device_id,
    )

    open_str  = day_info["open_dt"].strftime("%H:%M")
    close_str = day_info["close_dt"].strftime("%H:%M")

    if is_future:
        return html.Div([
            dbc.Alert(
                f"⏳  {day_info['day_da']} {day_info['date'].strftime('%d/%m')} — "
                f"event åbner {open_str}",
                color="light",
            )
        ])

    # KPI row
    kpi = dbc.Row([
        dbc.Col(dbc.Card(dbc.CardBody([
            html.H6("Besøgende (peak)", className="card-subtitle text-muted"),
            html.H3(str(stats["peak"]), className="text-primary"),
        ]), className="text-center shadow-sm"), md=3),
        dbc.Col(dbc.Card(dbc.CardBody([
            html.H6("Ind", className="card-subtitle text-muted"),
            html.H3(str(stats["total_in"]), className="text-success"),
        ]), className="text-center shadow-sm"), md=3),
        dbc.Col(dbc.Card(dbc.CardBody([
            html.H6("Ud", className="card-subtitle text-muted"),
            html.H3(str(stats["total_out"]), className="text-danger"),
        ]), className="text-center shadow-sm"), md=3),
        dbc.Col(dbc.Card(dbc.CardBody([
            html.H6("Åbningstid", className="card-subtitle text-muted"),
            html.H3(f"{open_str}–{close_str}", className="text-secondary"),
        ]), className="text-center shadow-sm"), md=3),
    ], className="mb-3")

    # Time series chart
    fig = go.Figure()
    if ts_data:
        by_dev = {}
        for row in ts_data:
            dev = row["device_id"]
            by_dev.setdefault(dev, {"x": [], "y": []})
            by_dev[dev]["x"].append(_dt.datetime.fromtimestamp(row["bucket"]))
            by_dev[dev]["y"].append(row["peak_total"])
        for i, (dev, series) in enumerate(by_dev.items()):
            color = BRAND_COLORS[i % len(BRAND_COLORS)]
            fig.add_trace(go.Scatter(
                x=series["x"], y=series["y"],
                mode="lines+markers", name=dev,
                line=dict(width=2.5, color=color),
                marker=dict(size=5, color=color),
            ))

    # Shade the event window
    fig.add_vrect(
        x0=day_info["open_dt"], x1=day_info["close_dt"],
        fillcolor="rgba(0,200,100,0.07)", line_width=0,
    )
    fig.update_layout(
        xaxis_title="Tid",
        yaxis_title="Besøgende",
        hovermode="x unified",
        margin=dict(l=40, r=20, t=20, b=40),
        height=300,
    )

    label = "I dag" if is_today else day_info["day_da"]
    return html.Div([
        html.H6(
            f"{label}: {day_info['date'].strftime('%d. %B')}",
            className="text-muted mb-3",
        ),
        kpi,
        dcc_graph_placeholder := dbc.Card(dbc.CardBody([
            dcc_import_shim(go, fig, _dt),
        ]), className="shadow-sm"),
    ])


def dcc_import_shim(go, fig, _dt):
    """Returns a Graph component — import dcc here to avoid circular issues."""
    from dash import dcc
    return dcc.Graph(figure=fig, config={"displayModeBar": False})


# ── Helper: overview tab ──────────────────────────────────────────────────────

def _make_overview_tab(days, storage, go, _dt, dbc, html, dash_table, device_id=None):
    rows = []
    for d in days:
        stats = storage.get_stats_for_period(d["open_ts"], d["close_ts"], device_id=device_id)
        is_future = d["date"] > _dt.datetime.now().date()
        rows.append({
            "Dag":        f"{d['day_da']} {d['date'].strftime('%d/%m')}",
            "Åbningstid": f"{d['open_dt'].strftime('%H:%M')}–{d['close_dt'].strftime('%H:%M')}",
            "Ind":        str(stats["total_in"])  if not is_future else "–",
            "Ud":         str(stats["total_out"]) if not is_future else "–",
            "Peak":       str(stats["peak"])       if not is_future else "–",
        })

    # All-days time series chart
    all_start = min(d["open_ts"]  for d in days)
    all_end   = max(d["close_ts"] for d in days)
    ts_data = storage.get_timeseries_for_period(
        all_start, all_end, interval_minutes=30, device_id=device_id,
    )

    fig = go.Figure()
    if ts_data:
        by_dev = {}
        for row in ts_data:
            dev = row["device_id"]
            by_dev.setdefault(dev, {"x": [], "y": []})
            by_dev[dev]["x"].append(_dt.datetime.fromtimestamp(row["bucket"]))
            by_dev[dev]["y"].append(row["peak_total"])
        for i, (dev, series) in enumerate(by_dev.items()):
            color = BRAND_COLORS[i % len(BRAND_COLORS)]
            fig.add_trace(go.Scatter(
                x=series["x"], y=series["y"],
                mode="lines+markers", name=dev,
                line=dict(width=2.5, color=color),
                marker=dict(size=5, color=color),
            ))
    # Shade each event day
    for d in days:
        fig.add_vrect(
            x0=d["open_dt"], x1=d["close_dt"],
            fillcolor="rgba(0,150,255,0.07)", line_width=0,
        )
    fig.update_layout(
        xaxis_title="Tid",
        yaxis_title="Besøgende",
        hovermode="x unified",
        margin=dict(l=40, r=20, t=20, b=40),
        height=320,
    )

    from dash import dcc, dash_table as dt2
    return html.Div([
        html.H6("Oversigt over hele eventet", className="text-muted mb-3"),
        dbc.Card(dbc.CardBody(dcc.Graph(figure=fig, config={"displayModeBar": False})),
                 className="shadow-sm mb-3"),
        dbc.Card(dbc.CardBody([
            dt2.DataTable(
                data=rows,
                columns=[{"name": c, "id": c} for c in rows[0].keys()] if rows else [],
                style_cell={"textAlign": "left", "padding": "8px"},
                style_header={"backgroundColor": "#f8f9fa", "fontWeight": "bold"},
                style_data_conditional=[
                    {"if": {"row_index": "odd"}, "backgroundColor": "#f2f2f2"}
                ],
            ) if rows else html.P("Ingen data endnu.", className="text-muted"),
        ]), className="shadow-sm"),
    ])
