#!/usr/bin/env python3
"""
Cluster HDFS log messages into templates using Drain3.

Drain3 builds a parse tree over the message tokens and merges messages that
share a common prefix into a template (e.g. "Received block blk_<ID> of size
<NUM> from <IP>"). Each template becomes one cluster.

Input : data/logs.db        (logs table)
        data/logs.db        (blocks table, for anomaly attribution)
Output: logs.cluster_id / logs.template_id populated
        clusters table (cluster_id, template, count, level mix, samples)

Usage:
    python ml-models/cluster_logs.py
    python ml-models/cluster_logs.py --limit 100000     # quick test
"""
import argparse
import re
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from drain3 import TemplateMiner

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB_PATH = ROOT / "data" / "logs.db"

# Applied before Drain sees the text, so the parser groups on structure
# instead of on volatile ids. Order matters: blk_ ids contain digits, and
# IPs contain digits, so both must be masked before the bare-number rule.
MASK_RULES = [
    (re.compile(r"blk_-?\d+"), "blk_<ID>"),
    (re.compile(r"\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?"), "<IP>"),
    (re.compile(r"\b\d+\b"), "<NUM>"),
]


def mask(text: str) -> str:
    for pattern, repl in MASK_RULES:
        text = pattern.sub(repl, text)
    return text


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(logs)").fetchall()}
    if "cluster_id" not in cols:
        conn.execute("ALTER TABLE logs ADD COLUMN cluster_id INTEGER")
    if "template_id" not in cols:
        conn.execute("ALTER TABLE logs ADD COLUMN template_id INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_cluster ON logs(cluster_id)")
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="only cluster the first N logs (0 = all)")
    ap.add_argument("--sim-th", type=float, default=0.4,
                    help="Drain similarity threshold (higher = more clusters)")
    ap.add_argument("--depth", type=int, default=4,
                    help="Drain parse tree depth")
    args = ap.parse_args()

    if not DB_PATH.exists():
        raise SystemExit(f"Missing {DB_PATH}. Run: python scripts/load_hdfs.py")

    from drain3.template_miner_config import TemplateMinerConfig

    config = TemplateMinerConfig()
    config.drain_sim_th = args.sim_th
    config.drain_depth = args.depth
    config.profiling_enabled = False
    config.snapshot_interval_minutes = 0  # disable periodic snapshots
    miner = TemplateMiner(config=config)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_columns(conn)

    q = "SELECT id, message, level, block_id FROM logs ORDER BY id"
    if args.limit:
        q += f" LIMIT {args.limit}"
    print(f"Reading logs from {DB_PATH} ...")
    rows = conn.execute(q).fetchall()
    print(f"  logs to cluster : {len(rows):,}")

    # blocks.is_anomaly feeds per-cluster anomaly attribution
    block_anom = dict(conn.execute("SELECT block_id, is_anomaly FROM blocks").fetchall())
    print(f"  known blocks    : {len(block_anom):,}")

    print(f"\nRunning Drain3 (sim_th={args.sim_th}, depth={args.depth}) ...")
    t0 = time.perf_counter()

    updates = []                                   # (cluster_id, template_id, log_id)
    cluster_stats = defaultdict(lambda: {
        "count": 0, "error": 0, "warn": 0, "info": 0,
        "anomaly": 0, "template": "", "sample": "",
    })

    for log_id, message, level, block_id in rows:
        clean = mask((message or "").strip())
        result = miner.add_log_message(clean)
        cid = result["cluster_id"]
        template = result["template_mined"]

        updates.append((cid, cid, log_id))  # template_id == cluster_id in Drain3

        st = cluster_stats[cid]
        st["count"] += 1
        st["template"] = template
        if not st["sample"]:
            st["sample"] = (message or "").strip()[:300]
        lvl = (level or "").upper()
        if lvl in ("ERROR", "FATAL"):
            st["error"] += 1
        elif lvl == "WARN":
            st["warn"] += 1
        else:
            st["info"] += 1
        if block_id and block_anom.get(block_id):
            st["anomaly"] += 1

    elapsed = time.perf_counter() - t0
    print(f"  done in         : {elapsed:.2f}s "
          f"({len(rows) / max(elapsed, 1e-9):,.0f} logs/s)")
    print(f"  templates found : {len(cluster_stats):,}")

    print("\nWriting cluster_id back to logs ...")
    t0 = time.perf_counter()
    conn.executemany(
        "UPDATE logs SET cluster_id = ?, template_id = ? WHERE id = ?", updates
    )
    conn.commit()
    print(f"  updated {len(updates):,} rows in {time.perf_counter() - t0:.2f}s")

    print("Rebuilding clusters table ...")
    conn.execute("DROP TABLE IF EXISTS clusters")
    conn.execute("""
        CREATE TABLE clusters (
            cluster_id   INTEGER PRIMARY KEY,
            template     TEXT,
            log_count    INTEGER,
            error_count  INTEGER,
            warn_count   INTEGER,
            info_count   INTEGER,
            anomaly_count INTEGER,
            anomaly_rate REAL,
            sample       TEXT
        )
    """)
    payload = []
    for cid, st in cluster_stats.items():
        payload.append((
            int(cid), st["template"], st["count"], st["error"], st["warn"],
            st["info"], st["anomaly"],
            round(st["anomaly"] / max(st["count"], 1), 4), st["sample"],
        ))
    conn.executemany(
        "INSERT INTO clusters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", payload
    )
    conn.commit()
    conn.close()

    print("\n" + "=" * 78)
    print("TOP 10 CLUSTERS BY VOLUME")
    print("=" * 78)
    payload.sort(key=lambda r: -r[2])
    for cid, template, count, err, warn, info, anom, arate, _ in payload[:10]:
        print(f"[{cid:>3}] {count:>7,} logs | E={err:<5} W={warn:<6} "
              f"| anomaly={arate:.1%}")
        print(f"      {template[:110]}")

    sus = sorted((r for r in payload if r[2] >= 100), key=lambda r: -r[7])[:10]
    print("\n" + "=" * 78)
    print("TOP 10 CLUSTERS BY ANOMALY RATE (min 100 logs)")
    print("=" * 78)
    for cid, template, count, err, warn, info, anom, arate, _ in sus:
        print(f"[{cid:>3}] {arate:>7.1%} anomalous over {count:,} logs")
        print(f"      {template[:110]}")

    print(f"\nClusters written to {DB_PATH} (table: clusters)")


if __name__ == "__main__":
    main()
