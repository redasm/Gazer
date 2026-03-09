import React, { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import {
    Brain, Zap, ThumbsUp, ThumbsDown, PenLine, Send,
    BarChart3, RefreshCw, Sparkles, Save, Activity,
} from 'lucide-react';
import API_BASE from '../config';

/* ------------------------------------------------------------------ */
/*  Shared small components                                            */
/* ------------------------------------------------------------------ */

const Badge = ({ color, children }) => (
    <span style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        padding: '4px 12px', borderRadius: 8, fontSize: 12, fontWeight: 600,
        background: `${color}18`, color, border: `1px solid ${color}44`,
    }}>
        {children}
    </span>
);

const StatCard = ({ icon, label, value, color }) => (
    <div style={{
        flex: 1, minWidth: 120,
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderRadius: 14, padding: '16px 20px',
        display: 'flex', flexDirection: 'column', gap: 6,
    }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ color }}>{icon}</span>
            <span style={{ fontSize: 11, color: '#8899ac', textTransform: 'uppercase', letterSpacing: 1 }}>{label}</span>
        </div>
        <span style={{ fontSize: 24, fontWeight: 700, color: '#fff', fontFamily: 'monospace' }}>{value}</span>
    </div>
);

const OceanSlider = ({ label, labelZh, value, onChange, color }) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '6px 0' }}>
        <div style={{ width: 110, flexShrink: 0 }}>
            <div style={{ fontSize: 13, color: '#e5e7eb', fontWeight: 500 }}>{label}</div>
            {labelZh && <div style={{ fontSize: 11, color: '#6b7280' }}>{labelZh}</div>}
        </div>
        <input
            type="range" min="0" max="1" step="0.01"
            value={value}
            onChange={(e) => onChange(parseFloat(e.target.value))}
            style={{ flex: 1, accentColor: color }}
        />
        <span style={{ color: '#9ca3af', fontSize: 13, minWidth: 36, textAlign: 'right', fontFamily: 'monospace' }}>
            {value.toFixed(2)}
        </span>
    </div>
);

/* ------------------------------------------------------------------ */
/*  Main Personality Page                                               */
/* ------------------------------------------------------------------ */

const Personality = ({ t, showToast }) => {
    const [state, setState] = useState(null);
    const [ocean, setOcean] = useState(null);
    const [promptText, setPromptText] = useState('');
    const [promptDirty, setPromptDirty] = useState(false);
    const [saving, setSaving] = useState(false);
    const promptDirtyRef = useRef(false);

    // Evolution state
    const [stats, setStats] = useState(null);
    const [feedbackLabel, setFeedbackLabel] = useState('correction');
    const [feedbackText, setFeedbackText] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [optimizing, setOptimizing] = useState(false);
    const [historyItems, setHistoryItems] = useState([]);

    const showMessage = useCallback((text, type = 'success') => {
        if (showToast) {
            showToast(text, type);
        }
    }, [showToast]);

    const fetchState = useCallback(async () => {
        try {
            const res = await axios.get(`${API_BASE}/personality/state`);
            if (res.data?.status === 'ok') {
                setState(res.data);
                setOcean(res.data.ocean);
                if (!promptDirtyRef.current) {
                    setPromptText(res.data.system_prompt || '');
                }
            }
        } catch { /* backend offline */ }
    }, []);

    const fetchEvolution = useCallback(async () => {
        try {
            const [statsRes, historyRes] = await Promise.all([
                axios.get(`${API_BASE}/evolution/stats`),
                axios.get(`${API_BASE}/evolution/history`, { params: { limit: 50 } }),
            ]);
            setStats(statsRes.data);
            setHistoryItems((historyRes.data || {}).items || []);
        } catch { /* backend offline */ }
    }, []);

    useEffect(() => { fetchState(); fetchEvolution(); }, [fetchState, fetchEvolution]);

    // --- OCEAN save ---
    const handleSaveOcean = async () => {
        if (!ocean) return;
        setSaving(true);
        try {
            await axios.post(`${API_BASE}/personality/state`, { ocean });
            showMessage(t.oceanSaved || 'Personality vector saved');
            fetchState();
        } catch {
            showMessage(t.oceanSaveFailed || 'Failed to save personality', 'error');
        } finally { setSaving(false); }
    };

    // --- System prompt save ---
    const handleSavePrompt = async () => {
        setSaving(true);
        try {
            await axios.post(`${API_BASE}/personality/state`, { system_prompt: promptText });
            setPromptDirty(false);
            promptDirtyRef.current = false;
            showMessage(t.promptSaved || 'System prompt saved');
        } catch {
            showMessage(t.promptSaveFailed || 'Failed to save prompt', 'error');
        } finally { setSaving(false); }
    };

    // --- Feedback ---
    const handleSubmitFeedback = async () => {
        if (!feedbackText.trim() && feedbackLabel !== 'positive') return;
        setSubmitting(true);
        try {
            await axios.post(`${API_BASE}/feedback`, {
                label: feedbackLabel, feedback: feedbackText.trim(), context: 'web_console',
            });
            showMessage(t.feedbackReceived || 'Feedback submitted');
            setFeedbackText('');
            fetchEvolution();
        } catch {
            showMessage(t.feedbackFailed || 'Failed to submit feedback', 'error');
        } finally { setSubmitting(false); }
    };

    // --- Optimize ---
    const handleOptimize = async () => {
        setOptimizing(true);
        try {
            const res = await axios.post(`${API_BASE}/evolution/optimize`);
            if (res.data.updated) {
                showMessage(t.optimizeSuccess || 'System prompt optimized!');
                setPromptDirty(false);
                promptDirtyRef.current = false;
                fetchState();
                fetchEvolution();
            } else {
                showMessage(t.optimizeSkipped || 'Not enough feedback yet', 'info');
            }
        } catch {
            showMessage(t.optimizeFailed || 'Optimization failed', 'error');
        } finally { setOptimizing(false); }
    };

    const labelOptions = [
        { value: 'positive', icon: <ThumbsUp size={14} />, color: '#4ade80', label: t.positiveFeedbackLabel || 'Positive' },
        { value: 'negative', icon: <ThumbsDown size={14} />, color: '#f87171', label: t.negativeFeedbackLabel || 'Negative' },
        { value: 'correction', icon: <PenLine size={14} />, color: '#facc15', label: t.correctionFeedbackLabel || 'Correction' },
    ];
    const recentHistory = (historyItems || []).slice().reverse().slice(0, 20);

    const panelStyle = {
        background: 'rgba(255,255,255,0.02)',
        border: '1px solid rgba(255,255,255,0.06)',
        borderRadius: 16, padding: 24,
    };

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24, maxWidth: 1100 }}>
            {/* Header */}
            <header>
                <h2 style={{ fontSize: 22, fontWeight: 700, color: '#fff', margin: 0, marginBottom: 4 }}>
                    <Brain size={22} style={{ verticalAlign: 'middle', marginRight: 8, color: '#c084fc' }} />
                    {t.personalityPage || 'Personality'}
                </h2>
                <p style={{ color: '#8899ac', margin: 0 }}>
                    {t.personalityPageDesc || 'View, customize, and train your AI companion\'s personality.'}
                </p>
            </header>

            {/* ── Section 1: Personality State ────────────────────── */}
            <div style={panelStyle}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
                    <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: '#fff' }}>
                        {t.personalityState || 'Personality State'}
                    </h3>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        {state?.affect && (
                            <Badge color="#a78bfa">{t.emotion || '情绪'}: {state.affect.label}</Badge>
                        )}
                        {state?.mental_state && (
                            <Badge color="#60a5fa">{state.mental_state.description}</Badge>
                        )}
                        <button onClick={fetchState} style={{
                            background: 'none', border: '1px solid rgba(255,255,255,0.1)',
                            borderRadius: 8, padding: '6px 10px', color: '#8899ac', cursor: 'pointer',
                            display: 'flex', alignItems: 'center', gap: 4, fontSize: 12,
                        }}>
                            <RefreshCw size={12} /> {t.refresh || 'Refresh'}
                        </button>
                    </div>
                </div>

                {ocean ? (
                    <>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 32px' }}>
                        <OceanSlider label="Openness" labelZh={t.oceanOpenness} value={ocean.openness} color="#8b5cf6"
                                onChange={(v) => setOcean({ ...ocean, openness: v })} />
                            <OceanSlider label="Conscientiousness" labelZh={t.oceanConscientiousness} value={ocean.conscientiousness} color="#3b82f6"
                                onChange={(v) => setOcean({ ...ocean, conscientiousness: v })} />
                            <OceanSlider label="Extraversion" labelZh={t.oceanExtraversion} value={ocean.extraversion} color="#22c55e"
                                onChange={(v) => setOcean({ ...ocean, extraversion: v })} />
                            <OceanSlider label="Agreeableness" labelZh={t.oceanAgreeableness} value={ocean.agreeableness} color="#f59e0b"
                                onChange={(v) => setOcean({ ...ocean, agreeableness: v })} />
                            <OceanSlider label="Neuroticism" labelZh={t.oceanNeuroticism} value={ocean.neuroticism} color="#ef4444"
                                onChange={(v) => setOcean({ ...ocean, neuroticism: v })} />
                            <OceanSlider label="Humor" labelZh={t.oceanHumor} value={ocean.humor_level} color="#ec4899"
                                onChange={(v) => setOcean({ ...ocean, humor_level: v })} />
                            <OceanSlider label="Verbosity" labelZh={t.oceanVerbosity} value={ocean.verbosity} color="#14b8a6"
                                onChange={(v) => setOcean({ ...ocean, verbosity: v })} />
                            <OceanSlider label="Formality" labelZh={t.oceanFormality} value={ocean.formality} color="#6366f1"
                                onChange={(v) => setOcean({ ...ocean, formality: v })} />
                        </div>
                        <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
                            <button onClick={handleSaveOcean} disabled={saving} style={{
                                display: 'flex', alignItems: 'center', gap: 6,
                                padding: '8px 18px', borderRadius: 10,
                                background: 'linear-gradient(135deg, #7c3aed, #6366f1)',
                                color: '#fff', fontWeight: 600, fontSize: 13, cursor: 'pointer',
                                opacity: saving ? 0.6 : 1,
                            }}>
                                <Save size={14} /> {t.saveOcean || 'Save Personality'}
                            </button>
                        </div>
                    </>
                ) : (
                    <div style={{ color: '#6b7280', fontSize: 13 }}>
                        {t.personalityLoading || 'Loading personality state...'}
                    </div>
                )}
            </div>

            {/* ── Section 2: System Prompt ────────────────────── */}
            <div style={panelStyle}>
                <h3 style={{ margin: 0, marginBottom: 12, fontSize: 16, fontWeight: 600, color: '#fff' }}>
                    {t.systemPrompt || 'System Prompt'}
                </h3>
                <p style={{ color: '#6b7280', fontSize: 12, margin: '0 0 10px' }}>
                    {t.systemPromptHint || 'Synced from assets/SOUL.md. Edit here or directly in the file.'}
                </p>
                <textarea
                    value={promptText}
                    onChange={(e) => { setPromptText(e.target.value); setPromptDirty(true); promptDirtyRef.current = true; }}
                    rows={10}
                    style={{
                        width: '100%', background: 'rgba(0,0,0,0.3)',
                        border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12,
                        padding: 14, color: '#fff', fontSize: 13, resize: 'vertical',
                        fontFamily: 'monospace', lineHeight: 1.6, outline: 'none',
                    }}
                />
                <div style={{ marginTop: 10, display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                    {promptDirty && (
                        <span style={{ color: '#fbbf24', fontSize: 12, alignSelf: 'center' }}>
                            {t.unsavedChanges || 'Unsaved changes'}
                        </span>
                    )}
                    <button onClick={handleSavePrompt} disabled={saving || !promptDirty} style={{
                        display: 'flex', alignItems: 'center', gap: 6,
                        padding: '8px 18px', borderRadius: 10,
                        background: promptDirty ? 'linear-gradient(135deg, #2563eb, #3b82f6)' : 'rgba(255,255,255,0.05)',
                        color: '#fff', fontWeight: 600, fontSize: 13,
                        cursor: promptDirty ? 'pointer' : 'default',
                        opacity: (saving || !promptDirty) ? 0.5 : 1,
                    }}>
                        <Save size={14} /> {t.savePrompt || 'Save Prompt'}
                    </button>
                </div>
            </div>

            {/* ── Section 3: Training ────────────────────── */}
            <div style={panelStyle}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
                    <Sparkles size={20} style={{ color: '#a78bfa' }} />
                    <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: '#fff' }}>
                        {t.trainingSection || 'Training'}
                    </h3>
                    <button onClick={fetchEvolution} style={{
                        marginLeft: 'auto', background: 'none',
                        border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8,
                        padding: '6px 10px', color: '#8899ac', cursor: 'pointer',
                        display: 'flex', alignItems: 'center', gap: 4, fontSize: 12,
                    }}>
                        <RefreshCw size={12} /> {t.refresh || 'Refresh'}
                    </button>
                </div>

                {/* Feedback stats */}
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
                    <StatCard icon={<BarChart3 size={16} />} label={t.totalFeedback || 'Total'} value={stats?.total ?? '—'} color="#60a5fa" />
                    <StatCard icon={<ThumbsUp size={16} />} label={t.positiveFeedback || 'Positive'} value={stats?.positive ?? '—'} color="#4ade80" />
                    <StatCard icon={<ThumbsDown size={16} />} label={t.negativeFeedback || 'Negative'} value={stats?.negative ?? '—'} color="#f87171" />
                    <StatCard icon={<PenLine size={16} />} label={t.correctionFeedback || 'Corrections'} value={stats?.correction ?? '—'} color="#facc15" />
                </div>

                {/* Feedback form */}
                <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                    {labelOptions.map(opt => (
                        <button
                            key={opt.value}
                            onClick={() => setFeedbackLabel(opt.value)}
                            style={{
                                display: 'flex', alignItems: 'center', gap: 6,
                                padding: '8px 14px', borderRadius: 10, fontSize: 12,
                                fontWeight: feedbackLabel === opt.value ? 600 : 400,
                                background: feedbackLabel === opt.value ? `${opt.color}18` : 'rgba(255,255,255,0.03)',
                                border: `1px solid ${feedbackLabel === opt.value ? opt.color + '55' : 'rgba(255,255,255,0.08)'}`,
                                color: feedbackLabel === opt.value ? opt.color : '#8899ac',
                                cursor: 'pointer',
                            }}
                        >
                            {opt.icon} {opt.label}
                        </button>
                    ))}
                </div>
                <textarea
                    value={feedbackText}
                    onChange={e => setFeedbackText(e.target.value)}
                    placeholder={t.feedbackPlaceholder || 'Describe what you liked or disliked about the response...'}
                    rows={2}
                    style={{
                        width: '100%', background: 'rgba(0,0,0,0.3)',
                        border: '1px solid rgba(255,255,255,0.08)', borderRadius: 10,
                        padding: 12, color: '#fff', fontSize: 13, resize: 'vertical',
                        outline: 'none', lineHeight: 1.5,
                    }}
                />
                <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
                    <button onClick={handleSubmitFeedback} disabled={submitting} style={{
                        display: 'flex', alignItems: 'center', gap: 6,
                        padding: '8px 18px', borderRadius: 10,
                        background: submitting ? 'rgba(255,255,255,0.05)' : 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                        color: '#fff', fontWeight: 600, fontSize: 13, cursor: submitting ? 'not-allowed' : 'pointer',
                        opacity: submitting ? 0.6 : 1,
                    }}>
                        <Send size={14} /> {submitting ? (t.submitting || 'Submitting...') : (t.submitFeedback || 'Submit Feedback')}
                    </button>
                    <button onClick={handleOptimize} disabled={optimizing} style={{
                        display: 'flex', alignItems: 'center', gap: 6,
                        padding: '8px 18px', borderRadius: 10,
                        background: optimizing ? 'rgba(255,255,255,0.05)' : 'linear-gradient(135deg, #7c3aed, #2563eb)',
                        color: '#fff', fontWeight: 600, fontSize: 13, cursor: optimizing ? 'not-allowed' : 'pointer',
                        opacity: optimizing ? 0.6 : 1,
                    }}>
                        {optimizing
                            ? <><RefreshCw size={14} style={{ animation: 'spin 1s linear infinite' }} /> {t.optimizing || 'Optimizing...'}</>
                            : <><Zap size={14} /> {t.triggerEvolution || 'Optimize Persona'}</>
                        }
                    </button>
                </div>
            </div>

            {/* ── Section 4: Evolution History ────────────────────── */}
            <div style={panelStyle}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                    <Activity size={18} style={{ color: '#60a5fa' }} />
                    <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: '#fff' }}>
                        {t.evolutionRecentHistory || 'Evolution History'}
                    </h3>
                </div>
                {recentHistory.length === 0 ? (
                    <div style={{ color: '#6b7280', fontSize: 13 }}>
                        {t.evolutionNoHistory || 'No evolution history yet. Submit feedback and trigger optimization to start.'}
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {/* Header row */}
                        <div style={{
                            display: 'grid', gridTemplateColumns: '1.2fr 1fr 0.8fr 1fr',
                            gap: 10, fontSize: 11, fontWeight: 600, color: '#8899ac',
                            textTransform: 'uppercase', letterSpacing: 1,
                            padding: '0 14px 4px',
                            borderBottom: '1px solid rgba(255,255,255,0.06)',
                        }}>
                            <div>{t.historyColTimestamp || 'Timestamp'}</div>
                            <div>{t.historyColEvent || 'Event'}</div>
                            <div>{t.historyColStatus || 'Status'}</div>
                            <div>{t.historyColReason || 'Reason'}</div>
                        </div>
                        {recentHistory.map((item, idx) => {
                            const ts = String(item.timestamp || '');
                            return (
                                <div key={`${ts}-${idx}`} style={{
                                    border: '1px solid rgba(255,255,255,0.06)',
                                    borderRadius: 10, padding: '10px 14px',
                                    background: 'rgba(0,0,0,0.2)',
                                    display: 'grid', gridTemplateColumns: '1.2fr 1fr 0.8fr 1fr',
                                    gap: 10, fontSize: 12,
                                }}>
                                    <div style={{ color: '#93c5fd' }}>{ts || '-'}</div>
                                    <div style={{ color: '#e5e7eb' }}>{String(item.event || '-')}</div>
                                    <div style={{ color: item.updated ? '#4ade80' : '#fbbf24' }}>
                                        {item.updated ? (t.updated || 'Updated') : (t.notUpdated || 'Not Updated')}
                                    </div>
                                    <div style={{ color: '#cbd5e1' }}>{String(item.reason || '-')}</div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>

            <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
        </div>
    );
};

export default Personality;
