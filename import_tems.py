#!/usr/bin/env python3
"""Import TEMS Discovery 'Table View' exported measurements into AQUAMER DB.

Supports TSV exports (Table_View_*.txt) and Excel exports (Table_View_*.xlsx).

Usage:
    python3 import_tems.py Table_View_DataRSRP.txt [--source TEMS]
    python3 import_tems.py Table_View_Trough.xlsx [--source TEMS] [--truncate]
"""

import argparse
import asyncio
import csv
from datetime import datetime

from database import insert_tems_measurements


def _num(val):
    if val is None:
        return None
    val = str(val).strip().replace(",", ".")
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _ts(date_str, time_str):
    date_str = str(date_str).strip()
    time_str = str(time_str).strip()
    for fmt in ("%m/%d/%Y %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(f"{date_str} {time_str.split('.')[0]}", fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def parse_tsv(path):
    rows = []
    with open(path, newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader, None)  # skip header
        for line in reader:
            if len(line) < 7:
                continue
            lat = _num(line[3])
            lon = _num(line[4])
            rsrp = _num(line[5])
            cell = line[6].strip()
            ts = _ts(line[2], line[1])
            if lat is None or lon is None or ts is None:
                continue
            rows.append({
                "source": "TEMS",
                "measured_at": ts,
                "latitude": lat,
                "longitude": lon,
                "rsrp": rsrp,
                "throughput_kbps": None,
                "cell_id": cell,
            })
    return rows


def parse_xlsx(path):
    import openpyxl
    rows = []
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb["Metric Group 1"]
    it = ws.iter_rows(values_only=True)
    header = next(it, None)
    if not header:
        wb.close()
        return rows

    t_idx = d_idx = la_idx = lo_idx = rs_idx = tp_idx = ci_idx = None
    for i, h in enumerate(header):
        h = str(h).strip().lower() if h else ""
        if h == "time":
            t_idx = i
        elif h == "date":
            d_idx = i
        elif "latitude" in h:
            la_idx = i
        elif "longitude" in h:
            lo_idx = i
        elif "rsrp" in h:
            rs_idx = i
        elif "throughput" in h:
            tp_idx = i
        elif "cell" in h and "identity" in h:
            ci_idx = i

    if t_idx is None or d_idx is None or la_idx is None or lo_idx is None:
        wb.close()
        return rows

    for row in it:
        lat = _num(row[la_idx]) if la_idx is not None and la_idx < len(row) else None
        lon = _num(row[lo_idx]) if lo_idx is not None and lo_idx < len(row) else None
        ts = _ts(row[d_idx], row[t_idx])
        if lat is None or lon is None or ts is None:
            continue
        rsrp = _num(row[rs_idx]) if rs_idx is not None and rs_idx < len(row) else None
        tp = _num(row[tp_idx]) if tp_idx is not None and tp_idx < len(row) else None
        cell = str(row[ci_idx]).strip() if ci_idx is not None and ci_idx < len(row) and row[ci_idx] is not None else ""
        rows.append({
            "source": "TEMS",
            "measured_at": ts,
            "latitude": lat,
            "longitude": lon,
            "rsrp": rsrp,
            "throughput_kbps": tp,
            "cell_id": cell,
        })
    wb.close()
    return rows


async def _truncate_tems():
    import aiosqlite
    from config import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tems_measurements")
        await db.commit()


def parse_file(path):
    if path.lower().endswith(".xlsx"):
        return parse_xlsx(path)
    return parse_tsv(path)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--source", default="TEMS")
    ap.add_argument("--truncate", action="store_true", help="clear TEMS table before import")
    args = ap.parse_args()

    if args.truncate:
        await _truncate_tems()
        print("TEMS table truncated")

    rows = parse_file(args.file)
    for r in rows:
        r["source"] = args.source
    print(f"Parsed {len(rows)} measurements from {args.file}")
    if not rows:
        print("Nothing to import.")
        return

    batch = 5000
    for i in range(0, len(rows), batch):
        await insert_tems_measurements(rows[i:i + batch])
        print(f"  imported {min(i + batch, len(rows))}/{len(rows)}")

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())