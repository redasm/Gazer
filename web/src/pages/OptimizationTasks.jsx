import React, { useEffect, useState } from 'react';
import axios from 'axios';
import API_BASE from '../config';
import NoticeBanner from '../components/NoticeBanner';
import useNotice from '../hooks/useNotice';

const OptimizationTasks = ({ t }) => {
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(false);
    const [statusFilter, setStatusFilter] = useState('');
    const [datasetFilter, setDatasetFilter] = useState('');
    const [editing, setEditing] = useState({});
    const { notice, showNotice } = useNotice();

    const load = async () => {
        setLoading(true);
        try {
            const params = {};
            if (statusFilter) params.status = statusFilter;
            if (datasetFilter) params.dataset_id = datasetFilter;
            const res = await axios.get(`${API_BASE}/debug/optimization-tasks`, { params });
            const list = res.data?.items || [];
            setItems(list);
        } catch {
            setItems([]);
            showNotice(t.noticeLoadOptimizationTasksFailed || 'Failed to load optimization tasks', 'error');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
    }, [statusFilter, datasetFilter]);

    const updateStatus = async (taskId) => {
        const current = editing[taskId] || {};
        const status = current.status || 'open';
        const note = current.note || '';
        try {
            await axios.post(`${API_BASE}/debug/optimization-tasks/${encodeURIComponent(taskId)}/status`, {
                status,
                note,
            });
            await load();
            showNotice(t.noticeOptimizationTaskUpdated || 'Optimization task updated', 'success');
        } catch {
            showNotice(t.noticeUpdateOptimizationTaskFailed || 'Failed to update optimization task', 'error');
        }
    };

    return (
        <div style={{ maxWidth: 1200 }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, color: '#fff', marginBottom: 6 }}>
                {t.optimizationTasks || 'Optimization Tasks'}
            </h2>
            <p style={{ color: '#6b7280', marginTop: 0, marginBottom: 16 }}>
                {t.optimizationTasksDesc || 'Track benchmark gate failure tasks and close optimization loops.'}
            </p>
            <NoticeBanner notice={notice} />

            <div className="card" style={{ padding: 16, marginBottom: 12 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(180px, 1fr))', gap: 12 }}>
                    <div>
                        <label className="label">{t.status || 'Status'}</label>
                        <select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                            <option value="">{t.all || 'All'}</option>
                            <option value="open">open</option>
                            <option value="resolved">resolved</option>
                            <option value="dismissed">dismissed</option>
                        </select>
                    </div>
                    <div>
                        <label className="label">dataset_id</label>
                        <input className="input" value={datasetFilter} onChange={(e) => setDatasetFilter(e.target.value)} />
                    </div>
                    <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                        <button className="btn-primary" onClick={load} disabled={loading}>
                            {t.refresh || 'Refresh'}
                        </button>
                    </div>
                </div>
            </div>

            <div className="card" style={{ padding: 16 }}>
                {items.length === 0 ? (
                    <div style={{ color: '#889' }}>{t.noData || 'No data'}</div>
                ) : (
                    items.map((item) => {
                        const taskId = item.task_id;
                        const current = editing[taskId] || { status: item.status || 'open', note: item.note || '' };
                        return (
                            <div
                                key={taskId}
                                style={{
                                    borderBottom: '1px solid rgba(255,255,255,0.06)',
                                    padding: '10px 0',
                                    display: 'grid',
                                    gridTemplateColumns: '2fr 1.2fr 1fr 1fr 1.6fr',
                                    gap: 10,
                                    alignItems: 'center',
                                    fontSize: 12,
                                }}
                            >
                                <div style={{ color: '#e2e8f0' }}>
                                    <div>{taskId}</div>
                                    <div style={{ color: '#7f8ea3', marginTop: 4 }}>
                                        {item.dataset_id} / streak={item.fail_streak}
                                    </div>
                                </div>
                                <div style={{ color: '#6b7280' }}>{item.priority || '-'}</div>
                                <div>
                                    <select
                                        className="input"
                                        value={current.status}
                                        onChange={(e) => setEditing((prev) => ({ ...prev, [taskId]: { ...current, status: e.target.value } }))}
                                    >
                                        <option value="open">open</option>
                                        <option value="resolved">resolved</option>
                                        <option value="dismissed">dismissed</option>
                                    </select>
                                </div>
                                <div style={{ color: '#6b7280' }}>
                                    {item.created_at ? new Date(item.created_at * 1000).toLocaleString() : '-'}
                                </div>
                                <div style={{ display: 'flex', gap: 8 }}>
                                    <input
                                        className="input"
                                        placeholder="note"
                                        value={current.note}
                                        onChange={(e) => setEditing((prev) => ({ ...prev, [taskId]: { ...current, note: e.target.value } }))}
                                    />
                                    <button className="btn-secondary" onClick={() => updateStatus(taskId)}>
                                        {t.save || 'Save'}
                                    </button>
                                </div>
                            </div>
                        );
                    })
                )}
            </div>
        </div>
    );
};

export default OptimizationTasks;
