import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import API_BASE from '../config';
import NoticeBanner from '../components/NoticeBanner';
import useNotice from '../hooks/useNotice';

const toCsvLine = (value) => {
    const raw = String(value ?? '');
    if (raw.includes(',') || raw.includes('"') || raw.includes('\n')) {
        return `"${raw.replaceAll('"', '""')}"`;
    }
    return raw;
};

const PolicyAudit = ({ t }) => {
    const [entries, setEntries] = useState([]);
    const [actionFilter, setActionFilter] = useState('all');
    const [loading, setLoading] = useState(false);
    const { notice, showNotice } = useNotice();

    const loadAudit = async (action = actionFilter) => {
        setLoading(true);
        try {
            const query = action && action !== 'all' ? `?limit=200&action=${encodeURIComponent(action)}` : '?limit=200';
            const res = await axios.get(`${API_BASE}/policy/audit${query}`);
            setEntries(res.data?.entries || []);
        } catch {
            setEntries([]);
            showNotice(t.noticeLoadPolicyAuditFailed || 'Failed to load policy audit', 'error');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadAudit();
    }, []);

    const actionOptions = useMemo(() => {
        const options = new Set(['all']);
        for (const item of entries) {
            if (item?.action) options.add(String(item.action));
        }
        return [...options];
    }, [entries]);

    const exportCsv = () => {
        const rows = [
            ['timestamp', 'action', 'details'],
            ...entries.map((item) => [item.timestamp || '', item.action || '', JSON.stringify(item.details || {})]),
        ];
        const csv = rows.map((row) => row.map(toCsvLine).join(',')).join('\n');
        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', 'policy-audit.csv');
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        window.URL.revokeObjectURL(url);
    };

    const clearAudit = async () => {
        try {
            await axios.delete(`${API_BASE}/policy/audit`);
            setEntries([]);
            showNotice(t.noticePolicyAuditCleared || 'Policy audit cleared', 'success');
        } catch {
            showNotice(t.noticeClearPolicyAuditFailed || 'Failed to clear policy audit', 'error');
        }
    };

    return (
        <div style={{ maxWidth: 980 }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, color: '#fff', marginBottom: 6 }}>
                {t.policyAudit || 'Policy Audit'}
            </h2>
            <p style={{ color: '#6b7280', marginTop: 0, marginBottom: 16 }}>
                {t.policyAuditDesc || 'Track policy and router strategy changes for governance and debugging.'}
            </p>
            <NoticeBanner notice={notice} />

            <div className="card" style={{ padding: 16, marginBottom: 12 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr auto auto auto', gap: 8 }}>
                    <select
                        className="input"
                        value={actionFilter}
                        onChange={(e) => setActionFilter(e.target.value)}
                    >
                        {actionOptions.map((action) => (
                            <option key={action} value={action}>
                                {action}
                            </option>
                        ))}
                    </select>
                    <button className="btn-ghost" onClick={() => loadAudit(actionFilter)} disabled={loading}>
                        {t.refresh || 'Refresh'}
                    </button>
                    <button className="btn-ghost" onClick={exportCsv} disabled={entries.length === 0}>
                        {t.exportCsv || 'Export CSV'}
                    </button>
                    <button className="btn-ghost" onClick={clearAudit} disabled={entries.length === 0}>
                        {t.clear || 'Clear'}
                    </button>
                </div>
            </div>

            <div className="card" style={{ padding: 16 }}>
                <div style={{ maxHeight: 460, overflowY: 'auto' }}>
                    {entries.map((item, idx) => (
                        <div key={`${item.timestamp || 'ts'}_${idx}`} style={{ padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                            <div style={{ color: '#e2e8f0', fontSize: 12 }}>{item.action}</div>
                            <div style={{ color: '#7f8ea3', fontSize: 11 }}>{item.timestamp}</div>
                            <div style={{ color: '#6b7280', fontSize: 11 }}>{JSON.stringify(item.details || {})}</div>
                        </div>
                    ))}
                    {entries.length === 0 && (
                        <div style={{ color: '#889', fontSize: 12 }}>
                            {t.noData || 'No data'}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default PolicyAudit;
