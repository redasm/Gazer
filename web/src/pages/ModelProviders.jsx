import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';

import API_BASE from '../config';
import ToggleSwitch from '../components/ToggleSwitch';

const emptyProvider = {
    base_url: '',
    api_key: '',
    default_model: '',
    api: '',
    auth: '',
    headers: {},
    authHeader: false,
    models: [],
    agents: {},
};


const ModelProviders = ({ modelProviders, setModelProviders, fetchModelProviders, t }) => {
    const providerNames = useMemo(
        () => Object.keys(modelProviders || {}).sort(),
        [modelProviders],
    );
    const [selected, setSelected] = useState(providerNames[0] || '');
    const [newName, setNewName] = useState('');
    const [modelsJson, setModelsJson] = useState('[]');
    const [agentsJson, setAgentsJson] = useState('{}');
    const [headersJson, setHeadersJson] = useState('{}');
    const [isRefreshing, setIsRefreshing] = useState(false);
    const [isCreating, setIsCreating] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [isDeleting, setIsDeleting] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    // Incremented after each server fetch to force JSON re-sync without depending on modelProviders
    const [refreshKey, setRefreshKey] = useState(0);

    const selectedCfg = selected ? (modelProviders?.[selected] || emptyProvider) : emptyProvider;

    const refreshProviderJson = (name) => {
        const cfg = (modelProviders || {})[name] || emptyProvider;
        const models = Array.isArray(cfg.models) ? cfg.models : [];
        const agents = Object.prototype.hasOwnProperty.call(cfg || {}, 'agents') ? cfg.agents : {};
        const headers =
            cfg && typeof cfg.headers === 'object' && !Array.isArray(cfg.headers)
                ? cfg.headers
                : {};
        setModelsJson(JSON.stringify(models, null, 2));
        setAgentsJson(JSON.stringify(agents, null, 2));
        setHeadersJson(JSON.stringify(headers, null, 2));
    };

    const selectProvider = (name) => {
        setSelected(name);
        refreshProviderJson(name);
    };

    useEffect(() => {
        if (!providerNames.length) {
            if (selected) setSelected('');
            setModelsJson('[]');
            setAgentsJson('{}');
            setHeadersJson('{}');
            return;
        }
        const activeName = selected && providerNames.includes(selected) ? selected : providerNames[0];
        if (activeName !== selected) {
            setSelected(activeName);
        }
        refreshProviderJson(activeName);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [providerNames, selected, refreshKey]); // excludes modelProviders: updateLocal changes it but must not reset JSON textarea edits

    const updateLocal = (patch) => {
        if (!selected) return;
        setModelProviders((prev) => ({
            ...(prev || {}),
            [selected]: {
                ...(prev?.[selected] || {}),
                ...patch,
            },
        }));
    };

    const addProvider = async () => {
        const name = String(newName || '').trim();
        if (!name) return;
        if ((modelProviders || {})[name]) {
            alert(t.providerAlreadyExists || 'Provider already exists.');
            return;
        }
        setIsCreating(true);
        try {
            await axios.post(`${API_BASE}/model-providers`, { name, provider: emptyProvider });
            await fetchModelProviders();
            setNewName('');
            setSelected(name);
            setModelsJson('[]');
            setAgentsJson('{}');
            setHeadersJson('{}');
        } finally {
            setIsCreating(false);
        }
    };

    const saveProvider = async () => {
        if (!selected) return;
        let parsedModels = [];
        let parsedAgents = {};
        let parsedHeaders = {};
        try {
            parsedModels = JSON.parse(modelsJson || '[]');
        } catch {
            alert(t.noticeInvalidProviderCostsJson || 'Invalid JSON.');
            return;
        }
        try {
            parsedAgents = JSON.parse(agentsJson || '{}');
        } catch {
            alert(t.noticeInvalidProviderCostsJson || 'Invalid JSON.');
            return;
        }
        try {
            const parsed = JSON.parse(headersJson || '{}');
            if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
                throw new Error('headers must be an object');
            }
            parsedHeaders = parsed;
        } catch {
            alert(t.headersJsonObjectOnly || 'Headers must be a JSON object.');
            return;
        }
        const payload = {
            ...(modelProviders?.[selected] || {}),
            models: Array.isArray(parsedModels) ? parsedModels : [],
            agents: parsedAgents,
            headers: parsedHeaders,
            authHeader: Boolean((modelProviders?.[selected] || {}).authHeader),
        };
        setIsSaving(true);
        try {
            await axios.put(`${API_BASE}/model-providers/${encodeURIComponent(selected)}`, {
                provider: payload,
            });
            await fetchModelProviders();
            setRefreshKey(k => k + 1);
        } finally {
            setIsSaving(false);
        }
    };

    const removeProvider = async () => {
        if (!selected) return;
        setIsDeleting(true);
        try {
            await axios.delete(`${API_BASE}/model-providers/${encodeURIComponent(selected)}`);
            await fetchModelProviders();
            const nextNames = Object.keys(modelProviders || {}).filter((name) => name !== selected);
            const nextSelected = nextNames[0] || '';
            setSelected(nextSelected);
            refreshProviderJson(nextSelected);
            setConfirmDelete(false);
        } finally {
            setIsDeleting(false);
        }
    };

    const onRefresh = async () => {
        setIsRefreshing(true);
        try {
            await fetchModelProviders();
            setRefreshKey(k => k + 1);
        } finally {
            setIsRefreshing(false);
        }
    };

    return (
        <div style={{ flex: 1, maxWidth: 1000, display: 'flex', flexDirection: 'column', gap: 20 }}>
            <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                    <h2 className="text-2xl font-bold text-white mb-2">{t.modelProviders || 'Model Providers'}</h2>
                    <p className="text-gray-400">
                        {t.modelProvidersDesc || 'Manage provider endpoints/keys outside settings.yaml.'}
                    </p>
                </div>
                <button className="btn btn-secondary" onClick={onRefresh} disabled={isRefreshing}>
                    {isRefreshing ? (t.refreshing || 'Refreshing...') : (t.refresh || 'Refresh')}
                </button>
            </header>

            <section className="glass-panel p-6 space-y-4">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 8 }}>
                    <input
                        className="w-full bg-black/30 border border-white/10 rounded-lg p-2.5 text-white outline-none focus:border-blue-500 transition-all"
                        value={newName}
                        onChange={(e) => setNewName(e.target.value)}
                        placeholder={t.enterProviderName || 'Enter provider name'}
                    />
                    <button className="btn btn-primary" onClick={addProvider} disabled={isCreating}>
                        {isCreating ? (t.creating || 'Creating...') : (t.addProvider || 'Add Provider')}
                    </button>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 16 }}>
                    <div style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 10, padding: 8 }}>
                        {providerNames.map((name) => (
                            <button
                                key={name}
                                type="button"
                                onClick={() => selectProvider(name)}
                                style={{
                                    width: '100%',
                                    textAlign: 'left',
                                    border: 'none',
                                    borderRadius: 8,
                                    padding: '8px 10px',
                                    marginBottom: 4,
                                    background: selected === name ? 'rgba(239,35,60,0.12)' : 'transparent',
                                    color: selected === name ? '#fff' : '#9ca3af',
                                    cursor: 'pointer',
                                }}
                            >
                                {name}
                            </button>
                        ))}
                    </div>

                    <div style={{ display: 'grid', gap: 10 }}>
                        <input
                            className="w-full bg-black/30 border border-white/10 rounded-lg p-2.5 text-white outline-none focus:border-blue-500 transition-all"
                            value={selectedCfg.base_url || ''}
                            onChange={(e) => updateLocal({ base_url: e.target.value })}
                            placeholder="base_url"
                        />
                        <input
                            className="w-full bg-black/30 border border-white/10 rounded-lg p-2.5 text-white outline-none focus:border-blue-500 transition-all"
                            value={selectedCfg.api_key || ''}
                            onChange={(e) => updateLocal({ api_key: e.target.value })}
                            placeholder="api_key"
                            type="password"
                        />
                        <input
                            className="w-full bg-black/30 border border-white/10 rounded-lg p-2.5 text-white outline-none focus:border-blue-500 transition-all"
                            value={selectedCfg.default_model || ''}
                            onChange={(e) => updateLocal({ default_model: e.target.value })}
                            placeholder="default_model"
                        />
                        <input
                            className="w-full bg-black/30 border border-white/10 rounded-lg p-2.5 text-white outline-none focus:border-blue-500 transition-all"
                            value={selectedCfg.api || ''}
                            onChange={(e) => updateLocal({ api: e.target.value })}
                            placeholder="api (optional)"
                        />
                        <input
                            className="w-full bg-black/30 border border-white/10 rounded-lg p-2.5 text-white outline-none focus:border-blue-500 transition-all"
                            value={selectedCfg.auth || ''}
                            onChange={(e) => updateLocal({ auth: e.target.value })}
                            placeholder="auth (api-key | bearer | none)"
                        />
                        <div
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                gap: 8,
                            }}
                        >
                            <span style={{ color: '#cbd5e1', textAlign: 'left' }}>authHeader</span>
                            <ToggleSwitch
                                checked={Boolean(selectedCfg.authHeader)}
                                onChange={(next) => updateLocal({ authHeader: next })}
                            />
                        </div>
                        <span style={{ color: '#cbd5e1', textAlign: 'left' }}>headers</span>
                        <textarea
                            className="w-full bg-black/30 border border-white/10 rounded-lg p-2.5 text-white outline-none focus:border-blue-500 transition-all"
                            style={{ minHeight: 120, fontFamily: 'monospace', fontSize: 12 }}
                            value={headersJson}
                            onChange={(e) => setHeadersJson(e.target.value)}
                            placeholder={t.headersJson || 'Headers (JSON)'}
                        />
                        <span style={{ color: '#cbd5e1', textAlign: 'left' }}>models</span>
                        <p style={{ color: '#6b7280', fontSize: 12, marginTop: -4, marginBottom: 2 }}>
                            {t.modelsJsonHint || 'Per-model config (id/name/reasoning/input/cost/contextWindow/maxTokens).'}
                        </p>
                        <textarea
                            className="w-full bg-black/30 border border-white/10 rounded-lg p-2.5 text-white outline-none focus:border-blue-500 transition-all"
                            style={{ minHeight: 220, fontFamily: 'monospace', fontSize: 12 }}
                            value={modelsJson}
                            onChange={(e) => setModelsJson(e.target.value)}
                            placeholder="models JSON array"
                        />
                        <span style={{ color: '#cbd5e1', textAlign: 'left' }}>agents</span>
                        <textarea
                            className="w-full bg-black/30 border border-white/10 rounded-lg p-2.5 text-white outline-none focus:border-blue-500 transition-all"
                            style={{ minHeight: 220, fontFamily: 'monospace', fontSize: 12 }}
                            value={agentsJson}
                            onChange={(e) => setAgentsJson(e.target.value)}
                            placeholder="agents JSON"
                        />
                        <div style={{ display: 'flex', gap: 8 }}>
                            <button className="btn btn-primary" onClick={saveProvider} disabled={!selected || isSaving}>
                                {isSaving ? (t.saving || 'Saving...') : (t.saveConfig || 'Save')}
                            </button>
                            {!confirmDelete ? (
                                <button
                                    className="btn btn-secondary"
                                    onClick={() => setConfirmDelete(true)}
                                    disabled={!selected || isDeleting}
                                >
                                    {t.delete || 'Delete'}
                                </button>
                            ) : (
                                <>
                                    <button
                                        className="btn btn-secondary"
                                        onClick={() => setConfirmDelete(false)}
                                        disabled={isDeleting}
                                    >
                                        {t.cancel || 'Cancel'}
                                    </button>
                                    <button
                                        className="btn btn-primary"
                                        onClick={removeProvider}
                                        disabled={!selected || isDeleting}
                                    >
                                        {isDeleting ? (t.deleting || 'Deleting...') : (t.deleteConfirm || 'Confirm Delete')}
                                    </button>
                                </>
                            )}
                        </div>
                    </div>
                </div>
            </section>
        </div>
    );
};

export default ModelProviders;
