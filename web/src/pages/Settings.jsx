import React, { useState } from 'react';
import {
    Save, RefreshCw, Brain, Zap, Mic, Volume2, Bell, Eye,
    MessageSquare, User, Palette, X, Plus, Monitor, Globe, Database, Shield,
} from 'lucide-react';

/* ------------------------------------------------------------------ */
/*  ToggleSwitch                                                       */
/* ------------------------------------------------------------------ */
const ToggleSwitch = ({ checked, onChange, disabled = false }) => (
    <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-disabled={disabled}
        onClick={() => {
            if (!disabled) {
                onChange(!checked);
            }
        }}
        style={{
            position: 'relative', width: 44, height: 24, borderRadius: 12,
            background: checked ? '#3b82f6' : 'rgba(255,255,255,0.1)',
            border: '1px solid rgba(255,255,255,0.15)',
            cursor: disabled ? 'not-allowed' : 'pointer',
            flexShrink: 0,
            opacity: disabled ? 0.45 : 1,
        }}
    >
        <span style={{
            position: 'absolute', top: 2, left: checked ? 22 : 2,
            width: 18, height: 18, borderRadius: '50%',
            background: '#fff', transition: 'left 0.2s',
            boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
        }} />
    </button>
);

/* ------------------------------------------------------------------ */
/*  TagInput                                                            */
/* ------------------------------------------------------------------ */
const TagInput = ({ tags = [], onChange, placeholder }) => {
    const [input, setInput] = useState('');

    const addTag = () => {
        const val = input.trim();
        if (val && !tags.includes(val)) {
            onChange([...tags, val]);
        }
        setInput('');
    };

    const removeTag = (idx) => {
        onChange(tags.filter((_, i) => i !== idx));
    };

    return (
        <div style={{
            display: 'flex', flexWrap: 'wrap', gap: 6, padding: 8,
            background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: 8, minHeight: 42, alignItems: 'center',
        }}>
            {tags.map((tag, i) => (
                <span key={i} style={{
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                    padding: '3px 10px', borderRadius: 6, fontSize: 13,
                    background: 'rgba(59,130,246,0.2)', color: '#93c5fd',
                    border: '1px solid rgba(59,130,246,0.3)',
                }}>
                    {tag}
                    <X size={12} style={{ cursor: 'pointer', opacity: 0.7 }}
                        onClick={() => removeTag(i)} />
                </span>
            ))}
            <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTag(); } }}
                placeholder={tags.length === 0 ? placeholder : ''}
                style={{
                    flex: 1, minWidth: 100, background: 'transparent',
                    border: 'none', outline: 'none', color: '#fff', fontSize: 13,
                    padding: '2px 4px',
                }}
            />
        </div>
    );
};

/* ------------------------------------------------------------------ */
/*  Shared sub-components                                               */
/* ------------------------------------------------------------------ */
const SectionHeader = ({ icon, color, title, desc }) => (
    <div style={{ marginBottom: 4 }}>
        <h3 className="text-xl font-bold text-white flex items-center gap-2" style={{ marginBottom: 4 }}>
            <span style={{ color }}>{icon}</span> {title}
        </h3>
        {desc && <p style={{ color: '#6b7a8d', fontSize: 13, margin: 0 }}>{desc}</p>}
    </div>
);

const ToggleRow = ({ label, checked, onChange, disabled = false, hint = '' }) => (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '6px 0', gap: 12 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <span style={{ color: disabled ? '#6b7280' : '#9ca3af', fontSize: 14 }}>{label}</span>
            {hint ? <span style={{ color: '#6b7a8d', fontSize: 12 }}>{hint}</span> : null}
        </div>
        <ToggleSwitch checked={checked} onChange={onChange} disabled={disabled} />
    </div>
);

const ModelAdvancedFields = ({ providerName, modelName, config, handleUpdate, inputClass, labelClass, t }) => {
    const providerConfig = config?.models?.providers?.[providerName];
    if (!providerConfig || !modelName) {
        return null;
    }

    const modelKey = String(modelName).trim();
    if (!modelKey) {
        return null;
    }

    const models = Array.isArray(providerConfig.models) ? providerConfig.models : [];
    const modelIndex = models.findIndex((entry) => {
        if (!entry || typeof entry !== 'object') return false;
        const entryId = String(entry.id || entry.name || '').trim();
        return entryId === modelKey;
    });
    const modelEntry = modelIndex >= 0 ? models[modelIndex] : {};

    const updateModelEntry = (patch) => {
        const nextModels = [...models];
        const normalizedPatch = { ...patch };
        Object.keys(normalizedPatch).forEach((key) => {
            if (normalizedPatch[key] === undefined) {
                delete normalizedPatch[key];
            }
        });
        if (modelIndex >= 0) {
            const merged = { ...nextModels[modelIndex], ...normalizedPatch };
            Object.keys(merged).forEach((key) => {
                if (merged[key] === undefined) {
                    delete merged[key];
                }
            });
            nextModels[modelIndex] = merged;
        } else {
            nextModels.push({
                id: modelKey,
                name: modelKey,
                ...normalizedPatch,
            });
        }
        handleUpdate(`models.providers.${providerName}.models`, nextModels);
    };

    const inputTypes = Array.isArray(modelEntry.input)
        ? modelEntry.input.map((item) => String(item).trim().toLowerCase()).filter(Boolean)
        : [];
    const hasInputType = (value) => inputTypes.includes(value);
    const toggleInputType = (value) => {
        const next = hasInputType(value)
            ? inputTypes.filter((item) => item !== value)
            : [...inputTypes, value];
        updateModelEntry({ input: next });
    };

    const cost = (modelEntry.cost && typeof modelEntry.cost === 'object') ? modelEntry.cost : {};

    return (
        <div style={{ marginTop: 10, padding: 10, border: '1px solid rgba(255,255,255,0.1)', borderRadius: 10, background: 'rgba(255,255,255,0.03)' }}>
            <h6 style={{ fontSize: 12, color: '#93c5fd', fontWeight: 700, marginBottom: 8 }}>
                {(t.modelAdvancedConfig || 'Advanced Model Config')} ({modelKey})
            </h6>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div>
                    <label className={labelClass}>{t.apiMode || 'API Mode'}</label>
                    <input
                        className={`${inputClass} text-sm`}
                        value={modelEntry.api || ''}
                        onChange={(e) => updateModelEntry({ api: e.target.value })}
                        placeholder="openai-responses / openai-completions"
                    />
                </div>
                <div>
                    <label className={labelClass}>{t.reasoning || 'Reasoning'}</label>
                    <select
                        className={inputClass}
                        value={typeof modelEntry.reasoning === 'boolean' ? String(modelEntry.reasoning) : ''}
                        onChange={(e) => {
                            if (e.target.value === '') {
                                const nextModels = [...models];
                                if (modelIndex >= 0) {
                                    const nextEntry = { ...nextModels[modelIndex] };
                                    delete nextEntry.reasoning;
                                    nextModels[modelIndex] = nextEntry;
                                    handleUpdate(`models.providers.${providerName}.models`, nextModels);
                                }
                                return;
                            }
                            updateModelEntry({ reasoning: e.target.value === 'true' });
                        }}
                    >
                        <option value="">{t.followProvider || 'Follow provider default'}</option>
                        <option value="true">true</option>
                        <option value="false">false</option>
                    </select>
                </div>

                <div>
                    <label className={labelClass}>{t.maxTokens || 'Max Tokens'}</label>
                    <input
                        type="number"
                        min="1"
                        className={inputClass}
                        value={modelEntry.maxTokens ?? ''}
                        onChange={(e) => {
                            const value = e.target.value.trim();
                            updateModelEntry({ maxTokens: value === '' ? undefined : parseInt(value, 10) || undefined });
                        }}
                    />
                </div>
                <div>
                    <label className={labelClass}>{t.contextWindow || 'Context Window'}</label>
                    <input
                        type="number"
                        min="1"
                        className={inputClass}
                        value={modelEntry.contextWindow ?? ''}
                        onChange={(e) => {
                            const value = e.target.value.trim();
                            updateModelEntry({ contextWindow: value === '' ? undefined : parseInt(value, 10) || undefined });
                        }}
                    />
                </div>
            </div>

            <div style={{ marginTop: 8 }}>
                <label className={labelClass}>{t.inputTypes || 'Input Types'}</label>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {['text', 'image', 'audio'].map((value) => (
                        <button
                            key={value}
                            type="button"
                            className={hasInputType(value) ? 'btn-primary' : 'btn-secondary'}
                            onClick={() => toggleInputType(value)}
                            style={{ padding: '4px 10px' }}
                        >
                            {value}
                        </button>
                    ))}
                </div>
            </div>

            <div style={{ marginTop: 8 }}>
                <label className={labelClass}>{t.costPerMillion || 'Cost (per 1M tokens, optional)'}</label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 8 }}>
                    {[
                        ['input', t.inputCost || 'Input'],
                        ['output', t.outputCost || 'Output'],
                        ['cacheRead', t.cacheReadCost || 'Cache Read'],
                        ['cacheWrite', t.cacheWriteCost || 'Cache Write'],
                    ].map(([key, label]) => (
                        <div key={key}>
                            <label style={{ fontSize: 11, color: '#6b7280', marginBottom: 4, display: 'block' }}>{label}</label>
                            <input
                                type="number"
                                step="0.000001"
                                min="0"
                                className={inputClass}
                                value={cost[key] ?? ''}
                                onChange={(e) => {
                                    const value = e.target.value.trim();
                                    const nextCost = { ...cost };
                                    if (value === '') {
                                        delete nextCost[key];
                                    } else {
                                        const parsed = Number(value);
                                        if (!Number.isNaN(parsed)) {
                                            nextCost[key] = parsed;
                                        }
                                    }
                                    updateModelEntry({ cost: nextCost });
                                }}
                            />
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
};

/* ------------------------------------------------------------------ */
/*  Tab definitions                                                     */
/* ------------------------------------------------------------------ */
const TABS = [
    { key: 'model', icon: Brain, color: '#c084fc' },
    { key: 'voice', icon: Volume2, color: '#22d3ee' },
    { key: 'channels', icon: MessageSquare, color: '#60a5fa' },
    { key: 'web', icon: Globe, color: '#fbbf24' },
    { key: 'memory', icon: Database, color: '#34d399' },
    { key: 'safety', icon: Shield, color: '#f97316' },
    { key: 'hardware', icon: Monitor, color: '#4ade80' },
    { key: 'persona', icon: User, color: '#f472b6' },
];

/* ================================================================== */
/*  Tab: Model                                                         */
/* ================================================================== */
const TabModel = ({ config, modelProviders, handleUpdate, inputClass, labelClass, sectionClass, t }) => {
    const providerNames = Object.keys(modelProviders || {}).sort();
    // Embedding config logic removed
    const modelDefaults = config.agents?.defaults?.model || {};
    const primaryRef =
        typeof modelDefaults === "string"
            ? modelDefaults.trim()
            : String(modelDefaults?.primary || "").trim();
    const fallbackRefRaw = Array.isArray(modelDefaults?.fallbacks) ? modelDefaults.fallbacks[0] : "";
    const fallbackRef = String(fallbackRefRaw || "").trim() || primaryRef;

    const splitModelRef = (ref) => {
        const text = String(ref || "").trim();
        if (!text.includes("/")) {
            return { provider: "", model: "" };
        }
        const idx = text.indexOf("/");
        return {
            provider: text.slice(0, idx).trim(),
            model: text.slice(idx + 1).trim(),
        };
    };

    const slowCurrent = splitModelRef(primaryRef);
    const fastCurrent = splitModelRef(fallbackRef);

    const setPrimaryRef = (provider, model) => {
        const p = String(provider || "").trim();
        const m = String(model || "").trim();
        const ref = p && m ? `${p}/${m}` : "";
        handleUpdate("agents.defaults.model.primary", ref);
    };

    const setFastRef = (provider, model) => {
        const p = String(provider || "").trim();
        const m = String(model || "").trim();
        const ref = p && m ? `${p}/${m}` : "";
        handleUpdate("agents.defaults.model.fallbacks", ref ? [ref] : []);
    };

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            <section className={sectionClass}>
                <SectionHeader icon={<Brain size={20} />} color="#c084fc" title={t.brainProfiles} />
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* Slow Brain */}
                    <div className="bg-black/20 p-4 rounded-lg border border-white/5">
                        <h4 className="text-lg font-semibold text-blue-300 mb-3 flex items-center gap-2">
                            <Zap size={16} /> {t.slowBrain}
                        </h4>
                        <div className="space-y-4">
                            <div>
                                <label className={labelClass}>{t.provider}</label>
                                <select className={inputClass}
                                    value={slowCurrent.provider || ""}
                                    onChange={(e) => setPrimaryRef(e.target.value, slowCurrent.model)}>
                                    <option value="" disabled>{t.selectProvider || 'Select provider'}</option>
                                    {providerNames.map(p => (
                                        <option key={p} value={p}>{p}</option>
                                    ))}
                                </select>
                            </div>
                            <div>
                                <label className={labelClass}>{t.model}</label>
                                <input className={inputClass}
                                    value={slowCurrent.model || ""}
                                    onChange={(e) => setPrimaryRef(slowCurrent.provider, e.target.value)}
                                    placeholder="e.g. gpt-4o" />
                            </div>
                        </div>
                    </div>

                    {/* Fast Brain */}
                    <div className="bg-black/20 p-4 rounded-lg border border-white/5">
                        <h4 className="text-lg font-semibold text-yellow-300 mb-3 flex items-center gap-2">
                            <Zap size={16} /> {t.fastBrain}
                        </h4>
                        <div className="space-y-4">
                            <div>
                                <label className={labelClass}>{t.provider}</label>
                                <select className={inputClass}
                                    value={fastCurrent.provider || ""}
                                    onChange={(e) => setFastRef(e.target.value, fastCurrent.model)}>
                                    <option value="" disabled>{t.selectProvider || 'Select provider'}</option>
                                    {providerNames.map(p => (
                                        <option key={p} value={p}>{p}</option>
                                    ))}
                                </select>
                            </div>
                            <div>
                                <label className={labelClass}>{t.model}</label>
                                <input className={inputClass}
                                    value={fastCurrent.model || ""}
                                    onChange={(e) => setFastRef(fastCurrent.provider, e.target.value)}
                                    placeholder="e.g. llama3" />
                            </div>
                        </div>
                    </div>

                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-6">
                    {/* Embedding */}
                    <div className="bg-black/20 p-4 rounded-lg border border-white/5">
                        <div className="flex items-center justify-between mb-3">
                            <h4 className="text-lg font-semibold text-emerald-300 flex items-center gap-2">
                                <Zap size={16} /> {t.embeddingModel || "Embedding Model"}
                            </h4>
                        </div>
                        <ToggleRow
                            label={t.enableEmbeddingSettings}
                            hint={t.embeddingDesc}
                            checked={config.models?.embedding?.enabled || false}
                            onChange={(v) => handleUpdate("models.embedding.enabled", v)}
                        />

                        {config.models?.embedding?.enabled && (
                            <div className="space-y-4">
                                <div>
                                    <label className={labelClass}>{t.embeddingProvider || t.provider}</label>
                                    <select className={inputClass}
                                        value={config.models?.embedding?.provider || ""}
                                        onChange={(e) => handleUpdate("models.embedding.provider", e.target.value)}>
                                        <option value="" disabled>{t.selectProvider || 'Select provider'}</option>
                                        {providerNames.map(p => (
                                            <option key={p} value={p}>{p}</option>
                                        ))}
                                    </select>
                                </div>
                                <div>
                                    <label className={labelClass}>{t.embeddingModel || "Model"}</label>
                                    <input className={inputClass}
                                        value={config.models?.embedding?.model || ""}
                                        onChange={(e) => handleUpdate("models.embedding.model", e.target.value)}
                                        placeholder="e.g. text-embedding-v3" />
                                    <p style={{ color: '#6b7280', fontSize: 12, marginTop: 4 }}>
                                        {t.embeddingModelDesc || "Overrides OpenViking's default embedding engine when enabled"}
                                    </p>
                                </div>
                            </div>
                        )}
                    </div>
                </div>

            </section>
        </div>
    );
};

/* ================================================================== */
/*  Tab: Voice (Wake Word + TTS + ASR)                                 */
/* ================================================================== */
const TabVoice = ({ config, modelProviders, handleUpdate, inputClass, labelClass, sectionClass, t }) => {
    const providerNames = Object.keys(modelProviders || {}).sort();
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            {/* Wake Word */}
            <section className={sectionClass}>
                <SectionHeader icon={<Bell size={20} />} color="#fb923c" title={t.wakeWordSection} />
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-end">
                    <ToggleRow label={t.enabled}
                        checked={config.wake_word?.enabled ?? true}
                        onChange={(v) => handleUpdate("wake_word.enabled", v)} />
                    <div>
                        <label className={labelClass}>{t.keyword}</label>
                        <input className={inputClass}
                            value={config.wake_word?.keyword || "gazer"}
                            placeholder="gazer"
                            onChange={(e) => handleUpdate("wake_word.keyword", e.target.value)} />
                    </div>
                    <div>
                        <label className={labelClass}>{t.sensitivity}</label>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            <input type="range" min="0" max="1" step="0.05"
                                style={{ flex: 1, accentColor: '#3b82f6' }}
                                value={config.wake_word?.sensitivity || 0.5}
                                onChange={(e) => handleUpdate("wake_word.sensitivity", parseFloat(e.target.value))} />
                            <span style={{ color: '#9ca3af', fontSize: 13, minWidth: 36, textAlign: 'right' }}>
                                {(config.wake_word?.sensitivity || 0.5).toFixed(2)}
                            </span>
                        </div>
                    </div>
                </div>
            </section>

            {/* Voice TTS */}
            <section className={sectionClass}>
                <SectionHeader icon={<Volume2 size={20} />} color="#22d3ee" title={t.voiceTTSSection} />
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                    <div>
                        <label className={labelClass}>{t.provider}</label>
                        <select className={inputClass}
                            value={config.voice?.provider || "edge-tts"}
                            onChange={(e) => handleUpdate("voice.provider", e.target.value)}>
                            <option value="edge-tts">Edge TTS</option>
                            <option value="cloud_openai_compatible">Cloud (OpenAI Compatible)</option>
                        </select>
                    </div>
                    <div>
                        <label className={labelClass}>{t.voiceId}</label>
                        <input className={inputClass}
                            value={config.voice?.voice_id || ""}
                            placeholder="zh-CN-XiaoxiaoNeural"
                            onChange={(e) => handleUpdate("voice.voice_id", e.target.value)} />
                    </div>
                    <div>
                        <label className={labelClass}>{t.rate}</label>
                        <input className={inputClass}
                            value={config.voice?.rate || "+0%"}
                            placeholder="+0%"
                            onChange={(e) => handleUpdate("voice.rate", e.target.value)} />
                    </div>
                    <div>
                        <label className={labelClass}>{t.volume}</label>
                        <input className={inputClass}
                            value={config.voice?.volume || "+0%"}
                            placeholder="+0%"
                            onChange={(e) => handleUpdate("voice.volume", e.target.value)} />
                    </div>
                </div>
                {String(config.voice?.provider || "edge-tts") === "cloud_openai_compatible" && (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-2">
                        <div>
                            <label className={labelClass}>{t.providerRef || "Provider Ref (registry)"}</label>
                            <select
                                className={inputClass}
                                value={config.voice?.cloud?.provider_ref || ""}
                                onChange={(e) => handleUpdate("voice.cloud.provider_ref", e.target.value)}
                            >
                                <option value="">{t.none || "None"}</option>
                                {providerNames.map((p) => (
                                    <option key={p} value={p}>{p}</option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className={labelClass}>{t.model || "Model"}</label>
                            <input
                                className={inputClass}
                                value={config.voice?.cloud?.model || "gpt-4o-mini-tts"}
                                onChange={(e) => handleUpdate("voice.cloud.model", e.target.value)}
                            />
                        </div>
                        <div>
                            <label className={labelClass}>{t.baseUrl || "Base URL"}</label>
                            <input
                                className={inputClass}
                                value={config.voice?.cloud?.base_url || ""}
                                onChange={(e) => handleUpdate("voice.cloud.base_url", e.target.value)}
                            />
                        </div>
                        <div>
                            <label className={labelClass}>{t.apiKey || "API Key"}</label>
                            <input
                                type="password"
                                className={inputClass}
                                value={config.voice?.cloud?.api_key || ""}
                                onChange={(e) => handleUpdate("voice.cloud.api_key", e.target.value)}
                            />
                        </div>
                        <div>
                            <label className={labelClass}>{t.responseFormat || "Response Format"}</label>
                            <select
                                className={inputClass}
                                value={config.voice?.cloud?.response_format || "pcm"}
                                onChange={(e) => handleUpdate("voice.cloud.response_format", e.target.value)}
                            >
                                <option value="pcm">pcm</option>
                                <option value="mp3">mp3</option>
                            </select>
                        </div>
                        <div>
                            <label className={labelClass}>{t.retryCount || "Retry Count"}</label>
                            <input
                                type="number"
                                min="0"
                                max="5"
                                className={inputClass}
                                value={config.voice?.cloud?.retry_count ?? 1}
                                onChange={(e) => handleUpdate("voice.cloud.retry_count", parseInt(e.target.value, 10) || 0)}
                            />
                        </div>
                        <div className="md:col-span-2">
                            <ToggleRow
                                label={t.fallbackToEdge || "Fallback to Edge-TTS on Cloud failure"}
                                checked={Boolean(config.voice?.cloud?.fallback_to_edge ?? true)}
                                onChange={(v) => handleUpdate("voice.cloud.fallback_to_edge", v)}
                            />
                        </div>
                        <div className="md:col-span-2">
                            <ToggleRow
                                label={t.strictCloudRequired || "Strict cloud required"}
                                hint={t.strictCloudRequiredDesc || "Fail fast when cloud provider is unavailable; do not silently downgrade."}
                                checked={Boolean(config.voice?.cloud?.strict_required ?? false)}
                                onChange={(v) => handleUpdate("voice.cloud.strict_required", v)}
                            />
                        </div>
                    </div>
                )}
            </section>

            {/* ASR */}
            <section className={sectionClass}>
                <SectionHeader icon={<Mic size={20} />} color="#f472b6" title={t.asrSection} />
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className={labelClass}>{t.provider}</label>
                        <select className={inputClass}
                            value={config.asr?.provider || "whisper_local"}
                            onChange={(e) => handleUpdate("asr.provider", e.target.value)}>
                            <option value="whisper_local">Whisper (Local)</option>
                            <option value="cloud_openai_compatible">Cloud (OpenAI Compatible)</option>
                            <option value="hybrid">Hybrid</option>
                        </select>
                    </div>
                    <div>
                        <label className={labelClass}>{t.routeMode || "Route Mode"}</label>
                        <select className={inputClass}
                            value={config.asr?.route_mode || "local_first"}
                            onChange={(e) => handleUpdate("asr.route_mode", e.target.value)}>
                            <option value="local_first">local_first</option>
                            <option value="cloud_first">cloud_first</option>
                            <option value="auto">auto</option>
                        </select>
                    </div>
                    <div>
                        <label className={labelClass}>{t.modelSize}</label>
                        <select className={inputClass}
                            value={config.asr?.model_size || "base"}
                            onChange={(e) => handleUpdate("asr.model_size", e.target.value)}>
                            <option value="tiny">Tiny (Fastest)</option>
                            <option value="base">Base</option>
                            <option value="small">Small</option>
                            <option value="medium">Medium</option>
                            <option value="large">Large (Best)</option>
                        </select>
                    </div>
                    <div>
                        <label className={labelClass}>{t.providerRef || "Provider Ref (registry)"}</label>
                        <select className={inputClass}
                            value={config.asr?.cloud?.provider_ref || ""}
                            onChange={(e) => handleUpdate("asr.cloud.provider_ref", e.target.value)}>
                            <option value="">{t.none || "None"}</option>
                            {providerNames.map((p) => (
                                <option key={p} value={p}>{p}</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className={labelClass}>{t.inputDevice || "Input Device"}</label>
                        <input
                            className={inputClass}
                            value={config.asr?.input_device ?? ""}
                            placeholder={t.inputDeviceHint || "default / device id / device name"}
                            onChange={(e) => handleUpdate("asr.input_device", e.target.value === "" ? null : e.target.value)}
                        />
                    </div>
                    <div>
                        <label className={labelClass}>{t.model || "Model"}</label>
                        <input className={inputClass}
                            value={config.asr?.cloud?.model || "gpt-4o-mini-transcribe"}
                            onChange={(e) => handleUpdate("asr.cloud.model", e.target.value)} />
                    </div>
                    <div>
                        <label className={labelClass}>{t.baseUrl || "Base URL"}</label>
                        <input className={inputClass}
                            value={config.asr?.cloud?.base_url || ""}
                            onChange={(e) => handleUpdate("asr.cloud.base_url", e.target.value)} />
                    </div>
                    <div>
                        <label className={labelClass}>{t.apiKey || "API Key"}</label>
                        <input type="password" className={inputClass}
                            value={config.asr?.cloud?.api_key || ""}
                            onChange={(e) => handleUpdate("asr.cloud.api_key", e.target.value)} />
                    </div>
                    <div className="md:col-span-2">
                        <ToggleRow
                            label={t.strictCloudRequired || "Strict cloud required"}
                            hint={t.strictCloudRequiredDesc || "Fail fast when cloud provider is unavailable; do not silently downgrade."}
                            checked={Boolean(config.asr?.cloud?.strict_required ?? false)}
                            onChange={(v) => handleUpdate("asr.cloud.strict_required", v)}
                        />
                    </div>
                </div>
            </section>
        </div>
    );
};

/* ================================================================== */
/*  Tab: Channels (Telegram + Feishu)                                  */
/* ================================================================== */
const TabChannels = ({ config, handleUpdate, inputClass, labelClass, sectionClass, t }) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        <section className={sectionClass}>
            <SectionHeader icon={<MessageSquare size={20} />} color="#60a5fa" title={t.channelsSection} desc={t.channelsDesc} />

            {/* Telegram */}
            <div style={{ background: 'rgba(0,0,0,0.2)', padding: 16, borderRadius: 12, border: '1px solid rgba(255,255,255,0.05)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
                    <span style={{ fontWeight: 600, fontSize: 15, color: '#93c5fd' }}>Telegram</span>
                    <ToggleSwitch
                        checked={config.telegram?.enabled ?? false}
                        onChange={(v) => handleUpdate("telegram.enabled", v)} />
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className={labelClass}>{t.botToken}</label>
                        <input type="password" className={inputClass}
                            value={config.telegram?.token || ""}
                            placeholder="123456:ABC-DEF..."
                            onChange={(e) => handleUpdate("telegram.token", e.target.value)} />
                    </div>
                    <div>
                        <label className={labelClass}>{t.allowedIds}</label>
                        <TagInput
                            tags={config.telegram?.allowed_ids || []}
                            onChange={(v) => handleUpdate("telegram.allowed_ids", v)}
                            placeholder={t.addTag} />
                    </div>
                </div>
            </div>

            {/* Feishu / Lark */}
            <div style={{ background: 'rgba(0,0,0,0.2)', padding: 16, borderRadius: 12, border: '1px solid rgba(255,255,255,0.05)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
                    <span style={{ fontWeight: 600, fontSize: 15, color: '#34d399' }}>{t.feishuEnabled}</span>
                    <ToggleSwitch
                        checked={config.feishu?.enabled ?? false}
                        onChange={(v) => handleUpdate("feishu.enabled", v)} />
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className={labelClass}>{t.feishuAppId}</label>
                        <input className={inputClass}
                            value={config.feishu?.app_id || ""}
                            placeholder="cli_xxxxxxxx"
                            onChange={(e) => handleUpdate("feishu.app_id", e.target.value)} />
                    </div>
                    <div>
                        <label className={labelClass}>{t.feishuAppSecret}</label>
                        <input type="password" className={inputClass}
                            value={config.feishu?.app_secret || ""}
                            placeholder="..."
                            onChange={(e) => handleUpdate("feishu.app_secret", e.target.value)} />
                    </div>
                </div>
                <div style={{ marginTop: 12 }}>
                    <label className={labelClass}>{t.allowedIds}</label>
                    <TagInput
                        tags={config.feishu?.allowed_ids || []}
                        onChange={(v) => handleUpdate("feishu.allowed_ids", v)}
                        placeholder={t.addTag} />
                </div>
                <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                    <ToggleRow
                        label={t.feishuSimulatedTypingEnabled || '模拟输入状态消息'}
                        checked={config.feishu?.simulated_typing?.enabled ?? false}
                        onChange={(v) => handleUpdate("feishu.simulated_typing.enabled", v)}
                    />
                    <div style={{ marginTop: 8 }}>
                        <ToggleRow
                            label={t.feishuSimulatedTypingAutoRecall || '回复后自动撤回状态消息'}
                            checked={config.feishu?.simulated_typing?.auto_recall_on_reply ?? true}
                            onChange={(v) => handleUpdate("feishu.simulated_typing.auto_recall_on_reply", v)}
                        />
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4" style={{ marginTop: 10 }}>
                        <div>
                            <label className={labelClass}>{t.feishuSimulatedTypingText || '提示文案'}</label>
                            <input
                                className={inputClass}
                                value={config.feishu?.simulated_typing?.text || "正在思考中..."}
                                onChange={(e) => handleUpdate("feishu.simulated_typing.text", e.target.value)}
                                placeholder="正在思考中..."
                            />
                        </div>
                        <div>
                            <label className={labelClass}>{t.feishuSimulatedTypingMinInterval || '最小发送间隔（秒）'}</label>
                            <input
                                type="number"
                                min="1"
                                className={inputClass}
                                value={config.feishu?.simulated_typing?.min_interval_seconds ?? 8}
                                onChange={(e) => handleUpdate("feishu.simulated_typing.min_interval_seconds", parseInt(e.target.value, 10) || 1)}
                            />
                        </div>
                    </div>
                    <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                        <ToggleRow
                            label={t.feishuMediaAnalysisEnabled || 'Media auto analysis'}
                            checked={config.feishu?.media_analysis?.enabled ?? true}
                            onChange={(v) => handleUpdate("feishu.media_analysis.enabled", v)}
                        />
                        <ToggleRow
                            label={t.feishuMediaIncludeSummary || 'Append media summary to inbound text'}
                            checked={config.feishu?.media_analysis?.include_inbound_summary ?? true}
                            onChange={(v) => handleUpdate("feishu.media_analysis.include_inbound_summary", v)}
                        />
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4" style={{ marginTop: 8 }}>
                            <ToggleRow
                                label={t.feishuMediaAnalyzeImages || 'Analyze image/sticker content'}
                                checked={config.feishu?.media_analysis?.analyze_images ?? true}
                                onChange={(v) => handleUpdate("feishu.media_analysis.analyze_images", v)}
                            />
                            <ToggleRow
                                label={t.feishuMediaTranscribeAudio || 'Transcribe audio media'}
                                checked={config.feishu?.media_analysis?.transcribe_audio ?? true}
                                onChange={(v) => handleUpdate("feishu.media_analysis.transcribe_audio", v)}
                            />
                            <ToggleRow
                                label={t.feishuMediaAnalyzeVideo || 'Analyze video keyframe'}
                                checked={config.feishu?.media_analysis?.analyze_video_keyframe ?? true}
                                onChange={(v) => handleUpdate("feishu.media_analysis.analyze_video_keyframe", v)}
                            />
                            <div>
                                <label className={labelClass}>{t.feishuMediaTimeoutSeconds || 'Media analysis timeout (seconds)'}</label>
                                <input
                                    type="number"
                                    min="3"
                                    max="60"
                                    className={inputClass}
                                    value={config.feishu?.media_analysis?.timeout_seconds ?? 12}
                                    onChange={(e) => handleUpdate("feishu.media_analysis.timeout_seconds", parseInt(e.target.value, 10) || 12)}
                                />
                            </div>
                        </div>
                    </div>
                </div>
            </div>

        </section>
    </div>
);

/* ================================================================== */
/*  Tab: Web Search                                                    */
/* ================================================================== */
const TabWeb = ({ config, handleUpdate, inputClass, labelClass, sectionClass, t }) => {
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            <section className={sectionClass}>
                <SectionHeader icon={<Globe size={20} />} color="#fbbf24" title={t.webSearchSection || 'Web Search'} desc={t.webSearchDesc || 'Configure primary provider, fallback chain, relevance gate, and API keys.'} />

                <div style={{ background: 'rgba(0,0,0,0.2)', padding: 16, borderRadius: 12, border: '1px solid rgba(255,255,255,0.05)' }}>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label className={labelClass}>{t.webSearchPrimaryProvider || 'Primary Provider'}</label>
                            <select
                                className={inputClass}
                                value={config.web?.search?.primary_provider || "brave"}
                                onChange={(e) => handleUpdate("web.search.primary_provider", e.target.value)}
                            >
                                <option value="brave">{t.searchProviderBrave || 'Brave'}</option>
                                <option value="perplexity">{t.searchProviderPerplexity || 'Perplexity'}</option>
                                <option value="duckduckgo">{t.searchProviderDuckduckgo || 'DuckDuckGo'}</option>
                                <option value="wikipedia">{t.searchProviderWikipedia || 'Wikipedia'}</option>
                                <option value="bing_rss">{t.searchProviderBingRss || 'Bing RSS'}</option>
                            </select>
                            <ToggleRow
                                label={t.webSearchPrimaryOnly || 'Primary only (disable fallback chain)'}
                                checked={config.web?.search?.primary_only ?? false}
                                onChange={(v) => handleUpdate("web.search.primary_only", v)}
                                hint={t.webSearchPrimaryOnlyDesc || 'If enabled, only the primary provider will be used.'}
                            />
                        </div>
                        <div>
                            <label className={labelClass}>{t.webSearchProvidersEnabled || 'Enabled Providers'}</label>
                            <div style={{ marginTop: 6 }}>
                                <ToggleRow
                                    label={t.searchProviderBrave || 'Brave'}
                                    checked={config.web?.search?.providers_enabled?.brave ?? true}
                                    onChange={(v) => handleUpdate("web.search.providers_enabled.brave", v)}
                                />
                                <ToggleRow
                                    label={t.searchProviderPerplexity || 'Perplexity'}
                                    checked={config.web?.search?.providers_enabled?.perplexity ?? false}
                                    onChange={(v) => handleUpdate("web.search.providers_enabled.perplexity", v)}
                                />
                                <ToggleRow
                                    label={t.searchProviderDuckduckgo || 'DuckDuckGo'}
                                    checked={config.web?.search?.providers_enabled?.duckduckgo ?? true}
                                    onChange={(v) => handleUpdate("web.search.providers_enabled.duckduckgo", v)}
                                />
                                <ToggleRow
                                    label={t.searchProviderBingRss || 'Bing RSS'}
                                    checked={config.web?.search?.providers_enabled?.bing_rss ?? true}
                                    onChange={(v) => handleUpdate("web.search.providers_enabled.bing_rss", v)}
                                />
                                <ToggleRow
                                    label={t.searchProviderWikipedia || 'Wikipedia'}
                                    checked={config.web?.search?.providers_enabled?.wikipedia ?? true}
                                    onChange={(v) => handleUpdate("web.search.providers_enabled.wikipedia", v)}
                                />
                            </div>
                        </div>
                        <div>
                            <label className={labelClass}>{t.webSearchProvidersOrder || 'Provider Order'}</label>
                            <p style={{ color: '#6b7280', fontSize: 12, marginTop: 4, marginBottom: 8 }}>
                                {t.webSearchProvidersOrderDesc || 'Execution order for web_search fallback chain.'}
                            </p>
                            <TagInput
                                tags={config.web?.search?.providers_order || ["brave", "perplexity", "duckduckgo", "wikipedia", "bing_rss"]}
                                onChange={(v) => {
                                    const allowed = new Set(["brave", "perplexity", "duckduckgo", "bing_rss", "wikipedia"]);
                                    const normalized = (v || [])
                                        .map((x) => String(x || "").trim().toLowerCase())
                                        .filter((x) => allowed.has(x));
                                    handleUpdate("web.search.providers_order", normalized);
                                }}
                                placeholder="brave / perplexity / duckduckgo / wikipedia / bing_rss"
                            />
                        </div>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4" style={{ marginTop: 8 }}>
                        <div>
                            <label className={labelClass}>{t.webSearchScenarioRouting || 'Scenario Routing'}</label>
                            <ToggleRow
                                label={t.webSearchScenarioRoutingEnabled || 'Enable scenario routing'}
                                checked={config.web?.search?.scenario_routing?.enabled ?? true}
                                onChange={(v) => handleUpdate("web.search.scenario_routing.enabled", v)}
                            />
                            <ToggleRow
                                label={t.webSearchScenarioAutoDetect || 'Auto detect scene from query'}
                                checked={config.web?.search?.scenario_routing?.auto_detect ?? true}
                                onChange={(v) => handleUpdate("web.search.scenario_routing.auto_detect", v)}
                            />
                        </div>
                        <div>
                            <label className={labelClass}>{t.webSearchRelevanceGate || 'Relevance Gate'}</label>
                            <ToggleRow
                                label={t.webSearchRelevanceGateEnabled || 'Enable relevance gate'}
                                checked={config.web?.search?.relevance_gate?.enabled ?? true}
                                onChange={(v) => handleUpdate("web.search.relevance_gate.enabled", v)}
                            />
                            <div style={{ marginTop: 8 }}>
                                <label className={labelClass}>{t.webSearchRelevanceMinScore || 'Min relevance score (0-1)'}</label>
                                <input
                                    type="number"
                                    min="0"
                                    max="1"
                                    step="0.01"
                                    className={inputClass}
                                    value={config.web?.search?.relevance_gate?.min_score ?? 0.25}
                                    onChange={(e) => {
                                        const parsed = parseFloat(e.target.value);
                                        const value = Number.isFinite(parsed) ? Math.max(0, Math.min(1, parsed)) : 0.25;
                                        handleUpdate("web.search.relevance_gate.min_score", value);
                                    }}
                                />
                            </div>
                            <ToggleRow
                                label={t.webSearchAllowLowRelevanceFallback || 'Allow low-relevance fallback output'}
                                checked={config.web?.search?.relevance_gate?.allow_low_relevance_fallback ?? true}
                                onChange={(v) => handleUpdate("web.search.relevance_gate.allow_low_relevance_fallback", v)}
                            />
                        </div>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4" style={{ marginTop: 8 }}>
                        <div>
                            <label className={labelClass}>{t.braveApiKey || 'Brave API Key'}</label>
                            <input
                                type="password"
                                className={inputClass}
                                value={config.web?.search?.brave_api_key || ""}
                                onChange={(e) => handleUpdate("web.search.brave_api_key", e.target.value)}
                                placeholder="BSA..."
                            />
                        </div>
                        <div>
                            <label className={labelClass}>{t.perplexityApiKey || 'Perplexity API Key'}</label>
                            <input
                                type="password"
                                className={inputClass}
                                value={config.web?.search?.perplexity_api_key || ""}
                                onChange={(e) => handleUpdate("web.search.perplexity_api_key", e.target.value)}
                                placeholder="pplx..."
                            />
                        </div>
                        <div>
                            <label className={labelClass}>{t.perplexityBaseUrl || 'Perplexity Base URL'}</label>
                            <input
                                className={inputClass}
                                value={config.web?.search?.perplexity_base_url || "https://api.perplexity.ai"}
                                onChange={(e) => handleUpdate("web.search.perplexity_base_url", e.target.value)}
                                placeholder="https://api.perplexity.ai"
                            />
                        </div>
                        <div>
                            <label className={labelClass}>{t.perplexityModel || 'Perplexity Model'}</label>
                            <input
                                className={inputClass}
                                value={config.web?.search?.perplexity_model || "sonar"}
                                onChange={(e) => handleUpdate("web.search.perplexity_model", e.target.value)}
                                placeholder="sonar"
                            />
                        </div>
                        <div>
                            <label className={labelClass}>{t.webSearchReportFile || 'Search Observation Report File'}</label>
                            <input
                                className={inputClass}
                                value={config.web?.search?.report_file || "data/reports/web_search_observations.jsonl"}
                                onChange={(e) => handleUpdate("web.search.report_file", e.target.value)}
                                placeholder="data/reports/web_search_observations.jsonl"
                            />
                        </div>
                    </div>
                </div>

            </section>
        </div>
    );
};

/* ================================================================== */
/*  Tab: Memory                                                        */
/* ================================================================== */
const TabMemory = ({ config, handleUpdate, inputClass, labelClass, sectionClass, t }) => {
    const normalizeToolNames = (values = []) => {
        const cleaned = (Array.isArray(values) ? values : [])
            .map((x) => String(x || '').trim().toLowerCase())
            .filter(Boolean);
        return Array.from(new Set(cleaned));
    };
    const toolPersistence = config.memory?.tool_result_persistence || {};
    const toolPersistenceMode = String(toolPersistence.mode || 'allowlist').trim().toLowerCase() === 'denylist'
        ? 'denylist'
        : 'allowlist';

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            <section className={sectionClass}>
                <SectionHeader
                    icon={<Database size={20} />}
                    color="#34d399"
                    title={t.memoryToolPersistenceSection || 'Memory Tool Persistence'}
                    desc={t.memoryToolPersistenceDesc || 'Control which tool results are persisted into memory vs trajectory only.'}
                />

                <div style={{ background: 'rgba(0,0,0,0.2)', padding: 16, borderRadius: 12, border: '1px solid rgba(255,255,255,0.05)' }}>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <ToggleRow
                                label={t.memoryToolPersistenceEnabled || 'Enable tool result persistence'}
                                checked={toolPersistence.enabled ?? true}
                                onChange={(v) => handleUpdate("memory.tool_result_persistence.enabled", v)}
                            />
                            <label className={labelClass}>{t.memoryToolPersistenceMode || 'Policy Mode'}</label>
                            <p style={{ color: '#6b7280', fontSize: 12, marginTop: 0, marginBottom: 8 }}>
                                {t.memoryToolPersistenceModeDesc || 'allowlist: only listed tools are written; denylist: all except denied tools are written.'}
                            </p>
                            <select
                                className={inputClass}
                                value={toolPersistenceMode}
                                onChange={(e) => handleUpdate("memory.tool_result_persistence.mode", e.target.value)}
                            >
                                <option value="allowlist">{t.memoryToolPersistenceModeAllowlist || 'Allowlist'}</option>
                                <option value="denylist">{t.memoryToolPersistenceModeDenylist || 'Denylist'}</option>
                            </select>
                            <ToggleRow
                                label={t.memoryToolPersistencePersistOnError || 'Persist failed tool results'}
                                checked={toolPersistence.persist_on_error ?? false}
                                onChange={(v) => handleUpdate("memory.tool_result_persistence.persist_on_error", v)}
                            />
                        </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4" style={{ marginTop: 8 }}>
                        <div>
                            <label className={labelClass}>{t.memoryToolPersistenceAllowTools || 'Allow tools'}</label>
                            <TagInput
                                tags={normalizeToolNames(toolPersistence.allow_tools)}
                                onChange={(v) => handleUpdate("memory.tool_result_persistence.allow_tools", normalizeToolNames(v))}
                                placeholder={t.memoryToolPersistenceAllowPlaceholder || 'e.g. web_search / web_fetch / read_file'}
                            />
                        </div>
                        <div>
                            <label className={labelClass}>{t.memoryToolPersistenceDenyTools || 'Deny tools'}</label>
                            <TagInput
                                tags={normalizeToolNames(toolPersistence.deny_tools)}
                                onChange={(v) => handleUpdate("memory.tool_result_persistence.deny_tools", normalizeToolNames(v))}
                                placeholder={t.memoryToolPersistenceDenyPlaceholder || 'e.g. exec / write_file / node_invoke'}
                            />
                        </div>
                    </div>
                </div>
            </section>
        </div>
    );
};

/* ================================================================== */
/*  Tab: Safety                                                        */
/* ================================================================== */
const TabSafety = ({ config, handleUpdate, inputClass, labelClass, sectionClass, t }) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        <section className={sectionClass}>
            <div style={{ marginTop: 4 }}>
                <SectionHeader
                    icon={<Zap size={18} />}
                    color="#f59e0b"
                    title={t.releaseGateHealthConfigTitle || 'Release Gate Health Thresholds'}
                    desc={t.releaseGateHealthConfigDesc || 'Tune warning/critical thresholds used by Release Gate health linkage.'}
                />
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    <div>
                        <label className={labelClass}>{t.warningSuccessRate || 'Warning Success Rate'}</label>
                        <input
                            type="number"
                            min="0"
                            max="1"
                            step="0.01"
                            className={inputClass}
                            value={config.observability?.release_gate_health_thresholds?.warning_success_rate ?? 0.9}
                            onChange={(e) => handleUpdate("observability.release_gate_health_thresholds.warning_success_rate", parseFloat(e.target.value) || 0)}
                        />
                    </div>
                    <div>
                        <label className={labelClass}>{t.criticalSuccessRate || 'Critical Success Rate'}</label>
                        <input
                            type="number"
                            min="0"
                            max="1"
                            step="0.01"
                            className={inputClass}
                            value={config.observability?.release_gate_health_thresholds?.critical_success_rate ?? 0.75}
                            onChange={(e) => handleUpdate("observability.release_gate_health_thresholds.critical_success_rate", parseFloat(e.target.value) || 0)}
                        />
                    </div>
                    <div>
                        <label className={labelClass}>{t.warningFailures || 'Warning Failures'}</label>
                        <input
                            type="number"
                            min="0"
                            step="1"
                            className={inputClass}
                            value={config.observability?.release_gate_health_thresholds?.warning_failures ?? 1}
                            onChange={(e) => handleUpdate("observability.release_gate_health_thresholds.warning_failures", parseInt(e.target.value) || 0)}
                        />
                    </div>
                    <div>
                        <label className={labelClass}>{t.criticalFailures || 'Critical Failures'}</label>
                        <input
                            type="number"
                            min="0"
                            step="1"
                            className={inputClass}
                            value={config.observability?.release_gate_health_thresholds?.critical_failures ?? 3}
                            onChange={(e) => handleUpdate("observability.release_gate_health_thresholds.critical_failures", parseInt(e.target.value) || 0)}
                        />
                    </div>
                    <div>
                        <label className={labelClass}>{t.warningP95Ms || 'Warning P95 (ms)'}</label>
                        <input
                            type="number"
                            min="0"
                            step="50"
                            className={inputClass}
                            value={config.observability?.release_gate_health_thresholds?.warning_p95_latency_ms ?? 2500}
                            onChange={(e) => handleUpdate("observability.release_gate_health_thresholds.warning_p95_latency_ms", parseInt(e.target.value) || 0)}
                        />
                    </div>
                    <div>
                        <label className={labelClass}>{t.criticalP95Ms || 'Critical P95 (ms)'}</label>
                        <input
                            type="number"
                            min="0"
                            step="50"
                            className={inputClass}
                            value={config.observability?.release_gate_health_thresholds?.critical_p95_latency_ms ?? 4000}
                            onChange={(e) => handleUpdate("observability.release_gate_health_thresholds.critical_p95_latency_ms", parseInt(e.target.value) || 0)}
                        />
                    </div>
                </div>
            </div>

            <div style={{ marginTop: 20 }}>
                <SectionHeader
                    icon={<Monitor size={18} />}
                    color="#22d3ee"
                    title={t.codingExecBackendTitle || 'Coding Execution Backend'}
                    desc={t.codingExecBackendDesc || 'Choose how exec tool runs commands: local, docker sandbox, or remote SSH.'}
                />
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className={labelClass}>{t.codingExecBackend || 'Exec Backend'}</label>
                        <select
                            className={inputClass}
                            value={config.coding?.exec_backend || "local"}
                            onChange={(e) => handleUpdate("coding.exec_backend", e.target.value)}
                        >
                            <option value="local">local</option>
                            <option value="sandbox">sandbox</option>
                            <option value="ssh">ssh</option>
                        </select>
                    </div>
                    <div>
                        <ToggleRow
                            label={t.codingAllowLocalFallback || 'Allow fallback to local backend'}
                            checked={config.coding?.allow_local_fallback ?? false}
                            onChange={(v) => handleUpdate("coding.allow_local_fallback", v)}
                            hint={t.codingAllowLocalFallbackDesc || 'If disabled, sandbox/ssh backend failures will fail fast.'}
                        />
                    </div>
                    <div>
                        <ToggleRow
                            label={t.codingSshEnabled || 'Enable SSH Backend'}
                            checked={config.coding?.ssh?.enabled ?? false}
                            onChange={(v) => handleUpdate("coding.ssh.enabled", v)}
                        />
                    </div>
                    <div>
                        <label className={labelClass}>{t.codingSshHost || 'SSH Host'}</label>
                        <input
                            className={inputClass}
                            value={config.coding?.ssh?.host || ""}
                            onChange={(e) => handleUpdate("coding.ssh.host", e.target.value)}
                            placeholder="192.168.1.10"
                        />
                    </div>
                    <div>
                        <label className={labelClass}>{t.codingSshUser || 'SSH User'}</label>
                        <input
                            className={inputClass}
                            value={config.coding?.ssh?.user || ""}
                            onChange={(e) => handleUpdate("coding.ssh.user", e.target.value)}
                            placeholder="ubuntu"
                        />
                    </div>
                    <div>
                        <label className={labelClass}>{t.codingSshPort || 'SSH Port'}</label>
                        <input
                            type="number"
                            min="1"
                            max="65535"
                            className={inputClass}
                            value={config.coding?.ssh?.port ?? 22}
                            onChange={(e) => handleUpdate("coding.ssh.port", parseInt(e.target.value, 10) || 22)}
                        />
                    </div>
                    <div>
                        <label className={labelClass}>{t.codingSshIdentityFile || 'Identity File'}</label>
                        <input
                            className={inputClass}
                            value={config.coding?.ssh?.identity_file || ""}
                            onChange={(e) => handleUpdate("coding.ssh.identity_file", e.target.value)}
                            placeholder="~/.ssh/id_rsa"
                        />
                    </div>
                    <div>
                        <label className={labelClass}>{t.codingSshRemoteWorkspace || 'Remote Workspace'}</label>
                        <input
                            className={inputClass}
                            value={config.coding?.ssh?.remote_workspace || "."}
                            onChange={(e) => handleUpdate("coding.ssh.remote_workspace", e.target.value)}
                            placeholder="/workspace/project"
                        />
                    </div>
                    <div>
                        <ToggleRow
                            label={t.codingSshStrictHostKey || 'Strict Host Key Checking'}
                            checked={config.coding?.ssh?.strict_host_key_checking ?? true}
                            onChange={(v) => handleUpdate("coding.ssh.strict_host_key_checking", v)}
                        />
                    </div>
                </div>
            </div>
        </section>
    </div>
);

/* ================================================================== */
/*  Tab: Hardware (Perception + Satellite)                             */
/* ================================================================== */
const TabHardware = ({ config, modelProviders, handleUpdate, inputClass, labelClass, sectionClass, t }) => {
    const providerNames = Object.keys(modelProviders || {}).sort();
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            <section className={sectionClass}>
                <SectionHeader icon={<Eye size={20} />} color="#4ade80" title={t.perceptionSection} desc={t.perceptionDesc} />
                {(() => {
                    const satelliteIds = Array.isArray(config.perception?.satellite_ids)
                        ? config.perception.satellite_ids.filter((id) => String(id).trim().length > 0)
                        : [];
                    const satelliteModeActive = satelliteIds.length > 0;
                    return (
                        <>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2">
                                <ToggleRow
                                    label={t.screenEnabled}
                                    checked={config.perception?.screen_enabled ?? true}
                                    onChange={(v) => handleUpdate("perception.screen_enabled", v)}
                                    disabled={satelliteModeActive}
                                />
                                <ToggleRow label={t.cameraEnabled}
                                    checked={config.perception?.camera_enabled ?? false}
                                    onChange={(v) => handleUpdate("perception.camera_enabled", v)} />
                                <ToggleRow label={t.actionEnabled}
                                    checked={config.perception?.action_enabled ?? true}
                                    onChange={(v) => handleUpdate("perception.action_enabled", v)} />
                                <div>
                                    <label className={labelClass}>{t.cameraDeviceIndex || 'Camera Device Index'}</label>
                                    <input type="number" min="0" max="16" className={inputClass}
                                        value={config.perception?.camera_device_index ?? 0}
                                        onChange={(e) => handleUpdate("perception.camera_device_index", Math.max(0, parseInt(e.target.value, 10) || 0))} />
                                </div>
                                <div>
                                    <label className={labelClass}>{t.captureInterval}</label>
                                    <input type="number" min="5" max="3600" className={inputClass}
                                        value={config.perception?.capture_interval ?? 60}
                                        onChange={(e) => handleUpdate("perception.capture_interval", parseInt(e.target.value) || 60)} />
                                </div>
                            </div>
                            <div>
                                <label className={labelClass}>{t.satelliteIds}</label>
                                <TagInput
                                    tags={config.perception?.satellite_ids || []}
                                    onChange={(v) => handleUpdate("perception.satellite_ids", v)}
                                    placeholder={t.addTag} />
                                <p style={{ color: '#6b7280', fontSize: 12, marginTop: 6 }}>
                                    {t.perceptionModeExclusiveHint || "Local and satellite screen modes are mutually exclusive. Configuring satellite IDs disables local screen capture."}
                                </p>
                            </div>
                            <div style={{ marginTop: 10 }} className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <ToggleRow
                                    label={t.spatialEnabled || "Spatial Perception"}
                                    checked={config.perception?.spatial_enabled ?? false}
                                    onChange={(v) => handleUpdate("perception.spatial_enabled", v)}
                                />
                                <div>
                                    <label className={labelClass}>{t.provider}</label>
                                    <select
                                        className={inputClass}
                                        value={config.perception?.spatial?.provider || "local_mediapipe"}
                                        onChange={(e) => handleUpdate("perception.spatial.provider", e.target.value)}
                                    >
                                        <option value="local_mediapipe">local_mediapipe</option>
                                        <option value="cloud_vision">cloud_vision</option>
                                        <option value="hybrid">hybrid</option>
                                    </select>
                                </div>
                                <div>
                                    <label className={labelClass}>{t.routeMode || "Route Mode"}</label>
                                    <select
                                        className={inputClass}
                                        value={config.perception?.spatial?.route_mode || "local_first"}
                                        onChange={(e) => handleUpdate("perception.spatial.route_mode", e.target.value)}
                                    >
                                        <option value="local_first">local_first</option>
                                        <option value="cloud_first">cloud_first</option>
                                        <option value="auto">auto</option>
                                    </select>
                                </div>
                                <div>
                                    <label className={labelClass}>{t.providerRef || "Provider Ref (registry)"}</label>
                                    <select
                                        className={inputClass}
                                        value={config.perception?.spatial?.cloud?.provider_ref || ""}
                                        onChange={(e) => handleUpdate("perception.spatial.cloud.provider_ref", e.target.value)}
                                    >
                                        <option value="">{t.none || "None"}</option>
                                        {providerNames.map((p) => (
                                            <option key={p} value={p}>{p}</option>
                                        ))}
                                    </select>
                                </div>
                                <div>
                                    <label className={labelClass}>{t.model || "Model"}</label>
                                    <input
                                        className={inputClass}
                                        value={config.perception?.spatial?.cloud?.model || ""}
                                        onChange={(e) => handleUpdate("perception.spatial.cloud.model", e.target.value)}
                                    />
                                </div>
                                <div>
                                    <label className={labelClass}>{t.baseUrl || "Base URL"}</label>
                                    <input
                                        className={inputClass}
                                        value={config.perception?.spatial?.cloud?.base_url || ""}
                                        onChange={(e) => handleUpdate("perception.spatial.cloud.base_url", e.target.value)}
                                    />
                                </div>
                                <div>
                                    <label className={labelClass}>{t.apiKey || "API Key"}</label>
                                    <input
                                        type="password"
                                        className={inputClass}
                                        value={config.perception?.spatial?.cloud?.api_key || ""}
                                        onChange={(e) => handleUpdate("perception.spatial.cloud.api_key", e.target.value)}
                                    />
                                </div>
                                <div className="md:col-span-2">
                                    <ToggleRow
                                        label={t.strictCloudRequired || "Strict cloud required"}
                                        hint={t.strictCloudRequiredDesc || "Fail fast when cloud provider is unavailable; do not silently downgrade."}
                                        checked={Boolean(config.perception?.spatial?.cloud?.strict_required ?? false)}
                                        onChange={(v) => handleUpdate("perception.spatial.cloud.strict_required", v)}
                                    />
                                </div>
                            </div>
                            <div style={{ marginTop: 18, paddingTop: 12, borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                                <SectionHeader
                                    icon={<Monitor size={18} />}
                                    color="#34d399"
                                    title={t.bodyNodeSection || 'Body Node'}
                                    desc={t.bodyNodeSectionDesc || 'Expose physical body controls as a node target for node_invoke.'}
                                />
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <ToggleRow
                                        label={t.bodyNodeEnabled || 'Enable Body Node'}
                                        checked={config.devices?.body_node?.enabled ?? true}
                                        onChange={(v) => handleUpdate("devices.body_node.enabled", v)}
                                    />
                                    <ToggleRow
                                        label={t.bodyNodeAllowConnectControl || 'Allow Connect/Disconnect Actions'}
                                        checked={config.devices?.body_node?.allow_connect_control ?? true}
                                        onChange={(v) => handleUpdate("devices.body_node.allow_connect_control", v)}
                                    />
                                    <div>
                                        <label className={labelClass}>{t.bodyNodeId || 'Body Node ID'}</label>
                                        <input
                                            className={inputClass}
                                            value={config.devices?.body_node?.node_id || "body-main"}
                                            onChange={(e) => handleUpdate("devices.body_node.node_id", e.target.value)}
                                        />
                                    </div>
                                    <div>
                                        <label className={labelClass}>{t.bodyNodeLabel || 'Body Node Label'}</label>
                                        <input
                                            className={inputClass}
                                            value={config.devices?.body_node?.label || "Physical Body"}
                                            onChange={(e) => handleUpdate("devices.body_node.label", e.target.value)}
                                        />
                                    </div>
                                </div>
                            </div>
                        </>
                    );
                })()}
            </section>
        </div>
    );
};

/* ================================================================== */
/*  Tab: Persona (Personality + Visual)                                */
/* ================================================================== */
const TabPersona = ({ config, handleUpdate, inputClass, labelClass, sectionClass, t }) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        {/* Personality */}
        <section className={sectionClass}>
            <SectionHeader icon={<User size={20} />} color="#c084fc" title={t.personalitySection} desc={t.personalityDesc} />
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                    <label className={labelClass}>{t.personalityName}</label>
                    <input className={inputClass}
                        value={config.personality?.name || "Gazer"}
                        onChange={(e) => handleUpdate("personality.name", e.target.value)} />
                </div>
                <div>
                    <label className={labelClass}>{t.trustLevel}</label>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        <input type="range" min="0" max="1" step="0.05"
                            style={{ flex: 1, accentColor: '#c084fc' }}
                            value={config.personality?.trust_level ?? 0.5}
                            onChange={(e) => handleUpdate("personality.trust_level", parseFloat(e.target.value))} />
                        <span style={{ color: '#9ca3af', fontSize: 13, minWidth: 36, textAlign: 'right' }}>
                            {(config.personality?.trust_level ?? 0.5).toFixed(2)}
                        </span>
                    </div>
                </div>
            </div>
            <div>
                <label className={labelClass}>{t.systemPrompt}</label>
                <textarea className={inputClass}
                    rows={6}
                    style={{ resize: 'vertical', fontFamily: 'monospace', fontSize: 13, lineHeight: 1.6 }}
                    value={config.personality?.system_prompt || ""}
                    onChange={(e) => handleUpdate("personality.system_prompt", e.target.value)} />
            </div>
            <div style={{ marginTop: 8, padding: 14, border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, background: 'rgba(0,0,0,0.22)' }}>
                <h4 style={{ margin: 0, marginBottom: 10, color: '#e9d5ff', fontSize: 14, fontWeight: 700 }}>
                    {t.autoOptimizeSection || 'Auto Optimize'}
                </h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="md:col-span-2">
                        <ToggleRow
                            label={t.autoEnabled || 'Enable auto optimize'}
                            checked={Boolean(config.personality?.evolution?.auto_optimize?.enabled)}
                            onChange={(v) => handleUpdate("personality.evolution.auto_optimize.enabled", v)}
                        />
                    </div>
                </div>
                <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                    <h5 style={{ margin: 0, marginBottom: 10, color: '#c4b5fd', fontSize: 13, fontWeight: 700 }}>
                        {t.publishGateSection || 'Publish Gate'}
                    </h5>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="md:col-span-2">
                            <ToggleRow
                                label={t.publishGateEnabled || 'Enable publish gate'}
                                checked={Boolean(config.personality?.evolution?.publish_gate?.enabled)}
                                onChange={(v) => handleUpdate("personality.evolution.publish_gate.enabled", v)}
                            />
                            <ToggleRow
                                label={t.publishGateRequireName || 'Require personality name'}
                                checked={Boolean(config.personality?.evolution?.publish_gate?.require_personality_name ?? true)}
                                onChange={(v) => handleUpdate("personality.evolution.publish_gate.require_personality_name", v)}
                            />
                            <ToggleRow
                                label={t.publishGateRespectRelease || 'Respect release gate'}
                                checked={Boolean(config.personality?.evolution?.publish_gate?.respect_release_gate ?? true)}
                                onChange={(v) => handleUpdate("personality.evolution.publish_gate.respect_release_gate", v)}
                            />
                        </div>
                    </div>
                </div>
                <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                    <h5 style={{ margin: 0, marginBottom: 10, color: '#c4b5fd', fontSize: 13, fontWeight: 700 }}>
                        {t.prePublishEvalSection || 'Pre-publish Eval'}
                    </h5>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="md:col-span-2">
                            <ToggleRow
                                label={t.prePublishEvalEnabled || 'Enable pre-publish eval'}
                                checked={Boolean(config.personality?.evolution?.pre_publish_eval?.enabled ?? true)}
                                onChange={(v) => handleUpdate("personality.evolution.pre_publish_eval.enabled", v)}
                            />
                            <ToggleRow
                                label={t.prePublishEvalBlockOnFail || 'Block on fail'}
                                checked={Boolean(config.personality?.evolution?.pre_publish_eval?.block_on_fail ?? true)}
                                onChange={(v) => handleUpdate("personality.evolution.pre_publish_eval.block_on_fail", v)}
                            />
                            <ToggleRow
                                label={t.prePublishEvalSetGate || 'Set release gate on fail'}
                                checked={Boolean(config.personality?.evolution?.pre_publish_eval?.set_release_gate_on_fail ?? true)}
                                onChange={(v) => handleUpdate("personality.evolution.pre_publish_eval.set_release_gate_on_fail", v)}
                            />
                        </div>
                        <div>
                            <label className={labelClass}>{t.prePublishEvalMinScore || 'Min score (0-1)'}</label>
                            <input
                                type="number"
                                min="0"
                                max="1"
                                step="0.01"
                                className={inputClass}
                                value={config.personality?.evolution?.pre_publish_eval?.min_score ?? 0.55}
                                onChange={(e) => handleUpdate("personality.evolution.pre_publish_eval.min_score", Math.max(0, Math.min(1, parseFloat(e.target.value) || 0)))}
                            />
                        </div>
                    </div>
                </div>
            </div>
        </section>

        {/* Visual */}
        <section className={sectionClass}>
            <SectionHeader icon={<Palette size={20} />} color="#22d3ee" title={t.visualSection} desc={t.visualDesc} />
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                    <label className={labelClass}>{t.eyeColor}</label>
                    <div style={{ display: 'flex', gap: 8 }}>
                        {['R', 'G', 'B'].map((ch, i) => (
                            <div key={ch} style={{ flex: 1 }}>
                                <span style={{ fontSize: 11, color: '#6b7280', display: 'block', marginBottom: 2 }}>{ch}</span>
                                <input type="number" min="0" max="255" className={inputClass}
                                    style={{ padding: '6px 8px' }}
                                    value={(config.visual?.eye_color || [0, 200, 255])[i]}
                                    onChange={(e) => {
                                        const color = [...(config.visual?.eye_color || [0, 200, 255])];
                                        color[i] = Math.max(0, Math.min(255, parseInt(e.target.value) || 0));
                                        handleUpdate("visual.eye_color", color);
                                    }} />
                            </div>
                        ))}
                        <div style={{
                            width: 42, height: 42, borderRadius: 8, alignSelf: 'flex-end',
                            border: '1px solid rgba(255,255,255,0.15)',
                            background: `rgb(${(config.visual?.eye_color || [0, 200, 255]).join(',')})`,
                        }} />
                    </div>
                </div>
                <div>
                    <label className={labelClass}>{t.blinkInterval}</label>
                    <input type="number" min="500" max="10000" className={inputClass}
                        value={config.visual?.blink_interval ?? 3000}
                        onChange={(e) => handleUpdate("visual.blink_interval", parseInt(e.target.value) || 3000)} />
                </div>
                <div>
                    <label className={labelClass}>{t.breathingSpeed}</label>
                    <input type="number" min="0.001" max="0.1" step="0.005" className={inputClass}
                        value={config.visual?.breathing_speed ?? 0.02}
                        onChange={(e) => handleUpdate("visual.breathing_speed", parseFloat(e.target.value) || 0.02)} />
                </div>
            </div>
        </section>
    </div>
);

/* ================================================================== */
/*  Tab content router                                                 */
/* ================================================================== */
const TAB_COMPONENTS = {
    model: TabModel,
    voice: TabVoice,
    channels: TabChannels,
    web: TabWeb,
    memory: TabMemory,
    safety: TabSafety,
    hardware: TabHardware,
    persona: TabPersona,
};

/* ================================================================== */
/*  Settings Page                                                       */
/* ================================================================== */
const Settings = ({ config, setConfig, saveConfig, fetchConfig, modelProviders, t }) => {
    const [activeTab, setActiveTab] = useState('model');

    if (!config) return <div style={{ color: '#fff', padding: 32 }}>Loading configuration...</div>;

    const handleUpdate = (path, value) => {
        setConfig((prevConfig) => {
            const newConfig = JSON.parse(JSON.stringify(prevConfig));
            const keys = path.split(".");
            let current = newConfig;
            for (let i = 0; i < keys.length - 1; i++) {
                if (!current[keys[i]]) current[keys[i]] = {};
                current = current[keys[i]];
            }
            current[keys[keys.length - 1]] = value;

            if (!newConfig.perception || typeof newConfig.perception !== 'object') {
                newConfig.perception = {};
            }

            if (path === "perception.satellite_ids") {
                const normalizedSatelliteIds = Array.isArray(value)
                    ? value.map((id) => String(id).trim()).filter((id) => id.length > 0)
                    : [];
                newConfig.perception.satellite_ids = normalizedSatelliteIds;
                if (normalizedSatelliteIds.length > 0) {
                    newConfig.perception.screen_enabled = false;
                }
            }

            if (path === "perception.screen_enabled" && value === true) {
                newConfig.perception.satellite_ids = [];
            }

            return newConfig;
        });
    };

    const inputClass = "w-full bg-black/30 border border-white/10 rounded-lg p-2.5 text-white outline-none focus:border-blue-500 transition-all";
    const labelClass = "block text-sm text-gray-400 mb-1.5";
    const sectionClass = "glass-panel p-6 space-y-4";

    const TabContent = TAB_COMPONENTS[activeTab];

    const tabLabels = {
        model: t.settingsTabModel || 'Models',
        voice: t.settingsTabVoice || 'Voice',
        channels: t.settingsTabChannels || 'Channels',
        web: t.settingsTabWeb || 'Web',
        memory: t.settingsTabMemory || 'Memory',
        safety: t.settingsTabSafety || 'Safety',
        hardware: t.settingsTabHardware || 'Hardware',
        persona: t.settingsTabPersona || 'Persona',
    };

    const handleSaveClick = () => saveConfig();

    return (
        <div style={{ flex: 1, maxWidth: 960, display: 'flex', flexDirection: 'column', gap: 20 }}>
            {/* Header */}
            <header className="flex justify-between items-center">
                <div>
                    <h2 className="text-2xl font-bold text-white mb-2">{t.configSection}</h2>
                    <p className="text-gray-400">{t.configDesc}</p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button onClick={fetchConfig} className="btn-icon">
                        <RefreshCw size={18} />
                    </button>
                    <button onClick={handleSaveClick} className="btn-primary">
                        <Save size={16} /> {t.saveConfig}
                    </button>
                </div>
            </header>

            {/* Tab Bar */}
            <nav style={{
                display: 'flex', gap: 4, padding: 4, borderRadius: 12,
                background: 'rgba(255,255,255,0.04)',
                border: '1px solid rgba(255,255,255,0.06)',
            }}>
                {TABS.map(({ key, icon: Icon, color }) => {
                    const isActive = activeTab === key;
                    return (
                        <button
                            key={key}
                            onClick={() => setActiveTab(key)}
                            style={{
                                flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
                                gap: 8, padding: '10px 12px', borderRadius: 10, border: 'none',
                                cursor: 'pointer', fontSize: 13, fontWeight: 600,
                                transition: 'all 0.2s',
                                background: isActive ? 'rgba(255,255,255,0.08)' : 'transparent',
                                color: isActive ? color : 'rgba(255,255,255,0.45)',
                                boxShadow: isActive ? '0 1px 4px rgba(0,0,0,0.3)' : 'none',
                            }}
                        >
                            <Icon size={16} />
                            <span className="hidden sm:inline">{tabLabels[key]}</span>
                        </button>
                    );
                })}
            </nav>

            {/* Active Tab Content */}
            <TabContent
                config={config}
                modelProviders={modelProviders}
                handleUpdate={handleUpdate}
                inputClass={inputClass}
                labelClass={labelClass}
                sectionClass={sectionClass}
                t={t}
            />
        </div>
    );
};

export default Settings;
