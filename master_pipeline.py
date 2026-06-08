"""SSU Campus Energy Pipeline."""
from __future__ import annotations
import os, sys, shutil, smtplib, traceback
from collections import defaultdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from ftplib import FTP

import pandas as pd
import pymysql
import requests

# .env loader
def _load_env(path: str) -> None:
    """Tiny .env parser."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)

_HERE = os.path.dirname(os.path.abspath(__file__))
_load_env(os.path.join(_HERE, ".env"))

from energy_core import (
    POINT_ID_MAP, UNIT_TO_KWH, ENERGY_UNITS, THERMAL_UNITS,
    process_csv, to_kwh,
)

# Config
def _req(key: str) -> str:
    v = os.environ.get(key)
    if not v:
        sys.stderr.write(f"FATAL: missing required env var {key} (check .env)\n")
        sys.exit(2)
    return v

DB_CONFIG = {
    "user":     _req("DB_USER"),
    "password": _req("DB_PASSWORD"),
    "host":     os.environ.get("DB_HOST", "193.203.166.234"),
    "database": _req("DB_NAME"),
    "port":     int(os.environ.get("DB_PORT", "3306")),
    "connect_timeout": 30,
}
FTP_CONFIG = {
    "host":           _req("FTP_HOST"),
    "username":       _req("FTP_USER"),
    "password":       _req("FTP_PASSWORD"),
    "base_directory": os.environ.get("FTP_BASE_DIR", "/siqReports"),
}
EMAIL_CONFIG = {
    "enabled":         os.environ.get("EMAIL_ENABLED", "false").lower() == "true",
    "smtp_server":     os.environ.get("SMTP_SERVER", "smtp.gmail.com"),
    "smtp_port":       int(os.environ.get("SMTP_PORT", "587")),
    "sender_email":    os.environ.get("SENDER_EMAIL", ""),
    "sender_password": os.environ.get("SENDER_PASSWORD", ""),
    "recipient_email": os.environ.get("RECIPIENT_EMAIL", ""),
}
POWERBI_CONFIG = {
    "enabled":      os.environ.get("POWERBI_ENABLED", "false").lower() == "true",
    "dataset_id":   os.environ.get("POWERBI_DATASET_ID", ""),
    "access_token": os.environ.get("POWERBI_ACCESS_TOKEN", ""),
}

BASE_DIR        = os.environ.get("PIPELINE_DIR", _HERE)
PUBLIC_DATA_DIR = os.environ.get(
    "PUBLIC_DATA_DIR",
    "/home/u209446640/domains/faridfarahmand.net/public_html/data",
)
UPLOAD_DIR      = os.path.join(BASE_DIR, "uploads")
PROCESSED_DIR   = os.path.join(BASE_DIR, "processed")
FAILED_DIR      = os.path.join(BASE_DIR, "failed")
LOG_DIR         = os.path.join(BASE_DIR, "logs")
TEMP_DIR        = os.path.join(BASE_DIR, "temp")
WEEKLY_CSV_PATH = os.path.join(PUBLIC_DATA_DIR, "weekly_energy.csv")
FTP_FOLDERS     = ["degreeDayReports", "intervalMeterReports", "pgeReports"]

for _d in [UPLOAD_DIR, PROCESSED_DIR, FAILED_DIR, LOG_DIR, TEMP_DIR, PUBLIC_DATA_DIR]:
    os.makedirs(_d, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"pipeline_{datetime.now():%Y%m%d}.log")


# Logging
def log(msg, level="INFO", error=False):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
            if error:
                f.write(traceback.format_exc() + "\n")
    except Exception:
        pass


# Email / PowerBI
def send_email(subject, body, is_error=False):
    if not EMAIL_CONFIG["enabled"]:
        return
    try:
        m = MIMEMultipart()
        m["From"], m["To"] = EMAIL_CONFIG["sender_email"], EMAIL_CONFIG["recipient_email"]
        m["Subject"] = f"SSU Energy Pipeline: {subject}"
        m.attach(MIMEText(body, "plain"))
        s = smtplib.SMTP(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"])
        s.starttls()
        s.login(EMAIL_CONFIG["sender_email"], EMAIL_CONFIG["sender_password"])
        s.send_message(m); s.quit()
    except Exception as e:
        log(f"Email failed: {e}", "WARNING", error=True)


def refresh_powerbi():
    if not POWERBI_CONFIG["enabled"]:
        return True
    try:
        url = f"https://api.powerbi.com/v1.0/myorg/datasets/{POWERBI_CONFIG['dataset_id']}/refreshes"
        r = requests.post(url, headers={
            "Authorization": f"Bearer {POWERBI_CONFIG['access_token']}",
            "Content-Type": "application/json"})
        return r.status_code == 202
    except Exception as e:
        log(f"PowerBI error: {e}", "ERROR", error=True); return False


# FTP download
def download_from_ftp():
    stats = {"downloaded": 0, "errors": 0}
    try:
        ftp = FTP(FTP_CONFIG["host"])
        ftp.login(FTP_CONFIG["username"], FTP_CONFIG["password"])
        log("FTP connected", "SUCCESS")
        for folder in FTP_FOLDERS:
            ftp_dir   = f"{FTP_CONFIG['base_directory']}/{folder}"
            local_dir = os.path.join(UPLOAD_DIR, folder)
            os.makedirs(local_dir, exist_ok=True)
            try:
                ftp.cwd(ftp_dir)
                try: ftp.mkd("Backup")
                except Exception: pass
                for fname in ftp.nlst():
                    if not fname.endswith(".csv"):
                        continue
                    local_path = os.path.join(local_dir, fname)
                    with open(local_path, "wb") as fh:
                        ftp.retrbinary(f"RETR {fname}", fh.write)
                    stats["downloaded"] += 1
                    try: ftp.rename(fname, f"Backup/{fname}")
                    except Exception: pass
                ftp.cwd(FTP_CONFIG["base_directory"])
            except Exception as e:
                log(f"FTP folder {folder}: {e}", "ERROR", error=True)
                stats["errors"] += 1
        ftp.quit()
    except Exception as e:
        log(f"FTP connect: {e}", "ERROR", error=True)
        stats["errors"] += 1
    return stats


# DB helpers
def ensure_unique_indexes(conn):
    """Ensure UNIQUE(timestamp, location, unit) on all three tables."""
    cur = conn.cursor()
    for table in ("energy_usage", "gas_usage", "water_usage"):
        cur.execute(f"SHOW INDEX FROM {table} WHERE Key_name = 'uq_{table}'")
        if not cur.fetchone():
            cur.execute(
                f"ALTER TABLE {table} ADD UNIQUE KEY uq_{table} "
                "(timestamp, location, unit)"
            )
            log(f"Added UNIQUE index on {table}", "SUCCESS")
    conn.commit()
    cur.close()


def push_to_db(df_clean: pd.DataFrame) -> dict:
    """Insert a cleaned DataFrame into the correct MySQL table."""
    stats = {"energy": 0, "gas": 0, "water": 0, "errors": 0}
    if df_clean.empty:
        return stats
    conn = pymysql.connect(**DB_CONFIG)
    try:
        ensure_unique_indexes(conn)
        cur = conn.cursor()
        INSERT = "INSERT IGNORE INTO {} (timestamp, location, value, unit) VALUES (%s,%s,%s,%s)"
        for table_name in ("energy", "gas", "water"):
            subset = df_clean[df_clean["table"] == table_name]
            if subset.empty:
                continue
            rows = list(zip(
                subset["timestamp"], subset["location"],
                subset["value"],     subset["unit"],
            ))
            sql = INSERT.format(f"{table_name}_usage")
            for i in range(0, len(rows), 1000):
                try:
                    cur.executemany(sql, rows[i:i+1000])
                    stats[table_name] += cur.rowcount
                except pymysql.MySQLError as e:
                    log(f"{table_name} batch error: {e}", "ERROR", error=True)
                    stats["errors"] += 1
        conn.commit()
        log(f"DB insert: energy={stats['energy']} gas={stats['gas']} water={stats['water']}")
    finally:
        conn.close()
    return stats


# Weekly CSV export
def generate_weekly_csv(output_path=WEEKLY_CSV_PATH) -> bool:
    """Aggregate all three MySQL tables by ISO week and building."""
    conn = pymysql.connect(**DB_CONFIG)
    try:
        def _week_query(table):
            return f"""
                SELECT
                    DATE_FORMAT(DATE_SUB(timestamp, INTERVAL WEEKDAY(timestamp) DAY),
                                '%Y-%m-%d') AS week,
                    location, unit, SUM(value) AS total
                FROM {table}
                GROUP BY week, location, unit
            """
        cur = conn.cursor()
        cur.execute(_week_query("energy_usage")); energy_rows = cur.fetchall()
        cur.execute(_week_query("gas_usage"));    gas_rows    = cur.fetchall()
        cur.execute(_week_query("water_usage"));  water_rows  = cur.fetchall()
        cur.close()

        def building_of(loc):
            pid = loc.split("p:sonomastate:r:")[-1].strip() if "p:sonomastate:r:" in str(loc) else None
            return POINT_ID_MAP[pid][0] if pid in POINT_ID_MAP else str(loc)

        kwh_agg, gas_agg, water_agg = defaultdict(float), defaultdict(float), defaultdict(float)
        thermal_kwh_agg = defaultdict(float)

        for week, loc, unit, total in energy_rows:
            kwh_contribution = float(total) * UNIT_TO_KWH.get(unit, 0.0 if unit not in ENERGY_UNITS else 1.0)
            bld = building_of(loc)
            kwh_agg[(week, bld)] += kwh_contribution
            if unit in THERMAL_UNITS:
                thermal_kwh_agg[(week, bld)] += kwh_contribution
        for week, loc, _, total in gas_rows:
            gas_agg[(week, building_of(loc))] += float(total)
        for week, loc, _, total in water_rows:
            water_agg[(week, building_of(loc))] += float(total)

        keys = sorted(set(kwh_agg) | set(gas_agg) | set(water_agg))
        rows = []
        for (week, bld) in keys:
            kwh = kwh_agg.get((week, bld), 0.0)
            rows.append({
                "week": week, "building": bld,
                "kWh":           round(kwh, 6),
                "thermal_kWh":   round(thermal_kwh_agg.get((week, bld), 0.0), 6),
                "gas_therm":     round(gas_agg.get((week, bld), 0.0), 6),
                "water_gallon":  round(water_agg.get((week, bld), 0.0), 6),
                "heating_dd":    0.0,
                "normalized_kWh": round(kwh, 6),
            })
        if not rows:
            log("No DB data — weekly_energy.csv not written", "WARNING")
            return False
        # Atomic write
        tmp = output_path + ".tmp"
        pd.DataFrame(rows).to_csv(tmp, index=False)
        os.replace(tmp, output_path)
        log(f"weekly_energy.csv → {output_path} ({len(rows)} rows, "
            f"{len(set(r['week'] for r in rows))} weeks)")
        return True
    finally:
        conn.close()


# File management
def move_to(dest_root, file_path, folder):
    subdir = os.path.join(dest_root, folder)
    os.makedirs(subdir, exist_ok=True)
    base, ext = os.path.splitext(os.path.basename(file_path))
    dest = os.path.basename(file_path)
    i = 1
    while os.path.exists(os.path.join(subdir, dest)):
        dest = f"{base}_{i}{ext}"; i += 1
    shutil.move(file_path, os.path.join(subdir, dest))


# Main
def main():
    start = datetime.now()
    log("=" * 70); log("SSU ENERGY PIPELINE STARTED")

    stats = {"downloaded": 0, "processed": 0, "skipped": 0, "inserted": 0, "errors": 0}

    # Phase 0: DB sanity
    try:
        conn = pymysql.connect(**DB_CONFIG)
        ensure_unique_indexes(conn)
        conn.close()
    except Exception as e:
        log(f"DB connect failed: {e}", "ERROR", error=True)
        send_email("Pipeline FAILED — DB unreachable", str(e), is_error=True)
        sys.exit(1)

    # Phase 1: FTP
    ftp = download_from_ftp()
    stats["downloaded"] = ftp["downloaded"]
    stats["errors"]    += ftp["errors"]
    if stats["downloaded"] == 0:
        log("No new files", "INFO")
        generate_weekly_csv()
        send_email("No new data", "FTP had no new files today")
        return

    # Phase 2: clean + insert
    for folder in FTP_FOLDERS:
        folder_path = os.path.join(UPLOAD_DIR, folder)
        if not os.path.isdir(folder_path):
            continue
        for fname in [f for f in os.listdir(folder_path) if f.endswith(".csv")]:
            src = os.path.join(folder_path, fname)
            log(f"→ {fname}")
            cleaned, pstats = process_csv(src)
            if pstats["error"] or cleaned.empty:
                log(f"  skip: {pstats['error'] or 'no data'}", "WARNING")
                move_to(FAILED_DIR, src, folder)
                stats["skipped"] += 1
                continue
            db = push_to_db(cleaned)
            stats["processed"] += 1
            stats["inserted"]  += db["energy"] + db["gas"] + db["water"]
            stats["errors"]    += db["errors"]
            move_to(PROCESSED_DIR, src, folder)

    # Phase 3: weekly export + PowerBI
    generate_weekly_csv()
    if stats["inserted"] > 0:
        refresh_powerbi()

    # Summary
    end = datetime.now()
    summary = (f"Status: {'OK' if stats['errors']==0 else 'WITH ERRORS'}\n"
               f"Duration: {(end-start).total_seconds():.1f}s\n"
               f"Downloaded: {stats['downloaded']}  Processed: {stats['processed']}  "
               f"Skipped: {stats['skipped']}\n"
               f"DB inserts: {stats['inserted']}  Errors: {stats['errors']}\n"
               f"Output: {WEEKLY_CSV_PATH}\n")
    log(summary)
    send_email(
        "Success" if stats["errors"] == 0 else "Completed with errors",
        summary, is_error=stats["errors"] > 0,
    )
    sys.exit(0 if stats["errors"] == 0 else 1)


if __name__ == "__main__":
    main()
