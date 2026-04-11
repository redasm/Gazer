from pathlib import Path
import json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_KANBAN_PATH = PROJECT_ROOT / "web" / "src" / "pages" / "AgentKanban.jsx"
I18N_PATH = PROJECT_ROOT / "web" / "src" / "i18n.js"
EN_LOCALE_PATH = PROJECT_ROOT / "web" / "src" / "locales" / "en.json"
ZH_LOCALE_PATH = PROJECT_ROOT / "web" / "src" / "locales" / "zh.json"
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
    en = json.loads(EN_LOCALE_PATH.read_text(encoding="utf-8"))
    zh = json.loads(ZH_LOCALE_PATH.read_text(encoding="utf-8"))

    assert en["agentKanbanTitle"] == "Multi-Agent Board"
    assert en["agentKanbanSubtitle"] == "Multi-Agent Mission Control"
    assert en["agentKanbanExecutionControl"] == "Execution Control"
    assert en["agentKanbanCommentSubmit"] == "Post Comment"
    assert en["agentKanbanLiveLog"] == "Live Log"
    assert en["agentKanbanOwnerLabel"] == "Owner"
    assert en["agentKanbanMissionFocus"] == "Mission Focus"
    assert en["agentKanbanControlDeck"] == "Operator Deck"

    assert zh["agentKanbanTitle"] == "多 Agent 看板"
    assert zh["agentKanbanSubtitle"] == "多 Agent 任务控制台"
    assert zh["agentKanbanExecutionControl"] == "执行控制"
    assert zh["agentKanbanCommentSubmit"] == "发送评论"
    assert zh["agentKanbanLiveLog"] == "实时日志"
    assert zh["agentKanbanOwnerLabel"] == "负责人"
    assert zh["agentKanbanMissionFocus"] == "任务焦点"
    assert zh["agentKanbanControlDeck"] == "操作台"


def test_i18n_loader_imports_locale_json_files() -> None:
    source = I18N_PATH.read_text(encoding="utf-8")

    assert "import en from './locales/en.json';" in source
    assert "import zh from './locales/zh.json';" in source
    assert "supportedLocales" in source


def test_locale_files_share_identical_top_level_keys() -> None:
    en = json.loads(EN_LOCALE_PATH.read_text(encoding="utf-8"))
    zh = json.loads(ZH_LOCALE_PATH.read_text(encoding="utf-8"))

    assert set(en.keys()) == set(zh.keys())


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
