# SSU Campus Energy Dashboard

Automated pipeline + interactive dashboard for tracking electricity, gas, and water usage across 30+ buildings at Sonoma State University.

Built as part of an internship under Dr. Farid Farahmand. The whole pipeline is automated end-to-end. Data comes in from the campus BMS every day at 6 AM UTC, gets cleaned and stored, and the dashboard is updated with fresh data.

---

## How it works

```
FTP Server (SSU building meters)
        │
        ▼
master_pipeline.py   ← runs every morning at 6 AM via cron
        │
        ├──▶ MySQL database   (energy / gas / water tables)
        │
        └──▶ weekly_energy.csv  ──▶  served over HTTPS
                                            │
                                            ▼
                                   Streamlit Dashboard
```

1. Pipeline connects to FTP, downloads new CSV reports from three meter folders
2. Cleans them — handles bad timestamps, unit mismatches, duplicate records
3. Inserts into MySQL with duplicate protection
4. Exports a weekly summary CSV to a public URL
5. Dashboard fetches that CSV at load time (5-min cache) and displays everything

---

## Features

- **FTP ingestion** — pulls from `degreeDayReports`, `intervalMeterReports`, and `pgeReports` daily
- **Smart unit handling** — resolves kWh, Wh, BTU, kBTU, MBTU, therm, tonref, and gallon; cell-level units take priority over the point-ID map
- **Duplicate-safe inserts** — UNIQUE constraint on `(timestamp, location, unit)` across all three tables so re-runs are safe
- **Outlier detection** — flags meters where any month's average is 10× higher or lower than another month (caught a real 1,000× misclassification on the PE building)
- **Dashboard** — tabbed by utility, KPI cards, weekly trend charts, per-building leaderboard
- **Fallback** — if the remote CSV is unreachable, dashboard falls back to any local raw CSVs
- **Logging** — structured per-day log files, optional email alerts, optional PowerBI refresh trigger

---

## Project structure

```
ssu-energy-dashboard/
├── master_pipeline.py      # Main orchestrator — FTP, clean, insert, export
├── energy_core.py          # Shared logic: unit maps, building map, CSV parser
├── app_fil.py              # Streamlit dashboard
├── app_data_loader.py      # Loads weekly CSV (remote first, local fallback)
├── detect_outliers.py      # Monthly per-meter outlier detection
├── run_pipeline.sh         # Shell wrapper for cron
├── diff.py                 # Utility to compare two weekly CSVs
├── .env.example            # Template for required environment variables
└── logs/                   # Auto-created, one log file per day
```

---

## Getting started

```bash
git clone <your-repo-url>
cd ssu-energy-dashboard

pip install pandas pymysql requests streamlit

cp .env.example .env
# Fill in your DB and FTP credentials in .env

python3 master_pipeline.py   # run once manually to test
streamlit run app_fil.py     # launch the dashboard locally
```

---

## Environment variables

All secrets go in a `.env` file that never gets committed. Copy `.env.example` and fill it in.

| Variable | What it's for |
|---|---|
| `DB_USER` | MySQL username |
| `DB_PASSWORD` | MySQL password |
| `DB_HOST` | MySQL host |
| `DB_NAME` | Database name |
| `FTP_HOST` | FTP server address |
| `FTP_USER` | FTP username |
| `FTP_PASSWORD` | FTP password |
| `PIPELINE_DIR` | Absolute path to the pipeline folder on the server |
| `PUBLIC_DATA_DIR` | Path to the web-accessible folder for the weekly CSV |
| `EMAIL_ENABLED` | Set to `true` to get email alerts on pipeline runs |
| `POWERBI_ENABLED` | Set to `true` to trigger a PowerBI dataset refresh |

---

## Deploying to Hostinger

Full step-by-step instructions are in [`DEPLOY.md`](DEPLOY.md), including SSH setup, installing Python dependencies without root, cron job configuration, and connecting Streamlit Cloud.

For Streamlit Cloud, add this under **Settings → Secrets**:

```toml
WEEKLY_CSV_URL = "https://your-domain.com/data/weekly_energy.csv"
```

---

## Stack

Python · Pandas · PyMySQL · Streamlit · MySQL · Hostinger Premium · FTP · Cron
