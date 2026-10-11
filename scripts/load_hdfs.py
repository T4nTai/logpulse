#!/usr/bin/env python3
"""
Load HDFS sorted.log (11.2M lines) into SQLite database.

Creates:
  - blocks table : aggregated per-block features (train input)
  - logs table   : sampled raw logs (dashboard)
  - features.csv : block features for the ML model

Usage:
    python scripts/load_hdfs.py            # full file
    python scripts/load_hdfs.py 200000     # only first 200K lines (quick test)
"""
import os
import re
import sys
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SORTED_LOG = HERE.parent / "data" / "sorted.log"
DB_PATH = HERE.parent / "data" / "logs.db"
FEATURES_CSV = HERE.parent / "data" / "features.csv"
SAMPLE_RATE = 22  # keep 1 of every 22 lines -> ~509K sampled logs

LEVELS = {"INFO", "WARN", "ERROR", "FATAL", "SEVERE"}
BLOCK_RE = re.compile(r"blk_-?\d+")

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else None
if LIMIT:
    print(f"*** TEST MODE: processing only first {LIMIT:,} lines ***\n")

blocks = defaultdict(lambda: [0, 0, 0, 0, set(), set(), "", "", []])
#                  [log_count, info, warn, error, components, pids, first_ts, last_ts, events]

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute("DROP TABLE IF EXISTS logs")
cur.execute("DROP TABLE IF EXISTS blocks")
cur.execute("""
CREATE TABLE blocks (
    block_id TEXT PRIMARY KEY,
    log_count INTEGER,
    info_count INTEGER,
    warn_count INTEGER,
    error_count INTEGER,
    unique_components INTEGER,
    unique_pids INTEGER,
    first_ts TEXT,
    last_ts TEXT,
    duration_sec INTEGER,
    event_seq TEXT,
    is_anomaly INTEGER DEFAULT 0,
    anomaly_score REAL DEFAULT 0.0
)
""")
cur.execute("""
CREATE TABLE logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    level TEXT,
    component TEXT,
    message TEXT,
    block_id TEXT,
    is_anomaly INTEGER DEFAULT 0
)
""")

BATCH = 20_000
log_buf = []
n = 0
skipped = 0
CHK = 2_000_000


def flush_logs(force=False):
    global log_buf
    if len(log_buf) >= BATCH or (force and log_buf):
        cur.executemany(
            "INSERT INTO logs (timestamp, level, component, message, block_id) VALUES (?, ?, ?, ?, ?)",
            log_buf,
        )
        log_buf = []


print(f"Parsing {SORTED_LOG} ...")
with open(SORTED_LOG, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        n += 1
        if n % CHK == 0:
            print(f"  ... {n:,} lines   {len(blocks):,} blocks")
        if LIMIT and n > LIMIT:
            break

        # "081109 203518 143 INFO dfs.Foo$Bar: message..."
        parts = line.split(" ", 4)
        if len(parts) < 5:
            skipped += 1
            continue
        date, time, pid, level, rest = parts
        if level not in LEVELS:
            skipped += 1
            continue
        level = level.rstrip()

        # block id via fast substring scan
        i = rest.find("blk_")
        block_id = None
        if i != -1:
            j = i + 4
            if j < len(rest) and rest[j] == "-":
                j += 1
            k = j
            while k < len(rest) and rest[k].isdigit():
                k += 1
            if k > j:
                block_id = rest[i:k]

        component = rest.split(":", 1)[0] if ":" in rest else rest.split(" ", 1)[0]

        # sample for logs table
        if n % SAMPLE_RATE == 0:
            log_buf.append((f"{date} {time}", level, component, rest[:500], block_id))
            flush_logs()

        # aggregate per block
        if block_id is not None:
            b = blocks[block_id]
            b[0] += 1
            if level == "INFO":
                b[1] += 1
            elif level == "WARN":
                b[2] += 1
            else:  # ERROR / FATAL / SEVERE
                b[3] += 1
            b[4].add(component)
            b[5].add(pid)
            ts = f"{date} {time}"
            if not b[6]:
                b[6] = ts
            b[7] = ts
            if len(b[8]) < 50:
                b[8].append(component)

flush_logs(force=True)

print(f"\nParsed  : {n - 1:,} lines")
print(f"Skipped : {skipped:,}")
print(f"Blocks  : {len(blocks):,}")

# ---- insert blocks ----
print("Inserting blocks ...")
rows = []
for bid, b in blocks.items():
    duration = 0
    if b[6] and b[7]:
        try:
            t1, t2 = b[6].split()[1], b[7].split()[1]
            s1 = int(t1[0:2]) * 3600 + int(t1[2:4]) * 60 + int(t1[4:6])
            s2 = int(t2[0:2]) * 3600 + int(t2[2:4]) * 60 + int(t2[4:6])
            duration = s2 - s1
            if duration < 0:
                duration = 0
        except Exception:
            pass
    rows.append((bid, b[0], b[1], b[2], b[3], len(b[4]), len(b[5]),
                 b[6], b[7], duration, ",".join(b[8])))

for i in range(0, len(rows), BATCH):
    cur.executemany(
        "INSERT INTO blocks (block_id, log_count, info_count, warn_count, error_count, "
        "unique_components, unique_pids, first_ts, last_ts, duration_sec, event_seq) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows[i:i + BATCH],
    )
print(f"Inserted {len(rows):,} blocks")

cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_logs_block ON logs(block_id)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_blocks_error ON blocks(error_count)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_blocks_warn ON blocks(warn_count)")
conn.commit()

# ---- export features.csv ----
print(f"Exporting {FEATURES_CSV} ...")
cur.execute("""
SELECT block_id, log_count, info_count, warn_count, error_count,
       unique_components, unique_pids, duration_sec
FROM blocks ORDER BY block_id
""")
with open(FEATURES_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["block_id", "log_count", "info_count", "warn_count",
                "error_count", "unique_components", "unique_pids", "duration_sec"])
    w.writerows(cur.fetchall())

tot = cur.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
errb = cur.execute("SELECT COUNT(*) FROM blocks WHERE error_count>0").fetchone()[0]
warnb = cur.execute("SELECT COUNT(*) FROM blocks WHERE warn_count>0").fetchone()[0]
totl = cur.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
conn.close()

print("\n" + "=" * 56)
print("LOAD COMPLETE")
print("=" * 56)
print(f"Blocks           : {tot:,}")
print(f"  with ERROR     : {errb:,}  ({errb/max(tot,1)*100:.3f}%)")
print(f"  with WARN      : {warnb:,}  ({warnb/max(tot,1)*100:.2f}%)")
print(f"Sampled logs     : {totl:,}")
print(f"DB               : {DB_PATH}")
print(f"Features CSV     : {FEATURES_CSV}")
