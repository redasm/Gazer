import React, { useState } from 'react';
import { Copy, Check } from 'lucide-react';

/**
 * CodeBlock — fenced code display with copy-to-clipboard.
 *
 * Used both as a standalone `code` MessageBlock and as the inline code
 * renderer inside TextBlock. Never executes its contents.
 */
const CodeBlock = ({ lang, code }) => {
    const [copied, setCopied] = useState(false);
    const payload = String(code || '').replace(/\n$/, '');

    const handleCopy = () => {
        navigator.clipboard.writeText(payload);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div
            style={{
                position: 'relative',
                background: 'rgba(0,0,0,0.65)',
                borderRadius: 8,
                margin: '8px 0',
                border: '1px solid rgba(255,255,255,0.07)',
            }}
        >
            <div
                style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '6px 12px',
                    borderBottom: '1px solid rgba(255,255,255,0.08)',
                    fontSize: 11,
                    color: '#666',
                }}
            >
                <span>{lang || ''}</span>
                <button
                    onClick={handleCopy}
                    style={{
                        color: copied ? '#4ade80' : '#666',
                        padding: '2px 6px',
                        borderRadius: 4,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 4,
                        fontSize: 11,
                        background: 'none',
                        border: 'none',
                        cursor: 'pointer',
                    }}
                    aria-label="Copy code"
                >
                    {copied ? <Check size={12} /> : <Copy size={12} />}
                    {copied ? 'Copied' : 'Copy'}
                </button>
            </div>
            <pre
                style={{
                    margin: 0,
                    padding: 12,
                    fontSize: 12,
                    color: '#d4d4d8',
                    overflowX: 'auto',
                    whiteSpace: 'pre',
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                }}
            >
                <code>{payload}</code>
            </pre>
        </div>
    );
};

export default CodeBlock;
