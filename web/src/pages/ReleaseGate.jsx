import React, { useEffect, useState } from 'react';
import axios from 'axios';
import API_BASE from '../config';
import NoticeBanner from '../components/NoticeBanner';
import useNotice from '../hooks/useNotice';
import WorkflowObservabilityCard from '../components/WorkflowObservabilityCard';
import ToggleSwitch from '../components/ToggleSwitch';

const ReleaseGate = ({ t }) => {
    const [gate, setGate] = useState(null);
    const [workflowHealth, setWorkflowHealth] = useState(null);
    const [gateHealth, setGateHealth] = useState(null);
    const [loading, setLoading] = useState(false);
    const { notice, showNotice } = useNotice();
    const [form, setForm] = useState({
        blocked: false,
        reason: '',
        source: 'manual',
        metadataText: '{}',
    });

    const load = async () => {
        setLoading(true);
        try {
            const gateRes = await axios.get(`${API_BASE}/debug/release-gate`);
            const data = gateRes.data?.gate || {};
            const workflow = gateRes.data?.workflow || null;
            const health = gateRes.data?.health || null;
            setGate(data);
            setWorkflowHealth(workflow);
            setGateHealth(health);
            setForm((prev) => ({
                ...prev,
                blocked: Boolean(data.blocked),
                reason: String(data.reason || ''),
                source: String(data.source || 'manual'),
                metadataText: JSON.stringify(data.metadata || {}, null, 2),
            }));
        } catch {
            setGate(null);
            setWorkflowHealth(null);
            setGateHealth(null);
            showNotice(t.noticeLoadReleaseGateFailed || 'Failed to load release gate', 'error');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
    }, []);

    const overrideGate = async () => {
        let metadata = {};
        try {
            metadata = JSON.parse(form.metadataText || '{}');
            if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) {
                metadata = {};
            }
        } catch {
            showNotice(t.noticeInvalidMetadataJson || 'Invalid metadata JSON', 'error');
            return;
        }

        try {
            await axios.post(`${API_BASE}/debug/release-gate/override`, {
                blocked: form.blocked,
                reason: form.reason,
                source: form.source,
                metadata,
            });
            await load();
            showNotice(t.noticeReleaseGateUpdated || 'Release gate updated', 'success');
        } catch {
            showNotice(t.noticeOverrideReleaseGateFailed || 'Failed to override release gate', 'error');
        }
    };

    const gateBlocked = Boolean(gate?.blocked);
    const workflowSuccessRate = Number(gateHealth?.signals?.success_rate ?? workflowHealth?.success_rate ?? 1);
    const workflowFailures = Number(gateHealth?.signals?.failures ?? workflowHealth?.failures ?? 0);
    const workflowRuns = Number(gateHealth?.signals?.total_runs ?? workflowHealth?.total_runs ?? 0);
    const workflowP95 = Number(gateHealth?.signals?.p95_latency_ms ?? workflowHealth?.p95_latency_ms ?? 0);
    const linkageLevel = String(gateHealth?.level || (gateBlocked ? 'critical' : 'healthy'));
    const linkageText = gateBlocked
        ? (t.releaseGateHealthBlocked || 'Release Gate is blocked. High-risk actions remain hard-stopped.')
        : linkageLevel === 'critical'
            ? (t.releaseGateHealthCritical || 'Workflow health is critical. Recommend blocking risky releases/actions.')
            : linkageLevel === 'warning'
                ? (t.releaseGateHealthWarning || 'Workflow health is degraded. Proceed with caution and monitor closely.')
                : linkageLevel === 'unknown'
                    ? (t.releaseGateHealthUnknown || 'Workflow health has no recent runs yet. Collect runtime data first.')
                : (t.releaseGateHealthHealthy || 'Release Gate and workflow health are stable.');
    const linkageBg = linkageLevel === 'critical'
        ? 'rgba(239,68,68,0.18)'
        : linkageLevel === 'warning'
            ? 'rgba(245,158,11,0.16)'
            : 'rgba(34,197,94,0.16)';
    const linkageBorder = linkageLevel === 'critical'
        ? '1px solid rgba(239,68,68,0.45)'
        : linkageLevel === 'warning'
            ? '1px solid rgba(245,158,11,0.45)'
            : '1px solid rgba(34,197,94,0.45)';

    return (
        <div style={{ maxWidth: 980 }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, color: '#fff', marginBottom: 6 }}>
                {t.releaseGate || 'Release Gate'}
            </h2>
            <p style={{ color: '#8aa0bd', marginTop: 0, marginBottom: 16 }}>
                {t.releaseGateDesc || 'Observe release gate status and manually override during incidents.'}
            </p>
            <NoticeBanner notice={notice} />
            <div
                className="card"
                style={{
                    padding: 12,
                    marginBottom: 12,
                    background: linkageBg,
                    border: linkageBorder,
                }}
            >
                <div style={{ fontSize: 13, color: '#e2e8f0', marginBottom: 4 }}>
                    {t.releaseGateHealthTitle || 'Gate × Workflow Health'}
                </div>
                <div style={{ fontSize: 12, color: '#cbd5e1' }}>{linkageText}</div>
                <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 6 }}>
                    {`runs=${workflowRuns} · success_rate=${workflowSuccessRate} · failures=${workflowFailures} · p95=${workflowP95}ms`}
                </div>
                <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>
                    {`recommend_block_high_risk=${String(Boolean(gateHealth?.recommend_block_high_risk))}`}
                </div>
            </div>
            <details className="card" style={{ padding: 12, marginBottom: 12 }}>
                <summary style={{ cursor: 'pointer', color: '#dbeafe', fontSize: 13, fontWeight: 600 }}>
                    {t.releaseGateHealthThresholdDetails || 'Current Threshold Details'}
                </summary>
                <div style={{ marginTop: 10, display: 'grid', gridTemplateColumns: 'repeat(2, minmax(220px, 1fr))', gap: 8, fontSize: 12 }}>
                    <div style={{ color: '#9fb3c8' }}>
                        warning_success_rate: <span style={{ color: '#dbeafe' }}>{gateHealth?.thresholds?.warning_success_rate ?? '-'}</span>
                    </div>
                    <div style={{ color: '#9fb3c8' }}>
                        critical_success_rate: <span style={{ color: '#dbeafe' }}>{gateHealth?.thresholds?.critical_success_rate ?? '-'}</span>
                    </div>
                    <div style={{ color: '#9fb3c8' }}>
                        warning_failures: <span style={{ color: '#dbeafe' }}>{gateHealth?.thresholds?.warning_failures ?? '-'}</span>
                    </div>
                    <div style={{ color: '#9fb3c8' }}>
                        critical_failures: <span style={{ color: '#dbeafe' }}>{gateHealth?.thresholds?.critical_failures ?? '-'}</span>
                    </div>
                    <div style={{ color: '#9fb3c8' }}>
                        warning_p95_latency_ms: <span style={{ color: '#dbeafe' }}>{gateHealth?.thresholds?.warning_p95_latency_ms ?? '-'}</span>
                    </div>
                    <div style={{ color: '#9fb3c8' }}>
                        critical_p95_latency_ms: <span style={{ color: '#dbeafe' }}>{gateHealth?.thresholds?.critical_p95_latency_ms ?? '-'}</span>
                    </div>
                </div>
            </details>
            <WorkflowObservabilityCard t={t} compact={false} limit={5} />

            <div className="card" style={{ padding: 16, marginBottom: 12 }}>
                <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 13, color: '#9fb3c8' }}>
                    <span>blocked: {String(gate?.blocked ?? false)}</span>
                    <span>reason: {gate?.reason || '-'}</span>
                    <span>source: {gate?.source || '-'}</span>
                    <span>updated_at: {gate?.updated_at ? new Date(gate.updated_at * 1000).toLocaleString() : '-'}</span>
                </div>
            </div>

            <div className="card" style={{ padding: 16 }}>
                <label className="label">{t.releaseGateOverride || 'Manual Override'}</label>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(220px, 1fr))', gap: 12 }}>
                    <div style={{ gridColumn: '1 / span 2', color: '#9fb3c8', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
                        <span>blocked</span>
                        <ToggleSwitch
                            checked={Boolean(form.blocked)}
                            onChange={(v) => setForm((prev) => ({ ...prev, blocked: v }))}
                        />
                    </div>
                    <div>
                        <label className="label">reason</label>
                        <input
                            className="input"
                            value={form.reason}
                            onChange={(e) => setForm((prev) => ({ ...prev, reason: e.target.value }))}
                        />
                    </div>
                    <div>
                        <label className="label">source</label>
                        <input
                            className="input"
                            value={form.source}
                            onChange={(e) => setForm((prev) => ({ ...prev, source: e.target.value }))}
                        />
                    </div>
                </div>
                <div style={{ marginTop: 12 }}>
                    <label className="label">metadata (JSON)</label>
                    <textarea
                        className="input"
                        rows={6}
                        value={form.metadataText}
                        onChange={(e) => setForm((prev) => ({ ...prev, metadataText: e.target.value }))}
                        style={{ fontFamily: 'monospace' }}
                    />
                </div>
                <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
                    <button className="btn-primary" onClick={overrideGate} disabled={loading}>
                        {t.save || 'Save'}
                    </button>
                </div>
            </div>
        </div>
    );
};

export default ReleaseGate;
