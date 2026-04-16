import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import API_BASE from '../config';
import NoticeBanner from '../components/NoticeBanner';
import useNotice from '../hooks/useNotice';
import useUrlState from '../hooks/useUrlState';
import ToggleSwitch from '../components/ToggleSwitch';

const StatCard = ({ label, value }) => (
    <div className="card" style={{ padding: 12 }}>
        <div style={{ color: '#7f8ea3', fontSize: 12 }}>{label}</div>
        <div style={{ color: '#e2e8f0', fontSize: 20, fontWeight: 700 }}>{value}</div>
    </div>
);

const rateColor = (value) => {
    const n = Number(value ?? 0);
    if (n >= 0.95) return '#34d399';
    if (n >= 0.8) return '#fbbf24';
    return '#f87171';
};

const _SORT_DEFAULTS = {
    tf: { key: 'failures', dir: 'desc' },
    te: { key: 'count', dir: 'desc' },
};

const Observability = ({ t }) => {
    const [payload, setPayload] = useState(null);
    const [trends, setTrends] = useState(null);
    const [efficiencyBaseline, setEfficiencyBaseline] = useState(null);
    const [memoryQuality, setMemoryQuality] = useState(null);
    const [memoryQualityLoading, setMemoryQualityLoading] = useState(false);
    const [memoryQualityExporting, setMemoryQualityExporting] = useState('');
    const [memoryWindowDays, setMemoryWindowDays] = useState(7);
    const [memoryStaleDays, setMemoryStaleDays] = useState(14);
    const [memoryIncludePersonaDrift, setMemoryIncludePersonaDrift] = useState(true);
    const [memoryTurnHealth, setMemoryTurnHealth] = useState(null);
    const [memoryTurnHealthLoading, setMemoryTurnHealthLoading] = useState(false);
    const [alerts, setAlerts] = useState([]);
    const [trajectoryItems, setTrajectoryItems] = useState([]);
    const [selectedRunId, setSelectedRunId] = useState('');
    const [trajectoryDetail, setTrajectoryDetail] = useState(null);
    const [trajectoryLoading, setTrajectoryLoading] = useState(false);
    const [retryingRunId, setRetryingRunId] = useState('');
    const [limit, setLimit] = useUrlState('limit', 200, {
        parse: (raw) => {
            const n = Number(raw);
            if (Number.isNaN(n)) return 200;
            return Math.max(10, Math.min(1000, n));
        },
        serialize: (value) => String(value),
    });
    const [toolFailureSort, setToolFailureSort] = useUrlState('tfs', _SORT_DEFAULTS.tf, {
        parse: (raw) => {
            const [keyRaw = '', dirRaw = ''] = String(raw || '').split('.');
            const key = keyRaw === 'tool' ? 'tool' : 'failures';
            const dir = dirRaw === 'asc' ? 'asc' : 'desc';
            return { key, dir };
        },
        serialize: (value) => `${value?.key || _SORT_DEFAULTS.tf.key}.${value?.dir || _SORT_DEFAULTS.tf.dir}`,
    });
    const [toolErrorSort, setToolErrorSort] = useUrlState('tes', _SORT_DEFAULTS.te, {
        parse: (raw) => {
            const [keyRaw = '', dirRaw = ''] = String(raw || '').split('.');
            const key = keyRaw === 'code' ? 'code' : 'count';
            const dir = dirRaw === 'asc' ? 'asc' : 'desc';
            return { key, dir };
        },
        serialize: (value) => `${value?.key || _SORT_DEFAULTS.te.key}.${value?.dir || _SORT_DEFAULTS.te.dir}`,
    });
    const { notice, showNotice } = useNotice();

    const load = async () => {
        try {
            const [metricsRes, trendsRes, baselineRes, alertsRes, trajectoriesRes] = await Promise.all([
                axios.get(`${API_BASE}/observability/metrics`, { params: { limit } }),
                axios.get(`${API_BASE}/observability/trends`, { params: { window: 60 } }),
                axios.get(`${API_BASE}/observability/efficiency-baseline`, { params: { window_days: 7, limit } }),
                axios.get(`${API_BASE}/observability/alerts`, { params: { limit: 20 } }),
                axios.get(`${API_BASE}/debug/trajectories`, { params: { limit: 30 } }),
            ]);
            setPayload(metricsRes.data || null);
            setTrends(trendsRes.data?.trends || null);
            setEfficiencyBaseline(baselineRes.data?.report || null);
            setAlerts(alertsRes.data?.items || []);
            const items = Array.isArray(trajectoriesRes.data?.items) ? trajectoriesRes.data.items : [];
            setTrajectoryItems(items);
            if (items.length > 0) {
                setSelectedRunId((prev) => {
                    if (prev && items.some((item) => item?.run_id === prev)) return prev;
                    return String(items[0]?.run_id || '');
                });
            } else {
                setSelectedRunId('');
                setTrajectoryDetail(null);
            }
        } catch {
            setPayload(null);
            setTrends(null);
            setEfficiencyBaseline(null);
            setAlerts([]);
            setTrajectoryItems([]);
            setSelectedRunId('');
            setTrajectoryDetail(null);
            showNotice(t.noticeLoadObservabilityFailed || 'Failed to load observability metrics', 'error');
        }
    };

    const loadMemoryQuality = async () => {
        setMemoryQualityLoading(true);
        try {
            const res = await axios.get(`${API_BASE}/memory/quality-report`, {
                params: {
                    window_days: memoryWindowDays,
                    stale_days: memoryStaleDays,
                    include_persona_drift: memoryIncludePersonaDrift,
                    include_samples: true,
                    sample_limit: 6,
                },
            });
            setMemoryQuality(res.data || null);
        } catch {
            setMemoryQuality(null);
            showNotice(t.noticeLoadMemoryQualityFailed || 'Failed to load memory quality report', 'error');
        } finally {
            setMemoryQualityLoading(false);
        }
    };

    const exportMemoryQuality = async (format) => {
        setMemoryQualityExporting(format);
        try {
            const res = await axios.post(`${API_BASE}/memory/quality-report/export`, {
                format,
                window_days: memoryWindowDays,
                stale_days: memoryStaleDays,
                include_persona_drift: memoryIncludePersonaDrift,
                include_samples: true,
                sample_limit: 10,
            });
            const path = String(res?.data?.path || '').trim();
            if (path) {
                showNotice(`Memory quality report exported: ${path}`, 'success');
            } else {
                showNotice(t.noticeExported || 'Export completed', 'success');
            }
        } catch (err) {
            const detail = err?.response?.data?.detail;
            showNotice(
                detail ? `Memory quality export failed: ${detail}` : (t.noticeExportFailed || 'Export failed'),
                'error',
            );
        } finally {
            setMemoryQualityExporting('');
        }
    };

    const loadMemoryTurnHealth = async () => {
        setMemoryTurnHealthLoading(true);
        try {
            const res = await axios.get(`${API_BASE}/memory/turn-health`, { params: { limit: 80 } });
            setMemoryTurnHealth(res.data || null);
        } catch {
            setMemoryTurnHealth(null);
            showNotice('Failed to load memory turn health', 'error');
        } finally {
            setMemoryTurnHealthLoading(false);
        }
    };

    useEffect(() => {
        load();
    }, [limit]);

    useEffect(() => {
        loadMemoryQuality();
    }, [memoryWindowDays, memoryStaleDays, memoryIncludePersonaDrift]);

    useEffect(() => {
        loadMemoryTurnHealth();
    }, []);

    useEffect(() => {
        if (!selectedRunId) {
            setTrajectoryDetail(null);
            return;
        }
        let cancelled = false;
        const fetchDetail = async () => {
            setTrajectoryLoading(true);
            try {
                const res = await axios.get(`${API_BASE}/debug/trajectories/${selectedRunId}`);
                if (!cancelled) setTrajectoryDetail(res.data || null);
            } catch {
                if (!cancelled) setTrajectoryDetail(null);
            } finally {
                if (!cancelled) setTrajectoryLoading(false);
            }
        };
        fetchDetail();
        return () => {
            cancelled = true;
        };
    }, [selectedRunId]);

    const retryTrajectory = async (runId) => {
        if (!runId) return;
        setRetryingRunId(runId);
        try {
            const sessionId = 'web-main';
            await axios.post(`${API_BASE}/debug/trajectories/${runId}/resume/auto`, {
                session_id: sessionId,
            });
            showNotice(t.debugTrajReplayEnqueued || 'Retry request sent', 'success');
        } catch (err) {
            const detail = err?.response?.data?.detail;
            showNotice(
                detail ? `Retry failed: ${detail}` : (t.debugTrajResumeSendFailed || 'Retry failed'),
                'error',
            );
        } finally {
            setRetryingRunId('');
        }
    };

    const provider = payload?.provider || {};
    const models = payload?.model || [];
    const agents = payload?.agent || [];
    const workflow = payload?.workflow || {};
    const persona = payload?.persona || {};
    const llmTool = payload?.llm_tool || {};
    const inboundMedia = payload?.inbound_media || {};
    const baselineCurrent = efficiencyBaseline?.current_window || {};
    const baselineDelta = efficiencyBaseline?.delta || {};
    const memoryCurrent = memoryQuality?.current_window || {};
    const memoryScores = memoryCurrent?.scores || {};
    const memoryMetrics = memoryCurrent?.metrics || {};
    const memoryRelevance = memoryMetrics?.relevance || {};
    const memoryTimeliness = memoryMetrics?.timeliness || {};
    const memoryConflict = memoryMetrics?.conflict || {};
    const memoryTrend = memoryQuality?.trend || {};
    const memoryIssues = Array.isArray(memoryCurrent?.top_issues) ? memoryCurrent.top_issues : [];
    const memorySamples = memoryCurrent?.samples || {};
    const staleSamples = Array.isArray(memorySamples?.stale_events) ? memorySamples.stale_events : [];
    const conflictingSamples = Array.isArray(memorySamples?.conflicting_keys) ? memorySamples.conflicting_keys : [];
    const memoryPersonaDrift = memoryQuality?.persona_drift || {};
    const memoryTurnSummary = memoryTurnHealth?.summary || {};
    const memoryTurnItems = Array.isArray(memoryTurnHealth?.items) ? memoryTurnHealth.items : [];
    const toolPersist = memoryTurnHealth?.tool_persistence || {};
    const toolPersistPolicy = toolPersist?.policy || {};
    const toolPersistCounts = toolPersist?.decision_counts || {};
    const llmProfile = llmTool?.llm || {};
    const toolProfile = llmTool?.tool || {};
    const baseToolFailureRows = [...(toolProfile.by_tool_failures || [])];
    const baseToolErrorRows = Object.entries(toolProfile.error_codes || {})
        .map(([code, count]) => ({ code, count: Number(count || 0) }))
        .sort((a, b) => b.count - a.count);
    const toolFailureRows = [...baseToolFailureRows].sort((a, b) => {
        const factor = toolFailureSort.dir === 'asc' ? 1 : -1;
        if (toolFailureSort.key === 'tool') {
            return String(a?.tool || '').localeCompare(String(b?.tool || '')) * factor;
        }
        return (Number(a?.failures || 0) - Number(b?.failures || 0)) * factor;
    });
    const toolErrorRows = [...baseToolErrorRows].sort((a, b) => {
        const factor = toolErrorSort.dir === 'asc' ? 1 : -1;
        if (toolErrorSort.key === 'code') {
            return String(a?.code || '').localeCompare(String(b?.code || '')) * factor;
        }
        return (Number(a?.count || 0) - Number(b?.count || 0)) * factor;
    });
    const trajectoryEvents = Array.isArray(trajectoryDetail?.events) ? trajectoryDetail.events : [];
    const trajectoryToolResults = useMemo(
        () => trajectoryEvents
            .filter((evt) => String(evt?.action || '') === 'tool_result')
            .map((evt, idx) => {
                const payload = evt?.payload || {};
                return {
                    key: `${payload?.tool_call_id || idx}_${idx}`,
                    tool: String(payload?.tool || ''),
                    status: String(payload?.status || ''),
                    errorCode: String(payload?.error_code || ''),
                    traceId: String(payload?.trace_id || ''),
                    errorHint: String(payload?.error_hint || ''),
                    resultPreview: String(payload?.result_preview || ''),
                };
            }),
        [trajectoryEvents]
    );
    const trajectoryToolErrors = trajectoryToolResults.filter((row) => row.status === 'error');

    const toggleSort = (setState, current, key) => {
        if (current.key === key) {
            setState({ key, dir: current.dir === 'asc' ? 'desc' : 'asc' });
            return;
        }
        setState({ key, dir: 'desc' });
    };

    const sortMark = (current, key) => (current.key === key ? (current.dir === 'asc' ? '↑' : '↓') : '');

    return (
        <div style={{ maxWidth: 1200 }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, color: '#fff', marginBottom: 6 }}>
                {t.observability || 'Observability'}
            </h2>
            <p style={{ color: '#6b7280', marginTop: 0, marginBottom: 16 }}>
                {t.observabilityDesc || 'Unified model/provider/agent metrics: success rate, P95, errors, budget usage.'}
            </p>

            <NoticeBanner notice={notice} />

            <div style={{ marginBottom: 12, display: 'flex', gap: 10, alignItems: 'center' }}>
                <input
                    className="input"
                    type="number"
                    min={10}
                    value={limit}
                    onChange={(e) => setLimit(Number(e.target.value || 200))}
                    style={{ width: 120 }}
                />
                <button className="btn-secondary" onClick={load}>{t.refresh || 'Refresh'}</button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(140px, 1fr))', gap: 10, marginBottom: 12 }}>
                <StatCard label="provider.calls" value={provider.total_calls ?? 0} />
                <StatCard label="provider.success_rate" value={provider.success_rate ?? 0} />
                <StatCard label="provider.p95_ms" value={provider.p95_latency_ms ?? 0} />
                <StatCard label="budget.used_calls" value={provider?.budget?.used_calls ?? 0} />
            </div>

            <div className="card" style={{ padding: 16, marginBottom: 12 }}>
                <div style={{ color: '#e2e8f0', marginBottom: 8 }}>
                    {t.efficiencyBaseline || 'Efficiency Baseline'}
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(140px, 1fr))', gap: 10 }}>
                    <StatCard label="baseline.success_rate" value={baselineCurrent.success_rate ?? 1} />
                    <StatCard label="baseline.p95_ms" value={baselineCurrent.p95_latency_ms ?? 0} />
                    <StatCard label="baseline.avg_tokens_run" value={baselineCurrent.avg_tokens_per_run ?? 0} />
                    <StatCard label="baseline.tool_error_rate" value={baselineCurrent.tool_error_rate ?? 0} />
                </div>
                <div style={{ marginTop: 8, color: '#6b7280', fontSize: 12 }}>
                    Δ success_rate={baselineDelta.success_rate ?? 0} · Δ p95={baselineDelta.p95_latency_ms ?? 0}
                    · Δ avg_tokens={baselineDelta.avg_tokens_per_run ?? 0}
                    · Δ tool_error_rate={baselineDelta.tool_error_rate ?? 0}
                </div>
            </div>

            <div className="card" style={{ padding: 16, marginBottom: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
                    <div style={{ color: '#e2e8f0' }}>
                        {t.memoryQualityReport || 'Memory Quality Report'}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <input
                            className="input"
                            type="number"
                            min={1}
                            max={30}
                            value={memoryWindowDays}
                            onChange={(e) => setMemoryWindowDays(Math.max(1, Math.min(30, Number(e.target.value || 7))))}
                            style={{ width: 80 }}
                            title="window_days"
                        />
                        <input
                            className="input"
                            type="number"
                            min={1}
                            max={365}
                            value={memoryStaleDays}
                            onChange={(e) => setMemoryStaleDays(Math.max(1, Math.min(365, Number(e.target.value || 14))))}
                            style={{ width: 90 }}
                            title="stale_days"
                        />
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, minWidth: 130, color: '#6b7280', fontSize: 12 }}>
                            <span>persona drift</span>
                            <ToggleSwitch
                                checked={memoryIncludePersonaDrift}
                                onChange={(v) => setMemoryIncludePersonaDrift(Boolean(v))}
                            />
                        </div>
                        <button className="btn-secondary" onClick={loadMemoryQuality} disabled={memoryQualityLoading}>
                            {memoryQualityLoading ? (t.loading || 'Loading...') : (t.refresh || 'Refresh')}
                        </button>
                        <button
                            className="btn-secondary"
                            onClick={() => exportMemoryQuality('markdown')}
                            disabled={memoryQualityExporting === 'markdown'}
                        >
                            {memoryQualityExporting === 'markdown' ? 'Exporting...' : 'Export MD'}
                        </button>
                        <button
                            className="btn-secondary"
                            onClick={() => exportMemoryQuality('json')}
                            disabled={memoryQualityExporting === 'json'}
                        >
                            {memoryQualityExporting === 'json' ? 'Exporting...' : 'Export JSON'}
                        </button>
                    </div>
                </div>
                {!memoryQuality ? (
                    <div style={{ color: '#889', marginTop: 10, fontSize: 12 }}>
                        {t.noData || 'No data'}
                    </div>
                ) : (
                    <>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(140px, 1fr))', gap: 10, marginTop: 10 }}>
                            <StatCard label="memory.quality_score" value={memoryScores.quality_score ?? 0} />
                            <StatCard label="memory.relevance_score" value={memoryScores.relevance_score ?? 0} />
                            <StatCard label="memory.timeliness_score" value={memoryScores.timeliness_score ?? 0} />
                            <StatCard label="memory.stability_score" value={memoryScores.stability_score ?? 0} />
                        </div>
                        <div style={{ marginTop: 8, color: '#6b7280', fontSize: 12 }}>
                            quality_level={memoryScores.quality_level || 'unknown'} · trend={memoryTrend.direction || 'stable'}
                            · Δ quality_score={memoryTrend.quality_score_delta ?? 0}
                            {memoryPersonaDrift?.joint_risk_level ? ` · joint_risk=${memoryPersonaDrift.joint_risk_level}` : ''}
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(160px, 1fr))', gap: 10, marginTop: 10 }}>
                            <div style={{ color: '#6b7280', fontSize: 12 }}>
                                relevance: yield={memoryRelevance.yield_rate ?? 0}, binding={memoryRelevance.source_binding_rate ?? 0}, align={memoryRelevance.alignment_avg ?? 0}
                            </div>
                            <div style={{ color: '#6b7280', fontSize: 12 }}>
                                timeliness: stale_ratio={memoryTimeliness.stale_ratio ?? 0}, median_age={memoryTimeliness.median_age_days ?? 0}
                            </div>
                            <div style={{ color: '#6b7280', fontSize: 12 }}>
                                conflict: conflict_rate={memoryConflict.conflict_rate ?? 0}, duplicate={memoryConflict.duplicate_key_ratio ?? 0}
                            </div>
                        </div>
                        <div style={{ marginTop: 10, color: '#e2e8f0', fontSize: 12 }}>Top Issues</div>
                        {memoryIssues.length === 0 ? (
                            <div style={{ color: '#889', fontSize: 12, marginTop: 4 }}>{t.noData || 'No data'}</div>
                        ) : memoryIssues.map((issue, idx) => (
                            <div
                                key={`${issue?.code || 'issue'}_${idx}`}
                                style={{ color: '#6b7280', fontSize: 12, padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}
                            >
                                [{issue?.severity || 'info'}] {issue?.code || 'unknown'}: {issue?.detail || ''}
                            </div>
                        ))}
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 10 }}>
                            <div>
                                <div style={{ color: '#6b7280', fontSize: 12, marginBottom: 4 }}>stale samples</div>
                                {staleSamples.length === 0 ? (
                                    <div style={{ color: '#889', fontSize: 12 }}>{t.noData || 'No data'}</div>
                                ) : staleSamples.slice(0, 3).map((item, idx) => (
                                    <div key={`stale_${idx}`} style={{ color: '#6b7280', fontSize: 12, padding: '3px 0' }}>
                                        {item?.timestamp || '-'} · age_days={item?.age_days ?? 0}
                                    </div>
                                ))}
                            </div>
                            <div>
                                <div style={{ color: '#6b7280', fontSize: 12, marginBottom: 4 }}>conflicting keys</div>
                                {conflictingSamples.length === 0 ? (
                                    <div style={{ color: '#889', fontSize: 12 }}>{t.noData || 'No data'}</div>
                                ) : conflictingSamples.slice(0, 3).map((item, idx) => (
                                    <div key={`conflict_${idx}`} style={{ color: '#6b7280', fontSize: 12, padding: '3px 0' }}>
                                        {item?.key || '-'} · decisions={Array.isArray(item?.decisions) ? item.decisions.join(',') : '-'}
                                    </div>
                                ))}
                            </div>
                        </div>
                    </>
                )}
            </div>

            <div className="card" style={{ padding: 16, marginBottom: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
                    <div style={{ color: '#e2e8f0' }}>
                        Memory Turn Health
                    </div>
                    <button className="btn-secondary" onClick={loadMemoryTurnHealth} disabled={memoryTurnHealthLoading}>
                        {memoryTurnHealthLoading ? (t.loading || 'Loading...') : (t.refresh || 'Refresh')}
                    </button>
                </div>
                {!memoryTurnHealth ? (
                    <div style={{ color: '#889', marginTop: 10, fontSize: 12 }}>
                        {t.noData || 'No data'}
                    </div>
                ) : (
                    <>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(140px, 1fr))', gap: 10, marginTop: 10 }}>
                            <StatCard label="turn.count" value={memoryTurnSummary.turn_count ?? 0} />
                            <StatCard label="memory_context_chars.avg" value={memoryTurnSummary.avg_memory_context_chars ?? 0} />
                            <StatCard label="recall_count.avg" value={memoryTurnSummary.avg_recall_count ?? 0} />
                            <StatCard label="persist_ok_rate" value={memoryTurnSummary.persist_ok_rate ?? 0} />
                        </div>
                        <div style={{ marginTop: 10, color: '#6b7280', fontSize: 12 }}>
                            tool_result_persistence: mode={toolPersistPolicy.mode || 'allowlist'} · enabled={String(toolPersistPolicy.enabled ?? true)}
                            · memory={toolPersistCounts.memory ?? 0} · trajectory_only={toolPersistCounts.trajectory_only ?? 0}
                        </div>
                        <div style={{ marginTop: 10, overflowX: 'auto' }}>
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                                <thead>
                                    <tr style={{ color: '#6b7280', textAlign: 'left' }}>
                                        <th style={{ padding: '6px 8px' }}>ts</th>
                                        <th style={{ padding: '6px 8px' }}>status</th>
                                        <th style={{ padding: '6px 8px' }}>memory_context_chars</th>
                                        <th style={{ padding: '6px 8px' }}>recall_count</th>
                                        <th style={{ padding: '6px 8px' }}>persist_ok</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {memoryTurnItems.slice(-20).reverse().map((row, idx) => (
                                        <tr key={`${row?.ts || 'row'}_${idx}`} style={{ borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                                            <td style={{ padding: '6px 8px', color: '#6b7280' }}>
                                                {row?.ts ? new Date(row.ts * 1000).toLocaleString() : '-'}
                                            </td>
                                            <td style={{ padding: '6px 8px', color: '#e2e8f0' }}>{row?.status || '-'}</td>
                                            <td style={{ padding: '6px 8px', color: '#e2e8f0' }}>{row?.memory_context_chars ?? 0}</td>
                                            <td style={{ padding: '6px 8px', color: '#e2e8f0' }}>{row?.recall_count ?? 0}</td>
                                            <td style={{ padding: '6px 8px', color: '#e2e8f0' }}>{String(row?.persist_ok)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </>
                )}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(140px, 1fr))', gap: 10, marginBottom: 12 }}>
                <StatCard label="workflow.runs" value={workflow.total_runs ?? 0} />
                <StatCard label="workflow.success_rate" value={workflow.success_rate ?? 0} />
                <StatCard label="workflow.p95_ms" value={workflow.p95_latency_ms ?? 0} />
                <StatCard label="workflow.p95_nodes" value={workflow.p95_trace_nodes ?? 0} />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(140px, 1fr))', gap: 10, marginBottom: 12 }}>
                <StatCard label="llm.calls" value={llmProfile.calls ?? 0} />
                <StatCard label="llm.failures" value={llmProfile.failures ?? 0} />
                <StatCard label="tool.calls" value={toolProfile.calls ?? 0} />
                <StatCard label="tool.failures" value={toolProfile.failures ?? 0} />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(140px, 1fr))', gap: 10, marginBottom: 12 }}>
                <div className="card" style={{ padding: 12 }}>
                    <div style={{ color: '#7f8ea3', fontSize: 12 }}>llm.success_rate</div>
                    <div style={{ color: rateColor(llmProfile.success_rate), fontSize: 20, fontWeight: 700 }}>
                        {llmProfile.success_rate ?? 1}
                    </div>
                </div>
                <div className="card" style={{ padding: 12 }}>
                    <div style={{ color: '#7f8ea3', fontSize: 12 }}>tool.success_rate</div>
                    <div style={{ color: rateColor(toolProfile.success_rate), fontSize: 20, fontWeight: 700 }}>
                        {toolProfile.success_rate ?? 1}
                    </div>
                </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(140px, 1fr))', gap: 10, marginBottom: 12 }}>
                <StatCard label="persona.latest_score" value={persona.latest_score ?? '-'} />
                <StatCard label="persona.dataset_id" value={persona.dataset_id || '-'} />
                <StatCard label="alerts.count" value={alerts.length} />
                <StatCard label="budget.degrade" value={String(provider.budget_degrade_active ?? false)} />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(140px, 1fr))', gap: 10, marginBottom: 12 }}>
                <StatCard label="inbound.events" value={inboundMedia.events ?? 0} />
                <StatCard label="inbound.media_entries" value={inboundMedia.media_entries ?? 0} />
                <StatCard label="inbound.success_rate" value={inboundMedia.success_rate ?? 1} />
                <StatCard label="inbound.failed_entries" value={inboundMedia.failed_entries ?? 0} />
            </div>

            <div className="card" style={{ padding: 16, marginBottom: 12 }}>
                <div style={{ color: '#e2e8f0', marginBottom: 10 }}>Providers</div>
                {(provider.providers || []).map((item) => (
                    <div key={item.name} style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 1fr 1fr 1fr 1fr', gap: 8, fontSize: 12, padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                        <span style={{ color: '#e2e8f0' }}>{item.name}</span>
                        <span style={{ color: '#6b7280' }}>{item.model}</span>
                        <span style={{ color: '#6b7280' }}>ok={item.success_rate}</span>
                        <span style={{ color: '#6b7280' }}>p95={item.p95_latency_ms}</span>
                        <span style={{ color: '#6b7280' }}>rpm={item.capacity_rpm}</span>
                        <span style={{ color: '#6b7280' }}>{JSON.stringify(item.error_classes || {})}</span>
                    </div>
                ))}
            </div>

            <div className="card" style={{ padding: 16, marginBottom: 12 }}>
                <div style={{ color: '#e2e8f0', marginBottom: 10 }}>Models</div>
                {models.map((item) => (
                    <div key={item.model} style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 1fr 1fr 1.2fr', gap: 8, fontSize: 12, padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                        <span style={{ color: '#e2e8f0' }}>{item.model}</span>
                        <span style={{ color: '#6b7280' }}>calls={item.calls}</span>
                        <span style={{ color: '#6b7280' }}>ok={item.success_rate}</span>
                        <span style={{ color: '#6b7280' }}>p95={item.p95_latency_ms}</span>
                        <span style={{ color: '#6b7280' }}>{JSON.stringify(item.error_classes || {})}</span>
                    </div>
                ))}
            </div>

            <div className="card" style={{ padding: 16 }}>
                <div style={{ color: '#e2e8f0', marginBottom: 10 }}>Agents</div>
                {agents.map((item) => (
                    <div key={item.agent_id} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr 1.2fr', gap: 8, fontSize: 12, padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                        <span style={{ color: '#e2e8f0' }}>{item.agent_id}</span>
                        <span style={{ color: '#6b7280' }}>turns={item.turns}</span>
                        <span style={{ color: '#6b7280' }}>ok={item.success_rate}</span>
                        <span style={{ color: '#6b7280' }}>p95={item.p95_latency_ms}</span>
                        <span style={{ color: '#6b7280' }}>{JSON.stringify(item.error_classes || {})}</span>
                    </div>
                ))}
            </div>

            <div className="card" style={{ padding: 16, marginTop: 12 }}>
                <div style={{ color: '#e2e8f0', marginBottom: 10 }}>Workflows</div>
                {(workflow.workflows || []).map((item) => (
                    <div key={item.workflow_id} style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 1fr 1fr 1fr 1.2fr', gap: 8, fontSize: 12, padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                        <span style={{ color: '#e2e8f0' }}>{item.workflow_name || item.workflow_id}</span>
                        <span style={{ color: '#6b7280' }}>runs={item.runs}</span>
                        <span style={{ color: '#6b7280' }}>ok={item.success_rate}</span>
                        <span style={{ color: '#6b7280' }}>p95={item.p95_latency_ms}</span>
                        <span style={{ color: '#6b7280' }}>nodes={item.p95_trace_nodes}</span>
                        <span style={{ color: '#6b7280' }}>{JSON.stringify(item.error_classes || {})}</span>
                    </div>
                ))}
            </div>

            <div className="card" style={{ padding: 16, marginTop: 12 }}>
                <div style={{ color: '#e2e8f0', marginBottom: 10 }}>
                    {t.llmToolFailureProfile || 'LLM / Tool Failure Profile'}
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div>
                        <div style={{ color: '#6b7280', marginBottom: 6, fontSize: 12 }}>
                            {t.llmErrorClasses || 'LLM Error Classes'}
                        </div>
                        <pre style={{ margin: 0, color: '#6b7280', fontSize: 12, whiteSpace: 'pre-wrap' }}>
                            {JSON.stringify(llmProfile.error_classes || {}, null, 2)}
                        </pre>
                    </div>
                    <div>
                        <div style={{ color: '#6b7280', marginBottom: 6, fontSize: 12 }}>
                            {t.toolErrorCodes || 'Tool Error Codes'}
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, fontSize: 11, color: '#6b7280', marginBottom: 4 }}>
                            <button
                                type="button"
                                className="btn-secondary"
                                style={{ padding: '2px 6px', textAlign: 'left' }}
                                onClick={() => toggleSort(setToolErrorSort, toolErrorSort, 'code')}
                            >
                                code {sortMark(toolErrorSort, 'code')}
                            </button>
                            <button
                                type="button"
                                className="btn-secondary"
                                style={{ padding: '2px 6px' }}
                                onClick={() => toggleSort(setToolErrorSort, toolErrorSort, 'count')}
                            >
                                count {sortMark(toolErrorSort, 'count')}
                            </button>
                        </div>
                        {toolErrorRows.length === 0 ? (
                            <div style={{ color: '#889', fontSize: 12 }}>{t.noData || 'No data'}</div>
                        ) : (
                            toolErrorRows.map((row) => (
                                <div key={row.code} style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, fontSize: 12, padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                                    <span style={{ color: '#e2e8f0' }}>{row.code}</span>
                                    <span style={{ color: '#6b7280' }}>{row.count}</span>
                                </div>
                            ))
                        )}
                    </div>
                </div>
                <div style={{ marginTop: 12 }}>
                    <div style={{ color: '#6b7280', marginBottom: 6, fontSize: 12 }}>
                        {t.topToolFailures || 'Top Tool Failures'}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: 8, fontSize: 11, color: '#6b7280', marginBottom: 4 }}>
                        <span />
                        <button
                            type="button"
                            className="btn-secondary"
                            style={{ padding: '2px 6px', textAlign: 'left' }}
                            onClick={() => toggleSort(setToolFailureSort, toolFailureSort, 'tool')}
                        >
                            tool {sortMark(toolFailureSort, 'tool')}
                        </button>
                        <button
                            type="button"
                            className="btn-secondary"
                            style={{ padding: '2px 6px' }}
                            onClick={() => toggleSort(setToolFailureSort, toolFailureSort, 'failures')}
                        >
                            failures {sortMark(toolFailureSort, 'failures')}
                        </button>
                    </div>
                    {toolFailureRows.length === 0 ? (
                        <div style={{ color: '#889', fontSize: 12 }}>{t.noData || 'No data'}</div>
                    ) : (
                        toolFailureRows.map((item, idx) => (
                            <div key={`${item.tool}_${idx}`} style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: 8, fontSize: 12, padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                                <span style={{ color: '#6b7280' }}>#{idx + 1}</span>
                                <span style={{ color: '#e2e8f0' }}>{item.tool}</span>
                                <span style={{ color: '#f87171' }}>{item.failures}</span>
                            </div>
                        ))
                    )}
                    <div style={{ color: '#6b7280', marginTop: 8, fontSize: 12 }}>
                        replan_hints={llmTool.replan_hints ?? 0}
                    </div>
                </div>
            </div>

            <div className="card" style={{ padding: 16, marginTop: 12 }}>
                <div style={{ color: '#e2e8f0', marginBottom: 10 }}>
                    {t.inboundMediaProfile || 'Inbound Media Profile'}
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div>
                        <div style={{ color: '#6b7280', marginBottom: 6, fontSize: 12 }}>
                            {t.inboundMediaBySource || 'By Source'}
                        </div>
                        <pre style={{ margin: 0, color: '#6b7280', fontSize: 12, whiteSpace: 'pre-wrap' }}>
                            {JSON.stringify(inboundMedia.by_source || {}, null, 2)}
                        </pre>
                    </div>
                    <div>
                        <div style={{ color: '#6b7280', marginBottom: 6, fontSize: 12 }}>
                            {t.inboundMediaByType || 'By Type'}
                        </div>
                        <pre style={{ margin: 0, color: '#6b7280', fontSize: 12, whiteSpace: 'pre-wrap' }}>
                            {JSON.stringify(inboundMedia.by_type || {}, null, 2)}
                        </pre>
                    </div>
                </div>
            </div>

            <div className="card" style={{ padding: 16, marginTop: 12 }}>
                <div style={{ color: '#e2e8f0', marginBottom: 10 }}>
                    {t.debugTrajectory || 'Trajectories'}
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '0.9fr 1.1fr', gap: 12 }}>
                    <div style={{ maxHeight: 320, overflowY: 'auto' }}>
                        {trajectoryItems.length === 0 ? (
                            <div style={{ color: '#889', fontSize: 12 }}>{t.noData || 'No data'}</div>
                        ) : trajectoryItems.map((item) => {
                            const runId = String(item?.run_id || '');
                            const active = runId === selectedRunId;
                            return (
                                <button
                                    key={runId}
                                    type="button"
                                    className="btn-secondary"
                                    onClick={() => setSelectedRunId(runId)}
                                    style={{
                                        width: '100%',
                                        marginBottom: 6,
                                        padding: '8px 10px',
                                        textAlign: 'left',
                                        borderColor: active ? 'rgba(96,165,250,0.8)' : undefined,
                                    }}
                                >
                                    <div style={{ color: '#e2e8f0', fontSize: 12, fontWeight: 600 }}>{runId}</div>
                                    <div style={{ color: '#6b7280', fontSize: 11 }}>
                                        status={item?.status || 'running'} · events={item?.event_count ?? 0}
                                    </div>
                                </button>
                            );
                        })}
                    </div>
                    <div>
                        {trajectoryLoading ? (
                            <div style={{ color: '#6b7280', fontSize: 12 }}>Loading trajectory...</div>
                        ) : !trajectoryDetail ? (
                            <div style={{ color: '#889', fontSize: 12 }}>
                                {t.debugSelectTrajectory || 'Select one trajectory to inspect.'}
                            </div>
                        ) : (
                            <>
                                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                                    <span style={{ color: '#6b7280', fontSize: 12 }}>
                                        run_id={trajectoryDetail?.run_id || ''}
                                    </span>
                                    <button
                                        type="button"
                                        className="btn-secondary"
                                        onClick={() => retryTrajectory(trajectoryDetail?.run_id)}
                                        disabled={retryingRunId === trajectoryDetail?.run_id}
                                        style={{ padding: '4px 8px' }}
                                    >
                                        {retryingRunId === trajectoryDetail?.run_id
                                            ? (t.loading || 'Retrying...')
                                            : (t.retry || 'Retry')}
                                    </button>
                                </div>
                                <div style={{ color: '#6b7280', fontSize: 12, marginBottom: 6 }}>
                                    tool_results={trajectoryToolResults.length} · errors={trajectoryToolErrors.length}
                                </div>
                                <div style={{ maxHeight: 250, overflowY: 'auto' }}>
                                    {trajectoryToolErrors.length === 0 ? (
                                        <div style={{ color: '#889', fontSize: 12 }}>
                                            {t.noData || 'No data'}
                                        </div>
                                    ) : trajectoryToolErrors.map((row) => (
                                        <div
                                            key={row.key}
                                            style={{
                                                padding: '8px 0',
                                                borderBottom: '1px solid rgba(255,255,255,0.06)',
                                                fontSize: 12,
                                            }}
                                        >
                                            <div style={{ color: '#e2e8f0' }}>
                                                tool={row.tool} · code={row.errorCode || 'UNKNOWN'}
                                            </div>
                                            {row.traceId && (
                                                <div style={{ color: '#6b7280' }}>trace_id={row.traceId}</div>
                                            )}
                                            {row.errorHint && (
                                                <div style={{ color: '#6b7280' }}>hint={row.errorHint}</div>
                                            )}
                                            {row.resultPreview && (
                                                <div style={{ color: '#6b7280' }}>{row.resultPreview}</div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            </>
                        )}
                    </div>
                </div>
            </div>

            <div className="card" style={{ padding: 16, marginTop: 12 }}>
                <div style={{ color: '#e2e8f0', marginBottom: 10 }}>Trends</div>
                <pre style={{ margin: 0, color: '#6b7280', fontSize: 12, whiteSpace: 'pre-wrap' }}>
                    {JSON.stringify(trends || {}, null, 2)}
                </pre>
            </div>

            <div className="card" style={{ padding: 16, marginTop: 12 }}>
                <div style={{ color: '#e2e8f0', marginBottom: 10 }}>Alerts</div>
                {alerts.length === 0 ? (
                    <div style={{ color: '#889' }}>{t.noData || 'No data'}</div>
                ) : (
                    alerts.map((item, idx) => (
                        <div key={`${item.timestamp || ''}_${idx}`} style={{ fontSize: 12, color: '#6b7280', padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                            [{item.level}] {item.category}: {item.message}
                        </div>
                    ))
                )}
            </div>
        </div>
    );
};

export default Observability;
