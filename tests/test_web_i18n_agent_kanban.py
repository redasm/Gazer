from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_KANBAN_PATH = PROJECT_ROOT / "web" / "src" / "pages" / "AgentKanban.jsx"
I18N_PATH = PROJECT_ROOT / "web" / "src" / "i18n.js"
CSS_PATH = PROJECT_ROOT / "web" / "src" / "index.css"


def test_agent_kanban_uses_translation_keys() -> None:
    source = AGENT_KANBAN_PATH.read_text(encoding="utf-8")

    required_usages = [
        "t.agentKanbanTitle",
        "t.agentKanbanSubtitle",
        "t.agentKanbanColumnQueued",
        "t.agentKanbanExecutionControl",
        "t.agentKanbanCommentSubmit",
        "t.agentKanbanLiveLog",
        "t.agentKanbanConnectionLive",
        "t.agentKanbanOwnerLabel",
        "t.agentKanbanMissionFocus",
        "t.agentKanbanControlDeck",
    ]

    for usage in required_usages:
        assert usage in source


def test_i18n_contains_agent_kanban_strings_for_en_and_zh() -> None:
    source = I18N_PATH.read_text(encoding="utf-8")

    expected_fragments = [
        'agentKanbanTitle: "Multi-Agent Board"',
        'agentKanbanSubtitle: "Multi-Agent Mission Control"',
        'agentKanbanExecutionControl: "Execution Control"',
        'agentKanbanCommentSubmit: "Post Comment"',
        'agentKanbanLiveLog: "Live Log"',
        'agentKanbanOwnerLabel: "Owner"',
        'agentKanbanMissionFocus: "Mission Focus"',
        'agentKanbanControlDeck: "Operator Deck"',
        'agentKanbanTitle: "多 Agent 看板"',
        'agentKanbanSubtitle: "多 Agent 任务控制台"',
        'agentKanbanExecutionControl: "执行控制"',
        'agentKanbanCommentSubmit: "发送评论"',
        'agentKanbanLiveLog: "实时日志"',
        'agentKanbanOwnerLabel: "负责人"',
        'agentKanbanMissionFocus: "任务焦点"',
        'agentKanbanControlDeck: "操作台"',
    ]

    for fragment in expected_fragments:
        assert fragment in source


def test_agent_kanban_uses_mission_control_layout_classes() -> None:
    page_source = AGENT_KANBAN_PATH.read_text(encoding="utf-8")
    css_source = CSS_PATH.read_text(encoding="utf-8")

    required_classes = [
        "agent-kanban-shell",
        "agent-kanban-topbar",
        "agent-kanban-mission-strip",
        "agent-kanban-workbench",
        "agent-kanban-stage",
        "agent-kanban-rail",
        "agent-kanban-detail-stack",
        "agent-kanban-log-panel",
    ]

    for class_name in required_classes:
        assert class_name in page_source
        assert f".{class_name}" in css_source


def test_agent_kanban_declares_monitor_hook_callback_before_use() -> None:
    source = AGENT_KANBAN_PATH.read_text(encoding="utf-8")

    assert "const handleMonitorEvent = useCallback((message) => dispatch({ type: 'event', message }), [dispatch]);" in source
    assert "const connection = useMonitorWS({ onEvent: handleMonitorEvent });" in source
    assert "useMonitorWS({ onEvent: useCallback((message) => dispatch({ type: 'event', message }), []) })" not in source
