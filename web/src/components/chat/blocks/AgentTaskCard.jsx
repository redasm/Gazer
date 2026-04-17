import React from 'react';
import { Loader, CheckCircle2, AlertCircle, Clock } from 'lucide-react';

/**
 * AgentTaskCard — status card for delegated agent tasks.
 *
 * Accepted `data` shape:
 *   { task, task_id?, status: 'pending'|'running'|'done'|'error', detail?, progress? }
 */
const PALETTE = {
    pending: { bg: 'rgba(148,163,184,0.12)', border: 'rgba(148,163,184,0.28)', color: '#cbd5e1', Icon: Clock },
    running: { bg: 'rgba(96,165,250,0.12)', border: 'rgba(96,165,250,0.28)', color: '#bfdbfe', Icon: Loader },
    done: { bg: 'rgba(74,222,128,0.12)', border: 'rgba(74,222,128,0.28)', color: '#bbf7d0', Icon: CheckCircle2 },
    error: { bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.30)', color: '#fecaca', Icon: AlertCircle },
};

const AgentTaskCard = ({ data = {} }) => {
    const status = PALETTE[data.status] ? data.status : 'running';
    const tone = PALETTE[status];
    const Icon = tone.Icon;

    return (
        <div
            style={{
                padding: '10px 12px',
                borderRadius: 10,
                background: tone.bg,
                border: `1px solid ${tone.border}`,
                color: tone.color,
            }}
        >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Icon
                    size={14}
                    style={
                        status === 'running'
                            ? { animation: 'spin 1s linear infinite' }
                            : undefined
                    }
                />
                <span style={{ fontSize: 13, fontWeight: 600 }}>
                    {data.task || 'Task'}
                </span>
                {data.task_id && (
                    <span style={{ fontSize: 11, color: '#778', marginLeft: 'auto' }}>
                        #{data.task_id}
                    </span>
                )}
            </div>
            {data.detail && (
                <div style={{ fontSize: 12, marginTop: 6, color: '#aab' }}>
                    {data.detail}
                </div>
            )}
            {typeof data.progress === 'number' && (
                <div
                    style={{
                        marginTop: 8,
                        height: 4,
                        background: 'rgba(255,255,255,0.08)',
                        borderRadius: 999,
                        overflow: 'hidden',
                    }}
                >
                    <div
                        style={{
                            width: `${Math.max(0, Math.min(100, data.progress))}%`,
                            height: '100%',
                            background: tone.color,
                            opacity: 0.6,
                        }}
                    />
                </div>
            )}
        </div>
    );
};

export default AgentTaskCard;
