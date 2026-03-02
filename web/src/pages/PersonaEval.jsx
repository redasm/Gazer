import React, { useEffect, useState } from 'react';
import axios from 'axios';
import API_BASE from '../config';
import NoticeBanner from '../components/NoticeBanner';
import useNotice from '../hooks/useNotice';

const PersonaEval = ({ t }) => {
    const [datasets, setDatasets] = useState([]);
    const [datasetName, setDatasetName] = useState('core_persona');
    const [selectedDataset, setSelectedDataset] = useState('');
    const [outputsText, setOutputsText] = useState(
        JSON.stringify(
            {
                tone_warm: 'Good morning, I am Gazer.',
                identity_consistency: 'I am Gazer, your AI companion.',
                safety_consistency: "I can't do that unsafe action, here is a safer way.",
            },
            null,
            2,
        ),
    );
    const [lastReport, setLastReport] = useState(null);
    const [mentalYaml, setMentalYaml] = useState('');
    const { notice, showNotice } = useNotice();

    const load = async () => {
        try {
            const [datasetsRes, mentalRes] = await Promise.all([
                axios.get(`${API_BASE}/debug/persona-eval/datasets`),
                axios.get(`${API_BASE}/debug/persona/mental-process`),
            ]);
            const items = datasetsRes.data?.items || [];
            setDatasets(items);
            if (!selectedDataset && items.length > 0) {
                setSelectedDataset(items[0].id);
            }
            setMentalYaml(mentalRes.data?.yaml || '');
        } catch {
            setDatasets([]);
            showNotice(t.noticeLoadPersonaDatasetsFailed || 'Failed to load persona datasets', 'error');
        }
    };

    useEffect(() => {
        load();
    }, []);

    const build = async () => {
        try {
            await axios.post(`${API_BASE}/debug/persona-eval/datasets/build`, { name: datasetName });
            await load();
            showNotice(t.noticePersonaDatasetCreated || 'Persona dataset created', 'success');
        } catch {
            showNotice(t.noticeBuildPersonaDatasetFailed || 'Failed to build persona dataset', 'error');
        }
    };

    const run = async () => {
        if (!selectedDataset) return;
        let outputs = {};
        try {
            const parsed = JSON.parse(outputsText || '{}');
            if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                outputs = parsed;
            } else {
                showNotice(t.noticeInvalidOutputsJson || 'Invalid outputs JSON', 'error');
                return;
            }
        } catch {
            showNotice(t.noticeInvalidOutputsJson || 'Invalid outputs JSON', 'error');
            return;
        }
        try {
            const res = await axios.post(`${API_BASE}/debug/persona-eval/datasets/${encodeURIComponent(selectedDataset)}/run`, {
                outputs,
            });
            setLastReport(res.data?.report || null);
            showNotice(t.noticePersonaEvaluationCompleted || 'Persona evaluation completed', 'success');
        } catch {
            showNotice(t.noticeRunPersonaEvaluationFailed || 'Failed to run persona evaluation', 'error');
        }
    };

    const saveMentalProcess = async () => {
        if (!mentalYaml.trim()) return;
        try {
            await axios.post(`${API_BASE}/debug/persona/mental-process`, { yaml: mentalYaml });
            showNotice('MentalProcess YAML 已保存', 'success');
            await load();
        } catch (err) {
            const detail = err?.response?.data?.detail;
            showNotice(detail || '保存 MentalProcess YAML 失败', 'error');
        }
    };

    return (
        <div style={{ maxWidth: 1100 }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, color: '#fff', marginBottom: 6 }}>
                {t.personaEval || 'Persona Eval'}
            </h2>
            <p style={{ color: '#8aa0bd', marginTop: 0, marginBottom: 16 }}>
                {t.personaEvalDesc || 'Build persona consistency datasets and run automatic scoring.'}
            </p>

            <NoticeBanner notice={notice} />

            <div className="card" style={{ padding: 16, marginBottom: 12 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1.5fr auto auto', gap: 10 }}>
                    <input className="input" value={datasetName} onChange={(e) => setDatasetName(e.target.value)} />
                    <button className="btn-primary" onClick={build}>{t.create || 'Create'}</button>
                    <button className="btn-secondary" onClick={load}>{t.refresh || 'Refresh'}</button>
                </div>
            </div>

            <div className="card" style={{ padding: 16, marginBottom: 12 }}>
                <label className="label">dataset</label>
                <select className="input" value={selectedDataset} onChange={(e) => setSelectedDataset(e.target.value)}>
                    <option value="">--</option>
                    {datasets.map((item) => (
                        <option key={item.id} value={item.id}>{item.name} ({item.id})</option>
                    ))}
                </select>
                <div style={{ marginTop: 12 }}>
                    <label className="label">outputs (JSON: sample_id {'->'} text)</label>
                    <textarea
                        className="input"
                        rows={9}
                        value={outputsText}
                        onChange={(e) => setOutputsText(e.target.value)}
                        style={{ fontFamily: 'monospace' }}
                    />
                </div>
                <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
                    <button className="btn-primary" onClick={run}>{t.run || 'Run'}</button>
                </div>
            </div>

            <div className="card" style={{ padding: 16, marginBottom: 12 }}>
                <div style={{ color: '#dbeafe', marginBottom: 8 }}>MentalProcess YAML</div>
                <textarea
                    className="input"
                    rows={12}
                    value={mentalYaml}
                    onChange={(e) => setMentalYaml(e.target.value)}
                    style={{ fontFamily: 'monospace' }}
                />
                <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
                    <button className="btn-primary" onClick={saveMentalProcess}>保存 YAML</button>
                </div>
            </div>

            {lastReport && (
                <div className="card" style={{ padding: 16 }}>
                    <div style={{ color: '#dbeafe', marginBottom: 8 }}>
                        score={lastReport.consistency_score} / auto_passed={String(lastReport.auto_passed)}
                    </div>
                    <pre style={{ margin: 0, color: '#9fb3c8', fontSize: 12, whiteSpace: 'pre-wrap' }}>
                        {JSON.stringify(lastReport, null, 2)}
                    </pre>
                </div>
            )}
        </div>
    );
};

export default PersonaEval;
