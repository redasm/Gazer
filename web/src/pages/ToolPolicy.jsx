import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import API_BASE from '../config';
import NoticeBanner from '../components/NoticeBanner';
import useNotice from '../hooks/useNotice';

const parseLines = (text) =>
    (text || '')
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);

const toCsvLine = (value) => {
    const raw = String(value ?? '');
    if (raw.includes(',') || raw.includes('"') || raw.includes('\n')) {
        return `"${raw.replaceAll('"', '""')}"`;
    }
    return raw;
};

const ToolPolicy = ({ config, setConfig, saveConfig, t }) => {
    const security = config?.security || {};
    const [groupsText, setGroupsText] = useState('{}');
    const [explainTool, setExplainTool] = useState('exec');
    const [simAgentId, setSimAgentId] = useState('');
    const [simTier, setSimTier] = useState('standard');
    const [explainResult, setExplainResult] = useState(null);
    const [simulateResults, setSimulateResults] = useState([]);
    const [reasonFilter, setReasonFilter] = useState('all');
    const [statusFilter, setStatusFilter] = useState('all');
    const [toolQuery, setToolQuery] = useState('');
    const [effectivePolicy, setEffectivePolicy] = useState(null);
    const { notice, showNotice } = useNotice();

    useEffect(() => {
        setGroupsText(JSON.stringify(security.tool_groups || {}, null, 2));
    }, [security.tool_groups]);

    const setSecurity = (patch) => {
        setConfig((prev) => ({
            ...(prev || {}),
            security: {
                ...(prev?.security || {}),
                ...patch,
            },
        }));
    };

    const setList = (key, raw) => {
        setSecurity({ [key]: parseLines(raw) });
    };

    const setGroupsFromText = (raw) => {
        setGroupsText(raw);
        try {
            const parsed = JSON.parse(raw || '{}');
            if (typeof parsed !== 'object' || Array.isArray(parsed) || parsed === null) {
                throw new Error('Invalid object');
            }
            setSecurity({ tool_groups: parsed });
        } catch {
            // keep editing local text only, apply when valid JSON
            showNotice(t.noticeInvalidToolGroupsJson || 'Invalid tool groups JSON', 'error');
        }
    };

    const loadEffectivePolicy = async () => {
        try {
            const query = simAgentId ? `?agent_id=${encodeURIComponent(simAgentId)}` : '';
            const res = await axios.get(`${API_BASE}/policy/effective${query}`);
            setEffectivePolicy(res.data || null);
        } catch {
            setEffectivePolicy(null);
            showNotice(t.noticeLoadEffectivePolicyFailed || 'Failed to load effective policy', 'error');
        }
    };

    useEffect(() => {
        loadEffectivePolicy();
    }, [simAgentId]);

    const explainPolicy = async () => {
        try {
            const res = await axios.post(`${API_BASE}/policy/explain`, {
                tool_name: explainTool,
                agent_id: simAgentId || undefined,
                max_tier: simTier,
            });
            setExplainResult(res.data?.result || null);
            showNotice(t.noticePolicyExplanationLoaded || 'Policy explanation loaded', 'success');
        } catch (err) {
            setExplainResult({
                allowed: false,
                reason: err?.response?.data?.detail || 'request_failed',
            });
            showNotice(t.noticeExplainPolicyFailed || 'Failed to explain policy', 'error');
        }
    };

    const simulatePolicy = async () => {
        try {
            const res = await axios.post(`${API_BASE}/policy/simulate`, {
                agent_id: simAgentId || undefined,
                max_tier: simTier,
            });
            setSimulateResults(res.data?.results || []);
            showNotice(t.noticePolicySimulationCompleted || 'Policy simulation completed', 'success');
        } catch {
            setSimulateResults([]);
            showNotice(t.noticeSimulatePolicyFailed || 'Failed to simulate policy', 'error');
        }
    };

    const handleSaveConfig = async () => {
        try {
            await saveConfig();
            showNotice(t.noticeToolPolicyConfigSaved || 'Tool policy configuration saved', 'success');
        } catch {
            showNotice(t.noticeSaveToolPolicyConfigFailed || 'Failed to save tool policy configuration', 'error');
        }
    };

    const reasonCounts = useMemo(() => {
        const counts = {};
        for (const item of simulateResults) {
            const key = item.reason || 'unknown';
            counts[key] = (counts[key] || 0) + 1;
        }
        return counts;
    }, [simulateResults]);

    const filteredResults = useMemo(() => {
        return simulateResults.filter((item) => {
            if (reasonFilter !== 'all' && item.reason !== reasonFilter) return false;
            if (statusFilter === 'allowed' && !item.allowed) return false;
            if (statusFilter === 'blocked' && item.allowed) return false;
            if (toolQuery.trim() && !String(item.tool || '').toLowerCase().includes(toolQuery.trim().toLowerCase())) {
                return false;
            }
            return true;
        });
    }, [simulateResults, reasonFilter, statusFilter, toolQuery]);

    const exportCsv = () => {
        const rows = [
            ['tool', 'allowed', 'reason', 'provider', 'tier'],
            ...filteredResults.map((item) => [
                item.tool,
                item.allowed,
                item.reason,
                item.provider || '',
                item.tier || '',
            ]),
        ];
        const csv = rows.map((row) => row.map(toCsvLine).join(',')).join('\n');
        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', 'policy-simulate.csv');
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        window.URL.revokeObjectURL(url);
    };

    if (!config) {
        return (
            <div style={{ color: '#889', padding: 16 }}>
                {t.loadingConfig || 'Loading config...'}
            </div>
        );
    }

    return (
        <div style={{ maxWidth: 980 }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, color: '#fff', marginBottom: 6 }}>
                {t.toolPolicy || 'Tool Policy'}
            </h2>
            <p style={{ color: '#8aa0bd', marginTop: 0, marginBottom: 18 }}>
                {t.toolPolicyDesc || 'Manage tool exposure and execution policy outside general settings.'}
            </p>
            <NoticeBanner notice={notice} />

            <div className="card" style={{ padding: 16, marginBottom: 14 }}>
                <label className="label">{t.toolMaxTier || 'Tool Max Tier'}</label>
                <select
                    className="input"
                    value={security.tool_max_tier || 'standard'}
                    onChange={(e) => setSecurity({ tool_max_tier: e.target.value })}
                >
                    <option value="safe">safe</option>
                    <option value="standard">standard</option>
                    <option value="privileged">privileged</option>
                </select>
            </div>

            <div className="card" style={{ padding: 16, marginBottom: 14 }}>
                <label className="label">{t.toolAllowlist || 'Tool Allowlist'}</label>
                <textarea
                    className="input"
                    rows={5}
                    value={(security.tool_allowlist || []).join('\n')}
                    onChange={(e) => setList('tool_allowlist', e.target.value)}
                    placeholder="one_tool_name_per_line"
                />
            </div>

            <div className="card" style={{ padding: 16, marginBottom: 14 }}>
                <label className="label">{t.toolDenylist || 'Tool Denylist'}</label>
                <textarea
                    className="input"
                    rows={5}
                    value={(security.tool_denylist || []).join('\n')}
                    onChange={(e) => setList('tool_denylist', e.target.value)}
                    placeholder="one_tool_name_per_line"
                />
            </div>

            <div className="card" style={{ padding: 16, marginBottom: 18 }}>
                <label className="label">{t.toolGroups || 'Tool Groups (JSON)'}</label>
                <textarea
                    className="input"
                    rows={12}
                    value={groupsText}
                    onChange={(e) => setGroupsFromText(e.target.value)}
                    placeholder='{"coding":["read_file","write_file"]}'
                />
                <div style={{ fontSize: 12, color: '#7f8ea3', marginTop: 8 }}>
                    {t.toolGroupsHint || 'Use JSON object format: group_name -> tool names array.'}
                </div>
            </div>

            <div className="card" style={{ padding: 16, marginBottom: 18 }}>
                <h3 style={{ fontSize: 14, color: '#cdd9e8', marginTop: 0 }}>
                    {t.policySimulator || 'Policy Simulator'}
                </h3>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 180px 120px 120px', gap: 8 }}>
                    <input
                        className="input"
                        value={explainTool}
                        onChange={(e) => setExplainTool(e.target.value)}
                        placeholder="tool name"
                    />
                    <input
                        className="input"
                        value={simAgentId}
                        onChange={(e) => setSimAgentId(e.target.value)}
                        placeholder="agent id (optional)"
                    />
                    <select className="input" value={simTier} onChange={(e) => setSimTier(e.target.value)}>
                        <option value="safe">safe</option>
                        <option value="standard">standard</option>
                        <option value="privileged">privileged</option>
                    </select>
                    <button className="btn-ghost" onClick={explainPolicy}>
                        {t.explain || 'Explain'}
                    </button>
                    <button className="btn-ghost" onClick={simulatePolicy}>
                        {t.simulate || 'Simulate'}
                    </button>
                </div>

                {explainResult && (
                    <div style={{ marginTop: 12, fontSize: 13, color: explainResult.allowed ? '#4ade80' : '#fca5a5' }}>
                        {t.result || 'Result'}: {explainResult.allowed ? (t.allowed || 'allowed') : (t.blocked || 'blocked')} ({explainResult.reason})
                    </div>
                )}

                {simulateResults.length > 0 && (
                    <div style={{ marginTop: 12 }}>
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                            <button
                                className="btn-ghost"
                                onClick={() => setReasonFilter('all')}
                                style={{ borderColor: reasonFilter === 'all' ? 'rgba(96,165,250,0.6)' : undefined }}
                            >
                                {t.all || 'all'} ({simulateResults.length})
                            </button>
                            {Object.entries(reasonCounts).map(([reason, count]) => (
                                <button
                                    key={reason}
                                    className="btn-ghost"
                                    onClick={() => setReasonFilter(reason)}
                                    style={{ borderColor: reasonFilter === reason ? 'rgba(96,165,250,0.6)' : undefined }}
                                >
                                    {reason} ({count})
                                </button>
                            ))}
                        </div>

                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 160px 120px', gap: 8, marginBottom: 8 }}>
                            <input
                                className="input"
                                value={toolQuery}
                                onChange={(e) => setToolQuery(e.target.value)}
                                placeholder={t.searchTool || 'search tool...'}
                            />
                            <select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                                <option value="all">{t.all || 'all'}</option>
                                <option value="allowed">{t.allowed || 'allowed'}</option>
                                <option value="blocked">{t.blocked || 'blocked'}</option>
                            </select>
                            <button className="btn-ghost" onClick={exportCsv}>
                                {t.exportCsv || 'Export CSV'}
                            </button>
                        </div>

                        <div style={{ maxHeight: 260, overflowY: 'auto', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                            {filteredResults.slice(0, 200).map((item) => (
                                <div key={item.tool} style={{ display: 'grid', gridTemplateColumns: '1.2fr .7fr 1fr', gap: 8, fontSize: 12, padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                                    <span style={{ color: '#cdd9e8' }}>{item.tool}</span>
                                    <span style={{ color: item.allowed ? '#4ade80' : '#fca5a5' }}>
                                        {item.allowed ? (t.allowed || 'allowed') : (t.blocked || 'blocked')}
                                    </span>
                                    <span style={{ color: '#9fb3c8' }}>{item.reason}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>

            <div className="card" style={{ padding: 16, marginBottom: 18 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <h3 style={{ fontSize: 14, color: '#cdd9e8', margin: 0 }}>
                        {t.effectivePolicy || 'Effective Policy'}
                    </h3>
                    <button className="btn-ghost" onClick={loadEffectivePolicy}>
                        {t.refresh || 'Refresh'}
                    </button>
                </div>
                {!effectivePolicy ? (
                    <div style={{ color: '#889', fontSize: 12 }}>
                        {t.noData || 'No data'}
                    </div>
                ) : (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, fontSize: 12 }}>
                        <div style={{ color: '#9fb3c8' }}>
                            max_tier: <span style={{ color: '#dbeafe' }}>{effectivePolicy.global?.max_tier || '-'}</span>
                        </div>
                        <div style={{ color: '#9fb3c8' }}>
                            groups: <span style={{ color: '#dbeafe' }}>{effectivePolicy.global?.group_count || 0}</span>
                        </div>
                        <div style={{ color: '#9fb3c8' }}>
                            global allow_names: <span style={{ color: '#dbeafe' }}>{(effectivePolicy.global?.policy?.allow_names || []).length}</span>
                        </div>
                        <div style={{ color: '#9fb3c8' }}>
                            global deny_names: <span style={{ color: '#dbeafe' }}>{(effectivePolicy.global?.policy?.deny_names || []).length}</span>
                        </div>
                        {effectivePolicy.agent && (
                            <>
                                <div style={{ color: '#9fb3c8' }}>
                                    agent: <span style={{ color: '#dbeafe' }}>{effectivePolicy.agent.id}</span>
                                </div>
                                <div style={{ color: '#9fb3c8' }}>
                                    agent allow_names: <span style={{ color: '#dbeafe' }}>{(effectivePolicy.agent?.effective_policy?.allow_names || []).length}</span>
                                </div>
                            </>
                        )}
                    </div>
                )}
            </div>

            <div style={{ display: 'flex', gap: 10 }}>
                <button className="btn-primary" onClick={handleSaveConfig}>
                    {t.saveConfig || 'Save Config'}
                </button>
            </div>
        </div>
    );
};

export default ToolPolicy;
