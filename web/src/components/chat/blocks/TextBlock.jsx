import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import InlineCodeBlock from './CodeBlock.jsx';

/**
 * TextBlock — Markdown-formatted assistant text. Reuses the existing
 * Gazer Markdown palette (Red Noir accents).
 *
 * Inline fenced code blocks that survive MessageParser (i.e. unknown
 * fence languages) are rendered via the shared InlineCodeBlock with
 * a copy button.
 */

const markdownComponents = {
    code({ className, children, ...props }) {
        const isBlock = className || String(children).includes('\n');
        if (isBlock) {
            const lang = (className || '').replace('language-', '');
            return <InlineCodeBlock lang={lang} code={String(children)} />;
        }
        return (
            <code
                style={{
                    background: 'rgba(255,255,255,0.08)',
                    padding: '2px 6px',
                    borderRadius: 4,
                    fontSize: '0.9em',
                }}
                {...props}
            >
                {children}
            </code>
        );
    },
    a({ href, children }) {
        return (
            <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: '#ef233c' }}
            >
                {children}
            </a>
        );
    },
    p({ children }) {
        return (
            <p
                style={{
                    margin: '6px 0',
                    lineHeight: 1.6,
                    overflowWrap: 'anywhere',
                    wordBreak: 'break-word',
                }}
            >
                {children}
            </p>
        );
    },
    ul({ children }) {
        return <ul style={{ margin: '6px 0', paddingLeft: 20 }}>{children}</ul>;
    },
    ol({ children }) {
        return <ol style={{ margin: '6px 0', paddingLeft: 20 }}>{children}</ol>;
    },
};

const TextBlock = ({ markdown }) => (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {markdown || ''}
    </ReactMarkdown>
);

export default TextBlock;
