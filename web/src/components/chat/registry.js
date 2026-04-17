/**
 * ComponentRegistry — maps backend component keys to React components.
 *
 * All non-trivial blocks are loaded via React.lazy so first paint of the
 * chat shell stays small. Unknown keys resolve to FallbackBlock, which
 * renders the accompanying `fallback` text so messages never disappear.
 *
 * To add a new renderable:
 *   1. Create `blocks/<Key>.jsx` exporting a default component that
 *      accepts `{ data, fallback, messageId }`.
 *   2. Register it here.
 *   3. Add the matching fence-language in
 *      `src/rendering/fence_registry.py` if LLM fences should map to it.
 */

import React from 'react';
import FallbackBlock from './blocks/FallbackBlock.jsx';

const REGISTRY = {
    OptionsBlock: React.lazy(() => import('./blocks/OptionsBlock.jsx')),
    TableBlock: React.lazy(() => import('./blocks/TableBlock.jsx')),
    ChartBlock: React.lazy(() => import('./blocks/ChartBlock.jsx')),
    MermaidBlock: React.lazy(() => import('./blocks/MermaidBlock.jsx')),
    TimelineBlock: React.lazy(() => import('./blocks/TimelineBlock.jsx')),

    // Gazer-specific cards
    WeatherCard: React.lazy(() => import('./blocks/WeatherCard.jsx')),
    AgentTaskCard: React.lazy(() => import('./blocks/AgentTaskCard.jsx')),
};

export function resolveComponent(key) {
    return REGISTRY[key] || FallbackBlock;
}

export function registerComponent(key, component) {
    if (!key || typeof key !== 'string') return;
    if (REGISTRY[key]) {
        // Keep behavior observable rather than silently overwriting.
        // eslint-disable-next-line no-console
        console.warn(`[chat/registry] "${key}" already registered, overwriting`);
    }
    REGISTRY[key] = component;
}

export function listRegisteredComponents() {
    return Object.keys(REGISTRY);
}
