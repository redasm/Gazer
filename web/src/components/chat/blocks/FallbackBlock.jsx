import React from 'react';

/**
 * Rendered when a `render` block references an unknown component key,
 * or when an ErrorBoundary above us catches a component crash. Always
 * shows the tool-supplied `fallback` text so the user never sees a
 * blank bubble.
 */
const FallbackBlock = ({ data, fallback, component }) => {
    const text = (fallback && String(fallback).trim())
        || (data && data.fallback_text)
        || `[Unsupported block${component ? `: ${component}` : ''}]`;

    return (
        <div
            style={{
                padding: '8px 12px',
                borderRadius: 8,
                background: 'rgba(255,255,255,0.04)',
                border: '1px dashed rgba(255,255,255,0.14)',
                color: '#cbd',
                fontSize: 13,
                lineHeight: 1.55,
                whiteSpace: 'pre-wrap',
                overflowWrap: 'anywhere',
            }}
        >
            {text}
        </div>
    );
};

export default FallbackBlock;
