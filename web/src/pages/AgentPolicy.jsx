import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import API_BASE from '../config';

const parseLines = (text) =>
    (text || '')
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);

const listToText = (value) => (Array.isArray(value) ? value.join('\n') : '');
const overlap = (arrA, arrB) => {
    const a = new Set(Array.isArray(arrA) ? arrA : []);
    const b = new Set(Array.isArray(arrB) ? arrB : []);
    return [...a].filter((item) => b.has(item));
};

const AgentPolicy = ({ config, setConfig, saveConfig, t }) => {
    const agents = config?.agents?.list || [];
    const [selectedIndex, setSelectedIndex] = useState(0);
    const [effectivePolicy, setEffectivePolicy] = useState(null);

    useEffect(() => {
        if (agents.length === 0) {
            setSelectedIndex(0);
            return;
        }
        if (selectedIndex > agents.length - 1) {
            setSelectedIndex(agents.length - 1);
        }
    }, [agents.length, selectedIndex]);

    const setAgents = (nextAgents) => {
        setConfig((prev) => ({
            ...(prev || {}),
            agents: {
                ...(prev?.agents || {}),
                list: nextAgents,
            },
        }));
    };

    const updateAgent = (index, patch) => {
        const next = agents.map((agent, i) => (i === index ? { ...agent, ...patch } : agent));
        setAgents(next);
    };

    const updateToolPolicy = (index, patch) => {
        const agent = agents[index] || {};
        const current = agent.tool_policy || {};
        updateAgent(index, { tool_policy: { ...current, ...patch } });
    };

    const addAgent = () => {
        setAgents([
            ...agents,
            {
                id: `agent_${agents.length + 1}`,
                name: `Agent ${agents.length + 1}`,
                workspace: '.',
                model: '',
                tool_policy: {
                    max_tier: 'standard',
                    allow_names: [],
                    deny_names: [],
                    allow_groups: [],
                    deny_groups: [],
                    allow_providers: [],
                    deny_providers: [],
                },
            },
        ]);
    };

    const removeAgent = (index) => {
        const nextIndex = Math.max(0, Math.min(selectedIndex, agents.length - 2));
        setAgents(agents.filter((_, i) => i !== index));
        setSelectedIndex(nextIndex);
    };

    const selectedAgent = useMemo(
        () => (agents.length > 0 ? agents[selectedIndex] : null),
        [agents, selectedIndex]
    );

    const selectedPolicy = selectedAgent?.tool_policy || {};
    const nameConflicts = overlap(selectedPolicy.allow_names, selectedPolicy.deny_names);
    const groupConflicts = overlap(selectedPolicy.allow_groups, selectedPolicy.deny_groups);
    const providerConflicts = overlap(selectedPolicy.allow_providers, selectedPolicy.deny_providers);

    const loadEffectiveForSelected = async () => {
        if (!selectedAgent?.id) {
            setEffectivePolicy(null);
            return;
        }
        try {
            const res = await axios.get(`${API_BASE}/policy/effective?agent_id=${encodeURIComponent(selectedAgent.id)}`);
            setEffectivePolicy(res.data?.agent?.effective_policy || null);
        } catch {
            setEffectivePolicy(null);
        }
    };

    useEffect(() => {
        loadEffectiveForSelected();
    }, [selectedAgent?.id, selectedAgent?.tool_policy]);

    if (!config) {
        return (
            <div style={{ color: '#889', padding: 16 }}>
                {t.loadingConfig || 'Loading config...'}
            </div>
        );
    }

    return (
        <div style={{ maxWidth: 1180 }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, color: '#fff', marginBottom: 6 }}>
                {t.agentPolicy || 'Agent Policy'}
            </h2>
            <p style={{ color: '#8aa0bd', marginTop: 0, marginBottom: 18 }}>
                {t.agentPolicyDesc || 'Configure per-agent tool policy in one dedicated place.'}
            </p>

            <div style={{ display: 'flex', gap: 10, marginBottom: 12 }}>
                <button className="btn-ghost" onClick={addAgent}>
                    {t.addAgent || '+ Add Agent'}
                </button>
                <button className="btn-primary" onClick={saveConfig}>
                    {t.saveConfig || 'Save Config'}
                </button>
            </div>

            {agents.length === 0 ? (
                <div className="card" style={{ padding: 16, color: '#889' }}>
                    {t.noAgentsConfigured || 'No agents configured.'}
                </div>
            ) : (
                <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 14 }}>
                    <div className="card" style={{ padding: 10, minHeight: 560 }}>
                        <div style={{ fontSize: 12, color: '#7f8ea3', marginBottom: 8 }}>
                            {t.agentPolicyList || 'Agents'}
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                            {agents.map((agent, index) => {
                                const active = index === selectedIndex;
                                return (
                                    <button
                                        key={`${agent.id || 'agent'}-${index}`}
                                        className="btn-ghost"
                                        onClick={() => setSelectedIndex(index)}
                                        style={{
                                            justifyContent: 'space-between',
                                            textAlign: 'left',
                                            padding: '10px 12px',
                                            borderColor: active ? 'rgba(96,165,250,0.55)' : 'rgba(255,255,255,0.08)',
                                            background: active ? 'rgba(59,130,246,0.16)' : 'rgba(255,255,255,0.04)',
                                            color: active ? '#dbeafe' : '#cbd5e1',
                                        }}
                                    >
                                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {agent.name || `Agent ${index + 1}`}
                                        </span>
                                        <span style={{ fontSize: 11, color: '#8aa0bd', marginLeft: 8 }}>
                                            {agent.id || '-'}
                                        </span>
                                    </button>
                                );
                            })}
                        </div>
                    </div>

                    {selectedAgent && (
                        <div className="card" style={{ padding: 16 }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
                                <div style={{ fontWeight: 600, color: '#fff' }}>
                                    {selectedAgent.name || `Agent ${selectedIndex + 1}`}
                                </div>
                                <button className="btn-danger" onClick={() => removeAgent(selectedIndex)}>
                                    {t.delete || 'Delete'}
                                </button>
                            </div>

                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 12 }}>
                                <input
                                    className="input"
                                    value={selectedAgent.id || ''}
                                    onChange={(e) => updateAgent(selectedIndex, { id: e.target.value })}
                                    placeholder="agent id"
                                />
                                <input
                                    className="input"
                                    value={selectedAgent.name || ''}
                                    onChange={(e) => updateAgent(selectedIndex, { name: e.target.value })}
                                    placeholder="agent name"
                                />
                                <input
                                    className="input"
                                    value={selectedAgent.workspace || ''}
                                    onChange={(e) => updateAgent(selectedIndex, { workspace: e.target.value })}
                                    placeholder="workspace"
                                />
                                <input
                                    className="input"
                                    value={selectedAgent.model || ''}
                                    onChange={(e) => updateAgent(selectedIndex, { model: e.target.value })}
                                    placeholder="model (optional)"
                                />
                            </div>

                            <div style={{ marginTop: 12 }}>
                                <label className="label">{t.toolMaxTier || 'Tool Max Tier'}</label>
                                <select
                                    className="input"
                                    value={selectedPolicy.max_tier || 'standard'}
                                    onChange={(e) => updateToolPolicy(selectedIndex, { max_tier: e.target.value })}
                                >
                                    <option value="safe">safe</option>
                                    <option value="standard">standard</option>
                                    <option value="privileged">privileged</option>
                                </select>
                            </div>

                            {(nameConflicts.length > 0 || groupConflicts.length > 0 || providerConflicts.length > 0) && (
                                <div className="card" style={{ padding: 10, marginTop: 10, borderColor: 'rgba(251,191,36,0.4)', background: 'rgba(251,191,36,0.08)' }}>
                                    <div style={{ color: '#facc15', fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
                                        {t.policyConflictWarning || 'Policy conflict detected (deny overrides allow):'}
                                    </div>
                                    {nameConflicts.length > 0 && (
                                        <div style={{ color: '#fde68a', fontSize: 12 }}>name: {nameConflicts.join(', ')}</div>
                                    )}
                                    {groupConflicts.length > 0 && (
                                        <div style={{ color: '#fde68a', fontSize: 12 }}>group: {groupConflicts.join(', ')}</div>
                                    )}
                                    {providerConflicts.length > 0 && (
                                        <div style={{ color: '#fde68a', fontSize: 12 }}>provider: {providerConflicts.join(', ')}</div>
                                    )}
                                </div>
                            )}

                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 12 }}>
                                <div>
                                    <label className="label">{t.allowNames || 'allow_names'}</label>
                                    <textarea
                                        className="input"
                                        rows={4}
                                        value={listToText(selectedPolicy.allow_names)}
                                        onChange={(e) => updateToolPolicy(selectedIndex, { allow_names: parseLines(e.target.value) })}
                                    />
                                </div>
                                <div>
                                    <label className="label">{t.denyNames || 'deny_names'}</label>
                                    <textarea
                                        className="input"
                                        rows={4}
                                        value={listToText(selectedPolicy.deny_names)}
                                        onChange={(e) => updateToolPolicy(selectedIndex, { deny_names: parseLines(e.target.value) })}
                                    />
                                </div>
                                <div>
                                    <label className="label">{t.allowGroups || 'allow_groups'}</label>
                                    <textarea
                                        className="input"
                                        rows={4}
                                        value={listToText(selectedPolicy.allow_groups)}
                                        onChange={(e) => updateToolPolicy(selectedIndex, { allow_groups: parseLines(e.target.value) })}
                                    />
                                </div>
                                <div>
                                    <label className="label">{t.denyGroups || 'deny_groups'}</label>
                                    <textarea
                                        className="input"
                                        rows={4}
                                        value={listToText(selectedPolicy.deny_groups)}
                                        onChange={(e) => updateToolPolicy(selectedIndex, { deny_groups: parseLines(e.target.value) })}
                                    />
                                </div>
                                <div>
                                    <label className="label">{t.allowProviders || 'allow_providers'}</label>
                                    <textarea
                                        className="input"
                                        rows={4}
                                        value={listToText(selectedPolicy.allow_providers)}
                                        onChange={(e) => updateToolPolicy(selectedIndex, { allow_providers: parseLines(e.target.value) })}
                                    />
                                </div>
                                <div>
                                    <label className="label">{t.denyProviders || 'deny_providers'}</label>
                                    <textarea
                                        className="input"
                                        rows={4}
                                        value={listToText(selectedPolicy.deny_providers)}
                                        onChange={(e) => updateToolPolicy(selectedIndex, { deny_providers: parseLines(e.target.value) })}
                                    />
                                </div>
                            </div>

                            <div style={{ marginTop: 12 }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                                    <div style={{ fontSize: 12, color: '#7f8ea3' }}>
                                        {t.effectivePolicy || 'Effective Policy'}
                                    </div>
                                    <button className="btn-ghost" onClick={loadEffectiveForSelected}>
                                        {t.refresh || 'Refresh'}
                                    </button>
                                </div>
                                {!effectivePolicy ? (
                                    <div style={{ color: '#889', fontSize: 12 }}>
                                        {t.noData || 'No data'}
                                    </div>
                                ) : (
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: 12 }}>
                                        <div style={{ color: '#9fb3c8' }}>
                                            max_tier: <span style={{ color: '#dbeafe' }}>{effectivePolicy.max_tier || '-'}</span>
                                        </div>
                                        <div style={{ color: '#9fb3c8' }}>
                                            allow_names: <span style={{ color: '#dbeafe' }}>{(effectivePolicy.allow_names || []).length}</span>
                                        </div>
                                        <div style={{ color: '#9fb3c8' }}>
                                            deny_names: <span style={{ color: '#dbeafe' }}>{(effectivePolicy.deny_names || []).length}</span>
                                        </div>
                                        <div style={{ color: '#9fb3c8' }}>
                                            allow_providers: <span style={{ color: '#dbeafe' }}>{(effectivePolicy.allow_providers || []).length}</span>
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default AgentPolicy;
