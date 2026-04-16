import React, { useEffect, useState } from 'react';
import axios from 'axios';
import API_BASE from '../config';
import NoticeBanner from '../components/NoticeBanner';
import useNotice from '../hooks/useNotice';
import ToggleSwitch from '../components/ToggleSwitch';

const LlmRouter = ({ t }) => {
    const [data, setData] = useState(null);
    const [strategy, setStrategy] = useState('priority');
    const [budget, setBudget] = useState({
        enabled: false,
        window_seconds: 60,
        max_calls: 120,
        max_cost_usd: 2.0,
        estimated_input_tokens_per_char: 0.25,
        provider_cost_per_1k_tokens: {},
    });
    const [providerCostsText, setProviderCostsText] = useState('{}');
    const [loading, setLoading] = useState(false);
    const { notice, showNotice } = useNotice();

    const load = async () => {
        setLoading(true);
        try {
            // Read LLM router status through MCP
            const res = await axios.post(`${API_BASE}/mcp`, {
                jsonrpc: "2.0",
                method: "resources/read",
                params: { uri: "gazer://llm/router/status" },
                id: Date.now()
            });
            const contents = res.data?.contents || [];
            if (contents.length > 0) {
                const statusData = JSON.parse(contents[0].text);
                setData(statusData);
                setStrategy(statusData?.strategy || 'priority');
                const latestBudget = statusData?.budget || {};
                setBudget((prev) => ({
                    ...prev,
                    ...latestBudget,
                }));
                const providerCosts = latestBudget.provider_cost_per_1k_tokens || {};
                setProviderCostsText(JSON.stringify(providerCosts, null, 2));
            } else {
                setData({ enabled: false, note: 'empty_response' });
            }
        } catch {
            setData({ enabled: false, note: 'request_failed' });
            showNotice(t.noticeLoadRouterStatusFailed || 'Failed to load router status', 'error');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
    }, []);

    const saveStrategy = async () => {
        try {
            await axios.post(`${API_BASE}/config`, { models: { router: { strategy } } });
            await load();
            showNotice(t.noticeRoutingStrategySaved || 'Routing strategy saved', 'success');
        } catch {
            showNotice(t.noticeSaveRoutingStrategyFailed || 'Failed to save routing strategy', 'error');
        }
    };

    const saveBudget = async () => {
        try {
            let providerCosts = {};
            try {
                const parsed = JSON.parse(providerCostsText || '{}');
                if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                    providerCosts = parsed;
                }
            } catch {
                showNotice(t.noticeInvalidProviderCostsJson || 'Invalid provider costs JSON', 'error');
                return;
            }
            await axios.post(`${API_BASE}/config`, {
                models: {
                    router: {
                        budget: {
                            ...budget,
                            provider_cost_per_1k_tokens: providerCosts,
                        }
                    }
                }
            });
            await load();
            showNotice(t.noticeRouterBudgetSaved || 'Router budget saved', 'success');
        } catch {
            showNotice(t.noticeSaveRouterBudgetFailed || 'Failed to save router budget', 'error');
        }
    };

    return (
        <div style={{ maxWidth: 980 }}>
            <h2 style={{ fontSize: 18, fontWeight: 600, color: '#fff', marginBottom: 6 }}>
                {t.llmRouter || 'LLM Router'}
            </h2>
            <p style={{ color: '#6b7280', marginTop: 0, marginBottom: 16 }}>
                {t.llmRouterDesc || 'Observe provider health and switch routing strategy.'}
            </p>
            <NoticeBanner notice={notice} />

            {!data?.enabled ? (
                <div className="card" style={{ padding: 16, color: '#889' }}>
                    {data?.note || t.routerNotEnabled || 'LLM router not enabled'}
                </div>
            ) : (
                <>
                    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
                        <label className="label">{t.routingStrategy || 'Routing Strategy'}</label>
                        <div style={{ display: 'flex', gap: 8 }}>
                            <select className="input" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
                                <option value="priority">priority</option>
                                <option value="latency">latency</option>
                                <option value="success_rate">success_rate</option>
                            </select>
                            <button className="btn-primary" onClick={saveStrategy} disabled={loading}>
                                {t.save || 'Save'}
                            </button>
                        </div>
                    </div>

                    <div className="card" style={{ padding: 16 }}>
                        <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 10, display: 'flex', gap: 16 }}>
                            <span>total_calls: {data.total_calls ?? 0}</span>
                            <span>total_failures: {data.total_failures ?? 0}</span>
                            <span>avg_latency_ms: {data.avg_latency_ms ?? 0}</span>
                        </div>
                        <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 10, display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                            <span>budget_enabled: {String(data?.budget?.enabled ?? false)}</span>
                            <span>used_calls: {data?.budget?.used_calls ?? 0}</span>
                            <span>max_calls: {data?.budget?.max_calls ?? 0}</span>
                            <span>used_cost_usd: {data?.budget?.used_cost_usd ?? 0}</span>
                            <span>max_cost_usd: {data?.budget?.max_cost_usd ?? 0}</span>
                        </div>
                        <div style={{ fontSize: 12, color: '#7f8ea3', marginBottom: 10 }}>
                            {t.providers || 'Providers'}
                        </div>
                        {(data.providers || []).map((provider) => (
                            <div key={provider.name} style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 1fr 1fr 1fr', gap: 8, fontSize: 12, padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                                <span style={{ color: '#e2e8f0' }}>{provider.name}</span>
                                <span style={{ color: '#6b7280' }}>{provider.model}</span>
                                <span style={{ color: '#6b7280' }}>calls: {provider.calls}</span>
                                <span style={{ color: '#6b7280' }}>ok: {provider.success_rate}</span>
                                <span style={{ color: '#6b7280' }}>lat: {provider.last_latency_ms}ms</span>
                            </div>
                        ))}
                    </div>

                    <div className="card" style={{ padding: 16, marginTop: 12 }}>
                        <label className="label">{t.routerBudget || 'Router Budget Policy'}</label>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(220px, 1fr))', gap: 12 }}>
                            <div style={{ gridColumn: '1 / span 2', color: '#6b7280', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
                                <span>enabled</span>
                                <ToggleSwitch
                                    checked={Boolean(budget.enabled)}
                                    onChange={(v) => setBudget((prev) => ({ ...prev, enabled: v }))}
                                />
                            </div>
                            <div>
                                <label className="label">window_seconds</label>
                                <input
                                    className="input"
                                    type="number"
                                    min={10}
                                    value={budget.window_seconds ?? 60}
                                    onChange={(e) => setBudget((prev) => ({ ...prev, window_seconds: Number(e.target.value || 60) }))}
                                />
                            </div>
                            <div>
                                <label className="label">max_calls</label>
                                <input
                                    className="input"
                                    type="number"
                                    min={1}
                                    value={budget.max_calls ?? 120}
                                    onChange={(e) => setBudget((prev) => ({ ...prev, max_calls: Number(e.target.value || 120) }))}
                                />
                            </div>
                            <div>
                                <label className="label">max_cost_usd</label>
                                <input
                                    className="input"
                                    type="number"
                                    min={0}
                                    step="0.01"
                                    value={budget.max_cost_usd ?? 2.0}
                                    onChange={(e) => setBudget((prev) => ({ ...prev, max_cost_usd: Number(e.target.value || 0) }))}
                                />
                            </div>
                            <div>
                                <label className="label">estimated_input_tokens_per_char</label>
                                <input
                                    className="input"
                                    type="number"
                                    min={0.05}
                                    step="0.01"
                                    value={budget.estimated_input_tokens_per_char ?? 0.25}
                                    onChange={(e) => setBudget((prev) => ({ ...prev, estimated_input_tokens_per_char: Number(e.target.value || 0.25) }))}
                                />
                            </div>
                        </div>
                        <div style={{ marginTop: 12 }}>
                            <label className="label">provider_cost_per_1k_tokens (JSON)</label>
                            <textarea
                                className="input"
                                value={providerCostsText}
                                onChange={(e) => setProviderCostsText(e.target.value)}
                                rows={6}
                                style={{ fontFamily: 'monospace' }}
                            />
                        </div>
                        <div style={{ marginTop: 12, display: 'flex', justifyContent: 'flex-end' }}>
                            <button className="btn-primary" onClick={saveBudget} disabled={loading}>
                                {t.save || 'Save'}
                            </button>
                        </div>
                    </div>
                </>
            )}
        </div>
    );
};

export default LlmRouter;
