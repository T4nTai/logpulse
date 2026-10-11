#!/usr/bin/env python3
"""
Train Isolation Forest on HDFS block-level features.

Input : data/features.csv        (575,061 blocks, 7 numeric features)
        data/anomaly_label.csv   (ground truth: Normal / Anomaly)
Output: ml-models/models/anomaly_detector.pkl
        ml-models/models/metrics.json
        updates blocks.is_anomaly / blocks.anomaly_score in SQLite

Usage:
    python ml-models/train_anomaly.py
    python ml-models/train_anomaly.py --contamination 0.0293
    python ml-models/train_anomaly.py --n-estimators 300
"""
import argparse
import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sqlite3
from sklearn.ensemble import IsolationForest
from sklearn.metrics import (average_precision_score, classification_report,
                             confusion_matrix, roc_auc_score)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FEATURES_CSV = ROOT / "data" / "features.csv"
LABELS_CSV = ROOT / "data" / "anomaly_label.csv"
MODEL_DIR = HERE / "models"
MODEL_PATH = MODEL_DIR / "anomaly_detector.pkl"
METRICS_PATH = MODEL_DIR / "metrics.json"
DB_PATH = ROOT / "data" / "logs.db"

# error_count removed — it was both a feature and the proxy label (data leakage)
FEATURES = [
    "log_count",
    "info_count",
    "warn_count",
    "unique_components",
    "unique_pids",
    "duration_sec",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contamination", type=float, default=0.0293,
                    help="expected outlier fraction (default 0.0293 = HDFS ground truth rate)")
    ap.add_argument("--n-estimators", type=int, default=200)
    ap.add_argument("--max-samples", type=float, default=1.0,
                    help="subsample ratio for the forest (1.0 = all rows)")
    ap.add_argument("--no-writeback", action="store_true",
                    help="do not update the blocks table in SQLite")
    args = ap.parse_args()

    if not FEATURES_CSV.exists():
        raise SystemExit(f"Missing {FEATURES_CSV}. Run: python scripts/load_hdfs.py")
    if not LABELS_CSV.exists():
        raise SystemExit(f"Missing {LABELS_CSV}. Download from logpai/loglizer repo.")

    print(f"Loading features from {FEATURES_CSV} ...")
    df = pd.read_csv(FEATURES_CSV)
    print(f"  rows    : {len(df):,}")
    print(f"  columns : {list(df.columns)}")

    print(f"\nLoading ground truth from {LABELS_CSV} ...")
    labels_df = pd.read_csv(LABELS_CSV)
    labels_df.columns = [c.strip() for c in labels_df.columns]
    label_map = dict(zip(labels_df["BlockId"], labels_df["Label"]))
    df["label"] = df["block_id"].map(label_map)
    n_missing = df["label"].isna().sum()
    if n_missing:
        print(f"  WARNING: {n_missing:,} blocks have no label — dropping them")
        df = df.dropna(subset=["label"]).reset_index(drop=True)
    y_true = (df["label"] == "Anomaly").astype(int).to_numpy()
    print(f"  Anomaly : {y_true.sum():,}  ({y_true.sum() / len(df) * 100:.2f}%)")
    print(f"  Normal  : {(1 - y_true).sum():,}")

    X = df[FEATURES].to_numpy(dtype=np.float32)

    print(f"\nTraining Isolation Forest")
    print(f"  n_estimators   : {args.n_estimators}")
    print(f"  contamination  : {args.contamination}")
    print(f"  features       : {FEATURES}")
    t0 = time.perf_counter()
    model = IsolationForest(
        n_estimators=args.n_estimators,
        contamination=args.contamination,
        max_samples=args.max_samples,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X)
    train_secs = time.perf_counter() - t0
    print(f"  trained in     : {train_secs:.2f}s")

    # ---- inference ----
    t0 = time.perf_counter()
    scores = model.decision_function(X)   # higher = more normal
    pred = model.predict(X)               # -1 = outlier
    infer_secs = time.perf_counter() - t0
    y_pred = (pred == -1).astype(int)
    print(f"  inference     : {infer_secs:.2f}s for {len(df):,} blocks")

    # ---- evaluation vs ground truth ----
    anomaly_score = -scores               # higher = more anomalous
    report = classification_report(y_true, y_pred, target_names=["normal", "anomaly"],
                                   zero_division=0, output_dict=True)
    cm = confusion_matrix(y_true, y_pred).tolist()
    metrics = {
        "model": "IsolationForest",
        "n_features": len(FEATURES),
        "features": FEATURES,
        "n_rows": int(len(df)),
        "n_estimators": args.n_estimators,
        "contamination": args.contamination,
        "label_source": "anomaly_label.csv (HDFS_v1 ground truth)",
        "n_anomaly_true": int(y_true.sum()),
        "n_flagged": int(y_pred.sum()),
        "train_seconds": round(train_secs, 2),
        "inference_seconds": round(infer_secs, 2),
        "precision": round(report["anomaly"]["precision"], 4),
        "recall": round(report["anomaly"]["recall"], 4),
        "f1": round(report["anomaly"]["f1-score"], 4),
        "average_precision": round(average_precision_score(y_true, y_pred), 4),
        "roc_auc": round(roc_auc_score(y_true, anomaly_score), 4),
        "confusion_matrix": {"tn": cm[0][0], "fp": cm[0][1], "fn": cm[1][0], "tp": cm[1][1]},
    }

    print("\n" + "=" * 56)
    print("EVALUATION vs HDFS_v1 ground truth")
    print("=" * 56)
    print(f"  flagged as anomaly : {metrics['n_flagged']:,}")
    print(f"  true anomalies     : {metrics['n_anomaly_true']:,}")
    print(f"  true positives     : {metrics['confusion_matrix']['tp']:,}")
    print(f"  false positives    : {metrics['confusion_matrix']['fp']:,}")
    print(f"  false negatives    : {metrics['confusion_matrix']['fn']:,}")
    print(f"  precision          : {metrics['precision']}")
    print(f"  recall             : {metrics['recall']}")
    print(f"  f1                 : {metrics['f1']}")
    print(f"  average precision  : {metrics['average_precision']}")
    print(f"  roc auc            : {metrics['roc_auc']}")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "features": FEATURES}, MODEL_PATH)
    print(f"\nSaved model  -> {MODEL_PATH}")

    # ---- top 20 most anomalous blocks ----
    order = np.argsort(-anomaly_score)[:20]
    top = df.iloc[order].copy()
    top["anomaly_score"] = anomaly_score[order]
    top_path = ROOT / "data" / "top_anomalies.csv"
    top.to_csv(top_path, index=False)
    print(f"Saved top-20 -> {top_path}")
    print("\n  Top 5 most anomalous blocks:")
    print(top[["block_id", "log_count", "warn_count", "error_count",
               "unique_components", "duration_sec", "label", "anomaly_score"]]
          .head(5).to_string(index=False))

    # ---- write back to SQLite ----
    if not args.no_writeback:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        ids = df["block_id"].to_numpy()
        cur.executemany(
            "UPDATE blocks SET is_anomaly = ?, anomaly_score = ? WHERE block_id = ?",
            [(int(y_pred[i]), float(anomaly_score[i]), ids[i]) for i in range(len(df))],
        )
        conn.commit()
        n_flagged = cur.execute("SELECT COUNT(*) FROM blocks WHERE is_anomaly=1").fetchone()[0]
        conn.close()
        print(f"\nUpdated blocks table: {n_flagged:,} blocks flagged is_anomaly=1")

    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(f"Saved metrics -> {METRICS_PATH}")


if __name__ == "__main__":
    main()
