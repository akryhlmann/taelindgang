import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


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
            import datetime
        except ImportError as exc:
            logger.error("Dashboard dependencies not available: %s", exc)
            return

        dash_cfg = self._cfg.get("dashboard", {})
        refresh_ms = dash_cfg.get("refresh_interval", 30) * 1000

        app = dash.Dash(
            __name__,
            external_stylesheets=[dbc.themes.FLATLY],
            title="Besøgende Tæller",
        )
        self._app = app

        app.layout = dbc.Container(
            [
                dcc.Interval(id="interval", interval=refresh_ms, n_intervals=0),
                dbc.Row(
                    dbc.Col(
                        html.H1(
                            "Besøgende Tæller",
                            className="text-center my-4 text-primary fw-bold",
                        )
                    )
                ),
                dbc.Row(
                    [
                        dbc.Col(
                            dbc.Card(
                                dbc.CardBody(
                                    [
                                        html.H6("Nuværende besøgende", className="card-subtitle text-muted"),
                                        html.H2(id="current-count", className="card-title text-primary display-4"),
                                    ]
                                ),
                                className="shadow text-center",
                            ),
                            md=4,
                        ),
                        dbc.Col(
                            dbc.Card(
                                dbc.CardBody(
                                    [
                                        html.H6("Ind i dag", className="card-subtitle text-muted"),
                                        html.H2(id="total-in", className="card-title text-success display-4"),
                                    ]
                                ),
                                className="shadow text-center",
                            ),
                            md=4,
                        ),
                        dbc.Col(
                            dbc.Card(
                                dbc.CardBody(
                                    [
                                        html.H6("Ud i dag", className="card-subtitle text-muted"),
                                        html.H2(id="total-out", className="card-title text-danger display-4"),
                                    ]
                                ),
                                className="shadow text-center",
                            ),
                            md=4,
                        ),
                    ],
                    className="mb-4",
                ),
                dbc.Row(
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.H5("Besøgende over tid (seneste 24 timer)", className="card-title"),
                                    dcc.Graph(id="timeseries-chart"),
                                ]
                            ),
                            className="shadow",
                        )
                    ),
                    className="mb-4",
                ),
                dbc.Row(
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.H5("Enhedsoversigt", className="card-title"),
                                    html.Div(id="device-table"),
                                ]
                            ),
                            className="shadow",
                        )
                    ),
                    className="mb-4",
                ),
                dbc.Row(
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.H5("LoRa signalstatus", className="card-title"),
                                    html.Div(id="lora-status"),
                                ]
                            ),
                            className="shadow",
                        )
                    ),
                    className="mb-4",
                ),
                dbc.Row(
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.H5("Google Sheets synkronisering", className="card-title"),
                                    html.Div(id="sheets-status"),
                                ]
                            ),
                            className="shadow",
                        )
                    ),
                    className="mb-4",
                ),
            ],
            fluid=True,
        )

        def _rssi_badge(rssi):
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

        def _time_ago(ts):
            if ts is None:
                return "–"
            diff = int(time.time() - ts)
            if diff < 60:
                return f"{diff} sek. siden"
            if diff < 3600:
                return f"{diff // 60} min. siden"
            return f"{diff // 3600} t. siden"

        @app.callback(
            Output("current-count", "children"),
            Output("total-in", "children"),
            Output("total-out", "children"),
            Output("timeseries-chart", "figure"),
            Output("device-table", "children"),
            Output("lora-status", "children"),
            Output("sheets-status", "children"),
            Input("interval", "n_intervals"),
        )
        def update_dashboard(n):
            import time as _time
            stats = self._storage.get_stats(since_hours=24)
            current = stats.get("current", 0)
            total_in = stats.get("total_in", 0)
            total_out = stats.get("total_out", 0)

            timeseries = self._storage.get_timeseries(hours=24, interval_minutes=15)
            fig = go.Figure()
            if timeseries:
                df_data = {}
                for row in timeseries:
                    dev = row["device_id"]
                    if dev not in df_data:
                        df_data[dev] = {"x": [], "y": []}
                    ts_dt = datetime.datetime.utcfromtimestamp(row["bucket"])
                    df_data[dev]["x"].append(ts_dt)
                    df_data[dev]["y"].append(row["peak_total"])
                for dev, series in df_data.items():
                    fig.add_trace(
                        go.Scatter(
                            x=series["x"],
                            y=series["y"],
                            mode="lines+markers",
                            name=dev,
                            line=dict(width=2),
                        )
                    )
            fig.update_layout(
                xaxis_title="Tid",
                yaxis_title="Besøgende",
                legend_title="Enhed",
                hovermode="x unified",
                margin=dict(l=40, r=20, t=20, b=40),
                height=350,
            )

            lora_stats = self._storage.get_device_lora_stats()

            # Device table — includes current total
            device_rows = []
            for s in lora_stats:
                dev_total = self._storage.get_current_total(device_id=s["device_id"])
                device_rows.append({"Enhed": s["device_id"], "Nuværende": dev_total})

            if device_rows:
                table = dash_table.DataTable(
                    data=device_rows,
                    columns=[{"name": c, "id": c} for c in device_rows[0].keys()],
                    style_cell={"textAlign": "left", "padding": "8px"},
                    style_header={"backgroundColor": "#f8f9fa", "fontWeight": "bold"},
                    style_data_conditional=[
                        {"if": {"row_index": "odd"}, "backgroundColor": "#f2f2f2"}
                    ],
                )
            else:
                table = html.P("Ingen enheder tilsluttet endnu.", className="text-muted")

            # LoRa status cards — one per device
            if lora_stats:
                lora_cards = dbc.Row(
                    [
                        dbc.Col(
                            dbc.Card(
                                dbc.CardBody(
                                    [
                                        html.H6(s["device_id"], className="card-subtitle text-muted mb-2"),
                                        html.Div(
                                            [
                                                html.Span("Seneste pakke:", className="text-muted me-1"),
                                                html.Strong(_time_ago(s["last_seen"])),
                                            ],
                                            className="mb-1",
                                        ),
                                        html.Div(
                                            [
                                                html.Span("Seneste RSSI:", className="text-muted me-1"),
                                                _rssi_badge(s["latest_rssi"]),
                                            ],
                                            className="mb-1",
                                        ),
                                        html.Div(
                                            [
                                                html.Span("Gns. RSSI (10 pkter):", className="text-muted me-1"),
                                                _rssi_badge(
                                                    round(s["avg_rssi"]) if s["avg_rssi"] is not None else None
                                                ),
                                            ],
                                        ),
                                    ]
                                ),
                                className="shadow-sm h-100",
                            ),
                            md=4,
                            className="mb-3",
                        )
                        for s in lora_stats
                    ]
                )
            else:
                lora_cards = html.P("Ingen LoRa-enheder registreret endnu.", className="text-muted")

            # Google Sheets status
            sheets = self._sheets
            if sheets is None:
                sheets_card = html.P("Google Sheets sync ikke konfigureret.", className="text-muted")
            else:
                if sheets._mock_mode:
                    conn_badge = dbc.Badge("Mock tilstand", color="secondary")
                    error_div = html.Small("gspread ikke installeret", className="text-muted")
                elif sheets.connected:
                    conn_badge = dbc.Badge("Forbundet", color="success")
                    error_div = html.Span()
                else:
                    conn_badge = dbc.Badge("Ikke forbundet", color="danger")
                    err_text = sheets.last_error or "Ukendt fejl"
                    # Truncate long error messages
                    if len(err_text) > 80:
                        err_text = err_text[:77] + "…"
                    error_div = html.Small(err_text, className="text-danger d-block mt-1")

                if sheets.last_sync_time:
                    sync_ago = _time_ago(sheets.last_sync_time)
                    sync_text = f"{sync_ago}  ({sheets.last_sync_rows} rækker)"
                else:
                    sync_text = "Ikke synkroniseret endnu"

                sheets_card = dbc.Row(
                    dbc.Col(
                        [
                            html.Div(
                                [
                                    html.Span("Status:", className="text-muted me-2"),
                                    conn_badge,
                                ],
                                className="mb-2",
                            ),
                            html.Div(
                                [
                                    html.Span("Seneste sync:", className="text-muted me-1"),
                                    html.Strong(sync_text),
                                ],
                                className="mb-1",
                            ),
                            error_div,
                        ],
                        md=6,
                    )
                )

            return str(current), str(total_in), str(total_out), fig, table, lora_cards, sheets_card

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
