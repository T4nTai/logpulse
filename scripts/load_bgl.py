"""Load BGL_2k.log_structured.csv into SQLite logs table.

Usage: python scripts/load_bgl.py
"""
import csv
import os
import re
import sqlite3

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "logs.db")
CSV_PATH = os.path.join(DATA_DIR, "loghub", "BGL", "BGL_2k.log_structured.csv")

# "2005-06-03-15.42.50.675872" -> "2005-06-03 15:42:50"
TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})\.(\d{2})\.(\d{2})")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    level TEXT,
    component TEXT,
    message TEXT,
    raw TEXT,
    is_anomaly INTEGER DEFAULT 0,
    cluster_id INTEGER,
    event_id TEXT,
    node TEXT,
    line_id INTEGER
)
"""


def normalize_time(raw_time: str) -> str:
    m = TIME_RE.match(raw_time or "")
    if not m:
        return raw_time
    date, h, mi, s = m.groups()
    return f"{date} {h}:{mi}:{s}"


def main() -> None:
    if not os.path.exists(CSV_PATH):
        raise SystemExit(f"CSV not found: {CSV_PATH}")

    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(CREATE_SQL)

    # Reset then reload so re-running is idempotent
    conn.execute("DELETE FROM logs")

    inserted = 0
    anomalies = 0
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            label = (row.get("Label") or "").strip()
            is_anomaly = 0 if label == "-" else 1
            anomalies += is_anomaly

            message = row.get("Content") or ""
            component = " ".join(
                p for p in [row.get("Type"), row.get("Component")] if p
            )
            raw = " ".join(
                p
                for p in [
                    row.get("Label"),
                    row.get("Timestamp"),
                    row.get("Time"),
                    row.get("Type"),
                    row.get("Component"),
                    row.get("Level"),
                    message,
                ]
                if p
            )

            conn.execute(
                "INSERT INTO logs "
                "(timestamp, level, component, message, raw, is_anomaly, cluster_id, event_id, node, line_id) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    normalize_time(row.get("Time", "")),
                    (row.get("Level") or "INFO").upper(),
                    component,
                    message,
                    raw,
                    is_anomaly,
                    row.get("EventId"),
                    row.get("Node"),
                    int(row.get("LineId") or 0),
                ),
            )
            inserted += 1

    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    err = conn.execute(
        "SELECT COUNT(*) FROM logs WHERE level = 'ERROR' OR level = 'FATAL'"
    ).fetchone()[0]
    conn.close()

    print(f"Inserted : {inserted}")
    print(f"Anomaly  : {anomalies} ({anomalies / max(inserted, 1) * 100:.2f}%)")
    print(f"Total db : {total}")
    print(f"Errors   : {err}")


if __name__ == "__main__":
    main()
