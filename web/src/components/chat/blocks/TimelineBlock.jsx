import React from 'react';

/**
 * TimelineBlock — vertical event list.
 *
 * Accepted `data` shape:
 *   { items: [{time, title, detail?}] }
 */
const TimelineBlock = ({ data = {} }) => {
    const items = Array.isArray(data.items) ? data.items : [];

    return (
        <div
            style={{
                padding: '10px 12px',
                borderRadius: 10,
                background: 'rgba(255,255,255,0.04)',
                border: '1px solid rgba(255,255,255,0.08)',
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
            <ul
                style={{
                    listStyle: 'none',
                    padding: 0,
                    margin: 0,
                    borderLeft: '1px solid rgba(239,35,60,0.35)',
                }}
            >
                {items.map((item, i) => (
                    <li
                        key={i}
                        style={{
                            position: 'relative',
                            paddingLeft: 14,
                            paddingBottom: i === items.length - 1 ? 0 : 10,
                        }}
                    >
                        <span
                            style={{
                                position: 'absolute',
                                left: -5,
                                top: 4,
                                width: 10,
                                height: 10,
                                borderRadius: 999,
                                background: '#ef233c',
                                boxShadow: '0 0 0 2px rgba(239,35,60,0.2)',
                            }}
                        />
                        <div style={{ fontSize: 11, color: '#778' }}>{item.time}</div>
                        <div style={{ color: '#e0e8f0', fontSize: 13 }}>{item.title}</div>
                        {item.detail && (
                            <div style={{ color: '#aab', fontSize: 12, marginTop: 2 }}>
                                {item.detail}
                            </div>
                        )}
                    </li>
                ))}
            </ul>
        </div>
    );
};

export default TimelineBlock;
