# LogPulse

AIOps pipeline cho HDFS: log ingestion → template clustering → anomaly detection → LLM root-cause analysis.

## Kiến trúc

```
HDFS logs (11.2M lines) → Parser → Block aggregation → Features
                                              │
                          ┌───────────────────┼───────────────────┐
                          ▼                   ▼                   ▼
                      Drain3 (cluster)  IsolationForest    Claude Opus 4.6
                      51 templates      ROC AUC 0.97       RCA summary
                          │                   │                   │
                          └───────────────────┼───────────────────┘
                                              ▼
                                    SQLite → FastAPI → React
```

| Thành phần | Công nghệ | Ghi chú |
|-----------|-----------|---------|
| Parser | Python, string split | 575.061 blocks, sampling ~509K logs |
| Clustering | Drain3 | sim_th 0.4, depth 4 |
| Anomaly | IsolationForest (sklearn) | 6 features, contamination 0.0293 |
| LLM RCA | Anthropic Messages API | system prompt + 30 log samples, cache SQLite |
| API | FastAPI | prefix `/api` |
| Frontend | React 18, Vite, Recharts, Axios |  |
| DB | SQLite | `data/logs.db` (~504 MB) |

## Dataset

- **HDFS v1** — 11.1M lines (`data/sorted.log`, Xu et al.), block_id `blk_-?\d+`.
- **Ground truth** — `data/anomaly_label.csv` (logpai/loglizer, 17.2 MB): 558.223 Normal / 16.838 Anomaly (2,93%).
- **Features** — `log_count, info_count, warn_count, unique_components, unique_pids, duration_sec` (bỏ `error_count` để tránh leakage).
- **Đặc trưng** — WARN chiếm 23,35% blocks, ERROR chỉ 0,013%; `error_count` cũ làm ROC 0,9996 giả.

## Kết quả

### Anomaly detection (`ml-models/models/metrics.json`)

```
n_rows 575.061 | n_anomaly 16.838 | contamination 0.0293
precision 0.4706 | recall 0.4638 | F1 0.4672
ROC AUC 0.9703 | average precision 0.234
train 18.95s | inference 17.73s | flagged 16.595
TN 549.438 | FP 8.785 | FN 9.028 | TP 7.810
```

Trước khi dùng ground truth (proxy `error_count > 0`): precision 0,013, F1 0,0257, ROC 0,9996 — kết quả vô nghĩa do leakage.

### Clustering (`data/logs.db: clusters`)

- Full HDFS: 51 templates (Drain3, ~0,90s/100K logs).
- Ví dụ anomaly 100%: `Block blk_<ID> is not valid`, `Connection reset by peer`.

### RCA

- `POST /api/rca/analyze/{cluster_id}` → `{summary, causes, fixes, severity P0/P1/P2}`.
- Cache `rca_results` (INSERT OR REPLACE); powershell hiện CP1252 sẽ làm vỡ dấu tiếng Việt, data SQLite vẫn UTF-8 đúng.

## Khởi chạy

### Yêu cầu

- Python 3.11+, Node 20+ (kiểm chứng Node v20.20.2).
- `data/logs.db` đã sẵn; `data/sorted.log` + `anomaly_label.csv` không commit.

### Cài đặt

```powershell
# backend
cd backend
.\.venv\Scripts\Activate.ps1     # lần đầu: python -m venv .venv; pip install -r requirements.txt
pip install -r requirements.txt  # fastapi, uvicorn, python-dotenv, scikit-learn, apscheduler, joblib
pip install anthropic            # nếu chưa có
copy .env.example .env           # điền ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL

# ml
pip install -r ..\ml-models\requirements.txt  # scikit-learn, pandas, drain3, ...

# frontend
cd ..\frontend
npm install
```

### Biến môi trường (`backend/.env`)

```
ANTHROPIC_API_KEY=sk-...
ANTHROPIC_BASE_URL=https://api.anthropic.com
ANTHROPIC_MODEL=claude-sonnet-4-20250514
```

Bản sao mẫu: `backend/.env.example`. File `.env` chứa secret nên đã gitignored.

### Chạy dev

```powershell
# terminal 1
cd backend
.\.venv\Scripts\Activate.ps1
$env:ANTHROPIC_API_KEY="..."; $env:ANTHROPIC_BASE_URL="https://api.anthropic.com"; $env:ANTHROPIC_MODEL="claude-sonnet-4-20250514"
uvicorn app.main:app --reload                  # http://127.0.0.1:8000, /docs

# terminal 2
cd frontend
npm run dev                                   # http://localhost:5173
npm run build                                 # kiểm tra build (đã pass: 3.67s, 586 kB)
```

### Re-train / re-cluster (tùy chọn)

```powershell
python ml-models\cluster_logs.py --limit 100000   # test
python ml-models\cluster_logs.py                 # full
python ml-models\train_anomaly.py --contamination 0.0293
```

## API

- `GET /api/logs?level=&block_id=&limit=`
- `GET /api/logs/stats` — tổng, errors/warnings, anomalies, rate
- `GET /api/blocks?sort=log_count|warn|error|duration|anomaly&min_errors=&limit=`
- `GET /api/blocks/{block_id}` — block + 100 sample logs
- `GET /api/clusters?sort=log_count|anomaly_rate|error|warn&min_logs=&limit=`
- `GET /api/clusters/{cluster_id}`
- `POST /api/rca/analyze/{cluster_id}?use_cache=true|false`
- `GET /api/rca/results?limit=`

## Cấu trúc thư mục

```
.
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── routers/{logs,rca}.py
│   │   └── services/{log_parser,llm_rca}.py
│   ├── requirements.txt
│   └── .env.example
├── ml-models/
│   ├── train_anomaly.py
│   ├── cluster_logs.py
│   ├── requirements.txt
│   └── models/{anomaly_detector.pkl, metrics.json}
├── frontend/
│   └── src/{App.jsx, RCAModal.jsx, index.css, main.jsx}
├── scripts/{load_hdfs.py, load_bgl.py}
└── data/
    ├── logs.db
    ├── features.csv
    ├── anomaly_label.csv
    ├── sorted.log
    ├── top_anomalies.csv
    └── loghub/        # clone riêng, không commit
```

## Ghi chú

- Không commit `data/sorted.log`, `*.db`, `.env` (xem `.gitignore`).
- Kiểm tra UTF-8 RCA trực tiếp SQLite: `python -c "import sqlite3; print(sqlite3.connect('data/logs.db').execute('SELECT summary FROM rca_results WHERE cluster_id=34').fetchone()[0])"`.
