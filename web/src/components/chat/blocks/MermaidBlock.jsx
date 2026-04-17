import React from 'react';

/**
 * MermaidBlock — placeholder renderer.
 *
 * Mermaid rendering requires a runtime library (mermaid@^10) that
 * isn't installed yet. Until it is, degrade gracefully to a styled
 * code block so the payload stays visible. Upgrading is a drop-in
 * replacement that parses `data.diagram` or `fallback`.
 */
const MermaidBlock = ({ data = {}, fallback }) => {
    const source = (data.diagram || fallback || '').toString();
    return (
        <div
            style={{
                padding: 12,
                borderRadius: 8,
                background: 'rgba(0,0,0,0.5)',
                border: '1px dashed rgba(255,255,255,0.15)',
                color: '#cbd',
            }}
        >
            <div style={{ fontSize: 11, color: '#778', marginBottom: 6 }}>
                mermaid (preview)
            </div>
            <pre
                style={{
                    margin: 0,
                    whiteSpace: 'pre-wrap',
                    fontSize: 12,
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                    color: '#d4d4d8',
                }}
            >
                {source}
            </pre>
        </div>
    );
};

export default MermaidBlock;
