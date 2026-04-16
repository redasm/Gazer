import React, { useEffect, useState } from 'react';
import axios from 'axios';
import API_BASE from '../config';
import NoticeBanner from '../components/NoticeBanner';
import useNotice from '../hooks/useNotice';

const TrainerJobs = ({ t }) => {
    const jobsGridTemplate = 'minmax(280px, 2fr) minmax(220px, 1.2fr) minmax(140px, 1fr) minmax(120px, 1fr) 92px 92px';
    const [jobs, setJobs] = useState([]);
    const [experiments, setExperiments] = useState([]);
    const [status, setStatus] = useState('');
    const [datasetId, setDatasetId] = useState('');
    const [experimentName, setExperimentName] = useState('');
    const [selectedExperiment, setSelectedExperiment] = useState('');
    const [loading, setLoading] = useState(false);
    const [selected, setSelected] = useState(null);
    const [detailOpen, setDetailOpen] = useState(false);
    const [detailLoading, setDetailLoading] = useState(false);
    const { notice, showNotice } = useNotice();

    const load = async () => {
        setLoading(true);
        try {
            const params = {};
            if (status) params.status = status;
            const [jobsRes, experimentsRes] = await Promise.all([
                axios.get(`${API_BASE}/debug/training-jobs`, { params }),
                axios.get(`${API_BASE}/debug/training-experiments`, { params: { limit: 50 } }),
            ]);
            setJobs(jobsRes.data?.items || []);
            const experimentItems = experimentsRes.data?.items || [];
            setExperiments(experimentItems);
            if (!selectedExperiment && experimentItems.length > 0) {
                setSelectedExperiment(experimentItems[0].experiment_id);
            }
        } catch {
            setJobs([]);
            setExperiments([]);
            showNotice(t.noticeLoadTrainingJobsFailed || 'Failed to load training jobs', 'error');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
    }, [status]);

    const createJob = async () => {
        if (!datasetId.trim()) return;
        try {
            await axios.post(`${API_BASE}/debug/training-jobs`, { dataset_id: datasetId.trim() });
            setDatasetId('');
            await load();
            showNotice(t.noticeTrainingJobCreated || 'Training job created', 'success');
        } catch {
            showNotice(t.noticeCreateTrainingJobFailed || 'Failed to create training job', 'error');
        }
    };

    const runJob = async (jobId) => {
        try {
            await axios.post(`${API_BASE}/debug/training-jobs/${encodeURIComponent(jobId)}/run`, {});
            await load();
            if (detailOpen && selected?.job_id === jobId) {
                await openDetail(jobId);
            }
            showNotice(t.noticeTrainingJobExecuted || 'Training job executed', 'success');
        } catch {
            showNotice(t.noticeRunTrainingJobFailed || 'Failed to run training job', 'error');
        }
    };

    const createExperiment = async () => {
        if (!datasetId.trim()) return;
        try {
            await axios.post(`${API_BASE}/debug/training-experiments`, {
                dataset_id: datasetId.trim(),
                name: experimentName.trim() || `${datasetId.trim()}_experiment`,
            });
            setExperimentName('');
            await load();
            showNotice('Training experiment created', 'success');
        } catch {
            showNotice('Failed to create training experiment', 'error');
        }
    };

    const runExperiment = async () => {
        if (!selectedExperiment) return;
        try {
            const res = await axios.post(
                `${API_BASE}/debug/training-experiments/${encodeURIComponent(selectedExperiment)}/run`,
                {},
            );
            const jobId = res.data?.job?.job_id;
            await load();
            if (jobId) {
                await openDetail(jobId);
            }
            showNotice('Training experiment executed', 'success');
        } catch {
            showNotice('Failed to run training experiment', 'error');
        }
    };

    const openDetail = async (jobId) => {
        setDetailOpen(true);
        setDetailLoading(true);
        try {
            const res = await axios.get(`${API_BASE}/debug/training-jobs/${encodeURIComponent(jobId)}`);
            setSelected(res.data?.job || null);
        } catch {
            setSelected(null);
            setDetailOpen(false);
            showNotice(t.noticeLoadTrainingJobDetailFailed || 'Failed to load training job detail', 'error');
        } finally {
            setDetailLoading(false);
        }
    };

    return (
        <div style={{ maxWidth: 1200 }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, color: '#fff', marginBottom: 6 }}>
                {t.trainerJobs || 'Trainer Jobs'}
            </h2>
            <p style={{ color: '#8aa0bd', marginTop: 0, marginBottom: 16 }}>
                {t.trainerJobsDesc || 'Manage lightning-lite training jobs and inspect generated prompt/policy patches.'}
            </p>

            <NoticeBanner notice={notice} />

            <div className="card" style={{ padding: 16, marginBottom: 12 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto auto', gap: 10 }}>
                    <input
                        className="input"
                        placeholder="dataset_id"
                        value={datasetId}
                        onChange={(e) => setDatasetId(e.target.value)}
                    />
                    <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
                        <option value="">{t.all || 'All'}</option>
                        <option value="pending">pending</option>
                        <option value="running">running</option>
                        <option value="completed">completed</option>
                    </select>
                    <button className="btn-primary" onClick={createJob}>{t.create || 'Create'}</button>
                    <button className="btn-secondary" onClick={load} disabled={loading}>{t.refresh || 'Refresh'}</button>
                </div>
            </div>

            <div className="card" style={{ padding: 16, marginBottom: 12 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr auto auto', gap: 10 }}>
                    <input
                        className="input"
                        placeholder="experiment_name"
                        value={experimentName}
                        onChange={(e) => setExperimentName(e.target.value)}
                    />
                    <select className="input" value={selectedExperiment} onChange={(e) => setSelectedExperiment(e.target.value)}>
                        <option value="">-- experiment --</option>
                        {experiments.map((item) => (
                            <option key={item.experiment_id} value={item.experiment_id}>
                                {item.name} ({item.experiment_id})
                            </option>
                        ))}
                    </select>
                    <div style={{ color: '#8aa0bd', alignSelf: 'center', fontSize: 12 }}>
                        {experiments.length} experiments
                    </div>
                    <button className="btn-secondary" onClick={createExperiment}>Create Exp</button>
                    <button className="btn-primary" onClick={runExperiment} disabled={!selectedExperiment}>Run Exp</button>
                </div>
            </div>

            <div className="card" style={{ padding: 16 }}>
                <div
                    style={{
                        display: 'grid',
                        gridTemplateColumns: jobsGridTemplate,
                        gap: 10,
                        alignItems: 'center',
                        padding: '0 0 8px 0',
                        marginBottom: 4,
                        borderBottom: '1px solid rgba(255,255,255,0.12)',
                        fontSize: 12,
                        fontWeight: 600,
                        color: '#b7c9e6',
                        letterSpacing: '0.02em',
                    }}
                >
                    <div>{t.trainerJobsColJobId || 'Job ID'}</div>
                    <div>{t.trainerJobsColDatasetId || 'Dataset ID'}</div>
                    <div>{t.trainerJobsColSource || 'Source'}</div>
                    <div>{t.trainerJobsColStatus || 'Status'}</div>
                    <div style={{ justifySelf: 'start' }}>{t.trainerJobsColDetail || 'Detail'}</div>
                    <div style={{ justifySelf: 'start' }}>{t.trainerJobsColRun || 'Run'}</div>
                </div>
                {jobs.length === 0 ? (
                    <div style={{ color: '#889' }}>{t.noData || 'No data'}</div>
                ) : (
                    jobs.map((job) => (
                        <div
                            key={job.job_id}
                            style={{
                                display: 'grid',
                                gridTemplateColumns: jobsGridTemplate,
                                gap: 10,
                                alignItems: 'center',
                                padding: '8px 0',
                                borderBottom: '1px solid rgba(255,255,255,0.06)',
                                fontSize: 12,
                            }}
                        >
                            <div style={{ color: '#dbeafe' }}>{job.job_id}</div>
                            <div style={{ color: '#9fb3c8' }}>{job.dataset_id}</div>
                            <div style={{ color: '#9fb3c8' }}>{job.source}</div>
                            <div style={{ color: '#9fb3c8' }}>{job.status}</div>
                            <button
                                className="btn-secondary"
                                style={{ width: '100%', justifySelf: 'stretch' }}
                                onClick={() => openDetail(job.job_id)}
                            >
                                {t.detail || 'Detail'}
                            </button>
                            <button
                                className="btn-primary"
                                style={{ width: '100%', justifySelf: 'stretch' }}
                                disabled={job.status === 'completed'}
                                onClick={() => runJob(job.job_id)}
                            >
                                {t.run || 'Run'}
                            </button>
                        </div>
                    ))
                )}
            </div>

            {detailOpen && (
                <div
                    style={{
                        position: 'fixed',
                        inset: 0,
                        background: 'rgba(2, 6, 23, 0.86)',
                        backdropFilter: 'blur(3px)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        zIndex: 1200,
                        padding: 20,
                    }}
                    onClick={() => setDetailOpen(false)}
                >
                    <div
                        className="card"
                        style={{
                            width: 'min(1200px, 94vw)',
                            maxHeight: '84vh',
                            overflow: 'hidden',
                            background: 'rgba(7, 16, 40, 0.96)',
                            border: '1px solid rgba(120, 170, 255, 0.22)',
                            boxShadow: '0 20px 60px rgba(0,0,0,0.55)',
                            padding: 16,
                            display: 'flex',
                            flexDirection: 'column',
                            gap: 10,
                        }}
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                            <div style={{ color: '#dbeafe', fontSize: 14, fontWeight: 600 }}>
                                {selected?.job_id || (t.detail || 'Detail')}
                            </div>
                            <button className="btn-secondary" onClick={() => setDetailOpen(false)}>
                                {t.close || t.cancel || 'Close'}
                            </button>
                        </div>
                        <div
                            style={{
                                border: '1px solid rgba(255,255,255,0.12)',
                                borderRadius: 10,
                                background: 'rgba(3, 8, 24, 0.92)',
                                padding: 12,
                                overflow: 'auto',
                                minHeight: 220,
                            }}
                        >
                            {detailLoading ? (
                                <div style={{ color: '#9fb3c8', fontSize: 12 }}>
                                    {t.loading || 'Loading...'}
                                </div>
                            ) : (
                                <pre
                                    style={{
                                        margin: 0,
                                        whiteSpace: 'pre-wrap',
                                        wordBreak: 'break-word',
                                        color: '#dbeafe',
                                        fontSize: 12,
                                    }}
                                >
                                    {JSON.stringify((selected && (selected.output || selected)) || {}, null, 2)}
                                </pre>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default TrainerJobs;
