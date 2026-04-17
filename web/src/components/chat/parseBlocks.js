/**
 * parseBlocks — port of backend MessageParser (src/rendering/parser.py).
 *
 * Splits raw assistant text + any externally-supplied render hints into
 * a list of `MessageBlock`s:
 *   - { type: 'text', markdown }
 *   - { type: 'code', lang, code }
 *   - { type: 'render', component, data, fallback }
 *
 * Rules must match the Python implementation byte-for-byte so that
 * replies rendered on either side of the wire look the same.
 */

export const MAX_BLOCKS_PER_MESSAGE = 20;
export const MAX_FALLBACK_PREVIEW_CHARS = 200;

const FENCE_COMPONENT_MAP = Object.freeze({
    chart: 'ChartBlock',
    options: 'OptionsBlock',
    table: 'TableBlock',
    timeline: 'TimelineBlock',
    mermaid: 'MermaidBlock',
});

export function resolveFenceComponent(lang) {
    if (typeof lang !== 'string') return null;
    return FENCE_COMPONENT_MAP[lang.trim().toLowerCase()] || null;
}

const FENCE_RE = /```(\w+)[ \t]*\n([\s\S]*?)```/g;

function pushText(blocks, text) {
    const trimmed = (text || '').trim();
    if (trimmed) blocks.push({ type: 'text', markdown: trimmed });
}

/**
 * @param {string} rawText
 * @param {Array<{component: string, data: object, fallback_text: string}>} renderHints
 * @returns {Array} MessageBlock[]
 */
export function parseBlocks(rawText, renderHints = []) {
    const blocks = [];
    const text = rawText || '';
    let lastEnd = 0;
    FENCE_RE.lastIndex = 0;

    let match;
    while ((match = FENCE_RE.exec(text)) !== null) {
        const [full, lang, content] = match;
        const start = match.index;

        if (start > lastEnd) pushText(blocks, text.slice(lastEnd, start));

        const component = resolveFenceComponent(lang);
        if (component) {
            try {
                const data = JSON.parse(content);
                if (data && typeof data === 'object' && !Array.isArray(data)) {
                    blocks.push({
                        type: 'render',
                        component,
                        data,
                        fallback: content.slice(0, MAX_FALLBACK_PREVIEW_CHARS),
                    });
                } else {
                    blocks.push({ type: 'code', lang, code: content });
                }
            } catch {
                blocks.push({ type: 'code', lang, code: content });
            }
        } else {
            blocks.push({ type: 'code', lang, code: content });
        }

        lastEnd = start + full.length;
    }

    if (lastEnd < text.length) pushText(blocks, text.slice(lastEnd));

    for (const hint of renderHints || []) {
        if (!hint || !hint.component) continue;
        blocks.push({
            type: 'render',
            component: hint.component,
            data: hint.data || {},
            fallback: hint.fallback_text || '',
        });
    }

    if (blocks.length > MAX_BLOCKS_PER_MESSAGE) {
        const truncated = blocks.slice(0, MAX_BLOCKS_PER_MESSAGE - 1);
        truncated.push({
            type: 'text',
            markdown: `_(已省略 ${blocks.length - truncated.length} 个渲染块)_`,
        });
        return truncated;
    }

    return blocks;
}
