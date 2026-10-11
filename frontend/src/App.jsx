import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import RCAModal from './RCAModal';

const API_LOGS = 'http://127.0.0.1:8000/api/logs';
const API_STATS = 'http://127.0.0.1:8000/api/logs/stats';
const API_BLOCKS = 'http://127.0.0.1:8000/api/blocks?sort=anomaly&limit=10';
const API_CLUSTERS = 'http://127.0.0.1:8000/api/clusters?sort=anomaly_rate&min_logs=50&limit=15';

function App() {
  const [logs, setLogs] = useState([]);
  const [blocks, setBlocks] = useState([]);
  const [clusters, setClusters] = useState([]);
  const [stats, setStats] = useState({
    total_logs: 0, total_blocks: 0, errors: 0, warnings: 0,
    anomalies: 0, error_rate: 0, warn_rate: 0,
  });
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [activeCluster, setActiveCluster] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [logsRes, statsRes, blocksRes, clustersRes] = await Promise.all([
        axios.get(`${API_LOGS}?limit=20`),
        axios.get(API_STATS),
        axios.get(API_BLOCKS),
        axios.get(API_CLUSTERS),
      ]);
      setLogs(logsRes.data);
      setStats(statsRes.data);
      setBlocks(blocksRes.data);
      setClusters(clustersRes.data);
    } catch (error) {
      console.error('Failed to fetch:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const chartData = useMemo(() => {
    const byMinute = {};
    logs.forEach((log) => {
      const key = (log.timestamp || '').slice(0, 16);
      if (!key) return;
      byMinute[key] = (byMinute[key] || 0) + 1;
    });
    return Object.entries(byMinute)
      .map(([time, count]) => ({ time, count }))
      .slice(-12);
  }, [logs]);

  const visibleLogs = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return logs;
    return logs.filter(
      (l) =>
        (l.message || '').toLowerCase().includes(q) ||
        (l.component || '').toLowerCase().includes(q) ||
        (l.level || '').toLowerCase().includes(q),
    );
  }, [logs, search]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">AIOps Monitor</p>
          <h1>LogPulse</h1>
        </div>
        <input
          className="search-input"
          type="text"
          placeholder="Tìm trong log..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </header>

      <section className="stats-grid">
        <div className="stat-card">
          <span>Total blocks</span>
          <strong>{stats.total_blocks.toLocaleString()}</strong>
        </div>
        <div className="stat-card info">
          <span>Logs ingested</span>
          <strong>{stats.total_logs.toLocaleString()}</strong>
        </div>
        <div className="stat-card warning">
          <span>Warnings</span>
          <strong>{stats.warnings.toLocaleString()}</strong>
        </div>
        <div className="stat-card danger">
          <span>Errors</span>
          <strong>{stats.errors.toLocaleString()}</strong>
        </div>
      </section>

      <section className="stats-grid">
        <div className="stat-card">
          <span>Avg logs / block</span>
          <strong>{stats.total_blocks ? (stats.total_logs / stats.total_blocks).toFixed(1) : '0'}</strong>
        </div>
        <div className="stat-card danger">
          <span>Error rate</span>
          <strong>{stats.error_rate}%</strong>
        </div>
        <div className="stat-card warning">
          <span>Warn rate</span>
          <strong>{stats.warn_rate ?? 0}%</strong>
        </div>
        <div className="stat-card info">
          <span>Anomalies flagged</span>
          <strong>{stats.anomalies.toLocaleString()}</strong>
        </div>
      </section>

      <section className="panel table-panel">
        <h2>Alerts · clusters by anomaly rate</h2>
        <p className="panel-hint">Click một dòng để Claude phân tích nguyên nhân gốc.</p>
        {loading ? (
          <p>Loading...</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Template</th>
                <th>Logs</th>
                <th>Anomaly rate</th>
              </tr>
            </thead>
            <tbody>
              {clusters.map((c) => (
                <tr
                  key={c.cluster_id}
                  className="clickable-row"
                  onClick={() => setActiveCluster(c)}
                >
                  <td className="mono">#{c.cluster_id}</td>
                  <td className="mono template-cell">{c.template}</td>
                  <td>{c.log_count.toLocaleString()}</td>
                  <td className="level error">{(c.anomaly_rate * 100).toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel table-panel">
        <h2>Top anomalous blocks</h2>
        {loading ? (
          <p>Loading...</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Block ID</th>
                <th>Logs</th>
                <th>Warn</th>
                <th>Error</th>
                <th>Components</th>
                <th>Score</th>
              </tr>
            </thead>
            <tbody>
              {blocks.map((b) => (
                <tr key={b.block_id}>
                  <td className="mono">{b.block_id}</td>
                  <td>{b.log_count}</td>
                  <td className="level warn">{b.warn_count}</td>
                  <td className="level error">{b.error_count}</td>
                  <td>{b.unique_components}</td>
                  <td className="mono">{b.anomaly_score?.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel chart-panel">
        <h2>Log volume</h2>
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="time" stroke="#94a3b8" />
              <YAxis stroke="#94a3b8" allowDecimals={false} />
              <Tooltip />
              <Line type="monotone" dataKey="count" stroke="#f97316" strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="panel table-panel">
        <h2>Recent logs {search && `· filter: "${search}"`}</h2>
        {loading ? (
          <p>Loading logs...</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Level</th>
                <th>Component</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {visibleLogs.map((log) => (
                <tr key={log.id}>
                  <td>{log.timestamp || 'N/A'}</td>
                  <td className={`level ${String(log.level || '').toLowerCase()}`}>
                    {log.level || 'INFO'}
                  </td>
                  <td>{log.component || 'N/A'}</td>
                  <td>{log.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <RCAModal cluster={activeCluster} onClose={() => setActiveCluster(null)} />
    </div>
  );
}

export default App;