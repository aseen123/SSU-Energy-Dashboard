"""
detect_outliers.py — find meters whose per-reading values shift dramatically
across time windows. The PE meter issue (1000x inflated readings during
May–Jul 2025) had a clear signal: same meter's average per-reading value
was 1194x higher in the bad window than the good window.

This script computes per-meter monthly averages and flags any meter whose
ratio between any two months exceeds a threshold. Manual review still
required — some legitimate variation exists — but this catches the smoking
guns automatically.

Usage:
    /opt/alt/python311/bin/python3.11 detect_outliers.py

Outputs:
    - Console summary of suspicious meters
    - outlier_report.csv with full per-meter, per-month detail
"""
from __future__ import annotations
import os, csv
from collections import defaultdict
import pymysql

# ── Load .env (stdlib only) ───────────────────────────────────────────────
def _load_env(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_HERE = os.path.dirname(os.path.abspath(__file__))
_load_env(os.path.join(_HERE, ".env"))

# ── Config ────────────────────────────────────────────────────────────────
RATIO_THRESHOLD = 10.0   # flag meters where any month's avg is >10x another's
MIN_READINGS    = 100    # skip meters with too few readings to be meaningful

DB = {
    "user":     os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
    "host":     os.environ.get("DB_HOST", "193.203.166.234"),
    "database": os.environ["DB_NAME"],
    "port":     int(os.environ.get("DB_PORT", "3306")),
}

# Building name resolution from energy_core
from energy_core import POINT_ID_MAP

def building_of(loc: str) -> str:
    if "p:sonomastate:r:" in str(loc):
        pid = loc.split("p:sonomastate:r:")[-1].strip()
        return POINT_ID_MAP.get(pid, ("UNKNOWN", ""))[0]
    return str(loc)


# ── Run ──────────────────────────────────────────────────────────────────
def main():
    conn = pymysql.connect(**DB)
    cur = conn.cursor()

    # Per-(location, unit, month): avg reading, count
    cur.execute("""
        SELECT location, unit,
               DATE_FORMAT(timestamp, '%Y-%m') AS month,
               AVG(value) AS avg_value,
               COUNT(*)   AS n_readings,
               MIN(value) AS min_value,
               MAX(value) AS max_value
        FROM energy_usage
        GROUP BY location, unit, month
        ORDER BY location, unit, month
    """)
    rows = cur.fetchall()
    conn.close()

    # Group by (location, unit) → list of (month, avg, count, min, max)
    by_meter = defaultdict(list)
    for loc, unit, month, avg, n, mn, mx in rows:
        by_meter[(loc, unit)].append((month, float(avg), n, float(mn), float(mx)))

    # Detect ratio outliers
    suspicious = []
    full_report = []

    for (loc, unit), months in by_meter.items():
        # Filter months with enough readings to be meaningful
        valid = [m for m in months if m[2] >= MIN_READINGS]
        if len(valid) < 2:
            continue

        avgs = [m[1] for m in valid if m[1] > 0]
        if len(avgs) < 2:
            continue

        max_avg = max(avgs)
        min_avg = min(avgs)
        ratio   = max_avg / min_avg if min_avg > 0 else float("inf")

        if ratio >= RATIO_THRESHOLD:
            # Find which month is the high one
            high_months = [m for m in valid if m[1] == max_avg]
            low_months  = [m for m in valid if m[1] == min_avg]
            suspicious.append({
                "building": building_of(loc),
                "location": loc[-50:],  # truncate for display
                "unit":     unit,
                "ratio":    ratio,
                "high_month":   high_months[0][0],
                "high_avg":     max_avg,
                "high_n":       high_months[0][2],
                "low_month":    low_months[0][0],
                "low_avg":      min_avg,
                "low_n":        low_months[0][2],
            })

        # Add to full report regardless
        for month, avg, n, mn, mx in valid:
            full_report.append({
                "building": building_of(loc),
                "location": loc,
                "unit":     unit,
                "month":    month,
                "avg":      avg,
                "n":        n,
                "min":      mn,
                "max":      mx,
            })

    # ── Console output ────────────────────────────────────────────────────
    print("=" * 90)
    print(f"OUTLIER DETECTION  — flagging meters with >{RATIO_THRESHOLD}x ratio between months")
    print("=" * 90)

    if not suspicious:
        print(f"\nNo meters exceed the {RATIO_THRESHOLD}x threshold. Data looks clean.")
    else:
        suspicious.sort(key=lambda x: x["ratio"], reverse=True)
        print(f"\nFound {len(suspicious)} suspicious meters:\n")
        print(f"  {'Building':<28} {'unit':<6} {'ratio':>8}   "
              f"{'high mo':<8} {'high avg':>14}   {'low mo':<8} {'low avg':>14}")
        print("  " + "-" * 88)
        for s in suspicious:
            print(f"  {s['building'][:28]:<28} {s['unit']:<6} {s['ratio']:>8,.1f}x  "
                  f"{s['high_month']:<8} {s['high_avg']:>14,.2f}   "
                  f"{s['low_month']:<8} {s['low_avg']:>14,.2f}")
        print()
        print("These are CANDIDATES for review — not automatically wrong.")
        print("Confirm by spot-checking the meter's readings before deleting.")

    # ── Full report CSV ───────────────────────────────────────────────────
    out_path = os.path.join(_HERE, "outlier_report.csv")
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["building","location","unit","month","avg","n","min","max"])
        w.writeheader()
        w.writerows(full_report)
    print(f"\nFull per-meter monthly report → {out_path}")
    print(f"  ({len(full_report)} meter-month rows)")


if __name__ == "__main__":
    main()
