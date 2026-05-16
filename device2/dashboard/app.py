import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DashApp:
    def __init__(self, storage, config: dict):
        self._storage = storage
        self._cfg = config
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
            ],
            fluid=True,
        )

        @app.callback(
            Output("current-count", "children"),
            Output("total-in", "children"),
            Output("total-out", "children"),
            Output("timeseries-chart", "figure"),
            Output("device-table", "children"),
            Input("interval", "n_intervals"),
        )
        def update_dashboard(n):
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

            devices = self._storage.get_all_devices()
            device_rows = []
            for dev in devices:
                dev_total = self._storage.get_current_total(device_id=dev)
                device_rows.append({"Enhed": dev, "Nuværende": dev_total})

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

            return str(current), str(total_in), str(total_out), fig, table

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
