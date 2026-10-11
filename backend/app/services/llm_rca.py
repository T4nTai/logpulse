"""
LLM Root Cause Analysis service.

Given a cluster_id, pull sample logs from SQLite, send to Claude,
and return a structured diagnosis (summary / causes / fixes / severity).

Results are cached in the `rca_results` table so repeated calls for the
same cluster don't burn API quota.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = ROOT / "data" / "logs.db"

SYSTEM_PROMPT = """Bạn là kỹ sư SRE phân tích log production của hệ thống HDFS.
Nhiệm vụ: đưa ra chẩn đoán ngắn gọn, hành động được, dựa trên các log mẫu.

Trả lời bằng tiếng Việt, đúng JSON schema sau (không thêm markdown fence):
{
  "summary": "một câu tóm tắt vấn đề",
  "causes": ["nguyên nhân 1", "nguyên nhân 2", "nguyên nhân 3"],
  "fixes": ["cách khắc phục 1", "cách khắc phục 2"],
  "severity": "P0|P1|P2"
}

Quy tắc severity:
- P0: mất dữ liệu / block không đọc được / nhiều node cùng lỗi
- P1: giảm hiệu năng / retry nhiều / một node lỗi
- P2: cảnh báo nhỏ / bình thường trong vận hành

Chỉ dùng thông tin có trong log. Không bịa. Nếu log quá ít hoặc mơ hồ,
nói rõ trong summary và đặt severity P2."""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_cache_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rca_results (
            cluster_id INTEGER PRIMARY KEY,
            summary TEXT,
            causes TEXT,
            fixes TEXT,
            severity TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def _fetch_cluster_logs(conn: sqlite3.Connection, cluster_id: int, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        """
        SELECT l.level, l.component, l.message, l.timestamp
        FROM logs l
        WHERE l.cluster_id = ?
        ORDER BY l.id DESC
        LIMIT ?
        """,
        (cluster_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_cluster_meta(conn: sqlite3.Connection, cluster_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM clusters WHERE cluster_id = ?", (cluster_id,)
    ).fetchone()
    return dict(row) if row else None


def _format_logs_for_prompt(logs: list[dict]) -> str:
    lines = []
    for log in logs:
        ts = log.get("timestamp") or "?"
        level = log.get("level") or "?"
        comp = log.get("component") or "?"
        msg = (log.get("message") or "").strip()
        lines.append(f"[{ts}] {level} {comp}: {msg}")
    return "\n".join(lines)


def _call_claude(prompt: str) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError(
            "anthropic SDK not installed. Run: pip install anthropic"
        ) from e

    client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
    resp = client.messages.create(
        model=model,
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def analyze_cluster(cluster_id: int, use_cache: bool = True) -> dict:
    """
    Analyze a log cluster with Claude. Returns dict with keys:
        cluster_id, summary, causes, fixes, severity, cached, n_logs
    """
    conn = _get_conn()
    _ensure_cache_table(conn)

    if use_cache:
        row = conn.execute(
            "SELECT summary, causes, fixes, severity FROM rca_results WHERE cluster_id = ?",
            (cluster_id,),
        ).fetchone()
        if row:
            conn.close()
            return {
                "cluster_id": cluster_id,
                "summary": row["summary"],
                "causes": json.loads(row["causes"]),
                "fixes": json.loads(row["fixes"]),
                "severity": row["severity"],
                "cached": True,
                "n_logs": 0,
            }

    meta = _fetch_cluster_meta(conn, cluster_id)
    if meta is None:
        conn.close()
        raise ValueError(f"cluster_id {cluster_id} not found")

    logs = _fetch_cluster_logs(conn, cluster_id)
    if not logs:
        conn.close()
        raise ValueError(f"cluster_id {cluster_id} has no logs")

    prompt = f"""Phân tích cụm log sau (cluster {cluster_id}, tổng {meta['log_count']} log, tỉ lệ anomaly {meta['anomaly_rate'] * 100:.1f}%):

{_format_logs_for_prompt(logs)}

Trả lời JSON theo schema đã chỉ định."""

    result = _call_claude(prompt)

    conn.execute(
        """
        INSERT OR REPLACE INTO rca_results (cluster_id, summary, causes, fixes, severity)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            cluster_id,
            result.get("summary", ""),
            json.dumps(result.get("causes", []), ensure_ascii=False),
            json.dumps(result.get("fixes", []), ensure_ascii=False),
            result.get("severity", "P2"),
        ),
    )
    conn.commit()
    conn.close()

    return {
        "cluster_id": cluster_id,
        "summary": result.get("summary", ""),
        "causes": result.get("causes", []),
        "fixes": result.get("fixes", []),
        "severity": result.get("severity", "P2"),
        "cached": False,
        "n_logs": len(logs),
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("cluster_id", type=int)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    out = analyze_cluster(args.cluster_id, use_cache=not args.no_cache)
    print(json.dumps(out, ensure_ascii=False, indent=2))
