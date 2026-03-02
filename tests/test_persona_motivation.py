from unittest.mock import MagicMock

from soul.persona import GazerPersonality


def _make_personality(monkeypatch, mapping):
    """Create a GazerPersonality with config stubbed out."""
    monkeypatch.setattr(
        "soul.persona.config.get",
        lambda key, default=None: mapping.get(key, default),
    )
    mock_mm = MagicMock()
    mock_mm.backend = MagicMock()
    return GazerPersonality(memory_manager=mock_mm)


def test_persona_motivation_context_contains_drives_and_goals(monkeypatch):
    mapping = {
        "personality.drives": ["be_helpful", "protect_user_safety"],
        "personality.goals": ["deliver_reliable_task_results"],
    }
    persona = _make_personality(monkeypatch, mapping)
    ctx = persona._build_motivation_context()
    assert "Drives & Goals" in ctx
    assert "be_helpful" in ctx
    assert "deliver_reliable_task_results" in ctx


def test_persona_goal_progress_updates_context(monkeypatch):
    mapping = {
        "personality.drives": ["be_helpful"],
        "personality.goals": ["deliver_reliable_task_results"],
    }
    persona = _make_personality(monkeypatch, mapping)

    persona.reset_goal_progress()
    persona._update_goal_progress(
        "please deliver reliable task results",
        "I will deliver reliable task results.",
    )
    ctx = persona._build_motivation_context()

    assert "Goal Progress:" in ctx
    assert "turn_success_rate: 1/1" in ctx
    assert "deliver_reliable_task_results: mentions=1" in ctx
