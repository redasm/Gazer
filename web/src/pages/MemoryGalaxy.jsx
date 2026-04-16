import React, { useEffect, useState, useRef, useMemo, useCallback, Suspense } from 'react';
import axios from 'axios';
import { Search, X, Network, Circle } from 'lucide-react';
import API_BASE from '../config';
const MemoryGraphCanvas = React.lazy(() => import('../components/memory/MemoryGraphCanvas'));

const TYPE_COLORS = {
    root: '#ef233c',
    entity: '#f472b6',
    topic: '#60a5fa',
    event: '#fbbf24',
    daily: '#4ade80',
    emotion: '#c084fc',
};

const TYPE_LABELS = {
    root: 'Core',
    entity: 'Entity',
    topic: 'Topic',
    event: 'Event',
    daily: 'Daily',
    emotion: 'Emotion',
};

const MemoryGalaxy = ({ t }) => {
    const [graphData, setGraphData] = useState({ nodes: [], links: [] });
    const [selectedNode, setSelectedNode] = useState(null);
    const [, setHoverNode] = useState(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [highlightNodes, setHighlightNodes] = useState(new Set());
    const [highlightLinks, setHighlightLinks] = useState(new Set());
    const fgRef = useRef();

    useEffect(() => {
        axios.get(`${API_BASE}/memory/graph`)
            .then(res => {
                const data = res.data || {};
                const nodes = Array.isArray(data.nodes) ? data.nodes : [];
                const links = Array.isArray(data.links) ? data.links : [];
                const nodeById = {};
                nodes.forEach(n => {
                    n._neighbors = [];
                    n._links = [];
                    nodeById[n.id] = n;
                });
                links.forEach(link => {
                    const sid = typeof link.source === 'object' ? link.source.id : link.source;
                    const tid = typeof link.target === 'object' ? link.target.id : link.target;
                    if (nodeById[sid]) {
                        nodeById[sid]._neighbors.push(tid);
                        nodeById[sid]._links.push(link);
                    }
                    if (nodeById[tid]) {
                        nodeById[tid]._neighbors.push(sid);
                        nodeById[tid]._links.push(link);
                    }
                });
                setGraphData({ ...data, nodes, links });
            })
            .catch(err => console.error(err));
    }, []);


    const stats = useMemo(() => ({
        nodes: graphData.nodes.length,
        links: graphData.links.length,
    }), [graphData]);

    const typeStats = useMemo(() => {
        const counts = {};
        graphData.nodes.forEach(n => {
            const g = n.group || 'unknown';
            counts[g] = (counts[g] || 0) + 1;
        });
        return counts;
    }, [graphData]);

    const connectionMap = useMemo(() => {
        const map = {};
        graphData.links.forEach(link => {
            const src = typeof link.source === 'object' ? link.source.id : link.source;
            const tgt = typeof link.target === 'object' ? link.target.id : link.target;
            map[src] = (map[src] || 0) + 1;
            map[tgt] = (map[tgt] || 0) + 1;
        });
        return map;
    }, [graphData]);

    const getNeighborIds = useCallback((nodeId) => {
        const ids = new Set();
        graphData.links.forEach(link => {
            const src = typeof link.source === 'object' ? link.source.id : link.source;
            const tgt = typeof link.target === 'object' ? link.target.id : link.target;
            if (src === nodeId) ids.add(tgt);
            if (tgt === nodeId) ids.add(src);
        });
        return ids;
    }, [graphData]);

    // Hover: highlight connected nodes and links, dim the rest
    const handleNodeHover = useCallback((node) => {
        setHoverNode(node || null);
        const newHighlightNodes = new Set();
        const newHighlightLinks = new Set();
        if (node) {
            newHighlightNodes.add(node.id);
            (node._neighbors || []).forEach(id => newHighlightNodes.add(id));
            (node._links || []).forEach(link => newHighlightLinks.add(link));
        }
        setHighlightNodes(newHighlightNodes);
        setHighlightLinks(newHighlightLinks);
    }, []);

    const handleNodeClick = useCallback((node) => {
        setSelectedNode(node);
        // With orthographic camera, just center the view on the node
        fgRef.current?.cameraPosition(
            { x: node.x, y: node.y, z: node.z + 200 },
            { x: node.x, y: node.y, z: node.z },
            1500
        );
    }, []);

    const handleSearch = useCallback((query) => {
        setSearchQuery(query);
        if (!query.trim()) {
            setHoverNode(null);
            setHighlightNodes(new Set());
            setHighlightLinks(new Set());
            return;
        }
        const lower = query.toLowerCase();
        const matched = new Set();
        graphData.nodes.forEach(n => {
            if ((n.name || n.id || '').toLowerCase().includes(lower)) {
                matched.add(n.id);
            }
        });
        setHighlightNodes(matched);
        setHighlightLinks(new Set());

        if (matched.size > 0) {
            const firstId = [...matched][0];
            const firstNode = graphData.nodes.find(n => n.id === firstId);
            if (firstNode && firstNode.x != null) {
                fgRef.current?.cameraPosition(
                    { x: firstNode.x, y: firstNode.y, z: firstNode.z + 200 },
                    { x: firstNode.x, y: firstNode.y, z: firstNode.z },
                    1500
                );
            }
        }
    }, [graphData]);

    const neighborIds = useMemo(() => {
        return selectedNode ? getNeighborIds(selectedNode.id) : new Set();
    }, [selectedNode, getNeighborIds]);

    const neighborNodes = useMemo(() => {
        return graphData.nodes.filter(n => neighborIds.has(n.id));
    }, [graphData, neighborIds]);

    return (
        <div style={{ height: 'calc(100vh - 80px)', width: '100%', position: 'relative', borderRadius: '16px', overflow: 'hidden' }}>
            <Suspense fallback={<div style={{ height: '100%', width: '100%', background: 'rgba(0,0,0,0.2)' }} />}>
                <MemoryGraphCanvas
                    fgRef={fgRef}
                    graphData={graphData}
                    typeColors={TYPE_COLORS}
                    connectionMap={connectionMap}
                    highlightNodes={highlightNodes}
                    highlightLinks={highlightLinks}
                    onNodeClick={handleNodeClick}
                    onNodeHover={handleNodeHover}
                />
            </Suspense>

            {/* Top overlay */}
            <div style={{
                position: 'absolute', top: 16, left: 16,
                right: selectedNode ? 340 : 16,
                display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
                gap: '12px', pointerEvents: 'none',
            }}>
                <div>
                    <h2 style={{ margin: 0, fontSize: '16px', fontWeight: 600, color: '#fff' }}>
                        {t.memoryGalaxyTitle}
                    </h2>
                    <p style={{ margin: '2px 0 0', fontSize: '12px', color: '#667' }}>{t.memoryGalaxyDesc}</p>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', pointerEvents: 'auto' }}>
                    <div style={{ display: 'flex', gap: '6px', fontSize: '11px' }}>
                        <span style={{
                            background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(8px)',
                            padding: '4px 10px', borderRadius: '6px', color: '#aab',
                            border: '1px solid rgba(255,255,255,0.08)',
                        }}>
                            <Network size={10} style={{ marginRight: '4px', verticalAlign: 'middle' }} />
                            {stats.nodes} {t.memoryNodes || 'nodes'}
                        </span>
                        <span style={{
                            background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(8px)',
                            padding: '4px 10px', borderRadius: '6px', color: '#aab',
                            border: '1px solid rgba(255,255,255,0.08)',
                        }}>
                            {stats.links} {t.memoryLinks || 'links'}
                        </span>
                    </div>
                    <div style={{
                        display: 'flex', alignItems: 'center',
                        background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(8px)',
                        borderRadius: '8px', border: '1px solid rgba(255,255,255,0.1)',
                        padding: '0 10px',
                    }}>
                        <Search size={14} color="#667" />
                        <input
                            value={searchQuery}
                            onChange={(e) => handleSearch(e.target.value)}
                            placeholder={t.memorySearch || 'Search nodes...'}
                            style={{
                                background: 'none', border: 'none', color: '#fff',
                                padding: '6px 8px', fontSize: '12px', outline: 'none', width: '140px',
                            }}
                        />
                        {searchQuery && (
                            <X size={14} color="#667" style={{ cursor: 'pointer' }} onClick={() => handleSearch('')} />
                        )}
                    </div>
                </div>
            </div>

            {/* Legend */}
            <div style={{
                position: 'absolute', bottom: 16,
                right: selectedNode ? 340 : 16,
                background: 'rgba(0,0,0,0.5)', backdropFilter: 'blur(8px)',
                borderRadius: '10px', border: '1px solid rgba(255,255,255,0.08)',
                padding: '10px 14px',
                display: 'flex', flexDirection: 'column', gap: '4px',
                transition: 'right 0.3s ease',
            }}>
                {Object.entries(TYPE_COLORS).map(([type, color]) => (
                    <div key={type} style={{
                        display: 'flex', alignItems: 'center', gap: '8px', fontSize: '11px', color: '#aab',
                    }}>
                        <Circle size={8} fill={color} color={color} />
                        <span>{TYPE_LABELS[type]}</span>
                        <span style={{ marginLeft: 'auto', color: '#667' }}>{typeStats[type] || 0}</span>
                    </div>
                ))}
            </div>

            {/* Node detail panel */}
            <div style={{
                position: 'absolute', top: 0, right: 0, bottom: 0, width: '320px',
                background: 'rgba(10,15,30,0.9)', backdropFilter: 'blur(16px)',
                borderLeft: '1px solid rgba(255,255,255,0.08)',
                transform: selectedNode ? 'translateX(0)' : 'translateX(100%)',
                transition: 'transform 0.3s ease',
                overflowY: 'auto', padding: '20px',
                display: 'flex', flexDirection: 'column', gap: '16px',
            }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <h3 style={{ margin: 0, fontSize: '14px', color: '#fff' }}>
                        {t.memoryNodeDetail || 'Node Detail'}
                    </h3>
                    <X size={16} color="#667" style={{ cursor: 'pointer' }} onClick={() => setSelectedNode(null)} />
                </div>
                {selectedNode && (
                    <>
                        <div>
                            <div style={{ fontSize: '11px', color: '#556', marginBottom: '2px' }}>ID</div>
                            <div style={{ fontSize: '13px', color: '#ccd', wordBreak: 'break-all' }}>{selectedNode.id}</div>
                        </div>
                        <div>
                            <div style={{ fontSize: '11px', color: '#556', marginBottom: '2px' }}>{t.memoryNodeName || 'Name'}</div>
                            <div style={{ fontSize: '13px', color: '#ccd' }}>{selectedNode.name || selectedNode.id}</div>
                        </div>
                        <div>
                            <div style={{ fontSize: '11px', color: '#556', marginBottom: '2px' }}>{t.memoryNodeType || 'Type'}</div>
                            <div style={{
                                display: 'inline-flex', alignItems: 'center', gap: '6px',
                                fontSize: '12px', padding: '2px 8px', borderRadius: '4px',
                                background: 'rgba(255,255,255,0.05)',
                                color: TYPE_COLORS[selectedNode.group] || '#8b5cf6',
                            }}>
                                <Circle size={6} fill={TYPE_COLORS[selectedNode.group] || '#8b5cf6'} color={TYPE_COLORS[selectedNode.group] || '#8b5cf6'} />
                                {selectedNode.group || 'unknown'}
                            </div>
                        </div>
                        <div>
                            <div style={{ fontSize: '11px', color: '#556', marginBottom: '2px' }}>{t.memoryConnections || 'Connections'}</div>
                            <div style={{ fontSize: '12px', color: '#889' }}>{connectionMap[selectedNode.id] || 0}</div>
                        </div>
                        {selectedNode.content && (
                            <div>
                                <div style={{ fontSize: '11px', color: '#556', marginBottom: '2px' }}>{t.memoryContent || 'Content'}</div>
                                <div style={{
                                    fontSize: '12px', color: '#aab', lineHeight: 1.5,
                                    background: 'rgba(255,255,255,0.03)', padding: '8px', borderRadius: '6px',
                                    maxHeight: '200px', overflowY: 'auto',
                                }}>{selectedNode.content}</div>
                            </div>
                        )}
                        {neighborNodes.length > 0 && (
                            <div>
                                <div style={{ fontSize: '11px', color: '#556', marginBottom: '6px' }}>
                                    {t.memoryNeighbors || 'Related Nodes'} ({neighborNodes.length})
                                </div>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                                    {neighborNodes.slice(0, 20).map(n => (
                                        <div
                                            key={n.id}
                                            onClick={() => handleNodeClick(n)}
                                            style={{
                                                fontSize: '12px', color: '#889', cursor: 'pointer',
                                                padding: '4px 8px', borderRadius: '4px',
                                                background: 'rgba(255,255,255,0.03)',
                                                display: 'flex', alignItems: 'center', gap: '6px',
                                                transition: 'background 0.15s',
                                            }}
                                            onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(255,255,255,0.08)'}
                                            onMouseLeave={(e) => e.currentTarget.style.background = 'rgba(255,255,255,0.03)'}
                                        >
                                            <Circle size={6} fill={TYPE_COLORS[n.group] || '#8b5cf6'} color={TYPE_COLORS[n.group] || '#8b5cf6'} />
                                            {n.name || n.id}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </>
                )}
            </div>
        </div>
    );
};

export default MemoryGalaxy;
