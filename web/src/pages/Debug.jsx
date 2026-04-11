import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Bug, RefreshCw, Play, Cpu, HardDrive, Activity, Thermometer, CircuitBoard, Zap, Settings, ChevronDown, ChevronRight, Hash, Clock } from 'lucide-react';
import API_BASE from '../config';
import ToggleSwitch from '../components/ToggleSwitch';

const Debug = ({ t }) => {
    const [systemInfo, setSystemInfo] = useState(null);
    const [processes, setProcesses] = useState([]);
    const [error, setError] = useState(null);
    const [testResults, setTestResults] = useState([]);
    const [runningTestId, setRunningTestId] = useState(null);
    const [llmHistory, setLlmHistory] = useState([]);
    const [debugConfig, setDebugConfig] = useState(null);
    const [configExpanded, setConfigExpanded] = useState({});
    const [trajectories, setTrajectories] = useState([]);
    const [trajectoryDetail, setTrajectoryDetail] = useState(null);
    const [trajectoryTaskView, setTrajectoryTaskView] = useState(null);
    const [trajectoryReplay, setTrajectoryReplay] = useState(null);
    const [trajectoryComparison, setTrajectoryComparison] = useState(null);
    const [trajectoryResume, setTrajectoryResume] = useState(null);
    const [trajectoryResumeSessionId, setTrajectoryResumeSessionId] = useState('web-main');
    const [trajectoryResumeSendStatus, setTrajectoryResumeSendStatus] = useState('');
    const [taskRuns, setTaskRuns] = useState([]);
    const [codingQuality, setCodingQuality] = useState(null);
    const [codingQualityKind, setCodingQualityKind] = useState('all');
    const [codingBenchmarkHistory, setCodingBenchmarkHistory] = useState([]);
    const [codingBenchmarkLeaderboard, setCodingBenchmarkLeaderboard] = useState(null);
    const [codingBenchmarkScheduler, setCodingBenchmarkScheduler] = useState(null);
    const [codingBenchmarkSchedulerState, setCodingBenchmarkSchedulerState] = useState(null);
    const [codingBenchmarkSchedulerStatus, setCodingBenchmarkSchedulerStatus] = useState('');
    const [codingBenchmarkObservability, setCodingBenchmarkObservability] = useState(null);
    const [codingBenchmarkCasesText, setCodingBenchmarkCasesText] = useState('[]');
    const [selectedTaskId, setSelectedTaskId] = useState('');
    const [selectedTask, setSelectedTask] = useState(null);
    const [trajectoryCompareRunId, setTrajectoryCompareRunId] = useState('');
    const [selectedRunId, setSelectedRunId] = useState('');
    const [trajectoryLimit, setTrajectoryLimit] = useState(50);
    const [trajectorySessionKey, setTrajectorySessionKey] = useState('');
    const [trajectoryLoading, setTrajectoryLoading] = useState(false);
    const [trajectoryDetailLoading, setTrajectoryDetailLoading] = useState(false);
    const [trajectoryError, setTrajectoryError] = useState(null);
    const [trajectoryEventFilter, setTrajectoryEventFilter] = useState('all');
    const [trajectoryEventSearch, setTrajectoryEventSearch] = useState('');
    const [trajectoryToolCallIdFilter, setTrajectoryToolCallIdFilter] = useState('all');
    const [activeTab, setActiveTab] = useState('system'); // system | llm | config | trajectories

    useEffect(() => {
        fetchSystemInfo();
        fetchLlmHistory();
        const interval = setInterval(() => {
            fetchSystemInfo();
            if (activeTab === 'llm') fetchLlmHistory();
            if (activeTab === 'trajectories') {
                fetchTrajectories();
                fetchTaskRuns();
                fetchCodingQuality();
                fetchCodingBenchmarkHistory();
                fetchCodingBenchmarkLeaderboard();
                fetchCodingBenchmarkScheduler();
                fetchCodingBenchmarkObservability();
            }
        }, 5000);
        return () => clearInterval(interval);
    }, [activeTab]);

    const fetchSystemInfo = async () => {
        try {
            const res = await axios.get(`${API_BASE}/debug/system`);
            setSystemInfo(res.data);
            setProcesses(res.data.processes || []);
            setError(null);
        } catch (err) {
            console.error("Failed to fetch system info", err);
            setError("Failed to fetch system info. Backend may be offline.");
        }
    };

    const fetchLlmHistory = async () => {
        try {
            const res = await axios.get(`${API_BASE}/debug/llm-history?limit=50`);
            setLlmHistory(res.data.calls || []);
        } catch (err) {
            console.error("Failed to fetch LLM history", err);
        }
    };

    const fetchDebugConfig = async () => {
        try {
            const res = await axios.get(`${API_BASE}/debug/config`);
            setDebugConfig(res.data);
        } catch (err) {
            console.error("Failed to fetch config", err);
        }
    };

    const fetchTrajectories = async () => {
        setTrajectoryLoading(true);
        setTrajectoryError(null);
        try {
            const params = { limit: Number(trajectoryLimit) || 50 };
            if (trajectorySessionKey.trim()) params.session_key = trajectorySessionKey.trim();
            const res = await axios.get(`${API_BASE}/debug/trajectories`, { params });
            const items = Array.isArray(res.data?.items) ? res.data.items : [];
            setTrajectories(items);
            if (!selectedRunId && items.length > 0) {
                setSelectedRunId(items[0].run_id);
                fetchTrajectoryDetail(items[0].run_id);
            }
        } catch (err) {
            console.error("Failed to fetch trajectories", err);
            setTrajectoryError(t.debugTrajFetchFailed || "Failed to fetch trajectories.");
        } finally {
            setTrajectoryLoading(false);
        }
    };

    const fetchTaskRuns = async () => {
        try {
            const res = await axios.get(`${API_BASE}/debug/task-runs?limit=50`);
            const items = Array.isArray(res.data?.items) ? res.data.items : [];
            setTaskRuns(items);
            if (!selectedTaskId && items.length > 0) {
                setSelectedTaskId(items[0].task_id);
                fetchTaskDetail(items[0].task_id);
            }
        } catch (err) {
            console.error("Failed to fetch task runs", err);
            setTaskRuns([]);
        }
    };

    const fetchCodingQuality = async (kindOverride = null) => {
        try {
            const kind = kindOverride ?? codingQualityKind;
            const params = { window: 100 };
            if (kind && kind !== 'all') params.kind = kind;
            const res = await axios.get(`${API_BASE}/debug/coding-quality`, { params });
            setCodingQuality(res.data?.metrics || null);
        } catch (err) {
            console.error("Failed to fetch coding quality", err);
            setCodingQuality(null);
        }
    };

    const fetchCodingBenchmarkHistory = async () => {
        try {
            const res = await axios.get(`${API_BASE}/debug/coding-benchmark/history`, {
                params: { limit: 20 },
            });
            setCodingBenchmarkHistory(Array.isArray(res.data?.items) ? res.data.items : []);
        } catch (err) {
            console.error("Failed to fetch coding benchmark history", err);
            setCodingBenchmarkHistory([]);
        }
    };

    const fetchCodingBenchmarkLeaderboard = async () => {
        try {
            const res = await axios.get(`${API_BASE}/debug/coding-benchmark/leaderboard`, {
                params: { window: 20 },
            });
            setCodingBenchmarkLeaderboard(res.data?.leaderboard || null);
        } catch (err) {
            console.error("Failed to fetch coding benchmark leaderboard", err);
            setCodingBenchmarkLeaderboard(null);
        }
    };

    const fetchCodingBenchmarkScheduler = async () => {
        try {
            const res = await axios.get(`${API_BASE}/debug/coding-benchmark/scheduler`);
            const scheduler = res.data?.scheduler || null;
            setCodingBenchmarkScheduler(scheduler);
            setCodingBenchmarkSchedulerState(res.data?.state || null);
            const cases = Array.isArray(scheduler?.payload?.cases) ? scheduler.payload.cases : [];
            setCodingBenchmarkCasesText(JSON.stringify(cases, null, 2));
        } catch (err) {
            console.error("Failed to fetch coding benchmark scheduler", err);
            setCodingBenchmarkScheduler(null);
            setCodingBenchmarkSchedulerState(null);
            setCodingBenchmarkCasesText('[]');
        }
    };

    const fetchCodingBenchmarkObservability = async () => {
        try {
            const res = await axios.get(`${API_BASE}/debug/coding-benchmark/observability`, {
                params: { window: 60 },
            });
            setCodingBenchmarkObservability(res.data?.observability || null);
        } catch (err) {
            console.error("Failed to fetch coding benchmark observability", err);
            setCodingBenchmarkObservability(null);
        }
    };

    const updateSchedulerField = (path, value) => {
        setCodingBenchmarkScheduler((prev) => {
            const next = JSON.parse(JSON.stringify(prev || {}));
            const keys = path.split('.');
            let cur = next;
            for (let i = 0; i < keys.length - 1; i += 1) {
                const k = keys[i];
                if (!cur[k] || typeof cur[k] !== 'object') cur[k] = {};
                cur = cur[k];
            }
            cur[keys[keys.length - 1]] = value;
            return next;
        });
    };

    const saveCodingBenchmarkScheduler = async () => {
        let parsedCases = [];
        try {
            const parsed = JSON.parse(codingBenchmarkCasesText || '[]');
            if (!Array.isArray(parsed)) {
                throw new Error('payload.cases must be an array');
            }
            parsedCases = parsed;
        } catch {
            setCodingBenchmarkSchedulerStatus(t.debugCodingBenchmarkCasesInvalid || 'Invalid payload.cases JSON');
            return;
        }
        updateSchedulerField('payload.cases', parsedCases);
        const nextScheduler = JSON.parse(JSON.stringify(codingBenchmarkScheduler || {}));
        if (!nextScheduler.payload || typeof nextScheduler.payload !== 'object') nextScheduler.payload = {};
        nextScheduler.payload.cases = parsedCases;
        if (Boolean(nextScheduler?.enabled) && parsedCases.length <= 0) {
            setCodingBenchmarkSchedulerStatus(
                t.debugCodingBenchmarkSchedulerCasesEmpty || 'Enabled but payload.cases is empty.'
            );
            return;
        }
        try {
            setCodingBenchmarkSchedulerStatus(t.debugCodingBenchmarkSaving || 'Saving...');
            await axios.post(`${API_BASE}/config`, {
                security: {
                    coding_benchmark_scheduler: nextScheduler,
                },
            });
            setCodingBenchmarkSchedulerStatus(t.debugCodingBenchmarkSaved || 'Saved');
            fetchCodingBenchmarkScheduler();
        } catch (err) {
            console.error("Failed to save coding benchmark scheduler", err);
            const detail = err?.response?.data?.detail;
            setCodingBenchmarkSchedulerStatus(
                detail
                    ? `${t.debugCodingBenchmarkSaveFailed || 'Save failed'}: ${detail}`
                    : (t.debugCodingBenchmarkSaveFailed || 'Save failed')
            );
        }
    };

    const runCodingBenchmarkSchedulerNow = async () => {
        try {
            setCodingBenchmarkSchedulerStatus(t.debugCodingBenchmarkRunNowRunning || 'Running...');
            await axios.post(`${API_BASE}/debug/coding-benchmark/scheduler/run-now`);
            setCodingBenchmarkSchedulerStatus(t.debugCodingBenchmarkRunNowDone || 'Run completed');
            fetchCodingBenchmarkHistory();
            fetchCodingBenchmarkLeaderboard();
            fetchCodingBenchmarkScheduler();
            fetchCodingBenchmarkObservability();
        } catch (err) {
            console.error("Failed to run coding benchmark scheduler now", err);
            const detail = err?.response?.data?.detail;
            setCodingBenchmarkSchedulerStatus(
                detail
                    ? `${t.debugCodingBenchmarkRunNowFailed || 'Run failed'}: ${detail}`
                    : (t.debugCodingBenchmarkRunNowFailed || 'Run failed')
            );
        }
    };

    const fetchTaskDetail = async (taskId) => {
        if (!taskId) return;
        try {
            const res = await axios.get(`${API_BASE}/debug/task-runs/${taskId}`);
            setSelectedTask(res.data?.task || null);
        } catch (err) {
            console.error("Failed to fetch task detail", err);
            setSelectedTask(null);
        }
    };

    const fetchTrajectoryDetail = async (runId) => {
        if (!runId) return;
        setTrajectoryDetailLoading(true);
        setTrajectoryError(null);
        try {
            const res = await axios.get(`${API_BASE}/debug/trajectories/${runId}`);
            setTrajectoryDetail(res.data || null);
            setTrajectoryToolCallIdFilter('all');
            fetchTrajectoryTaskView(runId);
            fetchTrajectoryReplay(runId);
            fetchTrajectoryResume(runId);
        } catch (err) {
            console.error("Failed to fetch trajectory detail", err);
            setTrajectoryError(t.debugTrajDetailFetchFailed || "Failed to fetch trajectory detail.");
            setTrajectoryDetail(null);
            setTrajectoryTaskView(null);
            setTrajectoryReplay(null);
            setTrajectoryComparison(null);
            setTrajectoryResume(null);
        } finally {
            setTrajectoryDetailLoading(false);
        }
    };

    const fetchTrajectoryTaskView = async (runId) => {
        try {
            const res = await axios.get(`${API_BASE}/debug/trajectories/${runId}/task-view`);
            setTrajectoryTaskView(res.data || null);
        } catch (err) {
            console.error("Failed to fetch trajectory task view", err);
            setTrajectoryTaskView(null);
        }
    };

    const fetchTrajectoryReplay = async (runId, compareRunId = '') => {
        try {
            const params = {};
            if (compareRunId.trim()) params.compare_run_id = compareRunId.trim();
            const res = await axios.get(`${API_BASE}/debug/trajectories/${runId}/replay-preview`, { params });
            setTrajectoryReplay(res.data?.replay || null);
            setTrajectoryComparison(res.data?.comparison || null);
        } catch (err) {
            console.error("Failed to fetch trajectory replay preview", err);
            setTrajectoryReplay(null);
            setTrajectoryComparison(null);
        }
    };

    const fetchTrajectoryResume = async (runId) => {
        try {
            const res = await axios.get(`${API_BASE}/debug/trajectories/${runId}/resume`);
            setTrajectoryResume(res.data || null);
            setTrajectoryResumeSendStatus('');
        } catch (err) {
            console.error("Failed to fetch trajectory resume", err);
            setTrajectoryResume(null);
            setTrajectoryResumeSendStatus('');
        }
    };

    const sendTrajectoryResume = async (runId) => {
        if (!runId) return;
        try {
            const sessionId = trajectoryResumeSessionId.trim() || 'web-main';
            const res = await axios.post(`${API_BASE}/debug/trajectories/${runId}/resume/send`, {
                session_id: sessionId,
            });
            setTrajectoryResumeSendStatus(
                `${t.debugTrajResumeSent || 'Sent to'} ${res.data?.chat_id || sessionId}`
            );
            fetchTaskRuns();
        } catch (err) {
            console.error("Failed to send trajectory resume", err);
            const detail = err?.response?.data?.detail;
            setTrajectoryResumeSendStatus(
                detail
                    ? `${t.debugTrajResumeSendFailed || 'Send failed'}: ${detail}`
                    : (t.debugTrajResumeSendFailed || 'Send failed')
            );
        }
    };

    const autoResumeTrajectory = async (runId) => {
        if (!runId) return;
        try {
            const sessionId = trajectoryResumeSessionId.trim() || 'web-main';
            const res = await axios.post(`${API_BASE}/debug/trajectories/${runId}/resume/auto`, {
                session_id: sessionId,
            });
            setTrajectoryResumeSendStatus(
                `${t.debugTrajResumeSent || 'Sent to'} ${res.data?.chat_id || sessionId} (${t.debugTrajAutoMode || 'auto'})`
            );
            fetchTaskRuns();
        } catch (err) {
            console.error("Failed to auto resume trajectory", err);
        }
    };

    const replayExecuteTrajectory = async (runId) => {
        if (!runId) return;
        try {
            const sessionId = trajectoryResumeSessionId.trim() || 'web-main';
            await axios.post(`${API_BASE}/debug/trajectories/${runId}/replay-execute`, {
                session_id: sessionId,
                compare_run_id: trajectoryCompareRunId.trim(),
            });
            setTrajectoryResumeSendStatus(t.debugTrajReplayEnqueued || 'Replay execution enqueued');
            fetchTaskRuns();
        } catch (err) {
            console.error("Failed to enqueue replay execution", err);
        }
    };

    const runCodingLoop = async (taskId) => {
        if (!taskId) return;
        try {
            const sessionId = trajectoryResumeSessionId.trim() || 'web-main';
            await axios.post(`${API_BASE}/debug/task-runs/${taskId}/coding-loop`, {
                session_id: sessionId,
            });
            fetchTaskDetail(taskId);
            fetchTaskRuns();
        } catch (err) {
            console.error("Failed to run coding loop", err);
        }
    };

    const runTest = async (testName) => {
        setRunningTestId(testName);
        try {
            const res = await axios.post(`${API_BASE}/debug/test/${testName}`);
            setTestResults(prev => [...prev, {
                name: testName,
                success: res.data.success,
                message: res.data.message,
                timestamp: new Date().toISOString()
            }]);
        } catch (err) {
            setTestResults(prev => [...prev, {
                name: testName,
                success: false,
                message: err.message,
                timestamp: new Date().toISOString()
            }]);
        }
        setRunningTestId(null);
    };

    const diagnosticTests = [
        { id: 'llm_connection', name: t.tests.llm.name, description: t.tests.llm.desc },
        { id: 'tts_synthesis', name: t.tests.tts.name, description: t.tests.tts.desc },
        { id: 'asr_recognition', name: t.tests.asr.name, description: t.tests.asr.desc },
        { id: 'memory_index', name: t.tests.memory.name, description: t.tests.memory.desc },
        { id: 'hardware_bridge', name: t.tests.hardware.name, description: t.tests.hardware.desc },
    ];

    const formatUptime = (seconds) => {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        return `${h}h ${m}m`;
    };

    const toggleConfigKey = (key) => {
        setConfigExpanded(prev => ({ ...prev, [key]: !prev[key] }));
    };

    const renderConfigValue = (value, key, depth = 0) => {
        if (value === null || value === undefined) return <span className="text-gray-500">null</span>;
        if (typeof value === 'boolean') return <span className={value ? 'text-green-400' : 'text-red-400'}>{String(value)}</span>;
        if (typeof value === 'number') return <span className="text-amber-400">{value}</span>;
        if (typeof value === 'string') {
            if (value === '***') return <span className="text-red-400/60 italic">••••••</span>;
            return <span className="text-emerald-400">"{value.length > 80 ? value.slice(0, 80) + '...' : value}"</span>;
        }
        if (Array.isArray(value)) {
            if (value.length === 0) return <span className="text-gray-500">[]</span>;
            return (
                <div style={{ marginLeft: depth > 0 ? 16 : 0 }}>
                    {value.map((item, i) => (
                        <div key={i} className="text-xs"><span className="text-gray-600">[{i}]</span> {renderConfigValue(item, `${key}.${i}`, depth + 1)}</div>
                    ))}
                </div>
            );
        }
        if (typeof value === 'object') {
            const entries = Object.entries(value);
            const isExpanded = configExpanded[key] !== false; // default expanded for top-level
            return (
                <div style={{ marginLeft: depth > 0 ? 16 : 0 }}>
                    <button
                        onClick={() => toggleConfigKey(key)}
                        className="flex items-center gap-1 text-xs text-gray-400 hover:text-white"
                    >
                        {isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                        <span className="text-cyan-400 font-medium">{depth === 0 ? key : ''}</span>
                        <span className="text-gray-600">({entries.length} keys)</span>
                    </button>
                    {isExpanded && entries.map(([k, v]) => (
                        <div key={k} className="flex items-start gap-2 text-xs py-0.5" style={{ marginLeft: 8 }}>
                            <span className="text-cyan-400/70 whitespace-nowrap">{k}:</span>
                            {renderConfigValue(v, `${key}.${k}`, depth + 1)}
                        </div>
                    ))}
                </div>
            );
        }
        return <span className="text-gray-400">{String(value)}</span>;
    };

    const tabs = [
        { id: 'system', label: t.debugTabSystem || 'System', icon: <Cpu size={14} /> },
        { id: 'llm', label: t.debugTabLLM || 'LLM History', icon: <Zap size={14} /> },
        { id: 'config', label: t.debugTabConfig || 'Config', icon: <Settings size={14} /> },
        { id: 'trajectories', label: t.debugTabTrajectories || 'Trajectories', icon: <Activity size={14} /> },
    ];

    const getFilteredTrajectoryEvents = () => {
        const events = Array.isArray(trajectoryDetail?.events) ? trajectoryDetail.events : [];
        const keyword = trajectoryEventSearch.trim().toLowerCase();
        return events.filter((evt) => {
            const payload = evt?.payload || {};
            const action = String(evt?.action || '').toLowerCase();
            const resultStatus = String(payload?.status || '').toLowerCase();
            const toolCallId = String(payload?.tool_call_id || '');
            if (trajectoryEventFilter === 'tool_call' && action !== 'tool_call') return false;
            if (trajectoryEventFilter === 'tool_result' && action !== 'tool_result') return false;
            if (trajectoryEventFilter === 'inbound_metadata' && action !== 'inbound_metadata') return false;
            if (trajectoryEventFilter === 'error' && !(action === 'tool_result' && resultStatus === 'error')) {
                return false;
            }
            if (trajectoryToolCallIdFilter !== 'all' && toolCallId !== trajectoryToolCallIdFilter) return false;
            if (!keyword) return true;
            const haystack = JSON.stringify({
                stage: evt?.stage,
                action: evt?.action,
                payload,
            }).toLowerCase();
            return haystack.includes(keyword);
        });
    };

    const getTrajectoryToolCallIds = () => {
        const events = Array.isArray(trajectoryDetail?.events) ? trajectoryDetail.events : [];
        const seen = new Set();
        const ids = [];
        for (const evt of events) {
            const toolCallId = String(evt?.payload?.tool_call_id || '').trim();
            if (!toolCallId || seen.has(toolCallId)) continue;
            seen.add(toolCallId);
            ids.push(toolCallId);
        }
        return ids;
    };

    const resetTrajectoryFilters = () => {
        setTrajectoryEventFilter('all');
        setTrajectoryEventSearch('');
        setTrajectoryToolCallIdFilter('all');
    };

    return (
        <div className="flex flex-col space-y-6" style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 28 }}>
            <header className="flex justify-between items-center">
                <div>
                    <h2 className="text-2xl font-bold text-white mb-2 flex items-center gap-2">
                        <Bug size={24} className="text-red-400" />
                        {t.debugTitle}
                    </h2>
                    <p className="text-gray-400">{t.debugDesc}</p>
                </div>
                <button onClick={() => {
                    fetchSystemInfo();
                    fetchLlmHistory();
                    if (activeTab === 'config') fetchDebugConfig();
                    if (activeTab === 'trajectories') {
                        fetchTrajectories();
                        fetchTaskRuns();
                        fetchCodingQuality();
                        fetchCodingBenchmarkHistory();
                        fetchCodingBenchmarkLeaderboard();
                        fetchCodingBenchmarkScheduler();
                        fetchCodingBenchmarkObservability();
                    }
                }} className="btn-icon">
                    <RefreshCw size={18} />
                </button>
            </header>

            {error && (
                <div className="glass-panel p-4 text-red-400 text-center">
                    {error}
                </div>
            )}

            {/* Tab Navigation */}
            <div className="flex gap-2">
                {tabs.map(tab => (
                    <button
                        key={tab.id}
                        onClick={() => {
                            setActiveTab(tab.id);
                            if (tab.id === 'config' && !debugConfig) fetchDebugConfig();
                            if (tab.id === 'llm') fetchLlmHistory();
                            if (tab.id === 'trajectories') {
                                fetchTrajectories();
                                fetchTaskRuns();
                                fetchCodingQuality();
                                fetchCodingBenchmarkHistory();
                                fetchCodingBenchmarkLeaderboard();
                                fetchCodingBenchmarkScheduler();
                                fetchCodingBenchmarkObservability();
                            }
                        }}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                            activeTab === tab.id
                                ? 'bg-white/10 text-white border border-white/20'
                                : 'text-gray-500 hover:text-gray-300 hover:bg-white/5'
                        }`}
                    >
                        {tab.icon}
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* === System Tab === */}
            {activeTab === 'system' && (
                <>
                    {/* System Metrics */}
                    <div
                        className="grid grid-cols-2 md:grid-cols-4 gap-4"
                        style={{ display: 'flex', flexDirection: 'column', gap: 28 }}
                    >
                        <div className="glass-panel p-4">
                            <div className="flex items-center gap-2 text-gray-400 text-sm mb-2">
                                <Cpu size={16} />
                                {t.cpuUsage}
                            </div>
                            <div className="text-2xl font-bold text-white">
                                {systemInfo?.cpu_percent?.toFixed(1) || '--'}%
                            </div>
                            <div className="mt-2 h-2 bg-black/30 rounded-full overflow-hidden">
                                <div
                                    className="h-full bg-blue-500 transition-all"
                                    style={{ width: `${systemInfo?.cpu_percent || 0}%` }}
                                />
                            </div>
                        </div>

                        <div className="glass-panel p-4">
                            <div className="flex items-center gap-2 text-gray-400 text-sm mb-2">
                                <CircuitBoard size={16} />
                                {t.memoryUsage}
                            </div>
                            <div className="text-2xl font-bold text-white">
                                {systemInfo?.memory_used_gb?.toFixed(1) || '--'} GB
                            </div>
                            <div className="text-xs text-gray-500">
                                / {systemInfo?.memory_total_gb?.toFixed(1) || '--'} GB ({systemInfo?.memory_percent?.toFixed(0)}%)
                            </div>
                            <div className="mt-2 h-2 bg-black/30 rounded-full overflow-hidden">
                                <div
                                    className="h-full bg-green-500 transition-all"
                                    style={{ width: `${systemInfo?.memory_percent || 0}%` }}
                                />
                            </div>
                        </div>

                        <div className="glass-panel p-4">
                            <div className="flex items-center gap-2 text-gray-400 text-sm mb-2">
                                <HardDrive size={16} />
                                {t.diskUsage}
                            </div>
                            <div className="text-2xl font-bold text-white">
                                {systemInfo?.disk_percent?.toFixed(1) || '--'}%
                            </div>
                            <div className="mt-2 h-2 bg-black/30 rounded-full overflow-hidden">
                                <div
                                    className="h-full bg-purple-500 transition-all"
                                    style={{ width: `${systemInfo?.disk_percent || 0}%` }}
                                />
                            </div>
                        </div>

                        <div className="glass-panel p-4">
                            <div className="flex items-center gap-2 text-gray-400 text-sm mb-2">
                                <Activity size={16} />
                                {t.uptime}
                            </div>
                            <div className="text-2xl font-bold text-white">
                                {formatUptime(systemInfo?.uptime_seconds || 0)}
                            </div>
                            <div className="text-xs text-gray-500">
                                Python {systemInfo?.python_version || '--'}
                            </div>
                        </div>
                    </div>

                    {/* Process Status */}
                    <section className="glass-panel p-4">
                        <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                            <Thermometer size={18} className="text-orange-400" />
                            {t.processStatus}
                        </h3>
                        <div className="space-y-2">
                            {processes.map((proc, idx) => (
                                <div key={idx} className="flex items-center justify-between p-3 bg-black/20 rounded-lg">
                                    <div className="flex items-center gap-3">
                                        <div className={`w-3 h-3 rounded-full ${proc.status === 'running' ? 'bg-green-500' : 'bg-red-500'}`} />
                                        <span className="text-white font-medium">{proc.name}</span>
                                        {proc.pid && <span className="text-gray-500 text-sm">PID: {proc.pid}</span>}
                                    </div>
                                    <div className="flex items-center gap-4">
                                        <span className="text-gray-400 text-sm">{proc.memory_mb} MB</span>
                                        <span className={`px-2 py-1 rounded text-xs ${proc.status === 'running' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                                            {proc.status?.toUpperCase() ?? 'UNKNOWN'}
                                        </span>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </section>

                    {/* Diagnostic Tests */}
                    <section className="glass-panel p-4">
                        <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                            <Play size={18} className="text-cyan-400" />
                            {t.diagnosticTests}
                        </h3>
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                            {diagnosticTests.map((test) => (
                                <button
                                    key={test.id}
                                    onClick={() => runTest(test.id)}
                                    disabled={runningTestId !== null}
                                    style={{
                                        padding: '14px 16px',
                                        background: 'rgba(0,0,0,0.2)',
                                        border: '1px solid rgba(255,255,255,0.08)',
                                        borderRadius: 10,
                                        textAlign: 'left',
                                        opacity: runningTestId !== null ? 0.5 : 1,
                                        cursor: runningTestId !== null ? 'not-allowed' : 'pointer',
                                    }}
                                    onMouseEnter={(e) => { if (!runningTestId) e.currentTarget.style.background = 'rgba(255,255,255,0.04)'; }}
                                    onMouseLeave={(e) => { e.currentTarget.style.background = 'rgba(0,0,0,0.2)'; }}
                                >
                                    <div style={{ color: '#fff', fontWeight: 500, fontSize: 13 }}>{test.name}</div>
                                    <div style={{ color: '#667', fontSize: 11, marginTop: 4 }}>{test.description}</div>
                                    {runningTestId === test.id && (
                                        <div style={{ color: '#22d3ee', fontSize: 11, marginTop: 6 }}>Running...</div>
                                    )}
                                </button>
                            ))}
                        </div>
                    </section>

                    {/* Test Results */}
                    {testResults.length > 0 && (
                        <section className="glass-panel p-4">
                            <h3 className="text-lg font-bold text-white mb-4">{t.testResults}</h3>
                            <div className="space-y-2 font-mono text-sm">
                                {testResults.map((result, idx) => (
                                    <div
                                        key={idx}
                                        className={`p-3 rounded-lg flex items-center justify-between ${result.success ? 'bg-green-500/10 border border-green-500/30' : 'bg-red-500/10 border border-red-500/30'}`}
                                    >
                                        <div className="flex items-center gap-3">
                                            <span className={result.success ? 'text-green-400' : 'text-red-400'}>
                                                {result.success ? '✓' : '✗'}
                                            </span>
                                            <span className="text-white">{result.name}</span>
                                        </div>
                                        <span className="text-gray-400 text-xs">{result.message}</span>
                                    </div>
                                ))}
                            </div>
                        </section>
                    )}
                </>
            )}

            {/* === LLM History Tab === */}
            {activeTab === 'llm' && (
                <section className="glass-panel p-4 flex-1 overflow-hidden flex flex-col">
                    <h3 className="text-lg font-bold text-white mb-4 flex items-center gap-2">
                        <Zap size={18} className="text-amber-400" />
                        {t.debugLlmHistory || 'LLM Call History'}
                        <span className="text-sm font-normal text-gray-500 ml-2">{llmHistory.length} calls</span>
                    </h3>
                    {llmHistory.length === 0 ? (
                        <div className="flex-1 flex items-center justify-center text-gray-500">
                            {t.debugNoLlmCalls || 'No LLM calls recorded yet.'}
                        </div>
                    ) : (
                        <div className="flex-1 overflow-y-auto space-y-2 font-mono text-sm">
                            {[...llmHistory].reverse().map((call, idx) => (
                                <div
                                    key={idx}
                                    className={`p-3 rounded-lg border ${
                                        call.level === 'ERROR'
                                            ? 'bg-red-500/10 border-red-500/20'
                                            : 'bg-black/20 border-white/5'
                                    }`}
                                >
                                    <div className="flex items-center justify-between mb-2">
                                        <div className="flex items-center gap-2">
                                            {call.model && (
                                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-cyan-500/15 text-cyan-400 font-medium">
                                                    <Cpu size={10} />
                                                    {call.model}
                                                </span>
                                            )}
                                            {call.request_id && (
                                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-violet-500/15 text-violet-400">
                                                    <Hash size={10} />
                                                    {call.request_id}
                                                </span>
                                            )}
                                        </div>
                                        <div className="flex items-center gap-3">
                                            {call.tokens && (
                                                <span className="text-xs text-gray-500">
                                                    {call.tokens.prompt_tokens != null && `↑${call.tokens.prompt_tokens}`}
                                                    {call.tokens.completion_tokens != null && ` ↓${call.tokens.completion_tokens}`}
                                                    {call.tokens.total_tokens != null && ` Σ${call.tokens.total_tokens}`}
                                                </span>
                                            )}
                                            <span className="text-xs text-gray-600 flex items-center gap-1">
                                                <Clock size={10} />
                                                {new Date(call.timestamp).toLocaleTimeString()}
                                            </span>
                                        </div>
                                    </div>
                                    <div className="text-xs text-gray-400 truncate">{call.message}</div>
                                </div>
                            ))}
                        </div>
                    )}
                </section>
            )}

            {/* === Config Viewer Tab === */}
            {activeTab === 'config' && (
                <section className="glass-panel p-4 flex-1 overflow-hidden flex flex-col">
                    <div className="flex items-center justify-between mb-4">
                        <h3 className="text-lg font-bold text-white flex items-center gap-2">
                            <Settings size={18} className="text-gray-400" />
                            {t.debugConfigViewer || 'Config Viewer'}
                        </h3>
                        <button onClick={fetchDebugConfig} className="btn-icon">
                            <RefreshCw size={14} />
                        </button>
                    </div>
                    {!debugConfig ? (
                        <div className="flex-1 flex items-center justify-center text-gray-500">
                            {t.debugLoadingConfig || 'Loading config...'}
                        </div>
                    ) : (
                        <div className="flex-1 overflow-y-auto font-mono text-sm space-y-1">
                            {Object.entries(debugConfig).map(([key, value]) => (
                                <div key={key} className="p-2 rounded hover:bg-white/5">
                                    {typeof value === 'object' && value !== null && !Array.isArray(value)
                                        ? renderConfigValue(value, key, 0)
                                        : (
                                            <div className="flex items-start gap-2 text-xs">
                                                <span className="text-cyan-400 font-medium">{key}:</span>
                                                {renderConfigValue(value, key, 1)}
                                            </div>
                                        )
                                    }
                                </div>
                            ))}
                        </div>
                    )}
                    <div className="mt-3 pt-3 border-t border-white/5 text-xs text-gray-600">
                        {t.debugConfigNote || 'Sensitive values (API keys, passwords) are redacted.'}
                    </div>
                </section>
            )}

            {/* === Trajectories Tab === */}
            {activeTab === 'trajectories' && (
                <section className="glass-panel p-4 flex-1 overflow-hidden flex flex-col">
                    <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
                        <h3 className="text-lg font-bold text-white flex items-center gap-2">
                            <Activity size={18} className="text-cyan-400" />
                            {t.debugTrajectoryViewer || 'Trajectory Replay'}
                        </h3>
                        <div className="flex items-center gap-2">
                            <input
                                value={trajectorySessionKey}
                                onChange={(e) => setTrajectorySessionKey(e.target.value)}
                                placeholder={t.debugTrajectorySession || 'session_key (optional)'}
                                className="px-3 py-1.5 rounded bg-black/20 border border-white/10 text-sm text-white w-56"
                            />
                            <input
                                type="number"
                                min={1}
                                max={200}
                                value={trajectoryLimit}
                                onChange={(e) => setTrajectoryLimit(e.target.value)}
                                className="px-3 py-1.5 rounded bg-black/20 border border-white/10 text-sm text-white w-20"
                            />
                            <button onClick={fetchTrajectories} className="btn-icon">
                                <RefreshCw size={14} />
                            </button>
                        </div>
                    </div>
                    <div className="flex items-center gap-2 mb-3 flex-wrap">
                        <select
                            value={trajectoryEventFilter}
                            onChange={(e) => setTrajectoryEventFilter(e.target.value)}
                            className="px-3 py-1.5 rounded bg-black/20 border border-white/10 text-sm text-white"
                        >
                            <option value="all">{t.debugTrajFilterAll || 'All Events'}</option>
                            <option value="tool_call">{t.debugTrajFilterToolCall || 'Tool Calls'}</option>
                            <option value="tool_result">{t.debugTrajFilterToolResult || 'Tool Results'}</option>
                            <option value="inbound_metadata">{t.debugTrajFilterInboundMetadata || 'Inbound Metadata'}</option>
                            <option value="error">{t.debugTrajFilterError || 'Errors Only'}</option>
                        </select>
                        <input
                            value={trajectoryEventSearch}
                            onChange={(e) => setTrajectoryEventSearch(e.target.value)}
                            placeholder={t.debugTrajSearchPlaceholder || 'Search in events...'}
                            className="px-3 py-1.5 rounded bg-black/20 border border-white/10 text-sm text-white w-64"
                        />
                        <select
                            value={trajectoryToolCallIdFilter}
                            onChange={(e) => setTrajectoryToolCallIdFilter(e.target.value)}
                            className="px-3 py-1.5 rounded bg-black/20 border border-white/10 text-sm text-white min-w-48"
                        >
                            <option value="all">{t.debugTrajToolCallAll || 'All tool_call_id'}</option>
                            {getTrajectoryToolCallIds().map((id) => (
                                <option key={id} value={id}>{id}</option>
                            ))}
                        </select>
                        <button
                            onClick={resetTrajectoryFilters}
                            className="px-3 py-1.5 rounded bg-white/5 border border-white/10 text-sm text-gray-200 hover:bg-white/10"
                        >
                            {t.debugTrajResetFilters || 'Reset Filters'}
                        </button>
                    </div>

                    {trajectoryError && (
                        <div className="mb-3 p-2 rounded bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
                            {trajectoryError}
                        </div>
                    )}

                    <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-3 gap-3">
                        <div className="lg:col-span-1 min-h-0 overflow-y-auto border border-white/10 rounded-lg bg-black/20">
                            {trajectoryLoading ? (
                                <div className="p-4 text-gray-400 text-sm">{t.debugLoading || 'Loading...'}</div>
                            ) : trajectories.length === 0 ? (
                                <div className="p-4 text-gray-500 text-sm">{t.debugNoTrajectories || 'No trajectories found.'}</div>
                            ) : (
                                trajectories.map((item) => (
                                    <button
                                        key={item.run_id}
                                        onClick={() => {
                                            setSelectedRunId(item.run_id);
                                            fetchTrajectoryDetail(item.run_id);
                                        }}
                                        className={`w-full text-left p-3 border-b border-white/5 hover:bg-white/5 ${
                                            selectedRunId === item.run_id ? 'bg-white/10' : ''
                                        }`}
                                    >
                                        <div className="text-xs text-cyan-400 truncate">{item.run_id}</div>
                                        <div className="text-xs text-gray-500 mt-1">
                                            {(item.channel || 'unknown')} · {(item.status || 'running')}
                                        </div>
                                        <div className="text-xs text-gray-500">
                                            events: {item.event_count ?? 0} · feedback: {item.feedback_count ?? 0}
                                        </div>
                                        <div className="text-xs text-gray-600 mt-1">
                                            {item.ts ? new Date(item.ts * 1000).toLocaleString() : '--'}
                                        </div>
                                    </button>
                                ))
                            )}
                        </div>

                        <div className="lg:col-span-2 min-h-0 overflow-y-auto border border-white/10 rounded-lg bg-black/20 p-3 font-mono text-xs">
                            {trajectoryDetailLoading ? (
                                <div className="text-gray-400">{t.debugLoading || 'Loading...'}</div>
                            ) : !trajectoryDetail ? (
                                <div className="text-gray-500">{t.debugSelectTrajectory || 'Select one trajectory to inspect.'}</div>
                            ) : (
                                <div className="space-y-3">
                                    <div className="p-2 rounded bg-white/5 border border-white/10">
                                        <div className="text-cyan-400">run_id: {trajectoryDetail.run_id}</div>
                                        <div className="text-gray-400 mt-1">status: {trajectoryDetail.final?.status || 'running'}</div>
                                        <div className="text-gray-400">event_count: {trajectoryDetail.event_count ?? 0}</div>
                                        <div className="text-gray-500 mt-1 truncate">
                                            final: {trajectoryDetail.final?.final_content || ''}
                                        </div>
                                    </div>

                                    <div className="p-2 rounded bg-white/5 border border-white/10">
                                        <div className="text-cyan-300 mb-1">{t.debugTrajTaskView || 'Task View'}</div>
                                        <div className="text-gray-400">
                                            {t.debugTrajTaskDuration || 'duration'}: {trajectoryTaskView?.duration_ms ?? '--'} ms
                                        </div>
                                        <div className="text-gray-400">
                                            {t.debugTrajTaskErrors || 'errors'}: {trajectoryTaskView?.error_count ?? 0}
                                        </div>
                                        <div className="text-gray-400">
                                            {t.debugTrajTaskLatency || 'turn_latency'}: {trajectoryTaskView?.turn_latency_ms ?? '--'} ms
                                        </div>
                                        {Array.isArray(trajectoryTaskView?.stages) && trajectoryTaskView.stages.length > 0 && (
                                            <div className="mt-2 space-y-1">
                                                {trajectoryTaskView.stages.map((s) => {
                                                    const maxDur = Math.max(
                                                        1,
                                                        ...trajectoryTaskView.stages.map((x) => Number(x.duration_ms || 0)),
                                                    );
                                                    const width = Math.max(6, Math.round((Number(s.duration_ms || 0) / maxDur) * 100));
                                                    return (
                                                        <div key={s.stage} className="flex items-center gap-2">
                                                            <div className="w-20 text-gray-500 truncate">{s.stage}</div>
                                                            <div className="flex-1 h-2 bg-black/30 rounded overflow-hidden">
                                                                <div
                                                                    className="h-full bg-cyan-500/70"
                                                                    style={{ width: `${width}%` }}
                                                                />
                                                            </div>
                                                            <div className="text-gray-500">{s.duration_ms ?? 0}ms</div>
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        )}
                                    </div>

                                    <div className="p-2 rounded bg-white/5 border border-white/10">
                                        <div className="text-cyan-300 mb-1">{t.debugTrajReplay || 'Replay Compare'}</div>
                                        <div className="flex items-center gap-2 mb-1">
                                            <input
                                                value={trajectoryCompareRunId}
                                                onChange={(e) => setTrajectoryCompareRunId(e.target.value)}
                                                placeholder={t.debugTrajCompareRunId || 'compare run_id'}
                                                className="px-2 py-1 rounded bg-black/20 border border-white/10 text-xs text-white w-64"
                                            />
                                            <button
                                                onClick={() => fetchTrajectoryReplay(trajectoryDetail.run_id, trajectoryCompareRunId)}
                                                className="px-2 py-1 rounded bg-white/5 border border-white/10 text-xs text-gray-200 hover:bg-white/10"
                                            >
                                                {t.debugTrajCompareAction || 'Compare'}
                                            </button>
                                        </div>
                                        <div className="text-gray-400">
                                            {t.debugTrajReplaySteps || 'steps'}: {trajectoryReplay?.step_count ?? '--'}
                                            {' · '}
                                            tool_call: {trajectoryReplay?.tool_call_steps ?? '--'}
                                            {' · '}
                                            tool_result: {trajectoryReplay?.tool_result_steps ?? '--'}
                                            {' · '}
                                            error: {trajectoryReplay?.error_steps ?? '--'}
                                        </div>
                                        {trajectoryComparison && (
                                            <div className="text-gray-500 mt-1">
                                                overlap: {trajectoryComparison.overlap_ratio}
                                                {' · '}
                                                shared: {trajectoryComparison.shared_steps}
                                                {' · '}
                                                missing: {trajectoryComparison.missing_from_run?.length ?? 0}
                                                {' · '}
                                                added: {trajectoryComparison.added_in_run?.length ?? 0}
                                            </div>
                                        )}
                                    </div>

                                    <div className="p-2 rounded bg-white/5 border border-white/10">
                                        <div className="text-cyan-300 mb-1">{t.debugTrajResumeTitle || 'Resume Draft'}</div>
                                        <div className="flex items-center gap-2 mb-1">
                                            <input
                                                value={trajectoryResumeSessionId}
                                                onChange={(e) => setTrajectoryResumeSessionId(e.target.value)}
                                                placeholder={t.debugTrajResumeSessionPlaceholder || 'session_id'}
                                                className="px-2 py-1 rounded bg-black/20 border border-white/10 text-xs text-white w-48"
                                            />
                                            <button
                                                onClick={() => sendTrajectoryResume(trajectoryDetail.run_id)}
                                                className="px-2 py-1 rounded bg-cyan-500/15 border border-cyan-500/30 text-xs text-cyan-200 hover:bg-cyan-500/25"
                                            >
                                                {t.debugTrajResumeSend || 'Send Resume'}
                                            </button>
                                            <button
                                                onClick={() => autoResumeTrajectory(trajectoryDetail.run_id)}
                                                className="px-2 py-1 rounded bg-emerald-500/15 border border-emerald-500/30 text-xs text-emerald-200 hover:bg-emerald-500/25"
                                            >
                                                {t.debugTrajAutoResume || 'Auto Resume'}
                                            </button>
                                            <button
                                                onClick={() => replayExecuteTrajectory(trajectoryDetail.run_id)}
                                                className="px-2 py-1 rounded bg-violet-500/15 border border-violet-500/30 text-xs text-violet-200 hover:bg-violet-500/25"
                                            >
                                                {t.debugTrajReplayExecute || 'Replay Execute'}
                                            </button>
                                        </div>
                                        <div className="text-gray-400">
                                            {t.debugTrajResumeStatus || 'can_resume'}: {String(trajectoryResume?.can_resume ?? false)}
                                        </div>
                                        {trajectoryResume?.last_error?.error_code && (
                                            <div className="text-red-300">
                                                last_error: {trajectoryResume.last_error.error_code}
                                            </div>
                                        )}
                                        <div className="mt-1 text-gray-500 whitespace-pre-wrap break-words">
                                            {trajectoryResume?.resume_message || '--'}
                                        </div>
                                        {trajectoryResumeSendStatus && (
                                            <div className="mt-1 text-emerald-300 break-words">
                                                {trajectoryResumeSendStatus}
                                            </div>
                                        )}
                                    </div>
                                    <div className="p-2 rounded bg-white/5 border border-white/10">
                                        <div className="text-cyan-300 mb-1">{t.debugTaskRunsTitle || 'Task Runs'}</div>
                                        <div className="grid grid-cols-1 lg:grid-cols-3 gap-2">
                                            <div className="max-h-40 overflow-y-auto border border-white/10 rounded bg-black/20">
                                                {taskRuns.length === 0 ? (
                                                    <div className="p-2 text-gray-500">{t.debugNoTaskRuns || 'No task runs'}</div>
                                                ) : taskRuns.map((task) => (
                                                    <button
                                                        key={task.task_id}
                                                        onClick={() => {
                                                            setSelectedTaskId(task.task_id);
                                                            fetchTaskDetail(task.task_id);
                                                        }}
                                                        className={`w-full text-left p-2 border-b border-white/5 hover:bg-white/5 ${
                                                            selectedTaskId === task.task_id ? 'bg-white/10' : ''
                                                        }`}
                                                    >
                                                        <div className="text-xs text-cyan-300 truncate">{task.task_id}</div>
                                                        <div className="text-[11px] text-gray-500">{task.kind} · {task.status}</div>
                                                    </button>
                                                ))}
                                            </div>
                                            <div className="border border-white/10 rounded bg-black/20 p-2">
                                                {!selectedTask ? (
                                                    <div className="text-gray-500">{t.debugSelectTaskRun || 'Select a task run'}</div>
                                                ) : (
                                                    <>
                                                        <div className="text-gray-300 text-xs">{selectedTask.kind} · {selectedTask.status}</div>
                                                        <div className="text-gray-500 text-xs truncate">{selectedTask.task_id}</div>
                                                        <button
                                                            onClick={() => runCodingLoop(selectedTask.task_id)}
                                                            className="mt-1 px-2 py-1 rounded bg-amber-500/15 border border-amber-500/30 text-xs text-amber-200 hover:bg-amber-500/25"
                                                        >
                                                            {t.debugTaskRunCodingLoop || 'Run Coding Loop'}
                                                        </button>
                                                        {Array.isArray(selectedTask.checkpoints) && selectedTask.checkpoints.length > 0 && (
                                                            <div className="mt-2 space-y-1 max-h-24 overflow-y-auto">
                                                                {selectedTask.checkpoints.slice(-10).map((cp, idx) => (
                                                                    <div key={idx} className="text-[11px] text-gray-500">
                                                                        {cp.stage} · {cp.status} · {cp.note}
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        )}
                                                    </>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                    <div className="p-2 rounded bg-white/5 border border-white/10">
                                                <div className="flex items-center justify-between mb-1 gap-2">
                                                    <div className="text-cyan-300">{t.debugCodingQualityTitle || 'Coding Quality'}</div>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-[11px] text-gray-500">{t.debugCodingQualityKind || 'kind'}</span>
                                                        <select
                                                            value={codingQualityKind}
                                                            onChange={(e) => {
                                                                const value = e.target.value;
                                                                setCodingQualityKind(value);
                                                                fetchCodingQuality(value);
                                                            }}
                                                            className="px-2 py-1 rounded bg-black/20 border border-white/10 text-xs text-white"
                                                        >
                                                            <option value="all">{t.debugCodingQualityKindAll || 'all'}</option>
                                                            <option value="coding_loop">{t.debugCodingQualityKindCodingLoop || 'coding_loop'}</option>
                                                            <option value="replay_execute">{t.debugCodingQualityKindReplayExecute || 'replay_execute'}</option>
                                                            <option value="resume_send">{t.debugCodingQualityKindResumeSend || 'resume_send'}</option>
                                                        </select>
                                                    </div>
                                                </div>
                                                {!codingQuality ? (
                                                    <div className="text-gray-500 text-xs">{t.debugCodingQualityNoData || 'No coding quality data yet.'}</div>
                                                ) : (
                                            <>
                                                <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 text-xs">
                                                    <div className="p-2 rounded bg-black/20 border border-white/10">
                                                        <div className="text-gray-500">{t.debugCodingQualityPassRate || 'pass_rate'}</div>
                                                        <div className="text-emerald-300">{Math.round((Number(codingQuality.pass_rate || 0) * 100) * 10) / 10}%</div>
                                                    </div>
                                                    <div className="p-2 rounded bg-black/20 border border-white/10">
                                                        <div className="text-gray-500">{t.debugCodingQualityDuration || 'avg_duration_ms'}</div>
                                                        <div className="text-cyan-300">{codingQuality.avg_duration_ms ?? 0}</div>
                                                    </div>
                                                    <div className="p-2 rounded bg-black/20 border border-white/10">
                                                        <div className="text-gray-500">{t.debugCodingQualityFiles || 'avg_files_changed'}</div>
                                                        <div className="text-amber-300">{codingQuality.avg_files_changed ?? 0}</div>
                                                    </div>
                                                    <div className="p-2 rounded bg-black/20 border border-white/10">
                                                        <div className="text-gray-500">{t.debugCodingQualityTests || 'avg_test_commands'}</div>
                                                        <div className="text-violet-300">{codingQuality.avg_test_commands ?? 0}</div>
                                                    </div>
                                                </div>
                                                <div className="mt-2 text-[11px] text-gray-500">
                                                    {t.debugCodingQualitySummary || 'runs'}: {codingQuality.total_runs ?? 0}
                                                    {' · '}
                                                    {t.debugCodingQualitySuccess || 'success'}: {codingQuality.success_runs ?? 0}
                                                    {' · '}
                                                    window: {codingQuality.window ?? 0}
                                                </div>
                                                {Array.isArray(codingQuality.recent) && codingQuality.recent.length > 0 && (
                                                    <div className="mt-2 max-h-24 overflow-y-auto space-y-1">
                                                        {codingQuality.recent.slice(-10).map((item, idx) => (
                                                            <div key={idx} className="text-[11px] text-gray-500">
                                                                {item.task_id || '-'} · {item.success ? 'ok' : 'fail'} · {item.duration_ms ?? 0}ms · files={item.files_changed ?? 0} · tests={item.tests_passed ?? 0}/{item.tests_total ?? 0}
                                                            </div>
                                                        ))}
                                                    </div>
                                                )}
                                            </>
                                        )}
                                    </div>
                                    <div className="p-2 rounded bg-white/5 border border-white/10">
                                        <div className="text-cyan-300 mb-1">{t.debugCodingBenchmarkTitle || 'Coding Benchmark'}</div>
                                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
                                            <div className="border border-white/10 rounded bg-black/20 p-2">
                                                <div className="text-[11px] text-gray-500 mb-1">
                                                    {t.debugCodingBenchmarkLeaderboard || 'Leaderboard'}
                                                    {' · '}
                                                    {t.debugCodingBenchmarkWindow || 'window'}={codingBenchmarkLeaderboard?.window ?? 0}
                                                </div>
                                                {!codingBenchmarkLeaderboard || !Array.isArray(codingBenchmarkLeaderboard.top) || codingBenchmarkLeaderboard.top.length === 0 ? (
                                                    <div className="text-xs text-gray-500">{t.debugCodingBenchmarkNoData || 'No benchmark data yet.'}</div>
                                                ) : (
                                                    <div className="space-y-1 max-h-28 overflow-y-auto">
                                                        {codingBenchmarkLeaderboard.top.slice(0, 10).map((item, idx) => (
                                                            <div key={`${item.name || 'suite'}_${idx}`} className="text-[11px] text-gray-400">
                                                                #{idx + 1} {item.name || '-'} · score={item.score ?? 0} · {item.success_cases ?? 0}/{item.total_cases ?? 0}
                                                            </div>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                            <div className="border border-white/10 rounded bg-black/20 p-2">
                                                <div className="text-[11px] text-gray-500 mb-1">{t.debugCodingBenchmarkObservability || 'Observability'}</div>
                                                {!codingBenchmarkObservability ? (
                                                    <div className="text-xs text-gray-500">{t.debugCodingBenchmarkNoData || 'No benchmark data yet.'}</div>
                                                ) : (
                                                    <>
                                                        <div className="text-[11px] text-gray-500 mb-1">
                                                            avg_score={codingBenchmarkObservability.avg_score ?? 0} · runs={codingBenchmarkObservability.total_runs ?? 0}
                                                        </div>
                                                        <div className="max-h-20 overflow-y-auto space-y-1">
                                                            {(codingBenchmarkObservability.trend || []).slice(-7).map((row, idx) => (
                                                                <div key={`trend_${idx}`} className="text-[11px] text-gray-400">
                                                                    {row.date} · runs={row.runs} · score={row.avg_score}
                                                                </div>
                                                            ))}
                                                        </div>
                                                        <div className="mt-1 text-[11px] text-gray-500">{t.debugCodingBenchmarkFailureReasons || 'Top failure reasons'}</div>
                                                        <div className="max-h-16 overflow-y-auto space-y-1">
                                                            {(codingBenchmarkObservability.failure_reasons || []).slice(0, 5).map((row, idx) => (
                                                                <div key={`reason_${idx}`} className="text-[11px] text-gray-400 break-words">
                                                                    {row.reason} · {row.count}
                                                                </div>
                                                            ))}
                                                        </div>
                                                        <button
                                                            onClick={() => window.open(`${API_BASE}/debug/coding-benchmark/export.csv?window=60`, '_blank')}
                                                            className="mt-2 px-2 py-1 rounded bg-white/10 border border-white/15 text-xs text-white hover:bg-white/20"
                                                        >
                                                            {t.debugCodingBenchmarkExportCsv || 'Export CSV'}
                                                        </button>
                                                    </>
                                                )}
                                            </div>
                                            <div className="border border-white/10 rounded bg-black/20 p-2">
                                                <div className="text-[11px] text-gray-500 mb-1">{t.debugCodingBenchmarkHistory || 'Recent Runs'}</div>
                                                {codingBenchmarkHistory.length === 0 ? (
                                                    <div className="text-xs text-gray-500">{t.debugCodingBenchmarkNoData || 'No benchmark data yet.'}</div>
                                                ) : (
                                                    <div className="space-y-1 max-h-28 overflow-y-auto">
                                                        {codingBenchmarkHistory.slice(-10).reverse().map((item, idx) => (
                                                            <div key={`${item.name || 'suite'}_${idx}`} className="text-[11px] text-gray-400">
                                                                {item.name || '-'} · score={item.score ?? 0} · {item.success_cases ?? 0}/{item.total_cases ?? 0} · {Math.round(Number(item.duration_ms || 0))}ms
                                                            </div>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                            <div className="border border-white/10 rounded bg-black/20 p-2">
                                                <div className="text-[11px] text-gray-500 mb-2">{t.debugCodingBenchmarkScheduler || 'Scheduler'}</div>
                                                {!codingBenchmarkScheduler ? (
                                                    <div className="text-xs text-gray-500">{t.debugCodingBenchmarkNoData || 'No benchmark data yet.'}</div>
                                                ) : (
                                                    <>
                                                        {(() => {
                                                            const caseCount = Array.isArray(codingBenchmarkScheduler?.payload?.cases)
                                                                ? codingBenchmarkScheduler.payload.cases.length
                                                                : 0;
                                                            const invalid = Boolean(codingBenchmarkScheduler?.enabled) && caseCount <= 0;
                                                            return (
                                                                <div className={`mb-2 text-[11px] ${invalid ? 'text-amber-300' : 'text-gray-500'}`}>
                                                                    {t.debugCodingBenchmarkSchedulerCases || 'payload.cases'}: {caseCount}
                                                                    {invalid ? ` · ${t.debugCodingBenchmarkSchedulerCasesEmpty || 'Enabled but empty'}` : ''}
                                                                </div>
                                                            );
                                                        })()}
                                                        <div className="space-y-2 text-xs">
                                                            <div className="flex items-center justify-between gap-2 text-gray-300">
                                                                <span>{t.debugCodingBenchmarkSchedulerEnabled || 'enabled'}</span>
                                                                <ToggleSwitch
                                                                    checked={Boolean(codingBenchmarkScheduler.enabled)}
                                                                    onChange={(v) => updateSchedulerField('enabled', v)}
                                                                />
                                                            </div>
                                                            <label className="flex items-center gap-2 text-gray-300">
                                                                <span>{t.debugCodingBenchmarkSchedulerInterval || 'interval_seconds'}</span>
                                                                <input
                                                                    type="number"
                                                                    min="30"
                                                                    className="px-2 py-1 rounded bg-black/20 border border-white/10 text-xs text-white w-24"
                                                                    value={codingBenchmarkScheduler.interval_seconds ?? 1800}
                                                                    onChange={(e) => updateSchedulerField('interval_seconds', parseInt(e.target.value, 10) || 1800)}
                                                                />
                                                            </label>
                                                            <div className="flex items-center justify-between gap-2 text-gray-300">
                                                                <span>{t.debugCodingBenchmarkSchedulerAutoLink || 'auto_link_release_gate'}</span>
                                                                <ToggleSwitch
                                                                    checked={Boolean(codingBenchmarkScheduler.auto_link_release_gate)}
                                                                    onChange={(v) => updateSchedulerField('auto_link_release_gate', v)}
                                                                />
                                                            </div>
                                                            <label className="flex items-center gap-2 text-gray-300">
                                                                <span>{t.debugCodingBenchmarkWindow || 'window'}</span>
                                                                <input
                                                                    type="number"
                                                                    min="1"
                                                                    max="200"
                                                                    className="px-2 py-1 rounded bg-black/20 border border-white/10 text-xs text-white w-24"
                                                                    value={codingBenchmarkScheduler.window ?? 20}
                                                                    onChange={(e) => updateSchedulerField('window', parseInt(e.target.value, 10) || 20)}
                                                                />
                                                            </label>
                                                        </div>
                                                        <div className="mt-2">
                                                            <label className="text-[11px] text-gray-500">{t.debugCodingBenchmarkSchedulerCasesEditor || 'payload.cases (JSON array)'}</label>
                                                            <textarea
                                                                value={codingBenchmarkCasesText}
                                                                onChange={(e) => setCodingBenchmarkCasesText(e.target.value)}
                                                                className="mt-1 w-full h-24 px-2 py-1 rounded bg-black/20 border border-white/10 text-xs text-white font-mono"
                                                            />
                                                        </div>
                                                        <div className="mt-2 flex items-center gap-2">
                                                            <button
                                                                onClick={saveCodingBenchmarkScheduler}
                                                                className="px-2 py-1 rounded bg-cyan-500/15 border border-cyan-500/30 text-xs text-cyan-200 hover:bg-cyan-500/25"
                                                            >
                                                                {t.debugCodingBenchmarkSave || 'Save'}
                                                            </button>
                                                            <button
                                                                onClick={runCodingBenchmarkSchedulerNow}
                                                                className="px-2 py-1 rounded bg-amber-500/15 border border-amber-500/30 text-xs text-amber-200 hover:bg-amber-500/25"
                                                            >
                                                                {t.debugCodingBenchmarkRunNow || 'Run Now'}
                                                            </button>
                                                        </div>
                                                        <div className="mt-2 text-[11px] text-gray-500">
                                                            {t.debugCodingBenchmarkLastRun || 'last_run_ts'}: {codingBenchmarkSchedulerState?.last_run_ts ? new Date(codingBenchmarkSchedulerState.last_run_ts * 1000).toLocaleString() : '--'}
                                                        </div>
                                                        {codingBenchmarkSchedulerStatus && (
                                                            <div className="mt-1 text-[11px] text-emerald-300 break-words">{codingBenchmarkSchedulerStatus}</div>
                                                        )}
                                                    </>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                    {getFilteredTrajectoryEvents().map((evt, idx) => {
                                        const payload = evt.payload || {};
                                        const inboundMeta = payload.metadata || {};
                                        const inboundMetaKeys = Array.isArray(payload.keys) ? payload.keys : [];
                                        const inboundFeishuMedia = Array.isArray(inboundMeta.feishu_media)
                                            ? inboundMeta.feishu_media
                                            : [];
                                        return (
                                            <div key={`${evt.ts || 0}_${idx}`} className="p-2 rounded border border-white/10 bg-black/30">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <span className="px-2 py-0.5 rounded bg-cyan-500/15 text-cyan-300">{evt.stage || '-'}</span>
                                                    <span className="px-2 py-0.5 rounded bg-violet-500/15 text-violet-300">{evt.action || '-'}</span>
                                                    <span className="text-gray-600">{evt.ts ? new Date(evt.ts * 1000).toLocaleTimeString() : '--'}</span>
                                                </div>
                                                {payload.tool && <div className="mt-1 text-gray-300">tool: {payload.tool}</div>}
                                                {payload.tool_call_id && <div className="text-gray-500">tool_call_id: {payload.tool_call_id}</div>}
                                                {payload.args_hash && <div className="text-gray-500">args_hash: {payload.args_hash}</div>}
                                                {payload.args_preview && <div className="text-emerald-300 mt-1 break-all">args: {payload.args_preview}</div>}
                                                {payload.status && <div className="mt-1 text-gray-300">status: {payload.status}</div>}
                                                {payload.error_code && (
                                                    <div className="flex items-center gap-2 mt-1">
                                                        <div className="text-red-300">error_code: {payload.error_code}</div>
                                                        {payload.tool_call_id && (
                                                            <button
                                                                onClick={() => setTrajectoryToolCallIdFilter(payload.tool_call_id)}
                                                                className="px-2 py-0.5 rounded text-[10px] bg-red-500/15 text-red-200 border border-red-500/30 hover:bg-red-500/25"
                                                            >
                                                                {t.debugTrajFocusChain || 'Focus Chain'}
                                                            </button>
                                                        )}
                                                    </div>
                                                )}
                                                {payload.result_preview && <div className="text-amber-200 mt-1 break-all">result: {payload.result_preview}</div>}
                                                {Array.isArray(payload.media_paths) && payload.media_paths.length > 0 && (
                                                    <div className="text-sky-300 mt-1 break-all">media: {payload.media_paths.join(', ')}</div>
                                                )}
                                                {evt.action === 'inbound_metadata' && (
                                                    <div className="mt-2 p-2 rounded border border-cyan-500/20 bg-cyan-500/5">
                                                        <div className="text-cyan-300">
                                                            {t.debugTrajInboundMetadata || 'Inbound Metadata'}
                                                        </div>
                                                        {inboundMetaKeys.length > 0 && (
                                                            <div className="text-gray-500 mt-1 break-all">
                                                                {t.debugTrajInboundKeys || 'keys'}: {inboundMetaKeys.join(', ')}
                                                            </div>
                                                        )}
                                                        {(inboundMeta.feishu_message_id || inboundMeta.feishu_message_type) && (
                                                            <div className="text-gray-400 mt-1">
                                                                {t.debugTrajFeishuMessageId || 'feishu.message_id'}: {inboundMeta.feishu_message_id || '-'}
                                                                {' · '}
                                                                {t.debugTrajFeishuMessageType || 'feishu.message_type'}: {inboundMeta.feishu_message_type || '-'}
                                                            </div>
                                                        )}
                                                        {inboundFeishuMedia.length > 0 && (
                                                            <div className="mt-1 space-y-1">
                                                                {inboundFeishuMedia.map((item, itemIdx) => (
                                                                    <div key={`${item.path || 'media'}_${itemIdx}`} className="text-sky-300 break-all">
                                                                        [{item.message_type || 'unknown'}] {item.path || '-'}
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        )}
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    </div>
                </section>
            )}
        </div>
    );
};

export default Debug;
