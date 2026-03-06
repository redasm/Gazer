from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PAGE_PATH = PROJECT_ROOT / "web" / "src" / "pages" / "Settings.jsx"
KANBAN_PAGE_PATH = PROJECT_ROOT / "web" / "src" / "pages" / "AgentKanban.jsx"


def test_settings_page_no_longer_exposes_planning_policy_controls() -> None:
    source = SETTINGS_PAGE_PATH.read_text(encoding="utf-8")

    assert "agents.defaults.planning" not in source
    assert "planningPolicy" not in source
    assert "minMessageChars" not in source
    assert "minHistoryMessages" not in source
    assert "minLineBreaks" not in source
    assert "minListLines" not in source


def test_settings_page_hides_internal_threshold_tuning_controls() -> None:
    source = SETTINGS_PAGE_PATH.read_text(encoding="utf-8")

    assert "memory.tool_result_persistence.min_result_chars" not in source
    assert "memory.tool_result_persistence.max_result_chars" not in source
    assert "personality.evolution.auto_optimize.min_feedback_total" not in source
    assert "personality.evolution.auto_optimize.min_actionable_feedback" not in source
    assert "personality.evolution.auto_optimize.cooldown_seconds" not in source
    assert "personality.evolution.publish_gate.min_similarity" not in source
    assert "personality.evolution.publish_gate.min_length_ratio" not in source
    assert "personality.evolution.publish_gate.max_length_ratio" not in source


def test_multi_agent_controls_remain_on_kanban_page() -> None:
    source = KANBAN_PAGE_PATH.read_text(encoding="utf-8")

    assert "multi_agent.allow_multi" in source
    assert "multi_agent.max_workers" in source
