import { useEffect, useState } from 'react';
import axios from 'axios';

const RCA_API = 'http://127.0.0.1:8000/api/rca/analyze';

const SEVERITY_STYLE = {
  P0: { bg: 'rgba(239,68,68,0.18)', fg: '#fca5a5', border: '#ef4444' },
  P1: { bg: 'rgba(251,146,60,0.18)', fg: '#fdba74', border: '#f97316' },
  P2: { bg: 'rgba(234,179,8,0.18)', fg: '#fde047', border: '#eab308' },
};

function Badge({ level }) {
  const s = SEVERITY_STYLE[level] || SEVERITY_STYLE.P2;
  return (
    <span
      className="sev-badge"
      style={{ background: s.bg, color: s.fg, borderColor: s.border }}
    >
      {level}
    </span>
  );
}

export default function RCAModal({ cluster, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!cluster) return;
    setData(null);
    setLoading(true);
    setError(null);
    axios
      .post(`${RCA_API}/${cluster.cluster_id}`)
      .then((res) => setData(res.data))
      .catch((err) => {
        setError(err.response?.data?.detail || err.message);
      })
      .finally(() => setLoading(false));
  }, [cluster]);

  if (!cluster) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <p className="eyebrow">Root Cause Analysis · Cluster #{cluster.cluster_id}</p>
            <h3 className="modal-title">{cluster.template}</h3>
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        <div className="modal-meta">
          <span>{cluster.log_count.toLocaleString()} logs</span>
          <span>{(cluster.anomaly_rate * 100).toFixed(1)}% anomalous</span>
          <span>
            E={cluster.error_count} W={cluster.warn_count} I={cluster.info_count}
          </span>
        </div>

        {loading && <p className="modal-loading">Claude Opus 4.6 đang phân tích...</p>}

        {error && <p className="modal-error">Lỗi: {error}</p>}

        {data && (
          <div className="modal-body">
            <div className="rca-section">
              <div className="rca-section-head">
                <h4>Tóm tắt</h4>
                <Badge level={data.severity} />
              </div>
              <p>{data.summary}</p>
            </div>

            <div className="rca-section">
              <h4>Nguyên nhân có thể</h4>
              <ul>
                {data.causes.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>

            <div className="rca-section">
              <h4>Cách khắc phục</h4>
              <ul>
                {data.fixes.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ul>
            </div>

            <div className="rca-footer">
              <span>
                {data.n_logs} log mẫu · model claude-opus-4.6 ·{' '}
                {data.cached ? 'cache hit' : 'fresh'}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
