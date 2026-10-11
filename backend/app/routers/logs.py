from fastapi import APIRouter
from typing import Optional
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', '..', 'data', 'logs.db')


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


router = APIRouter()


@router.on_event("startup")
def startup():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            level TEXT,
            component TEXT,
            message TEXT,
            block_id TEXT,
            is_anomaly INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS blocks (
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
    conn.commit()
    conn.close()


@router.post("/logs/ingest")
def ingest(logs: list[dict]):
    conn = get_conn()
    cur = conn.cursor()
    for l in logs:
        cur.execute(
            "INSERT INTO logs (timestamp, level, component, message, block_id) VALUES (?, ?, ?, ?, ?)",
            (l.get("timestamp"), l.get("level"), l.get("component"), l.get("message"), l.get("block_id")),
        )
    conn.commit()
    conn.close()
    return {"inserted": len(logs)}


@router.get("/logs")
def list_logs(level: Optional[str] = None, block_id: Optional[str] = None, limit: int = 100):
    conn = get_conn()
    q = "SELECT * FROM logs"
    conds, params = [], []
    if level:
        conds.append("level = ?")
        params.append(level)
    if block_id:
        conds.append("block_id = ?")
        params.append(block_id)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(min(limit, 1000))
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/logs/stats")
def log_stats():
    conn = get_conn()
    total_logs = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    total_blocks = conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
    errors = conn.execute(
        "SELECT COUNT(*) FROM logs WHERE level IN ('ERROR', 'FATAL')"
    ).fetchone()[0]
    warnings = conn.execute(
        "SELECT COUNT(*) FROM logs WHERE level = 'WARN'"
    ).fetchone()[0]
    anomalies = conn.execute(
        "SELECT COUNT(*) FROM blocks WHERE is_anomaly = 1"
    ).fetchone()[0]
    conn.close()
    error_rate = round(errors / max(total_logs, 1) * 100, 2)
    warn_rate = round(warnings / max(total_logs, 1) * 100, 2)
    return {
        "total_logs": total_logs,
        "total_blocks": total_blocks,
        "errors": errors,
        "warnings": warnings,
        "anomalies": anomalies,
        "error_rate": error_rate,
        "warn_rate": warn_rate,
    }


@router.get("/clusters")
def list_clusters(sort: str = "log_count", min_logs: int = 0, limit: int = 50):
    """Top log clusters (templates) discovered by Drain3.

    sort= log_count | anomaly_rate | error | warn
    """
    conn = get_conn()
    allowed = {
        "log_count": "log_count DESC",
        "anomaly_rate": "anomaly_rate DESC",
        "error": "error_count DESC",
        "warn": "warn_count DESC",
    }
    order = allowed.get(sort, "log_count DESC")
    q = "SELECT * FROM clusters WHERE log_count >= ?"
    params = [min_logs]
    q += f" ORDER BY {order} LIMIT ?"
    params.append(min(limit, 500))
    try:
        rows = conn.execute(q, params).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []
    conn.close()
    return [dict(r) for r in rows]


@router.get("/clusters/{cluster_id}")
def cluster_detail(cluster_id: int, limit: int = 50):
    """One cluster's template plus a sample of raw log lines from it."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM clusters WHERE cluster_id = ?", (cluster_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is None:
        conn.close()
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Cluster not found")
    logs = conn.execute(
        "SELECT id, timestamp, level, component, message, block_id "
        "FROM logs WHERE cluster_id = ? ORDER BY id LIMIT ?",
        (cluster_id, min(limit, 200)),
    ).fetchall()
    conn.close()
    cluster = dict(row)
    cluster["sample_logs"] = [dict(r) for r in logs]
    return cluster


@router.get("/blocks")
def list_blocks(
    sort: Optional[str] = "log_count",
    min_errors: Optional[int] = None,
    limit: int = 50,
):
    conn = get_conn()
    allowed_sorts = {
        "log_count": "log_count DESC",
        "warn": "warn_count DESC",
        "error": "error_count DESC",
        "duration": "duration_sec DESC",
        "anomaly": "anomaly_score DESC",
    }
    order = allowed_sorts.get(sort, "log_count DESC")
    q = "SELECT block_id, log_count, warn_count, error_count, unique_components, duration_sec, is_anomaly, anomaly_score FROM blocks"
    params = []
    if min_errors is not None:
        q += " WHERE error_count >= ?"
        params.append(min_errors)
    q += f" ORDER BY {order} LIMIT ?"
    params.append(min(limit, 500))
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/blocks/{block_id}")
def block_detail(block_id: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT block_id, log_count, info_count, warn_count, error_count, "
        "unique_components, unique_pids, first_ts, last_ts, duration_sec, "
        "event_seq, is_anomaly, anomaly_score FROM blocks WHERE block_id = ?",
        (block_id,),
    ).fetchone()
    if row is None:
        conn.close()
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Block not found")
    logs = conn.execute(
        "SELECT id, timestamp, level, component, message FROM logs "
        "WHERE block_id = ? ORDER BY id LIMIT 100",
        (block_id,),
    ).fetchall()
    conn.close()
    block = dict(row)
    block["sample_logs"] = [dict(r) for r in logs]
    return block
