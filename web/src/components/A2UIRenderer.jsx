import React, { useMemo, useState } from 'react';

const cardStyle = {
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 10,
    padding: 12,
    background: 'rgba(255,255,255,0.03)',
};

const readPath = (model, pathExpr) => {
    if (!pathExpr || typeof pathExpr !== 'string') return pathExpr;
    const raw = pathExpr.trim();
    if (!raw.startsWith('$')) return pathExpr;
    const path = raw.replace(/^\$\./, '').replace(/^\$/, '');
    if (!path) return model;
    const parts = path.split('.').filter(Boolean);
    let cursor = model;
    for (const p of parts) {
        if (cursor && typeof cursor === 'object' && p in cursor) {
            cursor = cursor[p];
        } else {
            return undefined;
        }
    }
    return cursor;
};

const normalizeCategory = (category) => String(category || '').trim().toLowerCase();

const childIdFromRef = (item) => {
    if (typeof item === 'string') return item;
    if (!item || typeof item !== 'object') return '';
    if (typeof item.componentId === 'string') return item.componentId;
    return '';
};

const extractChildIds = (component) => {
    if (!component || typeof component !== 'object') return [];
    const props = component.properties && typeof component.properties === 'object'
        ? component.properties
        : {};
    const candidates = [props.children];
    for (const candidate of candidates) {
        if (Array.isArray(candidate)) {
            return candidate.map(childIdFromRef).filter(Boolean);
        }
        const single = childIdFromRef(candidate);
        if (single) return [single];
    }
    return [];
};

const A2UIRenderer = ({ snapshot, onUserAction }) => {
    const [localInputs, setLocalInputs] = useState({});

    const components = useMemo(
        () => (snapshot && snapshot.components && typeof snapshot.components === 'object'
            ? snapshot.components
            : {}),
        [snapshot],
    );
    const dataModel = useMemo(
        () => (snapshot && snapshot.dataModel && typeof snapshot.dataModel === 'object'
            ? snapshot.dataModel
            : {}),
        [snapshot],
    );

    const emitAction = (componentId, eventType, data = {}) => {
        if (!onUserAction || !snapshot) return;
        onUserAction({
            surfaceId: snapshot.surfaceId || 'main',
            componentId,
            eventType,
            data,
        });
    };

    const renderNode = (componentId, depth = 0, seen = new Set()) => {
        if (!componentId) return null;
        if (seen.has(componentId)) {
            return (
                <div key={`${componentId}-cycle`} style={{ color: '#fca5a5', fontSize: 12 }}>
                    cycle: {componentId}
                </div>
            );
        }
        const component = components[componentId];
        if (!component) {
            return (
                <div key={`${componentId}-missing`} style={{ color: '#fca5a5', fontSize: 12 }}>
                    missing component: {componentId}
                </div>
            );
        }
        const nextSeen = new Set(seen);
        nextSeen.add(componentId);

        const props = component.properties && typeof component.properties === 'object'
            ? component.properties
            : {};
        const category = normalizeCategory(component.category);
        const children = extractChildIds(component);

        const renderChildren = () => (
            children.length > 0 ? (
                children.map((id) => renderNode(id, depth + 1, nextSeen))
            ) : null
        );

        if (category === 'text') {
            const textValue = readPath(dataModel, props.text) ?? props.text ?? '';
            return (
                <div key={componentId} style={{ fontSize: 14, color: '#e5e7eb' }}>
                    {String(textValue)}
                </div>
            );
        }

        if (category === 'button') {
            const label = readPath(dataModel, props.label) ?? props.label ?? componentId;
            return (
                <button
                    key={componentId}
                    type="button"
                    className="btn-ghost"
                    onClick={() => emitAction(componentId, 'click', { label })}
                >
                    {String(label)}
                </button>
            );
        }

        if (category === 'input') {
            const binding = String(props.bind || '').trim();
            const boundValue = binding ? readPath(dataModel, binding) : undefined;
            const currentValue = localInputs[componentId] ?? boundValue ?? props.value ?? '';
            return (
                <div key={componentId} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {props.label ? <span style={{ fontSize: 12, color: '#9ca3af' }}>{String(props.label)}</span> : null}
                    <input
                        value={String(currentValue)}
                        onChange={(event) => {
                            const value = event.target.value;
                            setLocalInputs((prev) => ({ ...prev, [componentId]: value }));
                            emitAction(componentId, 'change', { value, bind: binding });
                        }}
                        onBlur={() => emitAction(componentId, 'blur', { value: String(currentValue), bind: binding })}
                        style={{
                            borderRadius: 8,
                            border: '1px solid rgba(255,255,255,0.12)',
                            background: 'rgba(0,0,0,0.15)',
                            color: '#fff',
                            padding: '8px 10px',
                        }}
                    />
                </div>
            );
        }

        if (category === 'image') {
            const src = readPath(dataModel, props.src) ?? props.src ?? '';
            const alt = props.alt || componentId;
            if (!src) {
                return <div key={componentId} style={{ color: '#9ca3af', fontSize: 12 }}>image src missing</div>;
            }
            return (
                <img
                    key={componentId}
                    src={String(src)}
                    alt={String(alt)}
                    style={{ maxWidth: '100%', borderRadius: 8, border: '1px solid rgba(255,255,255,0.08)' }}
                />
            );
        }

        if (category === 'divider') {
            return <hr key={componentId} style={{ borderColor: 'rgba(255,255,255,0.12)' }} />;
        }

        if (category === 'row') {
            return (
                <div key={componentId} style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                    {renderChildren()}
                </div>
            );
        }

        if (category === 'column') {
            return (
                <div key={componentId} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {renderChildren()}
                </div>
            );
        }

        if (category === 'container') {
            return (
                <div key={componentId} style={cardStyle}>
                    {renderChildren()}
                </div>
            );
        }

        return (
            <div key={componentId} style={{ ...cardStyle, borderStyle: 'dashed' }}>
                <div style={{ fontSize: 11, color: '#9ca3af', marginBottom: 6 }}>
                    Unsupported component category: {component.category || 'unknown'}
                </div>
                <pre style={{ margin: 0, fontSize: 11, whiteSpace: 'pre-wrap', color: '#d1d5db' }}>
                    {JSON.stringify(component, null, 2)}
                </pre>
            </div>
        );
    };

    const rootId = String(snapshot?.root || '').trim() || Object.keys(components)[0] || '';
    if (!rootId) {
        return <div style={{ color: '#9ca3af', fontSize: 12 }}>Empty A2UI surface</div>;
    }

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {renderNode(rootId, 0, new Set())}
        </div>
    );
};

export default A2UIRenderer;
