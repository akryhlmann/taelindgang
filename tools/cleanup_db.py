#!/usr/bin/env python3
"""
Rens Device 2-databasen for mock-data og forældede enheder.

Viser alle kendte enheder med datastatistik, og lader dig vælge
hvad der skal slettes.

Usage:
    python tools/cleanup_db.py [--db ~/data/visitor_counts.db]
    python tools/cleanup_db.py --all     # slet ALT uden bekræftelse
"""
import sqlite3
import argparse
import os
import time
from datetime import datetime


def ts(unix):
    if unix is None:
        return "—"
    return datetime.fromtimestamp(unix).strftime("%Y-%m-%d %H:%M")


def open_db(path):
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        print(f"Database ikke fundet: {path}")
        raise SystemExit(1)
    return sqlite3.connect(path)


def show_devices(con):
    print("\n=== Kendte enheder ===\n")
    rows = con.execute("""
        SELECT d.device_id,
               d.first_seen,
               d.last_seen,
               COUNT(ce.id)         AS events,
               SUM(ce.count_in)     AS total_in,
               SUM(ce.count_out)    AS total_out
        FROM   devices d
        LEFT JOIN count_events ce ON ce.device_id = d.device_id
        GROUP BY d.device_id
        ORDER BY d.first_seen
    """).fetchall()

    if not rows:
        print("  (ingen enheder)")
        return []

    print(f"  {'ID':<20} {'Første set':<20} {'Sidst set':<20} {'Events':>7} {'Ind':>6} {'Ud':>6}")
    print("  " + "-" * 85)
    for r in rows:
        did, first, last, evts, ci, co = r
        print(f"  {did:<20} {ts(first):<20} {ts(last):<20} {evts or 0:>7} {ci or 0:>6} {co or 0:>6}")

    print(f"\n  Total: {len(rows)} enhed(er), {sum(r[3] or 0 for r in rows)} events")
    return [r[0] for r in rows]


def show_summary(con):
    total_events = con.execute("SELECT COUNT(*) FROM count_events").fetchone()[0]
    total_hourly = con.execute("SELECT COUNT(*) FROM hourly_summary").fetchone()[0]
    oldest = con.execute("SELECT MIN(timestamp) FROM count_events").fetchone()[0]
    newest = con.execute("SELECT MAX(timestamp) FROM count_events").fetchone()[0]
    print(f"\n  count_events: {total_events} rækker  ({ts(oldest)} → {ts(newest)})")
    print(f"  hourly_summary: {total_hourly} rækker")


def delete_device(con, device_id):
    n_events  = con.execute("DELETE FROM count_events    WHERE device_id = ?", (device_id,)).rowcount
    n_hourly  = con.execute("DELETE FROM hourly_summary  WHERE device_id = ?", (device_id,)).rowcount
    n_devices = con.execute("DELETE FROM devices         WHERE device_id = ?", (device_id,)).rowcount
    con.commit()
    print(f"  Slettet '{device_id}': {n_events} events, {n_hourly} hourly-rækker, {n_devices} device-post")


def delete_all(con):
    for tbl in ("count_events", "hourly_summary", "devices"):
        con.execute(f"DELETE FROM {tbl}")
    con.commit()
    print("  Alt data slettet.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",  default="~/data/visitor_counts.db")
    parser.add_argument("--all", action="store_true", help="Slet ALT data uden bekræftelse")
    args = parser.parse_args()

    con = open_db(args.db)

    if args.all:
        delete_all(con)
        con.close()
        return

    device_ids = show_devices(con)
    show_summary(con)

    if not device_ids:
        con.close()
        return

    print("\n=== Muligheder ===")
    print("  [nummer]  Slet specifik enhed (f.eks. '1' for første enhed)")
    print("  all       Slet ALT data og alle enheder")
    print("  q         Afslut uden at slette noget")

    while True:
        try:
            val = input("\nHvad vil du slette? ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nAfbrudt.")
            break

        if val in ("q", ""):
            print("Ingen ændringer.")
            break

        if val == "all":
            confirm = input(f"  Slet ALLE {len(device_ids)} enhed(er) og alt data? [ja/nej] ").strip().lower()
            if confirm == "ja":
                delete_all(con)
            else:
                print("  Annulleret.")
            break

        try:
            idx = int(val) - 1
            if 0 <= idx < len(device_ids):
                did = device_ids[idx]
                confirm = input(f"  Slet alle data for '{did}'? [ja/nej] ").strip().lower()
                if confirm == "ja":
                    delete_device(con, did)
                    device_ids.pop(idx)
                    show_devices(con)
                else:
                    print("  Annulleret.")
            else:
                print(f"  Ugyldigt nummer. Vælg 1–{len(device_ids)}.")
        except ValueError:
            print("  Ukendt kommando.")

    con.close()
    print("\nFærdig.")


if __name__ == "__main__":
    main()
