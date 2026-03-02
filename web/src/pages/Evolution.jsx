import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import {
    Zap, ThumbsUp, ThumbsDown, PenLine, Send,
    BarChart3, RefreshCw, Sparkles,
} from 'lucide-react';
import API_BASE from '../config';


const StatCard = ({ icon, label, value, color }) => (
    <div style={{
        flex: 1, minWidth: 140,
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderRadius: 16, padding: '20px 24px',
        display: 'flex', flexDirection: 'column', gap: 8,
    }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ color }}>{icon}</span>
            <span style={{ fontSize: 12, color: '#8899ac', textTransform: 'uppercase', letterSpacing: 1 }}>{label}</span>
        </div>
        <span style={{ fontSize: 28, fontWeight: 700, color: '#fff', fontFamily: 'monospace' }}>{value}</span>
    </div>
);

const Evolution = ({ t }) => {
    const [stats, setStats] = useState(null);

    const [feedbackLabel, setFeedbackLabel] = useState('correction');
    const [feedbackText, setFeedbackText] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [optimizing, setOptimizing] = useState(false);
    const [historySummary, setHistorySummary] = useState(null);
    const [clearingHistory, setClearingHistory] = useState(false);
    const [historyEvent, setHistoryEvent] = useState('');
    const [historyReason, setHistoryReason] = useState('');
    const [historyItems, setHistoryItems] = useState([]);
    const [exportingHistory, setExportingHistory] = useState(false);
    const [message, setMessage] = useState(null);
    const eventOptions = Object.keys(historySummary?.by_event || {});
    const reasonOptions = Object.keys(historySummary?.by_reason || {});

    const labelOptions = [
        { value: 'positive', icon: <ThumbsUp size={16} />, color: '#4ade80', label: t.positiveFeedbackLabel || 'Positive' },
        { value: 'negative', icon: <ThumbsDown size={16} />, color: '#f87171', label: t.negativeFeedbackLabel || 'Negative' },
        { value: 'correction', icon: <PenLine size={16} />, color: '#facc15', label: t.correctionFeedbackLabel || 'Correction' },
    ];

    const fetchStats = useCallback(async () => {
        try {
            const res = await axios.get(`${API_BASE}/evolution/stats`);
            setStats(res.data);
            try {
                const summaryRes = await axios.get(`${API_BASE}/evolution/history/summary`);
                setHistorySummary((summaryRes.data || {}).summary || null);
            } catch {
                setHistorySummary(null);
            }
            try {
                const historyRes = await axios.get(`${API_BASE}/evolution/history`, { params: { limit: 100 } });
                setHistoryItems((historyRes.data || {}).items || []);
            } catch {
                setHistoryItems([]);
            }
        } catch {
            /* backend offline */
        }
    }, []);

    useEffect(() => { fetchStats(); }, [fetchStats]);

    const showMessage = (text, type = 'success') => {
        setMessage({ text, type });
        setTimeout(() => setMessage(null), 4000);
    };

    const handleSubmitFeedback = async () => {
        if (!feedbackText.trim() && feedbackLabel !== 'positive') return;
        setSubmitting(true);
        try {
            await axios.post(`${API_BASE}/feedback`, {
                label: feedbackLabel,
                feedback: feedbackText.trim(),
                context: 'web_console',
            });
            showMessage(t.feedbackReceived || 'Feedback submitted');
            setFeedbackText('');
            fetchStats();
        } catch {
            showMessage(t.feedbackFailed || 'Failed to submit feedback', 'error');
        } finally {
            setSubmitting(false);
        }
    };

    const handleOptimize = async () => {
        setOptimizing(true);
        try {
            const res = await axios.post(`${API_BASE}/evolution/optimize`);
            if (res.data.updated) {
                showMessage(t.optimizeSuccess || 'System prompt optimized!');
                fetchStats();
            } else {
                showMessage(t.optimizeSkipped || 'Not enough feedback to optimize yet.', 'info');
            }
        } catch {
            showMessage(t.optimizeFailed || 'Optimization failed', 'error');
        } finally {
            setOptimizing(false);
        }
    };



    const autoStatus = stats?.auto_optimize || {};
    const cooldownRemaining = Number(autoStatus.cooldown_remaining_seconds || 0);
    const autoReason = autoStatus.last_reason || 'never';
    const autoReasonLabel = t[`autoReason_${autoReason}`] || autoReason;
    const fallbackRecentHistory = Array.isArray(autoStatus.recent_history) ? autoStatus.recent_history : [];
    const recentHistory = (Array.isArray(historyItems) && historyItems.length > 0 ? historyItems : fallbackRecentHistory).slice().reverse();

    const handleClearHistory = async () => {
        setClearingHistory(true);
        try {
            await axios.post(`${API_BASE}/evolution/history/clear`);
            showMessage(t.evolutionHistoryCleared || 'Evolution history cleared');
            fetchStats();
        } catch {
            showMessage(t.evolutionHistoryClearFailed || 'Failed to clear evolution history', 'error');
        } finally {
            setClearingHistory(false);
        }
    };

    const handleApplyHistoryFilter = async () => {
        try {
            const res = await axios.get(`${API_BASE}/evolution/history`, {
                params: {
                    limit: 100,
                    event: historyEvent || undefined,
                    reason: historyReason || undefined,
                },
            });
            setHistoryItems((res.data || {}).items || []);
        } catch {
            showMessage(t.historyFilterFailed || 'Failed to filter history', 'error');
        }
    };

    const handleExportHistoryCsv = async () => {
        setExportingHistory(true);
        try {
            const res = await axios.get(`${API_BASE}/evolution/history`, {
                params: {
                    limit: 500,
                    event: historyEvent || undefined,
                    reason: historyReason || undefined,
                    format: 'csv',
                },
                responseType: 'blob',
            });
            const blob = new Blob([res.data], { type: 'text/csv;charset=utf-8;' });
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.setAttribute('download', 'evolution_history.csv');
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            window.URL.revokeObjectURL(url);
            showMessage(t.historyExported || 'History exported');
        } catch {
            showMessage(t.historyExportFailed || 'Failed to export history', 'error');
        } finally {
            setExportingHistory(false);
        }
    };

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>
            {/* Header */}
            <header>
                <h2 style={{ fontSize: 22, fontWeight: 700, color: '#fff', margin: 0, marginBottom: 6 }}>
                    {t.sectionEvolution}
                </h2>
                <p style={{ color: '#8899ac', margin: 0 }}>{t.evolutionDesc}</p>
            </header>

            {/* Stats Row */}
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                <StatCard
                    icon={<BarChart3 size={18} />}
                    label={t.totalFeedback || 'Total'}
                    value={stats?.total ?? '—'}
                    color="#60a5fa"
                />
                <StatCard
                    icon={<ThumbsUp size={18} />}
                    label={t.positiveFeedback || 'Positive'}
                    value={stats?.positive ?? '—'}
                    color="#4ade80"
                />
                <StatCard
                    icon={<ThumbsDown size={18} />}
                    label={t.negativeFeedback || 'Negative'}
                    value={stats?.negative ?? '—'}
                    color="#f87171"
                />
                <StatCard
                    icon={<PenLine size={18} />}
                    label={t.correctionFeedback || 'Corrections'}
                    value={stats?.correction ?? '—'}
                    color="#facc15"
                />
            </div>

            {/* Auto optimize status + config */}
            <div className="glass-panel" style={{ padding: 28 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
                    <Sparkles size={20} style={{ color: '#a78bfa' }} />
                    <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: '#fff' }}>
                        {t.autoOptimizeSection || 'Auto Optimize'}
                    </h3>
                    <button onClick={fetchStats} className="btn-ghost" style={{ marginLeft: 'auto' }}>
                        <RefreshCw size={12} /> {t.refresh || 'Refresh'}
                    </button>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 12, marginBottom: 16 }}>
                    <div style={{ background: 'rgba(0,0,0,0.25)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, padding: 12 }}>
                        <div style={{ color: '#8899ac', fontSize: 12 }}>{t.autoStatus || 'Status'}</div>
                        <div style={{ color: autoStatus.enabled ? '#4ade80' : '#f87171', fontWeight: 700 }}>
                            {autoStatus.enabled ? (t.enabled || 'Enabled') : (t.disabled || 'Disabled')}
                        </div>
                    </div>
                    <div style={{ background: 'rgba(0,0,0,0.25)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, padding: 12 }}>
                        <div style={{ color: '#8899ac', fontSize: 12 }}>{t.autoLastReason || 'Last Reason'}</div>
                        <div style={{ color: '#fff', fontWeight: 600 }}>{autoReasonLabel}</div>
                    </div>
                    <div style={{ background: 'rgba(0,0,0,0.25)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, padding: 12 }}>
                        <div style={{ color: '#8899ac', fontSize: 12 }}>{t.autoCooldownRemaining || 'Cooldown Remaining'}</div>
                        <div style={{ color: '#fff', fontWeight: 600 }}>{cooldownRemaining}s</div>
                    </div>
                    <div style={{ background: 'rgba(0,0,0,0.25)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, padding: 12 }}>
                        <div style={{ color: '#8899ac', fontSize: 12 }}>{t.autoAttempts || 'Attempts / Successes'}</div>
                        <div style={{ color: '#fff', fontWeight: 600 }}>{autoStatus.attempts || 0} / {autoStatus.successes || 0}</div>
                    </div>
                </div>

                <p style={{ color: '#6b7280', fontSize: 12, marginTop: 8 }}>
                    {t.autoOptimizeConfigHint || 'To edit auto optimize settings, go to Settings → Persona tab.'}
                </p>
            </div>

            {/* Recent evolution history */}
            <div className="glass-panel" style={{ padding: 28 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                    <BarChart3 size={20} style={{ color: '#60a5fa' }} />
                    <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: '#fff' }}>
                        {t.evolutionRecentHistory || 'Recent Evolution History'}
                    </h3>
                    <button
                        onClick={handleClearHistory}
                        disabled={clearingHistory}
                        className="btn-ghost"
                        style={{ marginLeft: 'auto' }}
                    >
                        {clearingHistory ? (t.clearing || 'Clearing...') : (t.clearHistory || 'Clear History')}
                    </button>
                </div>
                {historySummary && (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 10, marginBottom: 12 }}>
                        <div style={{ color: '#93c5fd', fontSize: 12 }}>{t.total || 'Total'}: {historySummary.total || 0}</div>
                        <div style={{ color: '#4ade80', fontSize: 12 }}>{t.updated || 'Updated'}: {historySummary.updated || 0}</div>
                        <div style={{ color: '#fbbf24', fontSize: 12 }}>{t.notUpdated || 'Not Updated'}: {historySummary.not_updated || 0}</div>
                    </div>
                )}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto auto', gap: 8, marginBottom: 12 }}>
                    <select
                        value={historyEvent}
                        onChange={(e) => setHistoryEvent(e.target.value)}
                        style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 8, padding: '8px 10px', color: '#fff', fontSize: 12 }}
                    >
                        <option value="">{t.historyEventFilter || 'Filter by event'} ({t.all || 'all'})</option>
                        {eventOptions.map((eventKey) => (
                            <option key={eventKey} value={eventKey}>{eventKey}</option>
                        ))}
                    </select>
                    <select
                        value={historyReason}
                        onChange={(e) => setHistoryReason(e.target.value)}
                        style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 8, padding: '8px 10px', color: '#fff', fontSize: 12 }}
                    >
                        <option value="">{t.historyReasonFilter || 'Filter by reason'} ({t.all || 'all'})</option>
                        {reasonOptions.map((reasonKey) => (
                            <option key={reasonKey} value={reasonKey}>{reasonKey}</option>
                        ))}
                    </select>
                    <button className="btn-ghost" onClick={handleApplyHistoryFilter}>
                        {t.applyFilter || 'Apply'}
                    </button>
                    <button className="btn-ghost" onClick={handleExportHistoryCsv} disabled={exportingHistory}>
                        {exportingHistory ? (t.exporting || 'Exporting...') : (t.exportCsv || 'Export CSV')}
                    </button>
                </div>
                {recentHistory.length === 0 ? (
                    <div style={{ color: '#8899ac', fontSize: 13 }}>
                        {t.evolutionNoHistory || 'No history yet'}
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                        {recentHistory.map((item, idx) => {
                            const ts = String(item.timestamp || item.checked_at || '');
                            const reasonKey = `autoReason_${String(item.reason || '')}`;
                            const reasonText = t[reasonKey] || String(item.reason || '-');
                            return (
                                <div
                                    key={`${ts}-${idx}`}
                                    style={{
                                        border: '1px solid rgba(255,255,255,0.08)',
                                        borderRadius: 10,
                                        padding: '10px 12px',
                                        background: 'rgba(0,0,0,0.24)',
                                        display: 'grid',
                                        gridTemplateColumns: '1.2fr 1fr 0.8fr 1fr',
                                        gap: 10,
                                        fontSize: 12,
                                    }}
                                >
                                    <div style={{ color: '#93c5fd' }}>{ts || '-'}</div>
                                    <div style={{ color: '#e5e7eb' }}>{String(item.event || '-')}</div>
                                    <div style={{ color: item.updated ? '#4ade80' : '#fbbf24' }}>
                                        {item.updated ? (t.updated || 'Updated') : (t.notUpdated || 'Not Updated')}
                                    </div>
                                    <div style={{ color: '#cbd5e1' }}>{reasonText}</div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>



            {/* Feedback Submission */}
            <div className="glass-panel" style={{ padding: 28 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20 }}>
                    <Send size={20} style={{ color: '#a78bfa' }} />
                    <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: '#fff', textTransform: 'none', letterSpacing: 0 }}>
                        {t.feedbackSubmission || 'Submit Feedback'}
                    </h3>
                </div>

                {/* Label selector */}
                <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
                    {labelOptions.map(opt => (
                        <button
                            key={opt.value}
                            onClick={() => setFeedbackLabel(opt.value)}
                            style={{
                                display: 'flex', alignItems: 'center', gap: 8,
                                padding: '10px 18px', borderRadius: 12, fontSize: 13,
                                fontWeight: feedbackLabel === opt.value ? 600 : 400,
                                background: feedbackLabel === opt.value
                                    ? `${opt.color}18` : 'rgba(255,255,255,0.03)',
                                border: `1px solid ${feedbackLabel === opt.value ? opt.color + '55' : 'rgba(255,255,255,0.08)'}`,
                                color: feedbackLabel === opt.value ? opt.color : '#8899ac',
                            }}
                        >
                            {opt.icon} {opt.label}
                        </button>
                    ))}
                </div>

                {/* Text area */}
                <textarea
                    value={feedbackText}
                    onChange={e => setFeedbackText(e.target.value)}
                    placeholder={t.feedbackPlaceholder}
                    rows={3}
                    style={{
                        width: '100%', background: 'rgba(0,0,0,0.3)',
                        border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12,
                        padding: 16, color: '#fff', fontSize: 14, resize: 'vertical',
                        outline: 'none', lineHeight: 1.6,
                    }}
                />

                {/* Submit button */}
                <div style={{ display: 'flex', gap: 12, marginTop: 16 }}>
                    <button
                        onClick={handleSubmitFeedback}
                        disabled={submitting}
                        style={{
                            display: 'flex', alignItems: 'center', gap: 8,
                            padding: '10px 22px', borderRadius: 10,
                            background: submitting ? 'rgba(255,255,255,0.05)'
                                : 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                            color: '#fff', fontWeight: 600, fontSize: 13,
                            cursor: submitting ? 'not-allowed' : 'pointer',
                            opacity: submitting ? 0.6 : 1,
                        }}
                    >
                        <Send size={16} />
                        {submitting ? (t.submitting || 'Submitting...') : (t.submitFeedback || 'Submit Feedback')}
                    </button>
                </div>
            </div>

            {/* Optimize Trigger */}
            <div className="glass-panel" style={{ padding: 28 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                    <Sparkles size={20} style={{ color: '#f59e0b' }} />
                    <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: '#fff', textTransform: 'none', letterSpacing: 0 }}>
                        {t.evolutionControl || 'Evolution Control'}
                    </h3>
                </div>
                <p style={{ color: '#8899ac', fontSize: 13, marginBottom: 20, lineHeight: 1.7 }}>
                    {t.optimizeDesc || 'Analyze accumulated feedback and use LLM self-critique to generate an improved system prompt. Requires at least 3 negative or correction entries.'}
                </p>
                <button
                    onClick={handleOptimize}
                    disabled={optimizing}
                    style={{
                        display: 'flex', alignItems: 'center', gap: 10,
                        padding: '12px 24px', borderRadius: 10,
                        background: optimizing ? 'rgba(255,255,255,0.05)'
                            : 'linear-gradient(135deg, #7c3aed, #2563eb)',
                        color: '#fff', fontWeight: 600, fontSize: 14,
                        cursor: optimizing ? 'not-allowed' : 'pointer',
                        opacity: optimizing ? 0.6 : 1,
                        boxShadow: optimizing ? 'none' : '0 4px 24px rgba(124,58,237,0.3)',
                    }}
                >
                    {optimizing
                        ? <><RefreshCw size={18} style={{ animation: 'spin 1s linear infinite' }} /> {t.optimizing}</>
                        : <><Zap size={18} /> {t.triggerEvolution}</>
                    }
                </button>
            </div>

            {/* Toast message */}
            {message && (
                <div style={{
                    position: 'fixed', bottom: 32, right: 32, zIndex: 9999,
                    padding: '14px 28px', borderRadius: 12,
                    background: message.type === 'error' ? 'rgba(239,68,68,0.85)'
                        : message.type === 'info' ? 'rgba(59,130,246,0.85)'
                            : 'rgba(34,197,94,0.85)',
                    backdropFilter: 'blur(12px)',
                    color: '#fff', fontWeight: 600, fontSize: 14,
                    boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
                    animation: 'fadeIn 0.3s ease-out',
                }}>
                    {message.text}
                </div>
            )}

            <style>{`
                @keyframes spin {
                    from { transform: rotate(0deg); }
                    to { transform: rotate(360deg); }
                }
            `}</style>
        </div>
    );
};

export default Evolution;
