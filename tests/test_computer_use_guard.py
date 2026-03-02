"""Tests for tools.computer_use_guard — Appendix-01 acceptance criteria.

Verifies:
  - Base pattern matching yields correct scores.
  - Emotional state (agitated) boosts the score.
  - requires_confirmation flag is correctly set at >= 0.5.
"""

from tools.computer_use_guard import ComputerUseGuard
from soul.affect.affective_state import AffectiveState


def test_computer_use_guard_safe_command() -> None:
    guard = ComputerUseGuard()
    # "ls -la" does not match dangerous patterns
    result = guard.assess("ls -la")
    assert result.score == 0.0
    assert not result.requires_confirmation
    assert "操作安全" in result.reason


def test_computer_use_guard_base_pattern_match() -> None:
    guard = ComputerUseGuard()
    # "delete" has a score of 0.6
    result = guard.assess("delete the production database")
    assert result.score == 0.6
    assert result.requires_confirmation
    assert "delete" in result.reason


def test_computer_use_guard_affect_boost_safe_command() -> None:
    guard = ComputerUseGuard()
    # Agitated state: arousal > 0.6 and valence < -0.3
    agitated_affect = AffectiveState(valence=-0.5, arousal=0.8, dominance=0.0)
    
    # Safe command should still get +0.25 boost
    result = guard.assess("echo hello", current_affect=agitated_affect)
    assert result.score == 0.25
    assert not result.requires_confirmation
    assert "建议冷静" in result.reason


def test_computer_use_guard_affect_boost_dangerous_command() -> None:
    guard = ComputerUseGuard()
    agitated_affect = AffectiveState(valence=-0.5, arousal=0.8, dominance=0.0)
    
    # "submit" base score 0.5
    # Agitated boost +0.25
    # Total expected: 0.75
    result = guard.assess("submit the form", current_affect=agitated_affect)
    assert result.score == 0.75
    assert result.requires_confirmation
    assert "submit" in result.reason
    assert "建议冷静" in result.reason


def test_computer_use_guard_score_capped_at_1() -> None:
    guard = ComputerUseGuard()
    agitated_affect = AffectiveState(valence=-0.5, arousal=0.8, dominance=0.0)
    
    # "reset" base score 0.9
    # Agitated boost +0.25
    # Total expected: 1.0 (capped)
    result = guard.assess("reset system", current_affect=agitated_affect)
    assert result.score == 1.0
    assert result.requires_confirmation


def test_computer_use_guard_calm_affect_no_boost() -> None:
    guard = ComputerUseGuard()
    # Calm state, should not trigger boost
    calm_affect = AffectiveState(valence=0.5, arousal=0.2, dominance=0.0)
    
    result = guard.assess("submit the form", current_affect=calm_affect)
    # Only base score 0.5
    assert result.score == 0.5
    assert result.requires_confirmation
    assert "建议冷静" not in result.reason
