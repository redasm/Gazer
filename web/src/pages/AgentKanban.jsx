import React, { useCallback, useEffect, useMemo, useReducer, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  Clock3,
  MessageSquareText,
  PauseCircle,
  PlayCircle,
  RefreshCw,
  Save,
  Settings2,
  X,
} from 'lucide-react';

import API_BASE from '../config';
import { postTaskComment } from '../api/kanbanApi';
import { useMonitorWS } from '../hooks/useMonitorWS';

const COLUMNS = [
  { key: 'queued', accent: '#64748b' },
  { key: 'running', accent: '#22d3ee' },
  { key: 'sleeping', accent: '#facc15' },
  { key: 'completed', accent: '#34d399' },
  { key: 'failed', accent: '#fb7185' },
];

const PRIORITY_ORDER = { critical: 0, high: 1, normal: 2, low: 3 };
const INITIAL_STATE = {
  sessionKey: '',
  sessionLabel: '',
  tasks: {},
  logs: [],
  stats: { total: 0, queued: 0, running: 0, sleeping: 0, done: 0, failed: 0, tokens: 0 },
  phase: 'idle',
  selectedTaskId: '',
  connection: 'connecting',
};

function buildLabels(t) {
  return {
    subtitle: t.agentKanbanSubtitle || 'Multi-Agent Mission Control',
    missionFocus: t.agentKanbanMissionFocus || 'Mission Focus',
    controlDeck: t.agentKanbanControlDeck || 'Operator Deck',
    executionLanes: t.agentKanbanExecutionLanes || 'Execution Lanes',
    railHint: t.agentKanbanRailHint || 'Select a task card to inspect details, progress, and operator comments.',
    sessionEmpty: t.agentKanbanSessionEmpty || 'No active multi-agent session',
    unnamedTask: t.agentKanbanUnnamedTask || 'Unnamed Task',
    workerFallback: t.agentKanbanWorkerFallback || 'worker',
    session: t.agentKanbanSession || 'Session',
    total: t.agentKanbanStatTotal || 'Total',
    running: t.agentKanbanStatRunning || 'Running',
    sleeping: t.agentKanbanStatSleeping || 'Sleeping',
    done: t.agentKanbanStatDone || 'Done',
    failed: t.agentKanbanStatFailed || 'Failed',
    noTasks: t.agentKanbanNoTasks || 'No tasks',
    noDescription: t.agentKanbanNoDescription || 'No description provided.',
    noTaskDescription: t.agentKanbanNoTaskDescription || 'No task description available.',
    deps: t.agentKanbanDeps || 'deps',
    tools: t.agentKanbanTools || 'tools',
    comments: t.agentKanbanCommentsCount || 'comments',
    currentTool: t.agentKanbanCurrentTool || 'Current tool',
    executionControl: t.agentKanbanExecutionControl || 'Execution Control',
    executionControlDesc: t.agentKanbanExecutionControlDesc || 'Single-agent remains the default path. Turn this on to let Gazer split complex goals and fan them out to workers.',
    multiAgent: t.agentKanbanMultiAgent || 'Multi-agent',
    multiAgentDesc: t.agentKanbanMultiAgentDesc || 'Automatic task decomposition and worker pool scheduling',
    maxWorkers: t.agentKanbanMaxWorkers || 'Max workers',
    maxWorkersHint: t.agentKanbanMaxWorkersHint || 'Upper budget for parallel workers. The runtime still decides how many are justified per task.',
    taskDetail: t.agentKanbanTaskDetail || 'Task Detail',
    taskDetailDesc: t.agentKanbanTaskDetailDesc || 'Click a card to inspect progress, notes, and logs',
    noTaskSelected: t.agentKanbanNoTaskSelected || 'No task selected',
    noTaskSelectedDesc: t.agentKanbanNoTaskSelectedDesc || 'The board will populate when multi-agent planning starts. Select a task card to inspect its details and leave operator notes.',
    commentTitle: t.agentKanbanCommentTitle || 'Comments',
    commentPlaceholder: t.agentKanbanCommentPlaceholder || 'Add operator guidance or a corrective note...',
    commentRequired: t.agentKanbanCommentRequired || 'Comment text is required.',
    commentSubmit: t.agentKanbanCommentSubmit || 'Post Comment',
    commentPosting: t.agentKanbanCommentPosting || 'Posting...',
    commentFailed: t.agentKanbanCommentFailed || 'Failed to submit comment.',
    noComments: t.agentKanbanNoComments || 'No comments yet.',
    ownerLabel: t.agentKanbanOwnerLabel || 'Owner',
    tokens: t.agentKanbanTokens || 'Tokens',
    statusLabel: t.agentKanbanStatusLabel || 'Status',
    liveLog: t.agentKanbanLiveLog || 'Live Log',
    noLogs: t.agentKanbanNoLogs || 'No logs yet.',
    started: t.agentKanbanStarted || 'Started',
    ended: t.agentKanbanEnded || 'Ended',
    toolCalls: t.agentKanbanToolCalls || 'tool calls',
    noActiveTool: t.agentKanbanNoActiveTool || 'No active tool',
    result: t.agentKanbanResult || 'Result',
    failure: t.agentKanbanFailure || 'Failure',
    statusQueued: t.agentKanbanStatusQueued || 'Queued',
    statusRunning: t.agentKanbanStatusRunning || 'Running',
    statusSleeping: t.agentKanbanStatusSleeping || 'Sleeping',
    statusCompleted: t.agentKanbanStatusCompleted || 'Completed',
    statusFailed: t.agentKanbanStatusFailed || 'Failed',
    priorityCritical: t.agentKanbanPriorityCritical || 'critical',
    priorityHigh: t.agentKanbanPriorityHigh || 'high',
    priorityNormal: t.agentKanbanPriorityNormal || 'normal',
    priorityLow: t.agentKanbanPriorityLow || 'low',
    connectionLive: t.agentKanbanConnectionLive || 'LIVE',
    connectionReconnecting: t.agentKanbanConnectionReconnecting || 'RECONNECTING',
    connectionError: t.agentKanbanConnectionError || 'ERROR',
    connectionClosed: t.agentKanbanConnectionClosed || 'CLOSED',
    connectionConnecting: t.agentKanbanConnectionConnecting || 'CONNECTING',
    phaseActive: t.agentKanbanPhaseActive || 'ACTIVE',
    phaseDone: t.agentKanbanPhaseDone || 'DONE',
    phaseIdle: t.agentKanbanPhaseIdle || 'IDLE',
    logTypeStart: t.agentKanbanLogTypeStart || 'START',
    logTypeTool: t.agentKanbanLogTypeTool || 'TOOL',
    logTypeComplete: t.agentKanbanLogTypeComplete || 'COMPLETE',
    logTypeError: t.agentKanbanLogTypeError || 'ERROR',
    logTypeSystem: t.agentKanbanLogTypeSystem || 'SYSTEM',
    enabled: t.enabled || 'Enabled',
    disabled: t.disabled || 'Disabled',
    settingsTitle: t.agentKanbanSettingsTitle || t.settings || 'Settings',
    settingsButton: t.agentKanbanSettingsButton || t.settings || 'Settings',
    settingsClose: t.close || 'Close',
  };
}

function normalizeTask(task = {}) {
  return {
    task_id: task.task_id || '',
    title: task.title || task.task_id || '',
    description: task.description || '',
    agent_id: task.agent_id || '',
    depends: Array.isArray(task.depends) ? task.depends : [],
    session_key: task.session_key || '',
    priority: task.priority || 'normal',
    status: task.status || 'queued',
    current_tool: task.current_tool || null,
    tool_calls: Number(task.tool_calls || 0),
    result_summary: task.result_summary || '',
    started_at: task.started_at ?? null,
    ended_at: task.ended_at ?? null,
    error: task.error || '',
    comments: Array.isArray(task.comments) ? task.comments : [],
    tokens_used: Number(task.tokens_used || 0),
  };
}

function deriveStats(tasks) {
  const stats = { total: 0, queued: 0, running: 0, sleeping: 0, done: 0, failed: 0, tokens: 0 };
  Object.values(tasks).forEach((task) => {
    stats.total += 1;
    stats.tokens += Number(task.tokens_used || 0);
    if (task.status === 'completed') stats.done += 1;
    else if (task.status === 'failed') stats.failed += 1;
    else if (task.status === 'sleeping') stats.sleeping += 1;
    else if (task.status === 'running') stats.running += 1;
    else stats.queued += 1;
  });
  return stats;
}

function derivePhase(stats) {
  if (!stats.total) return 'idle';
  if (stats.running || stats.queued || stats.sleeping) return 'running';
  return 'done';
}
function patchTask(state, taskId, patch) {
  const tasks = {
    ...state.tasks,
    [taskId]: {
      ...normalizeTask(state.tasks[taskId] || { task_id: taskId, title: taskId }),
      ...patch,
    },
  };
  const stats = deriveStats(tasks);
  return {
    ...state,
    tasks,
    stats,
    phase: derivePhase(stats),
    selectedTaskId: state.selectedTaskId || taskId,
  };
}

function sameSession(state, sessionKey) {
  return !state.sessionKey || !sessionKey || state.sessionKey === sessionKey;
}

function reducer(state, action) {
  if (action.type === 'connection') return { ...state, connection: action.status };
  if (action.type === 'select') return { ...state, selectedTaskId: action.taskId };
  if (action.type !== 'event') return state;

  const message = action.message || {};
  const payload = message.payload || {};
  const sessionKey = payload.session_key || '';

  switch (message.event) {
    case 'session.init': {
      const tasks = Object.fromEntries((payload.tasks || []).map((task) => {
        const normalized = normalizeTask(task);
        return [normalized.task_id, normalized];
      }));
      const stats = deriveStats(tasks);
      return {
        ...state,
        sessionKey,
        sessionLabel: payload.session_label || '',
        tasks,
        logs: Array.isArray(payload.logs) ? [...payload.logs].reverse() : [],
        stats,
        phase: derivePhase(stats),
        selectedTaskId: state.selectedTaskId && tasks[state.selectedTaskId] ? state.selectedTaskId : (Object.keys(tasks)[0] || ''),
      };
    }
    case 'task.created':
      if (!sameSession(state, sessionKey)) return state;
      return patchTask(state, payload.task_id, normalizeTask({ ...payload, status: 'queued' }));
    case 'task.status':
      if (!sameSession(state, sessionKey)) return state;
      return patchTask(state, payload.task_id, {
        status: payload.status || 'queued',
        agent_id: payload.agent_id ?? state.tasks[payload.task_id]?.agent_id ?? '',
        current_tool: Object.prototype.hasOwnProperty.call(payload, 'current_tool')
          ? payload.current_tool
          : state.tasks[payload.task_id]?.current_tool ?? null,
        tool_calls: payload.tool_calls ?? state.tasks[payload.task_id]?.tool_calls ?? 0,
        started_at: Object.prototype.hasOwnProperty.call(payload, 'started_at')
          ? payload.started_at
          : state.tasks[payload.task_id]?.started_at ?? null,
        ended_at: Object.prototype.hasOwnProperty.call(payload, 'ended_at')
          ? payload.ended_at
          : state.tasks[payload.task_id]?.ended_at ?? null,
      });
    case 'task.tool_call':
      if (!sameSession(state, sessionKey)) return state;
      return patchTask(state, payload.task_id, {
        status: state.tasks[payload.task_id]?.status || 'running',
        agent_id: payload.agent_id ?? state.tasks[payload.task_id]?.agent_id ?? '',
        current_tool: payload.tool_name || null,
        tool_calls: payload.tool_call_index ?? state.tasks[payload.task_id]?.tool_calls ?? 0,
      });
    case 'task.completed':
      if (!sameSession(state, sessionKey)) return state;
      return patchTask(state, payload.task_id, {
        status: 'completed',
        result_summary: payload.result_summary || '',
        tool_calls: payload.tool_calls ?? state.tasks[payload.task_id]?.tool_calls ?? 0,
        started_at: payload.started_at ?? state.tasks[payload.task_id]?.started_at ?? null,
        ended_at: payload.ended_at ?? state.tasks[payload.task_id]?.ended_at ?? null,
        tokens_used: payload.tokens_used ?? state.tasks[payload.task_id]?.tokens_used ?? 0,
        current_tool: null,
        error: '',
      });
    case 'task.failed':
      if (!sameSession(state, sessionKey)) return state;
      return patchTask(state, payload.task_id, {
        status: 'failed',
        error: payload.error || '',
        ended_at: payload.ended_at ?? state.tasks[payload.task_id]?.ended_at ?? null,
        current_tool: null,
      });
    case 'task.comment':
      if (!sameSession(state, sessionKey)) return state;
      return patchTask(state, payload.task_id, {
        comments: [...(state.tasks[payload.task_id]?.comments || []), payload],
      });
    case 'log.entry':
      if (!sameSession(state, sessionKey)) return state;
      return {
        ...state,
        logs: [{ ...payload, ts: payload.ts || message.ts }, ...state.logs].slice(0, 200),
      };
    default:
      return state;
  }
}

function cloneConfig(config) {
  return JSON.parse(JSON.stringify(config || {}));
}

function updateNestedConfig(config, path, value) {
  const next = cloneConfig(config);
  const keys = path.split('.');
  let current = next;
  for (let index = 0; index < keys.length - 1; index += 1) {
    const key = keys[index];
    if (!current[key] || typeof current[key] !== 'object') current[key] = {};
    current = current[key];
  }
  current[keys[keys.length - 1]] = value;
  return next;
}

function sortTasks(tasks) {
  return [...tasks].sort((left, right) => {
    const priorityDelta = (PRIORITY_ORDER[left.priority] ?? 9) - (PRIORITY_ORDER[right.priority] ?? 9);
    if (priorityDelta !== 0) return priorityDelta;
    const leftTime = left.started_at ?? left.ended_at ?? 0;
    const rightTime = right.started_at ?? right.ended_at ?? 0;
    return rightTime - leftTime;
  });
}

function formatTimestamp(ts) {
  if (!ts) return '--';
  try {
    return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  } catch {
    return '--';
  }
}

function formatPhase(phase, labels) {
  if (phase === 'running') return labels.phaseActive;
  if (phase === 'done') return labels.phaseDone;
  return labels.phaseIdle;
}

function phaseAccent(phase) {
  if (phase === 'running') return '#38bdf8';
  if (phase === 'done') return '#34d399';
  return '#94a3b8';
}

function translateStatus(status, labels) {
  if (status === 'running') return labels.statusRunning;
  if (status === 'sleeping') return labels.statusSleeping;
  if (status === 'completed') return labels.statusCompleted;
  if (status === 'failed') return labels.statusFailed;
  return labels.statusQueued;
}

function translatePriority(priority, labels) {
  if (priority === 'critical') return labels.priorityCritical;
  if (priority === 'high') return labels.priorityHigh;
  if (priority === 'low') return labels.priorityLow;
  return labels.priorityNormal;
}

function previewText(value, limit = 34) {
  if (!value) return '';
  if (value.length <= limit) return value;
  return `${value.slice(0, limit - 1)}...`;
}

function formatCount(value) {
  return Number(value || 0).toLocaleString();
}

function accentForStatus(status) {
  return COLUMNS.find((column) => column.key === status)?.accent || '#64748b';
}

function taskProgress(task) {
  if (task.status === 'completed' || task.status === 'failed') return 100;
  if (task.status === 'sleeping') return Math.min(88, Math.max(26, 18 + (task.tool_calls || 0) * 14));
  if (task.status === 'running') return Math.min(92, Math.max(22, 20 + (task.tool_calls || 0) * 16));
  return 8;
}

function TaskColumn({ column, tasks, selectedTaskId, onSelect, labels }) {
  return (
    <section className="agent-kanban-column" style={{ '--agent-kanban-accent': column.accent }}>
      <header className="agent-kanban-column-header">
        <div className="agent-kanban-column-heading">
          <span className="agent-kanban-column-dot" />
          <span className="agent-kanban-column-label">{column.label}</span>
        </div>
        <div className="agent-kanban-column-header-meta">
          <span className="agent-kanban-column-code">{column.systemLabel}</span>
          <span className="agent-kanban-column-count">{tasks.length}</span>
        </div>
      </header>
      <div className="agent-kanban-card-stack">
        {tasks.length === 0 && (
          <div className="agent-kanban-task-card agent-kanban-task-card-empty">{labels.noTasks}</div>
        )}
        {tasks.map((task) => {
          const isActive = selectedTaskId === task.task_id;
          const progress = taskProgress(task);
          const preview = task.result_summary || task.error || task.description || labels.noDescription;
          return (
            <button
              key={task.task_id}
              type="button"
              className={`agent-kanban-task-card${isActive ? ' is-active' : ''}`}
              style={{ '--agent-kanban-accent': column.accent, '--agent-kanban-progress': `${progress}%` }}
              onClick={() => onSelect(task.task_id)}
              aria-pressed={isActive}
            >
              <div className="agent-kanban-task-card-head">
                <div>
                  <div className="agent-kanban-task-title">{task.title || labels.unnamedTask}</div>
                  <div className="agent-kanban-task-owner">{task.agent_id || labels.workerFallback} · {translatePriority(task.priority, labels)}</div>
                </div>
                <span className="agent-kanban-task-status">{translateStatus(task.status, labels)}</span>
              </div>
              <div className="agent-kanban-task-description">{previewText(preview, 96)}</div>
              {task.current_tool && <div className="agent-kanban-task-tool">&gt; {task.current_tool}</div>}
              <div className="agent-kanban-task-progress"><span /></div>
              <div className="agent-kanban-task-meta">
                <span>{task.tool_calls || 0} {labels.toolCalls}</span>
                <span>{task.comments?.length || 0} {labels.comments}</span>
                {task.depends.length > 0 && <span>{labels.deps}: {task.depends.length}</span>}
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function TaskDetail({ task, commentText, commentError, submittingComment, onCommentChange, onCommentSubmit, labels }) {
  if (!task) {
    return (
      <section className="agent-kanban-detail-panel agent-kanban-detail-panel-empty">
        <div className="agent-kanban-panel-kicker">{labels.taskDetail}</div>
        <div className="agent-kanban-detail-empty-icon"><Bot size={18} /></div>
        <div className="agent-kanban-detail-empty-title">{labels.noTaskSelected}</div>
        <div className="agent-kanban-detail-empty-copy">{labels.railHint}</div>
      </section>
    );
  }

  return (
    <section className="agent-kanban-detail-panel">
      <div className="agent-kanban-panel-kicker">{labels.taskDetail}</div>
      <div className="agent-kanban-detail-head">
        <div>
          <div className="agent-kanban-detail-title">{task.title || labels.unnamedTask}</div>
          <div className="agent-kanban-detail-subtitle">{task.agent_id || labels.workerFallback} · {translatePriority(task.priority, labels)}</div>
        </div>
        <div className="agent-kanban-detail-status" style={{ '--agent-kanban-accent': accentForStatus(task.status) }}>
          {translateStatus(task.status, labels)}
        </div>
      </div>

      <div className="agent-kanban-detail-description">{task.description || labels.noTaskDescription}</div>

      <div className="agent-kanban-detail-grid">
        <div className="agent-kanban-detail-metric">
          <Clock3 size={14} />
          <div>
            <span>{labels.started}</span>
            <strong>{formatTimestamp(task.started_at)}</strong>
          </div>
        </div>
        <div className="agent-kanban-detail-metric">
          <CheckCircle2 size={14} />
          <div>
            <span>{labels.ended}</span>
            <strong>{formatTimestamp(task.ended_at)}</strong>
          </div>
        </div>
        <div className="agent-kanban-detail-metric">
          <Activity size={14} />
          <div>
            <span>{labels.toolCalls}</span>
            <strong>{task.tool_calls || 0}</strong>
          </div>
        </div>
        <div className="agent-kanban-detail-metric">
          {task.status === 'sleeping' ? <PauseCircle size={14} /> : <PlayCircle size={14} />}
          <div>
            <span>{labels.currentTool}</span>
            <strong>{task.current_tool || labels.noActiveTool}</strong>
          </div>
        </div>
      </div>

      {task.depends.length > 0 && (
        <div className="agent-kanban-dependency-list">
          {task.depends.map((dependency) => (
            <span key={dependency} className="agent-kanban-inline-chip">{dependency}</span>
          ))}
        </div>
      )}

      {task.result_summary && (
        <div className="agent-kanban-summary-box is-success">
          <div className="agent-kanban-panel-kicker">{labels.result}</div>
          <div className="agent-kanban-summary-copy">{task.result_summary}</div>
        </div>
      )}
      {task.error && (
        <div className="agent-kanban-summary-box is-error">
          <div className="agent-kanban-panel-kicker">{labels.failure}</div>
          <div className="agent-kanban-summary-copy">{task.error}</div>
        </div>
      )}

      <section className="agent-kanban-comments-panel">
        <div className="agent-kanban-comments-head">
          <div>
            <div className="agent-kanban-panel-kicker">{labels.commentTitle}</div>
            <div className="agent-kanban-comments-count">{task.comments?.length || 0} {labels.comments}</div>
          </div>
        </div>
        <textarea
          className="agent-kanban-comment-input"
          value={commentText}
          onChange={(event) => onCommentChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              onCommentSubmit();
            }
          }}
          placeholder={labels.commentPlaceholder}
          rows={3}
        />
        {commentError && (
          <div className="agent-kanban-inline-error">
            <AlertTriangle size={14} />
            <span>{commentError}</span>
          </div>
        )}
        <button type="button" onClick={onCommentSubmit} className="btn-primary agent-kanban-comment-button" disabled={submittingComment}>
          <MessageSquareText size={16} />
          {submittingComment ? labels.commentPosting : labels.commentSubmit}
        </button>
        <div className="agent-kanban-comment-list">
          {(task.comments || []).length === 0 && <div className="agent-kanban-empty-copy">{labels.noComments}</div>}
          {(task.comments || []).slice().reverse().map((comment) => (
            <div key={comment.comment_id || `${comment.author}-${comment.ts}`} className="agent-kanban-comment-card">
              <div className="agent-kanban-comment-head">
                <div className="agent-kanban-comment-author">{comment.author || labels.ownerLabel}</div>
                <div className="agent-kanban-comment-time">{formatTimestamp(comment.ts)}</div>
              </div>
              <div className="agent-kanban-comment-body">{comment.text}</div>
            </div>
          ))}
        </div>
      </section>
    </section>
  );
}

function WorkersModal({
  open,
  maxWorkers,
  labels,
  t,
  savingSettings,
  onClose,
  onConfigUpdate,
  onSave,
}) {
  if (!open) return null;

  return (
    <div className="agent-kanban-settings-modal" role="dialog" aria-modal="true" aria-label={labels.maxWorkers} onClick={onClose}>
      <div className="agent-kanban-settings-modal-card" onClick={(event) => event.stopPropagation()}>
        <div className="agent-kanban-settings-modal-head">
          <div className="agent-kanban-settings-modal-title">{labels.maxWorkers}</div>
          <button type="button" className="btn-ghost agent-kanban-settings-close" onClick={onClose} aria-label={labels.settingsClose}>
            <X size={16} />
          </button>
        </div>

        <div className="agent-kanban-field" style={{ marginTop: 0 }}>
          <input
            id="max-workers-modal"
            type="number"
            min="1"
            max="20"
            value={maxWorkers}
            onChange={(event) => onConfigUpdate('multi_agent.max_workers', Math.min(20, Math.max(1, parseInt(event.target.value, 10) || 1)))}
            className="agent-kanban-number-input"
          />
          <div className="agent-kanban-field-hint">{t.agentKanbanMaxWorkersHint || labels.maxWorkersHint}</div>
        </div>

        <div className="agent-kanban-action-row">
          <button type="button" onClick={onSave} className="btn-primary agent-kanban-action-button" disabled={savingSettings}>
            <Save size={16} />
            {savingSettings ? (t.saving || 'Saving...') : (t.saveConfig || 'Save')}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function AgentKanban({ config, setConfig, saveConfig, t }) {
  const labels = useMemo(() => buildLabels(t), [t]);
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);
  const [commentText, setCommentText] = useState('');
  const [commentError, setCommentError] = useState('');
  const [submittingComment, setSubmittingComment] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const handleMonitorEvent = useCallback((message) => dispatch({ type: 'event', message }), [dispatch]);
  const connection = useMonitorWS({ onEvent: handleMonitorEvent });

  useEffect(() => {
    dispatch({ type: 'connection', status: connection });
  }, [connection]);

  const orderedTasks = useMemo(() => sortTasks(Object.values(state.tasks)), [state.tasks]);
  const selectedTask = useMemo(() => state.tasks[state.selectedTaskId] || orderedTasks[0] || null, [orderedTasks, state.selectedTaskId, state.tasks]);
  const missionTasks = useMemo(() => orderedTasks.slice(0, 4), [orderedTasks]);
  const tasksByColumn = useMemo(() => (
    Object.fromEntries(COLUMNS.map((column) => [column.key, orderedTasks.filter((task) => task.status === column.key)]))
  ), [orderedTasks]);
  const localizedColumns = useMemo(() => ([
    { ...COLUMNS[0], label: t.agentKanbanColumnQueued || 'Queued', systemLabel: 'QUEUED' },
    { ...COLUMNS[1], label: t.agentKanbanColumnRunning || 'Running', systemLabel: 'RUNNING' },
    { ...COLUMNS[2], label: t.agentKanbanColumnSleeping || 'Sleeping', systemLabel: 'SLEEPING' },
    { ...COLUMNS[3], label: t.agentKanbanColumnCompleted || 'Completed', systemLabel: 'DONE' },
    { ...COLUMNS[4], label: t.agentKanbanColumnFailed || 'Failed', systemLabel: 'FAILED' },
  ]), [t]);

  const handleConfigUpdate = useCallback((path, value) => {
    setConfig((prev) => updateNestedConfig(prev, path, value));
  }, [setConfig]);

  const handleSaveSettings = useCallback(async (closeAfterSave = false) => {
    setSavingSettings(true);
    try {
      await saveConfig();
      if (closeAfterSave) setSettingsOpen(false);
    } finally {
      setSavingSettings(false);
    }
  }, [saveConfig]);

  const handleToggleMulti = useCallback(() => {
    const next = !config?.multi_agent?.allow_multi;
    setConfig((prev) => updateNestedConfig(prev, 'multi_agent.allow_multi', next));
    if (next) {
      setSettingsOpen(true);
    } else {
      setTimeout(() => saveConfig(), 0);
    }
  }, [config, setConfig, saveConfig]);

  useEffect(() => {
    if (!settingsOpen) return undefined;
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') setSettingsOpen(false);
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [settingsOpen]);

  const handleSubmitComment = useCallback(async () => {
    if (!selectedTask) return;
    const text = commentText.trim();
    if (!text) {
      setCommentError(labels.commentRequired);
      return;
    }
    setSubmittingComment(true);
    setCommentError('');
    try {
      await postTaskComment(selectedTask.task_id, { text, author: labels.ownerLabel });
      setCommentText('');
    } catch (error) {
      setCommentError(error?.response?.data?.detail || labels.commentFailed);
    } finally {
      setSubmittingComment(false);
    }
  }, [commentText, labels.commentFailed, labels.commentRequired, labels.ownerLabel, selectedTask]);

  if (!config) {
    return (
      <div style={{ color: '#889', padding: 16, textAlign: 'center', marginTop: 40 }}>
        {labels.sessionEmpty}
      </div>
    );
  }

  const allowMulti = Boolean(config.multi_agent?.allow_multi);
  const maxWorkers = config.multi_agent?.max_workers ?? 5;
  return (
    <div className="agent-kanban-shell">
      <div className="agent-kanban-topbar" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2 style={{ fontSize: 18, fontWeight: 600, color: '#fff', margin: 0, display: 'flex', alignItems: 'center', gap: 8, textTransform: 'none', letterSpacing: 0 }}>
            <Bot size={20} style={{ color: 'var(--accent-red)' }} />
            {t.agentKanbanTitle || 'Multi-Agent Board'}
          </h2>
          <p style={{ fontSize: 13, color: '#667', margin: '4px 0 0 0' }}>{t.agentKanbanSubtitle || labels.subtitle}</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 12, color: allowMulti ? '#34d399' : '#667' }}>
            {allowMulti ? labels.enabled : labels.disabled}
          </span>
          <button
            type="button"
            className={`agent-kanban-switch${allowMulti ? ' is-on' : ''}`}
            onClick={handleToggleMulti}
            aria-pressed={allowMulti}
          >
            <span />
          </button>
          {allowMulti && (
            <button type="button" className="btn-ghost" onClick={() => setSettingsOpen(true)} title={labels.maxWorkers}>
              <Settings2 size={14} />
            </button>
          )}
        </div>
      </div>

      <div className="agent-kanban-stage" style={{ display: 'flex', gap: 10, marginTop: 16, marginBottom: 14, flexWrap: 'wrap' }}>
        {[
          { label: labels.total, value: formatCount(state.stats.total), color: '#67e8f9' },
          { label: labels.done, value: formatCount(state.stats.done), color: '#34d399' },
          { label: labels.failed, value: formatCount(state.stats.failed), color: '#fb7185' },
          { label: labels.tokens, value: formatCount(state.stats.tokens), color: '#f59e0b' },
          { label: labels.statusLabel, value: formatPhase(state.phase, labels), color: phaseAccent(state.phase) },
        ].map((s) => (
          <div key={s.label} style={{
            padding: '8px 16px', borderRadius: 10,
            border: '1px solid rgba(255,255,255,0.06)',
            background: 'rgba(255,255,255,0.02)',
            minWidth: 90,
          }}>
            <div style={{ fontSize: 10, color: '#667', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{s.label}</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: s.color, marginTop: 2 }}>{s.value}</div>
          </div>
        ))}
      </div>

      {missionTasks.length > 0 && (
        <div className="agent-kanban-mission-strip" style={{ display: 'flex', gap: 8, marginBottom: 14, overflowX: 'auto' }}>
          {missionTasks.map((task) => (
            <div key={task.task_id} style={{
              flex: '0 0 auto', padding: '6px 12px', borderRadius: 8,
              border: '1px solid rgba(255,255,255,0.06)', background: 'rgba(255,255,255,0.02)',
              display: 'flex', gap: 10, alignItems: 'center', fontSize: 12,
            }}>
              <span style={{ color: '#d1d5db' }}>{previewText(task.title || labels.unnamedTask, 24)}</span>
              <span style={{ color: accentForStatus(task.status), fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                {translateStatus(task.status, labels)}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="agent-kanban-layout agent-kanban-workbench">
        <div className="agent-kanban-board agent-kanban-execution-lanes">
          {localizedColumns.map((column) => (
            <TaskColumn
              key={column.key}
              column={column}
              tasks={tasksByColumn[column.key] || []}
              selectedTaskId={selectedTask?.task_id || ''}
              onSelect={(taskId) => dispatch({ type: 'select', taskId })}
              labels={labels}
            />
          ))}
        </div>
        <div className="agent-kanban-rail" style={{ position: 'sticky', top: 12 }}>
          <div className="agent-kanban-detail-stack">
            <TaskDetail
              task={selectedTask}
              commentText={commentText}
              commentError={commentError}
              submittingComment={submittingComment}
              onCommentChange={setCommentText}
              onCommentSubmit={handleSubmitComment}
              labels={labels}
            />
            <div className="agent-kanban-log-panel" style={{ display: 'none' }} aria-hidden="true" />
          </div>
        </div>
      </div>

      <WorkersModal
        open={settingsOpen}
        maxWorkers={maxWorkers}
        labels={labels}
        t={t}
        savingSettings={savingSettings}
        onClose={() => setSettingsOpen(false)}
        onConfigUpdate={handleConfigUpdate}
        onSave={() => handleSaveSettings(true)}
      />
    </div>
  );
}
