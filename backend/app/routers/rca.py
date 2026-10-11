from fastapi import APIRouter, HTTPException

from app.services.llm_rca import analyze_cluster

router = APIRouter()


@router.post("/rca/analyze/{cluster_id}")
def analyze(cluster_id: int, use_cache: bool = True):
    """Run LLM root-cause analysis for a log cluster."""
    try:
        return analyze_cluster(cluster_id, use_cache=use_cache)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/rca/results")
def list_results(limit: int = 50):
    """List cached RCA results, newest first."""
    import sqlite3
    from pathlib import Path

    db = Path(__file__).resolve().parent.parent.parent.parent / "data" / "logs.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM rca_results ORDER BY created_at DESC LIMIT ?",
            (min(limit, 200),),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    import json

    return [
        {
            **dict(r),
            "causes": json.loads(r["causes"]) if r["causes"] else [],
            "fixes": json.loads(r["fixes"]) if r["fixes"] else [],
        }
        for r in rows
    ]