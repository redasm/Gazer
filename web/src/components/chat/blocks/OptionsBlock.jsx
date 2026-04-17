import React from 'react';

/**
 * OptionsBlock — renders a list of selectable options as buttons.
 *
 * Accepted `data` shape:
 *   { title?: string, items: [{label, value?}] }
 *
 * Click behavior: dispatches a CustomEvent on `window` with the chosen
 * value, so the host Chat page can forward it through its existing
 * send-message pipeline without tight coupling.
 */
const OptionsBlock = ({ data = {}, messageId }) => {
    const items = Array.isArray(data.items) ? data.items : [];

    const handleClick = (item) => {
        const value = item.value ?? item.label;
        const detail = { messageId, value, label: item.label };
        window.dispatchEvent(new CustomEvent('gazer:chat-option', { detail }));
    };

    return (
        <div
            style={{
                padding: '10px 12px',
                borderRadius: 10,
                background: 'rgba(239,35,60,0.05)',
                border: '1px solid rgba(239,35,60,0.18)',
            }}
        >
            {data.title && (
                <div
                    style={{
                        color: '#e0e8f0',
                        fontSize: 13,
                        fontWeight: 600,
                        marginBottom: 8,
                    }}
                >
                    {data.title}
                </div>
            )}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {items.map((item, i) => (
                    <button
                        key={i}
                        type="button"
                        onClick={() => handleClick(item)}
                        style={{
                            padding: '6px 12px',
                            borderRadius: 999,
                            fontSize: 12,
                            background: 'rgba(239,35,60,0.12)',
                            border: '1px solid rgba(239,35,60,0.32)',
                            color: '#ffd0d6',
                            cursor: 'pointer',
                        }}
                    >
                        {item.label}
                    </button>
                ))}
            </div>
        </div>
    );
};

export default OptionsBlock;
