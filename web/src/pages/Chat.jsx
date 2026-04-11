import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Send, User, Bot, Loader, Plus, Copy, Check, MessageSquare, Trash2, Pencil, PanelLeftClose, PanelLeft, ChevronRight, Wrench, CheckCircle2, AlertCircle } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import API_BASE from '../config';

const wsBase = API_BASE.replace(/^http/, 'ws');

// --- Session / localStorage helpers ---
const STORAGE = {
    sessions: 'gazer_sessions',
    active: 'gazer_active_session',
    msgs: (id) => `gazer_session_${id}`,
};
const MAX_MSGS = 200;
const MAX_SESSIONS = 50;
const genId = () => `s_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
const genMessageId = () => `m_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
const previewText = (value, limit = 60) => {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    if (!text) return '';
    if (text.length <= limit) return text;
    return `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
};
const parseJsonObject = (value) => {
    if (!value || typeof value !== 'string') return null;
    try {
        const parsed = JSON.parse(value);
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
    } catch {
        return null;
    }
};
const compactInline = (value, limit = 72) => {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    if (!text) return '';
    if (text.length <= limit) return text;
    return `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
};
const resolveToolLabel = (payload) => {
    const explicit = compactInline(payload?.label || '', 90);
    if (explicit) return explicit;

    const toolName = compactInline(payload?.tool || 'tool', 32) || 'tool';
    const args = parseJsonObject(payload?.args_preview);
    if (args) {
        for (const key of ['command', 'pattern', 'path', 'query', 'url', 'name']) {
            const value = compactInline(args[key], 72);
            if (value) return `${toolName}: "${value}"`;
        }
        for (const value of Object.values(args)) {
            if (['string', 'number', 'boolean'].includes(typeof value)) {
                const preview = compactInline(value, 72);
                if (preview) return `${toolName}: "${preview}"`;
            }
        }
    }

    const rawArgs = compactInline(payload?.args_preview || '', 72);
    return rawArgs ? `${toolName}: "${rawArgs}"` : toolName;
};
const resolveToolMeta = (payload, status) => {
    const runningDetail = compactInline(payload?.progress_message || payload?.result_summary || '', 120);
    if (status === 'running' && runningDetail) return runningDetail;

    if (status === 'error') {
        const errorCode = compactInline(payload?.error_code || '', 32);
        const hint = compactInline(payload?.error_hint || '', 120);
        if (hint && errorCode) return `${errorCode} · ${hint}`;
        if (hint) return hint;
        if (errorCode) return errorCode;
    }

    const summary = compactInline(payload?.result_summary || payload?.result_preview || '', 120);
    if (summary && status !== 'running') return summary;
    if (payload?.has_media && status !== 'running') return 'Generated media attachment';
    return status === 'running' ? 'Running' : status === 'error' ? 'Failed' : 'Completed';
};

const resolveReplyPreview = (messages, replyTo) => {
    const targetId = String(replyTo || '').trim();
    if (!targetId) return null;
    return [...messages].reverse().find(
        (candidate) => candidate.role === 'user' && candidate.clientMessageId === targetId,
    ) || null;
};
const loadSessions = () => { try { return JSON.parse(localStorage.getItem(STORAGE.sessions)) || []; } catch { return []; } };
const persistSessions = (list) => {
    // Prune oldest sessions beyond limit, cleaning up their stored messages
    if (list.length > MAX_SESSIONS) {
        const removed = list.slice(MAX_SESSIONS);
        removed.forEach(s => { try { localStorage.removeItem(STORAGE.msgs(s.id)); } catch { /* ignore */ } });
        list = list.slice(0, MAX_SESSIONS);
    }
    localStorage.setItem(STORAGE.sessions, JSON.stringify(list));
};
const loadMsgs = (id) => { try { return (JSON.parse(localStorage.getItem(STORAGE.msgs(id))) || []).map(m => ({ ...m, time: new Date(m.time) })); } catch { return []; } };
const persistMsgs = (id, msgs) => {
    const data = msgs
        .filter(m => !m.streaming && m.role !== 'event')
        .slice(-MAX_MSGS)
        .map(({ role, content, time }) => ({ role, content, time }));
    localStorage.setItem(STORAGE.msgs(id), JSON.stringify(data));
};

const formatTime = (date) => {
    const h = String(date.getHours()).padStart(2, '0');
    const m = String(date.getMinutes()).padStart(2, '0');
    return `${h}:${m}`;
};

const CodeBlock = ({ children, className }) => {
    const [copied, setCopied] = useState(false);
    const lang = className?.replace('language-', '') || '';
    const code = String(children).replace(/\n$/, '');

    const handleCopy = () => {
        navigator.clipboard.writeText(code);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div style={{
            position: 'relative',
            background: '#0d1117',
            borderRadius: '8px',
            margin: '8px 0',
            border: '1px solid rgba(255,255,255,0.08)',
        }}>
            <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '6px 12px',
                borderBottom: '1px solid rgba(255,255,255,0.08)',
                fontSize: '11px',
                color: '#666',
            }}>
                <span>{lang}</span>
                <button
                    onClick={handleCopy}
                    style={{
                        color: copied ? '#4ade80' : '#666',
                        padding: '2px 6px',
                        borderRadius: '4px',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                        fontSize: '11px',
                    }}
                >
                    {copied ? <Check size={12} /> : <Copy size={12} />}
                    {copied ? 'Copied' : 'Copy'}
                </button>
            </div>
            <pre style={{
                margin: 0,
                padding: '12px',
                overflow: 'auto',
                fontSize: '13px',
                lineHeight: 1.5,
            }}>
                <code>{code}</code>
            </pre>
        </div>
    );
};

const ThinkBlock = ({ children, defaultOpen = false }) => {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <div style={{
            margin: '8px 0',
            border: '1px solid rgba(255,255,255,0.06)',
            borderRadius: '8px',
            background: 'rgba(255,255,255,0.02)',
            overflow: 'hidden',
        }}>
            <button
                onClick={() => setOpen(!open)}
                style={{
                    width: '100%',
                    padding: '6px 12px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    fontSize: '12px',
                    color: '#889',
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    textAlign: 'left',
                }}
            >
                <ChevronRight size={12} style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }} />
                💭 Thinking...
            </button>
            {open && (
                <div style={{ padding: '8px 12px', borderTop: '1px solid rgba(255,255,255,0.06)', fontSize: '13px', color: '#778', lineHeight: 1.5 }}>
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                        {children}
                    </ReactMarkdown>
                </div>
            )}
        </div>
    );
};

const markdownComponents = {
    code({ className, children, ...props }) {
        const isBlock = className || String(children).includes('\n');
        if (isBlock) {
            return <CodeBlock className={className}>{children}</CodeBlock>;
        }
        return (
            <code style={{
                background: 'rgba(255,255,255,0.08)',
                padding: '2px 6px',
                borderRadius: '4px',
                fontSize: '0.9em',
            }} {...props}>{children}</code>
        );
    },
    a({ href, children }) {
        return <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: '#58a6ff' }}>{children}</a>;
    },
    p({ children }) {
        return <p style={{ margin: '6px 0', lineHeight: 1.6, overflowWrap: 'anywhere', wordBreak: 'break-word' }}>{children}</p>;
    },
    ul({ children }) {
        return <ul style={{ margin: '6px 0', paddingLeft: '20px' }}>{children}</ul>;
    },
    ol({ children }) {
        return <ol style={{ margin: '6px 0', paddingLeft: '20px' }}>{children}</ol>;
    },
};

const ToolEventCard = ({ msg }) => {
    const status = msg.status || 'running';
    const palette = status === 'error'
        ? {
            bg: 'rgba(239,68,68,0.10)',
            border: 'rgba(239,68,68,0.28)',
            title: '#fecaca',
            meta: '#fca5a5',
            badgeBg: 'rgba(239,68,68,0.16)',
            badgeText: '#fca5a5',
            Icon: AlertCircle,
        }
        : status === 'ok'
            ? {
                bg: 'rgba(74,222,128,0.10)',
                border: 'rgba(74,222,128,0.24)',
                title: '#dcfce7',
                meta: '#86efac',
                badgeBg: 'rgba(74,222,128,0.16)',
                badgeText: '#86efac',
                Icon: CheckCircle2,
            }
            : {
                bg: 'rgba(96,165,250,0.10)',
                border: 'rgba(96,165,250,0.22)',
                title: '#dbeafe',
                meta: '#93c5fd',
                badgeBg: 'rgba(96,165,250,0.16)',
                badgeText: '#93c5fd',
                Icon: Loader,
            };
    const Icon = palette.Icon;
    const badgeText = status === 'error' ? 'Error' : status === 'ok' ? 'Done' : 'Running';

    return (
        <div style={{
            padding: '10px 12px',
            borderRadius: '14px 14px 14px 2px',
            background: palette.bg,
            border: `1px solid ${palette.border}`,
            color: palette.title,
            minWidth: 0,
        }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
                    <div style={{
                        width: '24px',
                        height: '24px',
                        borderRadius: '999px',
                        background: 'rgba(255,255,255,0.05)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        flexShrink: 0,
                    }}>
                        {status === 'running'
                            ? <Icon size={13} style={{ animation: 'spin 1s linear infinite' }} />
                            : <Icon size={13} />}
                    </div>
                    <div style={{
                        fontSize: '13px',
                        fontWeight: 600,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        minWidth: 0,
                    }}>
                        {msg.label || msg.toolName || 'tool'}
                    </div>
                </div>
                <span style={{
                    fontSize: '10px',
                    padding: '3px 7px',
                    borderRadius: '999px',
                    background: palette.badgeBg,
                    color: palette.badgeText,
                    flexShrink: 0,
                }}>
                    {badgeText}
                </span>
            </div>
            {msg.meta && (
                <div style={{
                    marginTop: '6px',
                    paddingLeft: '32px',
                    fontSize: '12px',
                    lineHeight: 1.5,
                    color: palette.meta,
                    whiteSpace: 'pre-wrap',
                    overflowWrap: 'anywhere',
                    wordBreak: 'break-word',
                }}>
                    {msg.meta}
                </div>
            )}
        </div>
    );
};

/** Split content into text and <think> blocks for rendering. */
const renderMessageContent = (content) => {
    if (!content) return null;
    const parts = [];
    const regex = /<think>([\s\S]*?)<\/think>/g;
    let lastIndex = 0;
    let match;
    while ((match = regex.exec(content)) !== null) {
        if (match.index > lastIndex) parts.push({ t: 'text', c: content.slice(lastIndex, match.index) });
        parts.push({ t: 'think', c: match[1].trim() });
        lastIndex = match.index + match[0].length;
    }
    const remaining = content.slice(lastIndex);
    // Detect incomplete <think> (streaming in progress)
    const incMatch = remaining.match(/^([\s\S]*?)<think>([\s\S]*)$/);
    if (incMatch) {
        if (incMatch[1].trim()) parts.push({ t: 'text', c: incMatch[1] });
        parts.push({ t: 'think_open', c: incMatch[2].trim() });
    } else if (remaining.trim()) {
        parts.push({ t: 'text', c: remaining });
    }
    if (parts.length === 0 || (parts.length === 1 && parts[0].t === 'text')) {
        return <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{content}</ReactMarkdown>;
    }
    return parts.map((p, i) => {
        if (p.t === 'think') return <ThinkBlock key={i}>{p.c}</ThinkBlock>;
        if (p.t === 'think_open') return <ThinkBlock key={i} defaultOpen>{p.c}</ThinkBlock>;
        return p.c.trim() ? <ReactMarkdown key={i} remarkPlugins={[remarkGfm]} components={markdownComponents}>{p.c}</ReactMarkdown> : null;
    });
};

const Chat = ({ t }) => {
    // --- Session state ---
    const [sessions, setSessions] = useState(() => loadSessions());
    const [activeSessionId, setActiveSessionId] = useState(() => {
        const saved = localStorage.getItem(STORAGE.active);
        const list = loadSessions();
        if (saved && list.some(s => s.id === saved)) return saved;
        // Auto-create first session
        const id = genId();
        const first = { id, name: 'New Chat', lastMessage: '', updatedAt: new Date().toISOString() };
        persistSessions([first]);
        return id;
    });
    const [sidebarOpen, setSidebarOpen] = useState(true);
    const [editingId, setEditingId] = useState(null);
    const [editingName, setEditingName] = useState('');

    const [messages, setMessages] = useState(() => {
        const saved = localStorage.getItem(STORAGE.active);
        const list = loadSessions();
        if (saved && list.some(s => s.id === saved)) return loadMsgs(saved);
        return [];
    });
    const [input, setInput] = useState('');
    const [connected, setConnected] = useState(false);
    const [isTyping, setIsTyping] = useState(false);
    const [autoScroll, setAutoScroll] = useState(true);
    const ws = useRef(null);
    const reconnectTimer = useRef(null);
    const responseTimeoutRef = useRef(null);
    const scrollContainerRef = useRef(null);
    const textareaRef = useRef(null);
    const sessionRef = useRef(activeSessionId);
    const mountedRef = useRef(true);

    // Keep sessionRef in sync without re-triggering connectWs
    useEffect(() => { sessionRef.current = activeSessionId; }, [activeSessionId]);

    // Ensure sessions list includes the active session (first load)
    useEffect(() => {
        setSessions(prev => {
            if (prev.some(s => s.id === activeSessionId)) return prev;
            const updated = [{ id: activeSessionId, name: 'New Chat', lastMessage: '', updatedAt: new Date().toISOString() }, ...prev];
            persistSessions(updated);
            return updated;
        });
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    // Persist messages when they change (skip while streaming)
    useEffect(() => {
        if (!activeSessionId || messages.some(m => m.streaming)) return;
        persistMsgs(activeSessionId, messages);
        if (messages.length > 0) {
            const last = [...messages].reverse().find((item) => item.role !== 'event') || messages[messages.length - 1];
            setSessions(prev => {
                const updated = prev.map(s =>
                    s.id === activeSessionId
                        ? { ...s, lastMessage: (last.content || '').slice(0, 60), updatedAt: new Date().toISOString() }
                        : s
                );
                persistSessions(updated);
                return updated;
            });
        }
    }, [messages, activeSessionId]);

    // Persist active session id
    useEffect(() => {
        if (activeSessionId) localStorage.setItem(STORAGE.active, activeSessionId);
    }, [activeSessionId]);

    const scrollToBottom = useCallback(() => {
        if (!autoScroll) return;
        const el = scrollContainerRef.current;
        if (!el) return;
        el.scrollTop = el.scrollHeight;
    }, [autoScroll]);

    useEffect(() => {
        scrollToBottom();
    }, [messages, autoScroll, scrollToBottom]);

    const handleScroll = () => {
        const el = scrollContainerRef.current;
        if (!el) return;
        const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        setAutoScroll(atBottom);
    };

    const clearResponseTimeout = useCallback(() => {
        if (responseTimeoutRef.current) {
            clearTimeout(responseTimeoutRef.current);
            responseTimeoutRef.current = null;
        }
    }, []);

    const startResponseTimeout = useCallback(() => {
        clearResponseTimeout();
        responseTimeoutRef.current = setTimeout(() => {
            setIsTyping(false);
        }, 45000);
    }, [clearResponseTimeout]);

    const connectWs = useCallback(function connectChatWs(sessionId) {
        // Close any existing connection first
        if (ws.current) {
            const old = ws.current;
            ws.current = null;
            // Suppress event handlers on the old socket
            old.onclose = null;
            old.onerror = null;
            old.onmessage = null;
            if (old.readyState === WebSocket.OPEN) {
                old.onopen = null;
                try { old.close(); } catch { /* ignore */ }
            } else if (old.readyState === WebSocket.CONNECTING) {
                // Let it finish the handshake, then close — avoids "closed before established" warning
                old.onopen = () => { try { old.close(); } catch { /* ignore */ } };
            }
        }

        const wsUrl = `${wsBase}/ws/chat?session_id=${encodeURIComponent(sessionId || 'web-main')}`;
        const socket = new WebSocket(wsUrl);

        let pingTimer;

        socket.onopen = () => {
            if (!mountedRef.current) { socket.close(); return; }
            setConnected(true);
            if (reconnectTimer.current) { clearTimeout(reconnectTimer.current); reconnectTimer.current = null; }

            // Keep connection alive
            pingTimer = setInterval(() => {
                if (socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({ type: 'ping' }));
                }
            }, 25000);
        };

        socket.onclose = () => {
            clearInterval(pingTimer);
            if (!mountedRef.current) return;
            setConnected(false);
            setIsTyping(false);
            clearResponseTimeout();
            // Only auto-reconnect if this is still the active socket
            if (ws.current === socket) {
                reconnectTimer.current = setTimeout(() => {
                    if (mountedRef.current) connectChatWs(sessionRef.current || 'web-main');
                }, 3000);
            }
        };

        socket.onerror = () => {
            clearInterval(pingTimer);
            if (!mountedRef.current) return;
            setConnected(false);
            setIsTyping(false);
            clearResponseTimeout();
        };

        socket.onmessage = (event) => {
            let data;
            try {
                data = JSON.parse(event.data);
            } catch (err) {
                console.error("Failed to parse WebSocket message", err);
                return;
            }
            // Use ref to get current session, avoiding stale closures
            const currentSession = sessionRef.current || 'web-main';
            if (data.chat_id && data.chat_id !== currentSession) {
                return;
            }
            if (data.type === 'chat_stream') {
                setIsTyping(false);
                clearResponseTimeout();
                setMessages(prev => {
                    const last = prev[prev.length - 1];
                    if (last && last.role === 'assistant' && last.streaming) {
                        return [...prev.slice(0, -1), { ...last, content: last.content + data.content, replyTo: data.reply_to || last.replyTo || '' }];
                    }
                    return [...prev, { role: 'assistant', content: data.content, time: new Date(), streaming: true, replyTo: data.reply_to || '' }];
                });
            } else if (data.type === 'tool_call_event') {
                const payload = data.payload || {};
                const eventType = data.event_type || '';
                const toolCallId = String(payload.tool_call_id || `${payload.tool || 'tool'}_${Date.now()}`);
                const nextStatus = (eventType === 'call' || eventType === 'progress') ? 'running' : (payload.status || 'ok');
                startResponseTimeout();
                setMessages(prev => {
                    const existingIndex = prev.findIndex(
                        (item) => item.role === 'event' && item.eventKind === 'tool' && item.toolCallId === toolCallId,
                    );
                    const eventMessage = {
                        role: 'event',
                        eventKind: 'tool',
                        toolCallId,
                        toolName: payload.tool || 'tool',
                        label: resolveToolLabel(payload),
                        meta: resolveToolMeta(payload, nextStatus),
                        status: nextStatus,
                        payload,
                        time: existingIndex >= 0 ? prev[existingIndex].time : new Date(),
                    };
                    if (existingIndex >= 0) {
                        const updated = [...prev];
                        updated[existingIndex] = { ...updated[existingIndex], ...eventMessage };
                        return updated;
                    }
                    return [...prev, eventMessage];
                });
            } else if (data.type === 'chat_end') {
                setIsTyping(false);
                clearResponseTimeout();
                setMessages(prev => {
                    const last = prev[prev.length - 1];
                    if (last && last.role === 'assistant' && last.streaming) {
                        return [...prev.slice(0, -1), { ...last, content: data.content, streaming: false, replyTo: data.reply_to || last.replyTo || '' }];
                    }
                    return [...prev, { role: 'assistant', content: data.content, time: new Date(), replyTo: data.reply_to || '' }];
                });
            } else if (data.type === 'chat_response') {
                setIsTyping(false);
                clearResponseTimeout();
                setMessages(prev => [...prev, { role: 'assistant', content: data.content, time: new Date(), replyTo: data.reply_to || '' }]);
            } else if (data.type === 'error') {
                setIsTyping(false);
                clearResponseTimeout();
                setMessages(prev => [...prev, { role: 'system', content: `Error: ${data.message}`, time: new Date() }]);
            }
        };

        ws.current = socket;
    }, [clearResponseTimeout]); // No activeSessionId dependency — uses sessionRef

    // Connect on mount and when activeSessionId changes
    useEffect(() => {
        mountedRef.current = true;
        connectWs(activeSessionId || 'web-main');
        return () => {
            mountedRef.current = false;
            if (reconnectTimer.current) { clearTimeout(reconnectTimer.current); reconnectTimer.current = null; }
            clearResponseTimeout();
            const sock = ws.current;
            ws.current = null;
            if (!sock) return;
            // Suppress handlers to avoid post-unmount state updates
            sock.onclose = null;
            sock.onerror = null;
            sock.onmessage = null;
            if (sock.readyState === WebSocket.OPEN) {
                sock.onopen = null;
                try { sock.close(); } catch { /* ignore */ }
            } else if (sock.readyState === WebSocket.CONNECTING) {
                // Wait for handshake to complete before closing — avoids "closed before established" warning
                sock.onopen = () => { try { sock.close(); } catch { /* ignore */ } };
            }
        };
    }, [connectWs, activeSessionId, clearResponseTimeout]);

    const sendMessage = (e) => {
        e.preventDefault();
        if (!input.trim() || !connected) return;
        if (ws.current?.readyState !== WebSocket.OPEN) return;

        // Auto-name session from first user message
        if (!messages.some(m => m.role === 'user')) {
            setSessions(prev => {
                const s = prev.find(x => x.id === activeSessionId);
                if (s && s.name === 'New Chat') {
                    const u = prev.map(x => x.id === activeSessionId ? { ...x, name: input.trim().slice(0, 30) } : x);
                    persistSessions(u);
                    return u;
                }
                return prev;
            });
        }

        const clientMessageId = genMessageId();
        setMessages(prev => [...prev, { role: 'user', content: input, time: new Date(), clientMessageId }]);
        setIsTyping(true);
        startResponseTimeout();
        setAutoScroll(true);
        ws.current.send(JSON.stringify({
            content: input,
            session_id: activeSessionId || 'web-main',
            metadata: {
                reply_to: clientMessageId,
                client_message_id: clientMessageId,
            },
        }));
        setInput('');
        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto';
        }
    };

    // --- Session management ---
    const handleNewSession = () => {
        const id = genId();
        const entry = { id, name: 'New Chat', lastMessage: '', updatedAt: new Date().toISOString() };
        setSessions(prev => { const u = [entry, ...prev]; persistSessions(u); return u; });
        setActiveSessionId(id);
        setMessages([]);
        setIsTyping(false);
        clearResponseTimeout();
    };

    const handleSwitchSession = (id) => {
        if (id === activeSessionId) return;
        setActiveSessionId(id);
        setMessages(loadMsgs(id));
        setIsTyping(false);
        clearResponseTimeout();
    };

    const handleDeleteSession = (id) => {
        localStorage.removeItem(STORAGE.msgs(id));
        setSessions(prev => {
            const updated = prev.filter(s => s.id !== id);
            persistSessions(updated);
            if (id === activeSessionId) {
                if (updated.length > 0) {
                    setActiveSessionId(updated[0].id);
                    setMessages(loadMsgs(updated[0].id));
                } else {
                    // Create a fresh session
                    const newId = genId();
                    const entry = { id: newId, name: 'New Chat', lastMessage: '', updatedAt: new Date().toISOString() };
                    persistSessions([entry]);
                    setActiveSessionId(newId);
                    setMessages([]);
                    return [entry];
                }
            }
            return updated;
        });
        setIsTyping(false);
        clearResponseTimeout();
    };

    const handleRenameSubmit = (id) => {
        const name = editingName.trim() || 'New Chat';
        setSessions(prev => { const u = prev.map(s => s.id === id ? { ...s, name } : s); persistSessions(u); return u; });
        setEditingId(null);
    };

    const handleKeyDown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage(e);
        }
    };

    const handleTextareaInput = (e) => {
        setInput(e.target.value);
        e.target.style.height = 'auto';
        e.target.style.height = Math.min(e.target.scrollHeight, 150) + 'px';
    };

    const isEmpty = messages.length === 0;

    const formatRelTime = (iso) => {
        if (!iso) return '';
        const d = new Date(iso);
        const now = new Date();
        const diff = now - d;
        if (diff < 60000) return 'just now';
        if (diff < 3600000) return `${Math.floor(diff / 60000)}m`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}h`;
        return d.toLocaleDateString();
    };

    return (
        <div style={{ display: 'flex', flex: 1, width: '100%', overflow: 'hidden', minWidth: 0 }}>
            {/* Session Sidebar */}
            {sidebarOpen && (
                <div style={{
                    width: '240px',
                    flexShrink: 0,
                    background: 'rgba(10, 18, 40, 0.8)',
                    borderRadius: '16px 0 0 16px',
                    border: '1px solid rgba(255,255,255,0.08)',
                    borderRight: 'none',
                    display: 'flex',
                    flexDirection: 'column',
                    overflow: 'hidden',
                }}>
                    <div style={{ padding: '10px', borderBottom: '1px solid rgba(255,255,255,0.08)', display: 'flex', gap: '6px' }}>
                        <button onClick={handleNewSession} className="btn-ghost" style={{ flex: 1, justifyContent: 'center' }}>
                            <Plus size={14} /> {t.newSession || 'New Chat'}
                        </button>
                        <button onClick={() => setSidebarOpen(false)} className="btn-ghost" style={{ padding: '6px' }}>
                            <PanelLeftClose size={14} />
                        </button>
                    </div>
                    <div style={{ flex: 1, overflowY: 'auto', padding: '4px' }}>
                        {sessions.map(s => (
                            <div
                                key={s.id}
                                onClick={() => handleSwitchSession(s.id)}
                                style={{
                                    padding: '8px 10px',
                                    borderRadius: '8px',
                                    cursor: 'pointer',
                                    background: s.id === activeSessionId ? 'rgba(59,130,246,0.15)' : 'transparent',
                                    border: s.id === activeSessionId ? '1px solid rgba(59,130,246,0.25)' : '1px solid transparent',
                                    marginBottom: '2px',
                                    transition: 'background 0.15s',
                                }}
                                onMouseEnter={e => { if (s.id !== activeSessionId) e.currentTarget.style.background = 'rgba(255,255,255,0.04)'; }}
                                onMouseLeave={e => { if (s.id !== activeSessionId) e.currentTarget.style.background = 'transparent'; }}
                            >
                                {editingId === s.id ? (
                                    <input
                                        autoFocus
                                        value={editingName}
                                        onChange={e => setEditingName(e.target.value)}
                                        onBlur={() => handleRenameSubmit(s.id)}
                                        onKeyDown={e => { if (e.key === 'Enter') handleRenameSubmit(s.id); if (e.key === 'Escape') setEditingId(null); }}
                                        onClick={e => e.stopPropagation()}
                                        style={{
                                            width: '100%', background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(59,130,246,0.4)',
                                            borderRadius: '4px', padding: '2px 6px', color: '#fff', fontSize: '12px', outline: 'none',
                                        }}
                                    />
                                ) : (
                                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{ fontSize: '12px', color: '#ddd', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                                {s.name || 'New Chat'}
                                            </div>
                                            <div style={{ fontSize: '10px', color: '#556', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', marginTop: '2px' }}>
                                                {s.lastMessage || (t.chatEmptyHint ? '...' : '...')}
                                            </div>
                                        </div>
                                        <div style={{ display: 'flex', gap: '2px', marginLeft: '4px', flexShrink: 0 }}>
                                            <span style={{ fontSize: '9px', color: '#445', whiteSpace: 'nowrap', marginRight: '4px', alignSelf: 'center' }}>
                                                {formatRelTime(s.updatedAt)}
                                            </span>
                                            <button
                                                onClick={e => { e.stopPropagation(); setEditingId(s.id); setEditingName(s.name || ''); }}
                                                style={{ color: '#556', padding: '2px', borderRadius: '4px' }}
                                                title="Rename"
                                            >
                                                <Pencil size={11} />
                                            </button>
                                            <button
                                                onClick={e => { e.stopPropagation(); handleDeleteSession(s.id); }}
                                                style={{ color: '#556', padding: '2px', borderRadius: '4px' }}
                                                title="Delete"
                                            >
                                                <Trash2 size={11} />
                                            </button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Main Chat Area */}
            <div className="flex flex-col overflow-hidden" style={{ flex: 1, minWidth: 0, background: 'rgba(15, 25, 50, 0.65)', borderRadius: sidebarOpen ? '0 16px 16px 0' : '16px', border: '1px solid rgba(255,255,255,0.08)' }}>
                {/* Header */}
                <div style={{
                    padding: '12px 16px',
                    borderBottom: '1px solid rgba(255,255,255,0.08)',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    flexShrink: 0,
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        {!sidebarOpen && (
                            <button onClick={() => setSidebarOpen(true)} className="btn-ghost" style={{ padding: '6px' }}>
                                <PanelLeft size={16} />
                            </button>
                        )}
                        <h2 style={{
                            fontSize: '15px',
                            fontWeight: 600,
                            color: '#fff',
                            margin: 0,
                            display: 'flex',
                            alignItems: 'center',
                            gap: '8px',
                        }}>
                            <Bot size={20} style={{ color: '#60a5fa' }} />
                            {t.directLink}
                        </h2>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <span style={{
                            fontSize: '11px',
                            padding: '3px 8px',
                            borderRadius: '10px',
                            background: connected ? 'rgba(74,222,128,0.15)' : 'rgba(239,68,68,0.15)',
                            color: connected ? '#4ade80' : '#ef4444',
                        }}>
                            {connected ? t.statusLive : t.statusDisconnectedCaps}
                        </span>
                    </div>
                </div>

                {/* Messages */}
                <div
                    ref={scrollContainerRef}
                    onScroll={handleScroll}
                    style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '16px' }}
                >
                    {isEmpty ? (
                        <div style={{
                            display: 'flex',
                            flexDirection: 'column',
                            alignItems: 'center',
                            justifyContent: 'center',
                            height: '100%',
                            color: '#556',
                            textAlign: 'center',
                            gap: '16px',
                        }}>
                            <MessageSquare size={48} style={{ color: '#334', strokeWidth: 1.5 }} />
                            <div>
                                <div style={{ fontSize: '16px', color: '#889', marginBottom: '4px' }}>
                                    {t.chatWelcome}
                                </div>
                                <div style={{ fontSize: '12px', color: '#556' }}>
                                    {t.chatEmptyHint || 'Type a message below to start a conversation.'}
                                </div>
                            </div>
                        </div>
                    ) : (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                            {messages.map((msg, idx) => (
                                (() => {
                                    const replied = msg.role === 'assistant' ? resolveReplyPreview(messages, msg.replyTo) : null;
                                    const isEvent = msg.role === 'event' && msg.eventKind === 'tool';
                                    return (
                                <div key={idx} style={{
                                    display: 'flex',
                                    justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                                }}>
                                    <div style={{
                                        maxWidth: '75%',
                                        minWidth: 0,
                                        display: 'flex',
                                        gap: '10px',
                                        flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
                                        alignItems: 'flex-start',
                                    }}>
                                        <div style={{
                                            width: '28px',
                                            height: '28px',
                                            borderRadius: '50%',
                                            background: msg.role === 'user' ? 'rgba(59,130,246,0.3)' : 'rgba(255,255,255,0.08)',
                                            display: 'flex',
                                            alignItems: 'center',
                                            justifyContent: 'center',
                                            flexShrink: 0,
                                        }}>
                                            {msg.role === 'user'
                                                ? <User size={14} color="#93c5fd" />
                                                : isEvent
                                                    ? <Wrench size={14} color="#93c5fd" />
                                                    : <Bot size={14} color="#60a5fa" />}
                                        </div>
                                        <div style={{ minWidth: 0 }}>
                                            {isEvent ? (
                                                <ToolEventCard msg={msg} />
                                            ) : (
                                                <div style={{
                                                    padding: '10px 14px',
                                                    borderRadius: msg.role === 'user' ? '14px 14px 2px 14px' : '14px 14px 14px 2px',
                                                    background: msg.role === 'user' ? 'rgba(59,130,246,0.2)' : 'rgba(255,255,255,0.05)',
                                                    border: msg.role === 'user' ? '1px solid rgba(59,130,246,0.25)' : '1px solid rgba(255,255,255,0.08)',
                                                    color: '#e0e8f0',
                                                    fontSize: '14px',
                                                    lineHeight: 1.6,
                                                    overflowWrap: 'anywhere',
                                                    wordBreak: 'break-word',
                                                    maxWidth: '100%',
                                                }}>
                                                    {replied && (
                                                        <div className="chat-reply-card">
                                                            <div className="chat-reply-kicker">Replying to</div>
                                                            <div className="chat-reply-preview">{previewText(replied.content, 60)}</div>
                                                        </div>
                                                    )}
                                                    {msg.role === 'assistant' ? (
                                                        renderMessageContent(msg.content)
                                                    ) : (
                                                        <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', wordBreak: 'break-word' }}>{msg.content}</div>
                                                    )}
                                                </div>
                                            )}
                                            {msg.time && (
                                                <div style={{
                                                    fontSize: '10px',
                                                    color: '#556',
                                                    marginTop: '3px',
                                                    textAlign: msg.role === 'user' ? 'right' : 'left',
                                                    paddingInline: '4px',
                                                }}>
                                                    {formatTime(msg.time)}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                </div>
                                    );
                                })()
                            ))}
                            {isTyping && (
                                <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
                                    <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-start' }}>
                                        <div style={{
                                            width: '28px',
                                            height: '28px',
                                            borderRadius: '50%',
                                            background: 'rgba(255,255,255,0.08)',
                                            display: 'flex',
                                            alignItems: 'center',
                                            justifyContent: 'center',
                                        }}>
                                            <Bot size={14} color="#60a5fa" />
                                        </div>
                                        <div style={{
                                            padding: '10px 14px',
                                            borderRadius: '14px 14px 14px 2px',
                                            background: 'rgba(255,255,255,0.05)',
                                            border: '1px solid rgba(255,255,255,0.08)',
                                            color: '#889',
                                            fontSize: '12px',
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: '6px',
                                        }}>
                                            {t.thinking}
                                            <Loader size={12} style={{ animation: 'spin 1s linear infinite' }} />
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* Input */}
                <div style={{
                    padding: '12px 16px',
                    borderTop: '1px solid rgba(255,255,255,0.08)',
                    background: 'rgba(0,0,0,0.15)',
                    flexShrink: 0,
                }}>
                    <form onSubmit={sendMessage} style={{ display: 'flex', gap: '8px', alignItems: 'flex-end' }}>
                        <textarea
                            ref={textareaRef}
                            value={input}
                            onChange={handleTextareaInput}
                            onKeyDown={handleKeyDown}
                            placeholder={connected ? (t.chatInputHint || 'Message (Enter to send, Shift+Enter for new line)') : t.connecting}
                            disabled={!connected}
                            rows={1}
                            style={{
                                flex: 1,
                                background: 'rgba(0,0,0,0.3)',
                                border: '1px solid rgba(255,255,255,0.1)',
                                borderRadius: '10px',
                                padding: '10px 14px',
                                color: '#fff',
                                fontSize: '14px',
                                outline: 'none',
                                resize: 'none',
                                lineHeight: 1.5,
                                maxHeight: '150px',
                                transition: 'border-color 0.2s',
                            }}
                        />
                        <button
                            type="submit"
                            disabled={!connected || !input.trim()}
                            style={{
                                background: (!connected || !input.trim()) ? 'rgba(59,130,246,0.3)' : '#3b82f6',
                                border: 'none',
                                color: '#fff',
                                padding: '10px',
                                borderRadius: '10px',
                                cursor: (!connected || !input.trim()) ? 'not-allowed' : 'pointer',
                                opacity: (!connected || !input.trim()) ? 0.5 : 1,
                                transition: 'all 0.2s',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                            }}
                        >
                            <Send size={18} />
                        </button>
                    </form>
                </div>
            </div>
        </div>
    );
};

export default Chat;
