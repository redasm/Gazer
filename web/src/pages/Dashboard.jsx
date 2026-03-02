import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Activity, Cpu, Clock, HardDrive, MemoryStick, Zap, Database, Gauge, Timer } from 'lucide-react';
import axios from 'axios';
import API_BASE from '../config';

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const formatUptime = (seconds) => {
    if (!seconds || seconds < 0) return '--:--:--';
    const h = String(Math.floor(seconds / 3600)).padStart(2, '0');
    const m = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0');
    const s = String(seconds % 60).padStart(2, '0');
    return `${h}:${m}:${s}`;
};

const fmtNum = (n) => {
    if (n == null || isNaN(n)) return '--';
    if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
    return String(Math.round(n));
};

const fmtCost = (n) => {
    if (n == null || isNaN(n)) return '--';
    if (n >= 1000) return `$${(n / 1000).toFixed(1)}K`;
    if (n >= 1) return `$${n.toFixed(2)}`;
    return `$${n.toFixed(4)}`;
};

const DONUT_COLORS = ['#8b5cf6', '#3b82f6', '#06b6d4', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#6366f1'];
const TREND_COLORS = { input: '#f59e0b', output: '#3b82f6', cache: '#10b981' };

/* ------------------------------------------------------------------ */
/*  StatCard                                                           */
/* ------------------------------------------------------------------ */

const StatCard = ({ icon, label, value, sub, color }) => (
    <div className="glass-panel p-6">
        <div className="flex items-center gap-4 mb-4">
            {icon}
            <h3 className="font-bold text-lg text-white">{label}</h3>
        </div>
        <p className="text-3xl font-mono" style={{ color: color || '#fff' }}>{value}</p>
        {sub && <p style={{ color: '#667', fontSize: 12, marginTop: 6 }}>{sub}</p>}
    </div>
);

/* ------------------------------------------------------------------ */
/*  DonutChart — SVG donut with center label                           */
/* ------------------------------------------------------------------ */

const DonutChart = ({ slices, size = 180 }) => {
    const total = slices.reduce((s, d) => s + d.value, 0);
    if (!total) return <svg width={size} height={size} />;
    const cx = size / 2, cy = size / 2, r = size * 0.38, stroke = size * 0.12;
    let cumAngle = -90;
    const circumference = 2 * Math.PI * r;

    return (
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
            {slices.map((d, i) => {
                const pct = d.value / total;
                const dashLen = pct * circumference;
                const dashOff = -((cumAngle + 90) / 360) * circumference;
                cumAngle += pct * 360;
                return (
                    <circle key={i} cx={cx} cy={cy} r={r} fill="none"
                        stroke={DONUT_COLORS[i % DONUT_COLORS.length]}
                        strokeWidth={stroke}
                        strokeDasharray={`${dashLen} ${circumference - dashLen}`}
                        strokeDashoffset={-dashOff}
                        style={{ transition: 'stroke-dasharray 0.6s ease' }}
                    />
                );
            })}
            <text x={cx} y={cy - 6} textAnchor="middle" fill="#fff" fontSize={16} fontWeight="bold">
                {fmtNum(total)}
            </text>
            <text x={cx} y={cy + 14} textAnchor="middle" fill="#667" fontSize={11}>
                总调用
            </text>
        </svg>
    );
};

/* ------------------------------------------------------------------ */
/*  TrendChart — SVG area/line chart for token usage over time          */
/* ------------------------------------------------------------------ */

const TrendChart = ({ data, width = 480, height = 200 }) => {
    if (!data || !data.length) {
        return (
            <div style={{ width, height, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#667' }}>
                暂无趋势数据
            </div>
        );
    }

    const pad = { top: 20, right: 16, bottom: 32, left: 56 };
    const cw = width - pad.left - pad.right;
    const ch = height - pad.top - pad.bottom;

    const allVals = data.flatMap(d => [d.input || 0, d.output || 0, d.cache || 0]);
    const maxVal = Math.max(...allVals, 1);

    const xScale = (i) => pad.left + (i / Math.max(data.length - 1, 1)) * cw;
    const yScale = (v) => pad.top + ch - (v / maxVal) * ch;

    const makePath = (key) => {
        return data.map((d, i) => `${i === 0 ? 'M' : 'L'}${xScale(i).toFixed(1)},${yScale(d[key] || 0).toFixed(1)}`).join(' ');
    };
    const makeArea = (key) => {
        const line = data.map((d, i) => `${i === 0 ? 'M' : 'L'}${xScale(i).toFixed(1)},${yScale(d[key] || 0).toFixed(1)}`).join(' ');
        return `${line} L${xScale(data.length - 1).toFixed(1)},${(pad.top + ch).toFixed(1)} L${xScale(0).toFixed(1)},${(pad.top + ch).toFixed(1)} Z`;
    };

    // Y-axis labels
    const yTicks = [0, 0.25, 0.5, 0.75, 1].map(p => ({ val: maxVal * p, y: yScale(maxVal * p) }));

    return (
        <svg width={width} height={height} style={{ overflow: 'visible' }}>
            {/* Grid */}
            {yTicks.map((t, i) => (
                <g key={i}>
                    <line x1={pad.left} x2={width - pad.right} y1={t.y} y2={t.y} stroke="rgba(255,255,255,0.06)" />
                    <text x={pad.left - 8} y={t.y + 4} textAnchor="end" fill="#556" fontSize={10}>{fmtNum(t.val)}</text>
                </g>
            ))}

            {/* Areas */}
            {['cache', 'output', 'input'].map(key => (
                <path key={key} d={makeArea(key)} fill={TREND_COLORS[key]} opacity={0.1} />
            ))}

            {/* Lines */}
            {['cache', 'output', 'input'].map(key => (
                <path key={`l-${key}`} d={makePath(key)} fill="none" stroke={TREND_COLORS[key]} strokeWidth={2} />
            ))}

            {/* X-axis labels */}
            {data.map((d, i) => {
                if (data.length > 8 && i % Math.ceil(data.length / 7) !== 0 && i !== data.length - 1) return null;
                return (
                    <text key={i} x={xScale(i)} y={height - 6} textAnchor="middle" fill="#556" fontSize={10}>
                        {d.label || ''}
                    </text>
                );
            })}
        </svg>
    );
};

/* ------------------------------------------------------------------ */
/*  Main Dashboard                                                     */
/* ------------------------------------------------------------------ */

const Dashboard = ({ status, t }) => {
    const [sys, setSys] = useState(null);
    const [usage, setUsage] = useState(null);
    const [metrics, setMetrics] = useState(null);
    const [timeRange, setTimeRange] = useState('7d');
    const [granularity, setGranularity] = useState('day');

    const fetchSystem = useCallback(async () => {
        try {
            const res = await axios.get(`${API_BASE}/debug/system`);
            setSys(res.data);
        } catch { /* ignore */ }
    }, []);

    const fetchUsage = useCallback(async () => {
        try {
            const res = await axios.get(`${API_BASE}/health/usage`);
            setUsage(res.data);
        } catch { /* ignore */ }
    }, []);

    const fetchMetrics = useCallback(async () => {
        try {
            const res = await axios.get(`${API_BASE}/observability/metrics?limit=200`);
            setMetrics(res.data);
        } catch { /* ignore */ }
    }, []);

    useEffect(() => {
        fetchSystem();
        fetchUsage();
        fetchMetrics();
        const iv = setInterval(() => { fetchSystem(); fetchUsage(); fetchMetrics(); }, 10000);
        return () => clearInterval(iv);
    }, [fetchSystem, fetchUsage, fetchMetrics]);

    // Extract token stats from usage tracker
    const tokenStats = useMemo(() => {
        const u = usage?.usage;
        if (!u) return null;

        const totalInput = u.prompt_tokens ?? 0;
        const totalOutput = u.completion_tokens ?? 0;
        const totalCache = u.cache_read_tokens ?? u.cached_tokens ?? 0;
        const totalTokens = u.total_tokens ?? (totalInput + totalOutput);
        const totalRequests = u.requests ?? 0;
        const avgLatency = u.avg_latency_ms ?? 0;

        // Today
        const todayInput = u.today_input_tokens ?? 0;
        const todayOutput = u.today_output_tokens ?? 0;
        const todayTokens = u.today_total_tokens ?? (todayInput + todayOutput);
        const todayRequests = u.today_requests ?? 0;
        const todayCost = u.today_cost_usd ?? 0;

        // Per-model breakdown
        const byModel = u.by_model || {};

        return {
            totalInput, totalOutput, totalCache, totalTokens, totalRequests, avgLatency,
            todayInput, todayOutput, todayTokens, todayRequests, todayCost,
            byModel,
        };
    }, [usage]);

    // Model table: prefer usage tracker per-model, fall back to observability/metrics
    const modelTable = useMemo(() => {
        // Primary: per-model breakdown from usage tracker
        if (tokenStats?.byModel && Object.keys(tokenStats.byModel).length > 0) {
            return Object.entries(tokenStats.byModel).map(([name, data]) => ({
                name,
                requests: data.requests || 0,
                tokens: data.total_tokens || 0,
                cost: data.cost_usd || 0,
                avgLatency: data.avg_latency_ms || 0,
            })).sort((a, b) => b.tokens - a.tokens);
        }
        // Fallback: observability metrics
        if (metrics?.model?.length) {
            return metrics.model.map(m => ({
                name: m.model || 'unknown',
                requests: m.calls || 0,
                tokens: (m.input_tokens || 0) + (m.output_tokens || 0) || m.calls * 500,
                cost: m.estimated_cost_usd ?? 0,
                avgLatency: 0,
            }));
        }
        return [];
    }, [tokenStats, metrics]);

    // Donut slices
    const donutSlices = useMemo(() => {
        if (modelTable.length) return modelTable.map(m => ({ label: m.name, value: m.requests }));
        return [];
    }, [modelTable]);

    // Trend data from usage tracker daily breakdown
    const trendData = useMemo(() => {
        const daily = usage?.usage?.daily || [];
        if (Array.isArray(daily) && daily.length > 0) {
            return daily.map(d => ({
                label: d.date || '',
                input: d.input_tokens ?? 0,
                output: d.output_tokens ?? 0,
                cache: d.cache_tokens ?? 0,
            }));
        }
        // If we have any token data at all, show as single point
        if (tokenStats && tokenStats.totalTokens > 0) {
            return [{
                label: '累计',
                input: tokenStats.totalInput,
                output: tokenStats.totalOutput,
                cache: tokenStats.totalCache,
            }];
        }
        return [];
    }, [usage, tokenStats]);

    // Total requests from model table
    const totalModelRequests = useMemo(() => donutSlices.reduce((s, d) => s + d.value, 0), [donutSlices]);

    // RPM / TPM from metrics
    const rpm = useMemo(() => {
        const calls = metrics?.provider?.total_calls || tokenStats?.totalRequests || 0;
        if (!calls) return 0;
        // Estimate RPM: calls / uptime minutes (from sys), capped at reasonable value
        const uptimeMin = (sys?.uptime_seconds || 60) / 60;
        return Math.round(calls / Math.max(uptimeMin, 1));
    }, [metrics, tokenStats, sys]);

    const tpm = useMemo(() => {
        const tokens = tokenStats?.totalTokens || 0;
        if (!tokens) return 0;
        const uptimeMin = (sys?.uptime_seconds || 60) / 60;
        return Math.round(tokens / Math.max(uptimeMin, 1));
    }, [tokenStats, sys]);

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>
            <header>
                <h2 className="text-2xl font-bold text-white mb-2">{t.systemOverview}</h2>
                <p className="text-gray-400">{t.systemOverviewDesc}</p>
            </header>

            {/* ============ System status row ============ */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
                <StatCard
                    icon={<Activity size={20} className={status === 'Connected' ? 'text-green-400' : 'text-red-400'} />}
                    label={t.connection}
                    value={status === 'Connected' ? t.statusConnected : t.statusDisconnected}
                    color={status === 'Connected' ? '#4ade80' : '#ef4444'}
                />
                <StatCard
                    icon={<Clock size={20} className="text-purple-400" />}
                    label={t.runtime}
                    value={formatUptime(sys?.uptime_seconds)}
                    sub={sys?.platform}
                />
                <StatCard
                    icon={<Cpu size={20} className="text-blue-400" />}
                    label="CPU"
                    value={sys?.cpu_percent != null ? `${sys.cpu_percent.toFixed(1)}%` : '--'}
                />
                <StatCard
                    icon={<MemoryStick size={20} className="text-yellow-400" />}
                    label={t.memory || 'Memory'}
                    value={sys?.memory_percent != null ? `${sys.memory_percent.toFixed(1)}%` : '--'}
                    sub={sys?.memory_used_gb != null ? `${sys.memory_used_gb.toFixed(1)} / ${(sys.memory_total_gb ?? 0).toFixed(1)} GB` : ''}
                />
                <StatCard
                    icon={<HardDrive size={20} className="text-cyan-400" />}
                    label={t.disk || 'Disk'}
                    value={sys?.disk_percent != null ? `${sys.disk_percent.toFixed(1)}%` : '--'}
                />
            </div>

            {/* ============ Token stats row ============ */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
                <StatCard
                    icon={<Zap size={20} className="text-amber-400" />}
                    label="今日 Token"
                    value={fmtNum(tokenStats?.todayTokens ?? 0)}
                    sub={`输入: ${fmtNum(tokenStats?.todayInput ?? 0)} / 输出: ${fmtNum(tokenStats?.todayOutput ?? 0)}`}
                    color="#f59e0b"
                />
                <StatCard
                    icon={<Database size={20} className="text-blue-400" />}
                    label="累计 Token"
                    value={fmtNum(tokenStats?.totalTokens ?? 0)}
                    sub={`输入: ${fmtNum(tokenStats?.totalInput ?? 0)} / 输出: ${fmtNum(tokenStats?.totalOutput ?? 0)}`}
                    color="#3b82f6"
                />
                <StatCard
                    icon={<Gauge size={20} className="text-emerald-400" />}
                    label="性能指标"
                    value={`${rpm} RPM`}
                    sub={`${fmtNum(tpm)} TPM`}
                    color="#10b981"
                />
                <StatCard
                    icon={<Timer size={20} className="text-rose-400" />}
                    label="平均响应"
                    value={
                        tokenStats?.avgLatency ? `${(tokenStats.avgLatency / 1000).toFixed(2)}s` :
                            (metrics?.provider?.p95_latency_ms ? `${(metrics.provider.p95_latency_ms / 1000).toFixed(2)}s` : '0s')
                    }
                    sub="平均时间"
                    color="#f43f5e"
                />
            </div>

            {/* ============ Time range + granularity controls ============ */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ color: '#889', fontSize: 13, whiteSpace: 'nowrap' }}>时间范围:</span>
                    <select
                        value={timeRange}
                        onChange={e => setTimeRange(e.target.value)}
                        style={{
                            background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.12)',
                            borderRadius: 8, color: '#ddd', padding: '6px 14px', fontSize: 13,
                            outline: 'none', cursor: 'pointer',
                        }}
                    >
                        <option value="1d">今天</option>
                        <option value="7d">近 7 天</option>
                        <option value="30d">近 30 天</option>
                        <option value="all">全部</option>
                    </select>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ color: '#889', fontSize: 13, whiteSpace: 'nowrap' }}>粒度:</span>
                    <select
                        value={granularity}
                        onChange={e => setGranularity(e.target.value)}
                        style={{
                            background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.12)',
                            borderRadius: 8, color: '#ddd', padding: '6px 14px', fontSize: 13,
                            outline: 'none', cursor: 'pointer',
                        }}
                    >
                        <option value="day">按天</option>
                        <option value="hour">按小时</option>
                    </select>
                </div>
            </div>

            {/* ============ Model distribution + Token trend ============ */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, minHeight: 280 }}>
                {/* Donut + table */}
                <div className="glass-panel p-6">
                    <h3 className="font-bold text-lg text-white" style={{ marginBottom: 16 }}>模型分布</h3>
                    {modelTable.length > 0 ? (
                        <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start' }}>
                            <DonutChart slices={donutSlices} size={160} />
                            <div style={{ flex: 1, overflow: 'auto', maxHeight: 220 }}>
                                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                                    <thead>
                                        <tr style={{ color: '#889', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                                            <th style={{ textAlign: 'left', padding: '6px 8px', fontWeight: 500 }}>模型</th>
                                            <th style={{ textAlign: 'right', padding: '6px 8px', fontWeight: 500 }}>请求</th>
                                            <th style={{ textAlign: 'right', padding: '6px 8px', fontWeight: 500 }}>Token</th>
                                            <th style={{ textAlign: 'right', padding: '6px 8px', fontWeight: 500 }}>实际</th>
                                            <th style={{ textAlign: 'right', padding: '6px 8px', fontWeight: 500 }}>标准</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {modelTable.map((row, i) => (
                                            <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                                                <td style={{ padding: '6px 8px', color: '#ddd', display: 'flex', alignItems: 'center', gap: 6 }}>
                                                    <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: DONUT_COLORS[i % DONUT_COLORS.length], flexShrink: 0 }} />
                                                    <span style={{ maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.name}</span>
                                                </td>
                                                <td style={{ textAlign: 'right', padding: '6px 8px', color: '#aab' }}>{fmtNum(row.requests)}</td>
                                                <td style={{ textAlign: 'right', padding: '6px 8px', color: '#aab' }}>{fmtNum(row.tokens)}</td>
                                                <td style={{ textAlign: 'right', padding: '6px 8px', color: '#10b981' }}>{row.cost ? fmtCost(row.cost) : '$0'}</td>
                                                <td style={{ textAlign: 'right', padding: '6px 8px', color: '#10b981' }}>{row.cost ? fmtCost(row.cost) : '$0'}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    ) : (
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 160, color: '#667' }}>
                            暂无模型使用数据
                        </div>
                    )}
                </div>

                {/* Token trend chart */}
                <div className="glass-panel p-6">
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                        <h3 className="font-bold text-lg text-white">Token 使用趋势</h3>
                        <div style={{ display: 'flex', gap: 16, fontSize: 12 }}>
                            {[['input', '● Input', TREND_COLORS.input], ['output', '● Output', TREND_COLORS.output], ['cache', '● Cache', TREND_COLORS.cache]].map(([key, label, color]) => (
                                <span key={key} style={{ color, display: 'flex', alignItems: 'center', gap: 4 }}>
                                    {label}
                                </span>
                            ))}
                        </div>
                    </div>
                    <TrendChart data={trendData} width={440} height={200} />
                </div>
            </div>

            {/* ============ Process list ============ */}
            {sys?.processes && (
                <div className="glass-panel p-6">
                    <h3 className="font-bold text-lg text-white" style={{ marginBottom: 12 }}>{t.processes || 'Processes'}</h3>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                        {sys.processes.map((p, i) => (
                            <div key={i} style={{
                                display: 'flex', alignItems: 'center', gap: 12,
                                padding: '8px 12px', background: 'rgba(0,0,0,0.15)', borderRadius: 8,
                                fontSize: 13,
                            }}>
                                <span style={{ color: '#4ade80', fontSize: 8 }}>●</span>
                                <span style={{ color: '#ddd', fontWeight: 500, flex: 1 }}>{p.name}</span>
                                <span style={{ color: '#667' }}>PID {p.pid}</span>
                                <span style={{ color: '#88a' }}>{p.memory_mb} MB</span>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
};

export default Dashboard;
