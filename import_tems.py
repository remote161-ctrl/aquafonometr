#!/usr/bin/env python3
"""Import TEMS Discovery 'Table View' exported measurements into AQUAMER DB.

Usage:
    python3 import_tems.py Table_View_DataRSRP.txt [--source TEMS] [--truncate]
"""

import argparse
import asyncio
import csv
from datetime import datetime

from database import insert_tems_measurements


def _num(val):
    val = val.strip().replace(",", ".")
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _ts(date_str, time_str):
    date_str = date_str.strip()
    time_str = time_str.strip()
    for fmt in ("%m/%d/%Y %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(f"{date_str} {time_str.split('.')[0]}", fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def parse_file(path):
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
                "cell_id": cell,
            })
    return rows


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--source", default="TEMS")
    ap.add_argument("--start-id", type=int, help="reserved")
    args = ap.parse_args()

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