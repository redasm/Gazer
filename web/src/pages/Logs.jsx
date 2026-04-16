import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { FileText, RefreshCw, Trash2, Download, Filter, Search, Cpu, Hash } from 'lucide-react';
import API_BASE from '../config';
import ConfirmModal from '../components/ConfirmModal';

const levelColors = {
    ERROR:   { color: '#f87171', bg: 'rgba(239,68,68,0.12)' },
    WARNING: { color: '#fbbf24', bg: 'rgba(234,179,8,0.12)' },
    INFO:    { color: '#60a5fa', bg: 'rgba(59,130,246,0.12)' },
    DEBUG:   { color: '#9ca3af', bg: 'rgba(156,163,175,0.10)' },
};

const getLevelStyle = (level) => {
    const l = levelColors[level?.toUpperCase()] || levelColors.DEBUG;
    return {
        color: l.color,
        background: l.bg,
        padding: '1px 8px',
        borderRadius: '4px',
        fontSize: '11px',
        fontWeight: 600,
        minWidth: '52px',
        textAlign: 'center',
        display: 'inline-block',
        lineHeight: '18px',
    };
};

const badgeStyle = (color, bg) => ({
    display: 'inline-flex',
    alignItems: 'center',
    gap: '4px',
    padding: '1px 8px',
    borderRadius: '4px',
    fontSize: '11px',
    fontWeight: 500,
    color,
    background: bg,
    fontFamily: 'monospace',
});

const Logs = ({ t }) => {
    const [logs, setLogs] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [filter, setFilter] = useState('all');
    const [searchQuery, setSearchQuery] = useState('');
    const [confirmClear, setConfirmClear] = useState(false);
    const [autoScroll, setAutoScroll] = useState(false);
    const scrollRef = useRef(null);
    const bottomRef = useRef(null);

    const intervalRef = useRef(null);

    const fetchLogs = async () => {
        try {
            const res = await axios.get(`${API_BASE}/logs?limit=200`);
            setLogs(res.data.logs || []);
            setError(null);
        } catch (err) {
            if (err?.response?.status === 401) {
                // Stop polling — user needs to authenticate first
                clearInterval(intervalRef.current);
                setError("Authentication required. Please log in.");
            } else {
                setError("Failed to fetch logs. Backend may be offline.");
            }
        }
        setLoading(false);
    };

    useEffect(() => {
        const kickoff = setTimeout(() => {
            void fetchLogs();
        }, 0);
        intervalRef.current = setInterval(fetchLogs, 3000);
        return () => {
            clearTimeout(kickoff);
            clearInterval(intervalRef.current);
        };
    }, []);

    useEffect(() => {
        if (autoScroll && bottomRef.current) {
            bottomRef.current.scrollIntoView({ behavior: 'smooth' });
        }
    }, [logs, autoScroll]);

    const handleScroll = () => {
        const el = scrollRef.current;
        if (!el) return;
        const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
        setAutoScroll(atBottom);
    };

    const clearLogs = async () => {
        try {
            await axios.delete(`${API_BASE}/logs`);
            setLogs([]);
        } catch (err) {
            console.error("Failed to clear logs", err);
        }
        setConfirmClear(false);
    };

    const downloadLogs = () => {
        const content = logs.map(l => {
            let line = `[${l.timestamp}] [${l.level}] [${l.source}] ${l.message}`;
            if (l.meta) {
                const parts = [];
                if (l.meta.model) parts.push(`model=${l.meta.model}`);
                if (l.meta.request_id) parts.push(`request_id=${l.meta.request_id}`);
                if (l.meta.tokens?.total_tokens) parts.push(`tokens=${l.meta.tokens.total_tokens}`);
                if (parts.length) line += ` {${parts.join(', ')}}`;
            }
            return line;
        }).join('\n');
        const blob = new Blob([content], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `gazer_logs_${new Date().toISOString().slice(0, 10)}.txt`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const filteredLogs = logs.filter(log => {
        const matchesFilter = filter === 'all' || log.level?.toUpperCase() === filter.toUpperCase();
        const searchLower = searchQuery.toLowerCase();
        const matchesSearch = !searchQuery ||
            log.message?.toLowerCase().includes(searchLower) ||
            log.source?.toLowerCase().includes(searchLower) ||
            log.meta?.request_id?.toLowerCase().includes(searchLower) ||
            log.meta?.model?.toLowerCase().includes(searchLower);
        return matchesFilter && matchesSearch;
    });

    const inputStyle = {
        background: 'rgba(0,0,0,0.3)',
        border: '1px solid rgba(255,255,255,0.1)',
        borderRadius: '8px',
        padding: '7px 12px 7px 32px',
        color: '#fff',
        fontSize: '13px',
        outline: 'none',
        width: '100%',
    };

    const selectStyle = {
        background: 'rgba(0,0,0,0.3)',
        border: '1px solid rgba(255,255,255,0.1)',
        borderRadius: '8px',
        padding: '7px 10px',
        color: '#fff',
        fontSize: '13px',
        outline: 'none',
        cursor: 'pointer',
    };

    return (
        <>
        <div style={{ display: 'flex', flexDirection: 'column', flex: 1, gap: '16px', height: '100%' }}>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                    <h2 style={{ fontSize: '18px', fontWeight: 600, color: '#fff', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <FileText size={20} style={{ color: '#4ade80' }} />
                        {t.logsTitle}
                    </h2>
                    <p style={{ fontSize: '13px', color: '#667', margin: '4px 0 0 0' }}>{t.logsDesc}</p>
                </div>
                <div style={{ display: 'flex', gap: '6px' }}>
                    <button onClick={fetchLogs} className="btn-ghost"><RefreshCw size={14} /></button>
                    <button onClick={downloadLogs} className="btn-ghost"><Download size={14} /></button>
                    <button onClick={() => setConfirmClear(true)} className="btn-ghost" style={{ color: '#f87171' }}><Trash2 size={14} /></button>
                </div>
            </div>

            {/* Filters */}
            <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <Filter size={14} style={{ color: '#667' }} />
                    <select style={selectStyle} value={filter} onChange={(e) => setFilter(e.target.value)}>
                        <option value="all">{t.allLevels}</option>
                        <option value="error">{t.errors}</option>
                        <option value="warning">{t.warnings}</option>
                        <option value="info">{t.info}</option>
                        <option value="debug">Debug</option>
                    </select>
                </div>
                <div style={{ flex: 1, position: 'relative' }}>
                    <Search size={14} style={{ position: 'absolute', left: '10px', top: '50%', transform: 'translateY(-50%)', color: '#556' }} />
                    <input
                        type="text"
                        placeholder={t.searchLogs}
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        style={inputStyle}
                    />
                </div>
                <span style={{ fontSize: '12px', color: '#556', whiteSpace: 'nowrap' }}>
                    {filteredLogs.length} {t.entries}
                </span>
            </div>

            {/* Log Stream */}
            <div style={{
                flex: 1,
                background: 'rgba(10,10,10,0.6)',
                border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: '12px',
                overflow: 'hidden',
                display: 'flex',
                flexDirection: 'column',
                minHeight: 0,
            }}>
                {/* Table Header */}
                <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    padding: '8px 14px',
                    borderBottom: '1px solid rgba(255,255,255,0.08)',
                    fontSize: '11px',
                    fontWeight: 600,
                    color: '#556',
                    textTransform: 'uppercase',
                    letterSpacing: '0.5px',
                    flexShrink: 0,
                }}>
                    <span style={{ width: '72px' }}>{t.time || '时间'}</span>
                    <span style={{ width: '56px' }}>{t.levelLabel || '级别'}</span>
                    <span style={{ width: '120px' }}>{t.sourceLabel || '来源'}</span>
                    <span style={{ flex: 1 }}>{t.messageLabel || '内容'}</span>
                </div>

                {loading ? (
                    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#556' }}>
                        {t.loadingLogs}
                    </div>
                ) : error ? (
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: '#f87171' }}>
                        <FileText size={48} style={{ opacity: 0.2, marginBottom: '12px' }} />
                        <p style={{ margin: 0 }}>{error}</p>
                    </div>
                ) : filteredLogs.length === 0 ? (
                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: '#556' }}>
                        <FileText size={48} style={{ opacity: 0.2, marginBottom: '12px' }} />
                        <p style={{ margin: 0 }}>{t.noLogs}</p>
                    </div>
                ) : (
                    <div
                        ref={scrollRef}
                        onScroll={handleScroll}
                        style={{ flex: 1, overflowY: 'auto', fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace", fontSize: '12px' }}
                    >
                        {filteredLogs.map((log, idx) => {
                            const hasLLMMeta = log.meta && (log.meta.request_id || log.meta.model || log.meta.tokens);
                            return (
                                <div key={idx} style={{
                                    display: 'flex',
                                    flexDirection: 'column',
                                    padding: '6px 14px',
                                    borderBottom: '1px solid rgba(255,255,255,0.03)',
                                    background: idx % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.015)',
                                    borderLeft: hasLLMMeta ? '3px solid rgba(96,165,250,0.4)' : '3px solid transparent',
                                }}>
                                    {/* Main row */}
                                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px' }}>
                                        <span style={{ width: '72px', flexShrink: 0, color: '#556', fontSize: '11px', lineHeight: '20px' }}>
                                            {(() => {
                                                try { return new Date(log.timestamp).toLocaleTimeString(); }
                                                catch { return log.timestamp; }
                                            })()}
                                        </span>
                                        <span style={{ ...getLevelStyle(log.level), flexShrink: 0 }}>
                                            {log.level}
                                        </span>
                                        <span style={{
                                            width: '120px',
                                            flexShrink: 0,
                                            color: '#a78bfa',
                                            fontSize: '11px',
                                            lineHeight: '20px',
                                            whiteSpace: 'nowrap',
                                            overflow: 'hidden',
                                            textOverflow: 'ellipsis',
                                        }}>
                                            {log.source}
                                        </span>
                                        <span style={{
                                            flex: 1,
                                            color: '#d1d5db',
                                            lineHeight: '20px',
                                            wordBreak: 'break-all',
                                        }}>
                                            {log.message}
                                        </span>
                                    </div>

                                    {/* Meta row (LLM call info) */}
                                    {hasLLMMeta && (
                                        <div style={{
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: '8px',
                                            marginTop: '4px',
                                            paddingLeft: '84px',
                                        }}>
                                            {log.meta.model && (
                                                <span style={badgeStyle('#22d3ee', 'rgba(34,211,238,0.12)')}>
                                                    <Cpu size={10} />
                                                    {log.meta.model}
                                                </span>
                                            )}
                                            {log.meta.request_id && (
                                                <span style={badgeStyle('#a78bfa', 'rgba(167,139,250,0.12)')}>
                                                    <Hash size={10} />
                                                    {log.meta.request_id}
                                                </span>
                                            )}
                                            {log.meta.tokens && (
                                                <span style={{ fontSize: '11px', color: '#667' }}>
                                                    {log.meta.tokens.prompt_tokens != null && `↑${log.meta.tokens.prompt_tokens}`}
                                                    {log.meta.tokens.completion_tokens != null && ` ↓${log.meta.tokens.completion_tokens}`}
                                                    {log.meta.tokens.total_tokens != null && ` Σ${log.meta.tokens.total_tokens}`}
                                                </span>
                                            )}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                        <div ref={bottomRef} />
                    </div>
                )}
            </div>
        </div>

        <ConfirmModal
            open={confirmClear}
            title={t.clearLogsTitle || "清除日志"}
            message={t.clearLogsMessage || "将永久删除所有日志记录，此操作不可撤销。"}
            confirmText={t.clearLogsConfirm || "全部清除"}
            onConfirm={clearLogs}
            onCancel={() => setConfirmClear(false)}
        />
        </>
    );
};

export default Logs;
