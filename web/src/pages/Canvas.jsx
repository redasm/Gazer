import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Layers, Trash2 } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import axios from 'axios';
import API_BASE from '../config';
import A2UIRenderer from '../components/A2UIRenderer';

const wsBase = API_BASE.replace(/^http/, 'ws');

const PanelCard = ({ panel, onUserAction }) => {
    const renderContent = () => {
        switch (panel.content_type) {
            case 'markdown':
                return <ReactMarkdown remarkPlugins={[remarkGfm]}>{panel.content}</ReactMarkdown>;
            case 'json':
                try {
                    return (
                        <pre style={{ margin: 0, fontSize: 12, overflowX: 'auto', whiteSpace: 'pre-wrap' }}>
                            {JSON.stringify(JSON.parse(panel.content), null, 2)}
                        </pre>
                    );
                } catch {
                    return <pre style={{ margin: 0, fontSize: 12 }}>{panel.content}</pre>;
                }
            case 'table':
                try {
                    const data = JSON.parse(panel.content);
                    if (!Array.isArray(data) || data.length === 0) return <span style={{ color: '#667' }}>Empty table</span>;
                    const cols = Object.keys(data[0]);
                    return (
                        <div style={{ overflowX: 'auto' }}>
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                                <thead>
                                    <tr>{cols.map(c => <th key={c} style={{ padding: '6px 10px', borderBottom: '1px solid rgba(255,255,255,0.1)', color: '#aab', textAlign: 'left' }}>{c}</th>)}</tr>
                                </thead>
                                <tbody>
                                    {data.map((row, i) => (
                                        <tr key={i}>{cols.map(c => <td key={c} style={{ padding: '4px 10px', borderBottom: '1px solid rgba(255,255,255,0.05)', color: '#ddd' }}>{String(row[c] ?? '')}</td>)}</tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    );
                } catch {
                    return <pre style={{ margin: 0, fontSize: 12 }}>{panel.content}</pre>;
                }
            case 'a2ui':
                try {
                    const snapshot = JSON.parse(panel.content);
                    return <A2UIRenderer snapshot={snapshot} onUserAction={onUserAction} />;
                } catch {
                    return <pre style={{ margin: 0, fontSize: 12 }}>{panel.content}</pre>;
                }
            default:
                return <pre style={{ margin: 0, fontSize: 13, whiteSpace: 'pre-wrap', color: '#ddd' }}>{panel.content}</pre>;
        }
    };

    return (
        <div style={{
            background: 'rgba(15, 25, 50, 0.65)',
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: '12px',
            overflow: 'hidden',
        }}>
            <div style={{
                padding: '10px 14px',
                borderBottom: '1px solid rgba(255,255,255,0.06)',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
            }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 13, fontWeight: 500, color: '#ddd' }}>{panel.title || panel.id}</span>
                    <code style={{ fontSize: 10, background: 'rgba(255,255,255,0.06)', padding: '1px 6px', borderRadius: 4, color: '#88a' }}>
                        {panel.content_type}
                    </code>
                </div>
            </div>
            <div style={{ padding: '12px 14px', fontSize: 13, lineHeight: 1.6, color: '#ccc', maxHeight: 400, overflowY: 'auto' }}>
                {renderContent()}
            </div>
        </div>
    );
};

const Canvas = ({ t }) => {
    const [panels, setPanels] = useState([]);
    const [version, setVersion] = useState(0);
    const [connected, setConnected] = useState(false);
    const wsRef = useRef(null);
    const reconnectRef = useRef(null);

    const connectWs = useCallback(() => {
        if (wsRef.current && wsRef.current.readyState <= 1) return;
        const url = `${wsBase}/ws/canvas`;
        const socket = new WebSocket(url);

        let pingTimer;

        socket.onopen = () => {
            console.log('Canvas WebSocket Opened.');
            setConnected(true);
            if (reconnectRef.current) { clearTimeout(reconnectRef.current); reconnectRef.current = null; }

            // Keep connection alive
            pingTimer = setInterval(() => {
                if (socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({ type: 'ping' }));
                }
            }, 30000);
        };
        socket.onclose = (event) => {
            console.error("Closed code:", event.code, "reason:", event.reason);
            clearInterval(pingTimer);
            setConnected(false);
            reconnectRef.current = setTimeout(connectWs, 3000);
        };
        socket.onerror = (error) => {
            console.error('Canvas WebSocket Error:', error);
            clearInterval(pingTimer);
        };
        socket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'canvas_update') {
                    setPanels(data.panels || []);
                    setVersion(data.version || 0);
                }
            } catch { /* ignore */ }
        };
        wsRef.current = socket;
    }, []);

    useEffect(() => {
        // Debounce to prevent React 18 StrictMode double-mount aborted WebSockets
        const mountTimer = setTimeout(() => {
            connectWs();
        }, 50);
        return () => {
            clearTimeout(mountTimer);
            if (reconnectRef.current) clearTimeout(reconnectRef.current);
            wsRef.current?.close();
        };
    }, [connectWs]);

    const handleClear = async () => {
        try {
            await axios.delete(`${API_BASE}/canvas`);
        } catch { /* ignore */ }
    };

    const sendUserAction = useCallback((userAction) => {
        const socket = wsRef.current;
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        try {
            socket.send(JSON.stringify({ userAction }));
        } catch {
            // ignore send failures
        }
    }, []);

    return (
        <div style={{ maxWidth: 900 }}>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
                <h2 style={{ fontSize: 18, fontWeight: 600, color: '#fff', margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Layers size={20} style={{ color: '#60a5fa' }} />
                    {t.canvas || 'Canvas'}
                    <span style={{ fontSize: 11, color: '#556', fontWeight: 400 }}>v{version}</span>
                </h2>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{
                        fontSize: 10, padding: '2px 8px', borderRadius: 10,
                        background: connected ? 'rgba(74,222,128,0.15)' : 'rgba(239,68,68,0.15)',
                        color: connected ? '#4ade80' : '#ef4444',
                    }}>
                        {connected ? 'Live' : 'Disconnected'}
                    </span>
                    <button onClick={handleClear} className="btn-ghost" title="Clear all panels">
                        <Trash2 size={14} />
                    </button>
                </div>
            </div>

            {/* Panels */}
            {panels.length === 0 ? (
                <div style={{
                    background: 'rgba(15, 25, 50, 0.65)',
                    border: '1px solid rgba(255,255,255,0.08)',
                    borderRadius: 12,
                    padding: 40,
                    textAlign: 'center',
                    color: '#556',
                }}>
                    <Layers size={36} style={{ color: '#334', marginBottom: 12 }} />
                    <div style={{ fontSize: 14, color: '#778' }}>{t.noCanvasPanels || 'No canvas panels'}</div>
                    <div style={{ fontSize: 12, marginTop: 4 }}>
                        {t.noCanvasPanelsHint || 'The agent can build UI here using the a2ui_apply tool.'}
                    </div>
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {panels.map(panel => (
                        <PanelCard key={panel.id} panel={panel} onUserAction={sendUserAction} />
                    ))}
                </div>
            )}
        </div>
    );
};

export default Canvas;
