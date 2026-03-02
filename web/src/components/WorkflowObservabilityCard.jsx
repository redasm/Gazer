import React, { useEffect, useState } from 'react';
import axios from 'axios';
import API_BASE from '../config';

const MiniStat = ({ label, value }) => (
    <div style={{ minWidth: 120 }}>
        <div style={{ fontSize: 11, color: '#7f8ea3' }}>{label}</div>
        <div style={{ fontSize: 18, fontWeight: 700, color: '#dbeafe' }}>{value}</div>
    </div>
);

const WorkflowObservabilityCard = ({ t, compact = false, limit = 5 }) => {
    const [workflow, setWorkflow] = useState(null);
    const [loading, setLoading] = useState(false);

    const load = async () => {
        setLoading(true);
        try {
            const res = await axios.get(`${API_BASE}/observability/metrics`, { params: { limit: 200 } });
            setWorkflow(res.data?.workflow || null);
        } catch {
            setWorkflow(null);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
    }, []);

    const rows = (workflow?.workflows || []).slice(0, Math.max(1, limit));

    return (
        <div className="card" style={{ padding: 16, marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <div style={{ color: '#dbeafe', fontWeight: 600 }}>
                    {t.workflowHealth || 'Workflow Health'}
                </div>
                <button className="btn-secondary" onClick={load} disabled={loading}>
                    {loading ? (t.loading || 'Loading...') : (t.refresh || 'Refresh')}
                </button>
            </div>
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: compact ? 0 : 10 }}>
                <MiniStat label="runs" value={workflow?.total_runs ?? 0} />
                <MiniStat label="success_rate" value={workflow?.success_rate ?? 1} />
                <MiniStat label="p95_ms" value={workflow?.p95_latency_ms ?? 0} />
                <MiniStat label="p95_nodes" value={workflow?.p95_trace_nodes ?? 0} />
            </div>
            {!compact && (
                <div style={{ marginTop: 8 }}>
                    {rows.length === 0 ? (
                        <div style={{ color: '#7f8ea3', fontSize: 12 }}>{t.noData || 'No data'}</div>
                    ) : (
                        rows.map((item) => (
                            <div
                                key={item.workflow_id}
                                style={{
                                    display: 'grid',
                                    gridTemplateColumns: '1.4fr 0.9fr 0.9fr 0.9fr 1.2fr',
                                    gap: 8,
                                    fontSize: 12,
                                    padding: '6px 0',
                                    borderBottom: '1px solid rgba(255,255,255,0.06)',
                                }}
                            >
                                <span style={{ color: '#dbeafe' }}>{item.workflow_name || item.workflow_id}</span>
                                <span style={{ color: '#9fb3c8' }}>runs={item.runs}</span>
                                <span style={{ color: '#9fb3c8' }}>ok={item.success_rate}</span>
                                <span style={{ color: '#9fb3c8' }}>p95={item.p95_latency_ms}</span>
                                <span style={{ color: '#9fb3c8' }}>{JSON.stringify(item.error_classes || {})}</span>
                            </div>
                        ))
                    )}
                </div>
            )}
        </div>
    );
};

export default WorkflowObservabilityCard;

