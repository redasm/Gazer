import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import API_BASE from '../config';
import ConfirmModal from '../components/ConfirmModal';
import ToggleSwitch from '../components/ToggleSwitch';

const GRID = 20;
const NODE_W = 220;
const NODE_H = 168;
const NODE_TYPES = ['input', 'prompt', 'tool', 'condition', 'output'];

const snap = (value) => Math.round(value / GRID) * GRID;

const emptyGraph = (t) => ({
    id: '',
    name: t?.newWorkflowName || 'New Workflow',
    description: '',
    nodes: [
        { id: 'input_1', type: 'input', label: t?.inputNodeLabel || 'Input', position: { x: 80, y: 100 }, config: { default: '' } },
        { id: 'output_1', type: 'output', label: t?.outputNodeLabel || 'Output', position: { x: 420, y: 100 }, config: { text: '{{prev}}' } },
    ],
    edges: [{ id: 'edge_1', source: 'input_1', target: 'output_1' }],
});

const buildTemplates = (t) => [
    {
        name: t?.workflowTemplateLlmQa || 'LLM QA',
        build: () => {
            const seed = Date.now().toString(36);
            const input = `input_${seed}`;
            const prompt = `prompt_${seed}`;
            const output = `output_${seed}`;
            return {
                nodes: [
                    { id: input, type: 'input', label: t?.inputNodeLabel || 'Input', position: { x: 80, y: 120 }, config: { default: '' } },
                    { id: prompt, type: 'prompt', label: t?.askLlmNodeLabel || 'Ask LLM', position: { x: 360, y: 120 }, config: { prompt: '{{prev}}' } },
                    { id: output, type: 'output', label: t?.outputNodeLabel || 'Output', position: { x: 640, y: 120 }, config: { text: '{{prev}}' } },
                ],
                edges: [
                    { id: `e1_${seed}`, source: input, target: prompt },
                    { id: `e2_${seed}`, source: prompt, target: output },
                ],
            };
        },
    },
    {
        name: t?.workflowTemplatePromptTool || 'Prompt + Tool',
        build: () => {
            const seed = Date.now().toString(36);
            const input = `input_${seed}`;
            const prompt = `prompt_${seed}`;
            const tool = `tool_${seed}`;
            const output = `output_${seed}`;
            return {
                nodes: [
                    { id: input, type: 'input', label: t?.inputNodeLabel || 'Input', position: { x: 80, y: 260 }, config: { default: '' } },
                    { id: prompt, type: 'prompt', label: t?.promptNodeLabel || 'Prompt', position: { x: 320, y: 260 }, config: { prompt: '{{prev}}' } },
                    { id: tool, type: 'tool', label: t?.toolNodeLabelShort || 'Tool', position: { x: 560, y: 260 }, config: { tool_name: 'web_search', args: { query: '{{prev}}', count: 3 } } },
                    { id: output, type: 'output', label: t?.outputNodeLabel || 'Output', position: { x: 800, y: 260 }, config: { text: '{{prev}}' } },
                ],
                edges: [
                    { id: `e1_${seed}`, source: input, target: prompt },
                    { id: `e2_${seed}`, source: prompt, target: tool },
                    { id: `e3_${seed}`, source: tool, target: output },
                ],
            };
        },
    },
];

const defaultConfigByType = (type) => {
    switch (type) {
        case 'input':
            return { default: '' };
        case 'prompt':
            return { prompt: '{{prev}}' };
        case 'tool':
            return { tool_name: '', args: {} };
        case 'condition':
            return { operator: 'contains', value: '' };
        case 'output':
            return { text: '{{prev}}' };
        default:
            return {};
    }
};

const asJson = (obj) => {
    try {
        return JSON.stringify(obj, null, 2);
    } catch {
        return '{}';
    }
};
const cloneGraph = (graph) => JSON.parse(JSON.stringify(graph));

const edgePath = (sx, sy, tx, ty) => {
    const dx = Math.max(40, Math.abs(tx - sx) * 0.45);
    return `M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`;
};

const outPort = (n) => ({ x: (n.position?.x || 0) + NODE_W, y: (n.position?.y || 0) + 84 });
const inPort = (n) => ({ x: (n.position?.x || 0), y: (n.position?.y || 0) + 84 });
const nodeTypeLabel = (type, t) => {
    switch (type) {
        case 'input':
            return t?.nodeTypeInput || 'Input';
        case 'prompt':
            return t?.nodeTypePrompt || 'Prompt';
        case 'tool':
            return t?.nodeTypeTool || 'Tool';
        case 'condition':
            return t?.nodeTypeCondition || 'Condition';
        case 'output':
            return t?.nodeTypeOutput || 'Output';
        default:
            return type;
    }
};

const WorkflowStudio = ({ t, showNotice }) => {
    const canvasRef = useRef(null);
    const importFileRef = useRef(null);
    const flowiseImportFileRef = useRef(null);
    const dragStartRef = useRef({});
    const panDragMovedRef = useRef(false);
    const [graph, setGraph] = useState(emptyGraph(t));
    const [workflows, setWorkflows] = useState([]);
    const [selectedId, setSelectedId] = useState('');
    const [selectedNodeIds, setSelectedNodeIds] = useState([]);
    const [selectedEdgeId, setSelectedEdgeId] = useState('');
    const [runInput, setRunInput] = useState('');
    const [runResult, setRunResult] = useState(null);
    const [loading, setLoading] = useState(false);
    const [dragNodeId, setDragNodeId] = useState('');
    const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
    const [connectFrom, setConnectFrom] = useState('');
    const [connectWhen, setConnectWhen] = useState('');
    const [hoverInPortId, setHoverInPortId] = useState('');
    const [pointer, setPointer] = useState({ x: 0, y: 0 });
    const [selectionBox, setSelectionBox] = useState(null);
    const [showRawResult, setShowRawResult] = useState(false);
    const [selectedTraceIndex, setSelectedTraceIndex] = useState(-1);
    const [advancedJsonText, setAdvancedJsonText] = useState('{}');
    const [advancedJsonError, setAdvancedJsonError] = useState('');
    const [viewport, setViewport] = useState({ scale: 1, offsetX: 0, offsetY: 0 });
    const [isPanning, setIsPanning] = useState(false);
    const [panStart, setPanStart] = useState({ x: 0, y: 0, offsetX: 0, offsetY: 0, button: -1 });
    const [clipboard, setClipboard] = useState(null);
    const [pasteCount, setPasteCount] = useState(0);
    const [historyMeta, setHistoryMeta] = useState({ canUndo: false, canRedo: false });
    const [batchNodeType, setBatchNodeType] = useState('prompt');
    const [batchPrefix, setBatchPrefix] = useState('');
    const [batchSuffix, setBatchSuffix] = useState('');
    const [importPreview, setImportPreview] = useState(null);
    const [importPendingGraph, setImportPendingGraph] = useState(null);
    const [leftPanelCollapsed, setLeftPanelCollapsed] = useState(false);
    const [rightPanelCollapsed, setRightPanelCollapsed] = useState(false);
    const [hoveredNodeId, setHoveredNodeId] = useState('');
    const [canvasMenu, setCanvasMenu] = useState(null);
    const [nodePaletteOpen, setNodePaletteOpen] = useState(false);
    const [editModalOpen, setEditModalOpen] = useState(false);
    const [editWorkflowName, setEditWorkflowName] = useState('');
    const [editWorkflowDescription, setEditWorkflowDescription] = useState('');
    const [editWorkflowId, setEditWorkflowId] = useState('');
    const [viewportWidth, setViewportWidth] = useState(() => {
        if (typeof window === 'undefined') return 1440;
        return window.innerWidth || 1440;
    });
    const historyRef = useRef([cloneGraph(emptyGraph(t))]);
    const templates = useMemo(() => buildTemplates(t), [t]);
    const historyIndexRef = useRef(0);

    const nodeMap = useMemo(() => Object.fromEntries((graph.nodes || []).map((n) => [n.id, n])), [graph.nodes]);
    const traceMap = useMemo(() => {
        const out = {};
        for (const step of (runResult?.trace || [])) {
            if (step?.node_id) out[step.node_id] = step.status || 'ok';
        }
        return out;
    }, [runResult]);
    const focusedTraceStep = useMemo(() => {
        const trace = runResult?.trace || [];
        if (!trace.length) return null;
        if (selectedTraceIndex < 0 || selectedTraceIndex >= trace.length) return trace[trace.length - 1];
        return trace[selectedTraceIndex];
    }, [runResult, selectedTraceIndex]);
    const focusedTraceNodeId = focusedTraceStep?.node_id || '';
    const workflowId = graph.id || selectedId;
    const selectedNodeId = selectedNodeIds[0] || '';
    const selectedNode = graph.nodes?.find((n) => n.id === selectedNodeId) || null;
    const saveLabel = t.saveChanges || t.save || 'Save';
    const isTablet = viewportWidth <= 1280;
    const isMobile = viewportWidth <= 900;
    const shellGridColumns = isMobile
        ? 'minmax(0, 1fr)'
        : (leftPanelCollapsed ? '72px minmax(0, 1fr)' : '320px minmax(0, 1fr)');
    const workspaceColumns = (isTablet || rightPanelCollapsed)
        ? 'minmax(0, 1fr)'
        : 'minmax(0, 1fr) minmax(300px, 420px)';
    const shellMinHeight = isMobile ? 'auto' : 'calc(100vh - 210px)';
    const workspaceMinHeight = isMobile ? 'auto' : 'calc(100vh - 390px)';
    const canvasHeight = isMobile ? 420 : 'calc(100vh - 390px)';
    const recordSnapshot = (nextGraph) => {
        const stack = historyRef.current.slice(0, historyIndexRef.current + 1);
        stack.push(cloneGraph(nextGraph));
        if (stack.length > 120) stack.shift();
        historyRef.current = stack;
        historyIndexRef.current = stack.length - 1;
        setHistoryMeta({ canUndo: historyIndexRef.current > 0, canRedo: false });
    };
    const resetHistory = (baseGraph) => {
        historyRef.current = [cloneGraph(baseGraph)];
        historyIndexRef.current = 0;
        setHistoryMeta({ canUndo: false, canRedo: false });
    };
    const withGraphUpdate = (updater) => {
        setGraph((prev) => {
            const next = typeof updater === 'function' ? updater(prev) : updater;
            recordSnapshot(next);
            return next;
        });
    };
    const undoGraph = () => {
        if (historyIndexRef.current <= 0) return;
        historyIndexRef.current -= 1;
        const next = cloneGraph(historyRef.current[historyIndexRef.current]);
        setGraph(next);
        setHistoryMeta({
            canUndo: historyIndexRef.current > 0,
            canRedo: historyIndexRef.current < historyRef.current.length - 1,
        });
    };
    const redoGraph = () => {
        if (historyIndexRef.current >= historyRef.current.length - 1) return;
        historyIndexRef.current += 1;
        const next = cloneGraph(historyRef.current[historyIndexRef.current]);
        setGraph(next);
        setHistoryMeta({
            canUndo: historyIndexRef.current > 0,
            canRedo: historyIndexRef.current < historyRef.current.length - 1,
        });
    };

    const toCanvas = (event) => {
        const rect = canvasRef.current?.getBoundingClientRect();
        if (!rect) return { x: event.clientX, y: event.clientY };
        return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };
    const toWorld = (event) => {
        const p = toCanvas(event);
        return {
            x: (p.x - viewport.offsetX) / viewport.scale,
            y: (p.y - viewport.offsetY) / viewport.scale,
        };
    };
    const closeCanvasMenu = () => setCanvasMenu(null);
    const openCanvasMenu = (event, payload = {}) => {
        event.preventDefault();
        event.stopPropagation();
        const p = toCanvas(event);
        setCanvasMenu({
            x: p.x,
            y: p.y,
            targetType: payload.targetType || 'canvas',
            nodeId: payload.nodeId || '',
            edgeId: payload.edgeId || '',
        });
    };
    const zoomAt = (nextScale, anchorCanvasPoint) => {
        const clamped = Math.min(2, Math.max(0.5, nextScale));
        setViewport((prev) => {
            const ax = anchorCanvasPoint?.x ?? 0;
            const ay = anchorCanvasPoint?.y ?? 0;
            const worldX = (ax - prev.offsetX) / prev.scale;
            const worldY = (ay - prev.offsetY) / prev.scale;
            return {
                scale: clamped,
                offsetX: ax - worldX * clamped,
                offsetY: ay - worldY * clamped,
            };
        });
    };

    const loadList = async () => {
        try {
            const res = await axios.get(`${API_BASE}/workflows/graphs`);
            setWorkflows(Array.isArray(res.data?.items) ? res.data.items : []);
        } catch {
            showNotice?.(t.noticeLoadWorkflowsFailed || 'Failed to load workflows', 'error');
        }
    };

    const loadWorkflow = async (id) => {
        if (!id) {
            const nextEmpty = emptyGraph(t);
            setGraph(nextEmpty);
            resetHistory(nextEmpty);
            setSelectedId('');
            setSelectedNodeIds([]);
            setSelectedEdgeId('');
            setRunResult(null);
            setPasteCount(0);
            return nextEmpty;
        }
        setLoading(true);
        try {
            const res = await axios.get(`${API_BASE}/workflows/graphs/${encodeURIComponent(id)}`);
            const wf = res.data?.workflow;
            if (wf) {
                setGraph(wf);
                resetHistory(wf);
                setSelectedId(id);
                setSelectedNodeIds(wf.nodes?.[0]?.id ? [wf.nodes[0].id] : []);
                setSelectedEdgeId('');
                setRunResult(null);
                setPasteCount(0);
            }
            return wf || null;
        } catch {
            showNotice?.(t.noticeLoadWorkflowFailed || 'Failed to load workflow', 'error');
            return null;
        } finally {
            setLoading(false);
        }
    };

    const openWorkflowEditor = async (wf) => {
        const targetId = String(wf?.id || '').trim();
        if (!targetId) return;
        const loaded = await loadWorkflow(targetId);
        const source = loaded || wf || {};
        setEditWorkflowId(targetId);
        setEditWorkflowName(String(source.name || ''));
        setEditWorkflowDescription(String(source.description || ''));
        setEditModalOpen(true);
    };

    const applyWorkflowMetaAndSave = async () => {
        const name = String(editWorkflowName || '').trim();
        if (!name) {
            showNotice?.(t.noticeWorkflowNameRequired || 'Workflow name is required', 'error');
            return;
        }
        withGraphUpdate((prev) => ({
            ...prev,
            name,
            description: String(editWorkflowDescription || ''),
            id: prev.id || editWorkflowId || selectedId || '',
        }));
        setEditModalOpen(false);
        await saveWorkflow();
    };

    const openCurrentWorkflowEditor = () => {
        setEditWorkflowId(workflowId || '');
        setEditWorkflowName(String(graph.name || ''));
        setEditWorkflowDescription(String(graph.description || ''));
        setEditModalOpen(true);
    };

    useEffect(() => { loadList(); }, []);

    useEffect(() => {
        const onResize = () => setViewportWidth(window.innerWidth || 1440);
        window.addEventListener('resize', onResize);
        return () => window.removeEventListener('resize', onResize);
    }, []);

    useEffect(() => {
        if (viewportWidth <= 1080) {
            setLeftPanelCollapsed(true);
            setRightPanelCollapsed(true);
        }
    }, [viewportWidth]);

    useEffect(() => {
        const onWindowClick = () => {
            setCanvasMenu(null);
            setNodePaletteOpen(false);
        };
        window.addEventListener('click', onWindowClick);
        return () => window.removeEventListener('click', onWindowClick);
    }, []);
    useEffect(() => {
        if (!connectFrom) {
            setHoverInPortId('');
            setConnectWhen('');
        }
    }, [connectFrom]);
    useEffect(() => {
        if (!selectedNode) {
            setAdvancedJsonText('{}');
            setAdvancedJsonError('');
            return;
        }
        setAdvancedJsonText(asJson(selectedNode.config || {}));
        setAdvancedJsonError('');
    }, [selectedNode]);

    useEffect(() => {
        const onMove = (event) => {
            const p = toWorld(event);
            setPointer(p);
            if (isPanning) {
                if (panStart.button === 2) {
                    const moved = Math.hypot(event.clientX - panStart.x, event.clientY - panStart.y);
                    if (moved > 4) panDragMovedRef.current = true;
                }
                setViewport((prev) => ({
                    ...prev,
                    offsetX: panStart.offsetX + (event.clientX - panStart.x),
                    offsetY: panStart.offsetY + (event.clientY - panStart.y),
                }));
                return;
            }
            if (dragNodeId) {
                const dx = p.x - dragOffset.x;
                const dy = p.y - dragOffset.y;
                const draggedIds = selectedNodeIds.includes(dragNodeId) ? selectedNodeIds : [dragNodeId];
                setGraph((prev) => ({
                    ...prev,
                    nodes: (prev.nodes || []).map((node) => {
                        if (!draggedIds.includes(node.id)) return node;
                        const base = dragStartRef.current[node.id] || node.position || { x: 0, y: 0 };
                        return {
                            ...node,
                            position: {
                                x: Math.max(0, snap(base.x + dx)),
                                y: Math.max(0, snap(base.y + dy)),
                            },
                        };
                    }),
                }));
                return;
            }
            if (selectionBox?.active) {
                setSelectionBox((prev) => (prev ? { ...prev, currentX: p.x, currentY: p.y } : prev));
            }
        };

        const onUp = () => {
            if (dragNodeId) {
                recordSnapshot(graph);
            }
            if (selectionBox?.active) {
                const minX = Math.min(selectionBox.startX, selectionBox.currentX);
                const minY = Math.min(selectionBox.startY, selectionBox.currentY);
                const maxX = Math.max(selectionBox.startX, selectionBox.currentX);
                const maxY = Math.max(selectionBox.startY, selectionBox.currentY);
                const hit = (graph.nodes || [])
                    .filter((node) => {
                        const x = node.position?.x || 0;
                        const y = node.position?.y || 0;
                        return x + NODE_W >= minX && x <= maxX && y + NODE_H >= minY && y <= maxY;
                    })
                    .map((n) => n.id);
                if (hit.length) setSelectedNodeIds(hit);
            }
            setSelectionBox(null);
            setDragNodeId('');
            setIsPanning(false);
            setPanStart((prev) => ({ ...prev, button: -1 }));
        };
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
        return () => {
            window.removeEventListener('mousemove', onMove);
            window.removeEventListener('mouseup', onUp);
        };
    }, [dragNodeId, dragOffset, selectionBox, selectedNodeIds, graph, isPanning, panStart, viewport]);

    const updateNode = (id, patch) => withGraphUpdate((prev) => ({ ...prev, nodes: (prev.nodes || []).map((n) => (n.id === id ? { ...n, ...patch } : n)) }));
    const updateEdge = (id, patch) => withGraphUpdate((prev) => ({ ...prev, edges: (prev.edges || []).map((e) => (e.id === id ? { ...e, ...patch } : e)) }));
    const removeEdge = (id) => withGraphUpdate((prev) => ({ ...prev, edges: (prev.edges || []).filter((e) => e.id !== id) }));

    const addNode = (type = 'prompt') => {
        const id = `node_${Date.now().toString(36)}`;
        const resolvedType = NODE_TYPES.includes(type) ? type : 'prompt';
        withGraphUpdate((prev) => ({
            ...prev,
            nodes: [...(prev.nodes || []), {
                id,
                type: resolvedType,
                label: nodeTypeLabel(resolvedType, t),
                position: { x: 120, y: 240 },
                config: defaultConfigByType(resolvedType),
            }],
        }));
        setSelectedNodeIds([id]);
    };

    const addTemplate = (tpl) => {
        const built = tpl.build();
        withGraphUpdate((prev) => ({
            ...prev,
            nodes: [...(prev.nodes || []), ...built.nodes],
            edges: [...(prev.edges || []), ...built.edges],
        }));
        setSelectedNodeIds(built.nodes.map((n) => n.id));
    };

    const addEdge = (source, target, when = '') => {
        if (!source || !target || source === target) return;
        const exists = (graph.edges || []).some((e) => e.source === source && e.target === target);
        if (exists) return;
        const normalizedWhen = String(when || '').trim().toLowerCase();
        withGraphUpdate((prev) => ({
            ...prev,
            edges: [...(prev.edges || []), { id: `edge_${Date.now().toString(36)}`, source, target, when: normalizedWhen }],
        }));
    };

    const removeNode = (nodeId) => {
        withGraphUpdate((prev) => ({
            ...prev,
            nodes: (prev.nodes || []).filter((n) => n.id !== nodeId),
            edges: (prev.edges || []).filter((e) => e.source !== nodeId && e.target !== nodeId),
        }));
        setSelectedNodeIds((prev) => prev.filter((id) => id !== nodeId));
        if (connectFrom === nodeId) setConnectFrom('');
    };
    const removeSelectedNodes = () => {
        if (!selectedNodeIds.length) return;
        const removed = new Set(selectedNodeIds);
        withGraphUpdate((prev) => ({
            ...prev,
            nodes: (prev.nodes || []).filter((n) => !removed.has(n.id)),
            edges: (prev.edges || []).filter((e) => !removed.has(e.source) && !removed.has(e.target)),
        }));
        setSelectedNodeIds([]);
        setSelectedEdgeId('');
        setConnectFrom((prev) => (removed.has(prev) ? '' : prev));
    };
    const duplicateSelectedNodes = () => {
        if (!selectedNodeIds.length) return;
        const selectedSet = new Set(selectedNodeIds);
        const selectedNodes = (graph.nodes || []).filter((n) => selectedSet.has(n.id));
        if (!selectedNodes.length) return;
        const idMap = {};
        const createdNodes = selectedNodes.map((node) => {
            const nextId = `${node.id}_copy_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
            idMap[node.id] = nextId;
            return {
                ...node,
                id: nextId,
                label: `${node.label || node.id} Copy`,
                position: {
                    x: snap((node.position?.x || 0) + 40),
                    y: snap((node.position?.y || 0) + 40),
                },
            };
        });
        const createdEdges = (graph.edges || [])
            .filter((edge) => selectedSet.has(edge.source) && selectedSet.has(edge.target))
            .map((edge) => ({
                ...edge,
                id: `edge_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
                source: idMap[edge.source],
                target: idMap[edge.target],
            }));
        withGraphUpdate((prev) => ({
            ...prev,
            nodes: [...(prev.nodes || []), ...createdNodes],
            edges: [...(prev.edges || []), ...createdEdges],
        }));
        setSelectedNodeIds(createdNodes.map((n) => n.id));
    };
    const copySelectedNodes = () => {
        if (!selectedNodeIds.length) return;
        const selectedSet = new Set(selectedNodeIds);
        const selectedNodes = (graph.nodes || []).filter((n) => selectedSet.has(n.id));
        if (!selectedNodes.length) return;
        const selectedEdges = (graph.edges || []).filter((e) => selectedSet.has(e.source) && selectedSet.has(e.target));
        setClipboard({
            nodes: cloneGraph(selectedNodes),
            edges: cloneGraph(selectedEdges),
        });
        setPasteCount(0);
        showNotice?.(t.noticeCopiedNodes || `Copied ${selectedNodes.length} node(s)`, 'success');
    };
    const pasteClipboardNodes = () => {
        if (!clipboard?.nodes?.length) return;
        const offset = 40 * (pasteCount + 1);
        const idMap = {};
        const createdNodes = clipboard.nodes.map((node) => {
            const nextId = `${node.id}_paste_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
            idMap[node.id] = nextId;
            return {
                ...node,
                id: nextId,
                label: `${node.label || node.id} Copy`,
                position: {
                    x: snap((node.position?.x || 0) + offset),
                    y: snap((node.position?.y || 0) + offset),
                },
            };
        });
        const createdEdges = (clipboard.edges || [])
            .filter((edge) => idMap[edge.source] && idMap[edge.target])
            .map((edge) => ({
                ...edge,
                id: `edge_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
                source: idMap[edge.source],
                target: idMap[edge.target],
            }));
        withGraphUpdate((prev) => ({
            ...prev,
            nodes: [...(prev.nodes || []), ...createdNodes],
            edges: [...(prev.edges || []), ...createdEdges],
        }));
        setSelectedNodeIds(createdNodes.map((n) => n.id));
        setPasteCount((prev) => prev + 1);
    };
    const focusNode = (nodeId) => {
        if (!nodeId) return;
        setSelectedNodeIds([nodeId]);
        setSelectedEdgeId('');
        setRightPanelCollapsed(false);
    };
    const toggleNodeLocked = (nodeId) => {
        const node = nodeMap[nodeId];
        if (!node) return;
        updateNode(nodeId, { locked: node.locked !== true });
    };
    const toggleNodeEnabled = (nodeId) => {
        const node = nodeMap[nodeId];
        if (!node) return;
        updateNode(nodeId, { enabled: node.enabled === false });
    };
    const runCanvasMenuAction = (action, payload = {}) => {
        const targetNodeId = payload.nodeId || canvasMenu?.nodeId || '';
        const targetEdgeId = payload.edgeId || canvasMenu?.edgeId || '';
        if (String(action).startsWith('template:')) {
            const index = Number(String(action).split(':')[1] || -1);
            if (Number.isInteger(index) && index >= 0 && index < templates.length) {
                addTemplate(templates[index]);
            }
        }
        if (action === 'add_input') addNode('input');
        if (action === 'add_prompt') addNode('prompt');
        if (action === 'add_tool') addNode('tool');
        if (action === 'add_condition') addNode('condition');
        if (action === 'add_output') addNode('output');
        if (action === 'paste') pasteClipboardNodes();
        if (action === 'undo') undoGraph();
        if (action === 'redo') redoGraph();
        if (action === 'import_json') triggerImportWorkflow();
        if (action === 'export_json') exportWorkflowJson();
        if (action === 'import_flowise') triggerImportFlowise();
        if (action === 'export_flowise') exportFlowiseJson();
        if (action === 'remove_edge' && targetEdgeId) removeEdge(targetEdgeId);
        if (action === 'connect_from' && targetNodeId) {
            setConnectFrom(targetNodeId);
            const sourceNode = nodeMap[targetNodeId];
            setConnectWhen(sourceNode?.type === 'condition' ? 'true' : '');
        }
        if (action === 'copy_node' && targetNodeId) {
            setSelectedNodeIds([targetNodeId]);
            setTimeout(() => copySelectedNodes(), 0);
        }
        if (action === 'duplicate_node' && targetNodeId) {
            setSelectedNodeIds([targetNodeId]);
            setTimeout(() => duplicateSelectedNodes(), 0);
        }
        if (action === 'remove_node' && targetNodeId) removeNode(targetNodeId);
        if (action === 'toggle_node_lock' && targetNodeId) toggleNodeLocked(targetNodeId);
        if (action === 'toggle_node_enabled' && targetNodeId) toggleNodeEnabled(targetNodeId);
        if (action === 'inspect_node' && targetNodeId) focusNode(targetNodeId);
        closeCanvasMenu();
    };
    const alignSelectedNodes = (direction) => {
        const selected = (graph.nodes || []).filter((n) => selectedNodeIds.includes(n.id));
        if (selected.length < 2) {
            showNotice?.(t.noticeSelectAtLeastTwoNodes || 'Select at least 2 nodes', 'error');
            return;
        }
        if (direction === 'left') {
            const anchor = Math.min(...selected.map((n) => n.position?.x || 0));
            withGraphUpdate((prev) => ({
                ...prev,
                nodes: (prev.nodes || []).map((n) => selectedNodeIds.includes(n.id)
                    ? { ...n, position: { ...(n.position || {}), x: snap(anchor) } }
                    : n),
            }));
            return;
        }
        if (direction === 'top') {
            const anchor = Math.min(...selected.map((n) => n.position?.y || 0));
            withGraphUpdate((prev) => ({
                ...prev,
                nodes: (prev.nodes || []).map((n) => selectedNodeIds.includes(n.id)
                    ? { ...n, position: { ...(n.position || {}), y: snap(anchor) } }
                    : n),
            }));
        }
    };
    const distributeSelectedNodes = (axis) => {
        const selected = (graph.nodes || []).filter((n) => selectedNodeIds.includes(n.id));
        if (selected.length < 3) {
            showNotice?.(t.noticeSelectAtLeastThreeNodes || 'Select at least 3 nodes', 'error');
            return;
        }
        const sorted = [...selected].sort((a, b) => {
            if (axis === 'x') return (a.position?.x || 0) - (b.position?.x || 0);
            return (a.position?.y || 0) - (b.position?.y || 0);
        });
        const first = axis === 'x' ? (sorted[0].position?.x || 0) : (sorted[0].position?.y || 0);
        const lastIndex = sorted.length - 1;
        const last = axis === 'x' ? (sorted[lastIndex].position?.x || 0) : (sorted[lastIndex].position?.y || 0);
        const step = (last - first) / lastIndex;
        const patchMap = {};
        sorted.forEach((node, idx) => {
            if (idx === 0 || idx === lastIndex) return;
            const pos = snap(first + step * idx);
            patchMap[node.id] = axis === 'x'
                ? { ...(node.position || {}), x: pos }
                : { ...(node.position || {}), y: pos };
        });
        withGraphUpdate((prev) => ({
            ...prev,
            nodes: (prev.nodes || []).map((n) => (
                patchMap[n.id] ? { ...n, position: patchMap[n.id] } : n
            )),
        }));
    };
    const applyTypeToSelectedNodes = () => {
        if (!selectedNodeIds.length) {
            showNotice?.(t.noticeSelectNodesFirst || 'Select nodes first', 'error');
            return;
        }
        withGraphUpdate((prev) => ({
            ...prev,
            nodes: (prev.nodes || []).map((node) => (
                selectedNodeIds.includes(node.id)
                    ? { ...node, type: batchNodeType, config: defaultConfigByType(batchNodeType) }
                    : node
            )),
        }));
        showNotice?.(t.noticeBatchTypeApplied || 'Batch node type updated', 'success');
    };
    const applyLabelAffixesToSelectedNodes = () => {
        if (!selectedNodeIds.length) {
            showNotice?.(t.noticeSelectNodesFirst || 'Select nodes first', 'error');
            return;
        }
        if (!batchPrefix && !batchSuffix) {
            showNotice?.(t.noticeBatchLabelEmpty || 'Set prefix or suffix first', 'error');
            return;
        }
        withGraphUpdate((prev) => ({
            ...prev,
            nodes: (prev.nodes || []).map((node) => {
                if (!selectedNodeIds.includes(node.id)) return node;
                const baseLabel = String(node.label || node.id);
                return { ...node, label: `${batchPrefix}${baseLabel}${batchSuffix}` };
            }),
        }));
        showNotice?.(t.noticeBatchLabelApplied || 'Batch labels updated', 'success');
    };
    const clearSelectedNodeConfig = () => {
        if (!selectedNodeIds.length) {
            showNotice?.(t.noticeSelectNodesFirst || 'Select nodes first', 'error');
            return;
        }
        withGraphUpdate((prev) => ({
            ...prev,
            nodes: (prev.nodes || []).map((node) => (
                selectedNodeIds.includes(node.id)
                    ? { ...node, config: defaultConfigByType(node.type) }
                    : node
            )),
        }));
        showNotice?.(t.noticeBatchConfigReset || 'Selected node configs reset', 'success');
    };
    const setLockedForSelectedNodes = (locked) => {
        if (!selectedNodeIds.length) {
            showNotice?.(t.noticeSelectNodesFirst || 'Select nodes first', 'error');
            return;
        }
        withGraphUpdate((prev) => ({
            ...prev,
            nodes: (prev.nodes || []).map((node) => (
                selectedNodeIds.includes(node.id) ? { ...node, locked: !!locked } : node
            )),
        }));
        showNotice?.(locked ? (t.noticeNodesLocked || 'Selected nodes locked') : (t.noticeNodesUnlocked || 'Selected nodes unlocked'), 'success');
    };
    const setEnabledForSelectedNodes = (enabled) => {
        if (!selectedNodeIds.length) {
            showNotice?.(t.noticeSelectNodesFirst || 'Select nodes first', 'error');
            return;
        }
        withGraphUpdate((prev) => ({
            ...prev,
            nodes: (prev.nodes || []).map((node) => (
                selectedNodeIds.includes(node.id) ? { ...node, enabled: !!enabled } : node
            )),
        }));
        showNotice?.(enabled ? (t.noticeNodesEnabled || 'Selected nodes enabled') : (t.noticeNodesDisabled || 'Selected nodes disabled'), 'success');
    };
    const applyAdvancedJson = () => {
        if (!selectedNode) return;
        try {
            const parsed = JSON.parse(advancedJsonText || '{}');
            updateNode(selectedNode.id, { config: parsed });
            setAdvancedJsonError('');
            showNotice?.(t.noticeJsonApplied || 'Advanced JSON applied', 'success');
        } catch (err) {
            setAdvancedJsonError(err?.message || 'Invalid JSON');
            showNotice?.(t.noticeInvalidJson || 'Invalid JSON', 'error');
        }
    };
    useEffect(() => {
        const onKeyDown = (event) => {
            const key = event.key;
            const tagName = String(event.target?.tagName || '').toLowerCase();
            const typing = tagName === 'input' || tagName === 'textarea' || !!event.target?.isContentEditable;
            if (key === 'Escape' && connectFrom) {
                setConnectFrom('');
                setHoverInPortId('');
                setConnectWhen('');
                return;
            }
            if (typing) return;
            const hasPrimary = event.ctrlKey || event.metaKey;
            if (hasPrimary && key.toLowerCase() === 's') {
                event.preventDefault();
                saveWorkflow();
                return;
            }
            if (hasPrimary && key === 'Enter') {
                event.preventDefault();
                runWorkflow();
                return;
            }
            if (hasPrimary && key.toLowerCase() === 'd') {
                event.preventDefault();
                duplicateSelectedNodes();
                return;
            }
            if (hasPrimary && key.toLowerCase() === 'c') {
                event.preventDefault();
                copySelectedNodes();
                return;
            }
            if (hasPrimary && key.toLowerCase() === 'v') {
                event.preventDefault();
                pasteClipboardNodes();
                return;
            }
            if (hasPrimary && !event.shiftKey && key.toLowerCase() === 'z') {
                event.preventDefault();
                undoGraph();
                return;
            }
            if ((hasPrimary && event.shiftKey && key.toLowerCase() === 'z') || (hasPrimary && key.toLowerCase() === 'y')) {
                event.preventDefault();
                redoGraph();
                return;
            }
            if (hasPrimary && key === '0') {
                event.preventDefault();
                setViewport({ scale: 1, offsetX: 0, offsetY: 0 });
                return;
            }
            if (hasPrimary && key.toLowerCase() === 'f') {
                event.preventDefault();
                fitView();
                return;
            }
            if (event.altKey && key.toLowerCase() === 'l') {
                event.preventDefault();
                alignSelectedNodes('left');
                return;
            }
            if (event.altKey && key.toLowerCase() === 't') {
                event.preventDefault();
                alignSelectedNodes('top');
                return;
            }
            if (event.altKey && key.toLowerCase() === 'h') {
                event.preventDefault();
                distributeSelectedNodes('x');
                return;
            }
            if (event.altKey && key.toLowerCase() === 'v') {
                event.preventDefault();
                distributeSelectedNodes('y');
                return;
            }
            if (event.altKey && key.toLowerCase() === 'm') {
                event.preventDefault();
                applyTypeToSelectedNodes();
                return;
            }
            if (event.altKey && key.toLowerCase() === 'r') {
                event.preventDefault();
                clearSelectedNodeConfig();
                return;
            }
            if (event.altKey && key.toLowerCase() === 'k') {
                event.preventDefault();
                setLockedForSelectedNodes(true);
                return;
            }
            if (event.altKey && key.toLowerCase() === 'u') {
                event.preventDefault();
                setLockedForSelectedNodes(false);
                return;
            }
            if (event.altKey && key === ']') {
                event.preventDefault();
                setEnabledForSelectedNodes(true);
                return;
            }
            if (event.altKey && key === '[') {
                event.preventDefault();
                setEnabledForSelectedNodes(false);
                return;
            }
            if (key === 'Delete' || key === 'Backspace') {
                if (selectedEdgeId) {
                    removeEdge(selectedEdgeId);
                    setSelectedEdgeId('');
                    return;
                }
                if (selectedNodeIds.length) {
                    removeSelectedNodes();
                }
            }
        };
        window.addEventListener('keydown', onKeyDown);
        return () => window.removeEventListener('keydown', onKeyDown);
    }, [connectFrom, selectedEdgeId, selectedNodeIds, graph.nodes, graph.edges, viewport.scale, clipboard, pasteCount, batchNodeType, batchPrefix, batchSuffix]);

    const updateSelectedNodeConfig = (patch) => {
        if (!selectedNode) return;
        const prevCfg = (selectedNode.config && typeof selectedNode.config === 'object') ? selectedNode.config : {};
        updateNode(selectedNode.id, { config: { ...prevCfg, ...patch } });
    };
    const appendConfigToken = (field, token, fallbackValue = '') => {
        if (!selectedNode) return;
        const current = String(selectedNode.config?.[field] || fallbackValue);
        const next = current.includes(token) ? current : `${current}${current.endsWith(' ') || current.endsWith(':') ? '' : ' '}${token}`;
        updateSelectedNodeConfig({ [field]: next });
    };

    const saveWorkflow = async () => {
        if (!graph.name?.trim()) return showNotice?.(t.noticeWorkflowNameRequired || 'Workflow name is required', 'error');
        setLoading(true);
        try {
            const res = await axios.post(`${API_BASE}/workflows/graphs`, { ...graph, id: graph.id || selectedId || '' });
            const wf = res.data?.workflow;
            if (wf) {
                setGraph(wf);
                resetHistory(wf);
                setSelectedId(wf.id);
                await loadList();
            }
            showNotice?.(t.noticeWorkflowSaved || 'Workflow saved', 'success');
        } catch (err) {
            showNotice?.(err?.response?.data?.detail || t.noticeSaveWorkflowFailed || 'Failed to save workflow', 'error');
        } finally {
            setLoading(false);
        }
    };

    const deleteWorkflow = async (id) => {
        const targetId = String(id || workflowId || '').trim();
        if (!targetId) return;
        const ok = window.confirm(t.confirmDeleteWorkflow || 'Delete this workflow permanently?');
        if (!ok) return;
        setLoading(true);
        try {
            await axios.delete(`${API_BASE}/workflows/graphs/${encodeURIComponent(targetId)}`);
            if (selectedId === targetId || graph.id === targetId) {
                const nextEmpty = emptyGraph(t);
                setGraph(nextEmpty);
                resetHistory(nextEmpty);
                setSelectedId('');
                setSelectedNodeIds([]);
                setSelectedEdgeId('');
                setRunResult(null);
                setPasteCount(0);
            }
            await loadList();
            showNotice?.(t.noticeWorkflowDeleted || 'Workflow deleted', 'success');
        } catch (err) {
            showNotice?.(err?.response?.data?.detail || t.noticeDeleteWorkflowFailed || 'Failed to delete workflow', 'error');
        } finally {
            setLoading(false);
        }
    };

    const runWorkflow = async () => {
        if (!workflowId) return showNotice?.(t.noticeSaveBeforeRun || 'Please save workflow before run', 'error');
        setLoading(true);
        try {
            const res = await axios.post(`${API_BASE}/workflows/graphs/${encodeURIComponent(workflowId)}/run`, { input: runInput });
            setRunResult(res.data?.result || null);
            setSelectedTraceIndex(-1);
            showNotice?.(t.noticeWorkflowRunCompleted || 'Workflow run completed', 'success');
        } catch (err) {
            showNotice?.(err?.response?.data?.detail || t.noticeRunWorkflowFailed || 'Failed to run workflow', 'error');
        } finally {
            setLoading(false);
        }
    };
    const exportWorkflowJson = () => {
        try {
            const payload = cloneGraph(graph);
            const baseName = String(payload.name || 'workflow')
                .trim()
                .replace(/[\\/:*?"<>|]+/g, '_')
                .replace(/\s+/g, '_');
            const fileName = `${baseName || 'workflow'}.json`;
            const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
            const objectUrl = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = objectUrl;
            anchor.download = fileName;
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            URL.revokeObjectURL(objectUrl);
            showNotice?.(t.noticeWorkflowExported || 'Workflow JSON exported', 'success');
        } catch {
            showNotice?.(t.noticeWorkflowExportFailed || 'Failed to export workflow JSON', 'error');
        }
    };
    const exportFlowiseJson = async () => {
        try {
            const payload = workflowId
                ? { workflow_id: workflowId }
                : { workflow: cloneGraph(graph) };
            const res = await axios.post(`${API_BASE}/workflows/flowise/export`, payload);
            const flowise = res.data?.flowise || {};
            const unsupportedCount = Number(res.data?.unsupported_count || 0);
            const baseName = String(flowise.name || graph.name || 'workflow')
                .trim()
                .replace(/[\\/:*?"<>|]+/g, '_')
                .replace(/\s+/g, '_');
            const fileName = `${baseName || 'workflow'}.flowise.json`;
            const blob = new Blob([JSON.stringify(flowise, null, 2)], { type: 'application/json' });
            const objectUrl = URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = objectUrl;
            anchor.download = fileName;
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            URL.revokeObjectURL(objectUrl);
            if (unsupportedCount > 0) {
                showNotice?.(
                    `${t.noticeWorkflowExported || 'Workflow JSON exported'} (Flowise unsupported nodes: ${unsupportedCount})`,
                    'warning',
                );
            } else {
                showNotice?.((t.noticeWorkflowExported || 'Workflow JSON exported') + ' (Flowise)', 'success');
            }
        } catch (err) {
            showNotice?.(err?.response?.data?.detail || 'Failed to export Flowise JSON', 'error');
        }
    };
    const triggerImportWorkflow = () => {
        importFileRef.current?.click();
    };
    const triggerImportFlowise = () => {
        flowiseImportFileRef.current?.click();
    };
    const applyImportedWorkflow = (nextGraph) => {
        setSelectedId('');
        setGraph(nextGraph);
        resetHistory(nextGraph);
        setSelectedNodeIds([]);
        setSelectedEdgeId('');
        setRunResult(null);
        setSelectedTraceIndex(-1);
    };
    const confirmImportWorkflow = () => {
        if (!importPendingGraph) return;
        applyImportedWorkflow(importPendingGraph);
        setImportPreview(null);
        setImportPendingGraph(null);
        showNotice?.(t.noticeWorkflowImported || 'Workflow JSON imported. Save to persist.', 'success');
    };
    const cancelImportWorkflow = () => {
        setImportPreview(null);
        setImportPendingGraph(null);
    };
    const importWorkflowJson = async (event) => {
        const file = event?.target?.files?.[0];
        if (!file) return;
        try {
            const text = await file.text();
            const parsed = JSON.parse(text);
            const candidate = (parsed && typeof parsed === 'object' && parsed.workflow && typeof parsed.workflow === 'object')
                ? parsed.workflow
                : parsed;
            if (!candidate || typeof candidate !== 'object') {
                throw new Error('invalid payload');
            }
            if (!Array.isArray(candidate.nodes) || !Array.isArray(candidate.edges)) {
                throw new Error('nodes/edges must be arrays');
            }
            const nextGraph = {
                ...emptyGraph(t),
                ...candidate,
                id: '',
                version: 1,
                nodes: candidate.nodes,
                edges: candidate.edges,
            };
            setImportPendingGraph(nextGraph);
            setImportPreview({
                file_name: String(file.name || ''),
                name: String(nextGraph.name || ''),
                node_count: Array.isArray(nextGraph.nodes) ? nextGraph.nodes.length : 0,
                edge_count: Array.isArray(nextGraph.edges) ? nextGraph.edges.length : 0,
            });
        } catch (err) {
            showNotice?.(err?.message || t.noticeWorkflowImportFailed || 'Failed to import workflow JSON', 'error');
        } finally {
            if (event?.target) {
                event.target.value = '';
            }
        }
    };
    const importFlowiseJson = async (event) => {
        const file = event?.target?.files?.[0];
        if (!file) return;
        try {
            const text = await file.text();
            const parsed = JSON.parse(text);
            const flowisePayload = (parsed && typeof parsed === 'object' && parsed.flowise && typeof parsed.flowise === 'object')
                ? parsed.flowise
                : parsed;
            const response = await axios.post(`${API_BASE}/workflows/flowise/import`, {
                flowise: flowisePayload,
                strict: false,
                name: String(graph.name || '').trim() || undefined,
            });
            const imported = response?.data || {};
            const workflow = imported?.workflow;
            if (!workflow || typeof workflow !== 'object' || !Array.isArray(workflow.nodes) || !Array.isArray(workflow.edges)) {
                throw new Error('invalid imported workflow');
            }
            const nextGraph = {
                ...emptyGraph(t),
                ...workflow,
                id: '',
                version: 1,
                nodes: workflow.nodes,
                edges: workflow.edges,
            };
            const summary = imported?.summary || {};
            const validation = imported?.validation || {};
            setImportPendingGraph(nextGraph);
            setImportPreview({
                file_name: String(file.name || ''),
                name: String(nextGraph.name || ''),
                node_count: Array.isArray(nextGraph.nodes) ? nextGraph.nodes.length : 0,
                edge_count: Array.isArray(nextGraph.edges) ? nextGraph.edges.length : 0,
                import_mode: 'flowise',
                error_count: Number(imported?.error_count || 0),
                summary,
                validation,
            });
            const errorCount = Number(imported?.error_count || 0);
            if (errorCount > 0) {
                const levelNode = Number(summary?.node || 0);
                const levelEdge = Number(summary?.edge || 0);
                showNotice?.(
                    `Flowise import warnings: total=${errorCount}, node=${levelNode}, edge=${levelEdge}`,
                    'warning',
                );
            }
        } catch (err) {
            const detail = err?.response?.data?.detail;
            if (detail && typeof detail === 'object') {
                const summary = detail.summary || {};
                const validation = detail.validation || {};
                const msg = detail.message || err?.message || 'Failed to import Flowise JSON';
                const suffix = ` (node=${summary.node || 0}, edge=${summary.edge || 0}, code=${validation.code || 'n/a'})`;
                showNotice?.(`${msg}${suffix}`, 'error');
            } else {
                showNotice?.(err?.message || 'Failed to import Flowise JSON', 'error');
            }
        } finally {
            if (event?.target) {
                event.target.value = '';
            }
        }
    };
    const fitView = () => {
        const rect = canvasRef.current?.getBoundingClientRect();
        const nodes = graph.nodes || [];
        if (!rect || !nodes.length) {
            setViewport({ scale: 1, offsetX: 0, offsetY: 0 });
            return;
        }
        const margin = 28;
        let minX = Number.POSITIVE_INFINITY;
        let minY = Number.POSITIVE_INFINITY;
        let maxX = Number.NEGATIVE_INFINITY;
        let maxY = Number.NEGATIVE_INFINITY;
        for (const node of nodes) {
            const x = node.position?.x || 0;
            const y = node.position?.y || 0;
            minX = Math.min(minX, x);
            minY = Math.min(minY, y);
            maxX = Math.max(maxX, x + NODE_W);
            maxY = Math.max(maxY, y + NODE_H);
        }
        const contentW = Math.max(1, maxX - minX);
        const contentH = Math.max(1, maxY - minY);
        const scale = Math.min(
            2,
            Math.max(
                0.5,
                Math.min(
                    (rect.width - margin * 2) / contentW,
                    (rect.height - margin * 2) / contentH,
                ),
            ),
        );
        const offsetX = rect.width / 2 - (minX + contentW / 2) * scale;
        const offsetY = rect.height / 2 - (minY + contentH / 2) * scale;
        setViewport({ scale, offsetX, offsetY });
    };

    const renderSelectionRect = () => {
        if (!selectionBox?.active) return null;
        const x = Math.min(selectionBox.startX, selectionBox.currentX);
        const y = Math.min(selectionBox.startY, selectionBox.currentY);
        const w = Math.abs(selectionBox.currentX - selectionBox.startX);
        const h = Math.abs(selectionBox.currentY - selectionBox.startY);
        return <div style={{ position: 'absolute', left: x, top: y, width: w, height: h, border: '1px dashed rgba(96,165,250,0.9)', background: 'rgba(59,130,246,0.12)' }} />;
    };

    return (
        <div className="workflow-page">
            <style>{`
                .workflow-canvas-controls {
                    position: absolute;
                    left: 12px;
                    bottom: 12px;
                    z-index: 20;
                    display: inline-flex;
                    gap: 6px;
                    padding: 6px;
                    border: 1px solid rgba(148, 163, 184, 0.35);
                    border-radius: 10px;
                    background: rgba(15, 23, 42, 0.88);
                    backdrop-filter: blur(8px);
                }
                .workflow-canvas-controls button {
                    min-width: 34px;
                    height: 34px;
                    border: 1px solid rgba(148, 163, 184, 0.4);
                    border-radius: 8px;
                    background: rgba(30, 41, 59, 0.85);
                    color: #e2e8f0;
                    font-size: 16px;
                    font-weight: 700;
                    line-height: 1;
                    cursor: pointer;
                }
                .workflow-canvas-controls button:hover {
                    border-color: rgba(125, 211, 252, 0.7);
                    background: rgba(51, 65, 85, 0.96);
                }
                .workflow-context-menu {
                    position: absolute;
                    z-index: 30;
                    min-width: 178px;
                    max-width: 220px;
                    border: 1px solid rgba(148, 163, 184, 0.35);
                    border-radius: 10px;
                    background: rgba(15, 23, 42, 0.95);
                    box-shadow: 0 12px 30px rgba(2, 6, 23, 0.45);
                    padding: 6px;
                    display: grid;
                    gap: 4px;
                }
                .workflow-context-menu button {
                    width: 100%;
                    text-align: left;
                    border: 1px solid transparent;
                    border-radius: 8px;
                    background: transparent;
                    color: #e2e8f0;
                    padding: 6px 9px;
                    font-size: 13px;
                    cursor: pointer;
                }
                .workflow-context-menu button:hover:not(:disabled) {
                    background: rgba(51, 65, 85, 0.86);
                    border-color: rgba(125, 211, 252, 0.5);
                }
                .workflow-context-menu button:disabled {
                    opacity: 0.45;
                    cursor: not-allowed;
                }
                .workflow-node-palette-trigger {
                    position: absolute;
                    top: 12px;
                    left: 12px;
                    z-index: 26;
                    width: 44px;
                    height: 44px;
                    border: 1px solid rgba(56, 189, 248, 0.72);
                    border-radius: 50%;
                    background: rgba(37, 99, 235, 0.92);
                    color: #fff;
                    font-size: 26px;
                    line-height: 1;
                    cursor: pointer;
                    box-shadow: 0 10px 22px rgba(2, 6, 23, 0.45);
                }
                .workflow-node-palette-panel {
                    position: absolute;
                    top: 62px;
                    left: 12px;
                    z-index: 26;
                    min-width: 220px;
                    border: 1px solid rgba(148, 163, 184, 0.35);
                    border-radius: 10px;
                    background: rgba(15, 23, 42, 0.96);
                    box-shadow: 0 14px 30px rgba(2, 6, 23, 0.5);
                    padding: 8px;
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 6px;
                }
                .workflow-node-palette-panel button {
                    border: 1px solid rgba(148, 163, 184, 0.35);
                    border-radius: 8px;
                    background: rgba(30, 41, 59, 0.9);
                    color: #e2e8f0;
                    padding: 7px 8px;
                    text-align: left;
                    cursor: pointer;
                }
                .workflow-node-palette-panel button:hover {
                    border-color: rgba(125, 211, 252, 0.7);
                    background: rgba(51, 65, 85, 0.96);
                }
                .workflow-node-actions {
                    position: absolute;
                    right: -42px;
                    top: 10px;
                    z-index: 12;
                    display: flex;
                    flex-direction: column;
                    gap: 6px;
                    padding: 6px;
                    border: 1px solid rgba(148, 163, 184, 0.45);
                    border-radius: 9px;
                    background: rgba(15, 23, 42, 0.92);
                    box-shadow: 0 8px 18px rgba(2, 6, 23, 0.4);
                }
                .workflow-node-actions button {
                    width: 26px;
                    height: 26px;
                    border: 1px solid rgba(148, 163, 184, 0.45);
                    border-radius: 6px;
                    background: rgba(30, 41, 59, 0.95);
                    color: #e2e8f0;
                    cursor: pointer;
                    padding: 0;
                    line-height: 1;
                }
                .workflow-node-actions button:hover {
                    border-color: rgba(125, 211, 252, 0.7);
                    background: rgba(51, 65, 85, 0.96);
                }
                .workflow-page {
                    display: flex;
                    flex-direction: column;
                    gap: 12px;
                }
                .workflow-header {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    gap: 12px;
                    flex-wrap: wrap;
                }
                .workflow-header-meta {
                    display: grid;
                    gap: 4px;
                }
                .workflow-header-meta p {
                    margin: 0;
                    font-size: 13px;
                    color: #8fa7bd;
                }
                .workflow-badges {
                    display: inline-flex;
                    gap: 8px;
                    flex-wrap: wrap;
                }
                .workflow-badge {
                    display: inline-flex;
                    align-items: center;
                    gap: 6px;
                    padding: 4px 10px;
                    border-radius: 999px;
                    border: 1px solid rgba(148, 163, 184, 0.34);
                    background: rgba(15, 23, 42, 0.5);
                    font-size: 12px;
                    color: #dbeafe;
                }
                .workflow-shell {
                    display: grid;
                    gap: 12px;
                    min-height: calc(100vh - 210px);
                    border: 1px solid rgba(148, 163, 184, 0.22);
                    border-radius: 14px;
                    background:
                        radial-gradient(circle at 12% 0%, rgba(56, 189, 248, 0.08), transparent 42%),
                        radial-gradient(circle at 90% 100%, rgba(56, 189, 248, 0.06), transparent 46%),
                        rgba(2, 6, 23, 0.55);
                    box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);
                }
                .workflow-sidebar {
                    min-width: 0;
                    border-right: 1px solid rgba(148, 163, 184, 0.2);
                    padding-right: 6px;
                }
                .workflow-list {
                    max-height: 340px;
                    overflow-y: auto;
                    border: 1px solid rgba(148, 163, 184, 0.24);
                    border-radius: 10px;
                    background: rgba(15, 23, 42, 0.38);
                }
                .workflow-list-empty {
                    padding: 16px;
                    text-align: center;
                    color: #94a3b8;
                    font-size: 13px;
                }
                .workflow-list-item {
                    display: grid;
                    grid-template-columns: 1fr auto;
                    border-bottom: 1px solid rgba(148, 163, 184, 0.2);
                }
                .workflow-list-item:last-child {
                    border-bottom: none;
                }
                .workflow-list-item-btn {
                    width: 100%;
                    text-align: left;
                    border: none;
                    padding: 12px 12px;
                    background: transparent;
                    color: #e6edf3;
                }
                .workflow-list-item-btn.is-selected {
                    background: linear-gradient(90deg, rgba(59,130,246,0.25), rgba(14,165,233,0.12));
                }
                .workflow-toolbar {
                    display: flex;
                    justify-content: space-between;
                    gap: 10px;
                    flex-wrap: wrap;
                    align-items: center;
                    border: 1px solid rgba(148, 163, 184, 0.22);
                    border-radius: 10px;
                    padding: 8px 10px;
                    background: rgba(15, 23, 42, 0.5);
                }
                .workflow-toolbar-left,
                .workflow-toolbar-right {
                    display: flex;
                    gap: 8px;
                    align-items: center;
                    flex-wrap: wrap;
                }
                .workflow-toolbar-right .btn-secondary,
                .workflow-toolbar-right .btn-primary {
                    min-height: 34px;
                }
                .workflow-action-btn {
                    border: 1px solid rgba(125, 211, 252, 0.36);
                    background: linear-gradient(180deg, rgba(15, 23, 42, 0.95), rgba(30, 41, 59, 0.92));
                    color: #e6edf3;
                    border-radius: 10px;
                    padding: 8px 12px;
                    min-height: 36px;
                    font-size: 14px;
                    line-height: 1;
                    font-weight: 600;
                    cursor: pointer;
                    transition: all 0.18s ease;
                    box-shadow: inset 0 1px 0 rgba(255,255,255,0.08);
                }
                .workflow-action-btn:hover:not(:disabled) {
                    border-color: rgba(56, 189, 248, 0.82);
                    background: linear-gradient(180deg, rgba(30, 41, 59, 0.96), rgba(51, 65, 85, 0.92));
                    transform: translateY(-1px);
                }
                .workflow-action-btn:disabled {
                    opacity: 0.42;
                    cursor: not-allowed;
                }
                .workflow-action-btn--primary {
                    border-color: rgba(34, 211, 238, 0.55);
                    background: linear-gradient(180deg, rgba(12, 74, 110, 0.96), rgba(14, 116, 144, 0.92));
                    color: #ecfeff;
                }
                .workflow-action-btn--primary:hover:not(:disabled) {
                    border-color: rgba(103, 232, 249, 0.9);
                    background: linear-gradient(180deg, rgba(8, 145, 178, 0.96), rgba(6, 182, 212, 0.9));
                }
                .workflow-action-btn--danger {
                    border-color: rgba(248, 113, 113, 0.48);
                    background: linear-gradient(180deg, rgba(69, 10, 10, 0.96), rgba(127, 29, 29, 0.9));
                    color: #fee2e2;
                }
                .workflow-action-btn--danger:hover:not(:disabled) {
                    border-color: rgba(252, 165, 165, 0.9);
                    background: linear-gradient(180deg, rgba(153, 27, 27, 0.95), rgba(220, 38, 38, 0.92));
                }
                .workflow-action-btn--sm {
                    min-height: 30px;
                    padding: 6px 10px;
                    font-size: 13px;
                    border-radius: 8px;
                }
                .workflow-editor-grid {
                    display: grid;
                    gap: 10px;
                }
                @media (max-width: 1280px) {
                    .workflow-shell {
                        min-height: auto;
                    }
                }
                @media (max-width: 900px) {
                    .workflow-canvas-controls {
                        left: 8px;
                        bottom: 8px;
                    }
                    .workflow-node-palette-trigger {
                        top: 8px;
                        left: 8px;
                        width: 40px;
                        height: 40px;
                    }
                    .workflow-node-palette-panel {
                        top: 56px;
                        left: 8px;
                        min-width: 200px;
                    }
                    .workflow-sidebar {
                        border-right: none;
                        border-bottom: 1px solid rgba(148, 163, 184, 0.2);
                        padding-right: 0;
                        padding-bottom: 8px;
                    }
                }
            `}</style>
            <div className="page-header workflow-header" style={{ marginBottom: 0 }}>
                <div className="workflow-header-meta">
                    <h1>{t.workflowBuilder || 'Workflow Builder'}</h1>
                    <p>{t.workflowBuilderDesc || 'Flowise-inspired visual workflow studio with drag, connect, and debug.'}</p>
                </div>
                <div className="workflow-badges">
                    <span className="workflow-badge">
                        {t.workflowName || 'Workflow'}: <strong>{graph.name || '-'}</strong>
                    </span>
                    <span className="workflow-badge">
                        {t.nodes || 'nodes'}: <strong>{(graph.nodes || []).length}</strong>
                    </span>
                    <span className="workflow-badge">
                        {t.edges || 'edges'}: <strong>{(graph.edges || []).length}</strong>
                    </span>
                </div>
            </div>

            <div className="card workflow-shell" style={{ gridTemplateColumns: shellGridColumns, minHeight: shellMinHeight }}>
                <div className="workflow-sidebar">
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, alignItems: 'center' }}>
                        {!leftPanelCollapsed && <strong>{t.savedWorkflows || 'Saved Workflows'}</strong>}
                        <button className="workflow-action-btn workflow-action-btn--sm" onClick={() => setLeftPanelCollapsed((prev) => !prev)}>
                            {leftPanelCollapsed ? (t.expand || 'Expand') : (t.collapse || 'Collapse')}
                        </button>
                    </div>
                    {!leftPanelCollapsed && (
                        <>
                            <div className="workflow-list" style={{ marginBottom: 10 }}>
                                {(workflows || []).map((wf) => (
                                    <div key={wf.id} className="workflow-list-item">
                                        <button
                                            type="button"
                                            onClick={() => openWorkflowEditor(wf)}
                                            className={`workflow-list-item-btn ${selectedId === wf.id ? 'is-selected' : ''}`}
                                        >
                                            <div style={{ fontWeight: 600 }}>{wf.name}</div>
                                            <div style={{ fontSize: 12, opacity: 0.75 }}>
                                                {wf.node_count} {t.nodes || 'nodes'} / {wf.edge_count} {t.edges || 'edges'}
                                            </div>
                                        </button>
                                        <div style={{ display: 'flex', gap: 6, margin: 8 }}>
                                            <button
                                                type="button"
                                                className="workflow-action-btn workflow-action-btn--primary workflow-action-btn--sm"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    if ((graph.id || selectedId) !== wf.id) return;
                                                    saveWorkflow();
                                                }}
                                                disabled={(graph.id || selectedId) !== wf.id || loading}
                                                title={saveLabel}
                                            >
                                                {saveLabel}
                                            </button>
                                            <button
                                                type="button"
                                                className="workflow-action-btn workflow-action-btn--danger workflow-action-btn--sm"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    deleteWorkflow(wf.id);
                                                }}
                                                title={t.deleteWorkflow || 'Delete Workflow'}
                                            >
                                                {t.delete || 'Delete'}
                                            </button>
                                        </div>
                                    </div>
                                ))}
                                {(workflows || []).length === 0 && (
                                    <div className="workflow-list-empty">
                                        {t.noData || 'No data'}
                                    </div>
                                )}
                            </div>
                        </>
                    )}
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 0 }}>
                    <input
                        ref={importFileRef}
                        type="file"
                        accept="application/json,.json"
                        style={{ display: 'none' }}
                        onChange={importWorkflowJson}
                    />
                    <input
                        ref={flowiseImportFileRef}
                        type="file"
                        accept="application/json,.json"
                        style={{ display: 'none' }}
                        onChange={importFlowiseJson}
                    />
                    <div className="workflow-toolbar">
                        <div className="workflow-toolbar-left">
                            <button className="workflow-action-btn" type="button" onClick={openCurrentWorkflowEditor}>
                                {t.editWorkflow || 'Edit Workflow'}
                            </button>
                            <button className="workflow-action-btn" type="button" onClick={triggerImportWorkflow}>
                                {t.importJson || 'Import JSON'}
                            </button>
                            <button className="workflow-action-btn" type="button" onClick={exportWorkflowJson}>
                                {t.exportJson || 'Export JSON'}
                            </button>
                            <button className="workflow-action-btn" type="button" onClick={triggerImportFlowise}>
                                {t.importFlowiseJson || 'Import Flowise'}
                            </button>
                            <button className="workflow-action-btn" type="button" onClick={exportFlowiseJson}>
                                {t.exportFlowiseJson || 'Export Flowise'}
                            </button>
                            <button className="workflow-action-btn" type="button" onClick={() => setRightPanelCollapsed((prev) => !prev)}>
                                {rightPanelCollapsed ? (t.expand || 'Expand') : (t.collapse || 'Collapse')} {t.nodeEditor || 'Node Editor'}
                            </button>
                        </div>
                        <div className="workflow-toolbar-right">
                            <button className="workflow-action-btn workflow-action-btn--primary" type="button" onClick={saveWorkflow} disabled={loading}>
                                {saveLabel}
                            </button>
                            <button className="workflow-action-btn workflow-action-btn--primary" type="button" onClick={runWorkflow} disabled={loading || !workflowId}>
                                {t.runWorkflow || 'Run Workflow'}
                            </button>
                        </div>
                    </div>
                    {!!connectFrom && (
                        <div style={{ fontSize: 12, color: '#93c5fd', marginBottom: 4 }}>
                            {t.connectingFrom || 'Connecting from'}: {connectFrom}
                        </div>
                    )}
                    <div className="workflow-editor-grid" style={{ gridTemplateColumns: workspaceColumns, minHeight: workspaceMinHeight }}>
                    <div
                        ref={canvasRef}
                        style={{
                            position: 'relative',
                            height: canvasHeight,
                            minHeight: isMobile ? 420 : 440,
                            border: '1px solid rgba(255,255,255,0.08)',
                            borderRadius: 10,
                            backgroundColor: 'rgba(10,16,30,0.6)',
                            backgroundImage: 'linear-gradient(rgba(255,255,255,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.05) 1px, transparent 1px)',
                            backgroundSize: `${GRID}px ${GRID}px`,
                            overflow: 'hidden',
                            cursor: isPanning ? 'grabbing' : 'default',
                        }}
                        onMouseDown={(e) => {
                            closeCanvasMenu();
                            if (e.button === 1 || e.button === 2 || e.altKey) {
                                e.preventDefault();
                                panDragMovedRef.current = false;
                                setIsPanning(true);
                                setPanStart({ x: e.clientX, y: e.clientY, offsetX: viewport.offsetX, offsetY: viewport.offsetY, button: e.button });
                                return;
                            }
                            const p = toWorld(e);
                            setSelectedNodeIds([]);
                            setSelectionBox({ active: true, startX: p.x, startY: p.y, currentX: p.x, currentY: p.y });
                        }}
                        onMouseMove={(e) => setPointer(toWorld(e))}
                        onContextMenu={(e) => {
                            if (panDragMovedRef.current) {
                                e.preventDefault();
                                panDragMovedRef.current = false;
                                return;
                            }
                            openCanvasMenu(e, { targetType: 'canvas' });
                        }}
                        onWheel={(e) => {
                            const anchor = toCanvas(e);
                            const nextScale = viewport.scale + (e.deltaY < 0 ? 0.08 : -0.08);
                            zoomAt(nextScale, anchor);
                        }}
                    >
                        <div
                            style={{
                                position: 'absolute',
                                inset: 0,
                                transform: `translate(${viewport.offsetX}px, ${viewport.offsetY}px) scale(${viewport.scale})`,
                                transformOrigin: '0 0',
                            }}
                        >
                        <svg width="100%" height="100%" style={{ position: 'absolute', inset: 0 }}>
                            {(graph.edges || []).map((edge) => {
                                const sourceNode = nodeMap[edge.source];
                                const targetNode = nodeMap[edge.target];
                                if (!sourceNode || !targetNode) return null;
                                const p1 = outPort(sourceNode);
                                const p2 = inPort(targetNode);
                                const active = selectedEdgeId === edge.id;
                                const edgeDisabled = sourceNode.enabled === false || targetNode.enabled === false;
                                const whenLabel = String(edge.when || '').trim().toLowerCase();
                                return (
                                    <g key={edge.id}>
                                        <path
                                            d={edgePath(p1.x, p1.y, p2.x, p2.y)}
                                            stroke={active ? 'rgba(250,204,21,0.95)' : (edgeDisabled ? 'rgba(148,163,184,0.75)' : 'rgba(147,197,253,0.8)')}
                                            strokeWidth={active ? 3 : 2}
                                            fill="none"
                                            style={{ cursor: 'pointer' }}
                                            onMouseDown={(e) => e.stopPropagation()}
                                            onClick={(e) => { e.stopPropagation(); setSelectedEdgeId(edge.id); }}
                                            onContextMenu={(e) => openCanvasMenu(e, { targetType: 'edge', edgeId: edge.id })}
                                        />
                                        {!!whenLabel && (
                                            <text
                                                x={(p1.x + p2.x) / 2}
                                                y={(p1.y + p2.y) / 2 - 6}
                                                fill="rgba(226,232,240,0.95)"
                                                fontSize="10"
                                                textAnchor="middle"
                                                style={{ pointerEvents: 'none' }}
                                            >
                                                {whenLabel}
                                            </text>
                                        )}
                                    </g>
                                );
                            })}
                            {connectFrom && nodeMap[connectFrom] && (
                                <path
                                    d={edgePath(outPort(nodeMap[connectFrom]).x, outPort(nodeMap[connectFrom]).y, pointer.x, pointer.y)}
                                    stroke="rgba(59,130,246,0.9)"
                                    strokeWidth="2"
                                    fill="none"
                                    strokeDasharray="6 6"
                                />
                            )}
                        </svg>

                            {(graph.nodes || []).map((node) => {
                                const status = traceMap[node.id] || '';
                                const selected = selectedNodeIds.includes(node.id);
                                const nodeEnabled = node.enabled !== false;
                                const nodeLocked = node.locked === true;
                                const borderColor = focusedTraceNodeId && focusedTraceNodeId === node.id
                                    ? 'rgba(250,204,21,0.95)'
                                    : status === 'error'
                                        ? 'rgba(239,68,68,0.9)'
                                        : status === 'ok'
                                            ? 'rgba(34,197,94,0.9)'
                                            : status === 'skipped'
                                                ? 'rgba(148,163,184,0.9)'
                                            : selected
                                                ? 'rgba(96,165,250,0.9)'
                                                : 'rgba(255,255,255,0.14)';
                            return (
                                <div
                                    key={node.id}
                                    style={{
                                        position: 'absolute',
                                        left: node.position?.x || 0,
                                        top: node.position?.y || 0,
                                        width: NODE_W,
                                        minHeight: NODE_H,
                                        border: `1px solid ${borderColor}`,
                                        borderRadius: 10,
                                        padding: 8,
                                        background: 'rgba(20,28,45,0.95)',
                                        opacity: nodeEnabled ? 1 : 0.52,
                                        cursor: nodeLocked ? 'not-allowed' : 'move',
                                        userSelect: 'none',
                                    }}
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        setSelectedEdgeId('');
                                        if (e.shiftKey) {
                                            setSelectedNodeIds((prev) => (prev.includes(node.id) ? prev.filter((id) => id !== node.id) : [...prev, node.id]));
                                        } else {
                                            setSelectedNodeIds([node.id]);
                                        }
                                    }}
                                    onMouseDown={(e) => {
                                        e.stopPropagation();
                                        if (e.button !== 0) return;
                                        if (nodeLocked) return;
                                        const p = toWorld(e);
                                        const currentSelection = selectedNodeIds.includes(node.id) ? selectedNodeIds : [node.id];
                                        setSelectedNodeIds(currentSelection);
                                        setDragNodeId(node.id);
                                        setDragOffset({ x: p.x - (node.position?.x || 0), y: p.y - (node.position?.y || 0) });
                                        const map = {};
                                        for (const id of currentSelection) {
                                            const n = nodeMap[id];
                                            if (n) map[id] = { x: n.position?.x || 0, y: n.position?.y || 0 };
                                        }
                                        dragStartRef.current = map;
                                    }}
                                    onMouseEnter={() => setHoveredNodeId(node.id)}
                                    onMouseLeave={() => setHoveredNodeId((prev) => (prev === node.id ? '' : prev))}
                                    onContextMenu={(e) => openCanvasMenu(e, { targetType: 'node', nodeId: node.id })}
                                >
                                    <button
                                        type="button"
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            if (connectFrom) {
                                                if (nodeEnabled === false) return;
                                                addEdge(connectFrom, node.id, connectWhen);
                                                setConnectFrom('');
                                                setHoverInPortId('');
                                                setConnectWhen('');
                                            }
                                        }}
                                        onMouseEnter={() => {
                                            if (connectFrom && connectFrom !== node.id) setHoverInPortId(node.id);
                                        }}
                                        onMouseLeave={() => setHoverInPortId((prev) => (prev === node.id ? '' : prev))}
                                        style={{
                                            position: 'absolute',
                                            left: -6,
                                            top: 78,
                                            width: 12,
                                            height: 12,
                                            borderRadius: '50%',
                                            border: '1px solid #1d4ed8',
                                            background: hoverInPortId === node.id ? '#3b82f6' : '#60a5fa',
                                            boxShadow: hoverInPortId === node.id ? '0 0 0 4px rgba(59,130,246,0.22)' : 'none',
                                        }}
                                    />
                                    <button
                                        type="button"
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            if (nodeEnabled === false) return;
                                            setConnectFrom((prev) => {
                                                const next = prev === node.id ? '' : node.id;
                                                if (next) {
                                                    const sourceNode = nodeMap[node.id];
                                                    const suggestedWhen = sourceNode?.type === 'condition' ? 'true' : '';
                                                    setConnectWhen(suggestedWhen);
                                                } else {
                                                    setConnectWhen('');
                                                }
                                                return next;
                                            });
                                        }}
                                        style={{ position: 'absolute', right: -6, top: 78, width: 12, height: 12, borderRadius: '50%', border: '1px solid #14532d', background: connectFrom === node.id ? '#22c55e' : '#86efac' }}
                                    />
                                    {hoveredNodeId === node.id && (
                                        <div className="workflow-node-actions" onMouseDown={(e) => e.stopPropagation()}>
                                            <button
                                                type="button"
                                                title={t.copySelection || 'Copy'}
                                                aria-label={t.copySelection || 'Copy'}
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    setSelectedNodeIds([node.id]);
                                                    setTimeout(() => copySelectedNodes(), 0);
                                                }}
                                            >
                                                ⧉
                                            </button>
                                            <button
                                                type="button"
                                                title={t.remove || 'Remove'}
                                                aria-label={t.remove || 'Remove'}
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    removeNode(node.id);
                                                }}
                                            >
                                                🗑
                                            </button>
                                            <button
                                                type="button"
                                                title={t.nodeEditor || 'Node Editor'}
                                                aria-label={t.nodeEditor || 'Node Editor'}
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    setSelectedNodeIds([node.id]);
                                                    setSelectedEdgeId('');
                                                    setRightPanelCollapsed(false);
                                                }}
                                            >
                                                i
                                            </button>
                                        </div>
                                    )}

                                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                                        <strong style={{ fontSize: 13 }}>{node.label || node.id}</strong>
                                        <span style={{ fontSize: 11, opacity: 0.7 }}>{node.type}{nodeLocked ? ' · 🔒' : ''}{nodeEnabled ? '' : ' · ⏸'}</span>
                                    </div>
                                    <div style={{ fontSize: 11, opacity: 0.75, marginBottom: 6 }}>{node.id}</div>
                                    <pre style={{ margin: 0, fontSize: 11, maxHeight: 66, overflow: 'auto', background: 'rgba(255,255,255,0.04)', borderRadius: 6, padding: 6 }}>
                                        {asJson(node.config || {})}
                                    </pre>
                                </div>
                            );
                        })}
                        {renderSelectionRect()}
                        </div>
                        <button
                            type="button"
                            className="workflow-node-palette-trigger"
                            title={t.addNode || 'Add Node'}
                            aria-label={t.addNode || 'Add Node'}
                            onClick={(e) => {
                                e.stopPropagation();
                                setNodePaletteOpen((prev) => !prev);
                            }}
                        >
                            +
                        </button>
                        {nodePaletteOpen && (
                            <div className="workflow-node-palette-panel" onClick={(e) => e.stopPropagation()}>
                                {NODE_TYPES.map((type) => (
                                    <button
                                        key={type}
                                        type="button"
                                        onClick={() => {
                                            addNode(type);
                                            setNodePaletteOpen(false);
                                        }}
                                    >
                                        + {nodeTypeLabel(type, t)}
                                    </button>
                                ))}
                            </div>
                        )}
                        {canvasMenu && (
                            <div
                                className="workflow-context-menu"
                                style={{ left: canvasMenu.x, top: canvasMenu.y }}
                                onClick={(e) => e.stopPropagation()}
                            >
                                {canvasMenu.targetType === 'canvas' && (
                                    <>
                                        <button type="button" onClick={() => runCanvasMenuAction('add_input')}>+ {t.nodeTypeInput || 'Input'}</button>
                                        <button type="button" onClick={() => runCanvasMenuAction('add_prompt')}>+ {t.nodeTypePrompt || 'Prompt'}</button>
                                        <button type="button" onClick={() => runCanvasMenuAction('add_tool')}>+ {t.nodeTypeTool || 'Tool'}</button>
                                        <button type="button" onClick={() => runCanvasMenuAction('add_condition')}>+ {t.nodeTypeCondition || 'Condition'}</button>
                                        <button type="button" onClick={() => runCanvasMenuAction('add_output')}>+ {t.nodeTypeOutput || 'Output'}</button>
                                        {templates.map((tpl, idx) => (
                                            <button key={tpl.name} type="button" onClick={() => runCanvasMenuAction(`template:${idx}`)}>
                                                {t.insertTemplate || 'Template'}: {tpl.name}
                                            </button>
                                        ))}
                                        <button type="button" onClick={() => runCanvasMenuAction('paste')} disabled={!clipboard?.nodes?.length}>{t.pasteSelection || 'Paste'}</button>
                                        <button type="button" onClick={() => runCanvasMenuAction('undo')} disabled={!historyMeta.canUndo}>{t.undo || 'Undo'}</button>
                                        <button type="button" onClick={() => runCanvasMenuAction('redo')} disabled={!historyMeta.canRedo}>{t.redo || 'Redo'}</button>
                                        <button type="button" onClick={() => runCanvasMenuAction('import_json')}>{t.importJson || 'Import JSON'}</button>
                                        <button type="button" onClick={() => runCanvasMenuAction('export_json')}>{t.exportJson || 'Export JSON'}</button>
                                        <button type="button" onClick={() => runCanvasMenuAction('import_flowise')}>{t.importFlowiseJson || 'Import Flowise'}</button>
                                        <button type="button" onClick={() => runCanvasMenuAction('export_flowise')}>{t.exportFlowiseJson || 'Export Flowise'}</button>
                                    </>
                                )}
                                {canvasMenu.targetType === 'node' && (
                                    <>
                                        <button type="button" onClick={() => runCanvasMenuAction('inspect_node')}>{t.nodeEditor || 'Node Editor'}</button>
                                        <button type="button" onClick={() => runCanvasMenuAction('connect_from')}>{t.connectingFrom || 'Connect From'}</button>
                                        <button type="button" onClick={() => runCanvasMenuAction('copy_node')}>{t.copySelection || 'Copy'}</button>
                                        <button type="button" onClick={() => runCanvasMenuAction('duplicate_node')}>{t.duplicateSelection || 'Duplicate'}</button>
                                        <button type="button" onClick={() => runCanvasMenuAction('toggle_node_lock')}>
                                            {nodeMap[canvasMenu.nodeId]?.locked ? (t.batchUnlock || 'Unlock') : (t.batchLock || 'Lock')}
                                        </button>
                                        <button type="button" onClick={() => runCanvasMenuAction('toggle_node_enabled')}>
                                            {nodeMap[canvasMenu.nodeId]?.enabled === false ? (t.batchEnable || 'Enable') : (t.batchDisable || 'Disable')}
                                        </button>
                                        <button type="button" onClick={() => runCanvasMenuAction('remove_node')}>{t.remove || 'Remove'}</button>
                                    </>
                                )}
                                {canvasMenu.targetType === 'edge' && (
                                    <button type="button" onClick={() => runCanvasMenuAction('remove_edge')}>{t.deleteEdge || 'Delete Edge'}</button>
                                )}
                            </div>
                        )}
                        <div className="workflow-canvas-controls">
                            <button
                                type="button"
                                onClick={() => zoomAt(viewport.scale + 0.1, { x: 0, y: 0 })}
                                title={t.zoomIn || 'Zoom In'}
                                aria-label={t.zoomIn || 'Zoom In'}
                            >
                                +
                            </button>
                            <button
                                type="button"
                                onClick={() => zoomAt(viewport.scale - 0.1, { x: 0, y: 0 })}
                                title={t.zoomOut || 'Zoom Out'}
                                aria-label={t.zoomOut || 'Zoom Out'}
                            >
                                −
                            </button>
                            <button
                                type="button"
                                onClick={fitView}
                                title={t.fitView || 'Fit View'}
                                aria-label={t.fitView || 'Fit View'}
                            >
                                ⛶
                            </button>
                            <button
                                type="button"
                                onClick={() => setViewport({ scale: 1, offsetX: 0, offsetY: 0 })}
                                title={t.resetView || 'Reset View'}
                                aria-label={t.resetView || 'Reset View'}
                            >
                                ↺
                            </button>
                        </div>
                    </div>
                    {!rightPanelCollapsed && (
                        <div style={{ height: canvasHeight, minHeight: isMobile ? 420 : 440, overflowY: 'auto', paddingRight: 2 }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 10 }}>
                        <div className="card" style={{ padding: 10 }}>
                            <strong>{t.nodeEditor || 'Node Editor'}</strong>
                            {selectedNode ? (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
                                    <input className="input" value={selectedNode.id} onChange={(e) => updateNode(selectedNode.id, { id: e.target.value })} />
                                    <input className="input" value={selectedNode.label || ''} onChange={(e) => updateNode(selectedNode.id, { label: e.target.value })} />
                                    <div style={{ display: 'flex', gap: 14, alignItems: 'center', fontSize: 12 }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                            <span>{t.nodeEnabled || 'Enabled'}</span>
                                            <ToggleSwitch
                                                checked={selectedNode.enabled !== false}
                                                onChange={(v) => updateNode(selectedNode.id, { enabled: v })}
                                            />
                                        </div>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                            <span>{t.nodeLocked || 'Locked'}</span>
                                            <ToggleSwitch
                                                checked={selectedNode.locked === true}
                                                onChange={(v) => updateNode(selectedNode.id, { locked: v })}
                                            />
                                        </div>
                                    </div>
                                    <select
                                        className="select"
                                        value={selectedNode.type}
                                        onChange={(e) => updateNode(selectedNode.id, { type: e.target.value, config: defaultConfigByType(e.target.value) })}
                                    >
                                        {NODE_TYPES.map((it) => <option key={it} value={it}>{nodeTypeLabel(it, t)}</option>)}
                                    </select>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6 }}>
                                        <input
                                            className="input"
                                            type="number"
                                            min={0}
                                            step={100}
                                            value={Number(selectedNode.config?.timeout_ms || 0)}
                                            onChange={(e) => {
                                                const v = Number(e.target.value || 0);
                                                updateSelectedNodeConfig({ timeout_ms: Number.isFinite(v) ? Math.max(0, Math.floor(v)) : 0 });
                                            }}
                                            placeholder={t.nodeTimeoutMs || 'timeout_ms'}
                                            title={t.nodeTimeoutMsHint || 'Node execution timeout in milliseconds (0 = no timeout)'}
                                        />
                                        <input
                                            className="input"
                                            type="number"
                                            min={0}
                                            max={5}
                                            step={1}
                                            value={Number(selectedNode.config?.retry_count || 0)}
                                            onChange={(e) => {
                                                const v = Number(e.target.value || 0);
                                                const normalized = Number.isFinite(v) ? Math.max(0, Math.min(5, Math.floor(v))) : 0;
                                                updateSelectedNodeConfig({ retry_count: normalized });
                                            }}
                                            placeholder={t.nodeRetryCount || 'retry_count'}
                                            title={t.nodeRetryCountHint || 'Retry attempts after failure (0-5)'}
                                        />
                                        <select
                                            className="select"
                                            value={String(selectedNode.config?.on_error || 'fail')}
                                            onChange={(e) => updateSelectedNodeConfig({ on_error: e.target.value })}
                                            title={t.nodeOnErrorHint || 'Error policy for node execution'}
                                        >
                                            <option value="fail">{t.nodeOnErrorFail || 'on_error: fail'}</option>
                                            <option value="continue">{t.nodeOnErrorContinue || 'on_error: continue'}</option>
                                            <option value="fallback">{t.nodeOnErrorFallback || 'on_error: fallback'}</option>
                                        </select>
                                    </div>
                                    {String(selectedNode.config?.on_error || 'fail') === 'fallback' && (
                                        <>
                                            <div style={{ fontSize: 11, opacity: 0.72 }}>
                                                {t.nodeFallbackHint || 'Fallback template can use {{prev}}, {{input}}, {{error}}.'}
                                            </div>
                                            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                                <button className="btn-secondary" type="button" onClick={() => appendConfigToken('fallback_output', '{{prev}}', '{{prev}}')}>+ {'{{prev}}'}</button>
                                                <button className="btn-secondary" type="button" onClick={() => appendConfigToken('fallback_output', '{{input}}', '{{prev}}')}>+ {'{{input}}'}</button>
                                                <button className="btn-secondary" type="button" onClick={() => appendConfigToken('fallback_output', '{{error}}', '{{prev}}')}>+ {'{{error}}'}</button>
                                            </div>
                                            <textarea
                                                className="textarea"
                                                rows={2}
                                                value={String(selectedNode.config?.fallback_output || '{{prev}}')}
                                                onChange={(e) => updateSelectedNodeConfig({ fallback_output: e.target.value })}
                                                placeholder={t.nodeFallbackOutput || 'fallback output template (supports {{prev}}, {{input}}, {{error}})'}
                                            />
                                        </>
                                    )}

                                    {selectedNode.type === 'input' && (
                                        <input
                                            className="input"
                                            value={String(selectedNode.config?.default || '')}
                                            onChange={(e) => updateSelectedNodeConfig({ default: e.target.value })}
                                            placeholder={t.defaultInput || 'default input'}
                                        />
                                    )}

                                    {selectedNode.type === 'prompt' && (
                                        <>
                                            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                                <button className="btn-secondary" type="button" onClick={() => appendConfigToken('prompt', '{{prev}}', '{{prev}}')}>+ {'{{prev}}'}</button>
                                                <button className="btn-secondary" type="button" onClick={() => appendConfigToken('prompt', '{{input}}', '{{prev}}')}>+ {'{{input}}'}</button>
                                                <button className="btn-secondary" type="button" onClick={() => appendConfigToken('prompt', '{{error}}', '{{prev}}')}>+ {'{{error}}'}</button>
                                            </div>
                                            <textarea
                                                className="textarea"
                                                rows={3}
                                                value={String(selectedNode.config?.prompt || '')}
                                                onChange={(e) => updateSelectedNodeConfig({ prompt: e.target.value })}
                                                placeholder={t.promptTemplate || 'Prompt template'}
                                            />
                                        </>
                                    )}

                                    {selectedNode.type === 'tool' && (
                                        <>
                                            <div style={{ fontSize: 11, opacity: 0.72 }}>
                                                {t.toolNodeHint || 'Set tool_name and JSON args. Args can include {{prev}}/{{input}}/{{error}} tokens.'}
                                            </div>
                                            <input
                                                className="input"
                                                value={String(selectedNode.config?.tool_name || '')}
                                                onChange={(e) => updateSelectedNodeConfig({ tool_name: e.target.value })}
                                                placeholder={t.toolName || 'tool_name'}
                                            />
                                            <textarea
                                                className="textarea"
                                                rows={3}
                                                value={asJson(selectedNode.config?.args || {})}
                                                onChange={(e) => {
                                                    try {
                                                        updateSelectedNodeConfig({ args: JSON.parse(e.target.value || '{}') });
                                                    } catch {}
                                                }}
                                                placeholder={t.toolArgs || 'Tool args JSON'}
                                            />
                                        </>
                                    )}

                                    {selectedNode.type === 'condition' && (
                                        <>
                                            <div style={{ fontSize: 11, opacity: 0.72 }}>
                                                {t.conditionNodeHint || 'Condition compares previous output ({{prev}}) with value using selected operator.'}
                                            </div>
                                            <select
                                                className="select"
                                                value={String(selectedNode.config?.operator || 'contains')}
                                                onChange={(e) => updateSelectedNodeConfig({ operator: e.target.value })}
                                            >
                                                <option value="contains">contains</option>
                                                <option value="equals">equals</option>
                                                <option value="not_contains">not_contains</option>
                                            </select>
                                            <input
                                                className="input"
                                                value={String(selectedNode.config?.value || '')}
                                                onChange={(e) => updateSelectedNodeConfig({ value: e.target.value })}
                                                placeholder={t.conditionValue || 'Condition value'}
                                            />
                                        </>
                                    )}

                                    {selectedNode.type === 'output' && (
                                        <>
                                            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                                <button className="btn-secondary" type="button" onClick={() => appendConfigToken('text', '{{prev}}', '{{prev}}')}>+ {'{{prev}}'}</button>
                                                <button className="btn-secondary" type="button" onClick={() => appendConfigToken('text', '{{input}}', '{{prev}}')}>+ {'{{input}}'}</button>
                                                <button className="btn-secondary" type="button" onClick={() => appendConfigToken('text', '{{error}}', '{{prev}}')}>+ {'{{error}}'}</button>
                                            </div>
                                            <textarea
                                                className="textarea"
                                                rows={3}
                                                value={String(selectedNode.config?.text || '')}
                                                onChange={(e) => updateSelectedNodeConfig({ text: e.target.value })}
                                                placeholder={t.outputTemplate || 'Output template'}
                                            />
                                        </>
                                    )}

                                    <details>
                                        <summary style={{ cursor: 'pointer', fontSize: 12, opacity: 0.85 }}>{t.advancedJson || 'Advanced JSON'}</summary>
                                        <textarea
                                            className="textarea"
                                            rows={6}
                                            value={advancedJsonText}
                                            onChange={(e) => {
                                                setAdvancedJsonText(e.target.value);
                                                try {
                                                    JSON.parse(e.target.value || '{}');
                                                    setAdvancedJsonError('');
                                                } catch (err) {
                                                    setAdvancedJsonError(err?.message || 'Invalid JSON');
                                                }
                                            }}
                                        />
                                        {advancedJsonError && <div style={{ color: '#fca5a5', fontSize: 12, marginTop: 4 }}>{advancedJsonError}</div>}
                                        <div style={{ marginTop: 6 }}>
                                            <button className="btn-secondary" onClick={applyAdvancedJson}>{t.applyJson || 'Apply JSON'}</button>
                                        </div>
                                    </details>
                                    <button className="workflow-action-btn workflow-action-btn--danger workflow-action-btn--sm" onClick={() => removeNode(selectedNode.id)}>{t.remove || 'Remove'}</button>
                                </div>
                            ) : <div style={{ marginTop: 8, opacity: 0.7 }}>{t.selectNodeHint || 'Select a node to edit.'}</div>}
                        </div>
                        <div className="card" style={{ padding: 10 }}>
                            <strong>{t.edges || 'Edges'}</strong>
                            {(graph.edges || []).map((edge) => (
                                <div key={edge.id} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 110px auto', gap: 6, marginTop: 6 }}>
                                    <select className="select" value={edge.source} onChange={(e) => updateEdge(edge.id, { source: e.target.value })}>
                                        {Object.keys(nodeMap).map((id) => <option key={id} value={id}>{id}</option>)}
                                    </select>
                                    <select className="select" value={edge.target} onChange={(e) => updateEdge(edge.id, { target: e.target.value })}>
                                        {Object.keys(nodeMap).map((id) => <option key={id} value={id}>{id}</option>)}
                                    </select>
                                    <select className="select" value={String(edge.when || '')} onChange={(e) => updateEdge(edge.id, { when: e.target.value })}>
                                        <option value="">{t.edgeWhenAny || 'any'}</option>
                                        <option value="true">{t.edgeWhenTrue || 'true'}</option>
                                        <option value="false">{t.edgeWhenFalse || 'false'}</option>
                                        <option value="default">{t.edgeWhenDefault || 'default'}</option>
                                    </select>
                                    <button className="workflow-action-btn workflow-action-btn--danger workflow-action-btn--sm" onClick={() => removeEdge(edge.id)}>{t.remove || 'Remove'}</button>
                                </div>
                            ))}
                        </div>
                    </div>

                    <div className="card" style={{ padding: 10 }}>
                        <strong>{t.run || 'Run'}</strong>
                        <textarea className="textarea" rows={3} value={runInput} onChange={(e) => setRunInput(e.target.value)} placeholder={t.workflowRunInput || 'Input text'} style={{ width: '100%', marginTop: 6 }} />
                        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                            <button className="workflow-action-btn workflow-action-btn--primary" onClick={runWorkflow} disabled={loading || !workflowId}>{t.runWorkflow || 'Run Workflow'}</button>
                            <button className="workflow-action-btn workflow-action-btn--sm" onClick={() => setShowRawResult((prev) => !prev)}>{showRawResult ? (t.hideRaw || 'Hide Raw') : (t.showRaw || 'Show Raw')}</button>
                        </div>
                        {runResult && (
                            <>
                                <div style={{ marginTop: 10, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                                    <div style={{ background: 'rgba(0,0,0,0.25)', padding: 10, borderRadius: 8, maxHeight: 220, overflow: 'auto' }}>
                                        <div style={{ fontWeight: 600, marginBottom: 6 }}>{t.executionTrace || 'Execution Trace'}</div>
                                        {(runResult.trace || []).map((step, idx) => (
                                            <button
                                                key={`${step.node_id}_${idx}`}
                                                type="button"
                                                onClick={() => setSelectedTraceIndex(idx)}
                                                style={{
                                                    width: '100%',
                                                    textAlign: 'left',
                                                    border: 'none',
                                                    borderBottom: '1px solid rgba(255,255,255,0.08)',
                                                    padding: '6px 0',
                                                    background: selectedTraceIndex === idx ? 'rgba(250,204,21,0.12)' : 'transparent',
                                                    color: 'inherit',
                                                    cursor: 'pointer',
                                                }}
                                            >
                                                <div style={{ fontSize: 12 }}><strong>{step.node_id}</strong> · {step.node_type} · {step.status}</div>
                                                <div style={{ fontSize: 11, opacity: 0.72 }}>
                                                    {(step.duration_ms ?? 0)}ms
                                                    {Number(step.attempts_total || 0) > 0 ? ` · ${step.attempts_used || 0}/${step.attempts_total} tries` : ''}
                                                </div>
                                                <div style={{ fontSize: 11, opacity: 0.75 }}>{step.output}</div>
                                            </button>
                                        ))}
                                    </div>
                                    <div style={{ background: 'rgba(0,0,0,0.25)', padding: 10, borderRadius: 8 }}>
                                        <div style={{ fontWeight: 600, marginBottom: 6 }}>{t.finalOutput || 'Final Output'}</div>
                                        {runResult.metrics && (
                                            <div style={{ fontSize: 12, opacity: 0.78, marginBottom: 6 }}>
                                                {`${t.metricNodes || 'nodes'} ${runResult.metrics.trace_nodes || 0} · ${t.metricOk || 'ok'} ${runResult.metrics.ok_nodes || 0} · ${t.metricWarn || 'warn'} ${runResult.metrics.warning_nodes || 0} · ${t.metricSkipped || 'skipped'} ${runResult.metrics.skipped_nodes || 0} · ${t.metricErr || 'err'} ${runResult.metrics.error_nodes || 0} · ${t.metricTotal || 'total'} ${runResult.metrics.total_duration_ms || 0}ms`}
                                            </div>
                                        )}
                                        <pre style={{ margin: 0, maxHeight: 200, overflow: 'auto' }}>{String(runResult.output || '')}</pre>
                                    </div>
                                </div>
                                {showRawResult && (
                                    <pre style={{ marginTop: 10, maxHeight: 240, overflow: 'auto', background: 'rgba(0,0,0,0.25)', padding: 10, borderRadius: 8 }}>
                                        {JSON.stringify(runResult, null, 2)}
                                    </pre>
                                )}
                            </>
                        )}
                    </div>
                        </div>
                    )}
                    </div>
                </div>
            </div>
            {editModalOpen && (
                <div style={{ position: 'fixed', inset: 0, zIndex: 1200, background: 'rgba(2,6,23,0.62)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
                    <div className="card" style={{ width: 'min(620px, 92vw)', padding: 14 }}>
                        <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 10 }}>
                            {t.editWorkflow || 'Edit Workflow'}
                        </div>
                        <div style={{ display: 'grid', gap: 8 }}>
                            <input
                                className="input"
                                value={editWorkflowName}
                                onChange={(e) => setEditWorkflowName(e.target.value)}
                                placeholder={t.workflowName || 'Workflow name'}
                            />
                            <input
                                className="input"
                                value={editWorkflowDescription}
                                onChange={(e) => setEditWorkflowDescription(e.target.value)}
                                placeholder={t.workflowDescription || 'Description'}
                            />
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
                            <button className="btn-secondary" type="button" onClick={() => setEditModalOpen(false)}>
                                {t.cancel || 'Cancel'}
                            </button>
                            <button className="btn-primary" type="button" onClick={applyWorkflowMetaAndSave} disabled={loading}>
                                {saveLabel}
                            </button>
                        </div>
                    </div>
                </div>
            )}
            <ConfirmModal
                open={!!importPreview}
                title={
                    importPreview?.import_mode === 'flowise'
                        ? (t.confirmImportFlowiseTitle || 'Import Flowise JSON')
                        : (t.confirmImportWorkflowTitle || 'Import Workflow JSON')
                }
                message={
                    importPreview
                        ? (
                            <span style={{ whiteSpace: 'pre-line' }}>
                                {(() => {
                                    const lines = [
                                        t.confirmImportWorkflowMessage || 'Replace current canvas with imported workflow?',
                                        '',
                                    ];
                                    if (historyMeta.canUndo) {
                                        lines.push(t.unsavedChangesWillBeLost || 'Unsaved changes will be lost.');
                                    }
                                    lines.push(`${t.fileLabel || 'file'}: ${importPreview.file_name || '-'}`);
                                    lines.push(`${t.nameLabel || 'name'}: ${importPreview.name || '-'}`);
                                    lines.push(`${t.nodes || 'nodes'}: ${importPreview.node_count}`);
                                    lines.push(`${t.edges || 'edges'}: ${importPreview.edge_count}`);
                                    if (importPreview.import_mode === 'flowise') {
                                        const summary = importPreview.summary || {};
                                        const validation = importPreview.validation || {};
                                        lines.push(
                                            `flowise_warnings: total=${Number(importPreview.error_count || 0)}, node=${Number(summary.node || 0)}, edge=${Number(summary.edge || 0)}`,
                                        );
                                        if (validation.ok === false) {
                                            lines.push(`validation: failed (${validation.code || 'workflow_invalid'})`);
                                            if (validation.message) {
                                                lines.push(`validation_message: ${validation.message}`);
                                            }
                                        } else if (validation && typeof validation === 'object') {
                                            const checks = validation.checks || {};
                                            lines.push(
                                                `validation: dag=${checks.dag === false ? 'fail' : 'ok'}, reachable_output=${checks.reachable_output === false ? 'fail' : 'ok'}, condition_edges=${checks.condition_edges === false ? 'fail' : 'ok'}`,
                                            );
                                        }
                                    }
                                    return lines.join('\n');
                                })()}
                            </span>
                        )
                        : (t.confirmImportWorkflowMessage || 'Replace current canvas with imported workflow?')
                }
                confirmText={
                    importPreview?.import_mode === 'flowise'
                        ? (t.importFlowiseJson || 'Import Flowise')
                        : (t.importJson || 'Import JSON')
                }
                cancelText={t.cancel || 'Cancel'}
                onConfirm={confirmImportWorkflow}
                onCancel={cancelImportWorkflow}
                danger={false}
            />
        </div>
    );
};

export default WorkflowStudio;
