import React, { Suspense } from 'react';

import TextBlock from './blocks/TextBlock.jsx';
import CodeBlock from './blocks/CodeBlock.jsx';
import FallbackBlock from './blocks/FallbackBlock.jsx';
import BlockErrorBoundary from './BlockErrorBoundary.jsx';
import { resolveComponent } from './registry.js';

/**
 * BlockRenderer — dispatches a single MessageBlock to the right view.
 *
 * Layer ordering (outer → inner):
 *   BlockErrorBoundary   — catches runtime errors from the inner tree
 *     Suspense           — shows a placeholder while React.lazy resolves
 *       <resolved>       — one of Text/Code/registered render component
 *
 * The renderer is intentionally pure: no state, no effects. All data
 * mutation flows through host Chat state via window events (see
 * OptionsBlock) or, in the future, an explicit callback prop.
 */

const BlockSkeleton = () => (
    <div
        style={{
            padding: 12,
            borderRadius: 8,
            background: 'rgba(255,255,255,0.04)',
            color: '#667',
            fontSize: 12,
        }}
        aria-busy="true"
    >
        Loading…
    </div>
);

const BlockRenderer = ({ block, messageId }) => {
    if (!block || typeof block !== 'object') return null;

    if (block.type === 'text') {
        return <TextBlock markdown={block.markdown} />;
    }
    if (block.type === 'code') {
        return <CodeBlock lang={block.lang} code={block.code} />;
    }
    if (block.type === 'render') {
        const Resolved = resolveComponent(block.component);
        return (
            <BlockErrorBoundary
                fallback={block.fallback}
                component={block.component}
            >
                <Suspense fallback={<BlockSkeleton />}>
                    <Resolved
                        data={block.data || {}}
                        fallback={block.fallback}
                        component={block.component}
                        messageId={messageId}
                    />
                </Suspense>
            </BlockErrorBoundary>
        );
    }
    return <FallbackBlock fallback={`Unknown block type: ${block.type}`} />;
};

export default BlockRenderer;
