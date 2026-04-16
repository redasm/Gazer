import React, { useState, useEffect, useCallback } from 'react';
import { Clock, Plus, Trash2, Power, PowerOff, RefreshCw } from 'lucide-react';
import axios from 'axios';
import API_BASE from '../config';
import ToggleSwitch from '../components/ToggleSwitch';

const Cron = ({ t }) => {
    const [jobs, setJobs] = useState([]);
    const [loading, setLoading] = useState(true);
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState({ name: '', cron_expr: '', message: '', enabled: true, one_shot: false });

    const fetchJobs = useCallback(async () => {
        try {
            const res = await axios.get(`${API_BASE}/cron`);
            setJobs(res.data.jobs || []);
        } catch (err) {
            console.error('Failed to load cron jobs', err);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { fetchJobs(); }, [fetchJobs]);

    const handleCreate = async (e) => {
        e.preventDefault();
        if (!form.cron_expr.trim() || !form.message.trim()) return;
        try {
            await axios.post(`${API_BASE}/cron`, form);
            setShowForm(false);
            setForm({ name: '', cron_expr: '', message: '', enabled: true, one_shot: false });
            fetchJobs();
        } catch (err) {
            console.error('Failed to create cron job', err);
        }
    };

    const handleDelete = async (jobId) => {
        try {
            await axios.delete(`${API_BASE}/cron/${jobId}`);
            setJobs(prev => prev.filter(j => j.id !== jobId));
        } catch (err) {
            console.error('Failed to delete cron job', err);
        }
    };

    const handleToggle = async (job) => {
        try {
            await axios.put(`${API_BASE}/cron/${job.id}`, { enabled: !job.enabled });
            fetchJobs();
        } catch (err) {
            console.error('Failed to toggle cron job', err);
        }
    };

    const inputStyle = {
        width: '100%',
        background: 'rgba(8,8,8,0.8)',
        border: '1px solid rgba(255,255,255,0.10)',
        borderRadius: '8px',
        padding: '8px 12px',
        color: 'var(--text-primary)',
        fontSize: '13px',
        outline: 'none',
        fontFamily: 'inherit',
        transition: 'border-color 0.15s',
    };

    const labelStyle = { fontSize: '11px', fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '5px', display: 'block' };

    return (
        <div style={{ maxWidth: '800px' }}>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
                <h2 style={{ fontSize: '18px', fontWeight: 600, color: '#fff', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Clock size={20} style={{ color: 'var(--accent-red)' }} />
                    {t.cronJobs || 'Cron Jobs'}
                </h2>
                <div style={{ display: 'flex', gap: '8px' }}>
                    <button onClick={fetchJobs} className="btn-ghost"><RefreshCw size={14} /></button>
                    <button onClick={() => setShowForm(!showForm)} className="btn-ghost">
                        <Plus size={14} /> {t.addJob || 'Add Job'}
                    </button>
                </div>
            </div>

            {/* Create form */}
            {showForm && (
                <form onSubmit={handleCreate} style={{
                    background: 'rgba(10,10,10,0.6)',
                    border: '1px solid rgba(255,255,255,0.08)',
                    borderRadius: '12px',
                    padding: '16px',
                    marginBottom: '16px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '12px',
                }}>
                    <div>
                        <label style={labelStyle}>{t.jobName || 'Name'}</label>
                        <input style={inputStyle} value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="Daily summary" />
                    </div>
                    <div>
                        <label style={labelStyle}>{t.cronExpr || 'Cron Expression'} <span style={{ color: '#556' }}>(min hour day month weekday)</span></label>
                        <input style={inputStyle} value={form.cron_expr} onChange={e => setForm(f => ({ ...f, cron_expr: e.target.value }))} placeholder="0 9 * * *" required />
                    </div>
                    <div>
                        <label style={labelStyle}>{t.message || 'Message'}</label>
                        <textarea
                            style={{ ...inputStyle, minHeight: '60px', resize: 'vertical' }}
                            value={form.message}
                            onChange={e => setForm(f => ({ ...f, message: e.target.value }))}
                            placeholder="Summarize today's tasks"
                            required
                        />
                    </div>
                    <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
                        <div style={{ width: '100%', fontSize: '12px', color: '#889', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px' }}>
                            <span>{t.oneShot || 'One-shot (run once)'}</span>
                            <ToggleSwitch checked={form.one_shot} onChange={(v) => setForm(f => ({ ...f, one_shot: v }))} />
                        </div>
                    </div>
                    <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
                        <button type="button" onClick={() => setShowForm(false)} className="btn-ghost">{t.cancel || 'Cancel'}</button>
                        <button type="submit" className="btn-primary">{t.create || 'Create'}</button>
                    </div>
                </form>
            )}

            {/* Jobs list */}
            {loading ? (
                <div style={{ color: '#556', textAlign: 'center', padding: '40px' }}>Loading...</div>
            ) : jobs.length === 0 ? (
                <div style={{
                    background: 'rgba(10,10,10,0.6)',
                    border: '1px solid rgba(255,255,255,0.08)',
                    borderRadius: '12px',
                    padding: '40px',
                    textAlign: 'center',
                    color: '#556',
                }}>
                    <Clock size={36} style={{ color: '#334', marginBottom: '12px' }} />
                    <div style={{ fontSize: '14px', color: '#778' }}>{t.noCronJobs || 'No cron jobs configured'}</div>
                    <div style={{ fontSize: '12px', marginTop: '4px' }}>{t.noCronJobsHint || 'Create a job to schedule automated agent tasks.'}</div>
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {jobs.map(job => (
                        <div key={job.id} style={{
                            background: 'rgba(10,10,10,0.6)',
                            border: '1px solid rgba(255,255,255,0.08)',
                            borderRadius: '12px',
                            padding: '14px 16px',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '14px',
                            opacity: job.enabled ? 1 : 0.5,
                        }}>
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                                    <span style={{ fontSize: '14px', fontWeight: 500, color: '#ddd' }}>{job.name || 'Unnamed'}</span>
                                    <code style={{
                                        fontSize: '11px', background: 'rgba(255,255,255,0.06)', padding: '2px 6px',
                                        borderRadius: '4px', color: '#88a',
                                    }}>{job.cron_expr}</code>
                                    {job.one_shot && (
                                        <span style={{ fontSize: '10px', background: 'rgba(234,179,8,0.15)', color: '#eab308', padding: '2px 6px', borderRadius: '4px' }}>one-shot</span>
                                    )}
                                </div>
                                <div style={{ fontSize: '12px', color: '#667', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                    {job.message}
                                </div>
                            </div>
                            <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                                <button
                                    onClick={() => handleToggle(job)}
                                    style={{ color: job.enabled ? '#4ade80' : '#889', padding: '6px', borderRadius: '6px' }}
                                    title={job.enabled ? 'Disable' : 'Enable'}
                                >
                                    {job.enabled ? <Power size={14} /> : <PowerOff size={14} />}
                                </button>
                                <button
                                    onClick={() => handleDelete(job.id)}
                                    style={{ color: '#889', padding: '6px', borderRadius: '6px' }}
                                    title="Delete"
                                >
                                    <Trash2 size={14} />
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

export default Cron;
